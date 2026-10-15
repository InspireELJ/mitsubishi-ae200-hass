"""Bounded, read-only IC daily runtime evidence without aggregate claims.

The official IC-member daily graph displays 48 half-hour slots in minutes. A
numeric projection describes a controller report, not validated measurement,
enabled history collection, a daily total or a current HVAC operating state.
The 1970..2037 calendar window and single-record limit are pilot protections,
not statements of server retention, capacity or supported history dates.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .exceptions import MelansProtocolError
from .topology import _identifier, _response

MIN_YEAR = 1970
MAX_YEAR = 2037
BUCKET_COUNT = 48
BUCKET_MINUTES = 30
DATA_FIELDS = {"FAN_TIME": "FanTimeData", "THERMO_TIME": "ThermoTimeData"}
_XML_DECL = '<?xml version="1.0" encoding="UTF-8"?>'
_XML_WHITESPACE = re.compile(r"[ \t\r\n]*")
_UNSIGNED_DECIMAL = re.compile(r"[0-9]+(?:\.[0-9]+)?")


@dataclass(frozen=True)
class RuntimeIssue:
    key: str
    kind: str


@dataclass(frozen=True)
class RuntimeBucket:
    index: int
    start_time: str
    end_time: str
    raw_token: str = field(repr=False)
    minutes: float | None


@dataclass(frozen=True)
class MemberRuntime:
    address: str
    date: str
    data_name: str
    record_count: int
    raw_data: str | None = field(repr=False)
    raw_tokens: tuple[str, ...] = field(repr=False)
    buckets: tuple[RuntimeBucket, ...]
    issues: tuple[RuntimeIssue, ...] = ()

    def raw_attributes(self) -> dict[str, str]:
        """Return only the selected raw attribute in a new dictionary."""
        return {} if self.raw_data is None else {DATA_FIELDS[self.data_name]: self.raw_data}


def _selector(address: str | int, date_iso: str, data_name: str) -> dict[str, str]:
    member = _identifier(address, 250)
    if not isinstance(date_iso, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", date_iso) is None:
        raise ValueError("date must be a strict Gregorian ISO date")
    try:
        day = date.fromisoformat(date_iso)
    except ValueError as error:
        raise ValueError("date must be a valid Gregorian day") from error
    if not MIN_YEAR <= day.year <= MAX_YEAR:
        raise ValueError("date is outside the pilot interpretation window")
    if not isinstance(data_name, str) or data_name not in DATA_FIELDS:
        raise ValueError("data_name must be FAN_TIME or THERMO_TIME")
    return {
        "Address": member, "Year": str(day.year), "Month": str(day.month),
        "Day": str(day.day), "DataName": data_name,
    }


def build_member_runtime_request(address: str | int, date_iso: str, data_name: str) -> str:
    """Read one explicit member/day/runtime name; the client proves membership."""
    selector = _selector(address, date_iso, data_name)
    root = ET.Element("Packet")
    ET.SubElement(root, "Command").text = "getRequest"
    energy = ET.SubElement(ET.SubElement(root, "DatabaseManager"), "EnergyManagement")
    ET.SubElement(energy, "IcTrendDList", selector)
    return _XML_DECL + ET.tostring(root, encoding="unicode")


def _directory(payload: str, selector: dict[str, str]) -> ET.Element:
    _response(payload)
    # Shared byte/DTD/entity/tree/depth/attribute guards run before the second
    # bounded inspection. Validate the original tree before retaining fields.
    root = ET.fromstring(payload)
    if len(root) != 2 or sorted(child.tag for child in root) != ["Command", "DatabaseManager"]:
        raise MelansProtocolError("unexpected member-runtime envelope children")
    command = root.find("./Command")
    assert command is not None
    manager = root.find("./DatabaseManager")
    assert manager is not None
    children = list(manager)
    if len(command) or len(children) != 1 or children[0].tag != "EnergyManagement":
        raise MelansProtocolError("expected one direct member-runtime EnergyManagement")
    directories = list(children[0])
    if len(directories) != 1 or directories[0].tag != "IcTrendDList":
        raise MelansProtocolError("expected one direct EnergyManagement/IcTrendDList")
    directory = directories[0]
    if any(directory.get(key) != value for key, value in selector.items()):
        raise MelansProtocolError("member-runtime response selector mismatch")
    records = list(directory)
    if len(records) > 1 or any(node.tag != "IcTrendDRecord" or len(node) for node in records):
        raise MelansProtocolError("expected at most one direct leaf IcTrendDRecord")
    for node in root.iter():
        if "Group" in node.attrib or "MultiGroup" in node.attrib:
            raise MelansProtocolError("unverified group or aggregate member-runtime identity")
        if node is not directory and any(key in node.attrib for key in selector):
            raise MelansProtocolError("unexpected member-runtime selector placement")
        if node.tag != "Command" and _XML_WHITESPACE.fullmatch(node.text or "") is None:
            raise MelansProtocolError("non-whitespace text in member-runtime response")
        if _XML_WHITESPACE.fullmatch(node.tail or "") is None:
            raise MelansProtocolError("non-whitespace tail in member-runtime response")
    return directory


def _minute_value(raw: str, key: str, issues: list[RuntimeIssue]) -> float | None:
    if raw in ("", "*", "@m"):
        # These are unknown tokens here, not assertions of wire sentinel use.
        issues.append(RuntimeIssue(key, "missing_or_unknown_value"))
        return None
    if _UNSIGNED_DECIMAL.fullmatch(raw) is None:
        issues.append(RuntimeIssue(key, "invalid_value_format"))
        return None
    exact = Decimal(raw)
    # A protective single-member duration boundary, not a vendor schema or a
    # clamp. Decimal comparison avoids rounding 30.000...001 into accepted 30.
    if exact > BUCKET_MINUTES:
        issues.append(RuntimeIssue(key, "anomalous_value"))
        return None
    value = float(exact)
    if not math.isfinite(value) or exact != 0 and value == 0:
        issues.append(RuntimeIssue(key, "unrepresentable_value"))
        return None
    return value


def _time_label(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def parse_member_runtime(
    payload: str, *, expected_address: str | int,
    expected_date: str, expected_data_name: str,
) -> MemberRuntime:
    """Preserve raw evidence; project slots only for the exact 48-token shape.

    Slot labels are naive controller wall-clock intervals, ending at 24:00.
    No UTC conversion, chart padding, cross-record sum or daily total is made.
    All-zero data remains a controller report with unverified logging quality.
    """
    selector = _selector(expected_address, expected_date, expected_data_name)
    directory = _directory(payload, selector)
    selected_field = DATA_FIELDS[expected_data_name]
    records = list(directory)
    raw_data = records[0].get(selected_field) if records else None
    raw_tokens = tuple(raw_data.split(",")) if raw_data is not None else ()
    issues: list[RuntimeIssue] = []
    buckets: list[RuntimeBucket] = []
    if not records:
        issues.append(RuntimeIssue("record", "missing_record"))
    elif raw_data is None:
        issues.append(RuntimeIssue(selected_field, "missing_data"))
    elif len(raw_tokens) != BUCKET_COUNT:
        issues.append(RuntimeIssue(selected_field, "unexpected_token_count"))
    else:
        for index, token in enumerate(raw_tokens):
            start = index * BUCKET_MINUTES
            buckets.append(RuntimeBucket(
                index, _time_label(start), _time_label(start + BUCKET_MINUTES),
                token, _minute_value(token, f"bucket_{index}", issues),
            ))
    return MemberRuntime(
        selector["Address"], expected_date, expected_data_name, len(records),
        raw_data, raw_tokens, tuple(buckets), tuple(issues),
    )
