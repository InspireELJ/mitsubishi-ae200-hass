"""Legacy RemoCon switch with cached per-function local restrictions.

RemoCon is a reported summary. Its on state does not establish that every
local remote function is prohibited. Individual restrictions remain read-only
attributes and do not define the central controller's hardware capabilities.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import AE200Coordinator
from .entity import AE200Entity

_LOCAL_RESTRICTION_ATTRIBUTES = {
    "local_power_prohibited": "DriveItem",
    "local_mode_prohibited": "ModeItem",
    "local_temperature_prohibited": "SetTempItem",
    "local_filter_reset_prohibited": "FilterItem",
}


def _local_prohibited(summary: str | None, item: str | None) -> bool | None:
    """Apply the controller client's master/item local-prohibition rule."""
    if summary == "PERMIT" or item == "CHK_OFF":
        return False
    if summary == "PROHIBIT" and item == "CHK_ON":
        return True
    return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: AE200Coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(AE200RemoteLockSwitch(coordinator, gid) for gid in coordinator.data)


class AE200RemoteLockSwitch(AE200Entity, SwitchEntity):
    _attr_translation_key = "remote_lock"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:remote-off"

    def __init__(self, coordinator: AE200Coordinator, group_id: str) -> None:
        super().__init__(coordinator, group_id)
        self._attr_unique_id = f"{coordinator.host}_{group_id}_remote_lock"

    @property
    def is_on(self) -> bool | None:
        return self.device.local_remote_prohibited if self.device else None

    @property
    def extra_state_attributes(self) -> dict[str, bool | None]:
        if not self.available:
            return dict.fromkeys(_LOCAL_RESTRICTION_ATTRIBUTES)
        dev = self.device
        summary = dev.raw("RemoCon") if dev else None
        attributes: dict[str, bool | None] = {}
        for key, field in _LOCAL_RESTRICTION_ATTRIBUTES.items():
            value = dev.raw(field) if dev else None
            attributes[key] = _local_prohibited(summary, value)
        return attributes

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.client.set_local_remote(self._group_id, permit=False)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.client.set_local_remote(self._group_id, permit=True)
        await self.coordinator.async_request_refresh()
