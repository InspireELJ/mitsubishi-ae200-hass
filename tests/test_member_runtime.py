"""Offline one-member/day runtime grammar and unknown-value contracts."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import FrozenInstanceError

import pytest
from pymelans.exceptions import MelansProtocolError
from pymelans.member_runtime import (
    BUCKET_COUNT,
    DATA_FIELDS,
    build_member_runtime_request,
    parse_member_runtime,
)

SELECTOR = {"Address": "4", "Year": "2024", "Month": "2", "Day": "29", "DataName": "FAN_TIME"}
ZERO = ",".join(["0"] * 48)


def packet(*, selector=None, raw=ZERO, records=1, record_attributes=None):
    root = ET.Element("Packet")
    ET.SubElement(root, "Command").text = "getResponse"
    energy = ET.SubElement(ET.SubElement(root, "DatabaseManager"), "EnergyManagement")
    keys = SELECTOR if selector is None else selector
    directory = ET.SubElement(energy, "IcTrendDList", keys)
    attributes = {} if raw is None else {DATA_FIELDS[keys.get("DataName", "FAN_TIME")]: raw}
    attributes.update(record_attributes or {})
    for _ in range(records):
        ET.SubElement(directory, "IcTrendDRecord", attributes)
    return ET.tostring(root, encoding="unicode")


def parse(payload, **expected):
    return parse_member_runtime(payload, **{
        "expected_address": "4", "expected_date": "2024-02-29",
        "expected_data_name": "FAN_TIME", **expected,
    })


@pytest.mark.parametrize("address", [1, "4", "004", 250])
@pytest.mark.parametrize("data_name", DATA_FIELDS)
def test_request_has_one_exact_read_only_selector_without_wildcard_fields(address, data_name):
    root = ET.fromstring(build_member_runtime_request(address, "2024-02-29", data_name))
    assert root.tag == "Packet" and root.findtext("./Command") == "getRequest"
    manager = root.find("./DatabaseManager")
    assert len(manager) == 1 and manager[0].tag == "EnergyManagement"
    assert len(manager[0]) == 1 and manager[0][0].tag == "IcTrendDList"
    node = manager[0][0]
    assert len(node) == 0 and node.attrib == {**SELECTOR, "Address": str(int(address)), "DataName": data_name}
    assert not ("*" in node.attrib.values() or "Group" in node.attrib)


@pytest.mark.parametrize("address", [0, 251, True, None, "*", "4,5", "-4", "+4", " 4", "4 ", "4.0", "٤", "0004"])
def test_addresses_are_bounded_ascii_without_discovery_or_aliases(address):
    with pytest.raises(ValueError):
        build_member_runtime_request(address, "2024-02-29", "FAN_TIME")
    with pytest.raises(ValueError):
        parse(packet(), expected_address=address)


@pytest.mark.parametrize("day", [
    None, "", "20240229", "2024-2-29", "2024-02-9", "２０２４-02-29", "2024-02-29 ",
    "2024-02-29T00:00:00", "2024-02-29Z", "1969-12-31", "2038-01-01", "0000-01-01",
    "2023-02-29", "2000-02-30", "2024-04-31", "2024-00-10", "2024-13-01", "2024-01-00",
])
def test_date_requires_exact_gregorian_iso_inside_documented_pilot_window(day):
    with pytest.raises(ValueError):
        build_member_runtime_request("4", day, "FAN_TIME")
    with pytest.raises(ValueError):
        parse(packet(), expected_date=day)


@pytest.mark.parametrize("day,parts", [
    ("1970-01-01", ("1970", "1", "1")), ("2037-12-31", ("2037", "12", "31")),
    ("2000-02-29", ("2000", "2", "29")),
])
def test_valid_calendar_boundaries_use_canonical_unpadded_wire_month_and_day(day, parts):
    node = ET.fromstring(build_member_runtime_request("4", day, "FAN_TIME")).find("./DatabaseManager/EnergyManagement/IcTrendDList")
    assert (node.get("Year"), node.get("Month"), node.get("Day")) == parts


@pytest.mark.parametrize("data_name", [None, "", "fan_time", "FAN_TIME ", "FanTimeData", "ELECTRIC_ENERGY", "*", ["FAN_TIME"]])
def test_only_explicit_runtime_names_are_accepted(data_name):
    with pytest.raises(ValueError):
        build_member_runtime_request("4", "2024-02-29", data_name)
    with pytest.raises(ValueError):
        parse(packet(), expected_data_name=data_name)


@pytest.mark.parametrize("data_name", DATA_FIELDS)
def test_exact_zero_report_projects_48_local_half_hours_without_measurement_or_total_claim(data_name):
    sample = parse(packet(selector={**SELECTOR, "DataName": data_name}), expected_data_name=data_name)
    assert sample.address == "4" and sample.date == "2024-02-29" and sample.data_name == data_name
    assert sample.record_count == 1 and sample.raw_data == ZERO and sample.raw_tokens == ("0",) * 48
    assert len(sample.buckets) == BUCKET_COUNT and sample.issues == ()
    assert [(b.index, b.start_time, b.end_time, b.raw_token, b.minutes) for b in sample.buckets[:2]] == [
        (0, "00:00", "00:30", "0", 0.0), (1, "00:30", "01:00", "0", 0.0),
    ]
    assert sample.buckets[-1].start_time == "23:30" and sample.buckets[-1].end_time == "24:00"
    assert all(b.minutes == 0 for b in sample.buckets)
    assert not any(hasattr(sample, key) for key in ("total", "actual_total", "timezone", "utc_date", "hvac_action", "measurement_validated"))


@pytest.mark.parametrize("raw,value", [("0", 0.0), ("00", 0.0), ("0.0", 0.0), ("1", 1.0), ("0.5", 0.5), ("12.25", 12.25), ("30", 30.0), ("30.000", 30.0)])
def test_full_unsigned_decimal_values_are_nullable_minutes_with_raw_precision_preserved(raw, value):
    sample = parse(packet(raw=",".join([raw, *(["0"] * 47)])))
    assert sample.buckets[0].raw_token == raw and sample.buckets[0].minutes == value
    assert sample.raw_tokens[0] == raw and sample.issues == ()


def test_tiny_nonzero_decimal_cannot_underflow_into_a_reported_zero():
    raw = "0." + "0" * 400 + "1"
    sample = parse(packet(raw=",".join([raw, *(["0"] * 47)])))
    assert sample.buckets[0].raw_token == raw and sample.buckets[0].minutes is None
    assert [(issue.key, issue.kind) for issue in sample.issues] == [("bucket_0", "unrepresentable_value")]


def test_every_unknown_slot_retains_its_index_without_leaking_tokens_into_issues():
    sample = parse(packet(raw=",".join(["secret"] * 48)))
    assert len(sample.buckets) == len(sample.issues) == 48
    assert all(bucket.minutes is None and bucket.raw_token == "secret" for bucket in sample.buckets)
    assert [issue.key for issue in sample.issues] == [f"bucket_{index}" for index in range(48)]
    assert "secret" not in repr(sample.issues) and "secret" not in repr(sample)


@pytest.mark.parametrize("raw,kind", [
    ("", "missing_or_unknown_value"), ("*", "missing_or_unknown_value"), ("@m", "missing_or_unknown_value"),
    ("FUTURE", "invalid_value_format"), ("-1", "invalid_value_format"), ("+1", "invalid_value_format"),
    ("1e1", "invalid_value_format"), ("1E0", "invalid_value_format"), ("1junk", "invalid_value_format"),
    (" 1", "invalid_value_format"), ("1 ", "invalid_value_format"), (".5", "invalid_value_format"),
    ("1.", "invalid_value_format"), ("١", "invalid_value_format"), ("NaN", "invalid_value_format"),
    ("Infinity", "invalid_value_format"), ("31", "anomalous_value"), ("30.000000000000000000000001", "anomalous_value"),
])
@pytest.mark.parametrize("index", [0, 23, 47])
def test_unknown_invalid_or_anomalous_slots_preserve_position_raw_and_safe_issues(raw, kind, index):
    values = ["0"] * 48
    values[index] = raw
    sample = parse(packet(raw=",".join(values)))
    assert len(sample.buckets) == 48 and sample.buckets[index].minutes is None
    assert sample.buckets[index].raw_token == sample.raw_tokens[index] == raw
    assert all(bucket.minutes == 0 for bucket in sample.buckets if bucket.index != index)
    assert [(issue.key, issue.kind) for issue in sample.issues] == [(f"bucket_{index}", kind)]


@pytest.mark.parametrize("raw", ["", "*", "0", ",".join(["0"] * 47), ",".join(["0"] * 49), ZERO + ","])
def test_incorrect_token_count_never_pads_truncates_or_invents_48_slots(raw):
    sample = parse(packet(raw=raw))
    assert sample.raw_data == raw and sample.raw_tokens == tuple(raw.split(",")) and sample.buckets == ()
    assert [(issue.key, issue.kind) for issue in sample.issues] == [("FanTimeData", "unexpected_token_count")]


@pytest.mark.parametrize("records,kind", [(0, "missing_record"), (1, "missing_data")])
def test_empty_directory_and_missing_selected_attribute_remain_unknown(records, kind):
    sample = parse(packet(raw=None, records=records, record_attributes={"Other": "private"}))
    assert sample.record_count == records and sample.raw_data is None and sample.raw_tokens == sample.buckets == ()
    assert sample.raw_attributes() == {} and sample.issues[0].kind == kind


def test_data_and_nested_outputs_are_frozen_and_discard_unrequested_private_fields():
    sample = parse(packet(record_attributes={"ThermoTimeData": "private-other-series", "Room": "private-room", "IPAddress": "private-host"}))
    exported = sample.raw_attributes()
    exported["FanTimeData"] = "caller"
    assert sample.raw_attributes() == {"FanTimeData": ZERO}
    assert all(secret not in repr(sample) for secret in ("private", "caller"))
    with pytest.raises(FrozenInstanceError):
        sample.raw_data = "caller"
    with pytest.raises(FrozenInstanceError):
        sample.buckets[0].minutes = 1
    with pytest.raises(TypeError):
        sample.raw_tokens[0] = "caller"


@pytest.mark.parametrize("key,bad", [("Address", "04"), ("Address", "5"), ("Year", "2023"), ("Month", "02"), ("Day", "029"), ("DataName", "THERMO_TIME")])
def test_response_selector_requires_exact_five_canonical_strings(key, bad):
    selector = {**SELECTOR, key: bad}
    with pytest.raises(MelansProtocolError):
        parse(packet(selector=selector))


@pytest.mark.parametrize("key", SELECTOR)
def test_any_missing_selector_is_rejected(key):
    selector = dict(SELECTOR)
    selector.pop(key)
    with pytest.raises(MelansProtocolError):
        parse(packet(selector=selector))


@pytest.mark.parametrize("tag", ["Packet", "Command", "DatabaseManager", "EnergyManagement", "IcTrendDList", "IcTrendDRecord"])
@pytest.mark.parametrize("key,value", [("Group", ""), ("Group", "4"), ("MultiGroup", ""), ("MultiGroup", "*"), ("MultiGroup", "4,5")])
def test_group_and_aggregate_identity_are_rejected_on_every_original_node(tag, key, value):
    root = ET.fromstring(packet())
    root.find(f".//{tag}").set(key, value) if tag != "Packet" else root.set(key, value)
    with pytest.raises(MelansProtocolError):
        parse(ET.tostring(root, encoding="unicode"))


@pytest.mark.parametrize("tag", ["Packet", "DatabaseManager", "EnergyManagement", "IcTrendDRecord"])
@pytest.mark.parametrize("key", SELECTOR)
def test_selector_attributes_must_only_appear_on_the_selected_list(tag, key):
    root = ET.fromstring(packet())
    node = root if tag == "Packet" else root.find(f".//{tag}")
    node.set(key, SELECTOR[key])
    with pytest.raises(MelansProtocolError):
        parse(ET.tostring(root, encoding="unicode"))


@pytest.mark.parametrize("mutation", [
    "other-root", "wrong-command", "duplicate-command", "nested-command", "duplicate-manager", "extra-root",
    "wrong-energy", "extra-energy", "nested-energy", "extra-list", "wrong-list", "nested-list",
    "wrong-record", "nested-record", "two-records", "error-command", "partial-error",
])
def test_ambiguous_or_error_envelopes_are_rejected_before_any_data_projection(mutation):
    root = ET.fromstring(packet())
    manager = root.find("./DatabaseManager")
    energy = manager[0]
    directory = energy[0]
    if mutation == "other-root":
        root.tag = "Other"
    elif mutation == "wrong-command":
        root[0].text = "setResponse"
    elif mutation == "duplicate-command":
        ET.SubElement(root, "Command").text = "getResponse"
    elif mutation == "nested-command":
        ET.SubElement(root[0], "Nested")
    elif mutation == "duplicate-manager":
        ET.SubElement(root, "DatabaseManager")
    elif mutation == "extra-root":
        ET.SubElement(root, "Unknown")
    elif mutation == "wrong-energy":
        energy.tag = "Other"
    elif mutation == "extra-energy":
        ET.SubElement(manager, "EnergyManagement")
    elif mutation == "nested-energy":
        ET.SubElement(ET.SubElement(manager, "Wrapper"), "EnergyManagement")
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
        ET.SubElement(directory, "IcTrendDRecord", {"FanTimeData": ZERO})
    elif mutation == "error-command":
        root[0].text = "getErrorResponse"
    elif mutation == "partial-error":
        ET.SubElement(manager, "ERROR", {"Code": "0101", "Message": "private"})
    with pytest.raises(MelansProtocolError):
        parse(ET.tostring(root, encoding="unicode"))


@pytest.mark.parametrize("tag", ["Packet", "DatabaseManager", "EnergyManagement", "IcTrendDList", "IcTrendDRecord"])
@pytest.mark.parametrize("position", ["text", "tail"])
@pytest.mark.parametrize("text", ["junk", "\u00a0"])
def test_only_pure_xml_whitespace_is_accepted_between_original_nodes(tag, position, text):
    root = ET.fromstring(packet())
    node = root if tag == "Packet" else root.find(f".//{tag}")
    setattr(node, position, text)
    with pytest.raises(MelansProtocolError):
        parse(ET.tostring(root, encoding="unicode"))


def test_xml_whitespace_is_accepted_without_changing_raw_tokens():
    root = ET.fromstring(packet())
    for node in root.iter():
        if node.tag != "Command":
            node.text = " \t\r\n"
        node.tail = " \t\r\n"
    assert parse(ET.tostring(root, encoding="unicode")).raw_data == ZERO


@pytest.mark.parametrize("payload", [
    "not XML", '<!DOCTYPE Packet [<!ENTITY x "data">]>' + packet(),
    '<!ENTITY x "data">' + packet(), packet() + " " * (1024 * 1024),
    packet(raw="0" * 513), packet(record_attributes={"Private": "x" * 513}),
    packet(record_attributes={f"Attr{i}": "x" for i in range(65)}),
], ids=["bad-xml", "dtd-entity", "entity", "oversize", "long-data", "long-extra", "many-attributes"])
def test_shared_structural_dtd_and_size_limits_are_not_bypassed(payload):
    with pytest.raises(MelansProtocolError):
        parse(payload)
