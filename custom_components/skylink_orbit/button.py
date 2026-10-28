"""Opt-in native activation; no open/close or stop semantics are inferred."""

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import OrbitConfigEntry
from .coordinator import OrbitCoordinator
from .entity import OrbitEntity, setup_doors
from .orbitclient.errors import OrbitError
from .orbitclient.models import Door


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OrbitConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    entry.async_on_unload(
        setup_doors(entry.runtime_data, async_add_entities, OrbitButton)
    )


class OrbitButton(OrbitEntity, ButtonEntity):
    _attr_entity_registry_enabled_default = False
    _attr_icon = "mdi:garage"

    def __init__(self, coordinator: OrbitCoordinator, door: Door) -> None:
        super().__init__(coordinator, door, "activate")
        self._attr_name = f"Activate {door.name}" if door.channel else "Activate door"

    async def async_press(self) -> None:
        if not self.available:
            raise ServiceValidationError("Door is unavailable")
        try:
            await self.coordinator.client.activate(*self._key)
        except OrbitError as err:
            raise HomeAssistantError(str(err)) from err
