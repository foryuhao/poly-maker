"""One-market business recovery: real M1/M2/M3/FL, fake external HTTP/SDK only."""

import json
from copy import deepcopy

import httpx
import pytest

from polymaker.rdp_sm_entry import run_current_market
from test_rdp_sm_entry import boundary
from test_rdp_sm_m1 import START
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_m3_loop import current_orders, on_windows
from test_rdp_sm_m3_loop import env as loop_env
from test_rdp_sm_type2_account import type2


@pytest.fixture
def env(monkeypatch, tmp_path):
    yield from loop_env.__wrapped__(monkeypatch, tmp_path)


class Observer:
    def __init__(self):
        self.events = []

    def emit(self, kind, payload):
        if kind == "recovery":
            self.events.append(deepcopy(payload))


async def run(env, observer=None):
    v, cfg = env
    return await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono, observer=observer)


async def test_no_fill_then_cancel_live_lag_recovers_and_repairs_inventory(env):
    v, _ = env
    type2(env)
    v.time = START + 25
    reads = []
    def fills(venue, index):
        ids = current_orders(venue)
        if index == 1:
            venue.fill(ids["NO"], "no-filled", 5)
        elif index == 3:
            venue.fill(ids["YES"], "yes-repair", 5)
    on_windows(v, fills, stop_after=3)
    v.after_post = lambda venue: setattr(venue, "cancel_terminal", len(venue.posts) != 3)
    def lag(venue, oid):
        if oid == "o-3":
            reads.append(venue.time)
            if len(reads) >= 3:
                venue.rows[oid]["status"] = "CANCELED"
                venue.cancel_terminal = True
    v.on_order_read = lag
    observer = Observer()
    result = await run(env, observer)
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert result.stop_elapsed_s == 300 and result.merged_pairs == 5
    assert [w["state"] for w in result.windows] == ["NORMAL", "REBALANCE", "REBALANCE"]
    assert "ORDER_NOT_TERMINAL" in result.windows[1]["result"]["block_reason"]
    assert result.windows[1]["result"]["capital_committed"] > result.windows[0]["result"]["capital_committed"]
    assert len(v.posts) == 4 and result.confirmed_fills == 2 and result.delta == 0
    assert {e["status"] for e in observer.events} >= {"PAUSED", "RESUMED"}
    assert any(e["stage"] == "close" and "ORDER_NOT_TERMINAL" in e["reason"] for e in observer.events)


@pytest.mark.parametrize("path,status", [
    ("/v2/positions", None), ("/balance-allowance", None), ("/book", None),
    ("/data/order/o-1", None), ("/v2/positions", 429), ("/book", 503),
])
async def test_transient_read_after_no_fill_recovers_without_duplicate_orders(env, monkeypatch, path, status):
    v, _ = env
    failed = []
    v.time = START + 60
    def fills(venue, index):
        ids = current_orders(venue)
        venue.fill(ids["NO" if index == 1 else "YES"], f"fill-{index}", 5)
    on_windows(v, fills, stop_after=2)
    def fault(request):
        if request.url.path == path and v.trades and not failed:
            failed.append(v.time)
            if status:
                return httpx.Response(status, request=request, json={"error": "temporarily unavailable"})
            raise httpx.ReadTimeout("offline read timeout")
        return None
    with boundary(monkeypatch, v, fault):
        result = await run(env)
    assert failed and result.execution_status == "COMPLETED", result.stop_reason
    assert len(v.posts) == 3 and result.merged_pairs == 5
    assert result.confirmed_fills == 2 and result.final_close_barrier == "SAFE_TO_CONTINUE"


async def test_persistent_known_live_order_waits_until_market_end_and_retains_reservation(env):
    v, _ = env
    v.time = START + 60
    v.cancel_terminal = False
    on_windows(v, lambda *_: None)
    result = await run(env)
    assert result.execution_status == "INCOMPLETE" and result.stop_elapsed_s == 300
    assert result.final_close_barrier == "BLOCKED" and "ORDER_NOT_TERMINAL" in result.stop_reason
    assert len(v.posts) == 2 and v.executor.committed() > 4
    assert all(r["status"] == "LIVE" for r in v.rows.values())


async def test_runtime_clock_outage_keeps_confirmed_no_and_exits_at_anchored_deadline(env, monkeypatch):
    v, _ = env
    v.time = START + 60
    on_windows(v, lambda venue, _: venue.fill(current_orders(venue)["NO"], "no", 5))
    def clock_failure(request):
        if request.url.path == "/time" and v.trades:
            raise httpx.ReadTimeout("offline runtime clock outage")
        return None
    with boundary(monkeypatch, v, clock_failure):
        result = await run(env)
    assert result.stop_elapsed_s == 300 and result.execution_status == "BLOCKED"
    assert result.confirmed_no == 5 and result.confirmed_yes == 0
    assert len(v.posts) == 2 and result.final_close_barrier == "SAFE_TO_CONTINUE"


async def test_partial_trade_page_retains_existing_confirmation_and_account_risk_latch(env, monkeypatch):
    v, _ = env
    v.time = START + 60
    pages = []
    on_windows(v, lambda venue, _: venue.fill(current_orders(venue)["NO"], "no", 5))
    def partial_page(request):
        if (pages and request.url.path == "/data/trades"
                and request.url.params.get("next_cursor") != "next"):
            return httpx.Response(200, request=request, json={
                "data": deepcopy(v.trades), "next_cursor": "next"})
        if (request.url.path == "/data/trades" and v.trades
                and v.executor.safe_to_continue and not pages):
            pages.append(True)
            return httpx.Response(200, request=request, json={"data": deepcopy(v.trades), "next_cursor": "next"})
        if request.url.path == "/data/trades" and request.url.params.get("next_cursor") == "next":
            raise httpx.ReadTimeout("offline next-page outage")
        return None
    with boundary(monkeypatch, v, partial_page):
        result = await run(env)
    assert pages and result.stop_elapsed_s == 300 and result.execution_status == "INCOMPLETE"
    assert result.confirmed_no == 5 and result.confirmed_fills == 1
    assert len(v.posts) == 2 and result.final_close_barrier == "BLOCKED"
    assert v.executor.gateway._client.pagination_failure == "TRADES_PAGINATION_INCOMPLETE"


async def test_order_cumulative_lag_waits_for_exact_trade_proof_then_rebalances(env):
    v, _ = env
    v.time = START + 60
    def fills(venue, index):
        venue.fill(current_orders(venue)["NO" if index == 1 else "YES"], f"fill-{index}", 5)
    on_windows(v, fills, stop_after=2)
    def lag(venue, oid):
        if oid == "o-2" and venue.trades:
            venue.rows[oid]["size_matched"] = "0" if venue.time < START + 80 else "5"
    v.on_order_read = lag
    result = await run(env)
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert len(v.posts) == 3 and result.merged_pairs == 5 and result.confirmed_fills == 2
    assert result.windows[1]["elapsed_s"] >= 80 and result.windows[1]["state"] == "REBALANCE"


async def test_unknown_placement_is_not_resubmitted_or_forged_terminal_while_waiting(env):
    v, _ = env
    v.time = START + 60
    v.replies = [{"success": True, "orderID": "known"}, {}]
    result = await run(env)
    assert result.execution_status == "INCOMPLETE" and result.stop_elapsed_s == 300
    assert len(v.posts) == 2 and len(v.post_requests) == 1
    assert result.final_close_barrier == "BLOCKED" and v.executor.committed() > 2
    assert v.rows["hidden"]["status"] == "LIVE"
    assert v.executor.fatal == "UNKNOWN_PLACEMENT_EXPOSURE"


async def test_balance_recovers_then_first_real_order_is_allowed(env, monkeypatch):
    v, _ = env
    v.time = START + 60
    v.capital["balance"] = "3000000"
    submitted = []
    on_windows(v, lambda venue, _: [venue.fill(oid, name, 5) for name, oid in current_orders(venue).items()],
               stop_after=1)
    def topup(request):
        if v.time >= START + 90:
            v.capital["balance"] = "10000000"
        if request.method == "POST":
            submitted.append(v.time - START)
        return None
    with boundary(monkeypatch, v, topup):
        result = await run(env)
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert len(v.posts) == 2 and submitted and min(submitted) >= 90
    assert result.merged_pairs == 5 and v.executor.committed() <= 20


@pytest.mark.parametrize("recover", [True, False])
async def test_discovery_book_failure_pins_market_and_waits_for_recovery_or_end(env, monkeypatch, recover):
    v, _ = env
    v.time = START + 60
    good = deepcopy(v.books["1"])
    on_windows(v, lambda *_: None, stop_after=1)
    observed_slugs = []
    def book(request):
        if request.url.path == "/markets":
            observed_slugs.append(request.url.params["slug"])
        if request.url.path == "/book" and request.url.params["token_id"] == "1":
            raw = deepcopy(good)
            raw["timestamp"] = str(v.time * 1000)
            if not recover or v.time < START + 90:
                raw["asks"] = []
            return httpx.Response(200, request=request, json=raw)
        return None
    with boundary(monkeypatch, v, book):
        result = await run(env)
    assert len(set(observed_slugs)) == 1 and observed_slugs[0] == v.raw["slug"]
    assert result.stop_elapsed_s == 300
    if recover:
        assert result.execution_status == "COMPLETED" and len(v.posts) == 2
    else:
        assert result.execution_status == "BLOCKED" and result.stop_reason == "M1_SESSION_NOT_READY"
        assert not v.posts


async def test_temporarily_missing_market_read_recovers_without_selecting_next_market(env, monkeypatch):
    v, _ = env
    v.time = START + 60
    slugs = []
    on_windows(v, lambda *_: None, stop_after=1)
    def absent(request):
        if request.url.path == "/markets":
            slugs.append(request.url.params["slug"])
            if len(slugs) == 1:
                return httpx.Response(200, request=request, json=[])
        return None
    with boundary(monkeypatch, v, absent):
        result = await run(env)
    assert result.execution_status == "COMPLETED" and len(v.posts) == 2
    assert len(set(slugs)) == 1 and slugs[0] == v.raw["slug"]


@pytest.mark.parametrize("phase", ["m1", "wait_until_t15"])
async def test_startup_read_failure_retries_same_market_before_orders(env, monkeypatch, phase):
    v, _ = env
    v.time = START + (7 if phase == "wait_until_t15" else 60)
    failed = []
    time_reads = []
    on_windows(v, lambda *_: None, stop_after=1)
    def fault(request):
        if request.url.path == "/time":
            time_reads.append(True)
        ready = (request.url.path == "/balance-allowance" if phase == "m1"
                 else request.url.path == "/time" and len(time_reads) == 2)
        if ready and not failed:
            failed.append(v.time)
            raise httpx.ReadTimeout("offline startup timeout")
        return None
    with boundary(monkeypatch, v, fault):
        result = await run(env)
    assert failed and result.execution_status == "COMPLETED", result.stop_reason
    assert len(v.posts) == 2 and result.stop_elapsed_s == 300


async def test_missing_merge_auth_does_not_abandon_rebalance_or_release_inventory_cost(env):
    v, cfg = env
    type2(env)
    cfg.secrets.builder_key = None
    v.time = START + 60
    def fills(venue, index):
        ids = current_orders(venue)
        if index == 1:
            venue.fill(ids["YES"], "initial", 5)
            venue.books["2"]["bids"][0]["price"] = ".01"
            venue.books["2"]["asks"][0]["price"] = ".02"
        elif index == 2:
            venue.fill(ids["NO"], "minimum-notional", 100)
            venue.time = START + 240
            venue.books["1"]["bids"][0]["price"] = ".16"
            venue.books["1"]["asks"][0]["price"] = ".17"
        elif index == 3:
            venue.fill(ids["YES"], "repair", 95)
    on_windows(v, fills, stop_after=3)
    result = await run(env)
    assert result.stop_elapsed_s == 300 and result.execution_status == "BLOCKED"
    assert result.stop_reason == "MERGE_PREPARE_FAILED:BUILDER_AUTH_MISSING"
    assert len(v.posts) == 4 and result.delta == 0 and result.confirmed_yes == result.confirmed_no == 100
    assert not v.merge_submissions and result.merged_pairs == 0
    assert v.executor.committed() > 18 and result.final_close_barrier == "SAFE_TO_CONTINUE"
    assert len([r for r in journal_records(v.executor.journal.path) if r["kind"] == "sm_trade"]) > 0


async def test_complete_recovered_session_preserves_journal_and_confirmed_fills(env):
    # Regression for single-order explicit rejection after an earlier NO fill.
    v, _ = env
    v.time = START + 60
    def fills(venue, index):
        if index == 1:
            venue.fill(current_orders(venue)["NO"], "no", 5)
            venue.replies = [{"success": False, "errorMsg": "temporarily rejected"}]
        elif index == 3:
            venue.fill(current_orders(venue)["YES"], "repair", 5)
    on_windows(v, fills, stop_after=3)
    result = await run(env)
    assert result.execution_status == "COMPLETED" and result.merged_pairs == 5
    assert result.confirmed_fills == 2 and len(v.posts) == 4
    assert result.windows[1]["result"]["placement_results"][0]["outcome"] == "REJECTED"
    assert json.dumps(result.to_dict(), allow_nan=False)
