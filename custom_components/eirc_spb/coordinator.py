import logging
from dataclasses import dataclass, field
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import EircSpbApiClient
from .exceptions import EircSpbApiError, EircSpbAuthError
from .models import Account, Meter


@dataclass
class EircSpbData:
    accounts: dict[str, Account] = field(default_factory=dict)
    meters: dict[str, Meter] = field(default_factory=dict)


class EircSpbCoordinator(DataUpdateCoordinator[EircSpbData]):
    def __init__(
        self,
        hass: HomeAssistant,
        client: EircSpbApiClient,
        account_ids: list[str],
        scan_interval_hours: float,
    ) -> None:
        super().__init__(
            hass,
            logging.getLogger(__name__),
            name="eirc_spb",
            update_interval=timedelta(hours=scan_interval_hours),
        )
        self._client = client
        self._account_ids = account_ids

    async def _async_update_data(self) -> EircSpbData:
        data = EircSpbData()
        try:
            for account in await self._client.get_accounts():
                if account.account_id not in self._account_ids:
                    continue
                try:
                    account.address = await self._client.get_address(
                        account.account_id
                    )
                except EircSpbAuthError:
                    raise
                except EircSpbApiError:
                    pass
                finance = await self._client.get_finance(account.account_id)
                account.balance = finance.balance
                account.accruals_total = finance.accruals_total
                account.accruals_breakdown = finance.accruals_breakdown
                account.fines = finance.fines
                account.provider_accruals = finance.provider_accruals
                bill = await self._client.get_current_bill(account.account_id)
                account.accruals_period = bill.get("timestamp")
                account.current_bill_amount = bill.get("amount")
                account.current_bill_id = (
                    str(bill["id"]) if bill.get("id") is not None else None
                )
                period = await self._client.get_reading_period(account.account_id)
                params = period.get("acceptanceParameters") or {}
                interval = params.get("interval") or {}
                account.reading_deadline_day = params.get("deadLine")
                account.reading_period_name = params.get("name")
                account.reading_window = (
                    f"{interval.get('dateFrom', '')} – {interval.get('dateTo', '')}".strip(
                        " –"
                    )
                    or None
                )
                data.accounts[account.account_id] = account
                for meter in await self._client.get_meters(account.account_id):
                    data.meters[meter.meter_id] = meter
        except EircSpbAuthError as err:
            raise ConfigEntryAuthFailed from err
        except EircSpbApiError as err:
            raise UpdateFailed from err
        return data
