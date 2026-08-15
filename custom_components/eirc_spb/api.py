import asyncio
from datetime import date, timedelta
from typing import Any

import aiohttp

from .auth import Authenticator, AuthResult, Session
from .const import BASE_URL, REQUEST_TIMEOUT_SECONDS, USER_AGENT
from .exceptions import EircSpbApiError, EircSpbAuthError
from .models import (
    Account,
    BillsPayments,
    Meter,
    parse_accounts,
    parse_finance,
    parse_meters,
)

MAX_PAYMENT_DETAILS = 20


def _message(data: Any) -> str:
    if isinstance(data, dict) and data.get("message"):
        return str(data["message"])
    return "API request failed"


def _code(data: Any) -> str | None:
    if isinstance(data, dict) and data.get("code") is not None:
        return str(data["code"])
    return None


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
        self._state: Session | None = None
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        if self._own_session:
            await self._session.close()

    @property
    def verification_token(self) -> str | None:
        return self._verification_token

    async def authenticate(self) -> AuthResult:
        async with self._lock:
            result = await self._auth.login(
                self._login_id, self._password, self._verification_token
            )
            if result.session is not None:
                self._state = result.session
            return result

    async def send_code(self, transaction_id: str, channel: str) -> None:
        await self._auth.send_code(transaction_id, channel)

    async def verify_code(self, transaction_id: str, channel: str, code: str) -> Session:
        session = await self._auth.verify_code(transaction_id, channel, code)
        async with self._lock:
            self._state = session
            self._verification_token = session.verification_token
        return session

    async def _login(self) -> None:
        result = await self._auth.login(
            self._login_id, self._password, self._verification_token
        )
        if result.session is None:
            raise EircSpbAuthError("confirmation required")
        self._state = result.session

    async def _send(self, method: str, path: str, body: Any) -> tuple[int, Any]:
        assert self._state is not None
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
        async with self._session.request(
            method,
            f"{BASE_URL}/{path}",
            json=body,
            headers={
                "Authorization": f"Bearer {self._state.auth}",
                "User-Agent": USER_AGENT,
            },
            timeout=timeout,
        ) as resp:
            try:
                data = await resp.json(content_type=None)
            except ValueError:
                data = None
            return resp.status, data

    async def _request(self, method: str, path: str, body: Any = None) -> Any:
        async with self._lock:
            if self._state is None:
                await self._login()
            status, data = await self._send(method, path, body)
            if status == 401:
                await self._login()
                status, data = await self._send(method, path, body)
            if status == 401:
                raise EircSpbAuthError(_message(data), _code(data))
            if status >= 400:
                raise EircSpbApiError(_message(data), _code(data))
            return data

    async def get_accounts(self) -> list[Account]:
        data = await self._request("GET", "v8/accounts")
        return parse_accounts(data if isinstance(data, list) else [])

    async def get_address(self, account_id: str) -> str:
        data = await self._request("GET", f"v8/accounts/{account_id}/address")
        return str(data["value"]) if isinstance(data, dict) else ""

    async def get_meters(self, account_id: str) -> list[Meter]:
        data = await self._request("GET", f"v6/accounts/{account_id}/meters/info")
        return parse_meters(data if isinstance(data, list) else [], account_id)

    async def get_finance(self, account_id: str) -> BillsPayments:
        data = await self._request(
            "GET", f"v7/accounts/{account_id}/payments/at/current/amount/discretion"
        )
        return parse_finance(data if isinstance(data, list) else [])

    async def get_current_bill(self, account_id: str) -> dict:
        data = await self._request(
            "GET", f"v8/accounts/{account_id}/payments/bills/current"
        )
        return data if isinstance(data, dict) else {}

    async def get_reading_period(self, account_id: str) -> dict:
        data = await self._request(
            "GET", f"v6/accounts/{account_id}/reading/period"
        )
        return data if isinstance(data, dict) else {}

    async def submit_reading(
        self, account_id: str, registration: str, readings: list[dict]
    ) -> dict:
        data = await self._request(
            "POST",
            f"v7/accounts/{account_id}/indications",
            {
                "registration": registration,
                "indications": [
                    {"meterScaleId": r["scale_id"], "value": r["value"]}
                    for r in readings
                ],
            },
        )
        return data if isinstance(data, dict) else {}
