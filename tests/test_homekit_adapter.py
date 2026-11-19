"""Real HA HomeKit adapter, mocked driver/client; no bridge or device sockets.

Optional dependencies: see the HomeKit test command in README.md.
"""

from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .test_integration import client_factory

__all__ = ["client_factory"]

pytest.importorskip("pyhap")


async def test_homekit_routes_garage_and_resynchronizes_rejected_command(
    hass, client_factory
):
    from homeassistant.components.homekit.accessories import get_accessory
    from homeassistant.components.homekit.iidmanager import AccessoryIIDStorage
    from homeassistant.components.homekit.type_covers import GarageDoorOpener
    from pyhap.loader import Loader

    from custom_components.skylink_orbit.orbitclient.errors import CommandRejected

    entry = MockConfigEntry(
        domain="skylink_orbit",
        unique_id="account-test",
        data={"username": "owner@example.invalid", "password": "secret"},
        options={"enable_directional_controls": True},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    state = hass.states.async_all("cover")[0]
    driver = MagicMock()
    driver.loader = Loader()
    driver.iid_storage = AccessoryIIDStorage(hass, "synthetic-homekit")
    driver.iid_storage.store = MagicMock()  # No persistence or running HAP server.
    accessory = get_accessory(hass, driver, state, 2, {})
    assert isinstance(accessory, GarageDoorOpener)
    assert accessory.char_current_state.value == 1  # HAP Closed
    client = client_factory[-1]
    accessory.set_state(0)  # HAP Open -> actual HA cover service -> fake Orbit client.
    await hass.async_block_till_done()
    assert client.direction_requests == [("hub-test", None, False)]
    client.doors = [replace(client.doors[0], state=1)]
    client.emit()
    await hass.async_block_till_done()
    accessory.async_update_state(hass.states.get(state.entity_id))
    assert accessory.char_current_state.value == 0
    accessory.set_state(1)
    await hass.async_block_till_done()
    assert client.direction_requests[-1] == ("hub-test", None, True)
    # Error path must resync optimistic HomeKit Closing back to reported Open.
    with patch.object(
        client, "set_closed", side_effect=CommandRejected("Motion uncertain")
    ):
        accessory.set_state(1)
        await hass.async_block_till_done()
    assert accessory.char_current_state.value == 0
    assert accessory.char_target_state.value == 0
    await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.usefixtures("socket_enabled")
@pytest.mark.parametrize("reason", ["conflicting_reports", "history_lost"])
async def test_disputed_position_unavailable_in_ha_and_homekit(
    hass, orbit_client, broker, reason
):
    from homeassistant.components.homekit.accessories import get_accessory
    from homeassistant.components.homekit.iidmanager import AccessoryIIDStorage
    from homeassistant.helpers.event import async_track_state_change_event
    from pyhap.loader import Loader

    from .test_review_regressions import update

    entry = MockConfigEntry(
        domain="skylink_orbit",
        unique_id="account-test",
        data={"username": "local", "password": "local"},
        options={"enable_directional_controls": True},
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.skylink_orbit.orbitclient.OrbitClient",
        return_value=orbit_client,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert (await broker.published.get())[0].endswith("/get")
    await update(orbit_client, broker, 1)
    await hass.async_block_till_done()
    state = hass.states.async_all("cover")[0]
    assert state.state == "open"
    driver = MagicMock()
    driver.loader = Loader()
    driver.iid_storage = AccessoryIIDStorage(hass, "local-trust-test")
    driver.iid_storage.store = MagicMock()
    accessory = get_accessory(hass, driver, state, 2, {})
    unsubscribe = async_track_state_change_event(
        hass, [state.entity_id], accessory.async_update_event_state_callback
    )
    try:
        assert accessory.available and accessory.char_current_state.value == 0
        if reason == "conflicting_reports":
            accessory.set_state(
                1
            )  # fresh response is stale Closed, against pushed Open
            await hass.async_block_till_done()
            assert (await broker.published.get())[0].endswith("/get")
        else:
            orbit_client._mark_history_lost()
        await hass.async_block_till_done()
        assert orbit_client.doors[0].position_issue == reason
        invalid = hass.states.get(state.entity_id)
        assert invalid.state == "unavailable"
        assert invalid.attributes.get("orbit_door_state") is None
        assert not accessory.available
        accessory.set_state(0)
        await hass.async_block_till_done()
        assert not accessory.available and broker.published.empty()
    finally:
        unsubscribe()
        await hass.config_entries.async_unload(entry.entry_id)
