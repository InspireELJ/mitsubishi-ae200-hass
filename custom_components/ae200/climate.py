"""Climate platform for the Mitsubishi AE-200 / EW-50 integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
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

# MELANS vane positions are not angles or separate horizontal swing controls.
_SWING_TO_HA = {
    "HORIZONTAL": "fixed_horizontal",
    "MID0": "mid_0",
    "MID1": "mid_1",
    "MID2": "mid_2",
    "VERTICAL": "fixed_vertical",
    "SWING": "swing",
    "AUTO": "auto",
}
_HA_TO_SWING = {v: k for k, v in _SWING_TO_HA.items()}


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

    def _temperature_envelope(self) -> tuple[float, float]:
        """Cover selectable modes before HA validates a combined service call.

        HA checks these entity-wide limits before passing the requested mode
        to async_set_temperature. The client then enforces that mode's exact
        limits before sending the combined command.
        """
        fallback = (super().min_temp, super().max_temp)
        dev = self.device
        if dev is None:
            return fallback
        ranges = [
            dev.temperature_range(_HA_TO_MODE[mode]) or fallback
            for mode in self.hvac_modes
            if mode in (HVACMode.COOL, HVACMode.HEAT, HVACMode.DRY, HVACMode.HEAT_COOL)
        ]
        if not ranges:
            return fallback
        return min(bounds[0] for bounds in ranges), max(bounds[1] for bounds in ranges)

    @property
    def min_temp(self) -> float:
        return self._temperature_envelope()[0]

    @property
    def max_temp(self) -> float:
        return self._temperature_envelope()[1]

    @property
    def target_temperature_step(self) -> float:
        step = self.device.temperature_step if self.device else None
        return step if step is not None else self._attr_target_temperature_step

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
    def fan_modes(self) -> list[str]:
        speeds = self.device.supported_fan_speeds if self.device else None
        if speeds is None:
            return list(self._attr_fan_modes)
        return [_FAN_TO_HA[speed] for speed in speeds if speed in _FAN_TO_HA]

    @property
    def hvac_modes(self) -> list[HVACMode]:
        modes = self.device.supported_modes if self.device else None
        if modes is None:
            return list(self._attr_hvac_modes)
        return [HVACMode.OFF, *(_MODE_TO_HA[mode] for mode in modes if mode in _MODE_TO_HA)]

    @property
    def supported_features(self) -> ClimateEntityFeature:
        features = self._attr_supported_features
        if self.device and self.device.capability_attributes.get("GroupType") == "NEW":
            # NEW groups require separate setpoint fields. The client also
            # guards direct temperature writes until that protocol is verified.
            features &= ~ClimateEntityFeature.TARGET_TEMPERATURE
        if self.device and self.device.supported_fan_speeds == []:
            features &= ~ClimateEntityFeature.FAN_MODE
        if self.swing_modes:
            features |= ClimateEntityFeature.SWING_MODE
        return features

    @property
    def swing_mode(self) -> str | None:
        dev = self.device
        return _SWING_TO_HA.get(dev.air_direction) if dev else None

    @property
    def swing_modes(self) -> list[str] | None:
        directions = self.device.supported_air_directions if self.device else None
        if not directions:
            return None
        return [_SWING_TO_HA[direction] for direction in directions if direction in _SWING_TO_HA]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        dev = self.device
        if not dev:
            return {"filter_sign": None, "error_sign": None, "local_remote_prohibited": None}
        limits = dev.temperature_range()
        quality = self.coordinator.client.group_quality(self._group_id)
        fresh = self.available and quality == "fresh"
        return {
            "group_id": dev.group_id,
            "air_direction": dev.air_direction,
            "filter_sign": dev.filter_sign if fresh else None,
            "error_sign": dev.error_sign if fresh else None,
            "local_remote_prohibited": dev.local_remote_prohibited if fresh else None,
            "transport": self.coordinator.client.transport_name,
            "data_quality": quality,
            "fan_speed_raw": dev.fan_speed,
            "current_mode_min_temp_celsius": limits[0] if limits else None,
            "current_mode_max_temp_celsius": limits[1] if limits else None,
        }

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temp = kwargs.get(ATTR_TEMPERATURE)
        hvac_mode = kwargs.get(ATTR_HVAC_MODE)
        if temp is None and hvac_mode is None:
            raise ValueError("temperature or HVAC mode is required")
        temperature = float(temp) if temp is not None else None
        client = self.coordinator.client
        if hvac_mode is None:
            await client.set_temperature(self._group_id, temperature)
        elif hvac_mode == HVACMode.OFF:
            await client.set_climate(self._group_id, power=False, temperature=temperature)
        else:
            mode = _HA_TO_MODE.get(hvac_mode)
            if mode is None or hvac_mode not in self.hvac_modes:
                raise ValueError(f"unsupported HVAC mode: {hvac_mode}")
            await client.set_climate(
                self._group_id, mode=mode, power=True, temperature=temperature
            )
        await self.coordinator.async_request_refresh()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        client = self.coordinator.client
        if hvac_mode == HVACMode.OFF:
            await client.set_power(self._group_id, False)
        else:
            mode = _HA_TO_MODE.get(hvac_mode)
            if mode is None or hvac_mode not in self.hvac_modes:
                raise ValueError(f"unsupported HVAC mode: {hvac_mode}")
            # Set mode and power together, avoiding a start in the old mode if
            # the controller rejects the requested mode afterwards.
            await client.set_climate(self._group_id, mode=mode, power=True)
        await self.coordinator.async_request_refresh()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        speed = _HA_TO_FAN.get(fan_mode)
        if speed is None or fan_mode not in self.fan_modes:
            raise ValueError(f"unsupported fan mode: {fan_mode}")
        await self.coordinator.client.set_fan_speed(self._group_id, speed)
        await self.coordinator.async_request_refresh()

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        direction = _HA_TO_SWING.get(swing_mode)
        if direction is None or swing_mode not in (self.swing_modes or []):
            raise ValueError(f"unsupported swing mode: {swing_mode}")
        await self.coordinator.client.set_air_direction(self._group_id, direction)
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self) -> None:
        await self.coordinator.client.set_power(self._group_id, True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self) -> None:
        await self.coordinator.client.set_power(self._group_id, False)
        await self.coordinator.async_request_refresh()
