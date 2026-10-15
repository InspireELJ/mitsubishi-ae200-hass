"""Bounded, read-only membership and address XML without status interpretation.

Address records must not use the group-state parser: several physical members
can share a group. Raw records, including rejected or ambiguous records, remain
available to private diagnostics. These helpers perform no I/O or freshness
updates and never infer boolean meanings from unverified status tokens.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from itertools import islice

from .exceptions import MelansProtocolError
from .protocol import _raise_for_error

_XML_DECL = '<?xml version="1.0" encoding="UTF-8"?>'
MAX_XML_BYTES = 1024 * 1024
MAX_RECORDS = 512
MAX_REQUEST_ADDRESSES = 50
MAX_ADDRESS = 250
_MAX_NODES = 2048
_MAX_DEPTH = 16
_MAX_ATTRIBUTES = 64
_MAX_VALUE_LENGTH = 512

# Names are established by the official address schema. Their status enums are
# intentionally not interpreted here. No arbitrary fields or writes are built.
ADDRESS_READ_ATTRIBUTES = (
    "Group", "Model", "SubModel", "ErrorSign", "AlarmCode",
    "ThermoStatus", "FanStatus",
)
# DefrostStatus is present in the vendor schema but was rejected as an unknown
# attribute by the pilot firmware. Do not include it in the default request.


@dataclass(frozen=True)
class ParseIssue:
    """A structural/selection issue; index is zero-based in returned records."""

    kind: str
    record_index: int | None = None
    key: str | None = None


@dataclass(frozen=True)
class MembershipRecord:
    group_id: str | None
    address: str | None
    model: str | None
    sub_model: str | None
    contact: str | None
    attributes: Mapping[str, str]
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class MembershipSnapshot:
    records: tuple[MembershipRecord, ...]
    addresses: tuple[str, ...]
    issues: tuple[ParseIssue, ...]

    def members_for_group(self, group_id: str | int) -> tuple[MembershipRecord, ...]:
        """Return all records for a group, retaining rejected/duplicate rows."""
        group = _identifier(group_id, 50)
        return tuple(record for record in self.records if record.group_id == group)


@dataclass(frozen=True)
class AddressRecord:
    address: str | None
    group_id: str | None
    attributes: Mapping[str, str]
    issues: tuple[str, ...] = ()

    def value(self, attribute: str) -> str | None:
        """Return an uninterpreted token; absent/blank/wildcard stays unknown."""
        value = self.attributes.get(attribute)
        return None if value in (None, "", "*") else value


@dataclass(frozen=True)
class AddressSnapshot:
    records: tuple[AddressRecord, ...]
    states: Mapping[str, Mapping[str, str]]
    missing_addresses: tuple[str, ...]
    issues: tuple[ParseIssue, ...]


def _identifier(value: str | int, maximum: int, *, allow_zero: bool = False) -> str:
    text = str(value)
    if not re.fullmatch(r"[0-9]{1,3}", text):
        raise ValueError("identifier must be a bounded ASCII decimal integer")
    number = int(text)
    if not (0 if allow_zero else 1) <= number <= maximum:
        raise ValueError(f"identifier must be between {0 if allow_zero else 1} and {maximum}")
    return str(number)


def normalise_address(value: str | int) -> str:
    """Canonicalise a concrete M-NET address from 1 to 250, excluding zero."""
    return _identifier(value, MAX_ADDRESS)


def _ids(values: Iterable[str | int], maximum: int, count: int) -> tuple[str, ...]:
    # A bounded consume also rejects oversized generators without exhausting
    # them. Bound supplied entries, rather than only the deduplicated set.
    if isinstance(values, (str, bytes)):
        raise TypeError("identifiers must be supplied as a collection")
    supplied = list(islice(values, count + 1))
    if not supplied or len(supplied) > count:
        raise ValueError(f"requires 1 to {count} identifiers")
    return tuple(dict.fromkeys(_identifier(value, maximum) for value in supplied))


def _request() -> tuple[ET.Element, ET.Element]:
    root = ET.Element("Packet")
    ET.SubElement(root, "Command").text = "getRequest"
    return root, ET.SubElement(root, "DatabaseManager")


def build_membership_request() -> str:
    """Read the configured directory once; never enumerate unknown addresses."""
    root, manager = _request()
    ET.SubElement(ET.SubElement(manager, "ControlGroup"), "MnetGroupList")
    return _XML_DECL + ET.tostring(root, encoding="unicode")


def build_address_request(
    addresses: Iterable[str | int], *, allowed_addresses: Iterable[str | int]
) -> str:
    """Read at most 50 concrete addresses from an explicit directory allowlist."""
    requested = _ids(addresses, MAX_ADDRESS, MAX_REQUEST_ADDRESSES)
    allowed = set(_ids(allowed_addresses, MAX_ADDRESS, MAX_ADDRESS))
    if not set(requested) <= allowed:
        raise ValueError("address is outside the configured allowlist")
    root, manager = _request()
    for address in requested:
        ET.SubElement(manager, "Mnet", {
            "Address": address, **dict.fromkeys(ADDRESS_READ_ATTRIBUTES, "*"),
        })
    return _XML_DECL + ET.tostring(root, encoding="unicode")


def _response(payload: str) -> ET.Element:
    if len(payload) > MAX_XML_BYTES:
        raise MelansProtocolError("topology response exceeds size limit")
    try:
        size = len(payload.encode("utf-8"))
    except UnicodeError as err:
        raise MelansProtocolError("invalid response text") from err
    if size > MAX_XML_BYTES or re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", payload, re.IGNORECASE):
        raise MelansProtocolError("oversized response or unsupported XML declaration")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as err:
        raise MelansProtocolError("invalid topology XML") from err
    _raise_for_error(root)
    if (
        root.tag != "Packet"
        or len(root.findall("./Command")) != 1
        or root.findtext("./Command") != "getResponse"
        or len(root.findall("./DatabaseManager")) != 1
    ):
        raise MelansProtocolError("expected one getResponse database envelope")
    pending = [(root, 1)]
    count = 0
    while pending:
        node, depth = pending.pop()
        count += 1
        if count > _MAX_NODES or depth > _MAX_DEPTH:
            raise MelansProtocolError("topology response exceeds structural limits")
        if len(node.attrib) > _MAX_ATTRIBUTES or any(
            len(key) > 64 or len(value) > _MAX_VALUE_LENGTH
            for key, value in node.attrib.items()
        ):
            raise MelansProtocolError("topology response attributes exceed limits")
        pending.extend((child, depth + 1) for child in node)
    manager = root.find("./DatabaseManager")
    assert manager is not None
    return manager


def _parsed_id(
    raw: str | None, maximum: int, name: str, issues: list[str], *, allow_zero: bool = False
) -> str | None:
    if raw is None:
        issues.append(f"missing_{name}")
        return None
    try:
        return _identifier(raw, maximum, allow_zero=allow_zero)
    except ValueError:
        issues.append(f"invalid_{name}")
        return None


def _token(raw: str | None) -> str | None:
    return None if raw in (None, "", "*") else raw


def _signature(attributes: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    """Canonicalise only proven identifier syntax, never status tokens."""
    result = dict(attributes)
    for key, maximum in (("Address", MAX_ADDRESS), ("Group", 50)):
        try:
            result[key] = _identifier(result[key], maximum, allow_zero=key == "Address")
        except (KeyError, ValueError):
            pass
    return tuple(sorted(result.items()))


def _all_issues(records: Iterable[MembershipRecord | AddressRecord]) -> list[ParseIssue]:
    return [
        ParseIssue(issue, index, record.address)
        for index, record in enumerate(records) for issue in record.issues
    ]


def parse_membership(
    payload: str, *, expected_groups: Iterable[str | int]
) -> MembershipSnapshot:
    """Retain every configured-member row and isolate invalid/unexpected rows.

    ``addresses`` contains only valid, expected, unambiguous nonzero members.
    A directory can represent mixed equipment; callers must still select an
    appropriate equipment family before making optional address status reads.
    """
    expected = set(_ids(expected_groups, 50, 50))
    manager = _response(payload)
    directories = manager.findall("./ControlGroup/MnetGroupList")
    if len(directories) != 1:
        raise MelansProtocolError("expected one configured membership directory")
    nodes = directories[0].findall("./MnetGroupRecord")
    if len(nodes) > MAX_RECORDS:
        raise MelansProtocolError("membership response exceeds record limit")
    records = []
    edges: dict[tuple[str, str, str | None], list[int]] = defaultdict(list)
    addresses: dict[str, list[int]] = defaultdict(list)
    for node in nodes:
        attrs = dict(node.attrib)
        issues: list[str] = []
        group = _parsed_id(attrs.get("Group"), 50, "group", issues)
        address = _parsed_id(attrs.get("Address"), MAX_ADDRESS, "address", issues, allow_zero=True)
        if group is not None and group not in expected:
            issues.append("unexpected_group")
        if address == "0":
            issues.append("zero_address")
        record = MembershipRecord(
            group, address, _token(attrs.get("Model")), _token(attrs.get("SubModel")),
            _token(attrs.get("Contact")), attrs, tuple(issues),
        )
        index = len(records)
        records.append(record)
        if not issues and group is not None and address is not None:
            edges[group, address, record.contact].append(index)
            addresses[address].append(index)
    for indices in edges.values():
        if len(indices) > 1:
            signatures = {_signature(records[index].attributes) for index in indices}
            issue = "duplicate_membership" if len(signatures) == 1 else "conflicting_membership"
            for index in indices:
                records[index] = replace(records[index], issues=(*records[index].issues, issue))
    for indices in addresses.values():
        families = {(records[index].model, records[index].sub_model) for index in indices}
        if len(families) > 1:
            for index in indices:
                records[index] = replace(
                    records[index], issues=(*records[index].issues, "conflicting_address_model")
                )
    # A clean edge must not re-admit an address whose other directory record
    # is conflicting, invalid or outside the configured group selection.
    blocked = {
        record.address for record in records
        if any(issue != "duplicate_membership" for issue in record.issues)
    }
    allowed = tuple(sorted({
        record.address for record in records
        if record.address is not None and record.address not in blocked
    }, key=int))
    return MembershipSnapshot(tuple(records), allowed, tuple(_all_issues(records)))


def parse_address_state(
    payload: str, *, expected_addresses: Iterable[str | int]
) -> AddressSnapshot:
    """Parse by Address, retaining raw unknown tokens and ambiguous records.

    ``states`` contains only requested, structurally valid records without a
    duplicate conflict. It does not assert that any status enum is supported or
    that its values are fresh physical measurements. Conflicting duplicates
    are excluded as a whole; identical duplicates are retained in ``records``
    but represented once in ``states``.
    """
    expected = set(_ids(expected_addresses, MAX_ADDRESS, MAX_REQUEST_ADDRESSES))
    manager = _response(payload)
    nodes = manager.findall("./Mnet")
    if len(nodes) > MAX_RECORDS:
        raise MelansProtocolError("address response exceeds record limit")
    records = []
    by_address: dict[str, list[int]] = defaultdict(list)
    for node in nodes:
        attrs = dict(node.attrib)
        issues: list[str] = []
        address = _parsed_id(attrs.get("Address"), MAX_ADDRESS, "address", issues)
        group = None
        if attrs.get("Group") not in (None, "", "*"):
            group = _parsed_id(attrs["Group"], 50, "group", issues)
        if address is not None and address not in expected:
            issues.append("unexpected_address")
        index = len(records)
        records.append(AddressRecord(address, group, attrs, tuple(issues)))
        if address in expected:
            by_address[address].append(index)
    states = {}
    for address, indices in by_address.items():
        if len(indices) > 1:
            signatures = {_signature(records[index].attributes) for index in indices}
            issue = "duplicate_address" if len(signatures) == 1 else "conflicting_address"
            for index in indices:
                records[index] = replace(records[index], issues=(*records[index].issues, issue))
        if all(
            all(issue == "duplicate_address" for issue in records[index].issues)
            for index in indices
        ):
            states[address] = dict(records[indices[0]].attributes)
    missing = tuple(sorted(expected - set(by_address), key=int))
    issues = _all_issues(records)
    issues.extend(ParseIssue("missing_address", key=address) for address in missing)
    return AddressSnapshot(tuple(records), states, missing, tuple(issues))
