"""Typed view over a single MELANS group's attributes.

A :class:`MelansDevice` wraps the raw ``{attr: value}`` dict the controller
returns for one group and exposes clean, typed properties. It also infers the
unit *kind* (AC / ventilation / ATW) from which attributes are present, which
is what makes the integration generic across the Mitsubishi device family.
"""

from __future__ import annotations

import math

from . import const


def _to_float(value: str | None) -> float | None:
    if value in (None, "", "*"):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _flag(value: str | None, on: str, off: str) -> bool | None:
    if value == on:
        return True
    if value == off:
        return False
    return None


class MelansDevice:
    """Read-only typed view of one group's state."""

    def __init__(self, group_id: str, name: str | None, attrs: dict[str, str]):
        self.group_id = group_id
        self.name = name or f"Group {group_id}"
        self._attrs = attrs

    # -- raw access -------------------------------------------------------
    def raw(self, key: str, default: str | None = None) -> str | None:
        val = self._attrs.get(key, default)
        return default if val in ("", "*") else val

    def update(self, attrs: dict[str, str]) -> None:
        self._attrs = attrs

    def raw_attributes(self) -> dict[str, str]:
        """Return a copy for bounded diagnostics, never a mutable cache reference."""
        return dict(self._attrs)

    # -- classification ---------------------------------------------------
    def _has_value(self, *keys: str) -> bool:
        """True only if at least one key is present AND has a real value.

        The controller frequently returns unsupported attributes as empty
        strings or the ``*`` placeholder, so key-presence alone is not enough
        to infer a unit's kind.
        """
        for k in keys:
            v = self._attrs.get(k)
            if v not in (None, "", "*"):
                return True
        return False

    def _has_nonzero(self, *keys: str) -> bool:
        """True if any key holds a real, non-zero numeric value.

        AC units echo hydronic (HWHP) fields back as ``0.0`` placeholders, so a
        genuine air-to-water unit is only distinguishable by a non-zero water
        temperature.
        """
        for k in keys:
            v = _to_float(self._attrs.get(k))
            if v is not None and v != 0.0:
                return True
        return False

    @property
    def kind(self) -> str:
        # A setpoint and/or return-air temperature => indoor AC unit. This is
        # the strongest, least ambiguous signal, so check it first.
        if self._has_value("SetTemp", "InletTemp"):
            return const.KIND_AC
        # Air-to-water/hydronic only if the water temps carry a non-zero value
        # (AC units report HWHP fields as 0.0 placeholders).
        if self._has_nonzero("InletTempHWHP", "OutletTempHWHP", "HeadTempHWHP"):
            return const.KIND_ATW
        # A Lossnay/air handler has a ventilation drive but no setpoint.
        if self._has_value("Ventilation", "VentiDrive", "VentMode", "Vent24h"):
            return const.KIND_VENTILATION
        return const.KIND_UNKNOWN

    # -- core climate -----------------------------------------------------
    @property
    def is_on(self) -> bool | None:
        return _flag(self.raw("Drive"), const.DRIVE_ON, const.DRIVE_OFF)

    @property
    def mode(self) -> str | None:
        return self.raw("Mode")

    @property
    def target_temperature(self) -> float | None:
        return _to_float(self.raw("SetTemp"))

    @property
    def current_temperature(self) -> float | None:
        t = _to_float(self.raw("InletTemp"))
        if t is None or t <= const.FAULT_TEMP_THRESHOLD:
            return None
        return t

    @property
    def fan_speed(self) -> str | None:
        return self.raw("FanSpeed")

    @property
    def air_direction(self) -> str | None:
        return self.raw("AirDirection")

    @property
    def current_humidity(self) -> float | None:
        # Zero is returned as a placeholder by AC groups without a verified
        # humidity measurement. Preserve it in diagnostics, not statistics.
        for key in ("RoomHumidity", "InletHumidity"):
            value = _to_float(self.raw(key))
            if value is not None and 0 < value <= 100:
                return value
        return None

    @property
    def has_humidity_attribute(self) -> bool:
        return self._has_value("RoomHumidity", "InletHumidity")

    # -- ventilation (Lossnay / air handler) ------------------------------
    @property
    def ventilation(self) -> str | None:
        return self.raw("Ventilation") or self.raw("VentiDrive")

    @property
    def ventilation_mode(self) -> str | None:
        return self.raw("VentMode")

    # -- ATW / hydronic ---------------------------------------------------
    @property
    def water_inlet_temp(self) -> float | None:
        return _to_float(self.raw("InletTempHWHP"))

    @property
    def water_outlet_temp(self) -> float | None:
        return _to_float(self.raw("OutletTempHWHP"))

    @property
    def outdoor_temp(self) -> float | None:
        return _to_float(self.raw("OutdoorTemp"))

    # -- diagnostics / building-manager flags -----------------------------
    @property
    def filter_sign(self) -> bool | None:
        return _flag(self.raw("FilterSign"), const.SIGN_ON, const.SIGN_OFF)

    @property
    def error_sign(self) -> bool | None:
        return _flag(self.raw("ErrorSign"), const.SIGN_ON, const.SIGN_OFF)

    @property
    def local_remote_prohibited(self) -> bool | None:
        return _flag(self.raw("RemoCon"), const.REMOCON_PROHIBIT, const.REMOCON_PERMIT)

    # -- supported-feature hints from per-item PROHIBIT flags -------------
    @property
    def can_set_mode(self) -> bool:
        return self.raw("ModeItem") != "CHK_ON"

    @property
    def can_set_temp(self) -> bool:
        return self.raw("SetTempItem") != "CHK_ON"

    @property
    def temp_limits(self) -> dict[str, float | None]:
        return {
            "cool_min": _to_float(self.raw("CoolMin")),
            "cool_max": _to_float(self.raw("CoolMax")),
            "heat_min": _to_float(self.raw("HeatMin")),
            "heat_max": _to_float(self.raw("HeatMax")),
            "auto_min": _to_float(self.raw("AutoMin")),
            "auto_max": _to_float(self.raw("AutoMax")),
        }

    def as_dict(self) -> dict[str, object]:
        return {
            "group_id": self.group_id,
            "name": self.name,
            "kind": self.kind,
            "is_on": self.is_on,
            "mode": self.mode,
            "target_temperature": self.target_temperature,
            "current_temperature": self.current_temperature,
            "fan_speed": self.fan_speed,
            "air_direction": self.air_direction,
            "current_humidity": self.current_humidity,
            "ventilation": self.ventilation,
            "ventilation_mode": self.ventilation_mode,
            "outdoor_temp": self.outdoor_temp,
            "water_inlet_temp": self.water_inlet_temp,
            "water_outlet_temp": self.water_outlet_temp,
            "filter_sign": self.filter_sign,
            "error_sign": self.error_sign,
            "local_remote_prohibited": self.local_remote_prohibited,
        }

    def __repr__(self) -> str:
        return f"<MelansDevice {self.group_id} {self.name!r} kind={self.kind} on={self.is_on}>"
