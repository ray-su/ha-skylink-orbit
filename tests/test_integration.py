"""Behavior through Home Assistant config flows, registries, and services."""

from unittest.mock import patch

import pytest
from homeassistant.config_entries import SOURCE_USER

from custom_components.skylink_orbit.orbitclient.models import Door


class FakeClient:
    account_id = "account-test"
    connected = False
    error = None

    def __init__(self):
        self.doors = [Door("hub-test", None, "Garage", "Garage", "GDO", 4, True)]
        self.hubs = []
        self.listeners = set()
        self.closed = False
        self.activations = []
        self.light_commands = []

    async def start(self):
        self.connected = True

    async def close(self):
        self.connected = False
        self.closed = True

    def subscribe(self, callback):
        self.listeners.add(callback)
        return lambda: self.listeners.discard(callback)

    def emit(self):
        for callback in tuple(self.listeners):
            callback()

    async def activate(self, hub_id, channel):
        self.activations.append((hub_id, channel))

    async def set_light(self, hub_id, on):
        self.light_commands.append((hub_id, on))

    def diagnostics(self):
        return {"connected": self.connected, "door_count": len(self.doors)}


@pytest.fixture
def client_factory():
    clients = []

    def create(*args, **kwargs):
        client = FakeClient()
        clients.append(client)
        return client

    with patch(
        "custom_components.skylink_orbit.orbitclient.OrbitClient", side_effect=create
    ):
        yield clients


async def test_ui_login_creates_account_entry(hass, client_factory):
    result = await hass.config_entries.flow.async_init(
        "skylink_orbit",
        context={"source": SOURCE_USER},
        data={"username": "owner@example.invalid", "password": "synthetic-secret"},
    )
    assert result["type"] == "create_entry"
    assert result["result"].unique_id == "account-test"
    assert client_factory[0].closed
    await hass.async_block_till_done()


async def test_entry_exposes_status_with_disabled_activation_and_unloads(
    hass, client_factory, caplog
):
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain="skylink_orbit",
        unique_id="account-test",
        data={"username": "owner@example.invalid", "password": "synthetic-secret"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    covers = [state for state in hass.states.async_all("cover")]
    assert len(covers) == 1 and covers[0].state == "closed"
    assert covers[0].attributes["supported_features"] == 0
    buttons = [
        entity for entity in registry.entities.values() if entity.domain == "button"
    ]
    assert (
        len(buttons) == 1
        and buttons[0].disabled_by is er.RegistryEntryDisabler.INTEGRATION
    )
    client = client_factory[-1]
    client.connected = False
    client.emit()
    await hass.async_block_till_done()
    assert hass.states.get(covers[0].entity_id).state == "unavailable"
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert client.closed and not client.listeners
    assert "Unable to remove unknown job listener" not in caplog.text
    assert client.activations == []


async def test_duplicate_account_is_not_added(hass, client_factory):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain="skylink_orbit",
        unique_id="account-test",
        data={"username": "owner@example.invalid", "password": "old"},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        "skylink_orbit",
        context={"source": SOURCE_USER},
        data={"username": "alias@example.invalid", "password": "synthetic-secret"},
    )
    assert result["type"] == "abort" and result["reason"] == "already_configured"
    assert client_factory[0].closed


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ("AuthenticationError", "invalid_auth"),
        ("CannotConnect", "cannot_connect"),
        ("LoginRejected", "login_rejected"),
        ("ProtocolError", "invalid_response"),
    ],
)
async def test_flow_errors_leave_no_session(hass, client_factory, error, expected):
    from custom_components.skylink_orbit.orbitclient import errors

    with patch.object(
        FakeClient, "start", side_effect=getattr(errors, error)("failure")
    ):
        result = await hass.config_entries.flow.async_init(
            "skylink_orbit",
            context={"source": SOURCE_USER},
            data={"username": "owner@example.invalid", "password": "secret"},
        )
    assert result["type"] == "form" and result["errors"] == {"base": expected}
    assert client_factory[0].closed


@pytest.mark.parametrize(
    ("account", "expected"),
    [("account-test", "reauth_successful"), ("different-account", "wrong_account")],
)
async def test_reauthentication_preserves_account_identity(
    hass, client_factory, account, expected
):
    from homeassistant.config_entries import SOURCE_REAUTH
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain="skylink_orbit",
        unique_id="account-test",
        data={"username": "owner@example.invalid", "password": "old"},
    )
    entry.add_to_hass(hass)
    with patch.object(FakeClient, "account_id", account):
        result = await hass.config_entries.flow.async_init(
            "skylink_orbit",
            context={
                "source": SOURCE_REAUTH,
                "entry_id": entry.entry_id,
                "unique_id": entry.unique_id,
            },
            data=entry.data,
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"username": "owner@example.invalid", "password": "new"}
        )
    assert result["type"] == "abort" and result["reason"] == expected
    assert entry.data["password"] == ("new" if account == "account-test" else "old")
    await hass.async_block_till_done()


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (0, "opening"),
        (1, "open"),
        (2, "open"),
        (3, "closing"),
        (4, "closed"),
        (5, "unknown"),
        (6, "open"),
        (7, "unknown"),
        (None, "unknown"),
    ],
)
async def test_door_state_mapping_through_ha(hass, client_factory, code, expected):
    from dataclasses import replace

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain="skylink_orbit",
        unique_id="account-test",
        data={"username": "owner@example.invalid", "password": "secret"},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    client = client_factory[-1]
    client.doors = [replace(client.doors[0], state=code)]
    client.emit()
    await hass.async_block_till_done()
    assert hass.states.async_all("cover")[0].state == expected
    await hass.config_entries.async_unload(entry.entry_id)


async def test_diagnostics_exclude_account_and_device_identity(hass, client_factory):
    import json

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.skylink_orbit.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    entry = MockConfigEntry(
        domain="skylink_orbit",
        unique_id="account-test",
        data={"username": "owner@example.invalid", "password": "synthetic-secret"},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    output = await async_get_config_entry_diagnostics(hass, entry)
    rendered = json.dumps(output)
    for private in (
        "owner@example.invalid",
        "synthetic-secret",
        "account-test",
        "hub-test",
        "Garage",
    ):
        assert private not in rendered
    assert output["connected"] is True and output["door_count"] == 1
    await hass.config_entries.async_unload(entry.entry_id)


async def test_enabled_button_calls_activation_and_offline_request_is_rejected(
    hass, client_factory
):
    from homeassistant.exceptions import HomeAssistantError
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain="skylink_orbit",
        unique_id="account-test",
        data={"username": "owner@example.invalid", "password": "secret"},
    )
    entry.add_to_hass(hass)
    button = er.async_get(hass).async_get_or_create(
        "button",
        "skylink_orbit",
        "account-test:hub-test:single:activate",
        config_entry=entry,
        suggested_object_id="garage_activate",
    )
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await hass.services.async_call(
        "button", "press", {"entity_id": button.entity_id}, blocking=True
    )
    client = client_factory[-1]
    assert client.activations == [("hub-test", None)]
    client.connected = False
    client.emit()
    await hass.async_block_till_done()
    # Home Assistant itself can filter unavailable targets before dispatch.
    try:
        await hass.services.async_call(
            "button", "press", {"entity_id": button.entity_id}, blocking=True
        )
    except HomeAssistantError:
        pass
    assert client.activations == [("hub-test", None)]
    await hass.config_entries.async_unload(entry.entry_id)


async def test_new_dual_channels_share_one_device_and_disappear_when_removed(
    hass, client_factory
):
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain="skylink_orbit",
        unique_id="account-test",
        data={"username": "owner@example.invalid", "password": "secret"},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    client = client_factory[-1]
    client.doors = [
        Door("nova-test", "A", "Left", "Double garage", "NOVA", 4, True),
        Door("nova-test", "B", "Right", "Double garage", "NOVA", 1, True),
    ]
    client.emit()
    await hass.async_block_till_done()
    entities = [
        entity
        for entity in er.async_get(hass).entities.values()
        if entity.domain == "cover" and "nova-test" in entity.unique_id
    ]
    assert len(entities) == 2 and entities[0].device_id == entities[1].device_id
    assert {hass.states.get(entity.entity_id).state for entity in entities} == {
        "open",
        "closed",
    }
    client.doors = []
    client.emit()
    await hass.async_block_till_done()
    assert all(
        hass.states.get(entity.entity_id).state == "unavailable" for entity in entities
    )
    await hass.config_entries.async_unload(entry.entry_id)


async def test_background_authentication_failure_starts_reauth(hass, client_factory):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.skylink_orbit.orbitclient.errors import AuthenticationError

    entry = MockConfigEntry(
        domain="skylink_orbit",
        unique_id="account-test",
        data={"username": "owner@example.invalid", "password": "secret"},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    client = client_factory[-1]
    client.connected = False
    client.error = AuthenticationError("Authentication rejected")
    client.emit()
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress()
    assert len(flows) == 1 and flows[0]["context"]["source"] == "reauth"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_home_assistant_stop_cleans_up_without_listener_errors(
    hass, client_factory, caplog
):
    from homeassistant.const import EVENT_HOMEASSISTANT_STOP
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain="skylink_orbit",
        unique_id="account-test",
        data={"username": "owner@example.invalid", "password": "secret"},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    assert client_factory[-1].closed and not client_factory[-1].listeners
    await hass.config_entries.async_unload(entry.entry_id)
    assert "Unable to remove unknown job listener" not in caplog.text


async def test_light_and_signal_discovered_from_push_and_share_device(
    hass, client_factory, caplog
):
    from dataclasses import replace

    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.skylink_orbit.orbitclient.models import Hub

    entry = MockConfigEntry(
        domain="skylink_orbit",
        unique_id="account-test",
        data={"username": "owner@example.invalid", "password": "secret"},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.async_all("light") == []
    assert hass.states.async_all("sensor") == []
    client = client_factory[-1]
    client.hubs = [Hub("nova-test", "Garage", "NOVA", True, False, -61)]
    client.doors = [
        Door("nova-test", "A", "Left", "Garage", "NOVA", 4, True),
        Door("nova-test", "B", "Right", "Garage", "NOVA", 1, True),
    ]
    client.emit()
    await hass.async_block_till_done()
    lights = hass.states.async_all("light")
    sensors = hass.states.async_all("sensor")
    assert len(lights) == len(sensors) == 1
    light, signal = lights[0], sensors[0]
    assert light.state == "off" and signal.state == "-61"
    assert signal.attributes["unit_of_measurement"] == "dBm"
    assert signal.attributes["device_class"] == "signal_strength"
    assert light.attributes["supported_color_modes"] == ["onoff"]
    registry = er.async_get(hass)
    entities = [e for e in registry.entities.values() if "nova-test" in e.unique_id]
    assert len({e.device_id for e in entities}) == 1
    for service, target in [("turn_on", True), ("turn_off", False)]:
        await hass.services.async_call(
            "light", service, {"entity_id": light.entity_id}, blocking=True
        )
        assert client.light_commands[-1] == ("nova-test", target)
    # Commands do not optimistically change the light.
    assert hass.states.get(light.entity_id).state == "off"
    client.hubs = [replace(client.hubs[0], light=True, rssi=-74)]
    client.emit()
    await hass.async_block_till_done()
    assert hass.states.get(light.entity_id).state == "on"
    assert hass.states.get(signal.entity_id).state == "-74"
    assert (
        len(hass.states.async_all("light")) == len(hass.states.async_all("sensor")) == 1
    )
    client.hubs = [replace(client.hubs[0], light=None, rssi=None)]
    client.emit()
    await hass.async_block_till_done()
    assert hass.states.get(light.entity_id).state == "unknown"
    assert hass.states.get(signal.entity_id).state == "unknown"
    from homeassistant.exceptions import HomeAssistantError

    from custom_components.skylink_orbit.orbitclient.errors import CommandRejected

    with patch.object(
        client, "set_light", side_effect=CommandRejected("Light outcome unknown")
    ):
        with pytest.raises(HomeAssistantError, match="outcome unknown"):
            await hass.services.async_call(
                "light", "turn_on", {"entity_id": light.entity_id}, blocking=True
            )
    client.hubs = [replace(client.hubs[0], online=False)]
    client.emit()
    await hass.async_block_till_done()
    assert hass.states.get(light.entity_id).state == "unavailable"
    assert hass.states.get(signal.entity_id).state == "unavailable"
    client.hubs = [replace(client.hubs[0], online=True, light=False, rssi=-62)]
    client.emit()
    await hass.async_block_till_done()
    client.connected = False
    client.emit()
    await hass.async_block_till_done()
    assert hass.states.get(light.entity_id).state == "unavailable"
    assert hass.states.get(signal.entity_id).state == "unavailable"
    previous_commands = list(client.light_commands)
    try:
        await hass.services.async_call(
            "light", "turn_on", {"entity_id": light.entity_id}, blocking=True
        )
    except HomeAssistantError:
        pass
    assert client.light_commands == previous_commands
    client.connected = True
    client.hubs = []
    client.emit()
    await hass.async_block_till_done()
    assert hass.states.get(light.entity_id).state == "unavailable"
    assert hass.states.get(signal.entity_id).state == "unavailable"
    await hass.config_entries.async_unload(entry.entry_id)
    assert client.closed and not client.listeners
    assert [r.getMessage() for r in caplog.records if r.levelno >= 40] == [
        "Error requesting skylink_orbit data: Orbit session unavailable"
    ]
