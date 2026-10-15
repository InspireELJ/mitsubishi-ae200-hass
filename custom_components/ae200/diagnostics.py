"""Download the last cached controller sample without a Java UI or a new read."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from ._pymelans import cached_snapshot
from .const import DOMAIN


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    return {
        "schema": "inspire-ae200-ha-diagnostics-v1",
        "scan_interval_seconds": coordinator.update_interval.total_seconds(),
        "last_update_success": coordinator.last_update_success,
        "controller": cached_snapshot(coordinator.client),
    }
