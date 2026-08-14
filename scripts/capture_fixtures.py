"""Capture ikus.pesc.ru API responses as test fixtures. Throwaway tool.

Usage:
  pip install aiohttp python-dotenv
  cp scripts/.env.example scripts/.env  # fill EIRC_LOGIN, EIRC_PASSWORD
  python scripts/capture_fixtures.py   # interactive: channel + OTP code

Requires tests/fixtures/raw/confirmation_verify.json with a valid `verified`
token for scripts/fetch_bill.py and ad-hoc probes (no OTP needed).
"""
import asyncio
import json
import os
import sys
from datetime import date, timedelta
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
        if status == 424 and isinstance(stage1, dict) and stage1.get("transactionId"):
            print("confirmation required; types:", stage1.get("types"))
        elif status != 200:
            sys.exit("stage-1 login failed — dump above; adjust body/headers")

        token = stage1.get("auth") or stage1.get("access") or stage1
        needs_conf = status == 424
        print("needs confirmation?", needs_conf)

        verification = None
        if needs_conf:
            session_id = stage1["transactionId"]
            for ch in stage1.get("types") or []:
                print("channel:", ch)
            ctype = input("choose channel (copy exactly, e.g. EMAIL): ").strip()
            status, sent, _ = await req(
                s, "POST",
                f"v7/users/{session_id}/{ctype.lower()}/check/confirmation/send",
                {}, base_headers(),
            )
            dump("confirmation_send", sent)
            if status >= 400:
                sys.exit("confirmation send failed — dump above")
            code = input("code from message/call: ").strip()
            status, verified, _ = await req(
                s, "POST",
                f"v7/users/{session_id}/{ctype.lower()}/check/verification",
                {"code": code}, base_headers(),
            )
            dump("confirmation_verify", verified)
            if status >= 400:
                sys.exit("verification failed — dump above")
            token = verified.get("auth") or verified.get("access") or token
            verification = verified.get("verified")

        auth_headers = {"Authorization": f"Bearer {token}", "User-Agent": "fixture-capture/1.0"}
        if verification:
            auth_headers["Auth-Verification"] = verification

        status, accounts, _ = await req(s, "GET", "v8/accounts", headers=auth_headers)
        dump("accounts", accounts)
        if status >= 400:
            sys.exit("accounts fetch failed — dump above")
        acct_list = accounts if isinstance(accounts, list) else accounts.get("accounts", [accounts])
        for a in acct_list:
            print("account:", a.get("id"), a.get("tenancy", {}).get("register"))
        acct_id = acct_list[0]["id"]
        register = acct_list[0].get("tenancy", {}).get("register", "")

        to_d = date.today()
        from_d = to_d - timedelta(days=120)

        for label, path in [
            ("account_address", f"v8/accounts/{acct_id}/address"),
            ("account_details", f"v7/accounts/{acct_id}/details"),
            ("meters_info", f"v6/accounts/{acct_id}/meters/info"),
            ("reading_period", f"v6/accounts/{acct_id}/reading/period"),
            ("bills_current", f"v8/accounts/{acct_id}/payments/bills/current"),
            (
                "bills_payments",
                f"v7/bills/payments?account={acct_id}&from={from_d.isoformat()}&to={to_d.isoformat()}",
            ),
            (
                "payments_list",
                f"v7/payments?account={acct_id}&from={from_d.isoformat()}&to={to_d.isoformat()}",
            ),
        ]:
            status, data, _ = await req(s, "GET", path, headers=auth_headers)
            dump(label, data)
            if status >= 400:
                print(f"WARNING: {label} failed")

        pids = json.loads((RAW / "payments_list.json").read_text())
        for pid in pids[:3]:
            status, data, _ = await req(
                s, "GET", f"v8/payments/{pid}", headers=auth_headers
            )
            dump(f"payment_{pid}", data)

        status, again, _ = await req(
            s, "POST", "v8/users/auth",
            {"login": LOGIN, "password": PASSWORD},
            base_headers(verification),
        )
        dump("login_with_token", again)
        print("repeat-login status (OTP skipped?)", status)


if __name__ == "__main__":
    asyncio.run(main())
