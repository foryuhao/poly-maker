"""Actual cache/SDK/signing plus fault injection; no network or live runner."""
import asyncio
import json
import time
from dataclasses import replace
from decimal import Decimal

import httpx
import pytest
from pydantic import ValidationError

from polymaker.config import RDPInventoryLagConfig, RDPRepairPathConfig
from polymaker.domain import Side
from polymaker.rdp_sm_execution import InventoryMismatch, Placement, WindowQuote
from polymaker.rdp_sm_path_repair import PathRepairController
from polymaker.rdp_sm_sl import SLExecution
from test_rdp_sm_cached_reads import attach, finish
from test_rdp_sm_m1 import ADDRESS, CID
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_obi_execution import setup_obi
from test_rdp_sm_repair import env as env
from test_rdp_sm_repair import open_yes, reflect_positions


async def setup_lag(env, monkeypatch, tmp_path, *, size=5, side='YES'):
    setup = await setup_obi(env, monkeypatch)
    v, e = setup[:2]
    e.cfg.rdp_sm.repair.policy = "hd_tp_sl_v1"
    e.cfg.rdp_sm.repair.path = RDPRepairPathConfig(
        tp_profit_rate=".001", shock_window_sec=2, shock_z=1.6, drawdown_per_share=".02")
    e.cfg.rdp_sm.repair.inventory_lag = RDPInventoryLagConfig(
        enabled=True, exclusive_market_writer=True)
    e.cfg.rdp_sm.obi.network_probe_interval_sec = .02
    state = {"lag": True}

    def hook(request):
        if request.url.path == '/data/trades':
            for t in v.trades:
                t['trader_side'] = 'MAKER'
                for leg in t['maker_orders']:
                    leg['maker_address'] = ADDRESS
        if request.url.path == '/v2/positions' and state['lag']:
            return httpx.Response(200, json={'data': [], 'pagination': {'next_cursor': None}}, request=request)

    companion, calls = await attach(setup, tmp_path, hook=hook)
    try:
        if side=='YES':
            first, _ = await open_yes(setup, size=size)
        else:
            setup[7].update(direction='NO', bid='.68', yes_ask='.70', no_bid='.30', no_ask='.32')
            await asyncio.sleep(.025)

            def fill_no(venue):
                for oid, row in venue.rows.items():
                    if row['asset_id']=='2' and not row['associate_trades']:
                        venue.fill(oid, 'opening-no', size)
                reflect_positions(venue)

            v.on_cancel = fill_no
            first, _ = await setup[4].enter(*e.economic_inventory(), 'opening-no')
            v.on_cancel = None
        assert first.close_barrier == 'SAFE_TO_CONTINUE', first.block_reason
        assert e.inventory_lag.anchor['inventory'] == (Decimal(0), Decimal(0))
        end = time.monotonic()+1
        while True:
            try:
                setup[3].account = (await e._snapshot(
                    include_books=False, allow_repair_inventory_lag=True))[3]
                break
            except InventoryMismatch:
                if time.monotonic() >= end:
                    raise
                await asyncio.sleep(.01)
        assert e.inventory_mismatch is not None and e.inventory_lag.grant is not None
        return setup, companion, state, calls
    except BaseException:
        await finish(setup, companion)
        raise


@pytest.fixture
async def lagged(env, monkeypatch, tmp_path):
    setup, companion, state, calls = await setup_lag(env, monkeypatch, tmp_path)
    try:
        yield setup, companion, state, calls
    finally:
        await finish(setup, companion)


def actor_goal(setup, monkeypatch, mode='SL'):
    e = setup[1]
    controller = PathRepairController(e, setup[3])
    monkeypatch.setattr(controller, 'sigma', lambda: .02)
    actor = SLExecution(controller, 'lag-sl')
    snapshot = setup[3].snapshot(controller, *e.economic_inventory())
    return actor, ('NO', snapshot.no.best_ask-snapshot.no.tick, Decimal(5), snapshot)


def test_configuration_is_explicit_and_bounded():
    assert not RDPInventoryLagConfig().enabled
    for kw in ({'enabled': True}, {'grace_sec': 0}, {'grace_sec': 5.01},
               {'grace_sec': float('nan')}, {'max_unreported_shares': 0}, {'enabled': 'true'}):
        with pytest.raises(ValidationError):
            RDPInventoryLagConfig(**kw)


async def test_real_snapshot_retains_discrepancy_and_never_unlocks_normal(lagged):
    setup, _, _, _ = lagged
    v, e, _, lifecycle, normal = setup[:5]
    assert e.economic_inventory() == (Decimal(5), Decimal(0))
    assert e.inventory_mismatch['inventory']['YES']['actual'] == 0
    assert e.inventory_lag.grant['budget'] == Decimal(str(lifecycle.account['basic_spendable_budget_usd']))
    before = len(v.post_requests)
    assert await normal.enter(*e.economic_inventory(), 'forbidden-normal') is None
    assert len(v.post_requests) == before
    # Ordinary callers, including merge/close/normal, never get the exemption.
    with pytest.raises(InventoryMismatch):
        await e._snapshot(include_books=False)


async def test_confirmed_fill_refresh_is_once_and_duplicate_status_does_not_reset(lagged):
    setup, _, _, _ = lagged
    e = setup[1]
    revision, boundary, since = e.economic_revision, e.last_economic_change_monotonic, e.inventory_lag.since
    fill = e.confirmed()[0]
    for _ in range(20):
        e.processor.on_trade(fill, CID)
    assert (e.economic_revision, e.last_economic_change_monotonic, e.inventory_lag.since) == (revision, boundary, since)
    await e.journal.flush()
    events = [r for r in journal_records(e.journal.path) if r['kind']=='sm_account_refresh']
    assert len(events) == 1


@pytest.mark.parametrize('fault', ['expired', 'late_fill', 'cancel_race', 'unknown', 'null',
    'stale', 'partial', 'capital_change', 'extra_position', 'extra_order', 'stopped',
    'abandoned', 'cooldown', 'cutoff', 'journal_failure', 'mutation', 'write_boundary'])
async def test_evidence_or_safety_change_revokes_lease_before_post(lagged, monkeypatch, fault):
    setup, companion, _, _ = lagged
    e, manager = setup[1], setup[1].inventory_lag
    actor, goal = actor_goal(setup, monkeypatch)
    assert manager.admission_reason(actor, goal) is None
    snap = companion.service.latest()
    if fault == 'expired':
        manager.since -= 3
    elif fault == 'late_fill':
        f = e.confirmed()[0]
        e.processor.on_trade(replace(f, trade_id='late', size=1), CID)
    elif fault == 'cancel_race':
        e.processor.on_order(replace(next(iter(e.terminal.values())), status='LIVE'), CID)
    elif fault == 'unknown':
        e.attempts.append(Placement('unresolved', WindowQuote(CID, '2', 'NO', Side.BUY, .5, 5, 'unknown'),
                                    outcome='UNKNOWN', post_started=True))
    elif fault == 'mutation':
        e.shared_reads.inflight[object()] = ('DELETE', tuple(e.known))
    elif fault == 'write_boundary':
        e.shared_reads.last_write = time.monotonic()
    elif fault in {'abandoned', 'cooldown', 'cutoff', 'journal_failure'}:
        if fault == 'abandoned':
            e.repair_abandonment = {'reason': 'test'}
        elif fault == 'cooldown':
            monkeypatch.setattr(e.cooldown, 'deadline', e.monotonic()+20)
        elif fault == 'cutoff':
            e.official_time += 240
        else:
            monkeypatch.setattr(e.journal, 'failure', 'disk-failure')
    else:
        attempts = {r.name:r for r in snap.attempts}
        good = {r.name:r for r in snap.last_good}
        name = 'positions'
        if fault == 'null':
            attempts[name] = replace(attempts[name], complete=False, error_code='SYNC_POSITIONS_NOT_FOUND')
        elif fault == 'stale':
            attempts[name] = good[name] = replace(good[name], started_monotonic=time.monotonic()-10)
        elif fault == 'partial':
            attempts['trades'] = replace(attempts['trades'], complete=False)
        elif fault in {'capital_change', 'extra_position', 'extra_order'}:
            name = {'capital_change':'capital','extra_position':'positions','extra_order':'open_orders'}[fault]
            data = good[name].data
            if name == 'capital':
                data['balance'] = str(int(data['balance'])+1)
            elif name == 'positions':
                data.append({'token_id':'2','size':1,'avg_price':.5})
            else:
                data.append({'order_id':'external'})
            attempts[name] = good[name] = replace(good[name], data_json=json.dumps(data))
        altered = replace(snap, attempts=tuple(attempts.values()), last_good=tuple(good.values()),
                          status='STOPPED' if fault=='stopped' else snap.status)
        monkeypatch.setattr(companion.service, 'latest', lambda: altered)
    assert manager.admission_reason(actor, goal) is not None
    assert manager.maintenance_reason() is not None
    assert len(setup[0].post_requests) == 1
    if fault == 'journal_failure':
        e.journal.failure = None


async def test_lease_is_one_order_no_refresh_extension_and_target_is_restricted(lagged, monkeypatch):
    setup, _, _, _ = lagged
    e, manager = setup[1], setup[1].inventory_lag
    actor, goal = actor_goal(setup, monkeypatch)
    original = manager.since
    for _ in range(3):
        await e._snapshot(include_books=False, allow_repair_inventory_lag=True)
        assert manager.since == original
    assert manager.admission_reason(actor, ('YES', goal[1], goal[2], goal[3]))
    assert manager.admission_reason(actor, ('NO', goal[1], Decimal(6), goal[3]))
    q = WindowQuote(CID, '2', 'NO', Side.BUY, float(goal[1]), 5, 'one')
    first = Placement('first', q)
    e.attempts.append(first)
    manager.reserve(first)
    assert manager.admission_reason(actor, goal, first) is None
    assert manager.admission_reason(actor, goal) == 'INVENTORY_LAG_LEASE_CONSUMED'
    with pytest.raises(ValueError, match='LEASE_CONSUMED'):
        manager.reserve(Placement('second', q))
    first.outcome = 'ABORTED'


async def test_positions_convergence_requires_full_snapshot_before_clearing_mismatch(lagged):
    setup, _, state, _ = lagged
    e = setup[1]
    state['lag'] = False
    end = time.monotonic()+1
    while e.inventory_mismatch is not None:
        try:
            await e._snapshot(include_books=False)
        except InventoryMismatch:
            assert time.monotonic()<end
            await asyncio.sleep(.01)
    assert e.inventory_lag.grant is None and e.inventory_lag.since is None
    assert e.inventory_lag.anchor['inventory'] == (Decimal(5), Decimal(0))


async def test_actor_can_send_exactly_one_real_maker_repair_while_positions_lag(lagged, monkeypatch):
    setup, _, _, _ = lagged
    v, e = setup[:2]
    actor, goal = actor_goal(setup, monkeypatch)
    # Leave original policy/goal mechanics to their existing tests; exercise the
    # actual native admission, signing, durable WAL, SDK guard, POST and DELETE.
    monkeypatch.setattr(actor, 'goal', lambda: (goal, None))
    monkeypatch.setattr(actor, 'consistent', lambda _: True)
    e.inventory_lag.since = e.monotonic()-1.8
    created = await asyncio.wait_for(actor.run(), 2)
    assert len(created)==1 and len(v.post_requests)==2
    assert created[0].post_started and created[0].outcome=='ACK'
    assert e.inventory_mismatch is not None
    assert all(row['status']=='CANCELED' for row in v.rows.values())
    assert not e.cooldown.active


@pytest.mark.parametrize('mode', ['TP', 'SL'])
async def test_original_path_controller_uses_restricted_actor_and_cancels_at_expiry(lagged, monkeypatch, mode):
    setup, _, _, _ = lagged
    v, e = setup[:2]
    setup[7].update(bid='.50', yes_ask='.52', no_bid='.48', no_ask='.50')
    await asyncio.sleep(.025)
    controller = PathRepairController(e, setup[3])
    monkeypatch.setattr(controller, 'sigma', lambda:.02)
    controller._policy()
    if mode=='SL':
        controller.policy.force_sl(controller.elapsed())
    e.inventory_lag.since = e.monotonic()-1.7
    outcome = await asyncio.wait_for(controller.repair(*e.economic_inventory(), 'original-path'), 2)
    assert outcome is not None
    result, _ = outcome
    assert len(v.post_requests)==2 and len(result.placement_results)==1
    assert result.placement_results[0]['outcome']=='ACK'
    assert e.inventory_mismatch is not None and e.repair_episode is not None
    assert not e.cooldown.active and not e.merge_pending
    assert all(row['status']=='CANCELED' for row in v.rows.values())
    assert e.inventory_lag.claim is not None


async def test_late_partial_fill_during_signing_aborts_native_post_and_keeps_risk(env, monkeypatch, tmp_path):
    setup, companion, _, _ = await setup_lag(env, monkeypatch, tmp_path, size=3)
    v, e = setup[:2]
    try:
        actor, goal = actor_goal(setup, monkeypatch)
        monkeypatch.setattr(actor, 'goal', lambda:(goal, None))
        native_prepare = e.gateway.prepare

        async def late_prepare(*args, **kwargs):
            signed = await native_prepare(*args, **kwargs)
            old = e.confirmed()[0]
            v.fill(old.maker_order_id, 'late-cancel-race', 2)
            reflect_positions(v)
            from polymaker.domain import TradeState
            e.processor.on_trade(replace(old, trade_id='late-cancel-race', size=2,
                                         status=TradeState.CONFIRMED), CID, source='REST')
            return signed

        monkeypatch.setattr(e.gateway, 'prepare', late_prepare)
        created = await asyncio.wait_for(actor.run(), 2)
        assert len(created)==1 and created[0].outcome=='ABORTED'
        assert not created[0].post_started and len(v.post_requests)==1
        assert e.economic_inventory()==(Decimal(5), Decimal(0))
        assert e.inventory_mismatch is not None
        assert e.inventory_lag.maintenance_reason()
    finally:
        await finish(setup, companion)


async def test_no_opening_has_symmetric_permission_for_yes_repair(env, monkeypatch, tmp_path):
    setup, companion, _, _ = await setup_lag(env, monkeypatch, tmp_path, side='NO')
    try:
        v, e = setup[:2]
        actor, goal = actor_goal(setup, monkeypatch)
        snap = goal[3]
        goal = ('YES', snap.yes.best_ask-snap.yes.tick, Decimal(5), snap)
        assert e.economic_inventory()==(Decimal(0), Decimal(5))
        assert e.inventory_lag.admission_reason(actor, goal) is None
        monkeypatch.setattr(actor, 'goal', lambda:(goal, None))
        e.inventory_lag.since = e.monotonic()-1.8
        created = await asyncio.wait_for(actor.run(), 2)
        assert len(created)==1 and created[0].outcome=='ACK'
        assert created[0].quote.outcome=='YES' and len(v.post_requests)==2
    finally:
        await finish(setup, companion)


async def test_null_claimed_order_retains_full_envelope_and_never_grants_a_second_order(lagged, monkeypatch):
    from polymaker.rdp_sm_sl_ledger import exposure

    setup, companion, _, _ = lagged
    e, manager = setup[1], setup[1].inventory_lag
    actor, goal = actor_goal(setup, monkeypatch)
    q = WindowQuote(CID, '2', 'NO', Side.BUY, float(goal[1]), 5, 'null-claim')
    a = Placement('null-claim', q, expected_order_id='null-id')
    e.attempts.append(a)
    manager.reserve(a)
    a.post_started, a.order_id, a.outcome = True, 'null-id', 'ACK'
    e.known.add(a.order_id)
    snap = companion.service.latest()
    from polymaker.rdp_sm_sync_reads import ReadEvidence

    now = time.monotonic()
    detail = ReadEvidence('order:null-id', 'order', 0, 0, now, now, False, None, '[]', 1,
                          read_id='null-detail', error_code='SYNC_ORDER_NOT_FOUND')
    monkeypatch.setattr(companion.service, 'latest', lambda:replace(snap, attempts=(*snap.attempts,detail)))
    reserved = Decimal(5)*goal[1]
    assert exposure(e, retain_unknown_reservations=True).reserved==reserved
    assert a.order_id not in e.proofs and a.order_id not in e.terminal
    assert manager.admission_reason(actor, goal)=='INVENTORY_LAG_LEASE_CONSUMED'
    a.outcome = 'UNKNOWN'
    assert manager.maintenance_reason()
    assert exposure(e, retain_unknown_reservations=True).reserved==reserved


async def test_native_worst_exposure_check_still_blocks_minimum_notional_overfill(lagged, monkeypatch):
    setup, _, _, _ = lagged
    e = setup[1]
    setup[7].update(bid='.80', yes_ask='.82', no_bid='.18', no_ask='.20')
    await asyncio.sleep(.025)
    actor, goal = actor_goal(setup, monkeypatch)
    e.cfg.rdp_sm.repair.inventory_lag.max_unreported_shares = Decimal(20)
    goal = ('NO', goal[1], Decimal(11), goal[3])
    assert e.inventory_lag.admission_reason(actor, goal) is None
    assert actor.admission_reason(goal)=='WAIT_DELTA_CAPACITY'
    assert len(setup[0].post_requests)==1


@pytest.mark.parametrize('fault', ['missing_anchor', 'partial_visibility', 'overreport',
    'no_increment', 'cashflow', 'funds', 'position_reconciled', 'old_position_read', 'external_trade'])
async def test_lease_cannot_be_created_without_exact_confirmed_proof(lagged, monkeypatch, fault):
    setup, companion, _, _ = lagged
    e, manager = setup[1], setup[1].inventory_lag
    account = dict(setup[3].account)
    manager.grant = None
    if fault=='missing_anchor':
        manager.anchor = None
    elif fault=='partial_visibility':
        account['yes_inventory'] = 1
    elif fault=='overreport':
        account['yes_inventory'] = 6
    elif fault=='no_increment':
        from polymaker.rdp_sm_inventory_lag import fill_signature
        manager.anchor['fills'] = fill_signature(e.confirmed())
    elif fault=='cashflow':
        account['official_balance_usd'] += .01
    elif fault=='funds':
        account['basic_spendable_budget_usd'] = 0
    elif fault=='position_reconciled':
        e.proofs[next(iter(e.known))]['outcome'] = 'POSITION_RECONCILED'
    snap = companion.service.latest()
    if fault in {'old_position_read', 'external_trade'}:
        good = {r.name:r for r in snap.last_good}
        latest = {r.name:r for r in snap.attempts}
        if fault=='old_position_read':
            latest['positions'] = good['positions'] = replace(good['positions'],
                started_monotonic=e.last_economic_change_monotonic-.001)
        else:
            data = good['trades'].data
            data[0]['maker_legs'][0]['order_id'] = 'external-buy'
            latest['trades'] = good['trades'] = replace(good['trades'], data_json=json.dumps(data))
        snap = replace(snap, attempts=tuple(latest.values()), last_good=tuple(good.values()))
    monkeypatch.setattr(companion.service, 'latest', lambda:snap)
    e.shared_reads.active = snap
    try:
        assert not manager.consider(account)
    finally:
        e.shared_reads.active = None
    assert manager.grant is None


async def test_revoked_evidence_cannot_revive_by_reverting_a_replica(lagged, monkeypatch):
    setup, companion, _, _ = lagged
    manager = setup[1].inventory_lag
    snap = companion.service.latest()
    bad = replace(snap, status='STOPPED')
    with monkeypatch.context() as patch:
        patch.setattr(companion.service, 'latest', lambda:bad)
        assert manager.maintenance_reason()
    assert manager.maintenance_reason()
    assert manager.revoked_reason


@pytest.mark.parametrize('name', ['trades', 'open_orders', 'order:known'])
async def test_proof_progress_wakes_inventory_wait_without_book_polling(name):
    from polymaker.rdp_sm_sync_reads import ReadEvidence
    from test_repair_e_latency import cache_fixture

    cache = cache_fixture()
    cache.e.known = {'known'}
    observer = cache.service
    now = time.monotonic()
    read = ReadEvidence(name, 'account', 0, 0, now, now, True, '[]', '[]', 1, read_id='before')
    observer._attempts[name] = read
    observer._publish()
    cache.last_acquired = observer.latest()
    task = asyncio.create_task(cache.wait_account_update(1))
    await asyncio.sleep(0)
    for _ in range(5):
        observer._publish()
        await asyncio.sleep(0)
    assert not task.done()
    observer._attempts[name] = replace(read, read_id='after')
    observer._publish()
    await asyncio.wait_for(task, .1)
    assert not observer._async_waiters
