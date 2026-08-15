from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.eirc_spb.const import (
    CONF_ACCOUNTS,
    CONF_LOGIN,
    CONF_PASSWORD,
    DOMAIN,
)
from custom_components.eirc_spb.coordinator import EircSpbData
from custom_components.eirc_spb.models import (
    Account,
    BillsPayments,
    Meter,
    Payment,
    Scale,
)

ENTRY_DATA = {
    CONF_LOGIN: "user1@example.com",
    CONF_PASSWORD: "pw",
    CONF_ACCOUNTS: ["a1"],
}

FINANCE = BillsPayments(
    balance=150.25,
    accruals_total=2000.0,
    accruals_period=None,
    accruals_breakdown={"Услуга 5": 500.0, "Услуга 7": 1500.0},
    payments=[Payment("p1", "2026-02-28T10:07:34", 700.0)],
)
BILL = {"timestamp": "14.02.2026 00:00:00"}
READING_PERIOD = {
    "acceptanceParameters": {
        "name": "Август 2026",
        "interval": {"dateFrom": "17.03.2026", "dateTo": "16.04.2026"},
        "deadLine": 11,
    },
    "forbidden": False,
    "message": None,
}


def build_data() -> EircSpbData:
    account = Account(
        account_id="a1",
        number="1000000001",
        address="ул. Тестовая, д. 1",
        balance=150.25,
        accruals_total=2000.0,
        accruals_period="14.02.2026 00:00:00",
        accruals_breakdown={"Услуга 5": 500.0, "Услуга 7": 1500.0},
        payments_total=700.0,
        recent_payments=[Payment("p1", "2026-02-28T10:07:34", 700.0)],
    )
    meters = {
        "m1": Meter(
            meter_id="m1",
            account_id="a1",
            name="Услуга 5 (ПУ №100000)",
            device_class="water",
            unit="куб.м.",
            serial="100000",
            verification_date=None,
            scales=[
                Scale(
                    scale_id="0",
                    name="Услуга 5",
                    last_reading=123.45,
                    last_submit="12.11.2025",
                )
            ],
        ),
        "m2": Meter(
            meter_id="m2",
            account_id="a1",
            name="Электроэнергия",
            device_class="energy",
            unit="кВт.ч",
            serial="200000",
            verification_date=None,
            scales=[
                Scale(scale_id="2", name="T1", last_reading=100.0),
                Scale(scale_id="3", name="T2", last_reading=200.0),
            ],
        ),
        "m3": Meter(
            meter_id="m3",
            account_id="a1",
            name="Прочее",
            device_class=None,
            unit="л",
            serial="300000",
            verification_date=None,
            scales=[Scale(scale_id="5", name=None, last_reading=7.0)],
        ),
    }
    return EircSpbData(accounts={"a1": account}, meters=meters)


async def setup_sensors(hass: HomeAssistant, data: EircSpbData | None = None) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA)
    entry.add_to_hass(hass)
    coordinator = MagicMock()
    coordinator.data = data or build_data()
    coordinator.async_config_entry_first_refresh = AsyncMock()
    with (
        patch(
            "custom_components.eirc_spb.EircSpbApiClient",
            return_value=MagicMock(),
        ),
        patch(
            "custom_components.eirc_spb.EircSpbCoordinator",
            return_value=coordinator,
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


def state_for(hass: HomeAssistant, unique_id: str) -> State:
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, unique_id)
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    return state


async def test_balance_sensor(hass: HomeAssistant):
    await setup_sensors(hass)
    state = state_for(hass, "eirc_spb_1000000001_balance")
    assert float(state.state) == 150.25
    assert state.attributes["unit_of_measurement"] == "RUB"
    assert state.attributes["device_class"] == "monetary"
    assert state.attributes["state_class"] == "total"


async def test_accruals_sensor(hass: HomeAssistant):
    await setup_sensors(hass)
    state = state_for(hass, "eirc_spb_1000000001_accruals")
    assert float(state.state) == 2000.0
    assert state.attributes["unit_of_measurement"] == "RUB"
    assert state.attributes["period"] == "14.02.2026 00:00:00"
    assert state.attributes["Услуга 5"] == 500.0
    assert state.attributes["Услуга 7"] == 1500.0


async def test_payments_sensor(hass: HomeAssistant):
    await setup_sensors(hass)
    state = state_for(hass, "eirc_spb_1000000001_payments")
    assert float(state.state) == 700.0
    assert state.attributes["state_class"] == "total_increasing"
    assert state.attributes["payments"] == [
        {"id": "p1", "date": "2026-02-28T10:07:34", "amount": 700.0}
    ]


async def test_water_meter_sensor(hass: HomeAssistant):
    await setup_sensors(hass)
    state = state_for(hass, "eirc_spb_1000000001_m1_0")
    assert float(state.state) == 123.45
    assert state.attributes["device_class"] == "water"
    assert state.attributes["state_class"] == "total_increasing"
    assert state.attributes["unit_of_measurement"] == "m³"
    assert state.attributes["account_id"] == "a1"
    assert state.attributes["meter_id"] == "m1"
    assert state.attributes["scale_id"] == "0"
    assert state.attributes["last_submit"] == "12.11.2025"
    assert state.attributes["meter_serial"] == "100000"
    assert state.attributes["verification_date"] is None


async def test_energy_meter_sensors(hass: HomeAssistant):
    await setup_sensors(hass)
    t1 = state_for(hass, "eirc_spb_1000000001_m2_2")
    assert float(t1.state) == 100.0
    t2 = state_for(hass, "eirc_spb_1000000001_m2_3")
    assert float(t2.state) == 200.0
    assert t2.attributes["device_class"] == "energy"
    assert t2.attributes["state_class"] == "total_increasing"
    assert t2.attributes["unit_of_measurement"] == "kWh"
    assert t2.attributes["meter_id"] == "m2"
    assert t2.attributes["scale_id"] == "3"


async def test_unknown_device_class_meter(hass: HomeAssistant):
    await setup_sensors(hass)
    state = state_for(hass, "eirc_spb_1000000001_m3_5")
    assert float(state.state) == 7.0
    assert "device_class" not in state.attributes
    assert "state_class" not in state.attributes
    assert state.attributes["unit_of_measurement"] == "л"


async def test_device_registered_per_account(hass: HomeAssistant):
    await setup_sensors(hass)
    device = dr.async_get(hass).async_get_device({(DOMAIN, "a1")})
    assert device is not None
    assert device.name == "ЕИРЦ 1000000001"
    assert device.model == "ул. Тестовая, д. 1"


def make_client() -> AsyncMock:
    client = AsyncMock()
    account = build_data().accounts["a1"]
    account.balance = None
    account.accruals_total = None
    account.accruals_breakdown = {}
    account.payments_total = 0.0
    account.recent_payments = []
    client.get_accounts.return_value = [account]
    client.get_address.return_value = "ул. Тестовая, д. 1"
    client.get_finance.return_value = FINANCE
    client.get_current_bill.return_value = BILL
    client.get_payments.return_value = [Payment("p1", "2026-02-28T10:07:34", 700.0)]
    client.get_meters.return_value = list(build_data().meters.values())
    client.get_reading_period.return_value = READING_PERIOD
    return client


async def test_coordinator_refresh_updates_states(hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA)
    entry.add_to_hass(hass)
    client = make_client()
    with patch(
        "custom_components.eirc_spb.EircSpbApiClient",
        return_value=client,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    balance = state_for(hass, "eirc_spb_1000000001_balance")
    assert float(balance.state) == 150.25
    meter_state = state_for(hass, "eirc_spb_1000000001_m1_0")
    assert float(meter_state.state) == 123.45

    refreshed_meter = Meter(
        meter_id="m1",
        account_id="a1",
        name="Услуга 5 (ПУ №100000)",
        device_class="water",
        unit="куб.м.",
        serial="100000",
        scales=[
            Scale(
                scale_id="0",
                name="Услуга 5",
                last_reading=150.0,
                last_submit="12.11.2025",
            )
        ],
    )
    client.get_finance.return_value = BillsPayments(
        balance=999.0,
        accruals_total=2000.0,
        accruals_period=None,
        accruals_breakdown={},
        payments=[],
    )
    client.get_meters.return_value = [refreshed_meter]
    coordinator = hass.data[DOMAIN][entry.entry_id].coordinator
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    balance = state_for(hass, "eirc_spb_1000000001_balance")
    assert float(balance.state) == 999.0
    meter_state = state_for(hass, "eirc_spb_1000000001_m1_0")
    assert float(meter_state.state) == 150.0


def test_entity_precision_contract():
    from custom_components.eirc_spb import sensor

    coordinator = MagicMock()
    data = build_data()
    account = data.accounts["a1"]
    water = data.meters["m1"]
    assert (
        sensor.BalanceSensor(coordinator, account)._attr_suggested_display_precision
        == 2
    )
    assert (
        sensor.AccrualsSensor(coordinator, account)._attr_suggested_display_precision
        == 2
    )
    assert (
        sensor.PaymentsSensor(coordinator, account)._attr_suggested_display_precision
        == 2
    )
    unknown = data.meters["m3"]
    unknown_entity = sensor.MeterSensor(
        coordinator, account, unknown, unknown.scales[0]
    )
    assert unknown_entity._attr_suggested_display_precision == 3
    assert unknown_entity.device_class is None
    assert unknown_entity.state_class is None
    assert unknown_entity.native_unit_of_measurement == "л"


async def test_new_account_sensors(hass: HomeAssistant):
    data = build_data()
    account = data.accounts["a1"]
    account.current_bill_amount = 7633.65
    account.current_bill_id = "26071000000001"
    account.fines = 12.5
    account.reading_deadline_day = 11
    account.reading_period_name = "Август 2026"
    account.reading_window = "17.03.2026 – 16.04.2026"
    await setup_sensors(hass, data)

    erreg = er.async_get(hass)

    bill = hass.states.get(
        erreg.async_get_entity_id("sensor", DOMAIN, "eirc_spb_1000000001_bill")
    )
    assert bill is not None
    assert float(bill.state) == 7633.65
    assert bill.attributes["unit_of_measurement"] == "RUB"
    assert bill.attributes["bill_id"] == "26071000000001"
    assert bill.attributes["timestamp"] == "14.02.2026 00:00:00"

    fines = hass.states.get(
        erreg.async_get_entity_id("sensor", DOMAIN, "eirc_spb_1000000001_fines")
    )
    assert float(fines.state) == 12.5

    deadline = hass.states.get(
        erreg.async_get_entity_id(
            "sensor", DOMAIN, "eirc_spb_1000000001_reading_deadline"
        )
    )
    assert deadline.state == "11"
    assert deadline.attributes["period"] == "Август 2026"
    assert deadline.attributes["window"] == "17.03.2026 – 16.04.2026"
