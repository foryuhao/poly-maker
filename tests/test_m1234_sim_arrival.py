"""R3: known invalid price grids reject before fills; unknown is a separate evidence case."""

from dataclasses import replace
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from polymaker.m1234_sim.audit import reconcile
from polymaker.m1234_sim.broker import SimBroker
from polymaker.m1234_sim.store import Store
from test_m1234_sim_core import broker, intent, sell
from test_m1234_sim_engine import policy_setup
from test_m1234_sim_ticks import change, initial, send


def fine_book(b, price=".095"):
    return replace(b.books["1"], tick=D(".001"), bids=((D(price), D(80)),),
                   asks=((D(".10"), D(80)),))


@pytest.mark.parametrize("old_tick,new_tick,price,rejected", [
    (".001", ".01", ".095", True),
    (".001", ".01", ".09", False),
    (".01", ".001", ".09", False),
])
def test_price_grid_is_rechecked_at_exchange_arrival(old_tick, new_tick, price, rejected):
    b, trace = broker()
    book = replace(fine_book(b, price), tick=D(old_tick))
    b.update_books({**b.books, "1": book})
    order = b.submit(intent(price=price))
    b.advance(.05)
    b.update_books({**b.books, "1": replace(book, tick=D(new_tick), bids=((D(".09"), D(80)),))})
    b.advance(.1)
    b.trade(sell("90", price=".09"), "qualified-print")
    account = b.account("M2")
    if rejected:
        assert order.state == "REJECTED" and order.reject_reason == "PRICE_INCREMENT"
        assert order.filled == order.spent == order.reserved == 0
        assert account.cash == 20 and account.reserved == 0 and not account.inventory
        assert not any(k in {"LIVE", "MODELLED_FILL"} for k, _ in trace)
    else:
        assert order.filled == 10 and order.state == "FILLED" and not order.uncertain


@pytest.mark.parametrize("changed,expected", [(.099, "REJECTED"), (.1, "LIVE"), (.101, "LIVE")])
def test_equal_time_ordering_and_existing_live_order_rule(changed, expected):
    b, _ = broker()
    book = fine_book(b)
    b.update_books({**b.books, "1": book})
    order = b.submit(intent(price=".095"))
    b.advance(changed)
    b.update_books({**b.books, "1": replace(book, tick=D(".01"), bids=((D(".09"), D(80)),))})
    b.advance(.2)
    assert order.state == expected
    assert order.cancel_requested is None
    if expected == "LIVE":
        assert order.arrival_constraint["tick"] == "0.001" and not order.uncertain
    else:
        assert order.arrival_constraint["tick"] == "0.01" and order.reserved == 0


def test_precision_rejection_receipt_is_delayed_and_not_crossing(tmp_path):
    base, _ = broker()
    store = Store(tmp_path / "precision.sqlite3")
    b = SimBroker("precision", store.emit, ack_ms=1000)
    b.update_books(base.books)
    book = fine_book(b)
    b.update_books({**b.books, "1": book})
    order = b.submit(intent(price=".095"))
    b.advance(.05)
    b.update_books({**b.books, "1": replace(book, tick=D(".01"))})
    b.advance(.1)
    assert order.state == "REJECTED" and order.client == "PRECISION_REJECT_PENDING"
    assert order.reserved == 0
    b.trade(sell("100", price=".09"), "after-rejection")
    assert order.filled == 0
    receipts = b.advance(1, delivery=True)
    assert receipts == [(order.id, "PRECISION_REJECT")]
    store.snapshot("state", {"brokers": {"precision": b.snapshot()}})
    store.close()
    assert reconcile(tmp_path / "precision.sqlite3")["status"] == "PASS"


def test_multiple_physical_requests_keep_separate_precision_outcomes():
    b, _ = broker()
    book = fine_book(b)
    b.update_books({**b.books, "1": book})
    invalid = b.submit(intent(price=".095"), oid="physical-a")
    b.advance(.02)
    aligned = b.submit(intent(price=".09"), oid="physical-b")
    b.advance(.05)
    b.update_books({**b.books, "1": replace(book, tick=D(".01"), bids=((D(".09"), D(80)),))})
    b.advance(.13)
    b.trade(sell("90", price=".09"), "one-volume")
    assert invalid.state == "REJECTED" and invalid.filled == invalid.reserved == 0
    assert aligned.filled == 10 and b.account("M2").inventory == {"YES": D(10)}


def test_missing_arrival_constraint_is_not_a_definite_reject_or_accept():
    b, _ = broker()
    book = fine_book(b)
    b.update_books({**b.books, "1": book})
    unknown = b.submit(intent(price=".095"))
    c = {t: {**value, "known": False} for t, value in b.constraints.items()}
    b.update_constraints(c)
    b.advance(.2)
    assert unknown.state == "ARRIVAL_UNKNOWN" and unknown.filled == 0
    assert unknown.uncertain and unknown.reserved == D(".95") and unknown.client == "UNKNOWN"
    b.update_books({**b.books, "1": replace(book, tick=D(".01"), bids=((D(".09"), D(80)),))})
    c["1"].update({"tick": ".01", "known": True, "authority": "PUBLIC_REST",
                    "available_mono": .2, "received_mono": .2, "source_mono": .2})
    b.update_constraints(c)
    assert unknown.state == "ARRIVAL_UNKNOWN" and unknown.reserved == D(".95")
    valid = b.submit(intent(price=".09"))
    b.advance(.31)
    b.trade(sell("90", price=".09"), "known-after-recovery", source_mono=.31)
    assert valid.filled == 10 and not valid.uncertain and unknown.filled == 0


def test_future_constraint_cannot_prove_a_past_arrival_rejected():
    b, _ = broker()
    b.update_books({**b.books, "1": fine_book(b)})
    order = b.submit(intent(price=".095"))
    b.advance(.05)
    future = {t: dict(c) for t, c in b.constraints.items()}
    future["1"].update({"tick": ".01", "known": True, "authority": "PUBLIC_REST",
                         "available_mono": .2, "received_mono": .2, "source_mono": .2})
    b.update_constraints(future)
    b.advance(.1)
    assert order.state == "ARRIVAL_UNKNOWN" and order.filled == 0
    assert order.reserved == D(".95") and order.reject_reason is None


@pytest.mark.parametrize("processed,expected", [(.055, "REJECTED"), (.155, "ARRIVAL_UNKNOWN")])
async def test_tick_backlog_respects_constraint_availability_before_any_fill(tmp_path, processed, expected):
    e = await initial(tmp_path)
    try:
        await change(e, .005)
        orders = [b.submit(intent(price=".095")) for b in e.brokers.values()]
        wall = e.test_wall
        await send(e, "FRAME", processed, received_mono=.055, received_wall=wall + .055,
                   pending=[.055], raw={
                       "event_type": "tick_size_change", "asset_id": "1",
                       "market": e.meta.condition_id, "old_tick_size": ".001", "new_tick_size": ".01",
                       "timestamp": str((wall + .055) * 1000),
                   })
        await send(e, "FRAME", .2, raw={
            "event_type": "last_trade_price", "market": e.meta.condition_id,
            "asset_id": "1", "timestamp": str((wall + .2) * 1000),
            "price": ".09", "size": "100", "side": "SELL", "trade_id": "post-constraint",
        })
        for order in orders:
            assert order.state == expected and order.filled == 0
            if expected == "REJECTED":
                assert order.reject_reason == "PRICE_INCREMENT" and order.reserved == 0
            else:
                assert order.uncertain and order.reserved == D(".95")
    finally:
        await e.close()


@pytest.mark.parametrize("trade_first", [False, True])
async def test_atomic_frame_tick_fact_precedes_matching_regardless_of_row_order(tmp_path, trade_first):
    e = await initial(tmp_path)
    try:
        await change(e, .005)
        orders = [b.submit(intent(price=".095")) for b in e.brokers.values()]
        tick = {"event_type": "tick_size_change", "asset_id": "1", "market": e.meta.condition_id,
                "old_tick_size": ".001", "new_tick_size": ".01",
                "timestamp": str((e.test_wall + .055) * 1000)}
        trade = {"event_type": "last_trade_price", "asset_id": "1", "market": e.meta.condition_id,
                 "price": ".09", "size": "100", "side": "SELL", "trade_id": "atomic",
                 "timestamp": str((e.test_wall + .11) * 1000)}
        await send(e, "FRAME", .155, received_mono=.12, received_wall=e.test_wall + .12,
                   pending=[.12], raw=[trade, tick] if trade_first else [tick, trade])
        assert all(o.state == "ARRIVAL_UNKNOWN" and o.filled == 0 for o in orders)
        assert all(o.reserved == D(".95") for o in orders)
    finally:
        await e.close()


async def test_m1_precision_receipt_uses_original_nonretryable_rejection(tmp_path):
    feed, clock, b, p, trace, tick = await policy_setup(tmp_path, price=".095")
    try:
        feed.books["1"] = replace(feed.books["1"], tick=D(".001"))
        feed.tick_verified["1"] = D(".001")
        b.update_books(feed.books)
        await tick(0)
        await tick(.01)
        assert len(b.orders) == 1
        clock.now = .05
        b.advance(.05)
        feed.books["1"] = replace(feed.books["1"], tick=D(".01"),
                                   bids=((D(".09"), D(80)),), asks=((D(".10"), D(80)),))
        feed.tick_verified["1"] = D(".01")
        b.update_books(feed.books)
        await tick(.21)
        await tick(.22)
        await tick(.5)
        assert len(b.orders) == 1
        order = next(iter(b.orders.values()))
        assert order.state == "REJECTED" and order.client == "PRECISION_REJECT"
        assert order.filled == order.reserved == 0
        assert p.e.ledger.rows()[0]["state"] == "REJECTED"
        assert not p.market.pending and not p.e.ledger.events("m1_retry_authorized")
        assert not any(k == "RECEIPT_DELIVERED" and x["client"] == "CROSS_REJECT" for k, x in trace)
    finally:
        await p.close()


def test_m234_precision_rejection_does_not_release_direction_attempt():
    from polymaker.m2_config import M2Config
    from polymaker.m1234_sim.policy import M234Policy
    from test_rdp_sm_obi import feed_fixture

    b, _ = broker()
    book = fine_book(b)
    b.update_books({**b.books, "1": book})
    f, _, _ = feed_fixture()
    candidate = {"strategy": "M2", "id": "first", "token": "1", "outcome": "YES",
                 "price": ".095", "deadline": 10, "observation_valid": True}
    candidates = [candidate]
    f.books = b.books
    f.m1 = SimpleNamespace(legal=lambda *a: True)
    f.monotonic = lambda: b.now
    f.m1_watermark = lambda: b.now
    f.hub = SimpleNamespace(candidates=lambda: candidates)
    policy = M234Policy(SimpleNamespace(m234=f), b, lambda *a: None, M2Config())
    policy.poll(20)
    assert len(b.orders) == 1
    b.advance(.05)
    b.update_books({**b.books, "1": replace(book, tick=D(".01"), bids=((D(".09"), D(80)),))})
    b.advance(.1)
    assert next(iter(b.orders.values())).state == "REJECTED"
    f.books = b.books
    candidates[:] = [{**candidate, "id": "second", "price": ".09"}]
    policy.poll(21)
    assert len(b.orders) == 1 and ("M2", "YES") in policy.used
