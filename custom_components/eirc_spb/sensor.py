from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import EircSpbRuntime
from .const import ATTR_ACCOUNT_ID, ATTR_METER_ID, ATTR_SCALE_ID, DOMAIN
from .coordinator import EircSpbCoordinator, EircSpbData
from .models import Account, Meter, Scale

CURRENCY_RUB = "RUB"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime: EircSpbRuntime = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(_build_entities(runtime.coordinator))


def _device(account: Account) -> DeviceInfo:
    tenancy = account.tenancy_full or account.tenancy_short
    parts = [p for p in (account.alias, " ".join(filter(None, (tenancy, account.number)))) if p]
    return DeviceInfo(
        identifiers={(DOMAIN, account.account_id)},
        name=" - ".join(parts) or f"ЕИРЦ {account.number}",
        manufacturer="ЕИРЦ СПб",
        model=account.address or None,
    )


def _build_entities(coordinator: EircSpbCoordinator) -> list[SensorEntity]:
    data: EircSpbData = coordinator.data
    entities: list[SensorEntity] = []
    for account in data.accounts.values():
        entities.extend(
            [
                BalanceSensor(coordinator, account),
                AccrualsSensor(coordinator, account),
                PaymentsSensor(coordinator, account),
                CurrentBillSensor(coordinator, account),
                FinesSensor(coordinator, account),
                ReadingDeadlineSensor(coordinator, account),
            ]
        )
        for meter in data.meters.values():
            if meter.account_id != account.account_id:
                continue
            for scale in meter.scales:
                entities.append(MeterSensor(coordinator, account, meter, scale))
    return entities


class _AccountSensor(CoordinatorEntity[EircSpbCoordinator], SensorEntity):
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = CURRENCY_RUB
    _attr_has_entity_name = True
    _attr_suggested_display_precision = 2

    def __init__(
        self, coordinator: EircSpbCoordinator, account: Account
    ) -> None:
        super().__init__(coordinator)
        self._account_id = account.account_id
        self._attr_unique_id = f"{DOMAIN}_{account.number}_{self._key}"
        self._attr_device_info = _device(account)

    @property
    def account(self) -> Account | None:
        return self.coordinator.data.accounts.get(self._account_id)


class BalanceSensor(_AccountSensor):
    _key = "balance"
    _attr_state_class = SensorStateClass.TOTAL
    _attr_name = "Баланс"

    @property
    def native_value(self) -> float | None:
        account = self.account
        return account.balance if account else None


class AccrualsSensor(_AccountSensor):
    _key = "accruals"
    _attr_state_class = SensorStateClass.TOTAL
    _attr_name = "Начисления"

    @property
    def native_value(self) -> float | None:
        account = self.account
        return account.accruals_total if account else None

    @property
    def extra_state_attributes(self) -> dict:
        account = self.account
        if account is None:
            return {"period": None}
        return {
            "period": account.accruals_period,
            **account.accruals_breakdown,
        }


class PaymentsSensor(_AccountSensor):
    _key = "payments"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_name = "Платежи"

    @property
    def native_value(self) -> float | None:
        account = self.account
        return account.payments_total if account else None

    @property
    def extra_state_attributes(self) -> dict:
        account = self.account
        if account is None:
            return {"payments": []}
        return {
            "payments": [
                {"id": p.payment_id, "date": p.date, "amount": p.amount}
                for p in account.recent_payments
            ]
        }


class CurrentBillSensor(_AccountSensor):
    _key = "bill"
    _attr_state_class = SensorStateClass.TOTAL
    _attr_name = "Текущий счёт"

    @property
    def native_value(self) -> float | None:
        account = self.account
        return account.current_bill_amount if account else None

    @property
    def extra_state_attributes(self) -> dict:
        account = self.account
        if account is None:
            return {}
        attrs = {}
        if account.current_bill_id is not None:
            attrs["bill_id"] = account.current_bill_id
        if account.accruals_period is not None:
            attrs["timestamp"] = account.accruals_period
        return attrs


class FinesSensor(_AccountSensor):
    _key = "fines"
    _attr_state_class = SensorStateClass.TOTAL
    _attr_name = "Пеня"

    @property
    def native_value(self) -> float | None:
        account = self.account
        return account.fines if account else None


class ReadingDeadlineSensor(_AccountSensor):
    _key = "reading_deadline"
    _attr_name = "Дедлайн показаний"
    _attr_icon = "mdi:calendar-clock"

    @property
    def native_value(self) -> int | None:
        account = self.account
        return account.reading_deadline_day if account else None

    @property
    def extra_state_attributes(self) -> dict:
        account = self.account
        if account is None:
            return {}
        attrs = {}
        if account.reading_period_name is not None:
            attrs["period"] = account.reading_period_name
        if account.reading_window is not None:
            attrs["window"] = account.reading_window
        return attrs


class MeterSensor(CoordinatorEntity[EircSpbCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_suggested_display_precision = 3

    def __init__(
        self,
        coordinator: EircSpbCoordinator,
        account: Account,
        meter: Meter,
        scale: Scale,
    ) -> None:
        super().__init__(coordinator)
        self._account_id = account.account_id
        self._meter_id = meter.meter_id
        self._scale_id = scale.scale_id
        self._attr_unique_id = (
            f"{DOMAIN}_{account.number}_{meter.meter_id}_{scale.scale_id}"
        )
        self._attr_device_info = _device(account)
        if scale.name and meter.serial:
            self._attr_name = f"{scale.name} (ПУ № {meter.serial})"
        elif scale.name:
            self._attr_name = scale.name
        else:
            self._attr_name = meter.name
        if meter.device_class == "water":
            self._attr_device_class = SensorDeviceClass.WATER
            self._attr_state_class = SensorStateClass.TOTAL_INCREASING
            self._attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS
        elif meter.device_class == "energy":
            self._attr_device_class = SensorDeviceClass.ENERGY
            self._attr_state_class = SensorStateClass.TOTAL_INCREASING
            self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        else:
            self._attr_native_unit_of_measurement = meter.unit or None

    @property
    def meter(self) -> Meter | None:
        return self.coordinator.data.meters.get(self._meter_id)

    @property
    def scale(self) -> Scale | None:
        meter = self.meter
        if meter is None:
            return None
        return next(
            (s for s in meter.scales if s.scale_id == self._scale_id), None
        )

    @property
    def native_value(self) -> float | None:
        scale = self.scale
        return scale.last_reading if scale else None

    @property
    def extra_state_attributes(self) -> dict:
        meter = self.meter
        scale = self.scale
        return {
            ATTR_ACCOUNT_ID: self._account_id,
            ATTR_METER_ID: self._meter_id,
            ATTR_SCALE_ID: self._scale_id,
            "last_submit": scale.last_submit if scale else None,
            "meter_serial": meter.serial if meter else None,
            "verification_date": meter.verification_date if meter else None,
        }
