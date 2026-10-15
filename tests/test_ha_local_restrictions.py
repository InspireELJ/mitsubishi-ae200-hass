"""Read-only local prohibition attributes on the existing RemoCon switch."""

from __future__ import annotations

import importlib
import sys
import time
import types
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from pymelans import MelansClient, MelansDevice

FIELDS = {
    "local_power_prohibited": "DriveItem",
    "local_mode_prohibited": "ModeItem",
    "local_temperature_prohibited": "SetTempItem",
    "local_filter_reset_prohibited": "FilterItem",
}


@pytest.fixture
def local_restriction_module(monkeypatch):
    def module(name, **members):
        result = types.ModuleType(name)
        result.__dict__.update(members)
        monkeypatch.setitem(sys.modules, name, result)
        return result

    class CoordinatorEntity:
        @classmethod
        def __class_getitem__(cls, _):
            return cls

        def __init__(self, coordinator):
            self.coordinator = coordinator

        @property
        def available(self):
            return self.coordinator.last_update_success

    for name in ("homeassistant", "homeassistant.helpers", "homeassistant.components"):
        module(name).__path__ = []
    module("homeassistant.helpers.device_registry", DeviceInfo=dict)
    module("homeassistant.helpers.update_coordinator", CoordinatorEntity=CoordinatorEntity)
    module("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
    module("homeassistant.core", HomeAssistant=object)
    module("homeassistant.config_entries", ConfigEntry=object)
    module("homeassistant.const", EntityCategory=types.SimpleNamespace(CONFIG="config"))
    module("homeassistant.components.switch", SwitchEntity=type("SwitchEntity", (), {}))
    name = "ae200_local_restriction_test"
    package = module(name)
    package.__path__ = [str(Path(__file__).resolve().parents[1] / "custom_components/ae200")]
    module(name + ".coordinator", AE200Coordinator=object)
    yield importlib.import_module(name + ".switch")
    for key in list(sys.modules):
        if key.startswith(name + "."):
            sys.modules.pop(key, None)


def coordinator(attrs):
    client = MelansClient("fixture.invalid")
    client.devices["1"] = MelansDevice("1", "Fixture room", attrs)
    client._names = {"1": None}
    client._group_seen["1"] = time.monotonic()
    client._request = AsyncMock(side_effect=AssertionError("Attribute reads must use the cache"))
    return types.SimpleNamespace(
        host=client.host, client=client, data=client.devices,
        last_update_success=True, async_request_refresh=AsyncMock(),
    )


@pytest.mark.parametrize("attribute,field", FIELDS.items())
@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, None), ("", None), ("*", None), ("UNKNOWN", None),
        ("ON", None), ("OFF", None), ("CHK_ON", True), ("CHK_OFF", False),
    ],
)
def test_only_verified_item_tokens_have_boolean_meaning(
    local_restriction_module, attribute, field, raw, expected
):
    c = coordinator({"RemoCon": "PROHIBIT", **({} if raw is None else {field: raw})})
    entity = local_restriction_module.AE200RemoteLockSwitch(c, "1")
    attributes = entity.extra_state_attributes

    assert attributes == {key: expected if key == attribute else None for key in FIELDS}
    c.client._request.assert_not_awaited()
    c.async_request_refresh.assert_not_awaited()


@pytest.mark.parametrize("summary,expected", [("PERMIT", False), ("PROHIBIT", True), ("*", None)])
def test_legacy_summary_is_preserved_while_items_show_effective_restrictions(
    local_restriction_module, summary, expected
):
    c = coordinator({"RemoCon": summary, "DriveItem": "CHK_ON", "ModeItem": "CHK_OFF"})
    entity = local_restriction_module.AE200RemoteLockSwitch(c, "1")

    assert entity.is_on is expected
    assert entity.extra_state_attributes["local_power_prohibited"] is expected
    assert entity.extra_state_attributes["local_mode_prohibited"] is False
    assert entity._attr_unique_id == "fixture.invalid_1_remote_lock"
    assert entity._attr_translation_key == "remote_lock"
    assert entity.device_info["identifiers"] == {("ae200", "fixture.invalid:1")}
    assert entity.device_info["via_device"] == ("ae200", "fixture.invalid")
    c.client._request.assert_not_awaited()


def test_missing_device_and_returned_attribute_mutation_do_not_create_false_values(
    local_restriction_module,
):
    c = coordinator({"RemoCon": "PROHIBIT", "SetTempItem": "CHK_ON"})
    entity = local_restriction_module.AE200RemoteLockSwitch(c, "1")
    returned = entity.extra_state_attributes
    returned["local_temperature_prohibited"] = False
    assert entity.extra_state_attributes["local_temperature_prohibited"] is True

    c.data.clear()

    assert entity.extra_state_attributes == dict.fromkeys(FIELDS)
    assert entity.is_on is None
    assert not entity.available
    c.client._request.assert_not_awaited()


@pytest.mark.parametrize("attribute,field", FIELDS.items())
@pytest.mark.parametrize(
    "summary,item,expected",
    [
        ("PERMIT", "CHK_ON", False),
        ("PERMIT", "CHK_OFF", False),
        ("PERMIT", None, False),
        ("PERMIT", "UNKNOWN", False),
        ("PROHIBIT", "CHK_ON", True),
        ("PROHIBIT", "CHK_OFF", False),
        ("PROHIBIT", None, None),
        (None, "CHK_OFF", False),
        (None, "CHK_ON", None),
        ("*", "CHK_OFF", False),
        ("*", "CHK_ON", None),
        ("UNKNOWN", "UNKNOWN", None),
    ],
)
def test_effective_restrictions_follow_master_permission_and_item_flags(
    local_restriction_module, attribute, field, summary, item, expected
):
    attrs = {}
    if summary is not None:
        attrs["RemoCon"] = summary
    if item is not None:
        attrs[field] = item
    c = coordinator(attrs)
    entity = local_restriction_module.AE200RemoteLockSwitch(c, "1")

    assert entity.extra_state_attributes[attribute] is expected
    assert c.data["1"].raw_attributes() == attrs
    c.client._request.assert_not_awaited()
    c.async_request_refresh.assert_not_awaited()


@pytest.mark.parametrize("failure", ["group_failed", "group_expired", "coordinator_failed"])
def test_unavailable_groups_do_not_publish_historical_effective_restrictions(
    local_restriction_module, failure
):
    raw = {
        "RemoCon": "PROHIBIT", "DriveItem": "CHK_ON", "ModeItem": "CHK_OFF",
        "SetTempItem": "CHK_ON", "FilterItem": "CHK_OFF",
    }
    c = coordinator(raw)
    c.client.devices["2"] = MelansDevice("2", "Other room", {"RemoCon": "PERMIT"})
    c.client._names["2"] = None
    c.client._group_seen["2"] = time.monotonic()
    entity = local_restriction_module.AE200RemoteLockSwitch(c, "1")
    other = local_restriction_module.AE200RemoteLockSwitch(c, "2")
    assert entity.available and other.available
    assert entity.extra_state_attributes["local_power_prohibited"] is True
    assert entity.extra_state_attributes["local_mode_prohibited"] is False

    if failure == "group_failed":
        c.client.failed_groups.add("1")
    elif failure == "group_expired":
        c.client._group_seen["1"] = time.monotonic() - c.client.stale_after - 1
    else:
        c.last_update_success = False

    assert not entity.available
    assert entity.extra_state_attributes == dict.fromkeys(FIELDS)
    assert entity.is_on is True  # Existing summary behaviour is retained.
    assert c.client.devices["1"].raw_attributes() == raw
    if failure == "coordinator_failed":
        assert other.extra_state_attributes == dict.fromkeys(FIELDS)
    else:
        assert other.available
        assert other.extra_state_attributes == dict.fromkeys(FIELDS, False)
    c.client._request.assert_not_awaited()
    c.async_request_refresh.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("method,wire", [("async_turn_on", "PROHIBIT"), ("async_turn_off", "PERMIT")])
async def test_legacy_switch_commands_still_write_only_remocon(
    local_restriction_module, method, wire
):
    c = coordinator({"RemoCon": "PERMIT", "DriveItem": "CHK_OFF"})
    c.client._request = AsyncMock(
        return_value="<Packet><Command>setResponse</Command><DatabaseManager /></Packet>"
    )
    entity = local_restriction_module.AE200RemoteLockSwitch(c, "1")

    await getattr(entity, method)()

    c.client._request.assert_awaited_once()
    packet = ET.fromstring(c.client._request.call_args.args[0])
    assert packet.findtext("Command") == "setRequest"
    assert packet.find("./DatabaseManager/Mnet").attrib == {"Group": "1", "RemoCon": wire}
    c.async_request_refresh.assert_awaited_once()
    assert entity.is_on is False  # Only a later readback changes the reported summary.
    assert entity.extra_state_attributes["local_power_prohibited"] is False
