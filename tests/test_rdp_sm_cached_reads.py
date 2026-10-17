"""Real controllers/tracker + periodic cache; all venue I/O is MockTransport."""
import asyncio
import time
from collections import Counter
from dataclasses import replace
from decimal import Decimal

import httpx
import pytest

from polymaker.rdp_sm_cached_reads import CachedExecutionReads
from polymaker.rdp_sm_entry import AccountRiskPolicy
from polymaker.rdp_sm_rebalance import RebalanceController
from polymaker.rdp_sm_sync_companion import SyncCompanion
from polymaker.rdp_sm_sync_reads import ObserverReader
from test_rdp_sm_m1 import ADDRESS, CID
from test_rdp_sm_obi_execution import cleanup, setup_obi
from test_rdp_sm_obi_execution import env as env


async def attach(setup, tmp_path, *, hook=None):
    v, e = setup[:2]
    cfg = e.cfg
    cfg.rdp_sm.sync_observe.enabled = True
    cfg.rdp_sm.sync_observe.use_for_execution = True
    cfg.rdp_sm.sync_observe.order_interval_s = .025
    cfg.rdp_sm.sync_observe.account_interval_s = .04
    cfg.rdp_sm.sync_observe.output_dir = str(tmp_path / 'cache')
    calls = []

    def response(request):
        calls.append((request.method, request.url.path, time.monotonic()))
        if hook:
            result = hook(request)
            if result is not None:
                return result
        return v.response(request)

    companion = SyncCompanion(cfg, e.session_id, reader_factory=lambda emit, stop:
        ObserverReader(cfg, emit=emit, stop=stop, transport=httpx.MockTransport(response)))
    companion.bind_market(e.meta, int(e.slug.rsplit('-', 1)[1]))
    companion.start()
    policy = AccountRiskPolicy(use_starting_balance=True, sync_observer=companion)
    policy.market_id = CID
    e.reads.account_policy = e.gateway._client.account_policy = policy
    e.shared_reads = CachedExecutionReads(e, companion)
    e.shared_reads.timeout_s = .6
    setup[3].account = (await e._snapshot(include_books=False))[3]
    return companion, calls


async def finish(setup, companion):
    await asyncio.to_thread(companion.close)
    await cleanup(setup)


def counts(calls):
    return Counter(path for _, path, _ in calls)


async def test_normal_uses_cache_for_account_and_close_no_direct_state_gets(env, monkeypatch, tmp_path):
    setup = await setup_obi(env, monkeypatch)
    v, e, _, lifecycle, normal = setup[:5]
    companion, calls = await attach(setup, tmp_path)
    try:
        old_get = e.gateway._client._get
        direct = []
        def guard(endpoint, **kwargs):
            direct.append(endpoint)
            return old_get(endpoint, **kwargs)
        monkeypatch.setattr(e.gateway._client, '_get', guard)
        result, _ = await normal.enter(Decimal(0), Decimal(0), 'cached-normal')
        assert result.close_barrier == 'SAFE_TO_CONTINUE', result.block_reason
        assert len(v.post_requests) == 1
        assert all('/tick-size' in url or '/version' in url for url in direct)
        assert all(method == 'GET' for method, _, _ in calls)
        assert calls and e.shared_reads.last_version > 0
        assert v.cancels and len(v.cancels) < 20
        assert e.safe_to_continue and not e.shared_reads.inflight
        lifecycle.account = (await e._snapshot(include_books=False))[3]
    finally:
        await finish(setup, companion)


async def test_multiple_consumers_same_frame_add_no_http_and_no_extra_heartbeats(env, monkeypatch, tmp_path):
    setup = await setup_obi(env, monkeypatch)
    companion, calls = await attach(setup, tmp_path)
    e = setup[1]
    try:
        async with e.shared_reads.scope('many-consumers', account=True):
            await asyncio.to_thread(companion.service.stop)  # Drain after pinning a valid frame.
            before = len(calls)
            for _ in range(30):
                await e.gateway.positions()
                await e.gateway.open_orders()
                await e.gateway.get_trades(market=CID)
                await e.gateway.balance_allowance()
                await e.reads.server_time()
            assert len(calls) == before
    finally:
        await finish(setup, companion)


async def test_short_order_never_in_open_list_still_closes_via_periodic_proof(env, monkeypatch, tmp_path):
    setup = await setup_obi(env, monkeypatch, horizon=.03)
    def hook(request):
        if request.url.path == '/data/orders':
            return httpx.Response(200, json={'data': [], 'next_cursor': 'LTE='})
    companion, calls = await attach(setup, tmp_path, hook=hook)
    try:
        result, _ = await setup[4].enter(Decimal(0), Decimal(0), 'short-cached')
        assert result.close_barrier == 'SAFE_TO_CONTINUE', result.block_reason
        oid = result.known_order_ids[0]
        assert counts(calls)['/data/order/' + oid] > 0
        assert not any(r.data for r in companion.service.latest().last_good if r.name == 'open_orders')
    finally:
        await finish(setup, companion)


async def test_rebalance_uses_same_cache_and_reuses_old_terminal_details(env, monkeypatch, tmp_path):
    setup = await setup_obi(env, monkeypatch)
    v, e, _, lifecycle, normal = setup[:5]
    companion, calls = await attach(setup, tmp_path)
    try:
        first, _ = await normal.enter(Decimal(0), Decimal(0), 'first-cached')
        assert first.close_barrier == 'SAFE_TO_CONTINUE'
        old = first.known_order_ids[0]
        before = counts(calls)['/data/order/' + old]
        lifecycle.account = (await e._snapshot(include_books=False))[3]
        rebalance = RebalanceController(e, lifecycle)
        second, _ = await rebalance.repair(Decimal(5), Decimal(0), 'repair-cached')
        assert second.close_barrier == 'SAFE_TO_CONTINUE', second.block_reason
        assert second.requested_quotes[0].outcome == 'NO'
        assert counts(calls)['/data/order/' + old] == before
        assert len(v.post_requests) == 2
    finally:
        await finish(setup, companion)


async def test_mined_to_confirmed_is_applied_from_complete_cache_to_owned_inventory(env, monkeypatch, tmp_path):
    setup = await setup_obi(env, monkeypatch)
    v, e = setup[:2]
    filled = []
    observed_mined = []
    def on_cancel(venue):
        if not filled:
            oid = next(iter(venue.rows))
            t = venue.fill(oid, qty=5, status='MINED')
            t['trader_side'] = 'MAKER'
            t['maker_orders'][0]['maker_address'] = ADDRESS
            filled.append(t)
            venue.positions = [{'token_id': '1', 'current_size': '5', 'avg_price': '.5'}]
    v.on_cancel = on_cancel
    def hook(request):
        if request.url.path == '/data/trades' and filled:
            observed_mined.append(True)
            if len(observed_mined) >= 3:
                filled[0]['status'] = 'CONFIRMED'
    companion, _ = await attach(setup, tmp_path, hook=hook)
    try:
        result, _ = await setup[4].enter(Decimal(0), Decimal(0), 'filled-cached')
        assert result.close_barrier == 'SAFE_TO_CONTINUE', result.block_reason
        assert result.yes_inventory == 5 and result.no_inventory == 0
        assert len(e.confirmed()) == 1
        assert len(observed_mined) >= 3
    finally:
        await finish(setup, companion)


async def test_wrong_session_or_stopped_cache_cannot_fall_back_to_http(env, monkeypatch, tmp_path):
    setup = await setup_obi(env, monkeypatch)
    companion, _ = await attach(setup, tmp_path)
    e = setup[1]
    try:
        await asyncio.to_thread(companion.close)
        before = len(setup[0].calls)
        assert not await e.verify_close(cancel=True)
        assert e.reason == 'SYNC_CACHE_UNAVAILABLE'
        assert len(setup[0].calls) == before
        with pytest.raises(RuntimeError, match='SYNC_DIRECT_READ_PROHIBITED'):
            e.gateway._client.get_open_orders()
        companion.service._snapshot = replace(companion.service.latest(), session_id='other', status='RUNNING')
        e.shared_reads.timeout_s = .03
        with pytest.raises(ValueError, match='SYNC_CACHE_NOT_READY'):
            await e._snapshot(include_books=False)
    finally:
        await cleanup(setup)


async def test_operation_boundary_and_source_failure_do_not_bless_old_good_data(env, monkeypatch, tmp_path):
    setup = await setup_obi(env, monkeypatch)
    companion, _ = await attach(setup, tmp_path)
    e = setup[1]
    try:
        snap = await e.shared_reads.acquire(account=True)
        assert e.shared_reads._ready(snap, True, True)
        token = e.shared_reads.begin_write('POST', ['not-accepted'])
        assert not e.shared_reads._ready(snap, True, True)
        e.shared_reads.end_write(token)
        assert not e.shared_reads._ready(snap, True, True)
        good = next(r for r in snap.last_good if r.name == 'trades')
        failed = replace(good, complete=False, data_json=None, partial_json='[]', read_id='failed')
        broken = replace(snap, attempts=tuple(failed if r.name == 'trades' else r for r in snap.attempts))
        e.shared_reads.last_write = 0
        assert not e.shared_reads._ready(broken, False, True)
        assert e.gateway._client.pagination_failure is not None
    finally:
        await finish(setup, companion)


async def test_source_quantity_conflict_waits_for_next_heartbeat_without_quarantining(env, monkeypatch, tmp_path):
    setup = await setup_obi(env, monkeypatch)
    v, e = setup[:2]
    delayed = []
    def hook(request):
        if request.url.path == '/data/trades' and delayed and delayed[0] > 0:
            delayed[0] -= 1
            return httpx.Response(200, json={'data': [], 'next_cursor': 'LTE='})
    def on_cancel(venue):
        if not delayed:
            t = venue.fill(next(iter(venue.rows)), qty=5)
            t['trader_side'] = 'MAKER'
            t['maker_orders'][0]['maker_address'] = ADDRESS
            delayed.append(2)
    v.on_cancel = on_cancel
    companion, _ = await attach(setup, tmp_path, hook=hook)
    try:
        result, _ = await setup[4].enter(Decimal(0), Decimal(0), 'conflict-cached')
        assert result.close_barrier == 'SAFE_TO_CONTINUE', result.block_reason
        assert result.yes_inventory == 5
        assert not e.processor.quarantined_orders
    finally:
        await finish(setup, companion)


def test_execution_cache_requires_service_enabled():
    from polymaker.config import RDPSyncObserveConfig
    with pytest.raises(ValueError, match='requires'):
        RDPSyncObserveConfig(use_for_execution=True)
    assert RDPSyncObserveConfig(enabled=True, use_for_execution=True).use_for_execution


async def test_lost_post_ack_uses_official_cache_closure_without_resubmitting(env, monkeypatch, tmp_path):
    setup = await setup_obi(env, monkeypatch)
    companion, _ = await attach(setup, tmp_path)
    v, e = setup[:2]
    setup[5].fail_ack = True
    try:
        result, _ = await setup[4].enter(Decimal(0), Decimal(0), 'lost-ack-cached')
        assert result.close_barrier == 'SAFE_TO_CONTINUE', result.block_reason
        assert result.placement_results[0]['outcome'] == 'VERIFIED'
        assert len(v.post_requests) == 1 and e.committed() == 0
    finally:
        await finish(setup, companion)


async def test_final_admission_detects_new_account_facts_without_query(env, monkeypatch, tmp_path):
    setup = await setup_obi(env, monkeypatch)
    companion, calls = await attach(setup, tmp_path)
    e = setup[1]
    try:
        assert e.shared_reads.admission_current()
        snap = companion.service.latest()
        good = next(r for r in snap.last_good if r.name == 'capital')
        changed = replace(good, data_json='{"balance":"1","allowances":{}}')
        modified = replace(snap,
            last_good=tuple(changed if r.name == 'capital' else r for r in snap.last_good),
            attempts=tuple(changed if r.name == 'capital' else r for r in snap.attempts))
        monkeypatch.setattr(companion.service, 'latest', lambda: modified)
        before = len(calls)
        assert not e.shared_reads.admission_current()
        assert len(calls) == before
    finally:
        await finish(setup, companion)


@pytest.mark.parametrize('fault', [None, 'poll_timeout'])
async def test_merge_uses_shared_close_and_preserves_unknown_risk(env, monkeypatch, tmp_path, fault):
    from polymaker.rdp_sm_merge import merge_confirmed_pairs
    from rdp_sm_merge_sdk import install

    setup = await setup_obi(env, monkeypatch, horizon=.05)
    v, e, _, lifecycle, normal = setup[:5]
    install(monkeypatch, v, e.cfg)
    e.cfg.rdp_sm.obi.rebalance_order_ttl_sec = .05
    filled = set()
    def on_cancel(venue):
        for attempt in e.attempts:
            oid = attempt.order_id
            if oid and oid in venue.rows and oid not in filled:
                t = venue.fill(oid, tid='fill-' + oid, qty=5)
                t['trader_side'] = 'MAKER'
                t['maker_orders'][0]['maker_address'] = ADDRESS
                filled.add(oid)
        totals = Counter()
        for trade in venue.trades:
            for leg in trade['maker_orders']:
                totals[leg['asset_id']] += float(leg['matched_amount'])
        venue.positions = [{'token_id': token, 'current_size': size, 'avg_price': '.5'}
                           for token, size in totals.items()]
    v.on_cancel = on_cancel
    companion, _ = await attach(setup, tmp_path)
    try:
        first, _ = await normal.enter(Decimal(0), Decimal(0), 'pair-yes-cached')
        assert first.close_barrier == 'SAFE_TO_CONTINUE'
        lifecycle.account = (await e._snapshot(include_books=False))[3]
        second, _ = await RebalanceController(e, lifecycle).repair(
            Decimal(5), Decimal(0), 'pair-no-cached')
        assert second.close_barrier == 'SAFE_TO_CONTINUE', second.block_reason
        assert second.yes_inventory == second.no_inventory == 5
        v.merge_fault = fault
        if fault:
            with pytest.raises(RuntimeError, match='MERGE_UNKNOWN'):
                await merge_confirmed_pairs(e, elapsed_s=240)
            assert e.fatal == 'MERGE_UNKNOWN' and e.merged_pairs == 0
            assert not e.safe_to_continue and e.committed() > 0
        else:
            assert await merge_confirmed_pairs(e, elapsed_s=240)
            assert e.merged_pairs == 5 and e.safe_to_continue and e.committed() == 0
            account = (await e._snapshot(include_books=False))[3]
            assert account['yes_inventory'] == account['no_inventory'] == 0
        assert len(v.merge_submissions) == 1
        assert not e.shared_reads.inflight
    finally:
        await finish(setup, companion)


async def test_cancel_waiter_drains_real_work_before_operation_boundary(env, monkeypatch, tmp_path):
    setup = await setup_obi(env, monkeypatch)
    companion, _ = await attach(setup, tmp_path)
    cache = setup[1].shared_reads
    started, release = asyncio.Event(), asyncio.Event()
    async def delayed(_ids):
        started.set()
        await release.wait()
    cache.original_cancel = delayed
    task = asyncio.create_task(cache.cancel(['pending-id']))
    try:
        await started.wait()
        task.cancel()
        await asyncio.sleep(.01)
        assert cache.inflight and not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not cache.inflight and cache.order_writes['pending-id'] > 0
    finally:
        release.set()
        await finish(setup, companion)


async def test_early_cancel_not_found_cannot_close_before_late_post_returns(env, monkeypatch, tmp_path):
    setup = await setup_obi(env, monkeypatch, horizon=.03)
    setup[5].delay = lambda: time.sleep(.08)
    companion, _ = await attach(setup, tmp_path)
    try:
        result, _ = await setup[4].enter(Decimal(0), Decimal(0), 'late-accept-cached')
        assert result.close_barrier == 'SAFE_TO_CONTINUE', result.block_reason
        oid = result.known_order_ids[0]
        post_return = setup[1].shared_reads.post_returns[oid]
        assert any(method == 'DELETE' and stamp < post_return
                   for method, _path, stamp in setup[5].timeline)
        assert result.close_monotonic >= post_return
        assert setup[0].rows[oid]['status'] == 'CANCELED'
    finally:
        await finish(setup, companion)


async def test_strict_already_canceled_reply_uses_collections_without_inventing_order(env, monkeypatch, tmp_path):
    from test_rdp_sm_canceled_submission import inject_reply

    setup = await setup_obi(env, monkeypatch, horizon=.05)
    _state, client = inject_reply(setup, monkeypatch)
    companion, calls = await attach(setup, tmp_path)
    e = setup[1]
    try:
        result, _ = await setup[4].enter(Decimal(0), Decimal(0), 'rejected-cached')
        assert result.close_barrier == 'SAFE_TO_CONTINUE', result.block_reason
        assert result.placement_results[0]['outcome'] == 'REJECTED'
        oid = result.placement_results[0]['expected_order_id']
        assert oid in e.canceled_submission_proofs and oid not in e.known
        assert e.committed() == 0
        assert not counts(calls)['/data/order/' + oid]
        await e._snapshot(include_books=False)
        assert e.safe_to_continue
    finally:
        await finish(setup, companion)
        client.close()


async def test_terminal_with_wrong_maker_cannot_stop_cancellation(env, monkeypatch, tmp_path):
    import json

    setup = await setup_obi(env, monkeypatch)
    companion, _ = await attach(setup, tmp_path)
    e = setup[1]
    try:
        result, _ = await setup[4].enter(Decimal(0), Decimal(0), 'maker-check-cached')
        assert result.close_barrier == 'SAFE_TO_CONTINUE'
        oid = result.known_order_ids[0]
        snap = companion.service.latest()
        read = next(r for r in snap.last_good if r.name == 'order:' + oid)
        row = read.data
        row['maker_address'] = '0x' + '00' * 20
        bad = replace(read, data_json=json.dumps(row))
        snap = replace(snap,
            last_good=tuple(bad if r.name == bad.name else r for r in snap.last_good),
            attempts=tuple(bad if r.name == bad.name else r for r in snap.attempts))
        monkeypatch.setattr(companion.service, 'latest', lambda: snap)
        assert not e.shared_reads.terminal_observed([oid])
    finally:
        await finish(setup, companion)
