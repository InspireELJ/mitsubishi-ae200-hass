"""Read-only controller wall-clock evidence without timezone or accuracy claims.

The official settings UI bounds its calendar from 1970-01-01 to 2037-12-31.
This module uses that verified window for nullable typed values, preserving
unrecognised raw values rather than inferring support in other firmware.
No UTC offset, DST, synchronisation, drift or schedule date is inferred.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime

from .exceptions import MelansProtocolError
from .topology import _response

READ_ATTRIBUTES = ("Year", "Month", "Day", "Hour", "Minute", "Second")
MIN_YEAR = 1970
MAX_YEAR = 2037
_XML_DECL = '<?xml version="1.0" encoding="UTF-8"?>'
_RANGES = ((MIN_YEAR, MAX_YEAR), (1, 12), (1, 31), (0, 23), (0, 59), (0, 59))
_XML_WHITESPACE = re.compile(r"[ \t\r\n]*")


@dataclass(frozen=True)
class ClockIssue:
    kind: str
    key: str


@dataclass(frozen=True)
class ControllerClock:
    year: int | None
    month: int | None
    day: int | None
    hour: int | None
    minute: int | None
    second: int | None
    local_datetime: str | None
    _attributes: tuple[tuple[str, str], ...] = field(repr=False)
    issues: tuple[ClockIssue, ...] = ()

    def raw_attributes(self) -> dict[str, str]:
        """Return a new dictionary of only the six requested raw fields."""
        return dict(self._attributes)


def build_controller_clock_request() -> str:
    """Read the six displayed clock fields, without settings or calendar scans."""
    root = ET.Element("Packet")
    ET.SubElement(root, "Command").text = "getRequest"
    ET.SubElement(ET.SubElement(root, "DatabaseManager"), "Clock", dict.fromkeys(READ_ATTRIBUTES, "*"))
    return _XML_DECL + ET.tostring(root, encoding="unicode")


def _record(payload: str) -> ET.Element:
    manager = _response(payload)
    # The shared guard has already rejected DTDs, malformed/oversized XML and
    # excessive tree structure. Inspect the bounded envelope as well so text
    # outside DatabaseManager cannot be mistaken for XML whitespace.
    root = ET.fromstring(payload)
    if len(root) != 2 or sorted(child.tag for child in root) != ["Command", "DatabaseManager"]:
        raise MelansProtocolError("unexpected controller-clock envelope children")
    command = root.find("./Command")
    assert command is not None
    nodes = list(manager)
    if len(command) or len(nodes) != 1 or nodes[0].tag != "Clock" or len(nodes[0]):
        raise MelansProtocolError("expected exactly one direct leaf Clock record")
    if any(
        _XML_WHITESPACE.fullmatch(text or "") is None
        for text in (root.text, root.tail, command.tail, manager.text, manager.tail,
                     nodes[0].text, nodes[0].tail)
    ):
        raise MelansProtocolError("non-whitespace text in controller-clock response")
    return nodes[0]


def parse_controller_clock(payload: str) -> ControllerClock:
    """Keep raw values and independently validate nullable clock components.

    Numeric zero-padding is accepted, never added to raw values. A known but
    impossible Gregorian date invalidates Day and the complete local string;
    valid independent components remain available. The local string has no
    offset and does not assert that the controller's time is correct.
    """
    node = _record(payload)
    raw = tuple((key, node.attrib[key]) for key in READ_ATTRIBUTES if key in node.attrib)
    values: list[int | None] = []
    issues: list[ClockIssue] = []
    for key, (low, high) in zip(READ_ATTRIBUTES, _RANGES):
        token = node.get(key)
        if token in (None, "", "*"):
            issues.append(ClockIssue("missing_or_unknown_field", key))
            values.append(None)
        elif re.fullmatch(r"[0-9]{4}" if key == "Year" else r"[0-9]{1,2}", token) is None:
            issues.append(ClockIssue("invalid_field_format", key))
            values.append(None)
        elif not low <= int(token) <= high:
            issues.append(ClockIssue("out_of_range_field", key))
            values.append(None)
        else:
            values.append(int(token))
    if all(value is not None for value in values[:3]):
        try:
            datetime(*values[:3])
        except ValueError:
            values[2] = None
            issues.append(ClockIssue("invalid_calendar_date", "Day"))
    local_datetime = (
        datetime(*values).isoformat(timespec="seconds")
        if all(value is not None for value in values) else None
    )
    return ControllerClock(*values, local_datetime, raw, tuple(issues))
