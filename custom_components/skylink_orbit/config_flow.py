"""Verify credentials here; the entry owns discovery and its trust history."""

from collections.abc import Mapping
from typing import Any

import probatio as vol
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import orbitclient
from .const import CONF_DIRECTIONAL, DOMAIN
from .orbitclient.errors import (
    AuthenticationError,
    CannotConnect,
    LoginRejected,
    ProtocolError,
)


class OrbitConfigFlow(ConfigFlow, domain=DOMAIN):
    """One configuration entry per vendor account number."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return OrbitOptionsFlow()

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
                # A throwaway discovery session would discard its position
                # evidence before entry setup, especially on reauthentication.
                await self.async_set_unique_id(await client.login())
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


class OrbitOptionsFlow(OptionsFlow):
    """Explicit opt-in; existing installations remain status-only by default."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={
                    **self.config_entry.options,
                    CONF_DIRECTIONAL: user_input[CONF_DIRECTIONAL],
                },
            )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_DIRECTIONAL,
                        default=self.config_entry.options.get(CONF_DIRECTIONAL, False),
                    ): bool,
                }
            ),
        )
