"""Legacy and current device contracts preserve existing groups and entities.

Boundary stubs model the relevant official 2025.12.0 and 2026.10.1 signatures.
They do not execute Home Assistant, contact a controller or mutate a real registry.
"""

from __future__ import annotations

import copy
import importlib
import sys
import types
from enum import IntFlag, StrEnum
from typing import TypedDict
from unittest.mock import AsyncMock

import pytest
from test_ha_services import entry
from test_ha_services import ha_service_modules as ha_service_modules

from pymelans import MelansDevice

UNSET = object()
HOST = "fixture.invalid"
PARENT = ("ae200", HOST)
GROUP = ("ae200", HOST + ":1")
UID = HOST + "_1_climate"


class LegacyDeviceInfo(TypedDict, total=False):
    identifiers: set[tuple[str, str]]
    name: str | None
    manufacturer: str | None
    model: str | None
    via_device: tuple[str, str]


class ModernDeviceInfo(TypedDict, total=False):
    identifiers: set[tuple[str, str]]
    name: str | None
    manufacturer: str | None
    model: str | None
    via_device_id: str


class Registry:
    def __init__(self):
        self.devices = {}
        self.registrations = []
        self.lookups = []

    def seed(self, entry_id, identifier, device_id, *, model=None, via_device_id=None):
        result = types.SimpleNamespace(
            id=device_id, identifiers={identifier}, config_entry_id=entry_id,
            model=model, sw_version="8.01" if model else None, name="Reported name",
            manufacturer="Mitsubishi Electric", via_device_id=via_device_id,
            name_by_user="User device name", area_id="user-area", labels={"user-label"},
            disabled_by="user",
        )
        self.devices[entry_id, identifier] = result
        return result

    def register(self, config_entry_id, identifiers, name, manufacturer, model, sw_version, parent_id):
        identifier, = identifiers
        device = self.devices.get((config_entry_id, identifier))
        if device is None:
            device = self.seed(config_entry_id, identifier, f"registry-id-{len(self.devices) + 1}")
        for field, value in (
            ("name", name), ("manufacturer", manufacturer), ("model", model),
            ("sw_version", sw_version), ("via_device_id", parent_id),
        ):
            if value is not UNSET:
                setattr(device, field, value)
        self.registrations.append((config_entry_id, identifier, device.id, parent_id))
        return device


class LegacyRegistry(Registry):
    def async_get_device(self, identifiers=None, connections=None):
        self.lookups.append((identifiers, connections))
        return next((device for (_, identifier), device in self.devices.items()
                     if identifiers and identifier in identifiers), None)

    def async_get_or_create(
        self, *, config_entry_id, identifiers, name=UNSET, manufacturer=UNSET,
        model=UNSET, sw_version=UNSET, via_device=UNSET,
    ):
        parent_id = UNSET
        if via_device is not UNSET:
            assert isinstance(via_device, tuple) and len(via_device) == 2
            parent_id = self.devices[config_entry_id, via_device].id
        return self.register(config_entry_id, identifiers, name, manufacturer, model, sw_version, parent_id)


class ModernRegistry(Registry):
    def async_get_device(self, *args, **kwargs):
        raise AssertionError("Current HA must not use the deprecated global lookup")

    def async_get_device_by_identifier(self, identifier, config_entry_id):
        self.lookups.append((identifier, config_entry_id))
        return self.devices.get((config_entry_id, identifier))

    def async_get_or_create(
        self, *, config_entry_id, identifiers, name=UNSET, manufacturer=UNSET,
        model=UNSET, sw_version=UNSET, via_device_id=UNSET,
    ):
        if via_device_id is not UNSET:
            assert isinstance(via_device_id, str)
            assert any(device.id == via_device_id and owner == config_entry_id
                       for (owner, _), device in self.devices.items())
        return self.register(config_entry_id, identifiers, name, manufacturer, model, sw_version, via_device_id)


@pytest.fixture(params=["2025.12.0", "2026.10.1"])
def registry_context(ha_service_modules, monkeypatch, request):
    c = ha_service_modules
    modern = request.param == "2026.10.1"
    registry = ModernRegistry() if modern else LegacyRegistry()
    monkeypatch.setattr(c.integration.dr, "async_get", lambda _: registry)
    monkeypatch.setattr(c.integration.dr, "DeviceInfo", ModernDeviceInfo if modern else LegacyDeviceInfo,
                        raising=False)

    class CoordinatorEntity:
        @classmethod
        def __class_getitem__(cls, _):
            return cls

        def __init__(self, coordinator):
            self.coordinator = coordinator

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
        SWING_MODE = 32
        TURN_ON = 128
        TURN_OFF = 256

    def module(name, **members):
        result = types.ModuleType(name)
        result.__dict__.update(members)
        monkeypatch.setitem(sys.modules, name, result)
        return result

    module("homeassistant.helpers.update_coordinator", CoordinatorEntity=CoordinatorEntity)
    module("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
    module("homeassistant.components").__path__ = []
    module("homeassistant.components.climate", ClimateEntity=type("ClimateEntity", (), {}),
           ClimateEntityFeature=Features, HVACMode=HVACMode, ATTR_HVAC_MODE="hvac_mode")
    constants = sys.modules["homeassistant.const"]
    monkeypatch.setattr(constants, "ATTR_TEMPERATURE", "temperature", raising=False)
    monkeypatch.setattr(constants, "UnitOfTemperature", types.SimpleNamespace(CELSIUS="°C"), raising=False)
    climate = importlib.import_module(c.integration.__package__ + ".climate")
    entries = {}
    observations = []

    async def forward(hass_entry, platforms):
        assert platforms == c.integration.PLATFORMS
        coordinator = c.hass.data["ae200"][hass_entry.entry_id]
        parent = registry.devices[hass_entry.entry_id, PARENT]
        assert coordinator.controller_device_id == parent.id
        assert registry.registrations[-1][1] == PARENT

        def add_entities(entities):
            assert len(entities) == 1
            for entity in entities:
                info = entity.device_info
                assert info["identifiers"] == {GROUP}
                assert ("via_device_id" in info, "via_device" in info) == (modern, not modern)
                if modern:
                    assert info["via_device_id"] == parent.id
                else:
                    assert info["via_device"] == PARENT
                device = registry.async_get_or_create(config_entry_id=hass_entry.entry_id, **info)
                key = ("climate", "ae200", entity._attr_unique_id)
                registered = entries.setdefault(key, types.SimpleNamespace(
                    unique_id=entity._attr_unique_id, entity_id="climate.new_group", device_id=device.id,
                    name="User entity name", area_id="entity-area", disabled_by="user",
                    options={"user": "option"},
                ))
                registered.device_id = device.id
                observations.append((coordinator, info, registered))

        await climate.async_setup_entry(c.hass, hass_entry, add_entities)

    c.hass.config_entries.async_forward_entry_setups = AsyncMock(side_effect=forward)
    original_coordinator = c.Coordinator

    def create_coordinator(*args, **kwargs):
        coordinator = original_coordinator(*args, **kwargs)
        coordinator.data = {"1": MelansDevice(
            "1", "Reported group", {"Drive": "OFF", "Mode": "COOL", "SetTemp": "25"},
        )}
        coordinator.controller_device_id = None
        return coordinator

    monkeypatch.setattr(c.integration, "AE200Coordinator", create_coordinator)
    return types.SimpleNamespace(context=c, registry=registry, modern=modern,
                                 entity_entries=entries, observations=observations, climate=climate)


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", [False, True])
async def test_parent_first_setup_and_reload_keep_device_entity_ids_and_user_configuration(registry_context, existing):
    t = registry_context
    c, registry = t.context, t.registry
    selected_entry = entry()
    c.entries[selected_entry.entry_id] = selected_entry
    if existing:
        registry.seed("selected", PARENT, "controller-kept", model="Known controller")
        registry.seed("selected", GROUP, "group-kept", via_device_id="controller-kept")
        t.entity_entries["climate", "ae200", UID] = types.SimpleNamespace(
            unique_id=UID, entity_id="climate.user_custom_ac", device_id="group-kept",
            name="Custom entity", area_id="entity-area", disabled_by="user", options={"restore": False},
        )
    before_devices = copy.deepcopy(registry.devices)
    before_entities = copy.deepcopy(t.entity_entries)
    before_config = copy.deepcopy((selected_entry.data, selected_entry.options))
    assert await c.integration.async_setup_entry(c.hass, selected_entry)
    first_parent = registry.devices["selected", PARENT]
    first_group = registry.devices["selected", GROUP]
    first_entity = t.entity_entries["climate", "ae200", UID]
    ids = first_parent.id, first_group.id, first_entity.entity_id
    assert first_group.via_device_id == first_parent.id
    assert first_entity.device_id == first_group.id and first_entity.unique_id == UID
    assert first_parent.model == ("Known controller" if existing else "AE-200 / AE-50 / EW-50")
    assert await c.integration.async_setup_entry(c.hass, selected_entry)
    assert (first_parent.id, first_group.id, first_entity.entity_id) == ids
    assert len(registry.devices) == 2 and len(t.entity_entries) == 1
    assert (selected_entry.data, selected_entry.options) == before_config
    if existing:
        assert vars(first_entity) == vars(before_entities["climate", "ae200", UID])
        for key, before in before_devices.items():
            after = registry.devices[key]
            assert after.id == before.id and after.identifiers == before.identifiers
            for field in ("name_by_user", "area_id", "labels", "disabled_by", "sw_version", "via_device_id"):
                assert getattr(after, field) == getattr(before, field)
    if t.modern:
        assert registry.lookups == [(PARENT, "selected"), (PARENT, "selected")]
    else:
        assert registry.lookups == [({PARENT}, None), ({PARENT}, None)]


@pytest.mark.asyncio
@pytest.mark.parametrize("registry_context", ["2026.10.1"], indirect=True)
@pytest.mark.parametrize("selected_exists", [False, True])
async def test_modern_same_identifier_in_another_entry_cannot_supply_model_or_parent(registry_context, selected_exists):
    t = registry_context
    c, registry = t.context, t.registry
    other = registry.seed("other", PARENT, "other-controller", model="Other entry model")
    other_before = copy.deepcopy(vars(other))
    if selected_exists:
        registry.seed("selected", PARENT, "selected-controller", model="Selected known model")
    selected_entry = entry()
    c.entries[selected_entry.entry_id] = selected_entry
    assert await c.integration.async_setup_entry(c.hass, selected_entry)
    controller, info, _ = t.observations[-1]
    selected_parent = registry.devices["selected", PARENT]
    assert selected_parent.model == ("Selected known model" if selected_exists else "AE-200 / AE-50 / EW-50")
    assert controller.controller_device_id == info["via_device_id"] == selected_parent.id
    assert selected_parent.id != other.id
    assert vars(other) == other_before
    assert registry.lookups == [(PARENT, "selected")]


@pytest.mark.parametrize("registry_context", ["2026.10.1"], indirect=True)
def test_modern_entity_requires_registered_parent_instead_of_tuple_or_missing_link(registry_context):
    t = registry_context
    coordinator = types.SimpleNamespace(host=HOST, controller_device_id=None,
                                        data={"1": MelansDevice("1", None, {"Mode": "COOL"})})
    entity = t.climate.AE200Climate(coordinator, "1")
    with pytest.raises(RuntimeError, match="registered before group entities"):
        _ = entity.device_info
    assert not t.registry.registrations
