import asyncio,time
from dataclasses import replace
import pytest
from test_rdp_placement import placement_scope,blocked_network
from test_rdp_placement import _prepare_real_admission,_close_gateway,_compose_accepted_initial_pair,_rest_trade
from polymaker.domain import Side,TradeState
from polymaker.userstream.parse import TradeEvent

@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['cancel-exception','cancel-noop','open-read','malformed-open','order-read','trade-read','pending','failed','quarantined','unadmitted','registry','foreign'])
async def test_actual_partial_submission_failure_matrix(placement_scope,blocked_network,monkeypatch,fault):
    scope=placement_scope
    _prepare_real_admission(scope)
    admission=await scope.session.initial_normal_admission()
    gateway,client=_close_gateway(scope,admission.candidate.quotes)
    client.rows.pop('no-order')
    client.posts=[]
    def post(*a,**k):
        client.posts.append('POST')
        return [{'success':True,'orderID':'yes-order'},{'success':None,'orderID':'seen-no'}]
    client.create_order=lambda *a,**k:{'fake_signature':'signed'}
    client.post_orders=post
    gateway._derive_only=True
    if fault=='cancel-exception':client.cancel_error=RuntimeError('synthetic')
    if fault=='cancel-noop':client.cancel_mutates=False
    if fault=='open-read':client.read_error=RuntimeError('synthetic')
    if fault=='malformed-open':client.rows['yes-order'].pop('original_size')
    if fault=='order-read':client.get_order_error=RuntimeError('synthetic')
    if fault=='trade-read':client.trades_error=RuntimeError('synthetic')
    if fault=='foreign':
        client.rows['manual-no']={**client.rows['yes-order'],'id':'manual-no','asset_id':'no-token'}
    if fault=='quarantined':
        client.rows['yes-order']['size_matched']='1'
        client.trades=[_rest_trade('yes-order','foreign-token','.55','1')]
    if fault in ('pending','failed','unadmitted','registry'):
        original=scope.owner.resolve
        def resolve(attempt,outcome):
            resolved=original(attempt,outcome)
            if fault=='registry':
                scope.store.register_owned_order('yes-order',scope.market.condition_id,'yes-token',Side.BUY,5,.97,'rdp_initial')
            elif fault=='quarantined':scope.processor.quarantined_orders.add('yes-order')
            else:
                status={'pending':TradeState.MATCHED,'failed':TradeState.FAILED,'unadmitted':TradeState.CONFIRMED}[fault]
                event=TradeEvent('yes-token',Side.BUY,admission.candidate.quotes[0].price,1,'matrix-fill',status,time.time(),'yes-order',scope.market.condition_id)
                scope.processor.on_trade(event,scope.market.condition_id,received_at_us=1_800_000_000_000_000,received_monotonic_ns=2_000_000_000_000,connection_generation=4)
            return resolved
        monkeypatch.setattr(scope.owner,'resolve',resolve)
    try:
        submission=await scope.session.submit_initial_normal(admission,authorization=True,order_budget_s=2,close_budget_s=2)
        terminal=scope.session.terminal_failure_result
        assert terminal is not None and scope.session._terminal_stop_latched
        assert terminal.unresolved_exposure and terminal.human_attention_required
        assert not terminal.registered_reconciliation_complete
        assert terminal.cleanup.next_mode is None
        assert terminal.cleanup.capital is None or not terminal.cleanup.capital.certified
        assert terminal.cleanup.replacement_blocked
        assert client.posts==['POST']
        assert all(set(ids)<= {'yes-order'} for ids in client.cancel_calls)
        if fault=='foreign':assert client.rows['manual-no']['status']=='LIVE'
        cached=await scope.session.submit_initial_normal(admission,authorization=True,order_budget_s=2,close_budget_s=2)
        assert cached is submission and scope.session.terminal_failure_result is terminal
    finally:
        await gateway.aclose()
        await scope.session.aclose()

@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['sleeper','order-read','public-health'])
async def test_unreliable_complete_cycle_terminal_cleanup_cached(placement_scope,blocked_network,fault):
    scope=placement_scope
    attempt,quotes=await _compose_accepted_initial_pair(scope)
    gateway,client=_close_gateway(scope,quotes)
    if fault=='sleeper':
        async def failure(*a):raise RuntimeError('synthetic')
        scope.session._sleep=failure
    elif fault=='order-read':client.read_error=RuntimeError('synthetic')
    else:scope.session.public_session.failure_category=lambda *a:'PUBLIC_STALE'
    try:
        result=await scope.session.complete_initial_normal_cycle(attempt,close_budget_s=2)
        terminal=scope.session.terminal_failure_result
        assert terminal is not None and scope.session._terminal_stop_latched
        assert terminal.cleanup is result.close_result
        before=(client.open_count,client.get_order_count,client.get_trades_count,len(client.cancel_calls))
        cached=await scope.session.complete_initial_normal_cycle(attempt,close_budget_s=2)
        assert cached is result
        assert before==(client.open_count,client.get_order_count,client.get_trades_count,len(client.cancel_calls))
        assert terminal.cleanup.next_mode is None and terminal.cleanup.replacement_blocked
    finally:
        await gateway.aclose()
        await scope.session.aclose()
