import logging
from dataclasses import dataclass, field
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import EircSpbApiClient
from .const import DOMAIN
from .exceptions import EircSpbApiError, EircSpbAuthError
from .models import Account, Meter
from .notifications import NotificationDetector


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
        self._detector: NotificationDetector | None = None
        self._persistent = False

    def setup_notifications(self, persistent: bool, deadline_days: int = 3) -> None:
        self._persistent = persistent
        self._detector = NotificationDetector(deadline_days=deadline_days)

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
        if self._detector is not None:
            for account in data.accounts.values():
                for n in self._detector.feed(account):
                    self._emit(n)
            try:
                native = await self._client.get_unread_notifications()
            except EircSpbApiError:
                native = []
            for n in self._detector.native(native):
                self._emit(n)
        return data

    def _emit(self, n: dict) -> None:
        event_type = {
            "new_bill": f"{DOMAIN}_new_bill",
            "reading_deadline": f"{DOMAIN}_reading_deadline",
            "native": f"{DOMAIN}_notification",
        }[n["type"]]
        self.hass.bus.async_fire(event_type, n)
        if not self._persistent:
            return
        from homeassistant.components import persistent_notification as pn

        if n["type"] == "native":
            notification_id = f"{DOMAIN}_{n['native_id']}"
            title = n["title"]
            message = n["message"]
        elif n["type"] == "new_bill":
            notification_id = f"{DOMAIN}_bill_{n['account_id']}_{n['bill_id']}"
            title = "Новый счёт"
            message = f"Лицевой счёт {n['number']}: новый счёт {n['bill_id']} на {n['amount']} ₽"
        else:
            notification_id = (
                f"{DOMAIN}_deadline_{n['account_id']}_{n['deadline_day']}"
            )
            title = "Дедлайн показаний"
            message = (
                f"Лицевой счёт {n['number']}: осталось {n['days_left']} дн. "
                f"для передачи показаний ({n['period']})"
            )
        pn.async_create(
            self.hass,
            title=title,
            message=message,
            notification_id=notification_id,
        )
