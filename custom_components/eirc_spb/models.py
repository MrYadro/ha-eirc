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
    balance: float | None = None
    accruals_total: float | None = None
    accruals_period: str | None = None
    accruals_breakdown: dict[str, float] = field(default_factory=dict)
    payments_total: float = 0.0
    recent_payments: list[Payment] = field(default_factory=list)


@dataclass
class BillsPayments:
    balance: float | None
    accruals_total: float | None
    accruals_period: str | None
    accruals_breakdown: dict[str, float]
    payments: list[Payment]


def parse_accounts(raw: list) -> list[Account]:
    return [
        Account(
            account_id=str(item["id"]),
            number=str(item["tenancy"]["register"]),
            address="",
        )
        for item in raw
    ]


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
                scales=scales,
            )
        )
    return meters


def parse_finance(raw: list) -> BillsPayments:
    return BillsPayments(
        balance=round(sum(i["charge"]["balance"]["value"] for i in raw), 2),
        accruals_total=round(sum(i["charge"]["accrued"] for i in raw), 2),
        accruals_period=None,
        accruals_breakdown={
            i["subservice"]["name"]: i["charge"]["accrued"] for i in raw
        },
        payments=[],
    )


def parse_payment(raw: dict) -> Payment:
    return Payment(
        payment_id=str(raw["id"]),
        date=raw["timestamp"],
        amount=round(sum(d["charge"]["accrued"] for d in raw["details"]), 2),
    )


def sum_payments(payments: list[Payment]) -> float:
    return round(sum(p.amount for p in payments), 2)
