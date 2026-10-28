"""Hub light control and state through the existing Orbit push session."""

from typing import Any

from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import OrbitConfigEntry
from .coordinator import OrbitCoordinator
from .entity import OrbitHubEntity, setup_hubs
from .orbitclient.errors import OrbitError
from .orbitclient.models import Hub


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OrbitConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    entry.async_on_unload(
        setup_hubs(
            entry.runtime_data,
            async_add_entities,
            OrbitLight,
            lambda hub: hub.light is not None,
        )
    )


class OrbitLight(OrbitHubEntity, LightEntity):
    _attr_name = "Light"
    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes = {ColorMode.ONOFF}

    def __init__(self, coordinator: OrbitCoordinator, hub: Hub) -> None:
        super().__init__(coordinator, hub, "light")

    @property
    def is_on(self) -> bool | None:
        return self.hub.light if self.hub else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set_light(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set_light(False)

    async def _set_light(self, on: bool) -> None:
        if not self.available:
            raise ServiceValidationError("Light is unavailable")
        try:
            await self.coordinator.client.set_light(self._hub_id, on)
        except OrbitError as err:
            raise HomeAssistantError(str(err)) from err
