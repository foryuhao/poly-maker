"""Opposite BA exits through the real SDK, mocked venue and ownership barrier."""
import asyncio
from decimal import Decimal as D
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from polymaker.config import Config, RDPBAConfig, RDPSMConfig
from polymaker.rdp_sm import session_capital_limit
from polymaker.rdp_sm_ba import BidAbsorption
from polymaker.rdp_sm_execution import SMWindowExecutor
from polymaker.rdp_sm_normal import BANormalController
from polymaker.rdp_sm_opposite_ba import OppositeBAController
from polymaker.repair_policy import repair_readiness
from test_rdp_sm_ba import ba_feed, depth, trade
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_obi_execution import cleanup, setup_obi
from test_rdp_sm_repair import env as env
from test_rdp_sm_repair import open_yes, reflect_positions


def absorption(feed, token):
    size = feed.books[token].best_bid_size
    feed.apply([trade(feed, token), depth(feed, size-5, token), depth(feed, size, token)])


def controller_fixture(delta="5", **settings):
    feed, clock, _ = ba_feed(**settings)
    feed.opposite_ba = BidAbsorption(feed, feed.ba.cfg)
    cfg = RDPSMConfig(normal_entry_mode="ba_v1", repair={"policy": "opposite_ba_v1"})
    cfg.repair.opposite_ba = feed.ba.cfg
    d = D(delta)
    e = SimpleNamespace(cfg=SimpleNamespace(rdp_sm=cfg),
        economic_inventory=lambda: (max(d, D(0)), max(-d, D(0))),
        capital_limit=D(20), committed=lambda: D(0),
        repair_episode={"first_fill_monotonic": .005, "side": "YES" if d > 0 else "NO"}, meta=feed.meta)
    e.delta_candidate_allowed = lambda delta, side, qty: SMWindowExecutor.delta_candidate_allowed(e, delta, side, qty)
    return OppositeBAController(e, SimpleNamespace(feed=feed, account=None)), feed, clock


@pytest.mark.parametrize("delta,token,outcome", [("5", "2", "NO"), ("-5", "1", "YES")])
def test_only_fresh_opposite_ba_selects(delta, token, outcome):
    c, feed, clock = controller_fixture(delta)
    clock.now = .01
    absorption(feed, token)
    assert c.select(feed.snapshot())[0] == outcome
    c.executor.repair_episode["first_fill_monotonic"] = .02
    assert c.select(feed.snapshot())[0] is None
    assert c.signal_block_reason == "OPPOSITE_BA_PREDATES_POSITION"


def test_same_side_conflict_expiry_and_reconnect_cannot_exit():
    c, feed, clock = controller_fixture()
    clock.now = .01
    absorption(feed, "1")
    assert c.select(feed.snapshot())[0] is None
    absorption(feed, "2")
    assert c.select(feed.snapshot())[0] is None
    clock.now = 10
    assert c.select(feed.snapshot())[0] is None
    feed.connect()
    assert not feed.opposite_ba.states


@pytest.mark.parametrize("delta,quantity", [("2.5", None), ("3", 5), ("5", 5), ("5.009", 5.01), ("20", 15)])
def test_shared_repair_sizing_uses_minimums_and_reduces_delta(delta, quantity):
    c, feed, clock = controller_fixture(delta)
    clock.now = .01
    absorption(feed, "2")
    plans = c.candidates(feed.snapshot(), D(delta), D(0), "exit")
    if quantity is None:
        assert not plans and c.quote_block_reason == "OPPOSITE_BA_NO_POSITION"
    else:
        quote = plans["NO"][0][0]
        assert quote.size == quantity
        assert abs(D(delta)-D(str(quote.size))) < abs(D(delta))
        assert quote.price == float(feed.opposite_ba.states["2"].anchor_bid_price)


def test_entry_and_exit_have_independent_claims_and_parameters():
    c, feed, clock = controller_fixture()
    clock.now = .01
    absorption(feed, "2")
    signal = feed.ba.evaluate(feed.snapshot())
    claim = feed.ba.claim(signal)
    feed.ba.consume(claim)
    feed.ba.release(claim)
    assert c.select(feed.snapshot())[0] == "NO"
    assert not feed.opposite_ba.selected.consumed


def test_reverse_inventory_requires_ownership_handoff_before_submission():
    c, feed, clock = controller_fixture("-5")
    c.executor.repair_episode["side"] = "YES"
    clock.now = .01
    absorption(feed, "1")
    assert c.select(feed.snapshot())[0] is None
    assert c.signal_block_reason == "OPPOSITE_BA_DIRECTION_HANDOFF_UNPROVEN"


def test_configuration_and_readiness_do_not_depend_on_price_parameters():
    cfg = RDPSMConfig(normal_entry_mode="obi_v1", repair={"policy": "opposite_ba_v1",
        "path": {"tp_enabled": False, "sl_enabled": False,
                 "tp_profit_rate": None, "shock_z": None, "drawdown_per_share": None}})
    assert repair_readiness(cfg.repair)["ready_for_live"]
    assert session_capital_limit(SimpleNamespace(rdp_sm=cfg)) == 20
    with pytest.raises(ValidationError):
        RDPSMConfig(normal_entry_mode="legacy", repair={"policy": "opposite_ba_v1"})


def test_local_configs_keep_both_policies_and_independent_ba_settings():
    cfg = Config.load("config").rdp_sm
    live = Config.load("livecfg").rdp_sm
    assert cfg.repair.policy == "opposite_ba_v1"
    assert live.repair.policy == "hd_tp_sl_v1"
    assert cfg.repair.path.tp_enabled and cfg.repair.path.sl_enabled
    assert cfg.repair.opposite_ba.signal_lifetime_sec == 3
    assert cfg.repair.opposite_ba is not cfg.ba
    cfg.repair.opposite_ba.min_sell_shares = D(10)
    assert cfg.ba.min_sell_shares == 5


async def setup_exit(env, monkeypatch, size=5):
    setup = await setup_obi(env, monkeypatch)
    _, e, feed, lifecycle = setup[:4]
    e.cfg.rdp_sm.repair.policy = "opposite_ba_v1"
    e.cfg.rdp_sm.repair.opposite_ba = RDPBAConfig(
        observation_sec=.1, signal_lifetime_sec=.3, max_sell_age_ms=200,
        max_entry_age_ms=250, max_book_age_ms=200,
        network={"post_budget_floor_ms": 10, "min_live_budget_ms": 20})
    feed.opposite_ba = BidAbsorption(feed, e.cfg.rdp_sm.repair.opposite_ba)
    result, _ = await open_yes(setup, size=size)
    assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
    lifecycle.account = (await e._snapshot(include_books=False))[3]
    return setup, OppositeBAController(e, lifecycle)


async def test_no_signal_keeps_position_even_after_t180_and_ignores_abandonment(env, monkeypatch):
    setup, c = await setup_exit(env, monkeypatch)
    v, e = setup[:2]
    try:
        e.official_time = int(e.slug.rsplit("-", 1)[1])+181
        e.time_anchor = e.monotonic()
        e.cfg.rdp_sm.repair.abandon_high_entry.min_elapsed_sec = 0
        e.cfg.rdp_sm.repair.abandon_high_entry.entry_price_threshold = D(".01")
        for wid in ("wait-one", "wait-two"):
            assert await c.repair(*e.economic_inventory(), wid) is None
        assert not e.check_repair_abandonment()
        assert len(v.post_requests) == 1
        assert e.trading_state == "REPAIR" and e.economic_inventory() == (D(5), D(0))
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("fill_size", [0, 2.5, 3, 4.995, 5])
async def test_signal_order_full_partial_or_no_fill_preserves_ownership(env, monkeypatch, fill_size):
    setup, c = await setup_exit(env, monkeypatch)
    v, e, feed = setup[:3]
    try:
        absorption(feed, "2")
        ep = feed.opposite_ba.states["2"]

        def fill(venue):
            for oid, row in venue.rows.items():
                if row["asset_id"] == "2" and not row["associate_trades"] and fill_size:
                    venue.fill(oid, "exit-fill", fill_size)
            reflect_positions(venue)

        v.on_cancel = fill
        result, sizing = await c.repair(*e.economic_inventory(), "ba-exit")
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert len(v.post_requests) == 2
        assert result.requested_quotes[0].outcome == "NO"
        assert result.requested_quotes[0].price == float(ep.anchor_bid_price)
        assert result.requested_quotes[0].size == 5
        assert sizing["policy"] == "opposite_ba_v1"
        assert ep.consumed
        assert result.t0_monotonic == ep.signal_start_mono
        assert result.fill_deadline_monotonic == ep.order_deadline
        assert e.economic_inventory() == (D(5), D(str(fill_size)))
        complete = D(5)-D(str(fill_size)) <= D("2.5")
        assert (e.repair_episode is None) is complete
        assert e.trading_state == ("NORMAL" if complete else "REPAIR")
        assert await c.repair(*e.economic_inventory(), "no-reuse") is None
        assert len(v.post_requests) == 2
        events = journal_records(e.journal.path)
        assert not any(r["kind"] in {"sm_sl_signal", "sm_tp_signal", "sm_repair_policy_state"} for r in events)
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("quantity", [.005, .01, 2.5])
async def test_small_residual_returns_normal_without_erasing_inventory(env, monkeypatch, quantity):
    setup, c = await setup_exit(env, monkeypatch, size=quantity)
    v, e, feed = setup[:3]
    try:
        assert e.repair_episode is None and e.trading_state == "NORMAL"
        absorption(feed, "2")
        assert await c.repair(*e.economic_inventory(), "too-small") is None
        assert len(v.post_requests) == 1
        assert e.economic_inventory() == (D(str(quantity)), D(0))
        assert e.repair_episode is None
        events = journal_records(e.journal.path)
        complete = [r["data"]["payload"] for r in events if r["kind"] == "sm_repair_episode"
                    and r["data"]["payload"].get("event") == "COMPLETE_WITH_RESIDUAL_TOLERANCE"]
        assert complete[-1]["threshold_shares"] == "2.5"
        assert not complete[-1]["inventory_flat"]
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("quantity", [2.500001, 3])
async def test_minimum_order_can_reverse_into_small_residual_and_end_round(env, monkeypatch, quantity):
    setup, c = await setup_exit(env, monkeypatch, size=quantity)
    v, e, feed = setup[:3]
    try:
        assert e.repair_episode is not None
        absorption(feed, "2")

        def fill(venue):
            oid = e.attempts[-1].expected_order_id
            if venue.rows[oid]["asset_id"] == "2" and not venue.rows[oid]["associate_trades"]:
                venue.fill(oid, "minimum-exit", 5)
            reflect_positions(venue)

        v.on_cancel = fill
        result, _ = await c.repair(*e.economic_inventory(), "minimum-exit")
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert result.requested_quotes[0].size == 5
        assert e.economic_inventory() == (D(str(quantity)), D(5))
        assert e.repair_episode is None and e.trading_state == "NORMAL"
        assert len(v.post_requests) == 2
    finally:
        await cleanup(setup)


async def test_large_reverse_residual_reuses_fifo_handoff_and_waits_for_new_ba(env, monkeypatch):
    setup, c = await setup_exit(env, monkeypatch)
    v, e, feed, lifecycle, _, _, _, pump, _ = setup
    try:
        # A low-priced NO exit needs >5 shares to meet the existing $1 minimum.
        pump.update(bid=".87", yes_ask=".89", no_bid=".11", no_ask=".13")
        await asyncio.sleep(.025)
        absorption(feed, "2")
        round_id = e.cooldown.round_id

        def fill(venue):
            oid = e.attempts[-1].expected_order_id
            row = venue.rows[oid]
            if row["asset_id"] == "2" and not row["associate_trades"]:
                venue.fill(oid, "nine-point-one-no", 9.1)
            reflect_positions(venue)

        v.on_cancel = fill
        result, _ = await c.repair(*e.economic_inventory(), "low-price-exit")
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert result.requested_quotes[0].size == 9.1
        assert e.economic_inventory() == (D(5), D("9.1"))
        ep = e.repair_episode
        assert ep["side"] == "NO" and ep["direction_leg"] == 1
        assert D(ep["original_qty"]) == D("4.1") and D(ep["original_cost"]) == D(".451")
        assert e.cooldown.round_id == round_id and e.trading_state == "REPAIR"
        assert not c.reconcile_episode()
        lifecycle.account = (await e._snapshot(include_books=False))[3]
        assert await c.repair(*e.economic_inventory(), "no-new-signal") is None
        assert len(v.post_requests) == 2

        def pair(venue):
            oid = e.attempts[-1].expected_order_id
            if venue.rows[oid]["asset_id"] == "1" and not venue.rows[oid]["associate_trades"]:
                venue.fill(oid, "five-yes", 5)
            reflect_positions(venue)

        v.on_cancel = pair
        absorption(feed, "1")
        result, _ = await c.repair(*e.economic_inventory(), "reverse-ba-exit")
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert e.economic_inventory() == (D(10), D("9.1"))
        assert e.repair_episode is None and e.trading_state == "NORMAL"
        assert e.cooldown.last_result["round_id"] == round_id
        assert D(e.cooldown.last_result["paired_shares"]) == D("9.1")
        assert len(v.post_requests) == 3
        records = journal_records(e.journal.path)
        reverse = [r["data"]["payload"] for r in records if r["kind"] == "sm_repair_reverse"]
        assert len(reverse) == 1 and reverse[0]["mode"] == "WAIT_OPPOSITE_BA"
        assert not any(r["kind"] in {"sm_sl_signal", "sm_tp_signal", "sm_repair_policy_state"} for r in records)
    finally:
        await cleanup(setup)


async def test_new_signal_can_retry_then_only_new_entry_ba_can_resume_normal(env, monkeypatch):
    setup, c = await setup_exit(env, monkeypatch)
    v, e, feed, lifecycle = setup[:4]
    try:
        # Keep independent NORMAL BA evidence active alongside the exit detector.
        e.cfg.rdp_sm.normal_entry_mode = "ba_v1"
        e.cfg.rdp_sm.ba = e.cfg.rdp_sm.repair.opposite_ba.model_copy(deep=True)
        feed.ba = BidAbsorption(feed, e.cfg.rdp_sm.ba)
        normal = BANormalController(e, lifecycle)
        absorption(feed, "2")
        result, _ = await c.repair(*e.economic_inventory(), "exit-no-fill")
        assert result.close_barrier == "SAFE_TO_CONTINUE"
        assert e.repair_episode is not None
        lifecycle.account = (await e._snapshot(include_books=False))[3]
        absorption(feed, "2")

        def filled(venue):
            for oid, row in venue.rows.items():
                if (row["asset_id"] == "2" and row["status"] == "CANCELED"
                        and not row["associate_trades"] and oid == e.attempts[-1].expected_order_id):
                    venue.fill(oid, "second-ba-filled", 5)
            reflect_positions(venue)

        v.on_cancel = filled
        result, _ = await c.repair(*e.economic_inventory(), "exit-new-ba")
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert len(v.post_requests) == 3 and e.trading_state == "NORMAL"
        # An entry BA observed DURING exit is retired at completion.
        assert normal.select(feed.snapshot())[0] is None
        lifecycle.account = (await e._snapshot(include_books=False))[3]
        assert await normal.enter(*e.economic_inventory(), "old-entry") is None
        assert len(v.post_requests) == 3
        v.on_cancel = None
        absorption(feed, "1")
        result, _ = await normal.enter(*e.economic_inventory(), "new-entry")
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert len(v.post_requests) == 4
    finally:
        await cleanup(setup)


async def test_market_cutoff_blocks_new_exit_without_t180_fallback(env, monkeypatch):
    setup, c = await setup_exit(env, monkeypatch)
    v, e, feed = setup[:3]
    try:
        e.time_anchor = e.monotonic()
        e.official_time = int(e.slug.rsplit("-", 1)[1])+240
        absorption(feed, "2")
        await c.repair(*e.economic_inventory(), "too-late")
        assert len(v.post_requests) == 1 and e.repair_episode is not None
        assert c.boundary_close_reason(None, 180) is None
        assert c.boundary_close_reason(None, 240) == "MARKET_SUBMISSION_CUTOFF"
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("cached", [False, True])
async def test_selected_policy_wires_dedicated_feed_and_controller_through_real_runner(env, monkeypatch, cached):
    from test_rdp_sm_obi_entry import (
        test_new_mode_entire_entry_has_no_rest_book_and_owns_ws_teardown,
    )

    cfg = env[1]
    cfg.rdp_sm.repair.policy = "opposite_ba_v1"
    created = []
    native = OppositeBAController.__init__

    def capture(self, executor, lifecycle):
        native(self, executor, lifecycle)
        assert self.episodes is lifecycle.feed.opposite_ba
        assert self.episodes.cfg is cfg.rdp_sm.repair.opposite_ba
        assert self.episodes is not lifecycle.feed.ba
        created.append(self)

    monkeypatch.setattr(OppositeBAController, "__init__", capture)
    await test_new_mode_entire_entry_has_no_rest_book_and_owns_ws_teardown(
        env, monkeypatch, disconnect_preflight=False, cancel_before_order=False,
        prior_unresolved=False, cached=cached, mode="ba_v1")
    assert len(created) == 1


@pytest.mark.parametrize("fault", ["expires", "anchor", "network", "unknown"])
async def test_final_guards_and_unknown_ack_do_not_duplicate_orders(env, monkeypatch, fault):
    setup, c = await setup_exit(env, monkeypatch)
    v, e, feed, lifecycle, _, wire = setup[:6]
    try:
        absorption(feed, "2")
        if fault in {"expires", "anchor"}:
            native = e.gateway.send_prepared

            async def changed(*args, **kwargs):
                if fault == "expires":
                    await asyncio.sleep(.32)
                else:
                    feed.opposite_ba.invalidate("BA_ANCHOR_INVALID", "2")
                return await native(*args, **kwargs)

            monkeypatch.setattr(e.gateway, "send_prepared", changed)
        elif fault == "network":
            e.reads.network.observe("POST", .01, success=False)
        else:
            wire.fail_ack = True
        await c.repair(*e.economic_inventory(), "guarded-exit")
        assert len(v.post_requests) == (2 if fault == "unknown" else 1)
        assert e.repair_episode is not None
        assert await c.repair(*e.economic_inventory(), "do-not-repeat") is None
        assert len(v.post_requests) == (2 if fault == "unknown" else 1)
    finally:
        await cleanup(setup)
