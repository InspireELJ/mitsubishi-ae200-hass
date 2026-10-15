"""Synthetic failure-path tests. No site addresses or production data."""

from __future__ import annotations

import asyncio
import json
import time
import xml.etree.ElementTree as ET

import pytest
from pymelans.exceptions import MelansConnectionError, MelansProtocolError
from pymelans.protocol import build_set_request, build_state_request, parse_acknowledgement

from pymelans import MelansClient, MelansDevice, cached_snapshot, const

ACK = "<Packet><Command>setResponse</Command><DatabaseManager /></Packet>"
REJECT = '<Packet><Command>setErrorResponse</Command><DatabaseManager><ERROR Code="0201" Message="Rejected fixture" /></DatabaseManager></Packet>'


def packet(*groups: tuple[str, dict[str, str]]) -> str:
    root = ET.Element("Packet")
    ET.SubElement(root, "Command").text = "getResponse"
    manager = ET.SubElement(root, "DatabaseManager")
    for gid, attrs in groups:
        ET.SubElement(manager, "Mnet", {"Group": gid, **attrs})
    return ET.tostring(root, encoding="unicode")


@pytest.fixture
def client():
    c = MelansClient("fixture.invalid")
    c._names = {"1": "Private fixture room", "2": "Another private room"}
    return c


@pytest.fixture(autouse=True)
def fast_io(monkeypatch):
    # Tests bypass controller pacing except the explicit pacing tests below.
    monkeypatch.setattr(const, "MIN_REQUEST_SPACING", 0)


def expire_poll(c):
    c._last_poll = time.monotonic() - const.MIN_POLL_INTERVAL - 1


@pytest.mark.parametrize(
    "attrs", [{}, {"Drive": "*", "FilterSign": "*"}, {"Drive": "UNSUPPORTED", "ErrorSign": "0"}]
)
def test_missing_and_unrecognised_flags_are_unknown(attrs):
    d = MelansDevice("1", None, attrs)
    assert d.is_on is None
    assert d.filter_sign is None
    assert d.error_sign is None
    assert d.local_remote_prohibited is None


def test_explicit_false_flags_remain_false():
    d = MelansDevice(
        "1", None, {"Drive": "OFF", "FilterSign": "OFF", "ErrorSign": "OFF", "RemoCon": "PERMIT"}
    )
    assert (d.is_on, d.filter_sign, d.error_sign, d.local_remote_prohibited) == (
        False,
        False,
        False,
        False,
    )


@pytest.mark.parametrize("value", ["0", "0.0", "-1", "101", "nan", "inf", "*"])
def test_invalid_or_unverified_humidity_is_not_a_measurement(value):
    assert MelansDevice("1", None, {"InletHumidity": value}).current_humidity is None


def test_valid_secondary_humidity_and_zero_outdoor_temperature():
    d = MelansDevice(
        "1", None, {"RoomHumidity": "0.0", "InletHumidity": "45.5", "OutdoorTemp": "0.0"}
    )
    assert d.current_humidity == 45.5
    assert d.outdoor_temp == 0


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "-40.7", "*"])
def test_unusable_temperature_is_unknown(value):
    assert MelansDevice("1", None, {"InletTemp": value}).current_temperature is None


def test_raw_attributes_are_copied():
    d = MelansDevice("1", None, {"Drive": "ON"})
    attrs = d.raw_attributes()
    attrs["Drive"] = "OFF"
    assert d.is_on is True


@pytest.mark.parametrize("gid", ["0", "51", "-1", '1" Drive="ON'])
def test_invalid_group_ids_never_form_packets(gid):
    with pytest.raises(ValueError):
        build_state_request([gid])
    with pytest.raises(ValueError):
        build_set_request(gid, {"Drive": "ON"})


def test_write_values_are_xml_escaped():
    value = 'a" & <b>'
    xml = build_set_request("1", {"Example": value})
    assert ET.fromstring(xml).find("./DatabaseManager/Mnet").get("Example") == value


@pytest.mark.parametrize(
    "reply", [REJECT, "", "<html>error</html>", "<Packet><Command>getResponse</Command></Packet>"]
)
def test_unconfirmed_or_rejected_acknowledgement_raises(reply):
    with pytest.raises(MelansProtocolError):
        parse_acknowledgement(reply)


def test_normal_acknowledgement():
    assert parse_acknowledgement(ACK) is None


@pytest.mark.asyncio
async def test_total_outage_invalidates_cached_groups(client):
    async def good(_):
        return packet(("1", {"Drive": "ON"}), ("2", {"Drive": "OFF"}))

    client._request = good
    await client.async_update()
    last_success = dict(client.group_last_success)
    expire_poll(client)
    calls = []

    async def failed(payload):
        calls.append(payload)
        raise MelansConnectionError("synthetic outage")

    client._request = failed
    with pytest.raises(MelansConnectionError):
        await client.async_update()
    assert len(calls) == 2  # Batch plus one check, not a timeout for every group.
    assert client.health == "offline"
    assert client.failed_groups == {"1", "2"}
    assert client.group_last_success == last_success
    assert not client.is_group_available("1")
    assert client.devices["1"].is_on is True  # Historical only, never available.


@pytest.mark.asyncio
async def test_partial_read_marks_only_missing_group_stale(client):
    async def initial(_):
        return packet(("1", {"Drive": "ON"}), ("2", {"Drive": "OFF"}))

    client._request = initial
    await client.async_update()
    previous = client.group_last_success["2"]
    expire_poll(client)

    async def partial(payload):
        requested = [
            e.get("Group") for e in ET.fromstring(payload).findall("./DatabaseManager/Mnet")
        ]
        if requested == ["2"]:
            raise MelansConnectionError("group fixture failed")
        return packet(("1", {"Drive": "OFF"}))

    client._request = partial
    await client.async_update()
    assert client.health == "degraded"
    assert client.is_group_available("1")
    assert not client.is_group_available("2")
    assert client.group_last_success["2"] == previous


@pytest.mark.asyncio
async def test_single_group_fallback_handles_truncated_batches(client):
    calls = []

    async def reply(payload):
        calls.append(payload)
        return (
            packet(("2", {"Drive": "OFF"})) if len(calls) == 2 else packet(("1", {"Drive": "ON"}))
        )

    client._request = reply
    await client.async_update()
    assert len(calls) == 2
    assert client.health == "online"


@pytest.mark.asyncio
async def test_repeated_poll_does_not_promote_cached_data(client):
    calls = []

    async def reply(payload):
        calls.append(payload)
        return packet(("1", {"Drive": "OFF"}), ("2", {"Drive": "OFF"}))

    client._request = reply
    await client.async_update()
    last_success = client.last_success
    await client.async_update()
    assert len(calls) == 1
    assert client.last_success == last_success


@pytest.mark.asyncio
async def test_rejected_write_never_changes_cache(client):
    client.devices["1"] = MelansDevice("1", None, {"Drive": "OFF"})

    async def reject(_):
        return REJECT

    client._request = reject
    with pytest.raises(MelansProtocolError) as exc:
        await client.set_power("1", True)
    assert exc.value.code == "0201"
    assert client.devices["1"].is_on is False
    assert client.last_command["status"] == "failed"
    assert not client._pending_writes


@pytest.mark.asyncio
async def test_accepted_write_requires_readback(client):
    client._names = {"1": "Fixture room"}
    client.devices["1"] = MelansDevice("1", None, {"Drive": "OFF"})
    client._last_poll = time.monotonic()

    async def reply(payload):
        if ET.fromstring(payload).findtext("Command") == "setRequest":
            return ACK
        return packet(("1", {"Drive": "ON"}))

    client._request = reply
    await client.set_power("1", True)
    assert client.devices["1"].is_on is False
    assert client.last_command["status"] == "accepted_unconfirmed"
    await client.async_update()  # One post-write readback can bypass the poll floor.
    assert client.last_command["status"] == "confirmed"
    assert client.devices["1"].is_on is True


@pytest.mark.asyncio
async def test_readback_mismatch_is_visible_without_retry_storm(client):
    client._names = {"1": None}
    calls = []

    async def reply(payload):
        calls.append(payload)
        return (
            ACK
            if ET.fromstring(payload).findtext("Command") == "setRequest"
            else packet(("1", {"Drive": "OFF"}))
        )

    client._request = reply
    await client.set_power("1", True)
    await client.async_update()
    await client.async_update()
    assert len(calls) == 2
    assert client.last_command["status"] == "not_yet_confirmed"


@pytest.mark.asyncio
async def test_later_rejected_command_is_not_overwritten_by_earlier_readback(client):
    client._names = {"1": None}

    async def reply(payload):
        root = ET.fromstring(payload)
        if root.findtext("Command") != "setRequest":
            return packet(("1", {"Drive": "ON", "Mode": "COOL"}))
        return REJECT if root.find("./DatabaseManager/Mnet").get("Mode") else ACK

    client._request = reply
    await client.set_power("1", True)
    client._last_write = 0  # Avoid a real one-second wait in this fixture.
    with pytest.raises(MelansProtocolError):
        await client.set_mode("1", "HEAT")
    await client.async_update()
    assert client.last_command["status"] == "failed"


@pytest.mark.asyncio
async def test_transport_requests_are_serialised_and_paced(client, monkeypatch):
    monkeypatch.setattr(const, "MIN_REQUEST_SPACING", 0.02)
    starts = []
    concurrent = 0
    peak = 0

    async def reply(_):
        nonlocal concurrent, peak
        concurrent += 1
        peak = max(peak, concurrent)
        starts.append(time.monotonic())
        await asyncio.sleep(0.005)
        concurrent -= 1
        return ACK

    client._ws.request = reply
    await asyncio.gather(*(client._request("fixture") for _ in range(3)))
    assert peak == 1
    assert all(b - a >= 0.018 for a, b in zip(starts, starts[1:]))


@pytest.mark.asyncio
async def test_websocket_is_retried_after_cooldown(client):
    calls = []

    async def failed(_):
        raise MelansConnectionError("fixture")

    async def good(_):
        calls.append("ws")
        return ACK

    async def http(_):
        calls.append("http")
        return ACK

    client._ws.request = failed
    client._http.request = http
    await client._request("fixture")
    assert client.transport_name == "http"
    client._ws.request = good
    await client._request("fixture")
    assert calls == ["http", "http"]
    client._ws_retry_after = 0
    await client._request("fixture")
    assert client.transport_name == "websocket"


def test_cached_diagnostics_redact_identity_and_preserve_unknown_fields(client):
    client.devices["1"] = MelansDevice(
        "1",
        "Private fixture room",
        {
            "InletHumidity": "0.0",
            "FanSpeed": "FUTURE_SPEED",
            "Password": "fixture-secret",
            "FutureField": "supported-value",
            "LocalIP": "private-controller-address",
        },
    )
    client.last_command = {"attributes": {"Password": "another-secret"}}
    snapshot = cached_snapshot(client)
    text = json.dumps(snapshot)
    assert "fixture-secret" not in text and "another-secret" not in text
    assert client.host not in text and "Private fixture room" not in text
    assert "private-controller-address" not in text
    assert snapshot["groups"]["1"]["raw_attributes"]["InletHumidity"] == "0.0"
    assert "unmapped_fan_speed" in snapshot["groups"]["1"]["warnings"]
    assert "FutureField" in snapshot["groups"]["1"]["unrequested_attributes"]


def test_elapsed_freshness_expires_without_changing_last_good_timestamp(client):
    client._group_seen["1"] = time.monotonic() - client.stale_after - 1
    assert not client.is_group_available("1")
