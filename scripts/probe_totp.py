"""Probe the TOTP verification path. Throwaway.

Usage:
  .venv/bin/python scripts/probe_totp.py
  (venv needs: aiohttp python-dotenv — pip install into /tmp/eirc-probe is fine too)

Steps: fresh login WITHOUT verified token -> 424 challenge ->
you enter the current 6-digit TOTP code -> tries candidate verify
endpoints and dumps responses. Nothing is sent for TOTP (no send step).
"""
import asyncio
import json
import os
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

BASE = "https://ikus.pesc.ru/api"
LOGIN = os.environ["EIRC_LOGIN"]
PASSWORD = os.environ["EIRC_PASSWORD"]


def headers(**extra):
    h = {
        "Content-Type": "application/json",
        "Captcha": "none",
        "withTotp": "true",
        "User-Agent": "probe/1.0",
    }
    h.update(extra)
    return h


async def main():
    async with aiohttp.ClientSession() as s:
        async with s.post(
            f"{BASE}/v8/users/auth",
            json={"login": LOGIN, "password": PASSWORD},
            headers=headers(),
        ) as r:
            stage1 = await r.json(content_type=None)
            print("stage1:", r.status, json.dumps(stage1, ensure_ascii=False)[:200])
        tid = stage1["transactionId"]
        print("types:", stage1.get("types"))

        code = input("current TOTP code (6 digits): ").strip()
        candidates = [
            ("v7 check/verification", "POST", f"v7/users/{tid}/totp/check/verification"),
            ("v7 generic verification", "POST", f"v7/users/{tid}/totp/check/verification".replace("/totp/", "/totp/")),
        ]
        for label, method, path in candidates:
            try:
                async with s.request(
                    method,
                    f"{BASE}/{path}",
                    json={"code": code},
                    headers=headers(),
                ) as r:
                    text = await r.text()
                    print(f"== {label} -> {r.status}: {text[:400]}")
                    if r.status == 200:
                        Path("tests/fixtures/raw/totp_verify_probe.json").write_text(
                            text
                        )
                        print("saved raw probe response")
                        return
            except Exception as e:
                print(f"== {label} -> EXC {e}")

        print("also trying bundle paths:")
        for path in [
            f"v7/users/{tid}/totp/check/verification",
            f"v1/dfa/{tid}/totp/verify",
        ]:
            async with s.post(
                f"{BASE}/{path}", json={"code": code}, headers=headers()
            ) as r:
                text = await r.text()
                print(f"== {path} -> {r.status}: {text[:400]}")
                if r.status == 200:
                    Path("tests/fixtures/raw/totp_verify_probe.json").write_text(text)
                    print("saved raw probe response")
                    return


if __name__ == "__main__":
    asyncio.run(main())
