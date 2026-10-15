"""Shared bounded IC-member daily-trend XML and naive graph labels.

These helpers do not assign units or interpret channel tokens. The calendar
window, one-record limit and 48-slot labels are client pilot protections and
graph conventions, not backend capacity, retention or measurement guarantees.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date

from .exceptions import MelansProtocolError
from .topology import _identifier, _response

MIN_YEAR = 1970
MAX_YEAR = 2037
BUCKET_COUNT = 48
BUCKET_MINUTES = 30
_XML_DECL = '<?xml version="1.0" encoding="UTF-8"?>'
_XML_WHITESPACE = re.compile(r"[ \t\r\n]*")


@dataclass(frozen=True)
class RawDailyTrendIssue:
    key: str
    kind: str


@dataclass(frozen=True)
class RawDailyTrendSlot:
    index: int
    start_time: str
    end_time: str
    raw_token: str = field(repr=False)


@dataclass(frozen=True)
class RawDailyTrendSeries:
    record_count: int
    raw_data: str | None = field(repr=False)
    raw_tokens: tuple[str, ...] = field(repr=False)
    slots: tuple[RawDailyTrendSlot, ...]
    issues: tuple[RawDailyTrendIssue, ...] = ()


def member_day_selector(address: str | int, date_iso: str) -> dict[str, str]:
    """Validate one concrete member/date before a caller adds its fixed channel."""
    member = _identifier(address, 250)
    if not isinstance(date_iso, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", date_iso) is None:
        raise ValueError("date must be a strict Gregorian ISO date")
    try:
        day = date.fromisoformat(date_iso)
    except ValueError as error:
        raise ValueError("date must be a valid Gregorian day") from error
    if not MIN_YEAR <= day.year <= MAX_YEAR:
        raise ValueError("date is outside the pilot interpretation window")
    return {
        "Address": member, "Year": str(day.year), "Month": str(day.month),
        "Day": str(day.day),
    }


def _build_request(selector: dict[str, str]) -> str:
    """Build a GET from the calling channel's already validated full selector."""
    root = ET.Element("Packet")
    ET.SubElement(root, "Command").text = "getRequest"
    energy = ET.SubElement(ET.SubElement(root, "DatabaseManager"), "EnergyManagement")
    ET.SubElement(energy, "IcTrendDList", selector)
    return _XML_DECL + ET.tostring(root, encoding="unicode")


def _directory(payload: str, selector: dict[str, str], *, context: str) -> ET.Element:
    """Check the original envelope and exact selector before retaining a field."""
    _response(payload)
    # Shared byte/DTD/entity/tree/depth/attribute guards run before the second
    # bounded inspection. Validate the original tree before retaining fields.
    root = ET.fromstring(payload)
    if len(root) != 2 or sorted(child.tag for child in root) != ["Command", "DatabaseManager"]:
        raise MelansProtocolError(f"unexpected {context} envelope children")
    command = root.find("./Command")
    assert command is not None
    manager = root.find("./DatabaseManager")
    assert manager is not None
    children = list(manager)
    if len(command) or len(children) != 1 or children[0].tag != "EnergyManagement":
        raise MelansProtocolError(f"expected one direct {context} EnergyManagement")
    directories = list(children[0])
    if len(directories) != 1 or directories[0].tag != "IcTrendDList":
        raise MelansProtocolError("expected one direct EnergyManagement/IcTrendDList")
    directory = directories[0]
    if any(directory.get(key) != value for key, value in selector.items()):
        raise MelansProtocolError(f"{context} response selector mismatch")
    records = list(directory)
    if len(records) > 1 or any(node.tag != "IcTrendDRecord" or len(node) for node in records):
        raise MelansProtocolError("expected at most one direct leaf IcTrendDRecord")
    for node in root.iter():
        if "Group" in node.attrib or "MultiGroup" in node.attrib:
            raise MelansProtocolError(f"unverified group or aggregate {context} identity")
        if node is not directory and any(key in node.attrib for key in selector):
            raise MelansProtocolError(f"unexpected {context} selector placement")
        if node.tag != "Command" and _XML_WHITESPACE.fullmatch(node.text or "") is None:
            raise MelansProtocolError(f"non-whitespace text in {context} response")
        if _XML_WHITESPACE.fullmatch(node.tail or "") is None:
            raise MelansProtocolError(f"non-whitespace tail in {context} response")
    return directory


def _time_label(minutes: int) -> str:
    """Format an interval boundary without timezone or sampling assertions."""
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _raw_series(
    payload: str, selector: dict[str, str], *, selected_field: str, context: str,
) -> RawDailyTrendSeries:
    """Retain one raw field only after checking the untouched original payload.

    Callers supply a fixed allowlisted channel/field pair. All 48-token shapes,
    including 48 blanks, receive graph-position labels without numeric or unit
    interpretation. Other counts are retained without padding or truncation.
    """
    directory = _directory(payload, selector, context=context)
    records = list(directory)
    raw_data = records[0].get(selected_field) if records else None
    raw_tokens = tuple(raw_data.split(",")) if raw_data is not None else ()
    issues: list[RawDailyTrendIssue] = []
    slots: list[RawDailyTrendSlot] = []
    if not records:
        issues.append(RawDailyTrendIssue("record", "missing_record"))
    elif raw_data is None:
        issues.append(RawDailyTrendIssue(selected_field, "missing_data"))
    elif len(raw_tokens) != BUCKET_COUNT:
        issues.append(RawDailyTrendIssue(selected_field, "unexpected_token_count"))
    else:
        for index, token in enumerate(raw_tokens):
            start = index * BUCKET_MINUTES
            slots.append(RawDailyTrendSlot(
                index, _time_label(start), _time_label(start + BUCKET_MINUTES), token,
            ))
    return RawDailyTrendSeries(len(records), raw_data, raw_tokens, tuple(slots), tuple(issues))
