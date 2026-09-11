"""Async InfoMentor hub client.

Port of the reverse-engineered flow proven in the pymentor proof of concept.
Login is a multi-step handshake between the legacy IM1 site (infomentor.se) and
the modern hub (hub.infomentor.se); both hosts share one cookie jar.
"""

from __future__ import annotations

import html as html_lib
import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

HUB = "https://hub.infomentor.se"
IM1 = "https://infomentor.se/swedish/production/mentor/"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
DOWNLOAD_TIMEOUT = aiohttp.ClientTimeout(total=60)

LEARNLOG_INDIVIDUAL = 1
LEARNLOG_GROUP = 2

SWEDISH_MONTHS = {
    "januari": 1, "februari": 2, "mars": 3, "april": 4, "maj": 5, "juni": 6,
    "juli": 7, "augusti": 8, "september": 9, "oktober": 10, "november": 11,
    "december": 12,
}

_OAUTH_TOKEN_RE = re.compile(r'name="oauth_token"[^>]*value="([^"]*)"', re.IGNORECASE)
_PUPIL_ID_RE = re.compile(r"SwitchPupil/(\d+)", re.IGNORECASE)
_PUPIL_TEXT_RE = re.compile(r"pupilText['\"]?\s*>\s*([^<]+)", re.IGNORECASE)


class InfoMentorError(Exception):
    """Generic InfoMentor failure."""

class InfoMentorAuthError(InfoMentorError):
    """Credentials were rejected."""


class ModuleUnavailable(InfoMentorError):
    """The module is not enabled for the currently selected pupil."""


@dataclass
class Pupil:
    id: str
    name: str


@dataclass
class MediaFile:
    file_id: int
    name: str
    url: str
    extension: str
    source: str
    entry_id: int | None = None
    entry_title: str = ""
    entry_date: str = ""
    group_name: str = ""

    @property
    def is_group(self) -> bool:
        return bool(self.group_name)

    @property
    def absolute_url(self) -> str:
        return self.url if self.url.startswith("http") else HUB + self.url

    @property
    def filename(self) -> str:
        suffix = f".{self.extension.lstrip('.')}" if self.extension else ""
        name = safe_path_part(self.name) or str(self.file_id)
        if self.entry_date:
            name = f"{self.entry_date}_{name}"
        return name if suffix and name.lower().endswith(suffix.lower()) else name + suffix


@dataclass
class LearnLogEntry:
    id: int
    title: str
    group_name: str
    modified_on: datetime | None
    text: str = ""
    text_html: str = ""
    comments: list[dict[str, Any]] = field(default_factory=list)
    media: list[MediaFile] = field(default_factory=list)

    @property
    def is_individual(self) -> bool:
        return not self.group_name


def parse_swedish_datetime(text: str) -> datetime | None:
    """Parse 'den 24 augusti 2026 klockan 21:38' - the only date the API exposes."""
    if not text:
        return None
    match = re.search(
        r"(\d{1,2})\s+([a-zåäö]+)\s+(\d{4})(?:.*?(\d{1,2}):(\d{2}))?", text, re.IGNORECASE
    )
    if not match:
        return None
    month = SWEDISH_MONTHS.get(match.group(2).lower())
    if not month:
        return None
    return datetime(
        int(match.group(3)), month, int(match.group(1)),
        int(match.group(4) or 0), int(match.group(5) or 0),
    )


def safe_path_part(text: str) -> str:
    """Make one file or folder name safe: 'Andersson, Anna' -> 'Anna_Andersson'."""
    if "," in text:
        last, _, first = text.partition(",")
        text = f"{first.strip()} {last.strip()}"
    # Strip accents so the names survive SMB shares and other filesystems.
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", text)
    text = re.sub(r"_{2,}", "_", text)
    return text.strip("._-")


def plain_text_from_html(value: str) -> str:
    text = re.sub(r"<\s*br\s*/?>", "\n", value or "", flags=re.IGNORECASE)
    text = re.sub(r"</\s*p\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_lib.unescape(text)
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _hidden(content: str, name: str) -> str | None:
    match = re.search(
        rf'name="{re.escape(name)}"[^>]*value="([^"]*)"', content, re.IGNORECASE
    )
    return html_lib.unescape(match.group(1)) if match else None


class InfoMentorClient:
    """Talks to the InfoMentor hub on behalf of one account."""

    def __init__(self, session: aiohttp.ClientSession, username: str, password: str) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._headers = {"User-Agent": USER_AGENT, "Accept-Language": "sv-SE,sv;q=0.9"}
        self._ajax_headers = {
            **self._headers,
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{HUB}/",
        }
        self.authenticated = False

    # ------------------------------------------------------------------ login

    async def login(self) -> bool:
        self._session.cookie_jar.clear()

        async with self._session.get(HUB, headers=self._headers, timeout=REQUEST_TIMEOUT) as resp:
            content = await resp.text()
        oauth_token = self._extract_oauth_token(content)

        async with self._session.post(
            IM1, data={"oauth_token": oauth_token}, headers=self._headers, timeout=REQUEST_TIMEOUT
        ) as resp:
            content = await resp.text()

        payload = {
            "login_ascx$txtNotandanafn": self._username,
            "login_ascx$txtLykilord": self._password,
            "login_ascx$btnLogin": "Logga in",
            "__EVENTTARGET": "",
            "__EVENTARGUMENT": "",
        }
        for field_name in ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION"):
            value = _hidden(content, field_name)
            if value is None:
                raise InfoMentorError(f"Login form field {field_name} missing.")
            payload[field_name] = value

        async with self._session.post(IM1, data=payload, headers=self._headers, timeout=REQUEST_TIMEOUT) as resp:
            content = await resp.text()

        try:
            oauth_token = self._extract_oauth_token(content)
        except InfoMentorError as err:
            raise InfoMentorAuthError("Username or password rejected.") from err

        async with self._session.post(
            IM1, data={"oauth_token": oauth_token}, headers=self._headers, timeout=REQUEST_TIMEOUT
        ) as resp:
            await resp.text()

        async with self._session.post(
            f"{HUB}/authentication/authentication/isauthenticated/",
            headers=self._ajax_headers,
            timeout=REQUEST_TIMEOUT,
        ) as resp:
            text = (await resp.text()).strip().strip('"')

        self.authenticated = text.lower() == "true"
        if not self.authenticated:
            raise InfoMentorAuthError("Hub reports the session is not authenticated.")
        return True

    def _extract_oauth_token(self, content: str) -> str:
        match = _OAUTH_TOKEN_RE.search(content)
        if not match:
            raise InfoMentorError("No oauth_token in response.")
        return html_lib.unescape(match.group(1))

    # ------------------------------------------------------------------ pupils

    async def async_get_pupils(self) -> list[Pupil]:
        async with self._session.get(f"{HUB}/", headers=self._headers, timeout=REQUEST_TIMEOUT) as resp:
            content = await resp.text()

        matches = list(_PUPIL_ID_RE.finditer(content))
        pupils: list[Pupil] = []
        seen: set[str] = set()
        for index, match in enumerate(matches):
            pupil_id = match.group(1)
            if pupil_id in seen:
                continue
            seen.add(pupil_id)
            # Bound the name search to this pupil's block so names cannot slide
            # onto the wrong id when a block renders without a label.
            end = (
                matches[index + 1].start()
                if index + 1 < len(matches)
                else min(len(content), match.end() + 2000)
            )
            name_match = _PUPIL_TEXT_RE.search(content, match.end(), end)
            name = (
                html_lib.unescape(name_match.group(1)).strip()
                if name_match
                else f"Pupil {pupil_id}"
            )
            pupils.append(Pupil(id=pupil_id, name=name))
        return pupils

    async def switch_pupil(self, pupil_id: str) -> None:
        """Server-side session state - callers must serialise this."""
        async with self._session.get(
            f"{HUB}/Account/PupilSwitcher/SwitchPupil/{pupil_id}", headers=self._headers, timeout=REQUEST_TIMEOUT
        ) as resp:
            await resp.read()

    # --------------------------------------------------------------- endpoints

    async def get_notifications(self) -> Any:
        return await self._json(f"{HUB}/NotificationApp/NotificationApp/appData")

    async def get_news(self) -> Any:
        return await self._json(f"{HUB}/Communication/News/GetNewsList")

    async def get_attendance(self) -> Any:
        return await self._json(f"{HUB}/attendance/attendance/appData")

    async def get_time_registrations(self, day: date | None = None) -> Any:
        """Parent-registered times for the week containing day."""
        request_data = None
        if day is not None:
            request_data = {
                "date": f"{day.isoformat()}T00:00:00",
                "showNextWeekIfNoMoreSchoolDays": True,
            }
        return await self._json(
            f"{HUB}/TimeRegistration/TimeRegistration/GetTimeRegistrations/",
            json_body=request_data,
        )

    async def get_time_registration_day(self, day: date) -> Any:
        """Check-in/out plus the parent and teacher comments for one date."""
        return await self._json(
            f"{HUB}/TimeRegistration/TimeRegistration/GetComments/",
            json_body={"date": day.isoformat()},
        )

    async def save_time_registration_comment(
        self, time_registration_id: int, comment_id: int, comment: str
    ) -> Any:
        return await self._json(
            f"{HUB}/TimeRegistration/TimeRegistration/SaveComment/",
            json_body={
                "commentId": comment_id,
                "commentText": comment,
                "timeRegistrationId": time_registration_id,
            },
        )

    async def save_time_registrations(self, days: list[dict[str, Any]]) -> Any:
        return await self._json(
            f"{HUB}/TimeRegistration/TimeRegistration/SaveTimeRegistrations/",
            json_body={"days": days, "series": None},
        )

    async def get_timetable(self, start: date, end: date) -> Any:
        return await self._json(
            f"{HUB}/timetable/timetable/gettimetablelist",
            data={"UTCOffset": "-120", "start": start.isoformat(), "end": end.isoformat()},
        )

    async def get_calendar(self, start: date, end: date) -> Any:
        return await self._json(
            f"{HUB}/calendarv2/calendarv2/getentries",
            json_body={
                "startDate": start.strftime("%Y/%m/%d"),
                "endDate": end.strftime("%Y/%m/%d"),
            },
        )

    async def get_calendar_attachments(
        self, entry_id: int, title: str = "", entry_date: str = ""
    ) -> list[MediaFile]:
        items = await self._json(
            f"{HUB}/calendarv2/calendarv2/getattachments", json_body={"id": int(entry_id)}
        ) or []
        return [
            MediaFile(
                file_id=_file_id_from_url(item["url"]),
                name=item.get("title") or "attachment",
                url=item["url"],
                extension=item.get("fileType") or "",
                source="calendar",
                entry_id=int(entry_id),
                entry_title=title,
                entry_date=entry_date,
            )
            for item in items
        ]

    async def get_learnlog_entries(
        self,
        learn_log_type: int = LEARNLOG_INDIVIDUAL,
        page_number: int = 1,
        page_size: int = 20,
    ) -> list[LearnLogEntry]:
        raw = await self._json(
            f"{HUB}/learnlog/learnlog/getlearnlogs",
            params={
                "learnLogType": learn_log_type,
                "pageNumber": page_number,
                "pageSize": page_size,
            },
        ) or []
        return [_parse_learnlog_entry(item) for item in raw]

    async def download(self, url: str) -> bytes:
        """Resource URLs carry no token - they only work on the logged-in session."""
        absolute = url if url.startswith("http") else HUB + url
        async with self._session.get(absolute, headers=self._headers, timeout=DOWNLOAD_TIMEOUT) as resp:
            resp.raise_for_status()
            return await resp.read()

    # ----------------------------------------------------------------- plumbing

    async def _json(
        self,
        url: str,
        json_body: Any = None,
        data: Any = None,
        params: Any = None,
        _retry: bool = True,
    ) -> Any:
        async with self._session.post(
            url,
            json=json_body,
            data=data,
            params=params,
            headers=self._ajax_headers,
            timeout=REQUEST_TIMEOUT,
        ) as resp:
            if "HandleUnauthorizedRequest" in str(resp.url):
                raise ModuleUnavailable(f"{url} is not available for this pupil.")
            resp.raise_for_status()
            text = (await resp.text()).strip()

        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # An HTML body means the session expired; log in again and retry once.
        if _retry:
            _LOGGER.debug("Non-JSON from %s, re-authenticating", url)
            await self.login()
            return await self._json(url, json_body, data, params, _retry=False)
        raise InfoMentorError(f"{url} returned non-JSON ({len(text)} bytes).")


def _file_id_from_url(url: str) -> int:
    match = re.search(r"/Download/(\d+)", url)
    return int(match.group(1)) if match else 0


def _parse_learnlog_entry(item: dict[str, Any]) -> LearnLogEntry:
    entry_id = item["id"]
    title = (item.get("title") or "").strip()
    group_name = (item.get("groupName") or "").strip()
    modified_on = parse_swedish_datetime(item.get("lastModifiedOn") or "")
    entry_date = modified_on.date().isoformat() if modified_on else ""
    text_html = item.get("text") or ""
    return LearnLogEntry(
        id=entry_id,
        title=title,
        group_name=group_name,
        modified_on=modified_on,
        text=plain_text_from_html(text_html),
        text_html=text_html,
        comments=item.get("comments") or [],
        media=[
            MediaFile(
                file_id=media["fileId"],
                name=media.get("fileName") or str(media["fileId"]),
                url=media["fileUrl"],
                extension=media.get("fileExtension") or "",
                source="learnlog",
                entry_id=entry_id,
                entry_title=title,
                entry_date=entry_date,
                group_name=group_name,
            )
            for media in item.get("media") or []
            if (media.get("fileType") or "").lower() == "image"
        ],
    )
