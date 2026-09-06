import re
from dataclasses import dataclass, field

_UTILITY_DEVICE_CLASS = {"WATER": "water", "ELECTRICITY": "energy"}


@dataclass
class Scale:
    scale_id: str
    name: str | None = None
    last_reading: float | None = None
    last_submit: str | None = None


@dataclass
class Meter:
    meter_id: str
    account_id: str
    name: str
    device_class: str | None = None
    unit: str = ""
    serial: str | None = None
    model: str | None = None
    install_date: str | None = None
    verification_date: str | None = None
    subservice_name: str | None = None
    scales: list[Scale] = field(default_factory=list)


@dataclass
class Account:
    account_id: str
    number: str
    address: str
    alias: str = ""
    tenancy_full: str = ""
    tenancy_short: str = ""
    balance: float | None = None
    accruals_total: float | None = None
    accruals_period: str | None = None
    accruals_breakdown: dict[str, float] = field(default_factory=dict)
    current_bill_amount: float | None = None
    current_bill_id: str | None = None
    fines: float | None = None
    reading_deadline_day: int | None = None
    reading_period_name: str | None = None
    reading_window: str | None = None
    provider_accruals: dict[str, float] = field(default_factory=dict)
    auto_payment: bool | None = None
    delivery: str | None = None
    details: "AccountDetails | None" = None
    bills_history: list[dict] = field(default_factory=list)
    last_payment: dict | None = None


@dataclass
class BillsPayments:
    balance: float | None
    accruals_total: float | None
    accruals_breakdown: dict[str, float]
    fines: float = 0.0
    provider_accruals: dict[str, float] = field(default_factory=dict)


@dataclass
class MeterPassport:
    serial: str
    model: str | None = None
    install_date: str | None = None
    verification_date: str | None = None
    check_interval: str | None = None


@dataclass
class AccountDetails:
    area: str | None = None
    rooms: str | None = None
    owner: str | None = None
    management_company: str | None = None
    tariffs: dict[str, str | float] = field(default_factory=dict)
    meters: dict[str, MeterPassport] = field(default_factory=dict)


def parse_accounts(raw: list) -> list[Account]:
    accounts = []
    for item in raw:
        tenancy = item.get("tenancy") or {}
        name = tenancy.get("name") or {}
        accounts.append(
            Account(
                account_id=str(item["id"]),
                number=str(tenancy["register"]),
                address="",
                alias=str(item.get("alias") or ""),
                tenancy_full=str(name.get("fulled") or ""),
                tenancy_short=str(name.get("shorted") or ""),
                auto_payment=item.get("autoPaymentOn"),
                delivery=item.get("delivery"),
            )
        )
    return accounts


def parse_meters(raw: list, account_id: str) -> list[Meter]:
    meters = []
    for item in raw:
        indications = item.get("indications") or []
        scales = [
            Scale(
                scale_id=str(ind["meterScaleId"]),
                name=ind.get("scaleName"),
                last_reading=ind["registerReading"]
                if ind.get("registerReading") is not None
                else ind.get("previousReading"),
                last_submit=ind.get("previousReadingDate"),
            )
            for ind in indications
        ]
        subservice = item.get("subservice") or {}
        meters.append(
            Meter(
                meter_id=str(item["id"]["registration"]),
                account_id=account_id,
                name=item["name"],
                device_class=_UTILITY_DEVICE_CLASS.get(subservice.get("utility")),
                unit=indications[0]["unit"] if indications else "",
                serial=item.get("serial"),
                verification_date=None,
                subservice_name=subservice.get("name"),
                scales=scales,
            )
        )
    return meters


def parse_finance(raw: list) -> BillsPayments:
    checked = [i for i in raw if i.get("checked")]
    return BillsPayments(
        balance=round(sum(i["charge"]["balance"]["value"] for i in checked), 2),
        accruals_total=round(sum(i["charge"]["accrued"] for i in checked), 2),
        accruals_breakdown={
            i["subservice"]["name"]: i["charge"]["accrued"] for i in checked
        },
        fines=round(
            sum(
                (i["fine"]["balance"]["value"] or 0) for i in checked
            ),
            2,
        ),
        provider_accruals={
            provider: round(sum(i["charge"]["accrued"] for i in group), 2)
            for provider, group in _group_by_provider(checked).items()
        },
    )


def _group_by_provider(entries: list) -> dict[str, list]:
    groups: dict[str, list] = {}
    for entry in entries:
        provider = str(entry.get("providerServiceName") or "")
        if provider:
            groups.setdefault(provider, []).append(entry)
    return groups


_METER_HEADER_RE = re.compile(r"приборе учёта\s*№\s*(\S+)")
_RATE_RE = re.compile(r"^\d{1,6},\d{1,3}$")
_APARTMENT_FIELDS = {
    "Общая площадь": "area",
    "Кол-во комнат": "rooms",
    "Владелец": "owner",
    "Наименование УК": "management_company",
}
_PASSPORT_BY_CODE = {
    "METER_MODEL": "model",
    "METER_DATE": "install_date",
    "METER_CHECK_DATE": "verification_date",
    "CHECK_INTERVAL": "check_interval",
}
_PASSPORT_BY_NAME = {
    "Модель": "model",
    "Дата установки": "install_date",
    "Дата истечения поверки": "verification_date",
    "МПИ (лет)": "check_interval",
}


def _rate(value: str) -> str | float:
    value = value.strip()
    if _RATE_RE.match(value):
        return float(value.replace(",", "."))
    return value


def parse_details(raw: list) -> AccountDetails:
    details = AccountDetails()
    for block in raw:
        header = str(block.get("header") or "")
        content = block.get("content") or []
        if header == "Информация о жилом помещении":
            for item in content:
                field = _APARTMENT_FIELDS.get(str(item.get("name") or ""))
                if field and item.get("value") is not None:
                    setattr(details, field, str(item["value"]))
        elif match := _METER_HEADER_RE.search(header):
            passport = MeterPassport(serial=match.group(1))
            for item in content:
                key = _PASSPORT_BY_CODE.get(str(item.get("code") or "")) or (
                    _PASSPORT_BY_NAME.get(str(item.get("name") or ""))
                )
                if key and item.get("value") is not None:
                    setattr(passport, key, str(item["value"]))
            details.meters[passport.serial] = passport
        else:
            for item in content:
                if item.get("name") == "Тарифная ставка" and item.get("value"):
                    details.tariffs[header] = _rate(str(item["value"]))
    return details
