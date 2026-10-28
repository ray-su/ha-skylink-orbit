"""Account diagnostics with no raw packets or configuration data."""

from typing import Any

from homeassistant.core import HomeAssistant

from . import OrbitConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: OrbitConfigEntry
) -> dict[str, Any]:
    return entry.runtime_data.client.diagnostics()
