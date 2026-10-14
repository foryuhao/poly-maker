"""RDP's inclusive numeric tolerance through production Tracker/FillTruth/M2/M3."""

from decimal import Decimal

import pytest

from polymaker.domain import Side, TradeState
from polymaker.rdp_sm_entry import run_current_market
from polymaker.rdp_sm_execution import RDP_TOLERANCE
from polymaker.state.store import StateStore
from polymaker.state.tracker import TradeEvent, UserEventProcessor
from test_rdp_sm_m2_execution import open_executor, quotes
from test_rdp_sm_m3_loop import current_orders, on_windows
from test_rdp_sm_m3_loop import env as loop_env


@pytest.fixture
def env(monkeypatch, tmp_path):
    yield from loop_env.__wrapped__(monkeypatch, tmp_path)


async def test_actual_small_confirmed_price_difference_completes_market_without_losing_fill(env):
    v, cfg = env
    for token, bid in (("1", ".64"), ("2", ".32")):
        v.ticks[token] = v.books[token]["tick_size"] = ".01"
        v.books[token]["bids"][0]["price"] = bid
        v.books[token]["asks"][0]["price"] = str(Decimal(bid) + Decimal(".01"))

    def fills(venue, index):
        ids = current_orders(venue)
        if index == 1:
            venue.fill(ids["NO"], "initial-no", 5)
            venue.books["1"]["bids"][0]["price"] = ".63"
            venue.books["1"]["asks"][0]["price"] = ".64"
        elif index == 2:
            venue.fill(ids["YES"], "large-yes", 4.83)
            small = venue.fill(ids["YES"], "small-yes", .165406)
            small["maker_orders"][0]["price"] = "0.6300013300605782"
            venue.rows[ids["YES"]]["status"] = "MATCHED"

    on_windows(v, fills, stop_after=3)
    result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert result.final_close_barrier == "SAFE_TO_CONTINUE"
    assert [w["state"] for w in result.windows] == ["NORMAL", "REBALANCE", "NORMAL"]
    assert result.confirmed_fills == 3 and result.merged_pairs == 4.995406
    assert result.confirmed_yes == 0 and result.confirmed_no == .004594
    small = next(f for f in v.executor.confirmed() if f.trade_id == "small-yes")
    assert small.size == .165406 and small.price == .6300013300605782
    assert not v.executor.processor.quarantined_fill_keys
    assert [len(p) for p in v.post_requests] == [2, 1, 2]


@pytest.mark.parametrize("kind,offset,accepted", [
    ("fill_price", ".01", True), ("fill_price", ".010001", False),
    ("cumulative", ".01", True), ("cumulative", ".010001", False),
    ("order_price", ".01", True), ("order_price", ".010001", False),
    ("order_size", ".01", True), ("order_size", ".010001", False),
])
async def test_numeric_boundary_through_real_reconciliation_and_close(env, kind, offset, accepted):
    v, ex = await open_executor(env)
    offset = Decimal(offset)

    def fills(venue):
        if venue.trades:
            return
        trade = venue.fill("o-1", "boundary", 5)
        row = venue.rows["o-1"]
        if kind == "fill_price":
            trade["maker_orders"][0]["price"] = str(Decimal(".49") + offset)
        elif kind == "cumulative":
            row["size_matched"] = str(Decimal(5) - offset)
        elif kind == "order_price":
            row["price"] = str(Decimal(".49") + offset)
        else:
            row["original_size"] = str(Decimal(5) + offset)

    v.on_cancel = fills
    result = await ex.execute_window("w1", quotes())
    assert ex.processor.quantity_tolerance == ex.processor.price_tolerance == RDP_TOLERANCE
    assert result.close_barrier == ("SAFE_TO_CONTINUE" if accepted else "BLOCKED")
    if accepted:
        assert result.yes_inventory == 5 and len(ex.confirmed()) == 1
    else:
        assert result.capital_committed == 2.45 and not ex.safe_to_continue


@pytest.mark.parametrize("fault", ["token", "side", "condition"])
async def test_tolerance_never_accepts_foreign_identity(env, fault):
    v, ex = await open_executor(env)

    def fills(venue):
        if venue.trades:
            return
        trade = venue.fill("o-1", "wrong-identity", 5)
        if fault == "condition":
            trade["market"] = "foreign-condition"
        else:
            trade["maker_orders"][0]["asset_id" if fault == "token" else "side"] = (
                "2" if fault == "token" else "SELL")

    v.on_cancel = fills
    result = await ex.execute_window("w1", quotes())
    assert result.close_barrier == "BLOCKED" and result.yes_inventory == 0
    assert result.capital_committed == 2.45


def test_other_strategies_keep_original_price_tolerance():
    store = StateStore(":memory:")
    try:
        proc = UserEventProcessor(store)
        proc.register_order("ours", "condition", "token", Side.BUY, 5, .63)
        proc.on_trade(TradeEvent("token", Side.BUY, .6300013300605782, .165406,
                                "small", TradeState.CONFIRMED, 0, "ours", "condition"),
                      "condition", source="REST")
        assert proc.price_tolerance == proc.quantity_tolerance == Decimal(".000001")
        assert proc.has_unresolved_fill_key("ours") and proc.tracked_trade_qty("ours") == 0
    finally:
        store.close()
