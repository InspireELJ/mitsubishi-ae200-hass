"""Sensor platform: diagnostic temperatures and humidity for AE-200 units."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from pymelans import MelansDevice
from pymelans import const as mc

from .const import DOMAIN
from .coordinator import AE200Coordinator
from .entity import AE200Entity


@dataclass(frozen=True, kw_only=True)
class AE200SensorDescription(SensorEntityDescription):
    value_fn: Callable[[MelansDevice], float | None]
    exists_fn: Callable[[MelansDevice], bool] = lambda d: True


SENSORS: tuple[AE200SensorDescription, ...] = (
    AE200SensorDescription(
        key="inlet_temp",
        translation_key="inlet_temp",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.current_temperature,
        exists_fn=lambda d: d.kind == mc.KIND_AC,
    ),
    AE200SensorDescription(
        key="outdoor_temp",
        translation_key="outdoor_temp",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.outdoor_temp,
        exists_fn=lambda d: d.outdoor_temp is not None,
    ),
    AE200SensorDescription(
        key="humidity",
        translation_key="humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.current_humidity,
        exists_fn=lambda d: d.current_humidity is not None,
    ),
    AE200SensorDescription(
        key="water_outlet_temp",
        translation_key="water_outlet_temp",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.water_outlet_temp,
        exists_fn=lambda d: d.kind == mc.KIND_ATW,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: AE200Coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        AE200Sensor(coordinator, gid, desc)
        for gid, dev in coordinator.data.items()
        for desc in SENSORS
        if desc.exists_fn(dev)
    ]
    async_add_entities(entities)


class AE200Sensor(AE200Entity, SensorEntity):
    entity_description: AE200SensorDescription

    def __init__(
        self,
        coordinator: AE200Coordinator,
        group_id: str,
        description: AE200SensorDescription,
    ) -> None:
        super().__init__(coordinator, group_id)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.host}_{group_id}_{description.key}"

    @property
    def native_value(self) -> float | None:
        dev = self.device
        return self.entity_description.value_fn(dev) if dev else None
