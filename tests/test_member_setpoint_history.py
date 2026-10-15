"""Synthetic raw setpoint histories keep complete channel identity and blanks."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import FrozenInstanceError

import pytest
from pymelans.daily_trend import RawDailyTrendIssue, RawDailyTrendSlot
from pymelans.exceptions import MelansProtocolError
from pymelans.member_setpoint_history import (
    DATA_FIELDS,
    _selector,
    build_member_setpoint_history_request,
    parse_member_setpoint_history,
)

SELECTOR = {"Address": "4", "Year": "2024", "Month": "2", "Day": "29", "DataName": "COOL_SET_TEMP"}
TOKENS = tuple(f"{20 + index % 3}.{index % 10}" for index in range(48))
SERIES = ",".join(TOKENS)


def packet(*, data_name="COOL_SET_TEMP", selector=None, raw=SERIES, records=1, attributes=None):
    root = ET.Element("Packet")
    ET.SubElement(root, "Command").text = "getResponse"
    energy = ET.SubElement(ET.SubElement(root, "DatabaseManager"), "EnergyManagement")
    directory = ET.SubElement(energy, "IcTrendDList", {**SELECTOR, "DataName": data_name} if selector is None else selector)
    selected = {} if raw is None else {DATA_FIELDS[data_name]: raw}
    selected.update(attributes or {})
    for _ in range(records):
        ET.SubElement(directory, "IcTrendDRecord", selected)
    return ET.tostring(root, encoding="unicode")


def parse(payload, **expected):
    return parse_member_setpoint_history(payload, **{
        "expected_address": "4", "expected_date": "2024-02-29",
        "expected_data_name": "COOL_SET_TEMP", **expected,
    })


@pytest.mark.parametrize("address", [1, "004", 250])
@pytest.mark.parametrize("data_name", DATA_FIELDS)
def test_request_has_one_complete_fixed_channel_selector_and_no_controls(address, data_name):
    xml = build_member_setpoint_history_request(address, "2024-02-29", data_name)
    root = ET.fromstring(xml)
    assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert root.tag == "Packet" and root.findtext("./Command") == "getRequest"
    assert len(root) == 2
    manager = root.find("./DatabaseManager")
    assert len(manager) == 1 and manager[0].tag == "EnergyManagement"
    assert len(manager[0]) == 1 and manager[0][0].tag == "IcTrendDList"
    selected = {**SELECTOR, "Address": str(int(address)), "DataName": data_name}
    assert len(manager[0][0]) == 0 and manager[0][0].attrib == selected
    assert _selector(address, "2024-02-29", data_name) == selected


@pytest.mark.parametrize("address", [0, 251, True, None, "*", "4,5", "-4", "+4", " 4", "4.0", "٤", "0004"])
def test_only_bounded_explicit_member_identifiers_are_accepted(address):
    with pytest.raises(ValueError):
        build_member_setpoint_history_request(address, "2024-02-29", "COOL_SET_TEMP")
    with pytest.raises(ValueError):
        parse(packet(), expected_address=address)


@pytest.mark.parametrize("day", [
    None, "", "20240229", "2024-2-29", "２０２４-02-29", "2024-02-29 ",
    "2024-02-29T00:00:00", "1969-12-31", "2038-01-01", "0000-01-01",
    "2023-02-29", "2024-04-31", "2024-00-10", "2024-13-01", "2024-01-00",
])
def test_strict_calendar_and_local_pilot_window_are_shared_with_other_daily_reads(day):
    with pytest.raises(ValueError):
        build_member_setpoint_history_request("4", day, "COOL_SET_TEMP")
    with pytest.raises(ValueError):
        parse(packet(), expected_date=day)


@pytest.mark.parametrize("day,parts", [
    ("1970-01-01", ("1970", "1", "1")), ("2037-12-31", ("2037", "12", "31")),
    ("2000-02-29", ("2000", "2", "29")),
])
def test_canonical_wire_calendar_parts_at_boundaries(day, parts):
    node = ET.fromstring(build_member_setpoint_history_request("4", day, "HEAT_SET_TEMP")).find(
        "./DatabaseManager/EnergyManagement/IcTrendDList",
    )
    assert (node.get("Year"), node.get("Month"), node.get("Day")) == parts


@pytest.mark.parametrize("data_name", [
    None, "", True, "COOL", "HEAT", "cool", "heat", "AUTO", "AUTO_SET_TEMP", "ROOM_TEMP",
    "FAN_TIME", "THERMO_TIME", "CoolSetTempData", "HeatSetTempData", "COOL_SET_TEMP ",
    "cool_set_temp", "*", "COOL_SET_TEMP,HEAT_SET_TEMP", ["COOL_SET_TEMP"], {"COOL_SET_TEMP": True},
])
def test_only_two_complete_exact_string_data_names_are_accepted(data_name):
    with pytest.raises(ValueError, match="data_name must be COOL_SET_TEMP or HEAT_SET_TEMP"):
        build_member_setpoint_history_request("4", "2024-02-29", data_name)
    with pytest.raises(ValueError, match="data_name must be COOL_SET_TEMP or HEAT_SET_TEMP"):
        parse(packet(), expected_data_name=data_name)


@pytest.mark.parametrize("data_name", DATA_FIELDS)
def test_complete_series_is_raw_only_with_48_naive_graph_labels(data_name):
    sample = parse(packet(data_name=data_name), expected_data_name=data_name)
    assert sample.address == "4" and sample.date == "2024-02-29" and sample.data_name == data_name
    assert sample.record_count == 1 and sample.raw_data == SERIES and sample.raw_tokens == TOKENS
    assert sample.raw_attributes() == {DATA_FIELDS[data_name]: SERIES}
    assert sample.issues == () and len(sample.slots) == 48
    assert all(type(slot) is RawDailyTrendSlot and slot.raw_token == TOKENS[slot.index] for slot in sample.slots)
    assert sample.slots[0] == RawDailyTrendSlot(0, "00:00", "00:30", TOKENS[0])
    assert sample.slots[-1] == RawDailyTrendSlot(47, "23:30", "24:00", TOKENS[-1])
    assert set(vars(sample.slots[0])) == {"index", "start_time", "end_time", "raw_token"}
    assert not any(hasattr(sample, name) for name in (
        "unit", "celsius", "value", "current_setpoint", "mode", "mean", "total", "timezone", "aggregation", "supported",
    ))


@pytest.mark.parametrize("data_name", DATA_FIELDS)
@pytest.mark.parametrize("token", ["", "0", "0.0", "*", "@m"])
def test_48_blank_zero_or_unknown_tokens_are_preserved_without_support_or_numeric_claim(data_name, token):
    raw = ",".join([token] * 48)
    sample = parse(packet(data_name=data_name, raw=raw), expected_data_name=data_name)
    assert sample.raw_data == raw and sample.raw_tokens == (token,) * 48
    assert len(sample.slots) == 48 and all(slot.raw_token == token for slot in sample.slots)
    assert sample.issues == ()


def test_mixed_tokens_are_not_trimmed_coerced_dropped_or_classified_as_temperatures():
    tokens = ("", "0", "-40", "NaN", "Infinity", "1e4", "20junk", " 20 ", "secret") + TOKENS[:39]
    sample = parse(packet(raw=",".join(tokens)))
    assert sample.raw_tokens == tuple(slot.raw_token for slot in sample.slots) == tokens
    assert sample.issues == () and len(sample.slots) == 48
    assert "secret" not in repr(sample) and "secret" not in repr(sample.slots)


@pytest.mark.parametrize("data_name", DATA_FIELDS)
@pytest.mark.parametrize("records,raw,key,kind", [
    (0, None, "record", "missing_record"), (1, None, "field", "missing_data"),
    (1, "", "field", "unexpected_token_count"), (1, "0", "field", "unexpected_token_count"),
    (1, "," * 46, "field", "unexpected_token_count"), (1, "," * 48, "field", "unexpected_token_count"),
    (1, SERIES + ",", "field", "unexpected_token_count"),
])
def test_absence_empty_attribute_and_wrong_counts_are_distinct_without_padding(data_name, records, raw, key, kind):
    sample = parse(packet(data_name=data_name, raw=raw, records=records), expected_data_name=data_name)
    assert sample.record_count == records and sample.raw_data == raw and sample.slots == ()
    assert sample.raw_tokens == (tuple(raw.split(",")) if raw is not None else ())
    assert sample.raw_attributes() == ({DATA_FIELDS[data_name]: raw} if raw is not None else {})
    assert sample.issues == (RawDailyTrendIssue(DATA_FIELDS[data_name] if key == "field" else key, kind),)


def test_selected_field_is_never_borrowed_from_another_channel():
    sample = parse(packet(raw=None, attributes={"HeatSetTempData": SERIES, "RoomTempData": SERIES, "FanTimeData": SERIES}))
    assert sample.raw_data is None and sample.raw_attributes() == {} and sample.raw_tokens == sample.slots == ()
    assert sample.issues == (RawDailyTrendIssue("CoolSetTempData", "missing_data"),)


def test_frozen_raw_data_and_detached_export_discard_unrequested_private_fields():
    sample = parse(packet(attributes={"RoomTempData": "private-room-series", "Name": "private-room", "HeatSetTempData": "private-other"}))
    exported = sample.raw_attributes()
    exported["CoolSetTempData"] = "caller"
    assert sample.raw_attributes() == {"CoolSetTempData": SERIES}
    assert "private" not in repr(sample)
    with pytest.raises(FrozenInstanceError):
        sample.raw_data = "caller"
    with pytest.raises(FrozenInstanceError):
        sample.slots[0].raw_token = "caller"
    with pytest.raises(TypeError):
        sample.raw_tokens[0] = "caller"
    issue = parse(packet(records=0)).issues[0]
    with pytest.raises(FrozenInstanceError):
        issue.key = "caller"


@pytest.mark.parametrize("key,bad", [
    ("Address", "04"), ("Address", "5"), ("Year", "2023"), ("Month", "02"), ("Day", "029"),
    ("DataName", "HEAT_SET_TEMP"), ("DataName", "ROOM_TEMP"), ("DataName", "FAN_TIME"),
    ("DataName", "AUTO_SET_TEMP"), ("DataName", "COOL_SET_TEMP "),
])
def test_all_five_response_selector_strings_must_match_without_payload_aliases(key, bad):
    with pytest.raises(MelansProtocolError):
        parse(packet(selector={**SELECTOR, key: bad}, attributes={"HeatSetTempData": SERIES, "RoomTempData": SERIES}))


@pytest.mark.parametrize("key", SELECTOR)
def test_missing_selector_is_never_reconstructed_from_request(key):
    selector = dict(SELECTOR)
    selector.pop(key)
    with pytest.raises(MelansProtocolError):
        parse(packet(selector=selector))


@pytest.mark.parametrize("tag", ["Packet", "Command", "DatabaseManager", "EnergyManagement", "IcTrendDList", "IcTrendDRecord"])
@pytest.mark.parametrize("key,value", [("Group", ""), ("MultiGroup", "*"), ("MultiGroup", "")])
def test_group_or_aggregate_identity_anywhere_on_original_tree_is_rejected(tag, key, value):
    root = ET.fromstring(packet())
    node = root if tag == "Packet" else root.find(f".//{tag}")
    node.set(key, value)
    with pytest.raises(MelansProtocolError):
        parse(ET.tostring(root, encoding="unicode"))


@pytest.mark.parametrize("tag", ["Packet", "Command", "DatabaseManager", "EnergyManagement", "IcTrendDRecord"])
@pytest.mark.parametrize("key", SELECTOR)
def test_selector_is_legal_only_on_the_selected_original_list(tag, key):
    root = ET.fromstring(packet())
    node = root if tag == "Packet" else root.find(f".//{tag}")
    node.set(key, SELECTOR[key])
    with pytest.raises(MelansProtocolError):
        parse(ET.tostring(root, encoding="unicode"))


@pytest.mark.parametrize("mutation", [
    "wrong-root", "wrong-command", "duplicate-command", "nested-command", "duplicate-manager", "extra-root",
    "wrong-energy", "extra-energy", "extra-list", "wrong-list", "nested-list", "wrong-record", "nested-record",
    "two-records", "error-command", "partial-error",
])
def test_ambiguous_original_structure_and_errors_are_not_sanitised_into_history(mutation):
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
    elif mutation == "nested-list":
        ET.SubElement(directory, "IcTrendDList", SELECTOR)
    elif mutation == "wrong-record":
        directory[0].tag = "IcTrendMRecord"
    elif mutation == "nested-record":
        ET.SubElement(directory[0], "IcTrendDRecord")
    elif mutation == "two-records":
        ET.SubElement(directory, "IcTrendDRecord", {"CoolSetTempData": SERIES})
    elif mutation == "error-command":
        root[0].text = "getErrorResponse"
    elif mutation == "partial-error":
        ET.SubElement(directory[0], "ERROR", {"Code": "0101", "Message": "private"})
    with pytest.raises(MelansProtocolError):
        parse(ET.tostring(root, encoding="unicode"))


@pytest.mark.parametrize("tag", ["Packet", "DatabaseManager", "EnergyManagement", "IcTrendDList", "IcTrendDRecord"])
@pytest.mark.parametrize("position", ["text", "tail"])
@pytest.mark.parametrize("text", ["junk", "\u00a0"])
def test_mixed_text_is_rejected_before_raw_series_is_retained(tag, position, text):
    root = ET.fromstring(packet())
    node = root if tag == "Packet" else root.find(f".//{tag}")
    setattr(node, position, text)
    with pytest.raises(MelansProtocolError):
        parse(ET.tostring(root, encoding="unicode"))


def test_xml_whitespace_is_allowed_while_raw_token_spaces_are_preserved():
    root = ET.fromstring(packet(raw=",".join([" 21.5 "] * 48)))
    for node in root.iter():
        if node.tag != "Command":
            node.text = " \t\r\n"
        node.tail = " \t\r\n"
    assert parse(ET.tostring(root, encoding="unicode")).raw_tokens == (" 21.5 ",) * 48


@pytest.mark.parametrize("payload", [
    "not XML", '<!DOCTYPE Packet [<!ENTITY x "data">]>' + packet(),
    '<!ENTITY x "data">' + packet(), packet() + " " * (1024 * 1024),
    packet(raw="0" * 513), packet(attributes={"Private": "x" * 513}),
    packet(attributes={f"Attr{i}": "x" for i in range(65)}),
], ids=["bad-xml", "dtd-entity", "entity", "oversize", "long-data", "long-extra", "many-attributes"])
def test_shared_payload_dtd_attribute_and_size_guards_run_before_filtering(payload):
    with pytest.raises(MelansProtocolError):
        parse(payload)


def test_value_at_exact_shared_boundary_is_preserved_without_numeric_projection():
    raw = "X" * 512
    sample = parse(packet(raw=raw))
    assert sample.raw_data == raw and sample.raw_tokens == (raw,) and sample.slots == ()
    assert sample.issues == (RawDailyTrendIssue("CoolSetTempData", "unexpected_token_count"),)


def test_shared_depth_guard_is_not_bypassed_by_unrequested_children():
    root = ET.fromstring(packet())
    parent = root.find("./DatabaseManager/EnergyManagement/IcTrendDList/IcTrendDRecord")
    for _ in range(20):
        parent = ET.SubElement(parent, "Other")
    with pytest.raises(MelansProtocolError, match="structural limits"):
        parse(ET.tostring(root, encoding="unicode"))
