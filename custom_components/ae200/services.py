"""Explicit, bounded diagnostic actions for a selected AE-200 controller."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from ._pymelans import MelansError, cached_snapshot
from .const import DOMAIN

SERVICE_READ_MEMBER = "read_member"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_ADDRESS = "address"


def _member_address(value: Any) -> int:
    """Accept one integer address, without truncating fractional input."""
    if isinstance(value, bool) or not (
        isinstance(value, int) or isinstance(value, str) and value.isdecimal()
    ):
        raise vol.Invalid("address must be an integer from 1 to 250")
    address = int(value)
    if not 1 <= address <= 250:
        raise vol.Invalid("address must be an integer from 1 to 250")
    return address


READ_MEMBER_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): vol.All(str, vol.Length(min=1)),
        vol.Required(ATTR_ADDRESS): _member_address,
    }
)


def async_setup_services(hass: HomeAssistant) -> None:
    """Register once for the integration, independently of entry lifecycle."""
    if hass.services.has_service(DOMAIN, SERVICE_READ_MEMBER):
        return

    async def async_read_member(call: ServiceCall) -> ServiceResponse:
        entry_id = call.data[ATTR_CONFIG_ENTRY_ID]
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            raise ServiceValidationError("Select an existing AE-200 configuration entry")
        coordinator = hass.data.get(DOMAIN, {}).get(entry_id)
        if coordinator is None:
            raise HomeAssistantError("The selected AE-200 configuration entry is not loaded")

        address = call.data[ATTR_ADDRESS]
        try:
            result = await coordinator.client.async_read_member(address)
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        except MelansError as err:
            raise HomeAssistantError("The AE-200 member read failed") from err
        if result is None:
            raise HomeAssistantError(
                "The AE-200 member read failed or is cooling down. "
                "Wait at least 15 seconds before another member read"
            )

        # Never return the raw transport result. Shared diagnostics applies the
        # same allowlist and identity redaction as the diagnostic download.
        member = cached_snapshot(coordinator.client).get("members", {}).get(str(address))
        if member is None:
            raise HomeAssistantError("The AE-200 member read did not produce a diagnostic sample")
        return {"address": address, **member}

    hass.services.async_register(
        DOMAIN,
        SERVICE_READ_MEMBER,
        async_read_member,
        schema=READ_MEMBER_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
