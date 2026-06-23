"""Binary sensor platform: filter and fault signs for AE-200 units."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
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
    entities: list[AE200Entity] = []
    for gid in coordinator.data:
        entities.append(AE200FilterSign(coordinator, gid))
        entities.append(AE200ErrorSign(coordinator, gid))
    async_add_entities(entities)


class AE200FilterSign(AE200Entity, BinarySensorEntity):
    _attr_translation_key = "filter_sign"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AE200Coordinator, group_id: str) -> None:
        super().__init__(coordinator, group_id)
        self._attr_unique_id = f"{coordinator.host}_{group_id}_filter_sign"

    @property
    def is_on(self) -> bool | None:
        return self.device.filter_sign if self.device else None


class AE200ErrorSign(AE200Entity, BinarySensorEntity):
    _attr_translation_key = "error_sign"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AE200Coordinator, group_id: str) -> None:
        super().__init__(coordinator, group_id)
        self._attr_unique_id = f"{coordinator.host}_{group_id}_error_sign"

    @property
    def is_on(self) -> bool | None:
        return self.device.error_sign if self.device else None
