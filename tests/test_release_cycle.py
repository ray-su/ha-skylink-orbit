"""Regression cases from the release review; in-memory Orbit peers only."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.skylink_orbit.orbitclient import OrbitClient
from custom_components.skylink_orbit.orbitclient.client import _UPDATE_LIMIT
from custom_components.skylink_orbit.orbitclient.errors import AuthenticationError

from .test_position_trust import blocked, feed, push, snapshot


def entry_for(hass):
    entry = MockConfigEntry(
        domain="skylink_orbit",
        unique_id="fixture",
        data={"username": "local", "password": "local"},
        options={"enable_directional_controls": True},
    )
    entry.add_to_hass(hass)
    return entry


async def login(client):
    client.account_id = "fixture"
    return client.account_id


@pytest.mark.parametrize(
    "case,reason",
    [
        ("open", "conflicting_reports"),
        ("overflow", "history_lost"),
        ("metadata", None),
        ("consistent", None),
        ("empty", None),
    ],
)
async def test_actual_automatic_setup_retry(hass, case, reason):
    entry = entry_for(hass)
    attempts = []
    loaded = asyncio.Event()
    unsub = entry.async_on_state_change(
        lambda: loaded.set() if entry.state is ConfigEntryState.LOADED else None
    )

    async def connection(client):
        attempts.append(client)
        if len(attempts) == 1:
            events = {
                "open": [push({"door": 1})],
                "overflow": [push({"rssi": -50})] * _UPDATE_LIMIT + [push({"door": 1})],
                "metadata": [push({"rssi": -50})],
                "consistent": [push({"door": 4})],
                "empty": [],
            }[case]
            await feed(client, events)
            raise ConnectionError("fixture: interrupted discovery")
        client._mqtt = AsyncMock()
        await feed(client, [snapshot({"door": 4})])
        await asyncio.Event().wait()

    with (
        patch.object(OrbitClient, "login", login),
        patch.object(OrbitClient, "_connection", connection),
    ):
        try:
            await hass.config_entries.async_setup(entry.entry_id)
            assert entry.state is ConfigEntryState.SETUP_RETRY
            if hass.state is CoreState.running:
                async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=1))
            else:
                hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
            async with asyncio.timeout(5):
                await loaded.wait()
            await hass.async_block_till_done()
            assert len(attempts) == 2 and attempts[0] is not attempts[1]
            client = attempts[1]
            assert client.doors[0].position_issue == reason
            assert hass.states.async_all("cover")[0].state == (
                "unavailable" if reason else "closed"
            )
            if reason:
                await blocked(client)
                assert await hass.config_entries.async_reload(entry.entry_id)
                await hass.async_block_till_done()
                assert attempts[-1].doors[0].position_issue is None
                assert hass.states.async_all("cover")[0].state == "closed"
        finally:
            unsub()
            if entry.state is ConfigEntryState.LOADED:
                await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("code", [None, 2, 5, 6, 7])
@pytest.mark.parametrize("initial", [False, True])
async def test_homekit_unrepresentable_and_recovery(hass, code, initial):
    pytest.importorskip("pyhap")
    from homeassistant.components.homekit.accessories import get_accessory
    from homeassistant.components.homekit.iidmanager import AccessoryIIDStorage
    from homeassistant.helpers.event import async_track_state_change_event
    from pyhap.loader import Loader

    entry = entry_for(hass)

    async def connection(client):
        client._mqtt = AsyncMock()
        await feed(client, [snapshot({"door": code if initial else 4})])
        await asyncio.Event().wait()

    with (
        patch.object(OrbitClient, "login", login),
        patch.object(OrbitClient, "_connection", connection),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        state = hass.states.async_all("cover")[0]
        driver = MagicMock()
        driver.loader = Loader()
        driver.iid_storage = AccessoryIIDStorage(hass, "fixture")
        driver.iid_storage.store = MagicMock()
        accessory = get_accessory(hass, driver, state, 2, {})
        unsub = async_track_state_change_event(
            hass, [state.entity_id], accessory.async_update_event_state_callback
        )
        client = entry.runtime_data.client
        try:
            if not initial:
                assert accessory.available and accessory.char_current_state.value == 1
                await feed(client, [push({"door": code})])
                await hass.async_block_till_done()
            assert (
                hass.states.get(state.entity_id).state == "unavailable"
                and not accessory.available
            )
            await feed(client, [push({"door": 1})])
            await hass.async_block_till_done()
            assert (
                hass.states.get(state.entity_id).state == "open"
                and accessory.available
                and accessory.char_current_state.value == 0
            )
            client._mqtt.publish.assert_not_awaited()
        finally:
            unsub()
            await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize(
    "first,last,conflict", [(1, 4, True), (4, 1, True), (4, 4, False)]
)
async def test_duplicate_snapshot_rows(first, last, conflict):
    client = OrbitClient(None, "", "", tls_context=None)
    client._mqtt = AsyncMock()
    await feed(
        client,
        [("get/result", snapshot({"door": first})[1] + snapshot({"door": last})[1])],
    )
    assert client.doors[0].position_issue == (
        "conflicting_reports" if conflict else None
    )
    if conflict:
        await blocked(client)


async def test_different_login_cannot_populate_retry_history():
    client = OrbitClient(None, "", "", tls_context=None, expected_account_id="other")
    with (
        patch.object(OrbitClient, "login", login),
        patch.object(client, "_connection", new_callable=AsyncMock) as connection,
    ):
        with pytest.raises(AuthenticationError):
            await client.start()
        connection.assert_not_awaited()
