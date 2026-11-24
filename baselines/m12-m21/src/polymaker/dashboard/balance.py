"""One authenticated, GET-only cash observation per live M1 session.

Authentication helpers only: never construct a trading client, create a key,
update allowances, acquire the account lock or write into trading state.
"""

from __future__ import annotations

import json
import os
import re
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import httpx
from dotenv import dotenv_values

from polymaker.dashboard.pm_account import wallet_for

HOST = "https://clob.polymarket.com"
DERIVE = "/auth/derive-api-key"
BALANCE = "/balance-allowance"


class BalanceReadError(RuntimeError):
    """Contains only a fixed diagnostic code, never credential/HTTP bodies."""


class BalanceReadTransport(httpx.BaseTransport):
    def __init__(self, inner=None):
        self.inner = inner or httpx.HTTPTransport(retries=0)

    def handle_request(self, request):
        url = request.url
        valid_query = (not url.query if url.path == DERIVE else
            dict(url.params) in ({"asset_type": "COLLATERAL", "signature_type": str(i)} for i in range(4)))
        if (request.method != "GET" or url.scheme != "https" or url.host != "clob.polymarket.com"
                or url.port not in {None, 443} or url.path not in {DERIVE, BALANCE}
                or url.username or url.password or not valid_query):
            raise BalanceReadError("BALANCE_READ_ONLY_ROUTE_REQUIRED")
        return self.inner.handle_request(request)

    def close(self):
        self.inner.close()


def balance_settings(project, expected_wallet):
    # No Config/Secrets initialization or strategy imports. Only read the
    # existing signing identity and signature type required by these two GETs.
    values = {**dotenv_values(project / ".env", interpolate=False), **os.environ}

    def alias(first, second, *, address=False):
        found = [str(values[k]).strip() for k in (first, second) if values.get(k)]
        if address:
            found = [v.lower() for v in found]
        if not found or len(set(found)) != 1:
            raise BalanceReadError("BALANCE_IDENTITY_MISSING_OR_CONFLICTING")
        return found[0]

    key = alias("PK", "POLYMARKET_PRIVATE_KEY")
    wallet = alias("BROWSER_ADDRESS", "POLYMARKET_WALLET_ADDRESS", address=True)
    if not re.fullmatch(r"0x[0-9a-f]{40}", wallet) or wallet != expected_wallet:
        raise BalanceReadError("BALANCE_WALLET_MISMATCH")
    with (project / "config/config.toml").open("rb") as stream:
        config = tomllib.load(stream).get("wallet", {})
    kind = config.get("signature_type", 2)
    if (type(kind) is not int or kind not in range(4) or config.get("chain_id", 137) != 137
            or config.get("clob_host", HOST).rstrip("/") != HOST):
        raise BalanceReadError("BALANCE_WALLET_CONFIG_UNSUPPORTED")
    return key, wallet, kind


def fetch_balance(project, wallet, *, transport=None):
    # Imports are deferred until a live session actually needs this read.
    # These helpers sign authentication messages; no SDK network client exists.
    from py_clob_client_v2.clob_types import ApiCreds, RequestArgs
    from py_clob_client_v2.headers.headers import create_level_1_headers, create_level_2_headers
    from py_clob_client_v2.signer import Signer

    key, wallet, kind = balance_settings(project, wallet)
    signer = Signer(key, 137)
    if kind == 0 and signer.address().lower() != wallet:
        raise BalanceReadError("BALANCE_SIGNER_WALLET_MISMATCH")
    with httpx.Client(timeout=5, follow_redirects=False, transport=BalanceReadTransport(transport)) as client:
        response = client.get(HOST+DERIVE, headers=create_level_1_headers(signer, nonce=0))
        response.raise_for_status()
        raw = response.json()
        if not isinstance(raw, dict) or not all(isinstance(raw.get(k), str) and raw[k]
                for k in ("apiKey", "secret", "passphrase")):
            raise BalanceReadError("BALANCE_EXISTING_CREDENTIALS_UNAVAILABLE")
        creds = ApiCreds(api_key=raw["apiKey"], api_secret=raw["secret"], api_passphrase=raw["passphrase"])
        headers = create_level_2_headers(signer, creds, RequestArgs(method="GET", request_path=BALANCE))
        response = client.get(HOST+BALANCE, headers=headers,
            params={"asset_type": "COLLATERAL", "signature_type": kind})
        response.raise_for_status()
        payload = response.json()
        amount = payload.get("balance") if isinstance(payload, dict) else None
        if not isinstance(amount, str) or not re.fullmatch(r"[0-9]{1,78}", amount):
            raise BalanceReadError("BALANCE_MICRO_UNITS_INVALID")
        return {"state": "AVAILABLE", "wallet": wallet, "balance_usdc": str(Decimal(amount)/1_000_000),
            "observed_at": time.time(), "source": HOST+BALANCE}


class BalanceObserver:
    """Persist the attempt before submitting it; refresh/restart cannot re-read."""

    def __init__(self, collector, fetch=fetch_balance):
        self.collector, self.fetch = collector, fetch
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dashboard-balance-read")
        self.future, self.pending = None, None
        collector.db.execute("CREATE TABLE IF NOT EXISTS pm_session_balance(sid TEXT PRIMARY KEY,payload TEXT NOT NULL)")
        self.cache = {r[0]: json.loads(r[1]) for r in collector.db.execute("SELECT * FROM pm_session_balance")}
        for sid, payload in list(self.cache.items()):
            if payload["state"] == "REQUESTED":
                self.save(sid, {**payload, "state": "UNAVAILABLE", "error": "BALANCE_READ_INTERRUPTED"})

    def save(self, sid, payload):
        self.cache[sid] = payload
        self.collector.db.execute("INSERT OR REPLACE INTO pm_session_balance VALUES(?,?)", (sid, json.dumps(payload)))
        self.collector.db.commit()

    def tick(self, snapshot):
        if self.future is not None and self.future.done():
            sid, wallet = self.pending
            try:
                payload = self.future.result()
                if payload.get("wallet") != wallet or payload.get("state") != "AVAILABLE":
                    raise BalanceReadError("BALANCE_RESPONSE_IDENTITY_INVALID")
            except Exception as exc:
                # Never serialize repr/str of unknown exceptions: signing and
                # authentication failures may contain sensitive response data.
                error = str(exc) if isinstance(exc, BalanceReadError) else type(exc).__name__
                payload = {"state": "UNAVAILABLE", "wallet": wallet, "error": error}
            self.save(sid, {**self.cache[sid], **payload})
            self.future, self.pending = None, None
        now = snapshot["observed_at"]
        live = [s for s in snapshot["sessions"] if s.get("strategy") == "M1" and s.get("post_type") == "REAL"
                and s.get("market_start", 0) <= now < s.get("market_end", 0) and wallet_for(s)]
        current = max(live, key=lambda s: (s["market_start"], s["last_event_at"]), default=None)
        if self.future is None and current and current["session_id"] not in self.cache:
            sid, wallet = current["session_id"], wallet_for(current)
            self.save(sid, {"state": "REQUESTED", "wallet": wallet, "requested_at": now})
            self.pending = sid, wallet
            self.future = self.pool.submit(self.fetch, self.collector.project, wallet)
        for session in snapshot["sessions"]:
            cached = self.cache.get(session["session_id"])
            if cached and cached["wallet"] == wallet_for(session):
                session["pm_balance"] = cached
                session["account_balance_usdc"] = cached.get("balance_usdc")
                session["account_balance_at"] = cached.get("observed_at")
        snapshot["pm_balance_reader"] = {"mode": "AUTHENTICATED_GET_ONCE_PER_SESSION",
            "pending": self.pending[0] if self.pending else None}

    def close(self):
        self.pool.shutdown(wait=True, cancel_futures=True)
