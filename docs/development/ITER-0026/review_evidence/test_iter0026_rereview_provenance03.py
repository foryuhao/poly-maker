import time
from dataclasses import replace
import pytest
from test_rdp_placement import placement_scope,blocked_network,_prepare_real_admission,FakeBatchClient
from polymaker.config import Config
from polymaker.execution.gateway import ExecutionGateway
from polymaker.domain import Side,TradeState
from polymaker.userstream.parse import TradeEvent

@pytest.mark.asyncio
async def test_actual_capture_trigger_ignores_copies_stale_attempts_and_reliable_empty(placement_scope,blocked_network):
    scope=placement_scope
    _prepare_real_admission(scope)
    admission=await scope.session.initial_normal_admission()
    client=FakeBatchClient([])
    def post(*a,**k):
        event=TradeEvent('yes-token',Side.BUY,.55,1,'provenance-unmatched',TradeState.MATCHED,time.time(),'unattributed-order',scope.market.condition_id)
        scope.processor.on_trade(event,scope.market.condition_id,received_at_us=1_800_000_000_000_000,received_monotonic_ns=2_000_000_000_000,connection_generation=4)
        return [{'success':False},{'success':False}]
    client.post_orders=post
    gateway=ExecutionGateway(Config(),paper=False)
    gateway._client=client
    gateway._derive_only=True
    scope.session.user_session.gateway=gateway
    try:
        result=await scope.session.submit_initial_normal(admission,authorization=True,order_budget_s=2,close_budget_s=2)
        owner=scope.owner.current
        assert scope.session._matching_unresolved_submission_owner(result) is owner
        assert scope.session._submission_terminal_cause(result)=='PENDING_CAPTURE_ANOMALY'
        copy=replace(result,resolved_attempt=replace(owner))
        stale=replace(result,attempt=replace(result.attempt,attempt_id=result.attempt.attempt_id+1))
        assert scope.session._matching_unresolved_submission_owner(copy) is None
        assert scope.session._matching_unresolved_submission_owner(stale) is None
        assert not scope.session._submission_requires_terminal_stop(copy)
        assert not scope.session._submission_requires_terminal_stop(stale)
        assert not scope.session._submission_requires_terminal_stop(replace(result,authorization_granted=False))
        interrupted=replace(result,interruption_reason='CALLER_CANCELLED')
        assert scope.session._submission_terminal_cause(interrupted)=='CALLER_CANCELLED'
        # Exact current identity alone cannot make reliable EMPTY terminal.
        scope.owner._current=replace(owner,outcome='EMPTY',unresolved=False,replacement_blocked=False)
        clean=replace(result,resolved_attempt=scope.owner.current)
        assert scope.session._matching_unresolved_submission_owner(clean) is None
        assert not scope.session._submission_requires_terminal_stop(clean)
        scope.owner._current=owner
    finally:
        await gateway.aclose()
        await scope.session.aclose()
