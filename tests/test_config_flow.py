from unittest.mock import ANY, AsyncMock, MagicMock, patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.eirc_spb.auth import AuthResult, Session
from custom_components.eirc_spb.const import DOMAIN
from custom_components.eirc_spb.exceptions import (
    EircSpbApiError,
    EircSpbAuthError,
    EircSpbConfirmationError,
)
from custom_components.eirc_spb.models import Account

LOGIN = "user1@example.com"
CHANNELS = ["EMAIL", "PHONE", "FLASHCALL", "TOTP"]


def client_mock() -> MagicMock:
    client = MagicMock()
    client.authenticate = AsyncMock(
        return_value=AuthResult(
            session=Session(access="tok", auth="auth"),
            needs_confirmation=False,
        )
    )
    client.get_accounts = AsyncMock(
        return_value=[
            Account(account_id="a1", number="1000000001", address=""),
            Account(account_id="a2", number="1000000002", address=""),
        ]
    )
    client.send_code = AsyncMock()
    client.verify_code = AsyncMock(
        return_value=Session(
            access="tok", auth="auth", verification_token="vtok"
        )
    )
    client.verification_token = None
    return client


def start_user_flow(hass: HomeAssistant):
    return hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


def submit(hass: HomeAssistant, flow_id: str, user_input: dict):
    return hass.config_entries.flow.async_configure(flow_id, user_input)


def channel_options(result) -> set:
    for key, validator in result["data_schema"].schema.items():
        if key.schema == "channel":
            return set(validator.container)
    raise AssertionError("channel field not found in schema")


async def test_full_flow_no_confirmation_two_accounts(hass: HomeAssistant):
    client = client_mock()
    with patch(
        "custom_components.eirc_spb.config_flow.EircSpbApiClient",
        return_value=client,
    ):
        result = await start_user_flow(hass)
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"

        result = await submit(hass, result["flow_id"], {"login": LOGIN, "password": "pw"})
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "select_accounts"

        result = await submit(hass, result["flow_id"], {"accounts": ["a1"]})
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"]["login"] == LOGIN
        assert result["data"]["password"] == "pw"
        assert result["data"]["accounts"] == ["a1"]
        assert "verification_token" not in result["data"]


async def test_confirmation_flow_otp(hass: HomeAssistant):
    client = client_mock()
    client.authenticate = AsyncMock(
        return_value=AuthResult(
            session=None,
            needs_confirmation=True,
            transaction_id="t1",
            channels=CHANNELS,
        )
    )
    client.verification_token = "vtok"
    with patch(
        "custom_components.eirc_spb.config_flow.EircSpbApiClient",
        return_value=client,
    ):
        result = await start_user_flow(hass)
        result = await submit(hass, result["flow_id"], {"login": LOGIN, "password": "pw"})
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "confirm"
        assert channel_options(result) == {"EMAIL", "PHONE", "FLASHCALL"}

        result = await submit(hass, result["flow_id"], {"channel": "EMAIL"})
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "code"

        result = await submit(hass, result["flow_id"], {"code": "12345"})
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "select_accounts"

        result = await submit(hass, result["flow_id"], {"accounts": ["a1", "a2"]})
        assert result["type"] == FlowResultType.CREATE_ENTRY

    client.send_code.assert_awaited_once_with("t1", "email")
    client.verify_code.assert_awaited_once_with("t1", "email", "12345")
    assert result["data"]["verification_token"] == "vtok"
    assert result["data"]["accounts"] == ["a1", "a2"]


async def test_wrong_code_shows_error(hass: HomeAssistant):
    client = client_mock()
    client.authenticate = AsyncMock(
        return_value=AuthResult(
            session=None,
            needs_confirmation=True,
            transaction_id="t1",
            channels=CHANNELS,
        )
    )
    client.verify_code = AsyncMock(side_effect=EircSpbConfirmationError("Неправильный код"))
    with patch(
        "custom_components.eirc_spb.config_flow.EircSpbApiClient",
        return_value=client,
    ):
        result = await start_user_flow(hass)
        result = await submit(hass, result["flow_id"], {"login": LOGIN, "password": "pw"})
        result = await submit(hass, result["flow_id"], {"channel": "PHONE"})
        assert result["step_id"] == "code"

        result = await submit(hass, result["flow_id"], {"code": "0000"})
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "code"
        assert result["errors"] == {"base": "invalid_code"}


async def test_code_api_error_shows_cannot_connect(hass: HomeAssistant):
    client = client_mock()
    client.authenticate = AsyncMock(
        return_value=AuthResult(
            session=None,
            needs_confirmation=True,
            transaction_id="t1",
            channels=CHANNELS,
        )
    )
    client.verify_code = AsyncMock(side_effect=EircSpbApiError("server error"))
    with patch(
        "custom_components.eirc_spb.config_flow.EircSpbApiClient",
        return_value=client,
    ):
        result = await start_user_flow(hass)
        result = await submit(hass, result["flow_id"], {"login": LOGIN, "password": "pw"})
        result = await submit(hass, result["flow_id"], {"channel": "EMAIL"})
        assert result["step_id"] == "code"

        result = await submit(hass, result["flow_id"], {"code": "12345"})
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "code"
        assert result["errors"] == {"base": "cannot_connect"}


async def test_bad_credentials(hass: HomeAssistant):
    client = client_mock()
    client.authenticate = AsyncMock(side_effect=EircSpbAuthError("bad creds"))
    with patch(
        "custom_components.eirc_spb.config_flow.EircSpbApiClient",
        return_value=client,
    ):
        result = await start_user_flow(hass)
        result = await submit(hass, result["flow_id"], {"login": LOGIN, "password": "nope"})
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"
        assert result["errors"] == {"base": "invalid_auth"}


async def test_single_account_autoselected(hass: HomeAssistant):
    client = client_mock()
    client.get_accounts = AsyncMock(
        return_value=[Account(account_id="a1", number="1000000001", address="")]
    )
    with patch(
        "custom_components.eirc_spb.config_flow.EircSpbApiClient",
        return_value=client,
    ):
        result = await start_user_flow(hass)
        result = await submit(hass, result["flow_id"], {"login": LOGIN, "password": "pw"})
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"]["accounts"] == ["a1"]
        assert result["title"] == "ЕИРЦ СПб"


async def test_reauth_successful(hass: HomeAssistant):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "login": LOGIN,
            "password": "old",
            "accounts": ["a1"],
            "verification_token": "stored-vtok",
        },
    )
    entry.add_to_hass(hass)
    client = client_mock()
    client.verification_token = "vtok"
    with patch(
        "custom_components.eirc_spb.config_flow.EircSpbApiClient",
        return_value=client,
    ) as client_cls, patch(
        "custom_components.eirc_spb.async_setup_entry",
        AsyncMock(return_value=True),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_REAUTH,
                "entry_id": entry.entry_id,
            },
            data=entry.data,
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"
        assert result["description_placeholders"]["login"] == LOGIN

        result = await submit(hass, result["flow_id"], {"password": "newpw"})
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "reauth_successful"

        client_cls.assert_called_once_with(LOGIN, "newpw", "stored-vtok", ANY)
        assert entry.data["password"] == "newpw"
        assert entry.data["verification_token"] == "vtok"
        await hass.async_block_till_done()


async def test_reauth_needs_confirmation(hass: HomeAssistant):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"login": LOGIN, "password": "old", "accounts": ["a1"]},
    )
    entry.add_to_hass(hass)
    client = client_mock()
    client.authenticate = AsyncMock(
        return_value=AuthResult(
            session=None,
            needs_confirmation=True,
            transaction_id="t1",
            channels=["EMAIL"],
        )
    )
    with patch(
        "custom_components.eirc_spb.config_flow.EircSpbApiClient",
        return_value=client,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_REAUTH,
                "entry_id": entry.entry_id,
            },
            data=entry.data,
        )
        result = await submit(hass, result["flow_id"], {"password": "newpw"})
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"
        assert result["errors"] == {"base": "confirmation_required"}


async def test_options_flow_sets_scan_interval(hass: HomeAssistant):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"login": LOGIN, "password": "pw", "accounts": ["a1"]},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"scan_interval_hours": 6}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options == {"scan_interval_hours": 6.0}
