"""Skylink Orbit Home integration."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import orbitclient
from .coordinator import OrbitCoordinator
from .orbitclient.errors import AuthenticationError, OrbitError

type OrbitConfigEntry = ConfigEntry[OrbitCoordinator]
PLATFORMS = [Platform.COVER, Platform.BUTTON, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: OrbitConfigEntry) -> bool:
    """Establish the account session before exposing entities."""
    client = orbitclient.OrbitClient(
        async_get_clientsession(hass),
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )
    try:
        await client.start()
        if client.account_id != entry.unique_id:
            raise AuthenticationError("Account changed")
    except AuthenticationError as err:
        await client.close()
        raise ConfigEntryAuthFailed(str(err)) from err
    except OrbitError as err:
        await client.close()
        raise ConfigEntryNotReady(str(err)) from err
    coordinator = OrbitCoordinator(hass, entry, client)
    entry.runtime_data = coordinator
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await coordinator.async_shutdown()
        raise
    return True


async def async_unload_entry(hass: HomeAssistant, entry: OrbitConfigEntry) -> bool:
    """Unload entities and release the account session."""
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_shutdown()
    return unloaded
