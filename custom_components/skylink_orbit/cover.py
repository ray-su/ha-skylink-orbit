"""Garage status and guarded, pulse-backed directional controls."""

from typing import Any

from homeassistant.components.cover import (
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import OrbitConfigEntry
from .coordinator import OrbitCoordinator
from .entity import OrbitEntity, setup_doors
from .orbitclient.errors import OrbitError
from .orbitclient.models import Door, device_family


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OrbitConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    entry.async_on_unload(
        setup_doors(entry.runtime_data, async_add_entities, OrbitCover)
    )


class OrbitCover(OrbitEntity, CoverEntity):
    _attr_device_class = CoverDeviceClass.GARAGE

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self._set_closed(False)

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self._set_closed(True)

    async def _set_closed(self, closed: bool) -> None:
        if not self.supported_features:
            raise ServiceValidationError(
                "Directional control is not enabled for this door"
            )
        if not self.available:
            raise ServiceValidationError("Door is unavailable")
        try:
            await self.coordinator.client.set_closed(*self._key, closed)
        except OrbitError as err:
            raise HomeAssistantError(str(err)) from err

    def __init__(self, coordinator: OrbitCoordinator, door: Door) -> None:
        super().__init__(coordinator, door, "cover")
        self._attr_supported_features = (
            CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE
            if coordinator.directional_enabled and device_family(door.model) == "GDO"
            else CoverEntityFeature(0)
        )
        self._attr_name = door.name if door.channel else None

    @property
    def available(self) -> bool:
        # HA HomeKit retains its old position on `unknown`, not `unavailable`.
        if not super().available or self.is_closed is None:
            return False
        # Partial is not fully Open. HAP has no partial garage-door position.
        return not self.supported_features or (
            self.door is not None and self.door.state in (0, 1, 3, 4)
        )

    @property
    def is_closed(self) -> bool | None:
        if self.door is None or self.door.state in (None, 5, 7):
            return None
        return self.door.state == 4

    @property
    def is_opening(self) -> bool:
        return self.door is not None and self.door.state == 0

    @property
    def is_closing(self) -> bool:
        return self.door is not None and self.door.state == 3

    @property
    def extra_state_attributes(self) -> dict[str, int | None]:
        return {
            "orbit_door_state": self.door.state
            if self.door and not self.door.position_issue
            else None
        }
