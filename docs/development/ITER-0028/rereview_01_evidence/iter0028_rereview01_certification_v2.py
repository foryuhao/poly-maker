import asyncio
import pytest
from decimal import Decimal
from polymaker.domain import Side
from polymaker.state.tracker import OrderEvent
from polymaker.risk.rdp_capital import project_rdp_capital
from iter0028_reviewer_probes_v3 import setup,placement_scope,blocked_network

@pytest.mark.asyncio
async def test_concurrent_denial_does_not_certify_prior_scope(placement_scope,blocked_network,monkeypatch):
    s=placement_scope
    initial,c,g=await setup(s,monkeypatch,[('no-token',0)])
    entered,release=asyncio.Event(),asyncio.Event()
    old_sleep=s.session._sleep
    async def sleep(delay):
        await old_sleep(delay)
        if c.posts==1:entered.set();await release.wait()
    s.session._sleep=sleep
    op=asyncio.create_task(s.session.continue_normal(authorization=True,order_budget_s=2,close_budget_s=2))
    try:
        await asyncio.wait_for(entered.wait(),2)
        current=s.owner.current
        old_snapshot=initial.close_result.capital.snapshot
        current_ids=set(s.owner.registered_run_order_ids)
        snapshot_orders=list(s.store.orders_for('yes-token'))+list(s.store.orders_for('no-token'))
        fresh=project_rdp_capital(s.market,s.processor,current_ids,snapshot_orders,market_state_reliable=True,order_state_reliable=True,close_evidence=s.owner.close_proofs)
        result=await s.session.continue_normal(authorization=True,order_budget_s=2,close_budget_s=2)
        print('CURRENT_VS_OLD',result.capital_certified,result.unresolved_exposure,result.run_order_ids,current.closed_order_ids,current.replacement_blocked,old_snapshot.run_order_ids,old_snapshot.reserved_cash,fresh.certified,fresh.snapshot.reserved_cash if fresh.snapshot else fresh.reason)
        assert c.posts==1 and current.closed_order_ids==() and current.replacement_blocked
        assert fresh.certified and fresh.snapshot.reserved_cash==Decimal('5.3125')
        assert old_snapshot.reserved_cash==0 and set(old_snapshot.run_order_ids)!=current_ids
        assert result.outcome=='DENIED' and not result.execution_authorized
        assert not result.capital_certified, 'initial closed two-order snapshot falsely certifies four-order live scope'
        assert blocked_network==[]
    finally:
        release.set();op.cancel()
        try:await op
        except asyncio.CancelledError:pass
        g.close()

@pytest.mark.asyncio
async def test_resurrected_order_denial_not_capital_certified(placement_scope,blocked_network,monkeypatch):
    s=placement_scope
    _,c,g=await setup(s,monkeypatch)
    try:
        s.processor.on_order(OrderEvent('no-order','no-token',Side.BUY,.41,5,False,5,0,'LIVE',(),s.market.condition_id),s.market.condition_id)
        ids=set(s.owner.registered_run_order_ids)
        fresh=project_rdp_capital(s.market,s.processor,ids,[],market_state_reliable=True,order_state_reliable=True,close_evidence=s.owner.close_proofs)
        result=await s.session.continue_normal()
        print('RESURRECTED',result.outcome,result.capital_certified,result.unresolved_exposure,fresh.certified,fresh.reason,len(s.store.orders_for('no-token')))
        assert not fresh.certified and fresh.reason=='CLOSED_ORDER_LOCAL_OPEN'
        assert result.outcome=='DENIED' and c.posts==0
        assert not result.capital_certified, 'locally resurrected order invalidates retained close despite unchanged inventory'
        assert blocked_network==[]
    finally:g.close()
