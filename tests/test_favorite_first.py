"""Favorite-First policy and paper Engine integration; no live orders."""

import asyncio
import time
from dataclasses import replace

import pytest

from polymaker.domain import Side, TradeState
from polymaker.marketdata.orderbook import BookView
from polymaker.state.tracker import OrderEvent, TradeEvent
from polymaker.strategy.favorite_first import (
    FavoriteFirstConfig,
    PairTrigger,
    StrategyMemory,
    StrategySnapshot,
    StrategyState,
    evaluate,
)
from tests.test_engine import _engine_with_market

CFG = FavoriteFirstConfig(0.20, 2, 3, 1.0, 0.2)


def book(bid, ask):
    return BookView(bid, 10, ask, 10, None, None, 10, 10)


def snap(t=30, *, yes=(0.62, 0.63), no=(0.37, 0.38), **kwargs):
    return StrategySnapshot(
        t, 0, book(*yes), book(*no), 0, 0, 0, 0, "yes", "no", 0.01, venue_min_order_size=5, **kwargs
    )


def first(t, qty, *, first_ts=40, working=0, working_price=0.62, finalized=False, no=(0.37, 0.38)):
    return replace(
        snap(t, no=no),
        yes_qty=qty,
        yes_vwap=0.62,
        first_fill_ts=first_ts,
        first_leg_side="YES",
        first_leg_working_remaining=working,
        first_leg_working_price=working_price if working else None,
        first_leg_finalized=finalized,
    )


def pair(t=70, *, no_qty=0, no_vwap=0, no=(0.32, 0.33), working=0, working_price=None, minimum=5):
    return replace(
        first(t, 5, finalized=True, no=no),
        no_qty=no_qty,
        no_vwap=no_vwap,
        pair_working_remaining=working,
        pair_working_price=working_price,
        venue_min_order_size=minimum,
    )


@pytest.mark.parametrize("t,expected", [(29.9, False), (30, True), (179, True), (180, False)])
def test_entry_time(t, expected):
    m = StrategyMemory(confirming_side="YES", confirming_since=28)
    assert (evaluate(snap(t), m, CFG).quote is not None) is expected


@pytest.mark.parametrize(
    "mid,expected", [(0.59, False), (0.60, True), (0.65, True), (0.70, True), (0.71, False)]
)
@pytest.mark.parametrize("side", ["YES", "NO"])
def test_favorite_zone_both_sides(mid, expected, side):
    favored = (round(mid - 0.005, 3), round(mid + 0.005, 3))
    other = (round(1 - mid - 0.005, 3), round(1 - mid + 0.005, 3))
    s = snap(32, yes=favored if side == "YES" else other, no=favored if side == "NO" else other)
    m = StrategyMemory(confirming_side=side, confirming_since=30)
    assert (evaluate(s, m, CFG).quote is not None) is expected


def test_confirmation_flip_zone_exit_and_dynamic_first_quote():
    d = evaluate(snap(30), StrategyMemory(), CFG)
    assert d.quote is None and d.memory.state == StrategyState.ENTRY_CONFIRM
    assert evaluate(snap(31.9), d.memory, CFG).quote is None
    d = evaluate(snap(32), d.memory, CFG)
    assert d.quote.price == 0.62
    assert evaluate(snap(33, yes=(0.64, 0.65), no=(0.35, 0.36)), d.memory, CFG).quote.price == 0.64
    assert evaluate(snap(33, yes=(0.72, 0.73), no=(0.27, 0.28)), d.memory, CFG).quote is None
    flipped = evaluate(snap(33, yes=(0.35, 0.36), no=(0.64, 0.65)), d.memory, CFG)
    assert flipped.quote is None and flipped.memory.confirming_since == 33


def test_first_fill_starts_one_clock_and_2_plus_3_is_full_leg():
    partial = evaluate(first(40, 2, working=3), StrategyMemory(), CFG)
    assert partial.memory.state == StrategyState.FIRST_LEG_RISK_WINDOW
    assert partial.memory.first_fill_ts == 40
    assert partial.quote.token_id == "yes" and partial.quote.size == 3
    assert partial.submin_working_remainder
    full = evaluate(first(44, 5), partial.memory, CFG)
    assert full.memory.first_fill_ts == 40
    assert full.memory.state == StrategyState.FIRST_LEG_RISK_WINDOW
    assert full.quote is None
    before = evaluate(first(59.9, 5), full.memory, CFG)
    assert before.quote is None and before.memory.state == StrategyState.FIRST_LEG_RISK_WINDOW
    pending = evaluate(first(60, 5), before.memory, CFG)
    assert pending.first_leg_finalize_needed and pending.quote is None
    decided = evaluate(first(60, 5, finalized=True, no=(0.35, 0.36)), pending.memory, CFG)
    assert decided.memory.first_leg_final_qty == 5
    assert decided.memory.state == StrategyState.RUNNER
    assert decided.quote.token_id == "no" and decided.quote.size == 5


def test_t180_does_not_end_existing_risk_window():
    d = evaluate(first(190, 5, first_ts=179, working=0), StrategyMemory(), CFG)
    assert d.memory.state == StrategyState.FIRST_LEG_RISK_WINDOW
    assert not d.first_leg_finalize_needed and d.quote is None


def test_terminal_two_share_first_leg_is_unpairable():
    d = evaluate(first(60, 2, first_ts=40, working=3), StrategyMemory(), CFG)
    assert d.first_leg_finalize_needed and d.quote is None
    final = evaluate(first(60, 2, first_ts=40, finalized=True), d.memory, CFG)
    assert final.memory.state == StrategyState.UNPAIRABLE_RESIDUAL
    assert final.memory.first_leg_final_qty == 2
    assert final.quote is None


def test_emergency_cancels_first_and_uses_final_cancel_race_qty():
    bad = first(45, 2, no=(0.49, 0.50), working=3)
    d = evaluate(bad, StrategyMemory(), CFG)
    assert d.first_leg_finalize_needed
    assert d.memory.pair_trigger == PairTrigger.EMERGENCY_STOP
    assert d.quote is None
    under_min = evaluate(
        replace(bad, first_leg_working_remaining=0, first_leg_finalized=True), d.memory, CFG
    )
    assert under_min.memory.state == StrategyState.UNPAIRABLE_RESIDUAL
    race_full = evaluate(
        replace(bad, yes_qty=5, first_leg_working_remaining=0, first_leg_finalized=True),
        d.memory,
        CFG,
    )
    assert race_full.memory.state == StrategyState.PAIR_STOP
    assert race_full.quote.token_id == "no" and race_full.quote.size == 5


def test_favorite_invalidation_after_fill_keeps_risk_clock():
    s = first(44, 2, working=3)
    d = evaluate(replace(s, yes=book(0.72, 0.73)), StrategyMemory(), CFG)
    assert d.quote is None and d.memory.first_fill_ts == 40
    assert d.memory.state == StrategyState.FIRST_LEG_RISK_WINDOW
    assert evaluate(
        replace(s, now=60, first_leg_working_remaining=0), d.memory, CFG
    ).first_leg_finalize_needed


def test_total_lockable_pnl_and_no_false_trailing_after_pair_fill():
    m = StrategyMemory(
        state=StrategyState.RUNNER,
        first_leg_side="YES",
        first_fill_ts=40,
        first_leg_final_qty=5,
        peak_locked_pnl=0.40,
    )
    d = evaluate(pair(no_qty=2, no_vwap=0.30, minimum=0), m, CFG)
    assert d.paired_qty == 2 and d.unpaired_qty == 3
    assert d.paired_locked_pnl == pytest.approx(0.16)
    assert d.residual_lockable_pnl == pytest.approx(0.18)
    assert d.current_locked_pnl == pytest.approx(0.34)
    assert d.trailing_floor == pytest.approx(0.20)
    assert d.memory.state == StrategyState.RUNNER
    assert d.quote.size == 3


def test_second_leg_submin_working_remainder_survives_stop_and_forced():
    m = StrategyMemory(
        state=StrategyState.RUNNER,
        first_leg_side="YES",
        first_fill_ts=40,
        first_leg_final_qty=5,
        peak_locked_pnl=0.40,
    )
    s = pair(no_qty=2, no_vwap=0.30, working=3, working_price=0.28, no=(0.39, 0.40))
    stopped = evaluate(s, m, CFG)
    assert stopped.memory.state == StrategyState.PAIR_STOP
    assert stopped.submin_working_remainder
    assert stopped.quote.price == 0.28 and stopped.quote.size == 3
    forced = evaluate(replace(s, now=240), stopped.memory, CFG)
    assert forced.memory.state == StrategyState.FORCED_PAIR
    assert forced.quote.price == 0.28 and forced.submin_working_remainder
    assert evaluate(replace(s, now=270), forced.memory, CFG).quote is None


def test_pair_2_plus_3_completes_and_terminal_residual_fails_closed():
    m = StrategyMemory(
        state=StrategyState.RUNNER, first_leg_side="YES", first_fill_ts=40, first_leg_final_qty=5
    )
    partial = evaluate(pair(no_qty=2, no_vwap=0.30, working=3, working_price=0.30), m, CFG)
    assert partial.quote is not None and partial.submin_working_remainder
    complete = evaluate(pair(no_qty=5, no_vwap=0.30), partial.memory, CFG)
    assert complete.memory.state == StrategyState.COMPLETE and complete.quote is None
    terminal = evaluate(pair(no_qty=2, no_vwap=0.30), m, CFG)
    assert terminal.pair_finalize_needed and terminal.quote is None
    residual = evaluate(
        replace(pair(no_qty=2, no_vwap=0.30), pair_terminal_authoritative=True),
        terminal.memory,
        CFG,
    )
    assert residual.memory.state == StrategyState.UNPAIRABLE_RESIDUAL


def _paper_engine(tmp_path, meta, *, elapsed=32):
    eng = _engine_with_market(tmp_path, meta)
    cid = meta.condition_id
    now = time.time()
    eng.attach_favorite_first(cid, now - elapsed, CFG)
    start, config, _ = eng._favorite[cid]
    eng._favorite[cid] = (
        start,
        config,
        StrategyMemory(confirming_side="YES", confirming_since=now - 3),
    )
    eng.md.book(meta.yes.token_id).apply_snapshot([(0.62, 100)], [(0.63, 100)], now)
    eng.md.book(meta.no.token_id).apply_snapshot([(0.37, 100)], [(0.38, 100)], now)
    return eng


def _fill(eng, meta, order_id, token, qty, trade_id, ts, price):
    eng.user_proc.on_trade(
        TradeEvent(
            token,
            Side.BUY,
            price,
            qty,
            trade_id,
            TradeState.MATCHED,
            ts,
            order_id,
            meta.condition_id,
        ),
        meta.condition_id,
    )


async def test_engine_first_order_two_plus_three_keeps_original_until_full(tmp_path, meta):
    eng = _paper_engine(tmp_path, meta)
    cid = meta.condition_id
    try:
        await eng._recompute(cid)
        order = eng.state.orders_for(meta.yes.token_id)[0]
        first_ts = time.time() - 4
        _fill(eng, meta, order.order_id, meta.yes.token_id, 2, "f2", first_ts, 0.61)
        _fill(eng, meta, order.order_id, meta.yes.token_id, 2, "f2", first_ts, 0.61)
        assert eng._favorite_economics(cid)["first_qty"] == 2
        eng.user_proc.on_order(
            OrderEvent(
                order.order_id,
                meta.yes.token_id,
                Side.BUY,
                0.62,
                3,
                original_size=5,
                size_matched=2,
            ),
            cid,
        )
        eng.md.book(meta.yes.token_id).apply_snapshot([(0.64, 100)], [(0.65, 100)], time.time())
        await eng._recompute(cid)
        kept = eng.state.orders_for(meta.yes.token_id)[0]
        assert kept.order_id == order.order_id and kept.price == 0.62 and kept.size == 3
        assert not eng.state.orders_for(meta.no.token_id)
        _fill(eng, meta, order.order_id, meta.yes.token_id, 3, "f3", time.time(), 0.62)
        eng.user_proc.on_order(
            OrderEvent(
                order.order_id,
                meta.yes.token_id,
                Side.BUY,
                0.62,
                0,
                original_size=5,
                size_matched=5,
            ),
            cid,
        )
        await eng._recompute(cid)
        assert eng._favorite[cid][2].first_fill_ts == first_ts
        assert eng._favorite[cid][2].state == StrategyState.FIRST_LEG_RISK_WINDOW
        assert not eng.state.orders_for(meta.no.token_id)
    finally:
        eng.gateway.close()
        eng.state.close()
        eng.catalog.close()


async def test_engine_deadline_cancel_then_unpairable_without_illegal_pair(tmp_path, meta):
    eng = _paper_engine(tmp_path, meta)
    cid = meta.condition_id
    try:
        await eng._recompute(cid)
        order = eng.state.orders_for(meta.yes.token_id)[0]
        _fill(eng, meta, order.order_id, meta.yes.token_id, 2, "early", time.time() - 21, 0.61)
        eng.user_proc.on_order(
            OrderEvent(
                order.order_id,
                meta.yes.token_id,
                Side.BUY,
                0.62,
                3,
                original_size=5,
                size_matched=2,
            ),
            cid,
        )
        await eng._recompute(cid)
        assert not eng.state.orders_for(meta.yes.token_id)
        assert cid in eng._favorite_first_finalized
        await eng._recompute(cid)
        assert eng._favorite[cid][2].state == StrategyState.UNPAIRABLE_RESIDUAL
        assert not eng.state.orders_for(meta.no.token_id)
    finally:
        eng.gateway.close()
        eng.state.close()
        eng.catalog.close()


async def test_engine_blind_cancels_submin_pair_remainder(tmp_path, meta):
    eng = _paper_engine(tmp_path, meta)
    cid = meta.condition_id
    try:
        await eng._recompute(cid)
        first_order = eng.state.orders_for(meta.yes.token_id)[0]
        _fill(
            eng, meta, first_order.order_id, meta.yes.token_id, 5, "first", time.time() - 21, 0.62
        )
        eng.user_proc.on_order(
            OrderEvent(
                first_order.order_id,
                meta.yes.token_id,
                Side.BUY,
                0.62,
                0,
                original_size=5,
                size_matched=5,
            ),
            cid,
        )
        await eng._recompute(cid)
        await eng._recompute(cid)
        pair_order = eng.state.orders_for(meta.no.token_id)[0]
        _fill(
            eng,
            meta,
            pair_order.order_id,
            meta.no.token_id,
            2,
            "pair2",
            time.time(),
            pair_order.price,
        )
        eng.user_proc.on_order(
            OrderEvent(
                pair_order.order_id,
                meta.no.token_id,
                Side.BUY,
                pair_order.price,
                3,
                original_size=5,
                size_matched=2,
            ),
            cid,
        )
        await eng._recompute(cid)
        assert eng.state.orders_for(meta.no.token_id)[0].order_id == pair_order.order_id
        eng._on_market_ws_connection(True)
        eng._on_market_ws_connection(False)
        eng.md.connected = False
        await eng._recompute(cid)
        assert not eng.state.orders_for(meta.no.token_id)
        eng.md.connected = True
        eng._market_recovery_pending = False  # recovery gate tested separately in M2.5
        await eng._recompute(cid)
        await eng._recompute(cid)
        assert eng._favorite[cid][2].state == StrategyState.UNPAIRABLE_RESIDUAL
    finally:
        eng.gateway.close()
        eng.state.close()
        eng.catalog.close()


async def test_engine_pair_two_plus_three_finishes_without_repost(tmp_path, meta):
    eng = _paper_engine(tmp_path, meta)
    cid = meta.condition_id
    try:
        await eng._recompute(cid)
        first_order = eng.state.orders_for(meta.yes.token_id)[0]
        _fill(
            eng, meta, first_order.order_id, meta.yes.token_id, 5, "first", time.time() - 21, 0.62
        )
        eng.user_proc.on_order(
            OrderEvent(
                first_order.order_id,
                meta.yes.token_id,
                Side.BUY,
                0.62,
                0,
                original_size=5,
                size_matched=5,
            ),
            cid,
        )
        await eng._recompute(cid)
        await eng._recompute(cid)
        pair_order = eng.state.orders_for(meta.no.token_id)[0]
        _fill(
            eng,
            meta,
            pair_order.order_id,
            meta.no.token_id,
            2,
            "pair2",
            time.time(),
            pair_order.price,
        )
        eng.user_proc.on_order(
            OrderEvent(
                pair_order.order_id,
                meta.no.token_id,
                Side.BUY,
                pair_order.price,
                3,
                original_size=5,
                size_matched=2,
            ),
            cid,
        )
        eng.md.book(meta.no.token_id).apply_snapshot([(0.33, 100)], [(0.34, 100)], time.time())
        await eng._recompute(cid)
        assert eng.state.orders_for(meta.no.token_id)[0].order_id == pair_order.order_id
        _fill(
            eng,
            meta,
            pair_order.order_id,
            meta.no.token_id,
            3,
            "pair3",
            time.time(),
            pair_order.price,
        )
        eng.user_proc.on_order(
            OrderEvent(
                pair_order.order_id,
                meta.no.token_id,
                Side.BUY,
                pair_order.price,
                0,
                original_size=5,
                size_matched=5,
            ),
            cid,
        )
        await eng._recompute(cid)
        assert eng._favorite[cid][2].state == StrategyState.COMPLETE
        assert not eng.state.orders_for(meta.no.token_id)
        assert eng._favorite_economics(cid)["pair_qty"] == 5
    finally:
        eng.gateway.close()
        eng.state.close()
        eng.catalog.close()


async def test_engine_reprices_legal_first_remainder_as_first_leg(tmp_path, meta):
    meta = replace(meta, min_order_size=2)
    eng = _paper_engine(tmp_path, meta)
    cid = meta.condition_id
    try:
        await eng._recompute(cid)
        original = eng.state.orders_for(meta.yes.token_id)[0]
        _fill(eng, meta, original.order_id, meta.yes.token_id, 1, "one", time.time() - 1, 0.62)
        eng.user_proc.on_order(
            OrderEvent(
                original.order_id,
                meta.yes.token_id,
                Side.BUY,
                0.62,
                4,
                original_size=5,
                size_matched=1,
            ),
            cid,
        )
        eng.md.book(meta.yes.token_id).apply_snapshot([(0.64, 100)], [(0.65, 100)], time.time())
        eng._favorite_last_reprice[cid] = time.time() - 2
        await eng._recompute(cid)
        assert not eng.state.orders_for(meta.yes.token_id)
        await eng._recompute(cid)
        replacement = eng.state.orders_for(meta.yes.token_id)[0]
        assert replacement.order_id != original.order_id
        assert replacement.size == 4 and replacement.price == 0.64
        assert replacement.order_id in eng._favorite_first_orders[cid]
        assert not eng._favorite_pair_orders[cid]
    finally:
        eng.gateway.close()
        eng.state.close()
        eng.catalog.close()


async def test_engine_deadline_cancel_race_uses_late_owned_fill(tmp_path, meta):
    eng = _paper_engine(tmp_path, meta)
    cid = meta.condition_id
    try:
        await eng._recompute(cid)
        order = eng.state.orders_for(meta.yes.token_id)[0]
        _fill(eng, meta, order.order_id, meta.yes.token_id, 2, "early", time.time() - 21, 0.61)
        eng.user_proc.on_order(
            OrderEvent(
                order.order_id,
                meta.yes.token_id,
                Side.BUY,
                0.62,
                3,
                original_size=5,
                size_matched=2,
            ),
            cid,
        )
        original_cancel = eng.gateway.cancel

        async def fill_during_cancel(ids):
            _fill(eng, meta, order.order_id, meta.yes.token_id, 1, "late", time.time(), 0.62)
            return await original_cancel(ids)

        eng.gateway.cancel = fill_during_cancel
        await eng._recompute(cid)
        await eng._recompute(cid)
        assert eng._favorite[cid][2].first_leg_final_qty == 3
        assert eng._favorite[cid][2].state == StrategyState.UNPAIRABLE_RESIDUAL
        assert eng._favorite_economics(cid)["first_vwap"] == pytest.approx((2 * 0.61 + 0.62) / 3)
        assert not eng.state.orders_for(meta.no.token_id)
    finally:
        eng.gateway.close()
        eng.state.close()
        eng.catalog.close()


async def test_external_position_never_creates_strategy_fill_or_cost(tmp_path, meta):
    eng = _paper_engine(tmp_path, meta)
    cid = meta.condition_id
    try:
        eng.state.set_position(meta.yes.token_id, 2, 0.99)
        assert eng._favorite_economics(cid)["first_qty"] == 0
        assert eng._favorite_economics(cid)["first_vwap"] == 0
        await eng._recompute(cid)
        assert not eng.state.orders
        assert cid in eng._favorite_mismatch_pending
    finally:
        eng.gateway.close()
        eng.state.close()
        eng.catalog.close()


async def test_persistent_authoritative_position_mismatch_halts(tmp_path, meta):
    eng = _paper_engine(tmp_path, meta)
    cid = meta.condition_id
    eng.state.set_position(meta.yes.token_id, 2, 0.99)
    task = asyncio.create_task(eng._reconcile_loop())
    try:
        await eng._recompute(cid)
        await asyncio.sleep(0.05)
        assert cid in eng._favorite_mismatch_confirmed
        await eng._recompute(cid)
        assert eng._favorite[cid][2].state == StrategyState.HALTED
        assert not eng.state.orders
    finally:
        eng._running = False
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        eng.gateway.close()
        eng.state.close()
        eng.catalog.close()
