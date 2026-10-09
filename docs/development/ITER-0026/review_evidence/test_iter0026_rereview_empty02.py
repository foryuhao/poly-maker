import time
import pytest
from test_rdp_placement import placement_scope,blocked_network,_prepare_real_admission,FakeBatchClient
from polymaker.config import Config
from polymaker.execution.gateway import ExecutionGateway
from polymaker.domain import Side,TradeState
from polymaker.userstream.parse import TradeEvent

@pytest.mark.asyncio
async def test_authoritative_empty_with_unresolved_capture_has_terminal(placement_scope,blocked_network):
    scope=placement_scope
    _prepare_real_admission(scope)
    admission=await scope.session.initial_normal_admission()
    client=FakeBatchClient([])
    def post(args,*,post_only):
        client.posts.append('POST')
        event=TradeEvent('yes-token',Side.BUY,.55,1,'unmatched-during-post',TradeState.MATCHED,time.time(),'unattributed-order',scope.market.condition_id)
        scope.processor.on_trade(event,scope.market.condition_id,received_at_us=1_800_000_000_000_000,received_monotonic_ns=2_000_000_000_000,connection_generation=4)
        return [{'success':False},{'success':False}]
    client.post_orders=post
    gateway=ExecutionGateway(Config(),paper=False)
    gateway._client=client
    gateway._derive_only=True
    scope.session.user_session.gateway=gateway
    try:
        result=await scope.session.submit_initial_normal(admission,authorization=True,order_budget_s=2,close_budget_s=2)
        print('EMPTY_WITH_CAPTURE',result.outcome,result.post_started,result.resolved_attempt.outcome,result.resolved_attempt.unresolved,result.resolved_attempt.reason,result.resolved_attempt.pending_anomalies,'terminal',scope.session.terminal_failure_result)
        assert result.outcome=='EMPTY' and result.post_started
        assert result.resolved_attempt.outcome=='UNKNOWN' and result.resolved_attempt.unresolved
        assert client.posts==['POST']
        assert scope.session.terminal_failure_result is not None
        assert scope.session._terminal_stop_latched
    finally:
        await gateway.aclose()
        await scope.session.aclose()
