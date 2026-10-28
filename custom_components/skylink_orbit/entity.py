"""Door identity, availability and dynamic discovery shared by platforms."""

from collections.abc import Callable

from homeassistant.core import callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import OrbitCoordinator
from .orbitclient.models import Door


class OrbitEntity(CoordinatorEntity[OrbitCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: OrbitCoordinator, door: Door, kind: str) -> None:
        super().__init__(coordinator)
        self._key = door.key
        self._attr_unique_id = f"{coordinator.client.account_id}:{door.hub_id}:{door.channel or 'single'}:{kind}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, door.hub_id)},
            name=door.hub_name,
            manufacturer="Skylink",
            model=door.model,
        )

    @property
    def door(self) -> Door | None:
        return self.coordinator.data.get(self._key)

    @property
    def available(self) -> bool:
        return super().available and self.door is not None and self.door.online


def setup_doors(
    coordinator: OrbitCoordinator,
    async_add_entities: AddEntitiesCallback,
    factory: Callable[[OrbitCoordinator, Door], OrbitEntity],
) -> Callable[[], None]:
    """Add newly discovered channels; removed channels become unavailable."""
    known: set[tuple[str, str | None]] = set()

    @callback
    def discover() -> None:
        entities = []
        for key, door in coordinator.data.items():
            if key not in known:
                known.add(key)
                entities.append(factory(coordinator, door))
        if entities:
            async_add_entities(entities)

    discover()
    return coordinator.async_add_listener(discover)
