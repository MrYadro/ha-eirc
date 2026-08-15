import json
import re
from datetime import date
from pathlib import Path

import pytest
import aiohttp
from aiohttp import web
from aresponses import ResponsesMockServer

from custom_components.eirc_spb.api import EircSpbApiClient
from custom_components.eirc_spb.exceptions import (
    EircSpbApiError,
    EircSpbAuthError,
    EircSpbConfirmationError,
)

FIX = Path(__file__).parent / "fixtures"
HOST = "ikus.pesc.ru"
TID = "11111111-2222-3333-4444-555555555555"

pytestmark = pytest.mark.enable_socket


def load(name):
    return json.loads((FIX / f"{name}.json").read_text())


def ok(data, status=200):
    return web.json_response(data, status=status)


@pytest.fixture
async def client(aresponses: ResponsesMockServer):
    c = EircSpbApiClient("login", "password")
    yield c
    await c.close()


async def test_login_success_no_confirmation(aresponses, client):
    aresponses.add(HOST, "/api/v8/users/auth", "POST", ok({"access": "a1", "auth": "t1"}))
    result = await client.authenticate()
    assert result.session is not None
    assert result.session.auth == "t1"
    assert result.session.verification_token is None
    assert not result.needs_confirmation
    assert result.transaction_id is None


async def test_login_needs_confirmation(aresponses, client):
    aresponses.add(
        HOST, "/api/v8/users/auth", "POST", ok(load("login_stage1"), status=424)
    )
    result = await client.authenticate()
    assert result.session is None
    assert result.needs_confirmation
    assert result.transaction_id == TID
    assert result.channels == ["EMAIL", "PHONE", "FLASHCALL", "TOTP"]


async def test_send_and_verify_code(aresponses, client):
    aresponses.add(
        HOST, "/api/v8/users/auth", "POST", ok(load("login_stage1"), status=424)
    )
    aresponses.add(
        HOST,
        f"/api/v7/users/{TID}/email/check/confirmation/send",
        "POST",
        web.Response(status=200, text=""),
    )
    aresponses.add(
        HOST,
        f"/api/v7/users/{TID}/email/check/verification",
        "POST",
        ok({"access": "a", "auth": "b", "verified": "v"}),
    )
    result = await client.authenticate()
    assert result.needs_confirmation
    await client.send_code(result.transaction_id, "email")
    session = await client.verify_code(result.transaction_id, "email", "12345")
    assert session.auth == "b"
    assert session.verification_token == "v"
    assert client.verification_token == "v"


async def test_verify_wrong_code_raises_confirmation_error(aresponses, client):
    aresponses.add(
        HOST,
        f"/api/v7/users/{TID}/email/check/verification",
        "POST",
        ok({"code": "500", "message": "Неправильный код"}, status=400),
    )
    with pytest.raises(EircSpbConfirmationError) as e:
        await client.verify_code(TID, "email", "0000")
    assert "код" in str(e.value)
    assert e.value.code == "500"


async def test_login_with_token_sends_auth_verification_header(aresponses):
    c = EircSpbApiClient("login", "password", verification_token="vtok")
    seen = {}

    async def login_handler(request):
        seen["auth_verification"] = request.headers.get("Auth-Verification")
        seen["captcha"] = request.headers.get("Captcha")
        seen["with_totp"] = request.headers.get("withTotp")
        return ok({"access": "a1", "auth": "t1"})

    aresponses.add(HOST, "/api/v8/users/auth", "POST", login_handler)
    aresponses.add(HOST, "/api/v8/accounts", "GET", ok(load("accounts")))
    accounts = await c.get_accounts()
    assert accounts[0].number == "1000000001"
    assert seen["auth_verification"] == "vtok"
    assert seen["captcha"] == "none"
    assert seen["with_totp"] == "true"
    await c.close()


async def test_bad_credentials_raise_auth_error(aresponses, client):
    aresponses.add(
        HOST,
        "/api/v8/users/auth",
        "POST",
        ok({"code": "5", "message": "Неверный логин или пароль"}, status=401),
    )
    with pytest.raises(EircSpbAuthError) as e:
        await client.authenticate()
    assert "Неверный логин" in str(e.value)
    assert e.value.code == "5"


async def test_get_accounts(aresponses, client):
    aresponses.add(HOST, "/api/v8/users/auth", "POST", ok({"access": "a1", "auth": "t1"}))
    aresponses.add(HOST, "/api/v8/accounts", "GET", ok(load("accounts")))
    accounts = await client.get_accounts()
    assert accounts[0].account_id == "910000001"
    assert accounts[0].number == "1000000001"


async def test_get_address_meters_and_current_bill(aresponses, client):
    aresponses.add(HOST, "/api/v8/users/auth", "POST", ok({"access": "a1", "auth": "t1"}))
    aresponses.add(
        HOST, "/api/v8/accounts/910000001/address", "GET", ok(load("account_address"))
    )
    aresponses.add(
        HOST, "/api/v6/accounts/910000001/meters/info", "GET", ok(load("meters_info"))
    )
    aresponses.add(
        HOST,
        "/api/v8/accounts/910000001/payments/bills/current",
        "GET",
        ok(load("bills_current")),
    )
    assert await client.get_address("910000001") == "ул. Тестовая, д. 1, кв. 1"
    meters = await client.get_meters("910000001")
    assert len(meters) == 5
    assert meters[0].meter_id == "100000"
    bill = await client.get_current_bill("910000001")
    assert bill["id"] == "26071000000001"
    assert bill["amount"] == 7633.65


async def test_401_triggers_relogin_and_retry(aresponses):
    c = EircSpbApiClient("login", "password", verification_token="vtok")
    aresponses.add(HOST, "/api/v8/users/auth", "POST", ok({"access": "a1", "auth": "t1"}))
    aresponses.add(
        HOST,
        "/api/v8/accounts",
        "GET",
        ok({"code": "5", "message": "unauthorized"}, status=401),
    )
    seen = {}

    async def relogin_handler(request):
        seen["auth_verification"] = request.headers.get("Auth-Verification")
        return ok({"access": "a2", "auth": "t2"})

    aresponses.add(HOST, "/api/v8/users/auth", "POST", relogin_handler)
    aresponses.add(HOST, "/api/v8/accounts", "GET", ok(load("accounts")))
    accounts = await c.get_accounts()
    assert accounts[0].number == "1000000001"
    assert seen["auth_verification"] == "vtok"
    await c.close()


async def test_401_without_token_raises_confirmation_required(aresponses, client):
    aresponses.add(HOST, "/api/v8/users/auth", "POST", ok({"access": "a1", "auth": "t1"}))
    aresponses.add(
        HOST,
        "/api/v8/accounts",
        "GET",
        ok({"code": "5", "message": "unauthorized"}, status=401),
    )
    aresponses.add(
        HOST, "/api/v8/users/auth", "POST", ok(load("login_stage1"), status=424)
    )
    with pytest.raises(EircSpbAuthError, match="confirmation"):
        await client.get_accounts()


async def test_get_finance(aresponses, client):
    aresponses.add(HOST, "/api/v8/users/auth", "POST", ok({"access": "a1", "auth": "t1"}))
    aresponses.add(
        HOST,
        "/api/v7/accounts/910000001/payments/at/current/amount/discretion",
        "GET",
        ok(load("payments_discretion")),
    )
    finance = await client.get_finance("910000001")
    assert finance.balance == pytest.approx(10458.16)
    assert finance.payments == []


async def test_get_payments_fetches_details(aresponses, client):
    aresponses.add(HOST, "/api/v8/users/auth", "POST", ok({"access": "a1", "auth": "t1"}))
    today = date.today()
    aresponses.add(HOST, "/api/v7/payments", "GET", ok(["p1", "p2"]))
    for pid in ("p1", "p2"):
        aresponses.add(
            HOST,
            f"/api/v8/payments/{pid}",
            "GET",
            ok(
                {
                    "id": pid,
                    "timestamp": "2026-02-28T10:07:34",
                    "details": [{"charge": {"accrued": 100.0}}],
                }
            ),
        )
    payments = await client.get_payments("910000001")
    assert len(payments) == 2
    assert payments[0].payment_id == "p1"
    assert payments[0].amount == 100.0


async def test_get_payments_caps_detail_calls_at_20(aresponses, client):
    aresponses.add(HOST, "/api/v8/users/auth", "POST", ok({"access": "a1", "auth": "t1"}))
    aresponses.add(
        HOST, "/api/v7/payments", "GET", ok([f"p{i}" for i in range(25)])
    )
    calls = []

    async def detail_handler(request):
        calls.append(request.path)
        return ok(
            {
                "id": "x",
                "timestamp": "2026-02-28T10:07:34",
                "details": [{"charge": {"accrued": 1.0}}],
            }
        )

    for _ in range(20):
        aresponses.add(
            HOST, re.compile(r"^/api/v8/payments/p\d+$"), "GET", detail_handler
        )
    payments = await client.get_payments("910000001")
    assert len(payments) == 20
    assert len(calls) == 20


async def test_submit_reading(aresponses, client):
    aresponses.add(HOST, "/api/v8/users/auth", "POST", ok({"access": "a1", "auth": "t1"}))
    seen = {}

    async def submit_handler(request):
        seen["body"] = await request.json()
        return ok({"code": "0", "message": "ok"})

    aresponses.add(
        HOST, "/api/v7/accounts/910000001/indications", "POST", submit_handler
    )
    result = await client.submit_reading(
        "910000001", "100000", [{"scale_id": "2", "value": 123}]
    )
    assert result["code"] == "0"
    assert seen["body"] == {
        "registration": "100000",
        "indications": [{"meterScaleId": "2", "value": 123}],
    }


async def test_data_request_sends_user_agent_with_injected_session(aresponses):
    injected = aiohttp.ClientSession()
    c = EircSpbApiClient("login", "password", session=injected)
    aresponses.add(HOST, "/api/v8/users/auth", "POST", ok({"access": "a1", "auth": "t1"}))
    seen = {}

    async def accounts_handler(request):
        seen["user_agent"] = request.headers.get("User-Agent")
        return ok(load("accounts"))

    aresponses.add(HOST, "/api/v8/accounts", "GET", accounts_handler)
    accounts = await c.get_accounts()
    assert accounts[0].number == "1000000001"
    assert seen["user_agent"] == "home-assistant-eirc-spb/1.0.0"
    await c.close()
    assert not injected.closed
    await injected.close()


async def test_api_error_on_500(aresponses, client):
    aresponses.add(HOST, "/api/v8/users/auth", "POST", ok({"access": "a1", "auth": "t1"}))
    aresponses.add(
        HOST,
        "/api/v8/accounts",
        "GET",
        ok({"code": "500", "message": "boom"}, status=500),
    )
    with pytest.raises(EircSpbApiError) as e:
        await client.get_accounts()
    assert "boom" in str(e.value)
    assert e.value.code == "500"


async def test_get_reading_period(aresponses, client):
    aresponses.add(HOST, "/api/v8/users/auth", "POST", ok({"access": "a1", "auth": "t1"}))
    aresponses.add(
        HOST,
        "/api/v6/accounts/910000001/reading/period",
        "GET",
        ok(load("reading_period")),
    )
    period = await client.get_reading_period("910000001")
    assert period["acceptanceParameters"]["deadLine"] == 11
    assert period["acceptanceParameters"]["name"] == "Август 2026"
