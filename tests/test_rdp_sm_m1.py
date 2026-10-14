"""Integrated M1 tests: real FL readers/SDK/SM/CLI, fake HTTP transport only.

Run with the preserved M0 offline runner: no credentials/network/SQLite needed.
"""

from __future__ import annotations

import json
import logging
import socket
from copy import deepcopy
from datetime import UTC, datetime

import httpx
import pytest
import structlog
from eth_account import Account
from eth_utils import to_checksum_address
from py_clob_client_v2.config import get_contract_config
from typer.testing import CliRunner

from polymaker.btc5m import current_epoch, slug_for_epoch
from polymaker.cli import app
from polymaker.config import Config, Secrets
from polymaker.execution.gateway import ExecutionGateway
from polymaker.rdp_sm import ReadOnlyClobClient, SessionStatus, run_preflight

START = current_epoch(1_800_000_000)
SLUG = slug_for_epoch(START)
CID = "0x" + "ab" * 32
# Public deterministic dummy key; never used with a real transport.
DUMMY_KEY = "0x" + "01" * 32
ADDRESS = Account.from_key(DUMMY_KEY).address
SPENDER = get_contract_config(137).exchange_v2


class Venue:
    def __init__(self):
        self.funder = ADDRESS
        self.time = START + 60
        self.raw = {
            "slug": SLUG,
            "conditionId": CID,
            "question": "title is irrelevant",
            "active": True,
            "closed": False,
            "acceptingOrders": True,
            "enableOrderBook": True,
            "negRisk": False,
            "clobTokenIds": '["2", "1"]',
            "outcomes": '["Down", "Up"]',
            "orderMinSize": "5",
            "orderPriceMinTickSize": ".01",
            "endDate": datetime.fromtimestamp(START + 300, UTC).isoformat(),
        }
        self.books = {
            token: {
                "market": CID,
                "asset_id": token,
                "timestamp": str(self.time * 1000),
                "tick_size": tick,
                "bids": [{"price": bid, "size": "20"}],
                "asks": [{"price": ask, "size": "10"}],
            }
            for token, tick, bid, ask in (("1", ".01", ".49", ".51"), ("2", ".001", ".48", ".52"))
        }
        self.ticks = {"1": ".01", "2": ".001"}
        self.positions = []
        self.orders = []
        self.capital = {"balance": "100000000", "allowances": {SPENDER: "100000000"}}
        self.version = {"version": 2}
        self.fail_path = None
        self.calls = []

    def response(self, request):
        assert request.method == "GET", "M1 attempted a non-read HTTP method"
        self.calls.append((request.url.host, request.url.path, dict(request.url.params)))
        host, path = request.url.host, request.url.path
        assert host in {
            "gamma-api.polymarket.com",
            "clob.polymarket.com",
            "data-api.polymarket.com",
        }
        if path == self.fail_path:
            raise httpx.ReadTimeout("fake offline read failure")
        if host == "gamma-api.polymarket.com":
            assert path == "/markets" and request.url.params["slug"] == SLUG
            data = [self.raw] if self.raw is not None else []
        elif host == "data-api.polymarket.com":
            assert path == "/v2/positions" and request.url.params["user"] == self.funder
            data = {"data": self.positions, "pagination": {"next_cursor": None}}
        elif path == "/auth/derive-api-key":
            data = {"apiKey": "dummy-key", "secret": "c2FmZS10ZXN0", "passphrase": "dummy-pass"}
        elif path == "/book":
            data = self.books[request.url.params["token_id"]]
        elif path == "/tick-size":
            data = {"minimum_tick_size": self.ticks[request.url.params["token_id"]]}
        elif path == "/balance-allowance":
            data = self.capital
        elif path == "/data/orders":
            data = {"data": self.orders, "next_cursor": "LTE="}
        elif path == "/version":
            data = self.version
        elif path == "/time":
            data = self.time
        else:
            raise AssertionError(f"Unexpected endpoint {path}")
        return httpx.Response(200, json=deepcopy(data), request=request)


@pytest.fixture
def venue(monkeypatch, tmp_path):
    root_logger = logging.getLogger()
    handlers, level = root_logger.handlers[:], root_logger.level
    log_config = structlog.get_config().copy()

    def deny(*_args, **_kwargs):
        raise AssertionError("M1 tests forbid sockets/DNS")

    for name in ("connect", "connect_ex", "sendto", "sendmsg"):
        if hasattr(socket.socket, name):
            monkeypatch.setattr(socket.socket, name, deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)
    monkeypatch.chdir(tmp_path)
    v = Venue()
    transport = httpx.MockTransport(v.response)
    async_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: async_client(transport=transport, **kwargs)
    )
    client = httpx.Client(transport=transport)
    monkeypatch.setattr("py_clob_client_v2.http_helpers.helpers._http_client", client)
    monkeypatch.setattr(ExecutionGateway, "connect", deny)
    for name in (
        "place",
        "cancel",
        "cancel_all",
        "cancel_asset",
        "cancel_market_asset",
        "market_order",
        "token_balance",
        "token_balances",
        "_token_balance_opt",
    ):
        monkeypatch.setattr(ExecutionGateway, name, deny)
    yield v
    client.close()
    root_logger.handlers, root_logger.level = handlers, level
    structlog.configure(**log_config)
    assert not list(tmp_path.iterdir()), "preflight/CLI created persistent state"


@pytest.fixture
def cfg():
    return Config(secrets=Secrets.model_construct(pk=DUMMY_KEY, browser_address=ADDRESS))


async def test_t01_t02_t03_t12_integrated_ready_identity_mapping_tick(venue, cfg):
    result = await run_preflight(cfg, SLUG)
    assert result.status is SessionStatus.READY
    assert result.market["condition_id"] == CID
    assert result.market["start"] == START and result.market["end"] == START + 300
    assert result.market["yes"]["token_id"] == "1" and result.market["no"]["token_id"] == "2"
    assert result.market["yes"]["tick"] == 0.01 and result.market["no"]["tick"] == 0.001
    assert result.market["yes"]["best_bid"] == 0.49 and result.market["no"]["best_ask"] == 0.52
    assert result.time_source == "OFFICIAL_CLOB_TIME" and result.market["elapsed_s"] == 60
    assert result.account["signer"] == ADDRESS and result.account["funder"] == ADDRESS
    assert result.account["basic_spendable_budget_usd"] == 20
    data = json.loads(json.dumps(result.to_dict(), allow_nan=False))
    assert (
        data["status"] == "SESSION_READY" and data["read_only"] and not data["trading_authorized"]
    )
    assert {c["name"] for c in data["checks"]} == {
        "market",
        "account_reads",
        "clean_market",
        "capital",
        "freshness",
        "trading_window",
    }
    assert all(c["outcome"] == "PASS" for c in data["checks"])


async def test_proxy_positions_use_funder_and_eoa_mismatch_blocks(venue, cfg):
    venue.funder = to_checksum_address("0x" + "ab" * 20)
    cfg.secrets.browser_address = venue.funder
    result = await run_preflight(cfg, SLUG)
    assert result.status is SessionStatus.READY
    assert result.account["funder"] == venue.funder and result.account["signer"] == ADDRESS
    cfg.wallet.signature_type = 0
    result = await run_preflight(cfg, SLUG)
    assert result.status is SessionStatus.BLOCKED
    assert result.checks[-1].detail == "EOA_FUNDER_IDENTITY_MISMATCH"


@pytest.mark.parametrize(
    "elapsed,expected",
    [(0, False), (14.999, False), (15, True), (239.999, True), (240, True), (300, False)],
)
async def test_t05_t06_t07_exact_session_window(venue, cfg, elapsed, expected):
    venue.time = START + elapsed
    for book in venue.books.values():
        book["timestamp"] = str(venue.time * 1000)
    result = await run_preflight(cfg, SLUG)
    assert result.market["trading_eligible"] is expected
    assert result.status is (SessionStatus.READY if expected else SessionStatus.REJECTED)


@pytest.mark.parametrize(
    "problem",
    [
        "missing",
        "tokens",
        "duration",
        "book_identity",
        "bad_level",
        "tick_changed",
        "stale",
        "missing_flag",
    ],
)
async def test_t04_missing_invalid_or_stale_market_blocks(venue, cfg, problem):
    if problem == "missing":
        venue.raw = None
    elif problem == "tokens":
        venue.raw["outcomes"] = '["Left", "Right"]'
    elif problem == "duration":
        venue.raw["endDate"] = datetime.fromtimestamp(START + 600, UTC).isoformat()
    elif problem == "book_identity":
        venue.books["2"]["asset_id"] = "1"
    elif problem == "bad_level":
        venue.books["1"]["bids"][0]["price"] = True
    elif problem == "tick_changed":
        venue.ticks["2"] = ".01"
    elif problem == "stale":
        venue.books["1"]["timestamp"] = str((venue.time - 11) * 1000)
    else:
        venue.raw.pop("enableOrderBook")
    result = await run_preflight(cfg, SLUG)
    assert result.status is SessionStatus.BLOCKED and not result.trading_authorized
    expected_stage = {
        "missing": "market",
        "tokens": "market",
        "duration": "market",
        "missing_flag": "market",
        "book_identity": "books",
        "bad_level": "books",
        "tick_changed": "books",
        "stale": "freshness",
    }[problem]
    assert result.checks[-1].name == expected_stage


async def test_known_nontradable_market_rejected(venue, cfg):
    venue.raw["closed"] = True
    assert (await run_preflight(cfg, SLUG)).status is SessionStatus.REJECTED


@pytest.mark.parametrize("token", ["1", "2"])
async def test_t08_target_inventory_rejected(venue, cfg, token):
    venue.positions = [{"token_id": token, "current_size": 1, "avg_price": 0.5}]
    assert (await run_preflight(cfg, SLUG)).status is SessionStatus.REJECTED


def order(token="1", size="10", side="BUY"):
    return {
        "id": "external-1",
        "asset_id": token,
        "side": side,
        "original_size": size,
        "size_matched": "0",
        "price": ".5",
    }


async def test_t09_preexisting_target_order_rejected(venue, cfg):
    venue.orders = [order()]
    result = await run_preflight(cfg, SLUG)
    assert result.status is SessionStatus.REJECTED and result.account["target_open_orders"] == 1


async def test_other_market_positions_allowed_but_buy_commitment_subtracted(venue, cfg):
    venue.positions = [{"token_id": "3", "current_size": 50, "avg_price": 0.5}]
    venue.orders = [order("3", "190")]
    result = await run_preflight(cfg, SLUG)
    assert result.status is SessionStatus.READY
    assert result.account["existing_buy_commitment_usd"] == 95
    assert result.account["available_balance_usd"] == 5
    venue.orders = [order("3", "200")]
    result = await run_preflight(cfg, SLUG)
    assert result.status is SessionStatus.REJECTED and result.account["available_balance_usd"] == 0


@pytest.mark.parametrize(
    "path",
    [
        "/auth/derive-api-key",
        "/v2/positions",
        "/data/orders",
        "/balance-allowance",
        "/version",
        "/time",
    ],
)
async def test_t10_account_and_official_read_failures_block(venue, cfg, path):
    venue.fail_path = path
    result = await run_preflight(cfg, SLUG)
    assert result.status is SessionStatus.BLOCKED and not result.trading_authorized
    assert any(call_path == path for _, call_path, _ in venue.calls)
    assert result.checks[-1].name == (
        "account_identity"
        if path == "/auth/derive-api-key"
        else "freshness"
        if path == "/time"
        else "account"
    )


@pytest.mark.parametrize(
    "balance,allowance,expected",
    [
        ("10000000", "10000000", SessionStatus.READY),  # $10 != required $30 balance
        ("0", "10000000", SessionStatus.REJECTED),
        ("10000000", "0", SessionStatus.REJECTED),
        ("100", "10000000", SessionStatus.REJECTED),
        (True, "10000000", SessionStatus.BLOCKED),
        ("NaN", "10000000", SessionStatus.BLOCKED),
        ("10000000", None, SessionStatus.BLOCKED),
    ],
)
async def test_t11_capital_known_insufficient_vs_unknown(venue, cfg, balance, allowance, expected):
    venue.capital = {"balance": balance, "allowances": {SPENDER: allowance}}
    result = await run_preflight(cfg, SLUG)
    assert result.status is expected
    if expected is SessionStatus.BLOCKED:
        assert result.checks[-1].name == "account"
    else:
        assert result.account is not None
        assert next(c for c in result.checks if c.name == "capital").outcome == (
            "PASS" if expected is SessionStatus.READY else "REJECTED"
        )


async def test_missing_credentials_and_unapproved_hosts_block_before_external_reads(venue, cfg):
    cfg.wallet.clob_host = "https://third-party.invalid"
    assert (await run_preflight(cfg, SLUG)).status is SessionStatus.BLOCKED
    assert venue.calls == []
    cfg.wallet.clob_host = "https://clob.polymarket.com"
    cfg.secrets = Secrets.model_construct()
    assert (await run_preflight(cfg, SLUG)).status is SessionStatus.BLOCKED
    assert all(path == "/markets" for _, path, _ in venue.calls)


def test_t13_t14_cli_isolated_read_only_structured_output(venue, cfg, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("FL runtime/DB/settlement must not start")

    monkeypatch.setattr("polymaker.engine.Engine.__init__", forbidden)
    monkeypatch.setattr("polymaker.catalog.store.CatalogStore.__init__", forbidden)
    monkeypatch.setattr("polymaker.state.store.StateStore.__init__", forbidden)
    monkeypatch.setattr(Config, "load", lambda _path: cfg)
    result = CliRunner().invoke(app, ["rdp-sm-preflight", SLUG])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert (
        data["status"] == "SESSION_READY" and data["read_only"] and not data["trading_authorized"]
    )
    assert "/auth/api-key" not in [path for _, path, _ in venue.calls]


def test_sdk_read_only_boundary_blocks_writes_and_mutating_get(venue):
    client = ReadOnlyClobClient("https://clob.polymarket.com", 137)
    before = len(venue.calls)
    with pytest.raises(RuntimeError, match="POST prohibited"):
        client._post("https://clob.polymarket.com/order")
    with pytest.raises(RuntimeError, match="DELETE prohibited"):
        client._delete("https://clob.polymarket.com/order")
    with pytest.raises(ValueError, match="UNAPPROVED_CLOB_READ"):
        client._get("https://clob.polymarket.com/balance-allowance/update")
    assert len(venue.calls) == before


async def test_t15_repeated_preflight_reloads_state_and_creates_no_trading_state(venue, cfg):
    first = await run_preflight(cfg, SLUG)
    assert first.status is SessionStatus.READY
    venue.orders = [order()]
    second = await run_preflight(cfg, SLUG)
    assert second.status is SessionStatus.REJECTED
    assert first.account["target_open_orders"] == 0 and second.account["target_open_orders"] == 1
    assert [path for _, path, _ in venue.calls].count("/auth/derive-api-key") == 2
    assert [path for _, path, _ in venue.calls].count("/tick-size") == 4


@pytest.mark.parametrize("problem", ["original_size", "size_matched", "duplicate", "position"])
async def test_malformed_account_rows_never_become_clean_or_uncommitted(venue, cfg, problem):
    if problem == "position":
        venue.positions = [{"token_id": "1", "avg_price": 0.5}]
    else:
        venue.orders = [order("3")]
        if problem == "duplicate":
            venue.orders.append(order("3"))
        else:
            venue.orders[0].pop(problem)
    result = await run_preflight(cfg, SLUG)
    assert result.status is SessionStatus.BLOCKED
    assert result.checks[-1].name == "account" and result.account is None


async def test_order_pagination_cycle_blocks_without_hanging(venue, cfg, monkeypatch):
    original = venue.response

    def cycle(request):
        response = original(request)
        if request.url.path == "/data/orders":
            return httpx.Response(
                200,
                json={"data": [], "next_cursor": request.url.params["next_cursor"]},
                request=request,
            )
        return response

    monkeypatch.setattr(venue, "response", cycle)
    # Existing transports hold the original bound method; route via a fresh fake.
    transport = httpx.MockTransport(cycle)
    with httpx.Client(transport=transport) as client:
        monkeypatch.setattr("py_clob_client_v2.http_helpers.helpers._http_client", client)
        result = await run_preflight(cfg, SLUG)
    assert result.status is SessionStatus.BLOCKED and result.checks[-1].name == "account"
    assert [path for _, path, _ in venue.calls].count("/data/orders") == 1


@pytest.mark.parametrize(
    "state,exit_code,status",
    [
        ("inventory", 1, "SESSION_REJECTED"),
        ("read_failure", 2, "SESSION_BLOCKED"),
    ],
)
def test_cli_failure_results_are_structured_and_nonzero(
    venue, cfg, monkeypatch, state, exit_code, status
):
    monkeypatch.setattr(Config, "load", lambda _path: cfg)
    if state == "inventory":
        venue.positions = [{"token_id": "1", "current_size": 1, "avg_price": 0.5}]
    else:
        venue.fail_path = "/v2/positions"
    result = CliRunner().invoke(app, ["rdp-sm-preflight", SLUG])
    assert result.exit_code == exit_code
    assert json.loads(result.stdout)["status"] == status


async def test_long_preflight_snapshot_blocks_even_with_recent_books(venue, cfg, monkeypatch):
    # Deterministic elapsed-time simulation, not a sleep or fake preflight logic.
    clock = iter((100.0, 111.0))
    monkeypatch.setattr("polymaker.rdp_sm.monotonic", lambda: next(clock))
    result = await run_preflight(cfg, SLUG)
    assert result.status is SessionStatus.BLOCKED
    assert result.checks[-1].detail == "PREFLIGHT_SNAPSHOT_EXPIRED"
