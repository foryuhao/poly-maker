"""Causal pullback formations and real offline maker submission/cancel path."""

import asyncio
from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from polymaker.config import RDPBAConfig, RDPCAObserveConfig, RDPCAPullbackConfig, RDPSMConfig
from polymaker.rdp_sm_ca_pullback import CAPullbackEntry
from polymaker.rdp_sm_marketdata import SessionFeed
from polymaker.rdp_sm_repair_ba import RepairBidAbsorption
from test_rdp_sm_ba import trade
from test_rdp_sm_ca import buy, heartbeat
from test_rdp_sm_ca_entry import ca_setup, confirm_ca
from test_rdp_sm_ca_entry import env as env
from test_rdp_sm_ca_matched_tp import fill
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_obi import feed_fixture
from test_rdp_sm_obi_execution import cleanup
from test_rdp_sm_repair import reflect_positions


def book_rows(feed, *, bid=None, ask=None, token="1"):
    return [{"event_type": "book", "market": feed.meta.condition_id, "asset_id": t,
             "timestamp": str(feed.wall()*1000),
             "bids": [{"price": str(bid if t == token and bid is not None else b.best_bid),
                       "size": "80"}],
             "asks": [{"price": str(ask if t == token and ask is not None else b.best_ask),
                       "size": "20"}]} for t, b in feed.books.items()]


def pullback_feed(**settings):
    original, clock, rows = feed_fixture()
    ba = RDPBAConfig(signal_lifetime_sec=3, max_book_age_ms=200)
    feed = SessionFeed(original.meta, original.cfg, monotonic=original.monotonic,
        wall=original.wall, ba_cfg=ba, ca_config=RDPCAObserveConfig(enabled=True),
        ca_session_id="pullback-test", normal_entry_mode="ca_v1",
        ca_pullback_config=RDPCAPullbackConfig(enabled=True, **settings))
    feed.verify_ticks({t: b.tick for t, b in original.books.items()})
    feed.connect()
    feed.apply(rows)
    feed.observe_ca("set_context", "NORMAL", 20)
    return feed, clock


def confirm(feed, clock, token="1"):
    clock.now += .01
    feed.apply(trade(feed, token))
    clock.now += .11
    feed.apply(buy(feed, token))
    heartbeat(feed)
    return feed.ca_entry.states.get(token)


def advance(feed, clock, token="1"):
    ep = feed.ca_entry.states[token]
    clock.now += .01
    feed.apply(book_rows(feed, token=token, bid=ep.anchor_bid_price+2*ep.tick,
                         ask=ep.confirmation_ask+2*ep.tick))
    return ep


def test_ca_alone_and_bid_ask_trade_bounce_do_not_admit_entry():
    f, c = pullback_feed()
    ep = confirm(f, c)
    assert ep.state == "WAIT_ADVANCE"
    assert f.ca_entry.evaluate(f.snapshot()).direction is None
    c.now += .01
    f.apply(trade(f))
    assert ep.state == "WAIT_ADVANCE"
    assert f.ca_entry.evaluate(f.snapshot()).direction is None


@pytest.mark.parametrize("token", ["1", "2"])
def test_advance_survives_old_ca_end_and_freezes_same_side_quote_and_deadline(token):
    f, c = pullback_feed()
    ep = confirm(f, c, token)
    advance(f, c, token)
    assert f.ca.states[token].end_reason == "CA_ANCHOR_CHANGED"
    assert ep.state == "WAIT_MAKER" and ep.entry_price == ep.anchor_bid_price+ep.tick
    signal = f.ca_entry.evaluate(f.snapshot())
    assert signal.direction == ep.outcome
    assert ep.order_deadline == ep.advance_mono+3
    assert ep.advance_book["received_monotonic"] <= ep.advance_mono
    assert ep.advance_book["applied_monotonic"] <= ep.advance_mono
    deadline = ep.order_deadline
    c.now += .01
    heartbeat(f)
    f.ca_entry.evaluate(f.snapshot())
    assert ep.order_deadline == deadline
    assert f.ca_diagnostics()["pullback"]["setups"][0]["episode_id"] == ep.episode_id


@pytest.mark.parametrize("which", ["bid_only", "ask_only"])
def test_advance_requires_both_quote_sides_to_move(which):
    f, c = pullback_feed()
    f.apply(book_rows(f, bid=".50", ask=".55"))
    ep = confirm(f, c)
    c.now += .01
    f.apply(book_rows(f, bid=".52" if which == "bid_only" else ".50",
                         ask=".55" if which == "bid_only" else ".57"))
    assert ep.state == "WAIT_ADVANCE"
    assert not f.ca_entry.evaluate(f.snapshot()).direction


@pytest.mark.parametrize("fault", ["floor", "stale_gap", "reconnect", "missed_retrace", "timeout"])
def test_failed_formations_latch_and_fresh_books_do_not_revive_them(fault):
    f, c = pullback_feed()
    ep = confirm(f, c)
    advance(f, c)
    c.now += .01
    if fault == "floor":
        f.apply(book_rows(f, bid=".49", ask=".51"))
    elif fault == "missed_retrace":
        f.apply(book_rows(f, bid=".51", ask=".53"))
    elif fault == "reconnect":
        f.connect()
    elif fault == "stale_gap":
        c.now += .3
        f.apply(book_rows(f))
    else:
        # Advance expiry with continuous fresh data, not a stale-book failure.
        while c.now < ep.order_deadline:
            c.now += .1
            heartbeat(f)
        f.ca_entry.tick()
    assert ep.end_reason
    if fault == "timeout":
        assert ep.end_reason == "CA_PULLBACK_ORDER_EXPIRED"
    if fault != "reconnect":
        c.now += .01
        f.apply(book_rows(f, bid=".52", ask=".54"))
    assert not f.ca_entry.evaluate(f.snapshot()).direction


def test_advance_timeout_and_fast_confirmations_do_not_start_a_new_lease():
    f, c = pullback_feed(advance_timeout_sec=.15)
    ep = confirm(f, c)
    c.now += .1
    heartbeat(f)
    c.now += .06
    heartbeat(f)
    assert ep.end_reason == "CA_PULLBACK_ADVANCE_TIMEOUT"
    other, clock = pullback_feed()
    other.apply([trade(other), buy(other)])
    assert other.ca.states["1"].tC is not None
    assert not other.ca_entry.states


def test_posted_order_accepts_retrace_but_not_floor_break_or_second_submission():
    f, c = pullback_feed()
    confirm(f, c)
    advance(f, c)
    signal = f.ca_entry.evaluate(f.snapshot())
    claim = f.ca_entry.claim(signal)
    assert f.ca_entry.current(claim)
    f.ca_entry.consume(claim)
    c.now += .01
    f.apply(book_rows(f, bid=".51", ask=".53"))
    assert f.ca_entry.active_reason(claim, f.snapshot()) is None
    assert f.ca_entry.evidence(claim)["setup"]["state"] == "POSTED"
    assert not f.ca_entry.evaluate(f.snapshot()).direction
    c.now += .01
    f.apply(book_rows(f, bid=".49", ask=".51"))
    assert f.ca_entry.active_reason(claim, f.snapshot()) == "CA_PULLBACK_FLOOR_BROKEN"


def test_opposite_formations_conflict_and_preflight_ca_cannot_arm_entry():
    f, c = pullback_feed()
    yes = confirm(f, c)
    c.now += .01
    f.apply(trade(f, "2"))
    c.now += .06
    heartbeat(f)
    c.now += .05
    f.apply(buy(f, "2"))
    assert yes.end_reason == "CA_PULLBACK_BOTH_SIDES_VALID"
    assert f.ca_entry.states["2"].end_reason == yes.end_reason
    other, clock = pullback_feed()
    other.observe_ca("set_context", "PREFLIGHT", 20)
    confirm(other, clock)
    assert not other.ca_entry.states
    other.observe_ca("set_context", "NORMAL", 21)
    clock.now += .01
    other.apply(trade(other))
    assert not other.ca_entry.states  # Do not backfill preflight confirmations.


def test_floor_break_inside_one_frame_cannot_be_hidden_by_a_later_recovery():
    f, c = pullback_feed()
    ep = confirm(f, c)
    advance(f, c)
    c.now += .01
    f.apply(book_rows(f, bid=".49", ask=".51")+book_rows(f, bid=".52", ask=".54"))
    assert ep.end_reason == "CA_PULLBACK_FLOOR_BROKEN"
    assert not f.ca_entry.evaluate(f.snapshot()).direction


def test_new_same_side_ca_does_not_replace_the_original_advance_deadline():
    f, c = pullback_feed()
    original = confirm(f, c)
    advance(f, c)
    deadline = original.order_deadline
    confirm(f, c)
    assert f.ca.states["1"].episode_id != original.episode_id
    assert f.ca_entry.states["1"] is original
    assert original.order_deadline == deadline


def test_observer_failure_latches_a_previously_ready_formation():
    f, c = pullback_feed()
    ep = confirm(f, c)
    advance(f, c)
    f.observe_ca("disable", "TEST_WRITER_FAILURE")
    assert ep.end_reason and not f.ca_entry.evaluate(f.snapshot()).direction


def test_configuration_is_opt_in_same_side_and_rejects_invalid_price_geometry():
    assert not RDPSMConfig().ca_pullback.enabled
    cfg = RDPSMConfig(normal_entry_mode="ca_v1", ca_observe={"enabled": True},
                      ca_pullback={"enabled": True})
    assert cfg.ca_pullback.advance_ticks == 2
    with pytest.raises(ValidationError, match="ca_entry_direction=signal"):
        RDPSMConfig(normal_entry_mode="ca_v1", ca_entry_direction="higher_bid",
                    ca_observe={"enabled": True}, ca_pullback={"enabled": True})
    with pytest.raises(ValidationError, match="below the required advance"):
        RDPCAPullbackConfig(advance_ticks=1, entry_offset_ticks=1)
    with pytest.raises(ValidationError, match="execution budget"):
        RDPSMConfig(normal_entry_mode="ca_v1", ca_observe={"enabled": True},
                    ca_pullback={"enabled": True, "order_ttl_sec": .1})


async def pullback_setup(env, monkeypatch):
    setup = await ca_setup(env, monkeypatch)
    _, e, f = setup[:3]
    e.cfg.rdp_sm.ca_pullback = RDPCAPullbackConfig(enabled=True, order_ttl_sec=.3)
    e.cfg.rdp_sm.ca_entry_direction = "signal"
    f.ca_entry = CAPullbackEntry(f, e.cfg.rdp_sm.ca_pullback)
    f.observe_ca("set_context", "NORMAL", 60)
    await confirm_ca(f)
    f.apply(book_rows(f, bid=".52", ask=".54"))
    return setup


async def test_real_sdk_posts_fixed_pullback_quote_and_cancels_at_advance_deadline(env, monkeypatch):
    setup = await pullback_setup(env, monkeypatch)
    v, e, f, _, normal, wire = setup[:6]
    try:
        ep = f.ca_entry.states["1"]
        result, sizing = await asyncio.wait_for(normal.enter(D(0), D(0), "pullback"), 5)
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert len(v.post_requests) == 1
        assert v.post_requests[0][0]["postOnly"]
        assert result.requested_quotes[0].price == .51
        assert result.requested_quotes[0].outcome == "YES"
        assert result.t0_monotonic == ep.advance_mono
        assert result.fill_deadline_monotonic == ep.order_deadline
        assert next(t[2] for t in wire.timeline if t[0] == "DELETE") >= ep.order_deadline
        assert sizing["ca_pullback"]["setups"][0]["entry_price"] == D('.51')
        logs = journal_records(e.journal.path)
        decision = next(r["data"]["payload"] for r in logs if r["kind"] == "sm_ca_final_decision")
        proof = decision["intent"]["ca_observation"]["pullback"]["setup"]
        assert proof["advance_mono"] <= decision["intent"]["post_started"]
        assert proof["entry_price"] == "0.51"
        assert await normal.enter(D(0), D(0), "repeat") is None
        assert len(v.post_requests) == 1
    finally:
        await cleanup(setup)


async def test_support_break_during_preparation_blocks_native_post(env, monkeypatch):
    setup = await pullback_setup(env, monkeypatch)
    v, e, f, _, normal = setup[:5]
    native = e.gateway.prepare

    async def prepare(*args, **kwargs):
        signed = await native(*args, **kwargs)
        f.apply(book_rows(f, bid=".49", ask=".51"))
        return signed

    monkeypatch.setattr(e.gateway, "prepare", prepare)
    try:
        await normal.enter(D(0), D(0), "broken")
        assert not v.post_requests
        assert not f.ca_entry.consumed
    finally:
        await cleanup(setup)


async def test_support_break_after_post_cancels_before_deadline_and_proves_close(env, monkeypatch):
    setup = await pullback_setup(env, monkeypatch)
    v, e, f, _, normal, wire = setup[:6]
    native = e.gateway.send_prepared

    async def send(*args, **kwargs):
        raw = await native(*args, **kwargs)
        f.apply(book_rows(f, bid=".49", ask=".51"))
        return raw

    monkeypatch.setattr(e.gateway, "send_prepared", send)
    try:
        deadline = f.ca_entry.states["1"].order_deadline
        result, _ = await normal.enter(D(0), D(0), "break-after-post")
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert len(v.post_requests) == 1
        assert next(t[2] for t in wire.timeline if t[0] == "DELETE") < deadline
        assert f.ca_entry.states["1"].end_reason == "CA_PULLBACK_FLOOR_BROKEN"
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("rejected", [False, True])
async def test_unknown_or_rejected_pullback_does_not_repost_or_skip_close_proof(
    env, monkeypatch, rejected
):
    setup = await pullback_setup(env, monkeypatch)
    v, _, f, _, normal, wire = setup[:6]
    if rejected:
        wire.reject_index = 0
    else:
        wire.fail_ack = True
    try:
        result, _ = await normal.enter(D(0), D(0), "unknown-pullback")
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert len(v.post_requests) == 1 and f.ca_entry.consumed
        assert await normal.enter(D(0), D(0), "no-repeat") is None
        assert len(v.post_requests) == 1
    finally:
        await cleanup(setup)


async def test_pullback_fill_uses_existing_owned_fill_tp_and_close_proofs(env, monkeypatch):
    setup = await pullback_setup(env, monkeypatch)
    v, e, f, _, normal = setup[:5]
    e.cfg.rdp_sm.ca_matched_tp.enabled = True
    e.cfg.rdp_sm.ca_matched_tp.profit_per_share = D('.05')
    e.cfg.rdp_sm.repair.policy = "opposite_ba_v1"
    e.cfg.rdp_sm.repair.ba_enabled = False
    f.opposite_ba = RepairBidAbsorption(
        f, e.cfg.rdp_sm.repair.opposite_ba, e.cfg.rdp_sm.repair.opposite_ba_execution)
    native = e.gateway.send_prepared
    count = 0

    async def send(signed, **kwargs):
        nonlocal count
        raw = await native(signed, **kwargs)
        count += 1
        oid = signed.order_ids[0]
        fill(v, oid, "pullback-fill-"+str(count))
        v.rows[oid]["status"] = "MATCHED"
        reflect_positions(v)
        return raw

    monkeypatch.setattr(e.gateway, "send_prepared", send)
    try:
        result, sizing = await asyncio.wait_for(normal.enter(D(0), D(0), "pullback-tp"), 5)
        assert count == 2 and len(v.post_requests) == 2, sizing.get("ca_matched_tp")
        assert e.attempts[0].quote.price == .51
        assert e.attempts[1].quote.price == .44
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert e.economic_inventory() == (D(5), D(5))
        assert sizing["ca_matched_tp"]["reason"] == "TP_ORDER_TERMINAL"
    finally:
        await cleanup(setup)
