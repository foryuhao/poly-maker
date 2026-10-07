from __future__ import annotations

from dataclasses import asdict, replace

import pytest

from polymaker.domain import MarketMeta, Side, TokenMeta, TradeState
from polymaker.marketdata.orderbook import BookView
from polymaker.marketdata.service import MarketDataService
from polymaker.state.store import StateStore
from polymaker.state.tracker import TradeEvent, UserEventProcessor
from polymaker.strategy.rdp_accounting import RDPInventorySnapshot, snapshot_inventory
from polymaker.strategy.rdp_quotes import build_normal_quotes, build_rebalance_quote

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


def rebalance_candidate(delta, *, m=None, yes=_UNSET, no=_UNSET,
                        yes_tick=.01, no_tick=.01, inv=None):
    m = m or make_market()
    inv = inventory_for_delta(m, delta) if inv is None else inv
    yes_view = book() if yes is _UNSET else yes
    no_view = book(.42, .48) if no is _UNSET else no
    return build_rebalance_quote(m, inv, yes_view, no_view, yes_tick, no_tick)


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


@pytest.mark.parametrize(("delta", "token", "price"), [
    (5, "no", .44),
    (-5, "yes", .59),
    (12, "no", .44),
    (-12, "yes", .59),
])
def test_rebalance_exact_threshold_and_larger_exposure_selects_one_side(delta, token, price):
    m = make_market()
    yes_view, no_view = book(.55, .60), book(.40, .45)
    decision = rebalance_candidate(delta, m=m, yes=yes_view, no=no_view)
    assert decision.has_candidate and decision.reason is None
    assert len(decision.quotes) == 1
    quote = decision.quotes[0]
    assert (quote.token_id, quote.side, quote.price, quote.size) == (
        m.no.token_id if token == "no" else m.yes.token_id, Side.BUY, price, 5)
    assert decision.post_only_required is True
    assert "CanSafelyContinue" in decision.required_gates[0]
    assert "CapitalGate" in decision.required_gates
    assert any("cancel and reconcile" in gate for gate in decision.required_gates)
    assert "live authorization" in decision.required_gates
    assert "Book Filter" not in decision.required_gates
    assert "PriceGate" not in decision.required_gates


@pytest.mark.parametrize("delta", [4.999, -4.999])
def test_rebalance_rejects_values_strictly_inside_threshold(delta):
    decision = rebalance_candidate(delta)
    assert decision.quotes == () and decision.reason == "NON_REBALANCE_INVENTORY"


def test_rebalance_fractional_confirmed_inventory_integration(tmp_path):
    m = make_market()
    store = StateStore(tmp_path / "fractional-rebalance.db")
    try:
        proc = UserEventProcessor(store)
        fills = (("yes-order", m.yes.token_id, 5.3, .2, "yes-trade"),
                 ("no-order", m.no.token_id, .1, .3, "no-trade"))
        for oid, token, qty, price, trade_id in fills:
            proc.register_order(oid, m.condition_id, token, Side.BUY, qty, .5)
            proc.on_trade(TradeEvent(token, Side.BUY, price, qty, trade_id,
                TradeState.MATCHED, 1, oid), m.condition_id)
            proc.on_trade(TradeEvent(token, Side.BUY, price, qty, trade_id,
                TradeState.CONFIRMED, 2, oid), m.condition_id)
        inventory = snapshot_inventory(m, proc, {"yes-order", "no-order"})
        assert inventory.delta == inventory.qy - inventory.qn
        assert inventory.delta >= 5
        decision = build_rebalance_quote(m, inventory, book(.55, .60), book(.40, .45), .01, .01)
        assert [(q.token_id, q.price, q.size) for q in decision.quotes] == [
            (m.no.token_id, .44, 5)]
    finally:
        store.close()


def test_rebalance_uses_only_deficient_token_tick_and_book():
    m = make_market()
    # Positive Delta makes NO deficient. A bad surplus-side book/tick is not
    # consumed by this pure selected-side calculation.
    decision = rebalance_candidate(5, m=m, yes=None, no=book(.40, .45),
        yes_tick=float("nan"), no_tick=.01)
    assert [(q.token_id, q.price, q.size) for q in decision.quotes] == [
        (m.no.token_id, .44, 5)]
    inverse = rebalance_candidate(-5, m=m, yes=book(.55, .60), no=None,
        yes_tick=.001, no_tick=float("nan"))
    assert [(q.token_id, q.price, q.size) for q in inverse.quotes] == [
        (m.yes.token_id, .599, 5)]


def test_rebalance_selected_tick_change_updates_next_candidate():
    m = make_market()
    service = MarketDataService()
    service.set_markets([(m.condition_id, [m.yes.token_id, m.no.token_id])])
    service._dispatch({"event_type": "book", "market": m.condition_id, "asset_id": "no",
        "bids": [{"price": ".40", "size": "10"}],
        "asks": [{"price": ".45", "size": "10"}], "tick_size": ".01"})
    original = build_rebalance_quote(m, inventory_for_delta(m, 5), None, service.view("no"),
        float("nan"), service.book("no").tick_size)
    assert original.quotes[0].price == pytest.approx(.44)
    service._dispatch({"event_type": "tick_size_change", "asset_id": "no",
        "old_tick_size": ".01", "new_tick_size": ".001"})
    updated = build_rebalance_quote(m, inventory_for_delta(m, 5), None, service.view("no"),
        float("nan"), service.book("no").tick_size)
    assert updated.quotes[0].price == pytest.approx(.449)


@pytest.mark.parametrize(("bid", "ask", "expected"), [
    (.40, .45, .44),  # improves the current bid
    (.44, .45, .44),  # equals the current bid
])
def test_rebalance_highest_non_crossing_price_may_improve_or_equal_bid(bid, ask, expected):
    decision = rebalance_candidate(5, no=book(bid, ask))
    assert len(decision.quotes) == 1
    assert decision.quotes[0].token_id == "no"
    assert decision.quotes[0].price == pytest.approx(expected)
    assert decision.quotes[0].price < ask


def test_rebalance_does_not_apply_normal_price_gate():
    decision = rebalance_candidate(5, yes=book(.80, .90), no=book(.40, .45))
    assert .80 + .40 > 1
    assert decision.quotes[0].price == pytest.approx(.44)


def test_rebalance_economics_do_not_block_candidate():
    m = make_market()
    inventory = inventory_for_delta(m, 5)
    loss_inventory = replace(inventory, locked_pnl=-100, worst_settlement_pnl=-200)
    normal_economics = rebalance_candidate(5, m=m, inv=inventory)
    negative_economics = rebalance_candidate(5, m=m, inv=loss_inventory)
    assert negative_economics == normal_economics


@pytest.mark.parametrize("bad_selected", [
    None,
    book(.40, None),
    book(float("nan"), .45),
    book(.40, float("inf")),
    book(.45, .45),
    book(.46, .45),
])
def test_rebalance_invalid_selected_book_rejects(bad_selected):
    decision = rebalance_candidate(5, no=bad_selected)
    assert decision.quotes == () and decision.reason == "INVALID_BOOK"


@pytest.mark.parametrize("tick", [0, -.01, float("nan"), float("inf")])
def test_rebalance_invalid_selected_tick_rejects(tick):
    decision = rebalance_candidate(5, no_tick=tick)
    assert decision.quotes == () and decision.reason == "INVALID_TICK"


def test_rebalance_off_grid_ask_and_zero_subtraction_reject_without_substitution():
    off_grid = rebalance_candidate(5, no=book(.40, .455), no_tick=.01)
    assert off_grid.quotes == () and off_grid.reason == "OFF_TICK"
    zero = rebalance_candidate(5, no=book(.005, .01), no_tick=.01)
    assert zero.quotes == () and zero.reason == "ILLEGAL_QUOTE_PRICE"


@pytest.mark.parametrize(("tick", "reason"), [
    (1e-30, "INVALID_PRECISION"),
    (1e-20, "UNREPRESENTABLE_PRICE"),
])
def test_rebalance_tiny_tick_fails_closed(tick, reason):
    decision = rebalance_candidate(5, no=book(.40, .45), no_tick=tick)
    assert decision.quotes == () and decision.reason == reason


def test_rebalance_minimum_size_is_five_and_never_adjusted():
    assert rebalance_candidate(5, m=make_market(min_order_size=5)).quotes[0].size == 5
    decision = rebalance_candidate(12, m=make_market(min_order_size=5.01))
    assert decision.quotes == () and decision.reason == "MIN_ORDER_SIZE"


@pytest.mark.parametrize("minimum", [0, -1, float("nan"), float("inf")])
def test_rebalance_invalid_minimum_metadata_rejects(minimum):
    decision = rebalance_candidate(5, m=make_market(min_order_size=minimum))
    assert decision.quotes == () and decision.reason == "INVALID_MIN_ORDER_SIZE"


def test_rebalance_identity_and_inventory_mismatch_reject():
    m = make_market()
    assert rebalance_candidate(5, m=m, inv=empty_inventory(make_market(condition="other"))).reason == \
        "MARKET_INVENTORY_MISMATCH"
    base = empty_inventory(m)
    assert rebalance_candidate(5, m=m, inv=replace(base, qy=5)).reason == "INVALID_INVENTORY"


def test_rebalance_inputs_unchanged_after_accepted_and_rejected_calls():
    m = make_market()
    inv = inventory_for_delta(m, 5)
    selected = book(.40, .45)
    before = (asdict(m), asdict(inv), asdict(selected))
    result = build_rebalance_quote(m, inv, None, selected, float("nan"), .01)
    assert result.has_candidate
    assert (asdict(m), asdict(inv), asdict(selected)) == before
    rejected_book = book(.40, .455)
    rejected_before = (asdict(m), asdict(inv), asdict(rejected_book))
    rejected = build_rebalance_quote(m, inv, None, rejected_book, float("nan"), .01)
    assert rejected.quotes == ()
    assert (asdict(m), asdict(inv), asdict(rejected_book)) == rejected_before
