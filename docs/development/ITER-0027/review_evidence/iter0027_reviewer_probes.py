import asyncio
from dataclasses import replace
from decimal import Decimal
import pytest
import polymaker.rdp_runtime as runtime
from polymaker.domain import OpenOrder, OrderState, Side
from polymaker.gateway import ExactOpenOrder
from test_rdp_placement import placement_scope, blocked_network, _complete_cycle_with_confirmed_fill

@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', ['balance_bool','balance_nan','allowance_negative','allowance_bool','allowance_nan','missing_required','duplicate_conflict','extra_malformed'])
async def test_raw_account_parse_nonempty(placement_scope, blocked_network, mutation):
    s = placement_scope
    _, g, c = await _complete_cycle_with_confirmed_fill(s, size=1.25)
    raw = g.balance_allowance_result
    if mutation == 'balance_bool': raw['balance'] = True
    elif mutation == 'balance_nan': raw['balance'] = 'NaN'
    elif mutation == 'missing_required': raw['allowances'] = {'0x' + 'b'*40: '30000000'}
    elif mutation == 'duplicate_conflict': raw['allowances'][g.spender.upper()] = '29999999'
    elif mutation == 'extra_malformed': raw['allowances']['other'] = 'not-a-number'
    else: raw['allowances'][g.spender] = {'allowance_negative':'-1','allowance_bool':True,'allowance_nan':'NaN'}[mutation]
    before = (s.owner.current, tuple(c.cancel_calls), set(s.processor._owned))
    r = await s.session.post_cycle_next_action_admission()
    assert not r.eligible and not r.execution_authorized, (mutation,r)
    assert r.reason in {'INVALID_ACCOUNT_EVIDENCE','ALLOWANCE_UNAVAILABLE','CONTRADICTORY_ALLOWANCE_EVIDENCE'}
    assert (s.owner.current,tuple(c.cancel_calls),set(s.processor._owned)) == before
    assert not s.processor._pending and blocked_network == []

@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['status','account'])
@pytest.mark.parametrize('mutation', ['stop','owner','registry','resurrection'])
async def test_each_await_revalidates(placement_scope, blocked_network, monkeypatch, phase, mutation):
    s = placement_scope
    await _complete_cycle_with_confirmed_fill(s, size=1.25)
    def change():
        if mutation == 'stop': s.session._terminal_stop_latched = True
        elif mutation == 'owner': s.owner._current = replace(s.owner.current, unresolved=True)
        elif mutation == 'registry': s.store.register_owned_order('yes-order',s.market.condition_id,'yes-token',Side.BUY,5,.4,'rdp_initial')
        else: s.store.orders['yes-order'] = OpenOrder('yes-order','yes-token',Side.BUY,.55,1,OrderState.LIVE)
    if phase == 'account': s.session.user_session.gateway.positions_hook = change
    else:
        original = runtime.read_rdp_market_status
        async def read(*a, **k):
            r = await original(*a, **k)
            change()
            return r
        monkeypatch.setattr(runtime,'read_rdp_market_status',read)
    r = await s.session.post_cycle_next_action_admission()
    assert not r.eligible and not r.execution_authorized
    assert r.reason in {'TERMINAL_STOP_LATCHED','COMPLETED_CYCLE_OWNER_MISMATCH','PLACEMENT_REGISTRY_MISMATCH','CLOSED_ORDER_LOCAL_OPEN'}
    assert blocked_network == []

@pytest.mark.asyncio
@pytest.mark.parametrize('token', ['yes-token','no-token'])
async def test_rebalance_excludes_normal_filter_reserve(placement_scope, blocked_network, monkeypatch, token):
    s = placement_scope
    await _complete_cycle_with_confirmed_fill(s,token_id=token,size=5,cash=3,allowance=3)
    original = s.session.book_filter_result
    monkeypatch.setattr(s.session,'book_filter_result',lambda:replace(original(),ready=False,rejected=True))
    r = await s.session.post_cycle_next_action_admission()
    assert r.eligible and not r.execution_authorized
    assert r.current_backing.required_backing == r.capital_admission.new_notional
    assert r.current_backing.required_backing <= Decimal('3')
    assert r.candidate.quotes[0].token_id != token
    assert blocked_network == []

@pytest.mark.asyncio
async def test_foreign_selected_sell_is_neither_cost_nor_buy_commitment(placement_scope, blocked_network):
    s = placement_scope
    _,g,_ = await _complete_cycle_with_confirmed_fill(s,size=1.25,cash=50,allowance=50)
    g.open_orders_result = [ExactOpenOrder('foreign-selected','no-token',Side.SELL,Decimal('.5'),Decimal('2'))]
    r = await s.session.post_cycle_next_action_admission()
    assert r.eligible and not r.execution_authorized
    assert r.account_buy_commitment == 0
    assert r.capital_projection.snapshot.inventory_cost == Decimal('.6875')
    assert r.capital_projection.snapshot.free_cash == Decimal('29.3125')
    assert blocked_network == []
