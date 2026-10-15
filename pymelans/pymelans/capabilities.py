"""Interpret the capability fields used by the controller's own web client.

These are hardware capability switches, distinct from the per-operation local
remote prohibition flags in state responses. Missing data stays unknown.
"""

from __future__ import annotations

from collections.abc import Mapping

READ_ATTRIBUTES = (
    "Model",
    "SubModel",
    "GroupType",
    "IcKind",
    "AutoModeSW",
    "AutoModeSWEx",
    "DryModeSW",
    "FanModeSW",
    "FanSpeedSW",
    "FanAutoSW",
    "FanExLowSW",
    "FanExHighSW",
    "AirDirectionSW",
    "AirStageSW",
    "AirAutoSW",
    "SwingSW",
    "TempDetail",
    "TempDetailC",
    "HumiditySW",
    "HoldSW",
    "ProhibitSW",
    "RemoConProhibitSW",
    "TempRange1Low1",
    "TempRange1High1",
    "TempRange1Low2",
    "TempRange1High2",
    "TempRange1Low3",
    "TempRange1High3",
    "Deadband",
)


def fan_speeds(attrs: Mapping[str, str]) -> list[str] | None:
    stages = attrs.get("FanSpeedSW")
    if stages == "4STAGES":
        values = ["LOW", "MID2", "MID1", "HIGH"]
    elif stages == "3STAGES":
        values = ["MID2", "MID1", "HIGH"]
    elif stages == "2STAGES":
        values = ["MID2", "HIGH"]
        if attrs.get("FanExLowSW") == "ENABLE":
            values.insert(0, "LOW")
            if attrs.get("FanExHighSW") == "ENABLE":
                values.insert(-1, "MID1")
    elif stages == "NONE":
        return []
    else:
        return None
    if attrs.get("FanAutoSW") == "ENABLE":
        values.append("AUTO")
    return values


def air_directions(attrs: Mapping[str, str]) -> list[str] | None:
    if attrs.get("AirDirectionSW") == "DISABLE":
        return []
    if attrs.get("AirDirectionSW") != "ENABLE":
        return None
    stages = attrs.get("AirStageSW")
    if stages == "5STAGES":
        values = ["HORIZONTAL", "MID0", "MID1", "MID2", "VERTICAL"]
    elif stages == "4STAGES":
        values = ["HORIZONTAL", "MID1", "MID2", "VERTICAL"]
    else:
        return None
    if attrs.get("AirAutoSW") == "ENABLE":
        values.append("AUTO")
    if attrs.get("SwingSW") == "ENABLE":
        values.append("SWING")
    return values


def modes(attrs: Mapping[str, str]) -> list[str] | None:
    # This mapping is verified for ordinary indoor AC groups only. Other
    # equipment families use different heating/cooling and ventilation flags.
    if attrs.get("Model") != "IC" or attrs.get("IcKind") != "NORMAL":
        return None
    flags = ("AutoModeSW", "DryModeSW", "FanModeSW")
    if any(attrs.get(key) not in ("ENABLE", "DISABLE") for key in flags):
        return None
    values = ["COOL", "HEAT"]
    values.extend(
        mode
        for mode, key in (("DRY", "DryModeSW"), ("FAN", "FanModeSW"), ("AUTO", "AutoModeSW"))
        if attrs[key] == "ENABLE"
    )
    # NEW groups use a different auto-mode selector and different setpoint
    # fields. Do not claim a tested hardware mode list for that family yet.
    if attrs.get("GroupType") == "NEW":
        return None
    return values
