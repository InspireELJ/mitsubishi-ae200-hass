"""Sensor platform: diagnostic temperatures and humidity for AE-200 units."""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

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

from ._pymelans import MelansDevice
from ._pymelans import const as mc
from .const import DOMAIN
from .coordinator import AE200Coordinator
from .entity import AE200ControllerEntity, AE200Entity

_COMMAND_ERROR_TYPES = (
    "MelansError", "MelansConnectionError", "MelansCommandUncertainError",
    "MelansProtocolError", "MelansAuthError", "CancelledError",
)


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
        exists_fn=lambda d: d.has_humidity_attribute,
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
    async_add_entities(
        [
            AE200ControllerStatus(coordinator),
            AE200LastSuccessfulRead(coordinator),
            AE200LastCommandStatus(coordinator),
        ]
    )


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


class AE200ControllerStatus(AE200ControllerEntity, SensorEntity):
    _attr_translation_key = "controller_status"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["unknown", "online", "degraded", "offline"]

    def __init__(self, coordinator: AE200Coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.host}_controller_status"

    @property
    def native_value(self) -> str:
        return self.coordinator.client.health

    @property
    def extra_state_attributes(self) -> dict:
        attributes = {
            "transport": self.coordinator.client.transport_name,
            "failed_groups": sorted(self.coordinator.client.failed_groups, key=int),
        }
        metadata = self.coordinator.client.controller_metadata
        if metadata is not None:
            attributes.update(
                {
                    key: value
                    for key, value in (
                        ("model", metadata.model),
                        ("version", metadata.version),
                        ("version_if", metadata.version_if),
                    )
                    if value
                }
            )
        return attributes


class AE200LastSuccessfulRead(AE200ControllerEntity, SensorEntity):
    _attr_translation_key = "last_successful_read"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: AE200Coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.host}_last_successful_read"

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.client.last_success


class AE200LastCommandStatus(AE200ControllerEntity, SensorEntity):
    """Report acknowledgement/readback progress without sending a request."""

    _attr_translation_key = "last_command_status"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [
        "unknown",
        "pending",
        "accepted_unconfirmed",
        "not_yet_confirmed",
        "confirmed",
        "failed",
        "outcome_unknown",
    ]

    def __init__(self, coordinator: AE200Coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.host}_last_command_status"

    @property
    def native_value(self) -> str:
        command = self.coordinator.client.last_command
        status = command.get("status") if isinstance(command, dict) else None
        return status if status in self._attr_options else "unknown"

    @property
    def extra_state_attributes(self) -> dict:
        """Publish checked command fields and bounded errors, never raw messages."""
        command = self.coordinator.client.last_command
        if not isinstance(command, dict):
            return {}
        result = {}
        if command.get("status") in ("failed", "outcome_unknown"):
            error_type = command.get("error_type")
            if isinstance(error_type, str) and error_type in _COMMAND_ERROR_TYPES:
                result["error_type"] = error_type
                code = command.get("error_code")
                if "error_code" in command and (
                    code is None
                    or isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,32}", code)
                ):
                    result["error_code"] = code
        group_id = command.get("group_id")
        if (
            isinstance(group_id, str)
            and len(group_id) <= 2
            and group_id.isdecimal()
            and 1 <= int(group_id) <= mc.MAX_GROUPS
        ):
            result["group_id"] = group_id
        timestamp = command.get("at")
        if isinstance(timestamp, str):
            try:
                parsed = datetime.fromisoformat(timestamp)
            except ValueError:
                pass
            else:
                if parsed.tzinfo is not None:
                    result["at"] = parsed.isoformat()

        expected = command.get("attributes")
        if not isinstance(expected, dict):
            return result
        values = {
            "Drive": (mc.DRIVE_ON, mc.DRIVE_OFF),
            "Mode": mc.ALL_MODES,
            "FanSpeed": mc.ALL_FAN_SPEEDS,
            "AirDirection": mc.AIR_DIR_VALUES,
            "RemoCon": (mc.REMOCON_PERMIT, mc.REMOCON_PROHIBIT),
            "DriveItem": ("CHK_ON", "CHK_OFF"),
            "ModeItem": ("CHK_ON", "CHK_OFF"),
            "SetTempItem": ("CHK_ON", "CHK_OFF"),
            "FilterItem": ("CHK_ON", "CHK_OFF"),
        }
        safe = {
            key: expected[key]
            for key, options in values.items()
            if expected.get(key) in options
        }
        temperature = expected.get("SetTemp")
        if isinstance(temperature, str):
            try:
                number = float(temperature)
            except ValueError:
                pass
            else:
                if math.isfinite(number):
                    safe["SetTemp"] = f"{number:g}"
        if safe:
            result["expected_attributes"] = safe
        return result
