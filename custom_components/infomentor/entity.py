"""Shared entity base tying every entity to a per-pupil device."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import Pupil
from .const import DOMAIN
from .coordinator import InfoMentorCoordinator, PupilData


class InfoMentorEntity(CoordinatorEntity[InfoMentorCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: InfoMentorCoordinator, pupil: Pupil, key: str) -> None:
        super().__init__(coordinator)
        self._pupil = pupil
        self._attr_unique_id = f"{pupil.id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, pupil.id)},
            name=pupil.name,
            manufacturer="InfoMentor",
            model="Pupil",
        )

    @property
    def pupil_data(self) -> PupilData | None:
        return (self.coordinator.data or {}).get(self._pupil.id)
