"""NEW typed targets use a verified profile without changing OLD behaviour."""

from __future__ import annotations

import copy

import pytest
from pymelans.device import MelansDevice

CAPABILITIES = {
    "Model": "IC", "SubModel": "", "GroupType": "NEW", "IcKind": "NORMAL",
    "AutoModeSWEx": "ONE_SETPOINT", "TempDetailC": "ENABLE",
    "DryModeSW": "ENABLE", "FanModeSW": "ENABLE",
    "TempRange1Low1": "19", "TempRange1High1": "30",
    "TempRange1Low2": "19", "TempRange1High2": "28",
    "TempRange1Low3": "17", "TempRange1High3": "28",
}
STATE = {
    "Drive": "ON", "Mode": "COOL", "SetTemp": "11", "SetTemp1": "23",
    "SetTemp2": "21.5", "SetTemp3": "24.5", "InletTemp": "25",
}


@pytest.mark.parametrize("power", ["ON", "OFF"])
@pytest.mark.parametrize("mode,expected", [("COOL", 23), ("DRY", 23), ("HEAT", 21.5), ("AUTO", 24.5)])
def test_new_target_selects_the_mode_field_even_while_power_is_off(power, mode, expected):
    device = MelansDevice("4", None, {**STATE, "Drive": power, "Mode": mode}, CAPABILITIES)
    assert device.target_temperature == expected
    assert device.as_dict()["target_temperature"] == expected
    assert device.current_temperature == 25
    assert device.raw("SetTemp") == "11"


@pytest.mark.parametrize("mode", [None, "", "*", "FAN", "AUTOCOOL", "AUTOHEAT", "SETBACK", "cool", "FUTURE"])
def test_unmapped_new_modes_do_not_borrow_a_legacy_or_different_mode_target(mode):
    device = MelansDevice("4", None, {**STATE, "Mode": mode}, CAPABILITIES)
    assert device.target_temperature is None
    assert device.raw_attributes()["SetTemp1"] == "23"


@pytest.mark.parametrize("mode,field", [("COOL", "SetTemp1"), ("DRY", "SetTemp1"), ("HEAT", "SetTemp2"), ("AUTO", "SetTemp3")])
def test_missing_selected_field_does_not_fall_back_to_a_valid_other_target(mode, field):
    attrs = {**STATE, "Mode": mode}
    attrs.pop(field)
    device = MelansDevice("4", None, attrs, CAPABILITIES)
    assert device.target_temperature is None


@pytest.mark.parametrize("token", [
    None, "", "*", "0", "0.0", "18.5", "30.5", "999", "nan", "NaN", "inf", "-inf",
    "23junk", " 23", "23 ", "+23", "-23", "٢٣", "2.3e1", "23.25",
])
def test_new_invalid_or_unverified_target_stays_unknown_with_raw_preserved(token):
    attrs = {**STATE, "SetTemp1": token}
    device = MelansDevice("4", None, attrs, CAPABILITIES)
    assert device.target_temperature is None
    assert device.raw_attributes() == attrs


@pytest.mark.parametrize("key", CAPABILITIES)
def test_incomplete_new_profile_never_returns_the_legacy_setpoint(key):
    capabilities = {**CAPABILITIES}
    capabilities.pop(key)
    device = MelansDevice("4", None, STATE, capabilities)
    if key == "GroupType":
        # No new family is inferred when the discriminator itself is absent.
        assert device.target_temperature == 11
    else:
        assert device.target_temperature is None


@pytest.mark.parametrize("change", [
    {"Model": "LC"}, {"SubModel": "HH"}, {"SubModel": "EOA"}, {"SubModel": "*"},
    {"SubModel": "FUTURE"}, {"IcKind": "COOL_ONLY"}, {"AutoModeSWEx": "TWO_SETPOINT"},
    {"AutoModeSWEx": "FUTURE"}, {"TempDetailC": "*"}, {"TempRange1Low2": "nan"},
    {"TempRange1Low3": "28", "TempRange1High3": "17"},
])
def test_other_new_families_and_invalid_profiles_withhold_typed_target(change):
    device = MelansDevice("4", None, STATE, {**CAPABILITIES, **change})
    assert device.target_temperature is None


@pytest.mark.parametrize("detail,value,expected", [
    ("ENABLE", "19.0", 19), ("ENABLE", "30.000", 30), ("ENABLE", "23.5000", 23.5),
    ("DISABLE", "23.0", 23), ("DISABLE", "23.5", None),
])
def test_new_reading_respects_reported_range_and_celsius_grid(detail, value, expected):
    device = MelansDevice("4", None, {**STATE, "SetTemp1": value}, {**CAPABILITIES, "TempDetailC": detail})
    assert device.target_temperature == expected


def test_reads_do_not_mutate_the_cached_state_or_capability_evidence():
    attrs, capabilities = copy.deepcopy(STATE), copy.deepcopy(CAPABILITIES)
    device = MelansDevice("4", None, attrs, capabilities)
    expected_state, expected_capabilities = copy.deepcopy(attrs), copy.deepcopy(capabilities)
    for _ in range(3):
        assert device.target_temperature == 23
        device.as_dict()
    assert device.raw_attributes() == attrs == expected_state
    assert device.capability_attributes == capabilities == expected_capabilities


@pytest.mark.parametrize("capabilities", [{}, {"GroupType": "OLD"}, {"GroupType": "FUTURE"}])
@pytest.mark.parametrize("mode,value,expected", [
    ("COOL", "0", 0), ("HEAT", " 23.25 ", 23.25), ("FAN", "22", 22),
    ("AUTO", "2.3e1", 23), ("COOL", "nan", None),
])
def test_non_new_device_target_keeps_legacy_interpretation(capabilities, mode, value, expected):
    device = MelansDevice("4", None, {**STATE, "Mode": mode, "SetTemp": value}, capabilities)
    assert device.target_temperature == expected
