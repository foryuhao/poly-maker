from dataclasses import replace
from decimal import Decimal as D

import pytest
from m1223_execution import Broker, fill_attribution

from test_m1234_sim_core import broker as old_broker
from test_m1234_sim_core import intent as old_intent
from test_m1234_sim_core import sell


def setup():
    source, _ = old_broker()
    rows = []
    b = Broker("sop/trade-through", lambda k, v: rows.append((k, v)))
    b.update_books(source.books)
    return b, rows


def intent(**kw):
    return replace(old_intent(**kw), strategy="M12", profile="M12", signal_version="test-m12")


@pytest.mark.parametrize(
    "source,filled", [(0.099999, 0), (0.1, 3), (2.999999, 3), (3.0, 3), (3.199999, 3), (3.2, 0)]
)
def test_deadline_requests_cancel_but_does_not_delete_inflight_fills(source, filled):
    b, rows = setup()
    o = b.submit(intent())
    b.advance(max(0.1, source))
    b.trade(sell("3", price=".49"), "a", source_mono=source)
    assert o.filled == filled
    if source >= 3:
        assert o.cancel_requested == 3.0


@pytest.mark.parametrize(
    "price,side,derived,expected",
    [
        (".50", "SELL", False, 0),
        (".495", "SELL", False, 0),
        (".49", "SELL", False, 3),
        (".48", "BUY", False, 0),
        (".48", "SELL", True, 0),
    ],
)
def test_exact_frozen_tick_and_direct_side(price, side, derived, expected):
    b, _ = setup()
    o = b.submit(intent())
    b.advance(0.1)
    b.trade(sell("3", price=price, side=side, derived=derived), "a")
    assert o.filled == expected


def test_cancel_request_preserves_partial_fills_and_quantity_cannot_be_reused():
    b, _ = setup()
    o = b.submit(intent())
    b.advance(0.1)
    b.trade(sell("3", price=".49"), "first")
    b.advance(0.2)
    b.cancel(o.id)
    b.advance(0.25)
    b.trade(sell("4", price=".49", trade_id="second"), "second")
    b.trade(sell("4", price=".49", trade_id="second"), "duplicate")
    assert o.filled == 7 and o.spent == D("3.5")
    b.advance(0.4)
    b.trade(sell("9", price=".49", trade_id="after"), "after")
    assert o.filled == 7 and o.state == "CANCELLED"


def test_late_record_before_cancel_arrival_is_kept_at_original_price():
    b, _ = setup()
    o = b.submit(intent())
    b.advance(0.1)
    b.advance(0.2)
    b.cancel(o.id)
    b.advance(0.5)
    b.trade(sell("4", price=".48"), "late", source_mono=0.3)
    assert o.filled == 4 and o.spent == 2 and o.state == "CANCELLED"


def test_frozen_submission_tick_does_not_change_with_later_book_tick():
    b, _ = setup()
    o = b.submit(intent())
    b.advance(0.1)
    b.update_books({t: replace(v, tick=D(".001")) for t, v in b.books.items()})
    b.trade(sell("3", price=".495"), "not-through")
    assert o.filled == 0


def test_same_account_shares_print_volume_and_other_account_is_independent():
    b, _ = setup()
    a = b.submit(intent(size="5"))
    second = b.submit(replace(intent(size="5"), opportunity="second"))
    other = b.submit(replace(intent(size="5"), profile="other"))
    b.advance(0.1)
    b.trade(sell("7", price=".49"), "a")
    assert (a.filled, second.filled, other.filled) == (5, 2, 5)


def test_zero_trigger_at_one_cent_cannot_fill_from_positive_print():
    b, _ = setup()
    o = b.submit(intent(price=".01"))
    b.advance(0.1)
    b.trade(sell("100", price=".01"), "a")
    assert o.filled == 0


def test_missing_data_is_uncertain_with_known_partial_preserved():
    b, _ = setup()
    o = b.submit(intent())
    b.advance(0.1)
    b.trade(sell("3", price=".49"), "a")
    b.gap("DISCONNECT", "1")
    assert o.filled == 3 and o.uncertain
    b.advance(3.2)
    assert o.uncertain and o.filled == 3


def test_cancel_race_beyond_execution_window_is_not_a_valid_window_fill():
    fact = {"confirmed": 239.5, "deadline": 242.5}
    result = fill_attribution(fact, 240.1, 240.0, 100)
    assert result["classification"] == "AFTER_EXECUTION_WINDOW"
    assert result["after_execution_cutoff"] and not result["after_signal_end"]


def test_same_input_or_same_timestamp_remains_ordering_unknown():
    fact = {"confirmed": 1.0, "deadline": 4.0, "ended": 2.0, "end_input_seq": 10}
    assert fill_attribution(fact, 1.99, 4.0, 10)["classification"] == "ORDERING_UNKNOWN"
    assert fill_attribution(fact, 2.0, 4.0, 11)["classification"] == "ORDERING_UNKNOWN"
