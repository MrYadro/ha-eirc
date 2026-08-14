# EIRC SPb Home Assistant Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A HACS-ready Home Assistant integration (`eirc_spb`) exposing balance, accruals, payments, and meter-reading sensors from the ЕИРЦ СПб cabinet (ikus.pesc.ru), plus a service to submit meter readings.

**Architecture:** Three layers — pure aiohttp API client (`api.py` + `auth.py`, no HA imports), a `DataUpdateCoordinator` polling all selected лицевые счета, and HA glue (config/options flows, sensors, service). Real API responses are captured as fixtures first; all tests replay fixtures, no network in CI.

**Tech Stack:** Python 3.13, aiohttp (ships with HA), Home Assistant 2026.8.x custom-integration APIs, `pytest-homeassistant-custom-component==0.13.355` (pins HA 2026.8.1), `aresponses` for HTTP mocking.

**Spec:** `docs/superpowers/specs/2026-08-15-eirc-spb-integration-design.md` — read it before starting; this plan argues from it.

## Global Constraints

- Domain / unique-id prefix: `eirc_spb` (spec §1).
- Python 3.13; runtime requirements: `[]` (aiohttp ships with HA) (spec §8).
- Test pin: `pytest-homeassistant-custom-component==0.13.355` (HA 2026.8.1) — exact version.
- Base URL: `https://ikus.pesc.ru/api/` — exact value.
- Auth headers (confirmed from SPA bundle): `Captcha: none` (sentinel), `withTotp: true`, `Auth-Verification: <token>` (spec §2).
- Login-confirmation endpoints (SPA bundle, more precise than spec §2 — bundle shows these next to the auth service): `POST v7/users/{sessionId}/{channel}/check/confirmation/send` (empty body) and `POST v7/users/{sessionId}/{channel}/check/verification` with `{"code": "..."}`. The `v6/users/{id}/{phone|email}/{availability}/confirmation/send` path in the spec is the contact-change flow — do NOT use it for login confirmation.
- Default scan interval 12h, min 1h (spec §3).
- No comments in code unless asked. No PII in committed fixtures — sanitizer step is mandatory before commit (Task 2).
- All work in the worktree `.worktrees/eirc-spb-integration`, branch `eirc-spb-integration`.
- Task 2 is HUMAN-IN-THE-LOOP (real credentials + SMS/email code). The orchestrator executes it with the user directly — never dispatch a subagent for Task 2.

## File Structure

```
custom_components/eirc_spb/
├── __init__.py        # async_setup_entry/unload, services + coordinator wiring
├── api.py             # EircSpbApiClient — data endpoints, _request() w/ 401 handling
├── auth.py            # Authenticator — login/confirm/verify/refresh/relogin
├── models.py          # dataclasses + from-dict parsers
├── coordinator.py     # EircSpbCoordinator, EircSpbData
├── config_flow.py     # user → confirm → code → select_accounts, reauth
├── options_flow.py    # scan interval, password change
├── sensor.py          # account + meter sensors, one device per лицевой счёт
├── services.py        # eirc_spb.send_meter_reading
├── const.py           # DOMAIN, CONF_*, headers, URLs, defaults
├── exceptions.py      # EircSpbAuthError, EircSpbApiError, EircSpbConfirmationError
├── manifest.json
└── translations/{ru-RU,en}.json
scripts/capture_fixtures.py   # throwaway, NOT shipped
scripts/sanitize_fixtures.py  # throwaway, NOT shipped
tests/
├── conftest.py
├── fixtures/*.json            # sanitized captures (committed)
├── test_scaffold.py, test_models.py, test_api.py, test_coordinator.py,
├── test_config_flow.py, test_sensor.py, test_services.py
hacs.json, README.md, LICENSE, .github/workflows/ci.yml, pyproject.toml, requirements_test.txt
```

---

### Task 1: Repository scaffolding

**Files:**
- Create: `custom_components/eirc_spb/manifest.json`, `const.py`, `exceptions.py`, `__init__.py` (empty placeholder)
- Create: `hacs.json`, `LICENSE`, `pyproject.toml`, `requirements_test.txt`, `.github/workflows/ci.yml`, `README.md` (stub), `.gitignore` update
- Test: `tests/conftest.py`, `tests/test_scaffold.py`

**Interfaces:**
- Produces: `DOMAIN = "eirc_spb"` (const.py), `EircSpbAuthError`, `EircSpbApiError`, `EircSpbConfirmationError` (exceptions.py) — used by every later task. Test harness (`pytest`) proven green.

- [ ] **Step 1: Write the failing test**

`tests/conftest.py`:

```python
"""Enable custom integrations for pytest-homeassistant-custom-component."""
import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield
```

`tests/test_scaffold.py`:

```python
import json
from pathlib import Path

COMPONENT = Path(__file__).parent.parent / "custom_components" / "eirc_spb"


def test_manifest_valid():
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["domain"] == "eirc_spb"
    assert manifest["requirements"] == []
    assert manifest["version"] == "1.0.0"
    assert "sensor" in manifest["dependencies"] or "sensor" in manifest.get(
        "after_dependencies", []
    )


def test_const_domain():
    from custom_components.eirc_spb.const import DOMAIN

    assert DOMAIN == "eirc_spb"


def test_exceptions_exist():
    from custom_components.eirc_spb.exceptions import (
        EircSpbApiError,
        EircSpbAuthError,
        EircSpbConfirmationError,
    )

    assert issubclass(EircSpbAuthError, EircSpbApiError)
    assert issubclass(EircSpbConfirmationError, EircSpbApiError)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.13 -m pytest tests/test_scaffold.py -v`
Expected: FAIL — `custom_components/eirc_spb` missing.

- [ ] **Step 3: Write minimal implementation**

`custom_components/eirc_spb/manifest.json`:

```json
{
  "domain": "eirc_spb",
  "name": "EIRC SPb",
  "codeowners": ["yaroslav"],
  "config_flow": true,
  "documentation": "https://github.com/yaroslav/ha-eirc",
  "integration_type": "hub",
  "iot_class": "cloud_polling",
  "issue_tracker": "https://github.com/yaroslav/ha-eirc/issues",
  "requirements": [],
  "version": "1.0.0"
}
```

`custom_components/eirc_spb/const.py`:

```python
DOMAIN = "eirc_spb"

BASE_URL = "https://ikus.pesc.ru/api"

CONF_LOGIN = "login"
CONF_PASSWORD = "password"
CONF_VERIFICATION_TOKEN = "verification_token"
CONF_ACCOUNTS = "accounts"

DEFAULT_SCAN_INTERVAL_HOURS = 12
MIN_SCAN_INTERVAL_HOURS = 1

HEADER_CAPTCHA = "Captcha"
HEADER_CAPTCHA_NONE = "none"
HEADER_WITH_TOTP = "withTotp"
HEADER_AUTH_VERIFICATION = "Auth-Verification"

USER_AGENT = "home-assistant-eirc-spb/1.0.0"
REQUEST_TIMEOUT_SECONDS = 30

ATTR_ACCOUNT_ID = "account_id"
ATTR_METER_ID = "meter_id"
ATTR_SCALE_ID = "scale_id"
```

`custom_components/eirc_spb/exceptions.py`:

```python
class EircSpbApiError(Exception):
    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class EircSpbAuthError(EircSpbApiError):
    pass


class EircSpbConfirmationError(EircSpbApiError):
    pass
```

`custom_components/eirc_spb/__init__.py`: leave empty for now (filled in Task 5).

`hacs.json`:

```json
{
  "name": "EIRC SPb",
  "render_readme": true,
  "homeassistant": "2026.8.0"
}
```

`LICENSE`: MIT stub with copyright `2026 yaroslav`.

`requirements_test.txt`:

```
pytest-homeassistant-custom-component==0.13.355
aresponses==3.0.0
```

`pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
norecursedirs = [".git", ".worktrees"]
```

`.github/workflows/ci.yml`:

```yaml
name: CI
on:
  push:
    branches: [main, eirc-spb-integration]
  pull_request:
jobs:
  tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
      - run: pip install -r requirements_test.txt
      - run: pytest
  hassfest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: home-assistant/actions/hassfest@master
  hacs:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: hacs/action@main
        with:
          category: integration
```

`README.md` stub (Russian, expanded in Task 9):

```markdown
# ЕИРЦ СПб (eirc_spb) для Home Assistant

Интеграция личного кабинета ЕИРЦ СПб (ikus.pesc.ru): баланс, начисления,
платежи, показания счётчиков; отправка показаний.

Документация будет добавлена.
```

Append to `.gitignore`: `scripts/.env`, `tests/fixtures/raw/`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.13 -m pytest tests/ -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add custom_components tests hacs.json LICENSE pyproject.toml requirements_test.txt .github .gitignore README.md
git commit -m "feat: scaffold eirc_spb integration, CI and test harness"
```

---

### Task 2: Capture real API fixtures (HUMAN-IN-THE-LOOP — orchestrator + user only)

**Files:**
- Create: `scripts/capture_fixtures.py`, `scripts/sanitize_fixtures.py`
- Create (from capture, sanitized): `tests/fixtures/*.json`
- Raw (gitignored): `tests/fixtures/raw/*.json`

**Interfaces:**
- Produces: `tests/fixtures/*.json` — the source of truth every later test and parser reads. Expected files (names fixed): `login_stage1.json`, `confirmation_send.json`, `confirmation_verify.json`, `login_with_token.json`, `accounts.json`, `connection_objects.json`, `indications.json`, `upload_history.json`, `bills_payments.json`.

**How it runs:** user executes `python3.13 scripts/capture_fixtures.py` locally with real credentials in `scripts/.env` (gitignored). Script walks the full auth sequence interactively (prompts for the OTP code on stdin), then dumps every endpoint response to `tests/fixtures/raw/`. Then orchestrator runs the sanitizer and reviews the diff with the user before committing.

- [ ] **Step 1: Write the capture script**

`scripts/capture_fixtures.py`:

```python
"""Capture ikus.pesc.ru API responses as test fixtures. Throwaway tool."""
import asyncio
import json
import os
import sys
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

BASE = "https://ikus.pesc.ru/api"
RAW = Path(__file__).parent.parent / "tests" / "fixtures" / "raw"
RAW.mkdir(parents=True, exist_ok=True)

LOGIN = os.environ["EIRC_LOGIN"]
PASSWORD = os.environ["EIRC_PASSWORD"]


def dump(name: str, data) -> None:
    (RAW / f"{name}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2)
    )
    print(f"saved {name}.json")


def base_headers(verification_token: str | None = None) -> dict:
    h = {
        "Content-Type": "application/json",
        "Captcha": "none",
        "withTotp": "true",
        "User-Agent": "fixture-capture/1.0",
    }
    if verification_token:
        h["Auth-Verification"] = verification_token
    return h


async def req(session, method, path, body=None, headers=None):
    async with session.request(
        method, f"{BASE}/{path}", json=body, headers=headers
    ) as r:
        text = await r.text()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = text
        print(f"{method} {path} -> {r.status}")
        if r.status >= 400:
            print(json.dumps(data, ensure_ascii=False)[:800])
        return r.status, data, r.headers


async def main():
    async with aiohttp.ClientSession() as s:
        status, stage1, _ = await req(
            s, "POST", "v8/users/auth",
            {"login": LOGIN, "password": PASSWORD}, base_headers(),
        )
        dump("login_stage1", stage1)
        if status != 200:
            sys.exit("stage-1 login failed — dump above; adjust body/headers")

        print("stage1 keys:", list(stage1) if isinstance(stage1, dict) else type(stage1))
        token = stage1.get("access") or stage1.get("token") or stage1
        needs_conf = "code" in json.dumps(stage1)[:2000] or stage1.get("type") not in (None, "AUTHORIZED")
        print("needs confirmation?", needs_conf)

        verification = None
        if needs_conf:
            session_id = stage1.get("id") or stage1.get("sessionId") or input("session id: ")
            channels = stage1.get("channels") or [
                {"type": c} for c in input("channels (comma-sep, e.g. sms,email,call): ").split(",")
            ]
            for ch in channels:
                ctype = ch.get("type") if isinstance(ch, dict) else ch
                print("channel:", ctype, ch if isinstance(ch, dict) else "")
            ctype = input("choose channel type: ").strip()
            status, sent, _ = await req(
                s, "POST",
                f"v7/users/{session_id}/{ctype.lower()}/check/confirmation/send",
                {}, base_headers(),
            )
            dump("confirmation_send", sent)
            code = input("code from message/call: ").strip()
            status, verified, _ = await req(
                s, "POST",
                f"v7/users/{session_id}/{ctype.lower()}/check/verification",
                {"code": code}, base_headers(),
            )
            dump("confirmation_verify", verified)
            token = verified.get("access") or verified.get("token") or token
            verification = verified.get("verified") or verified.get("verificationToken")

        auth_headers = {"Authorization": f"Bearer {token}", "User-Agent": "fixture-capture/1.0"}
        if verification:
            auth_headers["Auth-Verification"] = verification

        status, accounts, _ = await req(s, "GET", "v8/accounts", headers=auth_headers)
        dump("accounts", accounts)
        acct = accounts[0] if isinstance(accounts, list) else accounts
        acct_id = acct.get("id") or acct.get("accountId") or input("account id: ")

        status, co, _ = await req(
            s, "GET", f"v1/mes/csp/connection-objects?accountId={acct_id}", headers=auth_headers
        )
        dump("connection_objects", co)
        status, ind, _ = await req(
            s, "GET", f"v1/mes/csp/indications?accountId={acct_id}", headers=auth_headers
        )
        dump("indications", ind)
        status, hist, _ = await req(
            s, "GET",
            f"v1/mes/csp/indications/upload/history?accountId={acct_id}",
            headers=auth_headers,
        )
        dump("upload_history", hist)
        status, bills, _ = await req(
            s, "GET", f"v7/bills/payments?accountId={acct_id}", headers=auth_headers
        )
        dump("bills_payments", bills)

        status, again, _ = await req(
            s, "POST", "v8/users/auth",
            {"login": LOGIN, "password": PASSWORD},
            base_headers(verification),
        )
        dump("login_with_token", again)
        print("repeat-login status (OTP skipped?)", status)


if __name__ == "__main__":
    asyncio.run(main())
```

Add `python-dotenv` note: `pip install aiohttp python-dotenv`.

- [ ] **Step 2: Write the sanitizer**

`scripts/sanitize_fixtures.py`:

```python
"""Replace real PII in raw fixtures with stable fakes. Throwaway tool."""
import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

FIXTURES = Path(__file__).parent.parent / "tests" / "fixtures"
RAW = FIXTURES / "raw"

REAL_PHONE = os.environ.get("EIRC_LOGIN", "")
REAL_ACCOUNT = os.environ.get("REAL_ACCOUNT", "")
FAKE_PHONE = "+79990000001"
FAKE_ACCOUNT = "1000000001"
FAKE_EMAIL = "user1@example.com"
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE_RE = re.compile(r"\+7\d{10}")


def scrub(value):
    if isinstance(value, str):
        value = PHONE_RE.sub(FAKE_PHONE, value)
        value = EMAIL_RE.sub(FAKE_EMAIL, value)
        if REAL_ACCOUNT and REAL_ACCOUNT in value:
            value = value.replace(REAL_ACCOUNT, FAKE_ACCOUNT)
        return value
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    return value


for path in RAW.glob("*.json"):
    data = json.loads(path.read_text())
    (FIXTURES / path.name).write_text(
        json.dumps(scrub(data), ensure_ascii=False, indent=2)
    )
    print(f"sanitized {path.name}")
```

- [ ] **Step 3: Run capture WITH THE USER**

Orchestrator: ask the user to create `scripts/.env` (`EIRC_LOGIN`, `EIRC_PASSWORD`) and run `python3.13 scripts/capture_fixtures.py`, entering the OTP code when prompted. If stage-1 login 400s, read the dumped error, adjust the body field names in the script (candidates: `login`/`username`, `password`/`pwd`), re-run. Record: exact login body that worked, whether confirmation was required, channel values, code length, and whether `login_with_token` skipped OTP. Append findings to `tests/fixtures/NOTES.md` (sanitized).

- [ ] **Step 4: Sanitize and review**

Run: `python3.13 scripts/sanitize_fixtures.py`
Then: `git diff --no-index tests/fixtures/raw/login_stage1.json tests/fixtures/login_stage1.json` style spot-checks + manual read of every sanitized file **with the user** — confirm no names, addresses, real numbers remain. Address strings that survive scrubbing get manually edited in the sanitized file to `"ул. Тестовая, 1, кв. 1"`.

- [ ] **Step 5: Verify fixture set complete and commit**

Verify all 9 expected fixture files exist and parse: `python3.13 -c "import json,pathlib; [json.loads(p.read_text()) for p in pathlib.Path('tests/fixtures').glob('*.json')]"`

```bash
git add scripts tests/fixtures .gitignore
git commit -m "test: capture and sanitize ikus API fixtures"
```

---

### Task 3: Data models + fixture parsers

**Files:**
- Create: `custom_components/eirc_spb/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: `tests/fixtures/*.json` (Task 2). Field names below are best-knowledge; **where a fixture differs, the fixture wins** — adjust parser and test expectations to the real fixture files, keeping the dataclass attribute names fixed (later tasks depend on them).
- Produces (used by Tasks 4–8): `Account(account_id, number, address, balance, accruals_total, accruals_period, accruals_breakdown, payments_total, recent_payments)`, `Meter(meter_id, account_id, name, device_class, unit, serial, verification_date, scales)`, `Scale(scale_id, name, last_reading, last_submit)`, `Payment(payment_id, date, amount)`, `BillsPayments(balance, accruals_total, accruals_period, accruals_breakdown, payments)`; functions `parse_accounts(list[dict]) -> list[Account]`, `parse_meters(list[dict], account_id) -> list[Meter]`, `parse_bills_payments(dict) -> BillsPayments`, `sum_payments(list[Payment]) -> float`.

- [ ] **Step 1: Write the failing test**

`tests/test_models.py`:

```python
import json
from pathlib import Path

from custom_components.eirc_spb.models import (
    parse_accounts,
    parse_bills_payments,
    parse_meters,
)

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / f"{name}.json").read_text())


def test_parse_accounts():
    accounts = parse_accounts(load("accounts"))
    assert len(accounts) >= 1
    acct = accounts[0]
    assert acct.account_id
    assert acct.number
    assert acct.address
    assert acct.balance is None
    assert acct.payments_total == 0.0


def test_parse_meters_and_scales():
    meters = parse_meters(load("connection_objects"), account_id="1000000001")
    assert len(meters) >= 1
    meter = meters[0]
    assert meter.meter_id
    assert meter.account_id == "1000000001"
    assert meter.unit
    assert len(meter.scales) >= 1
    assert all(s.scale_id for s in meter.scales)


def test_parse_bills_payments():
    bp = parse_bills_payments(load("bills_payments"))
    assert bp.balance is not None
    assert isinstance(bp.payments, list)
    assert bp.accruals_total is not None or bp.accruals_breakdown == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.13 -m pytest tests/test_models.py -v`
Expected: FAIL — `custom_components.eirc_spb.models` missing.

- [ ] **Step 3: Write minimal implementation**

`custom_components/eirc_spb/models.py` — write parsers against the ACTUAL fixture field names (check `tests/fixtures/*.json` first; the mapping block below documents best-guess names — fix `MAP_*` to match reality, keep dataclass attrs stable):

```python
from dataclasses import dataclass, field


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
    accounts = []
    for item in raw:
        accounts.append(
            Account(
                account_id=str(item.get("id") or item.get("accountId")),
                number=str(item.get("number") or item.get("accountNumber")),
                address=str(item.get("address") or item.get("fullAddress") or ""),
            )
        )
    return accounts


def parse_meters(raw, account_id: str) -> list[Meter]:
    meters = []
    objects = raw if isinstance(raw, list) else raw.get("objects", raw.get("data", []))
    for item in objects:
        scales = [
            Scale(
                scale_id=str(s.get("id") or s.get("scaleId")),
                name=s.get("name"),
                last_reading=_num(s.get("lastIndication") or s.get("value")),
                last_submit=s.get("date") or s.get("sendDate"),
            )
            for s in item.get("scales", item.get("tariffs", [{}]))
        ]
        meters.append(
            Meter(
                meter_id=str(item.get("id") or item.get("connectionObjectId")),
                account_id=account_id,
                name=str(item.get("name") or item.get("deviceName") or "meter"),
                device_class=_guess_device_class(item, scales),
                unit=item.get("unit") or item.get("measure") or "",
                serial=item.get("serialNumber") or item.get("number"),
                verification_date=item.get("verificationDate")
                or item.get("nextVerification"),
                scales=scales,
            )
        )
    return meters


def parse_bills_payments(raw: dict) -> BillsPayments:
    payments = [
        Payment(
            payment_id=str(p.get("id") or p.get("paymentId")),
            date=str(p.get("date") or p.get("paymentDate")),
            amount=float(p.get("amount") or p.get("sum") or 0),
        )
        for p in raw.get("payments", [])
    ]
    return BillsPayments(
        balance=_num(raw.get("balance") or raw.get("currentBalance")),
        accruals_total=_num(raw.get("accruals") or raw.get("chargesTotal")),
        accruals_period=raw.get("period"),
        accruals_breakdown={
            str(s.get("name")): float(s.get("amount") or s.get("sum") or 0)
            for s in raw.get("services", raw.get("accrualsBreakdown", []))
        },
        payments=payments,
    )


def sum_payments(payments: list[Payment]) -> float:
    return round(sum(p.amount for p in payments), 2)


def _num(value) -> float | None:
    if value is None:
        return None
    return float(value)


def _guess_device_class(item: dict, scales: list[Scale]) -> str | None:
    text = " ".join(
        str(item.get(k) or "") for k in ("name", "type", "serviceName")
    ).lower()
    if "электро" in text or "kwh" in text.lower() or "ток" in text:
        return "energy"
    if "вода" in text or "холод" in text or "горяч" in text:
        return "water"
    return None
```

- [ ] **Step 4: Run test to verify it passes (adjust mappings to fixtures until green)**

Run: `python3.13 -m pytest tests/test_models.py -v`
Expected: 3 PASS. If a `get(...)` chain misses, open the fixture, find the real field name, and extend the `or`-chain — do not change the dataclass.

- [ ] **Step 5: Commit**

```bash
git add custom_components/eirc_spb/models.py tests/test_models.py
git commit -m "feat: typed models with fixture-driven parsers"
```

---

### Task 4: Authenticator + API client

**Files:**
- Create: `custom_components/eirc_spb/auth.py`, `custom_components/eirc_spb/api.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `const.py` (Task 1), `exceptions.py` (Task 1), `models.py` parsers (Task 3), fixture JSON (Task 2).
- Produces (used by Tasks 5–8): `Authenticator(session: aiohttp.ClientSession)` with `async login(login_id, password, verification_token=None) -> AuthResult`, `async send_code(session_id, channel) -> None`, `async verify_code(session_id, channel, code) -> Session`, `async refresh(session) -> Session`, `async ensure_access() -> str` (returns Authorization header value; raises `EircSpbAuthError` when re-login needs confirmation or credentials fail); `Session(access, verification_token, raw)`, `AuthResult(session | None, needs_confirmation, session_id, channels: list[dict])`. `EircSpbApiClient(login_id, password, verification_token=None, session=None)` with `async get_accounts() -> list[Account]`, `async get_meters(account_id) -> list[Meter]`, `async get_bills_payments(account_id) -> BillsPayments`, `async submit_reading(connection_id, readings: list[dict]) -> dict`, `async close()`. The client constructs its own `aiohttp.ClientSession` when none passed (HA passes `hass.helpers.aiohttp_client.async_get_clientsession(hass)`).

- [ ] **Step 1: Write the failing test**

`tests/test_api.py`:

```python
import json
from pathlib import Path

import pytest
from aiohttp import web
from aresponses import ResponsesMockServer

from custom_components.eirc_spb.api import EircSpbApiClient
from custom_components.eirc_spb.auth import Authenticator
from custom_components.eirc_spb.exceptions import EircSpbAuthError

FIX = Path(__file__).parent / "fixtures"
HOST = "ikus.pesc.ru"


def load(name):
    return json.loads((FIX / f"{name}.json").read_text())


def ok(data, status=200):
    return web.json_response(data, status=status)


@pytest.fixture
async def client(aresponses):
    c = EircSpbApiClient("login", "password")
    yield c
    await c.close()


async def test_login_no_confirmation_needed(aresponses):
    aresponses.add(
        HOST, "/api/v8/users/auth", "POST", ok({"access": "tok", "type": "AUTHORIZED"})
    )
    auth = Authenticator(None)
    result = await auth.login("login", "password")
    assert result.session.access == "tok"
    assert not result.needs_confirmation


async def test_login_needs_confirmation_and_verify(aresponses):
    stage1 = load("login_stage1")
    aresponses.add(HOST, "/api/v8/users/auth", "POST", ok(stage1))
    aresponses.add(
        HOST,
        "/api/v7/users/s1/sms/check/confirmation/send",
        "POST",
        web.Response(status=200, text=""),
    )
    aresponses.add(
        HOST,
        "/api/v7/users/s1/sms/check/verification",
        "POST",
        ok({"access": "tok", "verified": "vtok"}),
    )
    auth = Authenticator(None)
    result = await auth.login("login", "password")
    assert result.needs_confirmation
    assert result.session_id == "s1"
    await auth.send_code("s1", "sms")
    session = await auth.verify_code("s1", "sms", "1234")
    assert session.access == "tok"
    assert session.verification_token == "vtok"


async def test_wrong_code_raises(aresponses):
    aresponses.add(
        HOST,
        "/api/v7/users/s1/sms/check/verification",
        "POST",
        ok({"code": "500", "message": "Неправильный код"}, status=400),
    )
    auth = Authenticator(None)
    with pytest.raises(Exception) as e:
        await auth.verify_code("s1", "sms", "0000")
    assert "код" in str(e.value)


async def test_bad_credentials_raise_auth_error(aresponses):
    aresponses.add(
        HOST,
        "/api/v8/users/auth",
        "POST",
        ok({"code": "5", "message": "Неверный логин или пароль"}, status=401),
    )
    auth = Authenticator(None)
    with pytest.raises(EircSpbAuthError):
        await auth.login("login", "wrong")


async def test_get_accounts_replays_fixture(aresponses, client):
    aresponses.add(
        HOST, "/api/v8/users/auth", "POST", ok({"access": "tok"})
    )
    aresponses.add(HOST, "/api/v8/accounts", "GET", ok(load("accounts")))
    accounts = await client.get_accounts()
    assert accounts[0].number


async def test_401_refreshes_then_retries(aresponses, client):
    aresponses.add(
        HOST, "/api/v8/users/auth", "POST", ok({"access": "tok"})
    )
    aresponses.add(
        HOST, "/api/v8/accounts", "GET",
        ok({"code": "5", "message": "unauthorized"}, status=401),
    )
    aresponses.add(
        HOST, "/api/v6/users/auth", "PUT", ok({"access": "tok2"})
    )
    aresponses.add(HOST, "/api/v8/accounts", "GET", ok(load("accounts")))
    accounts = await client.get_accounts()
    assert accounts[0].number


async def test_401_relogin_uses_verification_token(aresponses):
    c = EircSpbApiClient("login", "password", verification_token="vtok")
    aresponses.add(
        HOST, "/api/v8/users/auth", "GET", web.Response(status=404)
    )
    aresponses.add(
        HOST, "/api/v8/users/auth", "POST",
        ok({"access": "tok"}, status=200),
    )
    aresponses.add(HOST, "/api/v8/accounts", "GET", ok(load("accounts")))
    try:
        seen = {}

        async def handler(request):
            seen["auth_verification"] = request.headers.get("Auth-Verification")
            return ok(load("accounts"))

        aresponses.add(HOST, "/api/v8/accounts", "GET", handler)
        accounts = await c.get_accounts()
        assert accounts[0].number
    finally:
        await c.close()


async def test_submit_reading(aresponses, client):
    aresponses.add(
        HOST, "/api/v8/users/auth", "POST", ok({"access": "tok"})
    )
    aresponses.add(
        HOST, "/api/v1/mes/csp/connection-objects/indications/send",
        "POST",
        ok({"code": "0", "message": "Показания приняты"}),
    )
    result = await client.submit_reading("m1", [{"scale_id": 2, "value": 123}])
    assert result["code"] == "0"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.13 -m pytest tests/test_api.py -v`
Expected: FAIL — modules missing.

- [ ] **Step 3: Write minimal implementation**

`custom_components/eirc_spb/auth.py`:

```python
from dataclasses import dataclass, field

import aiohttp

from .const import (
    BASE_URL,
    HEADER_AUTH_VERIFICATION,
    HEADER_CAPTCHA,
    HEADER_CAPTCHA_NONE,
    HEADER_WITH_TOTP,
    REQUEST_TIMEOUT_SECONDS,
    USER_AGENT,
)
from .exceptions import EircSpbAuthError, EircSpbConfirmationError


@dataclass
class Session:
    access: str
    verification_token: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class AuthResult:
    session: Session | None
    needs_confirmation: bool
    session_id: str | None
    channels: list[dict] = field(default_factory=list)


CONFIRMATION_TYPES_OK = (None, "", "AUTHORIZED")


class Authenticator:
    def __init__(self, session: aiohttp.ClientSession | None) -> None:
        self._session = session

    async def _post(self, path, body, headers):
        assert self._session is not None
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
        async with self._session.post(
            f"{BASE_URL}/{path}", json=body, headers=headers, timeout=timeout
        ) as resp:
            data = await resp.json(content_type=None)
            if resp.status in (200, 201, 210):
                return data
            message = data.get("message", "") if isinstance(data, dict) else str(data)
            if "код" in message.lower() or "code" in message.lower():
                raise EircSpbConfirmationError(message, data.get("code"))
            raise EircSpbAuthError(message, str(data.get("code")) if isinstance(data, dict) else None)

    @staticmethod
    def _base_headers(verification_token: str | None = None) -> dict:
        headers = {
            "Content-Type": "application/json",
            HEADER_CAPTCHA: HEADER_CAPTCHA_NONE,
            HEADER_WITH_TOTP: "true",
            "User-Agent": USER_AGENT,
        }
        if verification_token:
            headers[HEADER_AUTH_VERIFICATION] = verification_token
        return headers

    async def login(
        self, login_id: str, password: str, verification_token: str | None = None
    ) -> AuthResult:
        assert self._session is not None
        try:
            data = await self._post(
                "v8/users/auth",
                {"login": login_id, "password": password},
                self._base_headers(verification_token),
            )
        except EircSpbAuthError as err:
            raise EircSpbAuthError(str(err)) from err
        auth_type = data.get("type") if isinstance(data, dict) else None
        needs = auth_type not in CONFIRMATION_TYPES_OK
        if needs:
            return AuthResult(
                session=None,
                needs_confirmation=True,
                session_id=str(data.get("id") or data.get("sessionId") or ""),
                channels=data.get("channels", []),
            )
        session = Session(
            access=str(data.get("access") or data.get("token") or ""),
            verification_token=verification_token,
            raw=data,
        )
        return AuthResult(session=session, needs_confirmation=False, session_id=None)

    async def send_code(self, session_id: str, channel: str) -> None:
        assert self._session is not None
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
        async with self._session.post(
            f"{BASE_URL}/v7/users/{session_id}/{channel.lower()}/check/confirmation/send",
            json={},
            headers=self._base_headers(),
            timeout=timeout,
        ) as resp:
            if resp.status >= 400:
                body = await resp.json(content_type=None)
                raise EircSpbApiErrorFromBody(body)
            await resp.read()

    async def verify_code(self, session_id: str, channel: str, code: str) -> Session:
        data = await self._post(
            f"v7/users/{session_id}/{channel.lower()}/check/verification",
            {"code": code},
            self._base_headers(),
        )
        return Session(
            access=str(data.get("access") or data.get("token") or ""),
            verification_token=data.get("verified") or data.get("verificationToken"),
            raw=data,
        )

    async def refresh(self, session: Session) -> Session:
        assert self._session is not None
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
        async with self._session.put(
            f"{BASE_URL}/v6/users/auth",
            json=session.raw,
            headers=self._base_headers(session.verification_token),
            timeout=timeout,
        ) as resp:
            if resp.status != 200:
                raise EircSpbAuthError("session refresh failed")
            data = await resp.json(content_type=None)
            merged = dict(session.raw)
            merged.update(data if isinstance(data, dict) else {})
            return Session(
                access=str(data.get("access", session.access)),
                verification_token=session.verification_token,
                raw=merged,
            )


def EircSpbApiErrorFromBody(body):
    from .exceptions import EircSpbApiError

    message = body.get("message", "API error") if isinstance(body, dict) else str(body)
    return EircSpbApiError(message, str(body.get("code")) if isinstance(body, dict) else None)
```

`custom_components/eirc_spb/api.py`:

```python
import asyncio
from typing import Any

import aiohttp

from .auth import Authenticator, AuthResult, Session
from .const import BASE_URL, REQUEST_TIMEOUT_SECONDS, USER_AGENT
from .exceptions import EircSpbApiError, EircSpbAuthError
from .models import (
    BillsPayments,
    Meter,
    Account,
    parse_accounts,
    parse_bills_payments,
    parse_meters,
)


class EircSpbApiClient:
    def __init__(
        self,
        login_id: str,
        password: str,
        verification_token: str | None = None,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._own_session = session is None
        self._session = session or aiohttp.ClientSession(
            headers={"User-Agent": USER_AGENT}
        )
        self._auth = Authenticator(self._session)
        self._login_id = login_id
        self._password = password
        self._verification_token = verification_token
        self._session_state: Session | None = None
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        if self._own_session:
            await self._session.close()

    async def authenticate(self) -> AuthResult:
        result = await self._auth.login(
            self._login_id, self._password, self._verification_token
        )
        if result.session is not None:
            self._session_state = result.session
        return result

    async def send_code(self, session_id: str, channel: str) -> None:
        await self._auth.send_code(session_id, channel)

    async def verify_code(self, session_id: str, channel: str, code: str) -> Session:
        session = await self._auth.verify_code(session_id, channel, code)
        self._session_state = session
        self._verification_token = session.verification_token
        return session

    @property
    def verification_token(self) -> str | None:
        return self._verification_token

    async def _ensure_access(self) -> str:
        async with self._lock:
            if self._session_state is None:
                result = await self.authenticate()
                if result.needs_confirmation:
                    raise EircSpbAuthError("confirmation required")
            assert self._session_state is not None
            return self._session_state.access

    async def _request(self, method: str, path: str, body: Any = None) -> Any:
        access = await self._ensure_access()
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
        async with self._session.request(
            method,
            f"{BASE_URL}/{path}",
            json=body,
            headers={"Authorization": f"Bearer {access}"},
            timeout=timeout,
        ) as resp:
            if resp.status == 401:
                refreshed = False
                assert self._session_state is not None
                try:
                    self._session_state = await self._auth.refresh(self._session_state)
                    refreshed = True
                except EircSpbAuthError:
                    pass
                if not refreshed:
                    result = await self.authenticate()
                    if result.needs_confirmation:
                        raise EircSpbAuthError("confirmation required again")
                access = self._session_state.access
                async with self._session.request(
                    method,
                    f"{BASE_URL}/{path}",
                    json=body,
                    headers={"Authorization": f"Bearer {access}"},
                    timeout=timeout,
                ) as retry:
                    return await self._parse(retry)
            return await self._parse(resp)

    @staticmethod
    async def _parse(resp: aiohttp.ClientResponse) -> Any:
        data = await resp.json(content_type=None)
        if resp.status >= 400:
            message = data.get("message", "API error") if isinstance(data, dict) else str(data)
            raise EircSpbApiError(message, str(data.get("code")) if isinstance(data, dict) else None)
        return data

    async def get_accounts(self) -> list[Account]:
        data = await self._request("GET", "v8/accounts")
        return parse_accounts(data if isinstance(data, list) else data.get("accounts", []))

    async def get_meters(self, account_id: str) -> list[Meter]:
        data = await self._request(
            "GET", f"v1/mes/csp/connection-objects?accountId={account_id}"
        )
        return parse_meters(data, account_id)

    async def get_bills_payments(self, account_id: str) -> BillsPayments:
        data = await self._request(
            "GET", f"v7/bills/payments?accountId={account_id}"
        )
        return parse_bills_payments(data if isinstance(data, dict) else {})

    async def submit_reading(self, connection_id: str, readings: list[dict]) -> dict:
        return await self._request(
            "POST",
            "v1/mes/csp/connection-objects/indications/send",
            {"connectionObjectId": connection_id, "indications": readings},
        )
```

- [ ] **Step 4: Run tests to verify they pass (adjust endpoint/body details to fixture NOTES.md findings)**

Run: `python3.13 -m pytest tests/test_api.py -v`
Expected: 8 PASS. Reconcile with `tests/fixtures/NOTES.md` (Task 2 Step 3): if capture proved different login body keys, confirmation path shape, or response markers for `needs_confirmation` (e.g. HTTP 210, `type` values), update `auth.py`/`api.py` and the corresponding tests together.

- [ ] **Step 5: Commit**

```bash
git add custom_components/eirc_spb/auth.py custom_components/eirc_spb/api.py tests/test_api.py
git commit -m "feat: authenticator (two-stage OTP) and API client with 401 recovery"
```

---

### Task 5: Coordinator + setup entry

**Files:**
- Create: `custom_components/eirc_spb/coordinator.py`
- Modify: `custom_components/eirc_spb/__init__.py` (replace placeholder)
- Test: `tests/test_coordinator.py`

**Interfaces:**
- Consumes: `EircSpbApiClient` (Task 4), `Account`/`Meter`/`BillsPayments`/`sum_payments` (Task 3), `const.py` keys.
- Produces: `EircSpbCoordinator(hass, client, account_ids, scan_interval)` (attribute `.data: EircSpbData | None`); `EircSpbData(accounts: dict[str, Account], meters: dict[str, Meter])`; `async_setup_entry`/`async_unload_entry` in `__init__.py`; runtime data stored via `hass.data` under key `entry.entry_id` → `EircSpbRuntime(client, coordinator)`. `async_get_entry_data(hass, entry_id)` helper used by sensor/service platforms.

- [ ] **Step 1: Write the failing test**

`tests/test_coordinator.py`:

```python
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.eirc_spb.const import DOMAIN
from custom_components.eirc_spb.coordinator import EircSpbCoordinator, EircSpbData
from custom_components.eirc_spb.models import Account, Meter, Scale

from pytest_homeassistant_custom_component.common import MockConfigEntry

ACCOUNT = Account(account_id="a1", number="1000000001", address="ул. Тестовая, 1")
METER = Meter(
    meter_id="m1",
    account_id="a1",
    name="Холодная вода",
    device_class="water",
    unit="м³",
    scales=[Scale(scale_id="s1", name=None, last_reading=123.0, last_submit="2026-08-01")],
)


def make_client():
    client = AsyncMock()
    client.get_accounts.return_value = [ACCOUNT]
    client.get_meters.return_value = [METER]
    client.get_bills_payments.return_value = AsyncMock(
        balance=100.0, accruals_total=1500.0, accruals_period="2026-07",
        accruals_breakdown={"ХВС": 500.0, "ТКО": 1000.0}, payments=[],
    ).return_value if False else _bp()
    return client


def _bp():
    from custom_components.eirc_spb.models import BillsPayments, Payment

    return BillsPayments(
        balance=100.0,
        accruals_total=1500.0,
        accruals_period="2026-07",
        accruals_breakdown={"ХВС": 500.0, "ТКО": 1000.0},
        payments=[Payment(payment_id="p1", date="2026-08-10", amount=700.0)],
    )


async def test_coordinator_merges_data(hass: HomeAssistant):
    coordinator = EircSpbCoordinator(hass, make_client(), ["a1"], 12)
    await coordinator.async_config_entry_first_refresh()
    data: EircSpbData = coordinator.data
    assert data.accounts["a1"].balance == 100.0
    assert data.accounts["a1"].payments_total == 700.0
    assert data.accounts["a1"].accruals_breakdown == {"ХВС": 500.0, "ТКО": 1000.0}
    assert data.meters["m1"].scales[0].last_reading == 123.0
```

(Write the test cleanly — drop the leftover conditional in `make_client`; simply set `client.get_bills_payments.return_value = _bp()`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.13 -m pytest tests/test_coordinator.py -v`
Expected: FAIL — `coordinator` module missing.

- [ ] **Step 3: Write minimal implementation**

`custom_components/eirc_spb/coordinator.py`:

```python
from dataclasses import dataclass, field

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import EircSpbApiClient
from .models import Account, Meter, sum_payments


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
            logger=None,
            name="eirc_spb",
            update_interval=__import__("datetime").timedelta(hours=scan_interval_hours),
        )
        self._client = client
        self._account_ids = account_ids

    async def _async_update_data(self) -> EircSpbData:
        data = EircSpbData()
        all_accounts = await self._client.get_accounts()
        for account in all_accounts:
            if account.account_id not in self._account_ids:
                continue
            bp = await self._client.get_bills_payments(account.account_id)
            account.balance = bp.balance
            account.accruals_total = bp.accruals_total
            account.accruals_period = bp.accruals_period
            account.accruals_breakdown = bp.accruals_breakdown
            account.payments_total = sum_payments(bp.payments)
            account.recent_payments = bp.payments[:10]
            data.accounts[account.account_id] = account
            for meter in await self._client.get_meters(account.account_id):
                data.meters[meter.meter_id] = meter
        return data
```

Replace `__init__.py` (imports at top, `datetime` imported normally — do not leave the `__import__` hack):

```python
import asyncio
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthError, HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EircSpbApiClient
from .const import (
    CONF_ACCOUNTS,
    CONF_LOGIN,
    CONF_PASSWORD,
    CONF_VERIFICATION_TOKEN,
    DEFAULT_SCAN_INTERVAL_HOURS,
    DOMAIN,
)
from .coordinator import EircSpbCoordinator
from .exceptions import EircSpbAuthError

PLATFORMS: list[str] = ["sensor"]


@dataclass
class EircSpbRuntime:
    client: EircSpbApiClient
    coordinator: EircSpbCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    client = EircSpbApiClient(
        entry.data[CONF_LOGIN],
        entry.data[CONF_PASSWORD],
        entry.data.get(CONF_VERIFICATION_TOKEN),
        async_get_clientsession(hass),
    )
    scan_hours = entry.options.get(
        "scan_interval_hours", DEFAULT_SCAN_INTERVAL_HOURS
    )
    coordinator = EircSpbCoordinator(
        hass, client, entry.data[CONF_ACCOUNTS], scan_hours
    )
    try:
        await coordinator.async_config_entry_first_refresh()
    except EircSpbAuthError as err:
        raise ConfigEntryAuthError from err
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = EircSpbRuntime(
        client, coordinator
    )
    await hass.config_entries.async_forward_platform_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    runtime: EircSpbRuntime | None = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if runtime:
        await runtime.client.close()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
```

Note: `DataUpdateCoordinator` requires `logger`; pass the integration logger via `logging.getLogger(__name__)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.13 -m pytest tests/test_coordinator.py tests/test_scaffold.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add custom_components/eirc_spb/coordinator.py custom_components/eirc_spb/__init__.py tests/test_coordinator.py
git commit -m "feat: data coordinator and setup/unload entry"
```

---

### Task 6: Config flow, reauth, options flow, translations

**Files:**
- Create: `custom_components/eirc_spb/config_flow.py`, `custom_components/eirc_spb/options_flow.py`, `custom_components/eirc_spb/translations/ru-RU.json`, `custom_components/eirc_spb/translations/en.json`
- Test: `tests/test_config_flow.py`

**Interfaces:**
- Consumes: `EircSpbApiClient` (Task 4), `AuthResult`/`Session` (Task 4), const keys, fixture-shaped channel lists.
- Produces: config entry `data = {CONF_LOGIN, CONF_PASSWORD, CONF_VERIFICATION_TOKEN?, CONF_ACCOUNTS: list[str]}`, `options = {"scan_interval_hours": float}`; step ids `user`, `confirm`, `code`, `select_accounts`, `reauth_confirm`; options step `init`. The flow keeps the half-finished auth state (session_id, channels, code length) in `self.login_result` / flow context.

- [ ] **Step 1: Write the failing test**

`tests/test_config_flow.py`:

```python
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.eirc_spb.const import DOMAIN
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.eirc_spb.auth import AuthResult, Session

LOGIN = "user1@example.com"


def client_mock(hass):
    client = MagicMock()
    client.authenticate = AsyncMock(
        return_value=AuthResult(
            session=Session(access="tok"), needs_confirmation=False, session_id=None
        )
    )
    client.get_accounts = AsyncMock(
        return_value=[
            MagicMock(account_id="a1", number="1000000001", address="ул. Тестовая, 1"),
            MagicMock(account_id="a2", number="1000000002", address="ул. Тестовая, 2"),
        ]
    )
    client.verification_token = None
    return client


async def _start_flow(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    return result


async def test_full_flow_two_accounts(hass: HomeAssistant):
    with patch(
        "custom_components.eirc_spb.config_flow.EircSpbApiClient",
        return_value=client_mock(hass),
    ):
        result = await _start_flow(hass)
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"login": LOGIN, "password": "pw"},
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "select_accounts"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"accounts": ["a1"]}
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"]["login"] == LOGIN
        assert result["data"]["accounts"] == ["a1"]


async def test_confirmation_flow(hass: HomeAssistant):
    client = client_mock(hass)
    client.authenticate = AsyncMock(
        return_value=AuthResult(
            session=None,
            needs_confirmation=True,
            session_id="s1",
            channels=[{"type": "sms", "masked": "+7 ***123"}],
        )
    )
    client.send_code = AsyncMock()
    client.verify_code = AsyncMock(
        return_value=Session(access="tok", verification_token="vtok")
    )
    with patch(
        "custom_components.eirc_spb.config_flow.EircSpbApiClient",
        return_value=client,
    ):
        result = await _start_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"login": LOGIN, "password": "pw"}
        )
        assert result["step_id"] == "confirm"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"channel": "sms"}
        )
        assert result["step_id"] == "code"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"code": "1234"}
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"]["verification_token"] == "vtok"


async def test_wrong_code_shows_error(hass: HomeAssistant):
    client = client_mock(hass)
    client.authenticate = AsyncMock(
        return_value=AuthResult(
            session=None, needs_confirmation=True, session_id="s1",
            channels=[{"type": "sms", "masked": "+7 ***123"}],
        )
    )
    client.send_code = AsyncMock()
    client.verify_code = AsyncMock(side_effect=Exception("Неправильный код"))
    with patch(
        "custom_components.eirc_spb.config_flow.EircSpbApiClient",
        return_value=client,
    ):
        result = await _start_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"login": LOGIN, "password": "pw"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"channel": "sms"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"code": "0000"}
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "code"
        assert result["errors"] == {"base": "invalid_code"}


async def test_single_account_autoselected(hass: HomeAssistant):
    client = client_mock(hass)
    client.get_accounts = AsyncMock(
        return_value=[MagicMock(account_id="a1", number="1000000001", address="X")]
    )
    with patch(
        "custom_components.eirc_spb.config_flow.EircSpbApiClient",
        return_value=client,
    ):
        result = await _start_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"login": LOGIN, "password": "pw"}
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"]["accounts"] == ["a1"]


async def test_bad_credentials(hass: HomeAssistant):
    client = client_mock(hass)
    client.authenticate = AsyncMock(side_effect=Exception("bad creds"))
    with patch(
        "custom_components.eirc_spb.config_flow.EircSpbApiClient",
        return_value=client,
    ):
        result = await _start_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"login": LOGIN, "password": "nope"}
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"
        assert result["errors"] == {"base": "invalid_auth"}


async def test_reauth(hass: HomeAssistant):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"login": LOGIN, "password": "old", "accounts": ["a1"]},
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.eirc_spb.config_flow.EircSpbApiClient",
        return_value=client_mock(hass),
    ), patch.object(hass.config_entries, "async_update_entry"):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_REAUTH, "entry_id": entry.entry_id},
            data=entry.data,
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"password": "newpw"}
        )
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "reauth_successful"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.13 -m pytest tests/test_config_flow.py -v`
Expected: FAIL — `config_flow` missing.

- [ ] **Step 3: Write minimal implementation**

`custom_components/eirc_spb/config_flow.py`:

```python
import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .api import EircSpbApiClient
from .const import (
    CONF_ACCOUNTS,
    CONF_LOGIN,
    CONF_PASSWORD,
    CONF_VERIFICATION_TOKEN,
    DEFAULT_SCAN_INTERVAL_HOURS,
    DOMAIN,
    MIN_SCAN_INTERVAL_HOURS,
)
from .exceptions import EircSpbAuthError, EircSpbConfirmationError

_LOGGER = logging.getLogger(__name__)


class EircSpbFlowHandler(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._login: str = ""
        self._password: str = ""
        self._client: EircSpbApiClient | None = None
        self._auth_result = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._login = user_input[CONF_LOGIN]
            self._password = user_input[CONF_PASSWORD]
            self._client = EircSpbApiClient(self._login, self._password)
            try:
                self._auth_result = await self._client.authenticate()
            except EircSpbAuthError:
                errors["base"] = "invalid_auth"
            else:
                if self._auth_result.needs_confirmation:
                    return await self.async_step_confirm()
                return await self._finish_auth()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_LOGIN): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        channels = self._auth_result.channels or [{"type": "sms", "masked": ""}]
        if user_input is not None:
            assert self._client is not None
            await self._client.send_code(
                self._auth_result.session_id, user_input["channel"]
            )
            self._channel = user_input["channel"]
            return await self.async_step_code()
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema(
                {
                    vol.Required("channel"): vol.In(
                        {c["type"]: c.get("masked", c["type"]) for c in channels}
                    )
                }
            ),
        )

    async def async_step_code(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            assert self._client is not None
            try:
                await self._client.verify_code(
                    self._auth_result.session_id, self._channel, user_input["code"]
                )
            except EircSpbConfirmationError:
                errors["base"] = "invalid_code"
            else:
                return await self._finish_auth()
        return self.async_show_form(
            step_id="code",
            data_schema=vol.Schema({vol.Required("code"): str}),
            errors=errors,
        )

    async def _finish_auth(self) -> FlowResult:
        assert self._client is not None
        accounts = await self._client.get_accounts()
        if len(accounts) == 1:
            return self._create_entry([accounts[0]], [accounts[0].account_id])
        schema = vol.Schema(
            {
                vol.Required(CONF_ACCOUNTS): cv_multi(
                    {a.account_id: f"{a.number} — {a.address}" for a in accounts}
                )
            }
        )
        self._accounts = accounts
        return self.async_show_form(step_id="select_accounts", data_schema=schema)

    async def async_step_select_accounts(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        assert user_input is not None
        chosen = [
            a for a in self._accounts if a.account_id in user_input[CONF_ACCOUNTS]
        ]
        return self._create_entry(chosen, user_input[CONF_ACCOUNTS])

    def _create_entry(self, accounts, account_ids: list[str]) -> FlowResult:
        data = {
            CONF_LOGIN: self._login,
            CONF_PASSWORD: self._password,
            CONF_ACCOUNTS: account_ids,
        }
        assert self._client is not None
        if self._client.verification_token:
            data[CONF_VERIFICATION_TOKEN] = self._client.verification_token
        title = "ЕИРЦ СПб"
        if len(accounts) == 1:
            title = f"ЕИРЦ СПб ({accounts[0].number})"
        return self.async_create_entry(title=title, data=data)

    async def async_step_reauth(self, entry_data):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            client = EircSpbApiClient(
                entry.data[CONF_LOGIN], user_input[CONF_PASSWORD]
            )
            try:
                result = await client.authenticate()
            except EircSpbAuthError:
                errors["base"] = "invalid_auth"
            else:
                if not result.needs_confirmation:
                    self.hass.config_entries.async_update_entry(
                        entry,
                        data={**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]},
                    )
                    return self.async_update_reload_and_abort(
                        entry, reason="reauth_successful"
                    )
                errors["base"] = "totp_unsupported_reauth_via_web"
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
        )


def cv_multi(options: dict) -> vol.Schema:
    import homeassistant.helpers.config_validation as cv

    return cv.multi_select(options)
```

`custom_components/eirc_spb/options_flow.py` (registered via `OptionsFlow` in config_flow — add `@staticmethod def async_get_options_flow` returning `EircSpbOptionsFlow` and the class below, or place both here and import):

```python
from homeassistant.config_entries import ConfigEntry, OptionsFlow
from homeassistant.core import callback
import voluptuous as vol

from .const import DEFAULT_SCAN_INTERVAL_HOURS, DOMAIN, MIN_SCAN_INTERVAL_HOURS


class EircSpbOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        "scan_interval_hours",
                        default=self.config_entry.options.get(
                            "scan_interval_hours", DEFAULT_SCAN_INTERVAL_HOURS
                        ),
                    ): vol.All(
                        vol.Coerce(float),
                        vol.Range(min=MIN_SCAN_INTERVAL_HOURS, max=24),
                    )
                }
            ),
        )
```

Wire it in `config_flow.py`:

```python
@staticmethod
@callback
def async_get_options_flow(config_entry):
    from .options_flow import EircSpbOptionsFlow

    return EircSpbOptionsFlow()
```

`translations/ru-RU.json`:

```json
{
  "config": {
    "step": {
      "user": {
        "title": "ЕИРЦ СПб",
        "description": "Войдите в личный кабинет ЕИРЦ (ikus.pesc.ru)",
        "data": {
          "login": "Телефон (+7…) или e-mail",
          "password": "Пароль"
        }
      },
      "confirm": {
        "title": "Подтверждение входа",
        "description": "Выберите способ получения кода",
        "data": { "channel": "Способ" }
      },
      "code": {
        "title": "Код подтверждения",
        "data": { "code": "Код из сообщения" }
      },
      "select_accounts": {
        "title": "Лицевые счета",
        "data": { "accounts": "Счета" }
      },
      "reauth_confirm": {
        "title": "Обновление пароля",
        "data": { "password": "Новый пароль" }
      }
    },
    "error": {
      "cannot_connect": "Не удалось подключиться",
      "invalid_auth": "Неверный логин или пароль",
      "invalid_code": "Неверный код подтверждения",
      "totp_unsupported_reauth_via_web": "Включён TOTP — войдите через веб и повторите"
    },
    "abort": { "reauth_successful": "Авторизация обновлена" }
  },
  "options": {
    "step": {
      "init": {
        "data": { "scan_interval_hours": "Интервал обновления, ч (1–24)" }
      }
    }
  }
}
```

`translations/en.json`: same structure with English strings (`login`: "Phone (+7…) or e-mail", etc.).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3.13 -m pytest tests/test_config_flow.py -v`
Expected: 6 PASS. If `async_update_reload_and_abort` needs different kwargs in 2026.8, adjust to the HA version's signature while keeping the abort reason.

- [ ] **Step 5: Commit**

```bash
git add custom_components/eirc_spb/config_flow.py custom_components/eirc_spb/options_flow.py custom_components/eirc_spb/translations tests/test_config_flow.py
git commit -m "feat: config flow with OTP confirmation, reauth and options"
```

---

### Task 7: Sensors + devices

**Files:**
- Create: `custom_components/eirc_spb/sensor.py`
- Test: `tests/test_sensor.py`

**Interfaces:**
- Consumes: `EircSpbRuntime`/`EircSpbData` (Task 5), models (Task 3), const attrs (Task 1).
- Produces: entities — per account: `sensor.eirc_spb_<number>_balance` (`device_class=monetary`, currency RUB), `sensor.eirc_spb_<number>_accruals` (monetary, attrs `period`, breakdown dict), `sensor.eirc_spb_<number>_payments` (monetary, `total_increasing`, attrs `payments` list); per meter scale: `sensor.eirc_spb_<number>_<meter_name>_<scale_name>` (`water`/`energy` device class or none, `total_increasing` for water/energy, attrs `account_id`, `meter_id`, `scale_id`, `last_submit`, `meter_serial`, `verification_date`). Device per account: name `ЕИРЦ <number>`, identifiers `{(DOMAIN, account_id)}`.

- [ ] **Step 1: Write the failing test**

`tests/test_sensor.py`:

```python
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.eirc_spb.const import DOMAIN
from custom_components.eirc_spb.coordinator import EircSpbCoordinator, EircSpbData
from custom_components.eirc_spb.models import (
    Account,
    BillsPayments,
    Meter,
    Payment,
    Scale,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

ENTRY_DATA = {
    "login": "user1@example.com",
    "password": "pw",
    "accounts": ["a1"],
}


def build_data() -> EircSpbData:
    account = Account(
        account_id="a1",
        number="1000000001",
        address="ул. Тестовая, 1",
        balance=150.25,
        accruals_total=2000.0,
        accruals_period="2026-07",
        accruals_breakdown={"ХВС": 500.0, "ТКО": 1500.0},
        payments_total=700.0,
        recent_payments=[Payment("p1", "2026-08-10", 700.0)],
    )
    meter = Meter(
        meter_id="m1",
        account_id="a1",
        name="Холодная вода",
        device_class="water",
        unit="м³",
        serial="ABC123",
        verification_date="2028-01-01",
        scales=[Scale(scale_id="s1", last_reading=123.45, last_submit="2026-08-01")],
    )
    return EircSpbData(accounts={"a1": account}, meters={"m1": meter})


async def test_sensors_created(hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA)
    entry.add_to_hass(hass)
    coordinator = MagicMock()
    coordinator.data = build_data()
    coordinator.async_config_entry_first_refresh = AsyncMock()

    with patch(
        "custom_components.eirc_spb.EircSpbCoordinator",
        return_value=coordinator,
    ), patch(
        "custom_components.eirc_spb.EircSpbApiClient",
        return_value=MagicMock(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    state = hass.states.get("sensor.eirc_spb_1000000001_balance")
    assert state is not None
    assert float(state.state) == 150.25
    assert state.attributes["unit_of_measurement"] == "RUB"

    accruals = hass.states.get("sensor.eirc_spb_1000000001_accruals")
    assert float(accruals.state) == 2000.0
    assert accruals.attributes["period"] == "2026-07"
    assert accruals.attributes["ХВС"] == 500.0

    payments = hass.states.get("sensor.eirc_spb_1000000001_payments")
    assert float(payments.state) == 700.0

    meter = hass.states.get("sensor.eirc_spb_1000000001_kholodnaia_voda_s1")
    assert meter is not None
    assert float(meter.state) == 123.45
    assert meter.attributes["meter_id"] == "m1"
    assert meter.attributes["scale_id"] == "s1"

    device_reg = dr.async_get(hass)
    device = device_reg.async_get_device({(DOMAIN, "a1")})
    assert device is not None
```

Note: the meter entity slug depends on `slugify("Холодная вода")` — if the test fails on the exact slug, read the actual `entity_id` from `er.async_get(hass)` for the unique_id `eirc_spb_a1_m1_s1` and adjust the test to look it up by unique_id instead of hardcoding the slug.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.13 -m pytest tests/test_sensor.py -v`
Expected: FAIL — `sensor.py` missing (platform not forwarded).

- [ ] **Step 3: Write minimal implementation**

`custom_components/eirc_spb/sensor.py`:

```python
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CURRENCY_RUB, UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from . import EircSpbRuntime
from .const import (
    ATTR_ACCOUNT_ID,
    ATTR_METER_ID,
    ATTR_SCALE_ID,
    DOMAIN,
)
from .coordinator import EircSpbCoordinator, EircSpbData
from .models import Account, Meter, Scale

UNIT_BY_CLASS = {
    "water": UnitOfVolume.CUBIC_METERS,
    "energy": UnitOfEnergy.KILO_WATT_HOUR,
}


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

    def __init__(self, coordinator, account: Account, key: str) -> None:
        super().__init__(coordinator)
        self._account = account
        self._attr_unique_id = f"{DOMAIN}_{account.number}_{key}"
        self._attr_device_info = _device(account)


class BalanceSensor(_AccountSensor):
    _attr_state_class = SensorStateClass.TOTAL

    def __init__(self, coordinator, account: Account) -> None:
        super().__init__(coordinator, account, "balance")

    @property
    def name(self) -> str:
        return "Баланс"

    @property
    def native_value(self) -> float | None:
        return self._account.balance


class AccrualsSensor(_AccountSensor):
    _attr_state_class = SensorStateClass.TOTAL

    def __init__(self, coordinator, account: Account) -> None:
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

    def __init__(self, coordinator, account: Account) -> None:
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

    def __init__(
        self,
        coordinator,
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
```

Note the entity_id slug is generated by HA (`has_entity_name` + device name + entity name); the test looks it up and asserts against what the registry produced (see test note on unique_id lookup).

- [ ] **Step 4: Run test to verify it passes**

Run: `python3.13 -m pytest tests/test_sensor.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add custom_components/eirc_spb/sensor.py tests/test_sensor.py
git commit -m "feat: balance/accruals/payments and meter sensors"
```

---

### Task 8: Service `send_meter_reading`

**Files:**
- Create: `custom_components/eirc_spb/services.py`
- Modify: `custom_components/eirc_spb/__init__.py` (register/unregister service)
- Test: `tests/test_services.py`

**Interfaces:**
- Consumes: `EircSpbRuntime` (Task 5), `EircSpbApiClient.submit_reading` (Task 4), meter sensor attributes (Task 7).
- Produces: service `eirc_spb.send_meter_reading`, schema `{entity_id: cv.entity_id (or list), readings: [{scale_id: int|str, value: float}]}` (all required), `supports_response=OPTIONAL`, returns `{"code": ..., "message": ...}`; on API rejection raises `HomeAssistantError` with the API message; on success triggers coordinator refresh.

- [ ] **Step 1: Write the failing test**

`tests/test_services.py`:

```python
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.eirc_spb.const import DOMAIN
from pytest_homeassistant_custom_component.common import MockConfigEntry

ENTRY_DATA = {
    "login": "user1@example.com",
    "password": "pw",
    "accounts": ["a1"],
}


async def _setup(hass, submit_result=None, submit_error=None):
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA)
    entry.add_to_hass(hass)
    coordinator = MagicMock()
    coordinator.async_request_refresh = AsyncMock()
    client = MagicMock()
    client.submit_reading = AsyncMock(return_value=submit_result)
    if submit_error:
        client.submit_reading = AsyncMock(side_effect=submit_error)
    runtime = MagicMock()
    runtime.coordinator = coordinator
    runtime.client = client
    with patch(
        "custom_components.eirc_spb.EircSpbCoordinator",
        return_value=coordinator,
    ), patch.object(client, "authenticate", AsyncMock()):
        hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return runtime


async def test_send_reading_success(hass: HomeAssistant):
    runtime = await _setup(
        hass, submit_result={"code": "0", "message": "Показания приняты"}
    )
    response = await hass.services.async_call(
        DOMAIN,
        "send_meter_reading",
        {
            "entity_id": "sensor.anything",
            "readings": [{"scale_id": 2, "value": 12345}],
        },
        blocking=True,
        return_response=True,
    )
    runtime.client.submit_reading.assert_awaited_once()
    assert response["code"] == "0"


async def test_send_reading_rejection_raises(hass: HomeAssistant):
    from custom_components.eirc_spb.exceptions import EircSpbApiError

    await _setup(hass, submit_error=EircSpbApiError("Показания меньше предыдущих"))
    with pytest.raises(Exception, match="меньше предыдущих"):
        await hass.services.async_call(
            DOMAIN,
            "send_meter_reading",
            {
                "entity_id": "sensor.anything",
                "readings": [{"scale_id": 2, "value": 1}],
            },
            blocking=True,
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3.13 -m pytest tests/test_services.py -v`
Expected: FAIL — service not registered.

- [ ] **Step 3: Write minimal implementation**

`custom_components/eirc_spb/services.py`:

```python
import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr

from .api import EircSpbApiClient
from .const import ATTR_METER_ID, DOMAIN
from .exceptions import EircSpbApiError

SERVICE_SEND_METER_READING = "send_meter_reading"

READING_SCHEMA = vol.Schema(
    {
        vol.Required("scale_id"): vol.Any(int, str),
        vol.Required("value"): vol.Coerce(float),
    }
)

SEND_METER_READING_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_ids,
        vol.Required("readings"): vol.All(cv.ensure_list, [READING_SCHEMA]),
    }
)


def _find_client_for_entity(hass: HomeAssistant, entity_id: str) -> tuple[EircSpbApiClient, str] | None:
    device_reg = dr.async_get(hass)
    device = device_reg.async_get_device_from_entity_id(entity_id)
    if device is None:
        return None
    for entry_id, runtime in hass.data[DOMAIN].items():
        coordinator = runtime.coordinator
        if coordinator is None or coordinator.data is None:
            continue
        for account in coordinator.data.accounts.values():
            if (DOMAIN, account.account_id) in set(device.identifiers):
                for meter in coordinator.data.meters.values():
                    if meter.account_id == account.account_id:
                        return runtime.client, meter.meter_id
    return None


async def async_setup_services(hass: HomeAssistant) -> None:
    async def handle_send_meter_reading(call: ServiceCall) -> ServiceResponse:
        entity_ids = call.data[ATTR_ENTITY_ID]
        found = None
        for entity_id in entity_ids:
            found = _find_client_for_entity(hass, entity_id)
            if found:
                break
        if found is None:
            raise HomeAssistantError(f"Не найден счётчик для {entity_ids}")
        client, connection_id = found
        try:
            result = await client.submit_reading(
                connection_id, call.data["readings"]
            )
        except EircSpbApiError as err:
            raise HomeAssistantError(str(err)) from err
        for runtime in hass.data[DOMAIN].values():
            await runtime.coordinator.async_request_refresh()
        return {"code": str(result.get("code", "")), "message": result.get("message", "")}

    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_METER_READING,
        handle_send_meter_reading,
        schema=SEND_METER_READING_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
```

In `__init__.py` add at the end of `async_setup_entry` (before `return True`):

```python
    if not hass.services.has_service(DOMAIN, "send_meter_reading"):
        from .services import async_setup_services

        await async_setup_services(hass)
```

And in `async_unload_entry`, after unloading platforms:

```python
    if not hass.data.get(DOMAIN):
        hass.services.async_remove(DOMAIN, "send_meter_reading")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3.13 -m pytest tests/test_services.py -v`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add custom_components/eirc_spb/services.py custom_components/eirc_spb/__init__.py tests/test_services.py
git commit -m "feat: send_meter_reading service"
```

---

### Task 9: README, hassfest/HACS validation, final green

**Files:**
- Modify: `README.md` (full Russian docs), `custom_components/eirc_spb/manifest.json` (only if hassfest demands keys)
- Test: full suite + hassfest + HACS action locally

**Interfaces:**
- Consumes: everything above.
- Produces: publishable repository state.

- [ ] **Step 1: Write the full README (Russian)**

Sections (in this order): описание (домен `eirc_spb`, данные из ikus.pesc.ru), возможности (список сенсоров с таблицей: баланс/начисления/платежи/показания, атрибуты), установка (HACS custom repository), настройка (логин телефон или e-mail, подтверждение по SMS/e-mail/звонку, выбор счетов), сервис отправки показаний (YAML-пример из spec §6 verbatim), опции (интервал 1–24 ч), отладка (logger snippet `custom_components.eirc_spb: debug`), предупреждение о хранении пароля в конфиге и запрете публикации бэкапов, ограничения (TOTP не поддерживается, только ikus.pesc.ru).

- [ ] **Step 2: Run the whole test suite**

Run: `python3.13 -m pytest -v`
Expected: all PASS (scaffold 3 + models 3 + api 8 + coordinator 1 + config_flow 6 + sensor 1 + services 2).

- [ ] **Step 3: Run hassfest and HACS validation**

Run:

```bash
pip install hassfest==20[dev]
python -m script.hassfest --integration-path custom_components/eirc_spb 2>/dev/null || hassfest
```

If no local hassfest package: run `pip install "homeassistant==2026.8.1" hassfest` or rely on CI (`.github/workflows/ci.yml` already runs both actions) and push the branch. Fix anything hassfest flags (common: missing `manifest.json` keys, translation key mismatches — run `python3.13 -m script.translations` equivalent or fix JSON by hand).

- [ ] **Step 4: Manual validation WITH THE USER (HUMAN-IN-THE-LOOP)**

Orchestrator + user: copy `custom_components/eirc_spb` into the user's HA `config/custom_components/`, restart HA, add the integration through UI, verify against the web cabinet: balance/accruals/payments/meter readings match, submit a reading via the service and see it appear in the cabinet.

- [ ] **Step 5: Commit**

```bash
git add README.md custom_components
git commit -m "docs: full Russian README; validation fixes"
```

---

## Self-Review (done at plan-writing time)

- **Spec coverage:** sensors (§4 → Task 7), two-stage auth incl. channel choice (§2/§5 → Tasks 4, 6), verification-token persistence (§2 → Tasks 4, 6), 401 refresh→relogin (§7 → Task 4), coordinator 12h/min 1h (§3 → Tasks 5, 6 options), service (§6 → Task 8), HACS repo layout + CI (§8 → Tasks 1, 9), fixtures-first testing (§9 → Task 2), README RU (§8 → Task 9), TOTP unsupported documented (§7 → Task 9 README). Validation items §10 folded into Task 2 Step 3 (NOTES.md) and Task 4 Step 4 (reconciliation).
- **Placeholders:** none — fixture-dependent field names carry explicit "fixture wins, adjust mapping" instructions with concrete candidate names, not TODOs.
- **Type consistency:** `Account`/`Meter`/`Scale`/`Payment`/`BillsPayments` attr names consistent across Tasks 3–8; `EircSpbApiClient(login_id, password, verification_token, session)` consistent in Tasks 4–6; runtime/`hass.data` access pattern consistent in Tasks 5, 7, 8.
