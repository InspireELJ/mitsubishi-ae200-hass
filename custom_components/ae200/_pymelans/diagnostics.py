"""Credential-free, cached diagnostics shared by HA and read-only tools."""

from __future__ import annotations

import re
from typing import Any

from . import capabilities, const, protocol, topology
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


def _topology_issues(issues: tuple[topology.ParseIssue, ...]) -> list[dict[str, Any]]:
    return [
        {"kind": issue.kind, "record_index": issue.record_index, "mnet_address": issue.key}
        for issue in issues
    ]


def _schema_token(value: str | None) -> str | None:
    """Equipment/contact codes must not carry connection or identity text."""
    if value is None:
        return None
    return value if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value) else "[REDACTED]"


def _cached_topology(client: MelansClient) -> dict[str, Any]:
    snapshot = client.topology
    return {
        "attempted": client._topology_attempted,
        "last_attempt": (
            client.topology_last_attempt.isoformat() if client.topology_last_attempt else None
        ),
        "last_successful_read": (
            client.topology_last_success.isoformat() if client.topology_last_success else None
        ),
        "error": dict(client.topology_error) if client.topology_error else None,
        "issues": _topology_issues(snapshot.issues) if snapshot else [],
        "records": [
            {
                "group_id": record.group_id,
                "mnet_address": record.address,
                "model": _schema_token(record.model),
                "sub_model": _schema_token(record.sub_model),
                "contact": _schema_token(record.contact),
                "issues": list(record.issues),
            }
            for record in snapshot.records
        ] if snapshot else [],
        "allowed_member_addresses": list(client.allowed_member_addresses),
    }


def _cached_members(client: MelansClient) -> dict[str, Any]:
    members = {}
    addresses = set(client.member_states) | set(client.member_last_attempt) | set(client.member_read_status)
    for address in sorted(addresses, key=int):
        last_attempt = client.member_last_attempt.get(address)
        last_success = client.member_last_success.get(address)
        raw = {
            key: (
                _schema_token(value)
                if key in ("Model", "SubModel") and value not in ("", "*")
                else value
            ) for key, value in client.member_states.get(address, {}).items()
            if key in topology.ADDRESS_READ_ATTRIBUTES
        }
        # Protocol Address is a validated numeric M-NET identifier, not an IP.
        # Unknown address-level fields and names are deliberately not exported.
        if address in client.member_states:
            raw["Address"] = address
        members[address] = {
            "quality": client.member_quality(address),
            "last_attempt": last_attempt.isoformat() if last_attempt else None,
            "last_successful_read": last_success.isoformat() if last_success else None,
            "error": dict(client.member_errors[address]) if address in client.member_errors else None,
            "read_status": client.member_read_status.get(address),
            "issues": _topology_issues(client.member_issues.get(address, ())),
            "raw_attributes": raw,
        }
    return members


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
        "control_context": client.control_context_snapshot(),
        "today_schedules": client.today_schedule_snapshot(),
        "controller_metadata": {
            "attempted": client._metadata_attempted,
            "last_attempt": client.metadata_last_attempt.isoformat() if client.metadata_last_attempt else None,
            "last_successful_read": client.metadata_last_success.isoformat() if client.metadata_last_success else None,
            "error": dict(client.metadata_error) if client.metadata_error else None,
            "model": client.controller_metadata.model if client.controller_metadata else None,
            "version": client.controller_metadata.version if client.controller_metadata else None,
            "version_if": client.controller_metadata.version_if if client.controller_metadata else None,
            "issues": [
                {"kind": issue.kind, "record_index": issue.record_index, "key": issue.key}
                for issue in client.controller_metadata.issues
            ] if client.controller_metadata else [],
        },
        "functions": {
            "attempted": client._functions_attempted,
            "last_attempt": client.functions_last_attempt.isoformat() if client.functions_last_attempt else None,
            "last_successful_read": client.functions_last_success.isoformat() if client.functions_last_success else None,
            "error": dict(client.functions_error) if client.functions_error else None,
            "records": [
                {"index": record.index, "status": record.status,
                 "time_limit": record.time_limit, "active": record.active}
                for record in client.function_list.records
            ] if client.function_list else [],
            "issues": [
                {"kind": issue.kind, "record_index": issue.record_index, "key": issue.key}
                for issue in client.function_list.issues
            ] if client.function_list else [],
        },
        "topology": _cached_topology(client),
        "members": _cached_members(client),
        "groups": groups,
        "notes": [
            "Cached data only; a stale group is not a successful new sample.",
            "Zero humidity is unverified and withheld from measurements; its raw value remains here.",
            "Controller credentials, connection addresses and group names are omitted.",
            "Numeric M-NET addresses identify topology members, not network endpoints.",
            "Member status tokens are raw cached values; no physical operating state is inferred.",
            "No controller firmware, licence, Thermo-ON or energy support is inferred from absent fields.",
            "Versions and function statuses are startup snapshots, not continuously refreshed state.",
            "An active licence does not establish endpoint or equipment support; time limits are raw tokens.",
            "Control-context reads are explicit and independently timed; stale typed stop flags are unknown.",
        ],
    }
