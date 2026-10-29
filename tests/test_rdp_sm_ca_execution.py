"""Read-only CA failure/association at real SDK, WS, filesystem boundaries."""

import asyncio
import hashlib
import json
import threading
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace

import pytest

from polymaker.config import RDPCAObserveConfig, RDPSMConfig
from polymaker.rdp_sm_ca import CounterAttack
from polymaker.rdp_sm_ca_writer import CAWriter
from polymaker.rdp_sm_execution import _ExecutionReads
from polymaker.rdp_sm_marketdata import SessionFeed
from test_rdp_sm_ba import absorption, execution_setup
from test_rdp_sm_ba import env as env
from test_rdp_sm_ca import buy, ca_feed, state
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_obi import feed_fixture
from test_rdp_sm_obi_execution import cleanup


@pytest.mark.parametrize("mode", ["no_ca", "same", "opposite", "observer_error", "sink_failure"])
async def test_ba_native_post_and_deadline_do_not_depend_on_ca(env, monkeypatch, mode):
    setup = await execution_setup(env, monkeypatch)
    v, e, f, _, normal = setup[:5]
    f.ca = CounterAttack(f, f.ba.cfg, RDPCAObserveConfig(enabled=True))
    try:
        absorption(f)
        ep = f.ba.states["1"]
        if mode == "same":
            f.apply(buy(f))
        elif mode == "opposite":
            from test_rdp_sm_ba import trade
            f.apply(trade(f, "2"))
            f.apply(buy(f, "2"))
        elif mode == "observer_error":
            def broken():
                raise RuntimeError("CA snapshot failed")
            monkeypatch.setattr(f.ca, "snapshot", broken)
        elif mode == "sink_failure":
            f.ca.writer = SimpleNamespace(failure="CA_LOG_FAILED:OSError",
                submit=lambda row: False, status=lambda: {}, close=lambda: None)
        result, sizing = await normal.enter(D(0), D(0), "ca-isolation")
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert len(v.post_requests) == 1 and len(v.post_requests[0]) == 1
        posted = v.post_requests[0][0]
        assert posted["postOnly"] and posted["order"]["side"] == "BUY"
        assert posted["order"]["tokenId"] == "1"
        assert result.requested_quotes[0].size == 5 and result.requested_quotes[0].price == .5
        assert result.t0_monotonic == ep.signal_start_mono
        assert result.fill_deadline_monotonic == ep.order_deadline
        assert sizing["normal_entry_mode"] == "ba_v1" and e.fatal is None
        await e.journal.flush()
        event = next(r["data"]["payload"] for r in journal_records(e.journal.path)
                     if r["kind"] == "sm_ba_final_decision")
        obs = event["intent"]["ca_observation"]
        assert obs["observed_monotonic"] <= event["intent"]["post_started"]
        if mode in {"sink_failure", "observer_error"}:
            assert not obs["data_complete"] and obs["failure"]
        else:
            assert obs["outcomes"]["YES"]["confirmed"] is (mode == "same")
            assert obs["outcomes"]["NO"]["confirmed"] is (mode == "opposite")
            assert len([r for r in f.ca.records if r["event"] == "CA_NORMAL_POST"]) == 1
    finally:
        await cleanup(setup)


async def test_ca_only_never_creates_a_normal_order(env, monkeypatch):
    setup = await execution_setup(env, monkeypatch)
    v, _, f, _, normal = setup[:5]
    f.ca = CounterAttack(f, f.ba.cfg, RDPCAObserveConfig(enabled=True))
    try:
        from test_rdp_sm_ba import trade
        f.apply(trade(f))
        f.apply(buy(f))
        assert state(f)["confirmed"]
        assert await normal.enter(D(0), D(0), "ca-alone") is None
        assert not v.post_requests
    finally:
        await cleanup(setup)


async def test_confirmation_during_http_post_cannot_backfill_opening_evidence(env, monkeypatch):
    setup = await execution_setup(env, monkeypatch)
    _, e, f, _, normal, wire = setup[:6]
    f.ca = CounterAttack(f, f.ba.cfg, RDPCAObserveConfig(enabled=True))
    try:
        absorption(f)
        wire.delay = lambda: f.apply(buy(f))  # native POST boundary has already passed
        result, _ = await normal.enter(D(0), D(0), "ca-no-lookahead")
        assert result.close_barrier == "SAFE_TO_CONTINUE"
        assert f.ca.states["1"].tC is not None
        await e.journal.flush()
        i = next(r["data"]["payload"]["intent"] for r in journal_records(e.journal.path)
                 if r["kind"] == "sm_ba_final_decision")
        assert not i["ca_observation"]["outcomes"]["YES"]["confirmed"]
        assert i["ca_observation"]["observed_monotonic"] <= i["post_started"] < f.ca.states["1"].tC
    finally:
        await cleanup(setup)


async def test_aborted_ba_candidate_has_no_ca_actual_post_record(env, monkeypatch):
    setup = await execution_setup(env, monkeypatch)
    v, _, f, lifecycle, normal = setup[:5]
    f.ca = CounterAttack(f, f.ba.cfg, RDPCAObserveConfig(enabled=True))
    try:
        absorption(f)
        f.apply(buy(f))
        lifecycle.account["basic_spendable_budget_usd"] = 0
        await normal.enter(D(0), D(0), "no-native-post")
        assert not v.post_requests
        assert not any(r["event"] == "CA_NORMAL_POST" for r in f.ca.records)
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("sink_failure", [False, True])
async def test_ca_during_real_opposite_repair_never_changes_exit_or_consumes_ca(env, monkeypatch, sink_failure):
    from polymaker.rdp_sm_ba import BidAbsorption
    from test_rdp_sm_opposite_ba import setup_exit
    from test_rdp_sm_repair import reflect_positions

    setup, opposite = await setup_exit(env, monkeypatch)
    v, e, f = setup[:3]
    e.cfg.rdp_sm.normal_entry_mode = "ba_v1"
    f.ba = BidAbsorption(f, e.cfg.rdp_sm.ba)
    f.ca = CounterAttack(f, e.cfg.rdp_sm.ba, RDPCAObserveConfig(enabled=True))
    try:
        absorption(f, "2")
        f.apply(buy(f, "2"))
        assert state(f, "NO")["confirmed"]
        if sink_failure:
            f.ca.writer = SimpleNamespace(failure="CA_LOG_FAILED:OSError",
                submit=lambda row: False, status=lambda: {}, close=lambda: None)
        def fill(venue):
            oid = e.attempts[-1].expected_order_id
            if venue.rows[oid]["asset_id"] == "2" and not venue.rows[oid]["associate_trades"]:
                venue.fill(oid, "ca-observed-exit", 5)
            reflect_positions(venue)
        v.on_cancel = fill
        result, _ = await opposite.repair(*e.economic_inventory(), "ca-during-repair")
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert len(v.post_requests) == 2 and result.requested_quotes[0].outcome == "NO"
        assert e.economic_inventory() == (D(5), D(5)) and e.repair_episode is None
        await e.journal.flush()
        decision = next(r["data"]["payload"] for r in journal_records(e.journal.path)
                        if r["kind"] == "sm_opposite_ba_final_decision")
        assert "ca_observation" not in decision["intent"]
        assert not any(r["event"] == "CA_NORMAL_POST" for r in f.ca.records)
    finally:
        await cleanup(setup)


async def test_execution_feed_factory_starts_isolated_writer_with_normal_ba_parameters(tmp_path, monkeypatch):
    old, _, _ = feed_fixture()
    sm = RDPSMConfig(normal_entry_mode="ba_v1",
        ba={"signal_lifetime_sec": 3, "max_book_age_ms": 200},
        ca_observe={"enabled": True, "output_dir": str(tmp_path)})
    reads = SimpleNamespace(cfg=SimpleNamespace(rdp_sm=sm, proxy=None), feed=None, ca_session_id="session-a")
    async def ticks():
        reads.feed.verify_ticks({t: b.tick for t, b in old.books.items()})
    async def run(feed):
        await feed.stop_event.wait()
    reads.refresh_feed_ticks = ticks
    monkeypatch.setattr(SessionFeed, "run", run)
    f = await _ExecutionReads.ensure_feed(reads, old.meta)
    try:
        assert f.ca is not None and f.ca.parameters["B_min"] == "10"
        assert f.ca.parameters["T_life"] == 3 and f.ca.parameters["max_book_age_ms"] == 200
    finally:
        await f.close()
    path = tmp_path/hashlib.sha256(b"session-a").hexdigest()[:32]/"events.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows[0]["event"] == "CA_SESSION" and rows[-1]["event"] == "CA_SUMMARY"
    assert all(r["session_id"] == "session-a" for r in rows)
    assert not f.ca.writer.thread.is_alive() and f.ca.writer.failure is None


async def test_writer_filesystem_failure_is_visible_without_affecting_ba(tmp_path):
    parent = tmp_path/"not-a-directory"
    parent.write_text("blocked")
    writer = CAWriter(parent/"events.jsonl")
    f, _ = ca_feed()
    f.ca.writer = writer
    try:
        await asyncio.to_thread(writer.thread.join, 1)
        assert writer.failure.startswith("CA_LOG_FAILED:")
        absorption(f)
        assert f.ba.evaluate(f.snapshot()).direction == "YES"
        snap = f.observe_ca("snapshot")
        assert not snap["data_complete"] and snap["failure"] == writer.failure
        assert f.connected and f.failure_count == 0
    finally:
        await f.close()


def test_writer_backpressure_never_waits_for_disk_and_exposes_gap(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    native = Path.open
    def held_open(path, *args, **kwargs):
        entered.set()
        assert release.wait(2)
        return native(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", held_open)
    writer = CAWriter(tmp_path/"events.jsonl", capacity=1)
    try:
        assert entered.wait(1)
        assert writer.submit({"event": "accepted"})
        assert not writer.submit({"event": "overflow"})
        assert writer.failure == "CA_LOG_QUEUE_OVERFLOW" and writer.dropped == 1
    finally:
        release.set()
        writer.close()
    assert not writer.thread.is_alive()


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("sink_blocked", [False, True])
async def test_ca_enabled_entire_runner_has_healthy_context_and_drains_observer(env, monkeypatch, tmp_path, cached, sink_blocked):
    from test_rdp_sm_obi_entry import (
        test_new_mode_entire_entry_has_no_rest_book_and_owns_ws_teardown,
    )

    cfg = env[1]
    cfg.rdp_sm.ca_observe.enabled = True
    cfg.rdp_sm.ca_observe.output_dir = str(tmp_path/"ca")
    if sink_blocked:
        (tmp_path/"ca").write_text("not a directory")
    cfg.rdp_sm.repair.policy = "opposite_ba_v1"
    await test_new_mode_entire_entry_has_no_rest_book_and_owns_ws_teardown(
        env, monkeypatch, disconnect_preflight=False, cancel_before_order=False,
        prior_unresolved=False, cached=cached, mode="ba_v1")
    paths = await asyncio.to_thread(lambda: list(Path(cfg.paths.journal_dir).glob("*obi-entry.jsonl")))
    journals = [r for p in paths for r in journal_records(p)]
    final = next(r["data"]["payload"] for r in journals if r["kind"] == "sm_run_result")
    assert final["ca_observation"]["snapshot"]["data_complete"] is (not sink_blocked)
    if sink_blocked:
        assert final["ca_observation"]["snapshot"]["failure"].startswith("CA_LOG_FAILED:")
        return  # the reused runner assertions already proved the same successful BA order
    assert not final["ca_observation"]["snapshot"]["recording_active"]
    path = tmp_path/"ca"/hashlib.sha256(b"obi-entry").hexdigest()[:32]/"events.jsonl"
    records = [json.loads(line) for line in path.read_text().splitlines()]
    summary = next(r for r in records if r["event"] == "CA_SUMMARY")
    assert summary["payload"]["snapshot"]["data_complete"]
    assert summary["payload"]["snapshot"]["failure"] is None
    posts = [r for r in records if r["event"] == "CA_NORMAL_POST"]
    assert len(posts) == 1 and posts[0]["coordinator_phase"] == "NORMAL"
    obs = posts[0]["payload"]["snapshot"]
    assert obs["observed_monotonic"] <= posts[0]["payload"]["post_started_monotonic"]
    session = next(r["data"]["payload"] for r in journals if r["kind"] == "sm_session")
    assert session["ca_observation"]["config"]["enabled"]
    assert session["ca_observation"]["runtime"]["parameters"]["B_min"] == "10"
