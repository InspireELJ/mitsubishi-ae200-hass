"""Use real integration entities with minimal HA API boundary stubs.

These verify local contracts, not an end-to-end Home Assistant installation.
"""

from __future__ import annotations

import importlib
import sys
import time
import types
from enum import IntFlag, StrEnum
from pathlib import Path

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
        ClimateEntity=type("ClimateEntity", (), {}),
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
    from unittest.mock import AsyncMock

    classes = importlib.import_module(ha_modules + ".climate")
    c = coordinator()
    c.client.set_fan_speed = AsyncMock()
    c.async_request_refresh = AsyncMock()
    await classes.AE200Climate(c, "1").async_set_fan_mode(mode)
    c.client.set_fan_speed.assert_awaited_once_with("1", raw)
    c.async_request_refresh.assert_awaited_once()


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
