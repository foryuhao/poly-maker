"""Production FOK kill response: preserve UNKNOWN until full positive proof."""
import asyncio
from dataclasses import replace
from decimal import Decimal as D

import httpx
import pytest

from polymaker.domain import Side
from polymaker.rdp_sm_execution import Placement, WindowQuote, _canceled_submission_response
from test_rdp_sm_ca_entry import confirm_ca
from test_rdp_sm_ca_entry import env as env
from test_rdp_sm_ca_taker import setup_taker, taker_fill
from test_rdp_sm_cached_reads import attach, finish
from test_rdp_sm_m1 import CID
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_obi_execution import cleanup


def fok_reply(oid):
    return {'errorMsg': "order couldn't be fully filled. FOK orders are fully filled or killed.",
            'orderID': oid, 'takingAmount': '', 'makingAmount': '', 'status': '', 'success': True}


async def setup_case(env, monkeypatch):
    setup = await setup_taker(env, monkeypatch)
    v, _, feed = setup[:3]
    # CLOB market-info publishes one tick for both outcomes. The shared old
    # fixture starts them on different ticks; send the real WS tick transition.
    v.ticks['1'] = '.001'
    feed.apply({'event_type': 'tick_size_change', 'market': CID, 'asset_id': '1',
                'old_tick_size': '.01', 'new_tick_size': '.001',
                'timestamp': str(feed.wall()*1000)})
    feed.verify_ticks(v.ticks)
    return setup


@pytest.mark.parametrize('change', [{}, {'orderID': '0x'+'cd'*32}, {'success': False},
                                 {'status': 'matched'}, {'takingAmount': '5'}, {'makingAmount': '0'},
                                 {'tradeIDs': ['trade']}, {'transactionsHashes': ['tx']},
                                 {'errorMsg': 'unknown'}, {'orderId': 'alias'}, {'status': None}])
def test_fok_rejection_requires_exact_wire_shape_and_signed_identity(change):
    oid = '0x'+'ab'*32
    a = Placement('a', WindowQuote(CID, '1', 'YES', Side.BUY, .78, 5, 's:w1'),
                  outcome='UNKNOWN', order_id=oid, expected_order_id=oid, post_started=True,
                  raw_response=[fok_reply(oid) | change], entry_execution='taker', order_type='FOK')
    assert _canceled_submission_response(a) is (not change)
    for modified in [replace(a, order_type='FAK'), replace(a, order_type='GTC'),
                     replace(a, entry_execution='maker'), replace(a, post_started=False),
                     replace(a, expected_order_id=None), replace(a, raw_response=[fok_reply(oid)]*2)]:
        assert not _canceled_submission_response(modified)


def inject_fok_kill(setup, monkeypatch, *, contradictory_fill=False, change=None):
    v, e, _, _, _, wire = setup[:6]
    state = {'sent': False, 'failed_read': None}
    original = type(wire).__call__

    def read(request):
        if not state['sent']:
            return None
        if request.url.path == state['failed_read']:
            return httpx.Response(503, json={'error': 'offline failure'}, request=request)
        if request.url.path.startswith('/data/order/') and request.url.path.rsplit('/', 1)[-1] not in v.rows:
            v.calls.append((request.url.host, request.url.path, dict(request.url.params)))
            return httpx.Response(200, json=None, request=request)
        return None

    def response(self, request):
        if self is not wire:
            return original(self, request)
        if request.method == 'POST' and not state['sent']:
            original(self, request)
            oid = e.gateway._client.batch_attempts[0].expected_order_id
            state['sent'] = True
            if contradictory_fill:
                taker_fill(setup, oid, quantity=2)
            else:
                v.rows.pop(oid)
            return httpx.Response(200, json=[fok_reply(oid) | (change or {})], request=request)
        override = read(request)
        return override if override is not None else original(self, request)

    monkeypatch.setattr(type(wire), '__call__', response)
    return state, read


@pytest.mark.parametrize('cached', [False, True])
async def test_fok_miss_is_proven_rejected_and_allows_a_new_ca_episode(env, monkeypatch, tmp_path, cached):
    setup = await setup_case(env, monkeypatch)
    v, e, feed, _, normal = setup[:5]
    _, read = inject_fok_kill(setup, monkeypatch)
    companion = (await attach(setup, tmp_path, hook=read))[0] if cached else None
    try:
        await confirm_ca(feed)
        result, _ = await asyncio.wait_for(normal.enter(D(0), D(0), 'session:w1'), 7)
        assert result.close_barrier == 'SAFE_TO_CONTINUE', result.block_reason
        a = e.attempts[0]
        assert a.order_type == 'FOK' and a.outcome == 'REJECTED'
        assert a.order_id in e.canceled_submission_proofs and a.order_id not in e.known
        assert e.committed() == 0 and not e.confirmed()
        assert len(v.post_requests) == 1 and feed.ca_entry.consumed
        assert await normal.enter(D(0), D(0), 'session:w2') is None
        await asyncio.sleep(.45)
        await confirm_ca(feed, '2')
        second = await asyncio.wait_for(normal.enter(D(0), D(0), 'session:w3'), 7)
        assert second is not None
        assert second[0].close_barrier == 'SAFE_TO_CONTINUE', second[0].block_reason
        assert len(v.post_requests) == 2
        assert e.attempts[-1].expected_order_id != a.expected_order_id
        await e.journal.flush()
        assert sum(r['kind'] == 'sm_order_rejection_proven' for r in journal_records(e.journal.path)) == 1
    finally:
        await finish(setup, companion) if companion else await cleanup(setup)


@pytest.mark.parametrize('cached', [False, True])
@pytest.mark.parametrize('path', ['/data/orders', '/data/trades'])
async def test_incomplete_collections_keep_risk_until_later_complete_proof(env, monkeypatch, tmp_path, cached, path):
    setup = await setup_case(env, monkeypatch)
    v, e, feed, _, normal = setup[:5]
    state, read = inject_fok_kill(setup, monkeypatch)
    companion = (await attach(setup, tmp_path, hook=read))[0] if cached else None
    state['failed_read'] = path
    try:
        await confirm_ca(feed)
        result, _ = await asyncio.wait_for(normal.enter(D(0), D(0), 'session:w1'), 8)
        assert result.close_barrier == 'BLOCKED'
        assert e.attempts[0].outcome == 'UNKNOWN'
        assert e.committed() > D('2.60') and not e.canceled_submission_proofs
        state['failed_read'] = None
        async with asyncio.timeout(8):
            while not await e._verify_close(cancel=True):
                assert e.shared_reads is not None, e.reason
                version = e.shared_reads.service.latest().version
                await e.shared_reads.service.wait_after_async(version, .1)
        assert e.attempts[0].outcome == 'REJECTED' and e.committed() == 0
        assert len(v.post_requests) == 1
    finally:
        await finish(setup, companion) if companion else await cleanup(setup)


@pytest.mark.parametrize('cached', [False, True])
async def test_positive_fill_overrides_fok_miss_and_cannot_be_erased(env, monkeypatch, tmp_path, cached):
    setup = await setup_case(env, monkeypatch)
    v, e, feed, _, normal = setup[:5]
    _, read = inject_fok_kill(setup, monkeypatch, contradictory_fill=True)
    companion = (await attach(setup, tmp_path, hook=read))[0] if cached else None
    try:
        await confirm_ca(feed)
        result, _ = await asyncio.wait_for(normal.enter(D(0), D(0), 'session:w1'), 8)
        assert result.close_barrier == 'SAFE_TO_CONTINUE', result.block_reason
        assert e.attempts[0].outcome == 'VERIFIED'
        assert not e.canceled_submission_proofs and len(e.confirmed()) == 1
        assert e.economic_inventory() == (D(2), D(0)) and e.committed() > D(1)
        assert len(v.post_requests) == 1
    finally:
        await finish(setup, companion) if companion else await cleanup(setup)


async def test_late_taker_fill_revokes_previous_fok_rejection_proof(env, monkeypatch):
    setup = await setup_case(env, monkeypatch)
    v, e, feed, _, normal = setup[:5]
    inject_fok_kill(setup, monkeypatch)
    try:
        await confirm_ca(feed)
        result, _ = await asyncio.wait_for(normal.enter(D(0), D(0), 'session:w1'), 7)
        assert result.close_barrier == 'SAFE_TO_CONTINUE', result.block_reason
        a = e.attempts[0]
        oid, q = a.order_id, a.quote
        v.rows[oid] = {'id': oid, 'asset_id': q.token_id, 'market': CID, 'side': 'BUY',
                       'price': str(q.price), 'original_size': str(q.size), 'size_matched': '0',
                       'status': 'LIVE', 'associate_trades': []}
        taker_fill(setup, oid)
        with pytest.raises(ValueError):
            await e._snapshot(include_books=False)
        assert a.outcome == 'UNKNOWN' and oid in e.known
        assert not e.canceled_submission_proofs and e.committed() > D('2.60')
        assert not e.safe_to_continue
        assert await asyncio.wait_for(e._verify_close(cancel=True), 7), e.reason
        assert a.outcome == 'VERIFIED' and e.economic_inventory() == (D(5), D(0))
        assert len(v.post_requests) == 1
    finally:
        await cleanup(setup)
