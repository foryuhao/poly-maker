import asyncio,time
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
import pytest
import polymaker.rdp_runtime as runtime
from polymaker.config import Config
from polymaker.execution.gateway import ExecutionGateway
from polymaker.rdp_account import RDPPositionEvidence
from polymaker.risk.rdp_capital import RDPMode
from polymaker.strategy.rdp_accounting import snapshot_inventory
from test_rdp_placement import placement_scope,blocked_network,_complete_cycle_with_confirmed_fill,RepeatedNormalSDKClient,_send_matched_then_confirmed

async def setup(s,monkeypatch, fills=None):
    completion,_,_=await _complete_cycle_with_confirmed_fill(s,size=1.25)
    client=RepeatedNormalSDKClient(s,fills or [('no-token',1),('no-token',5.25)])
    gateway=ExecutionGateway(Config(),paper=False)
    gateway._client=client
    async def inline(fn,*args): return fn(*args)
    async def no_wait(*a,**k):pass
    gateway._io=inline
    gateway._order_bucket.acquire=no_wait
    gateway._cancel_bucket.acquire=no_wait
    s.session.user_session.gateway=gateway
    s.session.user_session.observation.account=SimpleNamespace(signer='fake-signer',funder='fake-funder')
    async def capture(_bootstrap,_gateway,signer,funder,*,now):
        inv=snapshot_inventory(s.market,s.processor,set(s.processor._owned))
        ts=now(); cash=Decimal('30')-Decimal(str(inv.inventory_cost))
        return SimpleNamespace(signer=signer,funder=funder,condition_id=s.market.condition_id,
            balance_usd=cash,allowance_usd=cash,required_spenders=('0x'+'a'*40,),
            selected_positions=(RDPPositionEvidence('Up','yes-token',Decimal(str(inv.qy)),Decimal('0')),
                                RDPPositionEvidence('Down','no-token',Decimal(str(inv.qn)),Decimal('0'))),
            selected_open_orders=(),total_open_order_count=0,all_open_orders=(),
            orders_receipt_ts=ts-.002,positions_receipt_ts=ts-.001,collateral_receipt_ts=ts)
    monkeypatch.setattr(runtime,'_capture_account_with_gateway',capture)
    return completion,client,gateway

@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['partial','unknown'])
async def test_later_fault_inventory(placement_scope,blocked_network,monkeypatch,fault):
    s=placement_scope
    initial,c,g=await setup(s,monkeypatch,[('yes-token',1)])
    original=c.post_orders
    def post(*a,**k):
        response=original(*a,**k)
        if fault=='partial':return [response[0],{'success':False,'errorMsg':'PROBE_REJECTED'}]
        raise RuntimeError('probe-post-unknown')
    c.post_orders=post
    try:r=await s.session.continue_normal(authorization=True,order_budget_s=2,close_budget_s=2)
    finally:g.close()
    t=s.session.terminal_failure_result
    print('OBS',fault,r.outcome,r.reason,r.inventory.qy if r.inventory else None,t.confirmed_inventory.qy if t.confirmed_inventory else None,c.cancel_calls,r.run_order_ids)
    assert r.outcome=='TERMINAL' and s.session._terminal_stop_latched
    assert c.posts==1
    assert t is not None and t.cleanup.replacement_blocked
    assert not t.cleanup.capital or not t.cleanup.capital.certified
    assert blocked_network==[]
    if fault=='partial':
        assert c.cancel_calls==[('cycle-1-yes-token',)]
        assert t.confirmed_inventory.qy==pytest.approx(2.25)
        assert r.inventory.qy==pytest.approx(2.25), 'continuation result omits accepted current confirmed fill'
    else:assert c.cancel_calls==[] and t.confirmed_inventory.qy==pytest.approx(1.25)

@pytest.mark.asyncio
async def test_late_prior_fill_guard(placement_scope,blocked_network,monkeypatch):
    s=placement_scope
    _,c,g=await setup(s,monkeypatch)
    def late():_send_matched_then_confirmed(s,'yes-order',trade_id='late-prior',size=.25,price=.55,receipt_ns=s.session._monotonic_ns())
    c.on_create=late
    try:r=await s.session.continue_normal(authorization=True,order_budget_s=2,close_budget_s=2)
    finally:g.close()
    assert c.posts==0 and r.outcome=='TERMINAL'
    assert 'STALE_CLOSE_EVIDENCE' in str(r.submissions[0].guard_reasons)
    assert snapshot_inventory(s.market,s.processor,set(s.processor._owned)).qy==1.5
    assert r.inventory.qy==1.5, 'terminal result reuses stale prior close inventory'
    assert blocked_network==[]

@pytest.mark.asyncio
@pytest.mark.parametrize('action',['latefill','cancel','teardown'])
async def test_held_subsequent_close(placement_scope,blocked_network,monkeypatch,action):
    s=placement_scope
    _,c,g=await setup(s,monkeypatch,[('no-token',1),('no-token',4.75)])
    started,release=asyncio.Event(),asyncio.Event()
    original=g.cancel
    held=False
    async def cancel(*a,**k):
        nonlocal held
        if not held:
            held=True
            r=await original(*a,**k)
            started.set();await release.wait();return r
        return await original(*a,**k)
    g.cancel=cancel
    op=asyncio.create_task(s.session.continue_normal(authorization=True,order_budget_s=2,close_budget_s=2))
    await asyncio.wait_for(started.wait(),2)
    assert c.posts==1 and s.owner.current.replacement_blocked
    competing=await s.session.post_cycle_next_action_admission()
    assert not competing.eligible
    assert c.posts==1
    shutdown=None
    try:
        if action=='latefill':
            from test_rdp_placement import _rest_trade
            oid='cycle-1-no-token'
            price=float(c.rows[oid]['price'])
            _send_matched_then_confirmed(s,oid,trade_id='during-close',size=.5,price=price,receipt_ns=s.session._monotonic_ns())
            c.rows[oid]['size_matched']='1.5'
            c.trades.append(_rest_trade(oid,'no-token',str(price),'.5',trade_id='during-close'))
        elif action=='cancel':
            op.cancel();await asyncio.sleep(0);op.cancel();await asyncio.sleep(0)
            assert not op.done()
        else:
            shutdown=asyncio.create_task(s.session.aclose());await asyncio.sleep(0)
            assert not shutdown.done()
        release.set()
        if action=='cancel':
            with pytest.raises(asyncio.CancelledError):await op
        elif action=='teardown':
            await shutdown
            try:
                r=await op
                assert r.outcome=='TERMINAL'
            except asyncio.CancelledError:pass
        else:
            r=await op
            assert r.outcome=='REBALANCE_HANDOFF' and r.inventory.delta==-5
            assert c.posts==2
        if action!='latefill':
            assert s.session._terminal_stop_latched and c.posts==1
            t=s.session.terminal_failure_result
            assert t is not None and t.confirmed_inventory.qy==1.25 and t.confirmed_inventory.qn==1
            assert t.cleanup.replacement_blocked and t.cleanup.next_mode is None
        assert all(all(oid.startswith('cycle-') for oid in ids) for ids in c.cancel_calls)
        assert len(c.cancel_calls)==(2 if action=='latefill' else 1)
        assert blocked_network==[]
    finally:
        release.set()
        if not op.done():op.cancel()
        g.close()

@pytest.mark.asyncio
async def test_later_zero_fill_then_healthy_requote(placement_scope,blocked_network,monkeypatch):
    s=placement_scope
    _,c,g=await setup(s,monkeypatch,[('no-token',0),('no-token',5),('no-token',1.25)])
    old_sleep=s.session._sleep
    entered,release=asyncio.Event(),asyncio.Event()
    start=s.session._monotonic_ns()
    async def sleep(delay):
        await old_sleep(delay)
        if c.posts==1 and s.session._monotonic_ns()-start>4_000_000_000:
            entered.set();await release.wait()
    s.session._sleep=sleep
    op=asyncio.create_task(s.session.continue_normal(authorization=True,order_budget_s=2,close_budget_s=2))
    try:
        await asyncio.wait_for(entered.wait(),2)
        assert c.posts==1 and c.cancel_calls==[]
        w=s.session.initial_fill_window()
        assert w.reliable and not w.started and not w.closure_due
        book=s.session.public_session.service.book('yes-token')
        book.apply_delta(__import__('polymaker.domain',fromlist=['Side']).Side.BUY,.56,0,time.time())
        release.set()
        r=await asyncio.wait_for(op,5)
        assert r.outcome=='REBALANCE_HANDOFF'
        assert r.cycles[1].trigger=='QUOTE_PRICE_CHANGED'
        assert c.posts==3
        assert all(ids==tuple(sorted(ids)) for ids in c.cancel_calls)
        assert r.inventory.qy==1.25 and r.inventory.qn==6.25
        assert blocked_network==[]
    finally:
        release.set()
        if not op.done():op.cancel()
        g.close()

@pytest.mark.asyncio
async def test_reliable_no_send_quote_movement_is_not_stop(placement_scope,blocked_network,monkeypatch):
    s=placement_scope
    _,c,g=await setup(s,monkeypatch)
    c.on_create=lambda:s.session.public_session.service.book('yes-token').apply_delta(__import__('polymaker.domain',fromlist=['Side']).Side.BUY,.56,0,time.time())
    try:r=await s.session.continue_normal(authorization=True,order_budget_s=2,close_budget_s=2)
    finally:g.close()
    assert c.posts==0 and c.cancel_calls==[]
    assert r.submissions[0].reason=='CONTINUATION_QUOTE_INTENT_CHANGED'
    assert r.submissions[0].resolved_attempt.outcome=='EMPTY' and not r.submissions[0].resolved_attempt.unresolved
    print('CLEAN_NO_SEND',r.outcome,r.reason,s.session._terminal_stop_latched)
    assert not s.session._terminal_stop_latched, 'reliable no-exposure quote invalidation creates STATE_UNRELIABLE STOP'
    assert blocked_network==[]

@pytest.mark.asyncio
async def test_subsequent_complete_ack_operation_deadline(placement_scope,blocked_network,monkeypatch):
    import threading
    s=placement_scope
    _,c,g=await setup(s,monkeypatch)
    g._io=ExecutionGateway._io.__get__(g,ExecutionGateway)
    started,release=threading.Event(),threading.Event()
    original=c.post_orders
    def post(*a,**k):
        r=original(*a,**k);started.set();assert release.wait(3);return r
    c.post_orders=post
    op=asyncio.create_task(s.session.continue_normal(authorization=True,order_budget_s=.08,close_budget_s=2))
    try:
        assert await asyncio.wait_for(asyncio.to_thread(started.wait,2),3)
        await asyncio.sleep(.12)
        assert not op.done()
        release.set();r=await op
        t=s.session.terminal_failure_result
        assert c.posts==1 and len(c.cancel_calls)==1 and s.session._terminal_stop_latched
        assert t.confirmed_inventory.qy==1.25 and t.confirmed_inventory.qn==1
        assert t.cleanup.replacement_blocked and not t.cleanup.next_mode
        assert r.submissions[0].interruption_reason=='ORDER_OPERATION_DEADLINE'
        print('INTERRUPTED',r.reason,r.inventory.qn,t.confirmed_inventory.qn)
        assert r.inventory.qn==1, 'complete-ack interruption result forgets current accepted fill'
        assert blocked_network==[]
    finally:
        release.set()
        if not op.done():op.cancel()
        await g.aclose()

@pytest.mark.asyncio
async def test_later_window_uses_new_receipt_ignores_ordinary_move(placement_scope,blocked_network,monkeypatch):
    s=placement_scope
    _,c,g=await setup(s,monkeypatch)
    start=s.session._monotonic_ns()
    old_sleep=s.session._sleep
    entered,release=asyncio.Event(),asyncio.Event()
    async def sleep(delay):
        if c.posts==1 and not entered.is_set():
            entered.set();await release.wait()
        await old_sleep(delay)
    s.session._sleep=sleep
    op=asyncio.create_task(s.session.continue_normal(authorization=True,order_budget_s=2,close_budget_s=2))
    try:
        await asyncio.wait_for(entered.wait(),2)
        w=s.session.initial_fill_window()
        assert w.reliable and w.started and not w.closure_due
        assert w.first_receipt_monotonic_ns>=start
        assert w.deadline_monotonic_ns==w.first_receipt_monotonic_ns+3_000_000_000
        s.session.public_session.service.book('yes-token').apply_delta(__import__('polymaker.domain',fromlist=['Side']).Side.BUY,.56,0,time.time())
        await asyncio.sleep(0)
        assert c.posts==1 and c.cancel_calls==[]
        release.set();r=await asyncio.wait_for(op,5)
        assert r.outcome=='REBALANCE_HANDOFF' and r.inventory.delta==-5
        assert r.cycles[1].trigger=='WINDOW_EXPIRED'
        assert r.cycles[1].fill_window_completion.window_deadline_monotonic_ns==w.deadline_monotonic_ns
        assert blocked_network==[]
    finally:
        release.set()
        if not op.done():op.cancel()
        g.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('authorization',[1,'true',None,False])
async def test_nonboolean_continuation_denied_without_io(placement_scope,blocked_network,monkeypatch,authorization):
    s=placement_scope
    _,c,g=await setup(s,monkeypatch)
    try:r=await s.session.continue_normal(authorization=authorization,order_budget_s=2,close_budget_s=2)
    finally:g.close()
    assert r.outcome=='DENIED' and c.created==[] and c.posts==0 and c.cancel_calls==[]
    assert not s.session._terminal_stop_latched and blocked_network==[]

@pytest.mark.asyncio
@pytest.mark.parametrize('change',['cash','allowance','positions','generation','backlog','stale_book','timestamp','minimum'])
async def test_subsequent_guard_current_evidence(placement_scope,blocked_network,monkeypatch,change):
    s=placement_scope
    _,c,g=await setup(s,monkeypatch)
    def mutate():
        service=s.session.public_session.service
        account=s.session._issued_continuation_account
        if change=='cash':account.balance_usd=Decimal('.1')
        elif change=='allowance':account.allowance_usd=Decimal('.1')
        elif change=='positions':account.selected_positions=(replace(account.selected_positions[0],quantity=Decimal('2')),account.selected_positions[1])
        elif change=='generation':service.connection_generation+=1
        elif change=='backlog':service.metrics['received']+=1
        elif change=='stale_book':service.book('yes-token').local_ts=time.time()-100
        elif change=='timestamp':account.collateral_receipt_ts=time.time()-100
        elif change=='minimum':s.session.bootstrap=replace(s.session.bootstrap,token_metadata=tuple(replace(m,min_order_size=6) for m in s.session.bootstrap.token_metadata))
    c.on_create=mutate
    try:r=await s.session.continue_normal(authorization=True,order_budget_s=2,close_budget_s=2)
    finally:g.close()
    assert c.posts==0 and c.cancel_calls==[]
    assert r.outcome=='TERMINAL' and not r.submissions[0].post_started
    assert any(reason is not None for reason in r.submissions[0].guard_reasons)
    assert snapshot_inventory(s.market,s.processor,set(s.processor._owned)).inventory_cost==.6875
    assert blocked_network==[]

@pytest.mark.asyncio
async def test_clock_failure_terminal_cannot_resume_new_risk(placement_scope,blocked_network,monkeypatch):
    s=placement_scope
    _,c,g=await setup(s,monkeypatch)
    now=s.session._now
    def broken():raise RuntimeError('clock-failure-private')
    s.session._now=broken
    try:
        first=await s.session.continue_normal(authorization=True,order_budget_s=2,close_budget_s=2)
        assert first.outcome=='TERMINAL' and first.reason=='OPERATION_CLOCK_FAILED:RuntimeError' and c.posts==0
        latched=s.session._terminal_stop_latched
        s.session._now=now
        second=await s.session.continue_normal(authorization=True,order_budget_s=2,close_budget_s=2)
        print('CLOCK_TERMINAL',first.reason,latched,second.outcome,c.posts)
        assert c.posts==0, 'reported terminal clock failure permits subsequent new-risk POSTs'
        assert s.session._terminal_stop_latched
        assert blocked_network==[]
    finally:s.session._now=now;g.close()

@pytest.mark.asyncio
async def test_higher_current_cash_preserves_model_and_send_gates(placement_scope,blocked_network,monkeypatch):
    s=placement_scope
    _,c,g=await setup(s,monkeypatch)
    capture=runtime._capture_account_with_gateway
    async def high(*a,**k):
        account=await capture(*a,**k)
        account.balance_usd=account.allowance_usd=Decimal('100')
        return account
    monkeypatch.setattr(runtime,'_capture_account_with_gateway',high)
    try:r=await s.session.continue_normal(authorization=True,order_budget_s=2,close_budget_s=2)
    finally:g.close()
    assert r.outcome=='REBALANCE_HANDOFF' and c.posts==2
    assert all(a.capital_projection.snapshot.capital==Decimal('30') for a in r.admissions)
    assert all(a.capital_admission.protected_amount==Decimal('5') for a in r.admissions)
    assert all(a.observed_cash_backing==Decimal('100') for a in r.admissions)
    assert r.inventory_cost==Decimal('3.25')
    assert blocked_network==[]
