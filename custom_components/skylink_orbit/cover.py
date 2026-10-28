"""Status covers; directional motion has not been established by the protocol."""

from homeassistant.components.cover import (
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import OrbitConfigEntry
from .coordinator import OrbitCoordinator
from .entity import OrbitEntity, setup_doors
from .orbitclient.models import Door


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
    _attr_supported_features = CoverEntityFeature(0)

    def __init__(self, coordinator: OrbitCoordinator, door: Door) -> None:
        super().__init__(coordinator, door, "cover")
        self._attr_name = door.name if door.channel else None

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
        return {"orbit_door_state": self.door.state if self.door else None}
