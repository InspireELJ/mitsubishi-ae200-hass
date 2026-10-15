"""Optional topology and explicit member reads cannot change group monitoring."""

from __future__ import annotations

import asyncio
import json
import xml.etree.ElementTree as ET
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pymelans.exceptions import MelansConnectionError, MelansProtocolError

from pymelans import MelansClient, MelansDevice, cached_snapshot


def membership_packet(*records):
    root = ET.Element("Packet")
    ET.SubElement(root, "Command").text = "getResponse"
    manager = ET.SubElement(root, "DatabaseManager")
    directory = ET.SubElement(ET.SubElement(manager, "ControlGroup"), "MnetGroupList")
    for record in records:
        ET.SubElement(directory, "MnetGroupRecord", record)
    return ET.tostring(root, encoding="unicode")


def address_packet(*records, command="getResponse"):
    root = ET.Element("Packet")
    ET.SubElement(root, "Command").text = command
    manager = ET.SubElement(root, "DatabaseManager")
    for record in records:
        ET.SubElement(manager, "Mnet", record)
    return ET.tostring(root, encoding="unicode")


DIRECTORY = membership_packet(
    {"Group": "1", "Address": "1", "Model": "IC", "SubModel": ""},
    {"Group": "1", "Address": "2", "Model": "IC", "SubModel": ""},
    {"Group": "2", "Address": "3", "Model": "LC"},
    {"Group": "2", "Address": "4", "Model": "IC"},
    {"Group": "2", "Address": "4", "Model": "LC"},
    {"Group": "2", "Address": "0", "Model": "DC", "Contact": "1"},
)
STATE = {
    "Address": "1", "Group": "1", "Model": "IC", "SubModel": "",
    "ErrorSign": "OFF", "AlarmCode": "8000", "ThermoStatus": "OFF", "FanStatus": "OFF",
}


@pytest.fixture
def client(monkeypatch):
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(
        "pymelans.client.time",
        SimpleNamespace(monotonic=lambda: clock.now, perf_counter=lambda: clock.now),
    )
    result = MelansClient("fixture.invalid")
    result._names = {"1": "Private fixture room", "2": None}
    result._request = AsyncMock(return_value=DIRECTORY)
    return result, clock


@pytest.mark.asyncio
async def test_topology_is_one_optional_request_and_never_an_address_poll(client):
    c, _ = client
    await asyncio.gather(c.async_load_topology(), c.async_load_topology())
    c._request.assert_awaited_once()
    request = ET.fromstring(c._request.call_args.args[0])
    assert request.find("./DatabaseManager/ControlGroup/MnetGroupList") is not None
    assert request.find("./DatabaseManager/Mnet") is None
    assert c.allowed_member_addresses == ("1", "2")
    assert len(c.topology.records) == 6
    assert c.member_states == c.member_last_success == {}
    assert c.devices == {} and c.last_success is None and c.last_attempt is None
    assert c._group_seen == {} and c._pending_writes == {}


@pytest.mark.asyncio
async def test_optional_directory_failure_is_not_retried_or_a_group_failure(client):
    c, _ = client
    c._request = AsyncMock(side_effect=MelansProtocolError("private-controller-message", code="0101"))
    await c.async_load_topology()
    await c.async_load_topology()
    c._request.assert_awaited_once()
    assert c.topology is None and c.topology_last_success is None
    assert c.topology_error == {"type": "MelansProtocolError", "code": "0101"}
    c._request = AsyncMock(return_value=address_packet(
        {"Group": "1", "Drive": "OFF"}, {"Group": "2", "Drive": "OFF"}
    ))
    await c.async_update()
    assert c.health == "online" and c.failed_groups == set()
    assert "private-controller-message" not in json.dumps(cached_snapshot(c))


@pytest.mark.asyncio
@pytest.mark.parametrize("address", [0, 251, "*", "1,2", "private.invalid", "١", 3, 4, 5])
async def test_unknown_unsupported_and_ambiguous_member_addresses_make_no_request(client, address):
    c, _ = client
    await c.async_load_topology()
    c._request.reset_mock()
    with pytest.raises(ValueError):
        await c.async_read_member(address)
    c._request.assert_not_called()
    assert c.member_last_attempt == {}


@pytest.mark.asyncio
async def test_success_is_raw_separate_cached_data_and_returned_as_a_copy(client):
    c, clock = client
    await c.async_load_topology()
    c.devices["1"] = MelansDevice("1", None, {"Drive": "OFF", "Mode": "COOL"})
    c._request = AsyncMock(return_value=address_packet(STATE))
    result = await c.async_read_member("001")
    node = ET.fromstring(c._request.call_args.args[0]).find("./DatabaseManager/Mnet")
    assert node.get("Address") == "1" and node.get("Group") == "*"
    assert "DefrostStatus" not in node.attrib
    assert result == STATE and c.member_quality("1") == "fresh"
    assert c.member_read_status["1"] == "updated"
    result["AlarmCode"] = "changed-by-caller"
    assert c.member_states["1"]["AlarmCode"] == "8000"
    assert c.devices["1"].raw_attributes() == {"Drive": "OFF", "Mode": "COOL"}
    assert c.last_success is None and c._group_seen == {} and c.last_command is None
    clock.now += c.stale_after + 1
    assert c.member_quality("1") == "stale"


@pytest.mark.asyncio
async def test_global_member_cooldown_includes_other_addresses_and_does_not_renew_sample(client):
    c, clock = client
    await c.async_load_topology()
    c._request = AsyncMock(return_value=address_packet(STATE))
    await c.async_read_member("1")
    last_success = c.member_last_success["1"]
    assert await c.async_read_member("1") is None
    assert await c.async_read_member("2") is None
    c._request.assert_awaited_once()
    assert c.member_last_success["1"] == last_success
    assert "2" not in c.member_last_attempt
    assert c.member_read_status["1"] == c.member_read_status["2"] == "cooldown"
    clock.now += 15
    c._request.return_value = address_packet({**STATE, "Address": "2"})
    assert await c.async_read_member("2") is not None
    assert c.member_quality("2") == "fresh" and c._request.await_count == 2


@pytest.mark.asyncio
async def test_concurrent_member_actions_cannot_bypass_the_global_cooldown(client):
    c, _ = client
    await c.async_load_topology()
    started = asyncio.Event()
    release = asyncio.Event()

    async def reply(_):
        started.set()
        await release.wait()
        return address_packet(STATE)

    c._request = AsyncMock(side_effect=reply)
    first = asyncio.create_task(c.async_read_member("1"))
    await started.wait()
    second = asyncio.create_task(c.async_read_member("2"))
    await asyncio.sleep(0)
    c._request.assert_awaited_once()
    release.set()
    results = await asyncio.gather(first, second)
    assert results == [STATE, None]
    c._request.assert_awaited_once()
    assert "2" not in c.member_last_attempt


@pytest.mark.asyncio
async def test_failed_member_read_preserves_sample_and_success_time_but_marks_it_stale(client):
    c, clock = client
    await c.async_load_topology()
    c._request = AsyncMock(return_value=address_packet(STATE))
    await c.async_read_member("1")
    previous_success = c.member_last_success["1"]
    clock.now += 15
    c._request = AsyncMock(side_effect=MelansConnectionError("http://private.invalid/failed"))
    assert await c.async_read_member("1") is None
    assert c.member_quality("1") == "stale"
    assert c.member_states["1"] == STATE
    assert c.member_last_success["1"] == previous_success
    assert c.member_errors["1"] == {"type": "MelansConnectionError", "code": None}
    assert c.member_read_status["1"] == "error"
    assert await c.async_read_member("1") is None
    c._request.assert_awaited_once()
    assert "private.invalid" not in json.dumps(cached_snapshot(c))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        address_packet({**STATE, "Address": "2"}),
        address_packet({**STATE, "Group": "2"}),
        address_packet({**STATE, "Model": "LC"}),
        address_packet(STATE, {**STATE, "FanStatus": "ON"}),
        address_packet(STATE, command="notifyRequest"),
        '<Packet><Command>getErrorResponse</Command><DatabaseManager>'
        '<ERROR Code="0101" Message="private-controller-message" />'
        '<Mnet Address="1" FanStatus="ON" /></DatabaseManager></Packet>',
    ],
)
async def test_missing_conflicting_mismatched_or_notified_member_samples_are_not_fresh(client, response):
    c, _ = client
    await c.async_load_topology()
    c._request = AsyncMock(return_value=response)
    assert await c.async_read_member("1") is None
    assert "1" not in c.member_states
    assert "1" not in c.member_last_success
    assert c.member_quality("1") == "unknown"
    assert c.member_errors["1"] and c.member_read_status["1"] == "error"


@pytest.mark.asyncio
async def test_cached_member_diagnostics_preserve_unknown_tokens_and_do_no_io(client):
    c, _ = client
    c._request.return_value = membership_packet({
        "Group": "1", "Address": "1", "Model": "IC", "SubModel": "",
        "GroupNameWeb": "private-room-name", "IPAddress": "private.invalid",
        "Contact": "private.invalid",
    })
    await c.async_load_topology()
    c._request.return_value = address_packet({
        **STATE, "ThermoStatus": "FUTURE_TOKEN", "Password": "secret",
        "LocationCode": "private-location", "SerialNumber": "private-serial",
    })
    await c.async_read_member("1")
    c._request.reset_mock()
    snapshot = cached_snapshot(c)
    member = snapshot["members"]["1"]
    assert member["raw_attributes"] == {**STATE, "ThermoStatus": "FUTURE_TOKEN"}
    assert member["quality"] == "fresh"
    assert snapshot["topology"]["allowed_member_addresses"] == ["1"]
    rendered = json.dumps(snapshot)
    for private in ("fixture.invalid", "private.invalid", "private-room-name", "secret",
                    "private-location", "private-serial"):
        assert private not in rendered
    member["raw_attributes"]["AlarmCode"] = "mutated"
    assert c.member_states["1"]["AlarmCode"] == "8000"
    c._request.assert_not_called()
