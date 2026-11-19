"""Guarded direction requests against the controlled MQTT peer only."""

import asyncio

import pytest

from custom_components.skylink_orbit.orbitclient.errors import CommandRejected

pytestmark = pytest.mark.usefixtures("socket_enabled")
PREFIX = "skylink/things/client/account-test"


async def test_open_refreshes_then_sends_one_pulse(orbit_client, broker):
    await orbit_client.start()
    await broker.published.get()
    task = asyncio.create_task(orbit_client.set_closed("hub-test", None, False))
    async with asyncio.timeout(1):
        assert (await broker.published.get())[0] == PREFIX + "/get"
        topic, payload, header = await broker.published.get()
    assert topic == PREFIX + "/desire" and header == 0x30
    assert payload["data"]["desired"]["mdev"]["ctrlgdo"]["cmd"] == 0
    with pytest.raises(CommandRejected, match="pending"):
        await orbit_client.activate("hub-test", None)
    with pytest.raises(CommandRejected, match="pending"):
        await orbit_client.set_closed("hub-test", None, False)
    await broker.send(
        PREFIX + "/update/result",
        {"data": {"hub_id": "hub-test", "reported": {"mdev": {"door": 0}}}},
    )
    await task
    assert broker.published.empty()


@pytest.mark.parametrize("closed,state", [(False, 1), (True, 4)])
async def test_already_at_target_refreshes_without_pulse(
    orbit_client, broker, closed, state
):
    broker.snapshot[0]["reported"]["mdev"]["door"] = state
    await orbit_client.start()
    await broker.published.get()
    # A consistent at-target refresh is a no-op, not a new source of authority.
    await orbit_client.set_closed("hub-test", None, closed)
    assert (await broker.published.get())[0] == PREFIX + "/get"
    assert broker.published.empty()


@pytest.mark.parametrize("state", [0, 2, 3, 5, 6, 7, None])
@pytest.mark.parametrize("closed", [False, True])
async def test_ambiguous_states_never_pulse(orbit_client, broker, state, closed):
    await orbit_client.start()
    await broker.published.get()
    broker.snapshot[0]["reported"]["mdev"]["door"] = state
    with pytest.raises(CommandRejected, match="ambiguous"):
        await orbit_client.set_closed("hub-test", None, closed)
    assert (await broker.published.get())[0] == PREFIX + "/get"
    assert broker.published.empty()


async def test_missing_refresh_response_never_uses_cached_closed(orbit_client, broker):
    await orbit_client.start()
    await broker.published.get()
    broker.respond = False
    with pytest.raises(CommandRejected, match="no pulse sent"):
        await orbit_client.set_closed("hub-test", None, False)
    assert (await broker.published.get())[0] == PREFIX + "/get"
    assert broker.published.empty()
    assert not orbit_client._pending


async def test_newer_push_wins_over_refresh_snapshot(orbit_client, broker):
    await orbit_client.start()
    await broker.published.get()
    broker.before_snapshot = {"hub_id": "hub-test", "reported": {"mdev": {"door": 3}}}
    with pytest.raises(CommandRejected, match="ambiguous"):
        await orbit_client.set_closed("hub-test", None, False)
    assert orbit_client.doors[0].state == 3
    assert (await broker.published.get())[0] == PREFIX + "/get"
    assert broker.published.empty()


@pytest.mark.parametrize("missing", [False, True])
async def test_refresh_removed_or_offline_door_never_pulses(
    orbit_client, broker, missing
):
    await orbit_client.start()
    await broker.published.get()
    if missing:
        broker.snapshot = []
    else:
        broker.snapshot[0]["online"] = False
    with pytest.raises(CommandRejected, match="unavailable"):
        await orbit_client.set_closed("hub-test", None, False)
    assert (await broker.published.get())[0] == PREFIX + "/get"
    assert broker.published.empty()


async def test_close_gdo_pulse_and_timeout_no_replay(orbit_client, broker):
    broker.snapshot[0]["reported"]["mdev"]["door"] = 1
    await orbit_client.start()
    await broker.published.get()
    task = asyncio.create_task(orbit_client.set_closed("hub-test", None, True))
    async with asyncio.timeout(1):
        assert (await broker.published.get())[0] == PREFIX + "/get"
        topic, payload, header = await broker.published.get()
    assert topic == PREFIX + "/desire" and header == 0x30
    control = payload["data"]["desired"]["mdev"]["ctrlgdo"]
    assert control["cmd"] == 0 and "position" not in control
    with pytest.raises(CommandRejected, match="outcome unknown"):
        await task
    # Reconnect must not replay motion, only request discovery.
    broker.respond = False
    changed = asyncio.Event()
    orbit_client.subscribe(changed.set)
    await broker.disconnect()
    async with asyncio.timeout(1):
        while orbit_client.connected:
            await changed.wait()
            changed.clear()
    broker.respond = True
    async with asyncio.timeout(2):
        while not orbit_client.connected:
            await changed.wait()
            changed.clear()
    await orbit_client.close()
    while not broker.published.empty():
        assert (await broker.published.get())[0] == PREFIX + "/get"


async def test_disconnect_during_refresh_never_pulses(orbit_client, broker):
    await orbit_client.start()
    await broker.published.get()
    broker.respond = False
    task = asyncio.create_task(orbit_client.set_closed("hub-test", None, False))
    async with asyncio.timeout(1):
        assert (await broker.published.get())[0] == PREFIX + "/get"
    await broker.disconnect()
    broker.respond = True
    with pytest.raises(CommandRejected):
        await task
    await orbit_client.close()
    while not broker.published.empty():
        assert (await broker.published.get())[0] == PREFIX + "/get"


async def test_cancel_during_refresh_releases_channel_without_pulse(
    orbit_client, broker
):
    await orbit_client.start()
    await broker.published.get()
    broker.respond = False
    task = asyncio.create_task(orbit_client.set_closed("hub-test", None, False))
    async with asyncio.timeout(1):
        assert (await broker.published.get())[0] == PREFIX + "/get"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not orbit_client._pending
    assert orbit_client._snapshot_ready is None
    assert broker.published.empty()
