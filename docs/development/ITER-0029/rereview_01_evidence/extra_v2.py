import asyncio,time
from dataclasses import asdict
import pytest
import polymaker.execution.rdp_placement as owner_module
from test_rdp_placement import placement_scope,blocked_network,_send_matched_then_confirmed,_rest_trade,test_one_normal_continuation_call_operates_real_fake_sdk_cycles_cumulatively as _integrated_normal
from probes_v2 import setup

@pytest.mark.asyncio
async def test_registration_before_preserves_prior_inventory(placement_scope,blocked_network,monkeypatch):
    s=placement_scope;c,g=await setup(s,monkeypatch,[('no-token',1)])
    original=s.processor.register_order
    def register(oid,*a,**kw):
        if oid.startswith('cycle-1-'):raise RuntimeError('review-registration-fault')
        return original(oid,*a,**kw)
    monkeypatch.setattr(s.processor,'register_order',register)
    try:r=await s.session.continue_rebalance(authorization=True,order_budget_s=2,close_budget_s=2)
    finally:g.close()
    t=s.session.terminal_failure_result
    from polymaker.strategy.rdp_accounting import snapshot_inventory
    actual=snapshot_inventory(s.market,s.processor,set(s.processor._owned))
    print('KNOWN_PRIOR',actual.qy,actual.qn,r.inventory,t.confirmed_inventory,t.registered_order_ids,t.unregistered_acknowledgment_ids)
    assert actual.qy==5 and actual.qn==0 and c.cancel_calls==[]
    assert r.inventory and r.inventory.qy==5 and t.confirmed_inventory and t.confirmed_inventory.qy==5
    assert not r.capital_certified and r.unresolved_exposure and blocked_network==[]

@pytest.mark.asyncio
@pytest.mark.parametrize('late',['current','prior'])
async def test_held_recovery_rechecks_fills(placement_scope,blocked_network,monkeypatch,late):
    s=placement_scope;c,g=await setup(s,monkeypatch,[('no-token',1),('yes-token',5)])
    entered,release=asyncio.Event(),asyncio.Event();cancel=g.cancel
    held=False
    async def hold(*a,**kw):
        nonlocal held
        result=await cancel(*a,**kw)
        if not held:held=True;entered.set();await release.wait()
        return result
    g.cancel=hold
    op=asyncio.create_task(s.session.continue_rebalance(authorization=True,order_budget_s=2,close_budget_s=2))
    try:
        await asyncio.wait_for(entered.wait(),2)
        assert c.posts==1
        if late=='current':
            _send_matched_then_confirmed(s,'cycle-1-no-token',trade_id='review-late',size=3,price=.47,receipt_ns=s.session._monotonic_ns())
            c.rows['cycle-1-no-token']['size_matched']='4'
            c.trades.append(_rest_trade('cycle-1-no-token','no-token','.47','3',trade_id='review-late'))
        else:_send_matched_then_confirmed(s,'yes-order',trade_id='review-prior-late',size=1,price=.55,receipt_ns=s.session._monotonic_ns())
        release.set();r=await asyncio.wait_for(op,4)
        print('HELD_RECOVERY',late,r.outcome,r.reason,r.inventory.delta if r.inventory else None,c.posts)
        if late=='current':
            assert c.posts==2
            assert {q.token_id:q.size for q in c.created[1:3]}=={'yes-token':5,'no-token':6}
        else:assert c.posts==1 and r.outcome=='TERMINAL' and s.session._terminal_stop_latched and not r.capital_certified
        assert blocked_network==[]
    finally:
        release.set()
        if not op.done():op.cancel()
        g.close()

@pytest.mark.asyncio
async def test_normal_proof_order_variation(placement_scope,blocked_network,monkeypatch):
    original=owner_module.project_rdp_capital
    last=[]
    def project(*a,**kw):
        r=original(*a,**kw);last[:]=[r];return r
    monkeypatch.setattr(owner_module,'project_rdp_capital',project)
    old=placement_scope.owner.record_close
    def record(attempt,barrier,capital):
        r=old(attempt,barrier,capital)
        if r.replacement_blocked and capital and capital.certified and last and last[0].certified:
            supplied=asdict(capital.snapshot);fresh=asdict(last[0].snapshot)
            print('PROOF_COMPARISON',attempt.attempt_id,{k:(supplied[k],fresh[k]) for k in supplied if supplied[k]!=fresh[k]})
        return r
    monkeypatch.setattr(placement_scope.owner,'record_close',record)
    await _integrated_normal(placement_scope,blocked_network,monkeypatch,[('yes-token',1.25),('yes-token',1.0),('yes-token',1.0),('yes-token',1.75)],5.0,False)
