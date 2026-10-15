"""Explicit wall-remote patches delegate one transaction without service retries."""

from __future__ import annotations

import asyncio
import itertools
import json
import re
import types
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import voluptuous as vol
from test_ha_controller_clock import clock_service as clock_service

ROOT = Path(__file__).resolve().parents[1]
DATA = {"config_entry_id": "selected", "group_id": 4}
FIELDS = {
    "power_prohibited": "power", "mode_prohibited": "mode",
    "temperature_prohibited": "temperature", "filter_reset_prohibited": "filter_reset",
}


@pytest.fixture
def restriction_service(clock_service):
    c = clock_service
    c.client.async_set_local_restrictions = AsyncMock()
    c.services.async_setup_services(c.hass)
    return c


async def call(context, data):
    registration = context.hass.services.registered["ae200", "set_local_restrictions"]
    validated = registration["schema"](data)
    return await registration["handler"](types.SimpleNamespace(data=validated))


def report(status="confirmed"):
    return {
        "group_id": "4", "at": "2026-10-16T00:00:00+00:00", "status": status,
        "requested": {"filter_reset": True},
        "before_raw": {"RemoCon": "PERMIT", "DriveItem": "CHK_OFF", "ModeItem": "CHK_OFF",
                       "SetTempItem": "CHK_OFF", "FilterItem": "CHK_OFF"},
        "before": {"power": False, "mode": False, "temperature": False, "filter_reset": False},
        "expected_raw": {"RemoCon": "PROHIBIT", "DriveItem": "CHK_OFF", "ModeItem": "CHK_OFF",
                         "SetTempItem": "CHK_OFF", "FilterItem": "CHK_ON"},
        "after_raw": {}, "after": None, "readback_at": None, "readback_matches": None,
        "error": None, "write_error": None, "readback_error": None,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("value", [None, 0, 1, 0.0, 1.0, "true", "false", "ON", [], {}])
async def test_restrictions_require_real_bools_without_coercion(restriction_service, field, value):
    c = restriction_service

    with pytest.raises(vol.Invalid):
        await call(c, {**DATA, field: value})

    c.client.async_set_local_restrictions.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "supplied",
    [dict((field, index % 2 == 1) for index, (field, enabled) in enumerate(zip(FIELDS, flags, strict=True)) if enabled)
     for flags in itertools.product((False, True), repeat=len(FIELDS)) if any(flags)],
)
async def test_only_supplied_values_are_mapped_including_false(restriction_service, supplied):
    c = restriction_service
    c.client.async_set_local_restrictions.return_value = report()

    await call(c, {**DATA, **supplied})

    c.client.async_set_local_restrictions.assert_awaited_once_with(
        4, {FIELDS[field]: value for field, value in supplied.items()}
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [
    DATA, {"group_id": 4, "filter_reset_prohibited": True},
    {"config_entry_id": "selected", "filter_reset_prohibited": True},
    {**DATA, "config_entry_id": "", "filter_reset_prohibited": True},
    *({**DATA, "group_id": group, "filter_reset_prohibited": True}
      for group in (0, 51, True, 4.0, "4.0", "*", [4])),
    *({**DATA, "filter_reset_prohibited": True, key: value}
      for key, value in (("all", True), ("address", 1), ("RemoCon", "PERMIT"),
                         ("restore_snapshot", {}), ("FanSpeedItem", "CHK_OFF"))),
])
async def test_schema_requires_explicit_scope_a_nonempty_patch_and_no_extra_fields(restriction_service, data):
    c = restriction_service

    with pytest.raises(vol.Invalid):
        await call(c, data)

    c.client.async_set_local_restrictions.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("group,canonical", [(1, 1), (50, 50), ("004", 4)])
async def test_group_boundaries_use_one_selected_transaction(restriction_service, group, canonical):
    c = restriction_service
    c.client.async_set_local_restrictions.return_value = report()
    other = types.SimpleNamespace(async_set_local_restrictions=AsyncMock())
    c.entries["other"] = types.SimpleNamespace(domain="ae200")
    c.hass.data["ae200"]["other"] = types.SimpleNamespace(client=other)

    await call(c, {**DATA, "group_id": group, "filter_reset_prohibited": True})

    c.client.async_set_local_restrictions.assert_awaited_once_with(canonical, {"filter_reset": True})
    other.async_set_local_restrictions.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("entry_kind", ["missing", "wrong_domain", "unloaded"])
async def test_invalid_entry_never_uses_another_loaded_controller(restriction_service, entry_kind):
    c = restriction_service
    if entry_kind == "wrong_domain":
        c.entries["invalid"] = types.SimpleNamespace(domain="another_integration")
        c.hass.data["ae200"]["invalid"] = c.coordinator
    elif entry_kind == "unloaded":
        c.entries["invalid"] = types.SimpleNamespace(domain="ae200")
    exception = c.HomeAssistantError if entry_kind == "unloaded" else c.ServiceValidationError

    with pytest.raises(exception):
        await call(c, {**DATA, "config_entry_id": "invalid", "filter_reset_prohibited": True})

    c.client.async_set_local_restrictions.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [
    "no_change", "confirmed", "accepted_unconfirmed", "not_yet_confirmed", "outcome_unknown", "failed",
])
async def test_current_report_is_returned_without_retries_reads_or_refresh(restriction_service, status):
    c = restriction_service
    current = report(status)
    c.client.async_set_local_restrictions.return_value = current
    c.client.last_command = {"status": "confirmed", "group_id": "another", "at": "old"}

    result = await call(c, {**DATA, "filter_reset_prohibited": True})

    assert result is current and json.loads(json.dumps(result)) == current
    c.client.async_set_local_restrictions.assert_awaited_once_with(4, {"filter_reset": True})
    c.coordinator.async_request_refresh.assert_not_awaited()
    for name in ("async_read_member", "async_read_control_context", "async_read_today_schedule",
                 "async_read_controller_clock", "_write", "_request"):
        getattr(c.client, name).assert_not_awaited()


@pytest.mark.asyncio
async def test_precondition_validation_is_an_action_validation_error(restriction_service):
    c = restriction_service
    c.client.async_set_local_restrictions.side_effect = ValueError("requires a fresh available group")

    with pytest.raises(c.ServiceValidationError, match="fresh available group"):
        await call(c, {**DATA, "filter_reset_prohibited": True})

    c.coordinator.async_request_refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_unhandled_protocol_failure_is_safe_and_never_retried(restriction_service):
    c = restriction_service
    c.client.async_set_local_restrictions.side_effect = c.services.MelansError("http://private.invalid/")

    with pytest.raises(c.HomeAssistantError, match="local restriction change failed") as exc:
        await call(c, {**DATA, "filter_reset_prohibited": True})

    assert "private.invalid" not in str(exc.value)
    assert c.client.async_set_local_restrictions.await_count == 1
    c.coordinator.async_request_refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancellation_propagates_without_a_report_or_retry(restriction_service):
    c = restriction_service
    c.client.async_set_local_restrictions.side_effect = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await call(c, {**DATA, "filter_reset_prohibited": True})

    assert c.client.async_set_local_restrictions.await_count == 1
    c.coordinator.async_request_refresh.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [None, {}, [], {"status": "pending"}])
async def test_invalid_transaction_result_cannot_fall_back_to_an_old_command(restriction_service, invalid):
    c = restriction_service
    c.client.async_set_local_restrictions.return_value = invalid
    c.client.last_command = {"status": "confirmed"}

    with pytest.raises(c.HomeAssistantError, match="did not produce a local restriction report"):
        await call(c, {**DATA, "filter_reset_prohibited": True})


def test_restriction_action_schema_translation_and_response_contract(restriction_service):
    c = restriction_service
    registration = c.hass.services.registered["ae200", "set_local_restrictions"]
    assert registration["supports_response"] == c.SupportsResponse.ONLY
    component = ROOT / "custom_components/ae200"
    strings = json.loads((component / "strings.json").read_text())
    english = json.loads((component / "translations/en.json").read_text())
    action = strings["services"]["set_local_restrictions"]
    assert action == english["services"]["set_local_restrictions"]
    assert set(action["fields"]) == set(DATA) | set(FIELDS)
    assert all(field["name"] and field["description"] for field in action["fields"].values())
    assert "does not reset" in action["fields"]["filter_reset_prohibited"]["description"]
    yaml = (component / "services.yaml").read_text().split("set_local_restrictions:\n", 1)[1]
    yaml = re.split(r"(?m)^[a-z_]+:\n", yaml, maxsplit=1)[0]
    assert "config_entry:" in yaml and "integration: ae200" in yaml
    assert yaml.count("required: true") == 2 and yaml.count("boolean:") == 4
    assert "min: 1" in yaml and "max: 50" in yaml
