"""Cash reads are authenticated but physically restricted to two GET routes."""

import copy
import io
import json
import sqlite3
from types import SimpleNamespace

import httpx
import pytest
from rich.console import Console

from polymaker.dashboard.balance import (
    BalanceObserver,
    BalanceReadError,
    BalanceReadTransport,
    fetch_balance,
)
from polymaker.dashboard.m1_view import account_panel
from test_dashboard_pm_account import WALLET, session


@pytest.fixture
def project(tmp_path, monkeypatch):
    for key in ("PK", "POLYMARKET_PRIVATE_KEY", "BROWSER_ADDRESS", "POLYMARKET_WALLET_ADDRESS"):
        monkeypatch.delenv(key, raising=False)
    # Public test signing identity only; no production secrets used by tests.
    (tmp_path / ".env").write_text("PK="+"1"*64+"\nBROWSER_ADDRESS="+WALLET+"\n")
    (tmp_path / "config").mkdir()
    (tmp_path / "config/config.toml").write_text("[wallet]\nsignature_type=2\nchain_id=137\n")
    return tmp_path


def mock_balance(amount="31929546", recorded=None):
    def respond(request):
        if recorded is not None:
            recorded.append(request)
        assert request.method == "GET"
        assert request.headers["POLY_SIGNATURE"]
        if request.url.path == "/auth/derive-api-key":
            assert request.headers["POLY_NONCE"] == "0"
            return httpx.Response(200, json={"apiKey": "test-api-key", "secret": "c2VjcmV0", "passphrase": "test-pass"})
        assert request.url.path == "/balance-allowance"
        assert dict(request.url.params) == {"asset_type": "COLLATERAL", "signature_type": "2"}
        assert request.headers["POLY_API_KEY"] == "test-api-key"
        return httpx.Response(200, json={"balance": amount, "allowances": {"spender": "999999999999"}})
    return httpx.MockTransport(respond)


@pytest.mark.parametrize("amount,expected", [("31929546", "31.929546"), ("0", "0")])
def test_existing_auth_then_one_cash_get_normalizes_micro_units(project, amount, expected):
    requests = []
    payload = fetch_balance(project, WALLET, transport=mock_balance(amount, requests))
    assert payload["balance_usdc"] == expected and payload["wallet"] == WALLET
    assert len(requests) == 2
    encoded = json.dumps(payload)
    assert all(secret not in encoded for secret in ("test-api-key", "test-pass", "c2VjcmV0"))


@pytest.mark.parametrize("method,url", [
    ("POST", "https://clob.polymarket.com/auth/api-key"),
    ("POST", "https://clob.polymarket.com/order"),
    ("DELETE", "https://clob.polymarket.com/order"),
    ("GET", "https://clob.polymarket.com/balance-allowance/update"),
    ("GET", "https://clob.polymarket.com/balance-allowance?asset_type=CONDITIONAL&signature_type=2"),
    ("GET", "https://evil.example/auth/derive-api-key"),
    ("GET", "http://clob.polymarket.com/auth/derive-api-key"),
    ("GET", "https://clob.polymarket.com:444/auth/derive-api-key"),
])
def test_rejects_mutations_and_foreign_routes_before_network(method, url):
    requests = []
    with (httpx.Client(transport=BalanceReadTransport(mock_balance(recorded=requests))) as client,
          pytest.raises(BalanceReadError, match="READ_ONLY_ROUTE_REQUIRED")):
        client.request(method, url)
    assert not requests


@pytest.mark.parametrize("amount", [None, True, 100, "-1", "1.5", "NaN", "Infinity", "1e6"])
def test_bad_cash_is_never_zero_or_allowance(project, amount):
    with pytest.raises(BalanceReadError, match="MICRO_UNITS_INVALID"):
        fetch_balance(project, WALLET, transport=mock_balance(amount))


def test_wrong_funder_fails_before_network(project):
    requests = []
    with pytest.raises(BalanceReadError, match="WALLET_MISMATCH"):
        fetch_balance(project, "0x"+"2"*40, transport=mock_balance(recorded=requests))
    assert not requests


def test_derive_failure_never_creates_api_key(project):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(400, json={"error": "Could not derive api key"})

    with pytest.raises(httpx.HTTPStatusError):
        fetch_balance(project, WALLET, transport=httpx.MockTransport(respond))
    assert len(requests) == 1 and requests[0].method == "GET"


def collector(project):
    return SimpleNamespace(project=project, db=sqlite3.connect(":memory:"))


def snapshot(s):
    return {"observed_at": s["market_start"]+10, "sessions": [s]}


def test_one_attempt_per_session_survives_ticks_restart_and_new_session(project):
    c, calls = collector(project), []

    def fetch(root, wallet):
        calls.append(wallet)
        return {"state": "AVAILABLE", "wallet": wallet, "balance_usdc": "31.929546", "observed_at": 123}

    s = session()
    original = copy.deepcopy(s)
    obs = BalanceObserver(c, fetch)
    try:
        obs.tick(snapshot(s))
        assert s["pm_balance"]["state"] == "REQUESTED"
        obs.future.result(timeout=2)
        obs.tick(snapshot(s))
        assert s["account_balance_usdc"] == "31.929546"
        for _ in range(5):
            obs.tick(snapshot(copy.deepcopy(original)))
        assert calls == [WALLET]
    finally:
        obs.close()
    obs = BalanceObserver(c, fetch)
    try:
        obs.tick(snapshot(original))
        assert obs.future is None and len(calls) == 1
        assert original["account_balance_at"] == 123
        new = session()
        new["session_id"] += "new"
        new["sources"][0]["path"] = f"/logs/rdp_sm_{WALLET}_{new['session_id']}.jsonl"
        obs.tick(snapshot(new))
        obs.future.result(timeout=2)
        obs.tick(snapshot(new))
        assert len(calls) == 2
    finally:
        obs.close()
        c.db.close()


def test_failure_is_redacted_and_not_retried(project):
    c, calls = collector(project), []

    def fetch(*args):
        calls.append(1)
        raise RuntimeError("SECRET_PRIVATE_KEY_OR_API_RESPONSE")

    obs = BalanceObserver(c, fetch)
    try:
        s = session()
        obs.tick(snapshot(s))
        with pytest.raises(RuntimeError):
            obs.future.result(timeout=2)
        obs.tick(snapshot(s))
        obs.tick(snapshot(s))
        assert calls == [1] and s["account_balance_usdc"] is None
        assert s["pm_balance"]["error"] == "RuntimeError"
        assert "SECRET" not in str(c.db.execute("SELECT payload FROM pm_session_balance").fetchall())
    finally:
        obs.close()
        c.db.close()


def test_pending_on_restart_is_interrupted_without_second_attempt(project):
    c = collector(project)
    s = session()
    c.db.execute("CREATE TABLE pm_session_balance(sid TEXT PRIMARY KEY,payload TEXT NOT NULL)")
    c.db.execute("INSERT INTO pm_session_balance VALUES(?,?)", (s["session_id"], json.dumps({
        "state": "REQUESTED", "wallet": WALLET, "requested_at": 123})))
    obs = BalanceObserver(c, lambda *args: pytest.fail("must not repeat interrupted GET"))
    try:
        obs.tick(snapshot(s))
        assert obs.future is None
        assert s["pm_balance"]["error"] == "BALANCE_READ_INTERRUPTED"
    finally:
        obs.close()
        c.db.close()


def test_historical_sessions_are_not_backfilled_with_current_cash(project):
    c = collector(project)
    obs = BalanceObserver(c, lambda *args: pytest.fail("must not query historical cash"))
    try:
        s = session()
        obs.tick({"observed_at": s["market_end"]+1, "sessions": [s]})
        assert obs.future is None and "pm_balance" not in s
    finally:
        obs.close()
        c.db.close()


def test_once_per_session_snapshot_is_not_marked_expired_after_ten_seconds():
    s = session()
    s.update(account_balance_usdc="31.929546", account_balance_at=123,
        pm_balance={"state": "AVAILABLE"})
    stream = io.StringIO()
    Console(file=stream, width=100).print(account_panel(s, 180))
    assert "31.929546 USDC" in stream.getvalue()
    assert "本场一次快照" in stream.getvalue() and "已过期" not in stream.getvalue()
