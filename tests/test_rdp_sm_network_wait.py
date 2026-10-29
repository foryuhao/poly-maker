"""Network admission waits must not turn a hot book into account polling."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from polymaker.rdp_sm_loop import RunResult, SMTradingLoop
from polymaker.rdp_sm_normal import NormalController
from test_rdp_sm_cached_reads import attach, finish
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_obi_execution import cleanup, setup_obi
from test_rdp_sm_repair import env as env
from test_rdp_sm_repair import open_yes


async def controlled_loop(setup, monkeypatch, *, elapsed=20, duration=1.2, recovery=None):
    v, e, feed = setup[:3]
    clock = SimpleNamespace(now=e.monotonic())
    origin = clock.now
    start = int(e.slug.rsplit("-", 1)[1])
    e.official_time, e.time_anchor = start+elapsed, origin
    native_snapshot = e._snapshot
    reads, sleeps = [], []

    async def server_time():
        return start+elapsed+clock.now-origin

    async def snapshot(**kwargs):
        reads.append(clock.now-origin)
        return await native_snapshot(**kwargs)

    async def sleep(seconds):
        assert 0 < seconds <= 1
        sleeps.append(seconds)
        clock.now += seconds
        await asyncio.sleep(.01)  # Keep the real 100 Hz mocked socket alive.

    async def no_book_wait(*_args):
        raise AssertionError("blocked network must not retry the account on a book tick")

    blocked = replace(e.network_health(), allowed=False, state="PAUSED", reason="NETWORK_PAUSED")
    healthy = replace(blocked, allowed=True, state="PROBE", reason="NETWORK_PROBE_READY")
    monkeypatch.setattr(e, "monotonic", lambda: clock.now)
    monkeypatch.setattr(e.reads, "server_time", server_time)
    monkeypatch.setattr(e, "_snapshot", snapshot)
    monkeypatch.setattr(e, "network_health", lambda *_: healthy if recovery is not None and clock.now-origin >= recovery else blocked)
    monkeypatch.setattr(feed, "wait_after", no_book_wait)
    loop = SMTradingLoop(e.cfg, e.slug, e.session_id, sleep=sleep, monotonic=lambda: clock.now)
    loop.executor, loop.production = e, True
    loop.market_deadline = origin+duration
    monkeypatch.setattr(loop, "_merge", lambda *_: asyncio.sleep(0, result=False))
    return loop, clock, reads, sleeps, origin


@pytest.mark.parametrize("repair", [False, True])
async def test_hot_feed_does_not_control_network_account_retry_rate(env, monkeypatch, repair):
    setup = await setup_obi(env, monkeypatch)
    v, e, feed = setup[:3]
    try:
        if repair:
            await open_yes(setup)
        loop, _, reads, _, _ = await controlled_loop(setup, monkeypatch)
        before_posts, before_ticks = len(v.post_requests), setup[7]["count"]
        result = RunResult(e.meta.condition_id, e.session_id)
        await asyncio.wait_for(loop._run_obi_session(result), 3)
        assert reads == pytest.approx([0, .5, 1.0])
        assert len(v.post_requests) == before_posts and setup[7]["count"] > before_ticks
        assert result.stop_reason == "MARKET_CUTOFF"
        assert result.final_close_barrier == "SAFE_TO_CONTINUE"
    finally:
        await cleanup(setup)


async def test_probe_recovery_rechecks_original_admission_before_entering(env, monkeypatch):
    setup = await setup_obi(env, monkeypatch)
    v, e = setup[:2]
    try:
        loop, clock, reads, _, origin = await controlled_loop(setup, monkeypatch, recovery=.5)
        entries = []

        async def enter(_controller, *_args):
            assert e.network_health().allowed and (not e.attempts or e.safe_to_continue)
            entries.append(clock.now-origin)
            loop.market_deadline = clock.now
            return None

        monkeypatch.setattr(NormalController, "enter", enter)
        result = RunResult(e.meta.condition_id, e.session_id)
        await asyncio.wait_for(loop._run_obi_session(result), 3)
        assert entries == pytest.approx([.5]) and reads == pytest.approx([0, .5])
        assert not v.post_requests and result.final_close_barrier == "SAFE_TO_CONTINUE"
    finally:
        await cleanup(setup)


async def test_nonnetwork_invalid_book_keeps_existing_feed_wakeup(env, monkeypatch):
    setup = await setup_obi(env, monkeypatch)
    v, e, feed = setup[:3]
    try:
        loop = SMTradingLoop(e.cfg, e.slug, e.session_id)
        loop.executor, loop.production = e, True
        setup[7]["running"] = False
        await setup[-1]
        feed.invalidate("WS_DISCONNECTED")
        calls = []

        async def wait_after(sequence, timeout):
            calls.append(timeout)
            loop.market_deadline = e.monotonic()

        monkeypatch.setattr(feed, "wait_after", wait_after)
        monkeypatch.setattr(loop, "_merge", lambda *_: asyncio.sleep(0, result=False))
        result = RunResult(e.meta.condition_id, e.session_id)
        await asyncio.wait_for(loop._run_obi_session(result), 3)
        assert calls == [.5] and not v.post_requests
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("hazard", ["fatal", "journal", "closed", "inventory", "merge", "proof", "abandon", "timer"])
async def test_memory_safety_events_interrupt_retry_without_account_reads(hazard):
    clock = SimpleNamespace(now=100.)
    executor = SimpleNamespace(monotonic=lambda: clock.now, official_time=20., time_anchor=100.,
        fatal=None, journal=SimpleNamespace(failure=None), closed=False, inventory_mismatch=None,
        merge_pending=False, attempts=[object()], safe_to_continue=True, admission_ready=True,
        repair_abandonment=None)
    boundary = SimpleNamespace(done=lambda: False)
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)
        clock.now += seconds
        if hazard == "fatal":
            executor.fatal = "TEST_FATAL"
        elif hazard == "journal":
            executor.journal.failure = "TEST_JOURNAL_FAILURE"
        elif hazard == "closed":
            executor.closed = True
        elif hazard == "inventory":
            executor.inventory_mismatch = {"reason": "TEST_UNEXPLAINED"}
        elif hazard == "merge":
            executor.merge_pending = True
        elif hazard == "proof":
            executor.safe_to_continue = False
            executor.admission_ready = False
        elif hazard == "abandon":
            executor.repair_abandonment = {"event": "TRIGGERED"}
        else:
            boundary.done = lambda: True
        await asyncio.sleep(0)

    loop = SMTradingLoop(None, "btc-updown-5m-0", "test", sleep=sleep)
    loop.executor = executor
    await loop._wait_network_retry(boundary)
    assert sleeps == [.05] and clock.now == 100.05


async def test_loss_of_close_proof_is_handled_before_the_next_account_snapshot(env, monkeypatch):
    setup = await setup_obi(env, monkeypatch)
    v, e = setup[:2]
    try:
        await open_yes(setup)
        loop, clock, reads, _, origin = await controlled_loop(setup, monkeypatch)
        native_sleep = loop.sleep
        verifications = []

        async def sleep(seconds):
            await native_sleep(seconds)
            if not verifications:
                e.invalidate("TEST_CLOSE_EVIDENCE_CHANGED")

        async def verify_close(**_kwargs):
            verifications.append(clock.now-origin)
            return False

        # The existing close-recovery pause is allowed to sleep longer than
        # the new memory-only network wait, and advances directly to cutoff.
        async def pause(*_args, **_kwargs):
            loop.market_deadline = clock.now

        loop.sleep = sleep
        monkeypatch.setattr(e, "verify_close", verify_close)
        monkeypatch.setattr(loop, "_pause", pause)
        before = len(v.post_requests)
        result = RunResult(e.meta.condition_id, e.session_id)
        await asyncio.wait_for(loop._run_obi_session(result), 3)
        assert reads == [0] and verifications[0] == pytest.approx(.05)
        assert len(v.post_requests) == before and result.final_close_barrier == "BLOCKED"
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("boundary", [180, 240])
async def test_repair_time_boundaries_keep_running_during_network_wait(env, monkeypatch, boundary):
    setup = await setup_obi(env, monkeypatch)
    v, e = setup[:2]
    try:
        await open_yes(setup)
        e.cfg.rdp_sm.repair.policy = "hd_tp_sl_v1"
        loop, clock, reads, _, origin = await controlled_loop(setup, monkeypatch, elapsed=boundary-.1)
        loop.market_deadline = origin+300-(boundary-.1)
        native_snapshot = e._snapshot

        async def snapshot(**kwargs):
            value = await native_snapshot(**kwargs)
            if len(reads) >= 4:
                loop.market_deadline = clock.now
            return value

        monkeypatch.setattr(e, "_snapshot", snapshot)
        before = len(v.post_requests)
        result = RunResult(e.meta.condition_id, e.session_id)
        await asyncio.wait_for(loop._run_obi_session(result), 3)
        events = [r["data"]["payload"] for r in journal_records(e.journal.path)
                  if r["kind"] == "sm_repair_time_boundary"]
        assert any(p["boundary"] == boundary for p in events)
        assert len(v.post_requests) == before
    finally:
        await cleanup(setup)


async def test_real_periodic_cache_keeps_updating_without_hot_feed_consumer_spin(env, monkeypatch, tmp_path):
    setup = await setup_obi(env, monkeypatch)
    companion, calls = await attach(setup, tmp_path)
    v, e = setup[:2]
    loop = SMTradingLoop(e.cfg, e.slug, e.session_id)
    loop.executor, loop.production = e, True
    loop.market_deadline = e.monotonic()+1.15
    blocked = replace(e.network_health(), allowed=False, state="PAUSED", reason="NETWORK_PAUSED")
    monkeypatch.setattr(e, "network_health", lambda *_: blocked)
    monkeypatch.setattr(loop, "_merge", lambda *_: asyncio.sleep(0, result=False))
    native_snapshot = e._snapshot
    snapshots = []

    async def snapshot(**kwargs):
        if e.shared_reads.active is None:
            snapshots.append(e.monotonic())
        return await native_snapshot(**kwargs)

    async def no_book_wait(*_args):
        raise AssertionError("cached network pause must use a timer")

    monkeypatch.setattr(e, "_snapshot", snapshot)
    monkeypatch.setattr(setup[2], "wait_after", no_book_wait)
    first_version, first_calls, ticks = companion.service.latest().version, len(calls), setup[7]["count"]
    began = e.monotonic()
    try:
        result = RunResult(e.meta.condition_id, e.session_id)
        await asyncio.wait_for(loop._run_obi_session(result), 4)
        assert 2 <= len(snapshots) <= 3
        assert all(b-a >= .49 for a, b in zip(snapshots, snapshots[1:], strict=False))
        await e.journal.flush()
        account_reads = [r for r in journal_records(e.journal.path)
                         if r["kind"] == "sm_sync_cache_read" and r["data"]["occurred_monotonic"] >= began
                         and r["data"]["payload"]["purpose"] == "account"]
        assert len(account_reads) == len(snapshots)
        assert companion.service.latest().version > first_version and len(calls) > first_calls
        assert setup[7]["count"] > ticks and not v.post_requests
        assert all(method == "GET" for method, *_ in calls)
        assert result.stop_reason == "MARKET_CUTOFF" and result.final_close_barrier == "SAFE_TO_CONTINUE"
    finally:
        await finish(setup, companion)


@pytest.mark.parametrize("initialized", [False, True])
async def test_unavailable_cache_close_preserves_reason_and_handles_uninitialized_meta(
    env, monkeypatch, tmp_path, initialized,
):
    setup = await setup_obi(env, monkeypatch)
    companion, calls = await attach(setup, tmp_path)
    v, e = setup[:2]
    try:
        await asyncio.to_thread(companion.service.stop)
        loop, _, _, sleeps, _ = await controlled_loop(setup, monkeypatch, duration=.2)
        result = RunResult(e.meta.condition_id, e.session_id)
        if not initialized:
            e.meta = None  # The first foreground snapshot never succeeded.
            # Preserve existing reporting fields when there are no market facts
            # to recompute inventory; a failed read cannot prove zero inventory.
            result.confirmed_yes = 7
        before = len(calls)

        def no_direct_get(*_args, **_kwargs):
            raise AssertionError("unavailable cache must not fall back to foreground GETs")

        monkeypatch.setattr(e.gateway._client, "_get", no_direct_get)
        await asyncio.wait_for(loop._run_obi_session(result), 3)
        assert sleeps == pytest.approx([.2])
        assert result.execution_status == "INCOMPLETE"
        assert result.final_close_barrier == "BLOCKED"
        assert result.stop_reason == e.reason == "SYNC_CACHE_UNAVAILABLE"
        assert result.confirmed_yes == (0 if initialized else 7)
        assert result.total_windows == 0 and not v.post_requests and not v.cancels
        assert len(calls) == before
        await e.journal.flush()
        barriers = [row["data"]["payload"] for row in journal_records(e.journal.path)
                    if row["kind"] == "sm_close_barrier"]
        assert barriers[-1]["reason"] == "SYNC_CACHE_UNAVAILABLE"
        assert not any(task.get_name() in {"rdp-repair-time-boundaries", "rdp-network-warmup"}
                       for task in asyncio.all_tasks() if not task.done())
    finally:
        await finish(setup, companion)
