"""Regression coverage for persistent motion guards and refresh poisoning."""

import asyncio
from contextlib import suppress

import pytest

from custom_components.skylink_orbit.orbitclient.errors import CommandRejected

from .test_review_regressions import PREFIX, update

pytestmark = pytest.mark.usefixtures("socket_enabled")


async def start_motion(client, broker, closed=False):
    task = asyncio.create_task(client.set_closed("hub-test", None, closed))
    async with asyncio.timeout(1):
        assert (await broker.published.get())[0].endswith("/get")
        assert (await broker.published.get())[0].endswith("/desire")
    return task


@pytest.mark.parametrize(
    "closed,progress,terminal", [(False, 0, 1), (True, 3, 4), (True, 5, 4)]
)
async def test_motion_guard_awaits_reported_completion(
    orbit_client, broker, closed, progress, terminal
):
    broker.snapshot[0]["reported"]["mdev"]["door"] = 1 if closed else 4
    await orbit_client.start()
    await broker.published.get()
    task = await start_motion(orbit_client, broker, closed)
    await update(orbit_client, broker, progress)
    await task
    # Method success is progress, not completion; native button shares the guard.
    assert ("hub-test", None) in orbit_client._motions
    await asyncio.sleep(2.05)
    with pytest.raises(CommandRejected, match="pending"):
        await orbit_client.activate("hub-test", None)
    with pytest.raises(CommandRejected, match="pending"):
        await orbit_client.set_closed("hub-test", None, closed)
    assert broker.published.empty()
    await update(orbit_client, broker, terminal)
    assert ("hub-test", None) not in orbit_client._motions
    broker.snapshot[0]["reported"]["mdev"]["door"] = terminal
    await orbit_client.set_closed("hub-test", None, closed)  # At target: no pulse.
    assert (await broker.published.get())[0].endswith("/get")
    assert broker.published.empty()


@pytest.mark.parametrize(
    "closed,wrong",
    [
        (False, 3),
        (False, 5),
        (False, 2),
        (False, 6),
        (False, 7),
        (False, None),
        (True, 0),
    ],
)
async def test_contradictory_or_unknown_progress_latches_uncertainty(
    orbit_client, broker, closed, wrong
):
    broker.snapshot[0]["reported"]["mdev"]["door"] = 1 if closed else 4
    await orbit_client.start()
    await broker.published.get()
    task = await start_motion(orbit_client, broker, closed)
    await update(orbit_client, broker, wrong)
    with pytest.raises(CommandRejected, match="Unexpected direction"):
        await task
    await update(orbit_client, broker, 4 if closed else 1)
    assert orbit_client._motions[("hub-test", None)].uncertain
    with pytest.raises(CommandRejected):
        await orbit_client.activate("hub-test", None)
    assert broker.published.empty()


@pytest.mark.parametrize("cancel", [False, True])
async def test_ambiguous_sent_pulse_cannot_be_retried(orbit_client, broker, cancel):
    await orbit_client.start()
    await broker.published.get()
    task = await start_motion(orbit_client, broker)
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(CommandRejected, match="outcome unknown"):
            await task
    await asyncio.sleep(2.05)
    await update(orbit_client, broker, 1)
    for operation in (
        orbit_client.activate("hub-test", None),
        orbit_client.set_closed("hub-test", None, False),
    ):
        with pytest.raises(CommandRejected, match="pending"):
            await operation
    assert broker.published.empty()


async def test_conflicting_terminal_snapshot_cannot_authorize_motion(
    orbit_client, broker
):
    await orbit_client.start()
    await broker.published.get()
    await update(orbit_client, broker, 1)
    # No timed-out prior request needed: stale cloud Closed contradicts known Open.
    with pytest.raises(CommandRejected, match="Conflicting door reports"):
        await orbit_client.set_closed("hub-test", None, False)
    assert (await broker.published.get())[0].endswith("/get")
    with pytest.raises(CommandRejected, match="Conflicting door reports"):
        await orbit_client.set_closed("hub-test", None, False)
    assert broker.published.empty()


async def test_other_door_snapshot_cannot_clear_motion_interlock(orbit_client, broker):
    broker.snapshot.append(
        {
            "hub_id": "other",
            "type": "GDO",
            "online": True,
            "reported": {"mdev": {"door": 4}},
        }
    )
    await orbit_client.start()
    await broker.published.get()
    task = await start_motion(orbit_client, broker)
    await update(orbit_client, broker, 0)
    await task
    broker.snapshot[0]["reported"]["mdev"]["door"] = 1
    await orbit_client.set_closed("other", None, True)
    assert (await broker.published.get())[0].endswith("/get")
    assert ("hub-test", None) in orbit_client._motions
    await update(orbit_client, broker, 1)
    assert ("hub-test", None) not in orbit_client._motions
    assert broker.published.empty()


async def reconnect(client, broker):
    broker.respond = False
    changed = asyncio.Event()
    unsubscribe = client.subscribe(changed.set)
    try:
        await broker.disconnect()
        async with asyncio.timeout(1):
            while client.connected:
                await changed.wait()
                changed.clear()
        broker.respond = True
        async with asyncio.timeout(2):
            while not client.connected:
                await changed.wait()
                changed.clear()
    finally:
        unsubscribe()
    while not broker.published.empty():
        assert (await broker.published.get())[0].endswith("/get")


async def test_motion_uncertainty_survives_automatic_reconnect(orbit_client, broker):
    await orbit_client.start()
    await broker.published.get()
    task = await start_motion(orbit_client, broker)
    await update(orbit_client, broker, 0)
    await task
    await reconnect(orbit_client, broker)
    await update(orbit_client, broker, 1)
    with pytest.raises(CommandRejected, match="pending"):
        await orbit_client.set_closed("hub-test", None, False)
    assert broker.published.empty()


@pytest.mark.parametrize("cancel", [False, True])
async def test_refresh_poison_survives_reconnect(orbit_client, broker, cancel):
    await orbit_client.start()
    await broker.published.get()
    broker.respond = False
    task = asyncio.create_task(orbit_client.set_closed("hub-test", None, False))
    async with asyncio.timeout(1):
        assert (await broker.published.get())[0].endswith("/get")
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(CommandRejected):
            await task
    await reconnect(orbit_client, broker)
    with pytest.raises(CommandRejected, match="ordering uncertain"):
        await orbit_client.set_closed("hub-test", None, False)
    assert broker.published.empty()


async def test_another_door_refresh_is_rejected_not_queued(orbit_client, broker):
    broker.snapshot.append(
        {
            "hub_id": "other",
            "type": "GDO",
            "online": True,
            "reported": {"mdev": {"door": 4}},
        }
    )
    await orbit_client.start()
    await broker.published.get()
    broker.respond = False
    task = asyncio.create_task(orbit_client.set_closed("hub-test", None, False))
    try:
        async with asyncio.timeout(1):
            assert (await broker.published.get())[0].endswith("/get")
        with pytest.raises(CommandRejected, match="busy"):
            await orbit_client.set_closed("other", None, False)
        assert broker.published.empty()
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


@pytest.mark.parametrize("family", ["NOVA", "NVMini"])
async def test_unverified_family_rejects_direction_but_preserves_native_button(
    orbit_client, broker, family
):
    broker.snapshot = [
        {
            "hub_id": "hub-test",
            "type": family,
            "online": True,
            "firstdoor_name": "Left",
            "reported": {"mdev": {"firstdoor": {"door": 4}}},
        }
    ]
    await orbit_client.start()
    await broker.published.get()
    with pytest.raises(CommandRejected, match="only enabled"):
        await orbit_client.set_closed("hub-test", "A", False)
    assert broker.published.empty()
    task = asyncio.create_task(orbit_client.activate("hub-test", "A"))
    async with asyncio.timeout(1):
        topic, payload, header = await broker.published.get()
    assert topic.endswith("/desire") and header == 0x30
    assert payload["data"]["desired"]["mdev"]["ctrlgdo"]["position"] == "A"
    await broker.send(
        PREFIX + "/update/result",
        {
            "data": {
                "hub_id": "hub-test",
                "reported": {"mdev": {"firstdoor": {"door": 0}}},
            }
        },
    )
    await task
    assert broker.published.empty()
