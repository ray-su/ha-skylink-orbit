"""State-only transport/input regressions for the release candidate."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.skylink_orbit.orbitclient import OrbitClient
from custom_components.skylink_orbit.orbitclient.errors import CommandRejected

from .test_position_trust import blocked, feed, push, snapshot


async def packet(client, topic, payload, retain=False):
    async def messages():
        yield SimpleNamespace(topic="fixture/" + topic, payload=payload, retain=retain)

    async with asyncio.timeout(2) as deadline:
        await client._consume(SimpleNamespace(messages=messages()), "fixture", deadline)


async def test_retained_replay_not_discovery_or_current_position():
    client = OrbitClient(None, "", "", tls_context=None)
    client._mqtt = AsyncMock()
    for topic, data in [snapshot({"door": 4}), push({"door": 4})]:
        await packet(client, topic, json.dumps({"data": data}).encode(), True)
    assert not client.connected and not client.doors
    await feed(client, [snapshot({"door": 1})])
    client._snapshot_ready = asyncio.Event()
    for topic, data in [snapshot({"door": 4}), push({"door": 4})]:
        await packet(client, topic, json.dumps({"data": data}).encode(), True)
    assert not client._snapshot_ready.is_set() and client.doors[0].state == 1
    client._mqtt.publish.assert_not_awaited()


@pytest.mark.parametrize(
    "payload", [b"not json", b"{}", b'{"data":[]}', b'{"data":{"reported":{}}}']
)
async def test_unattributable_update_is_missing_history(payload):
    client = OrbitClient(None, "", "", tls_context=None)
    client._mqtt = AsyncMock()
    await packet(client, "update/result", payload)
    await feed(client, [snapshot({"door": 4})])
    assert client.doors[0].position_issue == "history_lost"
    await blocked(client)


async def test_malformed_row_alongside_valid_door_disables_motion():
    client = OrbitClient(None, "", "", tls_context=None)
    client._mqtt = AsyncMock()
    await feed(client, [("get/result", [None, *snapshot({"door": 4})[1]])])
    await blocked(client)


async def test_push_model_remap_cannot_enable_another_activation_key():
    client = OrbitClient(None, "", "", tls_context=None)
    client._mqtt = AsyncMock()
    await feed(
        client,
        [
            snapshot({"door": 4}),
            (
                "update/result",
                {
                    "hub_id": "test",
                    "type": "NVMini",
                    "reported": {"mdev": {"firstdoor": {"door": 4}}},
                },
            ),
        ],
    )
    assert (
        client.doors[0].channel == "A"
        and client.doors[0].position_issue == "conflicting_reports"
    )
    with pytest.raises(CommandRejected):
        await client.activate("test", "A")
    client._mqtt.publish.assert_not_awaited()
