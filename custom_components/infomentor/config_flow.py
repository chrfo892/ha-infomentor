"""Config flow for InfoMentor."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import InfoMentorAuthError, InfoMentorClient, InfoMentorError
from .const import (
    ALL_MODULES,
    CONF_AUTO_DOWNLOAD,
    CONF_DOWNLOAD_PATH,
    CONF_MODULES,
    CONF_PREP_LESSON_KEYWORDS,
    CONF_SCAN_MINUTES,
    DEFAULT_DOWNLOAD_PATH,
    DEFAULT_PREP_LESSON_KEYWORDS,
    DEFAULT_SCAN_MINUTES,
    DOMAIN,
    MIN_SCAN_MINUTES,
    MODULE_LABELS,
)

STEP_USER_SCHEMA = vol.Schema(
    {vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str}
)


class InfoMentorConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_USERNAME].lower())
            self._abort_if_unique_id_configured()

            session = async_create_clientsession(self.hass)
            client = InfoMentorClient(
                session, user_input[CONF_USERNAME], user_input[CONF_PASSWORD]
            )
            try:
                await client.login()
                pupils = await client.async_get_pupils()
            except InfoMentorAuthError:
                errors["base"] = "invalid_auth"
            except InfoMentorError:
                errors["base"] = "cannot_connect"
            else:
                if not pupils:
                    errors["base"] = "no_pupils"
                else:
                    return self.async_create_entry(
                        title=user_input[CONF_USERNAME], data=user_input
                    )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return InfoMentorOptionsFlow()


class InfoMentorOptionsFlow(OptionsFlow):
    def __init__(self) -> None:
        self._options: dict[str, Any] = {}
        self._pupils: list = []
        self._index = 0

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        coordinator = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
        self._pupils = list(coordinator.pupils) if coordinator else []

        if user_input is not None:
            self._options = {
                CONF_SCAN_MINUTES: user_input[CONF_SCAN_MINUTES],
                CONF_AUTO_DOWNLOAD: user_input[CONF_AUTO_DOWNLOAD],
                CONF_DOWNLOAD_PATH: user_input[CONF_DOWNLOAD_PATH],
                CONF_PREP_LESSON_KEYWORDS: _split_keywords(
                    user_input[CONF_PREP_LESSON_KEYWORDS]
                ),
                CONF_MODULES: dict(self.config_entry.options.get(CONF_MODULES, {})),
            }
            self._index = 0
            return await self.async_step_pupil()

        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SCAN_MINUTES,
                        default=options.get(CONF_SCAN_MINUTES, DEFAULT_SCAN_MINUTES),
                    ): vol.All(
                        vol.Coerce(int), vol.Range(min=MIN_SCAN_MINUTES, max=720)
                    ),
                    vol.Optional(
                        CONF_AUTO_DOWNLOAD,
                        default=options.get(CONF_AUTO_DOWNLOAD, False),
                    ): bool,
                    vol.Optional(
                        CONF_DOWNLOAD_PATH,
                        default=options.get(CONF_DOWNLOAD_PATH, DEFAULT_DOWNLOAD_PATH),
                    ): str,
                    vol.Optional(
                        CONF_PREP_LESSON_KEYWORDS,
                        default=", ".join(
                            options.get(
                                CONF_PREP_LESSON_KEYWORDS,
                                DEFAULT_PREP_LESSON_KEYWORDS,
                            )
                        ),
                    ): str,
                }
            ),
        )

    async def async_step_pupil(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if self._index >= len(self._pupils):
            return self.async_create_entry(data=self._options)

        pupil = self._pupils[self._index]
        if user_input is not None:
            self._options[CONF_MODULES][pupil.id] = user_input[CONF_MODULES]
            self._index += 1
            return await self.async_step_pupil()

        current = self._options[CONF_MODULES].get(pupil.id, ALL_MODULES)
        return self.async_show_form(
            step_id="pupil",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_MODULES, default=current): cv.multi_select(
                        MODULE_LABELS
                    )
                }
            ),
            description_placeholders={"pupil": pupil.name},
        )


def _split_keywords(value: str) -> list[str]:
    return [keyword.strip() for keyword in value.split(",") if keyword.strip()]
