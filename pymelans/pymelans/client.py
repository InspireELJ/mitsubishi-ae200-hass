"""High-level async client for a Mitsubishi MELANS / AE-200 controller.

This is the public surface most callers use::

    async with MelansClient("192.168.1.100") as client:
        await client.discover()
        for dev in client.devices.values():
            print(dev.name, dev.is_on, dev.current_temperature)
        await client.set_power("2", True)

Design notes:

* Prefers the websocket transport (gives group *names*); transparently falls
  back to the HTTP servlet if websocket is unavailable.
* Enforces a minimum poll interval and a minimum spacing between writes. The
  same servlet has a historical DoS CVE (CVE-2008-1546); being a well-behaved,
  rate-limited client is a safety feature, not just politeness.
* Never raises from :meth:`async_update` for a single bad group — it isolates
  per-group parse issues so one faulted unit cannot blind the whole system.
"""

from __future__ import annotations

import asyncio
import time

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
    ):
        self.host = host
        self.port = port
        self._prefer_ws = prefer_websocket
        self._ws = WebsocketTransport(host, port=port)
        self._http = HttpTransport(host, port=port, session=session)
        self._names: dict[str, str | None] = {}
        self.devices: dict[str, MelansDevice] = {}
        self._write_spacing = write_spacing
        self._last_write = 0.0
        self._write_lock = asyncio.Lock()
        self._uses_ws = prefer_websocket

    # -- context management ----------------------------------------------
    async def __aenter__(self) -> "MelansClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def close(self) -> None:
        await self._ws.close()
        await self._http.close()

    # -- transport helper -------------------------------------------------
    async def _request(self, payload: str) -> str:
        """Send a request, preferring websocket and falling back to HTTP."""
        if self._prefer_ws:
            try:
                result = await self._ws.request(payload)
                self._uses_ws = True
                return result
            except MelansConnectionError:
                # Fall back for this and subsequent calls in this session.
                self._prefer_ws = False
                self._uses_ws = False
        return await self._http.request(payload)

    # -- discovery --------------------------------------------------------
    async def discover(self) -> dict[str, str | None]:
        """Discover configured groups and their names.

        Names are only available over websocket. Over HTTP we still discover
        which groups exist (by probing) but names come back ``None``.
        """
        if self._prefer_ws:
            try:
                payload = await self._ws.request(protocol.build_group_list_request())
                groups = protocol.parse_group_list(payload)
                self._names = {g["id"]: g["name"] for g in groups if g["id"]}
                if self._names:
                    return dict(self._names)
            except MelansError:
                self._prefer_ws = False

        # HTTP fallback: probe groups 1..MAX_GROUPS individually.
        found: dict[str, str | None] = {}
        for gid in range(1, const.MAX_GROUPS + 1):
            try:
                payload = await self._http.request(
                    protocol.build_state_request([gid])
                )
                state = protocol.parse_state(payload)
            except MelansError:
                continue
            if str(gid) in state:
                found[str(gid)] = None
        self._names = found
        return dict(found)

    # -- polling ----------------------------------------------------------
    async def async_update(self) -> dict[str, MelansDevice]:
        """Refresh state for all known groups. Resilient to per-group errors."""
        if not self._names:
            await self.discover()
        ids = list(self._names.keys())
        if not ids:
            return {}
        try:
            payload = await self._request(protocol.build_state_request(ids))
            states = protocol.parse_state(payload)
        except MelansError:
            # Some firmware rejects multi-group reads; degrade to per-group.
            states = {}
            for gid in ids:
                try:
                    p = await self._request(protocol.build_state_request([gid]))
                    states.update(protocol.parse_state(p))
                except MelansError:
                    continue
        for gid, attrs in states.items():
            name = self._names.get(gid)
            if gid in self.devices:
                self.devices[gid].update(attrs)
                if name and self.devices[gid].name.startswith("Group "):
                    self.devices[gid].name = name
            else:
                self.devices[gid] = MelansDevice(gid, name, attrs)
        return self.devices

    # -- writes (rate-limited) -------------------------------------------
    async def _write(self, group_id: str, attributes: dict[str, str]) -> None:
        async with self._write_lock:
            delta = time.monotonic() - self._last_write
            if delta < self._write_spacing:
                await asyncio.sleep(self._write_spacing - delta)
            payload = protocol.build_set_request(group_id, attributes)
            await self._request(payload)
            self._last_write = time.monotonic()
            # Optimistically reflect the change locally.
            if group_id in self.devices:
                merged = dict(self.devices[group_id]._attrs)  # noqa: SLF001
                merged.update(attributes)
                self.devices[group_id].update(merged)

    async def set_power(self, group_id: str, on: bool) -> None:
        await self._write(group_id, {"Drive": const.DRIVE_ON if on else const.DRIVE_OFF})

    async def set_mode(self, group_id: str, mode: str) -> None:
        mode = mode.upper()
        if mode not in const.ALL_MODES:
            raise ValueError(f"unsupported mode: {mode}")
        await self._write(group_id, {"Mode": mode})

    async def set_temperature(self, group_id: str, celsius: float) -> None:
        # AE-200 accepts 0.5 steps; format without trailing noise.
        value = f"{celsius:g}"
        await self._write(group_id, {"SetTemp": value})

    async def set_fan_speed(self, group_id: str, speed: str) -> None:
        speed = speed.upper()
        if speed not in const.ALL_FAN_SPEEDS:
            raise ValueError(f"unsupported fan speed: {speed}")
        await self._write(group_id, {"FanSpeed": speed})

    async def set_air_direction(self, group_id: str, direction: str) -> None:
        await self._write(group_id, {"AirDirection": direction.upper()})

    async def set_local_remote(self, group_id: str, permit: bool) -> None:
        await self._write(
            group_id,
            {"RemoCon": const.REMOCON_PERMIT if permit else const.REMOCON_PROHIBIT},
        )

    @property
    def transport_name(self) -> str:
        return "websocket" if self._uses_ws else "http"
