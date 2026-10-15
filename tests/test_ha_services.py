"""AE-200 diagnostic actions and setup at the Home Assistant API boundary."""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
import types
from enum import StrEnum
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
import voluptuous as vol

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def ha_service_modules(monkeypatch):
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

    class Platform(StrEnum):
        CLIMATE = "climate"
        SENSOR = "sensor"
        BINARY_SENSOR = "binary_sensor"
        SWITCH = "switch"

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
            registration = self.registered["ae200", "read_member"]
            validated = registration["schema"](data)
            return await registration["handler"](types.SimpleNamespace(data=validated))

    events = []
    coordinators = []

    class Coordinator:
        def __init__(self, hass, entry, **options):
            self.host = options["host"]
            self.client = types.SimpleNamespace(
                async_read_member=AsyncMock(),
                async_load_topology=AsyncMock(side_effect=lambda: events.append("topology")),
                async_load_controller_metadata=AsyncMock(
                    side_effect=lambda: events.append("metadata")
                ),
                async_load_function_list=AsyncMock(side_effect=lambda: events.append("functions")),
                controller_metadata=None,
                close=AsyncMock(),
            )
            self.async_config_entry_first_refresh = AsyncMock(
                side_effect=lambda: events.append("refresh")
            )
            self.async_shutdown = AsyncMock()
            coordinators.append(self)

    entries = {}
    device_registry = types.SimpleNamespace(
        async_get_device=Mock(return_value=None), async_get_or_create=Mock()
    )
    hass = types.SimpleNamespace(
        data={},
        services=Registry(),
        config_entries=types.SimpleNamespace(
            async_get_entry=entries.get,
            async_forward_entry_setups=AsyncMock(side_effect=lambda *_: events.append("platforms")),
            async_unload_platforms=AsyncMock(return_value=True),
            async_reload=AsyncMock(),
        ),
    )
    for name in ("homeassistant", "homeassistant.helpers"):
        module(name).__path__ = []
    module("homeassistant.core", HomeAssistant=object, ServiceCall=object,
           ServiceResponse=dict, SupportsResponse=SupportsResponse)
    module("homeassistant.config_entries", ConfigEntry=object)
    module("homeassistant.const", CONF_HOST="host", CONF_PORT="port", Platform=Platform)
    module("homeassistant.exceptions", HomeAssistantError=HomeAssistantError,
           ServiceValidationError=ServiceValidationError)
    module("homeassistant.helpers.device_registry", async_get=lambda _: device_registry)
    module("homeassistant.helpers.typing", ConfigType=dict)
    name = "ae200_services_test"
    module(name + ".coordinator", AE200Coordinator=Coordinator)
    source = ROOT / "custom_components/ae200/__init__.py"
    spec = importlib.util.spec_from_file_location(
        name, source, submodule_search_locations=[str(source.parent)]
    )
    integration = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, integration)
    spec.loader.exec_module(integration)
    services = importlib.import_module(name + ".services")
    yield types.SimpleNamespace(
        hass=hass, entries=entries, integration=integration, services=services,
        coordinators=coordinators, Coordinator=Coordinator, events=events,
        device_registry=device_registry,
        HomeAssistantError=HomeAssistantError, ServiceValidationError=ServiceValidationError,
        SupportsResponse=SupportsResponse,
    )
    for key in list(sys.modules):
        if key.startswith(name + "."):
            sys.modules.pop(key, None)


def entry(entry_id="selected", domain="ae200"):
    return types.SimpleNamespace(
        entry_id=entry_id, domain=domain, data={"host": "fixture.invalid"}, options={},
        async_on_unload=Mock(), add_update_listener=Mock(return_value=lambda: None),
    )


async def loaded_service(context):
    selected = entry()
    context.entries[selected.entry_id] = selected
    coordinator = context.Coordinator(context.hass, selected, host="fixture.invalid")
    context.hass.data["ae200"] = {selected.entry_id: coordinator}
    await context.integration.async_setup(context.hass, {})
    return coordinator


@pytest.mark.asyncio
async def test_action_is_registered_without_entries_and_requires_a_response(ha_service_modules):
    c = ha_service_modules
    assert await c.integration.async_setup(c.hass, {})
    assert await c.integration.async_setup(c.hass, {})
    assert c.hass.services.registrations == 6
    registered = c.hass.services.registered["ae200", "read_member"]
    assert registered["supports_response"] == c.SupportsResponse.ONLY

    with pytest.raises(c.ServiceValidationError, match="existing AE-200"):
        await c.hass.services.call({"config_entry_id": "missing", "address": 1})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data",
    [
        {"address": 1}, {"config_entry_id": "selected"},
        {"config_entry_id": "", "address": 1},
        *({"config_entry_id": "selected", "address": value}
          for value in (0, 251, -1, True, 1.5, "1.5", "1,2", "*", [1, 2])),
        {"config_entry_id": "selected", "address": 1, "all": True},
    ],
)
async def test_action_requires_one_explicit_entry_and_one_valid_address(ha_service_modules, data):
    c = ha_service_modules
    coordinator = await loaded_service(c)

    with pytest.raises(vol.Invalid):
        await c.hass.services.call(data)

    coordinator.client.async_read_member.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", [None, "other_integration"])
async def test_unknown_or_wrong_entry_fails_before_controller_io(ha_service_modules, domain):
    c = ha_service_modules
    coordinator = await loaded_service(c)
    if domain is not None:
        c.entries["wrong"] = entry("wrong", domain)
        c.hass.data["ae200"]["wrong"] = coordinator

    with pytest.raises(c.ServiceValidationError, match="existing AE-200"):
        await c.hass.services.call({"config_entry_id": "wrong", "address": 1})

    coordinator.client.async_read_member.assert_not_awaited()


@pytest.mark.asyncio
async def test_unloaded_entry_has_a_clear_error_and_does_not_select_another(ha_service_modules):
    c = ha_service_modules
    coordinator = await loaded_service(c)
    c.entries["unloaded"] = entry("unloaded")

    with pytest.raises(c.HomeAssistantError, match="not loaded"):
        await c.hass.services.call({"config_entry_id": "unloaded", "address": 1})

    coordinator.client.async_read_member.assert_not_awaited()


@pytest.mark.asyncio
async def test_success_returns_only_the_selected_safe_member_sample(ha_service_modules, monkeypatch):
    c = ha_service_modules
    coordinator = await loaded_service(c)
    coordinator.client.async_read_member.return_value = {"Password": "raw-private-secret"}
    safe_member = {
        "quality": "fresh", "read_status": "updated", "raw_attributes": {"Thermo": "OFF"}
    }
    snapshot = Mock(return_value={
        "members": {"7": safe_member, "8": {"raw_attributes": {"PrivateField": "other"}}},
        "topology": {"records": ["not-the-selected-member"]},
    })
    monkeypatch.setattr(c.services, "cached_snapshot", snapshot)

    response = await c.hass.services.call({"config_entry_id": "selected", "address": "007"})

    coordinator.client.async_read_member.assert_awaited_once_with(7)
    snapshot.assert_called_once_with(coordinator.client)
    assert response == {"address": 7, **safe_member}
    serialised = json.dumps(response)
    assert "raw-private-secret" not in serialised
    assert "other" not in serialised and "topology" not in response


@pytest.mark.asyncio
async def test_unknown_member_is_a_validation_error(ha_service_modules):
    c = ha_service_modules
    coordinator = await loaded_service(c)
    coordinator.client.async_read_member.side_effect = ValueError("address is not a verified IC")

    with pytest.raises(c.ServiceValidationError, match="not a verified IC"):
        await c.hass.services.call({"config_entry_id": "selected", "address": 7})

    coordinator.client.async_read_member.assert_awaited_once_with(7)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["none", "network"])
async def test_failed_or_cooling_read_raises_instead_of_returning_old_cache(
    ha_service_modules, monkeypatch, failure
):
    c = ha_service_modules
    coordinator = await loaded_service(c)
    coordinator.client.async_read_member.return_value = None
    if failure == "network":
        coordinator.client.async_read_member.side_effect = c.services.MelansError(
            "private connection address must not appear in the service error"
        )
    snapshot = Mock()
    monkeypatch.setattr(c.services, "cached_snapshot", snapshot)

    with pytest.raises(c.HomeAssistantError, match="read failed") as exc:
        await c.hass.services.call({"config_entry_id": "selected", "address": 7})

    snapshot.assert_not_called()
    assert "private connection address" not in str(exc.value)


@pytest.mark.asyncio
async def test_missing_diagnostic_sample_is_an_error(ha_service_modules, monkeypatch):
    c = ha_service_modules
    coordinator = await loaded_service(c)
    coordinator.client.async_read_member.return_value = {"Thermo": "OFF"}
    monkeypatch.setattr(c.services, "cached_snapshot", lambda _: {"members": {}})

    with pytest.raises(c.HomeAssistantError, match="did not produce"):
        await c.hass.services.call({"config_entry_id": "selected", "address": 7})


@pytest.mark.asyncio
async def test_setup_loads_topology_after_first_refresh_without_reading_members(ha_service_modules):
    c = ha_service_modules
    selected = entry()
    assert await c.integration.async_setup_entry(c.hass, selected)
    coordinator = c.coordinators[0]

    assert c.events == ["refresh", "topology", "metadata", "functions", "platforms"]
    coordinator.client.async_load_topology.assert_awaited_once()
    coordinator.client.async_load_controller_metadata.assert_awaited_once()
    coordinator.client.async_load_function_list.assert_awaited_once()
    coordinator.client.async_read_member.assert_not_awaited()
    assert c.hass.data["ae200"][selected.entry_id] is coordinator
    assert selected.data == {"host": "fixture.invalid"}


@pytest.mark.asyncio
async def test_failed_first_refresh_never_loads_topology_or_platforms(
    ha_service_modules, monkeypatch
):
    c = ha_service_modules
    selected = entry()
    coordinator = c.Coordinator(c.hass, selected, host="fixture.invalid")
    coordinator.async_config_entry_first_refresh.side_effect = RuntimeError("first read failed")
    monkeypatch.setattr(c.integration, "AE200Coordinator", lambda *_args, **_kw: coordinator)

    with pytest.raises(RuntimeError, match="first read failed"):
        await c.integration.async_setup_entry(c.hass, selected)

    coordinator.client.async_load_topology.assert_not_awaited()
    coordinator.client.async_load_controller_metadata.assert_not_awaited()
    coordinator.client.async_load_function_list.assert_not_awaited()
    coordinator.client.async_read_member.assert_not_awaited()
    coordinator.client.close.assert_awaited_once()
    c.hass.config_entries.async_forward_entry_setups.assert_not_awaited()
    assert not c.hass.data


@pytest.mark.asyncio
async def test_first_setup_uses_generic_model_without_inventing_versions(ha_service_modules):
    c = ha_service_modules
    selected = entry()

    assert await c.integration.async_setup_entry(c.hass, selected)

    c.device_registry.async_get_device.assert_called_once_with(
        identifiers={("ae200", "fixture.invalid")}
    )
    registered = c.device_registry.async_get_or_create.call_args.kwargs
    assert registered["identifiers"] == {("ae200", "fixture.invalid")}
    assert registered["model"] == "AE-200 / AE-50 / EW-50"
    assert "sw_version" not in registered and "hw_version" not in registered


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", [None, types.SimpleNamespace(model="Generic model")])
async def test_cached_controller_metadata_updates_only_existing_registry_fields(
    ha_service_modules, monkeypatch, existing
):
    c = ha_service_modules
    selected = entry()
    coordinator = c.Coordinator(c.hass, selected, host="fixture.invalid")
    coordinator.client.controller_metadata = types.SimpleNamespace(
        model="AE-200E", version="8.01", version_if="1.07"
    )
    c.device_registry.async_get_device.return_value = existing
    monkeypatch.setattr(c.integration, "AE200Coordinator", lambda *_args, **_kw: coordinator)

    assert await c.integration.async_setup_entry(c.hass, selected)

    registered = c.device_registry.async_get_or_create.call_args.kwargs
    assert registered == {
        "config_entry_id": selected.entry_id,
        "identifiers": {("ae200", "fixture.invalid")},
        "name": "Mitsubishi centralised controller",
        "manufacturer": "Mitsubishi Electric",
        "model": "AE-200E", "sw_version": "8.01",
    }
    assert "hw_version" not in registered
    c.hass.config_entries.async_forward_entry_setups.assert_awaited_once_with(
        selected, c.integration.PLATFORMS
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "metadata",
    [None, types.SimpleNamespace(model=None, version=None, version_if="1.07")],
)
async def test_optional_metadata_failure_preserves_existing_model_and_version(
    ha_service_modules, monkeypatch, metadata
):
    c = ha_service_modules
    selected = entry()
    coordinator = c.Coordinator(c.hass, selected, host="fixture.invalid")
    coordinator.client.controller_metadata = metadata
    existing = types.SimpleNamespace(model="AE-200E", sw_version="8.01")
    c.device_registry.async_get_device.return_value = existing
    monkeypatch.setattr(c.integration, "AE200Coordinator", lambda *_args, **_kw: coordinator)

    assert await c.integration.async_setup_entry(c.hass, selected)

    registered = c.device_registry.async_get_or_create.call_args.kwargs
    assert "model" not in registered
    assert "sw_version" not in registered and "hw_version" not in registered
    assert (existing.model, existing.sw_version) == ("AE-200E", "8.01")
    assert c.events == ["refresh", "topology", "metadata", "functions", "platforms"]


@pytest.mark.asyncio
async def test_partial_metadata_preserves_known_model_and_keeps_version_as_text(
    ha_service_modules, monkeypatch
):
    c = ha_service_modules
    selected = entry()
    coordinator = c.Coordinator(c.hass, selected, host="fixture.invalid")
    coordinator.client.controller_metadata = types.SimpleNamespace(
        model=None, version="8.00", version_if="1.07"
    )
    c.device_registry.async_get_device.return_value = types.SimpleNamespace(model="AE-200E")
    monkeypatch.setattr(c.integration, "AE200Coordinator", lambda *_args, **_kw: coordinator)

    assert await c.integration.async_setup_entry(c.hass, selected)

    registered = c.device_registry.async_get_or_create.call_args.kwargs
    assert "model" not in registered
    assert registered["sw_version"] == "8.00"
    assert "hw_version" not in registered


@pytest.mark.asyncio
async def test_unload_keeps_global_action_registered(ha_service_modules):
    c = ha_service_modules
    coordinator = await loaded_service(c)
    selected = c.entries["selected"]

    assert await c.integration.async_unload_entry(c.hass, selected)

    coordinator.async_shutdown.assert_awaited_once()
    assert c.hass.services.has_service("ae200", "read_member")
    with pytest.raises(c.HomeAssistantError, match="not loaded"):
        await c.hass.services.call({"config_entry_id": "selected", "address": 7})


def test_action_fields_have_matching_translations():
    strings = json.loads((ROOT / "custom_components/ae200/strings.json").read_text())
    english = json.loads((ROOT / "custom_components/ae200/translations/en.json").read_text())
    action = strings["services"]["read_member"]
    assert action == english["services"]["read_member"]
    assert set(action["fields"]) == {"config_entry_id", "address"}
    assert all(field["name"] and field["description"] for field in action["fields"].values())
    yaml = (ROOT / "custom_components/ae200/services.yaml").read_text()
    assert "config_entry:" in yaml and "integration: ae200" in yaml
    assert yaml.count("required: true") == 13
