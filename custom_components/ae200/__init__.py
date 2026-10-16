"""The Mitsubishi AE-200 / EW-50 integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .const import CONF_PREFER_WEBSOCKET, DEFAULT_PORT, DOMAIN
from .coordinator import AE200Coordinator
from .services import async_setup_services

PLATFORMS: list[Platform] = [
    Platform.CLIMATE,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register actions even when no controller entry is currently loaded."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Mitsubishi AE-200 from a config entry."""
    coordinator = AE200Coordinator(
        hass,
        entry,
        host=entry.data[CONF_HOST],
        port=entry.data.get(CONF_PORT, DEFAULT_PORT),
        prefer_websocket=entry.data.get(CONF_PREFER_WEBSOCKET, True),
        scan_interval=entry.options.get("scan_interval"),
    )
    try:
        await coordinator.async_config_entry_first_refresh()
        await coordinator.client.async_load_topology()
        await coordinator.client.async_load_controller_metadata()
        await coordinator.client.async_load_function_list()
    except Exception:
        await coordinator.client.close()
        raise

    registry = dr.async_get(hass)
    identifiers = {(DOMAIN, coordinator.host)}
    get_by_identifier = getattr(registry, "async_get_device_by_identifier", None)
    if callable(get_by_identifier):
        existing = get_by_identifier(
            identifier=(DOMAIN, coordinator.host), config_entry_id=entry.entry_id
        )
    else:
        # Home Assistant 2025.12 has only the global identifier lookup.
        existing = registry.async_get_device(identifiers=identifiers)
    device_info: dr.DeviceInfo = {
        "identifiers": identifiers,
        "name": "Mitsubishi centralised controller",
        "manufacturer": "Mitsubishi Electric",
    }
    metadata = coordinator.client.controller_metadata
    if metadata is not None and metadata.model:
        device_info["model"] = metadata.model
    elif existing is None:
        device_info["model"] = "AE-200 / AE-50 / EW-50"
    if metadata is not None and metadata.version:
        device_info["sw_version"] = metadata.version
    controller = registry.async_get_or_create(config_entry_id=entry.entry_id, **device_info)
    coordinator.controller_device_id = controller.id

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator: AE200Coordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()
    return unloaded


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when options change (e.g. scan interval)."""
    await hass.config_entries.async_reload(entry.entry_id)
