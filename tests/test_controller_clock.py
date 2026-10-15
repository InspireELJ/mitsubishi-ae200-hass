"""Offline contracts for raw, timezone-free controller clock evidence."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import FrozenInstanceError

import pytest
from pymelans.controller_clock import (
    MAX_YEAR,
    MIN_YEAR,
    READ_ATTRIBUTES,
    build_controller_clock_request,
    parse_controller_clock,
)
from pymelans.exceptions import MelansProtocolError

VALID = {"Year": "2024", "Month": "2", "Day": "29", "Hour": "9", "Minute": "5", "Second": "0"}


def packet(body, command="getResponse"):
    return f"<Packet><Command>{command}</Command><DatabaseManager>{body}</DatabaseManager></Packet>"


def clock_packet(**attributes):
    node = ET.Element("Clock", attributes)
    return packet(ET.tostring(node, encoding="unicode"))


def test_request_is_exactly_one_read_only_six_field_clock():
    root = ET.fromstring(build_controller_clock_request())
    assert root.tag == "Packet" and root.findtext("./Command") == "getRequest"
    manager = root.find("./DatabaseManager")
    assert len(manager) == 1 and manager[0].tag == "Clock" and len(manager[0]) == 0
    assert manager[0].attrib == dict.fromkeys(("Year", "Month", "Day", "Hour", "Minute", "Second"), "*")
    assert set(READ_ATTRIBUTES) == set(VALID)


def test_valid_gregorian_clock_is_frozen_without_offset_or_accuracy_claims():
    sample = parse_controller_clock(clock_packet(**VALID))
    assert (sample.year, sample.month, sample.day, sample.hour, sample.minute, sample.second) == (2024, 2, 29, 9, 5, 0)
    assert sample.local_datetime == "2024-02-29T09:05:00" and sample.issues == ()
    assert sample.raw_attributes() == VALID
    assert not hasattr(sample, "timezone") and not hasattr(sample, "utc_datetime")
    assert not hasattr(sample, "synced") and not hasattr(sample, "clock_drift")
    with pytest.raises(FrozenInstanceError):
        sample.year = 2025


def test_zero_padded_fields_are_accepted_but_raw_strings_are_preserved():
    attributes = {**VALID, "Month": "02", "Hour": "09", "Minute": "05", "Second": "00"}
    sample = parse_controller_clock(clock_packet(**attributes))
    assert sample.raw_attributes() == attributes
    assert sample.local_datetime == "2024-02-29T09:05:00"


def test_unrequested_sensitive_fields_are_discarded_and_raw_export_is_an_independent_copy():
    sample = parse_controller_clock(clock_packet(
        **VALID, Name="private-location", IPAddress="private-host", Password="secret",
        Timezone="private-zone", UTCOffset="+09:00", Synced="YES", Number="private-number",
    ))
    exported = sample.raw_attributes()
    exported["Year"] = "2000"
    exported["Password"] = "caller-secret"
    assert sample.raw_attributes() == VALID and sample.year == 2024
    assert all(secret not in repr(sample) for secret in ("private-", "secret"))


@pytest.mark.parametrize("key", READ_ATTRIBUTES)
@pytest.mark.parametrize("value", [None, "", "*", "FUTURE", "-1", "+1", "1.0", " 1", "1 ", "١"])
def test_unknown_or_lexically_invalid_fields_are_nullable_with_raw_and_safe_issues(key, value):
    attributes = {**VALID}
    if value is None:
        attributes.pop(key)
    else:
        attributes[key] = value
    sample = parse_controller_clock(clock_packet(**attributes))
    assert getattr(sample, key.lower()) is None and sample.local_datetime is None
    assert sample.raw_attributes() == attributes
    assert len(sample.issues) == 1 and sample.issues[0].key == key
    assert sample.issues[0].kind in ("missing_or_unknown_field", "invalid_field_format")
    assert value in (None, "") or value not in repr(sample.issues)


@pytest.mark.parametrize("attributes,local", [
    ({"Year": str(MIN_YEAR), "Month": "1", "Day": "1", "Hour": "0", "Minute": "0", "Second": "0"}, "1970-01-01T00:00:00"),
    ({"Year": str(MAX_YEAR), "Month": "12", "Day": "31", "Hour": "23", "Minute": "59", "Second": "59"}, "2037-12-31T23:59:59"),
    ({**VALID, "Year": "2000"}, "2000-02-29T09:05:00"),
])
def test_known_ui_calendar_and_time_boundaries(attributes, local):
    sample = parse_controller_clock(clock_packet(**attributes))
    assert sample.local_datetime == local and sample.issues == ()


@pytest.mark.parametrize("key,value", [
    ("Year", "1969"), ("Year", "2038"), ("Year", "0000"),
    ("Month", "0"), ("Month", "13"), ("Day", "0"), ("Day", "32"),
    ("Hour", "24"), ("Minute", "60"), ("Second", "60"),
])
def test_out_of_range_fields_do_not_roll_over_or_erase_raw_values(key, value):
    sample = parse_controller_clock(clock_packet(**{**VALID, key: value}))
    assert getattr(sample, key.lower()) is None and sample.local_datetime is None
    assert sample.raw_attributes()[key] == value
    assert [(issue.kind, issue.key) for issue in sample.issues] == [("out_of_range_field", key)]


@pytest.mark.parametrize("year,month,day", [("2023", "2", "29"), ("2024", "2", "30"), ("2024", "4", "31")])
def test_impossible_gregorian_days_are_unknown_instead_of_becoming_another_date(year, month, day):
    attributes = {**VALID, "Year": year, "Month": month, "Day": day}
    sample = parse_controller_clock(clock_packet(**attributes))
    assert sample.year == int(year) and sample.month == int(month) and sample.day is None
    assert sample.hour == 9 and sample.minute == 5 and sample.second == 0
    assert sample.local_datetime is None and sample.raw_attributes() == attributes
    assert [(issue.kind, issue.key) for issue in sample.issues] == [("invalid_calendar_date", "Day")]


def test_missing_clock_fields_do_not_invent_a_default_date_or_time():
    sample = parse_controller_clock(clock_packet())
    assert sample.year is sample.month is sample.day is sample.hour is sample.minute is sample.second is None
    assert sample.local_datetime is None and sample.raw_attributes() == {}
    assert {issue.key for issue in sample.issues} == set(READ_ATTRIBUTES)


@pytest.mark.parametrize("text", [" ", "\t", "\r", "\n", " \t\r\n"])
def test_xml_whitespace_is_allowed_around_the_leaf_record(text):
    node = ET.Element("Clock", VALID)
    node.text = text
    body = ET.tostring(node, encoding="unicode")
    payload = (
        f"<Packet>{text}<Command>getResponse</Command>{text}<DatabaseManager>{text}"
        f"{body}{text}</DatabaseManager>{text}</Packet>"
    )
    assert parse_controller_clock(payload).local_datetime == "2024-02-29T09:05:00"


@pytest.mark.parametrize("text", ["junk", "\u00a0", "\u2003"])
@pytest.mark.parametrize("location", ["packet-text", "command-tail", "database-text", "clock-text", "clock-tail", "database-tail"])
def test_non_xml_whitespace_cannot_hide_in_mixed_content(text, location):
    slots = dict.fromkeys(("packet-text", "command-tail", "database-text", "clock-text", "clock-tail", "database-tail"), "")
    slots[location] = text
    payload = (
        f'<Packet>{slots["packet-text"]}<Command>getResponse</Command>{slots["command-tail"]}'
        f'<DatabaseManager>{slots["database-text"]}<Clock>{slots["clock-text"]}</Clock>'
        f'{slots["clock-tail"]}</DatabaseManager>{slots["database-tail"]}</Packet>'
    )
    with pytest.raises(MelansProtocolError):
        parse_controller_clock(payload)


@pytest.mark.parametrize("body", [
    "", "<Other />", "<Nested><Clock /></Nested>", "<Clock /><Clock />", "<Clock /><Other />",
    "<Clock><Other /></Clock>", "<Clock><Clock /></Clock>",
])
def test_clock_requires_exactly_one_direct_leaf_record(body):
    with pytest.raises(MelansProtocolError):
        parse_controller_clock(packet(body))


@pytest.mark.parametrize("payload", [
    "not XML",
    packet('<ERROR Code="0101" Message="private-message" /><Clock />'),
    packet("<Clock />", command="getErrorResponse"),
    packet("<Clock />", command="notifyRequest"),
    packet("<Clock />", command="setResponse"),
    '<!DOCTYPE Packet [<!ENTITY year "2024">]>' + packet('<Clock Year="&year;" />'),
    '<Packet><Command>getResponse</Command><Command>getResponse</Command><DatabaseManager><Clock /></DatabaseManager></Packet>',
    '<Packet><Command>getResponse</Command><DatabaseManager><Clock /></DatabaseManager><DatabaseManager /></Packet>',
    '<Packet><Command>getResponse<Other /></Command><DatabaseManager><Clock /></DatabaseManager></Packet>',
    '<Packet><Command>getResponse</Command><DatabaseManager><Clock /></DatabaseManager><Other /></Packet>',
    packet("<A>" * 20 + "</A>" * 20),
    packet("<A />" * 2100),
    clock_packet(**{**VALID, "Year": "x" * 513}),
    " " * (1024 * 1024 + 1),
    packet('<Clock Year="\ud800" />'),
], ids=[
    "malformed", "error-with-partial-clock", "error-command", "notification", "wrong-command",
    "dtd", "duplicate-command", "duplicate-database", "nested-command", "stray-root-record",
    "deep-tree", "many-nodes", "long-attribute", "oversized", "invalid-text",
])
def test_clock_uses_shared_response_guards_and_strict_envelope(payload):
    with pytest.raises(MelansProtocolError):
        parse_controller_clock(payload)
