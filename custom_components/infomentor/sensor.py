"""InfoMentor sensors."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    MODULE_ATTENDANCE,
    MODULE_LEARNLOG,
    MODULE_NOTIFICATIONS,
    MODULE_TIMEREGISTRATION,
    MODULE_TIMETABLE,
)
from .coordinator import InfoMentorCoordinator
from .entity import InfoMentorEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: InfoMentorCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = []
    for pupil in coordinator.pupils:
        enabled = coordinator.modules_for(pupil.id)
        if MODULE_TIMETABLE in enabled:
            entities.append(NextLessonSensor(coordinator, pupil))
        if MODULE_NOTIFICATIONS in enabled:
            entities.append(UnreadNotificationsSensor(coordinator, pupil))
        if MODULE_LEARNLOG in enabled:
            entities.append(LatestLearnLogSensor(coordinator, pupil))
        if MODULE_ATTENDANCE in enabled:
            entities.append(AbsenceSensor(coordinator, pupil))
        if MODULE_TIMEREGISTRATION in enabled:
            entities.append(ScheduledTimeSensor(coordinator, pupil))
    async_add_entities(entities)


def _as_local(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = dt_util.parse_datetime(value)
    return dt_util.as_local(parsed) if parsed else None


class NextLessonSensor(InfoMentorEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator, pupil) -> None:
        super().__init__(coordinator, pupil, "next_lesson")
        self._attr_name = "Next lesson"

    def _next(self) -> dict[str, Any] | None:
        data = self.pupil_data
        if not data:
            return None
        now = dt_util.now()
        upcoming = [
            lesson for lesson in data.timetable
            if (start := _as_local(lesson.get("start"))) and start > now
        ]
        upcoming.sort(key=lambda lesson: _as_local(lesson["start"]))
        return upcoming[0] if upcoming else None

    @property
    def native_value(self) -> datetime | None:
        lesson = self._next()
        return _as_local(lesson.get("start")) if lesson else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        lesson = self._next()
        if not lesson:
            return {}
        notes = lesson.get("notes") or {}
        return {
            "title": lesson.get("title"),
            "start_time": lesson.get("startTime"),
            "end_time": lesson.get("endTime"),
            "room": notes.get("roomInfo"),
            "teacher": notes.get("tutors"),
        }


class UnreadNotificationsSensor(InfoMentorEntity, SensorEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, pupil) -> None:
        super().__init__(coordinator, pupil, "unread_notifications")
        self._attr_name = "Unread notifications"

    def _unread(self) -> list[dict[str, Any]]:
        data = self.pupil_data
        if not data:
            return []
        return [
            item for item in data.notifications
            if (item.get("state") or "").lower() != "read"
        ]

    @property
    def native_value(self) -> int | None:
        return len(self._unread()) if self.pupil_data else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        unread = self._unread()
        return {
            "notifications": [
                {
                    "id": item.get("id"),
                    "title": item.get("title"),
                    "subtitle": item.get("subTitle"),
                    "type": item.get("appType"),
                    "date": item.get("dateSent"),
                    # Relative hub link, e.g. "#/learnlog".
                    "url": item.get("url"),
                }
                for item in unread[:20]
            ],
            "titles": [item.get("title") for item in unread[:20]],
        }


class LatestLearnLogSensor(InfoMentorEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator, pupil) -> None:
        super().__init__(coordinator, pupil, "latest_learnlog")
        self._attr_name = "Latest learnlog post"

    def _latest(self):
        data = self.pupil_data
        if not data or not data.learnlog:
            return None
        dated = [entry for entry in data.learnlog if entry.modified_on]
        return max(dated, key=lambda entry: entry.modified_on) if dated else None

    @property
    def native_value(self) -> datetime | None:
        entry = self._latest()
        return dt_util.as_local(entry.modified_on) if entry else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        entry = self._latest()
        if not entry:
            return {}
        return {
            "title": entry.title,
            "group": entry.group_name or "individual",
            "image_count": len(entry.media),
            "files": [
                {"file_id": media.file_id, "filename": media.filename}
                for media in entry.media
            ],
        }


class AbsenceSensor(InfoMentorEntity, SensorEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, pupil) -> None:
        super().__init__(coordinator, pupil, "registered_absences")
        self._attr_name = "Registered absences"

    def _items(self) -> list[dict[str, Any]]:
        data = self.pupil_data
        if not data or not data.attendance:
            return []
        return (data.attendance.get("pagedList") or {}).get("items") or []

    @property
    def native_value(self) -> int:
        return len(self._items())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"items": self._items()[:20]}


class ScheduledTimeSensor(InfoMentorEntity, SensorEntity):
    def __init__(self, coordinator, pupil) -> None:
        super().__init__(coordinator, pupil, "scheduled_time")
        self._attr_name = "Scheduled time today"

    def _today(self) -> dict[str, Any] | None:
        data = self.pupil_data
        if not data:
            return None
        today = dt_util.now().date().isoformat()
        for day in data.time_registration_week:
            if (day.get("date") or "")[:10] == today:
                return day
        return None

    @property
    def native_value(self) -> str | None:
        day = self._today()
        if not day:
            return None
        if day.get("onLeave"):
            return "Ledig"
        if day.get("isSchoolClosed"):
            return "Stängt"
        start = (day.get("startDateTime") or "")[11:16]
        end = (day.get("endDateTime") or "")[11:16]
        return f"{start}-{end}" if start and end else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        day = self._today() or {}
        return {
            "on_leave": day.get("onLeave"),
            "school_closed": day.get("isSchoolClosed"),
            "locked": day.get("isLocked"),
            "can_edit": day.get("canEdit"),
            "school_opening_time": (day.get("schoolOpeningTime") or "")[11:16] or None,
            "school_closing_time": (day.get("schoolClosingTime") or "")[11:16] or None,
        }
