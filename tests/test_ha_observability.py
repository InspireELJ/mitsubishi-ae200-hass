"""Cached command and schedule entities at the Home Assistant API boundary."""

from __future__ import annotations

import importlib
import json
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from pymelans import MelansDevice


@pytest.fixture
def observability_modules(monkeypatch):
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

    @dataclass(frozen=True, kw_only=True)
    class Description:
        key: str
        translation_key: str
        device_class: str | None = None
        native_unit_of_measurement: str | None = None
        state_class: str | None = None
        entity_category: str | None = None

    for name in ("homeassistant", "homeassistant.helpers", "homeassistant.components"):
        module(name).__path__ = []
    module("homeassistant.helpers.device_registry", DeviceInfo=dict)
    module("homeassistant.helpers.update_coordinator", CoordinatorEntity=CoordinatorEntity)
    module("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
    module("homeassistant.core", HomeAssistant=object)
    module("homeassistant.config_entries", ConfigEntry=object)
    module(
        "homeassistant.const",
        EntityCategory=types.SimpleNamespace(DIAGNOSTIC="diagnostic"),
        UnitOfTemperature=types.SimpleNamespace(CELSIUS="°C"),
        PERCENTAGE="%",
    )
    module(
        "homeassistant.components.sensor",
        SensorEntity=type("SensorEntity", (), {}),
        SensorEntityDescription=Description,
        SensorDeviceClass=types.SimpleNamespace(
            TEMPERATURE="temperature", HUMIDITY="humidity", TIMESTAMP="timestamp", ENUM="enum"
        ),
        SensorStateClass=types.SimpleNamespace(MEASUREMENT="measurement"),
    )
    module(
        "homeassistant.components.binary_sensor",
        BinarySensorEntity=type("BinarySensorEntity", (), {}),
        BinarySensorDeviceClass=types.SimpleNamespace(PROBLEM="problem"),
    )
    name = "ae200_observability_test"
    package = module(name)
    package.__path__ = [str(Path(__file__).resolve().parents[1] / "custom_components/ae200")]
    module(name + ".coordinator", AE200Coordinator=object)
    yield types.SimpleNamespace(
        sensor=importlib.import_module(name + ".sensor"),
        binary_sensor=importlib.import_module(name + ".binary_sensor"),
    )
    for key in list(sys.modules):
        if key.startswith(name + "."):
            sys.modules.pop(key, None)


def coordinator():
    client = types.SimpleNamespace(
        last_command=None,
        controller_metadata=None,
        health="online",
        transport_name="websocket",
        failed_groups=set(),
        poll=AsyncMock(side_effect=AssertionError("Entities must use the cache")),
        _request=AsyncMock(side_effect=AssertionError("Entities must not send requests")),
        is_group_available=lambda _: True,
    )
    return types.SimpleNamespace(
        host="fixture.invalid",
        client=client,
        data={"1": MelansDevice("1", "Fixture", {"Schedule": "OFF"})},
        last_update_success=True,
        async_add_passive_listener=Mock(return_value=Mock()),
    )


@pytest.mark.parametrize(
    "status",
    ["unknown", "pending", "accepted_unconfirmed", "not_yet_confirmed", "confirmed", "failed",
     "outcome_unknown"],
)
def test_command_status_is_cached_and_available_during_outage(observability_modules, status):
    c = coordinator()
    c.last_update_success = False
    entity = observability_modules.sensor.AE200LastCommandStatus(c)
    assert entity.native_value == "unknown"
    assert entity.extra_state_attributes == {}
    c.client.last_command = {"status": status}
    assert entity.native_value == status
    assert entity.available
    assert entity._attr_unique_id == "fixture.invalid_last_command_status"
    assert entity._attr_entity_registry_enabled_default is False
    c.client.poll.assert_not_called()
    c.client._request.assert_not_called()


def test_command_attributes_allow_only_expected_protocol_values(observability_modules):
    c = coordinator()
    c.client.last_command = {
        "status": "confirmed",
        "group_id": "1",
        "at": "2026-10-16T01:00:00+00:00",
        "host": "private.invalid",
        "error": "private.invalid response",
        "attributes": {
            "Drive": "ON", "Mode": "COOL", "SetTemp": "22.0", "FanSpeed": "MID2",
            "AirDirection": "SWING", "RemoCon": "PERMIT", "Password": "secret",
            "Schedule": "ON", "IPAddress": "private.invalid",
        },
    }
    entity = observability_modules.sensor.AE200LastCommandStatus(c)
    attrs = entity.extra_state_attributes
    assert attrs == {
        "group_id": "1",
        "at": "2026-10-16T01:00:00+00:00",
        "expected_attributes": {
            "Drive": "ON", "Mode": "COOL", "SetTemp": "22", "FanSpeed": "MID2",
            "AirDirection": "SWING", "RemoCon": "PERMIT",
        },
    }
    attrs["expected_attributes"]["Drive"] = "OFF"
    assert c.client.last_command["attributes"]["Drive"] == "ON"
    assert "private.invalid" not in str(attrs)
    assert "secret" not in str(attrs)


@pytest.mark.parametrize("temperature", ["nan", "inf", "-inf", "private.invalid"])
def test_invalid_command_details_do_not_leak_as_attributes(observability_modules, temperature):
    c = coordinator()
    c.client.last_command = {
        "status": "private.invalid",
        "group_id": "private.invalid",
        "at": "private.invalid",
        "attributes": {"Mode": "private.invalid", "SetTemp": temperature},
    }
    entity = observability_modules.sensor.AE200LastCommandStatus(c)
    assert entity.native_value == "unknown"
    assert entity.extra_state_attributes == {}


@pytest.mark.asyncio
async def test_schedule_entities_only_created_for_valid_cache_fields(observability_modules):
    c = coordinator()
    for index, value in enumerate(("ON", "", "*", "0", "UNKNOWN", None), 2):
        c.data[str(index)] = MelansDevice(str(index), None, {"Schedule": value})
    c.data["8"] = MelansDevice("8", None, {})
    entry = types.SimpleNamespace(entry_id="fixture")
    hass = types.SimpleNamespace(data={"ae200": {"fixture": c}})
    entities = []
    await observability_modules.binary_sensor.async_setup_entry(hass, entry, entities.extend)
    schedules = [e for e in entities if isinstance(e, observability_modules.binary_sensor.AE200ScheduleOn)]
    assert [e._group_id for e in schedules] == ["1", "2"]
    assert [e.is_on for e in schedules] == [False, True]
    assert all(e._attr_entity_registry_enabled_default is False for e in schedules)
    c.client.poll.assert_not_called()
    c.client._request.assert_not_called()


def test_schedule_state_handles_disappearing_or_unknown_cache(observability_modules):
    c = coordinator()
    entity = observability_modules.binary_sensor.AE200ScheduleOn(c, "1")
    assert entity._attr_unique_id == "fixture.invalid_1_schedule_on"
    assert entity.is_on is False
    for value in ("*", "", "UNKNOWN"):
        c.data["1"].update({"Schedule": value})
        assert entity.is_on is None
    c.data.clear()
    assert entity.is_on is None
    assert not entity.available


@pytest.mark.parametrize("flag,available", [("OFF", "ON"), ("ON", "OFF"), ("ON", "ON"), ("OFF", "OFF")])
def test_schedule_flag_and_availability_remain_independent_cached_fields(
    observability_modules, flag, available
):
    c = coordinator()
    c.data["1"].update({"Schedule": flag, "ScheduleAvail": available})
    entity = observability_modules.binary_sensor.AE200ScheduleOn(c, "1")

    assert entity._attr_unique_id == "fixture.invalid_1_schedule_on"
    assert entity.is_on is (flag == "ON")
    assert entity.extra_state_attributes == {
        "schedule_available": available == "ON",
        "raw_schedule_flag": flag,
        "raw_schedule_available": available,
    }
    c.client.poll.assert_not_called()
    c.client._request.assert_not_called()


@pytest.mark.parametrize("value,raw", [(None, None), ("", ""), ("*", "*"), ("FUTURE", None), ("private.invalid", None)])
def test_schedule_availability_unknowns_are_nullable_and_raw_values_allowlisted(
    observability_modules, value, raw
):
    c = coordinator()
    c.data["1"].update({"Schedule": value, "ScheduleAvail": value})
    entity = observability_modules.binary_sensor.AE200ScheduleOn(c, "1")

    assert entity.is_on is None
    assert entity.extra_state_attributes == {
        "schedule_available": None, "raw_schedule_flag": raw, "raw_schedule_available": raw,
    }


@pytest.mark.parametrize("failure", ["group", "coordinator", "missing"])
def test_unavailable_schedule_never_claims_typed_availability(observability_modules, failure):
    c = coordinator()
    c.data["1"].update({"Schedule": "OFF", "ScheduleAvail": "ON"})
    if failure == "group":
        c.client.is_group_available = lambda _: False
    elif failure == "coordinator":
        c.last_update_success = False
    else:
        c.data.clear()
    entity = observability_modules.binary_sensor.AE200ScheduleOn(c, "1")

    assert not entity.available
    assert entity.extra_state_attributes["schedule_available"] is None
    assert entity.extra_state_attributes["raw_schedule_available"] == (None if failure == "missing" else "ON")
    assert entity.is_on is (None if failure == "missing" else False)
    c.client.poll.assert_not_called()
    c.client._request.assert_not_called()


def test_expired_schedule_sample_retains_raw_without_typed_availability(observability_modules, monkeypatch):
    from datetime import UTC, datetime

    from pymelans import MelansClient

    c = coordinator()
    c.client = MelansClient("fixture.invalid", stale_after=20)
    c.data["1"].update({"Schedule": "ON", "ScheduleAvail": "ON"})
    c.client.devices = c.data
    c.client._group_seen = {"1": 100.0}
    c.client.group_last_success = {"1": datetime.now(UTC)}
    monkeypatch.setattr("pymelans.client.time.monotonic", lambda: 121.0)
    entity = observability_modules.binary_sensor.AE200ScheduleOn(c, "1")

    assert not entity.available
    assert entity.is_on is True
    assert entity.extra_state_attributes == {
        "schedule_available": None, "raw_schedule_flag": "ON", "raw_schedule_available": "ON",
    }


def test_schedule_name_does_not_claim_availability_or_execution():
    component = Path(__file__).resolve().parents[1] / "custom_components/ae200"
    for path in (component / "strings.json", component / "translations/en.json"):
        strings = json.loads(path.read_text())
        assert strings["entity"]["binary_sensor"]["schedule_on"]["name"] == "Controller schedule flag"


def test_existing_diagnostic_unique_ids_stay_compatible(observability_modules):
    c = coordinator()
    sensors = observability_modules.sensor
    binary = observability_modules.binary_sensor
    assert sensors.AE200ControllerStatus(c)._attr_unique_id == "fixture.invalid_controller_status"
    assert sensors.AE200LastSuccessfulRead(c)._attr_unique_id == "fixture.invalid_last_successful_read"
    assert binary.AE200FilterSign(c, "1")._attr_unique_id == "fixture.invalid_1_filter_sign"
    assert binary.AE200ErrorSign(c, "1")._attr_unique_id == "fixture.invalid_1_error_sign"
    assert binary.AE200CommunicationProblem(c, "1")._attr_unique_id == "fixture.invalid_1_communication_problem"


def test_controller_metadata_is_shown_from_safe_cached_fields_only(observability_modules):
    c = coordinator()
    c.client.controller_metadata = types.SimpleNamespace(
        model="AE-200E", version="8.01", version_if="1.07",
        host="private.invalid", license="private-licence", serial="private-serial",
    )
    entity = observability_modules.sensor.AE200ControllerStatus(c)

    assert entity.extra_state_attributes == {
        "transport": "websocket", "failed_groups": [],
        "model": "AE-200E", "version": "8.01", "version_if": "1.07",
    }
    assert entity.device_info["identifiers"] == {("ae200", "fixture.invalid")}
    assert entity._attr_unique_id == "fixture.invalid_controller_status"
    c.client.poll.assert_not_called()
    c.client._request.assert_not_called()


@pytest.mark.parametrize(
    "metadata,expected",
    [
        (None, {}),
        (types.SimpleNamespace(model=None, version=None, version_if=None), {}),
        (types.SimpleNamespace(model=None, version="8.00", version_if="1.00"),
         {"version": "8.00", "version_if": "1.00"}),
    ],
)
def test_missing_or_partial_controller_metadata_does_not_invent_values(
    observability_modules, metadata, expected
):
    c = coordinator()
    c.client.controller_metadata = metadata
    c.last_update_success = False
    entity = observability_modules.sensor.AE200ControllerStatus(c)

    assert entity.extra_state_attributes == {
        "transport": "websocket", "failed_groups": [], **expected
    }
    assert entity.available
    c.client.poll.assert_not_called()
    c.client._request.assert_not_called()


@pytest.mark.asyncio
async def test_controller_metadata_does_not_create_additional_entities(observability_modules):
    c = coordinator()
    entry = types.SimpleNamespace(entry_id="fixture", async_on_unload=Mock())
    hass = types.SimpleNamespace(data={"ae200": {"fixture": c}})
    before = []
    await observability_modules.sensor.async_setup_entry(hass, entry, before.extend)
    c.client.controller_metadata = types.SimpleNamespace(
        model="AE-200E", version="8.01", version_if="1.07"
    )
    after = []
    await observability_modules.sensor.async_setup_entry(hass, entry, after.extend)

    assert [e._attr_unique_id for e in before] == [e._attr_unique_id for e in after]
    assert len(before) == 3
    c.client.poll.assert_not_called()
    c.client._request.assert_not_called()


def test_observability_translations_are_synchronised():
    component = Path(__file__).resolve().parents[1] / "custom_components/ae200"
    source = json.loads((component / "strings.json").read_text(encoding="utf-8"))
    english = json.loads((component / "translations/en.json").read_text(encoding="utf-8"))
    assert source == english
    assert source["entity"]["binary_sensor"]["schedule_on"]["name"] == "Controller schedule flag"
    assert set(source["entity"]["sensor"]["last_command_status"]["state"]) == {
        "unknown", "pending", "accepted_unconfirmed", "not_yet_confirmed", "confirmed", "failed",
        "outcome_unknown"
    }
