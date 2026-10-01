"""Reviewer regressions for attribution, venue minimum and paired economics."""

import time
from dataclasses import replace

import pytest

from polymaker.domain import Quote, Side
from polymaker.marketdata.orderbook import BookView
from polymaker.strategy.favorite_first import (
    FavoriteFirstConfig,
    StrategyMemory,
    StrategySnapshot,
    StrategyState,
    evaluate,
)
from tests.test_engine import _engine_with_market

CFG = FavoriteFirstConfig(0.20, 2, 3, 1.0, 0.2)


def book(bid, ask):
    return BookView(bid, 10, ask, 10, None, None, 10, 10)


def paired_snapshot(*, now=50, no_qty=0, no_vwap=0, no_book=None):
    return StrategySnapshot(
        now,
        0,
        book(0.68, 0.69),
        no_book or book(0.29, 0.30),
        5,
        no_qty,
        0.62,
        no_vwap,
        "yes",
        "no",
        0.01,
    )


@pytest.mark.parametrize("side", ["YES", "NO"])
def test_baseline_position_is_rejected_at_engine_attachment(tmp_path, meta, side):
    eng = _engine_with_market(tmp_path, meta)
    token = meta.yes.token_id if side == "YES" else meta.no.token_id
    eng.state.set_position(token, 1, 0.42)
    try:
        with pytest.raises(ValueError, match="flat market"):
            eng.attach_favorite_first(meta.condition_id, time.time() - 30, CFG)
    finally:
        eng.gateway.close()
        eng.state.close()
        eng.catalog.close()


def test_account_position_alone_must_not_establish_strategy_first_leg():
    s = paired_snapshot(now=40)
    d = evaluate(s, StrategyMemory(), CFG)
    assert d.memory.first_leg_side is None and d.quote is None


async def test_below_venue_minimum_pair_order_is_rejected_locally(tmp_path, meta):
    eng = _engine_with_market(tmp_path, replace(meta, min_order_size=5))
    try:
        q = Quote(meta.no.token_id, Side.BUY, 0.35, 2)
        placed = await eng.gateway.place([q], eng.metas[meta.condition_id], force_post_only=True)
        assert placed == []
    finally:
        eng.gateway.close()
        eng.state.close()
        eng.catalog.close()


def test_partial_second_fill_preserves_total_cycle_pnl():
    memory = StrategyMemory(
        state=StrategyState.RUNNER,
        first_leg_side="YES",
        first_fill_ts=40,
        first_leg_final_qty=5,
        peak_locked_pnl=0.40,
    )
    s = paired_snapshot(no_qty=2, no_vwap=0.30, no_book=book(0.32, 0.33))
    d = evaluate(s, memory, CFG)
    assert d.paired_qty == 2 and d.unpaired_qty == 3
    assert d.current_locked_pnl == pytest.approx(0.34)
    assert d.memory.state == StrategyState.RUNNER
    assert d.quote is not None and d.quote.size == 3


def test_full_passive_pair_completes_and_partial_forced_uses_residual():
    m = StrategyMemory(
        state=StrategyState.RUNNER, first_leg_side="YES", first_fill_ts=40, peak_locked_pnl=0.40
    )
    complete = evaluate(paired_snapshot(no_qty=5, no_vwap=0.30), m, CFG)
    assert complete.memory.state == StrategyState.COMPLETE and complete.quote is None
    forced = evaluate(paired_snapshot(now=240, no_qty=2, no_vwap=0.30), m, CFG)
    assert forced.memory.state == StrategyState.FORCED_PAIR
    assert forced.quote is not None and forced.quote.size == 3
    assert evaluate(paired_snapshot(now=270, no_qty=2), m, CFG).quote is None


def test_partial_pair_adverse_move_never_duplicates_opposite_quantity():
    m = StrategyMemory(
        state=StrategyState.RUNNER, first_leg_side="YES", first_fill_ts=40, peak_locked_pnl=0.40
    )
    s = paired_snapshot(no_qty=2, no_vwap=0.30, no_book=book(0.39, 0.40))
    d = evaluate(s, m, CFG)
    assert d.paired_qty == 2 and d.unpaired_qty == 3
    assert d.memory.state == StrategyState.PAIR_STOP
    assert d.quote is not None and d.quote.size == 3


@pytest.mark.parametrize(
    "opposite,tick",
    [
        (book(0.20, None), 0.01),
        (book(0.30, 0.29), 0.01),
        (book(0.00, 0.01), 0.01),
        (book(0.01, 0.02), 0.0),
    ],
)
def test_invalid_aggressive_price_fails_closed(opposite, tick):
    s = replace(paired_snapshot(), no=opposite, tick=tick)
    m = StrategyMemory(state=StrategyState.RUNNER, first_leg_side="YES", first_fill_ts=40)
    assert evaluate(s, m, CFG).quote is None


def test_passive_offset_below_price_floor_fails_closed():
    s = paired_snapshot(no_book=book(0.01, 0.02))
    m = StrategyMemory(state=StrategyState.RUNNER, first_leg_side="YES", first_fill_ts=40)
    assert evaluate(s, m, CFG).quote is None


def test_fill_generation_invalidates_pending_submit(tmp_path, meta):
    eng = _engine_with_market(tmp_path, meta)
    try:
        generation = eng._fill_truth_generation.get(meta.condition_id, 0)
        assert eng._can_submit_market(
            eng._market_connectivity_generation, meta.condition_id, generation
        )
        eng._invalidate_fill_truth(meta.condition_id)
        assert not eng._can_submit_market(
            eng._market_connectivity_generation, meta.condition_id, generation
        )
    finally:
        eng.gateway.close()
        eng.state.close()
        eng.catalog.close()
