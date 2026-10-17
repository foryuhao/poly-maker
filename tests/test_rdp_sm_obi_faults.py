"""Disk, restart and thread scheduling faults at external boundaries."""

import asyncio
import json
import threading
import time
from contextlib import suppress
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from polymaker.rdp_sm_fence import AccountLease, create_fence, recover_fence
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_obi_execution import cleanup, setup_obi
from test_rdp_sm_obi_execution import env as env


def test_account_state_overwrites_truncates_and_survives_process_reopen(tmp_path):
    cfg = SimpleNamespace(paths=SimpleNamespace(journal_dir=str(tmp_path)))
    lease = AccountLease(cfg, "test-account")
    path = lease.path
    inode = path.stat().st_ino
    try:
        lease.activate(tmp_path / ("long-fence-" * 10), "long-session-" * 10)
        lease.verified_closed()
        closed_state = json.loads(path.read_text())
        assert closed_state == lease.state and closed_state["status"] == "CLOSED"
        with pytest.raises(BlockingIOError):
            AccountLease(cfg, "test-account")
        assert json.loads(path.read_text()) == closed_state
        assert path.stat().st_ino == inode
    finally:
        lease.close()
    lease = AccountLease(cfg, "test-account")
    try:
        assert lease.state == closed_state
        lease.activate(tmp_path / "short", "next")
        assert json.loads(path.read_text()) == lease.state
        assert path.stat().st_ino == inode
    finally:
        lease.close()
    lease = AccountLease(cfg, "test-account")
    try:
        assert lease.state["status"] == "UNRESOLVED" and lease.state["session_id"] == "next"
        lease.verified_closed()
        assert json.loads(path.read_text())["status"] == "CLOSED"
    finally:
        lease.close()


@pytest.mark.parametrize("raw", ['{"status":"UNRESOLVED"}{"status":"CLOSED"}', '{"status":'])
def test_corrupt_account_state_still_blocks_without_modifying_evidence(tmp_path, raw):
    cfg = SimpleNamespace(paths=SimpleNamespace(journal_dir=str(tmp_path)))
    lease = AccountLease(cfg, "test-account")
    path = lease.path
    lease.close()
    path.write_text(raw)
    with pytest.raises(json.JSONDecodeError):
        AccountLease(cfg, "test-account")
    assert path.read_text() == raw


async def test_prepared_fsync_wait_does_not_block_feed_and_never_posts_early(env, monkeypatch):
    setup = await setup_obi(env, monkeypatch)
    v, e, _, _, normal, _, _, state, _ = setup
    entered, release = threading.Event(), threading.Event()
    original = e.journal.journal.write

    def slow(kind, payload, ts):
        if kind == "sm_prepared":
            entered.set()
            assert release.wait(3)
        original(kind, payload, ts)

    monkeypatch.setattr(e.journal.journal, "write", slow)
    task = asyncio.create_task(normal.enter(Decimal(0), Decimal(0), "slow-wal"))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        count = state["count"]
        await asyncio.sleep(0.15)
        assert state["count"] > count + 5 and not v.post_requests
        release.set()
        result, _ = await task
        assert result.close_barrier == "SAFE_TO_CONTINUE"
        assert len(v.post_requests) == 1
    finally:
        release.set()
        await cleanup(setup)


async def test_failed_preparation_disk_write_is_zero_post(env, monkeypatch):
    setup = await setup_obi(env, monkeypatch)
    v, e, _, _, normal, *_ = setup

    def fail(*_):
        raise OSError("disk full")

    monkeypatch.setattr(e.journal.journal, "write", fail)
    try:
        # Request diagnostics may expose the disk failure before the durable
        # candidate write; either failure path must prevent the POST.
        with pytest.raises((OSError, RuntimeError)):
            await normal.enter(Decimal(0), Decimal(0), "disk-full")
        assert not v.post_requests and e.journal.failure
    finally:
        with suppress(RuntimeError):
            await cleanup(setup)


async def test_restart_cancels_all_durable_candidates_and_never_reuses_slot(env, monkeypatch):
    setup = await setup_obi(env, monkeypatch)
    v, e, _, lifecycle, normal, *_ = setup
    try:
        slot = await lifecycle.prepare(normal, Decimal(0), Decimal(0), "crash-before-post")
        create_fence(e.cfg, e.gateway.funder, e.meta, e.session_id, e.journal.path)
        # Simulate a POST/ACK-log crash: one candidate has reached the venue,
        # while no post-started or ACK record exists in the durable journal.
        candidate = slot.candidates[0]
        oid, q = candidate.signed.order_ids[0], candidate.quotes[0]
        v.rows[oid] = {
            "id": oid,
            "market": e.meta.condition_id,
            "asset_id": q.token_id,
            "side": "BUY",
            "price": str(q.price),
            "original_size": str(q.size),
            "size_matched": "0",
            "status": "LIVE",
            "associate_trades": [],
        }
        assert not any(r["kind"] == "sm_placement_result" for r in journal_records(e.journal.path))
        with pytest.raises(ValueError, match="OBI_RESTART_NORMAL_DISABLED"):
            await recover_fence(e.reads, e.meta)
        assert v.rows[oid]["status"] == "CANCELED" and not v.post_requests
        assert set(v.cancels[0]) == {oid for c in slot.candidates for oid in c.signed.order_ids}
    finally:
        await cleanup(setup)


async def test_read_pool_congestion_does_not_starve_inflight_cancellation(env, monkeypatch):
    setup = await setup_obi(env, monkeypatch)
    v, e, _, _, normal, wire, *_ = setup
    entered, release = threading.Event(), threading.Event()

    def post_wait():
        entered.set()
        assert release.wait(3)

    wire.delay = post_wait
    task = asyncio.create_task(normal.enter(Decimal(0), Decimal(0), "congested"))
    read_tasks = []
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        read_tasks = [asyncio.create_task(e.gateway._io(release.wait, 3)) for _ in range(7)]
        await asyncio.sleep(0.18)
        assert v.cancels, "reserved DELETE capacity was starved by reads/POST"
        release.set()
        result, _ = await task
        await asyncio.gather(*read_tasks)
        assert result.close_barrier == "SAFE_TO_CONTINUE"
        assert all(row["status"] == "CANCELED" for row in v.rows.values())
    finally:
        release.set()
        await asyncio.gather(*read_tasks, return_exceptions=True)
        await cleanup(setup)


async def test_cancelled_owner_drains_same_post_then_closes_all_legs(env, monkeypatch):
    setup = await setup_obi(env, monkeypatch)
    v, _, _, _, normal, wire, *_ = setup
    entered, release = threading.Event(), threading.Event()
    wire.delay = lambda: (entered.set(), release.wait(3))
    task = asyncio.create_task(normal.enter(Decimal(0), Decimal(0), "interrupted"))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        await asyncio.sleep(0.01)
        task.cancel()
        release.set()
        result, _ = await task
        assert result.close_barrier == "BLOCKED" and len(v.post_requests) == 1
        assert all(row["status"] == "CANCELED" for row in v.rows.values())
    finally:
        release.set()
        await cleanup(setup)


async def test_unresolved_prior_market_does_not_block_independent_new_market(env, monkeypatch):
    setup = await setup_obi(env, monkeypatch)
    v, e, _, lifecycle, normal, *_ = setup
    try:
        lease = AccountLease(e.cfg, e.gateway.funder)
        e.reads.lease = lease
        with pytest.raises(BlockingIOError):
            AccountLease(e.cfg, e.gateway.funder)
        slot = await lifecycle.prepare(normal, Decimal(0), Decimal(0), "unresolved")
        create_fence(e.cfg, e.gateway.funder, e.meta, e.session_id, e.journal.path, lease)
        assert lease.state["status"] == "UNRESOLVED"
        other = replace(e.meta, condition_id="0x" + "cd" * 32)
        await recover_fence(e.reads, other)
        assert not v.post_requests and not v.cancels
        assert lease.state["status"] == "UNRESOLVED"  # No fabricated historical close.
        # Account concurrency and the original market's episode fence remain.
        with pytest.raises(BlockingIOError):
            AccountLease(e.cfg, e.gateway.funder)
        with pytest.raises(ValueError, match="OBI_RESTART_NORMAL_DISABLED"):
            await recover_fence(e.reads, e.meta)
        assert set(v.cancels[0]) == {oid for c in slot.candidates for oid in c.signed.order_ids}
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("old_state", ["missing", "corrupt"])
async def test_new_market_does_not_read_missing_or_corrupt_prior_fence_or_journal(
    env, monkeypatch, old_state,
):
    setup = await setup_obi(env, monkeypatch)
    v, e, _, _, _, *_ = setup
    try:
        lease = AccountLease(e.cfg, e.gateway.funder)
        e.reads.lease = lease
        old_fence = lease.path.parent / "rdp_obi_market_old.json"
        if old_state == "corrupt":
            old_fence.write_text('{"journal_path":')
        lease.activate(old_fence, "old-session")
        original = old_fence.read_text() if old_fence.exists() else None
        other = replace(e.meta, condition_id="0x" + "cd" * 32)
        await recover_fence(e.reads, other)
        assert not v.post_requests and not v.cancels
        assert lease.state["session_id"] == "old-session"
        assert (old_fence.read_text() if old_fence.exists() else None) == original
    finally:
        await cleanup(setup)


async def test_missing_current_market_fence_still_blocks_same_market_restart(env, monkeypatch):
    setup = await setup_obi(env, monkeypatch)
    v, e, _, _, _, *_ = setup
    try:
        from polymaker.rdp_sm_fence import fence_path

        e.reads.lease = lease = AccountLease(e.cfg, e.gateway.funder)
        lease.activate(fence_path(e.cfg, e.gateway.funder, e.meta.condition_id), "same-market")
        with pytest.raises(ValueError, match="OBI_RECOVERY_FENCE_MISSING"):
            await recover_fence(e.reads, e.meta)
        assert not v.post_requests and not v.cancels
    finally:
        await cleanup(setup)


async def test_writer_failure_after_post_closes_orders_without_waiting_for_disk(env, monkeypatch):
    setup = await setup_obi(env, monkeypatch, horizon=2)
    v, e, _, _, normal, *_ = setup
    original = e.journal.journal.write

    def fail_after_post(kind, payload, ts):
        if kind == "sm_strategy_window":
            raise OSError("runtime disk failure")
        original(kind, payload, ts)

    monkeypatch.setattr(e.journal.journal, "write", fail_after_post)
    began = time.monotonic()
    try:
        with pytest.raises(RuntimeError):
            await normal.enter(Decimal(0), Decimal(0), "runtime-disk-failure")
        assert len(v.post_requests) == 1
        assert v.cancels and all(row["status"] == "CANCELED" for row in v.rows.values())
        assert time.monotonic() - began < 1 and not e.safe_to_continue
    finally:
        with suppress(RuntimeError):
            await cleanup(setup)


async def test_cancel_during_prepared_write_drains_future_without_killing_writer(env, monkeypatch):
    setup = await setup_obi(env, monkeypatch)
    v, e, _, _, normal, *_ = setup
    entered, release = threading.Event(), threading.Event()
    original = e.journal.journal.write

    def slow(kind, payload, ts):
        if kind == "sm_prepared":
            entered.set()
            assert release.wait(3)
        original(kind, payload, ts)

    monkeypatch.setattr(e.journal.journal, "write", slow)
    task = asyncio.create_task(normal.enter(Decimal(0), Decimal(0), "cancel-prepared"))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        await asyncio.sleep(0.01)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await e.journal.flush()
        assert not v.post_requests and not e.journal.failure and e.journal.thread.is_alive()
        assert e.preparation_started and not e.interrupted_before_orders()
    finally:
        release.set()
        await cleanup(setup)
