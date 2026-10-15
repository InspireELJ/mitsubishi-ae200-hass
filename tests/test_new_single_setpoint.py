"""Strict offline NEW one-point planning never enables a public write path."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal, Inexact, Overflow, Rounded, Underflow, localcontext
from unittest.mock import AsyncMock

import pytest
from pymelans.new_single_setpoint import (
    new_single_setpoint_profile,
    plan_new_single_setpoint_climate,
    read_new_single_setpoint_temperature,
)

from pymelans import MelansClient, MelansDevice

CAPS = {
    "Model": "IC", "SubModel": "", "IcKind": "NORMAL", "GroupType": "NEW",
    "AutoModeSWEx": "ONE_SETPOINT", "AutoModeSW": "DISABLE", "DryModeSW": "ENABLE",
    "FanModeSW": "ENABLE", "TempDetailC": "ENABLE",
    "TempRange1Low1": "19", "TempRange1High1": "30",
    "TempRange1Low2": "19", "TempRange1High2": "28",
    "TempRange1Low3": "17", "TempRange1High3": "28",
}
STATE = {"Mode": "COOL", "SetTemp": "99", "SetTemp1": "20.5", "SetTemp2": "18", "SetTemp3": "22"}


def plan(**settings):
    return plan_new_single_setpoint_climate("4", capability_attributes=CAPS, state_attributes=STATE, **settings)


def test_profile_has_exact_modes_step_and_copied_allowlisted_evidence():
    source = {**CAPS, "Password": "private-secret", "GroupName": "private-room"}
    profile = new_single_setpoint_profile(source)
    assert profile.supported_modes == ("COOL", "HEAT", "DRY", "FAN", "AUTO")
    assert profile.step == Decimal("0.5")
    assert profile.temperature_field("FAN") is profile.temperature_range("FAN") is None
    assert profile.temperature_field("AUTOCOOL") is profile.temperature_range("SETBACK") is None
    exported = profile.raw_attributes()
    exported["Model"] = "LC"
    source["TempRange1Low1"] = "25"
    assert profile.raw_attributes()["Model"] == "IC" and profile.temperature_range("COOL") == (Decimal(19), Decimal(30))
    assert "Password" not in profile.raw_attributes() and "private" not in repr(profile)
    with pytest.raises(FrozenInstanceError):
        profile.step = Decimal(1)


@pytest.mark.parametrize("mode,field,limits", [
    ("COOL", "SetTemp1", (19, 30)), ("DRY", "SetTemp1", (19, 30)),
    ("HEAT", "SetTemp2", (17, 28)), ("AUTO", "SetTemp3", (19, 28)),
])
def test_target_mode_selects_exact_new_register_and_factory_range(mode, field, limits):
    profile = new_single_setpoint_profile(CAPS)
    result = plan(mode=mode, power=True, temperature=20.5)
    assert profile.temperature_field(mode) == field
    assert profile.temperature_range(mode) == tuple(Decimal(value) for value in limits)
    assert result.group_id == "4" and result.target_mode == mode and result.selected_temperature_field == field
    assert result.requested_celsius == Decimal("20.5")
    assert result.attributes() == result.readback_attributes() == {"Mode": mode, "Drive": "ON", field: "20.5"}
    assert "SetTemp" not in result.attributes() and len(result.attributes()) == 3


@pytest.mark.parametrize("mode,temperature", [("HEAT", 18), ("COOL", 30)])
def test_combined_target_mode_uses_its_own_range_instead_of_the_current_mode(mode, temperature):
    state = {**STATE, "Mode": "HEAT" if mode == "COOL" else "COOL"}
    result = plan_new_single_setpoint_climate(
        4, capability_attributes=CAPS, state_attributes=state, mode=mode, power=True, temperature=temperature,
    )
    assert result.attributes()["Mode"] == mode and result.requested_celsius == temperature


def test_explicit_target_mode_does_not_guess_a_missing_current_mode():
    result = plan_new_single_setpoint_climate(
        "004", capability_attributes=CAPS, state_attributes={}, mode="HEAT", temperature=18,
    )
    assert result.attributes() == {"Mode": "HEAT", "SetTemp2": "18"} and result.group_id == "4"


def test_temperature_only_changes_only_the_active_register_and_off_does_not_erase_mode_context():
    assert plan(temperature=21).attributes() == {"SetTemp1": "21"}
    assert plan(power=False, temperature=21).attributes() == {"Drive": "OFF", "SetTemp1": "21"}
    result = plan(mode="FAN", power=False)
    assert result.attributes() == {"Mode": "FAN", "Drive": "OFF"}
    assert result.selected_temperature_field is result.requested_celsius is None


def test_power_only_can_be_planned_without_inventing_a_mode_or_temperature():
    result = plan_new_single_setpoint_climate("4", capability_attributes=CAPS, state_attributes={}, power=True)
    assert result.target_mode is None and result.attributes() == {"Drive": "ON"}


@pytest.mark.parametrize("key,bad", [
    ("Model", None), ("Model", ""), ("Model", "LC"), ("Model", "AIC"),
    ("SubModel", None), ("SubModel", "*"), ("SubModel", "HH"), ("SubModel", "EOA"), ("SubModel", "FUTURE"),
    ("IcKind", ""), ("IcKind", "COOLONLY"), ("GroupType", None), ("GroupType", ""), ("GroupType", "OLD"),
    ("AutoModeSWEx", None), ("AutoModeSWEx", ""), ("AutoModeSWEx", "*"), ("AutoModeSWEx", "TWO_SETPOINT"),
    ("AutoModeSWEx", "FUTURE"), ("TempDetailC", None), ("TempDetailC", ""), ("TempDetailC", "*"),
    ("DryModeSW", None), ("DryModeSW", ""), ("DryModeSW", "*"), ("FanModeSW", "FUTURE"),
])
def test_missing_or_unrecognised_required_profile_never_falls_back_to_old_or_other_equipment(key, bad):
    caps = dict(CAPS)
    if bad is None:
        caps.pop(key)
    else:
        caps[key] = bad
    with pytest.raises(ValueError):
        new_single_setpoint_profile(caps)
    with pytest.raises(ValueError):
        plan_new_single_setpoint_climate("4", capability_attributes=caps, state_attributes=STATE, mode="COOL", temperature=20)
    assert read_new_single_setpoint_temperature(caps, STATE) is None


def test_one_setpoint_auto_uses_extended_flag_even_when_legacy_auto_is_disabled_or_absent():
    for caps in (CAPS, {key: value for key, value in CAPS.items() if key != "AutoModeSW"}):
        profile = new_single_setpoint_profile(caps)
        assert "AUTO" in profile.supported_modes and profile.temperature_field("AUTO") == "SetTemp3"
        result = plan_new_single_setpoint_climate("4", capability_attributes=caps, state_attributes=STATE, mode="AUTO", temperature=22)
        assert result.attributes() == {"Mode": "AUTO", "SetTemp3": "22"}


@pytest.mark.parametrize("mode,flag", [("DRY", "DryModeSW"), ("FAN", "FanModeSW")])
def test_disabled_optional_mode_is_neither_advertised_planned_nor_used_for_typed_read(mode, flag):
    caps = {**CAPS, flag: "DISABLE"}
    profile = new_single_setpoint_profile(caps)
    assert mode not in profile.supported_modes and profile.temperature_field(mode) is None
    with pytest.raises(ValueError):
        plan_new_single_setpoint_climate("4", capability_attributes=caps, state_attributes=STATE, mode=mode, power=True)
    assert read_new_single_setpoint_temperature(caps, {**STATE, "Mode": mode}) is None


@pytest.mark.parametrize("key,bad", [
    ("TempRange1Low1", None), ("TempRange1High2", ""), ("TempRange1Low3", "*"),
    ("TempRange1High1", "NaN"), ("TempRange1Low2", "-1"), ("TempRange1High3", "Infinity"),
    ("TempRange1Low1", "19junk"), ("TempRange1High1", " 30"), ("TempRange1Low1", "١٩"),
    ("TempRange1High1", "3e1"), ("TempRange1Low2", "+19"), ("TempRange1High1", "91"),
    ("TempRange1Low1", "30"), ("TempRange1Low1", "31"),
])
def test_all_three_ranges_must_be_complete_finite_ordered_and_bounded_without_ui_defaults(key, bad):
    caps = dict(CAPS)
    if bad is None:
        caps.pop(key)
    else:
        caps[key] = bad
    with pytest.raises(ValueError):
        new_single_setpoint_profile(caps)


def test_ranges_align_inward_to_reported_step_without_weakening_slightly_off_grid_bounds():
    caps = {**CAPS, "TempRange1Low1": "19.0000000000000000000000000000001", "TempRange1High1": "29.9"}
    assert new_single_setpoint_profile(caps).temperature_range("COOL") == (Decimal("19.5"), Decimal("29.5"))
    caps["TempDetailC"] = "DISABLE"
    assert new_single_setpoint_profile(caps).step == Decimal(1)
    assert new_single_setpoint_profile(caps).temperature_range("COOL") == (Decimal(20), Decimal(29))
    with pytest.raises(ValueError):
        new_single_setpoint_profile({**CAPS, "TempRange1Low1": "19.1", "TempRange1High1": "19.9"})


@pytest.mark.parametrize("settings", [
    {}, {"power": 1}, {"power": "true"}, {"mode": "heat"}, {"mode": "AUTOCOOL"}, {"mode": "SETBACK"},
    {"mode": True}, {"temperature": True}, {"temperature": "20"}, {"temperature": Decimal("20")},
    {"temperature": float("nan")}, {"temperature": float("inf")}, {"temperature": 20.1},
    {"temperature": 18}, {"mode": "COOL", "temperature": 18, "power": True},
    {"mode": "HEAT", "temperature": 30, "power": True}, {"mode": "FAN", "temperature": 20},
])
def test_all_settings_are_validated_before_a_combined_plan_can_be_obtained(settings):
    with pytest.raises(ValueError):
        plan(**settings)


@pytest.mark.parametrize("reported_mode", [None, "", "*", "FUTURE", "AUTOCOOL", "AUTOHEAT", "SETBACK", "FAN"])
def test_temperature_only_requires_exact_supported_current_mode_without_aliases(reported_mode):
    state = {**STATE, "Mode": reported_mode}
    with pytest.raises(ValueError):
        plan_new_single_setpoint_climate("4", capability_attributes=CAPS, state_attributes=state, temperature=20)
    assert read_new_single_setpoint_temperature(CAPS, state) is None


@pytest.mark.parametrize("mode,field,value", [("COOL", "SetTemp1", 20.5), ("DRY", "SetTemp1", 20.5), ("HEAT", "SetTemp2", 18), ("AUTO", "SetTemp3", 22)])
def test_typed_read_uses_only_the_exact_active_new_register(mode, field, value):
    state = {**STATE, "Mode": mode}
    assert read_new_single_setpoint_temperature(CAPS, state) == value
    state.pop(field)
    assert read_new_single_setpoint_temperature(CAPS, state) is None
    assert state["SetTemp"] == "99"


@pytest.mark.parametrize("token", [None, "", "*", "NaN", "Infinity", "20junk", "2e1", " 20", "20 ", "+20", "-20", "٢٠", "18", "31", "20.2", 20])
def test_unknown_invalid_off_range_or_off_step_active_raw_temperature_stays_none(token):
    assert read_new_single_setpoint_temperature(CAPS, {**STATE, "SetTemp1": token}) is None


def test_whole_degree_profile_rejects_half_degree_requests_and_off_step_readings():
    caps = {**CAPS, "TempDetailC": "DISABLE"}
    with pytest.raises(ValueError):
        plan_new_single_setpoint_climate("4", capability_attributes=caps, state_attributes=STATE, temperature=20.5)
    assert read_new_single_setpoint_temperature(caps, STATE) is None
    result = plan_new_single_setpoint_climate("4", capability_attributes=caps, state_attributes=STATE, temperature=20.0)
    assert result.attributes() == {"SetTemp1": "20"}


@pytest.mark.parametrize("group", [0, 51, True, "*", "4,5", "٤"])
def test_group_selection_is_bounded_ascii_but_membership_remains_the_callers_responsibility(group):
    with pytest.raises(ValueError):
        plan_new_single_setpoint_climate(group, capability_attributes=CAPS, state_attributes=STATE, temperature=20)


def test_atomic_plan_is_frozen_and_exports_are_independent_without_mutating_input_state():
    state, caps = dict(STATE), dict(CAPS)
    result = plan_new_single_setpoint_climate("4", capability_attributes=caps, state_attributes=state, mode="HEAT", power=True, temperature=18)
    attributes = result.attributes()
    attributes["SetTemp2"] = "28"
    confirmation = result.readback_attributes()
    confirmation["Mode"] = "COOL"
    assert result.attributes() == {"Mode": "HEAT", "Drive": "ON", "SetTemp2": "18"}
    assert state == STATE and caps == CAPS
    with pytest.raises(FrozenInstanceError):
        result.target_mode = "COOL"
    assert not any(hasattr(result, key) for key in ("confirmed", "supported_live", "write", "request", "execute", "retry"))


def test_extreme_numeric_inputs_fail_cleanly_without_overflow_or_rounding_to_a_grid():
    for value in (10 ** 1000, -(10 ** 1000), 1e-300, 20.500000000000004):
        with pytest.raises(ValueError):
            plan(temperature=value)


def test_zero_formatting_does_not_emit_negative_or_scientific_wire_tokens():
    caps = {**CAPS, "TempRange1Low1": "0"}
    result = plan_new_single_setpoint_climate("4", capability_attributes=caps, state_attributes=STATE, temperature=-0.0)
    assert result.attributes() == {"SetTemp1": "0"}


@pytest.mark.parametrize("precision", [1, 2])
@pytest.mark.parametrize("trap_rounding", [False, True])
def test_ambient_decimal_precision_cannot_round_a_valid_request_to_another_temperature(precision, trap_rounding):
    with localcontext() as context:
        context.prec = precision
        context.traps[Inexact] = context.traps[Rounded] = trap_rounding
        result = plan(temperature=20.5)
        assert result.requested_celsius == Decimal("20.5")
        assert result.attributes() == {"SetTemp1": "20.5"}
        assert read_new_single_setpoint_temperature(CAPS, STATE) == 20.5
        caps = {**CAPS, "TempRange1Low1": "19.0000000000000000000000000000001"}
        assert new_single_setpoint_profile(caps).temperature_range("COOL") == (Decimal("19.5"), Decimal(30))


def test_ambient_exponent_bounds_and_traps_cannot_escape_typed_reads_or_change_plans():
    with localcontext() as context:
        context.prec = 1
        context.Emax = context.Emin = 0
        for signal in (Overflow, Underflow, Inexact, Rounded):
            context.traps[signal] = True
        context.clear_flags()
        profile = new_single_setpoint_profile(CAPS)
        assert profile.temperature_range("COOL") == (Decimal(19), Decimal(30))
        assert read_new_single_setpoint_temperature(CAPS, STATE) == 20.5
        assert MelansDevice("4", None, dict(STATE), dict(CAPS)).target_temperature == 20.5
        assert read_new_single_setpoint_temperature(CAPS, {**STATE, "SetTemp1": "20.2"}) is None
        assert plan(temperature=20.5).attributes() == {"SetTemp1": "20.5"}
        assert context.prec == 1 and context.Emax == context.Emin == 0
        assert not any(context.flags.values())


@pytest.mark.parametrize("mode", [["COOL"], {"mode": "COOL"}, {"COOL"}, 1, True, None])
def test_untyped_reported_modes_remain_unknown_in_queries_and_temperature_only_plans(mode):
    profile = new_single_setpoint_profile(CAPS)
    assert profile.temperature_field(mode) is profile.temperature_range(mode) is None
    state = {**STATE, "Mode": mode}
    assert read_new_single_setpoint_temperature(CAPS, state) is None
    with pytest.raises(ValueError):
        plan_new_single_setpoint_climate("4", capability_attributes=CAPS, state_attributes=state, temperature=20)


@pytest.mark.asyncio
async def test_adding_a_pure_plan_does_not_enable_the_existing_new_public_temperature_write_path():
    client = MelansClient("fixture.invalid")
    client.devices["4"] = MelansDevice("4", None, dict(STATE), dict(CAPS))
    client._request = AsyncMock()
    assert plan(mode="HEAT", power=True, temperature=18).attributes() == {"Mode": "HEAT", "Drive": "ON", "SetTemp2": "18"}
    with pytest.raises(ValueError, match="NEW group"):
        await client.set_climate("4", mode="HEAT", power=True, temperature=18)
    client._request.assert_not_awaited()
    assert client.last_command is None and client.devices["4"].raw_attributes() == STATE
