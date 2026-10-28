"""Last reported Wi-Fi signal; no polling or refresh requests."""

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import SIGNAL_STRENGTH_DECIBELS_MILLIWATT, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import OrbitConfigEntry
from .coordinator import OrbitCoordinator
from .entity import OrbitHubEntity, setup_hubs
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
            OrbitSignalSensor,
            lambda hub: hub.rssi is not None,
        )
    )


class OrbitSignalSensor(OrbitHubEntity, SensorEntity):
    _attr_name = "Wi-Fi signal"
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: OrbitCoordinator, hub: Hub) -> None:
        super().__init__(coordinator, hub, "wifi_signal")

    @property
    def native_value(self) -> int | None:
        return self.hub.rssi if self.hub else None
