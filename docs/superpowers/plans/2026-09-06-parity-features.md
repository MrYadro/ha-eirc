# Parity Features Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the gaps found in the parity audit (`docs/parity.md`): fix the broken native notifications feed, enrich sensors from the `details` endpoint (verification dates, meter passports, apartment info, tariffs), add reading pre-validation, bills/payments history, and an ЕПД PDF download service.

**Architecture:** Same three layers as today — pure aiohttp client (`api.py`), `EircSpbCoordinator` polling per лицевой счёт, HA glue (sensors/services). New fetches are secondary: cached (details/history 24h) and non-fatal on error. Five sequential phases, each shippable as a CalVer release.

**Tech Stack:** Python 3.14, aiohttp (ships with HA), `pytest-homeassistant-custom-component`, `aresponses` for HTTP mocking.

**Spec:** `docs/superpowers/specs/2026-09-06-parity-features-design.md` — read it before starting; this plan argues from it. Parity evidence: `docs/parity.md`.

## Global Constraints

- Domain: `eirc_spb`. Base URL: `https://ikus.pesc.ru/api` (`const.py:BASE_URL`).
- No new runtime dependencies; all new API calls go through `EircSpbApiClient._request` (auth/401-retry already handled).
- Secondary fetch failures (details, history, validate) must NOT fail the coordinator cycle — warn-once log per unique error code, continue.
- No comments in code unless asked. No PII in committed fixtures (fake values: register `1000000001`, account id `910000001`).
- Versions: CalVer `год.месяц.патч`, synced in `manifest.json` and `const.py:VERSION`. Phase releases: 2026.9.0 → 2026.9.1 → 2026.9.2 → 2026.10.0 → 2026.11.0.
- Test run: `pip install -r requirements_test.txt && pytest` (from repo root).
- Task 15 is HUMAN-IN-THE-LOOP (live credentials in `scripts/.env`). Never dispatch a subagent for it.
- Notifications endpoint contract (verified live): `GET v6/notifications?type=bell&state=unread&limit=N`; `type` values: MODAL/TOP/BELL/ONBOARDING/EXTERNAL (lowercased in query).
- Details endpoint contract (verified live): `GET v7/accounts/{id}/details` → list of display blocks; electric meter blocks use `code`s (`METER_CHECK_DATE`, `METER_MODEL`, `METER_DATE`, `CHECK_INTERVAL`), water meter blocks have **no codes** — localized `name`s only, serial in the block header.
- History endpoints (verified live): `v7/bills/payments?account=&from=YYYY-MM-DD&to=YYYY-MM-DD` → list of bill id strings (newest first); `v7/payments?...` → list of payment id strings; `v8/payments/bills/{id}` → `{id, amount, timestamp, file, canDownload}`; `v8/payments/{id}` → `{accountId, status, details[], timestamp, id}` (no top-level amount).

## File Structure

```
custom_components/eirc_spb/
├── api.py             # +get_details, +validate_reading, +history methods, +download_bill (P5)
├── models.py          # +AccountDetails/MeterPassport, +parse_details; Account/Meter new fields
├── coordinator.py     # warn-once logging, details cache, daily history fetch
├── notifications.py   # +payment detector (P4)
├── sensor.py          # +LastPaymentSensor, attrs enrichment, bill history attr
├── services.py        # confirm field + pre-validation (P3), +download_bill service (P5)
├── services.yaml      # service descriptions
├── const.py           # VERSION → CalVer
├── manifest.json      # version → CalVer
└── translations/{ru,en}.json
tests/
├── fixtures/          # existing sanitized fixtures + new validate_*.json
├── test_api.py, test_models.py, test_coordinator.py, test_sensor.py, test_services.py
```

---

## Phase 1 — Notifications fix + diagnostic attrs (release 2026.9.0)

### Task 1: Fix notifications URL (`type=bell`)

**Files:**
- Modify: `custom_components/eirc_spb/api.py:179`
- Test: `tests/test_api.py`

**Interfaces:**
- Produces: `get_unread_notifications()` now requests `v6/notifications?type=bell&state=unread&limit=20`.

- [ ] **Step 1: Write the failing test**

In `tests/test_api.py`, replace the loose regex matcher in `test_get_unread_notifications` with a strict query assertion:

```python
async def test_get_unread_notifications(aresponses, client):
    aresponses.add(HOST, "/api/v8/users/auth", "POST", ok({"access": "a1", "auth": "t1"}))
    aresponses.add(
        HOST, "/api/v6/users/current/session", "PATCH", web.Response(status=200)
    )
    seen = {}

    async def notifications_handler(request):
        seen["query"] = dict(request.query)
        return ok(
            [
                {
                    "id": "57295301",
                    "type": "BELL",
                    "title": "Новый счет доступен для оплаты",
                    "message": "<p>Счет за июль 2026 г.</p>",
                    "timestamp": "11.08.2026 15:35",
                }
            ]
        )

    aresponses.add(
        HOST, "/api/v6/notifications", "GET", notifications_handler
    )
    items = await client.get_unread_notifications()
    assert len(items) == 1
    assert items[0]["id"] == "57295301"
    assert seen["query"] == {
        "type": "bell",
        "state": "unread",
        "limit": "20",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_api.py::test_get_unread_notifications -v`
Expected: FAIL — `seen["query"]` lacks `type` (current URL sends only `state`, `limit`).

- [ ] **Step 3: Fix the URL**

In `custom_components/eirc_spb/api.py`, `get_unread_notifications`:

```python
    async def get_unread_notifications(self) -> list[dict]:
        data = await self._request(
            "GET", "v6/notifications?type=bell&state=unread&limit=20"
        )
        return data if isinstance(data, list) else []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_api.py::test_get_unread_notifications -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/eirc_spb/api.py tests/test_api.py
git commit -m "fix: notifications feed requires type=bell query param"
```

### Task 2: Warn-once on swallowed secondary errors

**Files:**
- Modify: `custom_components/eirc_spb/coordinator.py`
- Test: `tests/test_coordinator.py`

**Interfaces:**
- Produces: `EircSpbCoordinator._warn(code: str, err: Exception) -> None` — logs one warning per unique `code`, remembered for the coordinator lifetime.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_coordinator.py`:

```python
async def test_notifications_failure_warns_once(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
):
    client = make_client([make_account("a1", "1000000001")])
    client.get_unread_notifications.side_effect = EircSpbApiError("boom", "500")
    coordinator = build_coordinator(hass, client, ["a1"])
    coordinator.setup_notifications(persistent=False)
    await coordinator.async_config_entry_first_refresh()
    await coordinator.async_refresh()
    warnings = [
        r for r in caplog.records if r.levelname == "WARNING" and "notifications" in r.message
    ]
    assert len(warnings) == 1
    assert coordinator.last_update_success is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_coordinator.py::test_notifications_failure_warns_once -v`
Expected: FAIL — no warning is logged today (error swallowed silently).

- [ ] **Step 3: Implement warn-once**

In `coordinator.py`: add to imports `import time` (not needed — see below; skip). Add to `EircSpbCoordinator.__init__`:

```python
        self._warned: set[str] = set()
```

Add method:

```python
    def _warn(self, code: str, err: Exception) -> None:
        if code in self._warned:
            return
        self._warned.add(code)
        self.logger.warning("eirc_spb %s failed: %s", code, err)
```

Replace the silent fallback in `_async_update_data`:

```python
            try:
                native = await self._client.get_unread_notifications()
            except EircSpbApiError as err:
                self._warn("notifications", err)
                native = []
```

Note: `DataUpdateCoordinator` exposes `self.logger`.

- [ ] **Step 3.1: Fix the warning-message assertion if needed**

If `r.message` interpolation differs, match on `r.getMessage()`. Adjust the test filter to `"notifications" in r.getMessage()`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_coordinator.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/eirc_spb/coordinator.py tests/test_coordinator.py
git commit -m "feat: warn once when secondary api calls fail during refresh"
```

### Task 3: `auto_payment` / `delivery` diagnostic attributes

**Files:**
- Modify: `custom_components/eirc_spb/models.py` (`Account`, `parse_accounts`)
- Modify: `custom_components/eirc_spb/sensor.py` (`AccrualsSensor.extra_state_attributes`)
- Test: `tests/test_models.py`, `tests/test_sensor.py`

**Interfaces:**
- Produces: `Account.auto_payment: bool | None`, `Account.delivery: str | None`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_models.py` (follow existing style; load fixtures from `tests/fixtures/`):

```python
def test_parse_accounts_maps_diag_fields():
    accounts = parse_accounts(load("accounts"))
    assert accounts[0].auto_payment is True
    assert accounts[0].delivery == "PAPER"
```

In `tests/test_sensor.py` (uses the file's existing `build_data()` / `setup_sensors()` harness and entity-registry lookup):

```python
async def test_accruals_sensor_diagnostic_attributes(hass: HomeAssistant):
    data = build_data()
    data.accounts["a1"].auto_payment = True
    data.accounts["a1"].delivery = "PAPER"
    await setup_sensors(hass, data)
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, "eirc_spb_1000000001_accruals"
    )
    state = hass.states.get(entity_id)
    assert state.attributes["auto_payment"] is True
    assert state.attributes["delivery"] == "PAPER"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_models.py::test_parse_accounts_maps_diag_fields tests/test_sensor.py -v`
Expected: FAIL — `auto_payment` attribute missing.

- [ ] **Step 3: Implement**

`models.py` — add fields to `Account`:

```python
    auto_payment: bool | None = None
    delivery: str | None = None
```

In `parse_accounts`, add to the constructor call:

```python
                auto_payment=item.get("autoPaymentOn"),
                delivery=item.get("delivery"),
```

`sensor.py` — in `AccrualsSensor.extra_state_attributes`, before the `**account.accruals_breakdown` spread:

```python
        attrs = {"period": account.accruals_period}
        if account.auto_payment is not None:
            attrs["auto_payment"] = account.auto_payment
        if account.delivery is not None:
            attrs["delivery"] = account.delivery
        return {
            **attrs,
            **account.accruals_breakdown,
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_models.py tests/test_sensor.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/eirc_spb/models.py custom_components/eirc_spb/sensor.py tests/test_models.py tests/test_sensor.py
git commit -m "feat: auto_payment and delivery diagnostic attributes on accruals sensor"
```

### Task 4: CalVer switch + release 2026.9.0

**Files:**
- Modify: `custom_components/eirc_spb/const.py:21`, `custom_components/eirc_spb/manifest.json`, `tests/test_api.py:263`, `README.md`

- [ ] **Step 1: Update the UA test first**

In `tests/test_api.py`, `test_data_request_sends_user_agent_with_injected_session`:

```python
    assert seen["user_agent"] == "home-assistant-eirc-spb/2026.9.0"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_api.py::test_data_request_sends_user_agent_with_injected_session -v`
Expected: FAIL — UA still says `1.5.2`.

- [ ] **Step 3: Bump versions**

`const.py`: `VERSION = "2026.9.0"`. `manifest.json`: `"version": "2026.9.0"`.

- [ ] **Step 4: README**

Update «Уведомления» table row for `eirc_spb_notification` (unchanged behavior, now actually works) and add to «Возможности» table:

```markdown
| `auto_payment`, `delivery` | Диагностические атрибуты сенсора начислений (автоплатёж включён, способ получения счёта) | — | — |
```

Replace any remaining `1.5.x` references in README with the new version where a version is shown.

- [ ] **Step 5: Run full suite + release commit**

Run: `pytest -q`
Expected: all PASS

```bash
git add -A
git commit -m "chore: release 2026.9.0"
```

---

## Phase 2 — Details enrichment (release 2026.9.1)

### Task 5: `parse_details` parser

**Files:**
- Modify: `custom_components/eirc_spb/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces:

```python
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
    meters: dict[str, MeterPassport] = field(default_factory=dict)  # keyed by serial

def parse_details(raw: list) -> AccountDetails: ...
```

- [ ] **Step 1: Write the failing tests**

In `tests/test_models.py` (uses the existing sanitized `account_details.json`; structure preserved, values partly replaced by «Тест» — assert on structure and on the fields that kept real values):

```python
from custom_components.eirc_spb.models import parse_details


def test_parse_details_apartment():
    d = parse_details(load("account_details"))
    assert d.area == "Тест"
    assert d.rooms == "Тест"
    assert d.owner == "Тест"
    assert d.management_company == "Тест"


def test_parse_details_meter_passports_by_serial():
    d = parse_details(load("account_details"))
    assert set(d.meters) == {"100001", "100003", "100005", "100007", "100008"}
    electric = d.meters["100008"]
    assert electric.verification_date == "14.11.2036"
    assert electric.install_date == "07.10.2020"
    water = d.meters["100001"]
    assert water.verification_date == "17.10.2025"
    assert water.install_date == "14.01.2020"


def test_parse_details_tariffs():
    d = parse_details(load("account_details"))
    assert d.tariffs["Услуга 2"] == 22.36
    assert d.tariffs["Услуга 13"] == "2,131,47"


def test_parse_details_ignores_unknown_blocks():
    d = parse_details(
        [{"header": "Непонятный блок", "content": [{"name": "x", "value": "y"}]}]
    )
    assert d.tariffs == {}
    assert d.meters == {}
    assert d.area is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_models.py -v -k details`
Expected: FAIL — `parse_details` not defined.

- [ ] **Step 3: Implement in `models.py`**

```python
import re


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
```

Place `MeterPassport`/`AccountDetails` dataclasses above `parse_accounts`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_models.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/eirc_spb/models.py tests/test_models.py
git commit -m "feat: parse account details endpoint into structured model"
```

### Task 6: `api.get_details`

**Files:**
- Modify: `custom_components/eirc_spb/api.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Produces: `async def get_details(self, account_id: str) -> AccountDetails` — GET `v7/accounts/{account_id}/details`.

- [ ] **Step 1: Write the failing test**

In `tests/test_api.py`:

```python
async def test_get_details(aresponses, client):
    aresponses.add(HOST, "/api/v8/users/auth", "POST", ok({"access": "a1", "auth": "t1"}))
    aresponses.add(
        HOST, "/api/v7/accounts/910000001/details", "GET", ok(load("account_details"))
    )
    details = await client.get_details("910000001")
    assert details.meters["100008"].verification_date == "14.11.2036"
    assert details.tariffs["Услуга 2"] == 22.36
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_api.py::test_get_details -v`
Expected: FAIL — no `get_details`.

- [ ] **Step 3: Implement**

In `api.py` — extend the `models` import with `AccountDetails, parse_details`, then next to `get_address`:

```python
    async def get_details(self, account_id: str) -> AccountDetails:
        data = await self._request("GET", f"v7/accounts/{account_id}/details")
        return parse_details(data if isinstance(data, list) else [])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_api.py::test_get_details -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/eirc_spb/api.py tests/test_api.py
git commit -m "feat: api client method for account details"
```

### Task 7: Coordinator details cache (24h) + merge

**Files:**
- Modify: `custom_components/eirc_spb/coordinator.py`, `custom_components/eirc_spb/models.py`
- Test: `tests/test_coordinator.py`

**Interfaces:**
- Consumes: `client.get_details(account_id) -> AccountDetails` (Task 6).
- Produces: `Account.details: AccountDetails | None`; `Meter.model: str | None`, `Meter.install_date: str | None` (verification_date already exists on Meter).

- [ ] **Step 1: Write the failing tests**

In `tests/test_coordinator.py`:

```python
from custom_components.eirc_spb.models import AccountDetails, MeterPassport

DETAILS = AccountDetails(
    area="32.5",
    rooms="1",
    owner="Тест",
    management_company='ООО "Тест 1"',
    tariffs={"Услуга 2": 22.36},
    meters={
        "m1": MeterPassport(
            serial="m1",
            model="НАРТИС",
            install_date="22.11.2021",
            verification_date="30.12.2037",
        )
    },
)


async def test_coordinator_merges_details(hass: HomeAssistant):
    client = make_client([make_account("a1", "1000000001")])
    client.get_details.return_value = DETAILS
    coordinator = build_coordinator(hass, client, ["a1"])
    await coordinator.async_config_entry_first_refresh()
    account = coordinator.data.accounts["a1"]
    assert account.details is DETAILS
    meter = coordinator.data.meters["m1"]
    assert meter.verification_date == "30.12.2037"
    assert meter.model == "НАРТИС"
    assert meter.install_date == "22.11.2021"
    client.get_details.assert_awaited_once_with("a1")


async def test_coordinator_details_cached_within_24h(hass: HomeAssistant):
    client = make_client([make_account("a1", "1000000001")])
    client.get_details.return_value = DETAILS
    coordinator = build_coordinator(hass, client, ["a1"])
    await coordinator.async_config_entry_first_refresh()
    await coordinator.async_refresh()
    client.get_details.assert_awaited_once()


async def test_coordinator_details_failure_is_non_fatal(hass: HomeAssistant):
    client = make_client([make_account("a1", "1000000001")])
    client.get_details.side_effect = EircSpbApiError("boom")
    coordinator = build_coordinator(hass, client, ["a1"])
    await coordinator.async_config_entry_first_refresh()
    assert coordinator.last_update_success is True
    assert coordinator.data.accounts["a1"].details is None
```

Note: `make_meter()` sets no serial; for merge-by-serial to hit, either set `serial="m1"` in the local `DETAILS.meters` key AND extend `make_meter` with `serial="m1"` only in these tests via `client.get_meters.return_value = [make_meter()]` override:

```python
def make_detailed_meter() -> Meter:
    m = make_meter()
    m.serial = "m1"
    return m
```

and set `client.get_meters.return_value = [make_detailed_meter()]` in all three tests above.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_coordinator.py -v -k details`
Expected: FAIL — coordinator never calls `get_details`.

- [ ] **Step 3: Implement**

`models.py` — `Meter` gets `model: str | None = None` and `install_date: str | None = None` (after `serial`); `Account` gets `details: "AccountDetails | None" = None` (define `AccountDetails` above `Account`, or use `from __future__ import annotations` — the file already imports dataclasses; simplest is declaring `AccountDetails` before `Account`).

`coordinator.py` — in `__init__`:

```python
        self._details_cache: dict[str, tuple[float, AccountDetails]] = {}
```

Add constant at module top:

```python
DETAILS_TTL_SECONDS = 24 * 3600
```

Helper in the class:

```python
    async def _async_get_details(self, account_id: str) -> AccountDetails | None:
        import time

        cached = self._details_cache.get(account_id)
        if cached and time.monotonic() - cached[0] < DETAILS_TTL_SECONDS:
            return cached[1]
        try:
            details = await self._client.get_details(account_id)
        except EircSpbAuthError:
            raise
        except EircSpbApiError as err:
            self._warn("details", err)
            return cached[1] if cached else None
        self._details_cache[account_id] = (time.monotonic(), details)
        return details
```

Move `import time` to the module imports instead of inside the method. In `_async_update_data`, after `data.accounts[account.account_id] = account` and the meters loop, add (inside the per-account loop, after meters are fetched):

```python
                details = await self._async_get_details(account.account_id)
                if details is not None:
                    account.details = details
                    for meter in data.meters.values():
                        if meter.account_id != account.account_id:
                            continue
                        passport = details.meters.get(meter.serial or "")
                        if passport is None:
                            continue
                        meter.verification_date = passport.verification_date
                        meter.model = passport.model
                        meter.install_date = passport.install_date
```

Note ordering: this block must run AFTER `data.meters` is populated but inside the same account loop — place it right after the `for meter in await self._client.get_meters(...)` loop.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_coordinator.py -v`
Expected: all PASS (including the pre-existing tests — `make_client` is an `AsyncMock`, so `get_details` returns a MagicMock that is not `AccountDetails`; guard: the mock returns an `AccountDetails` only if set. To keep old tests passing, default the mock: in `make_client` add `client.get_details.return_value = None`.)

- [ ] **Step 5: Commit**

```bash
git add custom_components/eirc_spb/coordinator.py custom_components/eirc_spb/models.py tests/test_coordinator.py
git commit -m "feat: coordinator fetches account details with 24h cache"
```

### Task 8: Sensor attributes + README + release 2026.9.1

**Files:**
- Modify: `custom_components/eirc_spb/sensor.py`, `README.md`, `custom_components/eirc_spb/const.py`, `custom_components/eirc_spb/manifest.json`
- Test: `tests/test_sensor.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_sensor.py` (same harness as Task 3; import `AccountDetails, MeterPassport` from models):

```python
PASSPORT_DETAILS = AccountDetails(
    area="32.5",
    rooms="1",
    owner="Тест",
    management_company='ООО "Тест 1"',
    tariffs={"Услуга 2": 22.36},
    meters={
        "100000": MeterPassport(
            serial="100000",
            model="НАРТИС",
            install_date="22.11.2021",
            verification_date="30.12.2037",
        )
    },
)


async def test_meter_sensor_passport_attributes(hass: HomeAssistant):
    data = build_data()
    data.accounts["a1"].details = PASSPORT_DETAILS
    data.meters["m1"].verification_date = "30.12.2037"
    data.meters["m1"].model = "НАРТИС"
    data.meters["m1"].install_date = "22.11.2021"
    await setup_sensors(hass, data)
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, "eirc_spb_1000000001_m1_0"
    )
    state = hass.states.get(entity_id)
    assert state.attributes["verification_date"] == "30.12.2037"
    assert state.attributes["model"] == "НАРТИС"
    assert state.attributes["install_date"] == "22.11.2021"


async def test_accruals_sensor_apartment_attributes(hass: HomeAssistant):
    data = build_data()
    data.accounts["a1"].details = PASSPORT_DETAILS
    await setup_sensors(hass, data)
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, "eirc_spb_1000000001_accruals"
    )
    state = hass.states.get(entity_id)
    assert state.attributes["area"] == "32.5"
    assert state.attributes["management_company"] == 'ООО "Тест 1"'
    assert state.attributes["tariffs"] == {"Услуга 2": 22.36}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_sensor.py -v -k "passport or apartment"`
Expected: FAIL — attributes missing.

- [ ] **Step 3: Implement**

`MeterSensor.extra_state_attributes` — replace the return dict with:

```python
        attrs = {
            ATTR_ACCOUNT_ID: self._account_id,
            ATTR_METER_ID: self._meter_id,
            ATTR_SCALE_ID: self._scale_id,
            "last_submit": scale.last_submit if scale else None,
            "meter_serial": meter.serial if meter else None,
            "verification_date": meter.verification_date if meter else None,
        }
        if meter is not None and meter.model is not None:
            attrs["model"] = meter.model
        if meter is not None and meter.install_date is not None:
            attrs["install_date"] = meter.install_date
        return attrs
```

`AccrualsSensor.extra_state_attributes` — after the diagnostic attrs from Task 3, before breakdown spread:

```python
        details = account.details
        if details is not None:
            if details.area is not None:
                attrs["area"] = details.area
            if details.rooms is not None:
                attrs["rooms"] = details.rooms
            if details.owner is not None:
                attrs["owner"] = details.owner
            if details.management_company is not None:
                attrs["management_company"] = details.management_company
            if details.tariffs:
                attrs["tariffs"] = details.tariffs
```

- [ ] **Step 4: README**

In the meter-sensor row of «Возможности», replace `(атрибут зарезервирован, значение пока не передаётся)` for `verification_date` with real semantics, and add `model` / `install_date` to the attribute list. Add accruals-sensor attrs `area`, `rooms`, `owner`, `management_company`, `tariffs`.

- [ ] **Step 5: Full suite + release**

Run: `pytest -q` — all PASS. Bump `VERSION`/manifest to `2026.9.1`.

```bash
git add -A
git commit -m "feat: meter passport and apartment attributes from details endpoint (release 2026.9.1)"
```

---

## Phase 3 — Reading pre-validation (release 2026.9.2)

### Task 9: `api.validate_reading`

**Files:**
- Modify: `custom_components/eirc_spb/api.py`
- Create: `tests/fixtures/validate_ok.json`, `tests/fixtures/validate_reject.json`
- Test: `tests/test_api.py`

**Interfaces:**
- Produces:

```python
async def validate_reading(
    self, account_id: str, registration: str, scale_id: str, value: float
) -> dict:
    # {"status": "ok"|"warning"|"error"|"unavailable", "message": str | None}
```

Response contract (to refine in Task 15-adjacent live probe if credentials are available; conservative mapping below):
- HTTP 200 → `{"status": "ok", "message": <body message or None>}`; if the 200 body is a dict with a non-empty `message`, status is `"warning"`.
- HTTP 404 → `{"status": "unavailable", "message": None}` (endpoint missing — skip validation).
- other >= 400 → `{"status": "error", "message": <server message>}`.

- [ ] **Step 1: Create fixtures**

`tests/fixtures/validate_ok.json`:

```json
{}
```

`tests/fixtures/validate_reject.json`:

```json
{"code": "312", "message": "Ваш расход больше обычного"}
```

- [ ] **Step 2: Write the failing tests**

```python
async def test_validate_reading_ok(aresponses, client):
    aresponses.add(HOST, "/api/v8/users/auth", "POST", ok({"access": "a1", "auth": "t1"}))
    aresponses.add(
        HOST,
        "/api/v7/accounts/910000001/meters/100000/scales/0/reading/234.854/validate",
        "POST",
        ok(load("validate_ok")),
    )
    result = await client.validate_reading("910000001", "100000", "0", 234.854)
    assert result["status"] == "ok"


async def test_validate_reading_warning_on_message(aresponses, client):
    aresponses.add(HOST, "/api/v8/users/auth", "POST", ok({"access": "a1", "auth": "t1"}))
    aresponses.add(
        HOST,
        "/api/v7/accounts/910000001/meters/100000/scales/0/reading/234.854/validate",
        "POST",
        ok({"message": "Ваш расход больше обычного"}),
    )
    result = await client.validate_reading("910000001", "100000", "0", 234.854)
    assert result["status"] == "warning"
    assert result["message"] == "Ваш расход больше обычного"


async def test_validate_reading_reject(aresponses, client):
    aresponses.add(HOST, "/api/v8/users/auth", "POST", ok({"access": "a1", "auth": "t1"}))
    aresponses.add(
        HOST,
        "/api/v7/accounts/910000001/meters/100000/scales/0/reading/234.854/validate",
        "POST",
        ok(load("validate_reject"), status=400),
    )
    result = await client.validate_reading("910000001", "100000", "0", 234.854)
    assert result["status"] == "error"
    assert "расход" in result["message"]


async def test_validate_reading_unavailable(aresponses, client):
    aresponses.add(HOST, "/api/v8/users/auth", "POST", ok({"access": "a1", "auth": "t1"}))
    aresponses.add(
        HOST,
        "/api/v7/accounts/910000001/meters/100000/scales/0/reading/234.854/validate",
        "POST",
        web.Response(status=404, text=""),
    )
    result = await client.validate_reading("910000001", "100000", "0", 234.854)
    assert result["status"] == "unavailable"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_api.py -v -k validate`
Expected: FAIL — method missing.

- [ ] **Step 4: Implement**

In `api.py`:

```python
    async def validate_reading(
        self, account_id: str, registration: str, scale_id: str, value: float
    ) -> dict:
        try:
            data = await self._request(
                "POST",
                f"v7/accounts/{account_id}/meters/{registration}/scales/"
                f"{int(scale_id)}/reading/{value}/validate",
                {},
            )
        except EircSpbApiError as err:
            if err.code == "404":
                return {"status": "unavailable", "message": None}
            return {"status": "error", "message": str(err)}
        message = _message(data) if data != {} else None
        if message == "API request failed":
            message = None
        status = "warning" if message else "ok"
        return {"status": status, "message": message}
```

Note: `_message` returns the fallback string "API request failed" when there is no message — normalize it to `None` as above.

- [ ] **Step 5: Run tests + commit**

Run: `pytest tests/test_api.py -v -k validate` — PASS.

```bash
git add custom_components/eirc_spb/api.py tests/test_api.py tests/fixtures/validate_ok.json tests/fixtures/validate_reject.json
git commit -m "feat: api method to validate a candidate meter reading"
```

### Task 10: `confirm` field + pre-validation in the service

**Files:**
- Modify: `custom_components/eirc_spb/services.py`, `custom_components/eirc_spb/services.yaml`, `custom_components/eirc_spb/translations/{ru,en}.json`
- Test: `tests/test_services.py`

**Interfaces:**
- Consumes: `client.validate_reading(account_id, registration, scale_id, value) -> dict` (Task 9).
- Produces: `eirc_spb.send_meter_reading` accepts `confirm: bool` (default `false`); response gains `"validated": true|false`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_services.py`, first extend the existing `install_runtime` helper so the mocked client can await validation (existing tests keep passing — default means "skip validation"):

```python
def install_runtime(hass: HomeAssistant, data: EircSpbData | None = None):
    coordinator = MagicMock()
    coordinator.data = data if data is not None else make_data()
    coordinator.async_request_refresh = AsyncMock()
    client = MagicMock()
    client.submit_reading = AsyncMock(return_value=SUBMIT_RESULT)
    client.validate_reading = AsyncMock(
        return_value={"status": "unavailable", "message": None}
    )
    runtime = EircSpbRuntime(client=client, coordinator=coordinator)
    hass.data.setdefault(DOMAIN, {})["test_entry"] = runtime
    return runtime
```

Update the existing `test_send_reading_success` assertion (response now carries `validated`):

```python
    assert response == {**SUBMIT_RESULT, "validated": False}
```

New tests (reuse `install_runtime` / `call_service` as-is):

```python
async def test_send_reading_validates_first(hass: HomeAssistant):
    from custom_components.eirc_spb.services import async_setup_services

    runtime = install_runtime(hass)
    runtime.client.validate_reading = AsyncMock(
        return_value={"status": "ok", "message": None}
    )
    await async_setup_services(hass)
    hass.states.async_set("sensor.m", "1", {ATTR_METER_ID: "m1", ATTR_SCALE_ID: "0"})
    response = await call_service(
        hass,
        {"entity_id": "sensor.m", "readings": [{"scale_id": 0, "value": 123}]},
        return_response=True,
    )
    runtime.client.validate_reading.assert_awaited_once_with("a1", "m1", "0", 123.0)
    runtime.client.submit_reading.assert_awaited_once()
    assert response["validated"] is True


async def test_send_reading_error_blocks_submit(hass: HomeAssistant):
    from custom_components.eirc_spb.services import async_setup_services

    runtime = install_runtime(hass)
    runtime.client.validate_reading = AsyncMock(
        return_value={"status": "error", "message": "Показания меньше предыдущих"}
    )
    await async_setup_services(hass)
    hass.states.async_set("sensor.m", "1", {ATTR_METER_ID: "m1", ATTR_SCALE_ID: "0"})
    with pytest.raises(HomeAssistantError, match="Показания меньше"):
        await call_service(
            hass,
            {"entity_id": "sensor.m", "readings": [{"scale_id": "0", "value": 1}]},
        )
    runtime.client.submit_reading.assert_not_awaited()


async def test_send_reading_warning_needs_confirm(hass: HomeAssistant):
    from custom_components.eirc_spb.services import async_setup_services

    runtime = install_runtime(hass)
    runtime.client.validate_reading = AsyncMock(
        return_value={"status": "warning", "message": "Ваш расход больше обычного"}
    )
    await async_setup_services(hass)
    hass.states.async_set("sensor.m", "1", {ATTR_METER_ID: "m1", ATTR_SCALE_ID: "0"})
    with pytest.raises(HomeAssistantError, match="подтвердите"):
        await call_service(
            hass,
            {"entity_id": "sensor.m", "readings": [{"scale_id": "0", "value": 500}]},
        )
    runtime.client.submit_reading.assert_not_awaited()


async def test_send_reading_warning_confirmed_submits(hass: HomeAssistant):
    from custom_components.eirc_spb.services import async_setup_services

    runtime = install_runtime(hass)
    runtime.client.validate_reading = AsyncMock(
        return_value={"status": "warning", "message": "Ваш расход больше обычного"}
    )
    await async_setup_services(hass)
    hass.states.async_set("sensor.m", "1", {ATTR_METER_ID: "m1", ATTR_SCALE_ID: "0"})
    response = await call_service(
        hass,
        {
            "entity_id": "sensor.m",
            "readings": [{"scale_id": "0", "value": 500}],
            "confirm": True,
        },
        return_response=True,
    )
    runtime.client.submit_reading.assert_awaited_once()
    assert response["validated"] is True


async def test_send_reading_validation_unavailable_submits(hass: HomeAssistant):
    from custom_components.eirc_spb.services import async_setup_services

    runtime = install_runtime(hass)
    await async_setup_services(hass)
    hass.states.async_set("sensor.m", "1", {ATTR_METER_ID: "m1", ATTR_SCALE_ID: "0"})
    response = await call_service(
        hass,
        {"entity_id": "sensor.m", "readings": [{"scale_id": "0", "value": 234}]},
        return_response=True,
    )
    runtime.client.submit_reading.assert_awaited_once()
    assert response["validated"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_services.py -v`
Expected: FAIL — `confirm` unknown / no validation call.

- [ ] **Step 3: Implement in `services.py`**

Extend the schema:

```python
SEND_METER_READING_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_ids,
        vol.Required("readings"): vol.All(cv.ensure_list, [READING_SCHEMA]),
        vol.Optional("confirm", default=False): bool,
    }
)
```

In `handle_send_meter_reading`, after `readings` is built and before `submit_reading`:

```python
        validated = False
        for reading in readings:
            result = await runtime.client.validate_reading(
                meter.account_id,
                meter.meter_id,
                reading["scale_id"],
                reading["value"],
            )
            if result["status"] == "error":
                raise HomeAssistantError(
                    result.get("message") or "Сервер отклонил показания"
                )
            if result["status"] == "warning" and not call.data["confirm"]:
                raise HomeAssistantError(
                    f"Подтвердите отправку (confirm: true): "
                    f"{result.get('message') or 'необычный расход'}"
                )
            if result["status"] in {"ok", "warning"}:
                validated = True
```

Extend the return dict:

```python
        return {
            "code": str(result.get("code", "")),
            "message": result.get("message", ""),
            "validated": validated,
        }
```

- [ ] **Step 4: services.yaml + translations**

`services.yaml` — add to `send_meter_reading.fields`:

```yaml
confirm:
  name: Подтвердить
  description: >-
    Отправить показания, даже если сервер предупреждает о необычном расходе.
  example: "true"
```

Add the matching keys to `translations/ru.json` and `translations/en.json` services sections (follow the existing key layout in those files).

- [ ] **Step 5: Full suite + release**

Run: `pytest -q` — all PASS. Bump to `2026.9.2` in `const.py` + `manifest.json`. Update README «Отправка показаний» with the `confirm` field and pre-validation behavior.

```bash
git add -A
git commit -m "feat: pre-validate readings and confirm option (release 2026.9.2)"
```

---

## Phase 4 — Bills/payments history (release 2026.10.0)

### Task 11: History API methods

**Files:**
- Modify: `custom_components/eirc_spb/api.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Produces:

```python
async def get_bills_history(self, account_id: str, date_from: str, date_to: str) -> list[str]
async def get_payments_history(self, account_id: str, date_from: str, date_to: str) -> list[str]
async def get_bill(self, bill_id: str) -> dict          # {id, amount, timestamp, file, canDownload}
async def get_payment(self, payment_id: str) -> dict    # {accountId, status, details[], timestamp, id}
```

`date_from`/`date_to` are ISO `YYYY-MM-DD`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_get_bills_and_payments_history(aresponses, client):
    aresponses.add(HOST, "/api/v8/users/auth", "POST", ok({"access": "a1", "auth": "t1"}))
    aresponses.add(
        HOST,
        "/api/v7/bills/payments?account=910000001&from=2026-01-01&to=2026-09-06",
        "GET",
        ok(load("bills_payments")),
    )
    aresponses.add(
        HOST,
        "/api/v7/payments?account=910000001&from=2026-01-01&to=2026-09-06",
        "GET",
        ok(load("payments_list")),
    )
    bills = await client.get_bills_history("910000001", "2026-01-01", "2026-09-06")
    payments = await client.get_payments_history("910000001", "2026-01-01", "2026-09-06")
    assert bills[0] == "26071000000001"
    assert len(payments) == 21


async def test_get_bill_and_payment_details(aresponses, client):
    aresponses.add(HOST, "/api/v8/users/auth", "POST", ok({"access": "a1", "auth": "t1"}))
    aresponses.add(
        HOST, "/api/v8/payments/bills/26071000000001", "GET", ok(load("bill_26071000000001"))
    )
    aresponses.add(HOST, "/api/v8/payments/900000001", "GET", ok(load("payment_a")))
    bill = await client.get_bill("26071000000001")
    payment = await client.get_payment("900000001")
    assert bill["amount"] == 7633.65
    assert payment["status"] == "SUCCESS"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_api.py -v -k "history or bill_and"`
Expected: FAIL — methods missing.

- [ ] **Step 3: Implement**

In `api.py`:

```python
    async def get_bills_history(
        self, account_id: str, date_from: str, date_to: str
    ) -> list[str]:
        data = await self._request(
            "GET",
            f"v7/bills/payments?account={account_id}&from={date_from}&to={date_to}",
        )
        return [str(i) for i in data] if isinstance(data, list) else []

    async def get_payments_history(
        self, account_id: str, date_from: str, date_to: str
    ) -> list[str]:
        data = await self._request(
            "GET",
            f"v7/payments?account={account_id}&from={date_from}&to={date_to}",
        )
        return [str(i) for i in data] if isinstance(data, list) else []

    async def get_bill(self, bill_id: str) -> dict:
        data = await self._request("GET", f"v8/payments/bills/{bill_id}")
        return data if isinstance(data, dict) else {}

    async def get_payment(self, payment_id: str) -> dict:
        data = await self._request("GET", f"v8/payments/{payment_id}")
        return data if isinstance(data, dict) else {}
```

- [ ] **Step 4: Run tests + commit**

Run: `pytest tests/test_api.py -v` — PASS.

```bash
git add custom_components/eirc_spb/api.py tests/test_api.py
git commit -m "feat: api methods for bills and payments history"
```

### Task 12: Coordinator daily history fetch

**Files:**
- Modify: `custom_components/eirc_spb/coordinator.py`, `custom_components/eirc_spb/models.py`
- Test: `tests/test_coordinator.py`

**Interfaces:**
- Consumes: Task 11 methods.
- Produces: `Account.bills_history: list[dict]` (`[{id, amount, timestamp}]`, newest first) and `Account.last_payment: dict | None` (`{id, amount, date, status}`).

- [ ] **Step 1: Write the failing tests**

In `tests/test_coordinator.py`:

```python
BILL_DETAIL = {"id": "26071000000001", "amount": 7633.65, "timestamp": "14.02.2026 00:00:00"}
PAYMENT_DETAIL = {
    "id": "900000001",
    "status": "SUCCESS",
    "timestamp": "2026-08-15T10:11:32",
    "details": [
        {"checked": True, "charge": {"accrued": 100.0}},
        {"checked": True, "charge": {"accrued": 50.0}},
    ],
}


def make_history_client(accounts):
    client = make_client(accounts)
    client.get_bills_history.return_value = ["26071000000001", "26061000000001"]
    client.get_payments_history.return_value = ["900000001", "900000002"]
    client.get_bill.side_effect = [
        BILL_DETAIL,
        {"id": "26061000000001", "amount": 7000.0, "timestamp": "14.01.2026 00:00:00"},
    ]
    client.get_payment.side_effect = [PAYMENT_DETAIL]
    client.get_details.return_value = None
    return client


async def test_coordinator_fetches_history_daily(hass: HomeAssistant):
    client = make_history_client([make_account("a1", "1000000001")])
    coordinator = build_coordinator(hass, client, ["a1"])
    await coordinator.async_config_entry_first_refresh()
    account = coordinator.data.accounts["a1"]
    assert account.bills_history == [
        {"id": "26071000000001", "amount": 7633.65, "timestamp": "14.02.2026 00:00:00"},
        {"id": "26061000000001", "amount": 7000.0, "timestamp": "14.01.2026 00:00:00"},
    ]
    assert account.last_payment == {
        "id": "900000001",
        "amount": 150.0,
        "date": "2026-08-15T10:11:32",
        "status": "SUCCESS",
    }
    await coordinator.async_refresh()
    client.get_bills_history.assert_awaited_once()
    client.get_payments_history.assert_awaited_once()


async def test_coordinator_history_failure_is_non_fatal(hass: HomeAssistant):
    client = make_history_client([make_account("a1", "1000000001")])
    client.get_bills_history.side_effect = EircSpbApiError("boom")
    coordinator = build_coordinator(hass, client, ["a1"])
    await coordinator.async_config_entry_first_refresh()
    assert coordinator.last_update_success is True
    assert coordinator.data.accounts["a1"].bills_history == []
```

Note on `get_payment.side_effect`: only one payment detail is fetched because the first payment is newer than the baseline (None) — see implementation.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_coordinator.py -v -k history`
Expected: FAIL — history never fetched.

- [ ] **Step 3: Implement**

`models.py` — `Account` gains:

```python
    bills_history: list[dict] = field(default_factory=list)
    last_payment: dict | None = None
```

`coordinator.py` — module constant:

```python
HISTORY_TTL_SECONDS = 24 * 3600
HISTORY_WINDOW_DAYS = 365
```

In `__init__`:

```python
        self._history_fetched_at: dict[str, float] = {}
        self._known_bill_ids: dict[str, set[str]] = {}
```

Helper:

```python
    async def _async_update_history(self, account: Account) -> None:
        import time

        fetched = self._history_fetched_at.get(account.account_id)
        if fetched and time.monotonic() - fetched < HISTORY_TTL_SECONDS:
            return
        self._history_fetched_at[account.account_id] = time.monotonic()
        date_to = date.today()
        date_from = date_to - timedelta(days=HISTORY_WINDOW_DAYS)
        try:
            known = self._known_bill_ids.setdefault(account.account_id, set())
            bill_ids = await self._client.get_bills_history(
                account.account_id, date_from.isoformat(), date_to.isoformat()
            )
            fresh = [b for b in bill_ids if b not in known]
            if fresh:
                fresh_set = set(fresh)
                details = [
                    {
                        "id": str(b.get("id", "")),
                        "amount": b.get("amount"),
                        "timestamp": b.get("timestamp"),
                    }
                    for b in [
                        await self._client.get_bill(bill_id)
                        for bill_id in reversed(fresh)
                    ]
                    if b
                ]
                known.update(bill_ids)
                account.bills_history = details + [
                    h for h in account.bills_history if h["id"] not in fresh_set
                ]
                account.bills_history.sort(key=lambda h: str(h["timestamp"]), reverse=True)
            payment_ids = await self._client.get_payments_history(
                account.account_id, date_from.isoformat(), date_to.isoformat()
            )
            baseline = account.last_payment["date"] if account.last_payment else None
            for payment_id in payment_ids[:5]:
                payment = await self._client.get_payment(payment_id)
                if not payment:
                    continue
                entry = {
                    "id": str(payment.get("id", payment_id)),
                    "amount": self._payment_amount(payment),
                    "date": payment.get("timestamp"),
                    "status": payment.get("status"),
                }
                if baseline is None or str(entry["date"]) > baseline:
                    account.last_payment = entry
                    baseline = str(entry["date"])
                else:
                    break
        except EircSpbAuthError:
            raise
        except EircSpbApiError as err:
            self._warn("history", err)

    @staticmethod
    def _payment_amount(payment: dict) -> float | None:
        details = payment.get("details") or []
        try:
            return round(
                sum(
                    float(d["charge"]["accrued"])
                    for d in details
                    if d.get("checked") and d.get("charge", {}).get("accrued") is not None
                ),
                2,
            )
        except (TypeError, ValueError):
            return None
```

Move `import time` to module imports; add `from datetime import date, timedelta` (file currently imports `timedelta` only). Call `await self._async_update_history(account)` in `_async_update_data` right after the details block from Task 7, still inside the per-account loop, guarded by the same account filter.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_coordinator.py -v`
Expected: PASS. Also confirm `make_client`'s `AsyncMock` defaults don't leak MagicMock into `bills_history` — set `client.get_bills_history.return_value = []` and `client.get_payments_history.return_value = []` inside `make_client` so older tests stay green.

- [ ] **Step 5: Commit**

```bash
git add custom_components/eirc_spb/coordinator.py custom_components/eirc_spb/models.py tests/test_coordinator.py
git commit -m "feat: coordinator fetches bills and payments history daily"
```

### Task 13: `eirc_spb_new_payment` event + `LastPaymentSensor`

**Files:**
- Modify: `custom_components/eirc_spb/notifications.py`, `custom_components/eirc_spb/coordinator.py`, `custom_components/eirc_spb/sensor.py`
- Test: `tests/test_coordinator.py`, `tests/test_sensor.py`

**Interfaces:**
- Consumes: `Account.last_payment` (Task 12).
- Produces: HA event `eirc_spb_new_payment` with data `{payment_id, amount, date, status, number}`; sensor class `LastPaymentSensor` (`…_last_payment`, monetary, attrs `payment_id`, `date`, `status`).

- [ ] **Step 1: Write the failing tests**

In `tests/test_coordinator.py`:

```python
async def test_new_payment_event_fires_once(hass: HomeAssistant):
    client = make_history_client([make_account("a1", "71000000001")])
    coordinator = build_coordinator(hass, client, ["a1"])
    coordinator.setup_notifications(persistent=False)
    events = []
    hass.bus.async_listen("eirc_spb_new_payment", events.append)
    await coordinator.async_config_entry_first_refresh()
    assert events == []
    client.get_payment.side_effect = [
        {
            "id": "900000003",
            "status": "SUCCESS",
            "timestamp": "2026-09-01T10:00:00",
            "details": [{"checked": True, "charge": {"accrued": 200.0}}],
        }
    ]
    client.get_bills_history.return_value = []
    coordinator._history_fetched_at.clear()
    await coordinator.async_refresh()
    assert len(events) == 1
    assert events[0].data["payment_id"] == "900000003"
    assert events[0].data["amount"] == 200.0
    coordinator._history_fetched_at.clear()
    await coordinator.async_refresh()
    assert len(events) == 1
```

(First refresh establishes the baseline silently, mirroring `new_bill` behavior.)

In `tests/test_sensor.py` (harness pattern — set fields on `build_data()`, not a client):

```python
async def test_last_payment_sensor(hass: HomeAssistant):
    data = build_data()
    data.accounts["a1"].last_payment = {
        "id": "900000001",
        "amount": 150.0,
        "date": "2026-08-15T10:11:32",
        "status": "SUCCESS",
    }
    await setup_sensors(hass, data)
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, "eirc_spb_1000000001_last_payment"
    )
    state = hass.states.get(entity_id)
    assert float(state.state) == 150.0
    assert state.attributes["payment_id"] == "900000001"
    assert state.attributes["date"] == "2026-08-15T10:11:32"
    assert state.attributes["status"] == "SUCCESS"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_coordinator.py -k payment_event tests/test_sensor.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

`notifications.py` — in `NotificationDetector.__init__` add `self._last_payments: dict[str, str | None] = {}`. New method:

```python
    def payment(self, account: Account) -> list[dict]:
        out: list[dict] = []
        last = self._last_payments.get(account.account_id, None)
        current = account.last_payment
        if (
            current is not None
            and current.get("id") != last
            and last is not None
        ):
            out.append(
                {
                    "type": "payment",
                    "account_id": account.account_id,
                    "number": account.number,
                    "payment_id": current.get("id"),
                    "amount": current.get("amount"),
                    "date": current.get("date"),
                    "status": current.get("status"),
                }
            )
        if current is not None:
            self._last_payments[account.account_id] = current.get("id")
        return out
```

`coordinator.py` — in the detector section of `_async_update_data`:

```python
                for n in self._detector.payment(account):
                    self._emit(n)
```

and in `_emit`'s event map:

```python
            "payment": f"{DOMAIN}_new_payment",
```

plus a persistent-notification branch mirroring `new_bill`:

```python
        elif n["type"] == "payment":
            notification_id = f"{DOMAIN}_payment_{n['account_id']}_{n['payment_id']}"
            title = "Новый платёж"
            message = (
                f"Лицевой счёт {n['number']}: платёж {n['payment_id']} "
                f"на {n['amount']} ₽ ({n['date']})"
            )
```

`sensor.py` — new sensor class (after `CurrentBillSensor`):

```python
class LastPaymentSensor(_AccountSensor):
    _key = "last_payment"
    _object_id = "last_payment"
    _attr_state_class = SensorStateClass.TOTAL
    _attr_name = "Последний платёж"

    @property
    def native_value(self) -> float | None:
        account = self.account
        if account is None or account.last_payment is None:
            return None
        return account.last_payment.get("amount")

    @property
    def extra_state_attributes(self) -> dict:
        account = self.account
        if account is None or account.last_payment is None:
            return {}
        attrs = {}
        if account.last_payment.get("id") is not None:
            attrs["payment_id"] = account.last_payment["id"]
        if account.last_payment.get("date") is not None:
            attrs["date"] = account.last_payment["date"]
        if account.last_payment.get("status") is not None:
            attrs["status"] = account.last_payment["status"]
        return attrs
```

Add `LastPaymentSensor(coordinator, account)` to `_build_entities`.

- [ ] **Step 4: Run tests + commit**

Run: `pytest tests/test_coordinator.py tests/test_sensor.py -v` — PASS.

```bash
git add custom_components/eirc_spb/notifications.py custom_components/eirc_spb/coordinator.py custom_components/eirc_spb/sensor.py tests/test_coordinator.py tests/test_sensor.py
git commit -m "feat: last payment sensor and new payment event"
```

### Task 14: Bill `history` attribute + release 2026.10.0

**Files:**
- Modify: `custom_components/eirc_spb/sensor.py`, `README.md`, `const.py`, `manifest.json`
- Test: `tests/test_sensor.py`

- [ ] **Step 1: Write the failing test**

```python
async def test_bill_sensor_history_attribute(hass: HomeAssistant):
    data = build_data()
    data.accounts["a1"].current_bill_id = "26071000000001"
    data.accounts["a1"].bills_history = [
        {
            "id": "26071000000001",
            "amount": 7633.65,
            "timestamp": "14.02.2026 00:00:00",
        }
    ]
    await setup_sensors(hass, data)
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, "eirc_spb_1000000001_bill"
    )
    state = hass.states.get(entity_id)
    assert state.attributes["history"][0]["id"] == "26071000000001"
    assert state.attributes["bill_id"] == "26071000000001"
    assert state.attributes["account_id"] == "a1"
```

- [ ] **Step 2: Run — FAIL** (`history`/`account_id` missing on bill sensor).

- [ ] **Step 3: Implement**

`CurrentBillSensor.extra_state_attributes` — add `ATTR_ACCOUNT_ID: self._account_id` to the base attrs dict, and after the existing `timestamp` block:

```python
        if account.bills_history:
            attrs["history"] = account.bills_history
```

- [ ] **Step 4: README + release**

README: document `…_last_payment` sensor, `eirc_spb_new_payment` event (add to the notifications table), `history` attribute on the bill sensor. Bump `2026.10.0`.

Run: `pytest -q` — PASS.

```bash
git add -A
git commit -m "feat: bills history attribute and release 2026.10.0"
```

---

## Phase 5 — ЕПД PDF download (release 2026.11.0, spike-gated)

### Task 15: Spike — trace the bill file flow (HUMAN-IN-THE-LOOP)

**Files:**
- Modify: `scripts/capture_fixtures.py` (throwaway tooling, not shipped)
- Create: `docs/parity.md` update (outcome)

**This task requires live credentials (`scripts/.env`) and possibly a 2FA code relayed by the user. Never dispatch a subagent.**

- [ ] **Step 1: Extend the capture script**

Add after the payments loop in `main()`:

```python
        bill_id = json.loads((RAW / "bills_current.json").read_text())["id"]
        for label, path in [
            ("bill_detail", f"v8/payments/bills/{bill_id}"),
            ("bill_file", f"v1/file/bill-{bill_id}"),
        ]:
            status, data, headers = await req(
                s, "GET", path, headers=auth_headers
            )
            print(label, status, dict(headers)[:200] if False else headers.get("Content-Type"))
            dump(label, data)
```

(Adapt: the goal is to discover how `canDownload: true` turns into a file — try `v8/payments/bills/{id}` first, inspect the `file` field; if null, probe `v1/file/{uuid}` variants and any POST-generate route from the SPA bundle: `v1/file`, `v1/file/sign`, `v6/accounts/{id}/documents/download`.)

- [ ] **Step 2: Run live with credentials**

Run: `python scripts/capture_fixtures.py` (interactive). Record which call returns PDF bytes (content-type `application/pdf`) and what precedes it (uuid from where, sign step?).

- [ ] **Step 3: Record the outcome**

Update `docs/parity.md` section 3 «Скачивание ЕПД (PDF)» with the confirmed flow (endpoint chain). If NO working flow is found: mark the feature 🚫 in parity doc, write `Spike outcome: no viable download flow` in the parity doc, and SKIP Tasks 16-17 — jump to Task 18 with the remaining scope (translations/README already done in P1-P4).

- [ ] **Step 4: Commit**

```bash
git add scripts/capture_fixtures.py docs/parity.md
git commit -m "chore: spike notes for bill pdf download flow"
```

### Task 16: `api.download_bill` (only if spike succeeded)

**Files:**
- Modify: `custom_components/eirc_spb/api.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: the concrete endpoint chain discovered in Task 15 — the signature below is stable regardless: 
- Produces: `async def download_bill(self, account_id: str, bill_id: str) -> bytes | None`

- [ ] **Step 1: Write the failing test** (adjust the mocked path chain to the spike outcome; sketch assumes `v8/payments/bills/{id}` → `file` uuid → `v1/file/{uuid}`)

```python
async def test_download_bill_returns_pdf_bytes(aresponses, client):
    aresponses.add(HOST, "/api/v8/users/auth", "POST", ok({"access": "a1", "auth": "t1"}))
    aresponses.add(
        HOST,
        "/api/v8/payments/bills/26071000000001",
        "GET",
        ok({"id": "26071000000001", "file": "file-uuid-1", "canDownload": True}),
    )
    aresponses.add(
        HOST,
        "/api/v1/file/file-uuid-1",
        "GET",
        web.Response(body=b"%PDF-1.4 fake", content_type="application/pdf"),
    )
    data = await client.download_bill("910000001", "26071000000001")
    assert data == b"%PDF-1.4 fake"
```

- [ ] **Step 2: Run — FAIL** (method missing).

- [ ] **Step 3: Implement**

In `api.py` — add a raw-GET helper that reuses the session + auth but skips JSON parsing (returns `bytes | None`), then:

```python
    async def download_bill(self, account_id: str, bill_id: str) -> bytes | None:
        bill = await self.get_bill(bill_id)
        file_id = bill.get("file")
        if not file_id:
            return None
        return await self._download_file(str(file_id))
```

`_download_file` mirrors `_send` but uses `resp.read()`; raise `EircSpbApiError` on status >= 400. If the spike flow differs (extra sign step / different route), implement exactly what the spike recorded — the public signature stays.

- [ ] **Step 4: Run tests + commit**

```bash
git add custom_components/eirc_spb/api.py tests/test_api.py
git commit -m "feat: api method to download bill pdf"
```

### Task 17: `eirc_spb.download_bill` service

**Files:**
- Modify: `custom_components/eirc_spb/services.py`, `services.yaml`, `translations/{ru,en}.json`, `custom_components/eirc_spb/__init__.py`
- Test: `tests/test_services.py`

**Interfaces:**
- Consumes: `client.download_bill(account_id, bill_id) -> bytes | None`; bill sensor exposes `account_id` + `bill_id` attributes (Task 14).
- Produces: service `eirc_spb.download_bill(entity_id, bill_id?, path?)` → response `{path, url, bytes}`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_services.py` (reuses `install_runtime`; add `from pathlib import Path` to imports):

```python
async def test_download_bill_service_saves_pdf(hass: HomeAssistant):
    from custom_components.eirc_spb.services import async_setup_services

    runtime = install_runtime(hass)
    runtime.client.download_bill = AsyncMock(return_value=b"%PDF-1.4 fake")
    await async_setup_services(hass)
    hass.states.async_set(
        "sensor.bill", "7633.65", {"account_id": "a1", "bill_id": "26071000000001"}
    )
    response = await hass.services.async_call(
        DOMAIN,
        "download_bill",
        {"entity_id": "sensor.bill"},
        blocking=True,
        return_response=True,
    )
    path = Path(response["path"])
    assert path.read_bytes() == b"%PDF-1.4 fake"
    assert response["url"].startswith("/local/eirc/")
    assert response["bytes"] == 14
    path.unlink()


async def test_download_bill_rejects_path_escape(hass: HomeAssistant):
    from custom_components.eirc_spb.services import async_setup_services

    install_runtime(hass)
    await async_setup_services(hass)
    hass.states.async_set(
        "sensor.bill", "7633.65", {"account_id": "a1", "bill_id": "26071000000001"}
    )
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN,
            "download_bill",
            {"entity_id": "sensor.bill", "path": "/tmp/evil.pdf"},
            blocking=True,
            return_response=True,
        )
```

- [ ] **Step 2: Run — FAIL** (service not registered).

- [ ] **Step 3: Implement**

`services.py`:

```python
import os

SERVICE_DOWNLOAD_BILL = "download_bill"

DOWNLOAD_BILL_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Optional("bill_id"): str,
        vol.Optional("path"): str,
    }
)


def _resolve_account_and_bill(
    hass: HomeAssistant, entity_id: str
) -> tuple["EircSpbRuntime", str, str]:
    state = hass.states.get(entity_id)
    if state is None:
        raise HomeAssistantError(f"Сущность не найдена: {entity_id}")
    account_id = state.attributes.get(ATTR_ACCOUNT_ID)
    bill_id = state.attributes.get("bill_id")
    if not account_id or not bill_id:
        raise HomeAssistantError(f"Сущность без счёта: {entity_id}")
    for runtime in hass.data.get(DOMAIN, {}).values():
        data = getattr(runtime.coordinator, "data", None)
        if data is not None and account_id in data.accounts:
            return runtime, account_id, str(bill_id)
    raise HomeAssistantError(f"Счёт недоступен: {entity_id}")
```

Handler:

```python
    async def handle_download_bill(call: ServiceCall) -> ServiceResponse:
        runtime, account_id, bill_id = _resolve_account_and_bill(
            hass, call.data[ATTR_ENTITY_ID]
        )
        bill_id = str(call.data.get("bill_id") or bill_id)
        account = runtime.coordinator.data.accounts[account_id]
        default_dir = hass.config.path("www", "eirc")
        target = call.data.get("path") or os.path.join(
            default_dir, f"{account.number}_{bill_id}.pdf"
        )
        target = os.path.abspath(target)
        config_dir = os.path.abspath(hass.config.config_dir)
        if not target.startswith(config_dir + os.sep):
            raise HomeAssistantError(
                "Путь должен находиться внутри каталога конфигурации Home Assistant"
            )
        data = await runtime.client.download_bill(account_id, bill_id)
        if data is None:
            raise HomeAssistantError(f"Файл счёта недоступен: {bill_id}")

        def _write() -> None:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as fh:
                fh.write(data)

        await hass.async_add_executor_job(_write)
        rel = os.path.relpath(target, hass.config.path("www"))
        return {
            "path": target,
            "url": f"/local/{rel}",
            "bytes": len(data),
        }
```

Register alongside the existing service (same `async_setup_services`), `supports_response=SupportsResponse.OPTIONAL`. In `__init__.py`, extend the unload cleanup to also remove `SERVICE_DOWNLOAD_BILL` (import it).

- [ ] **Step 4: services.yaml + translations**

```yaml
download_bill:
  name: Скачать счёт (ЕПД)
  description: >-
    Скачивает PDF текущего или указанного счёта в каталог www Home Assistant.
  fields:
    entity_id:
      name: Сущность
      description: Сенсор текущего счёта (…_current_bill).
      required: true
      example: sensor.eirc_spb_1000000001_current_bill
      selector:
        entity:
          integration: eirc_spb
          domain: sensor
    bill_id:
      name: Номер счёта
      description: По умолчанию — текущий счёт.
      example: "26071000000001"
    path:
      name: Путь файла
      description: Абсолютный путь внутри каталога конфигурации Home Assistant.
      example: /config/www/eirc/bill.pdf
```

Mirror in `translations/ru.json` / `translations/en.json`.

- [ ] **Step 5: Run tests + commit**

Run: `pytest -q` — PASS.

```bash
git add -A
git commit -m "feat: download_bill service for epd pdf files"
```

### Task 18: Final docs + release 2026.11.0

**Files:**
- Modify: `README.md`, `docs/parity.md`, `const.py`, `manifest.json`

- [ ] **Step 1: README** — «Уведомления» table gains `eirc_spb_new_payment`; «Отправка показаний» documents `confirm`; new «Скачивание счёта» section with the service example:

```yaml
action: eirc_spb.download_bill
data:
  entity_id: sensor.eirc_spb_1000000001_current_bill
```

- [ ] **Step 2: parity doc** — flip implemented rows (§5 лента — ✅, §4 дата поверки/паспорт — ✅, предпроверка — ✅, §3 история/последний платёж — ✅, PDF — ✅/🚫 per spike).

- [ ] **Step 3: bump + full suite**

`2026.11.0` in `const.py` + `manifest.json`. Run: `pytest -q` — PASS.

- [ ] **Step 4: Release commit**

```bash
git add -A
git commit -m "chore: release 2026.11.0"
```

---

## Self-Review Notes

- Spec coverage: P1 = Tasks 1-4 (bug + warn-once + diag attrs + CalVer), P2 = Tasks 5-8 (parser, api, cache, sensors), P3 = Tasks 9-10, P4 = Tasks 11-14, P5 = Tasks 15-18 (spike gate at 15, tasks 16-17 skippable on negative spike).
- Type consistency: `MeterPassport`/`AccountDetails` field names match between Task 5 (parser) and Tasks 7-8 (coordinator/sensors); `last_payment`/`bills_history` names match Tasks 12-14; `validate_reading` return shape matches Task 10 usage.
- Known follow-up for executors: live-probe the `validate` response shape (Task 9 conservative mapping may need adjustment after Task 15's live session).
