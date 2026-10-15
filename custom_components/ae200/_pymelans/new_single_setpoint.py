"""Pure planning and typed reads for a narrow NEW single-setpoint IC profile.

This static-protocol implementation performs no I/O and enables no public
write path. It cannot establish membership, freshness, stop context or actual
NEW hardware support. The first profile requires complete reported evidence;
unknown values never fall back to OLD fields, UI defaults or another family.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_FLOOR, Context, Decimal, localcontext

from .topology import _identifier

_IDENTITY = {
    "Model": "IC", "SubModel": "", "IcKind": "NORMAL",
    "GroupType": "NEW", "AutoModeSWEx": "ONE_SETPOINT",
}
_OPTIONAL_MODES = (("DRY", "DryModeSW"), ("FAN", "FanModeSW"))
_MODE_FIELDS = {"COOL": "SetTemp1", "DRY": "SetTemp1", "HEAT": "SetTemp2", "AUTO": "SetTemp3"}
_MODE_RANGES = {"COOL": 1, "DRY": 1, "HEAT": 3, "AUTO": 2}
_CAPABILITY_FIELDS = (
    *_IDENTITY, "DryModeSW", "FanModeSW", "TempDetailC",
    *(key for index in (1, 2, 3) for key in (f"TempRange1Low{index}", f"TempRange1High{index}")),
)
_DECIMAL_TEXT = re.compile(r"[0-9]+(?:\.[0-9]+)?")
_MIN_TEMPERATURE = Decimal(0)
_MAX_TEMPERATURE = Decimal(90)


@dataclass(frozen=True)
class NewSingleSetpointProfile:
    supported_modes: tuple[str, ...]
    step: Decimal
    _ranges: tuple[tuple[int, Decimal, Decimal], ...] = field(repr=False)
    _attributes: tuple[tuple[str, str], ...] = field(repr=False)

    def temperature_field(self, mode: str) -> str | None:
        """Return the active register for a supported temperature mode."""
        return _MODE_FIELDS.get(mode) if type(mode) is str and mode in self.supported_modes else None

    def temperature_range(self, mode: str) -> tuple[Decimal, Decimal] | None:
        """Return inward step-aligned factory bounds, never local lockouts."""
        if type(mode) is not str or mode not in self.supported_modes or mode not in _MODE_RANGES:
            return None
        index = _MODE_RANGES[mode]
        return next((low, high) for key, low, high in self._ranges if key == index)

    def raw_attributes(self) -> dict[str, str]:
        return dict(self._attributes)


@dataclass(frozen=True)
class NewSingleSetpointPlan:
    group_id: str
    target_mode: str | None
    selected_temperature_field: str | None
    requested_celsius: Decimal | None
    _attributes: tuple[tuple[str, str], ...] = field(repr=False)

    def attributes(self) -> dict[str, str]:
        """Return one combined command's attributes, excluding Group identity."""
        return dict(self._attributes)

    def readback_attributes(self) -> dict[str, str]:
        """Return every explicitly requested attribute to confirm together."""
        return dict(self._attributes)


def _reported_decimal(value: object) -> Decimal:
    # The outer 0..90 window belongs to the shared vendor editor. It bounds
    # interpretation only; actual allowed temperatures still require ranges.
    if (
        type(value) is not str or len(value) > 64
        or _DECIMAL_TEXT.fullmatch(value) is None
    ):
        raise ValueError("expected a bounded unsigned Celsius decimal")
    number = Decimal(value)
    if not _MIN_TEMPERATURE <= number <= _MAX_TEMPERATURE:
        raise ValueError("reported temperature is outside the interpretation window")
    return number


def new_single_setpoint_profile(
    capability_attributes: Mapping[str, str],
) -> NewSingleSetpointProfile:
    """Require complete exact ordinary IC/NEW/ONE evidence before projection.

    Three complete factory ranges and known Dry/Fan/TempDetailC switches are
    intentionally required in this first conservative profile. A missing
    SubModel differs from its explicitly reported empty ordinary-IC value.
    Degenerate raw or inward-aligned intervals are rejected, never widened.
    """
    if not isinstance(capability_attributes, Mapping):
        raise ValueError("capability_attributes must be a reported capability mapping")  # noqa: TRY004 - uniform validation contract
    # Take one bounded copy of each required raw field before validating it.
    # The returned frozen profile never keeps a reference to the input map.
    attributes = {key: capability_attributes.get(key) for key in _CAPABILITY_FIELDS}
    if any(
        type(attributes[key]) is not str or attributes[key] != value
        for key, value in _IDENTITY.items()
    ):
        raise ValueError("requires an exact ordinary IC NEW ONE_SETPOINT profile")
    if any(type(attributes[key]) is not str or attributes[key] not in ("ENABLE", "DISABLE") for _, key in _OPTIONAL_MODES):
        raise ValueError("complete known mode switches are required")
    detail = attributes["TempDetailC"]
    if type(detail) is not str or detail not in ("ENABLE", "DISABLE"):
        raise ValueError("a known Celsius step capability is required")
    step = Decimal("0.5") if detail == "ENABLE" else Decimal(1)
    ranges = []
    for index in (1, 2, 3):
        low = _reported_decimal(attributes[f"TempRange1Low{index}"])
        high = _reported_decimal(attributes[f"TempRange1High{index}"])
        if low >= high:
            raise ValueError("a complete ordered factory temperature interval is required")
        # Bounded 64-character inputs fit this precision without rounding a
        # barely off-grid boundary outward before ceiling/floor is applied.
        # An independent context also isolates exponent bounds and traps;
        # changing precision alone would inherit the caller's other limits.
        with localcontext(Context(prec=128)):
            aligned_low = (low / step).to_integral_value(rounding=ROUND_CEILING) * step
            aligned_high = (high / step).to_integral_value(rounding=ROUND_FLOOR) * step
        if aligned_low >= aligned_high:
            raise ValueError("a nondegenerate step-aligned temperature interval is required")
        ranges.append((index, aligned_low, aligned_high))
    modes = ("COOL", "HEAT", *(
        mode for mode, key in _OPTIONAL_MODES if attributes[key] == "ENABLE"
    ), "AUTO")
    raw = tuple(attributes.items())
    return NewSingleSetpointProfile(modes, step, tuple(ranges), raw)


def _valid_target(profile: NewSingleSetpointProfile, mode: str, number: Decimal) -> None:
    bounds = profile.temperature_range(mode)
    if bounds is None:
        raise ValueError("target mode does not support a single temperature")
    if not bounds[0] <= number <= bounds[1]:
        raise ValueError("temperature is outside the target mode factory range")
    with localcontext(Context(prec=128)):
        quotient = number / profile.step
        if quotient != quotient.to_integral_value():
            raise ValueError("temperature is not on the reported Celsius step")


def read_new_single_setpoint_temperature(
    capability_attributes: Mapping[str, str], state_attributes: Mapping[str, str],
) -> float | None:
    """Read the exact active NEW register, without SetTemp or alias fallbacks."""
    if not isinstance(state_attributes, Mapping):
        return None
    try:
        profile = new_single_setpoint_profile(capability_attributes)
        mode = state_attributes.get("Mode")
        selected = profile.temperature_field(mode)
        if selected is None:
            return None
        number = _reported_decimal(state_attributes.get(selected))
        _valid_target(profile, mode, number)
    except ValueError:
        return None
    return float(number)


def plan_new_single_setpoint_climate(
    group_id: str | int, *, capability_attributes: Mapping[str, str],
    state_attributes: Mapping[str, str], mode: str | None = None,
    power: bool | None = None, temperature: float | None = None,
) -> NewSingleSetpointPlan:
    """Validate all requested settings and return one atomic attribute plan.

    An explicit mode determines the field, factory range and step before
    validating temperature. Temperature-only uses an exact reported mode;
    state freshness and matching group identity must be proven by the caller.
    No unrequested Mode/Drive/setpoint is added, rounded, clamped or restored.
    """
    group = _identifier(group_id, 50)
    profile = new_single_setpoint_profile(capability_attributes)
    if not isinstance(state_attributes, Mapping):
        raise ValueError("state_attributes must be a reported state mapping")  # noqa: TRY004 - uniform validation contract
    if mode is None and power is None and temperature is None:
        raise ValueError("at least one climate setting is required")
    if mode is not None and (type(mode) is not str or mode not in profile.supported_modes):
        raise ValueError("mode must be an exact supported ordinary IC mode")
    if power is not None and type(power) is not bool:
        raise ValueError("power must be strictly boolean")
    if temperature is not None and (
        type(temperature) not in (int, float)
        or type(temperature) is float and not math.isfinite(temperature)
    ):
        raise ValueError("temperature must be a finite Celsius number")
    current_mode = state_attributes.get("Mode")
    target_mode = mode if mode is not None else (
        current_mode if type(current_mode) is str and current_mode in profile.supported_modes else None
    )
    attributes = {}
    if mode is not None:
        attributes["Mode"] = mode
    if power is not None:
        attributes["Drive"] = "ON" if power else "OFF"
    selected = None
    number = None
    if temperature is not None:
        if not 0 <= temperature <= 90:
            # Bound native integers before conversion, including arbitrarily
            # large Python ints that math.isfinite cannot convert to float.
            raise ValueError("temperature is outside the interpretation window")
        if target_mode is None:
            raise ValueError("temperature-only planning requires an exact reported mode")
        selected = profile.temperature_field(target_mode)
        number = Decimal(str(temperature))
        _valid_target(profile, target_mode, number)
        text = format(number, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        attributes[selected] = "0" if number == 0 else text
    return NewSingleSetpointPlan(group, target_mode, selected, number, tuple(attributes.items()))
