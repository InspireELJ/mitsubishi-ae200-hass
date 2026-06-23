"""Config flow for the Mitsubishi AE-200 / EW-50 integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
import homeassistant.helpers.config_validation as cv

from pymelans import MelansClient, MelansError

from .const import (
    CONF_PREFER_WEBSOCKET,
    CONF_SCAN_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)


class AE200ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Mitsubishi AE-200."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            await self.async_set_unique_id(host)
            self._abort_if_unique_id_configured()

            client = MelansClient(
                host,
                port=user_input.get(CONF_PORT, DEFAULT_PORT),
                prefer_websocket=user_input.get(CONF_PREFER_WEBSOCKET, True),
            )
            try:
                groups = await client.discover()
            except MelansError:
                errors["base"] = "cannot_connect"
            else:
                if not groups:
                    errors["base"] = "no_groups"
                else:
                    title = f"AE-200 ({host})"
                    return self.async_create_entry(title=title, data=user_input)
            finally:
                await client.close()

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): cv.string,
                vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
                vol.Optional(CONF_PREFER_WEBSOCKET, default=True): cv.boolean,
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return AE200OptionsFlow(config_entry)


class AE200OptionsFlow(OptionsFlow):
    """Options: tune the poll interval (floored for controller safety)."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self.config_entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
        )
        schema = vol.Schema(
            {
                vol.Optional(CONF_SCAN_INTERVAL, default=current): vol.All(
                    vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL, max=600)
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
