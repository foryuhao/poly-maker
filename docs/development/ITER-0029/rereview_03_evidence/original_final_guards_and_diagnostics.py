import asyncio,threading,time
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
import pytest
import polymaker.rdp_runtime as runtime
from polymaker.execution.gateway import ExecutionGateway
from test_rdp_placement import placement_scope,blocked_network,test_one_normal_continuation_call_operates_real_fake_sdk_cycles_cumulatively as _normal_case
from probes_v2 import setup

@pytest.mark.asyncio
async def test_clock_regression_between_sign_and_post_denies_new_risk(placement_scope,blocked_network,monkeypatch):
    s=placement_scope;c,g=await setup(s,monkeypatch)
    base=time.time();now=[base+3];old_now=s.session._now;s.session._now=lambda:now[0]
    capture=runtime._capture_account_with_gateway;status=runtime.read_rdp_market_status
    async def older_account(*a,**kw):
        row=await capture(*a,**kw)
        return SimpleNamespace(**{**vars(row),'orders_receipt_ts':row.orders_receipt_ts-1.5,'positions_receipt_ts':row.positions_receipt_ts-1.5,'collateral_receipt_ts':row.collateral_receipt_ts-1.5})
    async def older_status(*a,**kw):
        row=await status(*a,**kw);return replace(row,receipt_ts=row.receipt_ts-1.5)
    monkeypatch.setattr(runtime,'_capture_account_with_gateway',older_account)
    monkeypatch.setattr(runtime,'read_rdp_market_status',older_status)
    c.on_create=lambda:now.__setitem__(0,base+2)
    try:
        r=await asyncio.wait_for(s.session.continue_rebalance(authorization=True,order_budget_s=2,close_budget_s=2),3)
        print('FINAL_CLOCK',r.outcome,r.reason,c.posts,c.cancel_calls,s.session._terminal_stop_detail,[(x.outcome,x.reason,x.post_started,x.guard_reasons) for x in r.submissions])
        assert r.outcome=='TERMINAL' and s.session._terminal_stop_latched
        assert c.posts==0, 'actual POST happened after operation clock regressed between signing and final guard'
        assert c.cancel_calls==[] and blocked_network==[]
    finally:s.session._now=old_now;g.close()

@pytest.mark.asyncio
async def test_failure_only_normal_diagnostic_survives_proved_close_results(placement_scope,blocked_network,monkeypatch):
    s=placement_scope;old=s.owner.record_close
    def reject_after_record(attempt,barrier,capital):
        result=old(attempt,barrier,capital)
        if attempt.attempt_id==2:return replace(result,replacement_blocked=True)
        return result
    monkeypatch.setattr(s.owner,'record_close',reject_after_record)
    error=None;message=None
    try:
        await _normal_case(s,blocked_network,monkeypatch,[('yes-token',1.25),('yes-token',1),('yes-token',1),('yes-token',1.75)],5.0,False)
    except BaseException as exc:error=type(exc).__name__;message=str(exc)
    print('FAILURE_DIAGNOSTIC',error,message)
    assert error=='Failed' and 'NORMAL positive path diagnostic:' in message and 'CLOSE_PROOF_NOT_RELEASED' in message
    assert blocked_network==[]

@pytest.mark.asyncio
@pytest.mark.parametrize('interruption',['deadline','teardown'])
async def test_primary_interruption_survives_postdrain_clock_failure(placement_scope,blocked_network,monkeypatch,interruption):
    s=placement_scope;c,g=await setup(s,monkeypatch,[('no-token',1)])
    g._io=ExecutionGateway._io.__get__(g,ExecutionGateway)
    entered,release=threading.Event(),threading.Event();old=c.post_orders;old_now=s.session._now
    def post(*a,**kw):
        result=old(*a,**kw);entered.set();assert release.wait(3);s.session._now=lambda:float('nan');return result
    c.post_orders=post
    op=asyncio.create_task(s.session.continue_rebalance(authorization=True,order_budget_s=.08 if interruption=='deadline' else 2,close_budget_s=2));shutdown=None
    try:
        assert await asyncio.wait_for(asyncio.to_thread(entered.wait,2),3)
        if interruption=='deadline':await asyncio.sleep(.12)
        else:shutdown=asyncio.create_task(s.session.aclose());await asyncio.sleep(.02)
        assert not op.done() and c.cancel_calls==[]
        release.set();error=None;r=None
        try:r=await asyncio.wait_for(op,4)
        except asyncio.CancelledError:error='CancelledError'
        if shutdown:await shutdown
        t=s.session.terminal_failure_result
        print('PRIMARY_AND_CLOCK',interruption,error,r.reason if r else None,t.cause if t else None,s.session._terminal_stop_detail,c.cancel_calls)
        assert c.posts==1 and c.cancel_calls==[('cycle-1-no-token',)]
        assert t and t.cause==('ORDER_OPERATION_DEADLINE' if interruption=='deadline' else 'SESSION_TEARDOWN')
        if interruption=='deadline':assert r and r.reason=='ORDER_OPERATION_DEADLINE'
        else:assert error=='CancelledError'
        assert blocked_network==[]
    finally:
        release.set();s.session._now=old_now
        if not op.done():op.cancel()
        await g.aclose()
