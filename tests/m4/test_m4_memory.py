"""T240 reclamation with real offline signing/ledger and delayed SDK workers."""
# ruff: noqa: ASYNC240 -- Isolated pytest files only.
import asyncio
import json
import threading
from decimal import Decimal as D
from pathlib import Path

import pytest
from m4_helpers import close, fixture_feed, form, op, setup
from test_m4_recovery import reopen

from polymaker import m2_memory, m4_execution


def rows(executor):
    return [json.loads(line) for line in Path(executor.journal.path).read_text().splitlines()]


async def test_cutoff_closes_feed_flushes_then_reclaims_once(tmp_path, monkeypatch):
    m, e, f, c = await setup(tmp_path)
    calls = []
    roots = f.m1.used_roots.copy()
    f.m1.record = lambda row: e.record('sm_m4_signal', row)
    original = f.release_signal_memory

    def release():
        # The close/invalidation evidence must already be on disk.
        assert any(r['kind'] == 'sm_m4_feed_stopped' for r in rows(e))
        return original()

    async def running():
        await asyncio.Event().wait()

    f.task = asyncio.create_task(running())
    task = f.task
    await asyncio.sleep(0)
    monkeypatch.setattr(f, 'release_signal_memory', release)
    monkeypatch.setattr(m4_execution, 'recycle_idle_memory', lambda: calls.append(1) or {})
    try:
        with pytest.raises(RuntimeError, match='FEED_NOT_STOPPED'):
            original()
        e.official_time += 219.9
        e.official_time_uncertainty_s = 1
        assert not m.entry_window_open()
        await m.recycle_after_cutoff()
        assert not m.feed_closed and not calls  # No GC before lower bound T240.
        e.official_time += .1
        await m.advance()
        await m.drain()
        assert task.cancelled() and not f.connected and f.task is None
        assert m.memory_recycle['status'] == 'OK' and calls == [1]
        assert all(not b.bids and not b.asks for b in f.books.values())
        assert not f.m1.events and not f.m1.opportunities and not f.m1.context
        assert not f.m1.rounds.keys and not f.m1.rounds.key_set
        assert not f.m1.frequency.states and not f.m1.frequency.candidates
        assert f.m1.used_roots == roots and f.m1.counts['ROUND_CLOSED'] > 0
        assert m.ledger.rows()[0]['state'] == 'SKIPPED'
        assert m.ledger.metric()['native_posts'] == 0
        await m.advance()
        assert calls == [1]
        # A new market owns fresh signal objects and can form the same pattern.
        fresh, clock = fixture_feed()
        form(fresh, clock)
        assert fresh.m1.selected is not None and not fresh.stop_event.is_set()
    finally:
        await close(m, e)


@pytest.mark.parametrize('live', [False, True])
@pytest.mark.parametrize('outcome', ['ACK', 'UNKNOWN', 'REJECTED'])
async def test_post_crossing_cutoff_keeps_durable_risk_and_direction(tmp_path, monkeypatch, live, outcome):
    m, e, f, c = await setup(tmp_path, live=live)
    entered, finish = threading.Event(), threading.Event()
    calls, recycled = [], []

    def native(*args, **kwargs):
        attempt = e.gateway._client._operation_value('batch_attempts')[0]
        calls.append(attempt.expected_order_id)
        if outcome == 'UNKNOWN' and len(calls) == 1:
            return {'unexpected': 'response'}
        entered.set()
        assert finish.wait(5), 'test worker was not released'
        if outcome == 'ACK':
            return [{'success': True, 'orderID': attempt.expected_order_id}]
        if outcome == 'REJECTED':
            return [{'success': False, 'errorMsg': 'post only'}]
        return {'unexpected': 'response'}

    e.gateway._client._native_order_post = native
    monkeypatch.setattr(m4_execution, 'recycle_idle_memory', lambda: recycled.append(1) or {})
    try:
        ep = next(iter(m.pending.values()))
        if outcome == 'UNKNOWN':
            await m.submit(ep)
        m.post_task = asyncio.create_task(m.submit(ep))
        assert await asyncio.to_thread(entered.wait, 2)
        e.official_time += 220
        await m.advance()
        assert m.feed_closed and not m.post_task.done() and not recycled
        assert f.books['1'].bids and f.m1.opportunities  # Worker still owns inputs.
        finish.set()
        await m.post_task
        await m.advance()
        await m.drain()
        await e.journal.flush()
        assert recycled == [1]
        risk = D('9.94') if outcome == 'UNKNOWN' else D('4.97') if outcome == 'ACK' else D(0)
        assert m.ledger.committed() == risk
        assert m.ledger.row(ep.episode_id)['state'] == outcome
        assert not m.pending and not m.retry_after
        assert len(calls) == (2 if outcome == 'UNKNOWN' else 1)
        log = rows(e)
        assert next(i for i, r in enumerate(log) if r['kind'] == 'sm_m4_memory_recycled') > max(
            i for i, r in enumerate(log) if r['kind'] == 'sm_m4_live_post')
        m = await reopen(m, e)
        assert not m.pending and m.ledger.committed() == risk
        m.ledger.observe(op(999), minimum=5, elapsed=20)
        assert not m.ledger.reserve('df999', spendable=100)
        assert m.ledger.row('df999')['reason'] == 'M4_DIRECTION_OPPORTUNITY_LIMIT'
        assert len(m.ledger.uncertain_posts()) == (1 if outcome == 'UNKNOWN' else 0)
    finally:
        finish.set()
        await close(m, e)


async def test_late_tick_worker_and_cash_stop_stay_safe_after_cutoff(tmp_path, monkeypatch):
    m, e, f, c = await setup(tmp_path, live=True)
    entered, finish = asyncio.Event(), asyncio.Event()
    recycled = []

    async def late_tick():
        entered.set()
        await finish.wait()
        f.verify_ticks({'1': D('.01'), '2': D('.01')})

    monkeypatch.setattr(m4_execution, 'recycle_idle_memory', lambda: recycled.append(1) or {})
    try:
        m.tick_task = asyncio.create_task(late_tick())
        await entered.wait()
        e.official_time += 220
        await m.advance()
        assert m.feed_closed and not recycled
        finish.set()
        await m.drain()
        await m.advance()
        await m.drain()
        assert recycled == [1]

        async def low_cash():
            return {'official_balance_usd': '5', 'applicable_allowance_usd': '40', 'source': 'MOCK'}

        e.account_snapshot = low_cash
        c.now += m.settings.account_poll_sec
        await m.advance()
        await m.drain()
        assert e.control.balance_path.exists() and m.stop.is_set() and e.inhibited()
        assert json.loads(e.control.balance_path.read_text())['requires_confirmation'] is True
        assert await m.run() is False and m.state == 'RETAINED'
        assert m.ledger.metric()['native_posts'] == 0
    finally:
        finish.set()
        await close(m, e)


async def test_signing_crossing_cutoff_aborts_without_consuming_direction(tmp_path, monkeypatch):
    m, e, f, c = await setup(tmp_path)
    entered, finish = asyncio.Event(), asyncio.Event()
    prepare = e.gateway.prepare

    async def delayed(*args, **kwargs):
        result = await prepare(*args, **kwargs)
        entered.set()
        await finish.wait()
        return result

    monkeypatch.setattr(e.gateway, 'prepare', delayed)
    monkeypatch.setattr(m4_execution, 'recycle_idle_memory', lambda: {})
    try:
        ep = next(iter(m.pending.values()))
        m.post_task = asyncio.create_task(m.submit(ep))
        await entered.wait()
        e.official_time += 220
        await m.advance()
        assert m.feed_closed and not m.memory_recycled
        finish.set()
        await m.post_task
        await m.advance()
        await m.drain()
        assert m.ledger.row(ep.episode_id)['state'] == 'ABORTED'
        assert m.ledger.committed() == 0 and m.ledger.metric()['native_posts'] == 0
        m = await reopen(m, e)
        m.ledger.observe(op(999), minimum=5, elapsed=20)
        assert m.ledger.reserve('df999', spendable=100)  # No POST, no used slot.
    finally:
        finish.set()
        await close(m, e)


async def test_failed_durability_barrier_never_discards_signal_evidence(tmp_path, monkeypatch):
    m, e, f, c = await setup(tmp_path)
    original = e.journal.flush

    async def failed():
        raise RuntimeError('synthetic disk failure')

    try:
        e.official_time += 220
        monkeypatch.setattr(e.journal, 'flush', failed)
        with pytest.raises(RuntimeError, match='disk failure'):
            await m.advance()
        assert m.feed_closed and not m.memory_recycled
        assert f.m1.events and f.m1.opportunities and f.books['1'].bids
    finally:
        monkeypatch.setattr(e.journal, 'flush', original)
        await close(m, e)


async def test_gc_failure_is_reported_once_and_market_still_completes(tmp_path, monkeypatch):
    m, e, f, c = await setup(tmp_path)
    calls = []

    def failed():
        calls.append(1)
        raise OSError('synthetic allocator failure')

    monkeypatch.setattr(m4_execution, 'recycle_idle_memory', failed)
    try:
        e.official_time += 220
        await m.advance()
        await m.drain()
        assert m.memory_recycle['status'] == 'FAILED'
        assert m.memory_recycle['error_type'] == 'OSError'
        e.official_time += 60
        assert await m.run() is True
        assert m.ledger.get('complete') and calls == [1]
        assert m.ledger.metric()['native_posts'] == 0
    finally:
        await close(m, e)


def test_reused_m234_helper_allows_unsupported_allocator(monkeypatch):
    def unsupported(*args):
        raise OSError('no libc trim')
    monkeypatch.setattr(m2_memory.ctypes, 'CDLL', unsupported)
    result = m2_memory.recycle_idle_memory()
    assert result['allocator_trimmed'] is None
    assert result['collected_objects'] >= 0
    assert set(result['after']) == {'rss_bytes', 'swap_bytes'}
