"""Cached read errors are safe token copies, including normal poll failures."""

from __future__ import annotations

import copy
import json
import xml.etree.ElementTree as ET
from unittest.mock import AsyncMock

import pytest
from pymelans.exceptions import MelansError

from pymelans import MelansClient, MelansDevice, cached_snapshot


@pytest.fixture
def client():
    c = MelansClient("fixture.invalid")
    c._request = AsyncMock(side_effect=AssertionError("Cached diagnostics must not request data"))
    return c


def read_errors(c):
    return cached_snapshot(c)["read_errors"]


def test_cold_export_is_empty_and_performs_no_io(client):
    assert read_errors(client) == {}
    assert client.group_errors == {}
    client._request.assert_not_awaited()


@pytest.mark.parametrize("token", [
    "controller_unavailable", "missing_group", "MelansError", "MelansProtocolError",
    "MelansConnectionError", "CancelledError", "FutureShortType_1", "T" * 64,
])
def test_safe_short_ascii_types_are_preserved_without_a_fixed_class_list(client, token):
    client.group_errors = {"1": {"type": token, "code": "0101_A-b"}}
    assert read_errors(client) == {"1": {"type": token, "code": "0101_A-b"}}
    client._request.assert_not_awaited()


@pytest.mark.parametrize("token", [None, "", "*", "T" * 65, "private.invalid/path", "bad type", "bad\nvalue", "١", True, 1, [], {}])
def test_invalid_or_missing_type_is_none_without_coercion_or_raw_text(client, token):
    client.group_errors = {"1": {"type": token, "code": "0101"}}
    assert read_errors(client) == {"1": {"type": None, "code": "0101"}}
    client.group_errors["1"].pop("type")
    assert read_errors(client) == {"1": {"type": None, "code": "0101"}}


@pytest.mark.parametrize("code,expected", [
    ("0101", "0101"), ("A_b-9", "A_b-9"), ("X" * 32, "X" * 32),
    (None, None), ("", None), ("*", None), ("X" * 33, None),
    ("http://fixture.invalid/private-secret", None), ("private.invalid", None),
    ("bad code", None), ("١", None), (True, None), (1, None), ([], None), ({}, None),
])
def test_only_bounded_ascii_error_codes_are_exported(client, code, expected):
    client.group_errors = {"1": {"type": "MelansProtocolError", "code": code}}
    assert read_errors(client) == {"1": {"type": "MelansProtocolError", "code": expected}}
    client.group_errors["1"].pop("code")
    assert read_errors(client)["1"] == {"type": "MelansProtocolError", "code": None}


@pytest.mark.parametrize("record", [None, "private-message", [], (), 1, True])
def test_malformed_error_record_does_not_crash_or_publish_its_contents(client, record):
    client.group_errors = {"1": record}
    assert read_errors(client) == {"1": {"type": None, "code": None}}


@pytest.mark.parametrize("records", [None, [], (), "private-message", 1, True])
def test_malformed_internal_error_directory_is_empty_without_string_coercion(client, records):
    client.group_errors = records
    assert read_errors(client) == {}


@pytest.mark.parametrize("key", [None, 1, True, 1.0, "0", "51", "100", "01", "004", "*", "1,2", "١", " 1", "1 ", "private.invalid"])
def test_malformed_group_identity_is_omitted_rather_than_normalised_or_exported(client, key):
    client.group_errors = {key: {"type": "MelansProtocolError", "code": "0101"}}
    assert read_errors(client) == {}


def test_legal_historical_group_keys_are_retained_without_current_allowlist_filtering(client):
    client._names = {"1": "private-room"}
    client.group_errors = {
        "1": {"type": "missing_group", "code": None},
        "50": {"type": "controller_unavailable", "code": None},
    }
    assert read_errors(client) == client.group_errors


def test_extra_error_keys_are_discarded_without_changing_internal_cache(client):
    client.group_errors = {"1": {
        "type": "MelansProtocolError", "code": "0101",
        "message": "http://fixture.invalid/private-secret", "point": "private-point",
        "url": "private-url", "nested": {"password": "private-password"},
    }}
    before = copy.deepcopy(client.group_errors)
    exported = read_errors(client)
    assert exported == {"1": {"type": "MelansProtocolError", "code": "0101"}}
    assert "private" not in json.dumps(exported)
    assert client.group_errors == before
    client._request.assert_not_awaited()


def test_outer_and_inner_error_records_are_detached_in_both_directions(client):
    client.group_errors = {
        "1": {"type": "missing_group", "code": None},
        "2": {"type": "MelansProtocolError", "code": "0101"},
    }
    expected = copy.deepcopy(client.group_errors)
    exported = read_errors(client)
    assert exported is not client.group_errors and exported["1"] is not client.group_errors["1"]
    exported["1"]["type"] = "caller"
    exported["2"]["code"] = "caller"
    exported.pop("2")
    exported["3"] = {"type": "caller", "code": None}
    assert client.group_errors == expected
    previous = read_errors(client)
    client.group_errors["1"]["type"] = "later"
    client.group_errors["2"]["code"] = "later"
    assert previous == expected
    client._request.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("code,expected", [
    ("0101_A-b", "0101_A-b"), ("http://fixture.invalid/private-secret", None),
    ("X" * 33, None), ("١", None),
])
async def test_poll_error_path_sanitises_code_before_caching_and_diagnostics_export(client, code, expected):
    root = ET.Element("Packet")
    ET.SubElement(root, "Command").text = "getErrorResponse"
    ET.SubElement(ET.SubElement(root, "DatabaseManager"), "ERROR", {
        "Code": code, "Message": "private-message", "Point": "private-point",
    })
    client._names = {"1": "private-room"}
    client.devices["1"] = MelansDevice("1", None, {"Drive": "OFF", "Mode": "COOL"})
    client._request = AsyncMock(return_value=ET.tostring(root, encoding="unicode"))
    before_raw = client.devices["1"].raw_attributes()
    with pytest.raises(MelansError):
        await client.async_update()
    assert client.group_errors == {"1": {"type": "MelansProtocolError", "code": expected}}
    exported = read_errors(client)
    assert exported == client.group_errors
    assert "private" not in json.dumps(exported)
    assert client.devices["1"].raw_attributes() == before_raw
    assert client.last_command is None and client._pending_writes == {}
    assert client._request.await_count == 2
