"""Credential-free, cached diagnostics shared by HA and read-only tools."""

from __future__ import annotations

import re
from typing import Any

from . import capabilities, const, protocol
from .client import MelansClient

_SENSITIVE = re.compile(
    r"password|passwd|secret|token|auth|cookie|session|user|email|serial|macaddress|"
    r"groupname|hostname|ipaddress|licen[cs]e|name$|address$|ip$|"
    r"(^|_)(host|ip|mac|name)($|_)",
    re.IGNORECASE,
)


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: "[REDACTED]" if _SENSITIVE.search(str(k)) else _redact(v) for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def cached_snapshot(client: MelansClient) -> dict[str, Any]:
    """Export cached samples, preserving field names but removing identities.

    This function performs no I/O and never refreshes or writes to the device.
    Group names and connection addresses are intentionally omitted.
    """
    groups = {}
    for gid, dev in sorted(client.devices.items(), key=lambda item: int(item[0])):
        raw = {
            k: "[REDACTED]" if _SENSITIVE.search(k) else v
            for k, v in list(dev.raw_attributes().items())[:512]
        }
        warnings = []
        if (
            any(dev.raw(k) is not None for k in ("RoomHumidity", "InletHumidity"))
            and dev.current_humidity is None
        ):
            warnings.append("humidity_unverified_or_out_of_range")
        if dev.fan_speed is not None and dev.fan_speed not in const.ALL_FAN_SPEEDS:
            warnings.append("unmapped_fan_speed")
        if dev.is_on is None:
            warnings.append("power_state_unknown")
        last_seen = client.group_last_success.get(gid)
        groups[gid] = {
            "kind": dev.kind,
            "quality": client.group_quality(gid),
            "last_successful_read": last_seen.isoformat() if last_seen else None,
            "raw_attributes": raw,
            "capability_attributes": {
                key: value
                for key, value in dev.capability_attributes.items()
                if key in capabilities.READ_ATTRIBUTES
            },
            "supported_modes": dev.supported_modes,
            "supported_fan_speeds": dev.supported_fan_speeds,
            "supported_air_directions": dev.supported_air_directions,
            "unrequested_attributes": sorted(set(raw) - set(protocol._MNET_READ_ATTRS)),
            "warnings": warnings,
        }
    return {
        "schema": "inspire-ae200-cached-diagnostics-v1",
        "library_version": const.VERSION,
        "health": client.health,
        "transport": client.transport_name,
        "last_attempt": client.last_attempt.isoformat() if client.last_attempt else None,
        "last_successful_read": client.last_success.isoformat() if client.last_success else None,
        "stale_after_seconds": client.stale_after,
        "configured_group_count": len(client._names),
        "failed_groups": sorted(client.failed_groups, key=int),
        "read_errors": client.group_errors,
        "capability_error": client.capability_error,
        "last_command": _redact(client.last_command),
        "groups": groups,
        "notes": [
            "Cached data only; a stale group is not a successful new sample.",
            "Zero humidity is unverified and withheld from measurements; its raw value remains here.",
            "Controller credentials, connection addresses and group names are omitted.",
            "No controller firmware, licence, Thermo-ON or energy support is inferred from absent fields.",
        ],
    }
