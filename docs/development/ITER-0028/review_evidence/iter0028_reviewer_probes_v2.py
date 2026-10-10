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
        if fault=='partial':return response[:1]
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
    assert s.session.terminal_failure_result.confirmed_inventory.qy==1.5
    assert r.inventory.qy==1.5, 'terminal result reuses stale prior close inventory'
    assert blocked_network==[]
