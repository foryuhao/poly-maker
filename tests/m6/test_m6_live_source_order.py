from decimal import Decimal
from pathlib import Path

import pytest
from helpers import candidate, feed_fixture, frame

from polymaker.m4_types import M6_LEDGER
from polymaker.m6_baseline.signals import M6Feed
from polymaker.taker_runner import strategy_settings

WALL = Decimal("1795027050.283")


def live_feed():
    base, clock = feed_fixture(".08")
    settings = strategy_settings(Path(__file__).resolve().parents[2], M6_LEDGER, None)
    feed = M6Feed(base.meta, base.cfg, taker_settings=settings, monotonic=base.monotonic,
                  wall=lambda: float(WALL + Decimal(str(clock.now))))
    feed.verify_ticks({"1": Decimal(".01"), "2": Decimal(".01")})
    feed.connect()
    return feed, clock


def book(feed, token="1", offset_ms=0):
    bid = ".08" if token == "1" else ".91"
    row = frame(feed, bid, token=token)
    row["timestamp"] = str(WALL * 1000 + offset_ms)
    return row


def change(feed, offset_ms, *, price=".09", size="100", best_ask=".09"):
    return {
        "event_type": "price_change", "market": feed.meta.condition_id,
        "timestamp": str(WALL * 1000 + offset_ms),
        "price_changes": [{"asset_id": "1", "side": "SELL", "price": price,
                           "size": size, "best_bid": ".08", "best_ask": best_ask}],
    }


def trade(feed, offset_ms, *, token="2", hash_digit="a"):
    return {
        "event_type": "last_trade_price", "market": feed.meta.condition_id, "asset_id": token,
        "timestamp": str(WALL * 1000 + offset_ms),
        "side": "BUY", "price": ".91" if token == "2" else ".09", "size": "1",
        "transaction_hash": "0x" + hash_digit * 64,
    }


@pytest.mark.parametrize(("source_ms", "legal"), [
    (Decimal("2.001"), False), (2, True), (-500, True), (Decimal("-500.001"), False),
])
def test_live_integer_source_boundaries_reach_the_send_book_guard(source_ms, legal):
    feed, clock = live_feed()
    feed.apply(book(feed, offset_ms=source_ms))
    assert feed.research.good["1"] is legal
    assert feed.m1.legal(feed.books["1"], clock.now) is legal
    if not legal:
        assert feed.research.reasons["1"] == "EVENT_SOURCE_AGE"


@pytest.mark.parametrize(("receipt_age", "legal"), [(0.2, True), (0.200001, False)])
def test_live_integer_receipt_age_boundary(receipt_age, legal):
    feed, clock = live_feed()
    feed.apply(book(feed))
    clock.now = receipt_age
    feed.m1.tick()
    assert feed.m1.legal(feed.books["1"], clock.now) is legal


def ready_with_trade():
    feed, clock = live_feed()
    feed.apply([book(feed), book(feed, "2")])
    opportunity = candidate(feed, ".08")
    clock.now = 0.04
    feed.apply(trade(feed, 40))
    assert feed.research.counts["NORMALIZED_TRADE"] == 2
    return feed, clock, opportunity


@pytest.mark.parametrize("bbo_only", [False, True])
def test_live_noop_within_20ms_keeps_candidate_without_refreshing_book(bbo_only):
    feed, clock, opportunity = ready_with_trade()
    research = feed.research
    saved_book = research.books["1"]
    before = saved_book.source, saved_book.received, tuple(research.flows["1"].coverage)
    clock.now = 0.045
    row = change(feed, 20)
    if bbo_only:
        row = {"event_type": "best_bid_ask", "market": row["market"], "timestamp": row["timestamp"],
               "asset_id": "1", "best_bid": ".08", "best_ask": ".09"}
    feed.apply(row)
    assert research.counts["LATE_QUOTE_NOOP_IGNORED"] == 1
    assert research.counts["NORMALIZED_TRADE"] == 2
    assert research.sources["1"] == float(WALL + Decimal(".04"))
    assert (saved_book.source, saved_book.received, tuple(research.flows["1"].coverage)) == before
    assert feed.m1.active_reason((opportunity.episode_id,)) is None


@pytest.mark.parametrize("late_input", [
    "deleted_ask", "changed_size", "changed_deeper_size", "bbo_mismatch", "outside_20ms",
    "intermediate_change", "prior_to_book", "snapshot", "tick", "direct_trade", "mirrored_trade",
])
def test_unproven_late_evidence_invalidates_and_requires_new_snapshot(late_input):
    feed, clock, opportunity = ready_with_trade()
    research = feed.research
    clock.now = 0.045
    row = change(feed, 36)
    delta = row["price_changes"][0]
    if late_input == "deleted_ask":
        delta.update(size="0", best_ask=".10")
    elif late_input == "changed_size":
        delta["size"] = "99"
    elif late_input == "changed_deeper_size":
        delta.update(price=".10", size="1")
    elif late_input == "bbo_mismatch":
        delta["best_bid"] = ".07"
    elif late_input == "outside_20ms":
        row = change(feed, Decimal("19.999"))
    elif late_input == "intermediate_change":
        row["price_changes"].insert(0, {**delta, "size": "0"})
    elif late_input == "prior_to_book":
        # An exact match to a newer quote cannot prove that an older delta
        # had no effect on the causal book or flow sequence before that quote.
        feed.apply(book(feed, offset_ms=41))
        row = change(feed, 40)
    elif late_input == "snapshot":
        row = book(feed, offset_ms=36)
    elif late_input == "tick":
        row = {"event_type": "tick_size_change", "market": row["market"], "timestamp": row["timestamp"],
               "asset_id": "1", "new_tick_size": ".001"}
    else:
        token = "1" if late_input == "direct_trade" else "2"
        row = trade(feed, 36, token=token, hash_digit="b")

    feed.apply(row)
    assert research.counts["LATE_QUOTE_NOOP_IGNORED"] == 0
    assert research.counts["NORMALIZED_TRADE"] == 2
    assert research.reasons["1"] == "UNRECONSTRUCTABLE_SOURCE_ORDER"
    assert not research.books["1"].complete
    assert feed.m1.active_reason((opportunity.episode_id,)) == "M6_EVIDENCE_ENDED"

    feed.apply(change(feed, 42))
    assert research.reasons["1"] == "DELTA_BEFORE_SNAPSHOT"
    feed.apply(book(feed, offset_ms=43))
    assert research.good["1"]
    assert feed.m1.active_reason((opportunity.episode_id,)) == "M6_EVIDENCE_ENDED"


def test_baseline_keeps_original_strict_rule_for_same_noop():
    feed, clock = feed_fixture(".08")
    research = feed.research
    clock.now = 0.04
    research.sources["1"] = feed.wall()
    row = change(feed, 36)
    row["timestamp"] = str((Decimal(str(feed.wall())) - Decimal(".004")) * 1000)
    feed.apply(row)
    assert research.reasons["1"] == "UNRECONSTRUCTABLE_SOURCE_ORDER"
    assert research.counts["LATE_QUOTE_NOOP_IGNORED"] == 0
