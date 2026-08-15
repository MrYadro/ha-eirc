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
    verification_date: str | None = None
    subservice_name: str | None = None
    scales: list[Scale] = field(default_factory=list)


@dataclass
class Payment:
    payment_id: str
    date: str
    amount: float


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
    payments_total: float = 0.0
    recent_payments: list[Payment] = field(default_factory=list)
    current_bill_amount: float | None = None
    current_bill_id: str | None = None
    fines: float | None = None
    reading_deadline_day: int | None = None
    reading_period_name: str | None = None
    reading_window: str | None = None
    provider_accruals: dict[str, float] = field(default_factory=dict)


@dataclass
class BillsPayments:
    balance: float | None
    accruals_total: float | None
    accruals_period: str | None
    accruals_breakdown: dict[str, float]
    payments: list[Payment]
    fines: float = 0.0
    provider_accruals: dict[str, float] = field(default_factory=dict)


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
        accruals_period=None,
        accruals_breakdown={
            i["subservice"]["name"]: i["charge"]["accrued"] for i in checked
        },
        payments=[],
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


def parse_payment(raw: dict) -> Payment:
    return Payment(
        payment_id=str(raw["id"]),
        date=raw["timestamp"],
        amount=round(sum(d["charge"]["accrued"] for d in raw["details"]), 2),
    )


def sum_payments(payments: list[Payment]) -> float:
    return round(sum(p.amount for p in payments), 2)
