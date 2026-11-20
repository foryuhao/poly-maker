import asyncio

import pytest
from helpers import candidate, close, frame, refresh, setup


@pytest.mark.parametrize(('clock_at', 'reason'), [
    (.8, 'M6_OPPORTUNITY_EXPIRED'),
    (.351, 'M6_POST_LATENCY_BUDGET'),
    (220., 'M6_ENTRY_WINDOW_ENDED'),
])
async def test_expiry_during_reservation_stops_before_signing(tmp_path, monkeypatch, clock_at, reason):
    m, e, f, c = await setup(tmp_path)
    try:
        op = candidate(f)
        await m.collect()
        m.pending.pop(op.episode_id)
        persist = m.persist

        async def delayed(method, *args, **kwargs):
            result = await persist(method, *args, **kwargs)
            if method.__name__ == 'reserve':
                # Isolate the time boundary: neither stale public books nor
                # the modeled account snapshot should mask this check.
                steps = (.1, .2, .3, clock_at) if clock_at == .351 else (clock_at,)
                for at in steps:
                    c.now = at
                    refresh(f)
                await m.refresh_account()
            return result

        async def forbidden(*args, **kwargs):
            pytest.fail('Expired/under-budget signal must not start signing')

        monkeypatch.setattr(m, 'persist', delayed)
        monkeypatch.setattr(e.gateway, 'prepare', forbidden)
        await m.submit(op)
        assert m.ledger.row(op.episode_id)['reason'] == reason
        assert not m.ledger.facts('virtual_post')
        assert not m.ledger.facts('signed_attempt')
        assert not m.pending
    finally:
        await close(m, e)


async def test_expired_signature_result_does_not_start_another_commit(tmp_path, monkeypatch):
    m, e, f, c = await setup(tmp_path)
    try:
        await e.gateway.signer.warmup()
        op = candidate(f)
        await m.collect()
        m.pending.pop(op.episode_id)
        prepare = e.gateway.prepare

        async def delayed(*args, **kwargs):
            result = await prepare(*args, **kwargs)
            f.m1.invalidate('SYNTHETIC_SIGNAL_ENDED_DURING_SIGNING')
            return result

        monkeypatch.setattr(e.gateway, 'prepare', delayed)
        await m.submit(op)
        assert m.ledger.row(op.episode_id)['reason'] == 'M6_EVIDENCE_ENDED'
        assert not m.ledger.facts('signed_attempt')
        assert not m.ledger.facts('virtual_post')
        assert not m.pending
    finally:
        await close(m, e)


async def test_native_failure_records_actual_reason_instead_of_generic_ended(tmp_path, monkeypatch):
    m, e, f, c = await setup(tmp_path)
    e.display_events = []
    try:
        await e.gateway.signer.warmup()
        op = candidate(f)
        await m.collect()
        m.pending.pop(op.episode_id)
        send = e.gateway.send_prepared

        async def ended(*args, **kwargs):
            f.m1.invalidate('SYNTHETIC_SIGNAL_ENDED_BEFORE_NATIVE_GUARD')
            return await send(*args, **kwargs)

        monkeypatch.setattr(e.gateway, 'send_prepared', ended)
        await m.submit(op)
        assert m.ledger.row(op.episode_id)['reason'] == 'M6_EVIDENCE_ENDED'
        assert not m.ledger.facts('virtual_post')
        checks = [p for k, p in e.display_events if k == 'sm_m6_submit_check'
                  and p['stage'] == 'NATIVE_POST']
        assert len(checks) == 1 and not checks[0]['allowed']
        assert checks[0]['reason'] == 'M6_EVIDENCE_ENDED'
        assert checks[0]['opportunity_id'] == op.episode_id
        assert checks[0]['required_remaining_ms'] == 400
    finally:
        await close(m, e)


async def test_unchanged_quote_uses_durable_original_reservation(tmp_path, monkeypatch):
    m, e, f, c = await setup(tmp_path)
    e.display_events = []
    try:
        await e.gateway.signer.warmup()
        op = candidate(f)
        await m.collect()
        m.pending.pop(op.episode_id)

        def redundant(*args, **kwargs):
            pytest.fail('Original price/size already owns its full reservation')

        monkeypatch.setattr(m.ledger, 'reserve_quote', redundant)
        await m.submit(op)
        assert len(m.ledger.facts('virtual_post')) == 1
        assert m.ledger.row(op.episode_id)['state'] == 'ACK'
        assert m.ledger.committed() == 5
        records = [p for k, p in e.display_events if k == 'sm_m6_persistence']
        assert all(p['opportunity_id'] == op.episode_id for p in records)
        assert all(p['worker_queue_ms'] >= 0 and p['worker_execution_ms'] >= 0
                   and p['result_delivery_ms'] >= 0 for p in records)
        timing = [p for k, p in e.display_events if k == 'sm_m6_prepare_timing'][0]
        worker = timing['signer']
        assert worker['worker_pid'] == e.gateway.signer.worker_pid
        assert worker['cpu_ms'] > 0 and worker['worker_wall_ms'] > 0
        assert worker['queue_ms'] >= 0 and worker['result_delivery_ms'] >= 0
    finally:
        await close(m, e)


async def test_input_during_advance_cannot_be_cleared_before_wait(tmp_path, monkeypatch):
    m, e, f, c = await setup(tmp_path)
    try:
        previous = f.sequence
        f.apply(frame(f))  # Input arrived while advance() yielded for persistence.
        original = f.wait_after
        called = []

        async def wait(sequence, timeout):
            called.append(sequence)
            assert sequence != f.sequence
            await original(sequence, timeout)

        monkeypatch.setattr(f, 'wait_after', wait)
        await m.wait_for_progress(previous)
        assert called == [previous]
    finally:
        await close(m, e)


@pytest.mark.parametrize('wake', ['input', 'sdk'])
async def test_progress_wakeup_never_cancels_sdk_task(tmp_path, monkeypatch, wake):
    m, e, f, c = await setup(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    waiter = None
    try:
        async def input_wait(sequence, timeout):
            f.changed.clear()
            entered.set()
            await f.changed.wait()

        async def sdk():
            await release.wait()

        monkeypatch.setattr(f, 'wait_after', input_wait)
        m.post_task = asyncio.create_task(sdk())
        waiter = asyncio.create_task(m.wait_for_progress(f.sequence))
        await asyncio.wait_for(entered.wait(), 1)
        assert not waiter.done()
        if wake == 'input':
            f.changed.set()
        else:
            release.set()
        await asyncio.wait_for(waiter, 1)
        assert not m.post_task.cancelled()
        if wake == 'input':
            assert not m.post_task.done()
    finally:
        release.set()
        if waiter is not None:
            await waiter
        await close(m, e)
