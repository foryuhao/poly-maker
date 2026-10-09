import asyncio,threading,time
from dataclasses import replace
import pytest
from test_rdp_placement import placement_scope,blocked_network,_prepare_real_admission,_close_gateway,_close_gateway_real_executor,_accepted_rows,_compose_accepted_initial_pair
from polymaker.domain import Side
from polymaker.execution.rdp_placement import RDPPlacementError

@pytest.mark.asyncio
@pytest.mark.parametrize('mutation',['condition','token','side','price','size'])
async def test_invalid_economics_excluded_and_matching_subset_only(placement_scope,blocked_network,monkeypatch,mutation):
    scope=placement_scope
    _prepare_real_admission(scope)
    admission=await scope.session.initial_normal_admission()
    gateway,client=_close_gateway(scope,admission.candidate.quotes)
    client.create_order=lambda *a,**k:{'fake_signature':'signed'}
    client.post_orders=lambda *a,**k:_accepted_rows()
    gateway._derive_only=True
    original=scope.owner.resolve
    yes=next(q for q in admission.candidate.quotes if q.token_id=='yes-token')
    def resolve(attempt,outcome):
        original(attempt,outcome)
        scope.store.register_owned_order('yes-order',
            'foreign-condition' if mutation=='condition' else scope.market.condition_id,
            'foreign-token' if mutation=='token' else 'yes-token',
            Side.SELL if mutation=='side' else Side.BUY,
            9 if mutation=='size' else yes.size,
            .97 if mutation=='price' else yes.price,'rdp_initial')
        raise RuntimeError('synthetic resolver contradiction')
    monkeypatch.setattr(scope.owner,'resolve',resolve)
    try:
        result=await scope.session.submit_initial_normal(admission,authorization=True,order_budget_s=2,close_budget_s=2)
        terminal=scope.session.terminal_failure_result
        assert terminal.registered_order_ids==('no-order',)
        assert terminal.unregistered_acknowledgment_ids==('yes-order',)
        assert terminal.cleanup.barrier is not None
        assert client.cancel_calls==[('no-order',)]
        assert client.rows['yes-order']['status']=='LIVE'
        assert terminal.unresolved_exposure and not terminal.registered_reconciliation_complete
        assert terminal.cleanup.next_mode is None and terminal.cleanup.replacement_blocked
        assert terminal.confirmed_inventory_order_ids==('no-order',)
        assert scope.session.terminal_failure_result is terminal
    finally:
        await gateway.aclose()
        await scope.session.aclose()

@pytest.mark.asyncio
async def test_every_post_stop_public_operation_preserves_owner_and_cached_evidence(placement_scope,blocked_network,monkeypatch):
    scope=placement_scope
    _prepare_real_admission(scope)
    admission=await scope.session.initial_normal_admission()
    gateway,client=_close_gateway(scope,admission.candidate.quotes)
    client.create_order=lambda *a,**k:{'fake_signature':'signed'}
    client.post_orders=lambda *a,**k:_accepted_rows()
    gateway._derive_only=True
    original=scope.owner.resolve
    def resolve(attempt,outcome):
        original(attempt,outcome)
        raise RuntimeError('synthetic resolver failure')
    monkeypatch.setattr(scope.owner,'resolve',resolve)
    try:
        submission=await scope.session.submit_initial_normal(admission,authorization=True,order_budget_s=2,close_budget_s=2)
        terminal=scope.session.terminal_failure_result
        current=scope.owner.current
        before=(client.open_count,client.get_order_count,client.get_trades_count,tuple(client.cancel_calls))
        denied=await scope.session.initial_normal_admission()
        assert not denied.eligible and denied.reason=='TERMINAL_STOP_LATCHED'
        with pytest.raises(RDPPlacementError,match='TERMINAL_STOP_LATCHED'):scope.session.arm_initial_normal_placement(admission)
        assert await scope.session.submit_initial_normal(admission,authorization=True,order_budget_s=2,close_budget_s=2) is submission
        decision=await scope.session.evaluate_initial_prefill(current)
        assert decision.action=='CLOSE' and decision.reason=='TERMINAL_STOP_LATCHED'
        for call in (scope.session.complete_initial_normal_cycle(current,close_budget_s=2),scope.session.complete_initial_fill_window(current,close_budget_s=2),scope.session.close_initial_normal_placement(current,deadline=asyncio.get_running_loop().time()+2)):
            with pytest.raises(RDPPlacementError,match='TERMINAL_STOP_LATCHED'):await call
        assert scope.session._initial_submission_permission_failure(scope.session._initial_submission_operation)=='TERMINAL_STOP_LATCHED'
        assert scope.session.initial_normal_submit_guard(admission,submission.attempt)() is False
        assert before==(client.open_count,client.get_order_count,client.get_trades_count,tuple(client.cancel_calls))
        assert current is scope.owner.current and current.replacement_blocked
        assert terminal is scope.session.terminal_failure_result
        assert terminal.cleanup.next_mode is None and (terminal.cleanup.capital is None or not terminal.cleanup.capital.certified)
    finally:
        await gateway.aclose()
        await scope.session.aclose()

@pytest.mark.asyncio
async def test_inflight_close_latch_cannot_certify_or_release(placement_scope,blocked_network):
    scope=placement_scope
    attempt,quotes=await _compose_accepted_initial_pair(scope)
    gateway,client=_close_gateway(scope,quotes)
    client.open_hook=lambda count: scope.session._latch_terminal_stop('SYNTHETIC_UNRELIABLE_INPUT') if count==2 else None
    try:
        result=await scope.session.close_initial_normal_placement(attempt,deadline=asyncio.get_running_loop().time()+2)
        assert result.barrier_complete
        assert result.next_mode is None and result.replacement_blocked
        assert result.capital is None or not result.capital.certified
        assert scope.owner.current.replacement_blocked
        assert client.cancel_calls==[('no-order','yes-order')]
    finally:
        await gateway.aclose()
        await scope.session.aclose()

@pytest.mark.asyncio
@pytest.mark.parametrize('method',['open_orders','cancel','get_order','get_trades'])
async def test_teardown_owns_actual_cleanup_until_sdk_release(placement_scope,blocked_network,method):
    scope=placement_scope
    _prepare_real_admission(scope)
    admission=await scope.session.initial_normal_admission()
    gateway,client=_close_gateway_real_executor(scope,admission.candidate.quotes)
    client.rows.pop('no-order')
    client.create_order=lambda *a,**k:{'fake_signature':'signed'}
    client.post_orders=lambda *a,**k:[{'success':True,'orderID':'yes-order'},{'success':None,'orderID':'seen-no'}]
    entered,release=threading.Event(),threading.Event()
    trace=[]
    def held():
        trace.append('sdk-enter')
        entered.set()
        assert release.wait(4)
        trace.append('sdk-finish')
    if method=='open_orders':client.open_hook=lambda count:held() if count==2 else None
    elif method=='cancel':client.cancel_hook=held
    elif method=='get_order':client.get_order_hook=lambda *a:held()
    else:client.get_trades_hook=lambda *a:held()
    async def user_close():
        trace.append('user-close')
        assert release.is_set()
        await gateway.aclose()
    scope.session.user_session.aclose=user_close
    task=asyncio.create_task(scope.session.submit_initial_normal(admission,authorization=True,order_budget_s=2,close_budget_s=2))
    shutdown=None
    try:
        assert await asyncio.to_thread(entered.wait,2)
        shutdown=asyncio.create_task(scope.session.aclose())
        await asyncio.sleep(0)
        shutdown.cancel()
        await asyncio.sleep(0)
        shutdown.cancel()
        await asyncio.sleep(0)
        assert not shutdown.done() and 'user-close' not in trace
        release.set()
        with pytest.raises(asyncio.CancelledError):await shutdown
        await task
        assert scope.session.terminal_failure_result is not None
        assert trace.index('sdk-finish')<trace.index('user-close')
    finally:
        release.set()
        if not task.done():await task
        if shutdown is not None and not shutdown.done():
            try:await shutdown
            except asyncio.CancelledError:pass
        await gateway.aclose()
        await scope.session.aclose()
