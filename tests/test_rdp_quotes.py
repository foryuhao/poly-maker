from __future__ import annotations

from dataclasses import asdict, replace

import pytest

from polymaker.domain import MarketMeta, Side, TokenMeta, TradeState
from polymaker.marketdata.orderbook import BookView
from polymaker.marketdata.service import MarketDataService
from polymaker.state.store import StateStore
from polymaker.state.tracker import TradeEvent, UserEventProcessor
from polymaker.strategy.rdp_accounting import RDPInventorySnapshot, snapshot_inventory
from polymaker.strategy.rdp_quotes import build_normal_quotes

_UNSET = object()


def make_market(*, min_order_size=5, condition="cid", yes="yes", no="no"):
    return MarketMeta(condition, "BTC Up", "btc-up", (TokenMeta(yes, "Yes"), TokenMeta(no, "No")),
        .01, False, min_order_size, 0, 0, 0, 0, 0, False, None, None)


def book(bid=.56, ask=.60):
    return BookView(bid, 10, ask, 10, None, None, 10, 10)


def empty_inventory(market):
    store = StateStore(":memory:")
    try:
        proc = UserEventProcessor(store)
        return snapshot_inventory(market, proc, set())
    finally:
        store.close()


def inventory_for_delta(market, delta):
    base = empty_inventory(market)
    qy, qn = max(delta, 0), max(-delta, 0)
    return replace(base, qy=qy, qn=qn, delta=delta, inventory_exposure=abs(delta))


def candidate(delta=0, *, m=None, yes=_UNSET, no=_UNSET, yes_tick=.01, no_tick=.01, inv=None):
    m = m or make_market()
    inv = inventory_for_delta(m, delta) if inv is None else inv
    yes_view = book() if yes is _UNSET else yes
    no_view = book(.42, .48) if no is _UNSET else no
    return build_normal_quotes(m, inv, yes_view, no_view, yes_tick, no_tick)


@pytest.mark.parametrize(("delta", "yes_size", "no_size"), [
    (0, 5, 5),
    (2, 5, 7),
    (-3, 8, 5),
    (-1.25, 6.25, 5),
])
def test_frozen_normal_sizes_and_deterministic_buy_pair(delta, yes_size, no_size):
    m = make_market()
    decision = candidate(delta, m=m)
    assert decision.has_candidate and decision.reason is None
    assert len(decision.quotes) == 2
    assert [(q.token_id, q.side, q.price, q.size) for q in decision.quotes] == [
        (m.yes.token_id, Side.BUY, .55, yes_size),
        (m.no.token_id, Side.BUY, .41, no_size),
    ]
    assert decision.post_only_required is True
    assert "Book Filter" in decision.required_gates
    assert "CapitalGate" in decision.required_gates
    assert "current-cycle reconciliation" in decision.required_gates
    assert not hasattr(decision, "ready_to_send")


@pytest.mark.parametrize("delta", [5, -5])
def test_both_normal_threshold_signs_reject(delta):
    decision = candidate(delta)
    assert decision.quotes == () and decision.reason == "NON_NORMAL_INVENTORY"


@pytest.mark.parametrize("delta", [4.999, -4.999])
def test_values_just_inside_both_normal_thresholds_are_eligible(delta):
    decision = candidate(delta)
    assert decision.has_candidate
    assert decision.quotes[0].size == pytest.approx(5 + max(-delta, 0))
    assert decision.quotes[1].size == pytest.approx(5 + max(delta, 0))


@pytest.mark.parametrize(("yes_bid", "no_bid", "reason"), [
    (.49, .50, None),
    (.50, .50, None),
    (.51, .50, "PRICE_GATE"),
])
def test_decimal_price_gate_below_equal_above(yes_bid, no_bid, reason):
    decision = candidate(yes=book(yes_bid, .8), no=book(no_bid, .8))
    if reason:
        assert decision.quotes == () and decision.reason == reason
    else:
        assert decision.has_candidate
        assert decision.quotes[0].price == pytest.approx(yes_bid - .01)
        assert decision.quotes[1].price == pytest.approx(no_bid - .01)


def test_price_gate_checks_bids_even_when_resulting_quote_sum_is_below_one():
    decision = candidate(yes=book(.8, .9), no=book(.3, .4), yes_tick=.1, no_tick=.1)
    assert .7 + .2 < 1
    assert decision.quotes == () and decision.reason == "PRICE_GATE"


def test_per_token_ticks_use_distinct_exact_offsets():
    decision = candidate(yes=book(.56, .6), no=book(.42, .48), yes_tick=.01, no_tick=.001)
    assert decision.has_candidate
    assert [q.price for q in decision.quotes] == pytest.approx([.55, .419])


@pytest.mark.parametrize(("tick", "reason"), [
    (1e-30, "INVALID_PRECISION"),
    (1e-20, "UNREPRESENTABLE_PRICE"),
])
def test_tiny_ticks_fail_closed_when_decimal_or_float_loses_required_price(tick, reason):
    decision = candidate(yes_tick=tick)
    assert decision.quotes == () and decision.reason == reason


def test_next_candidate_uses_tick_after_marketdata_tick_change_event():
    m = make_market()
    service = MarketDataService()
    service.set_markets([(m.condition_id, [m.yes.token_id, m.no.token_id])])
    service._dispatch({"event_type": "book", "market": m.condition_id, "asset_id": "yes",
        "bids": [{"price": ".50", "size": "10"}],
        "asks": [{"price": ".60", "size": "10"}], "tick_size": ".01"})
    service._dispatch({"event_type": "book", "market": m.condition_id, "asset_id": "no",
        "bids": [{"price": ".40", "size": "10"}],
        "asks": [{"price": ".50", "size": "10"}], "tick_size": ".01"})
    before = build_normal_quotes(m, empty_inventory(m), service.view("yes"), service.view("no"),
        service.book("yes").tick_size, service.book("no").tick_size)
    assert before.quotes[0].price == pytest.approx(.49)
    service._dispatch({"event_type": "tick_size_change", "asset_id": "yes",
        "old_tick_size": ".01", "new_tick_size": ".001"})
    after = build_normal_quotes(m, empty_inventory(m), service.view("yes"), service.view("no"),
        service.book("yes").tick_size, service.book("no").tick_size)
    assert after.quotes[0].price == pytest.approx(.499)
    assert after.quotes[1].price == pytest.approx(.39)


@pytest.mark.parametrize("bad_yes,bad_no", [
    (None, book(.4, .5)),
    (book(None, .6), book(.4, .5)),
    (book(.5, None), book(.4, .5)),
    (book(float("nan"), .6), book(.4, .5)),
    (book(.5, float("inf")), book(.4, .5)),
    (book(.5, .5), book(.4, .5)),
    (book(.6, .5), book(.4, .5)),
    (book(0, .5), book(.4, .5)),
    (book(.5, 1), book(.4, .5)),
])
def test_missing_invalid_locked_or_crossed_book_rejects_pair(bad_yes, bad_no):
    decision = candidate(yes=bad_yes, no=bad_no)
    assert decision.quotes == () and decision.reason == "INVALID_BOOK"


@pytest.mark.parametrize("tick", [0, -0.01, float("nan"), float("inf")])
def test_invalid_ticks_reject(tick):
    decision = candidate(yes_tick=tick)
    assert decision.quotes == () and decision.reason == "INVALID_TICK"


def test_subtraction_to_zero_is_rejected_without_clamping():
    decision = candidate(yes=book(.01, .02), yes_tick=.01)
    assert decision.quotes == () and decision.reason == "ILLEGAL_QUOTE_PRICE"


def test_off_grid_bid_rejects_without_snapping():
    decision = candidate(yes=book(.505, .6), no=book(.4, .5), yes_tick=.01)
    assert decision.quotes == () and decision.reason == "OFF_TICK"


def test_minimum_size_five_passes_and_larger_minimum_rejects_whole_pair():
    assert candidate(m=make_market(min_order_size=5)).has_candidate
    too_large = candidate(m=make_market(min_order_size=5.01))
    assert too_large.quotes == () and too_large.reason == "MIN_ORDER_SIZE"
    skewed = candidate(2, m=make_market(min_order_size=5.1))
    assert skewed.quotes == () and skewed.reason == "MIN_ORDER_SIZE"


@pytest.mark.parametrize("minimum", [0, -1, float("nan"), float("inf")])
def test_invalid_minimum_order_metadata_rejects(minimum):
    decision = candidate(m=make_market(min_order_size=minimum))
    assert decision.quotes == () and decision.reason == "INVALID_MIN_ORDER_SIZE"


def test_identity_mismatch_and_malformed_inventory_reject():
    m = make_market()
    wrong_market = make_market(condition="other")
    assert candidate(m=m, inv=empty_inventory(wrong_market)).reason == "MARKET_INVENTORY_MISMATCH"
    wrong_tokens = make_market(yes="other-yes")
    assert candidate(m=m, inv=empty_inventory(wrong_tokens)).reason == "MARKET_INVENTORY_MISMATCH"
    base = empty_inventory(m)
    assert candidate(m=m, inv=replace(base, qy=-1)).reason == "INVALID_INVENTORY"
    assert candidate(m=m, inv=replace(base, delta=1)).reason == "INVALID_INVENTORY"
    assert candidate(m=m, inv=replace(base, inventory_exposure=1)).reason == "INVALID_INVENTORY"
    assert candidate(m=m, inv=replace(base, delta=float("nan"))).reason == "INVALID_INVENTORY"


def test_inputs_unchanged_after_success_and_rejection():
    m = make_market()
    inv = inventory_for_delta(m, 1.5)
    yes, no = book(), book(.42, .48)
    before = (asdict(m), asdict(inv), asdict(yes), asdict(no))
    assert build_normal_quotes(m, inv, yes, no, .01, .01).has_candidate
    assert (asdict(m), asdict(inv), asdict(yes), asdict(no)) == before
    bad_yes = book(float("nan"), .6)
    bad_before = (asdict(m), asdict(inv), asdict(bad_yes), asdict(no))
    rejected = build_normal_quotes(m, inv, bad_yes, no, .01, .01)
    assert rejected.quotes == ()
    assert (asdict(m), asdict(inv), asdict(bad_yes), asdict(no)) == bad_before


def test_candidate_integrates_real_confirmed_accounting_snapshot(tmp_path):
    m = make_market()
    store = StateStore(tmp_path / "quotes.db")
    try:
        proc = UserEventProcessor(store)
        proc.register_order("yes-order", m.condition_id, m.yes.token_id, Side.BUY, 2, .5)
        for state, ts in ((TradeState.MATCHED, 1), (TradeState.CONFIRMED, 2)):
            proc.on_trade(TradeEvent(m.yes.token_id, Side.BUY, .3, 2, "trade", state, ts,
                                     "yes-order"), m.condition_id)
        inventory = snapshot_inventory(m, proc, {"yes-order"})
        assert inventory.delta == 2
        decision = build_normal_quotes(m, inventory, book(.56, .6), book(.42, .48), .01, .01)
        assert decision.has_candidate
        assert [(q.token_id, q.price, q.size) for q in decision.quotes] == [
            (m.yes.token_id, .55, 5), (m.no.token_id, .41, 7)]
    finally:
        store.close()


def test_fractional_confirmed_fill_snapshot_remains_normal_and_quotes(tmp_path):
    m = make_market()
    store = StateStore(tmp_path / "fractional-quotes.db")
    try:
        proc = UserEventProcessor(store)
        for order_id, token_id, qty, price, trade_id in (
            ("yes-order", m.yes.token_id, .3, .4, "yes-trade"),
            ("no-order", m.no.token_id, .1, .3, "no-trade"),
        ):
            proc.register_order(order_id, m.condition_id, token_id, Side.BUY, qty, .5)
            proc.on_trade(TradeEvent(token_id, Side.BUY, price, qty, trade_id,
                TradeState.MATCHED, 1, order_id), m.condition_id)
            proc.on_trade(TradeEvent(token_id, Side.BUY, price, qty, trade_id,
                TradeState.CONFIRMED, 2, order_id), m.condition_id)
        inventory = snapshot_inventory(m, proc, {"yes-order", "no-order"})
        assert inventory.qy == .3 and inventory.qn == .1
        assert inventory.delta == .3 - .1
        decision = build_normal_quotes(m, inventory, book(.56, .6), book(.42, .48), .01, .01)
        assert decision.has_candidate
        assert decision.quotes[0].size == 5
        assert decision.quotes[1].size == pytest.approx(5.2)
        # A mathematically rounded decimal Delta is inconsistent with the
        # accounting producer's exact float subtraction and is rejected.
        malformed = replace(inventory, delta=.2)
        assert build_normal_quotes(m, malformed, book(.56, .6), book(.42, .48),
            .01, .01).reason == "INVALID_INVENTORY"
    finally:
        store.close()
