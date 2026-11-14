"""Hand-computed volume/time cases for the direct-SELL evidence model."""

from dataclasses import replace
from decimal import Decimal as D

import pytest
from m510_trade_through import TradeThroughBroker

from test_m1234_sim_core import broker, sell
from test_m1234_sim_core import intent as base_intent


def intent(*args, **kwargs):
    return replace(base_intent(*args, **kwargs), strategy="M5")


def setup():
    old, _ = broker()
    trace = []
    b = TradeThroughBroker("research/trade-through", lambda k, p: trace.append((k, p)))
    b.update_books(old.books)
    return b, trace


@pytest.mark.parametrize("price,side,derived,filled", [
    (".50", "SELL", False, 0), (".495", "SELL", False, 0),
    (".49", "SELL", False, 3), (".48", "BUY", False, 0),
    (".48", "SELL", True, 0),
])
def test_only_direct_sell_through_one_submitted_tick(price, side, derived, filled):
    b, trace = setup()
    o = b.submit(intent())
    b.advance(.1)
    b.trade(sell("3", price=price, side=side, derived=derived), "x")
    assert o.filled == filled
    assert not b.queues
    assert not any(k == "QUEUE_JOIN" for k, _ in trace)


def test_quantity_caps_fifo_price_priority_and_independent_profiles():
    b, _ = setup()
    a = b.submit(intent(size="5"))
    c = b.submit(replace(intent(size="5"), opportunity="second"))
    other = b.submit(intent(profile="M3", size="5"))
    b.advance(.1)
    b.trade(sell("7", price=".49"), "first")
    assert (a.filled, c.filled, other.filled) == (5, 2, 5)
    b.trade(sell("7", price=".49"), "repeated")
    assert c.filled == 2
    b.trade(sell("20", price=".49", trade_id="next"), "second")
    assert c.filled == 5
    assert b.account("M2").spent == D(5)
    b.check()


def test_higher_limit_gets_shared_print_volume_first():
    b, _ = setup()
    low = b.submit(intent(price=".49", size="5"))
    high = b.submit(intent(size="5"))
    b.advance(.1)
    b.trade(sell("6", price=".48"), "price-priority")
    assert high.filled == 5 and low.filled == 1


def test_conflicting_identity_flags_unknown_and_never_reuses_quantity():
    b, _ = setup()
    o = b.submit(intent())
    b.advance(.1)
    b.trade(sell("3", price=".49"), "first")
    b.trade(sell("4", price=".49"), "conflicting")
    assert o.filled == 3 and o.uncertain


@pytest.mark.parametrize("source,expected", [(.099999, 0), (.1, 4), (2.999999, 4), (3, 0), (3.001, 0)])
def test_live_and_cutoff_boundaries(source, expected):
    b, _ = setup()
    o = b.submit(intent())
    b.advance(max(.1, source))
    b.trade(sell("4", price=".49"), "boundary", source_mono=source)
    assert o.filled == expected


def test_late_pre_cancel_evidence_at_original_price_and_frozen_tick():
    b, trace = setup()
    o = b.submit(intent())
    b.advance(.1)
    assert b.submitted_ticks[o.id] == "0.01"
    b.update_books({t: replace(book, tick=D(".001")) for t, book in b.books.items()})
    b.trade(sell("3", price=".495", trade_id="not-through"), "not-through")
    assert o.filled == 0
    b.advance(3)
    b.cancel(o.id)
    b.advance(3.21)
    assert o.state == "CANCELLED"
    b.trade(sell("4", price=".49"), "late", source_mono=2.99)
    assert o.filled == 4 and o.spent == D(2) and o.state == "CANCELLED"
    fill = next(p for k, p in trace if k == "MODELLED_FILL")
    assert fill["source_mono"] == 2.99 and fill["evidence_available_mono"] == 3.21
    b.trade(sell("6", price=".49", trade_id="late2"), "late2", source_mono=3)
    assert o.filled == 4
    b.check()


def test_gap_preserves_known_fills_and_later_positive_evidence():
    b, _ = setup()
    o = b.submit(intent())
    b.advance(.1)
    b.trade(sell("3", price=".49"), "first")
    b.advance(.2)
    b.gap("MISSING_INPUT")
    assert o.uncertain and o.filled == 3
    b.trade(sell("2", price=".49", trade_id="after-gap"), "later")
    assert o.filled == 5 and o.uncertain
    b.settle("YES")
    assert b.account("M2").payout == 5


def test_one_cent_order_has_no_positive_trigger_and_other_token_cannot_fill():
    b, _ = setup()
    o = b.submit(intent(price=".01"))
    b.advance(.1)
    b.trade(sell("100", price=".01"), "one-cent")
    b.trade(sell("100", price=".01", asset_id="other", trade_id="other"), "other")
    assert o.filled == 0


def test_unknown_arrival_and_conflicting_trade_identity_are_unresolved():
    b, _ = setup()
    o = b.submit(intent())
    b.update_constraints({t: {**c, "known": False} for t, c in b.constraints.items()})
    b.advance(.1)
    b.trade(sell("3", price=".49"), "no-arrival")
    assert o.filled == 0 and o.uncertain


def test_anonymous_repeat_keeps_one_volume_and_flags_identity_ambiguity():
    b, trace = setup()
    order = b.submit(intent())
    b.advance(.1)
    row = sell("3", price=".49")
    row.pop("trade_id")
    b.trade(row, "first-input")
    b.trade(row, "repeated-input")
    assert order.filled == 3 and order.uncertain
    assert any(k == "DUPLICATE_TRADE" for k, _ in trace)


def test_full_known_quantity_resolves_unknown_remainder_after_gap():
    b, _ = setup()
    order = b.submit(intent())
    b.advance(.1)
    b.trade(sell("3", price=".49"), "first")
    b.gap("MISSING_INPUT")
    assert order.uncertain
    b.trade(sell("20", price=".49", trade_id="later-positive"), "later")
    assert order.filled == 10 and order.remaining == 0 and not order.uncertain
    assert order.spent == D("5")
    b.check()
