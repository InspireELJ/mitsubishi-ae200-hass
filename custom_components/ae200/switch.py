"""Switch platform: local remote-control lockout (RemoCon PROHIBIT/PERMIT).

A building-manager feature: when ``on`` the wall/handset remote is locked out
so occupants cannot override the centrally managed setpoint.
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


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: AE200Coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        AE200RemoteLockSwitch(coordinator, gid) for gid in coordinator.data
    )


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

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.client.set_local_remote(self._group_id, permit=False)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.client.set_local_remote(self._group_id, permit=True)
        await self.coordinator.async_request_refresh()
