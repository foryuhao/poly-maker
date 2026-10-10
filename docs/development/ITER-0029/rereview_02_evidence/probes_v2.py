import asyncio,threading,time
from decimal import Decimal
from types import SimpleNamespace
import pytest
import polymaker.rdp_runtime as runtime
from polymaker.execution.gateway import ExecutionGateway
from test_rdp_placement import placement_scope,blocked_network,_complete_cycle_with_confirmed_fill,_prepare_repeated_continuation

async def setup(s,monkeypatch,fills=None):
    await _complete_cycle_with_confirmed_fill(s,size=5)
    c,g=await _prepare_repeated_continuation(s,monkeypatch,fills or [('foreign-token',0)],initial_size=None)
    s.session._market_data_max_age_s=60
    return c,g

@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['deadline','cancel','teardown'])
async def test_repair_held_sdk_post_drains(placement_scope,blocked_network,monkeypatch,fault):
    s=placement_scope;c,g=await setup(s,monkeypatch,[('no-token',1)])
    g._io=ExecutionGateway._io.__get__(g,ExecutionGateway)
    entered,release=threading.Event(),threading.Event();original=c.post_orders
    def post(*a,**kw):
        r=original(*a,**kw);entered.set();assert release.wait(3);return r
    c.post_orders=post
    op=asyncio.create_task(s.session.continue_rebalance(authorization=True,order_budget_s=.08 if fault=='deadline' else 2,close_budget_s=2))
    shut=None
    try:
        assert await asyncio.wait_for(asyncio.to_thread(entered.wait,2),3)
        if fault=='deadline':await asyncio.sleep(.12)
        elif fault=='cancel':op.cancel();await asyncio.sleep(.02)
        else:shut=asyncio.create_task(s.session.aclose());await asyncio.sleep(.02)
        assert not op.done() and c.cancel_calls==[]
        if shut:assert not shut.done()
        release.set()
        result=None;error=None
        try:result=await asyncio.wait_for(op,4)
        except asyncio.CancelledError:error='CancelledError'
        except Exception as exc:error=type(exc).__name__
        if shut:await shut
        t=s.session.terminal_failure_result
        print('HELD',fault,error,result.reason if result else None,s.session._terminal_stop_latched,t.cause if t else None,c.cancel_calls)
        assert c.posts==1 and len(c.cancel_calls)==1 and s.session._terminal_stop_latched
        assert t and t.confirmed_inventory.qy==5 and t.confirmed_inventory.qn==1
        if fault=='deadline':assert error is None and result.reason=='ORDER_OPERATION_DEADLINE'
        else:assert error=='CancelledError'
        assert blocked_network==[]
    finally:
        release.set()
        if not op.done():op.cancel()
        await g.aclose()

@pytest.mark.asyncio
@pytest.mark.parametrize('stage',['before','after'])
async def test_repair_registration_failure(placement_scope,blocked_network,monkeypatch,stage):
    s=placement_scope;c,g=await setup(s,monkeypatch,[('no-token',1)])
    original=s.processor.register_order
    def register(oid,*a,**kw):
        if oid.startswith('cycle-1-'):
            if stage=='after':original(oid,*a,**kw)
            raise RuntimeError('review-registration-fault')
        return original(oid,*a,**kw)
    monkeypatch.setattr(s.processor,'register_order',register)
    try:r=await s.session.continue_rebalance(authorization=True,order_budget_s=2,close_budget_s=2)
    finally:g.close()
    t=s.session.terminal_failure_result
    print('REGISTRATION',stage,r.outcome,r.reason,c.cancel_calls,t.registered_order_ids if t else None,t.unregistered_acknowledgment_ids if t else None)
    assert r.outcome=='TERMINAL' and s.session._terminal_stop_latched and c.posts==1
    assert t and not r.capital_certified
    if stage=='before':assert t.unresolved_exposure
    else:assert t.registered_reconciliation_complete and t.confirmed_inventory.qn==1
    assert c.cancel_calls==([] if stage=='before' else [('cycle-1-no-token',)])
    assert blocked_network==[]

@pytest.mark.asyncio
async def test_repair_outer_clock_failure_latches(placement_scope,blocked_network,monkeypatch):
    s=placement_scope;c,g=await setup(s,monkeypatch,[('no-token',5),('yes-token',5)])
    old=s.session._now
    def bad():raise RuntimeError('review-clock-fault')
    s.session._now=bad;r=None;error=None
    try:
        try:r=await s.session.continue_rebalance(authorization=True,order_budget_s=2,close_budget_s=2)
        except Exception as exc:error=type(exc).__name__
        s.session._now=old
        if not s.session._terminal_stop_latched:
            resumed=await s.session.continue_rebalance(authorization=True,order_budget_s=2,close_budget_s=2)
            print('CLOCK_REENTRY',error,resumed.outcome,c.posts,s.session._terminal_stop_latched)
        assert r is not None and r.outcome=='TERMINAL' and s.session._terminal_stop_latched
        assert c.posts==0 and blocked_network==[]
    finally:s.session._now=old;g.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['clock','quarantine'])
async def test_repair_monitor_unreliable_evidence_cleans(placement_scope,blocked_network,monkeypatch,fault):
    s=placement_scope;c,g=await setup(s,monkeypatch)
    old_now=s.session._now;old_sleep=s.session._sleep
    if fault=='clock':
        original=c.post_orders
        def post(*a,**kw):
            r=original(*a,**kw);s.session._now=lambda:float('nan');return r
        c.post_orders=post
    else:
        async def sleep(delay):
            s.processor.quarantine_order('cycle-1-no-token','review-unreliable')
            await old_sleep(delay)
        s.session._sleep=sleep
    r=None;error=None
    try:
        try:r=await asyncio.wait_for(s.session.continue_rebalance(authorization=True,order_budget_s=2,close_budget_s=2),3)
        except Exception as exc:error=type(exc).__name__
        print('MONITOR',fault,error,r.reason if r else None,s.session._terminal_stop_latched,c.cancel_calls,c.rows)
        assert error is None and r and r.outcome=='TERMINAL'
        assert s.session._terminal_stop_latched and c.posts==1 and len(c.cancel_calls)==1
        assert blocked_network==[]
    finally:s.session._now=old_now;s.session._sleep=old_sleep;g.close()

@pytest.mark.asyncio
async def test_repair_initial_capital_predicate_stops(placement_scope,blocked_network,monkeypatch):
    s=placement_scope;c,g=await setup(s,monkeypatch)
    old=runtime._capture_account_with_gateway
    async def zero(*a,**kw):
        row=await old(*a,**kw)
        return SimpleNamespace(**{**vars(row),'balance_usd':Decimal('0'),'allowance_usd':Decimal('0')})
    monkeypatch.setattr(runtime,'_capture_account_with_gateway',zero)
    old_now=s.session._now
    async def sleep(delay):s.session._now=lambda:s.bootstrap.end_epoch-.1
    s.session._sleep=sleep
    try:r=await s.session.continue_rebalance(authorization=True,order_budget_s=2,close_budget_s=2)
    finally:s.session._now=old_now;g.close()
    print('ENTRY_CAPITAL',r.outcome,r.reason,s.session._terminal_stop_latched,[a.reason for a in r.admissions])
    assert r.outcome=='TERMINAL' and s.session._terminal_stop_latched
    assert s.session._terminal_stop_cause=='CAPITAL_INSUFFICIENT' and c.posts==0 and blocked_network==[]
