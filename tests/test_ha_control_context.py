"""Explicit control-context actions at the Home Assistant API boundary."""

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


@pytest.fixture
def context_service(monkeypatch):
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
            registration = self.registered["ae200", "read_control_context"]
            data = registration["schema"](data)
            return await registration["handler"](types.SimpleNamespace(data=data))

    module("homeassistant").__path__ = []
    module("homeassistant.core", HomeAssistant=object, ServiceCall=object,
           ServiceResponse=dict, SupportsResponse=SupportsResponse)
    module("homeassistant.exceptions", HomeAssistantError=HomeAssistantError,
           ServiceValidationError=ServiceValidationError)
    name = "ae200_control_context_test"
    module(name).__path__ = [str(ROOT / "custom_components/ae200")]
    services = importlib.import_module(name + ".services")
    client = types.SimpleNamespace(
        async_read_control_context=AsyncMock(), async_read_member=AsyncMock(),
        _write=AsyncMock(side_effect=AssertionError("Diagnostic actions must not write")),
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


def sample(status):
    return {
        "group_id": "4", "read_status": status,
        "global": {
            "quality": "fresh" if status != "error" else "unknown",
            "last_attempt": "2026-10-16T00:00:00+00:00",
            "last_successful_read": "2026-10-16T00:00:00+00:00" if status != "error" else None,
            "error": None if status != "error" else {"type": "MelansConnectionError"},
            "raw_attributes": {"AllTurnOff": "OFF"} if status != "error" else {},
            "global_emergency_stop": False if status != "error" else None,
        },
        "group": {
            "quality": "fresh" if status == "updated" else "unknown",
            "last_attempt": "2026-10-16T00:00:01+00:00",
            "last_successful_read": "2026-10-16T00:00:01+00:00" if status == "updated" else None,
            "error": None if status == "updated" else {"type": "MelansProtocolError"},
            "raw_attributes": {"BACnetTurnOff": "OFF", "TurnOff": "OFF"}
            if status == "updated" else {},
            "bacnet_emergency_stop": False if status == "updated" else None,
            "energy_saving_stop": False if status == "updated" else None,
        },
        "control_inhibited": False if status == "updated" else None,
    }


@pytest.mark.parametrize("preexisting", [None, "read_member", "read_control_context"])
def test_actions_register_independently_and_setup_is_idempotent(context_service, preexisting):
    c = context_service
    sentinel = {"handler": object()}
    if preexisting is not None:
        c.hass.services.registered["ae200", preexisting] = sentinel

    c.services.async_setup_services(c.hass)
    c.services.async_setup_services(c.hass)

    assert set(c.hass.services.registered) == {
        ("ae200", "read_member"), ("ae200", "read_control_context"),
        ("ae200", "read_today_schedule"),
        ("ae200", "read_controller_clock"),
        ("ae200", "read_weekly_pattern"),
        ("ae200", "set_local_restrictions"),
    }
    assert c.hass.services.registrations == (6 if preexisting is None else 5)
    if preexisting is not None:
        assert c.hass.services.registered["ae200", preexisting] is sentinel
    for key, registered in c.hass.services.registered.items():
        if key[1] != preexisting:
            assert registered["supports_response"] == c.SupportsResponse.ONLY
    c.client.async_read_control_context.assert_not_awaited()
    c.client.async_read_member.assert_not_awaited()


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
async def test_schema_requires_one_explicit_controller_and_integer_group(context_service, data):
    c = context_service
    c.services.async_setup_services(c.hass)

    with pytest.raises(vol.Invalid):
        await c.hass.services.call(data)

    c.client.async_read_control_context.assert_not_awaited()
    c.client._write.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("entry_kind", ["missing", "wrong_domain", "unloaded"])
async def test_invalid_entry_never_falls_back_to_another_controller(context_service, entry_kind):
    c = context_service
    c.services.async_setup_services(c.hass)
    if entry_kind == "wrong_domain":
        c.entries["invalid"] = types.SimpleNamespace(domain="another_integration")
        c.hass.data["ae200"]["invalid"] = c.coordinator
    elif entry_kind == "unloaded":
        c.entries["invalid"] = types.SimpleNamespace(domain="ae200")

    exception = c.HomeAssistantError if entry_kind == "unloaded" else c.ServiceValidationError
    with pytest.raises(exception):
        await c.hass.services.call({"config_entry_id": "invalid", "group_id": 4})

    c.client.async_read_control_context.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["updated", "partial", "error"])
async def test_returns_independent_safe_context_without_refresh_or_control(context_service, status):
    c = context_service
    c.services.async_setup_services(c.hass)
    context = sample(status)
    c.client.async_read_control_context.return_value = context

    response = await c.hass.services.call({"config_entry_id": "selected", "group_id": "004"})

    c.client.async_read_control_context.assert_awaited_once_with(4)
    assert response == context
    assert json.loads(json.dumps(response)) == context
    c.coordinator.async_request_refresh.assert_not_awaited()
    c.client.async_read_member.assert_not_awaited()
    c.client._write.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_group_is_a_validation_error(context_service):
    c = context_service
    c.services.async_setup_services(c.hass)
    c.client.async_read_control_context.side_effect = ValueError("group is not configured")

    with pytest.raises(c.ServiceValidationError, match="not configured"):
        await c.hass.services.call({"config_entry_id": "selected", "group_id": 4})

    c.coordinator.async_request_refresh.assert_not_awaited()
    c.client._write.assert_not_awaited()


@pytest.mark.asyncio
async def test_cooldown_is_an_error_even_when_an_older_sample_exists(context_service):
    c = context_service
    c.services.async_setup_services(c.hass)
    c.client.async_read_control_context.return_value = {**sample("updated"), "read_status": "cooldown"}

    with pytest.raises(c.HomeAssistantError, match="Wait at least 15 seconds"):
        await c.hass.services.call({"config_entry_id": "selected", "group_id": 4})

    c.coordinator.async_request_refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_unexpected_client_failure_does_not_expose_connection_details(context_service):
    c = context_service
    c.services.async_setup_services(c.hass)
    c.client.async_read_control_context.side_effect = c.services.MelansError("private.invalid")

    with pytest.raises(c.HomeAssistantError, match="read failed") as exc:
        await c.hass.services.call({"config_entry_id": "selected", "group_id": 4})

    assert "private.invalid" not in str(exc.value)


def test_context_action_fields_have_matching_translations():
    component = ROOT / "custom_components/ae200"
    strings = json.loads((component / "strings.json").read_text())
    english = json.loads((component / "translations/en.json").read_text())
    action = strings["services"]["read_control_context"]
    assert action == english["services"]["read_control_context"]
    assert set(action["fields"]) == {"config_entry_id", "group_id"}
    assert all(field["name"] and field["description"] for field in action["fields"].values())
    yaml = (component / "services.yaml").read_text().split("read_control_context:\n", 1)[1]
    yaml = yaml.split("read_today_schedule:\n", 1)[0]
    assert "config_entry:" in yaml and "integration: ae200" in yaml
    assert "min: 1" in yaml and "max: 50" in yaml
    assert yaml.count("required: true") == 2
