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
    auth: str
    verification_token: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class AuthResult:
    session: Session | None
    needs_confirmation: bool
    transaction_id: str | None = None
    channels: list[str] = field(default_factory=list)


def _message(data) -> str:
    if isinstance(data, dict) and data.get("message"):
        return str(data["message"])
    return "auth request failed"


def _code(data) -> str | None:
    if isinstance(data, dict) and data.get("code") is not None:
        return str(data["code"])
    return None


class Authenticator:
    def __init__(self, session: aiohttp.ClientSession | None) -> None:
        self._session = session

    @staticmethod
    def _headers(verification_token: str | None = None) -> dict:
        headers = {
            "Content-Type": "application/json",
            HEADER_CAPTCHA: HEADER_CAPTCHA_NONE,
            HEADER_WITH_TOTP: "true",
            "User-Agent": USER_AGENT,
        }
        if verification_token:
            headers[HEADER_AUTH_VERIFICATION] = verification_token
        return headers

    async def _request(
        self, method: str, path: str, body: dict, verification_token: str | None = None
    ) -> tuple[int, object]:
        assert self._session is not None
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
        async with self._session.request(
            method,
            f"{BASE_URL}/{path}",
            json=body,
            headers=self._headers(verification_token),
            timeout=timeout,
        ) as resp:
            try:
                data = await resp.json(content_type=None)
            except ValueError:
                data = None
            return resp.status, data

    async def login(
        self, login_id: str, password: str, verification_token: str | None = None
    ) -> AuthResult:
        status, data = await self._request(
            "POST",
            "v8/users/auth",
            {"login": login_id, "password": password},
            verification_token,
        )
        if status == 424 and isinstance(data, dict):
            return AuthResult(
                session=None,
                needs_confirmation=True,
                transaction_id=str(data.get("transactionId", "")),
                channels=[str(t) for t in data.get("types", [])],
            )
        if status == 200 and isinstance(data, dict):
            return AuthResult(
                session=Session(
                    access=str(data.get("access", "")),
                    auth=str(data.get("auth", "")),
                    verification_token=verification_token,
                    raw=data,
                ),
                needs_confirmation=False,
            )
        raise EircSpbAuthError(_message(data), _code(data))

    async def send_code(self, transaction_id: str, channel: str) -> None:
        status, data = await self._request(
            "POST",
            f"v7/users/{transaction_id}/{channel}/check/confirmation/send",
            {},
        )
        if status >= 400:
            raise EircSpbAuthError(_message(data), _code(data))

    async def verify_code(self, transaction_id: str, channel: str, code: str) -> Session:
        status, data = await self._request(
            "POST",
            f"v7/users/{transaction_id}/{channel}/check/verification",
            {"code": code},
        )
        if status >= 400:
            raise EircSpbConfirmationError(_message(data), _code(data))
        assert isinstance(data, dict)
        return Session(
            access=str(data.get("access", "")),
            auth=str(data.get("auth", "")),
            verification_token=data.get("verified"),
            raw=data,
        )
