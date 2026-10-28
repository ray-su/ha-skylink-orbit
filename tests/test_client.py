"""Exercise the public client against controlled network services."""

import pytest
from aiohttp import ClientSession, web

from custom_components.skylink_orbit.orbitclient import OrbitClient

pytestmark = pytest.mark.usefixtures("socket_enabled")


async def test_signed_login_accepts_vendor_zero_result(aiohttp_server, monkeypatch):
    monkeypatch.setattr("time.time", lambda: 1700000000.999)

    async def login(request):
        assert request.query["cmd"] == "act_login"
        assert request.headers["REQ-TIMESTAMP"] == "1700000000000"
        assert request.headers["REQ-DATA"] == "owner@example.invalid"
        assert request.headers["REQ-SIGNATURE"] == "3d39dd4d390b32610a07fb74253ec8a9"
        assert await request.json() == {
            "username": "owner@example.invalid",
            "password": "synthetic-password",
            "app_sys": "gcm",
            "app_brand": "00",
            "pn_token": "",
        }
        return web.Response(text='{"result":00,"acc_no":"account-test"}')

    app = web.Application()
    app.router.add_post("/login", login)
    server = await aiohttp_server(app)
    async with ClientSession() as session:
        client = OrbitClient(
            session,
            "owner@example.invalid",
            "synthetic-password",
            login_url=str(server.make_url("/login")),
        )
        assert await client.login() == "account-test"


@pytest.mark.parametrize(
    ("response", "status", "error"),
    [
        ('{"result":23,"message":"private vendor text"}', 200, "LoginRejected"),
        ('{"result":0,"acc_no":"other/+/account"}', 200, "ProtocolError"),
        ("not JSON", 200, "ProtocolError"),
        ("denied", 401, "AuthenticationError"),
        ("unavailable", 503, "CannotConnect"),
    ],
)
async def test_login_errors_are_classified_and_sanitized(
    aiohttp_server, response, status, error
):
    from custom_components.skylink_orbit.orbitclient import errors

    async def login(request):
        return web.Response(text=response, status=status)

    app = web.Application()
    app.router.add_post("/login", login)
    server = await aiohttp_server(app)
    async with ClientSession() as session:
        client = OrbitClient(
            session,
            "owner@example.invalid",
            "secret",
            login_url=str(server.make_url("/login")),
        )
        with pytest.raises(getattr(errors, error)) as raised:
            await client.login()
        assert "private vendor text" not in str(raised.value)


async def test_initial_snapshot_discovers_a_closed_door(orbit_client, broker):
    await orbit_client.start()
    assert orbit_client.connected
    door = orbit_client.doors[0]
    assert (door.hub_id, door.channel, door.state, door.online) == (
        "hub-test",
        None,
        4,
        True,
    )
    assert set(broker.subscriptions) == {
        "skylink/things/client/account-test/get/result",
        "skylink/things/client/account-test/update/result",
    }


async def test_snapshot_keeps_updates_and_missing_fields_do_not_reset_state(
    orbit_client, broker
):
    import asyncio

    broker.before_snapshot = {"hub_id": "hub-test", "reported": {"mdev": {"door": 1}}}
    await orbit_client.start()
    assert orbit_client.doors[0].state == 1
    changed = asyncio.Event()
    unsubscribe = orbit_client.subscribe(changed.set)
    await broker.send(
        "skylink/things/client/account-test/update/result",
        {
            "data": {
                "hub_id": "hub-test",
                "reported": {"mdev": {"rssi": -45}, "link": {"online": False}},
            }
        },
    )
    async with asyncio.timeout(1):
        await changed.wait()
    assert orbit_client.doors[0].state == 1
    assert not orbit_client.doors[0].online
    unsubscribe()


async def test_reconnect_replaces_old_snapshot_and_close_stops_retries(
    orbit_client, broker
):
    import asyncio

    await orbit_client.start()
    changed = asyncio.Event()
    orbit_client.subscribe(changed.set)
    broker.respond = False
    await broker.disconnect()
    async with asyncio.timeout(1):
        while orbit_client.connected:
            await changed.wait()
            changed.clear()
    assert not orbit_client.connected
    broker.snapshot = []
    broker.respond = True
    async with asyncio.timeout(2):
        while not orbit_client.connected:
            await changed.wait()
            changed.clear()
    assert orbit_client.doors == []
    await orbit_client.close()
    assert not orbit_client.connected
    async with asyncio.timeout(1):
        await asyncio.gather(*list(broker.tasks))
    assert broker.connections == []


@pytest.mark.parametrize(
    ("refused", "respond", "error"),
    [(4, True, "AuthenticationError"), (0, False, "CannotConnect")],
)
async def test_failed_mqtt_start_is_bounded_and_cleans_up(
    orbit_client, broker, refused, respond, error
):
    import asyncio

    from custom_components.skylink_orbit.orbitclient import errors

    broker.connack = refused
    broker.respond = respond
    with pytest.raises(getattr(errors, error)):
        await orbit_client.start()
    assert not orbit_client.connected
    async with asyncio.timeout(1):
        await asyncio.gather(*list(broker.tasks))
    assert broker.connections == []


async def test_activation_is_one_nonretained_pulse_and_never_queued(
    orbit_client, broker
):
    import asyncio

    from custom_components.skylink_orbit.orbitclient.errors import CommandRejected

    await orbit_client.start()
    await broker.published.get()  # discovery
    command = asyncio.create_task(orbit_client.activate("hub-test", None))
    async with asyncio.timeout(1):
        topic, payload, header = await broker.published.get()
    assert topic == "skylink/things/client/account-test/desire"
    assert header == 0x30  # QoS 0 and retain false
    control = payload["data"]["desired"]["mdev"]["ctrlgdo"]
    assert control["cmd"] == 0 and "position" not in control
    assert isinstance(control["ts"], str)
    with pytest.raises(CommandRejected):
        await orbit_client.activate("hub-test", None)
    await broker.send(
        "skylink/things/client/account-test/update/result",
        {"data": {"hub_id": "hub-test", "reported": {"mdev": {"door": 0}}}},
    )
    await command
    await orbit_client.close()
    with pytest.raises(CommandRejected):
        await orbit_client.activate("hub-test", None)
    assert broker.published.empty()


async def test_ambiguous_activation_timeout_does_not_retry(orbit_client, broker):
    from custom_components.skylink_orbit.orbitclient.errors import CommandRejected

    await orbit_client.start()
    await broker.published.get()
    with pytest.raises(CommandRejected, match="outcome unknown"):
        await orbit_client.activate("hub-test", None)
    assert (await broker.published.get())[0].endswith("/desire")
    assert broker.published.empty()


async def test_nova_channels_remain_distinct_and_absent_state_is_unknown(
    orbit_client, broker
):
    broker.snapshot = [
        {
            "hub_id": "nova-test",
            "type": "NOVA",
            "online": True,
            "firstdoor_name": "Left",
            "seconddoor_name": "Right",
            "reported": {"mdev": {"firstdoor": {"door": 4}, "seconddoor": {"GS": 1}}},
        },
        {"hub_id": "unsupported", "type": "SMO"},
    ]
    await orbit_client.start()
    assert [(door.hub_id, door.channel, door.state) for door in orbit_client.doors] == [
        ("nova-test", "A", 4),
        ("nova-test", "B", None),
    ]


async def test_diagnostics_allowlist_excludes_raw_packets(orbit_client, broker):
    import json

    broker.snapshot.append(
        {
            "hub_id": "private-hub",
            "type": "unknown-secret",
            "location": "private-location",
            "password": "private-password",
        }
    )
    await orbit_client.start()
    result = orbit_client.diagnostics()
    assert result["device_families"]["unsupported"] == 1
    assert all(
        secret not in json.dumps(result)
        for secret in (
            "account-test",
            "private-hub",
            "unknown-secret",
            "private-location",
            "private-password",
            "hub-test",
            "Garage",
        )
    )


async def test_snapshot_online_flag_wins_until_link_update(orbit_client, broker):
    import asyncio

    broker.snapshot[0]["online"] = False
    broker.snapshot[0]["reported"]["link"] = {"online": True}
    await orbit_client.start()
    assert not orbit_client.doors[0].online
    updated = asyncio.Event()
    orbit_client.subscribe(updated.set)
    await broker.send(
        "skylink/things/client/account-test/update/result",
        {"data": {"hub_id": "hub-test", "reported": {"link": {"online": True}}}},
    )
    async with asyncio.timeout(1):
        await updated.wait()
    assert orbit_client.doors[0].online


async def test_hub_telemetry_uses_existing_push_without_more_requests(
    orbit_client, broker
):
    import asyncio

    broker.snapshot[0]["reported"]["mdev"].update(light=0, rssi=-61)
    await orbit_client.start()
    assert (orbit_client.hubs[0].light, orbit_client.hubs[0].rssi) == (False, -61)
    assert (await broker.published.get())[0].endswith("/get")
    updated = asyncio.Event()
    orbit_client.subscribe(updated.set)
    for patch, expected in [
        ({"light": 1}, (True, -61)),
        ({"rssi": -73}, (True, -73)),
        ({"door": 0}, (True, -73)),
        ({"light": None, "rssi": 0}, (None, None)),
        ({"light": "1", "rssi": True}, (None, None)),
    ]:
        updated.clear()
        await broker.send(
            "skylink/things/client/account-test/update/result",
            {"data": {"hub_id": "hub-test", "reported": {"mdev": patch}}},
        )
        async with asyncio.timeout(1):
            await updated.wait()
        assert (orbit_client.hubs[0].light, orbit_client.hubs[0].rssi) == expected
    # No subscription or snapshot requests added by telemetry updates.
    assert len(broker.subscriptions) == 2
    assert broker.published.empty()


@pytest.mark.parametrize("target, opcode", [(True, 3), (False, 4)])
async def test_light_command_waits_for_reported_state(
    orbit_client, broker, target, opcode
):
    import asyncio

    from custom_components.skylink_orbit.orbitclient.errors import CommandRejected

    broker.snapshot[0]["reported"]["mdev"]["light"] = int(not target)
    await orbit_client.start()
    await broker.published.get()
    command = asyncio.create_task(orbit_client.set_light("hub-test", target))
    async with asyncio.timeout(1):
        topic, payload, header = await broker.published.get()
    assert topic == "skylink/things/client/account-test/desire"
    assert header == 0x30
    assert payload["data"]["hub_id"] == "hub-test"
    control = payload["data"]["desired"]["mdev"]["ctrlgdo"]
    assert set(control) == {"cmd", "ts"}
    assert control["cmd"] == opcode and control["ts"].isdigit()
    with pytest.raises(CommandRejected, match="pending"):
        await orbit_client.set_light("hub-test", target)
    updated = asyncio.Event()
    orbit_client.subscribe(updated.set)
    await broker.send("skylink/things/client/account-test/update/result", payload)
    async with asyncio.timeout(1):
        await updated.wait()
    assert not command.done()  # A desired-state echo is not a light report.
    assert orbit_client.hubs[0].light is not target
    await broker.send(
        "skylink/things/client/account-test/update/result",
        {"data": {"hub_id": "hub-test", "reported": {"mdev": {"light": int(target)}}}},
    )
    await command
    assert orbit_client.hubs[0].light is target
    await orbit_client.set_light("hub-test", target)  # Already reported: no-op.
    assert broker.published.empty()


@pytest.mark.parametrize("failure", ["timeout", "disconnect", "offline", "cancel"])
async def test_light_failure_never_retries_or_polls(orbit_client, broker, failure):
    import asyncio

    from custom_components.skylink_orbit.orbitclient.errors import CommandRejected

    broker.snapshot[0]["reported"]["mdev"]["light"] = 0
    await orbit_client.start()
    await broker.published.get()
    command = asyncio.create_task(orbit_client.set_light("hub-test", True))
    async with asyncio.timeout(1):
        assert (await broker.published.get())[0].endswith("/desire")
    if failure == "disconnect":
        await broker.disconnect()
    elif failure == "offline":
        await broker.send(
            "skylink/things/client/account-test/update/result",
            {"data": {"hub_id": "hub-test", "reported": {"link": {"online": False}}}},
        )
    elif failure == "cancel":
        command.cancel()
    with pytest.raises(
        asyncio.CancelledError if failure == "cancel" else CommandRejected
    ):
        await command
    if failure == "disconnect":
        changed = asyncio.Event()
        unsubscribe = orbit_client.subscribe(changed.set)
        async with asyncio.timeout(1):
            while not orbit_client.connected:
                await changed.wait()
                changed.clear()
        unsubscribe()
        assert (await broker.published.get())[0].endswith("/get")  # Reconnect only.
    if failure in ("timeout", "cancel"):
        await orbit_client.set_light("hub-test", False)  # Pending lock was released.
    await orbit_client.close()
    assert broker.published.empty()


@pytest.mark.parametrize(
    "device",
    [
        {"type": "GDO", "online": True, "reported": {"mdev": {"door": 4}}},
        {"type": "GDO", "online": False, "reported": {"mdev": {"light": 0}}},
        {
            "type": "SMO",
            "online": True,
            "reported": {"mdev": {"light": 0, "rssi": -50}},
        },
    ],
)
async def test_light_rejects_absent_capability_offline_and_unknown_family(
    orbit_client, broker, device
):
    from custom_components.skylink_orbit.orbitclient.errors import CommandRejected

    broker.snapshot = [{"hub_id": "hub-test", **device}]
    await orbit_client.start()
    await broker.published.get()
    with pytest.raises(CommandRejected):
        await orbit_client.set_light("hub-test", True)
    assert broker.published.empty()


async def test_hub_snapshot_replacement_discards_stale_telemetry(orbit_client, broker):
    import asyncio

    broker.snapshot[0]["reported"]["mdev"].update(light=1, rssi=-50)
    await orbit_client.start()
    assert orbit_client.hubs[0].light is True
    broker.snapshot[0]["reported"]["mdev"] = {"door": 4}
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
    assert orbit_client.hubs[0].light is None
    assert orbit_client.hubs[0].rssi is None
