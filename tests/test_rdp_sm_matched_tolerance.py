"""MATCHED remainder <=0.01 shares; actual fills and unresolved lifecycle stay intact."""

from decimal import Decimal

import pytest

from polymaker.rdp_sm_entry import run_current_market
from polymaker.rdp_sm_execution import _terminal
from polymaker.userstream.parse import normalize_order
from test_rdp_sm_m1 import CID, START
from test_rdp_sm_m2_execution import open_executor, quotes
from test_rdp_sm_m3_loop import current_orders, on_windows, runner
from test_rdp_sm_m3_loop import env as loop_env


@pytest.fixture
def env(monkeypatch, tmp_path):
    yield from loop_env.__wrapped__(monkeypatch, tmp_path)


def cancel_rows(venue):
    for row in venue.rows.values():
        row["status"] = "CANCELED"


@pytest.mark.parametrize("status,matched,terminal", [
    ("MATCHED", "5", True), ("MATCHED", "4.99410", True),
    ("MATCHED", "4.99001", True), ("MATCHED", "4.99", True),
    ("MATCHED", "4.98999", False), ("MATCHED", "4.9841", False), ("MATCHED", "4", False),
    ("LIVE", "4.99410", False), ("UNKNOWN", "4.99410", False),
    ("CANCELED", "0", True),
])
def test_terminal_uses_inclusive_point_zero_one(status, matched, terminal):
    row = {"id": "test", "asset_id": "1", "market": CID, "side": "BUY",
           "price": ".49", "original_size": "5", "size_matched": matched,
           "status": status, "associate_trades": []}
    assert _terminal(normalize_order(row)) is terminal


async def test_dust_matched_waits_for_confirmed_without_inventing_remaining_fill(env):
    v, executor = await open_executor(env)
    v.cancel_terminal = False
    def cancel(venue):
        cancel_rows(venue)
        venue.fill("o-1", "near-full", 4.9941, "MINED")
        venue.rows["o-1"]["status"] = "MATCHED"
    v.on_cancel = cancel
    pending = []
    async def settle(venue):
        if not venue.trades:
            return
        pending.append((len(executor.confirmed()), executor.safe_to_continue, executor.committed()))
        if venue.mono >= 7:
            venue.trades[0]["status"] = "CONFIRMED"
    v.on_sleep = settle
    result = await executor.execute_window("w1", quotes())
    assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
    assert pending == [(0, False, Decimal("2.45")), (0, False, Decimal("2.45"))]
    assert result.yes_inventory == 4.9941 and result.no_inventory == 0
    assert result.capital_committed == pytest.approx(4.9941 * .49)
    assert len(result.confirmed_fills) == 1 and result.confirmed_fills[0]["size"] == 4.9941
    assert result.fill_deadline_monotonic - result.t0_monotonic == 3
    assert result.close_monotonic == 7 and len(v.post_requests) == 1
    # Even below the dust threshold, a later LIVE state still invalidates the proof.
    v.rows["o-1"]["status"] = "LIVE"
    assert not await executor.verify_close()
    assert executor.fatal == "TERMINAL_LIVE_CONTRADICTION"


@pytest.mark.parametrize("fault", ["boundary", "live", "missing_trade", "fill_mismatch"])
async def test_tolerance_cannot_bypass_terminal_or_trade_evidence(env, fault):
    v, executor = await open_executor(env)
    v.cancel_terminal = False
    def cancel(venue):
        cancel_rows(venue)
        venue.fill("o-1", "observed", 4.98999 if fault == "boundary" else
                   4.98 if fault == "fill_mismatch" else 4.9941)
        venue.rows["o-1"]["status"] = "LIVE" if fault == "live" else "MATCHED"
        if fault == "missing_trade":
            venue.rows["o-1"]["associate_trades"].append("unrecovered")
        elif fault == "fill_mismatch":
            venue.rows["o-1"]["size_matched"] = "5"
    v.on_cancel = cancel
    result = await executor.execute_window("w1", quotes())
    assert result.close_barrier == "BLOCKED"
    assert result.capital_committed == 2.45 and len(v.post_requests) == 1
    assert ("ORDER_NOT_TERMINAL" if fault in {"boundary", "live"} else "FILLTRUTH_NOT_COMPLETE") in result.block_reason


async def test_dust_mined_timeout_remains_blocked_with_full_commitment(env):
    v, executor = await open_executor(env)
    v.cancel_terminal = False
    def cancel(venue):
        cancel_rows(venue)
        venue.fill("o-1", "pending", 4.9941, "MINED")
        venue.rows["o-1"]["status"] = "MATCHED"
    v.on_cancel = cancel
    result = await executor.execute_window("w1", quotes())
    assert result.close_barrier == "BLOCKED" and "TRADE_CONFIRMATION_UNRESOLVED" in result.block_reason
    assert result.yes_inventory == result.no_inventory == 0
    assert result.capital_committed == 2.45 and len(v.post_requests) == 1
    assert v.time == START + 315  # Hard market end plus the existing close buffer.


async def test_actual_fractional_fill_rebalance_closes_and_next_normal_uses_legal_sizes(env):
    v, _ = env
    v.cancel_terminal = False
    def fills(venue, index):
        cancel_rows(venue)
        ids = current_orders(venue)
        if index == 1:
            venue.fill(ids["NO"], "initial-no", 5)
        elif index == 2:
            oid = ids["YES"]
            venue.fill(oid, "repair-yes", 4.9941)
            venue.rows[oid]["status"] = "MATCHED"
    on_windows(v, fills, stop_after=3)
    result = await runner(env).run(offline=True)
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert [w["state"] for w in result.windows] == ["NORMAL", "REBALANCE", "NORMAL"]
    pair = result.windows[2]["result"]["requested_quotes"]
    assert {q["outcome"]: q["size"] for q in pair} == {"YES": 5.01, "NO": 5}
    assert result.confirmed_yes == 4.9941 and result.confirmed_no == 5
    assert result.merged_pairs == 0
    assert result.delta == pytest.approx(-.0059)
    assert result.final_close_barrier == "SAFE_TO_CONTINUE"
    assert [len(p) for p in v.post_requests] == [2, 1, 2]
    signed_yes = next(p["order"] for p in v.post_requests[2] if p["order"]["tokenId"] == "1")
    assert Decimal(signed_yes["takerAmount"]) == Decimal("5.01") * 1_000_000


async def test_production_mixed_confirmed_mined_dust_waits_then_resumes_normal(env):
    v, cfg = env
    v.cancel_terminal = False
    v.books["1"]["bids"][0]["price"] = ".23"
    v.books["1"]["asks"][0]["price"] = ".24"
    v.books["2"]["bids"][0]["price"] = ".72"
    v.books["2"]["asks"][0]["price"] = ".73"
    v.ticks["2"] = v.books["2"]["tick_size"] = ".01"
    born = [None]
    pending = []
    def fills(venue, index):
        cancel_rows(venue)
        ids = current_orders(venue)
        if index == 1:
            venue.fill(ids["NO"], "initial-no", 5)
            venue.books["1"]["bids"][0]["price"] = ".22"
            venue.books["1"]["asks"][0]["price"] = ".23"
        elif index == 2:
            for n, (qty, status) in enumerate(((2.4, "CONFIRMED"), (.01, "CONFIRMED"),
                                             (1.28705, "MINED"), (1.28705, "MINED"), (.01, "MINED"))):
                trade = venue.fill(ids["YES"], f"repair-{n}", qty, status)
                trade["trader_side"] = "MAKER"
                trade["maker_orders"][0]["maker_address"] = venue.funder
            venue.rows[ids["YES"]]["status"] = "MATCHED"
            born[0] = venue.mono
    on_windows(v, fills, stop_after=3)
    async def settle(venue):
        if born[0] is not None and venue.mono >= born[0] + 2:
            pending.append((sum((Decimal(str(f.size)) for f in venue.executor.confirmed()
                                 if f.token_id == "1"), Decimal(0)),
                            venue.executor.safe_to_continue, venue.executor.committed()))
            for trade in venue.trades:
                trade["status"] = "CONFIRMED"
            venue.positions = [{"token_id": "1", "current_size": 4.9941, "avg_price": .22},
                               {"token_id": "2", "current_size": 5, "avg_price": .71}]
    v.on_sleep = settle
    result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert pending[0] == (Decimal("2.41"), False, Decimal("4.65"))
    assert [w["state"] for w in result.windows] == ["NORMAL", "REBALANCE", "NORMAL"]
    assert [len(p) for p in v.post_requests] == [2, 1, 2]
    assert result.confirmed_yes == 0 and result.confirmed_no == pytest.approx(.0059)
    assert result.merged_pairs == 4.9941
    assert result.delta == pytest.approx(-.0059) and result.final_close_barrier == "SAFE_TO_CONTINUE"
