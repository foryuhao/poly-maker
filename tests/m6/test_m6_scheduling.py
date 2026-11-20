import asyncio
import os
from threading import Event, Thread

import pytest
from eth_account import Account
from helpers import candidate, close, frame, setup

from polymaker.domain import Quote, Side
from polymaker.m6_baseline import capture


@pytest.mark.parametrize('extra_pending', [False, True])
async def test_worker_cannot_see_applied_head_but_still_sees_real_backlog(
    tmp_path, monkeypatch, extra_pending
):
    m, e, f, c = await setup(tmp_path)
    finished, check, attempted = Event(), Event(), Event()
    result = {}

    def worker():
        assert check.wait(2)
        attempted.set()
        result['allowed'] = m.guard(op)
        result['reason'] = m.guard_reason
        result['pending'] = list(f.m1_pending_received)
        finished.set()

    try:
        op = candidate(f)
        await m.collect()
        raw, wall = frame(f), f.wall()
        f.m1_pending_received.append(0.)
        if extra_pending:
            # Identical receipt times are distinct messages; retire only one.
            f.m1_pending_received.append(0.)
        c.now = .04
        original = f.apply

        def interleaved(*args, **kwargs):
            original(*args, **kwargs)
            check.set()  # Exact old race: apply returned, receipt still queued.
            assert attempted.wait(2)
            assert not finished.is_set()

        monkeypatch.setattr(f, 'apply', interleaved)
        thread = Thread(target=worker)
        thread.start()
        f.consume_received(raw, 0., wall)
        assert await asyncio.to_thread(finished.wait, 2)
        thread.join(2)
        assert result['allowed'] is not extra_pending
        assert result['pending'] == ([0.] if extra_pending else [])
        if extra_pending:
            assert result['reason'] == 'M6_SUBMIT_INPUT_BACKLOG'
        assert f.m1_processing_received is None
    finally:
        check.set()
        await close(m, e)


async def test_apply_failure_closes_admission_before_retiring_receipt(tmp_path, monkeypatch):
    m, e, f, c = await setup(tmp_path)
    try:
        op = candidate(f)
        await m.collect()
        f.m1_pending_received.append(c.now)

        def broken(*args, **kwargs):
            raise ValueError('synthetic parse failure')

        monkeypatch.setattr(f, 'apply', broken)
        with pytest.raises(ValueError, match='synthetic parse failure'):
            f.consume_received({}, c.now, f.wall())
        assert not f.connected and not f.m1_pending_received and not m.guard(op)
    finally:
        await close(m, e)


@pytest.mark.parametrize('expired', [None, 'signal', 'T240'])
async def test_unsent_backlog_retry_waits_for_progress_not_100ms(tmp_path, expired):
    m, e, f, c = await setup(tmp_path)
    try:
        await e.gateway.signer.warmup()
        op = candidate(f)
        await m.collect()
        m.pending.pop(op.episode_id)
        raw, wall = frame(f), f.wall()
        f.m1_pending_received.append(0.)
        c.now = .04
        await m.submit(op)
        assert not m.ledger.facts('virtual_post')
        assert m.retry_after[op.episode_id] == .04
        await m.advance_entries(c.now)
        assert m.post_task is None  # No hot retry without queue progress.
        f.consume_received(raw, 0., wall)
        c.now = .05 if expired is None else .8 if expired == 'signal' else 220.
        await m.advance_entries(c.now)
        if m.post_task is not None:
            await m.post_task
        assert len(m.ledger.facts('virtual_post')) == (1 if expired is None else 0)
        if expired is None:
            assert m.ledger.row(op.episode_id)['state'] == 'ACK'
            assert m.ledger.row(op.episode_id)['posts'] == 1
    finally:
        await close(m, e)


async def test_slow_durable_admission_does_not_lock_feed_and_rechecks_signal(tmp_path, monkeypatch):
    m, e, f, c = await setup(tmp_path)
    entered, release = Event(), Event()
    job = None
    try:
        op = candidate(f)
        original = m.ledger.observe

        def observe(*args, **kwargs):
            with m.ledger.lock:
                entered.set()
                assert release.wait(2)
                return original(*args, **kwargs)

        monkeypatch.setattr(m.ledger, 'observe', observe)
        job = asyncio.create_task(m.collect())
        assert await asyncio.to_thread(entered.wait, 2)
        # Both operations must complete while the commit worker is blocked.
        f.apply(frame(f))
        f.m1.invalidate('SYNTHETIC_SIGNAL_ENDED_DURING_COMMIT')
        release.set()
        await job
        assert not m.pending and not m.ledger.facts('virtual_post')
        row = m.ledger.row(op.episode_id)
        assert row['state'] == 'SKIPPED'
        assert row['reason'] == 'M6_SIGNAL_ENDED_DURING_PERSISTENCE'
    finally:
        release.set()
        if job is not None:
            await job
        await close(m, e)


async def test_spawn_signer_uses_same_ephemeral_identity_and_unique_sdk_signatures(tmp_path):
    m, e, f, c = await setup(tmp_path)
    try:
        signer = e.gateway.signer
        await signer.warmup()
        assert signer.worker_pid != os.getpid()
        q = [Quote('1', Side.BUY, .1, 50.)]
        first = await e.gateway.prepare(q, e.meta, {'1': .01, '2': .01})
        second = await e.gateway.prepare(q, e.meta, {'1': .01, '2': .01})
        assert first.order_ids != second.order_ids
        for order in (first, second):
            recovered = Account._recover_hash(order.order_ids[0], signature=order.orders[0].order.signature)
            assert recovered == e.gateway._client.get_address()
            assert e.gateway._prepared_body(order.orders)  # Parent serializer accepts returned SDK objects.
        assert not m.ledger.facts('virtual_post')
    finally:
        await close(m, e)


async def test_final_guard_backlog_retries_without_resending_prepared_identity(tmp_path, monkeypatch):
    m, e, f, c = await setup(tmp_path)
    try:
        await e.gateway.signer.warmup()
        op = candidate(f)
        await m.collect()
        m.pending.pop(op.episode_id)
        send = e.gateway.send_prepared
        raw, wall = frame(f), f.wall()
        calls = 0

        async def delayed(prepared, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                f.m1_pending_received.append(0.)
                c.now = .04
            return await send(prepared, **kwargs)

        monkeypatch.setattr(e.gateway, 'send_prepared', delayed)
        await m.submit(op)
        assert not m.ledger.facts('virtual_post')
        assert m.ledger.row(op.episode_id)['state'] == 'RESERVED'
        assert m.retry_input_revision[op.episode_id] == 0
        f.consume_received(raw, 0., wall)
        c.now = .05
        await m.advance_entries(c.now)
        await m.post_task
        assert calls == 2
        assert len(m.ledger.facts('signed_attempt')) == 2
        assert len(m.ledger.facts('virtual_post')) == 1
        assert m.ledger.row(op.episode_id)['state'] == 'ACK'
        assert m.ledger.committed() == 5
        assert not m.retry_input_revision
    finally:
        await close(m, e)


@pytest.mark.parametrize('boundary', ['remaining_budget', 'T240'])
async def test_delayed_process_signature_rechecks_submission_boundaries(tmp_path, monkeypatch, boundary):
    m, e, f, c = await setup(tmp_path)
    try:
        await e.gateway.signer.warmup()
        op = candidate(f)
        await m.collect()
        prepare = e.gateway.prepare

        async def delayed(*args, **kwargs):
            result = await prepare(*args, **kwargs)
            c.now = .351 if boundary == 'remaining_budget' else 220.
            return result

        monkeypatch.setattr(e.gateway, 'prepare', delayed)
        await m.submit(op)
        assert not m.ledger.facts('virtual_post')
        assert m.ledger.row(op.episode_id)['state'] == 'ABORTED'
        assert not m.pending or all(r['posts'] == 0 for r in m.ledger.rows())
    finally:
        await close(m, e)


async def test_failed_persistence_diagnostic_does_not_downgrade_durable_ack(tmp_path, monkeypatch):
    m, e, f, c = await setup(tmp_path)
    try:
        op = candidate(f)
        await m.collect()
        record = e.record

        def failed_diagnostic(kind, payload, **kwargs):
            if kind == 'sm_m6_persistence' and payload['operation'] == 'outcome':
                raise OSError('synthetic diagnostic failure after committed ACK')
            return record(kind, payload, **kwargs)

        monkeypatch.setattr(e, 'record', failed_diagnostic)
        await m.submit(op)
        row = m.ledger.row(op.episode_id)
        assert row['state'] == 'ACK' and row['posts'] == 1
        assert e.fatal == 'M6_PERSISTENCE_DIAGNOSTIC_FAILED'
        assert row['uncertain_retries'] == 0
    finally:
        await close(m, e)


@pytest.mark.parametrize('subscription_failure', [False, True])
async def test_socket_consumer_drains_each_receipt_and_disconnect_cleans_state(
    tmp_path, monkeypatch, subscription_failure
):
    m, e, f, c = await setup(tmp_path)
    drained = asyncio.Event()
    inputs = [frame(f, token=str(i % 2 + 1)) for i in range(20)]
    received = 0

    class Socket:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def send(self, data):
            if subscription_failure:
                raise OSError('synthetic subscription failure')

        async def __aiter__(self):
            for raw in inputs:
                yield raw
            await asyncio.Event().wait()

    consume = f.consume_received

    def observed(*args):
        nonlocal received
        consume(*args)
        received += 1
        if received == len(inputs):
            drained.set()

    monkeypatch.setattr(capture.websockets, 'connect', lambda *args, **kwargs: Socket())
    monkeypatch.setattr(f, 'consume_received', observed)
    try:
        if subscription_failure:
            with pytest.raises(OSError, match='subscription failure'):
                await f._connection()
        else:
            f.task = asyncio.create_task(f._connection())
            await asyncio.wait_for(drained.wait(), 2)
            assert f.connected and f.input_revision == len(inputs)
            assert not f.m1_pending_received and f.m1_processing_received is None
            await f.close()
        assert not f.connected and f.ws is None
        assert not f.m1_pending_received and f.m1_processing_received is None
    finally:
        await f.close()
        await close(m, e)
