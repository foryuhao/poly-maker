"""Minimum BUY notional through actual M1/M3/M2/SDK, with fake HTTP only."""

from dataclasses import replace
from decimal import Decimal

import httpx
import pytest

from polymaker.config import RDPSMConfig
from polymaker.rdp_sm import _market
from polymaker.rdp_sm_entry import run_current_market
from polymaker.rdp_sm_execution import RiskJournal, maker_buy_size
from polymaker.rdp_sm_loop import QuoteUnavailable, make_quotes
from test_rdp_sm_entry import boundary
from test_rdp_sm_m1 import ADDRESS, SLUG, START, Venue
from test_rdp_sm_m2_execution import journal_records, open_executor, quotes
from test_rdp_sm_m3_loop import current_orders, on_windows
from test_rdp_sm_m3_loop import env as loop_env


@pytest.fixture
def env(monkeypatch, tmp_path):
    yield from loop_env.__wrapped__(monkeypatch, tmp_path)


def books(yes, no):
    return {str(i): {"best_bid": float(Decimal(p) + Decimal(".001")),
                     "best_ask": float(Decimal(p) + Decimal(".002")), "tick": .001}
            for i, p in ((1, yes), (2, no))}


@pytest.mark.parametrize("delta,yes,no,allowed", [
    (0, ".19", ".5", False), (0, ".5", ".19", False),
    (0, ".2", ".2", True), (0, ".199", ".5", False),
    (2, ".5", ".15", True), (-2, ".15", ".5", True),
    (2, ".19", ".5", False), (-2, ".5", ".19", False),
])
def test_normal_checks_each_actual_skewed_leg_amount(delta, yes, no, allowed):
    meta, _ = _market(Venue().raw, SLUG)
    inventory = (Decimal(max(delta, 0)), Decimal(max(-delta, 0)))
    if not allowed:
        with pytest.raises(QuoteUnavailable, match="NORMAL_LEG_NOTIONAL_BELOW_MINIMUM"):
            make_quotes(meta, books(yes, no), *inventory, "w", RDPSMConfig(base_size=5, max_order_size=1000))
        return
    state, pair = make_quotes(meta, books(yes, no), *inventory, "w", RDPSMConfig(base_size=5, max_order_size=1000))
    assert state == "NORMAL" and {q.outcome for q in pair} == {"YES", "NO"}
    assert all(Decimal(str(q.price)) * Decimal(str(q.size)) >= 1 for q in pair)
    assert {q.outcome: q.size for q in pair} == {"YES": 5 + max(-delta, 0), "NO": 5 + max(delta, 0)}


@pytest.mark.parametrize("delta,price,size", [
    (5, ".15", "6.67"), (-5, ".15", "6.67"),
    (5, ".2", "5"), (-5, ".2", "5"), (7, ".15", "7"),
    (5, ".1", "10"), (5, ".03", "33.34"), (5, ".01", "100"),
])
def test_rebalance_tops_up_only_short_side_to_smallest_legal_size(delta, price, size):
    meta, _ = _market(Venue().raw, SLUG)
    view = {str(i): {"best_bid": float(price), "best_ask": float(Decimal(price) + Decimal(".001")),
                    "tick": .001} for i in (1, 2)}
    state, single = make_quotes(meta, view, Decimal(max(delta, 0)), Decimal(max(-delta, 0)), "w", RDPSMConfig(base_size=5, max_order_size=1000))
    assert state == "REBALANCE" and len(single) == 1
    q = single[0]
    assert q.outcome == ("NO" if delta > 0 else "YES") and q.side.value == "BUY"
    qty = Decimal(str(q.size))
    assert qty == Decimal(size) and qty * Decimal(price) >= 1
    if qty > abs(delta):
        assert (qty - Decimal(".01")) * Decimal(price) < 1


@pytest.mark.parametrize("gap,price,minimum,expected", [
    ("4.99", ".49", "5", "5.00"),
    ("5", ".49", "5", "5.00"),
    ("5.003207", ".49", "5", "5.01"),
    ("5.010001", ".49", "5", "5.02"),
    ("5.003207", ".15", "5", "6.67"),
    ("5.003207", ".49", "6.001", "6.01"),
])
def test_legal_buy_size_covers_share_minimum_precision_and_notional(gap, price, minimum, expected):
    assert maker_buy_size(Decimal(price), Decimal(gap), Decimal(minimum)) == Decimal(expected)


@pytest.mark.parametrize("sign", [1, -1])
@pytest.mark.parametrize("late", [False, True])
async def test_actual_fractional_gap_submits_rounded_single_maker_and_preserves_inventory(env, sign, late):
    v, cfg = env
    first, second = ("NO", "YES") if sign > 0 else ("YES", "NO")

    def fills(venue, index):
        ids = current_orders(venue)
        if index == 1:
            venue.fill(ids[first], "fractional-first", 4.496793)
        elif index == 2:
            venue.fill(ids[second], "skew-second", 9.5)
            if late:
                venue.time = START + 200
        elif index == 3:
            assert set(ids) == {first}
            venue.fill(ids[first], "rounded-repair", 5.01)

    on_windows(v, fills, stop_after=3)
    result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert [w["state"] for w in result.windows] == ["NORMAL", "NORMAL", "REBALANCE"]
    assert [len(p) for p in v.post_requests] == [2, 2, 1]
    repair = result.windows[2]["result"]
    q = repair["requested_quotes"][0]
    assert q["outcome"] == first and q["size"] == 5.01 and q["side"] == "BUY"
    signed = v.post_requests[2][0]
    assert signed["postOnly"] is True and signed["orderType"] == "GTC"
    assert Decimal(signed["order"]["takerAmount"]) == Decimal("5010000")
    assert result.delta == (-sign) * .006793 and result.residual_shares == .006793
    assert result.confirmed_yes == (0 if sign > 0 else .006793)
    assert result.confirmed_no == (.006793 if sign > 0 else 0)
    assert result.merged_pairs == 9.5 and result.final_close_barrier == "SAFE_TO_CONTINUE"
    assert result.skipped_quotes == []
    assert repair["fill_deadline_monotonic"] - repair["t0_monotonic"] == 3


@pytest.mark.parametrize("size", [4.99, 5.001])
async def test_m2_rejects_below_minimum_or_off_grid_quantity_before_post(env, size):
    v, ex = await open_executor(env)
    result = await ex.execute_window("invalid-size", quotes("invalid-size", size=size))
    assert result.block_reason == "QUOTE_SIZE_INVALID"
    assert result.placement_results == [] and not v.post_requests
    assert result.capital_committed == 0 and result.t0_monotonic is None
    assert result.close_barrier == "BLOCKED" and not ex.safe_to_continue


@pytest.mark.parametrize("final_only", [False, True])
async def test_normal_small_leg_skips_both_at_decision_or_final_admission(env, monkeypatch, final_only):
    v, cfg = env
    reads = [0]
    def lower_no(request):
        if request.url.path == "/book" and request.url.params["token_id"] == "2" and v.executor is not None:
            reads[0] += 1
            if not final_only or reads[0] >= 2:
                v.books["2"]["bids"][0]["price"] = ".16"
                v.books["2"]["asks"][0]["price"] = ".17"
        return None
    async def finish_after_skip(_):
        v.time = START + 300
    v.on_sleep = finish_after_skip
    with boundary(monkeypatch, v, lower_no):
        result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert not v.posts and not v.cancels and result.confirmed_fills == 0
    assert result.skipped_quotes[0]["reason"] == "NORMAL_LEG_NOTIONAL_BELOW_MINIMUM"
    assert result.final_close_barrier == "SAFE_TO_CONTINUE"
    if final_only:
        assert result.windows[0]["result"]["placement_results"] == []
        assert result.windows[0]["result"]["t0_monotonic"] is None
    j = RiskJournal(cfg.paths.journal_dir, ADDRESS)
    assert j.sequence == 0
    j.close()


async def test_normal_skip_retries_with_fresh_eligible_pair(env):
    v, cfg = env
    v.books["2"]["bids"][0]["price"] = ".16"
    v.books["2"]["asks"][0]["price"] = ".17"
    async def recover_book(_):
        v.books["2"]["bids"][0]["price"] = ".30"
        v.books["2"]["asks"][0]["price"] = ".31"
    v.on_sleep = recover_book
    on_windows(v, lambda *_: None, stop_after=1)
    result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert result.skipped_quotes[0]["reason"] == "NORMAL_LEG_NOTIONAL_BELOW_MINIMUM"
    assert len(v.post_requests) == 1 and len(v.post_requests[0]) == 2
    assert all(Decimal(row["order"]["makerAmount"]) >= 1_000_000 for row in v.post_requests[0])
    assert result.final_close_barrier == "SAFE_TO_CONTINUE"


async def test_topped_up_confirmed_fill_recomputes_next_normal_sizes(env):
    v, cfg = env
    def fills(venue, index):
        ids = current_orders(venue)
        if index == 1:
            venue.fill(ids["YES"], "initial", 5)
            venue.books["2"]["bids"][0]["price"] = ".15"
            venue.books["2"]["asks"][0]["price"] = ".16"
        elif index == 2:
            venue.fill(ids["NO"], "repair", 6.67)
            venue.books["2"]["bids"][0]["price"] = ".30"
            venue.books["2"]["asks"][0]["price"] = ".31"
    on_windows(v, fills, stop_after=3)
    result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert [w["state"] for w in result.windows] == ["NORMAL", "REBALANCE", "NORMAL"]
    pair = result.windows[2]["result"]["requested_quotes"]
    assert {q["outcome"]: q["size"] for q in pair} == {"YES": 6.67, "NO": 5}
    assert result.confirmed_yes == 0 and result.confirmed_no == pytest.approx(1.67)
    assert result.merged_pairs == 5
    assert result.delta == pytest.approx(-1.67) and result.final_close_barrier == "SAFE_TO_CONTINUE"


@pytest.mark.parametrize("refresh,capital", [(False, "6000000"), (True, "6000000"), (False, "900000")])
async def test_rebalance_minimum_size_refresh_fill_and_capital_safety(env, monkeypatch, refresh, capital):
    v, cfg = env
    reads = [0]
    def fills(venue, index):
        ids = current_orders(venue)
        if index == 1:
            venue.fill(ids["YES"], "initial", 5)
            venue.books["2"]["bids"][0]["price"] = ".15"
            venue.books["2"]["asks"][0]["price"] = ".16"
            venue.capital["balance"] = capital
        else:
            venue.fill(ids["NO"], "repair", float(venue.rows[ids["NO"]]["original_size"]))
    on_windows(v, fills, stop_after=2)
    def change_price(request):
        if refresh and request.url.path == "/book" and request.url.params["token_id"] == "2" and v.executor is not None and len(v.executor.attempts) == 2:
            reads[0] += 1
            price = ".15" if reads[0] == 1 else ".10"
            v.books["2"]["bids"][0]["price"] = price
            v.books["2"]["asks"][0]["price"] = str(Decimal(price) + Decimal(".01"))
        return None
    with boundary(monkeypatch, v, change_price):
        result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    repair = result.windows[1]["result"]
    assert result.windows[1]["state"] == "REBALANCE"
    if capital == "900000":
        assert result.stop_reason == "OFFICIAL_CAPITAL_INSUFFICIENT"
        assert len(v.post_requests) == 1 and repair["placement_results"] == []
        assert result.confirmed_yes == 5 and result.confirmed_no == 0
    else:
        assert result.execution_status == "COMPLETED", result.stop_reason
        assert [len(p) for p in v.post_requests] == [2, 1]
        q = repair["requested_quotes"][0]
        assert q["outcome"] == "NO" and q["size"] == (10 if refresh else 6.67)
        assert q["price"] == (.1 if refresh else .15)
        assert Decimal(str(q["size"])) * Decimal(str(q["price"])) >= 1
        signed = v.post_requests[1][0]
        assert signed["postOnly"] is True and signed["orderType"] == "GTC"
        assert Decimal(signed["order"]["makerAmount"]) >= 1_000_000
        assert Decimal(signed["order"]["takerAmount"]) == Decimal(str(q["size"])) * 1_000_000
        assert result.confirmed_yes == 0 and result.confirmed_no == pytest.approx(q["size"] - 5)
        assert result.merged_pairs == 5
        assert result.delta == pytest.approx(5 - q["size"])
        assert repair["fill_deadline_monotonic"] - repair["t0_monotonic"] == 3
    assert result.final_close_barrier == "SAFE_TO_CONTINUE"


@pytest.mark.parametrize("fault", [None, "accepted_id", "missing_status", "wrong_amount", "fill_amount"])
async def test_exact_minimum_rejection_does_not_become_false_unknown(env, monkeypatch, fault):
    v, executor = await open_executor(env)
    submitted = [quotes()[0], replace(quotes()[1], price=.15)]
    v.books["2"]["bids"][0]["price"] = ".16"
    v.books["2"]["asks"][0]["price"] = ".17"
    v.replies = [{"success": True, "orderID": "o-1"}, {
        "success": False, "errorMsg": "invalid amount for a marketable BUY order ($0.75), min size: $1",
        "orderID": "", "status": "", "makingAmount": "", "takingAmount": ""}]
    def server_response(request):
        if request.method != "POST":
            return None
        response = v.response(request)
        raw = response.json()
        raw[1]["success"] = True
        if fault == "accepted_id":
            raw[1]["orderID"] = "possibly-accepted"
        elif fault == "missing_status":
            raw[1].pop("status")
        elif fault == "wrong_amount":
            raw[1]["errorMsg"] = raw[1]["errorMsg"].replace("0.75", "0.50")
        elif fault == "fill_amount":
            raw[1]["takingAmount"] = "5"
        return httpx.Response(200, json=raw, request=request)
    with boundary(monkeypatch, v, server_response):
        result = await executor.execute_window("w1", submitted)
    assert v.rows["o-1"]["status"] == "CANCELED" and result.confirmed_fills == []
    assert result.placement_results[1]["outcome"] == ("REJECTED" if fault is None else "UNKNOWN")
    assert result.close_barrier == ("SAFE_TO_CONTINUE" if fault is None else "BLOCKED")
    assert result.capital_committed == (0 if fault is None else .75)
    assert len(v.post_requests) == 1
    assert any(r["kind"] == "sm_official_reconciliation" for r in journal_records(executor.journal.path))
