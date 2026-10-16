"""Late humidity fields add one existing-style entity using cached updates."""

from __future__ import annotations

import time
import types
import xml.etree.ElementTree as ET
from unittest.mock import AsyncMock

import pytest
from pymelans.protocol import parse_state
from test_ha_observability import observability_modules as observability_modules

from pymelans import MelansClient, MelansDevice


def state(**attributes):
    root = ET.Element("Packet")
    ET.SubElement(root, "Command").text = "getResponse"
    ET.SubElement(ET.SubElement(root, "DatabaseManager"), "Mnet", {
        "Group": "1", "SetTemp": "25", **attributes,
    })
    return parse_state(ET.tostring(root, encoding="unicode"))["1"]


def humidity_entities(context):
    return [entity for entity in context.added
            if getattr(getattr(entity, "entity_description", None), "key", None) == "humidity"]


@pytest.fixture
def context(observability_modules):
    client = MelansClient("fixture.invalid")
    client._request = AsyncMock(side_effect=AssertionError("Sensor registration must not read"))
    client.async_update = AsyncMock(side_effect=AssertionError("Sensor registration must not poll"))
    client._names = {"1": None}
    client.devices = {"1": MelansDevice(
        "1", None, state(RoomHumidity="", InletHumidity="*"),
        capability_attrs={"HumiditySW": "DISABLE"},
    )}
    client._group_seen = {"1": time.monotonic()}
    listeners, unload, added = [], [], []

    def add_listener(update_callback, context=None):
        listeners.append(update_callback)
        return lambda: listeners.remove(update_callback)

    coordinator = types.SimpleNamespace(
        host=client.host, client=client, data=client.devices, last_update_success=True,
        async_add_passive_listener=add_listener,
        async_request_refresh=AsyncMock(side_effect=AssertionError("No refresh may be requested")),
    )
    entry = types.SimpleNamespace(entry_id="selected", async_on_unload=unload.append)
    hass = types.SimpleNamespace(data={"ae200": {"selected": coordinator}})
    result = types.SimpleNamespace(
        sensor=observability_modules.sensor, client=client, coordinator=coordinator,
        entry=entry, hass=hass, listeners=listeners, unload=unload, added=added,
        notify=lambda: [callback() for callback in list(listeners)],
    )
    yield result
    client._request.assert_not_awaited()
    client.async_update.assert_not_awaited()
    coordinator.async_request_refresh.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["RoomHumidity", "InletHumidity"])
async def test_late_positive_response_adds_humidity_with_original_uid_unit_and_no_control_feature(context, field):
    c = context
    await c.sensor.async_setup_entry(c.hass, c.entry, c.added.extend)
    assert not humidity_entities(c) and len(c.listeners) == len(c.unload) == 1
    c.coordinator.data["1"].update(state(**{field: "55.5"}))
    c.notify()
    sensor, = humidity_entities(c)
    assert sensor._attr_unique_id == "fixture.invalid_1_humidity"
    assert sensor.native_value == 55.5 and sensor.available
    assert sensor.entity_description.native_unit_of_measurement == "%"
    assert sensor.entity_description.device_class == "humidity"
    assert sensor.entity_description.state_class == "measurement"
    assert c.coordinator.data["1"].capability_attributes["HumiditySW"] == "DISABLE"
    for _ in range(5):
        c.notify()
    assert humidity_entities(c) == [sensor]
    c.coordinator.data["1"].update(state(RoomHumidity="", InletHumidity="*"))
    c.notify()
    assert humidity_entities(c) == [sensor] and sensor.native_value is None
    c.coordinator.data["1"].update(state(RoomHumidity="0", InletHumidity="45.5"))
    c.notify()
    assert humidity_entities(c) == [sensor] and sensor.native_value == 45.5


@pytest.mark.asyncio
@pytest.mark.parametrize("attributes", [{}, {"RoomHumidity": "", "InletHumidity": "*"},
                                          {"SetHumidity": "50", "Humid": "ON"}])
async def test_absent_measurement_fields_do_not_create_unconditional_unknown_humidity_entities(context, attributes):
    c = context
    c.coordinator.data["1"].update(state(**attributes))
    await c.sensor.async_setup_entry(c.hass, c.entry, c.added.extend)
    for _ in range(3):
        c.notify()
    assert not humidity_entities(c)


@pytest.mark.asyncio
async def test_late_zero_keeps_existing_field_presence_semantics_without_fabricating_measurement(context):
    c = context
    await c.sensor.async_setup_entry(c.hass, c.entry, c.added.extend)
    c.coordinator.data["1"].update(state(InletHumidity="0"))
    c.notify()
    sensor, = humidity_entities(c)
    assert sensor.native_value is None and sensor.available
    c.notify()
    assert humidity_entities(c) == [sensor]


@pytest.mark.asyncio
async def test_original_eighteen_zero_placeholder_entities_are_not_recreated_on_updates(context):
    c = context
    c.coordinator.data.clear()
    for group in range(1, 19):
        gid = str(group)
        c.client._names[gid] = None
        c.coordinator.data[gid] = MelansDevice(gid, None, state(InletHumidity="0", RoomHumidity=""))
        c.client._group_seen[gid] = time.monotonic()
    await c.sensor.async_setup_entry(c.hass, c.entry, c.added.extend)
    original = list(humidity_entities(c))
    assert len(original) == 18 and all(entity.native_value is None for entity in original)
    assert {entity._attr_unique_id for entity in original} == {
        f"fixture.invalid_{group}_humidity" for group in range(1, 19)
    }
    for device in c.coordinator.data.values():
        device.update(state(RoomHumidity="42.5", InletHumidity="0"))
    for _ in range(5):
        c.notify()
    assert humidity_entities(c) == original
    assert all(entity.native_value == 42.5 for entity in original)


@pytest.mark.asyncio
async def test_failed_coordinator_failed_group_and_stale_cache_cannot_register_late_measurements(context):
    c = context
    await c.sensor.async_setup_entry(c.hass, c.entry, c.added.extend)
    c.coordinator.data["1"].update(state(RoomHumidity="55.5"))
    c.coordinator.last_update_success = False
    c.notify()
    assert not humidity_entities(c)
    c.coordinator.last_update_success = True
    c.client.failed_groups.add("1")
    c.notify()
    assert not humidity_entities(c)
    c.client.failed_groups.clear()
    c.client._group_seen["1"] = time.monotonic() - c.client.stale_after - 1
    c.notify()
    assert not humidity_entities(c)
    c.client._group_seen["1"] = time.monotonic()
    c.notify()
    sensor, = humidity_entities(c)
    assert sensor.native_value == 55.5


@pytest.mark.asyncio
async def test_unload_removes_discovery_listener_and_future_updates_add_nothing(context):
    c = context
    await c.sensor.async_setup_entry(c.hass, c.entry, c.added.extend)
    remove, = c.unload
    remove()
    assert c.listeners == []
    c.coordinator.data["1"].update(state(RoomHumidity="55.5"))
    c.notify()
    assert not humidity_entities(c)


@pytest.mark.asyncio
async def test_reentrant_notification_during_entity_enqueue_does_not_duplicate_uid(context):
    c = context

    def add(entities):
        c.added.extend(entities)
        if any(getattr(getattr(entity, "entity_description", None), "key", None) == "humidity"
               for entity in entities):
            c.notify()

    await c.sensor.async_setup_entry(c.hass, c.entry, add)
    c.coordinator.data["1"].update(state(RoomHumidity="55.5"))
    c.notify()
    assert len(humidity_entities(c)) == 1
