"""Transient terminal regressions pause the same Session without releasing risk."""
import asyncio
import time
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from polymaker.rdp_sm_loop import RunResult, SMTradingLoop
from polymaker.userstream.parse import normalize_order
from test_rdp_sm_cached_reads import attach, finish
from test_rdp_sm_m2_execution import journal_records, open_executor, quotes
from test_rdp_sm_obi_execution import cleanup, setup_obi
from test_rdp_sm_obi_execution import env as env


async def test_regression_preserves_fills_and_missing_trade_proof_cannot_clear_it(env):
    v, e = await open_executor(env)
    v.on_cancel = lambda venue: venue.fill('o-1', 'confirmed-opening', 5)
    window = await e.execute_window('first', quotes('first'))
    assert window.close_barrier == 'SAFE_TO_CONTINUE'
    v.on_cancel = None
    e.processor.on_order(normalize_order({**v.rows['o-1'], 'status': 'LIVE', 'size_matched': '0'}),
                         e.meta.condition_id)
    trades = v.trades
    v.trades = []
    assert not await e.verify_close()
    assert e.fatal == 'TERMINAL_LIVE_CONTRADICTION' and not e.safe_to_continue
    assert e.committed() == Decimal('2.45')
    v.trades = trades
    assert await e.verify_close()
    assert e.fatal is None and len(e.confirmed()) == 1
    assert sum(f.size for f in e.confirmed()) == 5
    assert await e.verify_close()
    assert len(e.confirmed()) == 1 and e.committed() == Decimal('2.45')


async def test_terminal_recovery_cannot_clear_an_unrelated_fatal(env):
    v, e = await open_executor(env)
    assert (await e.execute_window('first', quotes('first'))).close_barrier == 'SAFE_TO_CONTINUE'
    e.terminal_conflict('o-1', source='ORDER_EVENT')
    e.fatal = 'JOURNAL_WRITE_FAILED'
    assert not await e.verify_close()
    assert e.fatal == 'JOURNAL_WRITE_FAILED' and e.terminal_conflicts


async def closed_order(env, monkeypatch, tmp_path, cached):
    setup = await setup_obi(env, monkeypatch)
    companion = None
    if cached:
        companion, _ = await attach(setup, tmp_path)
    window, _ = await setup[4].enter(Decimal(0), Decimal(0), 'before-regression')
    assert window.close_barrier == 'SAFE_TO_CONTINUE', window.block_reason
    return setup, companion, window.known_order_ids[0]


@pytest.mark.parametrize('cached', [False, True])
@pytest.mark.parametrize('recover', [False, True])
async def test_terminal_conflict_stays_in_same_session_until_proof_or_cutoff(
    env, monkeypatch, tmp_path, cached, recover,
):
    setup, companion, oid = await closed_order(env, monkeypatch, tmp_path, cached)
    v, e = setup[:2]
    live = {**v.rows[oid], 'status': 'LIVE'}
    v.open_rows_override = [live]
    e.processor.on_order(normalize_order(live), e.meta.condition_id)
    assert e.fatal == 'TERMINAL_LIVE_CONTRADICTION'
    assert not e.safe_to_continue and e.committed() > 0

    async def bounded_sleep(delay):
        await asyncio.sleep(min(.02, delay))

    recovery = []
    observer = SimpleNamespace(emit=lambda kind, payload:
        recovery.append(payload.copy()) if kind == 'recovery' else None)
    loop = SMTradingLoop(e.cfg, e.slug, e.session_id, monotonic=e.monotonic,
                         sleep=bounded_sleep, observer=observer)
    loop.executor = e
    loop.market_deadline = e.monotonic() + (.9 if recover else .3)
    result = RunResult(e.meta.condition_id, e.session_id)
    task = asyncio.create_task(loop._run_obi_session(result))
    try:
        await asyncio.sleep(.06)
        assert not task.done()  # The old coordinator exited immediately here.
        assert len(v.post_requests) == 1 and e.committed() > 0
        if recover:
            v.open_rows_override = []
            async with asyncio.timeout(2):
                while not e.safe_to_continue:  # noqa: ASYNC110 - observe the real coordinator's proof
                    await asyncio.sleep(.01)
            assert e.fatal is None and not e.terminal_conflicts
            assert not task.done() and e.committed() == 0
        await asyncio.wait_for(task, 3)
        assert result.stop_elapsed_s >= 300
        assert result.execution_status == ('COMPLETED' if recover else 'INCOMPLETE')
        assert result.final_close_barrier == ('SAFE_TO_CONTINUE' if recover else 'BLOCKED')
        assert len(v.post_requests) == 1
        assert any(event['status'] == 'PAUSED' for event in recovery)
        assert any(event['status'] == 'RESUMED' for event in recovery) is recover
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if companion:
            await finish(setup, companion)
        else:
            await cleanup(setup)


async def test_cached_conflict_cannot_reuse_pre_conflict_terminal_detail(env, monkeypatch, tmp_path):
    setup, companion, oid = await closed_order(env, monkeypatch, tmp_path, True)
    e = setup[1]
    try:
        await asyncio.to_thread(companion.service.stop)
        snap = companion.service.latest()
        e.terminal_conflict(oid, source='ORDER_EVENT')
        boundary = e.terminal_conflicts[oid]
        assert not e.shared_reads._ready(snap, True, True)
        assert e.shared_reads.order_writes[oid] >= boundary
        detail = next(r for r in snap.last_good if r.name == 'order:' + oid)
        assert detail.started_monotonic < boundary
        # Even newly fetched complete collections cannot bless an old detail.
        now = time.monotonic()
        updated = tuple(replace(r, started_monotonic=now, completed_monotonic=now)
                        if r.name in {'time', 'open_orders', 'trades', 'capital', 'positions'} else r
                        for r in snap.last_good)
        fresh_collections = replace(snap, status='RUNNING', last_good=updated, attempts=updated)
        assert oid in e.shared_reads._detail_requirements(fresh_collections)
        assert not e.shared_reads._ready(fresh_collections, True, True)
        assert not e.safe_to_continue and e.committed() > 0
    finally:
        await finish(setup, companion)


@pytest.mark.parametrize('fatal', ['MERGE_UNKNOWN', 'UNKNOWN_PLACEMENT_EXPOSURE'])
async def test_uncertain_mutation_waits_to_cutoff_without_new_orders(env, monkeypatch, fatal):
    setup = await setup_obi(env, monkeypatch)
    v, e = setup[:2]
    window, _ = await setup[4].enter(Decimal(0), Decimal(0), 'before-unknown')
    assert window.close_barrier == 'SAFE_TO_CONTINUE'
    e.fatal = fatal
    e.merge_pending = fatal == 'MERGE_UNKNOWN'
    loop = SMTradingLoop(e.cfg, e.slug, e.session_id, monotonic=e.monotonic)
    loop.executor = e
    loop.market_deadline = e.monotonic() + .2
    result = RunResult(e.meta.condition_id, e.session_id)
    try:
        await loop._run_obi_session(result)
        assert result.stop_elapsed_s >= 300 and result.execution_status == 'INCOMPLETE'
        assert len(v.post_requests) == 1 and e.fatal == fatal
        await e.journal.flush()
        records = journal_records(e.journal.path)
        assert not [r for r in records if r['kind'] == 'sm_merge_submitted']
    finally:
        await cleanup(setup)
