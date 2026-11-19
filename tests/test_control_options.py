"""Explicit enablement through the real HA options flow, no physical controls."""

from dataclasses import replace
from unittest.mock import patch

import pytest
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .test_integration import FakeClient, client_factory  # noqa: F401

__all__ = ["client_factory"]


async def test_directional_option_roundtrip_reloads_without_changing_identity(
    hass, client_factory
):
    entry = MockConfigEntry(
        domain="skylink_orbit",
        unique_id="account-test",
        data={"username": "owner@example.invalid", "password": "secret"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    cover = hass.states.async_all("cover")[0]
    entity_id = cover.entity_id
    assert cover.attributes["supported_features"] == 0
    # Status-only controls must reject before dispatch even though a cover exists.
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "cover", "open_cover", {"entity_id": entity_id}, blocking=True
        )
    assert not client_factory[-1].direction_requests
    for enabled in (True, False):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] == "form"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"enable_directional_controls": enabled}
        )
        assert result["type"] == "create_entry"
        await hass.async_block_till_done()
        assert entry.options["enable_directional_controls"] is enabled
        assert len(hass.states.async_all("cover")) == 1
        assert hass.states.get(entity_id).attributes["supported_features"] == (
            3 if enabled else 0
        )
        assert all(
            not c.activations and not c.direction_requests for c in client_factory
        )
    await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("model", ["NOVA", "NVMini"])
async def test_opt_in_does_not_expose_unvalidated_model(hass, client_factory, model):
    async def start(client):
        client.connected = True
        client.doors = [replace(client.doors[0], model=model, channel="A")]

    entry = MockConfigEntry(
        domain="skylink_orbit",
        unique_id="account-test",
        data={"username": "owner@example.invalid", "password": "secret"},
        options={"enable_directional_controls": True},
    )
    entry.add_to_hass(hass)
    with patch.object(FakeClient, "start", new=start):
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    cover = hass.states.async_all("cover")[0]
    assert cover.attributes["supported_features"] == 0
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "cover", "open_cover", {"entity_id": cover.entity_id}, blocking=True
        )
    assert not client_factory[-1].direction_requests
    await hass.config_entries.async_unload(entry.entry_id)
