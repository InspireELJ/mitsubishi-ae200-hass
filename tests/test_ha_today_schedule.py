"""Today's schedule is an explicit response-only Home Assistant action."""

from __future__ import annotations

import importlib
import json
import sys
import types
from enum import StrEnum
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import voluptuous as vol

ROOT = Path(__file__).resolve().parents[1]
ACTION_NAMES = {
    "read_member", "read_control_context", "read_today_schedule", "read_controller_clock",
    "read_weekly_pattern", "set_local_restrictions",
}


@pytest.fixture
def schedule_service(monkeypatch):
    def module(name, **members):
        result = types.ModuleType(name)
        result.__dict__.update(members)
        monkeypatch.setitem(sys.modules, name, result)
        return result

    class HomeAssistantError(Exception):
        pass

    class ServiceValidationError(HomeAssistantError):
        pass

    class SupportsResponse(StrEnum):
        ONLY = "only"

    class Registry:
        def __init__(self):
            self.registered = {}
            self.registrations = 0

        def has_service(self, domain, service):
            return (domain, service) in self.registered

        def async_register(self, domain, service, handler, **options):
            self.registrations += 1
            self.registered[domain, service] = {"handler": handler, **options}

        async def call(self, data):
            registration = self.registered["ae200", "read_today_schedule"]
            data = registration["schema"](data)
            return await registration["handler"](types.SimpleNamespace(data=data))

    module("homeassistant").__path__ = []
    module("homeassistant.core", HomeAssistant=object, ServiceCall=object,
           ServiceResponse=dict, SupportsResponse=SupportsResponse)
    module("homeassistant.exceptions", HomeAssistantError=HomeAssistantError,
           ServiceValidationError=ServiceValidationError)
    name = "ae200_today_schedule_test"
    module(name).__path__ = [str(ROOT / "custom_components/ae200")]
    services = importlib.import_module(name + ".services")
    client = types.SimpleNamespace(
        async_read_today_schedule=AsyncMock(), async_read_control_context=AsyncMock(),
        async_read_member=AsyncMock(),
        _write=AsyncMock(side_effect=AssertionError("Schedule reads must not write")),
        _request=AsyncMock(side_effect=AssertionError("The action must use the client method")),
    )
    coordinator = types.SimpleNamespace(client=client, async_request_refresh=AsyncMock())
    entries = {"selected": types.SimpleNamespace(domain="ae200")}
    hass = types.SimpleNamespace(
        services=Registry(), data={"ae200": {"selected": coordinator}},
        config_entries=types.SimpleNamespace(async_get_entry=entries.get),
    )
    yield types.SimpleNamespace(
        services=services, hass=hass, client=client, coordinator=coordinator, entries=entries,
        HomeAssistantError=HomeAssistantError, ServiceValidationError=ServiceValidationError,
        SupportsResponse=SupportsResponse,
    )
    for key in list(sys.modules):
        if key.startswith(name + "."):
            sys.modules.pop(key, None)


def sample(*, status="updated", quality="fresh", records=None, group="4"):
    return {
        "group_id": group, "read_status": status, "quality": quality,
        "last_attempt": "2026-10-16T00:00:00+00:00",
        "last_successful_read": "2026-10-16T00:00:00+00:00" if quality != "unknown" else None,
        "error": {"type": "MelansConnectionError", "code": None} if status == "error" else None,
        "records": records if records is not None else [], "issues": [],
    }


@pytest.mark.parametrize(
    "preexisting",
    [(), ("read_member",), ("read_control_context",),
     ("read_member", "read_control_context"), ("read_today_schedule",)],
)
def test_all_actions_register_independently_and_idempotently(schedule_service, preexisting):
    c = schedule_service
    sentinels = {name: {"handler": object()} for name in preexisting}
    for name, sentinel in sentinels.items():
        c.hass.services.registered["ae200", name] = sentinel

    c.services.async_setup_services(c.hass)
    c.services.async_setup_services(c.hass)

    assert set(c.hass.services.registered) == {("ae200", name) for name in ACTION_NAMES}
    assert c.hass.services.registrations == 6 - len(preexisting)
    for name in ACTION_NAMES:
        registered = c.hass.services.registered["ae200", name]
        if name in sentinels:
            assert registered is sentinels[name]
        else:
            assert registered["supports_response"] == c.SupportsResponse.ONLY
    c.client.async_read_today_schedule.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data",
    [
        {"group_id": 4}, {"config_entry_id": "selected"},
        {"config_entry_id": "", "group_id": 4},
        *({"config_entry_id": "selected", "group_id": value}
          for value in (0, 51, -1, True, False, 4.0, 4.5, "4.0", "*", "1,2", [4], None)),
        {"config_entry_id": "selected", "group_id": 4, "all": True},
    ],
)
async def test_action_requires_one_explicit_entry_and_one_integer_group(schedule_service, data):
    c = schedule_service
    c.services.async_setup_services(c.hass)

    with pytest.raises(vol.Invalid):
        await c.hass.services.call(data)

    c.client.async_read_today_schedule.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("group,canonical", [(1, 1), (50, 50), ("004", 4)])
async def test_group_boundaries_and_decimal_input_use_the_selected_client(
    schedule_service, group, canonical
):
    c = schedule_service
    c.services.async_setup_services(c.hass)
    c.client.async_read_today_schedule.return_value = sample(group=str(canonical))

    response = await c.hass.services.call({"config_entry_id": "selected", "group_id": group})

    assert response["group_id"] == str(canonical)
    c.client.async_read_today_schedule.assert_awaited_once_with(canonical)


@pytest.mark.asyncio
@pytest.mark.parametrize("entry_kind", ["missing", "wrong_domain", "unloaded"])
async def test_invalid_entry_does_not_fall_back_to_the_loaded_controller(schedule_service, entry_kind):
    c = schedule_service
    c.services.async_setup_services(c.hass)
    if entry_kind == "wrong_domain":
        c.entries["invalid"] = types.SimpleNamespace(domain="another_integration")
        c.hass.data["ae200"]["invalid"] = c.coordinator
    elif entry_kind == "unloaded":
        c.entries["invalid"] = types.SimpleNamespace(domain="ae200")

    exception = c.HomeAssistantError if entry_kind == "unloaded" else c.ServiceValidationError
    with pytest.raises(exception):
        await c.hass.services.call({"config_entry_id": "invalid", "group_id": 4})

    c.client.async_read_today_schedule.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "snapshot",
    [
        sample(),  # A successful empty list remains a successful response.
        sample(records=[{
            "index": 0, "hour": 8, "minute": 15,
            "raw_attributes": {"Hour": "08", "Minute": "15", "Drive": "OFF"},
        }]),
        sample(status="error", quality="unknown"),
        sample(status="error", quality="stale", records=[{
            "index": 0, "hour": None, "minute": None, "raw_attributes": {"Hour": "FUTURE"},
        }]),
    ],
)
async def test_safe_schedule_snapshots_are_returned_without_refresh_or_control(schedule_service, snapshot):
    c = schedule_service
    c.services.async_setup_services(c.hass)
    c.client.async_read_today_schedule.return_value = snapshot

    response = await c.hass.services.call({"config_entry_id": "selected", "group_id": 4})

    assert response == snapshot
    assert json.loads(json.dumps(response)) == snapshot
    assert "next_execution" not in response
    c.coordinator.async_request_refresh.assert_not_awaited()
    c.client.async_read_control_context.assert_not_awaited()
    c.client.async_read_member.assert_not_awaited()
    c.client._write.assert_not_awaited()
    c.client._request.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_group_is_a_validation_error(schedule_service):
    c = schedule_service
    c.services.async_setup_services(c.hass)
    c.client.async_read_today_schedule.side_effect = ValueError("group is not configured")

    with pytest.raises(c.ServiceValidationError, match="not configured"):
        await c.hass.services.call({"config_entry_id": "selected", "group_id": 4})

    c.coordinator.async_request_refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_cooldown_raises_even_when_cached_records_are_present(schedule_service):
    c = schedule_service
    c.services.async_setup_services(c.hass)
    c.client.async_read_today_schedule.return_value = sample(
        status="cooldown", records=[{"index": 0, "hour": 8, "minute": 0, "raw_attributes": {}}]
    )

    with pytest.raises(c.HomeAssistantError, match="Wait at least 15 seconds"):
        await c.hass.services.call({"config_entry_id": "selected", "group_id": 4})

    c.coordinator.async_request_refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_unexpected_client_failure_does_not_expose_connection_details(schedule_service):
    c = schedule_service
    c.services.async_setup_services(c.hass)
    c.client.async_read_today_schedule.side_effect = c.services.MelansError("private.invalid")

    with pytest.raises(c.HomeAssistantError, match="schedule read failed") as exc:
        await c.hass.services.call({"config_entry_id": "selected", "group_id": 4})

    assert "private.invalid" not in str(exc.value)


def test_today_schedule_action_fields_have_matching_translations():
    component = ROOT / "custom_components/ae200"
    strings = json.loads((component / "strings.json").read_text())
    english = json.loads((component / "translations/en.json").read_text())
    action = strings["services"]["read_today_schedule"]
    assert action == english["services"]["read_today_schedule"]
    assert set(action["fields"]) == {"config_entry_id", "group_id"}
    assert all(field["name"] and field["description"] for field in action["fields"].values())
    yaml = (component / "services.yaml").read_text().split("read_today_schedule:\n", 1)[1]
    yaml = yaml.split("read_controller_clock:\n", 1)[0]
    assert "config_entry:" in yaml and "integration: ae200" in yaml
    assert "min: 1" in yaml and "max: 50" in yaml
    assert yaml.count("required: true") == 2
