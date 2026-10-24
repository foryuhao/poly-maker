"""Repair scheduling only: real coordinator/publication, no external I/O."""
import asyncio
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from polymaker.rdp_sm_cached_reads import CachedExecutionReads
from polymaker.rdp_sm_sl import SLExecution
from polymaker.rdp_sm_sync import SyncObserver
from polymaker.rdp_sm_sync_reads import ReadEvidence
from test_rdp_sm_path_repair import env as env


def service(*, probe=.04):
    observer = SyncObserver(lambda *_: None)
    observer.network = SimpleNamespace(cfg=SimpleNamespace(network_probe_interval_sec=probe),
                                       snapshot=lambda: SimpleNamespace(state="NORMAL"))
    observer._loop = asyncio.get_running_loop()
    observer._async_stop = asyncio.Event()
    return observer


async def test_publication_wakes_registered_waiters_from_coordinator_thread_without_lost_wake():
    observer = service()
    waiters = [asyncio.create_task(observer.wait_after_async(0, 1)) for _ in range(4)]
    await asyncio.sleep(0)
    assert len(observer._async_waiters) == 4
    await asyncio.to_thread(observer._publish)
    frames = await asyncio.gather(*waiters)
    assert all(frame.version == 1 for frame in frames)
    # Publication before registration also returns immediately.
    assert (await observer.wait_after_async(0, 0)).version == 1
    assert not observer._async_waiters


async def test_cancel_timeout_and_shutdown_leave_no_publication_waiters():
    observer = service()
    task = asyncio.create_task(observer.wait_after_async(0, 1))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not observer._async_waiters
    assert (await observer.wait_after_async(0, .001)).version == 0
    assert not observer._async_waiters
    task = asyncio.create_task(observer.wait_after_async(0, 1))
    await asyncio.sleep(0)
    observer._publish(status="STOPPED")
    assert (await task).status == "STOPPED"
    assert not observer._async_waiters


@pytest.mark.parametrize("lane", ["orders", "account"])
async def test_refresh_coalesces_and_never_overlaps_or_exceeds_probe_cadence(lane):
    observer = service()
    starts, active = [], 0

    async def read_round():
        nonlocal active
        active += 1
        assert active == 1
        starts.append(time.monotonic())
        # Arriving during a read must survive until the next round, coalesced.
        for _ in range(100):
            assert observer.request_repair_refresh((lane,))
        await asyncio.sleep(.012)
        active -= 1
        if len(starts) == 3:
            observer._stop.set()
        return True, 0., True

    await asyncio.wait_for(observer._periodic(lane, .6, read_round), .5)
    assert len(starts) == 3
    assert all(.035 <= b-a < .2 for a, b in zip(starts[:-1], starts[1:], strict=True))


async def test_failed_round_and_retry_after_remain_hard_lower_bounds(monkeypatch):
    observer = service()
    deadlines = []

    async def read_round():
        observer.request_repair_refresh(("orders",))
        return False, 3.5, False

    async def inspect(lane, regular, earliest):
        deadlines.append((regular, earliest, time.monotonic()))
        observer._stop.set()

    monkeypatch.setattr(observer, "_wait_round", inspect)
    await observer._periodic("orders", 1., read_round)
    regular, earliest, now = deadlines[0]
    assert regular >= now+3.4 and earliest >= now+3.4


async def test_pending_refresh_cannot_wake_before_embargo_and_stop_interrupts_it():
    observer = service()
    observer.request_repair_refresh()
    start = time.monotonic()
    await observer._wait_round("orders", start+.09, start+.06)
    assert time.monotonic()-start >= .055
    task = asyncio.create_task(observer._wait_round("orders", start+60, start+60))
    await asyncio.sleep(0)
    observer._stop.set()
    observer._async_stop.set()
    await asyncio.wait_for(task, .1)


async def test_observe_only_rejects_refresh_and_invalid_lane_is_rejected():
    observer = service()
    observer.network = None
    assert not observer.request_repair_refresh(("orders", "account"))
    assert not observer._refresh_pending
    with pytest.raises(ValueError, match="SYNC_REFRESH_LANE_INVALID"):
        observer.request_repair_refresh(("invalid",))


def cache_fixture():
    cache = CachedExecutionReads.__new__(CachedExecutionReads)
    cache.service = service()
    cache.e = SimpleNamespace(repair_episode={"id": "repair"}, repair_abandonment=None,
                              cooldown=SimpleNamespace(active=False), sl_order_ids={"repair"})
    cache.last_write = 0.
    cache.post_returns, cache.order_writes, cache.inflight = {}, {}, {}
    cache.last_acquired = None
    cache.companion = SimpleNamespace(failure=None)
    return cache


@pytest.mark.parametrize("state", ["normal", "cooldown", "abandoned"])
async def test_normal_cooldown_and_abandonment_do_not_accelerate_reads(state):
    cache = cache_fixture()
    if state == "normal":
        cache.e.repair_episode = None
    elif state == "cooldown":
        cache.e.cooldown.active = True
    else:
        cache.e.repair_abandonment = {"triggered": True}
    cache.end_write(cache.begin_write("POST", ("repair",)))
    assert not cache.refresh_repair(account=True)
    assert not cache.service._refresh_pending


async def test_only_owned_repair_write_requests_refresh_and_boundaries_still_advance():
    cache = cache_fixture()
    cache.end_write(cache.begin_write("POST", ("normal",)))
    assert not cache.service._refresh_pending
    for kind in ("POST", "DELETE"):
        before = time.monotonic()
        cache.end_write(cache.begin_write(kind, ("repair",)))
        assert cache.order_writes["repair"] >= before
        assert cache.service._refresh_pending == {"orders", "account"}
        cache.service._refresh_pending.clear()
    assert cache.post_returns["repair"] <= cache.order_writes["repair"]
    assert not cache.inflight


async def test_inventory_retry_wakes_on_account_evidence_not_order_publications():
    cache = cache_fixture()
    observer = cache.service
    now = time.monotonic()
    read = ReadEvidence("positions", "account", 0, 0, now, now, True, "[]", "[]", 1,
                        read_id="account-before")
    observer._attempts["positions"] = read
    observer._publish()
    cache.last_acquired = observer.latest()
    task = asyncio.create_task(cache.wait_account_update(1))
    await asyncio.sleep(0)
    for _ in range(4):
        observer._publish()
        await asyncio.sleep(0)
    assert not task.done()
    observer._attempts["positions"] = replace(read, read_id="account-after")
    observer._publish()
    await asyncio.wait_for(task, .1)
    # Evidence published between the failed scope and wait registration is seen.
    await asyncio.wait_for(cache.wait_account_update(1), .1)
    assert not observer._async_waiters


async def test_completed_proof_can_wake_actor_before_poll_ceiling():
    actor = SLExecution.__new__(SLExecution)
    actor.changed = asyncio.Event()
    actor.changed.set()
    await asyncio.wait_for(actor.wait_changed(), .003)


async def test_unknown_three_round_recovery_advances_slow_heartbeat_without_early_normal(
        env, monkeypatch, tmp_path, record_property):
    import test_rdp_sm_cached_reads as cached_tests
    from polymaker.rdp_sm import _Issue
    from test_rdp_sm_m2_execution import journal_records
    from test_rdp_sm_unknown_recovery import fault_setup

    native_companion = cached_tests.SyncCompanion

    def slow_companion(cfg, *args, **kwargs):
        cfg.rdp_sm.sync_observe.order_interval_s = .25
        cfg.rdp_sm.sync_observe.account_interval_s = .35
        cfg.rdp_sm.obi.network_probe_interval_sec = .04
        return native_companion(cfg, *args, **kwargs)

    monkeypatch.setattr(cached_tests, "SyncCompanion", slow_companion)
    setup, _, actor, companion, client, state, calls = await fault_setup(
        env, monkeypatch, tmp_path)
    e = setup[1]
    try:
        await asyncio.wait_for(actor.run(), 4)
        assert len(state["posts"]) == 2
        await e.journal.flush()
        records = journal_records(e.journal.path)
        ready = [r["data"]["payload"] for r in records
                 if r["kind"] == "sm_unknown_repair_recovery"
                 and r["data"]["payload"]["event"] == "READY"]
        assert ready and ready[0]["rounds"] == 3 and not ready[0]["normal_allowed"]
        unknown = state["unknown"]
        elapsed = actor.signal_times[state["posts"][1].attempt_id]-actor.post_returned[unknown.attempt_id]
        record_property("unknown_repair_ready_s", elapsed)
        assert 0 < elapsed < .6  # Three independent .35 s account heartbeats no longer dominate.
        assert unknown.outcome == "UNKNOWN" and unknown.order_id not in e.proofs
        assert not e.safe_to_continue
        # Null retains its independent two-second detail backoff despite urgent rounds.
        assert sum(path == "/data/order/"+unknown.order_id for _, path, _ in calls) == 1
        with pytest.raises(_Issue, match="PREVIOUS_WINDOW_UNRESOLVED"):
            await setup[4].enter(*e.economic_inventory(), "no-normal")
    finally:
        from test_rdp_sm_cached_reads import finish

        state["cutoff"] = True
        await finish(setup, companion)
        client.close()



async def test_repair_inventory_wait_only_notifies_and_preserves_mismatch_until_rechecked():
    from polymaker.rdp_sm_loop import RunResult, SMTradingLoop

    cache = cache_fixture()
    e = cache.e
    mismatch = {"reason": "UNEXPLAINED"}
    e.inventory_mismatch = mismatch
    e.shared_reads = cache
    e.reads = SimpleNamespace(server_time=lambda: asyncio.sleep(0, result=320.))

    async def owned(awaitable):
        return await awaitable

    e._await_owned = owned
    loop = SMTradingLoop.__new__(SMTradingLoop)
    loop.executor, loop.slug, loop.observer = e, "test-300", None
    loop._inventory = lambda *_: None
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)

    loop.sleep = sleep
    now = time.monotonic()
    read = ReadEvidence("positions", "account", 0, 0, now, now, True, "[]", "[]", 1,
                        read_id="positions-before")
    cache.service._attempts["positions"] = read
    cache.service._publish()
    cache.last_acquired = cache.service.latest()
    task = asyncio.create_task(loop._wait_for_inventory(RunResult("market", "session")))
    for _ in range(5):
        await asyncio.sleep(0)
    assert not task.done() and cache.service._refresh_pending == {"orders", "account"}
    cache.service._attempts["positions"] = replace(read, read_id="positions-after")
    cache.service._publish()
    await asyncio.wait_for(task, .1)
    assert not sleeps and e.inventory_mismatch is mismatch
    # Notification never clears the discrepancy or authorizes an order itself.
    e.cooldown.active = True
    await loop._wait_for_inventory(RunResult("market", "session"))
    assert sleeps == [1]
