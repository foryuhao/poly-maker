import asyncio,time
from dataclasses import replace
import pytest
from structlog.testing import capture_logs
import polymaker.rdp_runtime as runtime
from polymaker.domain import TradeState
from test_rdp_placement import placement_scope,blocked_network,_send_window_trade,test_rebalance_safety_stop_closes_scope_without_reposting as _stop_case
from probes_v2 import setup

@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['time','capital','market','state'])
async def test_retained_terminal_stop_category_agrees(placement_scope,blocked_network,monkeypatch,failure):
    s=placement_scope
    with capture_logs() as logs:
        await _stop_case(s,blocked_network,monkeypatch,failure)
    expected={'time':'TIME_EXHAUSTED','capital':'CAPITAL_INSUFFICIENT','market':'MARKET_NOT_TRADABLE','state':'STATE_UNRELIABLE'}[failure]
    t=s.session.terminal_failure_result
    stopped=[r for r in logs if r.get('event') in {'rdp_terminal_stop_latched','rdp_terminal_failure_finished','rdp_rebalance_stopped'}]
    print('CATEGORY',failure,s.session._terminal_stop_reason,t.stop_reason if t else None,[(r['event'],r.get('stop_reason'),r.get('cause')) for r in stopped])
    assert t and t.stop_reason==expected
    assert all(r.get('stop_reason')==expected for r in stopped)
    assert blocked_network==[]

@pytest.mark.asyncio
@pytest.mark.parametrize('where',['entry','live'])
@pytest.mark.parametrize('value',['bool','str','nan','inf','before_epoch'])
async def test_invalid_clock_shapes_terminal_and_scoped_cleanup(placement_scope,blocked_network,monkeypatch,where,value):
    s=placement_scope;c,g=await setup(s,monkeypatch)
    old=s.session._now
    v={'bool':True,'str':'123','nan':float('nan'),'inf':float('inf'),'before_epoch':s.bootstrap.epoch-1}[value]
    if where=='entry':s.session._now=lambda:v
    else:
        original=c.post_orders
        def post(*a,**kw):
            result=original(*a,**kw);s.session._now=lambda:v;return result
        c.post_orders=post
    try:
        with capture_logs() as logs:r=await s.session.continue_rebalance(authorization=True,order_budget_s=2,close_budget_s=2)
        assert r.outcome=='TERMINAL' and r.reason=='STATE_UNRELIABLE' and s.session._terminal_stop_latched
        assert c.posts==(0 if where=='entry' else 1)
        assert c.cancel_calls==([] if where=='entry' else [('cycle-1-no-token',)])
        denied=await s.session.continue_rebalance(authorization=True,order_budget_s=2,close_budget_s=2)
        assert denied.outcome=='DENIED'
        if where=='live':
            stop=[x for x in logs if x.get('event')=='rdp_rebalance_stopped'][-1]
            assert set(stop['closed_order_ids'])<= {'cycle-1-no-token'}
            assert stop['close_proof_attempt_id'] in {None,2}
        assert not r.capital_certified and blocked_network==[]
    finally:s.session._now=old;g.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('fill_fault',['pending','malformed'])
async def test_uncertain_live_fill_cleans_without_fabricated_capital(placement_scope,blocked_network,monkeypatch,fill_fault):
    s=placement_scope;c,g=await setup(s,monkeypatch)
    old=s.session._sleep;injected=False
    async def sleep(delay):
        nonlocal injected
        if not injected:
            injected=True
            _send_window_trade(s,'cycle-1-no-token',status=TradeState.MATCHED,trade_id='rereview-pending',size=1 if fill_fault=='pending' else float('nan'),price=.47,receipt_ns=s.session._monotonic_ns())
        await old(delay)
    s.session._sleep=sleep
    try:
        with capture_logs() as logs:r=await asyncio.wait_for(s.session.continue_rebalance(authorization=True,order_budget_s=2,close_budget_s=.15),2)
        t=s.session.terminal_failure_result
        print('UNCERTAIN',fill_fault,r.reason,c.posts,c.cancel_calls,r.inventory_scope,t.unresolved_exposure if t else None)
        assert r.outcome=='TERMINAL' and r.reason=='STATE_UNRELIABLE' and s.session._terminal_stop_latched
        assert c.posts==1 and c.cancel_calls==[('cycle-1-no-token',)] and not r.capital_certified
        assert t and t.unresolved_exposure and r.inventory and r.inventory.qy==5
        stop=[x for x in logs if x.get('event')=='rdp_rebalance_stopped'][-1]
        assert set(stop['closed_order_ids'])<= {'cycle-1-no-token'}
        if not stop['close_complete']:assert stop['close_proof_attempt_id'] is None
        assert blocked_network==[]
    finally:s.session._sleep=old;g.close()

@pytest.mark.asyncio
async def test_monitor_checks_full_order_cancel_reconcile_budget(placement_scope,blocked_network,monkeypatch):
    s=placement_scope;c,g=await setup(s,monkeypatch)
    s.session._market_data_max_age_s=1000
    old_now=s.session._now;old_sleep=s.session._sleep
    polls=[]
    original=c.post_orders
    def post(*a,**kw):
        result=original(*a,**kw);s.session._now=lambda:s.bootstrap.end_epoch-1.5;return result
    c.post_orders=post
    async def sleep(delay):
        polls.append((s.session._now(),s.session._terminal_stop_latched,c.posts,len(c.cancel_calls)))
        s.session._now=lambda:s.bootstrap.end_epoch-.1
        await old_sleep(delay)
    s.session._sleep=sleep
    try:
        r=await asyncio.wait_for(s.session.continue_rebalance(authorization=True,order_budget_s=1,close_budget_s=1),3)
        print('FULL_TIME_BUDGET',r.reason,polls,c.cancel_calls)
        assert r.outcome=='TERMINAL' and r.reason=='TIME_EXHAUSTED'
        assert c.posts==1 and c.cancel_calls==[('cycle-1-no-token',)]
        assert polls==[], 'monitor rested despite less remaining time than order+close budgets'
        assert blocked_network==[]
    finally:s.session._now=old_now;s.session._sleep=old_sleep;g.close()

@pytest.mark.asyncio
async def test_backward_clock_inside_market_is_unreliable(placement_scope,blocked_network,monkeypatch):
    s=placement_scope;c,g=await setup(s,monkeypatch)
    base=time.time();now=[base+3];s.session._now=lambda:now[0]
    old_sleep=s.session._sleep;polls=[]
    async def sleep(delay):
        polls.append(now[0])
        now[0]=base+2 if len(polls)==1 else s.bootstrap.end_epoch-.1
        await old_sleep(delay)
    s.session._sleep=sleep
    try:
        r=await asyncio.wait_for(s.session.continue_rebalance(authorization=True,order_budget_s=1,close_budget_s=1),3)
        print('BACKWARD_CLOCK',r.reason,polls,c.cancel_calls)
        assert r.outcome=='TERMINAL' and r.reason=='STATE_UNRELIABLE'
        assert len(polls)==1 and c.posts==1 and c.cancel_calls==[('cycle-1-no-token',)]
        assert blocked_network==[]
    finally:s.session._sleep=old_sleep;g.close()
