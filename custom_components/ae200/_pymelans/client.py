"""Rate-limited MELANS client with per-group freshness and checked writes."""

from __future__ import annotations

import asyncio
import math
import time
from datetime import UTC, datetime

import aiohttp

from . import const, protocol
from .device import MelansDevice
from .exceptions import MelansConnectionError, MelansError
from .transport import HttpTransport, WebsocketTransport


class MelansClient:
    def __init__(
        self,
        host: str,
        *,
        port: int = const.DEFAULT_PORT,
        prefer_websocket: bool = True,
        session: aiohttp.ClientSession | None = None,
        write_spacing: float = const.MIN_WRITE_SPACING,
        stale_after: float = const.DEFAULT_STALE_AFTER,
    ):
        self.host = host
        self.port = port
        self._prefer_ws = prefer_websocket
        self._ws = WebsocketTransport(host, port=port)
        self._http = HttpTransport(host, port=port, session=session)
        self._names: dict[str, str | None] = {}
        self.devices: dict[str, MelansDevice] = {}
        self._write_spacing = max(write_spacing, const.MIN_WRITE_SPACING)
        self.stale_after = max(stale_after, const.MIN_POLL_INTERVAL)
        self._last_write = 0.0
        self._last_request: float | None = None
        self._last_poll: float | None = None
        self._last_poll_ok = False
        self._ws_retry_after = 0.0
        self._uses_ws = prefer_websocket
        self._write_lock = asyncio.Lock()
        self._io_lock = asyncio.Lock()
        self._poll_lock = asyncio.Lock()
        self._group_seen: dict[str, float] = {}
        self.group_last_success: dict[str, datetime] = {}
        self.failed_groups: set[str] = set()
        self.group_errors: dict[str, dict[str, str | None]] = {}
        self.last_attempt: datetime | None = None
        self.last_success: datetime | None = None
        self.last_command: dict[str, object] | None = None
        self._pending_writes: dict[str, dict[str, str]] = {}
        self._readback_requested = False

    async def __aenter__(self) -> MelansClient:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def close(self) -> None:
        await self._ws.close()
        await self._http.close()

    async def _request(self, payload: str) -> str:
        """Serialise discovery, reads and writes against one controller."""
        async with self._io_lock:
            if self._last_request is not None:
                delay = const.MIN_REQUEST_SPACING - (time.monotonic() - self._last_request)
                if delay > 0:
                    await asyncio.sleep(delay)
            self._last_request = time.monotonic()
            if self._prefer_ws and time.monotonic() >= self._ws_retry_after:
                try:
                    response = await self._ws.request(payload)
                except MelansConnectionError:
                    self._uses_ws = False
                    self._ws_retry_after = time.monotonic() + 300
                    delay = const.MIN_REQUEST_SPACING - (time.monotonic() - self._last_request)
                    if delay > 0:
                        await asyncio.sleep(delay)
                    self._last_request = time.monotonic()
                else:
                    self._uses_ws = True
                    self._ws_retry_after = 0.0
                    return response
            self._uses_ws = False
            return await self._http.request(payload)

    async def discover(self) -> dict[str, str | None]:
        """Try configured groups first, then bounded reads for old firmware."""
        if self._prefer_ws:
            try:
                payload = await self._request(protocol.build_group_list_request())
                groups = protocol.parse_group_list(payload)
                self._names = {g["id"]: g["name"] for g in groups if g["id"]}
                if self._names:
                    return dict(self._names)
            except MelansError:
                pass
        found: dict[str, str | None] = {}
        connection_failures = 0
        for gid in range(1, const.MAX_GROUPS + 1):
            try:
                payload = await self._request(protocol.build_state_request([gid]))
                state = protocol.parse_state(payload)
            except MelansConnectionError:
                connection_failures += 1
                if connection_failures >= 2:
                    raise MelansConnectionError("controller unavailable during discovery") from None
                continue
            except MelansError:
                connection_failures = 0
                continue
            connection_failures = 0
            if str(gid) in state:
                found[str(gid)] = None
        self._names = found
        return dict(found)

    async def async_update(self) -> dict[str, MelansDevice]:
        """Retain the historical cache for diagnostics, never as fresh data."""
        async with self._poll_lock:
            if self._last_poll is not None and not self._readback_requested:
                if time.monotonic() - self._last_poll < const.MIN_POLL_INTERVAL:
                    if not self._last_poll_ok:
                        raise MelansConnectionError(
                            "last poll failed; minimum poll interval applies"
                        )
                    return dict(self.devices)
            if not self._names:
                await self.discover()
            ids = list(self._names)
            if not ids:
                raise MelansConnectionError("no configured groups were found")
            self._last_poll = time.monotonic()
            self.last_attempt = datetime.now(UTC)
            self._readback_requested = False
            states: dict[str, dict[str, str]] = {}
            seen: dict[str, tuple[float, datetime]] = {}
            self.group_errors = {}
            connection_failures = 0
            try:
                payload = await self._request(protocol.build_state_request(ids))
                states = {
                    k: v for k, v in protocol.parse_state(payload).items() if k in self._names
                }
                seen.update({gid: (time.monotonic(), datetime.now(UTC)) for gid in states})
            except MelansConnectionError:
                connection_failures = 1
            except MelansError:
                pass
            # Some firmware rejects a batch, or returns only its first group.
            for gid in ids:
                if gid in states:
                    continue
                if connection_failures >= 2:
                    self.group_errors[gid] = {"type": "controller_unavailable", "code": None}
                    continue
                try:
                    payload = await self._request(protocol.build_state_request([gid]))
                    result = protocol.parse_state(payload)
                    if gid in result:
                        states[gid] = result[gid]
                        seen[gid] = (time.monotonic(), datetime.now(UTC))
                    else:
                        self.group_errors[gid] = {"type": "missing_group", "code": None}
                    connection_failures = 0
                except MelansError as err:
                    self.group_errors[gid] = {
                        "type": type(err).__name__,
                        "code": getattr(err, "code", None),
                    }
                    connection_failures = (
                        connection_failures + 1 if isinstance(err, MelansConnectionError) else 0
                    )
            self.failed_groups = set(ids) - set(states)
            for gid, attrs in states.items():
                if gid in self.devices:
                    self.devices[gid].update(attrs)
                else:
                    self.devices[gid] = MelansDevice(gid, self._names.get(gid), attrs)
                self._group_seen[gid], self.group_last_success[gid] = seen[gid]
                self.last_success = seen[gid][1]
                self._confirm_readback(gid, attrs)
            self._last_poll_ok = bool(states)
            if not states:
                raise MelansConnectionError("no groups returned fresh data")
            return dict(self.devices)

    def _confirm_readback(self, gid: str, attrs: dict[str, str]) -> None:
        expected = self._pending_writes.get(gid)
        if not expected:
            return

        def matches(key: str, value: str) -> bool:
            if key == "SetTemp":
                try:
                    return float(attrs.get(key, "")) == float(value)
                except ValueError:
                    return False
            return attrs.get(key) == value

        confirmed = all(matches(k, v) for k, v in expected.items())
        if (
            self.last_command
            and self.last_command.get("group_id") == gid
            and self.last_command.get("status") in ("accepted_unconfirmed", "not_yet_confirmed")
        ):
            self.last_command["status"] = "confirmed" if confirmed else "not_yet_confirmed"
        if confirmed:
            self._pending_writes.pop(gid)

    def is_group_available(self, group_id: str) -> bool:
        seen = self._group_seen.get(group_id)
        return (
            seen is not None
            and group_id not in self.failed_groups
            and time.monotonic() - seen <= self.stale_after
        )

    def group_quality(self, group_id: str) -> str:
        if group_id not in self.group_last_success:
            return "unknown"
        return "fresh" if self.is_group_available(group_id) else "stale"

    @property
    def health(self) -> str:
        if self.last_attempt is None:
            return "unknown"
        available = sum(self.is_group_available(gid) for gid in self._names)
        if not available:
            return "offline"
        return "online" if available == len(self._names) else "degraded"

    async def _write(self, group_id: str, attributes: dict[str, str]) -> None:
        payload = protocol.build_set_request(group_id, attributes)
        async with self._write_lock:
            delay = self._write_spacing - (time.monotonic() - self._last_write)
            if delay > 0:
                await asyncio.sleep(delay)
            self.last_command = {
                "group_id": group_id,
                "attributes": dict(attributes),
                "at": datetime.now(UTC).isoformat(),
                "status": "pending",
            }
            try:
                response = await self._request(payload)
                protocol.parse_acknowledgement(response)
            except MelansError:
                self.last_command["status"] = "failed"
                raise
            finally:
                self._last_write = time.monotonic()
            self.last_command["status"] = "accepted_unconfirmed"
            self._pending_writes.setdefault(group_id, {}).update(attributes)
            self._readback_requested = True

    async def set_power(self, group_id: str, on: bool) -> None:
        await self._write(group_id, {"Drive": const.DRIVE_ON if on else const.DRIVE_OFF})

    async def set_mode(self, group_id: str, mode: str) -> None:
        mode = mode.upper()
        if mode not in const.ALL_MODES:
            raise ValueError(f"unsupported mode: {mode}")
        await self._write(group_id, {"Mode": mode})

    async def set_temperature(self, group_id: str, celsius: float) -> None:
        if not math.isfinite(celsius):
            raise ValueError("temperature must be finite")
        await self._write(group_id, {"SetTemp": f"{celsius:g}"})

    async def set_fan_speed(self, group_id: str, speed: str) -> None:
        speed = speed.upper()
        if speed not in const.ALL_FAN_SPEEDS:
            raise ValueError(f"unsupported fan speed: {speed}")
        await self._write(group_id, {"FanSpeed": speed})

    async def set_air_direction(self, group_id: str, direction: str) -> None:
        direction = direction.upper()
        if direction not in const.AIR_DIR_VALUES:
            raise ValueError(f"unsupported air direction: {direction}")
        await self._write(group_id, {"AirDirection": direction})

    async def set_local_remote(self, group_id: str, permit: bool) -> None:
        await self._write(
            group_id, {"RemoCon": const.REMOCON_PERMIT if permit else const.REMOCON_PROHIBIT}
        )

    @property
    def transport_name(self) -> str:
        return "websocket" if self._uses_ws else "http"
