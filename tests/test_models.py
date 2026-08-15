import json
from pathlib import Path

from custom_components.eirc_spb.models import (
    Payment,
    parse_accounts,
    parse_finance,
    parse_meters,
    parse_payment,
    sum_payments,
)

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / f"{name}.json").read_text())


def test_parse_accounts():
    accounts = parse_accounts(load("accounts"))
    assert len(accounts) == 1
    acct = accounts[0]
    assert acct.account_id == "910000001"
    assert acct.number == "1000000001"
    assert acct.address == ""
    assert acct.alias == "Тест"
    assert acct.tenancy_full == ""
    assert acct.tenancy_short == "ЕЛС"
    assert acct.balance is None
    assert acct.accruals_total is None
    assert acct.accruals_period is None
    assert acct.accruals_breakdown == {}
    assert acct.payments_total == 0.0
    assert acct.recent_payments == []


def test_parse_meters_and_scales():
    meters = parse_meters(load("meters_info"), account_id="910000001")
    assert len(meters) == 5
    assert all(m.account_id == "910000001" for m in meters)
    meter = meters[0]
    assert meter.meter_id == "100000"
    assert meter.name == "Услуга 5 (ПУ №100001)"
    assert meter.device_class == "water"
    assert meter.unit == "куб.м."
    assert meter.serial == "100001"
    assert meter.subservice_name == "Услуга 5"
    assert meter.verification_date is None
    assert len(meter.scales) == 1
    scale = meter.scales[0]
    assert scale.scale_id == "0"
    assert scale.name == "Услуга 5"
    assert scale.last_reading == 202.156
    assert scale.last_submit == "12.11.2025"


def test_parse_meters_electricity():
    meters = parse_meters(load("meters_info"), account_id="910000001")
    elec = [m for m in meters if m.device_class == "energy"]
    assert len(elec) == 1
    meter = elec[0]
    assert meter.meter_id == "00000999ZU"
    assert meter.unit == "кВт*ч"
    assert len(meter.scales) == 2
    assert [s.scale_id for s in meter.scales] == ["2", "3"]
    assert all(s.last_reading > 0 for s in meter.scales)
    assert all(s.last_submit == "09.03.2026" for s in meter.scales)


def test_parse_finance():
    bp = parse_finance(load("payments_discretion"))
    assert bp.balance == 10458.16
    assert bp.accruals_total == 7633.68
    assert bp.fines == 0.0
    assert bp.accruals_period is None
    assert bp.payments == []
    assert bp.accruals_breakdown["Услуга 5"] == 697.62
    assert bp.accruals_breakdown["Услуга 33"] == 2354.93
    assert "Добровольное тест-страхование" not in bp.accruals_breakdown


def test_parse_finance_excludes_unchecked_fines():
    raw = load("payments_discretion")
    raw[0]["fine"]["balance"]["value"] = 55.55
    bp = parse_finance(raw)
    assert bp.fines == 55.55


def test_parse_payment():
    payment = parse_payment(load("payment_a"))
    assert payment.payment_id == "10900000001"
    assert payment.date == "2026-02-28T10:07:34"
    assert payment.amount == 726.65


def test_sum_payments():
    payments = [
        Payment(payment_id="1", date="2026-01-01", amount=10.10),
        Payment(payment_id="2", date="2026-01-02", amount=20.20),
        Payment(payment_id="3", date="2026-01-03", amount=0.03),
    ]
    assert sum_payments(payments) == 30.33
    assert sum_payments([]) == 0.0
