"""Account-wide snapshot conflicts must not become later motion authority."""

import asyncio

import pytest

from custom_components.skylink_orbit.orbitclient import OrbitClient
from custom_components.skylink_orbit.orbitclient.errors import CommandRejected

from .test_motion_safety import reconnect
from .test_review_regressions import PREFIX, stop, update

pytestmark = pytest.mark.usefixtures("socket_enabled")


def add_other(broker):
    broker.snapshot.append(
        {
            "hub_id": "other",
            "type": "GDO",
            "online": True,
            "reported": {"mdev": {"door": 4}},
        }
    )


async def push(client, broker, data):
    event = asyncio.Event()
    unsubscribe = client.subscribe(event.set)
    try:
        await broker.send(PREFIX + "/update/result", {"data": data})
        async with asyncio.timeout(1):
            await event.wait()
    finally:
        unsubscribe()


async def other_noop(client, broker):
    await client.set_closed("other", None, True)
    assert (await broker.published.get())[0].endswith("/get")
    assert broker.published.empty()


async def assert_all_motion_blocked(client, broker, channel=None):
    for closed in (False, True, None):
        with pytest.raises(CommandRejected, match="Conflicting door reports"):
            if closed is None:
                await client.activate("hub-test", channel)
            else:
                await client.set_closed("hub-test", channel, closed)
        assert broker.published.empty()


@pytest.mark.parametrize(
    "scenario", ["same-door-noop", "other-door-refresh", "idle-reconnect"]
)
@pytest.mark.parametrize("state", [1, 4])
async def test_both_directions_cannot_launder_conflicts(
    orbit_client, broker, scenario, state
):
    add_other(broker)
    stale = 4 if state == 1 else 1
    broker.snapshot[0]["reported"]["mdev"]["door"] = stale
    await orbit_client.start()
    await broker.published.get()
    await update(orbit_client, broker, state)
    if scenario == "same-door-noop":
        with pytest.raises(CommandRejected, match="Conflicting door reports"):
            await orbit_client.set_closed("hub-test", None, stale == 4)
        assert (await broker.published.get())[0].endswith("/get")
    elif scenario == "other-door-refresh":
        await other_noop(orbit_client, broker)
    else:
        await reconnect(orbit_client, broker)
    assert orbit_client._snapshot_conflicts == {"hub-test"}
    await assert_all_motion_blocked(orbit_client, broker)


@pytest.mark.parametrize("state", [0, 2, 3, 5, 6, 7, None])
async def test_other_door_snapshot_cannot_erase_moving_or_unknown(
    orbit_client, broker, state
):
    add_other(broker)
    await orbit_client.start()
    await broker.published.get()
    await update(orbit_client, broker, state)
    await other_noop(orbit_client, broker)
    await assert_all_motion_blocked(orbit_client, broker)


@pytest.mark.parametrize(
    "data",
    [
        {"hub_id": "hub-test", "reported": {"link": {"online": False}}},
        {"hub_id": "hub-test", "reported": {"mdev": None}},
        {"hub_id": "hub-test", "type": "unsupported"},
        {"hub_id": "hub-test", "type": None},
    ],
)
async def test_snapshot_cannot_erase_offline_missing_or_changed_model(
    orbit_client, broker, data
):
    add_other(broker)
    await orbit_client.start()
    await broker.published.get()
    await push(orbit_client, broker, data)
    await other_noop(orbit_client, broker)
    await assert_all_motion_blocked(orbit_client, broker)


@pytest.mark.parametrize("missing", [False, True])
async def test_removed_or_offline_snapshot_stays_blocked_after_return(
    orbit_client, broker, missing
):
    add_other(broker)
    await orbit_client.start()
    await broker.published.get()
    original = broker.snapshot[0]
    if missing:
        broker.snapshot.pop(0)
    else:
        original["online"] = False
    await other_noop(orbit_client, broker)
    if missing:
        broker.snapshot.insert(0, original)
    else:
        original["online"] = True
    await other_noop(orbit_client, broker)
    await assert_all_motion_blocked(orbit_client, broker)


async def test_conflict_cannot_be_cleared_by_push_snapshot_or_reconnect(
    orbit_client, broker
):
    add_other(broker)
    await orbit_client.start()
    await broker.published.get()
    await update(orbit_client, broker, 1)
    await other_noop(orbit_client, broker)
    # Even a later matching terminal push does not silently revoke the conflict.
    await update(orbit_client, broker, 4)
    await push(
        orbit_client,
        broker,
        {"hub_id": "hub-test", "reported": {"mdev": {"rssi": -55, "light": 1}}},
    )
    await other_noop(orbit_client, broker)
    await reconnect(orbit_client, broker)
    await assert_all_motion_blocked(orbit_client, broker)


@pytest.mark.parametrize("during", [False, True])
@pytest.mark.parametrize("closed", [False, True])
async def test_legitimate_push_is_reconciled_without_false_conflict(
    orbit_client, broker, during, closed
):
    await orbit_client.start()
    await broker.published.get()
    if during:
        broker.before_snapshot = {
            "hub_id": "hub-test",
            "reported": {"mdev": {"door": 1}},
        }
    else:
        await update(orbit_client, broker, 1)
        broker.snapshot[0]["reported"]["mdev"]["door"] = 1
    task = asyncio.create_task(orbit_client.set_closed("hub-test", None, closed))
    try:
        async with asyncio.timeout(1):
            assert (await broker.published.get())[0].endswith("/get")
            if closed:
                assert (await broker.published.get())[0].endswith("/desire")
                await update(orbit_client, broker, 3)
            await task
        assert not orbit_client._snapshot_conflicts
        assert broker.published.empty()
    finally:
        await stop(task)


async def test_reconnect_buffered_push_can_establish_legitimate_new_state(
    orbit_client, broker
):
    await orbit_client.start()
    await broker.published.get()
    await update(orbit_client, broker, 1)
    broker.before_snapshot = {"hub_id": "hub-test", "reported": {"mdev": {"door": 4}}}
    await reconnect(orbit_client, broker)
    assert not orbit_client._snapshot_conflicts
    await orbit_client.set_closed("hub-test", None, True)
    assert (await broker.published.get())[0].endswith("/get")
    assert broker.published.empty()


async def test_metadata_only_changes_do_not_block(orbit_client, broker):
    await orbit_client.start()
    await broker.published.get()
    broker.snapshot[0]["hub_name"] = "New garage name"
    broker.snapshot[0]["reported"]["mdev"].update(rssi=-55, light=1)
    await orbit_client.set_closed("hub-test", None, True)
    assert (await broker.published.get())[0].endswith("/get")
    assert not orbit_client._snapshot_conflicts
    assert broker.published.empty()


async def test_conflicting_model_channel_remap_cannot_bypass_native_guard(
    orbit_client, broker
):
    add_other(broker)
    await orbit_client.start()
    await broker.published.get()
    broker.snapshot[0] = {
        "hub_id": "hub-test",
        "type": "NOVA",
        "online": True,
        "firstdoor_name": "Left",
        "reported": {"mdev": {"firstdoor": {"door": 4}}},
    }
    await other_noop(orbit_client, broker)
    with pytest.raises(CommandRejected, match="Conflicting door reports"):
        await orbit_client.activate("hub-test", "A")
    assert broker.published.empty()


async def test_conflict_is_scoped_to_affected_hub(orbit_client, broker):
    add_other(broker)
    await orbit_client.start()
    await broker.published.get()
    await update(orbit_client, broker, 1)
    await other_noop(orbit_client, broker)
    task = asyncio.create_task(orbit_client.set_closed("other", None, False))
    try:
        async with asyncio.timeout(1):
            assert (await broker.published.get())[0].endswith("/get")
            topic, payload, _ = await broker.published.get()
            assert topic.endswith("/desire") and payload["data"]["hub_id"] == "other"
            await push(
                orbit_client,
                broker,
                {"hub_id": "other", "reported": {"mdev": {"door": 0}}},
            )
            await task
        await assert_all_motion_blocked(orbit_client, broker)
    finally:
        await stop(task)


@pytest.mark.parametrize("model", [None, "unsupported"])
async def test_hidden_model_cannot_hide_contradictory_door_reports(
    orbit_client, broker, model
):
    add_other(broker)
    await orbit_client.start()
    await broker.published.get()
    await update(orbit_client, broker, 1)
    await push(orbit_client, broker, {"hub_id": "hub-test", "type": model})
    broker.snapshot[0]["type"] = model
    await other_noop(orbit_client, broker)
    # Even though the malformed model temporarily yields no Door object,
    # snapshot Closed must not replace pushed Open without latching conflict.
    await push(orbit_client, broker, {"hub_id": "hub-test", "type": "GDO"})
    await assert_all_motion_blocked(orbit_client, broker)


async def test_invalid_model_removal_cannot_reset_hub_identity(orbit_client, broker):
    add_other(broker)
    await orbit_client.start()
    await broker.published.get()
    await push(
        orbit_client,
        broker,
        {
            "hub_id": "hub-test",
            "type": None,
            "online": False,
            "reported": {"mdev": {"door": None}},
        },
    )
    broker.snapshot[0] = {"hub_id": "hub-test", "online": False}
    await other_noop(orbit_client, broker)
    broker.snapshot[0] = {
        "hub_id": "hub-test",
        "type": "GDO",
        "online": True,
        "reported": {"mdev": {"door": 4}},
    }
    await other_noop(orbit_client, broker)
    await assert_all_motion_blocked(orbit_client, broker)


async def test_conflict_during_pending_pulse_is_reported_as_error(orbit_client, broker):
    add_other(broker)
    await orbit_client.start()
    await broker.published.get()
    task = asyncio.create_task(orbit_client.set_closed("hub-test", None, False))
    try:
        async with asyncio.timeout(1):
            assert (await broker.published.get())[0].endswith("/get")
            assert (await broker.published.get())[0].endswith("/desire")
            broker.snapshot[0]["reported"]["mdev"]["door"] = 1
            await other_noop(orbit_client, broker)
            with pytest.raises(CommandRejected, match="Conflicting door reports"):
                await task
        assert orbit_client._motions[("hub-test", None)].uncertain
        await update(orbit_client, broker, 1)
        assert orbit_client._motions[("hub-test", None)].uncertain
        assert broker.published.empty()
    finally:
        await stop(task)


async def test_initial_buffered_discovery_does_not_false_latch(orbit_client, broker):
    broker.before_snapshot = {"hub_id": "hub-test", "reported": {"mdev": {"rssi": -55}}}
    await orbit_client.start()
    await broker.published.get()
    assert not orbit_client._snapshot_conflicts
    await orbit_client.set_closed("hub-test", None, True)
    assert (await broker.published.get())[0].endswith("/get")
    assert broker.published.empty()


async def test_manual_new_client_is_explicit_recovery_boundary(orbit_client, broker):
    await orbit_client.start()
    await broker.published.get()
    await update(orbit_client, broker, 1)
    with pytest.raises(CommandRejected, match="Conflicting door reports"):
        await orbit_client.set_closed("hub-test", None, True)
    assert (await broker.published.get())[0].endswith("/get")
    await assert_all_motion_blocked(orbit_client, broker)
    await orbit_client.close()
    # Models attended inspection + HA reload creating a new client, not a retry.
    replacement = OrbitClient(
        orbit_client._session,
        "owner@example.invalid",
        "synthetic-password",
        login_url=orbit_client._login_url,
        broker_host="127.0.0.1",
        broker_port=broker.port,
        tls_context=None,
        timeout=0.5,
    )
    try:
        await replacement.start()
        assert (await broker.published.get())[0].endswith("/get")
        assert not replacement._snapshot_conflicts
        await replacement.set_closed("hub-test", None, True)
        assert (await broker.published.get())[0].endswith("/get")
        assert broker.published.empty()
    finally:
        await replacement.close()
