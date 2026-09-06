# Паритет: сайт ЛКК ikus.pesc.ru ↔ интеграция eirc_spb

Отчёт собран 06.09.2026 по живому аккаунту (ЕЛС 1000000001, provider EIRCPES):
исследованы JS-бандл фронтенда и ре ответы API. Статусы:

- ✅ — реализовано в интеграции
- ⚠️ — реализовано, но есть проблема
- ❌ — на сайте есть, в интеграции нет
- 🚫 — недоступно на сервере для тестового аккаунта (роль) — конкурирущая функциональность

> Примечание: флаги `role.operations` в ответе `v8/accounts` скрывают часть разделов
> сайта (для тестового аккаунта `documentsHistory`/`indicationsHistory`/`paymentsHistory` = false),
> при этом сами endpoint'ы отвечают — флаги управляют только UI.

## 1. Аутентификация и профиль

| Возможность сайта | API | Статус | Комментарий |
|---|---|---|---|
| Вход по логину/паролю | `POST v8/users/auth` | ✅ | |
| Подтверждение входа (E-mail / SMS / звонок) | `v7/users/{tx}/{ch}/check/...` | ✅ | |
| Подтверждение входа (TOTP) | `POST v1/dfa/{tx}/totp/verify` | ✅ | |
| Повторный вход без подтверждения (verified-токен) | `POST v8/users/auth` + `Auth-Verification` | ✅ | |
| Обновление сессии (device info) | `PATCH v6/users/current/session` | ✅ | |
| Re-login при 401 | — | ✅ | |
| Профиль (имя, телефон, e-mail, кастомные поля) | `GET v6/users/current` | ❌ | Например, сенсор e-mail/телефона не нужен, но пригодится для диагностики |
| Список активных сессий | `GET v6/users/sessions` | ❌ | Безопасность: видно посторонние устройства |
| Смена пароля / удаление профиля | `v6/users/{id}/password`, `v6/users/current/delete/reasons` | ❌ | Осознанно вне скоупа HA |
| Управление TOTP ключом | `v1/dfa/profile/totp/{create,remove}` | ❌ | Вне скоупа HA |
| Вход через ЕСИА | `v6/users/external/{sys}/auth` | ❌ | Вне скоупа HA |

## 2. Лицевые счета

| Возможность сайта | API | Статус | Комментарий |
|---|---|---|---|
| Список лицевых счетов | `GET v8/accounts` | ✅ | Выбор счетов в config flow |
| Адрес лицевого счёта | `GET v8/accounts/{id}/address` | ✅ | Имя устройства |
| Детали помещения: площадь, комнаты, владелец, УК, проживающие | `GET v7/accounts/{id}/details` | ❌ | Единый запрос; см. раздел 4 |
| Псевдоним счёта (alias) | `PUT v6/accounts/{id}/alias` | ❌ | В аккаунте уже задан («Тест»), не отображается |
| Способ получения счёта (бумага/e-mail) | `v8/accounts/{id}/delivery-changing`, `v6/accounts/{id}/bills/delivery` | ❌ | `delivery: "PAPER"` доступно в `v8/accounts` |
| Группы счетов | `v6/accounts/groups` | ❌ | Малоценно для HA |
| Совместный доступ (роли) | `v6/accounts/{id}/roles/users` | 🚫 | `roleManagement: false` |
| Добавление счёта (валидация по поставщику) | `v6/accounts/providers/{p}/validate` | ❌ | Счета добавляются на сайте; в HA — только config flow |

## 3. Начисления и счета

| Возможность сайта | API | Статус | Комментарий |
|---|---|---|---|
| Начисления за период по услугам | `GET v7/accounts/{id}/payments/at/current/amount/discretion` | ✅ | Сенсор `…_accruals` + разбивка |
| Начисления по поставщикам | там же | ✅ | Сенсоры `…_accruals_<поставщик>` |
| Пеня | там же | ✅ | Сенсор `…_fines` |
| Текущий счёт (ЕПД): сумма, id, дата | `GET v8/accounts/{id}/payments/bills/current` | ✅ | Сенсор `…_bill` |
| История счетов (по месяцам) | `GET v7/bills/payments?account=&from=&to=` → `v8/payments/bills/{id}` | ❌ | Ответ: id, сумма, дата, `canDownload` |
| Скачивание ЕПД (PDF) | `v8/payments/bills/{id}` + file | ❌ | `canDownload: true`; пригодится для `camera`/`notify` с вложением |
| История платежей (список id) | `GET v7/payments?account=&from=&to=` | ❌ | |
| Детализация платежа + чек | `GET v8/payments/{id}` | ❌ | Полная разбивка по услугам, `receiptUrl` |
| Калькулятор доплаты/переплаты на дату | `v6/accounts/{id}/payments/at/{date}/amount/{sum}` | ❌ | Малоценно для HA |
| Неподтверждённые платежи | `v6/accounts/{id}/payments/unconfirmed` | 🚫 | Вне скоупа HA |
| Оплата картой, автоплатёж, сохранённые карты | `v6/payments/*`, `v7/order` | ❌ | Осознанно вне скоупа HA (опасно) |
| Подписка на счета по e-mail | `v7/users/{id}/subscriptions` | 🚫 | `billSubscriptionsManagement: false` |
| Тарифы (капремонт, ТКО, содержание и т.д.) | `GET v7/accounts/{id}/details` | ❌ | Той же ручкой `details` |

## 4. Счётчики и показания

| Возможность сайта | API | Статус | Комментарий |
|---|---|---|---|
| Список счётчиков + последние показания | `GET v6/accounts/{id}/meters/info` | ✅ | Сенсоры по каждому тарифу, `total_increasing` |
| Окно и дедлайн передачи показаний | `GET v6/accounts/{id}/reading/period` | ✅ | Сенсор `…_reading_deadline` |
| Отправка показаний | `POST v8/accounts/{id}/meters/{reg}/reading` | ✅ | Сервис `send_meter_reading`; сайт шлёт через `v7/accounts/{id}/reading/{type}` — оба работают |
| Предпроверка показаний (расчёт расхода + валидация) | `GET/POST v7/accounts/{id}/meters/{reg}/scales/{s}/reading/{value}/{consumption,validate}` | ❌ | Улучшит UX сервиса: понятная ошибка до отправки |
| Дата поверки счётчика | `GET v7/accounts/{id}/details` (`METER_CHECK_DATE`) | ❌ | **README утверждает, что API её не отдаёт — отдаёт**, в `details` (напр. 05.08.2037) |
| Паспорт счётчика: модель, разрядность, дата установки, МПИ | `GET v7/accounts/{id}/details` | ❌ | Можно добавить в атрибуты сенсоров |
| История показаний (график расхода) | `v6/accounts/{id}/meters/{reg}/indications/history/{from}/{to}` | 🚫 | Роут есть в бандле, сервер отвечает 500 (роль: `indicationsHistory: false`) |
| Excel-отчёты по показаниям | `v6/users/current/readings/reports/send`, шаблоны | ❌ | Малоценно для HA |
| Акты снятия показаний | `v6/account/{id}/reading/.../acts` | 🚫 | |
| «Умные» счётчики (ИСУ / энергобъекты) | `v1/mes/csp/*`, `v6/.../energy-objects` | 🚫 | `iku: false`; при наличии ИСУ сайт сам скрывает ввод показаний |

## 5. Уведомления

| Возможность сайта | API | Статус | Комментарий |
|---|---|---|---|
| Лента «колокольчик» (непрочитанные) | `GET v6/notifications?type=bell&state=unread` | ⚠️ | Интеграция шлёт `?state=unread&limit=20` **без** `type` — сервер отвечает 500 (`type` обязателен). Ошибка глотается в `coordinator.py:93-96`, нативные уведомления молча не работают |
| Лента (прочитанные, история) | `GET v6/notifications?type=bell&state=read` | ❌ | |
| Счётчик непрочитанных | `GET v6/notifications/count?type=bell&state=unread` | ❌ | |
| Отметить прочитанным / переход по ссылке | `PUT v6/notifications/{id}/confirm`, `PUT v6/users/current/notifications/{id}/link` | ❌ | Опасно: подтвердить за пользователем |
| Прочие типы (TOP/MODAL/ONBOARDING) | `type=top` и др. | ❌ | Малоценно для HA |

## 6. Поддержка и прочее

| Возможность сайта | API | Статус | Комментарий |
|---|---|---|---|
| Обращения (appeals) | `v6/appeals`, черновики | ❌ | Вне скоупа HA |
| FAQ | `GET v6/help/faq` | ❌ | Малоценно |
| Витрина/партнёры (страхование и пр.) | `v6/partners`, `v8/marketplace/*` | ❌ | Реклама, вне скоупа |
| Мультипровайдерность (ПСК, ЛО, Газпром, Ростелеком, ФКР) | `GET v8/accounts/providers` | ❌ | Интеграция — только ЕИРЦ СПб (EIRCPES); см. «Ограничения» README |
| События аккаунта | `GET v6/users/current/events` | ❌ | Пусто для тестового аккаунта |

## Итог

**Реализовано (ядро):** вход с 2FA, счета, адрес, начисления/пеня/ЕПД, счётчики, дедлайн показаний, отправка показаний, события new_bill / reading_deadline.

**Кандидаты на реализацию (по ценности):**

1. **Fix: `type=bell` в запросе уведомлений** — иначе нативная лента не работает (bug).
2. `v7/accounts/{id}/details` — дата поверки (закрыть обещанный `verification_date`), паспорт ПУ, данные квартиры, тарифы.
3. История счетов/платежей (`v7/bills/payments`, `v7/payments` + `v8/payments/{id}`) — сенсоры или `extra_state_attributes`.
4. Предпроверка показаний (`validate`/`consumption`) перед отправкой в сервисе.
5. Скачивание ЕПД PDF (`v8/payments/bills/{id}`).
6. `autoPaymentOn` / `delivery` из `v8/accounts` — дешёвые диагностические атрибуты.
