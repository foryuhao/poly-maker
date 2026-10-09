import asyncio
import threading
from dataclasses import replace
import pytest
from test_rdp_placement import placement_scope, blocked_network
from test_rdp_placement import _prepare_real_admission, _close_gateway_real_executor, _close_gateway, _accepted_rows

@pytest.mark.asyncio
@pytest.mark.parametrize('interruption', ['caller-cancel','teardown'])
async def test_complete_ack_interruption_requires_terminal(placement_scope, blocked_network, interruption):
    scope = placement_scope
    _prepare_real_admission(scope)
    admission = await scope.session.initial_normal_admission()
    gateway, client = _close_gateway_real_executor(scope, admission.candidate.quotes)
    started, release = threading.Event(), threading.Event()
    client.create_order = lambda *a, **k: {'fake_signature':'signed'}
    def post(*a, **k):
        started.set()
        assert release.wait(4)
        return _accepted_rows()
    client.post_orders = post
    submission = asyncio.create_task(scope.session.submit_initial_normal(admission, authorization=True,order_budget_s=2,close_budget_s=2))
    shutdown = None
    try:
        assert await asyncio.to_thread(started.wait, 2)
        if interruption == 'caller-cancel':
            submission.cancel()
            await asyncio.sleep(0)
        else:
            shutdown = asyncio.create_task(scope.session.aclose())
            await asyncio.sleep(0)
        release.set()
        if shutdown: await shutdown
        try: await submission
        except asyncio.CancelledError: pass
        result = scope.session._initial_submission_result
        print('COMPLETE_INTERRUPTED',interruption,result.outcome,result.interruption_reason,'terminal',scope.session.terminal_failure_result,'cancel',client.cancel_calls,'venue_status',[r['status'] for r in client.rows.values()])
        assert scope.session.terminal_failure_result is not None
        assert scope.session._terminal_stop_latched
        assert client.cancel_calls == [('no-order','yes-order')]
    finally:
        release.set()
        await gateway.aclose()
        await scope.session.aclose()

@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['failed-registration'])
async def test_terminal_registration_labels_match_actual_registry(placement_scope,blocked_network,monkeypatch,fault):
    scope=placement_scope
    _prepare_real_admission(scope)
    admission=await scope.session.initial_normal_admission()
    gateway,client=_close_gateway(scope,admission.candidate.quotes)
    client.create_order=lambda *a,**k:{'fake_signature':'signed'}
    client.post_orders=lambda *a,**k:_accepted_rows()
    gateway._derive_only=True
    if fault=='failed-registration':
        def register(*a,**k): raise RuntimeError('synthetic failed registration')
        monkeypatch.setattr(scope.processor,'register_order',register)
    else:
        original=scope.owner.resolve
        def resolve(attempt,outcome):
            resolved=original(attempt,outcome)
            record=scope.processor._owned['yes-order']
            record['submitted_price']='.97'
            raise RuntimeError('synthetic after resolver error')
        monkeypatch.setattr(scope.owner,'resolve',resolve)
    try:
        result=await scope.session.submit_initial_normal(admission,authorization=True,order_budget_s=2,close_budget_s=2)
        terminal=scope.session.terminal_failure_result
        print('REGISTRATION_LABELS',fault,'outcome',result.outcome,'registered',terminal.registered_order_ids,'unregistered',terminal.unregistered_acknowledgment_ids,'actual',[oid for oid in terminal.registered_order_ids if scope.processor.owned_record(oid) is not None],'reason',terminal.cleanup.reason)
        actual=set()
        for oid in terminal.registered_order_ids:
            record=scope.processor.owned_record(oid)
            quote=next((q for q in admission.candidate.quotes if record and q.token_id==record.get('token_id')),None)
            if record and quote and str(record.get('submitted_price'))==str(quote.price): actual.add(oid)
        assert set(terminal.registered_order_ids)==actual
        assert set(terminal.unregistered_acknowledgment_ids)=={'yes-order','no-order'}-actual
    finally:
        await gateway.aclose()
        await scope.session.aclose()

@pytest.mark.asyncio
async def test_terminal_cannot_reclose_to_certify_or_release(placement_scope,blocked_network,monkeypatch):
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
        raise RuntimeError('synthetic after resolver error')
    monkeypatch.setattr(scope.owner,'resolve',resolve)
    try:
        await scope.session.submit_initial_normal(admission,authorization=True,order_budget_s=2,close_budget_s=2)
        terminal=scope.session.terminal_failure_result
        assert terminal is not None and scope.session._terminal_stop_latched
        assert terminal.cleanup.replacement_blocked
        assert terminal.cleanup.next_mode is None
        first_calls=len(client.cancel_calls)
        subsequent=await scope.session.close_initial_normal_placement(scope.owner.current,deadline=asyncio.get_running_loop().time()+2)
        print('RECLOSE_AFTER_TERMINAL','next_mode',subsequent.next_mode,'certified',subsequent.capital.certified if subsequent.capital else None,'blocked',subsequent.replacement_blocked,'terminal_blocked',terminal.cleanup.replacement_blocked,'cancels',client.cancel_calls)
        assert subsequent.next_mode is None
        assert subsequent.capital is None or not subsequent.capital.certified
        assert subsequent.replacement_blocked
    finally:
        await gateway.aclose()
        await scope.session.aclose()
