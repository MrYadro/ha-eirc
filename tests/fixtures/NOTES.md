# ikus.pesc.ru API — captured facts (2026-08-15)

Verified against production with a real account. Fixtures in this directory
are sanitized captures: phones/emails/account/serials/names/address faked;
ALL personal values randomized — amounts and readings jittered (×1.37),
dates shifted (−137 days), service names → "Услуга N", provider companies →
ООО "Тест N", display values → "Тест", JWTs → "fake.jwt.token", masked
emails → "use********@example.com", transactionId → fake UUID.
**Field names and structure are the API contract; values are illustrative
fakes and must not be treated as expected real-world outputs.**

## Auth

- `POST v8/users/auth` body `{"login": "<phone or email>", "password": "..."}`
  headers: `Captcha: none`, `withTotp: true`, `Content-Type: application/json`,
  optional `Auth-Verification: <verified token>`.
- Fresh login WITHOUT verification token → **HTTP 424** body
  `{"transactionId": "<uuid>", "types": ["EMAIL","PHONE","FLASHCALL","TOTP"]}`.
  This is the "confirmation required" signal (NOT a 200 + type field).
- Channel send: `POST v7/users/{transactionId}/{email|phone|flashcall}/check/confirmation/send`
  body `{}` → 200 empty. (Channel = lowercase of the 424 `types` entry; TOTP
  exists at `v1/dfa/{id}/totp/verify` — unsupported by this integration.)
- Verify: `POST v7/users/{transactionId}/{channel}/check/verification`
  body `{"code": "<digits>"}` → 200
  `{"access": jwt, "auth": jwt, "verified": jwt}`. Code was 5 digits (email).
- Login WITH `Auth-Verification: <verified>` → **200** directly
  `{"access", "auth"}` — OTP skipped. `verified` token observed valid ≥1 year
  (exp claim ~365d). REPEAT LOGIN CONFIRMED WORKING.
- Data requests use `Authorization: Bearer {auth}` — the **`auth`** JWT, NOT
  `access` (`access` has userId null; 401 if used for data endpoints).
- `access` is presumably the refresh-token object for `PUT v6/users/auth`
  (not yet exercised; client falls back to full re-login on 401).

## Accounts

- `GET v8/accounts` → list; item: `{id, tenancy: {register, name}, role,
  fullName, alias, delivery, service: {providerId, providerCode}, ...}`.
  Account number for display = `tenancy.register`. No balance here.
- `GET v8/accounts/{id}/address` → `{"value": "Санкт-Петербург, ...", "identifier": true}`.

## Meters

- `GET v6/accounts/{id}/meters/info` → list of meters:
  `{id: {provider, registration}, name: "Горячее водоснабжение (ПУ №100000)",
   numberOfDigitsLeft/Right, serial, status: "ACTIVE",
   indications: [{previousReadingDate, meterScaleId, scaleName,
                  previousReading, registerReading (null until submitted),
                  unit: "куб.м."}],
   subservice: {id, name, utility: "WATER"|"ELECTRICITY"|..., type}, subserviceId}`.
- Electricity meter has multiple `indications` entries (one per tariff,
  distinct `meterScaleId`).
- `v6/accounts/{id}/reading/period` → `{acceptanceParameters: {name:
  "Август 2026", interval: {dateFrom, dateTo}, deadLine: 11}, forbidden: false}`.
- Reading submission endpoints from the SPA bundle (NOT exercised):
  `POST v7/accounts/{id}/indications` (body = indications payload —
  needs live validation when the service is first used).

## Balance / accruals

- `GET v7/accounts/{id}/payments/at/current/amount/discretion` → list per
  subservice: `{subserviceId, subservice: {name, utility}, providerServiceName,
   fine: {balance: {value, type}, accrued}, charge: {balance: {value, type},
   accrued}, checked}`.
  - balance sensor = Σ `charge.balance.value`
  - accruals breakdown = {`subservice.name`: `charge.accrued`}
  - пеня available per service in `fine` (unused v1).

## Bills / payments

- `GET v8/accounts/{id}/payments/bills/current` → `{id, accountId, amount,
  timestamp "01.07.2026 00:00:00", file, canDownload}`.
- `GET v7/bills/payments?account={id}&from=YYYY-MM-DD&to=YYYY-MM-DD` → list
  of bill-id strings (latest first). `GET v8/payments/bills/{id}` → details
  (id, amount, timestamp, canDownload).
- `GET v7/payments?account={id}&from&to` → list of payment-id strings
  (mixed int-like and alphanumeric like "48PS9ZXAVGU8").
- `GET v8/payments/{id}` → `{accountId, status: "SUCCESS", details:
  [{subserviceId, subservice: {name}, charge: {balance, accrued}}...],
  timestamp "2026-07-15T10:07:34", id, file, receiptUrl}`.
  Payment amount = Σ `details[].charge.accrued`.
- History depth: `from`/`to` required params; 120 days probed, 21 payments.

## Dead ends (do not use)

- `v1/mes/csp/*` paths — UAT-only, 404 in production.
- `v6/users/current/accounts/{id}/energy-objects` — 404.
- `v6_1/products`, `v6/accounts/{id}/contacts` — 404.

## Plan deviations

- Plan's fixture names `connection_objects`, `indications`, `upload_history`
  replaced by `meters_info`, `reading_period`, `payments_discretion`,
  `payments_list`, `payment_*`, `bills_current`, `account_address`,
  `account_details`.
- Auth flow: confirmation signaled by 424+transactionId (plan assumed 200 +
  `type`/`channels`); channels are `["EMAIL","PHONE","FLASHCALL","TOTP"]`
  (plan assumed sms/email/call lowercase objects).
- Session has THREE tokens: `access` (refresh), `auth` (bearer), `verified`
  (OTP skip). Plan assumed single `access` + optional verification token.
