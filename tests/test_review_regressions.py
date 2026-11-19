"""Astra review probes: desired behavior; failures document unfixed defects.

Only the repository's synthetic localhost HTTP/MQTT fixtures are used.
"""

import asyncio
import copy
from contextlib import suppress

import pytest

from custom_components.skylink_orbit.orbitclient.errors import CommandRejected

pytestmark = pytest.mark.usefixtures("socket_enabled")
PREFIX = "skylink/things/client/account-test"


async def update(client, broker, state):
    event = asyncio.Event()
    unsubscribe = client.subscribe(event.set)
    try:
        await broker.send(
            PREFIX + "/update/result",
            {"data": {"hub_id": "hub-test", "reported": {"mdev": {"door": state}}}},
        )
        async with asyncio.timeout(1):
            await event.wait()
    finally:
        unsubscribe()


async def stop(task):
    task.cancel()
    with suppress(asyncio.CancelledError, CommandRejected):
        await task


async def test_late_snapshot_cannot_authorize_a_later_request(orbit_client, broker):
    await orbit_client.start()
    await broker.published.get()
    old_snapshot = copy.deepcopy(broker.snapshot)
    broker.respond = False
    # Request one times out without a response.
    with pytest.raises(CommandRejected):
        await orbit_client.set_closed("hub-test", None, False)
    assert (await broker.published.get())[0].endswith("/get")
    # The opener then reports fully open.
    await update(orbit_client, broker, 1)
    assert orbit_client.doors[0].state == 1
    # Recovery is deliberately fail-closed: a second request is rejected before
    # another /get can be sent, so there is no waiter for the old response to fool.
    task = asyncio.create_task(orbit_client.set_closed("hub-test", None, False))
    try:
        await broker.send(PREFIX + "/get/result", {"data": old_snapshot})
        with pytest.raises(CommandRejected, match="ordering uncertain"):
            await task
        await asyncio.sleep(0.05)
        assert broker.published.empty()
        assert orbit_client.doors[0].state == 1
    finally:
        await stop(task)


async def test_open_does_not_succeed_when_report_says_closing(orbit_client, broker):
    await orbit_client.start()
    await broker.published.get()
    task = asyncio.create_task(orbit_client.set_closed("hub-test", None, False))
    try:
        async with asyncio.timeout(1):
            assert (await broker.published.get())[0].endswith("/get")
            assert (await broker.published.get())[0].endswith("/desire")
        await update(orbit_client, broker, 3)
        with pytest.raises(CommandRejected):
            await task
    finally:
        await stop(task)


async def test_delayed_duplicate_open_does_not_pulse_again_during_motion(
    orbit_client, broker
):
    await orbit_client.start()
    await broker.published.get()
    first = asyncio.create_task(orbit_client.set_closed("hub-test", None, False))
    async with asyncio.timeout(1):
        assert (await broker.published.get())[0].endswith("/get")
        assert (await broker.published.get())[0].endswith("/desire")
    await update(orbit_client, broker, 0)
    await first
    # Past the two-second cooldown, but no terminal-state report was received.
    await asyncio.sleep(2.05)
    assert orbit_client.doors[0].state == 0
    # Cloud snapshot remains stale CLOSED while real reported progress was OPENING.
    second = asyncio.create_task(orbit_client.set_closed("hub-test", None, False))
    emitted = []
    try:
        with suppress(CommandRejected):
            await second
        while not broker.published.empty():
            emitted.append((await broker.published.get())[0])
        assert not any(t.endswith("/desire") for t in emitted), (
            "Repeated OPEN sent a second pulse without observed completion"
        )
    finally:
        await stop(second)


async def test_non_utf8_packet_does_not_kill_reconnect_loop(orbit_client, broker):
    import struct

    from tests.broker import packet

    await orbit_client.start()
    topic = (PREFIX + "/update/result").encode()
    wire = packet(0x30, struct.pack("!H", len(topic)) + topic + b"\xff")
    for writer in broker.connections:
        writer.write(wire)
        await writer.drain()
    await asyncio.sleep(0.05)
    assert orbit_client._task is not None and not orbit_client._task.done(), (
        "Malformed packet terminated client task"
    )


async def test_deeply_nested_json_does_not_kill_reconnect_loop(orbit_client, broker):
    import struct
    import sys

    from tests.broker import packet

    await orbit_client.start()
    topic = (PREFIX + "/update/result").encode()
    nesting = sys.getrecursionlimit() + 100
    payload = b'{"data":' + b"[" * nesting + b"0" + b"]" * nesting + b"}"
    wire = packet(0x30, struct.pack("!H", len(topic)) + topic + payload)
    for writer in broker.connections:
        writer.write(wire)
        await writer.drain()
    await asyncio.sleep(0.05)
    task = orbit_client._task
    dead = task is not None and task.done()
    error = task.exception() if dead else None
    # Retrieve the error and close sockets so a known defect doesn't mask the test.
    if dead:
        with suppress(RecursionError):
            await orbit_client.close()
        orbit_client._task = None
    assert not dead, (
        f"Malformed packet permanently terminated client: {type(error).__name__}"
    )


async def test_oversized_numeric_json_does_not_kill_reconnect_loop(
    orbit_client, broker
):
    import struct

    from tests.broker import packet

    await orbit_client.start()
    topic = (PREFIX + "/update/result").encode()
    # json.loads raises ValueError, which is already handled.
    wire = packet(
        0x30, struct.pack("!H", len(topic)) + topic + b'{"data":' + b"9" * 5000 + b"}"
    )
    for writer in broker.connections:
        writer.write(wire)
        await writer.drain()
    await asyncio.sleep(0.05)
    assert orbit_client._task is not None and not orbit_client._task.done()
