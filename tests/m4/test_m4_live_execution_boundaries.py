"""Three execution regressions using synthetic HTTP only; socket guard is autouse."""

import asyncio
import json
from dataclasses import replace
from decimal import Decimal as D
from threading import Event
from types import SimpleNamespace

import pytest
from m4_helpers import close, fixture_feed, form, op
from test_m4_live_budget import fixture
from test_m4_live_task import evidence, serve

from polymaker.m4_live_numeric import install_live_clock_boundaries


@pytest.mark.parametrize("change,reason", [
    ("cutoff", "M4_ENTRY_WINDOW_ENDED"),
    ("latency", "M4_POST_LATENCY_BUDGET"),
    ("stop", "M4_STOPPING"),
    ("cash", "M4_ACCOUNT_BUDGET"),
    ("tick", "M4_TAKER_TICK_UNVERIFIED"),
])
async def test_native_guard_after_durable_commit(tmp_path, monkeypatch, change, reason):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch)
    ep = next(iter(m.pending.values()))
    original = m.ledger.post_start
    network_observations = []
    e.gateway._client.network = SimpleNamespace(
        observe=lambda *args, **kwargs: network_observations.append((args, kwargs)))
    e.official_time = int(e.meta.slug.rsplit("-", 1)[1]) + 239
    e.time_anchor = c.now

    def after_commit(key, **kwargs):
        original(key, **kwargs)
        assert m.ledger.row(key)["state"] == "UNKNOWN"
        if change == "cutoff":
            c.now += 2
        elif change == "latency":
            c.now += .7
        elif change == "stop":
            m.stop.set()
        elif change == "cash":
            m.account["official_balance_usd"] = "0"
        elif change == "tick":
            f.tick_verified.pop(ep.token_id, None)

    monkeypatch.setattr(m.ledger, "post_start", after_commit)
    try:
        await m.submit(ep)
        assert not calls and not network_observations and e.fatal is None
        a = m.ledger.attempts()[0]
        assert a["state"] == "UNSENT" and a["started"] == 1
        proof = json.loads(a["proof"])
        assert proof == {"kind": "LOCAL_NATIVE_POST_BLOCKED", "order_id": a["order_id"],
                         "native_post_sent": False, "reason": reason}
        assert m.ledger.row(ep.episode_id)["spent"] == "0"
        assert not m.ledger.unresolved(ep.episode_id)
        assert not e.attempts[0].post_started
        assert m.ledger.facts("owned_post_receipt") == []
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()


async def test_proven_local_unsent_retries_new_signature_after_tick_verification(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch)
    ep = next(iter(m.pending.values()))
    original = m.ledger.post_start
    tick = f.tick_verified[ep.token_id]

    def tick_unverified(key, **kwargs):
        original(key, **kwargs)
        f.tick_verified.pop(ep.token_id, None)

    monkeypatch.setattr(m.ledger, "post_start", tick_unverified)
    try:
        await m.submit(ep)
        old_id = m.ledger.attempts()[0]["order_id"]
        f.tick_verified[ep.token_id] = tick
        monkeypatch.setattr(m.ledger, "post_start", original)
        await m.submit(ep)
        a = m.ledger.attempts()[-1]
        assert a["order_id"] != old_id and a["state"] == "ACK"
        assert len(calls) == 1
        assert D(json.loads(a["plan"])["planned_cost_usdc"]) <= 5
        assert m.ledger.committed() <= 10
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()


async def test_crash_before_local_unsent_proof_keeps_unknown(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch)
    ep = next(iter(m.pending.values()))
    original = m.ledger.post_start

    def stop_after_commit(key, **kwargs):
        original(key, **kwargs)
        m.stop.set()

    def failed_durability(*args):
        raise OSError("synthetic proof commit failure")

    monkeypatch.setattr(m.ledger, "post_start", stop_after_commit)
    monkeypatch.setattr(m.ledger, "native_not_sent", failed_durability)
    try:
        with pytest.raises(OSError, match="proof commit failure"):
            await m.submit(ep)
        assert not calls and m.ledger.unresolved(ep.episode_id)
        assert m.ledger.attempts()[0]["state"] == "UNKNOWN"
        assert m.ledger.committed() > 0
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()


async def test_late_prior_unknown_still_blocks_at_final_boundary(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".30")
    q = None
    try:
        ep = next(iter(m.pending.values()))
        await m.submit(ep)
        oid = m.ledger.row(ep.episode_id)["order_id"]
        order, trade = evidence(m, e, oid, "5", status="MINED")
        q, _ = serve(monkeypatch, t, order, trade)
        await m.reconcile(oid)
        original = m.ledger.post_start

        def late_conflict(key, **kwargs):
            original(key, **kwargs)
            m.ledger.block_reconciliation(oid, {"reason": "synthetic late conflict"})

        monkeypatch.setattr(m.ledger, "post_start", late_conflict)
        await m.submit(ep)
        assert len(calls) == 1 and m.ledger.unresolved(ep.episode_id)
        a = m.ledger.attempts()[-1]
        assert a["state"] == "UNSENT" and a["started"]
        assert json.loads(a["proof"])["reason"] == "M4_UNRESOLVED_PRIOR_ATTEMPT"
        assert m.ledger.row(ep.episode_id)["state"] == "WAIT_SETTLEMENT"
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()
        if q:
            q.close()


@pytest.mark.parametrize("field,delta,valid", [
    ("source_time", .002, True), ("source_time", .002001, False),
    ("source_time", -.500, True), ("source_time", -.500001, False),
    ("received_mono", -.200, True), ("received_mono", -.200001, False),
    ("received_mono", .000001, False),
])
def test_frequency_candidate_uses_live_clock_boundary(field, delta, valid):
    feed, clock = fixture_feed()
    baseline, baseline_clock = fixture_feed()
    install_live_clock_boundaries(feed)
    form(feed, clock)
    form(baseline, baseline_clock)
    original_tick = baseline.m1.frequency.tick.__func__
    assert feed.m1.frequency.candidates
    value = (feed.wall() if field == "source_time" else clock.now) + delta
    feed.books["1"] = replace(feed.books["1"], **{field: value})
    assert bool(feed.m1.legal(feed.books["1"], clock.now)) == valid
    feed.m1.frequency.tick()
    assert bool(feed.m1.frequency.candidates) == valid
    # Fresh materialization also uses the predicate, not just retained candidates.
    feed.m1.frequency.candidates.clear()
    feed.m1.frequency.tick()
    assert bool(feed.m1.frequency.candidates) == valid
    assert baseline.m1.frequency.tick.__func__ is original_tick
    assert baseline.m1.frequency.tick.__func__ is not feed.m1.frequency.tick.__func__


async def test_slow_native_post_does_not_block_other_signal_and_drain_waits(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch)
    first = next(iter(m.pending.values()))
    entered, release = asyncio.Event(), Event()
    loop = asyncio.get_running_loop()
    client = e.gateway._client
    original = client._native_order_post
    finished = []

    def slow_native(*args, **kwargs):
        attempt = client._operation_value("attempt")
        result = original(*args, **kwargs)
        if attempt.quote.window_id == first.episode_id:
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5), "test did not release synthetic POST response"
        finished.append(attempt.quote.window_id)
        return result

    monkeypatch.setattr(client, "_native_order_post", slow_native)
    monkeypatch.setattr(m, "collect", lambda: None)
    drain = None
    try:
        await m.advance_entries(c.now)
        await asyncio.wait_for(entered.wait(), 2)
        second = op("parallel-second", price=".709", token="2")
        m.ledger.observe(second, minimum=5, elapsed=m.elapsed())
        m.ledger.plan_signal(second.episode_id, f.books["2"].tick, 5)
        m.pending[second.episode_id] = second
        await m.advance_entries(c.now)
        await asyncio.wait_for(asyncio.shield(m.post_jobs[second.episode_id]), 2)
        assert not m.post_jobs[first.episode_id].done()
        assert finished == [second.episode_id]
        assert len(calls) == 2 and m.ledger.committed() <= 10
        assert m.ledger.row(first.episode_id)["state"] == "UNKNOWN"
        assert m.ledger.row(second.episode_id)["state"] == "ACK"
        assert len({a["order_id"] for a in m.ledger.attempts()}) == 2
        # T240 cleanup cannot erase inputs while any native worker is still active.
        e.official_time += 240
        await m.recycle_after_cutoff()
        assert not m.memory_recycled
        m.stop.set()
        drain = asyncio.create_task(m.drain())
        await asyncio.sleep(.02)
        assert not drain.done()
        release.set()
        await asyncio.wait_for(asyncio.shield(drain), 2)
        assert not m.post_jobs and not m.posting_keys
        assert len(calls) == 2 and e.fatal is None
        m.ledger.validate_recovery()
    finally:
        release.set()
        if drain:
            await drain
        await close(m, e)
        t.close()


async def test_same_signal_concurrency_cannot_duplicate_unknown_post(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, response="TIMEOUT")
    ep = next(iter(m.pending.values()))
    queries = []

    async def unknown(oid):
        queries.append(oid)
        m.ledger.block_reconciliation(oid, {"reason": "synthetic order endpoint unavailable"})

    monkeypatch.setattr(m, "reconcile", unknown)
    try:
        await asyncio.gather(m.submit(ep), m.submit(ep), m.submit(ep))
        assert len(calls) == 1 and len(m.ledger.attempts()) == 1
        assert len(queries) == 2 and m.ledger.unresolved(ep.episode_id)
        assert not m.posting_keys and m.ledger.committed() <= 10
    finally:
        await close(m, e)
        t.close()
