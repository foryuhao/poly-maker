"""Hand-computed exchange cases; expectations never call the matcher."""

from dataclasses import replace
from decimal import Decimal as D

import pytest

from polymaker.m1234_sim.broker import OrderIntent, SimBroker
from polymaker.m1234_sim.events import Clock, Recorder, read_events
from test_rdp_sm_obi import feed_fixture


def broker(**kwargs):
    f, _, _ = feed_fixture()
    trace = []
    b = SimBroker("test", lambda k, p: trace.append((k, p)), **kwargs)
    b.update_books(f.books)
    return b, trace


def intent(profile="M2", size="10", price=".50", **kwargs):
    return OrderIntent(
        profile,
        "M2",
        "frozen",
        "root",
        "market",
        "1",
        "YES",
        price,
        size,
        0,
        3,
        "CANDIDATE",
        **kwargs,
    )


def sell(size="85", **kwargs):
    return {
        "asset_id": "1",
        "price": ".50",
        "size": size,
        "side": "SELL",
        "trade_id": "t1",
        **kwargs,
    }


def test_eighty_ahead_eightyfive_print_fills_five():
    b, trace = broker()
    o = b.submit(intent())
    assert o.filled == 0 and b.account("M2").reserved == 5
    b.trade(sell(), "before-arrival")
    assert o.filled == 0
    b.advance(0.1)
    b.trade(sell(trade_id="t2"), "after-arrival")
    assert o.filled == 5 and o.spent == D("2.50") and o.reserved == D("2.50")
    assert b.account("M2").cash == D("17.50")
    assert b.account("M2").inventory == {"YES": D(5)}
    assert any(k == "MODELLED_FILL" for k, _ in trace)
    b.check()


def test_multi_order_fifo_shared_volume_and_independent_accounts():
    b, _ = broker()
    a = b.submit(intent(size="5"))
    b.advance(0.1)
    c = b.submit(intent(size="5"))
    other = b.submit(intent(profile="M3", size="5"))
    b.advance(0.2)
    b.trade(sell("87"), "x")
    assert (a.filled, c.filled, other.filled) == (D(5), D(2), D(5))
    b.check()


def test_cancel_in_flight_can_fill_and_releases_only_remainder():
    b, _ = broker(cancel_ms=200)
    o = b.submit(intent())
    b.advance(0.1)
    b.cancel(o.id)
    b.advance(0.2)
    b.trade(sell(), "x")
    assert o.filled == 5 and o.state == "CANCEL_IN_FLIGHT"
    b.advance(0.31)
    assert o.state == "CANCELLED" and o.reserved == 0
    b.trade(sell("100", trade_id="t2"), "late")
    assert o.filled == 5
    b.check()


def test_unknown_ack_is_not_rejection_and_fill_can_precede_ack():
    b, _ = broker(ack_ms=500)
    o = b.submit(intent(), fault="UNKNOWN")
    b.advance(0.1)
    b.trade(sell("90"), "x")
    assert o.filled == 10 and o.client == "PENDING"
    b.advance(0.5)
    assert o.client == "UNKNOWN" and o.state == "FILLED"
    b.settle("YES")
    assert b.account("M2").cash == 25


def test_reject_no_position_and_gap_retains_risk_after_cancel():
    b, _ = broker()
    rejected = b.submit(intent(), fault="REJECT")
    b.advance(0.2)
    assert rejected.state == "REJECTED" and rejected.reserved == 0
    o = b.submit(intent())
    b.advance(0.3)
    b.gap("DISCONNECTED")
    b.cancel(o.id)
    b.advance(0.6)
    assert o.uncertain and o.reserved == 5 and b.account("M2").reserved == 5
    b.check()


def test_explicit_duplicates_multimatch_and_ambiguous_identity():
    b, _ = broker()
    o = b.submit(intent(size="20"))
    b.advance(0.1)
    row = sell("85", transaction_hash="same-tx")
    b.trade(row, "a")
    b.trade(row, "duplicate")
    assert o.filled == 5
    b.trade(sell("5", transaction_hash="same-tx", trade_id="t2"), "second-match")
    assert o.filled == 10
    raw = sell("1")
    del raw["trade_id"]
    b.trade(raw, "unkeyed-a")
    b.trade(raw, "unkeyed-b")
    assert b.ambiguous == ["unkeyed-b"] and o.uncertain


def test_depth_decrease_no_fill_and_trade_depth_not_double_counted():
    b, _ = broker(ahead_cancel_fraction=".5")
    o = b.submit(intent())
    b.advance(0.1)
    b.trade(sell("40"), "trade")
    book = b.books["1"]
    b.update_books({**b.books, "1": replace(book, bids=((D(".50"), D(40)),))})
    b.advance(1.2)
    assert o.filled == 0
    assert b.queues[("M2", "1", "0.5")][0][1] == 40
    b.trade(sell("45", trade_id="new"), "later")
    assert o.filled == 5


def test_depth_before_trade_is_also_attributed_once():
    b, _ = broker(ahead_cancel_fraction=".5")
    b.submit(intent())
    b.advance(0.1)
    book = b.books["1"]
    b.update_books({**b.books, "1": replace(book, bids=((D(".50"), D(40)),))})
    b.advance(0.2)
    b.trade(sell("40"), "trade")
    b.advance(1.2)
    assert b.queues[("M2", "1", "0.5")][0][1] == 40


def test_sensitive_cancellation_is_an_assumption_and_never_a_fill():
    b, _ = broker(ahead_cancel_fraction=".5")
    o = b.submit(intent())
    b.advance(0.1)
    book = b.books["1"]
    b.update_books({**b.books, "1": replace(book, bids=((D(".50"), D(0)),))})
    b.advance(1.2)
    assert o.filled == 0 and b.queues[("M2", "1", "0.5")][0][1] == 40


def test_complement_print_does_not_consume_and_price_crossing_uses_volume_once():
    b, _ = broker()
    a = b.submit(intent())
    b.advance(0.1)
    b.trade(sell("100", derived=True), "mirror")
    assert a.filled == 0
    b.trade(sell("85", price=".49"), "cross")
    assert a.filled == 5


def test_settlement_pending_conflict_and_overfill_rejected():
    b, _ = broker()
    o = b.submit(intent())
    b.advance(0.1)
    with pytest.raises(ValueError, match="OVERFILL"):
        b.fill(o, D(11), "bad")
    b.trade(sell(), "x")
    assert b.account("M2").winner is None
    b.settle("NO")
    assert b.account("M2").cash == D("17.50")
    with pytest.raises(ValueError, match="CONFLICT"):
        b.settle("YES")


def test_clock_and_recording_are_repeatable(tmp_path):
    c = Clock()
    c.set(2, 1002)
    path = tmp_path / "sample.jsonl.gz"
    r = Recorder(path)
    r.append("CONNECT", c, 1)
    c.set(3, 1003)
    r.append("GAP", c, 1, reason="RECONNECT")
    r.close()
    assert list(read_events(path)) == list(read_events(path))
    with pytest.raises(ValueError, match="CLOCK"):
        c.set(2, 1004)


def test_writer_failure_is_visible(tmp_path):
    def fail(*args, **kwargs):
        raise OSError("disk full")

    r = Recorder(tmp_path / "failed.gz", opener=fail)
    r.thread.join()
    with pytest.raises(RuntimeError, match="WRITE_FAILED"):
        r.append("FRAME", Clock())
    with pytest.raises(RuntimeError, match="WRITE_FAILED"):
        r.close()


def test_prearrival_depth_decrease_does_not_improve_new_orders_queue():
    b, _ = broker(ahead_cancel_fraction=".5")
    book = b.books["1"]
    b.update_books({**b.books, "1": replace(book, bids=((D(".50"), D(40)),))})
    b.submit(intent())
    b.advance(0.1)
    b.advance(1.2)
    assert b.queues[("M2", "1", "0.5")][0][1] == 40


def test_depth_history_for_another_account_does_not_discount_new_profile():
    b, _ = broker(ahead_cancel_fraction=".5")
    b.submit(intent())
    b.advance(0.1)
    book = b.books["1"]
    b.update_books({**b.books, "1": replace(book, bids=((D(".50"), D(40)),))})
    b.submit(intent(profile="M3"))
    b.advance(0.2)
    b.advance(1.2)
    assert b.queues[("M3", "1", "0.5")][0][1] == 40


def test_crossing_print_and_higher_price_depth_cannot_advance_twice():
    b, _ = broker(ahead_cancel_fraction=".5")
    o = b.submit(intent())
    b.advance(0.1)
    b.trade(sell("40", price=".49"), "crossing-print")
    book = b.books["1"]
    b.update_books({**b.books, "1": replace(book, bids=((D(".50"), D(40)),))})
    b.advance(1.2)
    assert b.queues[("M2", "1", "0.5")][0][1] == 40
    b.trade(sell("45", trade_id="remaining"), "later-print")
    assert o.filled == 5


def test_cancel_requested_during_submit_remains_in_flight_after_activation():
    b, _ = broker()
    o = b.submit(intent())
    b.advance(0.05)
    b.cancel(o.id)
    b.advance(0.1)
    assert o.state == "CANCEL_IN_FLIGHT"
    b.trade(sell("85"), "before-cancel-effect")
    assert o.filled == 5
    b.advance(0.26)
    assert o.state == "CANCELLED" and o.reserved == 0


def test_late_received_print_before_model_arrival_cannot_fill_or_advance_queue():
    b, trace = broker()
    o = b.submit(intent())
    b.advance(0.2)
    b.trade(sell("90"), "late-pre-arrival", source_mono=0.05)
    assert o.filled == 0 and b.queues[("M2", "1", "0.5")][0][1] == 80
    assert any(k == "TRADE_BEFORE_QUEUE_ENTRY" for k, _ in trace)
    b.trade(sell("85", trade_id="genuine-later"), "after-arrival", source_mono=0.15)
    assert o.filled == 5


def test_multi_order_delayed_trade_only_fills_orders_that_existed_at_source_time():
    b, _ = broker()
    first = b.submit(intent(size="5"))
    b.advance(0.1)
    second = b.submit(intent(size="5"))
    b.advance(0.3)
    b.trade(sell("90"), "before-second-arrival", source_mono=0.15)
    assert first.filled == 5 and second.filled == 0
    b.trade(sell("5", trade_id="new"), "after-second-arrival", source_mono=0.25)
    assert second.filled == 5


def test_late_prearrival_depth_does_not_create_cancellation_credit():
    b, _ = broker(ahead_cancel_fraction=".5")
    b.submit(intent())
    b.advance(0.1)
    book = b.books["1"]
    b.update_books(
        {**b.books, "1": replace(book, bids=((D(".50"), D(40)),))}, source_times={"1": 0.05}
    )
    b.advance(1.2)
    assert b.queues[("M2", "1", "0.5")][0][1] == 80


def test_regressing_trade_source_is_uncertain_and_never_backfilled():
    b, _ = broker()
    o = b.submit(intent())
    b.advance(0.3)
    b.trade(sell("40"), "first", source_mono=0.2)
    b.trade(sell("100", trade_id="regressed"), "out-of-order", source_mono=0.15)
    assert o.uncertain and o.filled == 0 and o.reserved == 5


def test_new_order_does_not_inherit_abandoned_external_queue_timestamps():
    b, _ = broker()
    b.submit(intent())
    b.advance(0.1)
    b.gap("MISSING_INPUT")
    assert ("M2", "1", "0.5") not in b.queues
    b.advance(0.3)
    new = b.submit(intent())
    b.advance(0.5)
    b.trade(sell("90"), "before-new-order", source_mono=0.2)
    assert new.filled == 0 and b.queues[("M2", "1", "0.5")][0][1] == 80


def test_cancel_effect_before_post_arrival_is_not_a_future_order_tombstone():
    b, trace = broker(cancel_ms=0)
    o = b.submit(intent())
    b.cancel(o.id)
    b.advance(0.05)
    assert o.state == "SUBMITTING" and o.reserved == 5 and o.cancel_requested is None
    assert any(k == "CANCEL_NOT_FOUND" for k, _ in trace)
    b.advance(0.1)
    b.trade(sell("85"), "later-live-order", source_mono=0.1)
    assert o.filled == 5


def test_delayed_pre_cancel_trade_reopens_historical_risk_and_reconciles(tmp_path):
    from polymaker.m1234_sim.audit import reconcile
    from polymaker.m1234_sim.store import Store

    base, _ = broker()
    store = Store(tmp_path / "late.sqlite3")
    b = SimBroker("test", store.emit)
    b.update_books(base.books)
    o = b.submit(intent())
    b.advance(0.1)
    b.cancel(o.id)
    b.advance(0.4)
    assert o.state == "CANCELLED" and o.reserved == 0
    b.trade(sell("90"), "late-pre-cancel-print", source_mono=0.2)
    assert o.uncertain and o.reserved == 5 and o.filled == 0
    assert b.account("M2").reserved == 5 and b.account("M2").cash == 20
    store.snapshot("state", {"brokers": {"test": b.snapshot()}})
    store.close()
    assert reconcile(tmp_path / "late.sqlite3")["status"] == "PASS"


def test_trade_after_cancel_source_time_does_not_reopen_old_order():
    b, _ = broker()
    o = b.submit(intent())
    b.advance(0.1)
    b.cancel(o.id)
    b.advance(0.4)
    b.trade(sell("90"), "genuine-post-cancel", source_mono=0.35)
    assert o.state == "CANCELLED" and not o.uncertain and o.reserved == 0 and o.filled == 0


def test_transport_gap_reopens_recent_cancelled_order_history():
    b, _ = broker()
    o = b.submit(intent())
    b.advance(0.1)
    b.cancel(o.id)
    b.advance(0.4)
    assert o.reserved == 0
    b.gap("WS_DISCONNECTED")
    assert o.uncertain and o.reserved == 5 and b.account("M2").reserved == 5
    b.check()


def test_transport_gap_after_old_cancel_does_not_invent_unbounded_old_risk():
    b, _ = broker()
    o = b.submit(intent())
    b.advance(0.1)
    b.cancel(o.id)
    b.advance(2)
    b.gap("WS_DISCONNECTED")
    assert not o.uncertain and o.reserved == 0


def test_transport_gap_uses_oldest_lost_receipt_not_processing_time():
    b, _ = broker()
    o = b.submit(intent())
    b.advance(0.1)
    b.cancel(o.id)
    b.advance(2)
    b.gap("LOST_QUEUED_FRAMES", missing_since=0.2)
    assert o.uncertain and o.reserved == 5


def test_equal_numeric_prices_share_one_queue_despite_different_text():
    b, _ = broker()
    a = b.submit(intent(size="5", price=".50"))
    b.advance(0.1)
    c = b.submit(intent(size="5", price="0.500"))
    b.advance(0.2)
    b.trade(sell("87"), "one-economic-level")
    assert a.filled == 5 and c.filled == 2
    assert len(b.queues) == 1


def test_generic_broker_initial_budget_is_configurable_and_independently_audited(tmp_path):
    from polymaker.m1234_sim.audit import reconcile
    from polymaker.m1234_sim.store import Store

    base, _ = broker()
    store = Store(tmp_path / "budget.sqlite3")
    b = SimBroker("test", store.emit, initial_cash="100")
    b.update_books(base.books)
    o = b.submit(intent(size="100"))
    assert o.reserved == 50 and b.account("M2").cash == 100
    b.advance(0.1)
    b.trade(sell("90"), "generic-budget")
    assert o.filled == 10 and b.account("M2").cash == 95 and o.reserved == 45
    store.snapshot("state", {"brokers": {"test": b.snapshot()}})
    store.close()
    assert reconcile(tmp_path / "budget.sqlite3")["status"] == "PASS"
