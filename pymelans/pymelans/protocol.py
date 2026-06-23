"""MELANS XML protocol: payload builders and response parsing.

Kept deliberately transport-agnostic. Both the websocket and HTTP transports
build the same request strings and parse the same response strings through the
helpers here, so the wire grammar lives in exactly one place.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Iterable, Mapping

from .exceptions import MelansProtocolError

_XML_DECL = '<?xml version="1.0" encoding="UTF-8"?>'

# The full attribute superset we request per group. The controller silently
# omits attributes a given unit does not support, which is exactly how we tell
# an AC unit from a Lossnay/air handler from an ATW unit.
_MNET_READ_ATTRS: tuple[str, ...] = (
    "Drive", "Mode", "ModeStatus", "SetTemp", "SetTemp1", "SetTemp2",
    "SetTemp3", "SetTemp4", "SetTemp5", "SetHumidity", "InletTemp",
    "InletHumidity", "RoomHumidity", "AirDirection", "FanSpeed", "RemoCon",
    "DriveItem", "ModeItem", "SetTempItem", "FilterItem", "AirDirItem",
    "FanSpeedItem", "FilterSign", "ErrorSign", "Hold", "CheckWater",
    "Ventilation", "VentiDrive", "VentiFan", "VentMode", "Vent24h",
    "Vent24hMode", "Humid", "TempLimit", "CoolMin", "CoolMax", "HeatMin",
    "HeatMax", "AutoMin", "AutoMax", "OutdoorTemp", "InletTempHWHP",
    "OutletTempHWHP", "Occupancy", "Schedule", "ScheduleAvail",
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
    attrs = " ".join(f'{a}="*"' for a in _MNET_READ_ATTRS)
    mnets = "".join(f'<Mnet Group="{gid}" {attrs} />' for gid in group_ids)
    return (
        f"{_XML_DECL}"
        "<Packet><Command>getRequest</Command><DatabaseManager>"
        f"{mnets}"
        "</DatabaseManager></Packet>"
    )


def build_set_request(group_id: str | int, attributes: Mapping[str, str]) -> str:
    """Build a setRequest that writes ``attributes`` to a single group."""
    attrs = " ".join(f'{k}="{v}"' for k, v in attributes.items())
    return (
        f"{_XML_DECL}"
        "<Packet><Command>setRequest</Command><DatabaseManager>"
        f'<Mnet Group="{group_id}" {attrs} />'
        "</DatabaseManager></Packet>"
    )


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
        if gid is None:
            continue
        name = rec.get("GroupNameWeb") or rec.get("GroupName")
        groups.append({"id": gid, "name": name})
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
        if gid is None:
            continue
        attrs = {k: v for k, v in mnet.attrib.items() if k != "Group"}
        # Unconfigured group: controller echoes the wildcard back unchanged.
        if attrs and all(v == "*" for v in attrs.values()):
            continue
        out[gid] = attrs
    return out
