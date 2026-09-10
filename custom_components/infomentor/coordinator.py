"""Coordinator that fetches every pupil sequentially.

switch_pupil() mutates server-side session state, so concurrent fetches would
return the wrong child's data. All refreshes are serialised behind a lock.
"""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    LEARNLOG_GROUP,
    LEARNLOG_INDIVIDUAL,
    InfoMentorAuthError,
    InfoMentorClient,
    InfoMentorError,
    LearnLogEntry,
    MediaFile,
    ModuleUnavailable,
    Pupil,
    safe_path_part,
)
from .const import (
    ALL_MODULES,
    DOMAIN,
    EVENT_NEW_CALENDAR_ATTACHMENT,
    EVENT_NEW_LEARNLOG_MEDIA,
    MODULE_ATTENDANCE,
    MODULE_CALENDAR,
    MODULE_LEARNLOG,
    MODULE_NOTIFICATIONS,
    MODULE_TIMEREGISTRATION,
    MODULE_TIMETABLE,
    SOURCE_LETTERS,
    SOURCE_PHOTOS,
    STORAGE_KEY,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)


def _safe_folder(name: str) -> str:
    return safe_path_part(name) or "pupil"


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    return text.casefold()


def _matches_any_keyword(text: str, keywords: list[str]) -> bool:
    normalized = _normalize(text)
    tokens = set(re.findall(r"[a-z0-9]+", normalized))
    for keyword in keywords:
        needle = _normalize(keyword).strip()
        if not needle:
            continue
        if re.fullmatch(r"[a-z0-9]+", needle):
            if needle in tokens:
                return True
            continue
        if needle in normalized:
            return True
    return False


def _subfolder(media: MediaFile) -> Path:
    """Group posts go in their own folder so they are easy to tell apart."""
    if media.source == "calendar":
        return Path("letters")
    if media.is_group:
        return Path("photos") / "group" / _safe_folder(media.group_name)
    return Path("photos") / "individual"


def _learnlog_post_response(pupil: Pupil, entry: LearnLogEntry) -> dict[str, Any]:
    return {
        "pupil_id": pupil.id,
        "pupil_name": pupil.name,
        "id": entry.id,
        "title": entry.title,
        "date": entry.modified_on.isoformat() if entry.modified_on else None,
        "scope": "individual" if entry.is_individual else "group",
        "group_name": entry.group_name or None,
        "description": entry.text,
        "description_html": entry.text_html,
        "comments": entry.comments,
        "image_count": len(entry.media),
        "files": [
            {
                "file_id": media.file_id,
                "filename": media.filename,
                "entry_id": media.entry_id,
            }
            for media in entry.media
        ],
    }


@dataclass
class PupilData:
    pupil: Pupil
    timetable: list[dict[str, Any]] = field(default_factory=list)
    calendar: list[dict[str, Any]] = field(default_factory=list)
    notifications: list[dict[str, Any]] = field(default_factory=list)
    attendance: dict[str, Any] | None = None
    learnlog: list[LearnLogEntry] = field(default_factory=list)
    media: list[MediaFile] = field(default_factory=list)
    time_registration_today: dict[str, Any] | None = None
    time_registration_week: list[dict[str, Any]] = field(default_factory=list)


class InfoMentorCoordinator(DataUpdateCoordinator[dict[str, PupilData]]):
    def __init__(
        self,
        hass: HomeAssistant,
        client: InfoMentorClient,
        pupils: list[Pupil],
        update_interval: timedelta,
        modules: dict[str, list[str]] | None = None,
        download_path: str | None = None,
        default_download_path: str | None = None,
        prep_lesson_keywords: list[str] | None = None,
    ) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=update_interval)
        self.client = client
        self.pupils = pupils
        self._modules = modules or {}
        self._download_path = download_path
        self.default_download_path = default_download_path
        self.prep_lesson_keywords = prep_lesson_keywords or []
        self._lock = asyncio.Lock()
        self._store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._seen_files: set[int] = set()
        self._queried_media: dict[int, tuple[Pupil, MediaFile]] = {}
        self._loaded_seen = False
        self._has_completed_refresh = False

    def modules_for(self, pupil_id: str) -> list[str]:
        """Unconfigured pupils fetch everything."""
        return self._modules.get(pupil_id) or ALL_MODULES

    def lesson_requires_preparation(self, lesson: dict[str, Any]) -> bool:
        text = " ".join(
            str(lesson.get(key) or "") for key in ("title", "details")
        )
        notes = lesson.get("notes") or {}
        text += " " + " ".join(str(value or "") for value in notes.values())
        return _matches_any_keyword(text, self.prep_lesson_keywords)

    async def _async_update_data(self) -> dict[str, PupilData]:
        if not self._loaded_seen:
            stored = await self._store.async_load()
            self._seen_files = set(stored or [])
            self._loaded_seen = True
            first_run = not self._seen_files
        else:
            first_run = False

        async with self._lock:
            try:
                data = {}
                for pupil in self.pupils:
                    await self.client.switch_pupil(pupil.id)
                    data[pupil.id] = await self._fetch_pupil(pupil)
            except InfoMentorAuthError as err:
                raise UpdateFailed(f"Authentication failed: {err}") from err
            except InfoMentorError as err:
                raise UpdateFailed(f"InfoMentor request failed: {err}") from err
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                raise UpdateFailed(f"Connection to InfoMentor failed: {err}") from err

        suppress = first_run or not self._has_completed_refresh
        await self._emit_new_media(data, suppress=suppress)
        self._has_completed_refresh = True
        return data

    async def _fetch_pupil(self, pupil: Pupil) -> PupilData:
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        result = PupilData(pupil=pupil)
        enabled = self.modules_for(pupil.id)

        if MODULE_TIMETABLE in enabled:
            result.timetable = await self._safe(
                self.client.get_timetable(monday, monday + timedelta(days=13)), []
            )
        if MODULE_CALENDAR in enabled:
            result.calendar = await self._safe(
                self.client.get_calendar(
                    monday - timedelta(days=7), monday + timedelta(days=13)
                ),
                [],
            )
        if MODULE_NOTIFICATIONS in enabled:
            notifications = await self._safe(self.client.get_notifications(), {})
            # The endpoint returns every pupil's notifications; only the flag differs.
            result.notifications = [
                item
                for item in (notifications or {}).get("notifications", []) or []
                if item.get("currentlySelectedPupil")
            ]
        if MODULE_ATTENDANCE in enabled:
            result.attendance = await self._safe(self.client.get_attendance(), None)
        if MODULE_LEARNLOG in enabled:
            entries = await self._safe(
                self.client.get_learnlog_entries(LEARNLOG_INDIVIDUAL), []
            )
            group = await self._safe(self.client.get_learnlog_entries(LEARNLOG_GROUP), [])
            seen_entries = {entry.id for entry in entries}
            entries += [entry for entry in group if entry.id not in seen_entries]
            result.learnlog = entries
        if MODULE_TIMEREGISTRATION in enabled:
            registrations = await self._safe(self.client.get_time_registrations(), {})
            result.time_registration_week = (registrations or {}).get("days", []) or []
            if self._registered_today(result.time_registration_week, today):
                result.time_registration_today = await self._safe(
                    self.client.get_time_registration_day(today), None
                )

        media: list[MediaFile] = []
        for entry in result.learnlog:
            media.extend(entry.media)
        for entry in result.calendar:
            if entry.get("hasAttachments"):
                media.extend(
                    await self._safe(
                        self.client.get_calendar_attachments(
                            entry["id"],
                            entry.get("title", ""),
                            (entry.get("startDate") or "")[:10],
                        ),
                        [],
                    )
                )
        result.media = media
        return result

    @staticmethod
    def _registered_today(days: list[dict[str, Any]], today: date) -> bool:
        """Skip the extra request on days off and when the school is closed."""
        for day in days:
            raw = day.get("date") or ""
            if raw[:10] != today.isoformat():
                continue
            return not day.get("onLeave") and not day.get("isSchoolClosed")
        return False

    async def _safe(self, coro, default):
        """Modules differ per pupil; a preschooler has no timetable."""
        try:
            result = await coro
        except ModuleUnavailable:
            return default
        except InfoMentorError as err:
            _LOGGER.warning("InfoMentor fetch failed: %s", err)
            return default
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.warning("InfoMentor connection issue: %s", err)
            return default
        # An empty body decodes to None, which callers iterate over.
        return default if result is None else result

    async def _emit_new_media(self, data: dict[str, PupilData], suppress: bool) -> None:
        new_ids: set[int] = set()
        for pupil_id, pupil_data in data.items():
            for media in pupil_data.media:
                if media.file_id in self._seen_files or media.file_id in new_ids:
                    continue
                new_ids.add(media.file_id)
                if suppress:
                    continue

                saved_path = await self._download(media, pupil_data.pupil)
                event = (
                    EVENT_NEW_LEARNLOG_MEDIA
                    if media.source == "learnlog"
                    else EVENT_NEW_CALENDAR_ATTACHMENT
                )
                self.hass.bus.async_fire(
                    event,
                    {
                        "pupil_id": pupil_id,
                        "pupil_name": pupil_data.pupil.name,
                        "file_id": media.file_id,
                        "filename": media.filename,
                        "entry_id": media.entry_id,
                        "entry_title": media.entry_title,
                        "entry_date": media.entry_date,
                        "scope": "group" if media.is_group else "individual",
                        "group_name": media.group_name,
                        "path": saved_path,
                    },
                )

        if new_ids:
            self._seen_files |= new_ids
            await self._store.async_save(sorted(self._seen_files))

    async def async_download_media(
        self,
        media: MediaFile,
        pupil: Pupil,
        path: str | None = None,
        filename: str | None = None,
    ) -> str | None:
        async with self._lock:
            await self.client.switch_pupil(pupil.id)
            return await self._download_current_pupil(media, pupil, path, filename)

    async def _download_current_pupil(
        self,
        media: MediaFile,
        pupil: Pupil,
        path: str | None = None,
        filename: str | None = None,
    ) -> str | None:
        base = path or self._download_path
        if not base:
            return None

        folder = Path(base) / _safe_folder(pupil.name) / _subfolder(media)
        if not self.hass.config.is_allowed_path(str(folder)):
            _LOGGER.error(
                "%s is not an allowed path; add it to allowlist_external_dirs or use /media",
                folder,
            )
            return None

        try:
            async with asyncio.timeout(60):
                content = await self.client.download(media.url)
        except (InfoMentorError, aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.warning("Could not download %s: %s", media.filename, err)
            return None
        if not content:
            _LOGGER.warning("InfoMentor returned an empty file for %s", media.filename)
            return None

        target = folder / (filename or media.filename)

        def _write() -> None:
            folder.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)

        try:
            await self.hass.async_add_executor_job(_write)
        except OSError as err:
            _LOGGER.warning("Could not save %s: %s", target, err)
            return None

        _LOGGER.debug("Saved InfoMentor file to %s", target)
        return str(target)

    async def _download(
        self, media: MediaFile, pupil: Pupil, path: str | None = None
    ) -> str | None:
        return await self.async_download_media(media, pupil, path)

    def find_media(self, file_id: int) -> tuple[Pupil, MediaFile] | None:
        for pupil_data in (self.data or {}).values():
            for media in pupil_data.media:
                if media.file_id == file_id:
                    return pupil_data.pupil, media
        return self._queried_media.get(file_id)

    async def async_get_learnlog_posts(
        self, pupils: list[Pupil], start: date, end: date
    ) -> list[dict[str, Any]]:
        """Return individual and group learnlog posts within an inclusive date range."""
        posts: list[dict[str, Any]] = []

        async with self._lock:
            for pupil in pupils:
                await self.client.switch_pupil(pupil.id)
                entries = await self._learnlog_entries_in_range(start, end)
                for entry in entries:
                    for media in entry.media:
                        self._queried_media[media.file_id] = (pupil, media)
                    posts.append(_learnlog_post_response(pupil, entry))

        posts.sort(key=lambda post: post["date"] or "", reverse=True)
        return posts

    async def async_save_time_registration_comment(
        self, pupil: Pupil, day: date, comment: str, go_home_time: str | None = None
    ) -> dict[str, Any]:
        async with self._lock:
            await self.client.switch_pupil(pupil.id)
            registrations = await self.client.get_time_registrations()
            registration = next(
                (
                    item
                    for item in (registrations or {}).get("days", [])
                    if (item.get("date") or "")[:10] == day.isoformat()
                ),
                None,
            )
            if registration is None:
                raise InfoMentorError(
                    f"No time registration found for {pupil.name} on {day.isoformat()}."
                )

            current_comment = await self.client.get_time_registration_day(day)
            time_result: dict[str, Any] = {}
            if go_home_time is not None:
                time_result = await self.client.save_time_registrations(
                    [
                        {
                            "timeRegistrationId": registration.get("timeRegistrationId"),
                            "date": day.isoformat(),
                            "startDateTime": registration.get("startDateTime"),
                            "endDateTime": f"{day.isoformat()}T{go_home_time}",
                            "schoolOpeningTime": registration.get("schoolOpeningTime"),
                            "schoolClosingTime": registration.get("schoolClosingTime"),
                            "registrationType": (
                                "OnLeave"
                                if registration.get("onLeave")
                                else "TimeReg"
                            ),
                            "commentText": current_comment.get("userComment") or "",
                            "isCommentUpdated": False,
                            "commentId": current_comment.get("parentCommentId") or 0,
                        }
                    ]
                ) or {}

            comment_result = await self.client.save_time_registration_comment(
                int(registration["timeRegistrationId"]),
                int(current_comment.get("parentCommentId") or 0),
                comment,
            ) or {}
            verified = await self.client.get_time_registration_day(day)
            if (verified.get("userComment") or "") != comment:
                raise InfoMentorError(
                    f"InfoMentor did not persist the comment for {pupil.name} on {day.isoformat()}."
                )
            comment_success = bool(comment_result.get("success"))
            time_success = (
                bool(time_result.get("success"))
                if go_home_time is not None
                else True
            )
            result = {
                "success": comment_success and time_success and verified is not None,
                "comment_success": comment_success,
                "time_success": time_success,
                "comment": comment_result,
                "time": time_result,
                "verified": True,
            }
        await self.async_request_refresh()
        return result or {}

    async def _learnlog_entries_in_range(
        self, start: date, end: date
    ) -> list[LearnLogEntry]:
        page_size = 50
        entries_in_range: list[LearnLogEntry] = []
        seen: set[int] = set()

        for learn_log_type in (LEARNLOG_INDIVIDUAL, LEARNLOG_GROUP):
            for page in range(1, 101):
                entries = await self._safe(
                    self.client.get_learnlog_entries(learn_log_type, page, page_size), []
                )
                if not entries:
                    break

                reached_start = False
                for entry in entries:
                    if entry.id in seen:
                        continue
                    seen.add(entry.id)
                    if entry.modified_on is None:
                        continue
                    entry_day = entry.modified_on.date()
                    if entry_day < start:
                        reached_start = True
                        continue
                    if entry_day <= end:
                        entries_in_range.append(entry)

                if reached_start or len(entries) < page_size:
                    break

        return entries_in_range

    # ------------------------------------------------------------------ backlog

    async def async_download_backlog(
        self,
        pupils: list[Pupil],
        start: date,
        end: date,
        sources: list[str],
        path: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Fetch older posts that predate the seen-files ledger."""
        saved = 0
        failed = 0
        seen: set[int] = set()

        async with self._lock:
            for pupil in pupils:
                if limit is not None and saved >= limit:
                    break
                await self.client.switch_pupil(pupil.id)

                media: list[MediaFile] = []
                if SOURCE_PHOTOS in sources:
                    media += await self._backlog_learnlog(start, end)
                if SOURCE_LETTERS in sources:
                    media += await self._backlog_calendar(start, end)

                for item in media:
                    if limit is not None and saved >= limit:
                        break
                    if item.file_id in seen:
                        continue
                    seen.add(item.file_id)
                    if await self._download_current_pupil(item, pupil, path):
                        saved += 1
                        self._seen_files.add(item.file_id)
                    else:
                        failed += 1

        if saved:
            await self._store.async_save(sorted(self._seen_files))
        _LOGGER.info("Backlog download finished: %s saved, %s failed", saved, failed)
        return {"downloaded": saved, "failed": failed}

    async def _backlog_learnlog(self, start: date, end: date) -> list[MediaFile]:
        return [
            media
            for entry in await self._learnlog_entries_in_range(start, end)
            for media in entry.media
        ]

    async def _backlog_calendar(self, start: date, end: date) -> list[MediaFile]:
        media: list[MediaFile] = []
        entries = await self._safe(self.client.get_calendar(start, end), [])
        for entry in entries or []:
            if not entry.get("hasAttachments"):
                continue
            media += await self._safe(
                self.client.get_calendar_attachments(
                    entry["id"], entry.get("title", ""), (entry.get("startDate") or "")[:10]
                ),
                [],
            )
        return media
