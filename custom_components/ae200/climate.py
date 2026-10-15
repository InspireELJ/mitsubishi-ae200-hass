"""Climate platform for the Mitsubishi AE-200 / EW-50 integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from ._pymelans import const as mc
from .const import DOMAIN
from .coordinator import AE200Coordinator
from .entity import AE200Entity

# AE-200 mode string <-> HA HVACMode
_MODE_TO_HA = {
    mc.MODE_COOL: HVACMode.COOL,
    mc.MODE_HEAT: HVACMode.HEAT,
    mc.MODE_FAN: HVACMode.FAN_ONLY,
    mc.MODE_DRY: HVACMode.DRY,
    mc.MODE_AUTO: HVACMode.HEAT_COOL,
}
_HA_TO_MODE = {v: k for k, v in _MODE_TO_HA.items()}

_FAN_TO_HA = {
    mc.FAN_LOW: "low",
    mc.FAN_MID: "medium",
    mc.FAN_MID1: "medium_1",
    mc.FAN_MID2: "medium_2",
    mc.FAN_HIGH: "high",
    mc.FAN_AUTO: "auto",
}
_HA_TO_FAN = {v: k for k, v in _FAN_TO_HA.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: AE200Coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        AE200Climate(coordinator, gid)
        for gid, dev in coordinator.data.items()
        if dev.kind in (mc.KIND_AC, mc.KIND_ATW)
    ]
    async_add_entities(entities)


class AE200Climate(AE200Entity, ClimateEntity):
    """A single AE-200 indoor unit as an HA climate entity."""

    _attr_name = None  # use the device name
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 0.5
    _attr_hvac_modes = [
        HVACMode.OFF,
        HVACMode.COOL,
        HVACMode.HEAT,
        HVACMode.FAN_ONLY,
        HVACMode.DRY,
        HVACMode.HEAT_COOL,
    ]
    _attr_fan_modes = ["auto", "low", "medium", "medium_1", "medium_2", "high"]
    _enable_turn_on_off_backwards_compatibility = False

    def __init__(self, coordinator: AE200Coordinator, group_id: str) -> None:
        super().__init__(coordinator, group_id)
        self._attr_unique_id = f"{coordinator.host}_{group_id}_climate"
        self._attr_supported_features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.FAN_MODE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        )

    @property
    def current_temperature(self) -> float | None:
        return self.device.current_temperature if self.device else None

    @property
    def target_temperature(self) -> float | None:
        return self.device.target_temperature if self.device else None

    @property
    def hvac_mode(self) -> HVACMode | None:
        dev = self.device
        if not dev or dev.is_on is None:
            return None
        if not dev.is_on:
            return HVACMode.OFF
        return _MODE_TO_HA.get(dev.mode or "")

    @property
    def fan_mode(self) -> str | None:
        dev = self.device
        if not dev or not dev.fan_speed:
            return None
        return _FAN_TO_HA.get(dev.fan_speed)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        dev = self.device
        if not dev:
            return {}
        return {
            "group_id": dev.group_id,
            "air_direction": dev.air_direction,
            "filter_sign": dev.filter_sign,
            "error_sign": dev.error_sign,
            "local_remote_prohibited": dev.local_remote_prohibited,
            "transport": self.coordinator.client.transport_name,
            "data_quality": self.coordinator.client.group_quality(self._group_id),
            "fan_speed_raw": dev.fan_speed,
        }

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is not None:
            await self.coordinator.client.set_temperature(self._group_id, float(temp))
            await self.coordinator.async_request_refresh()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        client = self.coordinator.client
        if hvac_mode == HVACMode.OFF:
            await client.set_power(self._group_id, False)
        else:
            mode = _HA_TO_MODE.get(hvac_mode)
            if mode is None:
                return
            # Ensure the unit is on, then set the mode.
            if self.device and not self.device.is_on:
                await client.set_power(self._group_id, True)
            await client.set_mode(self._group_id, mode)
        await self.coordinator.async_request_refresh()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        speed = _HA_TO_FAN.get(fan_mode)
        if speed is not None:
            await self.coordinator.client.set_fan_speed(self._group_id, speed)
            await self.coordinator.async_request_refresh()

    async def async_turn_on(self) -> None:
        await self.coordinator.client.set_power(self._group_id, True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self) -> None:
        await self.coordinator.client.set_power(self._group_id, False)
        await self.coordinator.async_request_refresh()
