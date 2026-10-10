import asyncio,time
from decimal import Decimal
import pytest
from polymaker.domain import Side
from iter0028_reviewer_probes_v3 import setup,placement_scope,blocked_network,_send_matched_then_confirmed

@pytest.mark.asyncio
async def test_clean_no_send_fresh_readmission(placement_scope,blocked_network,monkeypatch):
    s=placement_scope
    _,c,g=await setup(s,monkeypatch)
    c.on_create=lambda:s.session.public_session.service.book('yes-token').apply_delta(Side.BUY,.56,0,time.time())
    try:r=await s.session.continue_normal(authorization=True,order_budget_s=2,close_budget_s=2)
    finally:g.close()
    first=r.submissions[0]
    print('FRESH',r.outcome,c.posts,len(c.created),[(x.outcome,x.reason) for x in r.submissions],[(a.attempt_id,a.outcome,a.acknowledged_order_ids) for a in s.session.placement_owner._attempts.values()])
    assert first.outcome=='NO_SEND' and not first.post_started
    assert first.reason=='CONTINUATION_QUOTE_INTENT_CHANGED'
    assert first.resolved_attempt.outcome=='EMPTY' and not first.resolved_attempt.unresolved
    assert not s.session._terminal_stop_latched and r.outcome=='REBALANCE_HANDOFF'
    assert c.posts==2 and len(c.created)==6
    assert c.created[0].price==.55 and c.created[2].price==.54
    assert r.submissions[1].attempt.attempt_id!=first.attempt.attempt_id
    assert all(x.authorization is True for x in r.submissions)
    assert len(s.session.placement_owner._close_proofs)==3
    assert blocked_network==[]

@pytest.mark.asyncio
async def test_active_later_cycle_denial_not_capital_certified(placement_scope,blocked_network,monkeypatch):
    s=placement_scope
    _,c,g=await setup(s,monkeypatch,[('no-token',0),('no-token',5),('no-token',1.25)])
    old_sleep=s.session._sleep
    entered,release=asyncio.Event(),asyncio.Event()
    async def sleep(delay):
        await old_sleep(delay)
        if c.posts==1:
            entered.set();await release.wait()
    s.session._sleep=sleep
    op=asyncio.create_task(s.session.continue_normal(authorization=True,order_budget_s=2,close_budget_s=2))
    try:
        await asyncio.wait_for(entered.wait(),2)
        result=await s.session.continue_normal(authorization=True,order_budget_s=2,close_budget_s=2)
        current=s.session.placement_owner.current
        print('ACTIVE_CERT',result.outcome,result.reason,result.capital_certified,result.unresolved_exposure,result.run_order_ids,result.inventory_order_ids,current.released,current.closed_order_ids,current.acknowledged_order_ids)
        assert c.posts==1 and current.acknowledged_order_ids and not current.released
        assert result.outcome=='DENIED'
        assert not result.capital_certified, 'older initial close projection certifies active later resting orders'
        assert blocked_network==[]
    finally:
        release.set()
        op.cancel()
        try:await op
        except asyncio.CancelledError:pass
        g.close()

@pytest.mark.asyncio
async def test_late_fractional_cost_observed(placement_scope,blocked_network,monkeypatch):
    s=placement_scope
    _,c,g=await setup(s,monkeypatch)
    def late():
        for i in range(3):
            _send_matched_then_confirmed(s,'yes-order',trade_id=f'late-fraction-{i}',size=.1,price=.55,receipt_ns=s.session._monotonic_ns())
    c.on_create=late
    try:r=await s.session.continue_normal(authorization=True,order_budget_s=2,close_budget_s=2)
    finally:g.close()
    print('FRACTION_COST',r.inventory.qy,r.inventory_cost,r.inventory.cy,r.capital_certified)
    assert r.outcome=='TERMINAL' and not r.capital_certified
    assert float(r.inventory_cost)==pytest.approx(.8525)
    assert blocked_network==[]
