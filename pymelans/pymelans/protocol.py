"""MELANS XML protocol: payload builders and response parsing.

Kept deliberately transport-agnostic. Both the websocket and HTTP transports
build the same request strings and parse the same response strings through the
helpers here, so the wire grammar lives in exactly one place.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping

from .exceptions import MelansProtocolError

_XML_DECL = '<?xml version="1.0" encoding="UTF-8"?>'


def _group_id(value: str | int) -> str:
    group = str(value)
    if not group.isdecimal() or not 1 <= int(group) <= 50:
        raise ValueError("group_id must be an integer from 1 to 50")
    return str(int(group))


# The full attribute superset we request per group. The controller silently
# omits attributes a given unit does not support, which is exactly how we tell
# an AC unit from a Lossnay/air handler from an ATW unit.
_MNET_READ_ATTRS: tuple[str, ...] = (
    "Drive",
    "Mode",
    "ModeStatus",
    "SetTemp",
    "SetTemp1",
    "SetTemp2",
    "SetTemp3",
    "SetTemp4",
    "SetTemp5",
    "SetHumidity",
    "InletTemp",
    "InletHumidity",
    "RoomHumidity",
    "AirDirection",
    "FanSpeed",
    "RemoCon",
    "DriveItem",
    "ModeItem",
    "SetTempItem",
    "FilterItem",
    "AirDirItem",
    "FanSpeedItem",
    "FilterSign",
    "ErrorSign",
    "Hold",
    "CheckWater",
    "Ventilation",
    "VentiDrive",
    "VentiFan",
    "VentMode",
    "Vent24h",
    "Vent24hMode",
    "Humid",
    "TempLimit",
    "CoolMin",
    "CoolMax",
    "HeatMin",
    "HeatMax",
    "AutoMin",
    "AutoMax",
    "OutdoorTemp",
    "InletTempHWHP",
    "OutletTempHWHP",
    "Occupancy",
    "Schedule",
    "ScheduleAvail",
)


def build_group_list_request() -> str:
    """Request the list of configured groups and their human names."""
    return (
        f"{_XML_DECL}"
        "<Packet><Command>getRequest</Command><DatabaseManager>"
        "<ControlGroup><MnetList /></ControlGroup>"
        "</DatabaseManager></Packet>"
    )


def build_state_request(group_ids: Iterable[str | int]) -> str:
    """Request the full attribute set for one or more groups."""
    root = ET.Element("Packet")
    ET.SubElement(root, "Command").text = "getRequest"
    manager = ET.SubElement(root, "DatabaseManager")
    for gid in group_ids:
        ET.SubElement(
            manager, "Mnet", {"Group": _group_id(gid), **dict.fromkeys(_MNET_READ_ATTRS, "*")}
        )
    return _XML_DECL + ET.tostring(root, encoding="unicode")


def build_set_request(group_id: str | int, attributes: Mapping[str, str]) -> str:
    """Build a setRequest that writes ``attributes`` to a single group."""
    if not attributes or any(
        not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", k) or k == "Group" for k in attributes
    ):
        raise ValueError("invalid write attributes")
    root = ET.Element("Packet")
    ET.SubElement(root, "Command").text = "setRequest"
    manager = ET.SubElement(root, "DatabaseManager")
    ET.SubElement(manager, "Mnet", {"Group": _group_id(group_id), **attributes})
    return _XML_DECL + ET.tostring(root, encoding="unicode")


def parse_acknowledgement(payload: str) -> None:
    """Reject error and malformed replies before reporting a write as accepted."""
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise MelansProtocolError(f"invalid acknowledgement XML: {exc}") from exc
    _raise_for_error(root)
    if root.tag != "Packet" or root.findtext("./Command") != "setResponse":
        raise MelansProtocolError("expected a setResponse acknowledgement")


def _raise_for_error(root: ET.Element) -> None:
    err = root.find(".//ERROR")
    if err is not None:
        raise MelansProtocolError(
            err.get("Message", "controller returned an error"),
            code=err.get("Code"),
            point=err.get("Point"),
        )
    cmd = root.findtext("./Command", default="")
    if cmd.endswith("ErrorResponse"):
        raise MelansProtocolError(f"controller returned {cmd}")


def parse_group_list(payload: str) -> list[dict[str, str | None]]:
    """Parse a group-list response into ``[{"id":..., "name":...}, ...]``."""
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:  # noqa: PERF203 - clarity over micro-perf
        raise MelansProtocolError(f"invalid XML: {exc}") from exc
    _raise_for_error(root)
    groups: list[dict[str, str | None]] = []
    for rec in root.findall("./DatabaseManager/ControlGroup/MnetList/MnetRecord"):
        gid = rec.get("Group")
        if gid is None or not gid.isdecimal() or not 1 <= int(gid) <= 50:
            continue
        name = rec.get("GroupNameWeb") or rec.get("GroupName")
        groups.append({"id": str(int(gid)), "name": name})
    return groups


def parse_state(payload: str) -> dict[str, dict[str, str]]:
    """Parse a state response into ``{group_id: {attr: value}}``.

    Groups whose attributes all come back as the literal ``*`` placeholder
    (i.e. unconfigured slots) are skipped.
    """
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise MelansProtocolError(f"invalid XML: {exc}") from exc
    _raise_for_error(root)
    out: dict[str, dict[str, str]] = {}
    for mnet in root.findall("./DatabaseManager/Mnet"):
        gid = mnet.get("Group")
        if gid is None or not gid.isdecimal() or not 1 <= int(gid) <= 50:
            continue
        attrs = {k: v for k, v in mnet.attrib.items() if k != "Group"}
        # Unconfigured group: controller echoes the wildcard back unchanged.
        if not attrs or all(v in ("*", "") for v in attrs.values()):
            continue
        out[str(int(gid))] = attrs
    return out
