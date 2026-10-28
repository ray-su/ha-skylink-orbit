"""Home Assistant and local protocol fixtures."""

import asyncio

import pytest

from .broker import Broker

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations):
    """Make the integration discoverable in Home Assistant tests."""


@pytest.fixture
async def broker():
    peer = Broker()
    server = await asyncio.start_server(peer.handle, "127.0.0.1", 0)
    peer.port = server.sockets[0].getsockname()[1]
    yield peer
    server.close()
    await server.wait_closed()
    await peer.disconnect()


@pytest.fixture
async def orbit_client(aiohttp_server, broker):
    from aiohttp import ClientSession, web

    from custom_components.skylink_orbit.orbitclient import OrbitClient

    async def login(request):
        return web.json_response({"result": 0, "acc_no": "account-test"})

    app = web.Application()
    app.router.add_post("/login", login)
    server = await aiohttp_server(app)
    async with ClientSession() as session:
        client = OrbitClient(
            session,
            "owner@example.invalid",
            "synthetic-password",
            login_url=str(server.make_url("/login")),
            broker_host="127.0.0.1",
            broker_port=broker.port,
            tls_context=None,
            timeout=0.5,
            reconnect_delay=0.02,
        )
        yield client
        await client.close()
