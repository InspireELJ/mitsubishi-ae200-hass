"""Controller clock reads are explicit, bounded and independent of entity state."""

from __future__ import annotations

import importlib
import itertools
import json
import sys
import types
from enum import StrEnum
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import voluptuous as vol

ROOT = Path(__file__).resolve().parents[1]
ACTION_NAMES = (
    "read_member", "read_control_context", "read_today_schedule", "read_controller_clock",
    "read_weekly_pattern", "set_local_restrictions",
)


@pytest.fixture
def clock_service(monkeypatch):
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
            registration = self.registered["ae200", "read_controller_clock"]
            validated = registration["schema"](data)
            return await registration["handler"](types.SimpleNamespace(data=validated))

    module("homeassistant").__path__ = []
    module("homeassistant.core", HomeAssistant=object, ServiceCall=object,
           ServiceResponse=dict, SupportsResponse=SupportsResponse)
    module("homeassistant.exceptions", HomeAssistantError=HomeAssistantError,
           ServiceValidationError=ServiceValidationError)
    name = "ae200_controller_clock_test"
    module(name).__path__ = [str(ROOT / "custom_components/ae200")]
    services = importlib.import_module(name + ".services")
    client = types.SimpleNamespace(
        async_read_controller_clock=AsyncMock(), async_read_today_schedule=AsyncMock(),
        async_read_control_context=AsyncMock(), async_read_member=AsyncMock(),
        _write=AsyncMock(side_effect=AssertionError("Clock reads must not write")),
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


def sample(*, status="updated", quality="fresh"):
    fresh = quality == "fresh"
    return {
        "read_status": status, "quality": quality,
        "last_attempt": "2026-10-16T00:00:00+00:00",
        "last_successful_read": "2026-10-16T00:00:00+00:00" if quality != "unknown" else None,
        "error": {"type": "MelansConnectionError", "code": None} if status == "error" else None,
        "raw_attributes": {"Year": "2026", "Month": "10"} if quality != "unknown" else {},
        "components": {
            "year": 2026 if fresh else None, "month": 10 if fresh else None,
            "day": 16 if fresh else None, "hour": 11 if fresh else None,
            "minute": 0 if fresh else None, "second": 0 if fresh else None,
        },
        "local_datetime": "2026-10-16T11:00:00" if fresh else None,
        "timezone": None, "issues": [],
    }


@pytest.mark.parametrize(
    "preexisting",
    [tuple(name for name, exists in zip(ACTION_NAMES, flags, strict=True) if exists)
     for flags in itertools.product((False, True), repeat=len(ACTION_NAMES))],
)
def test_each_action_registers_independently_and_idempotently(clock_service, preexisting):
    c = clock_service
    sentinels = {name: {"handler": object()} for name in preexisting}
    for name, sentinel in sentinels.items():
        c.hass.services.registered["ae200", name] = sentinel

    c.services.async_setup_services(c.hass)
    c.services.async_setup_services(c.hass)

    assert set(c.hass.services.registered) == {("ae200", name) for name in ACTION_NAMES}
    assert c.hass.services.registrations == len(ACTION_NAMES) - len(preexisting)
    for name in ACTION_NAMES:
        registered = c.hass.services.registered["ae200", name]
        if name in sentinels:
            assert registered is sentinels[name]
        else:
            assert registered["supports_response"] == c.SupportsResponse.ONLY
    c.client.async_read_controller_clock.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data",
    [
        {}, {"group_id": 4},
        *({"config_entry_id": value} for value in ("", None, True, 1, 1.0, ["selected"])),
        *({"config_entry_id": "selected", key: value}
          for key, value in (("group_id", 4), ("address", 1), ("all", True))),
    ],
)
async def test_only_an_explicit_nonempty_entry_is_accepted(clock_service, data):
    c = clock_service
    c.services.async_setup_services(c.hass)

    with pytest.raises(vol.Invalid):
        await c.hass.services.call(data)

    c.client.async_read_controller_clock.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("entry_kind", ["missing", "wrong_domain", "unloaded"])
async def test_invalid_entry_never_uses_another_loaded_controller(clock_service, entry_kind):
    c = clock_service
    c.services.async_setup_services(c.hass)
    if entry_kind == "wrong_domain":
        c.entries["invalid"] = types.SimpleNamespace(domain="another_integration")
        c.hass.data["ae200"]["invalid"] = c.coordinator
    elif entry_kind == "unloaded":
        c.entries["invalid"] = types.SimpleNamespace(domain="ae200")
    exception = c.HomeAssistantError if entry_kind == "unloaded" else c.ServiceValidationError

    with pytest.raises(exception):
        await c.hass.services.call({"config_entry_id": "invalid"})

    c.client.async_read_controller_clock.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "snapshot",
    [sample(), sample(status="error", quality="unknown"), sample(status="error", quality="stale")],
)
async def test_safe_clock_snapshots_preserve_quality_without_refresh_or_control(clock_service, snapshot):
    c = clock_service
    c.services.async_setup_services(c.hass)
    c.client.async_read_controller_clock.return_value = snapshot
    other_client = types.SimpleNamespace(async_read_controller_clock=AsyncMock())
    c.entries["other"] = types.SimpleNamespace(domain="ae200")
    c.hass.data["ae200"]["other"] = types.SimpleNamespace(client=other_client)

    response = await c.hass.services.call({"config_entry_id": "selected"})

    assert response == snapshot
    assert json.loads(json.dumps(response)) == snapshot
    assert response["timezone"] is None
    c.client.async_read_controller_clock.assert_awaited_once_with()
    other_client.async_read_controller_clock.assert_not_awaited()
    c.coordinator.async_request_refresh.assert_not_awaited()
    c.client.async_read_today_schedule.assert_not_awaited()
    c.client.async_read_control_context.assert_not_awaited()
    c.client.async_read_member.assert_not_awaited()
    c.client._write.assert_not_awaited()
    c.client._request.assert_not_awaited()


@pytest.mark.asyncio
async def test_cooldown_raises_even_when_a_cached_clock_is_present(clock_service):
    c = clock_service
    c.services.async_setup_services(c.hass)
    c.client.async_read_controller_clock.return_value = sample(status="cooldown")

    with pytest.raises(c.HomeAssistantError, match="Wait at least 15 seconds"):
        await c.hass.services.call({"config_entry_id": "selected"})

    c.coordinator.async_request_refresh.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [None, {}, [], {"read_status": "partial"}])
async def test_a_missing_or_invalid_client_sample_is_an_error(clock_service, invalid):
    c = clock_service
    c.services.async_setup_services(c.hass)
    c.client.async_read_controller_clock.return_value = invalid

    with pytest.raises(c.HomeAssistantError, match="did not produce a controller clock sample"):
        await c.hass.services.call({"config_entry_id": "selected"})


@pytest.mark.asyncio
async def test_unexpected_protocol_failure_hides_connection_details(clock_service):
    c = clock_service
    c.services.async_setup_services(c.hass)
    c.client.async_read_controller_clock.side_effect = c.services.MelansError("private.invalid")

    with pytest.raises(c.HomeAssistantError, match="controller clock read failed") as exc:
        await c.hass.services.call({"config_entry_id": "selected"})

    assert "private.invalid" not in str(exc.value)


@pytest.mark.asyncio
async def test_client_validation_failure_uses_service_validation_error(clock_service):
    c = clock_service
    c.services.async_setup_services(c.hass)
    c.client.async_read_controller_clock.side_effect = ValueError("clock read is not supported")

    with pytest.raises(c.ServiceValidationError, match="not supported"):
        await c.hass.services.call({"config_entry_id": "selected"})


def test_controller_clock_action_fields_have_matching_translations():
    component = ROOT / "custom_components/ae200"
    strings = json.loads((component / "strings.json").read_text())
    english = json.loads((component / "translations/en.json").read_text())
    action = strings["services"]["read_controller_clock"]
    assert action == english["services"]["read_controller_clock"]
    assert set(action["fields"]) == {"config_entry_id"}
    assert all(field["name"] and field["description"] for field in action["fields"].values())
    yaml = (component / "services.yaml").read_text().split("read_controller_clock:\n", 1)[1]
    yaml = yaml.split("read_weekly_pattern:\n", 1)[0]
    assert "config_entry:" in yaml and "integration: ae200" in yaml
    assert yaml.count("required: true") == 1
    assert "group_id:" not in yaml and "address:" not in yaml
