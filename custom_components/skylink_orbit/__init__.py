"""Skylink Orbit Home integration."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import orbitclient
from .const import DOMAIN
from .coordinator import OrbitCoordinator
from .orbitclient.client import DiscoveryHistory
from .orbitclient.errors import AuthenticationError, OrbitError

type OrbitConfigEntry = ConfigEntry[OrbitCoordinator]
PLATFORMS = [Platform.COVER, Platform.BUTTON, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: OrbitConfigEntry) -> bool:
    """Establish the account session before exposing entities."""
    attempts = hass.data.setdefault(DOMAIN, {})
    client = orbitclient.OrbitClient(
        async_get_clientsession(hass),
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        history=attempts.setdefault(entry.entry_id, DiscoveryHistory()),
        expected_account_id=entry.unique_id,
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
    entry.async_on_unload(entry.add_update_listener(async_reload_options))
    # The loaded client now owns the history; failed attempts are not resets.
    attempts.pop(entry.entry_id)
    return True


async def async_remove_entry(hass: HomeAssistant, entry: OrbitConfigEntry) -> None:
    """Forget a removed entry even if its setup never succeeded."""
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)


async def async_reload_options(hass: HomeAssistant, entry: OrbitConfigEntry) -> None:
    """Apply the user's explicit control option without changing entity identity."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: OrbitConfigEntry) -> bool:
    """Unload entities and release the account session."""
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_shutdown()
    return unloaded
