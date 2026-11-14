"""Production backlog and FAK cent rejection regressions; synthetic HTTP only."""
import json
from dataclasses import replace
from decimal import Decimal as D

import pytest
from m4_helpers import close
from test_m4_live_budget import fixture

from polymaker.m4_live_config import all_in_cost
from polymaker.m4_live_numeric import budget_quantity, fak_amounts_compatible
from polymaker.m4_live_task import FAK_AMOUNT_PRECISION_ERROR, zero_rejection

FEES = {"rate": "0.07", "exponent": 1}


@pytest.mark.parametrize("price,budget,expected", [
    (".92", "5", "5.0"), (".71", "5", "6.0"),
    (".65", "5", "7.4"), (".971", "5", "0"),
    (".92", "2.2", "2.0"), (".92", "0.9", "0.5"),
])
def test_maximum_compatible_point_one_share_quantity(price, budget, expected):
    p, cash = D(price), D(budget)
    qty = budget_quantity(p, cash, FEES)
    assert qty == D(expected) and fak_amounts_compatible(p, qty)
    assert all_in_cost(p, qty, FEES) <= cash
    # A larger .1-share candidate either exceeds cash or fails wire precision.
    candidate = qty + D('.1')
    while all_in_cost(p, candidate, FEES) <= cash:
        assert not fak_amounts_compatible(p, candidate)
        candidate += D('.1')


@pytest.mark.parametrize("at_native", [False, True])
@pytest.mark.parametrize("condition", ["queue37ms", "queue500ms", "stale", "ask_above_cap", "empty_book"])
async def test_durable_task_posts_once_despite_local_depth(tmp_path, monkeypatch, at_native, condition):
    m, e, f, c, calls, transport = await fixture(tmp_path, monkeypatch, price=".91")
    opportunity = next(iter(m.pending.values()))
    def change():
        if condition.startswith('queue'):
            f.m1_pending_received.append(c.now-(.037 if condition == 'queue37ms' else .5))
        elif condition == 'stale':
            f.books[opportunity.token_id] = replace(f.books[opportunity.token_id], received_mono=c.now-1)
        else:
            f.books[opportunity.token_id] = replace(f.books[opportunity.token_id],
                asks=() if condition == 'empty_book' else ((D('.95'),D(100)),))
    original = m.ledger.post_start
    if at_native:
        def after_commit(key, **kwargs):
            original(key, **kwargs)
            change()
        monkeypatch.setattr(m.ledger, 'post_start', after_commit)
    else:
        change()
    try:
        await m.submit(opportunity)
        assert len(calls) == 1
        signed = calls[0]['order']
        assert signed['makerAmount'] == '4600000' and signed['takerAmount'] == '5000000'
        assert m.ledger.row(opportunity.episode_id)['posts'] == 1
        assert m.ledger.row(opportunity.episode_id)['state'] == 'ACK'
        assert m.ledger.committed() <= 10
        # A successful submission with missing fill proof cannot be repeated.
        await m.submit(opportunity)
        assert len(calls) == 1
    finally:
        await close(m, e)
        transport.close()


@pytest.mark.parametrize('conflict', [
    {'orderID':'owned'}, {'status':'matched'}, {'tradeIDs':['trade']},
    {'trade_ids':['trade']}, {'transactions_hashes':['tx']},
    {'makingAmount':'0.01'}, {'takingAmount':'1'}, {'makingAmount':'NaN'},
    {'errorMsg':FAK_AMOUNT_PRECISION_ERROR+' delayed'},
])
def test_precision_error_cannot_override_positive_or_ambiguous_evidence(conflict):
    body={'success':True,'errorMsg':FAK_AMOUNT_PRECISION_ERROR,'orderID':'','status':'',
          'makingAmount':'','takingAmount':'',**conflict}
    assert zero_rejection([body],D('4.968')) is None


@pytest.mark.parametrize('success',[True,False])
def test_exact_actual_empty_precision_error_is_definitive_zero(success):
    receipt=[{'success':success,'errorMsg':FAK_AMOUNT_PRECISION_ERROR,'orderID':'',
              'status':'','makingAmount':'','takingAmount':''}]
    assert zero_rejection(receipt,D('4.968'))['kind'] == 'FAK_AMOUNT_PRECISION_REJECTED'
    # A contradictory local amount cannot be settled solely by this message.
    if success:
        assert zero_rejection(receipt,D('4.96')) is None


async def test_precision_rejection_recovered_after_restart_and_t240_without_post(tmp_path, monkeypatch):
    m, e, f, c, calls, transport = await fixture(tmp_path, monkeypatch, price=".91")
    try:
        opportunity=next(iter(m.pending.values()))
        # Reconstruct an old signed amount on a disposable offline ledger;
        # existing historic plans remain valid recovery inputs.
        key=opportunity.episode_id
        plan=m.ledger.root_plan(key)
        plan.update(quantity='5.4',principal_usdc='4.968',planned_fee_usdc='0.02783',
                    planned_cost_usdc='4.99583',commitment_usdc='5.663521')
        with m.ledger.lock,m.ledger.db:
            m.ledger.db.execute("UPDATE facts SET payload=? WHERE kind='taker_plan' AND identity=?",(json.dumps(plan),key))
        # Preserve the raw platform receipt and original incompatible signed
        # attempt, reproducing a restart boundary without a real POST.
        quote={'market_id':e.meta.condition_id,'token_id':opportunity.token_id,'outcome':opportunity.outcome,
               'side':'BUY','price':.92,'size':5.4,'window_id':key}
        m.ledger.reserve(key,spendable=40)
        m.ledger.set('next_plan:'+key,{**plan,'prior_spent_usdc':'0','prior_filled':'0','budget_basis':[]})
        oid='0x'+'a'*64
        m.ledger.prepare(key,oid,quote)
        m.ledger.post_start(key)
        m.ledger.post_receipt(oid,[{'success':True,'errorMsg':FAK_AMOUNT_PRECISION_ERROR,
                                'orderID':'','status':'','makingAmount':'','takingAmount':''}])
        e.official_time=int(e.meta.slug.rsplit('-',1)[1])+240
        e.time_anchor=c.now
        m.ledger.set('buy_closed',True)
        m.ledger.validate_recovery()
        await m.read_order(oid)
        assert m.ledger.attempt(oid)['state']=='TERMINAL'
        assert m.ledger.committed()==0 and m.ledger.row(key)['state']=='CUTOFF'
        assert not calls
        m.ledger.validate_recovery()
    finally:
        await close(m,e)
        transport.close()
