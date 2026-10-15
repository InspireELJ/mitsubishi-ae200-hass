"""Synthetic raw daylight settings preserve distinctions and strict wire scope."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import FrozenInstanceError

import pytest
from pymelans.controller_daylight_settings import (
    READ_ATTRIBUTES,
    ControllerDaylightSettings,
    DaylightSettingsIssue,
    build_controller_daylight_settings_request,
    parse_controller_daylight_settings,
)
from pymelans.exceptions import MelansProtocolError

FIELDS = (
    "CountryCode", "Month1", "Day1", "Hour1", "Minute1", "ShiftMin1",
    "Month2", "Day2", "Hour2", "Minute2", "ShiftMin2",
)
# Deliberately synthetic; no copied site, actual region, transition date or rules.
RAW = dict(zip(FIELDS, ("000", "3", "15", "1", "05", "30", "9", "21", "3", "45", "-30")))


def packet(attributes=None):
    root = ET.Element("Packet")
    ET.SubElement(root, "Command").text = "getResponse"
    ET.SubElement(ET.SubElement(root, "DatabaseManager"), "SummerTime", RAW if attributes is None else attributes)
    return ET.tostring(root, encoding="unicode")


def test_builder_is_one_exact_eleven_attribute_read_without_selectors_or_control():
    payload = build_controller_daylight_settings_request()
    assert payload.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    root = ET.fromstring(payload)
    assert root.tag == "Packet" and len(root) == 2 and root.findtext("./Command") == "getRequest"
    manager = root.find("./DatabaseManager")
    assert len(manager) == 1 and manager[0].tag == "SummerTime" and len(manager[0]) == 0
    assert manager[0].attrib == dict.fromkeys(FIELDS, "*")
    assert READ_ATTRIBUTES == FIELDS
    with pytest.raises(TypeError):
        build_controller_daylight_settings_request("1")


def test_sample_and_nested_data_are_frozen_raw_only_without_time_basis_claims():
    sample = parse_controller_daylight_settings(packet())
    assert type(sample) is ControllerDaylightSettings and sample.raw_attributes() == RAW
    assert sample.issues == ()
    assert sample.raw_attributes()["CountryCode"] == "000"
    assert sample.raw_attributes()["Minute1"] == "05" and sample.raw_attributes()["ShiftMin2"] == "-30"
    assert all(isinstance(value, str) for value in sample.raw_attributes().values())
    assert not any(hasattr(sample, key) for key in (
        "enabled", "daylight_enabled", "currently_in_dst", "offset", "utc_offset", "timezone",
        "country", "local_datetime", "utc_datetime", "clock_drift", "next_transition", "year",
    ))
    with pytest.raises(FrozenInstanceError):
        sample.issues = ()
    with pytest.raises(TypeError):
        sample._attributes[0] = ("CountryCode", "caller")


def test_raw_attribute_export_is_detached_and_extra_private_or_unit_fields_are_discarded():
    sample = parse_controller_daylight_settings(packet({
        **RAW, "Name": "private-room", "IPAddress": "private-host", "Password": "secret",
        "Enabled": "ON", "Timezone": "private-zone", "UTCOffset": "+01:00", "ClockBase": "UTC",
    }))
    exported = sample.raw_attributes()
    exported["CountryCode"] = "caller"
    exported["Password"] = "caller-secret"
    assert sample.raw_attributes() == RAW
    assert all(secret not in repr(sample) for secret in ("private", "secret", "caller"))


@pytest.mark.parametrize("key", FIELDS)
@pytest.mark.parametrize("token", ["", "*", "-60", "+60", "00", "1.5", "FUTURE", " 7 ", "١", "secret"])
def test_present_values_remain_exact_strings_without_numeric_or_enable_interpretation(key, token):
    attributes = {**RAW, key: token}
    sample = parse_controller_daylight_settings(packet(attributes))
    assert sample.raw_attributes() == attributes and sample.issues == ()
    assert all(token in ("", "*") or token not in repr(issue) for issue in sample.issues)


@pytest.mark.parametrize("key", FIELDS)
def test_missing_attribute_is_omitted_and_differs_from_explicit_empty_or_wildcard(key):
    attributes = dict(RAW)
    attributes.pop(key)
    sample = parse_controller_daylight_settings(packet(attributes))
    assert key not in sample.raw_attributes() and sample.raw_attributes() == attributes
    assert sample.issues == (DaylightSettingsIssue("missing_attribute", key),)
    with pytest.raises(FrozenInstanceError):
        sample.issues[0].kind = "caller"
    assert parse_controller_daylight_settings(packet({**RAW, key: ""})).raw_attributes()[key] == ""
    assert parse_controller_daylight_settings(packet({**RAW, key: "*"})).raw_attributes()[key] == "*"


def test_all_eleven_present_blank_attributes_are_a_raw_sample_without_disabled_inference():
    sample = parse_controller_daylight_settings(packet(dict.fromkeys(FIELDS, "")))
    assert sample.raw_attributes() == dict.fromkeys(FIELDS, "") and sample.issues == ()
    assert not hasattr(sample, "enabled")


def test_one_recognised_blank_field_is_partial_evidence_with_missing_issues_in_field_order():
    sample = parse_controller_daylight_settings(packet({"CountryCode": "", "Private": "secret"}))
    assert sample.raw_attributes() == {"CountryCode": ""}
    assert sample.issues == tuple(DaylightSettingsIssue("missing_attribute", key) for key in FIELDS[1:])
    assert "secret" not in repr(sample)


@pytest.mark.parametrize("attributes", [{}, {"Private": "secret"}, {"Country": "000"}, {"Enabled": "OFF"}])
def test_no_recognised_attributes_or_aliases_cannot_become_an_empty_success(attributes):
    with pytest.raises(MelansProtocolError, match="no recognised settings"):
        parse_controller_daylight_settings(packet(attributes))


@pytest.mark.parametrize("mutation", [
    "wrong-root", "wrong-command", "duplicate-command", "nested-command", "duplicate-manager", "extra-root",
    "missing-record", "wrong-record", "nested-record", "duplicate-record", "extra-record", "child-record",
    "error-command", "partial-error", "record-error",
])
def test_original_envelope_and_leaf_shape_are_verified_before_retaining_fields(mutation):
    root = ET.fromstring(packet())
    manager, command = root.find("./DatabaseManager"), root.find("./Command")
    record = manager[0]
    if mutation == "wrong-root":
        root.tag = "Other"
    elif mutation == "wrong-command":
        command.text = "setResponse"
    elif mutation == "duplicate-command":
        ET.SubElement(root, "Command").text = "getResponse"
    elif mutation == "nested-command":
        ET.SubElement(command, "Other")
    elif mutation == "duplicate-manager":
        ET.SubElement(root, "DatabaseManager")
    elif mutation == "extra-root":
        ET.SubElement(root, "Other")
    elif mutation == "missing-record":
        manager.remove(record)
    elif mutation == "wrong-record":
        record.tag = "Clock"
    elif mutation == "nested-record":
        manager.remove(record)
        ET.SubElement(manager, "Wrapper").append(record)
    elif mutation == "duplicate-record":
        ET.SubElement(manager, "SummerTime", RAW)
    elif mutation == "extra-record":
        ET.SubElement(manager, "Clock", {"Year": "2024"})
    elif mutation == "child-record":
        ET.SubElement(record, "Other")
    elif mutation == "error-command":
        command.text = "getErrorResponse"
    elif mutation == "partial-error":
        ET.SubElement(manager, "ERROR", {"Code": "0101", "Message": "private"})
    elif mutation == "record-error":
        ET.SubElement(record, "ERROR", {"Code": "0101", "Message": "private"})
    with pytest.raises(MelansProtocolError):
        parse_controller_daylight_settings(ET.tostring(root, encoding="unicode"))


@pytest.mark.parametrize("tag", ["Packet", "Command", "DatabaseManager", "SummerTime"])
@pytest.mark.parametrize("key", ["Group", "Address", "MultiGroup"])
@pytest.mark.parametrize("token", ["", "1", "*"])
def test_member_or_aggregate_identity_is_rejected_on_every_original_node(tag, key, token):
    root = ET.fromstring(packet())
    node = root if tag == "Packet" else root.find(f".//{tag}")
    node.set(key, token)
    with pytest.raises(MelansProtocolError):
        parse_controller_daylight_settings(ET.tostring(root, encoding="unicode"))


@pytest.mark.parametrize("tag", ["Packet", "Command", "DatabaseManager"])
@pytest.mark.parametrize("key", FIELDS)
def test_recognised_fields_cannot_be_relocated_or_duplicated_outside_the_leaf(tag, key):
    root = ET.fromstring(packet())
    node = root if tag == "Packet" else root.find(f".//{tag}")
    node.set(key, RAW[key])
    with pytest.raises(MelansProtocolError):
        parse_controller_daylight_settings(ET.tostring(root, encoding="unicode"))


@pytest.mark.parametrize("tag", ["Packet", "DatabaseManager", "SummerTime"])
@pytest.mark.parametrize("position", ["text", "tail"])
@pytest.mark.parametrize("text", ["junk", "\u00a0", "\u2003"])
def test_only_xml_whitespace_can_surround_the_original_leaf(tag, position, text):
    root = ET.fromstring(packet())
    node = root if tag == "Packet" else root.find(f".//{tag}")
    setattr(node, position, text)
    with pytest.raises(MelansProtocolError):
        parse_controller_daylight_settings(ET.tostring(root, encoding="unicode"))


def test_xml_whitespace_is_accepted_and_command_tail_is_checked():
    root = ET.fromstring(packet())
    for node in root.iter():
        if node.tag != "Command":
            node.text = " \t\r\n"
        node.tail = " \t\r\n"
    assert parse_controller_daylight_settings(ET.tostring(root, encoding="unicode")).raw_attributes() == RAW
    root.find("./Command").tail = "junk"
    with pytest.raises(MelansProtocolError):
        parse_controller_daylight_settings(ET.tostring(root, encoding="unicode"))


@pytest.mark.parametrize("payload", [
    "not XML", '<!DOCTYPE Packet [<!ENTITY x "000">]>' + packet(), '<!ENTITY x "000">' + packet(),
    packet() + " " * (1024 * 1024), packet({**RAW, "CountryCode": "x" * 513}),
    packet({**RAW, "Private": "x" * 513}), packet({**RAW, **{f"Attr{i}": "x" for i in range(65)}}),
    packet({**RAW, "CountryCode": "\ud800"}),
], ids=["bad-xml", "dtd", "entity", "oversize", "long-raw", "long-extra", "many-attributes", "invalid-text"])
def test_shared_response_safety_limits_are_checked_before_field_filtering(payload):
    with pytest.raises(MelansProtocolError):
        parse_controller_daylight_settings(payload)


def test_shared_size_limit_counts_utf8_bytes_and_protects_against_deep_or_large_trees():
    byte_oversize = '<Packet>' + '<!--' + "\u4e00" * 350_000 + '-->' + packet()[8:]
    with pytest.raises(MelansProtocolError, match="oversized response"):
        parse_controller_daylight_settings(byte_oversize)
    for depth, breadth in ((20, 0), (0, 2100)):
        root = ET.fromstring(packet())
        parent = root.find("./DatabaseManager/SummerTime")
        for _ in range(depth):
            parent = ET.SubElement(parent, "Other")
        for _ in range(breadth):
            ET.SubElement(parent, "Other")
        with pytest.raises(MelansProtocolError, match="structural limits"):
            parse_controller_daylight_settings(ET.tostring(root, encoding="unicode"))
