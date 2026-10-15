"""Weekly pattern actions require every selector and leave equipment unchanged."""

from __future__ import annotations

import json
import types
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import voluptuous as vol
from test_ha_controller_clock import clock_service as clock_service

ROOT = Path(__file__).resolve().parents[1]
DATA = {"config_entry_id": "selected", "group_id": 4, "season": 1, "pattern": 2}


@pytest.fixture
def weekly_service(clock_service):
    c = clock_service
    c.client.async_read_weekly_pattern = AsyncMock()
    c.services.async_setup_services(c.hass)
    return c


async def call(context, data):
    registration = context.hass.services.registered["ae200", "read_weekly_pattern"]
    validated = registration["schema"](data)
    return await registration["handler"](types.SimpleNamespace(data=validated))


def sample(*, status="updated", quality="fresh", records=None, group="4", season="1", pattern="2"):
    return {
        "group_id": group, "season": season, "pattern": pattern,
        "read_status": status, "quality": quality,
        "last_attempt": "2026-10-16T00:00:00+00:00",
        "last_successful_read": "2026-10-16T00:00:00+00:00" if quality != "unknown" else None,
        "error": {"type": "MelansProtocolError", "code": None} if status == "error" else None,
        "records": records if records is not None else [], "issues": [],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("field", DATA)
async def test_each_selector_is_required_even_with_one_loaded_controller(weekly_service, field):
    c = weekly_service
    data = {key: value for key, value in DATA.items() if key != field}

    with pytest.raises(vol.Invalid):
        await call(c, data)

    c.client.async_read_weekly_pattern.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("field,maximum", [("group_id", 50), ("season", 5), ("pattern", 7)])
@pytest.mark.parametrize("value", [0, -1, True, False, 1.0, 1.5, "1.0", "*", "1,2", [1], None])
async def test_selectors_reject_non_integer_or_unbounded_input(weekly_service, field, maximum, value):
    c = weekly_service

    with pytest.raises(vol.Invalid):
        await call(c, {**DATA, field: value})
    with pytest.raises(vol.Invalid):
        await call(c, {**DATA, field: maximum + 1})

    c.client.async_read_weekly_pattern.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("address", 1), ("all", True), ("weekday", 1), ("enabled", True)])
async def test_extra_fields_cannot_select_another_scope_or_write(weekly_service, field, value):
    c = weekly_service

    with pytest.raises(vol.Invalid):
        await call(c, {**DATA, field: value})

    c.client.async_read_weekly_pattern.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "selectors,expected",
    [((1, 1, 1), (1, 1, 1)), ((50, 5, 7), (50, 5, 7)), (("004", "02", "007"), (4, 2, 7))],
)
async def test_concrete_selector_boundaries_are_forwarded_once(weekly_service, selectors, expected):
    c = weekly_service
    c.client.async_read_weekly_pattern.return_value = sample(
        group=str(expected[0]), season=str(expected[1]), pattern=str(expected[2])
    )
    data = dict(zip(("group_id", "season", "pattern"), selectors, strict=True))

    result = await call(c, {"config_entry_id": "selected", **data})

    c.client.async_read_weekly_pattern.assert_awaited_once_with(*expected)
    assert (result["group_id"], result["season"], result["pattern"]) == tuple(map(str, expected))


@pytest.mark.asyncio
@pytest.mark.parametrize("entry_kind", ["missing", "wrong_domain", "unloaded"])
async def test_invalid_entry_is_rejected_before_any_read(weekly_service, entry_kind):
    c = weekly_service
    if entry_kind == "wrong_domain":
        c.entries["invalid"] = types.SimpleNamespace(domain="another_integration")
        c.hass.data["ae200"]["invalid"] = c.coordinator
    elif entry_kind == "unloaded":
        c.entries["invalid"] = types.SimpleNamespace(domain="ae200")
    exception = c.HomeAssistantError if entry_kind == "unloaded" else c.ServiceValidationError

    with pytest.raises(exception):
        await call(c, {**DATA, "config_entry_id": "invalid"})

    c.client.async_read_weekly_pattern.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "snapshot",
    [sample(), sample(records=[{
        "index": "1", "hour": 8, "minute": 15,
        "raw_attributes": {"Index": "1", "Hour": "8", "Minute": "15", "Drive": "OFF"},
        "issues": [],
    }]), sample(status="error", quality="unknown"), sample(status="error", quality="stale")],
)
async def test_safe_snapshots_are_returned_without_refresh_or_control(weekly_service, snapshot):
    c = weekly_service
    c.client.async_read_weekly_pattern.return_value = snapshot
    other_client = types.SimpleNamespace(async_read_weekly_pattern=AsyncMock())
    c.entries["other"] = types.SimpleNamespace(domain="ae200")
    c.hass.data["ae200"]["other"] = types.SimpleNamespace(client=other_client)

    response = await call(c, DATA)

    assert response == snapshot and json.loads(json.dumps(response)) == snapshot
    assert not {"weekday", "enabled", "next_execution"} & response.keys()
    c.client.async_read_weekly_pattern.assert_awaited_once_with(4, 1, 2)
    other_client.async_read_weekly_pattern.assert_not_awaited()
    c.coordinator.async_request_refresh.assert_not_awaited()
    for name in ("async_read_member", "async_read_control_context", "async_read_today_schedule",
                 "async_read_controller_clock", "_write", "_request"):
        getattr(c.client, name).assert_not_awaited()


@pytest.mark.asyncio
async def test_cooldown_is_an_error_even_with_cached_rows(weekly_service):
    c = weekly_service
    c.client.async_read_weekly_pattern.return_value = sample(status="cooldown", records=[{"index": "1"}])

    with pytest.raises(c.HomeAssistantError, match="Wait at least 15 seconds"):
        await call(c, DATA)

    c.coordinator.async_request_refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_unconfigured_group_is_a_validation_error(weekly_service):
    c = weekly_service
    c.client.async_read_weekly_pattern.side_effect = ValueError("group is not configured")

    with pytest.raises(c.ServiceValidationError, match="not configured"):
        await call(c, DATA)


@pytest.mark.asyncio
async def test_protocol_failure_does_not_expose_connection_details(weekly_service):
    c = weekly_service
    c.client.async_read_weekly_pattern.side_effect = c.services.MelansError("private.invalid")

    with pytest.raises(c.HomeAssistantError, match="weekly pattern read failed") as exc:
        await call(c, DATA)

    assert "private.invalid" not in str(exc.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [None, {}, [], {"read_status": "partial"}])
async def test_missing_or_invalid_snapshot_is_an_error(weekly_service, invalid):
    c = weekly_service
    c.client.async_read_weekly_pattern.return_value = invalid

    with pytest.raises(c.HomeAssistantError, match="did not produce a weekly pattern"):
        await call(c, DATA)


def test_weekly_action_metadata_matches_schema_and_response_contract(weekly_service):
    c = weekly_service
    assert c.hass.services.registered["ae200", "read_weekly_pattern"]["supports_response"] == c.SupportsResponse.ONLY
    component = ROOT / "custom_components/ae200"
    strings = json.loads((component / "strings.json").read_text())
    english = json.loads((component / "translations/en.json").read_text())
    action = strings["services"]["read_weekly_pattern"]
    assert action == english["services"]["read_weekly_pattern"]
    assert set(action["fields"]) == set(DATA)
    assert all(field["name"] and field["description"] for field in action["fields"].values())
    yaml = (component / "services.yaml").read_text().split("read_weekly_pattern:\n", 1)[1]
    yaml = yaml.split("set_local_restrictions:\n", 1)[0]
    assert "config_entry:" in yaml and "integration: ae200" in yaml
    assert yaml.count("required: true") == 4
    assert "max: 50" in yaml and "max: 5\n" in yaml and "max: 7" in yaml
