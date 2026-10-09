import time
import pytest
from test_rdp_placement import placement_scope,blocked_network,_prepare_real_admission,FakeBatchClient
from polymaker.config import Config
from polymaker.execution.gateway import ExecutionGateway
from polymaker.domain import Side,TradeState
from polymaker.userstream.parse import TradeEvent

@pytest.mark.asyncio
@pytest.mark.parametrize('stage',['guard-denial','sign-failure'])
async def test_no_post_with_unresolved_capture_still_has_terminal_result(placement_scope,blocked_network,stage):
    scope=placement_scope
    _prepare_real_admission(scope)
    admission=await scope.session.initial_normal_admission()
    sent=False
    def on_create():
        nonlocal sent
        if sent:return
        sent=True
        ev=TradeEvent('yes-token',Side.BUY,.55,1,'unattributed-before-post',TradeState.MATCHED,time.time(),'unattributed-order',scope.market.condition_id)
        scope.processor.on_trade(ev,scope.market.condition_id,received_at_us=1_800_000_000_000_000,received_monotonic_ns=2_000_000_000_000,connection_generation=4)
        if stage=='sign-failure':raise RuntimeError('synthetic signing failure')
    client=FakeBatchClient([],on_create=on_create)
    gateway=ExecutionGateway(Config(),paper=False)
    gateway._client=client
    gateway._derive_only=True
    scope.session.user_session.gateway=gateway
    try:
        result=await scope.session.submit_initial_normal(admission,authorization=True,order_budget_s=2,close_budget_s=2)
        print('NO_POST_UNRESOLVED',result.outcome,result.reason,result.resolved_attempt.outcome,result.resolved_attempt.unresolved,result.resolved_attempt.pending_anomalies,'terminal',scope.session.terminal_failure_result,'post',client.posts)
        assert not result.post_started and not client.posts
        assert result.resolved_attempt.unresolved
        assert scope.session.terminal_failure_result is not None
        assert scope.session._terminal_stop_latched
    finally:
        await gateway.aclose()
        await scope.session.aclose()
