"""R1: hand-derived source/receipt/processing attribution, never uncertain to hide overfill."""

from dataclasses import replace
from decimal import Decimal as D

import pytest

from m1234_fixtures import fixture_events
from polymaker.m1234_sim import INPUT_SCHEMA
from polymaker.m1234_sim.engine import Engine
from polymaker.m1234_sim.store import read_audit
from test_m1234_sim_core import broker, intent, sell


@pytest.mark.parametrize("processed", [.25, 1.25, 3.0])
async def test_received_trade_backlog_fills_exactly_five_in_both_scenarios(tmp_path, processed):
    engine = Engine(tmp_path / "backlog")
    for event in fixture_events()[:3]:
        await engine.ingest(event)
    wall = engine.clock.wall
    orders = {
        name: b.submit(replace(intent(), market=engine.meta.slug), oid="hand-order")
        for name, b in engine.brokers.items()
    }

    async def event(kind, at, **payload):
        await engine.ingest({
            "schema": INPUT_SCHEMA, "seq": engine.seq + 1, "generation": 1, "kind": kind,
            "available_mono": at, "available_wall": wall + at, **payload,
        })

    try:
        await event("RESOURCE", .1)
        await event("FRAME", .2, received_mono=.2, received_wall=wall + .2, raw={
            "event_type": "book", "market": engine.meta.condition_id, "asset_id": "1",
            "timestamp": str((wall + .2) * 1000),
            "bids": [{"price": ".49", "size": "10"}],
            "asks": [{"price": ".52", "size": "20"}],
        })
        await event("FRAME", processed, received_mono=.22, received_wall=wall + .22,
                    pending=[.22], raw={
                        "event_type": "last_trade_price", "market": engine.meta.condition_id,
                        "asset_id": "1", "timestamp": str((wall + .21) * 1000),
                        "price": ".50", "size": "85", "side": "SELL", "trade_id": "hand-85",
                    })
        await event("TIMER", max(1.8, processed + .1), pending=[])
        engine.store.commit()
        audit = list(read_audit(engine.store.path))
        for scenario, order in orders.items():
            account = engine.brokers[scenario].account("M2")
            assert order.filled == 5 and not order.uncertain
            assert order.reserved == D("2.50") and order.spent == D("2.50")
            assert account.cash == D("17.50") and account.inventory == {"YES": D(5)}
            assert sum(D(row["payload"]["quantity"]) for row in audit
                       if row["kind"] == "MODELLED_FILL"
                       and row["payload"]["scenario"] == scenario) == 5
        assert not any(row["kind"] == "ASSUMED_AHEAD_CANCEL" for row in audit)
    finally:
        await engine.close()


@pytest.mark.parametrize("fraction", ["0", ".5"])
@pytest.mark.parametrize("ordering", ["trade-first", "depth-first", "same-frame"])
def test_source_orderings_match_one_economic_volume(fraction, ordering):
    b, _ = broker(ahead_cancel_fraction=fraction)
    order = b.submit(intent())
    b.advance(.1, attribution_frontier=0)
    changed = {**b.books, "1": replace(b.books["1"], bids=((D(".49"), D(10)),))}
    b.advance(.25, attribution_frontier=.15)
    if ordering == "trade-first":
        b.trade(sell(), "trade-85", source_mono=.21)
    b.update_books(changed, source_times={"1": .2, "2": .2})
    if ordering != "trade-first":
        b.trade(sell(), "trade-85", source_mono=.21)
    b.advance(2, attribution_frontier=1.3)
    assert order.filled == 5 and not order.uncertain
    assert order.reserved == D("2.50")


def test_credit_retirement_uses_source_frontier_not_processing_time():
    b, trace = broker(ahead_cancel_fraction=".5")
    order = b.submit(intent())
    b.advance(.1, attribution_frontier=0)
    b.advance(.25, attribution_frontier=.1)
    b.trade(sell(), "received-at-022", source_mono=.21)
    b.advance(3, attribution_frontier=.15)
    assert len(b.trade_credits) == 1
    b.update_books({**b.books, "1": replace(b.books["1"], bids=((D(".49"), D(10)),))},
                   source_times={"1": .2, "2": .2})
    b.advance(3.1, attribution_frontier=1.3)
    assert order.filled == 5 and not order.uncertain
    attribution = [payload for kind, payload in trace if kind == "DEPTH_TRADE_ATTRIBUTION"]
    assert len(attribution) == 1 and D(attribution[0]["quantity"]) == 80
    b.advance(3.2, attribution_frontier=2.3)
    assert not b.trade_credits and not b.depth_decreases


@pytest.mark.parametrize("offset", [1.0, 1.0001])
def test_inclusive_attribution_boundary_retains_matching_credit(offset):
    b, trace = broker(ahead_cancel_fraction=".5")
    b.submit(intent())
    b.advance(.1, attribution_frontier=.1 - .502)
    b.advance(.2, attribution_frontier=.2 - .502)
    b.update_books({**b.books, "1": replace(b.books["1"], bids=((D(".50"), D(40)),))},
                   source_times={"1": .2})
    b.advance(1.4, attribution_frontier=.2)
    b.trade(sell("40"), "boundary-print", source_mono=.2 + offset)
    b.advance(2, attribution_frontier=1.3)
    assigned = sum(D(p["quantity"]) for k, p in trace if k == "ASSUMED_AHEAD_CANCEL")
    assert assigned == (D(0) if offset == 1 else D(20))
    # This test does not mistake the cancellation hypothesis itself for a fill.
    assert all(o.filled == 0 for o in b.orders.values())


@pytest.mark.parametrize("fraction", ["0", ".5"])
def test_multiple_orders_and_crossing_price_share_volume_during_backlog(fraction):
    b, _ = broker(ahead_cancel_fraction=fraction)
    book = replace(b.books["1"], bids=((D(".50"), D(80)), (D(".49"), D(20))))
    b.update_books({**b.books, "1": book})
    first = b.submit(intent(size="5"))
    second = b.submit(intent(size="5", price=".49"))
    b.advance(.1, attribution_frontier=.1 - .502)
    b.advance(.2, attribution_frontier=.2 - .502)
    b.update_books({**b.books, "1": replace(book, bids=((D(".49"), D(20)),))},
                   source_times={"1": .2})
    b.advance(1.25, attribution_frontier=.22 - .502)
    b.trade(sell("87", price=".49"), "one-crossing-volume", source_mono=.21)
    b.advance(1.8, attribution_frontier=1.3)
    assert first.filled == 5 and second.filled == 0
    b.trade(sell("23", price=".49", trade_id="second-print"), "remaining", source_mono=1.4)
    assert second.filled == 5 and not first.uncertain and not second.uncertain


def test_real_gap_discards_attribution_hypotheses_and_keeps_funds():
    b, trace = broker(ahead_cancel_fraction=".5")
    order = b.submit(intent())
    b.advance(.1)
    b.update_books({**b.books, "1": replace(b.books["1"], bids=())})
    assert b.depth_decreases
    b.gap("REAL_MISSING_INTERVAL", missing_since=.15)
    b.advance(2)
    assert not b.depth_decreases and not b.trade_credits
    assert order.filled == 0 and order.uncertain and order.reserved == 5
    assert not any(k in {"MODELLED_FILL", "ASSUMED_AHEAD_CANCEL"} for k, _ in trace)


def test_attribution_storage_has_explicit_fail_visible_bound(monkeypatch):
    import polymaker.m1234_sim.broker as module

    monkeypatch.setattr(module, "MAX_ATTRIBUTION_RECORDS", 2)
    b, _ = broker(ahead_cancel_fraction=".5")
    b.submit(intent())
    b.advance(.1, attribution_frontier=0)
    for i in range(2):
        b.trade(sell("1", trade_id=str(i)), str(i), source_mono=.1 + i / 100)
    with pytest.raises(RuntimeError, match="TRADE_ATTRIBUTION_CAPACITY"):
        b.trade(sell("1", trade_id="overflow"), "overflow", source_mono=.2)
    assert len(b.trade_credits) == 2


async def test_order_arrival_still_precedes_a_later_available_frame(tmp_path):
    engine = Engine(tmp_path / "physical-order")
    for event in fixture_events()[:3]:
        await engine.ingest(event)
    wall = engine.clock.wall
    orders = [b.submit(intent()) for b in engine.brokers.values()]
    try:
        await engine.ingest({
            "schema": INPUT_SCHEMA, "seq": engine.seq + 1, "kind": "FRAME", "generation": 1,
            "available_mono": .15, "available_wall": wall + .15,
            "received_mono": .15, "received_wall": wall + .15,
            "raw": {"event_type": "book", "asset_id": "1", "market": engine.meta.condition_id,
                    "timestamp": str((wall + .15) * 1000),
                    "bids": [{"price": ".48", "size": "80"}],
                    "asks": [{"price": ".49", "size": "80"}]},
        })
        assert all(o.state == "LIVE" for o in orders)
    finally:
        await engine.close()
