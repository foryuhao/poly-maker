"""Official public account economics without keys, writes or invented order attribution."""

import copy
import io
import sqlite3
import time
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from rich.console import Console

from polymaker.dashboard.m1_trades import trade_page, trade_records
from polymaker.dashboard.m1_view import pnl
from polymaker.dashboard.model import summarize
from polymaker.dashboard.pm_account import (
    AccountObserver,
    AccountReadTransport,
    account_market,
    apply_account,
    fetch_account,
    normalize_position,
    wallet_for,
)
from polymaker.dashboard.view import render
from test_dashboard import NOW, source
from test_dashboard_m1_native import live_events

WALLET = "0x"+"1"*40
CONDITION = "0x"+"a"*64
SID = "m1_live_"+"a"*32


def session():
    s = summarize(SID, live_events(), [source()])
    s.update(condition_id=CONDITION, slug=f"btc-updown-5m-{NOW-600}", market_start=NOW-600,
             market_end=NOW-300, token_ids={"YES": "1", "NO": "2"})
    s["sources"] = [{**source(), "path": f"/logs/rdp_sm_{WALLET}_{SID}.jsonl"}]
    return s


def position(**changes):
    return {"proxy_wallet": WALLET, "condition_id": CONDITION, "slug": session()["slug"],
        "token_id": "1", "opposite_token_id": "2", "outcome_index": 0,
        "current_size": 10, "current_value": 7.6, "total_size": 10, "avg_price": .1,
        "entry_cost_usdc": 1, "entry_fees_usdc": 0, "total_cost_usdc": 1,
        "realized_pnl": 0, "unrealized_pnl": 6.6, "total_pnl": 6.6, "status": "OPEN", **changes}


def activity(**changes):
    return {"proxy_wallet": WALLET, "condition_id": CONDITION, "slug": session()["slug"],
        "token_id": "1", "outcome_index": 0, "type": "TRADE", "side": "BUY", "size": 10,
        "price": .1, "usdc_size": 1, "timestamp": NOW-500,
        "transaction_hash": "0x"+"b"*64, **changes}


def resolution():
    return {"slug": session()["slug"], "condition_id": CONDITION,
        "token_ids": {"YES": "1", "NO": "2"}, "winner": "YES", "closed": True,
        "resolution_status": "resolved", "source": "https://gamma-api.polymarket.com/markets/slug/test"}


def envelope(rows, cursor=None, more=False):
    return httpx.Response(200, json={"data": rows,
        "pagination": {"has_more": more, "next_cursor": cursor}})


def transport_for(rows=None, trades=None, *, page=False, recorded=None):
    def respond(request):
        if recorded is not None:
            recorded.append(request)
        assert request.method == "GET" and "authorization" not in request.headers
        assert request.url.params["user"] == WALLET
        assert request.url.params["condition"] == CONDITION
        if request.url.path == "/v2/activity":
            assert request.url.params["type"] == "TRADE"
            return envelope([activity()] if trades is None else trades)
        assert request.url.path == "/v2/positions"
        if request.url.params["status"] == "CLOSED":
            return envelope([])
        assert request.url.params["filter_amount"] == "0"
        if page and "cursor" not in request.url.params:
            return envelope([], "opaque", True)
        return envelope([position()] if rows is None else rows)
    return httpx.MockTransport(respond)


@pytest.mark.parametrize("method,url", [("POST", "https://data-api.polymarket.com/v2/activity"),
    ("DELETE", "https://clob.polymarket.com/order"), ("GET", "https://example.com/v2/positions"),
    ("GET", "http://data-api.polymarket.com/v2/positions"), ("GET", "https://clob.polymarket.com/data/orders")])
def test_only_approved_public_get_requests_reach_transport(method, url):
    reached = []
    with (httpx.Client(transport=AccountReadTransport(httpx.MockTransport(lambda r: reached.append(r)))) as client,
          pytest.raises(RuntimeError, match="READ_ONLY_ROUTE_REQUIRED")):
        client.request(method, url)
    assert reached == []


def test_cursor_walk_preserves_filters_and_settlement_uses_final_payout_once():
    requests = []
    result = fetch_account(WALLET, [session()], {CONDITION: resolution()},
                           transport=transport_for(page=True, recorded=requests))[CONDITION]
    assert len(requests) == 4  # Empty first OPEN page still has a next cursor.
    assert result["settled_pnl"] == "9.0"  # 6.6 marked PnL + 10 payout - 7.6 marked value.
    assert result["realized_pnl"] == "0" and result["unrealized_pnl"] == "6.6"
    assert result["inventory"] == {"YES": "10", "NO": "0"}
    assert len(result["trades"]) == 1 and result["trades"][0]["amount"] == "1"
    assert result["wallet"] == WALLET


def test_closed_position_with_zero_remaining_cost_keeps_official_realized_profit():
    row = normalize_position(position(current_size=0, current_value=0, total_cost_usdc=0,
        entry_cost_usdc=0, status="CLOSED", realized_pnl=11.4943, unrealized_pnl=0, total_pnl=11.4943))
    result = account_market(session(), [row], [], None, NOW)
    assert result["settled_pnl"] == "11.4943"


def test_unresolved_marks_and_missing_index_are_not_treated_as_settled_pnl():
    row = normalize_position(position())
    result = account_market(session(), [row], [], None, NOW)
    assert result["settled_pnl"] is None and result["unrealized_pnl"] == "6.6"
    pending = account_market(session(), [], [], None, NOW)
    assert pending["state"] == "INDEX_PENDING" and pending["settled_pnl"] is None
    empty = session()
    empty["real_submitted_orders"] = 0
    assert account_market(empty, [], [], None, NOW)["settled_pnl"] == "0"
    empty["market_end"] = NOW+300
    active = account_market(empty, [], [], None, NOW)
    assert active["state"] == "AVAILABLE" and active["settled_pnl"] is None
    empty["market_end"] = NOW-300
    empty["sources"][0]["caught_up"] = False
    assert account_market(empty, [], [], None, NOW)["settled_pnl"] is None


@pytest.mark.parametrize("change", [{"proxy_wallet": "0x"+"2"*40}, {"slug": "another-market"},
    {"current_size": None}, {"total_pnl": 999}, {"entry_fees_usdc": None}])
def test_wrong_identity_missing_numbers_and_conflicting_pnl_are_rejected(change):
    with pytest.raises(ValueError):
        fetch_account(WALLET, [session()], {CONDITION: resolution()},
                      transport=transport_for(rows=[position(**change)]))


def test_repeated_cursor_fails_instead_of_publishing_a_partial_account():
    with pytest.raises(ValueError, match="CURSOR_INVALID"):
        fetch_account(WALLET, [session()], transport=httpx.MockTransport(
            lambda r: envelope([], "same-cursor", True)))


def test_identical_public_activity_rows_are_not_silently_dropped():
    result = fetch_account(WALLET, [session()], {CONDITION: resolution()},
        transport=transport_for(trades=[activity(), activity()]))[CONDITION]
    assert len(result["trades"]) == 2
    assert result["trades"][0]["id"] != result["trades"][1]["id"]


def test_account_result_enriches_view_and_history_without_assigning_fills_to_order_ids():
    s = session()
    result = fetch_account(WALLET, [s], {CONDITION: resolution()}, transport=transport_for())[CONDITION]
    apply_account(s, result)
    assert s["m1_fill_tracking"] == "NOT_TRACKED"  # The strategy is unchanged.
    assert pnl(s) == "9.0" and s["orders"][0]["confirmed_quantity"] is None
    records = trade_records([s])
    assert len(records) == 2
    actual = next(r for r in records if r.get("record_type") == "ACCOUNT_TRADE")
    assert actual["filled"] == "10" and actual["cost"] == "1" and Decimal(actual["pnl"]) == 9
    console = Console(width=140, file=io.StringIO(), record=True)
    console.print(render({"observed_at": NOW, "sessions": [s], "current_session_id": SID},
        now=NOW, width=140, height=50))
    console.print(trade_page([s], 50, 0))
    text = console.export_text()
    assert "PM 账户持仓 YES 10 / NO 0" in text and "真实账户场次 PnL +9.000" in text
    assert "真实成交" in text and "真实订单" in text
    assert "账户余额：当前只读接口未提供" in text


def test_background_reader_retains_last_good_economics_after_a_network_failure(tmp_path):
    s = session()
    payload = fetch_account(WALLET, [s], {CONDITION: resolution()}, transport=transport_for())[CONDITION]
    collector = SimpleNamespace(db=sqlite3.connect(":memory:"), output=tmp_path)
    calls = []

    def fetch(wallet, sessions, resolutions):
        calls.append(wallet)
        if len(calls) > 1:
            raise httpx.ReadTimeout("network unavailable")
        return {CONDITION: copy.deepcopy(payload)}

    reader = AccountObserver(collector, fetch=fetch)
    try:
        snapshot = {"sessions": [s], "observed_at": time.time()}
        reader.tick(snapshot)
        reader.future.result(timeout=2)
        reader.tick(snapshot)
        assert s["trading_pnl"] == "9.0" and wallet_for(s) == WALLET
        key = WALLET+":"+CONDITION
        reader.cache[key]["observed_at"] = 0
        reader.retry_at.clear()
        reader.tick(snapshot)
        with pytest.raises(httpx.ReadTimeout):
            reader.future.result(timeout=2)
        reader.tick(snapshot)
        assert s["trading_pnl"] == "9.0" and "PM_ACCOUNT_READ_FAILED" in s["issues"]
        assert "ReadTimeout" in s["pm_account"]["error"]
        assert collector.db.execute("SELECT count(*) FROM pm_account").fetchone()[0] == 1
    finally:
        reader.close()
        collector.db.close()


def test_multiple_journals_for_same_wallet_market_do_not_double_count_account_pnl(tmp_path):
    first = session()
    second = copy.deepcopy(first)
    second["session_id"] = "m1_live_"+"b"*32
    second["sources"][0]["path"] = f"/logs/rdp_sm_{WALLET}_{second['session_id']}.jsonl"
    second["last_event_at"] = first["last_event_at"]+1
    payload = fetch_account(WALLET, [first], {CONDITION: resolution()}, transport=transport_for())[CONDITION]
    collector = SimpleNamespace(db=sqlite3.connect(":memory:"), output=tmp_path)
    reader = AccountObserver(collector, fetch=lambda *args: {CONDITION: copy.deepcopy(payload)})
    try:
        snapshot = {"sessions": [first, second], "observed_at": time.time()}
        reader.tick(snapshot)
        reader.future.result(timeout=2)
        reader.tick(snapshot)
        assert first["trading_pnl"] is None and second["trading_pnl"] == "9.0"
    finally:
        reader.close()
        collector.db.close()
