"""Use real integration entities with minimal HA API boundary stubs.

These verify local contracts, not an end-to-end Home Assistant installation.
"""

from __future__ import annotations

import importlib
import sys
import time
import types
import xml.etree.ElementTree as ET
from enum import IntFlag, StrEnum
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from pymelans import MelansClient, MelansDevice


@pytest.fixture
def ha_modules(monkeypatch):
    def module(name, **members):
        result = types.ModuleType(name)
        result.__dict__.update(members)
        monkeypatch.setitem(sys.modules, name, result)
        return result

    class CoordinatorEntity:
        @classmethod
        def __class_getitem__(cls, _):
            return cls

        def __init__(self, coordinator):
            self.coordinator = coordinator

        @property
        def available(self):
            return self.coordinator.last_update_success

    class HVACMode(StrEnum):
        OFF = "off"
        COOL = "cool"
        HEAT = "heat"
        FAN_ONLY = "fan_only"
        DRY = "dry"
        HEAT_COOL = "heat_cool"

    class Features(IntFlag):
        TARGET_TEMPERATURE = 1
        FAN_MODE = 8
        SWING_MODE = 32
        TURN_ON = 128
        TURN_OFF = 256

    for name in ["homeassistant", "homeassistant.helpers", "homeassistant.components"]:
        result = module(name)
        result.__path__ = []
    module("homeassistant.helpers.device_registry", DeviceInfo=dict)
    module("homeassistant.helpers.update_coordinator", CoordinatorEntity=CoordinatorEntity)
    module("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
    module("homeassistant.core", HomeAssistant=object)
    module("homeassistant.config_entries", ConfigEntry=object)
    module(
        "homeassistant.const",
        ATTR_TEMPERATURE="temperature",
        UnitOfTemperature=types.SimpleNamespace(CELSIUS="°C"),
    )
    module(
        "homeassistant.components.climate",
        ATTR_HVAC_MODE="hvac_mode",
        ClimateEntity=type("ClimateEntity", (), {"min_temp": 7.0, "max_temp": 35.0}),
        ClimateEntityFeature=Features,
        HVACMode=HVACMode,
    )
    name = "ae200_contract_test"
    package = module(name)
    package.__path__ = [str(Path(__file__).resolve().parents[1] / "custom_components/ae200")]
    module(name + ".coordinator", AE200Coordinator=object)
    yield name
    for key in list(sys.modules):
        if key.startswith(name + ".") and key not in (name + ".coordinator",):
            sys.modules.pop(key, None)


def coordinator():
    c = MelansClient("fixture.invalid")
    c._names = {"1": None}
    c.devices["1"] = MelansDevice("1", "Fixture room", {"Drive": "ON", "Mode": "COOL"})
    c._group_seen["1"] = time.monotonic()
    return types.SimpleNamespace(host=c.host, client=c, data=c.devices, last_update_success=True)


def capability_coordinator(*, capabilities=None, attrs=None):
    c = coordinator()
    c.client.devices["1"] = MelansDevice(
        "1", "Fixture room", attrs or {"Drive": "OFF", "Mode": "COOL"},
        capability_attrs=capabilities,
    )
    return c


AC_CAPABILITIES = {
    "Model": "IC", "IcKind": "NORMAL", "AutoModeSW": "DISABLE",
    "DryModeSW": "ENABLE", "FanModeSW": "ENABLE", "FanSpeedSW": "4STAGES",
    "FanAutoSW": "ENABLE", "AirDirectionSW": "ENABLE", "AirStageSW": "5STAGES",
    "AirAutoSW": "ENABLE", "SwingSW": "ENABLE",
}

TEMPERATURE_CAPABILITIES = {
    **AC_CAPABILITIES,
    "TempRange1Low1": "19", "TempRange1High1": "30",
    "TempRange1Low2": "19", "TempRange1High2": "28",
    "TempRange1Low3": "17", "TempRange1High3": "28",
}


def test_operational_entity_becomes_unavailable_on_group_failure(ha_modules):
    classes = importlib.import_module(ha_modules + ".entity")
    c = coordinator()
    entity = classes.AE200Entity(c, "1")
    assert entity.available
    c.client.failed_groups = {"1"}
    assert not entity.available


def test_controller_health_survives_controller_outage(ha_modules):
    classes = importlib.import_module(ha_modules + ".entity")
    c = coordinator()
    c.last_update_success = False
    assert classes.AE200ControllerEntity(c).available
    assert not classes.AE200Entity(c, "1").available


def test_device_identifiers_and_climate_unique_id_stay_compatible(ha_modules):
    classes = importlib.import_module(ha_modules + ".climate")
    c = coordinator()
    entity = classes.AE200Climate(c, "1")
    assert entity._attr_unique_id == "fixture.invalid_1_climate"
    assert entity.device_info["identifiers"] == {("ae200", "fixture.invalid:1")}
    assert entity.device_info["via_device"] == ("ae200", "fixture.invalid")
    assert entity.hvac_mode == "cool"


def test_unknown_power_does_not_become_off(ha_modules):
    classes = importlib.import_module(ha_modules + ".climate")
    c = coordinator()
    c.client.devices["1"].update({"Mode": "COOL"})
    assert classes.AE200Climate(c, "1").hvac_mode is None


@pytest.mark.parametrize("raw,mode", [("MID", "medium"), ("MID1", "medium_1"), ("MID2", "medium_2")])
def test_fan_steps_keep_distinct_reported_values(ha_modules, raw, mode):
    classes = importlib.import_module(ha_modules + ".climate")
    c = coordinator()
    c.client.devices["1"].update({"FanSpeed": raw})
    entity = classes.AE200Climate(c, "1")
    assert entity.fan_mode == mode
    assert mode in entity._attr_fan_modes
    assert entity.extra_state_attributes["fan_speed_raw"] == raw


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,raw", [("medium", "MID"), ("medium_1", "MID1"), ("medium_2", "MID2")])
async def test_fan_commands_preserve_wire_enumerations(ha_modules, mode, raw):
    classes = importlib.import_module(ha_modules + ".climate")
    c = coordinator()
    c.client.set_fan_speed = AsyncMock()
    c.async_request_refresh = AsyncMock()
    await classes.AE200Climate(c, "1").async_set_fan_mode(mode)
    c.client.set_fan_speed.assert_awaited_once_with("1", raw)
    c.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("drive", ["ON", "OFF", "*"])
async def test_hvac_mode_and_power_use_one_checked_command(ha_modules, drive):
    classes = importlib.import_module(ha_modules + ".climate")
    c = coordinator()
    c.client.devices["1"].update({"Drive": drive, "Mode": "HEAT"})
    c.client.set_climate = AsyncMock()
    c.client.set_power = AsyncMock()
    c.client.set_mode = AsyncMock()
    c.async_request_refresh = AsyncMock()

    await classes.AE200Climate(c, "1").async_set_hvac_mode("cool")

    c.client.set_climate.assert_awaited_once_with("1", mode="COOL", power=True)
    c.client.set_power.assert_not_awaited()
    c.client.set_mode.assert_not_awaited()
    c.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_mode_change_does_not_power_on_or_refresh(ha_modules):
    classes = importlib.import_module(ha_modules + ".climate")
    c = coordinator()
    c.client.devices["1"].update({"Drive": "OFF", "Mode": "HEAT"})
    c.client.set_climate = AsyncMock(side_effect=ValueError("rejected fixture mode"))
    c.client.set_power = AsyncMock()
    c.async_request_refresh = AsyncMock()
    entity = classes.AE200Climate(c, "1")

    with pytest.raises(ValueError, match="rejected fixture"):
        await entity.async_set_hvac_mode("cool")

    c.client.set_power.assert_not_awaited()
    c.async_request_refresh.assert_not_awaited()
    assert entity.hvac_mode == "off"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "hvac_mode,expected",
    [
        ("cool", {"mode": "COOL", "power": True, "temperature": 22.5}),
        ("off", {"power": False, "temperature": 22.5}),
    ],
)
async def test_temperature_service_honours_requested_hvac_mode(ha_modules, hvac_mode, expected):
    classes = importlib.import_module(ha_modules + ".climate")
    c = coordinator()
    c.client.set_climate = AsyncMock()
    c.client.set_temperature = AsyncMock()
    c.async_request_refresh = AsyncMock()

    await classes.AE200Climate(c, "1").async_set_temperature(
        temperature=22.5, hvac_mode=hvac_mode
    )

    c.client.set_climate.assert_awaited_once_with("1", **expected)
    c.client.set_temperature.assert_not_awaited()
    c.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_temperature_only_does_not_change_power_or_mode(ha_modules):
    classes = importlib.import_module(ha_modules + ".climate")
    c = coordinator()
    c.client.set_climate = AsyncMock()
    c.client.set_temperature = AsyncMock()
    c.async_request_refresh = AsyncMock()

    await classes.AE200Climate(c, "1").async_set_temperature(temperature=22)

    c.client.set_temperature.assert_awaited_once_with("1", 22.0)
    c.client.set_climate.assert_not_awaited()
    c.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,kwargs",
    [
        ("async_set_hvac_mode", {"hvac_mode": "unsupported"}),
        ("async_set_fan_mode", {"fan_mode": "unsupported"}),
        ("async_set_temperature", {"temperature": 22, "hvac_mode": "unsupported"}),
        ("async_set_temperature", {}),
    ],
)
async def test_invalid_climate_arguments_do_not_send_commands(ha_modules, method, kwargs):
    classes = importlib.import_module(ha_modules + ".climate")
    c = coordinator()
    c.client.set_climate = AsyncMock()
    c.client.set_fan_speed = AsyncMock()
    c.client.set_temperature = AsyncMock()
    c.client.set_power = AsyncMock()
    c.async_request_refresh = AsyncMock()

    with pytest.raises(ValueError):
        await getattr(classes.AE200Climate(c, "1"), method)(**kwargs)

    for command in (
        c.client.set_climate,
        c.client.set_fan_speed,
        c.client.set_temperature,
        c.client.set_power,
        c.async_request_refresh,
    ):
        command.assert_not_awaited()


def test_known_unit_capabilities_control_available_climate_options(ha_modules):
    classes = importlib.import_module(ha_modules + ".climate")
    c = capability_coordinator(
        capabilities=AC_CAPABILITIES,
        attrs={"Drive": "ON", "Mode": "COOL", "FanSpeed": "MID1", "AirDirection": "MID0"},
    )
    entity = classes.AE200Climate(c, "1")

    assert entity.fan_modes == ["low", "medium_2", "medium_1", "high", "auto"]
    assert entity.hvac_modes == ["off", "cool", "heat", "dry", "fan_only"]
    assert entity.swing_modes == [
        "fixed_horizontal", "mid_0", "mid_1", "mid_2", "fixed_vertical", "auto", "swing"
    ]
    assert entity.swing_mode == "mid_0"
    assert entity.supported_features & classes.ClimateEntityFeature.SWING_MODE
    assert entity.extra_state_attributes["air_direction"] == "MID0"
    assert entity._attr_unique_id == "fixture.invalid_1_climate"


def test_unknown_capabilities_keep_legacy_options_without_inventing_vane_support(ha_modules):
    classes = importlib.import_module(ha_modules + ".climate")
    c = capability_coordinator(attrs={"AirDirection": "HORIZONTAL", "FanSpeed": "MID"})
    entity = classes.AE200Climate(c, "1")

    assert entity.fan_modes == entity._attr_fan_modes
    assert entity.hvac_modes == entity._attr_hvac_modes
    assert entity.fan_mode == "medium"
    assert entity.swing_modes is None
    assert not entity.supported_features & classes.ClimateEntityFeature.SWING_MODE


def test_capabilities_can_remove_unsupported_controls_without_hiding_reported_state(ha_modules):
    classes = importlib.import_module(ha_modules + ".climate")
    c = capability_coordinator(
        capabilities={
            **AC_CAPABILITIES, "FanSpeedSW": "NONE", "FanAutoSW": "DISABLE",
            "AirDirectionSW": "DISABLE",
        },
        attrs={"FanSpeed": "MID"},
    )
    entity = classes.AE200Climate(c, "1")

    assert entity.fan_modes == []
    assert entity.fan_mode == "medium"
    assert not entity.supported_features & classes.ClimateEntityFeature.FAN_MODE
    assert not entity.supported_features & classes.ClimateEntityFeature.SWING_MODE
    assert entity.hvac_modes == ["off", "cool", "heat", "dry", "fan_only"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "selection,wire",
    [
        ("fixed_horizontal", "HORIZONTAL"),
        ("mid_0", "MID0"),
        ("mid_1", "MID1"),
        ("mid_2", "MID2"),
        ("fixed_vertical", "VERTICAL"),
        ("auto", "AUTO"),
        ("swing", "SWING"),
    ],
)
async def test_swing_commands_preserve_verified_direction_vocabulary(ha_modules, selection, wire):
    classes = importlib.import_module(ha_modules + ".climate")
    c = capability_coordinator(capabilities=AC_CAPABILITIES, attrs={"AirDirection": wire})
    c.client.set_air_direction = AsyncMock()
    c.async_request_refresh = AsyncMock()
    entity = classes.AE200Climate(c, "1")

    assert entity.swing_mode == selection
    await entity.async_set_swing_mode(selection)

    c.client.set_air_direction.assert_awaited_once_with("1", wire)
    c.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,kwargs",
    [
        ("async_set_hvac_mode", {"hvac_mode": "heat_cool"}),
        ("async_set_temperature", {"temperature": 22, "hvac_mode": "heat_cool"}),
        ("async_set_fan_mode", {"fan_mode": "medium"}),
        ("async_set_swing_mode", {"swing_mode": "swing"}),
        ("async_set_swing_mode", {"swing_mode": "downward_1"}),
    ],
)
async def test_known_unsupported_options_fail_before_writing(ha_modules, method, kwargs):
    classes = importlib.import_module(ha_modules + ".climate")
    c = capability_coordinator(
        capabilities={
            **AC_CAPABILITIES, "FanSpeedSW": "2STAGES", "SwingSW": "DISABLE",
        }
    )
    c.client.set_climate = AsyncMock()
    c.client.set_fan_speed = AsyncMock()
    c.client.set_air_direction = AsyncMock()
    c.async_request_refresh = AsyncMock()

    with pytest.raises(ValueError):
        await getattr(classes.AE200Climate(c, "1"), method)(**kwargs)

    for command in (
        c.client.set_climate,
        c.client.set_fan_speed,
        c.client.set_air_direction,
        c.async_request_refresh,
    ):
        command.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_vane_capability_does_not_allow_writes(ha_modules):
    classes = importlib.import_module(ha_modules + ".climate")
    c = capability_coordinator(attrs={"AirDirection": "HORIZONTAL"})
    c.client.set_air_direction = AsyncMock()
    c.async_request_refresh = AsyncMock()

    with pytest.raises(ValueError):
        await classes.AE200Climate(c, "1").async_set_swing_mode("fixed_horizontal")

    c.client.set_air_direction.assert_not_awaited()
    c.async_request_refresh.assert_not_awaited()


@pytest.mark.parametrize(
    "mode,expected", [("COOL", (19, 30)), ("DRY", (19, 30)), ("HEAT", (17, 28))]
)
def test_temperature_envelope_and_current_mode_limits_are_distinct_while_off(
    ha_modules, mode, expected
):
    classes = importlib.import_module(ha_modules + ".climate")
    c = capability_coordinator(
        capabilities=TEMPERATURE_CAPABILITIES,
        attrs={"Drive": "OFF", "Mode": mode},
    )
    entity = classes.AE200Climate(c, "1")

    assert entity.hvac_mode == "off"
    assert (entity.min_temp, entity.max_temp) == (17, 30)
    assert (
        entity.extra_state_attributes["current_mode_min_temp_celsius"],
        entity.extra_state_attributes["current_mode_max_temp_celsius"],
    ) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "current_mode,requested_mode,temperature,wire_mode",
    [("COOL", "heat", 17, "HEAT"), ("HEAT", "cool", 30, "COOL")],
)
async def test_combined_mode_and_temperature_survive_ha_prevalidation(
    ha_modules, current_mode, requested_mode, temperature, wire_mode
):
    classes = importlib.import_module(ha_modules + ".climate")
    c = capability_coordinator(
        capabilities=TEMPERATURE_CAPABILITIES,
        attrs={"Drive": "OFF", "Mode": current_mode},
    )
    c.client._request = AsyncMock(
        return_value="<Packet><Command>setResponse</Command><DatabaseManager /></Packet>"
    )
    c.async_request_refresh = AsyncMock()
    entity = classes.AE200Climate(c, "1")

    # HA's climate service validates these limits before the entity receives
    # the requested mode. The current mode's narrower limits must not block it.
    assert entity.min_temp <= temperature <= entity.max_temp
    current_limits = entity.device.temperature_range()
    assert not current_limits[0] <= temperature <= current_limits[1]
    await entity.async_set_temperature(temperature=temperature, hvac_mode=requested_mode)

    c.client._request.assert_awaited_once()
    packet = ET.fromstring(c.client._request.call_args.args[0])
    assert packet.find("./DatabaseManager/Mnet").attrib == {
        "Group": "1", "Drive": "ON", "Mode": wire_mode, "SetTemp": str(temperature)
    }
    c.async_request_refresh.assert_awaited_once()
    assert entity.hvac_mode == "off"  # Acknowledgement is not an optimistic state update.


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs", [{"temperature": 17}, {"temperature": 29, "hvac_mode": "heat"}]
)
async def test_global_envelope_does_not_bypass_target_mode_limits(ha_modules, kwargs):
    classes = importlib.import_module(ha_modules + ".climate")
    c = capability_coordinator(
        capabilities=TEMPERATURE_CAPABILITIES,
        attrs={"Drive": "OFF", "Mode": "COOL"},
    )
    c.client._request = AsyncMock()
    c.async_request_refresh = AsyncMock()
    entity = classes.AE200Climate(c, "1")

    assert entity.min_temp <= kwargs["temperature"] <= entity.max_temp
    with pytest.raises(ValueError, match="temperature must be between"):
        await entity.async_set_temperature(**kwargs)

    c.client._request.assert_not_awaited()
    c.async_request_refresh.assert_not_awaited()


def test_disabled_modes_do_not_expand_entity_temperature_envelope(ha_modules):
    classes = importlib.import_module(ha_modules + ".climate")
    c = capability_coordinator(
        capabilities={
            **TEMPERATURE_CAPABILITIES, "TempRange1Low2": "5", "TempRange1High2": "99"
        }
    )
    entity = classes.AE200Climate(c, "1")

    assert "heat_cool" not in entity.hvac_modes
    assert (entity.min_temp, entity.max_temp) == (17, 30)


def test_missing_mode_range_uses_ha_fallback_without_hiding_other_reported_limits(ha_modules):
    classes = importlib.import_module(ha_modules + ".climate")
    c = capability_coordinator(
        capabilities={
            **TEMPERATURE_CAPABILITIES, "TempRange1High1": "40", "TempRange1Low3": ""
        }
    )
    entity = classes.AE200Climate(c, "1")

    assert (entity.min_temp, entity.max_temp) == (7, 40)


@pytest.mark.parametrize("capability,expected", [("ENABLE", 0.5), ("DISABLE", 1.0)])
def test_temperature_step_uses_reported_celsius_capability(ha_modules, capability, expected):
    classes = importlib.import_module(ha_modules + ".climate")
    c = capability_coordinator(capabilities={"TempDetailC": capability})

    assert classes.AE200Climate(c, "1").target_temperature_step == expected


def test_missing_temperature_capabilities_preserve_existing_ha_defaults(ha_modules):
    classes = importlib.import_module(ha_modules + ".climate")
    entity = classes.AE200Climate(capability_coordinator(), "1")

    assert (entity.min_temp, entity.max_temp) == (7.0, 35.0)
    assert entity.target_temperature_step == 0.5


def test_local_remote_prohibitions_do_not_relabel_central_hardware_capabilities(ha_modules):
    classes = importlib.import_module(ha_modules + ".climate")
    c = capability_coordinator(
        capabilities=AC_CAPABILITIES,
        attrs={"ModeItem": "CHK_ON", "SetTempItem": "CHK_ON", "RemoCon": "PROHIBIT"},
    )
    entity = classes.AE200Climate(c, "1")

    assert "cool" in entity.hvac_modes
    assert entity.supported_features & classes.ClimateEntityFeature.TARGET_TEMPERATURE
    assert entity.extra_state_attributes["local_remote_prohibited"] is True


def test_new_group_hides_unverified_temperature_control_without_changing_identity(ha_modules):
    classes = importlib.import_module(ha_modules + ".climate")
    c = capability_coordinator(capabilities={**AC_CAPABILITIES, "GroupType": "NEW"})
    entity = classes.AE200Climate(c, "1")

    assert not entity.supported_features & classes.ClimateEntityFeature.TARGET_TEMPERATURE
    assert entity.supported_features & classes.ClimateEntityFeature.TURN_ON
    assert entity.supported_features & classes.ClimateEntityFeature.TURN_OFF
    assert entity._attr_unique_id == "fixture.invalid_1_climate"


@pytest.mark.asyncio
async def test_new_group_can_receive_a_pure_hvac_mode_command(ha_modules):
    classes = importlib.import_module(ha_modules + ".climate")
    c = capability_coordinator(capabilities={**AC_CAPABILITIES, "GroupType": "NEW"})
    c.client._request = AsyncMock(
        return_value="<Packet><Command>setResponse</Command><DatabaseManager /></Packet>"
    )
    c.async_request_refresh = AsyncMock()

    await classes.AE200Climate(c, "1").async_set_temperature(hvac_mode="cool")

    c.client._request.assert_awaited_once()
    packet = ET.fromstring(c.client._request.call_args.args[0])
    assert packet.find("./DatabaseManager/Mnet").attrib == {
        "Group": "1", "Drive": "ON", "Mode": "COOL"
    }
    c.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [{}, {"hvac_mode": "cool"}, {"hvac_mode": "off"}])
async def test_new_group_direct_temperature_commands_reach_library_guard_without_writing(
    ha_modules, extra
):
    classes = importlib.import_module(ha_modules + ".climate")
    c = capability_coordinator(capabilities={**AC_CAPABILITIES, "GroupType": "NEW"})
    c.client._request = AsyncMock()
    c.async_request_refresh = AsyncMock()

    with pytest.raises(ValueError, match="NEW group temperature fields"):
        await classes.AE200Climate(c, "1").async_set_temperature(temperature=22, **extra)

    c.client._request.assert_not_awaited()
    c.async_request_refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_ha_diagnostics_are_cached_and_do_not_export_host_or_entry_data(ha_modules):
    diagnostics = importlib.import_module(ha_modules + ".diagnostics")
    c = coordinator()
    c.update_interval = types.SimpleNamespace(total_seconds=lambda: 30)
    entry = types.SimpleNamespace(entry_id="fixture", data={"password": "secret"})
    hass = types.SimpleNamespace(data={"ae200": {"fixture": c}})
    result = await diagnostics.async_get_config_entry_diagnostics(hass, entry)
    assert result["controller"]["groups"]["1"]["raw_attributes"]["Drive"] == "ON"
    assert "fixture.invalid" not in str(result)
    assert "secret" not in str(result)
