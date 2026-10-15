"""Shared entity base for AE-200 entities."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ._pymelans import MelansDevice
from .const import DOMAIN, MANUFACTURER
from .coordinator import AE200Coordinator


class AE200Entity(CoordinatorEntity[AE200Coordinator]):
    """Base entity tying an AE-200 group to an HA device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: AE200Coordinator, group_id: str) -> None:
        super().__init__(coordinator)
        self._group_id = group_id

    @property
    def device(self) -> MelansDevice | None:
        return self.coordinator.data.get(self._group_id)

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.device is not None
            and self.coordinator.client.is_group_available(self._group_id)
        )

    @property
    def device_info(self) -> DeviceInfo:
        dev = self.device
        name = dev.name if dev else f"Group {self._group_id}"
        kind = dev.kind if dev else "unit"
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self.coordinator.host}:{self._group_id}")},
            name=name,
            manufacturer=MANUFACTURER,
            model=f"AE-200 {kind} (group {self._group_id})",
            via_device=(DOMAIN, self.coordinator.host),
        )


class AE200ControllerEntity(CoordinatorEntity[AE200Coordinator]):
    """Controller health must remain readable when operational entities fail."""

    _attr_has_entity_name = True

    @property
    def available(self) -> bool:
        return True

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self.coordinator.host)})
