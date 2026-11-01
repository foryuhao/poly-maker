"""22:10 production FAK no-match is a proved kill, not transport UNKNOWN."""
import asyncio
import json
from dataclasses import replace
from decimal import Decimal as D

import httpx
import pytest

from polymaker.domain import Side
from polymaker.rdp_sm_execution import Placement, WindowQuote, _canceled_submission_response
from test_rdp_sm_ca_entry import confirm_ca
from test_rdp_sm_ca_entry import env as env
from test_rdp_sm_ca_entry_price_filter import set_ask
from test_rdp_sm_ca_taker import setup_taker, taker_fill
from test_rdp_sm_cached_reads import attach, finish
from test_rdp_sm_m1 import CID
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_obi_execution import cleanup


def fak_reply(oid):
    return {'errorMsg': 'no orders found to match with FAK order. FAK orders are partially filled or killed if no match is found.',
            'orderID': oid, 'takingAmount': '', 'makingAmount': '', 'status': '', 'success': True}


@pytest.mark.parametrize('change', [{}, {'orderID': '0x'+'cd'*32}, {'success': False},
                                 {'status': 'matched'}, {'takingAmount': '0'}, {'makingAmount': '0'},
                                 {'tradeIDs': ['trade']}, {'transactionsHashes': ['tx']},
                                 {'errorMsg': 'no match'}, {'orderId': 'alias'}, {'status': None}])
def test_fak_kill_requires_exact_type_identity_and_empty_execution_fields(change):
    oid = '0x'+'ab'*32
    a = Placement('a', WindowQuote(CID, '2', 'NO', Side.BUY, .14, 7.5, 's:w1:ca-expiry-exit'),
                  outcome='UNKNOWN', order_id=oid, expected_order_id=oid, post_started=True,
                  raw_response=[fak_reply(oid) | change], entry_execution='taker', order_type='FAK')
    assert _canceled_submission_response(a) is (not change)
    for other in [replace(a, order_type='FOK'), replace(a, order_type='GTC'),
                  replace(a, entry_execution='maker'), replace(a, post_started=False),
                  replace(a, expected_order_id=None), replace(a, raw_response=[fak_reply(oid)]*2)]:
        assert not _canceled_submission_response(other)


@pytest.mark.parametrize('cached', [False, True])
@pytest.mark.parametrize('fault', ['none', 'multiple_kills_reprice', 'orders_failed', 'trades_failed', 'contradictory_fill', 'ambiguous'])
async def test_expiry_fak_miss_retries_only_after_full_rejection_or_actual_fill_proof(
    env, monkeypatch, tmp_path, cached, fault
):
    setup = await setup_taker(env, monkeypatch, tp=True)
    v, e, feed, _, normal, wire = setup[:6]
    v.ticks['2'] = '.01'
    feed.apply({'event_type': 'tick_size_change', 'market': CID, 'asset_id': '2',
                'old_tick_size': '.001', 'new_tick_size': '.01', 'timestamp': str(feed.wall()*1000)})
    feed.verify_ticks(v.ticks)
    e.cfg.rdp_sm.ba.signal_lifetime_sec = 3
    e.cfg.rdp_sm.ba.max_entry_age_ms = 1000
    e.cfg.rdp_sm.ca_matched_tp.enabled = True
    e.cfg.rdp_sm.ca_matched_tp.taker_exit_on_expiry = True
    e.cfg.rdp_sm.ca_matched_tp.profit_per_share = D('.05')
    e.cfg.rdp_sm.ca_matched_tp.ttl_sec = .06
    e.cfg.rdp_sm.repair.ba_enabled = False
    set_ask(feed, '1', '.82')
    set_ask(feed, '2', '.14')
    state = {'oid': None, 'killed_ids': []}
    original_wire = type(wire).__call__

    def read(request):
        if not state['oid']:
            return None
        failed_path = {'orders_failed': '/data/orders', 'trades_failed': '/data/trades'}.get(fault)
        if request.url.path == failed_path:
            return httpx.Response(503, json={'error': 'offline failed collection'}, request=request)
        if (request.url.path.startswith('/data/order/')
                and request.url.path.rsplit('/', 1)[-1] in state['killed_ids']
                and request.url.path.rsplit('/', 1)[-1] not in v.rows):
            return httpx.Response(200, json=None, request=request)
        return None

    def response(self, request):
        if self is not wire:
            return original_wire(self, request)
        if request.method == 'POST' and len(state['killed_ids']) < (3 if fault == 'multiple_kills_reprice' else 1):
            rows = json.loads(request.content)
            if rows[0]['orderType'] == 'FAK':
                original_wire(self, request)
                oid = e.gateway._client.batch_attempts[0].expected_order_id
                state['oid'] = oid
                state['killed_ids'].append(oid)
                if fault == 'contradictory_fill':
                    taker_fill(setup, oid, quantity='.5', price='.14')
                else:
                    v.rows.pop(oid)
                reply = fak_reply(oid)
                if fault == 'ambiguous':
                    reply['takingAmount'] = '0'
                if fault in {'orders_failed', 'trades_failed', 'ambiguous'}:
                    # Persistent uncertainty holds the same owner until the
                    # market cutoff; keep that wait short in this fixture.
                    e.cfg.rdp_sm.repair.cutoff_sec = (e.official_time+e.monotonic()-e.time_anchor
                                                    - int(e.slug.rsplit('-', 1)[1])+.3)
                if fault == 'multiple_kills_reprice':
                    set_ask(feed, '2', '.16')
                return httpx.Response(200, json=[reply], request=request)
        override = read(request)
        return override if override is not None else original_wire(self, request)

    monkeypatch.setattr(type(wire), '__call__', response)
    companion = (await attach(setup, tmp_path, hook=read))[0] if cached else None
    native = e.gateway.send_prepared

    async def send(signed, **kwargs):
        first = not v.post_requests
        raw = await native(signed, **kwargs)
        oid = signed.order_ids[0]
        if first:
            taker_fill(setup, oid)
        elif not signed.post_only and oid not in state['killed_ids']:
            for killed_id in state['killed_ids']:
                prior = next(a for a in e.attempts if a.order_id == killed_id)
                if fault == 'contradictory_fill':
                    assert prior.outcome == 'VERIFIED' and prior.order_id in e.proofs
                else:
                    assert prior.outcome == 'REJECTED' and prior.order_id not in e.known
                    assert prior.order_id in e.canceled_submission_proofs
            taker_fill(setup, oid)
        return raw

    monkeypatch.setattr(e.gateway, 'send_prepared', send)
    try:
        await confirm_ca(feed)
        result, sizing = await asyncio.wait_for(normal.enter(D(0), D(0), 'session:w1'), 8)
        blocked = fault in {'orders_failed', 'trades_failed', 'ambiguous'}
        assert result.close_barrier == ('BLOCKED' if blocked else 'SAFE_TO_CONTINUE'), result.block_reason
        assert len(v.post_requests) == (3 if blocked else 6 if fault == 'multiple_kills_reprice' else 4)
        killed = next(a for a in e.attempts if a.order_id == state['oid'])
        assert killed.order_type == 'FAK'
        if blocked:
            assert killed.outcome == 'UNKNOWN' and killed.order_id in e.known
            assert not e.canceled_submission_proofs
            assert e.committed() > D('5.15')
        else:
            expected_no = (D('7.6428') if fault == 'contradictory_fill' else
                           D('6.25') if fault == 'multiple_kills_reprice' else D('7.1428'))
            assert e.economic_inventory() == (D(5), expected_no)
            if fault == 'multiple_kills_reprice':
                assert len(set(state['killed_ids'])) == 3
                assert e.attempts[-1].quote.price == .16 and e.attempts[-1].quote.size == 6.25
            assert sizing['ca_matched_tp']['expiry_exit_reason'] == 'HEDGED_WITH_EXCESS'
            await e.journal.flush()
            proofs = [r for r in journal_records(e.journal.path) if r['kind'] == 'sm_order_rejection_proven']
            assert len(proofs) == (0 if fault == 'contradictory_fill' else len(state['killed_ids']))
            if proofs:
                assert proofs[0]['data']['payload']['reason'] == 'FAK_NOT_FILLED'
    finally:
        await finish(setup, companion) if companion else await cleanup(setup)
