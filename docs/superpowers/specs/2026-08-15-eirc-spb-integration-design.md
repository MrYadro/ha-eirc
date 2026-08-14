# EIRC SPb Home Assistant Integration — Design

Date: 2026-08-15
Status: approved in brainstorming, pending implementation plan

## 1. Overview

A Home Assistant custom integration (HACS-ready) for АО «ЕИРЦ СПб» (eirc.spb.ru).
It reads data from the ЕИРЦ/ПСК personal account at `https://ikus.pesc.ru/` and
exposes it as sensors for statistics, plus a service to submit meter readings.

**Goals**

- Sensors for: account balance, accruals (начисления) with per-service breakdown,
  payments, and meter readings (water, electricity incl. multi-tariff, ТКО).
- Service `eirc_spb.send_meter_reading` to submit readings from HA.
- HACS-installable repository, tests, documented in README (Russian-first, like
  the target audience).

**Non-goals**

- Card payments / card attach, feedback/appeals, notifications, marketplace,
  travel passes — anything the cabinet supports beyond readings/bills/payments.
- Supporting lk.kvartplata.info (ВЦКП ЖХ) — ikus.pesc.ru only.
- Submitting readings automatically (user builds automations on top of the service).

**Naming**

- Integration domain / unique-id prefix: `eirc_spb` (avoids collision with the
  unrelated `StanislavBolshakov/ha-eirc` integration, domain `eirc`).

## 2. Source system research findings

The personal account is an Angular SPA backed by an undocumented REST API:

- Base URL: `https://ikus.pesc.ru/api/` (from SPA `config.json` → `basePath`).
- Backend is Java (`ru.sigma.ikus.*` in error traces). Errors come back as
  JSON: `{"code", "message", "cause", "data", "httpStatus"}`.
- Auth: `POST v8/users/auth` with login (phone) + password; login request may
  carry a captcha header, the SPA sends a `none` sentinel value by default
  (captcha appears to be enforced only after failed attempts — to be validated).
  Session refresh: `PUT v6/users/auth` with the token object; response merges
  refreshed fields back.
- Relevant endpoints observed in the SPA bundle:
  - `v8/accounts`, `v8/accounts/light`, `v8/accounts/providers` — лицевые счета
  - `v6/users/current` — profile
  - `v1/mes/csp/connection-objects` — metering devices (счётчики) per account
  - `v1/mes/csp/indications` — current/latest indications
  - `v1/mes/csp/indications/upload/history` — submission history
  - `v1/mes/csp/connection-objects/indications/save` / `.../send` — submit readings
  - `v7/bills/payments`, `v7/payments` — bills and payments
  - `v1/file` — binary files (PDF bills)
- Exact response schemas are NOT documented; implementation starts by capturing
  real responses from the user's account (see §9 Testing / fixtures).
  `StanislavBolshakov/ha-eirc` (license to be checked before borrowing any code)
  and `dentra/ha-pesc` (MIT) are behavioral references only.

## 3. Architecture

Three layers, HA dependencies only in the top layer:

```
custom_components/eirc_spb/
├── __init__.py        # setup/unload, coordinator creation, services registration
├── api.py             # EircSpbApiClient — pure aiohttp client, no HA imports
├── auth.py            # SessionManager — owns token state: login(), refresh(),
│                      #   ensure_valid(); api.py's _request() delegates to it on 401
├── coordinator.py     # DataUpdateCoordinator[EircSpbData]
├── config_flow.py     # phone+password → account discovery → multi-select
├── options_flow.py    # update interval, password change
├── sensor.py          # SensorEntity classes, one device per лицевой счёт
├── services.py        # eirc_spb.send_meter_reading
├── const.py           # domain, defaults, headers
├── exceptions.py      # EircSpbAuthError, EircSpbApiError
├── models.py          # typed dataclasses: Account, Meter, Reading, Bill, Payment
└── translations/ru-RU.json, en.json + manifest.json
```

**api.py** (standalone, unit-testable):

- `async def login(phone, password) -> Session`
- `async def refresh(session) -> Session`
- `async def get_accounts() -> list[Account]`
- `async def get_meters(account_id) -> list[Meter]` (connection objects + indications)
- `async def get_bills_payments(account_id) -> BillsPayments`
- `async def submit_reading(connection_id, readings: list[ReadingSubmit]) -> SubmitResult`
- Single shared `aiohttp.ClientSession` with base `https://ikus.pesc.ru/api/`,
  JSON in/out, explicit User-Agent, timeout ~30s.
- A wrapper `_request()` centralizes: auth header injection, 401 → one refresh
  or re-login attempt then retry once, error-body normalization to exceptions.

**coordinator.py**: polls all selected accounts via api.py, assembles
`EircSpbData` (accounts, meters, latest readings, balance, last accruals,
payments). Default scan interval 12h, configurable in options flow (min 1h to
be a polite API citizen).

## 4. Data model & sensors

Devices: one HA **device per лицевой счёт** (name = "ЕИРЦ <account number>",
identifiers from account id). All sensors attach to their account's device.

Per account:

| Sensor | Entity example | Class / unit | Notes |
|---|---|---|---|
| Balance | `sensor.eirc_spb_<acct>_balance` | monetary, RUB | positive = переплата if API reports it that way — confirm on fixtures |
| Last accruals total | `sensor.eirc_spb_<acct>_accruals` | monetary, RUB | state = total for last billed period; attributes: period, per-service breakdown |
| Payments total | `sensor.eirc_spb_<acct>_payments` | monetary RUB, `total_increasing` | cumulative sum so HA long-term statistics work; attributes: last N payments (date, amount, id) |

Per meter/tariff (device = the account; each meter gets one sensor, multi-tariff
electricity meters get one sensor per tariff with scale_id in attributes):

| Sensor | Entity example | Class / unit |
|---|---|---|
| Cold/hot water | `sensor.eirc_spb_<acct>_<meter>_cold` | water, m³, `total_increasing` |
| Electricity T1/T2/… | `sensor.eirc_spb_<acct>_<meter>_t1` | energy, kWh, `total_increasing` |
| ТКО / others | `sensor.eirc_spb_<acct>_<meter>` | none, unit per API | state = last submitted reading |

Sensor attributes carry raw API fields needed by the service: `account_id`,
`meter_id` (connection object id), `scale_id` (tariff id), `last_submit_date`,
`meter_serial`, `verification_date` (поверка) if the API returns it.

State formatting: readings keep full precision from API; monetary sensors round
to 2 decimals. `state_class` set as above so the built-in Statistics /
Energy dashboard work without extra config.

## 5. Config & options flows

**Config flow** (`user` step): phone (`+7XXXXXXXXXX`, validated) + password.
On submit: login → `get_accounts()` → if exactly one account, select it
automatically; otherwise show a multi-select checkbox step (account number +
address as labels). Store: phone, password, selected account ids. Title:
"ЕИРЦ СПб" (+ address when single-account).

**Reauth flow**: on hard auth failure (bad credentials), show the standard HA
reauth form (password update), then reload the entry.

**Options flow**: scan interval (slider 1–24h, default 12h), password change.
Adding/removing accounts after setup is handled by re-running setup
(one config entry per credentials is fine; multi-account lives inside one entry).

## 6. Service: submit readings

```yaml
action: eirc_spb.send_meter_reading
data:
  entity_id: sensor.eirc_spb_1234567890_kitchen_cold   # any meter sensor
  readings:
    - scale_id: 2      # from sensor attributes
      value: 12345
```

- Targets meter sensors (CV to `scale_id` attribute present).
- Calls `submit_reading`; on success refreshes the coordinator and returns
  `{code, message}` in the service response for automations.
- On API rejection (e.g., value less than previous) raises
  `HomeAssistantError` with the API's Russian message surfaced.

## 7. Auth & error handling

- Credentials stored in the config entry (HA-encrypted at rest via config entry
  storage; note in README not to share config backups).
- Every request: on 401 → refresh token once (`PUT v6/users/auth`); on refresh
  failure → full re-login with stored credentials; if that also fails with
  invalid-credentials → trigger reauth flow and set `ConfigEntryAuthError`.
- Network/API errors → `DataUpdateCoordinator` retry with backoff; sensors keep
  last state (coordinator default), integration logs at debug.
- Rate limiting: min scan interval 1h; login attempts are not retried in a loop.

## 8. Repository layout (HACS)

```
/ (repo root = this repo, ha-eirc)
├── custom_components/eirc_spb/   (all files from §3 + manifest.json)
│   └── manifest.json             domain eirc_spb, requirements: [] (aiohttp ships with HA)
├── tests/                        pytest + pytest-homeassistant-custom-component
│   ├── fixtures/                 recorded real API JSON responses (sanitized)
│   ├── test_api.py
│   ├── test_config_flow.py
│   ├── test_sensor.py
│   └── test_services.py
├── hacs.json                     name eirc_spb, integration_type: hub
├── README.md                     RU: install, config, sensors, service, debug
├── LICENSE                       MIT
└── .github/workflows/ci.yml      hassfest + HACS validation + tests
```

Version pinning: `pytest-homeassistant-custom-component` matched to the HA
version targeted by `manifest.json` (`core` requirement, e.g. `"2025.12.0"` —
pin during implementation planning to the then-current stable HA).

## 9. Testing strategy

**Fixtures first**: before writing api.py logic, run a capture script
(`scripts/capture_fixtures.py`, throwaway, kept out of the shipped component)
where the user logs in with real credentials and saves sanitized JSON of:
login response, `v8/accounts`, connection objects, indications, upload history,
`v7/bills/payments` for their accounts. These become `tests/fixtures/*.json`.

- `test_api.py`: `aiohttp_client` mock (aresponses or HA's `MockConfigEntry` +
  `aiohttp.test_utils`) replaying fixtures → typed models; auth lifecycle:
  401 → refresh → retry, refresh-dead → re-login, bad creds → auth error.
- `test_config_flow.py`: full flow incl. multi-account selection, reauth.
- `test_sensor.py`: entity creation per fixture data, device_class/state_class,
  attributes (scale_id etc.).
- `test_services.py`: happy path, API rejection, unknown entity.
- No real network calls in CI; fixtures sanitize phone/account numbers.

**Manual validation checklist** (with user's HA instance): real login, sensor
states match the web cabinet, reading submission updates the cabinet, PDF-bill
URLs are NOT in scope v1 (skip).

## 10. Implementation-time validation steps

Things the fixtures capture must confirm (each has a fallback):

1. Captcha header on fresh login — expect `none` sentinel works; fallback: fail
   with clear message telling user to log in via web once (unblocks captcha).
2. Balance sign convention — fallback: expose raw value, document convention.
3. Accruals source: whether per-service breakdown comes from `v7/bills/payments`
   or a sibling endpoint found in fixtures; fallback: totals only, breakdown in
   attributes when available.
4. Payment history depth the endpoint returns — use whatever is returned,
   no paging in v1.

Out of scope for v1 (explicitly): PDF bill download, push notifications,
multi-credential support (one phone = one config entry).
