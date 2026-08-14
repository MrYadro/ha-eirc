from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.eirc_spb import (
    async_get_entry_data,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.eirc_spb.const import (
    CONF_ACCOUNTS,
    CONF_LOGIN,
    CONF_PASSWORD,
    DOMAIN,
)
from custom_components.eirc_spb.coordinator import EircSpbCoordinator, EircSpbData
from custom_components.eirc_spb.exceptions import EircSpbApiError, EircSpbAuthError
from custom_components.eirc_spb.models import (
    Account,
    BillsPayments,
    Meter,
    Payment,
    Scale,
)

ADDRESS = "ул. Тестовая, д. 1, кв. 1"
FINANCE = BillsPayments(
    balance=100.0,
    accruals_total=1500.0,
    accruals_period=None,
    accruals_breakdown={"Услуга 5": 500.0, "Услуга 7": 1000.0},
    payments=[],
)
BILL = {"id": "26071000000001", "amount": 7633.65, "timestamp": "14.02.2026 00:00:00"}
PAYMENTS = [Payment(payment_id="p1", date="2026-02-28T10:07:34", amount=700.0)]


def make_account(account_id: str, number: str) -> Account:
    return Account(account_id=account_id, number=number, address="")


def make_meter(account_id: str = "a1") -> Meter:
    return Meter(
        meter_id="m1",
        account_id=account_id,
        name="Холодная вода",
        device_class="water",
        unit="м³",
        scales=[
            Scale(
                scale_id="s1",
                name=None,
                last_reading=123.0,
                last_submit="2026-08-01",
            )
        ],
    )


def make_client(accounts: list[Account]) -> AsyncMock:
    client = AsyncMock()
    client.get_accounts.return_value = accounts
    client.get_address.return_value = ADDRESS
    client.get_finance.return_value = FINANCE
    client.get_current_bill.return_value = BILL
    client.get_payments.return_value = PAYMENTS
    client.get_meters.return_value = [make_meter()]
    return client


def build_coordinator(
    hass: HomeAssistant, client: AsyncMock, account_ids: list[str]
) -> EircSpbCoordinator:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_LOGIN: "u", CONF_PASSWORD: "p", CONF_ACCOUNTS: list(account_ids)},
    )
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)
    token = config_entries.current_entry.set(entry)
    try:
        return EircSpbCoordinator(hass, client, account_ids, 12)
    finally:
        config_entries.current_entry.reset(token)


async def test_coordinator_merges_data(hass: HomeAssistant):
    client = make_client([make_account("a1", "1000000001")])
    coordinator = build_coordinator(hass, client, ["a1"])
    await coordinator.async_config_entry_first_refresh()
    data: EircSpbData = coordinator.data
    assert data.accounts["a1"].balance == 100.0
    assert data.accounts["a1"].accruals_total == 1500.0
    assert data.accounts["a1"].payments_total == 700.0
    assert data.accounts["a1"].accruals_breakdown == {"Услуга 5": 500.0, "Услуга 7": 1000.0}
    assert data.accounts["a1"].accruals_period == "14.02.2026 00:00:00"
    assert data.accounts["a1"].address == ADDRESS
    assert data.accounts["a1"].recent_payments == PAYMENTS
    assert data.meters["m1"].scales[0].last_reading == 123.0
    client.get_address.assert_awaited_once_with("a1")
    client.get_finance.assert_awaited_once_with("a1")
    client.get_current_bill.assert_awaited_once_with("a1")
    client.get_payments.assert_awaited_once_with("a1")
    client.get_meters.assert_awaited_once_with("a1")


async def test_coordinator_filters_accounts(hass: HomeAssistant):
    client = make_client(
        [make_account("a1", "1000000001"), make_account("a2", "1000000002")]
    )
    coordinator = build_coordinator(hass, client, ["a1"])
    await coordinator.async_config_entry_first_refresh()
    data = coordinator.data
    assert list(data.accounts) == ["a1"]
    assert list(data.meters) == ["m1"]
    client.get_finance.assert_awaited_once()


async def test_coordinator_address_failure_is_non_fatal(hass: HomeAssistant):
    client = make_client([make_account("a1", "1000000001")])
    client.get_address.side_effect = EircSpbApiError("boom")
    coordinator = build_coordinator(hass, client, ["a1"])
    await coordinator.async_config_entry_first_refresh()
    assert coordinator.last_update_success is True
    assert coordinator.data.accounts["a1"].address == ""
    assert coordinator.data.accounts["a1"].balance == 100.0


def make_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={CONF_LOGIN: "u", CONF_PASSWORD: "p", CONF_ACCOUNTS: ["a1"]},
    )


async def test_setup_and_unload_entry(hass: HomeAssistant):
    entry = make_entry()
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, options={"scan_interval_hours": 6})
    client = make_client([make_account("a1", "1000000001")])
    coordinator = MagicMock()
    coordinator.async_config_entry_first_refresh = AsyncMock()
    with (
        patch(
            "custom_components.eirc_spb.EircSpbApiClient",
            return_value=client,
        ) as client_cls,
        patch(
            "custom_components.eirc_spb.EircSpbCoordinator",
            return_value=coordinator,
        ) as coordinator_cls,
        patch(
            "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
            new_callable=AsyncMock,
        ) as forward,
    ):
        assert await async_setup_entry(hass, entry) is True
    client_cls.assert_called_once()
    assert client_cls.call_args.args[:3] == ("u", "p", None)
    assert coordinator_cls.call_args.args[1:] == (client, ["a1"], 6)
    forward.assert_awaited_once_with(entry, ["sensor"])
    runtime = async_get_entry_data(hass, entry.entry_id)
    assert runtime is not None
    assert runtime.client is client
    assert runtime.coordinator is coordinator
    with patch(
        "homeassistant.config_entries.ConfigEntries.async_unload_platforms",
        new_callable=AsyncMock,
        return_value=True,
    ) as unload_platforms:
        assert await async_unload_entry(hass, entry) is True
    unload_platforms.assert_awaited_once_with(entry, ["sensor"])
    client.close.assert_awaited_once()
    assert async_get_entry_data(hass, entry.entry_id) is None


async def test_setup_entry_maps_auth_error(hass: HomeAssistant):
    entry = make_entry()
    entry.add_to_hass(hass)
    client = make_client([make_account("a1", "1000000001")])
    coordinator = MagicMock()
    coordinator.async_config_entry_first_refresh = AsyncMock(
        side_effect=EircSpbAuthError("expired")
    )
    with (
        patch(
            "custom_components.eirc_spb.EircSpbApiClient", return_value=client
        ),
        patch(
            "custom_components.eirc_spb.EircSpbCoordinator",
            return_value=coordinator,
        ),
        patch(
            "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
            new_callable=AsyncMock,
        ),
    ):
        with pytest.raises(ConfigEntryAuthFailed):
            await async_setup_entry(hass, entry)
    assert async_get_entry_data(hass, entry.entry_id) is None
