"""Unavailable flags and cached command details must not imply fresh evidence."""

from __future__ import annotations

import asyncio
import copy
import importlib
import json
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_ha_contract import capability_coordinator
from test_ha_contract import ha_modules as ha_modules
from test_ha_observability import coordinator
from test_ha_observability import observability_modules as observability_modules

from pymelans import MelansClient, cached_snapshot

FLAGS = ("filter_sign", "error_sign", "local_remote_prohibited")
ITEMS = ("DriveItem", "ModeItem", "SetTempItem", "FilterItem")
STATE = {"Drive": "OFF", "Mode": "COOL", "SetTemp": "25", "InletTemp": "23.5",
         "FanSpeed": "HIGH", "AirDirection": "AUTO", "FilterSign": "OFF",
         "ErrorSign": "OFF", "RemoCon": "PERMIT"}


@pytest.fixture
def climate_context(ha_modules):
    c = capability_coordinator(attrs=dict(STATE))
    c.client.group_last_success["1"] = datetime.now(UTC)
    c.client._request = AsyncMock()
    c.async_request_refresh = AsyncMock()
    entity = importlib.import_module(ha_modules + ".climate").AE200Climate(c, "1")
    return SimpleNamespace(coordinator=c, client=c.client, entity=entity)


@pytest.mark.parametrize("sign,remote,expected", [("OFF", "PERMIT", False), ("ON", "PROHIBIT", True)])
def test_fresh_known_bool_attributes_keep_their_values_and_identity(climate_context, sign, remote, expected):
    context = climate_context
    context.coordinator.data["1"].update({**STATE, "FilterSign": sign, "ErrorSign": sign, "RemoCon": remote})
    attrs = context.entity.extra_state_attributes
    assert context.entity.available and attrs["data_quality"] == "fresh"
    assert all(attrs[key] is expected for key in FLAGS)
    assert context.entity._attr_unique_id == "fixture.invalid_1_climate"
    assert context.entity.device_info["identifiers"] == {("ae200", "fixture.invalid:1")}
    assert context.entity.current_temperature == 23.5 and context.entity.target_temperature == 25
    context.client._request.assert_not_awaited()
    context.coordinator.async_request_refresh.assert_not_awaited()


@pytest.mark.parametrize("token", [None, "", "*", "FUTURE"])
def test_fresh_missing_or_unrecognised_flags_stay_none_without_changing_raw(climate_context, token):
    context = climate_context
    state = {**STATE, "FilterSign": token, "ErrorSign": token, "RemoCon": token}
    if token is None:
        for field in ("FilterSign", "ErrorSign", "RemoCon"):
            state.pop(field)
    context.coordinator.data["1"].update(state)
    assert all(context.entity.extra_state_attributes[key] is None for key in FLAGS)
    assert context.coordinator.data["1"].raw_attributes() == state
    context.client._request.assert_not_awaited()


@pytest.mark.parametrize("failure", ["unknown", "inconsistent-unknown", "expired", "group-failed", "coordinator", "missing-device"])
def test_unavailable_or_nonfresh_bool_attributes_are_none_while_raw_cache_survives(climate_context, failure):
    context = climate_context
    if failure == "unknown":
        context.client._group_seen.pop("1")
        context.client.group_last_success.pop("1")
    elif failure == "inconsistent-unknown":
        # An isolated seen timestamp must not override unknown read quality.
        context.client.group_last_success.pop("1")
    elif failure == "expired":
        context.client._group_seen["1"] = time.monotonic() - context.client.stale_after - 1
    elif failure == "group-failed":
        context.client.failed_groups = {"1"}
    elif failure == "coordinator":
        context.coordinator.last_update_success = False
    else:
        context.coordinator.data.pop("1")
    attrs = context.entity.extra_state_attributes
    assert all(attrs[key] is None for key in FLAGS)
    assert context.entity.available is (failure == "inconsistent-unknown")
    if failure == "missing-device":
        assert attrs == dict.fromkeys(FLAGS)
        assert context.entity.current_temperature is context.entity.target_temperature is None
    else:
        assert context.entity.current_temperature == 23.5 and context.entity.target_temperature == 25
        assert context.coordinator.data["1"].raw_attributes() == STATE
        assert cached_snapshot(context.client)["groups"]["1"]["raw_attributes"] == STATE
        assert attrs["air_direction"] == "AUTO" and attrs["fan_speed_raw"] == "HIGH"
    context.client._request.assert_not_awaited()
    context.coordinator.async_request_refresh.assert_not_awaited()


@pytest.mark.parametrize("token", ["CHK_ON", "CHK_OFF"])
def test_last_command_shows_all_four_exact_permission_flags_without_changing_the_command(observability_modules, token):
    c = coordinator()
    c.last_update_success = False
    command = {"group_id": "1", "at": "2026-01-01T00:00:00+00:00", "status": "confirmed",
               "attributes": {"RemoCon": "PROHIBIT", **dict.fromkeys(ITEMS, token)}}
    c.client.last_command = copy.deepcopy(command)
    entity = observability_modules.sensor.AE200LastCommandStatus(c)
    assert entity.available and entity.native_value == "confirmed"
    assert entity._attr_unique_id == "fixture.invalid_last_command_status"
    assert entity._attr_entity_registry_enabled_default is False
    expected = entity.extra_state_attributes["expected_attributes"]
    assert expected == command["attributes"]
    expected["FilterItem"] = "caller"
    assert c.client.last_command == command
    c.client._request.assert_not_awaited()
    c.client.poll.assert_not_awaited()


@pytest.mark.parametrize("bad", [None, "", "*", "chk_on", "FUTURE", "private.invalid", True, 1, [], {}])
def test_invalid_permission_values_are_omitted_while_existing_safe_fields_remain(observability_modules, bad):
    c = coordinator()
    c.client.last_command = {"status": "confirmed", "attributes": {
        "Drive": "ON", "Mode": "COOL", "SetTemp": "22.0", "FanSpeed": "MID2",
        "AirDirection": "SWING", "RemoCon": "PERMIT", **dict.fromkeys(ITEMS, bad),
        "Password": "private-secret", "error": "private-error",
    }}
    entity = observability_modules.sensor.AE200LastCommandStatus(c)
    assert entity.extra_state_attributes == {"expected_attributes": {
        "Drive": "ON", "Mode": "COOL", "SetTemp": "22", "FanSpeed": "MID2",
        "AirDirection": "SWING", "RemoCon": "PERMIT",
    }}
    assert "private" not in json.dumps(entity.extra_state_attributes)
    c.client._request.assert_not_awaited()


@pytest.mark.parametrize("error_type", [
    "MelansError", "MelansConnectionError", "MelansCommandUncertainError",
    "MelansProtocolError", "MelansAuthError", "CancelledError",
])
@pytest.mark.parametrize("status", ["failed", "outcome_unknown"])
def test_only_failure_states_show_bounded_known_error_metadata(observability_modules, error_type, status):
    c = coordinator()
    c.client.last_command = {"status": status, "error_type": error_type, "error_code": "0101_A-b",
                            "error": "http://private.invalid/secret", "point": "private-point"}
    entity = observability_modules.sensor.AE200LastCommandStatus(c)
    assert entity.native_value == status
    assert entity.extra_state_attributes == {"error_type": error_type, "error_code": "0101_A-b"}
    assert "private" not in json.dumps(entity.extra_state_attributes)
    c.client._request.assert_not_awaited()


@pytest.mark.parametrize("code,expected", [
    (None, {"error_code": None}), ("A" * 32, {"error_code": "A" * 32}),
    ("", {}), ("*", {}), ("A" * 33, {}), ("private.invalid/path", {}), ("١", {}),
    (True, {}), (1, {}), ([], {}), ({}, {}),
])
def test_error_codes_are_bounded_ascii_or_explicit_none_with_no_string_coercion(observability_modules, code, expected):
    c = coordinator()
    c.client.last_command = {"status": "failed", "error_type": "MelansProtocolError", "error_code": code}
    entity = observability_modules.sensor.AE200LastCommandStatus(c)
    assert entity.extra_state_attributes == {"error_type": "MelansProtocolError", **expected}
    c.client.last_command.pop("error_code")
    assert entity.extra_state_attributes == {"error_type": "MelansProtocolError"}


@pytest.mark.parametrize("bad_type", [None, "", "private.invalid", "FutureError", [], {}])
def test_unknown_or_malformed_error_type_cannot_publish_a_code(observability_modules, bad_type):
    c = coordinator()
    c.client.last_command = {"status": "outcome_unknown", "error_type": bad_type, "error_code": "0101"}
    assert observability_modules.sensor.AE200LastCommandStatus(c).extra_state_attributes == {}


@pytest.mark.parametrize("status", ["unknown", "pending", "accepted_unconfirmed", "not_yet_confirmed", "confirmed", "no_change"])
def test_nonfailure_state_does_not_show_old_error_metadata_or_add_a_new_status(observability_modules, status):
    c = coordinator()
    c.client.last_command = {"status": status, "error_type": "CancelledError", "error_code": None}
    entity = observability_modules.sensor.AE200LastCommandStatus(c)
    assert entity.native_value == ("unknown" if status == "no_change" else status)
    assert entity.extra_state_attributes == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_one_stubbed_command_projects_ack_or_cancel_without_replay_or_io_from_properties(observability_modules, cancel):
    c = coordinator()
    c.client = MelansClient("fixture.invalid")
    c.client._request = AsyncMock(
        side_effect=asyncio.CancelledError() if cancel else None,
        return_value="<Packet><Command>setResponse</Command></Packet>",
    )
    expected = {"RemoCon": "PROHIBIT", "DriveItem": "CHK_OFF", "ModeItem": "CHK_OFF",
                "SetTempItem": "CHK_OFF", "FilterItem": "CHK_ON"}
    if cancel:
        with pytest.raises(asyncio.CancelledError):
            await c.client._write("1", expected)
    else:
        await c.client._write("1", expected)
    entity = observability_modules.sensor.AE200LastCommandStatus(c)
    assert entity.native_value == ("outcome_unknown" if cancel else "accepted_unconfirmed")
    for _ in range(3):
        assert entity.extra_state_attributes["expected_attributes"] == expected
    if cancel:
        assert entity.extra_state_attributes["error_type"] == "CancelledError"
        assert entity.extra_state_attributes["error_code"] is None
    else:
        assert "error_type" not in entity.extra_state_attributes
        c.client._confirm_readback("1", expected, sampled_at=c.client._group_write_ack_at["1"] + 1)
        assert entity.native_value == "confirmed"
    c.client._request.assert_awaited_once()
