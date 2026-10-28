"""Native door activation and explicit, stateless GDO light commands."""

from functools import partial

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import OrbitConfigEntry
from .coordinator import OrbitCoordinator
from .entity import OrbitEntity, OrbitHubEntity, setup_doors, setup_hubs
from .orbitclient.errors import OrbitError
from .orbitclient.models import Door, Hub


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OrbitConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    entry.async_on_unload(
        setup_doors(entry.runtime_data, async_add_entities, OrbitButton)
    )
    for on in (True, False):
        entry.async_on_unload(
            setup_hubs(
                entry.runtime_data,
                async_add_entities,
                partial(OrbitLightButton, on=on),
                lambda hub: hub.supports_light_commands,
            )
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


class OrbitLightButton(OrbitHubEntity, ButtonEntity):
    """A press sends a command; it does not represent the light's state."""

    def __init__(self, coordinator: OrbitCoordinator, hub: Hub, *, on: bool) -> None:
        super().__init__(coordinator, hub, "light_on" if on else "light_off")
        self._on = on
        self._attr_name = "Light on" if on else "Light off"
        self._attr_icon = "mdi:lightbulb-on" if on else "mdi:lightbulb-off"

    async def async_press(self) -> None:
        if not self.available:
            raise ServiceValidationError("Light controls are unavailable")
        try:
            await self.coordinator.client.set_light(self._hub_id, self._on)
        except OrbitError as err:
            raise HomeAssistantError(str(err)) from err
