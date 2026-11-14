"""Unavailable public results must remain pending and retry without inventing payouts."""

import asyncio
import time
from copy import deepcopy
from types import SimpleNamespace

import pytest

from m1234_rr1_fixtures import rr1_events
from polymaker.m1234_sim.engine import Engine
from polymaker.m1234_sim.runner import LiveRun
from polymaker.m1234_sim.store import read_audit


@pytest.mark.parametrize("active_market", [False, True])
async def test_expired_clock_reacquires_only_between_markets(active_market):
    stale = time.monotonic() - 61
    refreshed_at = time.monotonic()

    async def clock():
        return 1000, 0.1, refreshed_at

    run = SimpleNamespace(
        report_tasks={}, clock_task=None, clock=(939, 0.1, stale),
        api=SimpleNamespace(clock=clock), log=lambda *_: None,
        resolution_task=None, pending=[], resolution_due=float("inf"),
    )
    if active_market:
        with pytest.raises(RuntimeError, match="SIM_OFFICIAL_CLOCK_EXPIRED"):
            await LiveRun.refresh(run, engine=object())
        await run.clock_task
        assert run.clock[2] == stale
    else:
        await LiveRun.refresh(run)
        assert run.clock == (1000, 0.1, refreshed_at)
        assert run.clock_task is None


@pytest.mark.parametrize('raw', [None, [], 'temporarily unavailable', 0])
async def test_non_object_result_remains_pending_then_settles(tmp_path, raw):
    tape = rr1_events('normal')
    engine = Engine(tmp_path)
    try:
        for event in tape:
            if event['kind'] == 'SETTLEMENT':
                break
            await engine.ingest(event)
        pending = deepcopy(event)
        pending['raw'] = raw
        before = engine.snapshot()['brokers']
        await engine.ingest(pending)
        assert engine.resolution is None
        assert engine.validity['status'] == 'VALID'
        assert engine.snapshot()['brokers'] == before
        result = [r for r in read_audit(tmp_path/'simulation.sqlite3')
                  if r['kind'] == 'OFFICIAL_RESULT'][-1]['payload']
        assert result == {'result': None, 'raw': raw,
                          'read_error': 'PUBLIC_RESOLUTION_RESPONSE_NOT_OBJECT'}
        resolved = deepcopy(event)
        resolved['seq'] = engine.seq + 1
        await engine.ingest(resolved)
        assert engine.resolution['winner'] == 'YES'
        assert all(account.winner == 'YES' for broker in engine.brokers.values()
                   for account in broker.accounts.values())
    finally:
        await engine.close()


async def test_foreign_result_still_fails_identity_validation(tmp_path):
    engine = Engine(tmp_path)
    try:
        for event in rr1_events('normal'):
            event = deepcopy(event)
            if event['kind'] == 'SETTLEMENT':
                event['raw']['slug'] = 'foreign-market'
                with pytest.raises(ValueError, match='SETTLEMENT_MARKET_IDENTITY_MISMATCH'):
                    await engine.ingest(event)
                assert engine.resolution is None
                break
            await engine.ingest(event)
    finally:
        await engine.close()


async def test_coordinator_retries_null_without_losing_pending_market(tmp_path):
    engine = Engine(tmp_path)
    try:
        for event in rr1_events('normal'):
            if event['kind'] == 'SETTLEMENT':
                break
            await engine.ingest(event)
        settlement = deepcopy(event)

        async def record(old, kind, **payload):
            await old.ingest({**settlement, 'seq': old.seq + 1, 'kind': kind, **payload})

        run = SimpleNamespace(report_tasks={}, clock_task=None,
            clock=[0, time.monotonic(), time.monotonic()], pending=[engine],
            resolution_due=float('inf'), record=record, update_report=lambda: None)
        for raw in (None, settlement['raw']):
            future = asyncio.get_running_loop().create_future()
            future.set_result([(engine, raw, None)])
            run.resolution_task = future
            await LiveRun.refresh(run)
            if raw is None:
                assert run.pending == [engine] and engine.resolution is None
            else:
                assert run.pending == [] and engine.resolution['winner'] == 'YES'
        assert engine.validity['status'] == 'VALID'
    finally:
        await engine.close()
