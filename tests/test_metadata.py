"""Offline contracts for controller identity and licence-summary parsing."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest
from pymelans.exceptions import MelansProtocolError
from pymelans.metadata import (
    MAX_FUNCTION_RECORDS,
    FunctionRecord,
    build_function_list_request,
    build_system_data_request,
    parse_function_list,
    parse_system_data,
)


def packet(body, command="getResponse"):
    return f"<Packet><Command>{command}</Command><DatabaseManager>{body}</DatabaseManager></Packet>"


def function_packet(*attributes):
    directory = ET.Element("FunctionList")
    for values in attributes:
        ET.SubElement(directory, "FunctionRecord", values)
    return packet("<FunctionControl>" + ET.tostring(directory, encoding="unicode") + "</FunctionControl>")


def test_identity_request_contains_only_display_metadata():
    root = ET.fromstring(build_system_data_request())
    assert root.findtext("./Command") == "getRequest"
    manager = root.find("./DatabaseManager")
    assert len(manager) == 1
    assert manager[0].tag == "SystemData"
    assert manager[0].attrib == {"Model": "*", "Version": "*", "VersionIF": "*"}


def test_function_request_is_read_only_and_has_no_activation_or_keys():
    root = ET.fromstring(build_function_list_request())
    assert root.findtext("./Command") == "getRequest"
    assert root.find("./DatabaseManager/FunctionControl/FunctionList") is not None
    assert all(node.attrib == {} for node in root.iter())


def test_system_fields_preserve_versions_and_discard_all_other_attributes():
    metadata = parse_system_data(packet(
        '<SystemData Model="AE-200E" Version="8.01" VersionIF="1.07" '
        'Number="private-device-number" Name="private-location" MacAddress="private-mac" '
        'IPAdrsLan="private.invalid" Password="secret" />'
    ))
    assert (metadata.model, metadata.version, metadata.version_if) == ("AE-200E", "8.01", "1.07")
    assert metadata.issues == ()
    assert all(private not in repr(metadata) for private in ("private", "secret"))
    assert parse_system_data(packet('<SystemData Version="03.10" />')).version == "03.10"


@pytest.mark.parametrize("value", [None, "", "*"])
def test_missing_or_wildcard_metadata_remains_unknown(value):
    node = ET.Element("SystemData", {} if value is None else dict.fromkeys(("Model", "Version", "VersionIF"), value))
    metadata = parse_system_data(packet(ET.tostring(node, encoding="unicode")))
    assert metadata.model is metadata.version is metadata.version_if is None
    assert len(metadata.issues) == 3


@pytest.mark.parametrize("value", ["x" * 65, "AE 200", "AE\n200", "AE\t200", "ＡＥ-200", "https://private.invalid"])
def test_unsafe_model_or_version_text_is_not_returned(value):
    node = ET.Element("SystemData", {"Model": value, "Version": value, "VersionIF": value})
    metadata = parse_system_data(packet(ET.tostring(node, encoding="unicode")))
    assert metadata.model is metadata.version is metadata.version_if is None
    assert {issue.kind for issue in metadata.issues} == {"invalid_field_token"}
    assert value not in repr(metadata)


@pytest.mark.parametrize(
    "body",
    ["", "<Other />", "<SystemData /><SystemData />", "<Nested><SystemData /></Nested>",
     "<SystemData><SystemData /></SystemData>"],
)
def test_identity_requires_one_record_at_the_exact_path(body):
    with pytest.raises(MelansProtocolError):
        parse_system_data(packet(body))


@pytest.mark.parametrize(
    "status,active", [("ENABLE", True), ("TRIAL", True), ("DISABLE", False),
                      ("RESERVED", False), ("FUTURE_STATUS", None), ("enable", None), (None, None)],
)
def test_only_known_status_tokens_have_an_active_interpretation(status, active):
    record = FunctionRecord("1", status, "9999")
    assert record.active is active
    assert record.time_limit == "9999"
    assert not hasattr(record, "expires_at")


def test_function_summary_retains_only_whitelisted_raw_fields():
    summary = parse_function_list(function_packet(
        {"Index": "001", "Status": "ENABLE", "TimeLimit": "9999", "Option": "secret-option",
         "Option2": "other-secret", "KeyCode": "activation-key", "Name": "private-name"},
        {"Index": "38", "Status": "FUTURE_STATUS", "TimeLimit": "0002"},
        {"Index": "256", "Status": "TRIAL", "TimeLimit": "FUTURE_LIMIT"},
    ))
    assert summary.records == (
        FunctionRecord("1", "ENABLE", "9999"),
        FunctionRecord("38", "FUTURE_STATUS", "0002"),
        FunctionRecord("256", "TRIAL", "FUTURE_LIMIT"),
    )
    assert summary.issues == ()
    assert all(value not in repr(summary) for value in ("secret", "activation-key", "private-name"))
    assert summary.records[1].active is None


@pytest.mark.parametrize("index", [None, "", "*", "0", "257", "-1", "1.0", "1,2", "١", "1" * 100])
def test_invalid_function_indices_are_isolated(index):
    values = {"Status": "ENABLE", "TimeLimit": "9999"}
    if index is not None:
        values["Index"] = index
    summary = parse_function_list(function_packet(values))
    assert summary.records == ()
    assert summary.issues[0].kind == "invalid_function_index"


def test_missing_function_values_do_not_invent_a_status_or_limit():
    summary = parse_function_list(function_packet({"Index": "1", "Status": "*", "TimeLimit": ""}))
    assert summary.records == (FunctionRecord("1", None, None),)
    assert summary.records[0].active is None
    assert len(summary.issues) == 2


def test_identical_function_duplicates_are_not_counted_twice():
    summary = parse_function_list(function_packet(
        {"Index": "1", "Status": "ENABLE", "TimeLimit": "9999"},
        {"Index": "001", "Status": "ENABLE", "TimeLimit": "9999"},
    ))
    assert summary.records == (FunctionRecord("1", "ENABLE", "9999"),)
    assert {issue.kind for issue in summary.issues} == {"duplicate_function"}


@pytest.mark.parametrize("reverse", [False, True])
def test_conflicting_function_indices_never_overwrite_a_status(reverse):
    rows = [
        {"Index": "1", "Status": "ENABLE", "TimeLimit": "9999"},
        {"Index": "001", "Status": "DISABLE", "TimeLimit": "0"},
    ]
    summary = parse_function_list(function_packet(*reversed(rows) if reverse else rows))
    assert summary.records == ()
    assert {issue.kind for issue in summary.issues} == {"conflicting_function"}


@pytest.mark.parametrize(
    "body",
    ["", "<FunctionList />", "<FunctionControl><FunctionList /><FunctionList /></FunctionControl>",
     "<FunctionControl><FunctionList /></FunctionControl><FunctionControl />",
     "<FunctionControl><FunctionList><FunctionList /></FunctionList></FunctionControl>",
     '<FunctionControl><FunctionList /></FunctionControl><FunctionRecord Index="1" />'],
)
def test_function_directory_requires_one_exact_path_and_no_stray_records(body):
    with pytest.raises(MelansProtocolError):
        parse_function_list(packet(body))


def test_empty_function_directory_is_not_an_invented_disabled_licence():
    summary = parse_function_list(function_packet())
    assert summary.records == summary.issues == ()


def test_function_record_count_is_bounded():
    rows = [{"Index": "1", "Status": "ENABLE", "TimeLimit": "9999"}] * (MAX_FUNCTION_RECORDS + 1)
    with pytest.raises(MelansProtocolError):
        parse_function_list(function_packet(*rows))


@pytest.mark.parametrize("parser", [parse_system_data, parse_function_list])
@pytest.mark.parametrize(
    "payload",
    [
        "not XML",
        packet('<SystemData Model="AE-200E" />', command="notifyRequest"),
        packet('<SystemData Model="AE-200E" />', command="setResponse"),
        packet('<ERROR Code="0101" Message="private-message" /><SystemData Model="AE-200E" />'),
        '<!DOCTYPE Packet [<!ENTITY secret "private">]>' + packet('<SystemData Model="&secret;" />'),
        '<Packet><Command>getResponse</Command><Command>getResponse</Command><DatabaseManager /></Packet>',
        '<Packet><Command>getResponse</Command><DatabaseManager /><DatabaseManager /></Packet>',
        packet("<A>" * 20 + "</A>" * 20),
        packet("<A />" * 2100),
        packet('<SystemData Model="' + "x" * 513 + '" />'),
        " " * (1024 * 1024 + 1),
    ],
    ids=[
        "malformed", "notification", "wrong-command", "error-with-record", "dtd",
        "duplicate-command", "duplicate-database", "deep-tree", "many-nodes",
        "long-attribute", "oversized",
    ],
)
def test_metadata_parsers_reuse_strict_xml_safety_limits(parser, payload):
    with pytest.raises(MelansProtocolError):
        parser(payload)
