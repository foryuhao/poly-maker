"""CA-only admission through ordered WS evidence and the real maker SDK path."""

import asyncio
import json
from copy import deepcopy
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from polymaker.config import RDPSMConfig
from polymaker.rdp_sm_ca import CounterAttack
from polymaker.rdp_sm_normal import CANormalController
from test_rdp_sm_ba import absorption, depth, execution_setup, trade
from test_rdp_sm_ba import env as env
from test_rdp_sm_ca import buy, ca_feed, fresh_at
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_obi_execution import cleanup


async def ca_setup(env, monkeypatch):
    setup = list(await execution_setup(env, monkeypatch))
    _, e, f, lifecycle = setup[:4]
    e.cfg.rdp_sm.normal_entry_mode = f.normal_entry_mode = "ca_v1"
    e.cfg.rdp_sm.ca_observe.enabled = True
    f.ca = CounterAttack(f, e.cfg.rdp_sm.ba, e.cfg.rdp_sm.ca_observe)
    setup[4] = CANormalController(e, lifecycle)
    return setup


@pytest.mark.parametrize("token", ["1", "2"])
async def test_ca_alone_posts_at_same_bid_size_original_t0_deadline_and_once(env, monkeypatch, token):
    setup = await ca_setup(env, monkeypatch)
    v, e, f, _, normal, wire = setup[:6]
    try:
        f.apply([trade(f, token), buy(f, token)])
        ep = f.ca.states[token]
        assert f.ba.states[token].ba_confirmed_mono is None
        result, sizing = await normal.enter(D(0), D(0), "ca-entry")
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert len(v.post_requests) == 1
        row = v.post_requests[0][0]
        assert row["postOnly"] and row["order"]["side"] == "BUY"
        assert row["order"]["tokenId"] == token
        assert result.requested_quotes[0].price == float(ep.anchor_bid_price)
        assert result.requested_quotes[0].size == 5
        assert result.t0_monotonic == ep.t0
        assert result.fill_deadline_monotonic == ep.deadline
        assert next(t[2] for t in wire.timeline if t[0] == "DELETE") >= ep.deadline
        assert sizing["normal_entry_mode"] == "ca_v1"
        assert e.repair_episode is None and e.ba_opening is None
        assert await normal.enter(D(0), D(0), "again") is None
        assert len(v.post_requests) == 1
        logs = journal_records(e.journal.path)
        event = next(r["data"]["payload"] for r in logs if r["kind"] == "sm_ca_final_decision")
        i = event["intent"]
        assert event["mode"] == "ca_v1" and i["claim"][0] == ep.episode_id
        assert i["ca_admission"]["t0"] == ep.t0
        obs = i["ca_observation"]
        assert obs["mode"] == "NORMAL_ENTRY"
        assert obs["observed_monotonic"] <= i["post_started"]
        assert obs["outcomes"][ep.outcome]["confirmed"]
        assert any(r["kind"] == "sm_ca_normal_execution" for r in logs)
        assert not any(r["kind"] == "sm_ba_final_decision" for r in logs)
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("case", ["BA_only", "below_10", "missing_observer", "writer_failed", "observer_failed", "both_sides"])
async def test_no_fallback_to_ba_or_obi_without_eligible_ca(env, monkeypatch, case):
    setup = await ca_setup(env, monkeypatch)
    v, _, f, _, normal = setup[:5]
    try:
        absorption(f)
        if case == "below_10":
            f.apply(buy(f, size="9.803922"))
        elif case in {"writer_failed", "observer_failed", "both_sides"}:
            f.apply(buy(f))
            if case == "writer_failed":
                f.ca.writer = SimpleNamespace(failure="CA_LOG_FAILED:OSError")
            elif case == "observer_failed":
                def broken():
                    raise RuntimeError("unavailable evidence")
                monkeypatch.setattr(f.ca, "snapshot", broken)
            else:
                f.apply([trade(f, "2"), buy(f, "2")])
        elif case == "missing_observer":
            f.ca = None
        assert await normal.enter(D(0), D(0), "no-ca") is None
        assert not v.post_requests
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("fault", ["expires_signing", "expires_post", "anchor_post", "generation_post", "book_post", "capital", "network", "T120"])
async def test_ca_final_guard_cannot_be_bypassed(env, monkeypatch, fault):
    setup = await ca_setup(env, monkeypatch)
    v, e, f, lifecycle, normal = setup[:5]
    try:
        f.apply([trade(f), buy(f)])
        if fault == "expires_signing":
            native = e.gateway.prepare
            async def slow(*args, **kwargs):
                signed = await native(*args, **kwargs)
                await asyncio.sleep(.26)
                return signed
            monkeypatch.setattr(e.gateway, "prepare", slow)
        elif fault in {"expires_post", "anchor_post", "generation_post", "book_post"}:
            native = e.gateway.send_prepared
            async def delayed(*args, **kwargs):
                if fault == "expires_post":
                    await asyncio.sleep(.26)
                elif fault == "anchor_post":
                    f.apply(depth(f, 20, price=".51"))
                elif fault == "generation_post":
                    setup[-2]["running"] = False
                    f.connect()
                else:
                    setup[-2]["running"] = False
                    f.invalidate("WS_DISCONNECTED")
                return await native(*args, **kwargs)
            monkeypatch.setattr(e.gateway, "send_prepared", delayed)
        elif fault == "capital":
            lifecycle.account["basic_spendable_budget_usd"] = 0
        elif fault == "network":
            e.reads.network.state = "PAUSED"
            e.reads.network.pause_until = e.monotonic()+10
        else:
            e.official_time = int(e.slug.rsplit("-", 1)[1])+120
            e.time_anchor = e.monotonic()
        await normal.enter(D(0), D(0), "guard-ca")
        assert not v.post_requests
        assert not f.ca_entry.consumed
        assert not any(r["event"] == "CA_NORMAL_POST" for r in f.ca.records)
    finally:
        await cleanup(setup)


def test_entry_uses_confirming_buy_freshness_and_first_sell_age_independently():
    f, clock = ca_feed(observation_sec=.75, max_sell_age_ms=50)
    clock.now = .01
    f.apply(trade(f))
    for t in (.03, .06, .09, .12, .15, .18, .2):
        fresh_at(f, clock, t)
    f.apply(buy(f))
    assert f.ca_entry.evaluate(f.snapshot()).direction == "YES"
    fresh_at(f, clock, .23)
    fresh_at(f, clock, .251)
    assert f.ca_entry.evaluate(f.snapshot()).direction is None
    assert f.ca.states["1"].tC is not None


def test_conflict_does_not_retire_or_consume_either_ba_detector():
    f, _ = ca_feed()
    for token in ("1", "2"):
        absorption(f, token)
        f.apply(buy(f, token))
    before = [deepcopy(detector.states) for detector in (f.ba, f.opposite_ba)]
    assert not f.ca_entry.evaluate(f.snapshot()).direction
    assert all(e.end_reason == "CA_BOTH_SIDES_VALID" for e in f.ca.states.values())
    assert [detector.states for detector in (f.ba, f.opposite_ba)] == before
    assert all(not e.consumed for states in before for e in states.values())


async def test_ca_snapshot_failure_at_native_post_hook_prevents_actual_post(env, monkeypatch):
    setup = await ca_setup(env, monkeypatch)
    v, _, f, _, normal = setup[:5]
    try:
        f.apply([trade(f), buy(f)])
        snapshot = f.ca.snapshot
        def fail_after_consumption():
            if f.ca_entry.consumed:
                raise RuntimeError("final evidence failed")
            return snapshot()
        monkeypatch.setattr(f.ca, "snapshot", fail_after_consumption)
        result, _ = await normal.enter(D(0), D(0), "ca-native-fault")
        assert result.close_barrier == "SAFE_TO_CONTINUE"
        assert not v.post_requests
        assert not result.placement_results[0]["post_started"]
        assert not any(r["event"] == "CA_NORMAL_POST" for r in f.ca.records)
        assert f.ca.failure == "CA_OBSERVER_ERROR:RuntimeError"
    finally:
        await cleanup(setup)


async def test_ca_fill_owns_repair_and_opposite_ba_exits_even_if_ca_unavailable(env, monkeypatch):
    from polymaker.rdp_sm_ba import BidAbsorption
    from polymaker.rdp_sm_opposite_ba import OppositeBAController
    from test_rdp_sm_repair import reflect_positions

    setup = await ca_setup(env, monkeypatch)
    v, e, f, lifecycle, normal = setup[:5]
    e.cfg.rdp_sm.repair.policy = "opposite_ba_v1"
    e.cfg.rdp_sm.repair.opposite_ba = e.cfg.rdp_sm.ba.model_copy(deep=True)
    f.opposite_ba = BidAbsorption(f, e.cfg.rdp_sm.repair.opposite_ba)
    try:
        def fill(venue):
            oid = e.attempts[-1].expected_order_id
            if not venue.rows[oid]["associate_trades"]:
                venue.fill(oid, "fill-"+venue.rows[oid]["asset_id"], 5)
            reflect_positions(venue)
        v.on_cancel = fill
        f.apply([trade(f), buy(f)])
        result, _ = await normal.enter(D(0), D(0), "ca-filled")
        assert result.close_barrier == "SAFE_TO_CONTINUE"
        assert e.economic_inventory() == (D(5), D(0))
        assert e.repair_episode is not None and e.trading_state == "REPAIR"
        lifecycle.account = (await e._snapshot(include_books=False))[3]
        f.ca.disable("CA_LOG_FAILED:OSError")
        absorption(f, "2")
        opposite = OppositeBAController(e, lifecycle)
        result, sizing = await opposite.repair(*e.economic_inventory(), "ba-exit")
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert sizing["policy"] == "opposite_ba_v1"
        assert e.economic_inventory() == (D(5), D(5))
        assert e.repair_episode is None and e.safe_to_continue
        assert len(v.post_requests) == 2
        assert v.post_requests[-1][0]["order"]["tokenId"] == "2"
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("enabled", [False, True])
def test_ca_mode_requires_enabled_evidence_and_keeps_repair_policy(enabled):
    if enabled:
        cfg = RDPSMConfig(normal_entry_mode="ca_v1", ca_observe={"enabled": True},
                          repair={"policy": "opposite_ba_v1"})
        assert cfg.ca_observe.min_buy_shares == 10
        assert cfg.repair.policy == "opposite_ba_v1"
    else:
        with pytest.raises(ValidationError, match="ca_observe.enabled"):
            RDPSMConfig(normal_entry_mode="ca_v1")


@pytest.mark.parametrize("cached", [False, True])
async def test_real_runner_ca_controller_no_ba_confirmation_and_safe_teardown(env, monkeypatch, tmp_path, cached):
    import test_rdp_sm_obi_entry as runner

    _, cfg = env
    cfg.rdp_sm.ca_observe.enabled = True
    cfg.rdp_sm.ca_observe.output_dir = str(tmp_path/"ca")
    cfg.rdp_sm.repair.policy = "opposite_ba_v1"
    cfg.rdp_sm.ba.observation_sec = .2
    cfg.rdp_sm.ba.signal_lifetime_sec = .3
    cfg.rdp_sm.ba.max_sell_age_ms = 200
    cfg.rdp_sm.ba.max_entry_age_ms = 250
    cfg.rdp_sm.ba.network.post_budget_floor_ms = 10
    cfg.rdp_sm.ba.network.min_live_budget_ms = 10
    original = runner.SessionFeed
    feeds = []
    def factory(*args, **kwargs):
        feed = original(*args, **kwargs)
        apply = feed.apply
        def causal_prints(raw, **clock):
            decoded = json.loads(raw) if isinstance(raw, str) and raw.startswith("[") else raw
            if isinstance(decoded, list):
                for row in decoded:
                    if row.get("event_type") == "book":
                        row["bids"][0]["size"] = "80"
                        row["asks"][0]["size"] = "20"
            apply(decoded, **clock)
            if feed.connected and feed.books["1"].best_bid is not None:
                apply([trade(feed), buy(feed)])
        feed.apply = causal_prints
        feeds.append(feed)
        return feed
    monkeypatch.setattr(runner, "SessionFeed", factory)
    await runner.test_new_mode_entire_entry_has_no_rest_book_and_owns_ws_teardown(
        env, monkeypatch, disconnect_preflight=False, cancel_before_order=False,
        prior_unresolved=False, cached=cached, mode="ca_v1")
    paths = await asyncio.to_thread(lambda: list(Path(cfg.paths.journal_dir).glob("*obi-entry.jsonl")))
    rows = [r for path in paths
            for r in journal_records(path)]
    header = next(r["data"]["payload"] for r in rows if r["kind"] == "sm_session")
    assert header["normal_entry_mode"] == "ca_v1"
    assert header["normal_active_order_policy"] == "CA_FIXED_T0_DEADLINE"
    assert header["ca_observation"]["mode"] == "NORMAL_ENTRY"
    assert any(r["kind"] == "sm_ca_final_decision" for r in rows)
    assert not any(r["kind"] == "sm_ba_episode" and r["data"]["payload"]["event"] == "CONFIRMED" for r in rows)
    result = next(r["data"]["payload"] for r in rows if r["kind"] == "sm_run_result")
    assert result["ca_observation"]["snapshot"]["data_complete"]
    assert not result["ca_observation"]["snapshot"]["recording_active"]
    assert feeds and all(f.normal_entry_mode == "ca_v1" for f in feeds)
    assert all(not f.ca.writer.thread.is_alive() for f in feeds)
    files = list((tmp_path/"ca").glob("*/events.jsonl"))
    assert len(files) == 1
    events = [json.loads(s) for s in files[0].read_text().splitlines()]
    assert len([e for e in events if e["event"] == "CA_NORMAL_POST"]) == 1
