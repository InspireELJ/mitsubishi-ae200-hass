"""DataUpdateCoordinator for the AE-200 integration."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from ._pymelans import MelansClient, MelansDevice, MelansError
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN, MIN_SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


class AE200Coordinator(DataUpdateCoordinator[dict[str, MelansDevice]]):
    """Polls the controller and shares typed device state with all platforms."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        *,
        host: str,
        port: int,
        prefer_websocket: bool,
        scan_interval: int | None,
    ) -> None:
        interval = max(scan_interval or DEFAULT_SCAN_INTERVAL, MIN_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {host}",
            update_interval=timedelta(seconds=interval),
        )
        self.host = host
        self.client = MelansClient(
            host,
            port=port,
            prefer_websocket=prefer_websocket,
            session=async_get_clientsession(hass),
            stale_after=max(interval * 3, 90),
        )
        self._discovered = False

    async def _async_update_data(self) -> dict[str, MelansDevice]:
        try:
            if not self._discovered:
                await self.client.discover()
                await self.client.async_load_capabilities()
                self._discovered = True
            return await self.client.async_update()
        except MelansError as err:
            raise UpdateFailed(f"Error communicating with AE-200: {err}") from err

    async def async_shutdown(self) -> None:
        await super().async_shutdown()
        await self.client.close()
