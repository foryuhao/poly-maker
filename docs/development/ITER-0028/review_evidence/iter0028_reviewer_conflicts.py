import asyncio
import pytest
from iter0028_reviewer_probes_v3 import setup,placement_scope,blocked_network
from polymaker.execution.rdp_placement import RDPPlacementError

@pytest.mark.asyncio
async def test_manual_operations_cannot_overlap_later_close(placement_scope,blocked_network,monkeypatch):
    s=placement_scope
    _,c,g=await setup(s,monkeypatch)
    entered,release=asyncio.Event(),asyncio.Event()
    original=g.cancel
    held=False
    async def cancel(*a,**k):
        nonlocal held
        r=await original(*a,**k)
        if not held:held=True;entered.set();await release.wait()
        return r
    g.cancel=cancel
    op=asyncio.create_task(s.session.continue_normal(authorization=True,order_budget_s=2,close_budget_s=2))
    try:
        await asyncio.wait_for(entered.wait(),2)
        attempt=s.owner.current
        other=await s.session.continue_normal(authorization=True,order_budget_s=2,close_budget_s=2)
        assert other.outcome=='DENIED'
        with pytest.raises(RDPPlacementError):await s.session.close_initial_normal_placement(attempt,deadline=asyncio.get_running_loop().time()+2)
        with pytest.raises(RDPPlacementError):await s.session.complete_initial_normal_cycle(attempt,close_budget_s=2)
        with pytest.raises(RDPPlacementError):s.session.arm_normal_continuation_placement(s.session._issued_continuation_admission)
        assert c.posts==1 and len(c.cancel_calls)==1 and s.owner.current is attempt
        release.set();r=await op
        assert r.outcome=='REBALANCE_HANDOFF' and c.posts==2
        assert blocked_network==[]
    finally:
        release.set()
        if not op.done():op.cancel()
        g.close()
