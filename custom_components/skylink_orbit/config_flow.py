"""Configure Orbit using a verified account and initial MQTT snapshot."""

from collections.abc import Mapping
from typing import Any

import probatio as vol
from homeassistant.config_entries import SOURCE_REAUTH, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import orbitclient
from .const import DOMAIN
from .orbitclient.errors import (
    AuthenticationError,
    CannotConnect,
    LoginRejected,
    ProtocolError,
)


class OrbitConfigFlow(ConfigFlow, domain=DOMAIN):
    """One configuration entry per vendor account number."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._credentials("user", user_input)

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._credentials("reauth_confirm", user_input)

    async def _credentials(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            client = orbitclient.OrbitClient(
                async_get_clientsession(self.hass),
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
            )
            try:
                await client.start()
                assert client.account_id is not None
                await self.async_set_unique_id(client.account_id)
                if self.source == SOURCE_REAUTH:
                    self._abort_if_unique_id_mismatch(reason="wrong_account")
                    return self.async_update_reload_and_abort(
                        self._get_reauth_entry(), data_updates=user_input
                    )
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="Skylink Orbit", data=user_input)
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except LoginRejected:
                errors["base"] = "login_rejected"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except ProtocolError:
                errors["base"] = "invalid_response"
            finally:
                await client.close()
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(
                {vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str}
            ),
            errors=errors,
        )
