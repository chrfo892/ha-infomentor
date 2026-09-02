"""InfoMentor binary sensors."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MODULE_TIMEREGISTRATION
from .coordinator import InfoMentorCoordinator
from .entity import InfoMentorEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: InfoMentorCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[BinarySensorEntity] = []
    for pupil in coordinator.pupils:
        if MODULE_TIMEREGISTRATION in coordinator.modules_for(pupil.id):
            entities.append(PickupCommentMissingSensor(coordinator, pupil))
            entities.append(CheckedInSensor(coordinator, pupil))
    async_add_entities(entities)


class _TimeRegistrationEntity(InfoMentorEntity):
    @property
    def today(self) -> dict[str, Any] | None:
        data = self.pupil_data
        return data.time_registration_today if data else None


class PickupCommentMissingSensor(_TimeRegistrationEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator, pupil) -> None:
        super().__init__(coordinator, pupil, "pickup_comment_missing")
        self._attr_name = "Pickup comment missing"

    @property
    def available(self) -> bool:
        return super().available and self.today is not None

    @property
    def is_on(self) -> bool | None:
        today = self.today
        if today is None:
            return None
        return not (today.get("userComment") or "").strip()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        today = self.today or {}
        return {
            "comment": today.get("userComment"),
            "comment_by": today.get("userName"),
            "teacher_comment": today.get("teacherComment"),
            "teacher_name": today.get("teacherName"),
            "can_edit_comment": today.get("canEditComment"),
        }


class CheckedInSensor(_TimeRegistrationEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PRESENCE

    def __init__(self, coordinator, pupil) -> None:
        super().__init__(coordinator, pupil, "checked_in")
        self._attr_name = "Checked in"

    @property
    def available(self) -> bool:
        return super().available and self.today is not None

    @property
    def is_on(self) -> bool | None:
        today = self.today
        if today is None:
            return None
        # Present means checked in and not yet checked out again.
        return bool(today.get("checkedIn")) and not today.get("checkedOut")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        today = self.today or {}
        return {
            "checked_in": today.get("checkedIn"),
            "checked_out": today.get("checkedOut"),
            "checked_in_by": today.get("checkInUserDisplayName"),
            "checked_out_by": today.get("checkOutUserDisplayName"),
        }
