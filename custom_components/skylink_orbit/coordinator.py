"""Share a single push session across all doors on an account."""

import logging
from collections.abc import Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN
from .orbitclient import OrbitClient
from .orbitclient.errors import AuthenticationError
from .orbitclient.models import Door


class OrbitCoordinator(DataUpdateCoordinator[dict[tuple[str, str | None], Door]]):
    """Push-only state; reconnection is owned by the protocol client."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: OrbitClient
    ) -> None:
        super().__init__(
            hass, logging.getLogger(__name__), config_entry=entry, name=DOMAIN
        )
        self.client = client
        self._entry = entry
        self._unsubscribe = client.subscribe(self._updated)
        self._closed = False
        self._stop_listener: Callable[[], None] | None = hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP, self._stop
        )
        self._updated()

    async def _stop(self, event) -> None:
        self._stop_listener = None  # The one-shot listener already removed itself.
        await self.async_shutdown()

    @callback
    def _updated(self) -> None:
        if self.client.connected:
            self.async_set_updated_data({door.key: door for door in self.client.doors})
        else:
            self.async_set_update_error(UpdateFailed("Orbit session unavailable"))
            if isinstance(self.client.error, AuthenticationError):
                self._entry.async_start_reauth(self.hass)

    async def async_shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._unsubscribe()
        if self._stop_listener is not None:
            self._stop_listener()
            self._stop_listener = None
        await self.client.close()
        await super().async_shutdown()
