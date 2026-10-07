from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from polymaker.domain import MarketMeta, Side, TokenMeta
from polymaker.marketdata.orderbook import OrderBook
from polymaker.marketdata.parse import TradePrint
from polymaker.marketdata.service import MarketDataService
from polymaker.strategy.rdp_book_filter import RDPBookFilter


def market():
    return MarketMeta("cid", "BTC 5m", "btc-5m", (TokenMeta("yes", "Yes"),
        TokenMeta("no", "No")), .01, False, 5, 0, 0, 0, 0, 0, False, None, None)


def book(q_eff=10, bid=.50, tick=.01, neighbor=None):
    result = OrderBook(tick)
    neighbor_size = 0 if neighbor is None else neighbor
    best_size = q_eff - neighbor_size
    bids = [(bid, best_size)]
    if neighbor is not None and neighbor_size > 0:
        bids.append((round(bid - tick, 8), neighbor_size))
    result.apply_snapshot(bids, [(min(.99, bid + tick), 10)], 100)
    return result


def observe(filt, token, state, at, gen=1, cond="cid"):
    return filt.observe_book(token, state, at, gen, condition_id=cond)


def observe_both(filt, at=0, yes_q=10, no_q=10, yes_bid=.50, no_bid=.50,
                 tick=.01, gen=1):
    observe(filt, "yes", book(yes_q, yes_bid, tick), at, gen)
    observe(filt, "no", book(no_q, no_bid, tick), at, gen)


def eval_filter(filt, now, yes_tick=.01, no_tick=.01, gen=1, reliable=True):
    return filt.evaluate(now, generation=gen,
        ticks={"yes": yes_tick, "no": no_tick}, state_reliable=reliable)


def trade(token, at, *, size=1, price=.5, cond="cid"):
    return TradePrint(token, cond, Side.BUY, price, size, at)


@pytest.mark.parametrize(("step", "expected"), [(6, Decimal("12")), (3, Decimal("6"))])
def test_inactive_uses_cumulative_absolute_reversing_depth_variation(step, expected):
    filt = RDPBookFilter(market())
    observe_both(filt, 0, yes_q=20)
    observe(filt, "yes", book(20 + step), .25, 1)
    observe(filt, "yes", book(20), .50, 1)
    result = eval_filter(filt, 2)
    assert result.ready
    assert result.sides[0].depth_change_2s == expected
    assert result.sides[1].depth_change_2s == Decimal("0")
    assert result.inactive is True  # untouched NO side is inactive


@pytest.mark.parametrize(("change", "inactive"), [
    (Decimal("5"), False), (Decimal("4.999"), True), (Decimal("0"), True),
])
def test_variation_five_boundary_below_and_unchanged_snapshots(change, inactive):
    filt = RDPBookFilter(market())
    observe_both(filt, 0, yes_q=10)
    target_q = 15 if change == Decimal("5") else 14.999 if change == Decimal("4.999") else 10
    observe(filt, "yes", book(target_q), .5, 1)
    observe(filt, "yes", book(target_q), .5, 1)
    assert filt.observe_trade(trade("no", 1.5), 1.5, 1)
    result = eval_filter(filt, 2.1)
    assert result.ready
    assert result.sides[0].depth_change_2s == change
    assert result.sides[0].inactive is inactive
    assert result.rejected is inactive  # the unchanged NO side has no trade


def test_recent_trade_and_exact_expired_trade_boundary():
    filt = RDPBookFilter(market())
    observe_both(filt)
    assert filt.observe_trade(trade("yes", .1), .1, 1)
    assert filt.observe_trade(trade("no", .1001), .1001, 1)
    result = eval_filter(filt, 2.1)
    assert result.ready
    assert result.sides[0].real_trade_in_2s is False  # cutoff is exactly .1
    assert result.sides[1].real_trade_in_2s is True
    assert result.reasons == ("INACTIVE_YES",)


@pytest.mark.parametrize(("event_at", "expected_recent"), [
    (.2999999, False), (.3, False), (.3000001, True),
    (10.0, False), (10.000001, True),
])
def test_decimal_timestamp_trade_window_exact_expiry_and_neighbors(event_at, expected_recent):
    filt = RDPBookFilter(market())
    observe_both(filt, 0)
    assert filt.observe_trade(trade("yes", event_at), event_at, 1)
    now = 12.0 if event_at >= 10 else .3 + 2
    result = eval_filter(filt, now)
    assert result.ready and result.sides[0].real_trade_in_2s is expected_recent


def test_decimal_timestamp_depth_change_expiry_and_one_second_anchor():
    expired = RDPBookFilter(market())
    observe_both(expired, 0, yes_q=10)
    observe(expired, "yes", book(15), .3, 1)
    exact = eval_filter(expired, .3 + 2)
    assert exact.ready and exact.sides[0].depth_change_2s == Decimal("0")
    inside = RDPBookFilter(market())
    observe_both(inside, 0, yes_q=10)
    observe(inside, "yes", book(15), .3000001, 1)
    just_inside = eval_filter(inside, .3 + 2)
    assert just_inside.ready and just_inside.sides[0].depth_change_2s == Decimal("5")

    fast = RDPBookFilter(market())
    observe_both(fast, 0)
    observe(fast, "yes", book(10, .52), 1.299999, 1)
    observe(fast, "yes", book(10, .50), 1.3, 1)
    observe(fast, "yes", book(10, .51), 1.300001, 1)
    anchor_result = eval_filter(fast, .3 + 2)
    assert anchor_result.ready
    # At the exact 1-second boundary, .50 is carried; .52 just before it is
    # excluded from excursion, leaving only a one-tick move.
    assert anchor_result.sides[0].bid_excursion_1s == Decimal("0.01")
    assert anchor_result.sides[0].fast_move is False


def test_token_local_trade_activity_and_either_side_inactive():
    filt = RDPBookFilter(market())
    observe_both(filt)
    assert filt.observe_trade(trade("yes", 1.5), 1.5, 1)
    yes_only = eval_filter(filt, 2.1)
    assert yes_only.inactive is True and yes_only.reasons == ("INACTIVE_NO",)
    assert filt.observe_trade(trade("no", 1.6), 1.6, 1)
    both = eval_filter(filt, 2.1)
    assert both.inactive is False and not both.rejected


def test_q_eff_uses_exact_best_bid_minus_tick_and_missing_level_only():
    filt = RDPBookFilter(market())
    # The second bid is not adjacent to the current tick and must be ignored.
    spaced = OrderBook(.01)
    spaced.apply_snapshot([(.50, 7), (.47, 99)], [(.51, 10)], 1)
    observe(filt, "yes", spaced, 0, 1)
    observe(filt, "no", book(10), 0, 1)
    result = eval_filter(filt, 2)
    assert result.ready and result.sides[0].q_eff == Decimal("7")

    adjacent = OrderBook(.01)
    adjacent.apply_snapshot([(.49, 3), (.50, 7)], [(.51, 10)], 1)
    observe(filt, "yes", adjacent, 2.1, 1)
    assert filt._history["yes"].samples[-1].q_eff == Decimal("10")


@pytest.mark.parametrize(("yes_q", "no_q", "expected"), [
    (60, 15, False),       # ratio exactly four
    (60, 14.99, True),     # ratio strictly above four
    (50, 5, False),        # high depth exactly fifty
    (50.01, 5, True),      # high depth strictly above fifty
    (60, .1, True),        # low depth is floored at five: 60 / 5 = 12
])
def test_depth_extreme_strict_ratio_and_high_depth_boundaries(yes_q, no_q, expected):
    filt = RDPBookFilter(market())
    observe_both(filt, yes_q=yes_q, no_q=no_q)
    result = eval_filter(filt, 2)
    assert result.ready
    assert result.depth_extreme is expected
    assert ("DEPTH_EXTREME" in result.reasons) is expected
    if no_q == .1:
        assert result.depth_ratio == Decimal("12")


@pytest.mark.parametrize(("end_bid", "expected"), [
    (.51, False), (.52, True), (.53, True), (.48, True),
])
def test_fast_move_one_two_more_ticks_and_downward(end_bid, expected):
    filt = RDPBookFilter(market())
    observe_both(filt, 0)
    observe(filt, "yes", book(10, end_bid), 1.5, 1)
    result = eval_filter(filt, 2.1)
    assert result.ready
    expected_excursion = {0.51: Decimal("0.01"), 0.52: Decimal("0.02"),
                          0.53: Decimal("0.03"), 0.48: Decimal("0.02")}[end_bid]
    assert result.sides[0].bid_excursion_1s == expected_excursion
    assert result.sides[0].fast_move is expected


def test_fast_move_catches_reversal_but_not_repeated_one_tick_oscillation():
    reversal = RDPBookFilter(market())
    observe_both(reversal, 0)
    observe(reversal, "yes", book(10, .53), 1.2, 1)
    observe(reversal, "yes", book(10, .50), 1.5, 1)
    assert eval_filter(reversal, 2.1).sides[0].fast_move is True

    oscillation = RDPBookFilter(market())
    observe_both(oscillation, 0)
    for at, bid in ((1.2, .51), (1.3, .50), (1.4, .51), (1.5, .50)):
        observe(oscillation, "yes", book(10, bid), at, 1)
    result = eval_filter(oscillation, 2.1)
    assert result.sides[0].bid_excursion_1s == Decimal("0.01")
    assert result.sides[0].fast_move is False


def test_rolling_boundary_anchor_and_discrete_change_inclusion():
    filt = RDPBookFilter(market())
    observe_both(filt, 0, yes_q=10)
    observe(filt, "yes", book(100), .99, 1)
    observe(filt, "yes", book(12), 1, 1)
    observe(filt, "yes", book(13), 1.5, 1)
    result = eval_filter(filt, 3)
    assert result.ready
    # The update at cutoff=1 is the carried anchor; only 12 -> 13 counts.
    assert result.sides[0].depth_change_2s == Decimal("1")
    assert filt._history["yes"].samples[0].at == 1


def test_initial_warmup_quiet_coverage_and_repeated_evaluation_are_stable():
    filt = RDPBookFilter(market())
    observe_both(filt, 5)
    early = eval_filter(filt, 6.999)
    assert not early.ready and early.rejected and "NOT_READY:WARMUP" in early.reasons
    first = eval_filter(filt, 7)
    again = eval_filter(filt, 7)
    assert first.ready and first.inactive is True
    assert first == again


@pytest.mark.parametrize("bad_time", [float("nan"), float("inf"), -1])
def test_invalid_times_invalidate_history_and_fail_closed(bad_time):
    filt = RDPBookFilter(market())
    observe_both(filt, 0)
    invalid = eval_filter(filt, bad_time)
    assert not invalid.ready and invalid.rejected
    assert invalid.reasons == ("INVALID_EVALUATION_TIME",)
    later = eval_filter(filt, 2)
    assert not later.ready and later.rejected


def test_backward_and_future_evaluation_times_fail_closed_and_reset():
    filt = RDPBookFilter(market())
    observe_both(filt, 0)
    observe(filt, "yes", book(10), 2.2, 1)
    backward = eval_filter(filt, 2.1)
    assert not backward.ready and backward.rejected
    assert "EVALUATION_PRECEDES_EVIDENCE" in backward.reasons
    later = eval_filter(filt, 4.2)
    assert not later.ready and later.rejected


def test_decreasing_evaluation_invalidates_history_but_equal_evaluation_is_allowed():
    filt = RDPBookFilter(market())
    observe_both(filt, 0)
    assert filt.observe_trade(trade("yes", 2.9), 2.9, 1)
    assert filt.observe_trade(trade("no", 2.9), 2.9, 1)
    first = eval_filter(filt, 3)
    assert first.ready and not first.rejected
    repeated = eval_filter(filt, 3)
    assert repeated.ready and not repeated.rejected

    backward = eval_filter(filt, 2.1)
    assert not backward.ready and backward.rejected
    assert backward.reasons == ("EVALUATION_TIME_REVERSED",)
    assert not eval_filter(filt, 4.099).ready
    observe_both(filt, 4.1)
    recovered = eval_filter(filt, 6.1)
    assert recovered.ready


def test_invalid_scoped_book_trade_and_nonmonotone_observation_reset_side():
    filt = RDPBookFilter(market())
    observe_both(filt, 0)
    assert not observe(filt, "yes", book(10), 1, cond="foreign")
    assert not eval_filter(filt, 2).ready
    observe(filt, "yes", book(10), 2, 1)
    assert not filt.observe_trade(trade("yes", 2.1, size=0), 2.1, 1)
    assert not eval_filter(filt, 4.1).ready

    recovered = RDPBookFilter(market())
    observe_both(recovered, 0)
    assert not observe(recovered, "yes", book(10), -0.1)
    assert not eval_filter(recovered, 2).ready


@pytest.mark.parametrize(("tick", "bid"), [(0, .5), (float("nan"), .5), (.01, .505)])
def test_invalid_tick_and_off_grid_best_bid_fail_closed(tick, bid):
    filt = RDPBookFilter(market())
    bad = book(10, bid, tick)
    assert not observe(filt, "yes", bad, 0, 1)
    assert not eval_filter(filt, 2).ready


def test_invalid_depth_values_fail_closed():
    filt = RDPBookFilter(market())
    bad = OrderBook(.01)
    bad.bids[.50] = float("inf")
    assert not observe(filt, "yes", bad, 0, 1)
    assert not eval_filter(filt, 2).ready


def test_generation_reset_invalidates_both_sides():
    filt = RDPBookFilter(market())
    observe_both(filt, 0, gen=1)
    assert eval_filter(filt, 2, gen=1).ready
    observe(filt, "yes", book(10), 2.1, 2)
    reset = eval_filter(filt, 2.1, gen=2)
    assert not reset.ready and "NOT_READY:GENERATION_CHANGED" in reset.reasons
    observe(filt, "no", book(10), 2.2, 2)
    assert not eval_filter(filt, 4.1, gen=2).ready
    observe(filt, "yes", book(10), 4.2, 2)
    assert eval_filter(filt, 6.2, gen=2).ready


def test_tick_change_requires_new_baseline_and_evaluation_detects_no_dirty_change():
    filt = RDPBookFilter(market())
    yes_book, no_book = book(10, tick=.01), book(10, tick=.01)
    observe(filt, "yes", yes_book, 0, 1)
    observe(filt, "no", no_book, 0, 1)
    assert eval_filter(filt, 2).ready

    # No callback is delivered: evaluation's current authoritative tick still
    # invalidates the stale yes history.
    yes_book.set_tick_size(.001)
    changed = eval_filter(filt, 2.1, yes_tick=.001)
    assert not changed.ready and "NOT_READY:TICK_CHANGED" in changed.reasons
    assert observe(filt, "yes", yes_book, 2.2, 1)
    assert not eval_filter(filt, 4.199, yes_tick=.001).ready
    assert eval_filter(filt, 4.2, yes_tick=.001).ready


def test_equal_receipt_times_preserve_consecutive_total_variation():
    filt = RDPBookFilter(market())
    observe_both(filt, 0, yes_q=20)
    observe(filt, "yes", book(26), 1, 1)
    observe(filt, "yes", book(20), 1, 1)
    result = eval_filter(filt, 2)
    assert result.ready and result.sides[0].depth_change_2s == Decimal("12")


def test_unreliable_state_requires_fresh_history():
    filt = RDPBookFilter(market())
    observe_both(filt, 0)
    unreliable = eval_filter(filt, 2, reliable=False)
    assert not unreliable.ready and unreliable.rejected
    not_rebuilt = eval_filter(filt, 4)
    assert not not_rebuilt.ready and not_rebuilt.rejected
    observe_both(filt, 4)
    assert eval_filter(filt, 6).ready


def test_inexact_depth_sum_fails_closed_without_changing_depth_extreme_result():
    filt = RDPBookFilter(market())
    yes = OrderBook(.01)
    yes.apply_snapshot([(.5, 60), (.49, 1e-70)], [(.51, 10)], 0)
    observe(filt, "yes", yes, 0, 1)
    observe(filt, "no", book(15), 0, 1)
    assert filt.observe_trade(trade("yes", 1.5), 1.5, 1)
    assert filt.observe_trade(trade("no", 1.5), 1.5, 1)
    result = eval_filter(filt, 2)
    assert not result.ready and result.rejected
    assert "NOT_READY:UNSUPPORTED_PRECISION" in result.reasons


def test_inexact_cumulative_variation_fails_closed():
    filt = RDPBookFilter(market())
    observe(filt, "no", book(10), 0, 1)

    def exact_size_book(size):
        result = OrderBook(.01)
        result.bids[.5] = size
        result.asks[.51] = 10
        return result

    observe(filt, "yes", exact_size_book(Decimal("2e64")), 0, 1)
    observe(filt, "yes", exact_size_book(Decimal("1e64")), .5, 1)
    observe(filt, "yes", exact_size_book(Decimal("10000000000000000000000000000000000000000000000000000000000000001")),
            1, 1)
    # At 2 seconds the one-second excursion anchor is available, while the
    # depth changes are large enough that summing them exceeds supported digits.
    result = eval_filter(filt, 2)
    assert not result.ready and result.rejected
    assert "NOT_READY:UNSUPPORTED_PRECISION" in result.reasons


def test_synthetic_marketdata_callbacks_feed_snapshots_deltas_and_trades():
    filt = RDPBookFilter(market())
    clock = {"at": 0.0}
    svc = None

    def on_dirty(condition_id, token_id):
        assert svc is not None
        observe(filt, token_id, svc.book(token_id), clock["at"],
                svc.connection_generation, condition_id)

    def on_trade(print_):
        filt.observe_trade(print_, clock["at"], svc.connection_generation)

    svc = MarketDataService(on_dirty=on_dirty, on_trade=on_trade)
    svc.set_markets([("cid", ["yes", "no"])])
    svc.connection_generation = 7
    for token in ("yes", "no"):
        svc._dispatch({"event_type": "book", "market": "cid", "asset_id": token,
            "timestamp": "1000", "tick_size": ".01",
            "bids": [{"price": ".50", "size": "10"}],
            "asks": [{"price": ".51", "size": "10"}]})
    clock["at"] = .5
    svc._dispatch({"event_type": "price_change", "market": "cid", "timestamp": "1500",
        "price_changes": [{"asset_id": "yes", "price": ".50", "size": "15", "side": "BUY"}]})
    clock["at"] = 1
    svc._dispatch({"event_type": "last_trade_price", "market": "cid", "asset_id": "no",
        "price": ".50", "size": "1", "side": "BUY", "timestamp": "2000"})
    result = eval_filter(filt, 2, gen=7)
    assert result.ready
    assert result.sides[0].depth_change_2s == Decimal("5")
    assert result.sides[1].real_trade_in_2s is True
    assert not result.rejected


def test_existing_books_and_trade_objects_are_not_mutated_and_results_are_frozen():
    filt = RDPBookFilter(market())
    yes_book, no_book = book(10), book(10)
    before = ((tuple(yes_book.bids.items()), yes_book.tick_size),
        (tuple(no_book.bids.items()), no_book.tick_size))
    print_ = trade("yes", .5)
    assert observe(filt, "yes", yes_book, 0)
    assert observe(filt, "no", no_book, 0)
    assert filt.observe_trade(print_, .5, 1)
    result = eval_filter(filt, 2)
    after = ((tuple(yes_book.bids.items()), yes_book.tick_size),
        (tuple(no_book.bids.items()), no_book.tick_size))
    assert before == after
    assert print_ == trade("yes", .5)
    assert result.ready
    with pytest.raises(FrozenInstanceError):
        result.rejected = True


def test_pruning_retains_cutoff_anchor_and_discards_older_updates():
    filt = RDPBookFilter(market())
    observe_both(filt, 0, yes_q=10)
    for at, q in ((.25, 20), (.5, 10), (.75, 20), (1, 10), (1.25, 10)):
        observe(filt, "yes", book(q), at, 1)
    result = eval_filter(filt, 2.5)
    assert result.ready
    assert filt._history["yes"].samples[0].at == .5
    assert all(sample.at >= .5 for sample in filt._history["yes"].samples)
