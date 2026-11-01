"""22:25: transient close/account proof failure must not discard CA expiry ownership."""
import asyncio
from decimal import Decimal as D

import pytest

from polymaker.rdp_sm import SessionStatus, _Issue
from polymaker.rdp_sm_recovery import PreparationReadRetry
from test_rdp_sm_ca_entry import confirm_ca
from test_rdp_sm_ca_entry import env as env
from test_rdp_sm_ca_entry_price_filter import set_ask
from test_rdp_sm_ca_taker import setup_taker, taker_fill
from test_rdp_sm_cached_reads import attach, finish
from test_rdp_sm_m1 import CID
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_obi_execution import cleanup


@pytest.mark.parametrize('cached', [False, True])
@pytest.mark.parametrize('phase', ['close_before_exit', 'close_after_exit', 'account', 'constraints', 'close_cutoff'])
async def test_transient_read_recovery_keeps_original_owner_deadline_and_exposure(
    env, monkeypatch, tmp_path, cached, phase
):
    setup = await setup_taker(env, monkeypatch, tp=True)
    v, e, feed, lifecycle, normal = setup[:5]
    v.ticks['2'] = '.01'
    feed.apply({'event_type': 'tick_size_change', 'market': CID, 'asset_id': '2',
                'old_tick_size': '.001', 'new_tick_size': '.01', 'timestamp': str(feed.wall()*1000)})
    feed.verify_ticks(v.ticks)
    e.cfg.rdp_sm.ba.signal_lifetime_sec = 3
    e.cfg.rdp_sm.ba.max_entry_age_ms = 1000
    e.cfg.rdp_sm.ca_matched_tp.taker_exit_on_expiry = True
    e.cfg.rdp_sm.ca_matched_tp.profit_per_share = D('.05')
    e.cfg.rdp_sm.ca_matched_tp.ttl_sec = .08
    e.cfg.rdp_sm.repair.ba_enabled = False
    e.cfg.rdp_sm.obi.network_pause_sec = .02
    e.cfg.rdp_sm.obi.network_max_pause_sec = .05
    set_ask(feed, '1', '.36')
    set_ask(feed, '2', '.62')
    companion = (await attach(setup, tmp_path))[0] if cached else None
    send_native, verify_native = e.gateway.send_prepared, e._verify_close
    account_native, constraint_native = e._snapshot, lifecycle._read_preparation_constraint
    blocked, resume = asyncio.Event(), asyncio.Event()
    state = {'tp': None, 'exit': None, 'injected': False}

    def tp_canceled():
        return state['tp'] and v.rows[state['tp']]['status'] == 'CANCELED'

    async def send(signed, **kwargs):
        first = not v.post_requests
        if not first and not signed.post_only:
            assert tp_canceled() and state['tp'] in e.proofs
        raw = await send_native(signed, **kwargs)
        oid = signed.order_ids[0]
        if first:
            taker_fill(setup, oid, quantity='5.142858', price='.35')
            v.rows[oid]['status'] = 'MATCHED'
        elif signed.post_only:
            state['tp'] = oid
        else:
            state['exit'] = oid
            taker_fill(setup, oid)
        return raw

    async def verify(*, cancel=False):
        target = tp_canceled() and (phase in {'close_before_exit', 'close_cutoff'}
                                   or phase == 'close_after_exit' and state['exit'])
        if target and not resume.is_set():
            if not state['injected']:
                state['injected'] = True
                e.invalidate('SYNC_CACHE_NOT_READY')
                # Real transport circuit: pending CA must release only a
                # fully proved drained batch before a recovery probe can run.
                e.reads.network.observe('GET', .001, success=False, outcome='TRANSPORT_ERROR',
                                        operation_id='transient-orders-read')
            e.reason = 'SYNC_CACHE_NOT_READY'
            blocked.set()
            return False
        return await verify_native(cancel=cancel)

    async def account(*args, **kwargs):
        if phase == 'account' and tp_canceled() and not state['injected']:
            state['injected'] = True
            blocked.set()
            await resume.wait()
            raise _Issue(SessionStatus.BLOCKED, 'SYNC_CACHE_NOT_READY')
        return await account_native(*args, **kwargs)

    async def constraints(function, *args):
        if phase == 'constraints' and tp_canceled() and not state['injected']:
            state['injected'] = True
            blocked.set()
            await resume.wait()
            raise PreparationReadRetry('ReadTimeout')
        return await constraint_native(function, *args)

    monkeypatch.setattr(e.gateway, 'send_prepared', send)
    monkeypatch.setattr(e, '_verify_close', verify)
    monkeypatch.setattr(e, '_snapshot', account)
    monkeypatch.setattr(lifecycle, '_read_preparation_constraint', constraints)
    task = None
    try:
        await confirm_ca(feed)
        task = asyncio.create_task(normal.enter(D(0), D(0), 'session:w1'))
        await asyncio.wait_for(blocked.wait(), 4)
        await asyncio.sleep(.08)
        assert not task.done() and lifecycle.busy
        assert len(v.post_requests) == (3 if phase == 'close_after_exit' else 2)
        assert e.fatal is None
        for _ in range(10):
            e.reads.network.observe('GET', .001)
        if phase == 'close_cutoff':
            e.cfg.rdp_sm.repair.cutoff_sec = (e.official_time+e.monotonic()-e.time_anchor
                                            - int(e.slug.rsplit('-', 1)[1])-.01)
        else:
            resume.set()
        result, sizing = await asyncio.wait_for(task, 8)
        if phase == 'close_cutoff':
            assert result.close_barrier == 'BLOCKED'
            assert e.fatal == 'CA_EXPIRY_EXIT_FAILED:CA_EXPIRY_EXIT_OUTSIDE_WINDOW'
            assert len(v.post_requests) == 2 and e.economic_inventory() == (D('5.142858'), D(0))
            assert sizing['ca_matched_tp']['expiry_exit_reason'] == 'CA_EXPIRY_EXIT_OUTSIDE_WINDOW'
            return
        assert result.close_barrier == 'SAFE_TO_CONTINUE', result.block_reason
        assert len(v.post_requests) == 3 and e.fatal is None
        assert e.economic_inventory() == (D('5.142858'), D('5.1451'))
        assert sizing['ca_matched_tp']['expiry_exit_reason'] == 'HEDGED_WITH_EXCESS'
        await e.journal.flush()
        events = journal_records(e.journal.path)
        lease = next(r['data']['payload'] for r in events if r['kind'] == 'sm_ca_matched_tp_lease_started')
        signal = next(r['data']['payload'] for r in events if r['kind'] == 'sm_ca_expiry_exit_signal')
        assert signal['deadline_monotonic'] == lease['expires_at_monotonic']
        assert sum(r['kind'] == 'sm_ca_expiry_exit_command' for r in events) == 1
    finally:
        resume.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await finish(setup, companion) if companion else await cleanup(setup)
