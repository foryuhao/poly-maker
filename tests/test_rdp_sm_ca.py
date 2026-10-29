"""CA SOP v1.1 through the same ordered public WS/book boundary as BA."""

import asyncio
import json
from copy import deepcopy
from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from polymaker.config import RDPBAConfig, RDPCAObserveConfig, RDPSMConfig
from polymaker.rdp_sm_marketdata import SessionFeed
from test_rdp_sm_ba import absorption, depth, trade
from test_rdp_sm_obi import feed_fixture


def ca_feed(enabled=True, **settings):
    original, clock, rows = feed_fixture()
    cfg = RDPBAConfig(**{"signal_lifetime_sec": 3, "max_book_age_ms": 200, **settings})
    feed = SessionFeed(original.meta, original.cfg, monotonic=original.monotonic,
        wall=original.wall, ba_cfg=cfg, opposite_ba_cfg=cfg,
        ca_config=RDPCAObserveConfig(enabled=enabled), ca_session_id="test")
    feed.verify_ticks({t: b.tick for t, b in original.books.items()})
    feed.connect()
    feed.apply(rows)
    return feed, clock


def buy(feed, token="1", **changes):
    return trade(feed, token, **{"side": "BUY", "price": str(feed.books[token].best_ask),
                                "size": "10", **changes})


def heartbeat(feed):
    feed.apply([{"event_type": "book", "market": feed.meta.condition_id, "asset_id": token,
        "timestamp": str(feed.wall()*1000),
        "bids": [{"price": str(p), "size": str(q)} for p, q in b.bids],
        "asks": [{"price": str(p), "size": str(q)} for p, q in b.asks]}
        for token, b in feed.books.items()])


def fresh_at(feed, clock, target):
    while clock.now+.1 < target:
        clock.now += .1
        heartbeat(feed)
    clock.now = target
    heartbeat(feed)


def state(feed, side="YES"):
    return feed.observe_ca("snapshot")["outcomes"][side]


def events(feed, kind):
    return [r for r in feed.ca.records if r["event"] == kind]


@pytest.mark.parametrize("token,outcome", [("1", "YES"), ("2", "NO")])
def test_ca_independent_of_depth_refill_and_direction(token, outcome):
    f, clock = ca_feed()
    clock.now = .01
    f.apply(trade(f, token))
    clock.now = .02
    f.apply(buy(f, token))
    s = state(f, outcome)
    assert s["confirmed"] and s["candidate_usable"]
    assert f.ca.states[token].sell_shares == 5 and f.ca.states[token].buy_shares == 10
    assert f.ba.states[token].ba_confirmed_mono is None
    assert f.ba.evaluate(f.snapshot()).direction is None
    assert len(events(f, "CA_CONFIRMED")) == 1
    assert events(f, "CA_STARTED")[0]["payload"]["episode"]["sell_shares"] == "5"


def test_ordered_sell_threshold_no_buy_backfill_and_intervening_sell():
    f, clock = ca_feed()
    clock.now = .01
    f.apply([buy(f), trade(f, size="3"), buy(f, size="6"), trade(f, size="2"),
             buy(f, size="4"), trade(f, size="1"), buy(f, size="6", transaction_hash="later")])
    ep = f.ca.states["1"]
    assert ep.tC == .01 and ep.tS == .01  # ordinal, not a strict timestamp increase
    assert ep.sell_shares == 6 and ep.buy_shares == 10
    assert ep.threshold_sell["size"] == "2"
    assert ep.confirming_buy_ordinal > ep.sell_threshold_ordinal > ep.start_ordinal


@pytest.mark.parametrize("size,confirmed", [("9.99", False), ("10", True), ("12.3", True)])
def test_buy_threshold_is_independent_decimal_quantity(size, confirmed):
    f, _ = ca_feed()
    f.apply(trade(f))
    f.apply(buy(f, size=size))
    assert state(f)["confirmed"] is confirmed
    assert f.ca.states["1"].buy_shares == D(size)


@pytest.mark.parametrize("side,size", [("SELL", "4.99"), ("BUY", "100")])
def test_missing_sell_threshold_never_confirms(side, size):
    f, _ = ca_feed()
    if side == "SELL":
        f.apply(trade(f, size=size))
    else:
        f.apply(buy(f, size=size))
    f.apply(buy(f))
    assert not state(f)["confirmed"]


@pytest.mark.parametrize("fault", ["duplicate", "unknown_side", "negative", "nan", "missing_time",
                                   "future", "wrong_sell_price", "wrong_buy_price"])
def test_bad_trade_invalidates_ca_without_manufacturing_confirmation(fault):
    f, clock = ca_feed()
    clock.now = .01
    sell = trade(f)
    f.apply(sell)
    row = buy(f)
    if fault == "duplicate":
        row = sell
    elif fault == "unknown_side":
        row["side"] = "BID"
    elif fault == "negative":
        row["size"] = "-1"
    elif fault == "nan":
        row["size"] = "NaN"
    elif fault == "missing_time":
        row.pop("timestamp")
    elif fault == "future":
        row["timestamp"] = str((f.wall()+1)*1000)
    elif fault == "wrong_sell_price":
        row = trade(f, price=str(f.books["1"].best_ask))
    else:
        row["price"] = str(f.books["1"].best_bid)
    f.apply(row)
    assert f.ca.states["1"].end_reason is not None
    assert not state(f)["candidate_usable"] and not state(f)["confirmed"]
    assert not events(f, "CA_CONFIRMED")


def test_buy_uses_actual_pre_event_ask_and_ask_change_alone_is_allowed():
    f, clock = ca_feed()
    clock.now = .01
    f.apply(trade(f))
    clock.now = .02
    f.apply(depth(f, "30", price=".51", side="SELL"))
    assert f.ca.states["1"].end_reason is None
    f.apply(buy(f))
    assert state(f)["confirmed"]
    assert f.ca.states["1"].confirming_buy["pre_ask"] == "0.51"


def test_buy_cannot_use_a_later_book_in_same_socket_frame():
    f, _ = ca_feed()
    f.apply(trade(f))
    # Before the later depth update, .51 is NOT the resting ask (.52).
    f.apply([buy(f, price=".51"), depth(f, "30", price=".51", side="SELL")])
    assert not state(f)["confirmed"]
    assert f.ca.states["1"].end_reason == "CA_BUY_NOT_AT_ASK"


def test_anchor_change_and_return_requires_a_new_sell():
    f, clock = ca_feed()
    clock.now = .01
    f.apply(trade(f))
    old = f.ca.states["1"].episode_id
    clock.now = .02
    f.apply(depth(f, "80", price=".51"))
    f.apply(depth(f, "0", price=".51"))
    f.apply(buy(f))
    assert f.ca.states["1"].episode_id == old and not state(f)["confirmed"]
    clock.now = .03
    f.apply(trade(f))
    f.apply(buy(f))
    assert f.ca.states["1"].episode_id != old and state(f)["confirmed"]


def test_bbo_depth_contradiction_is_not_treated_as_a_proven_new_ask():
    f, _ = ca_feed()
    f.apply(trade(f))
    f.apply({"event_type": "best_bid_ask", "market": f.meta.condition_id,
             "asset_id": "1", "timestamp": str(f.wall()*1000),
             "best_bid": ".50", "best_ask": ".53"})
    assert f.ca.states["1"].end_reason == "CA_BOOK_UNPROVEN"


@pytest.mark.parametrize("when,confirmed", [(.749, True), (.75, False), (.751, False)])
def test_observation_deadline_is_strict_and_from_first_sell(when, confirmed):
    f, clock = ca_feed()
    f.apply(trade(f))
    fresh_at(f, clock, .6)
    f.apply(trade(f, size="1"))  # does not move t0 or observation deadline
    fresh_at(f, clock, when)
    f.apply(buy(f))
    assert state(f)["confirmed"] is confirmed
    assert f.ca.states["1"].t0 == 0
    if not confirmed:
        assert f.ca.states["1"].end_reason == "CA_OBSERVATION_TIMEOUT"


def test_queued_buy_cannot_confirm_after_observation_deadline():
    f, clock = ca_feed(observation_sec=.05)
    clock.now = .01
    f.apply(trade(f))
    clock.now = .02
    row, wall = buy(f), f.wall()
    clock.now = .09
    f.apply(row, received_mono=.02, received_wall=wall)
    assert f.ca.states["1"].end_reason == "CA_OBSERVATION_TIMEOUT"
    assert not state(f)["confirmed"]


def test_stale_gap_is_latched_before_same_price_depth_refresh():
    f, clock = ca_feed()
    f.apply(trade(f))
    clock.now = .201
    heartbeat(f)
    f.apply(buy(f))
    assert f.ca.states["1"].end_reason == "CA_BOOK_STALE"
    assert not state(f)["confirmed"]
    f.apply(trade(f))
    f.apply(buy(f, transaction_hash="new-segment"))
    assert state(f)["confirmed"]


def test_queued_first_sell_cannot_use_an_old_local_book():
    f, clock = ca_feed()
    row, wall = trade(f), f.wall()
    clock.now = .201
    f.apply(row, received_mono=0, received_wall=wall)
    assert not f.ca.states


@pytest.mark.parametrize("when,usable", [(2.999, False), (3, False)])
def test_confirmation_never_extends_total_deadline(when, usable):
    f, clock = ca_feed()
    f.apply(trade(f))
    clock.now = .01
    f.apply(buy(f))
    fresh_at(f, clock, when)
    assert state(f)["candidate_usable"] is usable
    assert f.ca.states["1"].deadline == 3
    assert (f.ca.states["1"].end_reason == "CA_EXPIRED") is (when == 3)
    assert len(events(f, "CA_CONFIRMED")) == 1


def test_candidate_uses_confirmation_buy_age_not_old_sell_and_never_renews():
    f, clock = ca_feed()
    f.apply(trade(f))
    fresh_at(f, clock, .7)
    f.apply(buy(f))
    assert state(f)["candidate_usable"]
    original = deepcopy(f.ca.states["1"].confirming_buy)
    fresh_at(f, clock, 1.01)
    assert state(f)["unavailable_reason"] == "CA_ENTRY_STALE"
    fresh_at(f, clock, 1.3)
    f.apply(buy(f, transaction_hash="does-not-renew"))
    assert state(f)["unavailable_reason"] == "CA_BUY_STALE"
    assert f.ca.states["1"].confirming_buy == original
    assert len(events(f, "CA_CONFIRMED")) == 1


def test_both_ca_sides_are_recorded_and_ba_conflict_does_not_clear_ca():
    f, _ = ca_feed()
    for token in ("1", "2"):
        absorption(f, token)
        f.apply(buy(f, token))
    assert f.ba.evaluate(f.snapshot()).direction is None
    assert all(state(f, s)["candidate_usable"] for s in ("YES", "NO"))
    f.ba.invalidate("BA_BOTH_SIDES_VALID")
    assert all(state(f, s)["confirmed"] for s in ("YES", "NO"))


def test_ba_consumption_does_not_consume_or_reset_ca():
    f, _ = ca_feed()
    absorption(f)
    f.apply(buy(f))
    signal = f.ba.evaluate(f.snapshot())
    claim = f.ba.claim(signal)
    f.ba.consume(claim)
    f.ba.release(claim)
    assert state(f)["candidate_usable"]
    assert f.ba.evaluate(f.snapshot()).direction is None


def test_confirmed_segment_freezes_sell_and_buy_totals_until_it_ends():
    f, clock = ca_feed()
    f.apply(trade(f))
    f.apply(buy(f))
    ep = f.ca.states["1"]
    frozen = deepcopy(ep)
    clock.now = .03
    f.apply(trade(f, size="2"))
    f.apply(buy(f, size="20"))
    assert ep == frozen and len(events(f, "CA_CONFIRMED")) == 1


def test_tick_change_retires_ca_even_if_bid_stays_the_same():
    f, _ = ca_feed()
    f.apply(trade(f))
    f.apply({"event_type": "tick_size_change", "market": f.meta.condition_id, "asset_id": "1",
             "old_tick_size": ".01", "new_tick_size": ".001"})
    assert f.ca.states["1"].end_reason == "CA_TICK_CHANGED"


@pytest.mark.parametrize("rollback,ignored", [(1, True), (10, True), (11, False)])
@pytest.mark.parametrize("kind", ["trade", "depth"])
def test_late_events_do_not_add_volume_refresh_books_or_reset_deadlines(rollback, ignored, kind):
    f, clock = ca_feed()
    clock.now = .02
    f.apply(trade(f))
    ep = f.ca.states["1"]
    row = buy(f) if kind == "trade" else depth(f, "90")
    row["timestamp"] = str(D(str(f.wall()))*1000-rollback)
    before = f.books["1"]
    f.apply(row)
    assert ep.buy_shares == 0 and ep.deadline == 3.02
    assert f.books["1"] == before
    assert (ep.end_reason is None) is ignored


def test_evicted_history_cannot_reaccept_uncertain_duplicates():
    f, _ = ca_feed()
    f.ca.trade_history_limit = 2
    for size in (2, 3, 4, 2):
        f.apply(trade(f, size=str(size)))
    assert not state(f)["candidate_usable"]
    assert f.ca.rejected["CA_TRADE_HISTORY_UNCERTAIN"]


def test_disconnect_and_generation_change_require_new_sell():
    f, _ = ca_feed()
    f.apply(trade(f))
    f.apply(buy(f))
    old = f.ca.states["1"]
    f.invalidate("WS_DISCONNECTED")
    assert old.end_reason and not state(f)["candidate_usable"]
    f.connect()
    assert not f.ca.states and f.generation == 2


@pytest.mark.parametrize("phase,elapsed", [("NORMAL", 20), ("REPAIR", 180), ("NORMAL", 250)])
def test_observation_continues_across_phase_and_trading_cutoffs(phase, elapsed):
    f, _ = ca_feed()
    f.observe_ca("set_context", phase, elapsed)
    f.apply(trade(f))
    f.apply(buy(f))
    assert state(f)["confirmed"]
    event = events(f, "CA_CONFIRMED")[0]
    assert event["coordinator_phase"] == phase and event["market_elapsed_s"] == elapsed


def test_ca_error_only_marks_observer_unavailable(monkeypatch):
    f, _ = ca_feed()
    def broken(*args):
        raise RuntimeError("observer only")
    monkeypatch.setattr(f.ca, "before_event", broken)
    absorption(f)
    assert f.ba.evaluate(f.snapshot()).direction == "YES"
    assert f.connected and f.failure_count == 0
    assert not f.observe_ca("snapshot")["data_complete"]


@pytest.mark.parametrize("enabled", [True, False])
def test_ca_presence_and_strict_failures_do_not_change_ba_or_shared_books(enabled):
    f, clock = ca_feed(enabled=enabled)
    baseline, baseclock = ca_feed(enabled=False)
    for step in ("absorb", "buy", "wrong-buy", "small-late", "depth", "conflict"):
        clock.now += .01
        baseclock.now = clock.now
        if step == "absorb":
            absorption(f)
            absorption(baseline)
        elif step == "conflict":
            absorption(f, "2")
            absorption(baseline, "2")
        else:
            row = buy(f) if step in {"buy", "wrong-buy"} else depth(f, "81")
            if step == "wrong-buy":
                row["price"] = ".50"
            if step == "small-late":
                row["timestamp"] = str(D(str(f.books["1"].source_time))*1000-1)
            f.apply(deepcopy(row))
            baseline.apply(deepcopy(row))
        assert f.snapshot() == baseline.snapshot()
        assert f.ba.diagnostics() == baseline.ba.diagnostics()
        assert f.ba.evaluate(f.snapshot()) == baseline.ba.evaluate(baseline.snapshot())


async def test_independent_timer_expires_silent_book_without_a_new_event(monkeypatch):
    f, clock = ca_feed()
    f.apply(trade(f))
    async def connection():
        await asyncio.Event().wait()
    monkeypatch.setattr(f, "_connection", connection)
    f.task = asyncio.create_task(f.run())
    clock.now = .201
    try:
        async with asyncio.timeout(1):
            while f.ca.states["1"].end_reason is None:  # noqa: ASYNC110 - observe the independent production timer
                await asyncio.sleep(.01)
        assert f.ca.states["1"].end_reason == "CA_BOOK_STALE"
    finally:
        await f.close()


@pytest.mark.parametrize("value", [0, -1, "NaN", "Infinity"])
def test_invalid_buy_threshold_rejected(value):
    with pytest.raises(ValidationError):
        RDPCAObserveConfig(min_buy_shares=value)


def test_config_requires_explicit_ba_observation_only_and_freezes_inheritance():
    assert not RDPCAObserveConfig().enabled
    for extra in ({"enabled": "true"}, {"mode": "trade"}):
        with pytest.raises(ValidationError):
            RDPCAObserveConfig(**extra)
    with pytest.raises(ValidationError):
        RDPSMConfig(normal_entry_mode="obi_v1", ca_observe={"enabled": True})
    f, _ = ca_feed()
    f.ba.cfg.min_sell_shares = D(999)
    f.apply(trade(f))
    f.apply(buy(f))
    assert state(f)["confirmed"] and f.ca.parameters["S_min"] == "5"
    json.dumps(f.ca_diagnostics(), allow_nan=False)  # includes only JSON-safe CA evidence
