"""Synthetic raw-only ROOM_TEMP evidence, strict selection and XML boundaries."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import FrozenInstanceError

import pytest
from pymelans.exceptions import MelansProtocolError
from pymelans.member_temperature_history import (
    DATA_FIELD,
    DATA_NAME,
    _selector,
    build_member_temperature_history_request,
    parse_member_temperature_history,
)

SELECTOR = {"Address": "4", "Year": "2024", "Month": "2", "Day": "29", "DataName": "ROOM_TEMP"}
TOKENS = tuple(f"{20 + index % 3}.{index % 10}" for index in range(48))
SERIES = ",".join(TOKENS)


def packet(*, selector=None, raw=SERIES, records=1, record_attributes=None):
    root = ET.Element("Packet")
    ET.SubElement(root, "Command").text = "getResponse"
    energy = ET.SubElement(ET.SubElement(root, "DatabaseManager"), "EnergyManagement")
    directory = ET.SubElement(energy, "IcTrendDList", SELECTOR if selector is None else selector)
    attributes = {} if raw is None else {DATA_FIELD: raw}
    attributes.update(record_attributes or {})
    for _ in range(records):
        ET.SubElement(directory, "IcTrendDRecord", attributes)
    return ET.tostring(root, encoding="unicode")


def parse(payload, **expected):
    return parse_member_temperature_history(payload, **{
        "expected_address": "4", "expected_date": "2024-02-29", **expected,
    })


@pytest.mark.parametrize("address", [1, "004", 250])
def test_request_is_one_fixed_room_channel_without_wildcards_or_unit_selector(address):
    xml = build_member_temperature_history_request(address, "2024-02-29")
    root = ET.fromstring(xml)
    assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert root.tag == "Packet" and root.findtext("./Command") == "getRequest"
    manager = root.find("./DatabaseManager")
    assert len(manager) == 1 and manager[0].tag == "EnergyManagement"
    assert len(manager[0]) == 1 and manager[0][0].tag == "IcTrendDList"
    node = manager[0][0]
    assert len(node) == 0
    assert node.attrib == {**SELECTOR, "Address": str(int(address))}
    assert _selector(address, "2024-02-29") == node.attrib


@pytest.mark.parametrize("address", [0, 251, True, None, "*", "4,5", "-4", "+4", " 4", "4.0", "٤", "0004"])
def test_address_requires_same_bounded_ascii_contract_as_runtime(address):
    with pytest.raises(ValueError):
        build_member_temperature_history_request(address, "2024-02-29")
    with pytest.raises(ValueError):
        parse(packet(), expected_address=address)


@pytest.mark.parametrize("day", [
    None, "", "20240229", "2024-2-29", "２０２４-02-29", "2024-02-29 ",
    "2024-02-29T00:00:00", "1969-12-31", "2038-01-01", "0000-01-01",
    "2023-02-29", "2024-04-31", "2024-00-10", "2024-13-01", "2024-01-00",
])
def test_date_requires_strict_gregorian_iso_inside_local_pilot_window(day):
    with pytest.raises(ValueError):
        build_member_temperature_history_request("4", day)
    with pytest.raises(ValueError):
        parse(packet(), expected_date=day)


@pytest.mark.parametrize("day,parts", [
    ("1970-01-01", ("1970", "1", "1")),
    ("2037-12-31", ("2037", "12", "31")),
    ("2000-02-29", ("2000", "2", "29")),
])
def test_valid_date_boundaries_have_canonical_wire_month_day(day, parts):
    node = ET.fromstring(build_member_temperature_history_request("4", day)).find(
        "./DatabaseManager/EnergyManagement/IcTrendDList",
    )
    assert (node.get("Year"), node.get("Month"), node.get("Day")) == parts


def test_exact_48_raw_tokens_receive_only_naive_graph_position_labels():
    sample = parse(packet())
    assert sample.address == "4" and sample.date == "2024-02-29"
    assert sample.record_count == 1 and sample.raw_data == SERIES
    assert sample.raw_tokens == TOKENS and sample.raw_attributes() == {DATA_FIELD: SERIES}
    assert sample.issues == () and len(sample.slots) == 48
    assert [(slot.index, slot.start_time, slot.end_time, slot.raw_token) for slot in sample.slots[:2]] == [
        (0, "00:00", "00:30", TOKENS[0]), (1, "00:30", "01:00", TOKENS[1]),
    ]
    assert sample.slots[-1].start_time == "23:30" and sample.slots[-1].end_time == "24:00"
    assert all(slot.raw_token == TOKENS[slot.index] for slot in sample.slots)
    assert set(vars(sample.slots[0])) == {"index", "start_time", "end_time", "raw_token"}
    assert not any(hasattr(sample, key) for key in (
        "unit", "temperature", "celsius", "mean", "total", "timezone", "aggregation", "hvac_action",
    ))


def test_raw_only_projection_does_not_parse_missing_or_numeric_looking_tokens():
    tokens = ("", "*", "@m", "-40", "0", "1e99", "NaN", "+Infinity", " 20 ", "secret") + ("0",) * 38
    sample = parse(packet(raw=",".join(tokens)))
    assert sample.raw_tokens == tokens
    assert tuple(slot.raw_token for slot in sample.slots) == tokens
    assert sample.issues == ()
    assert "secret" not in repr(sample) and "secret" not in repr(sample.slots)


@pytest.mark.parametrize("raw", ["", "*", "20", ",".join(TOKENS[:-1]), SERIES + ",20", SERIES + ","])
def test_non48_shapes_never_pad_drop_or_truncate_tokens(raw):
    sample = parse(packet(raw=raw))
    assert sample.raw_data == raw and sample.raw_tokens == tuple(raw.split(","))
    assert sample.slots == ()
    assert [(issue.key, issue.kind) for issue in sample.issues] == [(DATA_FIELD, "unexpected_token_count")]


@pytest.mark.parametrize("records,key,kind", [(0, "record", "missing_record"), (1, DATA_FIELD, "missing_data")])
def test_missing_record_or_selected_field_never_borrows_other_series(records, key, kind):
    sample = parse(packet(raw=None, records=records, record_attributes={
        "FanTimeData": "private-fan", "ThermoTimeData": "private-thermo",
        "CoolSetTempData": "private-setpoint",
    }))
    assert sample.record_count == records and sample.raw_data is None
    assert sample.raw_tokens == sample.slots == () and sample.raw_attributes() == {}
    assert [(issue.key, issue.kind) for issue in sample.issues] == [(key, kind)]


def test_frozen_outputs_and_raw_attribute_copies_keep_only_room_series():
    sample = parse(packet(record_attributes={"FanTimeData": "private-other", "Room": "private-room"}))
    exported = sample.raw_attributes()
    exported[DATA_FIELD] = "caller"
    assert sample.raw_attributes() == {DATA_FIELD: SERIES}
    assert "private" not in repr(sample)
    with pytest.raises(FrozenInstanceError):
        sample.raw_data = "caller"
    with pytest.raises(FrozenInstanceError):
        sample.slots[0].raw_token = "caller"
    with pytest.raises(TypeError):
        sample.raw_tokens[0] = "caller"
    issue = parse(packet(records=0)).issues[0]
    with pytest.raises(FrozenInstanceError):
        issue.kind = "caller"


@pytest.mark.parametrize("key,bad", [
    ("Address", "04"), ("Address", "5"), ("Year", "2023"), ("Month", "02"), ("Day", "029"),
    ("DataName", "FAN_TIME"), ("DataName", "THERMO_TIME"), ("DataName", "COOL_SET_TEMP"),
    ("DataName", "HEAT_SET_TEMP"), ("DataName", "ROOM_TEMP "),
])
def test_room_history_cannot_accept_another_channel_or_selector_alias(key, bad):
    with pytest.raises(MelansProtocolError):
        parse(packet(selector={**SELECTOR, key: bad}))


@pytest.mark.parametrize("key", SELECTOR)
def test_each_selector_attribute_is_required(key):
    selector = dict(SELECTOR)
    selector.pop(key)
    with pytest.raises(MelansProtocolError):
        parse(packet(selector=selector))


@pytest.mark.parametrize("tag,key,value", [
    (tag, key, value)
    for tag in ("Packet", "Command", "DatabaseManager", "EnergyManagement", "IcTrendDList", "IcTrendDRecord")
    for key, value in (("Group", ""), ("MultiGroup", "*"), ("Address", "4"))
    if (tag, key) != ("IcTrendDList", "Address")
])
def test_identity_on_wrong_original_node_is_rejected_before_field_filtering(tag, key, value):
    root = ET.fromstring(packet())
    node = root if tag == "Packet" else root.find(f".//{tag}")
    node.set(key, value)
    with pytest.raises(MelansProtocolError):
        parse(ET.tostring(root, encoding="unicode"))


@pytest.mark.parametrize("mutation", [
    "wrong-root", "wrong-command", "duplicate-command", "nested-command", "duplicate-manager", "extra-root",
    "wrong-energy", "extra-energy", "extra-list", "wrong-list", "wrong-record", "nested-record",
    "two-records", "error-command", "partial-error",
])
def test_strict_original_envelope_does_not_sanitise_errors_into_a_room_series(mutation):
    root = ET.fromstring(packet())
    manager = root.find("./DatabaseManager")
    energy, directory = manager[0], manager[0][0]
    if mutation == "wrong-root":
        root.tag = "Other"
    elif mutation == "wrong-command":
        root[0].text = "setResponse"
    elif mutation == "duplicate-command":
        ET.SubElement(root, "Command").text = "getResponse"
    elif mutation == "nested-command":
        ET.SubElement(root[0], "Other")
    elif mutation == "duplicate-manager":
        ET.SubElement(root, "DatabaseManager")
    elif mutation == "extra-root":
        ET.SubElement(root, "Other")
    elif mutation == "wrong-energy":
        energy.tag = "Other"
    elif mutation == "extra-energy":
        ET.SubElement(manager, "EnergyManagement")
    elif mutation == "extra-list":
        ET.SubElement(energy, "IcTrendDList", SELECTOR)
    elif mutation == "wrong-list":
        directory.tag = "IcTrendMList"
    elif mutation == "wrong-record":
        directory[0].tag = "IcTrendMRecord"
    elif mutation == "nested-record":
        ET.SubElement(directory[0], "IcTrendDRecord")
    elif mutation == "two-records":
        ET.SubElement(directory, "IcTrendDRecord", {DATA_FIELD: SERIES})
    elif mutation == "error-command":
        root[0].text = "getErrorResponse"
    elif mutation == "partial-error":
        ET.SubElement(manager, "ERROR", {"Code": "0101", "Message": "private"})
    with pytest.raises(MelansProtocolError):
        parse(ET.tostring(root, encoding="unicode"))


@pytest.mark.parametrize("tag", ["Packet", "DatabaseManager", "EnergyManagement", "IcTrendDList", "IcTrendDRecord"])
@pytest.mark.parametrize("position", ["text", "tail"])
@pytest.mark.parametrize("text", ["junk", "\u00a0"])
def test_mixed_text_cannot_be_removed_before_validating_original_envelope(tag, position, text):
    root = ET.fromstring(packet())
    node = root if tag == "Packet" else root.find(f".//{tag}")
    setattr(node, position, text)
    with pytest.raises(MelansProtocolError):
        parse(ET.tostring(root, encoding="unicode"))


def test_xml_whitespace_is_allowed_without_trimming_raw_room_tokens():
    root = ET.fromstring(packet(raw=",".join([" 20 "] * 48)))
    for node in root.iter():
        if node.tag != "Command":
            node.text = " \t\r\n"
        node.tail = " \t\r\n"
    sample = parse(ET.tostring(root, encoding="unicode"))
    assert sample.raw_tokens == (" 20 ",) * 48 and len(sample.slots) == 48


@pytest.mark.parametrize("payload", [
    "not XML", '<!DOCTYPE Packet [<!ENTITY x "data">]>' + packet(),
    '<!ENTITY x "data">' + packet(), packet() + " " * (1024 * 1024),
    packet(raw="0" * 513), packet(record_attributes={"Private": "x" * 513}),
    packet(record_attributes={f"Attr{i}": "x" for i in range(65)}),
], ids=["bad-xml", "dtd-entity", "entity", "oversize", "long-data", "long-extra", "many-attributes"])
def test_shared_xml_size_dtd_and_attribute_limits_protect_new_raw_history(payload):
    with pytest.raises(MelansProtocolError):
        parse(payload)


def test_shared_depth_limit_is_checked_before_room_field_extraction():
    root = ET.fromstring(packet())
    parent = root.find("./DatabaseManager/EnergyManagement/IcTrendDList/IcTrendDRecord")
    for _ in range(20):
        parent = ET.SubElement(parent, "Other")
    with pytest.raises(MelansProtocolError, match="structural limits"):
        parse(ET.tostring(root, encoding="unicode"))


def test_public_api_is_fixed_room_temp_and_does_not_accept_a_data_name_argument():
    assert DATA_NAME == "ROOM_TEMP"
    with pytest.raises(TypeError):
        build_member_temperature_history_request("4", "2024-02-29", "FAN_TIME")
    with pytest.raises(TypeError):
        parse_member_temperature_history(
            packet(), expected_address="4", expected_date="2024-02-29", expected_data_name="FAN_TIME",
        )
