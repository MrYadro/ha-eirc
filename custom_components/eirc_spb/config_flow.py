from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import EircSpbApiClient
from .auth import AuthResult
from .const import (
    CONF_ACCOUNTS,
    CONF_LOGIN,
    CONF_PASSWORD,
    CONF_VERIFICATION_TOKEN,
    DOMAIN,
)
from .exceptions import EircSpbApiError, EircSpbAuthError
from .models import Account

CHANNEL_LABELS = {"EMAIL": "E-mail", "PHONE": "SMS", "FLASHCALL": "Звонок"}

PASSWORD_FIELD = TextSelector(
    TextSelectorConfig(type=TextSelectorType.PASSWORD)
)


class EircSpbFlowHandler(ConfigFlow, domain=DOMAIN):
    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        self._login: str | None = None
        self._password: str | None = None
        self._client: EircSpbApiClient | None = None
        self._auth_result: AuthResult | None = None
        self._channel: str | None = None
        self._accounts: list[Account] = []

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        from .options_flow import EircSpbOptionsFlow

        return EircSpbOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._login = user_input[CONF_LOGIN]
            self._password = user_input[CONF_PASSWORD]
            await self.async_set_unique_id(self._login.lower())
            self._abort_if_unique_id_configured()
            self._client = EircSpbApiClient(self._login, self._password)
            try:
                self._auth_result = await self._client.authenticate()
            except EircSpbAuthError:
                errors["base"] = "invalid_auth"
            except EircSpbApiError:
                errors["base"] = "cannot_connect"
            else:
                if self._auth_result.needs_confirmation:
                    return await self.async_step_confirm()
                return await self._async_fetch_accounts("user", errors)
        return self.async_show_form(
            step_id="user",
            data_schema=self._user_schema(),
            errors=errors,
        )

    def _user_schema(self) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required(CONF_LOGIN, default=self._login or vol.UNDEFINED): str,
                vol.Required(CONF_PASSWORD): PASSWORD_FIELD,
            }
        )

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        assert self._auth_result is not None
        channels = [c for c in self._auth_result.channels if c != "TOTP"]
        if not channels:
            return self.async_abort(reason="totp_unsupported")
        if user_input is not None:
            assert self._client is not None
            self._channel = user_input["channel"]
            try:
                await self._client.send_code(
                    self._auth_result.transaction_id or "", self._channel.lower()
                )
            except EircSpbApiError:
                errors["base"] = "cannot_connect"
            else:
                return await self.async_step_code()
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema(
                {
                    vol.Required("channel"): vol.In(
                        {c: CHANNEL_LABELS.get(c, c) for c in channels}
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_code(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            assert self._client is not None
            assert self._auth_result is not None and self._channel is not None
            try:
                await self._client.verify_code(
                    self._auth_result.transaction_id or "",
                    self._channel.lower(),
                    user_input["code"],
                )
            except EircSpbApiError:
                errors["base"] = "invalid_code"
            else:
                return await self._async_fetch_accounts("code", errors)
        return self.async_show_form(
            step_id="code",
            data_schema=vol.Schema({vol.Required("code"): str}),
            errors=errors,
        )

    async def _async_fetch_accounts(
        self, step_id: str, errors: dict[str, str]
    ) -> ConfigFlowResult:
        assert self._client is not None
        try:
            self._accounts = await self._client.get_accounts()
        except EircSpbApiError:
            errors["base"] = "cannot_connect"
            schema = self._user_schema() if step_id == "user" else vol.Schema(
                {vol.Required("code"): str}
            )
            return self.async_show_form(
                step_id=step_id, data_schema=schema, errors=errors
            )
        return self._select_or_create()

    def _select_or_create(self) -> ConfigFlowResult:
        if len(self._accounts) == 1:
            account = self._accounts[0]
            return self._create_entry([account], [account.account_id])
        options = {
            a.account_id: f"{a.number} — {a.address}" if a.address else a.number
            for a in self._accounts
        }
        return self.async_show_form(
            step_id="select_accounts",
            data_schema=vol.Schema(
                {vol.Required(CONF_ACCOUNTS): cv.multi_select(options)}
            ),
        )

    async def async_step_select_accounts(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert user_input is not None
        chosen_ids = user_input[CONF_ACCOUNTS]
        chosen = [a for a in self._accounts if a.account_id in chosen_ids]
        return self._create_entry(chosen, chosen_ids)

    def _create_entry(
        self, accounts: list[Account], account_ids: list[str]
    ) -> ConfigFlowResult:
        data: dict[str, Any] = {
            CONF_LOGIN: self._login,
            CONF_PASSWORD: self._password,
            CONF_ACCOUNTS: account_ids,
        }
        assert self._client is not None
        if self._client.verification_token:
            data[CONF_VERIFICATION_TOKEN] = self._client.verification_token
        title = "ЕИРЦ СПб"
        if len(accounts) == 1:
            title = f"ЕИРЦ СПб ({accounts[0].number})"
        return self.async_create_entry(title=title, data=data)

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            client = EircSpbApiClient(entry.data[CONF_LOGIN], user_input[CONF_PASSWORD])
            try:
                result = await client.authenticate()
            except EircSpbAuthError:
                errors["base"] = "invalid_auth"
            except EircSpbApiError:
                errors["base"] = "cannot_connect"
            else:
                if result.needs_confirmation:
                    errors["base"] = "confirmation_required"
                else:
                    data = {**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]}
                    if client.verification_token:
                        data[CONF_VERIFICATION_TOKEN] = client.verification_token
                    else:
                        data.pop(CONF_VERIFICATION_TOKEN, None)
                    return self.async_update_reload_and_abort(
                        entry, data=data, reason="reauth_successful"
                    )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): PASSWORD_FIELD}),
            errors=errors,
        )
