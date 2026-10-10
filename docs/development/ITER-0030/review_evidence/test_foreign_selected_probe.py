import asyncio
from decimal import Decimal
from types import SimpleNamespace
import pytest
import polymaker.rdp_runtime as runtime
from polymaker.config import Config
from polymaker.execution.gateway import ExecutionGateway
from polymaker.rdp_account import RDPOrderEvidence
from test_rdp_placement import _complete_cycle_with_confirmed_fill, _IndependentCrossModeVenue
pytest_plugins = ('test_rdp_placement',)

@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['NORMAL','REBALANCE'])
async def test_foreign_selected_no_new_risk_probe(placement_scope, blocked_network, monkeypatch, mode):
    scope = placement_scope
    qty = Decimal('1.25') if mode == 'NORMAL' else Decimal('5')
    await _complete_cycle_with_confirmed_fill(scope, token_id='yes-token', size=float(qty))
    client = _IndependentCrossModeVenue(scope, [('foreign-token', 0)])
    client.confirmed['yes-token'] = [qty, qty * Decimal('0.55')]
    gateway = ExecutionGateway(Config(), paper=False)
    gateway._client = client
    async def io(fn, *args): return fn(*args)
    async def no_wait(*args, **kwargs): return None
    gateway._io = io
    gateway._order_bucket.acquire = no_wait
    gateway._cancel_bucket.acquire = no_wait
    scope.session.user_session.gateway = gateway
    scope.session.user_session.observation.account = SimpleNamespace(signer='fake-signer', funder='fake-funder')
    foreign = RDPOrderEvidence('foreign-selected-buy', 'yes-token', 'BUY', Decimal('0.50'), Decimal('1'))
    async def capture(_bootstrap, _gateway, signer, funder, *, now):
        account = client.independent_account(signer, funder, now())
        return SimpleNamespace(**{**vars(account),
            'all_open_orders': account.all_open_orders + (foreign,),
            'selected_open_orders': account.selected_open_orders + (foreign,),
            'total_open_order_count': account.total_open_order_count + 1})
    monkeypatch.setattr(runtime, '_capture_account_with_gateway', capture)
    orig_resolve = scope.owner.resolve
    def resolve(attempt, outcome):
        value = orig_resolve(attempt, outcome)
        event = client.registered.get(client.posts)
        if event is not None: event.set()
        return value
    monkeypatch.setattr(scope.owner, 'resolve', resolve)
    orig_post = client.post_orders
    def post(*args, **kwargs):
        value = orig_post(*args, **kwargs)
        scope.session._now = lambda: float(scope.bootstrap.end_epoch)
        return value
    client.post_orders = post
    try:
        run = scope.session.continue_normal if mode == 'NORMAL' else scope.session.continue_rebalance
        result = await asyncio.wait_for(run(authorization=True, order_budget_s=2, close_budget_s=2), 5)
    finally:
        gateway.close()
    print('FOREIGN_SELECTED_PROBE', mode, 'posts', client.posts, 'admissions', [(a.eligible, a.reason) for a in result.admissions], 'result', result.outcome, result.reason)
    assert blocked_network == []
    assert client.posts == 0, 'selected-market foreign BUY must prevent new-risk POST'
