"""InfoMentor calendar entity combining timetable and calendar entries."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN, MODULE_CALENDAR, MODULE_TIMETABLE
from .coordinator import InfoMentorCoordinator
from .entity import InfoMentorEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: InfoMentorCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        InfoMentorCalendar(coordinator, pupil)
        for pupil in coordinator.pupils
        if {MODULE_TIMETABLE, MODULE_CALENDAR} & set(coordinator.modules_for(pupil.id))
    )


class InfoMentorCalendar(InfoMentorEntity, CalendarEntity):
    def __init__(self, coordinator, pupil) -> None:
        super().__init__(coordinator, pupil, "schedule")
        self._attr_name = "Schedule"

    def _events(self) -> list[CalendarEvent]:
        data = self.pupil_data
        if not data:
            return []

        events: list[CalendarEvent] = []
        for lesson in data.timetable:
            start = dt_util.parse_datetime(lesson.get("start") or "")
            end = dt_util.parse_datetime(lesson.get("end") or "")
            if not start or not end:
                continue
            notes = lesson.get("notes") or {}
            events.append(
                CalendarEvent(
                    start=dt_util.as_local(start),
                    end=dt_util.as_local(end),
                    summary=lesson.get("title") or "Lesson",
                    location=notes.get("roomInfo") or None,
                    description=notes.get("tutors") or None,
                )
            )

        for entry in data.calendar:
            start_date = dt_util.parse_date(entry.get("startDate") or "")
            end_date = dt_util.parse_date(entry.get("endDate") or "")
            if not start_date:
                continue
            if entry.get("isAllDayEvent"):
                events.append(
                    CalendarEvent(
                        start=start_date,
                        end=(end_date or start_date) + timedelta(days=1),
                        summary=entry.get("title") or "",
                        description=entry.get("description") or None,
                    )
                )
                continue
            start = dt_util.parse_datetime(entry.get("startDateFull") or "")
            end = dt_util.parse_datetime(entry.get("endDateFull") or "")
            if start and end:
                events.append(
                    CalendarEvent(
                        start=dt_util.as_local(start),
                        end=dt_util.as_local(end),
                        summary=entry.get("title") or "",
                        description=entry.get("description") or None,
                    )
                )

        events.sort(key=lambda event: _sort_key(event.start))
        return events

    @property
    def event(self) -> CalendarEvent | None:
        now = dt_util.now()
        for event in self._events():
            if _sort_key(event.end) > now:
                return event
        return None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        return [
            event
            for event in self._events()
            if _sort_key(event.end) > start_date and _sort_key(event.start) < end_date
        ]


def _sort_key(value) -> datetime:
    if isinstance(value, datetime):
        return value
    return dt_util.start_of_local_day(value)
