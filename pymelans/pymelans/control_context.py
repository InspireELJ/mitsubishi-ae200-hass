"""Small, read-only control-context requests and strict stop-flag parsing.

The vendor client labels AllTurnOff and BACnetTurnOff as emergency stops and
TurnOff as an energy-saving stop. These flags describe control restrictions,
not device faults or actual running state. Only exact ON/OFF tokens receive a
boolean interpretation; all returned strings remain available unchanged.

These helpers perform no I/O, freshness updates, control or stop release.
Global and group context are separate requests so callers can isolate optional
firmware support and retain independent success/error timestamps.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass

from .exceptions import MelansProtocolError
from .topology import _identifier, _response

_XML_DECL = '<?xml version="1.0" encoding="UTF-8"?>'


def _on_off(raw: str | None) -> bool | None:
    if raw == "ON":
        return True
    if raw == "OFF":
        return False
    return None


@dataclass(frozen=True)
class GlobalControlContext:
    all_turn_off_raw: str | None

    @property
    def all_turn_off(self) -> bool | None:
        """Controller-wide emergency-stop flag, without inferring its cause."""
        return _on_off(self.all_turn_off_raw)


@dataclass(frozen=True)
class GroupControlContext:
    group_id: str
    turn_off_raw: str | None
    bacnet_turn_off_raw: str | None

    @property
    def turn_off(self) -> bool | None:
        """Group energy-saving stop, independently of its emergency-stop flag."""
        return _on_off(self.turn_off_raw)

    @property
    def bacnet_turn_off(self) -> bool | None:
        """Group BACnet emergency-stop flag; not an equipment fault indicator."""
        return _on_off(self.bacnet_turn_off_raw)


def _request() -> tuple[ET.Element, ET.Element]:
    root = ET.Element("Packet")
    ET.SubElement(root, "Command").text = "getRequest"
    return root, ET.SubElement(root, "DatabaseManager")


def build_global_control_context_request() -> str:
    """Read only the global stop flag, separately from identity metadata."""
    root, manager = _request()
    ET.SubElement(manager, "SystemData", {"AllTurnOff": "*"})
    return _XML_DECL + ET.tostring(root, encoding="unicode")


def build_group_control_context_request(group_id: str | int) -> str:
    """Read only two stop flags for one concrete, caller-selected group."""
    group = _identifier(group_id, 50)
    root, manager = _request()
    ET.SubElement(manager, "Mnet", {
        "Group": group, "TurnOff": "*", "BACnetTurnOff": "*",
    })
    return _XML_DECL + ET.tostring(root, encoding="unicode")


def _record(payload: str, tag: str) -> ET.Element:
    manager = _response(payload)
    records = list(manager)
    if len(records) != 1 or records[0].tag != tag or len(records[0]):
        raise MelansProtocolError(f"expected exactly one direct {tag} context record")
    return records[0]


def parse_global_control_context(payload: str) -> GlobalControlContext:
    """Retain only AllTurnOff from one valid global response record."""
    node = _record(payload, "SystemData")
    return GlobalControlContext(node.get("AllTurnOff"))


def parse_group_control_context(
    payload: str, *, expected_group: str | int
) -> GroupControlContext:
    """Require the requested group identity, excluding address-level records.

    Missing, blank, wildcard or future status tokens remain unknown rather
    than becoming an OFF value. Unknown attributes are never retained.
    """
    expected = _identifier(expected_group, 50)
    node = _record(payload, "Mnet")
    try:
        group = _identifier(node.get("Group", ""), 50)
    except ValueError as err:
        raise MelansProtocolError("invalid group identity in control context") from err
    if group != expected or "Address" in node.attrib:
        raise MelansProtocolError("unexpected group or address identity in control context")
    return GroupControlContext(group, node.get("TurnOff"), node.get("BACnetTurnOff"))
