"""Replace real PII in raw fixtures with stable fakes. Throwaway tool.

Runs from tests/fixtures/raw/*.json (real captures) into tests/fixtures/.
Follow with scripts/neuter_fixtures.py (JWT neutering + renames).
Values (amounts, readings, dates) are jittered deterministically — field
names are the API contract, values are illustrative only (see NOTES.md).
"""
import json
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

FIXTURES = Path(__file__).parent.parent / "tests" / "fixtures"
RAW = FIXTURES / "raw"

REAL_REGISTER = os.environ.get("REAL_ACCOUNT", "")
REAL_ACCOUNT_ID = os.environ.get("REAL_ACCOUNT_ID", "")
FAKE_REGISTER = "1000000001"
FAKE_ACCOUNT_ID = "910000001"
FAKE_PHONE = "+79990000001"
FAKE_EMAIL = "user1@example.com"
FAKE_MASKED_EMAIL = "use********@example.com"
FAKE_NAME = "Тест"
FAKE_ADDRESS = "ул. Тестовая, д. 1, кв. 1"
FAKE_UUID = "11111111-2222-3333-4444-555555555555"
JITTER = 1.37
DATE_SHIFT = timedelta(days=137)

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
MASKED_EMAIL_RE = re.compile(r"[A-Za-z0-9*]+\*+[A-Za-z0-9*]*@[A-Za-z0-9.-]+")
PHONE_RE = re.compile(r"\+7\d{10}")
DMY_RE = re.compile(r"\d{2}\.\d{2}\.\d{4}")
ISO_TS_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}:\d{2}:\d{2})")
UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)
SERIAL_IN_NAME_RE = re.compile(r"№\s?(\d{4,10})")
COMMA_NUM_RE = re.compile(r"^\d{1,6},\d{1,3}$")

AMOUNT_KEYS = {"amount", "accrued", "previousReading"}
DATEISH = re.compile(r"^(?:\d{2}\.\d{2}\.\d{4})(?:\s.*)?$")


def build_serial_map() -> dict[str, str]:
    serials: list[str] = []
    meters = json.loads((RAW / "meters_info.json").read_text())
    for meter in meters:
        reg = meter.get("id", {}).get("registration")
        if isinstance(reg, str) and reg.isdigit():
            serials.append(reg)
        ser = meter.get("serial")
        if isinstance(ser, str) and ser.isdigit():
            serials.append(ser)
        for match in SERIAL_IN_NAME_RE.finditer(str(meter.get("name", ""))):
            serials.append(match.group(1))
    seen: dict[str, str] = {}
    for serial in serials:
        if serial not in seen:
            seen[serial] = f"{100000 + len(seen)}"
    return seen


def build_name_maps() -> tuple[dict[str, str], dict[str, str]]:
    services: set[str] = set()
    providers: set[str] = set()
    meters = json.loads((RAW / "meters_info.json").read_text())
    for meter in meters:
        services.add(str(meter.get("subservice", {}).get("name", "")))
        for ind in meter.get("indications", []):
            services.add(str(ind.get("scaleName", "")))
        services.add(str(meter.get("name", "")).split(" (ПУ")[0])
    disc = json.loads((RAW / "payments_discretion.json").read_text())
    for item in disc:
        services.add(str(item.get("subservice", {}).get("name", "")))
        providers.add(str(item.get("providerServiceName", "")))
    for pfile in RAW.glob("payment_*.json"):
        pay = json.loads(pfile.read_text())
        for det in pay.get("details", []):
            services.add(str(det.get("subservice", {}).get("name", "")))
    services.discard("")
    providers.discard("")
    service_map = {name: f"Услуга {i + 1}" for i, name in enumerate(sorted(services))}
    provider_map = {name: f'ООО "Тест {i + 1}"' for i, name in enumerate(sorted(providers))}
    return service_map, provider_map


def raw_address() -> str:
    p = RAW / "account_address.json"
    if p.exists():
        return str(json.loads(p.read_text()).get("value", ""))
    return ""


SERIAL_MAP = build_serial_map()
SERVICE_MAP, PROVIDER_MAP = build_name_maps()
TOKENS: list[tuple[str, str]] = [
    (real, fake)
    for real, fake in [
        (REAL_REGISTER, FAKE_REGISTER),
        (REAL_ACCOUNT_ID, FAKE_ACCOUNT_ID),
        (raw_address(), FAKE_ADDRESS),
        *SERIAL_MAP.items(),
    ]
    if real
]


def shift_dmy(match: re.Match) -> str:
    d = datetime.strptime(match.group(0), "%d.%m.%Y").date() - DATE_SHIFT
    return d.strftime("%d.%m.%Y")


def shift_iso(match: re.Match) -> str:
    d = date(int(match.group(1)), int(match.group(2)), int(match.group(3))) - DATE_SHIFT
    return f"{d.isoformat()}T{match.group(4)}"


def scrub_string(value: str) -> str:
    value = MASKED_EMAIL_RE.sub(FAKE_MASKED_EMAIL, value)
    value = EMAIL_RE.sub(FAKE_EMAIL, value)
    value = PHONE_RE.sub(FAKE_PHONE, value)
    value = ISO_TS_RE.sub(shift_iso, value)
    value = DMY_RE.sub(shift_dmy, value)
    for real, fake in TOKENS:
        value = value.replace(real, fake)
    for real, fake in SERVICE_MAP.items():
        value = value.replace(real, fake)
    for real, fake in PROVIDER_MAP.items():
        value = value.replace(real, fake)
    return value


def jitter(value: float, decimals: int = 2) -> float:
    return round(value * JITTER, decimals)


def fake_display_value(value: str) -> str:
    if DATEISH.match(value):
        return DMY_RE.sub(shift_dmy, value)
    if COMMA_NUM_RE.match(value):
        num = float(value.replace(",", "."))
        return f"{jitter(num):,.2f}".replace(".", ",")
    return "Тест"


def scrub(value, key=None, parent=None):
    if isinstance(value, str):
        if parent == "account_details_display":
            return fake_display_value(value) if value else value
        value = scrub_string(value)
        if key == "alias" and value:
            value = FAKE_NAME
        if key == "fullName" and value:
            value = FAKE_NAME
        if key == "transactionId":
            value = FAKE_UUID
        return value
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        scrubbed = scrub_string(str(value))
        return int(scrubbed) if scrubbed.isdigit() else value
    if isinstance(value, float):
        if key in AMOUNT_KEYS or (key == "value" and parent == "balance"):
            return jitter(value, 3 if key == "previousReading" else 2)
        return value
    if isinstance(value, list):
        return [scrub(v, key, parent) for v in value]
    if isinstance(value, dict):
        if {"value", "type"} <= set(value) and parent != "account_details_display":
            out = {k: scrub(v, k, "balance") for k, v in value.items()}
            out["value"] = jitter(out["value"], 2) if isinstance(out["value"], (int, float)) else out["value"]
            return out
        return {k: scrub(v, k, key) for k, v in value.items()}
    return value


def scrub_account_details(data):
    for block in data:
        content = block.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and "value" in item:
                    raw_value = item["value"]
                    if isinstance(raw_value, str):
                        item["value"] = (
                            fake_display_value(scrub_string(raw_value))
                            if raw_value
                            else raw_value
                        )
                    elif isinstance(raw_value, float):
                        item["value"] = jitter(raw_value, 2)
    return data


for path in sorted(RAW.glob("*.json")):
    data = json.loads(path.read_text())
    if path.name == "account_details.json":
        data = scrub_account_details(data)
    data = scrub(data)
    if path.name == "login_stage1.json" or path.name == "confirmation_verify.json":
        text = json.dumps(data, ensure_ascii=False)
        text = UUID_RE.sub(FAKE_UUID, text)
        data = json.loads(text)
    (FIXTURES / path.name).write_text(
        json.dumps(data, ensure_ascii=False, indent=2)
    )
    print(f"sanitized {path.name}")

print(f"tokens={len(TOKENS)} services={len(SERVICE_MAP)} providers={len(PROVIDER_MAP)}")
