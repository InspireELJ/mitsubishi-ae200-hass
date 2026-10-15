"""Single-member runtime evidence requires every selector and performs no control."""

from __future__ import annotations

import asyncio
import json
import types
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import voluptuous as vol
from test_ha_controller_clock import clock_service as clock_service

DATA = {"config_entry_id": "selected", "address": 4,
        "date_iso": "2026-10-15", "data_name": "FAN_TIME"}
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def runtime_service(clock_service):
    context = clock_service
    context.client.async_read_member_runtime = AsyncMock()
    context.services.async_setup_services(context.hass)
    return context


async def call(context, data):
    registered = context.hass.services.registered["ae200", "read_member_runtime"]
    validated = registered["schema"](data)
    return await registered["handler"](types.SimpleNamespace(data=validated))


@pytest.mark.asyncio
@pytest.mark.parametrize("field", DATA)
async def test_every_selector_must_be_explicit(runtime_service, field):
    context = runtime_service
    with pytest.raises(vol.Invalid):
        await call(context, {key: value for key, value in DATA.items() if key != field})
    context.client.async_read_member_runtime.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("entry_kind", ["missing", "wrong_domain", "unloaded"])
async def test_invalid_entry_never_uses_another_loaded_controller(runtime_service, entry_kind):
    context = runtime_service
    if entry_kind == "wrong_domain":
        context.entries["invalid"] = types.SimpleNamespace(domain="another_integration")
        context.hass.data["ae200"]["invalid"] = context.coordinator
    elif entry_kind == "unloaded":
        context.entries["invalid"] = types.SimpleNamespace(domain="ae200")
    exception = context.HomeAssistantError if entry_kind == "unloaded" else context.ServiceValidationError
    with pytest.raises(exception):
        await call(context, {**DATA, "config_entry_id": "invalid"})
    context.client.async_read_member_runtime.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [
    "1969-12-31", "2038-01-01", "2023-02-29", "2024-02-30", "2024-04-31",
    "2024-2-01", "2024-02-1", "2024-02-29 ", " 2024-02-29", "２０２４-02-29",
    "2024-02-29T00:00:00", "2024-02-29Z", date(2024, 2, 29), None, True, 20240229,
])
async def test_dates_are_strict_explicit_gregorian_and_pilot_bounded(runtime_service, value):
    context = runtime_service
    with pytest.raises(vol.Invalid):
        await call(context, {**DATA, "date_iso": value})
    context.client.async_read_member_runtime.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [0, 251, True, False, 4.0, "4.0", "٤", "0004", "*", "4,5", [4]])
async def test_address_is_one_bounded_ascii_integer(runtime_service, value):
    context = runtime_service
    with pytest.raises(vol.Invalid):
        await call(context, {**DATA, "address": value})
    context.client.async_read_member_runtime.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["fan_time", "THERMO_TIME ", "ELECTRIC_ENERGY", "*", "FAN_TIME,THERMO_TIME", None, ["FAN_TIME"]])
async def test_runtime_channel_is_one_exact_supported_token(runtime_service, value):
    context = runtime_service
    with pytest.raises(vol.Invalid):
        await call(context, {**DATA, "data_name": value})
    context.client.async_read_member_runtime.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [
    {"group_id": 4}, {"addresses": [4]}, {"all": True}, {"start_collection": True},
    {"year": 2026}, {"date": "2026-10-15"}, {"sum": True},
])
async def test_extra_scope_or_control_fields_are_rejected(runtime_service, extra):
    context = runtime_service
    with pytest.raises(vol.Invalid):
        await call(context, {**DATA, **extra})
    context.client.async_read_member_runtime.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("address,day,channel", [
    (1, "1970-01-01", "FAN_TIME"), (250, "2037-12-31", "THERMO_TIME"),
    ("004", "2024-02-29", "FAN_TIME"),
])
async def test_valid_boundaries_forward_one_selector(runtime_service, address, day, channel):
    context = runtime_service
    snapshot = {"read_status": "updated", "quality": "fresh", "raw_attributes": {}}
    context.client.async_read_member_runtime.return_value = snapshot
    other = types.SimpleNamespace(async_read_member_runtime=AsyncMock())
    context.entries["other"] = types.SimpleNamespace(domain="ae200")
    context.hass.data["ae200"]["other"] = types.SimpleNamespace(client=other)
    assert await call(context, {**DATA, "address": address, "date_iso": day, "data_name": channel}) == snapshot
    context.client.async_read_member_runtime.assert_awaited_once_with(int(address), day, channel)
    other.async_read_member_runtime.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot", [
    {"read_status": "updated", "quality": "fresh", "raw_attributes": {"FanTimeData": ",".join(["0"] * 48)}},
    {"read_status": "updated", "quality": "fresh", "raw_attributes": {}, "buckets": []},
    {"read_status": "error", "quality": "unknown", "raw_attributes": {}, "buckets": []},
    {"read_status": "error", "quality": "stale", "raw_attributes": {"FanTimeData": "*"}, "buckets": []},
])
async def test_safe_runtime_evidence_is_preserved_without_inference_or_io(runtime_service, snapshot):
    context = runtime_service
    context.client.async_read_member_runtime.return_value = snapshot
    response = await call(context, DATA)
    assert response is snapshot and json.loads(json.dumps(response)) == snapshot
    assert not {"total", "daily_total", "hvac_action", "current_operation"} & response.keys()
    context.client.async_read_member_runtime.assert_awaited_once_with(4, "2026-10-15", "FAN_TIME")
    context.coordinator.async_request_refresh.assert_not_awaited()
    for name in ("async_read_member", "async_read_control_context", "async_read_today_schedule",
                 "async_read_controller_clock", "_request", "_write"):
        getattr(context.client, name).assert_not_awaited()


@pytest.mark.asyncio
async def test_cooldown_is_visible_without_another_channel_or_date_request(runtime_service):
    context = runtime_service
    context.client.async_read_member_runtime.return_value = {"read_status": "cooldown", "quality": "fresh"}
    with pytest.raises(context.HomeAssistantError, match="Wait at least 15 seconds"):
        await call(context, DATA)
    assert context.client.async_read_member_runtime.await_count == 1


@pytest.mark.asyncio
async def test_unverified_member_validation_is_not_replaced_by_a_group_probe(runtime_service):
    context = runtime_service
    context.client.async_read_member_runtime.side_effect = ValueError("member topology is not verified")
    with pytest.raises(context.ServiceValidationError, match="not verified"):
        await call(context, DATA)
    context.client._request.assert_not_awaited()


@pytest.mark.asyncio
async def test_protocol_failure_and_cancellation_never_retry(runtime_service):
    context = runtime_service
    context.client.async_read_member_runtime.side_effect = context.services.MelansError("private.invalid")
    with pytest.raises(context.HomeAssistantError) as error:
        await call(context, DATA)
    assert "private.invalid" not in str(error.value)
    context.client.async_read_member_runtime.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await call(context, DATA)
    assert context.client.async_read_member_runtime.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [None, {}, [], {"read_status": "partial"}])
async def test_missing_or_invalid_runtime_sample_is_an_error(runtime_service, invalid):
    context = runtime_service
    context.client.async_read_member_runtime.return_value = invalid
    with pytest.raises(context.HomeAssistantError, match="did not produce a member runtime sample"):
        await call(context, DATA)
    assert context.client.async_read_member_runtime.await_count == 1


def test_runtime_action_metadata_matches_explicit_read_contract(runtime_service):
    context = runtime_service
    registration = context.hass.services.registered["ae200", "read_member_runtime"]
    assert registration["supports_response"] == context.SupportsResponse.ONLY
    component = ROOT / "custom_components/ae200"
    strings = json.loads((component / "strings.json").read_text())
    english = json.loads((component / "translations/en.json").read_text())
    action = strings["services"]["read_member_runtime"]
    assert action == english["services"]["read_member_runtime"]
    assert set(action["fields"]) == set(DATA)
    assert all(field["name"] and field["description"] for field in action["fields"].values())
    assert "1970 to 2037" in action["fields"]["date_iso"]["description"]
    yaml = (component / "services.yaml").read_text().split("read_member_runtime:\n", 1)[1]
    assert "config_entry:" in yaml and "integration: ae200" in yaml
    assert "date:" in yaml and "- FAN_TIME" in yaml and "- THERMO_TIME" in yaml
    assert yaml.count("required: true") == 4
