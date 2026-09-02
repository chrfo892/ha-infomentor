"""The InfoMentor integration."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import InfoMentorAuthError, InfoMentorClient, InfoMentorError
from .const import (
    ATTR_COMMENT,
    ATTR_DATE,
    ATTR_DEVICE_ID,
    ATTR_END_DATE,
    ATTR_FILE_ID,
    ATTR_FILENAME,
    ATTR_LIMIT,
    ATTR_PATH,
    ATTR_PUPIL_ID,
    ATTR_SOURCES,
    ATTR_START_DATE,
    CONF_AUTO_DOWNLOAD,
    CONF_DOWNLOAD_PATH,
    CONF_MODULES,
    CONF_SCAN_MINUTES,
    DEFAULT_DOWNLOAD_PATH,
    DEFAULT_SCAN_MINUTES,
    DOMAIN,
    PLATFORMS,
    SERVICE_DOWNLOAD_BACKLOG,
    SERVICE_DOWNLOAD_FILE,
    SERVICE_SET_TIME_REGISTRATION_COMMENT,
    SOURCE_LETTERS,
    SOURCE_PHOTOS,
)
from .coordinator import InfoMentorCoordinator

_LOGGER = logging.getLogger(__name__)

DOWNLOAD_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_FILE_ID): vol.Coerce(int),
        vol.Optional(ATTR_PATH): cv.string,
        vol.Optional(ATTR_FILENAME): cv.string,
    }
)

BACKLOG_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_START_DATE): cv.date,
        vol.Optional(ATTR_END_DATE): cv.date,
        vol.Optional(ATTR_DEVICE_ID): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(ATTR_PUPIL_ID): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(ATTR_SOURCES, default=[SOURCE_PHOTOS, SOURCE_LETTERS]): vol.All(
            cv.ensure_list, [vol.In([SOURCE_PHOTOS, SOURCE_LETTERS])]
        ),
        vol.Optional(ATTR_PATH): cv.string,
        vol.Optional(ATTR_LIMIT): vol.All(vol.Coerce(int), vol.Range(min=1)),
    }
)

COMMENT_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(ATTR_DATE): cv.date,
        vol.Required(ATTR_COMMENT): vol.All(cv.string, vol.Length(min=1)),
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # A dedicated session keeps the InfoMentor cookies out of the shared jar.
    session = async_create_clientsession(hass)
    client = InfoMentorClient(session, entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])

    try:
        await client.login()
        pupils = await client.async_get_pupils()
    except InfoMentorAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except InfoMentorError as err:
        raise HomeAssistantError(f"Could not connect to InfoMentor: {err}") from err

    minutes = entry.options.get(CONF_SCAN_MINUTES, DEFAULT_SCAN_MINUTES)
    default_download_path = entry.options.get(CONF_DOWNLOAD_PATH, DEFAULT_DOWNLOAD_PATH)
    download_path = (
        default_download_path
        if entry.options.get(CONF_AUTO_DOWNLOAD)
        else None
    )
    coordinator = InfoMentorCoordinator(
        hass,
        client,
        pupils,
        timedelta(minutes=minutes),
        entry.options.get(CONF_MODULES),
        download_path,
        default_download_path,
    )
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    _async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_DOWNLOAD_FILE)
            hass.services.async_remove(DOMAIN, SERVICE_DOWNLOAD_BACKLOG)
            hass.services.async_remove(DOMAIN, SERVICE_SET_TIME_REGISTRATION_COMMENT)
    return unloaded


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _pupil_ids_from_devices(hass: HomeAssistant, device_ids: list[str]) -> set[str]:
    registry = dr.async_get(hass)
    pupil_ids: set[str] = set()
    for device_id in device_ids:
        device = registry.async_get(device_id)
        if device is None:
            continue
        pupil_ids |= {
            identifier for domain, identifier in device.identifiers if domain == DOMAIN
        }
    return pupil_ids


def _async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_DOWNLOAD_FILE):
        return

    async def async_download_file(call: ServiceCall) -> None:
        file_id = call.data[ATTR_FILE_ID]
        for coordinator in hass.data[DOMAIN].values():
            found = coordinator.find_media(file_id)
            if found is None:
                continue
            pupil, media = found

            saved = await coordinator.async_download_media(
                media,
                pupil,
                call.data.get(ATTR_PATH) or coordinator.default_download_path,
                call.data.get(ATTR_FILENAME),
            )
            if saved is None:
                raise HomeAssistantError(f"Could not save InfoMentor file {file_id}.")
            return

        raise HomeAssistantError(f"No known InfoMentor file with id {file_id}.")

    hass.services.async_register(
        DOMAIN, SERVICE_DOWNLOAD_FILE, async_download_file, schema=DOWNLOAD_SCHEMA
    )

    async def async_download_backlog(call: ServiceCall) -> ServiceResponse:
        start = call.data[ATTR_START_DATE]
        end = call.data.get(ATTR_END_DATE) or date.today()
        if end < start:
            raise HomeAssistantError("end_date must not be before start_date.")

        wanted = {str(pupil_id).strip() for pupil_id in call.data.get(ATTR_PUPIL_ID, [])}
        wanted |= _pupil_ids_from_devices(hass, call.data.get(ATTR_DEVICE_ID, []))

        path = call.data.get(ATTR_PATH)
        totals = {"downloaded": 0, "failed": 0}
        matched = 0
        known: list[str] = []

        for coordinator in hass.data[DOMAIN].values():
            known += [f"{p.id} ({p.name})" for p in coordinator.pupils]
            pupils = [
                pupil
                for pupil in coordinator.pupils
                if not wanted or pupil.id in wanted
            ]
            if not pupils:
                continue
            matched += len(pupils)
            result = await coordinator.async_download_backlog(
                pupils,
                start,
                end,
                call.data[ATTR_SOURCES],
                path,
                call.data.get(ATTR_LIMIT),
            )
            totals["downloaded"] += result["downloaded"]
            totals["failed"] += result["failed"]

        if wanted and not matched:
            raise HomeAssistantError(
                f"No pupil matched {sorted(wanted)}. Known pupils: {', '.join(known)}"
            )

        return totals

    hass.services.async_register(
        DOMAIN,
        SERVICE_DOWNLOAD_BACKLOG,
        async_download_backlog,
        schema=BACKLOG_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )

    async def async_set_time_registration_comment(call: ServiceCall) -> ServiceResponse:
        pupil_ids = _pupil_ids_from_devices(hass, call.data[ATTR_DEVICE_ID])
        if not pupil_ids:
            raise HomeAssistantError("No InfoMentor pupil device selected.")

        day = call.data.get(ATTR_DATE) or date.today()
        comment = call.data[ATTR_COMMENT]
        results: dict[str, Any] = {}
        known: list[str] = []

        for coordinator in hass.data[DOMAIN].values():
            known += [f"{p.id} ({p.name})" for p in coordinator.pupils]
            for pupil in coordinator.pupils:
                if pupil.id not in pupil_ids:
                    continue
                response = await coordinator.async_save_time_registration_comment(
                    pupil, day, comment
                )
                results[pupil.id] = {
                    "pupil_name": pupil.name,
                    "success": bool(response.get("success")),
                    "response": response,
                }

        if not results:
            raise HomeAssistantError(
                f"No selected devices matched InfoMentor pupils. Known pupils: {', '.join(known)}"
            )
        return results

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_TIME_REGISTRATION_COMMENT,
        async_set_time_registration_comment,
        schema=COMMENT_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
