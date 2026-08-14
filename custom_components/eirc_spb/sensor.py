from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfVolume

CURRENCY_RUB = "RUB"
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import EircSpbRuntime
from .const import ATTR_ACCOUNT_ID, ATTR_METER_ID, ATTR_SCALE_ID, DOMAIN
from .coordinator import EircSpbCoordinator, EircSpbData
from .models import Account, Meter, Scale


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime: EircSpbRuntime = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(_build_entities(runtime.coordinator))


def _device(account: Account) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, account.account_id)},
        name=f"ЕИРЦ {account.number}",
        manufacturer="ЕИРЦ СПб",
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
        self, coordinator: EircSpbCoordinator, account: Account, key: str
    ) -> None:
        super().__init__(coordinator)
        self._account = account
        self._attr_unique_id = f"{DOMAIN}_{account.number}_{key}"
        self._attr_device_info = _device(account)


class BalanceSensor(_AccountSensor):
    _attr_state_class = SensorStateClass.TOTAL

    def __init__(
        self, coordinator: EircSpbCoordinator, account: Account
    ) -> None:
        super().__init__(coordinator, account, "balance")

    @property
    def name(self) -> str:
        return "Баланс"

    @property
    def native_value(self) -> float | None:
        return self._account.balance


class AccrualsSensor(_AccountSensor):
    _attr_state_class = SensorStateClass.TOTAL

    def __init__(
        self, coordinator: EircSpbCoordinator, account: Account
    ) -> None:
        super().__init__(coordinator, account, "accruals")

    @property
    def name(self) -> str:
        return "Начисления"

    @property
    def native_value(self) -> float | None:
        return self._account.accruals_total

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "period": self._account.accruals_period,
            **self._account.accruals_breakdown,
        }


class PaymentsSensor(_AccountSensor):
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(
        self, coordinator: EircSpbCoordinator, account: Account
    ) -> None:
        super().__init__(coordinator, account, "payments")

    @property
    def name(self) -> str:
        return "Платежи"

    @property
    def native_value(self) -> float | None:
        return self._account.payments_total

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "payments": [
                {"id": p.payment_id, "date": p.date, "amount": p.amount}
                for p in self._account.recent_payments
            ]
        }


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
        self._account = account
        self._meter = meter
        self._scale = scale
        self._attr_unique_id = (
            f"{DOMAIN}_{account.number}_{meter.meter_id}_{scale.scale_id}"
        )
        self._attr_device_info = _device(account)
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
    def name(self) -> str:
        scale_suffix = f" {self._scale.name}" if self._scale.name else ""
        return f"{self._meter.name}{scale_suffix}"

    @property
    def native_value(self) -> float | None:
        return self._scale.last_reading

    @property
    def extra_state_attributes(self) -> dict:
        return {
            ATTR_ACCOUNT_ID: self._account.account_id,
            ATTR_METER_ID: self._meter.meter_id,
            ATTR_SCALE_ID: self._scale.scale_id,
            "last_submit": self._scale.last_submit,
            "meter_serial": self._meter.serial,
            "verification_date": self._meter.verification_date,
        }
