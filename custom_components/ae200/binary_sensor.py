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
    for gid, device in coordinator.data.items():
        entities.append(AE200FilterSign(coordinator, gid))
        entities.append(AE200ErrorSign(coordinator, gid))
        entities.append(AE200CommunicationProblem(coordinator, gid))
        if device.raw("Schedule") in ("ON", "OFF"):
            entities.append(AE200ScheduleOn(coordinator, gid))
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


class AE200CommunicationProblem(AE200Entity, BinarySensorEntity):
    _attr_translation_key = "communication_problem"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AE200Coordinator, group_id: str) -> None:
        super().__init__(coordinator, group_id)
        self._attr_unique_id = f"{coordinator.host}_{group_id}_communication_problem"

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool | None:
        if self._group_id not in self.coordinator.client.group_last_success:
            return None
        return not self.coordinator.client.is_group_available(self._group_id)


class AE200ScheduleOn(AE200Entity, BinarySensorEntity):
    """Preserve the controller Schedule flag, distinct from ScheduleAvail."""

    _attr_translation_key = "schedule_on"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: AE200Coordinator, group_id: str) -> None:
        super().__init__(coordinator, group_id)
        self._attr_unique_id = f"{coordinator.host}_{group_id}_schedule_on"

    @property
    def is_on(self) -> bool | None:
        value = self.device.raw("Schedule") if self.device else None
        if value == "ON":
            return True
        if value == "OFF":
            return False
        return None

    @property
    def extra_state_attributes(self) -> dict[str, str | bool | None]:
        dev = self.device
        raw = dev.raw_attributes() if dev else {}
        flag = raw.get("Schedule")
        available = raw.get("ScheduleAvail")
        # Retain safe raw evidence even during an outage. Typed availability
        # requires the current group sample, rather than its historical flag.
        tokens = ("ON", "OFF", "*", "")
        return {
            "schedule_available": (
                available == "ON" if self.available and available in ("ON", "OFF") else None
            ),
            "raw_schedule_flag": flag if flag in tokens else None,
            "raw_schedule_available": available if available in tokens else None,
        }
