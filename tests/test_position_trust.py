"""Discovery and bounded-history checks. No external sockets or physical targets."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.skylink_orbit.orbitclient import OrbitClient
from custom_components.skylink_orbit.orbitclient.client import (
    _UPDATE_LIMIT,
    _loss_aware_queue,
)
from custom_components.skylink_orbit.orbitclient.errors import CommandRejected


def push(mdev):
    return ("update/result", {"hub_id": "test", "reported": {"mdev": mdev}})


def snapshot(mdev):
    return (
        "get/result",
        [{"hub_id": "test", "type": "GDO", "online": True, "reported": {"mdev": mdev}}],
    )


async def feed(c, events):
    async def messages():
        for topic, data in events:
            yield SimpleNamespace(
                topic="fixture/" + topic, payload=json.dumps({"data": data}).encode()
            )

    async with asyncio.timeout(3) as deadline:
        await c._consume(SimpleNamespace(messages=messages()), "fixture", deadline)


async def blocked(c):
    for closed in (None, True, False):
        with pytest.raises(CommandRejected):
            if closed is None:
                await c.activate("test", None)
            else:
                await c.set_closed("test", None, closed)
    c._mqtt.publish.assert_not_awaited()


@pytest.fixture
def client():
    c = OrbitClient(None, "", "", tls_context=None)
    c._mqtt = AsyncMock()
    return c


@pytest.mark.parametrize(
    "first,candidate,conflict",
    [
        ({"door": 1}, {"door": 4}, True),
        ({"door": 4}, {"door": 1}, True),
        ({"door": None}, {"door": 4}, True),
        ({"door": 1}, {"door": 1}, False),
        ({"rssi": -55}, {"door": 4}, False),
        ({"firstdoor": {"door": 1}}, {"firstdoor": {"door": 4}, "door": 4}, True),
    ],
)
@pytest.mark.parametrize("phase", ["cold", "before-refresh", "interrupted-discovery"])
async def test_partial_model_evidence(client, first, candidate, conflict, phase):
    if phase == "before-refresh":
        await feed(client, [("get/result", []), push(first)])
        client._snapshot_ready = asyncio.Event()
        await feed(client, [snapshot(candidate)])
    elif phase == "interrupted-discovery":
        await feed(client, [push(first)])
        await feed(client, [snapshot(candidate)])
    else:
        await feed(client, [push(first), snapshot(candidate)])
    assert client.doors[0].position_issue == (
        "conflicting_reports" if conflict else None
    )
    if conflict:
        await blocked(client)


@pytest.mark.parametrize("phase", ["startup", "reconnect", "refresh"])
@pytest.mark.parametrize("overflow", [False, True])
async def test_bounded_log(client, phase, overflow):
    if phase != "startup":
        await feed(client, [snapshot({"door": 4})])
        if phase == "refresh":
            client._snapshot_ready = asyncio.Event()
        else:
            client.connected = False
    events = [push({"rssi": -55})] * _UPDATE_LIMIT
    if overflow:
        events.append(push({"door": 1}))
    await feed(client, [*events, snapshot({"door": 4})])
    assert client.doors[0].position_issue == ("history_lost" if overflow else None)
    if overflow:
        client.connected = False
        await feed(client, [snapshot({"door": 4}), push({"door": 4})])
        await blocked(client)
        assert client.diagnostics()["doors"][0]["position_issue"] == "history_lost"


async def test_transport_overflow_latches_and_notifies(client):
    await feed(client, [snapshot({"door": 4})])
    seen = []
    client.subscribe(lambda: seen.append(client.doors[0].position_issue))
    q = _loss_aware_queue(client._mark_history_lost)(maxsize=1)
    q.put_nowait(object())
    with pytest.raises(asyncio.QueueFull):
        q.put_nowait(object())
    assert seen == ["history_lost"]
    await blocked(client)
