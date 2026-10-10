import copy,time
from dataclasses import replace
from decimal import Decimal
import pytest
import polymaker.rdp_runtime as runtime
from polymaker.domain import Side,TradeState
from polymaker.state.tracker import TradeEvent
from polymaker.execution.filltruth import ReconciliationOutcome
from iter0028_reviewer_probes_v3 import setup,placement_scope,blocked_network,_send_matched_then_confirmed

@pytest.mark.asyncio
@pytest.mark.parametrize('change',['missing','incomplete','wrong_scope','overlap','late_confirmed','pending_fill','quarantine','fill_quarantine','foreign_registry','invalid_economics','unproved_id','unrelated'])
async def test_current_authority_reporting(placement_scope,blocked_network,monkeypatch,change):
    s=placement_scope
    _,c,g=await setup(s,monkeypatch)
    old=s.owner.current
    proof=s.owner._close_proofs[old.attempt_id]
    if change=='missing':s.owner._close_proofs.clear()
    elif change=='incomplete':s.owner._close_proofs[old.attempt_id]=replace(proof,complete=False)
    elif change=='wrong_scope':s.owner._close_proofs[old.attempt_id]=replace(proof,reconciliation_outcomes=(('foreign-proof-id',ReconciliationOutcome.COMPLETE),))
    elif change=='overlap':
        duplicate=replace(old,attempt_id=99)
        s.owner._attempts[99]=duplicate;s.owner._close_proofs[99]=proof
    elif change=='late_confirmed':_send_matched_then_confirmed(s,'yes-order',trade_id='new-confirmed',size=.25,price=.55,receipt_ns=s.session._monotonic_ns())
    elif change=='pending_fill':
        event=TradeEvent('yes-token',Side.BUY,.55,.25,'new-pending',TradeState.MATCHED,time.time(),'yes-order',s.market.condition_id)
        s.processor.on_trade(event,s.market.condition_id,received_monotonic_ns=s.session._monotonic_ns(),connection_generation=4)
    elif change=='quarantine':s.processor.quarantine_order('no-order','review-only')
    elif change=='fill_quarantine':s.processor.quarantined_fill_keys.add(next(key for key in s.processor.applied_fill_keys if key[1]=='yes-order'))
    elif change in {'foreign_registry','invalid_economics'}:
        original=s.processor.owned_record
        def row(order_id):
            result=original(order_id)
            if order_id=='no-order':result={**result,**({'condition_id':'foreign-condition'} if change=='foreign_registry' else {'submitted_qty':'NaN'})}
            return result
        monkeypatch.setattr(s.processor,'owned_record',row)
    elif change=='unproved_id':
        s.processor.register_order('unproved-id',s.market.condition_id,'no-token',Side.BUY,5,.41,'rdp')
        altered=replace(old,acknowledged_order_ids=old.acknowledged_order_ids+('unproved-id',),closed_order_ids=old.closed_order_ids+('unproved-id',))
        s.owner._current=altered;s.owner._attempts[old.attempt_id]=altered
    elif change=='unrelated':
        s.processor.register_order('unrelated-id','other-condition','other-token',Side.BUY,5,.4,'other')
    before=(s.owner.current,dict(s.owner._attempts),dict(s.owner._close_proofs),set(s.processor._owned),copy.deepcopy(s.store.orders),set(s.processor.applied_fill_keys),dict(s.processor.fill_records))
    calls=[]
    original_project=runtime.project_rdp_capital
    def project(*a,**kw):
        calls.append((set(a[2]),tuple(kw['close_evidence'])))
        return original_project(*a,**kw)
    monkeypatch.setattr(runtime,'project_rdp_capital',project)
    async def forbidden(*a,**kw):raise AssertionError('report attempted runtime I/O')
    for name in ['place','cancel','open_orders','get_order','get_trades']:
        monkeypatch.setattr(g,name,forbidden)
    monkeypatch.setattr(runtime,'_capture_account_with_gateway',forbidden)
    monkeypatch.setattr(runtime,'read_rdp_market_status',forbidden)
    try:r=await s.session.continue_normal()
    finally:g.close()
    after=(s.owner.current,dict(s.owner._attempts),dict(s.owner._close_proofs),set(s.processor._owned),copy.deepcopy(s.store.orders),set(s.processor.applied_fill_keys),dict(s.processor.fill_records))
    print('AUTHORITY',change,r.capital_certified,r.unresolved_exposure,r.inventory_scope,r.inventory.qy if r.inventory else None,len(calls))
    assert r.outcome=='DENIED' and not r.execution_authorized and c.posts==0 and c.cancel_calls==[]
    assert before==after
    if change=='unrelated':
        assert r.capital_certified and not r.unresolved_exposure
        assert 'unrelated-id' not in r.run_order_ids and r.inventory.qy==1.25
    else:assert not r.capital_certified and r.unresolved_exposure
    if calls:assert calls[0][0]==set(r.run_order_ids)
    if change=='late_confirmed':assert r.inventory.qy==1.5 and r.inventory_cost==Decimal('.825')
    if change=='pending_fill':assert r.inventory.qy==1.25
    if change=='quarantine':assert r.inventory.qy==1.25 and r.inventory_scope=='CLEAN_REGISTERED_SUBSET_CONFIRMED_FILLS'
    assert blocked_network==[]

@pytest.mark.asyncio
async def test_current_completed_multicycle_scope_revalidated(placement_scope,blocked_network,monkeypatch):
    s=placement_scope
    _,c,g=await setup(s,monkeypatch)
    try:
        completed=await s.session.continue_normal(authorization=True,order_budget_s=2,close_budget_s=2)
        assert completed.outcome=='REBALANCE_HANDOFF' and c.posts==2
        calls=[]
        original=runtime.project_rdp_capital
        def project(*a,**kw):
            calls.append((set(a[2]),len(kw['close_evidence'])))
            return original(*a,**kw)
        monkeypatch.setattr(runtime,'project_rdp_capital',project)
        old=s.owner.current
        count=(c.posts,len(c.cancel_calls))
        r=await s.session.continue_normal()
        assert len(r.run_order_ids)==6 and calls==[(set(r.run_order_ids),3)]
        assert r.capital_certified and not r.unresolved_exposure
        assert r.inventory==completed.inventory and r.inventory_cost==completed.inventory_cost
        assert count==(c.posts,len(c.cancel_calls)) and s.owner.current is old
        assert not r.execution_authorized and blocked_network==[]
    finally:g.close()
