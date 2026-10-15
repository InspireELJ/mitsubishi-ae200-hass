"""Optional startup snapshots do not refresh climate state or expose secrets."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from pymelans.exceptions import MelansCommandUncertainError, MelansProtocolError

from pymelans import MelansClient, MelansDevice, cached_snapshot, const

SYSTEM = ('<Packet><Command>getResponse</Command><DatabaseManager>'
          '<SystemData Model="AE-200E" Version="3.10" VersionIF="1.07" '
          'Number="private-number" Name="private-name" IPAdrsLan="private-host" />'
          '</DatabaseManager></Packet>')
FUNCTIONS = ('<Packet><Command>getResponse</Command><DatabaseManager>'
             '<FunctionControl><FunctionList>'
             '<FunctionRecord Index="1" Status="ENABLE" TimeLimit="9999" Option="private-key" />'
             '<FunctionRecord Index="2" Status="TRIAL" TimeLimit="7" Option2="private-secret" />'
             '<FunctionRecord Index="3" Status="FUTURE" TimeLimit="0" />'
             '</FunctionList></FunctionControl></DatabaseManager></Packet>')


@pytest.mark.asyncio
async def test_metadata_is_once_only_and_cached_export_never_reads():
    c = MelansClient("fixture.invalid")
    c._request = AsyncMock(return_value=SYSTEM)
    await asyncio.gather(c.async_load_controller_metadata(), c.async_load_controller_metadata())
    c._request.assert_awaited_once()
    assert c.controller_metadata.version == "3.10"
    assert c.controller_metadata.model == "AE-200E"
    assert c.metadata_last_attempt <= c.metadata_last_success
    assert c.last_attempt is None and c.last_success is None and not c.devices
    exported = cached_snapshot(c)["controller_metadata"]
    assert exported["version"] == "3.10" and exported["version_if"] == "1.07"
    assert exported["attempted"] and exported["error"] is None
    exported["model"] = "mutated-copy"
    assert c.controller_metadata.model == "AE-200E"
    assert "private-" not in json.dumps(cached_snapshot(c))
    c._request.assert_awaited_once()


@pytest.mark.asyncio
async def test_function_directory_is_once_and_only_status_fields_are_kept():
    c = MelansClient("fixture.invalid")
    c._request = AsyncMock(return_value=FUNCTIONS)
    await asyncio.gather(c.async_load_function_list(), c.async_load_function_list())
    c._request.assert_awaited_once()
    assert c.functions_last_attempt <= c.functions_last_success
    exported = cached_snapshot(c)["functions"]
    assert [r["active"] for r in exported["records"]] == [True, True, None]
    assert exported["records"][0]["time_limit"] == "9999"
    assert all(set(r) == {"index", "status", "time_limit", "active"} for r in exported["records"])
    assert "private-" not in json.dumps(cached_snapshot(c))
    assert not c._group_seen and not c._pending_writes and c.health == "unknown"


@pytest.mark.parametrize("method,prefix", [
    ("async_load_controller_metadata", "metadata"), ("async_load_function_list", "functions"),
])
@pytest.mark.asyncio
async def test_optional_failures_are_independent_sanitised_and_not_retried(method, prefix):
    c = MelansClient("fixture.invalid")
    c._request = AsyncMock(side_effect=MelansProtocolError("private-message", code="0101"))
    await getattr(c, method)()
    await getattr(c, method)()
    c._request.assert_awaited_once()
    assert getattr(c, f"{prefix}_last_attempt") is not None
    assert getattr(c, f"{prefix}_last_success") is None
    assert getattr(c, f"{prefix}_error") == {"type": "MelansProtocolError", "code": "0101"}
    assert "private-" not in json.dumps(cached_snapshot(c))
    c._request = AsyncMock(return_value=FUNCTIONS if prefix == "metadata" else SYSTEM)
    other = "async_load_function_list" if prefix == "metadata" else "async_load_controller_metadata"
    await getattr(c, other)()
    c._request.assert_awaited_once()


@pytest.mark.asyncio
async def test_uncertain_write_is_not_replayed_or_acknowledged(monkeypatch):
    monkeypatch.setattr(const, "MIN_REQUEST_SPACING", 0)
    c = MelansClient("fixture.invalid")
    c._names = {"1": None}
    c.devices["1"] = MelansDevice("1", None, {"Drive": "OFF"})
    c._pending_writes["1"] = {"Drive": "OFF"}
    c._ws.request = AsyncMock(side_effect=MelansCommandUncertainError("Write outcome unknown"))
    c._http.request = AsyncMock()
    with pytest.raises(MelansCommandUncertainError):
        await c.set_power("1", True)
    c._ws.request.assert_awaited_once()
    c._http.request.assert_not_awaited()
    assert c.last_command["status"] == "outcome_unknown"
    assert c.devices["1"].is_on is False and not c._pending_writes
    assert c._readback_requested
    c._request = AsyncMock(return_value=(
        '<Packet><Command>getResponse</Command><DatabaseManager>'
        '<Mnet Group="1" Drive="ON" /></DatabaseManager></Packet>'
    ))
    await c.async_update()
    assert c.devices["1"].is_on is True
    assert c.last_command["status"] == "outcome_unknown"
    c._request.assert_awaited_once()
