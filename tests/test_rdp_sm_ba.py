"""Small causal BA examples and the existing real SDK/ownership execution fixture."""

import asyncio
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal as D
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from polymaker.config import RDPBAConfig, RDPSMConfig
from polymaker.domain import Side, TradeState
from polymaker.rdp_sm import session_capital_limit
from polymaker.rdp_sm_ba import BidAbsorption
from polymaker.rdp_sm_marketdata import SessionFeed
from polymaker.rdp_sm_normal import BANormalController
from polymaker.rdp_sm_signal import evaluate
from polymaker.state.tracker import TradeEvent
from test_rdp_sm_m1 import CID
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_obi import feed_fixture
from test_rdp_sm_obi_execution import cleanup, setup_obi
from test_rdp_sm_obi_execution import env as old_env
from test_rdp_sm_repair import reflect_positions


@pytest.fixture
def env(monkeypatch, tmp_path):
    yield from old_env.__wrapped__(monkeypatch, tmp_path)


def ba_feed(**settings):
    original, clock, rows = feed_fixture()
    feed = SessionFeed(original.meta, original.cfg, monotonic=original.monotonic,
                       wall=original.wall, ba_cfg=RDPBAConfig(**settings))
    feed.verify_ticks({t: b.tick for t, b in original.books.items()})
    feed.connect()
    feed.apply(rows)
    return feed, clock, rows


def trade(feed, token="1", **overrides):
    return {"event_type": "last_trade_price", "market": CID, "asset_id": token,
            "timestamp": str(feed.wall()*1000), "price": str(feed.books[token].best_bid),
            "side": "SELL", "size": "5", **overrides}


def depth(feed, size, token="1", **overrides):
    return {"event_type": "price_change", "market": CID,
            "timestamp": str(feed.wall()*1000), "price_changes": [{
                "asset_id": token, "side": "BUY", "price": str(feed.books[token].best_bid),
                "size": str(size), **overrides}]}


def absorption(feed, token="1"):
    size = feed.books[token].best_bid_size
    feed.apply([trade(feed, token), depth(feed, size-5, token), depth(feed, size, token)])
    return feed.ba.evaluate(feed.snapshot())


@pytest.mark.parametrize("fault,reason", [
    ("direction", "BA_TRADE_DIRECTION_OR_QUANTITY_UNKNOWN"),
    ("future", "BA_SOURCE_CLOCK_INVALID"),
    ("huge_timestamp", "BA_SOURCE_CLOCK_INVALID"),
    ("order", "BA_EVENT_OUT_OF_ORDER"),
    ("price", "BA_SELL_NOT_FRESH_AT_BID"),
    ("book_age", "BA_SELL_NOT_FRESH_AT_BID"),
])
def test_rejected_sell_before_episode_is_visible_without_changing_admission(fault, reason):
    feed, clock, _ = ba_feed()
    clock.now = .01
    row = trade(feed)
    if fault == "direction":
        row["side"] = "BID"
    elif fault == "future":
        row["timestamp"] = str((feed.wall()+.01)*1000)
    elif fault == "huge_timestamp":
        row["timestamp"] = "1e999"
    elif fault == "order":
        row["timestamp"] = str((feed.wall()-.02)*1000)
    elif fault == "price":
        row["price"] = ".52"
    else:
        clock.now = .15
        row = trade(feed)
    feed.apply(row)
    diagnostic = feed.diagnostics()["ba"]
    assert diagnostic["counts"]["trade_events"] == 1
    assert diagnostic["rejected"]["last_trade_price:"+reason] == 1
    sample = diagnostic["last_rejections"]["last_trade_price:"+reason]
    import json

    json.dumps(diagnostic, default=str, allow_nan=False)
    assert sample["reason"] == reason and sample["generation"] == feed.generation
    assert sample["best_bid"] == "0.50" and sample["token_id"] == "1"
    assert not feed.ba.states and not feed.ba.drain()
    assert not feed.ba.evaluate(feed.snapshot()).direction


def test_trade_counts_distinguish_buy_only_and_repeat_rejection_samples_are_bounded():
    feed, clock, _ = ba_feed()
    clock.now = .01
    feed.apply(trade(feed, side="BUY"))
    feed.apply(trade(feed, price=".52"))
    clock.now = .02
    feed.apply(trade(feed, price=".52"))
    diagnostic = feed.diagnostics()["ba"]
    assert diagnostic["counts"]["buy_events"] == 1
    assert diagnostic["counts"]["sell_events"] == 2
    assert diagnostic["rejected"] == {"last_trade_price:BA_SELL_NOT_FRESH_AT_BID": 2}
    assert len(diagnostic["last_rejections"]) == 1
    assert diagnostic["last_rejections"]["last_trade_price:BA_SELL_NOT_FRESH_AT_BID"]["received_mono"] == .02
    assert not feed.ba.drain()


@pytest.mark.parametrize("transport_ms,signal", [(145, True), (510, False)])
def test_source_transport_age_uses_sell_budget_and_local_book_uses_receipt_budget(transport_ms, signal):
    feed, clock, rows = ba_feed()
    clock.now = 1.
    stamp = str((feed.wall()-transport_ms/1000)*1000)
    rows = deepcopy(rows)
    for row in rows:
        row["timestamp"] = stamp
    feed.apply(rows)
    feed.apply([trade(feed, timestamp=stamp), depth(feed, 75) | {"timestamp": stamp},
                depth(feed, 80) | {"timestamp": stamp}])
    assert (feed.ba.evaluate(feed.snapshot()).direction == "YES") == signal
    if signal:
        assert feed.ba.states["1"].sell_volume == 5
        # The final quote/POST guard must use the same fresh receipt clock.
        assert feed.ba.legal(feed.books["1"], clock.now)
        clock.now += .101
        assert not feed.ba.legal(feed.books["1"], clock.now)
        assert not feed.ba.evaluate(feed.snapshot()).direction
    else:
        assert not feed.ba.states


def test_queued_trade_cannot_treat_an_old_local_book_as_fresh():
    feed, clock, _ = ba_feed()
    clock.now = .01
    row = trade(feed)
    wall = feed.wall()
    clock.now = .15
    feed.apply(row, received_mono=.01, received_wall=wall)
    assert not feed.ba.states
    assert feed.ba.diagnostics()["rejected"] == {"last_trade_price:BA_SELL_NOT_FRESH_AT_BID": 1}


@pytest.mark.parametrize("token,outcome", [("1", "YES"), ("2", "NO")])
def test_batched_sell_depletion_refill_preserves_intermediate_depth(token, outcome):
    feed, clock, _ = ba_feed()
    clock.now = .01
    before = feed.books[token].bids
    signal = absorption(feed, token)
    assert signal.label == "VALID_"+outcome
    assert feed.books[token].bids == before  # Whole-frame diff alone misses this BA.
    ep = feed.ba.selected
    assert ep.sell_volume == ep.depletion_shares == ep.refill_shares == 5
    assert ep.signal_start_mono == .01 and ep.order_deadline == 2.01
    assert evaluate(feed.snapshot()).direction == "YES"  # NO BA is independent of OBI.
    claim = feed.ba.claim(signal)
    feed.ba.consume(claim)
    feed.ba.release(claim)
    assert not feed.ba.evaluate(feed.snapshot()).direction
    assert feed.ba.claim(signal) is None


@pytest.mark.parametrize("missing", ["sell", "depletion", "refill", "sell_qty", "depletion_qty", "refill_qty", "wrong_order"])
def test_incomplete_evidence_never_signals(missing):
    feed, clock, _ = ba_feed()
    clock.now = .01
    rows = [trade(feed, size="4" if missing == "sell_qty" else "5"),
            depth(feed, 76 if missing == "depletion_qty" else 75),
            depth(feed, 79 if missing == "refill_qty" else 80)]
    if missing in {"sell", "depletion", "refill"}:
        rows.pop({"sell": 0, "depletion": 1, "refill": 2}[missing])
    if missing == "wrong_order":
        rows = [rows[1], rows[0], rows[2]]
    feed.apply(rows)
    assert not feed.ba.evaluate(feed.snapshot()).direction


@pytest.mark.parametrize("fault", ["duplicate", "out_of_order", "missing_side", "book_side", "missing_time", "old_sell", "wrong_price", "reconnect", "wrong_token", "wrong_market"])
def test_bad_data_fails_closed(fault):
    feed, clock, rows = ba_feed()
    clock.now = .01
    first = trade(feed)
    feed.apply(first)
    bad = dict(first)
    if fault == "duplicate":
        feed.apply(bad)
    elif fault == "out_of_order":
        bad["timestamp"] = str(float(first["timestamp"])-1)
        feed.apply(bad)
    elif fault in {"missing_side", "book_side", "missing_time"}:
        bad.pop("timestamp" if fault == "missing_time" else "side")
        if fault == "book_side":
            bad["side"] = "BID"
        feed.apply(bad)
    elif fault == "old_sell":
        bad["timestamp"] = str(float(first["timestamp"])-600)
        feed.apply(bad)
    elif fault == "wrong_price":
        feed.apply(trade(feed, price=".52", size="6"))
    elif fault == "reconnect":
        feed.connect()
        feed.apply(rows)
    else:
        bad["asset_id" if fault == "wrong_token" else "market"] = "wrong"
        with pytest.raises(ValueError):
            feed.apply(bad)
        assert not feed.snapshot().valid
        return
    feed.apply([depth(feed, 75), depth(feed, 80)])
    assert not feed.ba.evaluate(feed.snapshot()).direction


@pytest.mark.parametrize("fault", ["observation", "deadline", "sell_age", "anchor"])
def test_fixed_deadlines_and_prepost_evidence(fault):
    feed, clock, rows = ba_feed()
    clock.now = .01
    feed.apply(trade(feed))
    if fault == "observation":
        clock.now += .75
    feed.apply([depth(feed, 75), depth(feed, 80)])
    ep = feed.ba.states["1"]
    if fault != "observation":
        assert feed.ba.evaluate(feed.snapshot()).direction == "YES"
    deadline = ep.order_deadline
    if fault in {"deadline", "sell_age"}:
        clock.now = deadline if fault == "deadline" else .511
        # Refresh both books without creating a new SELL or moving the anchor.
        rows = deepcopy(rows)
        for row in rows:
            row["timestamp"] = str(feed.wall()*1000)
        feed.apply(rows)
    if fault == "anchor":
        feed.apply(depth(feed, 7, price=".51"))
    assert not feed.ba.evaluate(feed.snapshot()).direction
    assert ep.order_deadline == deadline


def test_new_sell_and_refill_cannot_extend_confirmed_episode():
    feed, clock, _ = ba_feed()
    clock.now = .01
    absorption(feed)
    ep = feed.ba.states["1"]
    deadline, started = ep.order_deadline, ep.signal_start_mono
    clock.now += .01
    feed.apply([trade(feed, size="6"), depth(feed, 90)])
    assert ep.order_deadline == deadline and ep.signal_start_mono == started
    absorption(feed, "2")
    assert not feed.ba.evaluate(feed.snapshot()).direction
    assert all(ep.invalid_reason == "BA_BOTH_SIDES_VALID" for ep in feed.ba.states.values())


def test_bbo_only_anchor_change_and_source_age_fail_closed():
    feed, clock, _ = ba_feed()
    clock.now = .01
    absorption(feed)
    feed.apply({"event_type": "best_bid_ask", "market": CID, "asset_id": "1",
                "timestamp": str(feed.wall()*1000), "best_bid": ".51", "best_ask": ".52"})
    assert not feed.ba.evaluate(feed.snapshot()).direction
    assert feed.ba.states["1"].invalid_reason == "BA_TOP_CHANGED"

    feed, clock, _ = ba_feed()
    clock.now = .01
    feed.apply(trade(feed, timestamp=str((feed.wall()-.49)*1000)))
    feed.apply([depth(feed, 75), depth(feed, 80)])
    assert not feed.ba.evaluate(feed.snapshot()).direction  # Older than the book: unordered.


def test_configured_thresholds_accumulate_distinct_events_only():
    feed, clock, _ = ba_feed(min_sell_shares="7", min_depletion_shares="6", min_refill_shares="4")
    clock.now = .01
    feed.apply(trade(feed, size="3"))
    clock.now = .02
    feed.apply(trade(feed, size="4"))
    feed.apply([depth(feed, 78), depth(feed, 74), depth(feed, 74), depth(feed, 76)])
    assert not feed.ba.evaluate(feed.snapshot()).direction
    feed.apply(depth(feed, 78))
    assert feed.ba.evaluate(feed.snapshot()).direction == "YES"
    ep = feed.ba.states["1"]
    assert (ep.sell_volume, ep.depletion_shares, ep.refill_shares) == (7, 6, 4)
    assert ep.anchor_bid_size == 80 and feed.books["1"].best_bid_size == 78


def test_queued_frame_cannot_confirm_after_observation_deadline():
    feed, clock, _ = ba_feed(observation_sec=.05)
    clock.now = .01
    feed.apply(trade(feed))
    clock.now = .02
    rows = [depth(feed, 75), depth(feed, 80)]
    received_wall = feed.wall()
    clock.now = .09  # Arrival was timely; application/confirmation was too late.
    feed.apply(rows, received_mono=.02, received_wall=received_wall)
    assert not feed.ba.evaluate(feed.snapshot()).direction
    assert feed.ba.states["1"].invalid_reason == "BA_OBSERVATION_TIMEOUT"


def test_bounded_trade_history_cannot_reaccept_evicted_duplicates():
    feed, clock, _ = ba_feed()
    feed.ba.trade_history_limit = 2
    clock.now = .01
    for size in (2, 3, 4, 2):
        feed.apply(trade(feed, size=str(size)))
    feed.apply([depth(feed, 75), depth(feed, 80)])
    assert not feed.ba.evaluate(feed.snapshot()).direction
    assert feed.ba.states["1"].invalid_reason == "BA_TRADE_HISTORY_UNCERTAIN"


@pytest.mark.parametrize("mode", ["legacy", "obi_v1", "ba_v1", "unknown"])
def test_configuration_switch_and_shared_capital(mode):
    if mode == "unknown":
        with pytest.raises(ValidationError):
            RDPSMConfig(normal_entry_mode=mode)
    else:
        cfg = RDPSMConfig(normal_entry_mode=mode,
                          repair={"policy": "ev_legacy" if mode == "legacy" else "hd_tp_sl_v1"})
        assert session_capital_limit(SimpleNamespace(rdp_sm=cfg)) == (15 if mode == "legacy" else 20)
        assert cfg.obi.threshold == D(".30")


@pytest.mark.parametrize("settings", [{"min_sell_shares": "0"}, {"min_depletion_shares": "NaN"},
    {"min_refill_shares": "-1"}, {"observation_sec": 0}, {"signal_lifetime_sec": .5},
    {"max_sell_age_ms": 0}, {"max_book_age_ms": float("inf")}, {"unexpected": True}])
def test_ba_config_validation(settings):
    with pytest.raises(ValidationError):
        RDPBAConfig(**settings)


async def execution_setup(env, monkeypatch):
    setup = list(await setup_obi(env, monkeypatch))
    v, e, feed, lifecycle, _, wire, _, state, task = setup
    state["running"] = False
    await task
    e.cfg.rdp_sm.normal_entry_mode = "ba_v1"
    e.cfg.rdp_sm.ba = RDPBAConfig(observation_sec=.1, signal_lifetime_sec=.25,
                                max_sell_age_ms=150, max_entry_age_ms=180,
                                network={"post_budget_floor_ms": 10, "min_live_budget_ms": 20})
    feed.ba = BidAbsorption(feed, e.cfg.rdp_sm.ba)
    normal = BANormalController(e, lifecycle)
    setup[4] = normal
    state["running"] = True

    async def pump():
        while state["running"]:
            rows = []
            for token in ("1", "2"):
                book = feed.books[token]
                rows.append({"event_type": "book", "market": CID, "asset_id": token,
                    "timestamp": str(feed.wall()*1000),
                    "bids": [{"price": str(book.best_bid), "size": str(book.best_bid_size)}],
                    "asks": [{"price": str(book.best_ask), "size": str(book.best_ask_size)}]})
            feed.apply(rows)
            await asyncio.sleep(.01)
    setup[-1] = asyncio.create_task(pump())
    await asyncio.sleep(0)
    return setup


@pytest.mark.parametrize("token", ["1", "2"])
async def test_real_maker_chain_fixed_t0_deadline_no_obi_reversal_or_repost(env, monkeypatch, token):
    setup = await execution_setup(env, monkeypatch)
    v, e, feed, lifecycle, normal, wire = setup[:6]
    try:
        absorption(feed, token)
        ep = feed.ba.states[token]
        result, sizing = await normal.enter(D(0), D(0), "ba-entry")
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert len(v.post_requests) == 1 and len(v.post_requests[0]) == 1
        row = v.post_requests[0][0]
        assert row["postOnly"] is True and row["order"]["side"] == "BUY"
        assert row["order"]["tokenId"] == token
        assert result.requested_quotes[0].price == float(ep.anchor_bid_price)
        assert result.requested_quotes[0].size == 5
        assert result.t0_monotonic == ep.signal_start_mono
        assert result.fill_deadline_monotonic == ep.order_deadline
        assert next(t[2] for t in wire.timeline if t[0] == "DELETE") >= ep.order_deadline
        assert e.repair_episode is None and e.cooldown.round_id is None
        assert await normal.enter(D(0), D(0), "again") is None
        assert len(v.post_requests) == 1
        logs = journal_records(e.journal.path)
        assert not any(r["kind"] == "sm_repair_episode" for r in logs)
        events = [r["data"]["payload"] for r in logs if r["kind"] == "sm_ba_normal_execution"]
        assert events and events[-1]["episode_id"] == ep.episode_id
        assert events[-1]["confirmed_fill_qty"] == "0"
        assert sizing["normal_entry_mode"] == "ba_v1"
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("fault", ["expires_signing", "expires_post", "anchor_post", "network", "capital", "account", "conflict", "unresolved"])
async def test_admission_and_final_guard_never_bypassed(env, monkeypatch, fault):
    setup = await execution_setup(env, monkeypatch)
    v, e, feed, lifecycle, normal = setup[:5]
    try:
        absorption(feed)
        if fault == "expires_signing":
            native = e.gateway.prepare
            async def slow(*args, **kwargs):
                result = await native(*args, **kwargs)
                await asyncio.sleep(.26)
                return result
            monkeypatch.setattr(e.gateway, "prepare", slow)
        elif fault in {"expires_post", "anchor_post"}:
            native = e.gateway.send_prepared
            async def delayed(*args, **kwargs):
                if fault == "expires_post":
                    await asyncio.sleep(.26)
                else:
                    feed.apply(depth(feed, 20, price=".51"))
                return await native(*args, **kwargs)
            monkeypatch.setattr(e.gateway, "send_prepared", delayed)
        elif fault == "network":
            e.reads.network.observe("POST", .01, success=False)
        elif fault == "capital":
            lifecycle.account["basic_spendable_budget_usd"] = 0
        elif fault == "account":
            e.inventory_mismatch = "unknown"
        elif fault == "conflict":
            absorption(feed, "2")
        elif fault == "unresolved":
            from polymaker.rdp_sm_execution import Placement, WindowQuote
            e.attempts.append(Placement("unknown", WindowQuote(CID, "1", "YES", Side.BUY, .5, 5, "old")))
            with pytest.raises(ValueError, match="PREVIOUS_WINDOW_UNRESOLVED"):
                await normal.enter(D(0), D(0), "blocked")
            return
        await normal.enter(D(0), D(0), "blocked")
        assert not v.post_requests
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("late", [False, True])
async def test_partial_and_late_fill_keep_inventory_and_repair_ownership(env, monkeypatch, late):
    setup = await execution_setup(env, monkeypatch)
    v, e, feed, lifecycle, normal, wire = setup[:6]
    try:
        absorption(feed)
        async def partial():
            while not v.rows:  # noqa: ASYNC110 - injected venue/WS ordering boundary
                await asyncio.sleep(.001)
            oid = next(iter(v.rows))
            v.fill(oid, "initial", 3)
            reflect_positions(v)
            row = v.rows[oid]
            # Deliver the owned official WS fill, as the real user stream does.
            while e.processor.owned_record(oid) is None:  # noqa: ASYNC110
                await asyncio.sleep(.001)
            event = TradeEvent("1", Side.BUY, float(row["price"]), 3,
                "initial", TradeState.MATCHED, v.time, oid, CID)
            e.processor.on_trade(event, CID)
            e.processor.on_trade(replace(event, status=TradeState.CONFIRMED), CID)
        task = asyncio.create_task(partial()) if not late else None
        def fill_cancel(venue):
            oid = next(iter(venue.rows))
            if not any(t["id"] == "late" for t in venue.trades):
                venue.fill(oid, "late", 1 if not late else 3)
                reflect_positions(venue)
        v.on_cancel = fill_cancel
        result, _ = await normal.enter(D(0), D(0), "ba-fill")
        if task:
            await task
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert e.economic_inventory() == (D(3 if late else 4), D(0))
        assert e.repair_episode["opening_window"] == "ba-fill"
        assert D(e.repair_episode["original_qty"]) == (3 if late else 4)
        assert e.cooldown.baseline == {}
        first_delete = next(t[2] for t in wire.timeline if t[0] == "DELETE")
        assert (first_delete >= result.fill_deadline_monotonic) == late
        events = [r["data"]["payload"] for r in journal_records(e.journal.path)
                  if r["kind"] == "sm_ba_normal_execution"]
        assert D(events[-1]["confirmed_fill_qty"]) == (3 if late else 4)
        assert events[-1]["first_fill_time"] is not None
        assert events[-1]["fill_time_source"]
        assert events[-1]["signal_age_at_fill"] >= 0
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("cached", [False, True])
async def test_ba_discovery_preflight_loop_and_teardown_use_safe_ws_path(env, monkeypatch, cached):
    from test_rdp_sm_obi_entry import (
        test_new_mode_entire_entry_has_no_rest_book_and_owns_ws_teardown,
    )

    env[1].rdp_sm.repair.policy = "hd_tp_sl_v1"
    await test_new_mode_entire_entry_has_no_rest_book_and_owns_ws_teardown(
        env, monkeypatch, disconnect_preflight=False, cancel_before_order=False,
        prior_unresolved=False, cached=cached, mode="ba_v1")


@pytest.mark.parametrize("rejected", [False, True])
async def test_ba_lost_ack_or_rejection_consumes_only_one_submission(env, monkeypatch, rejected):
    setup = await execution_setup(env, monkeypatch)
    v, e, feed, _, normal, wire = setup[:6]
    try:
        if rejected:
            wire.reject_index = 0
        else:
            wire.fail_ack = True
        absorption(feed)
        episode = feed.ba.states["1"]
        result, _ = await normal.enter(D(0), D(0), "uncertain")
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert episode.consumed and len(v.post_requests) == 1
        assert await normal.enter(D(0), D(0), "no-repeat") is None
        assert len(v.post_requests) == 1 and e.repair_episode is None
    finally:
        await cleanup(setup)
