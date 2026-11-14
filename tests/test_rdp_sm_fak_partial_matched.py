"""23:05: FAK MATCHED 3.95/5 has no live remainder; prove then cover 1.05."""
import asyncio
import json
from decimal import Decimal as D

import httpx
import pytest

from polymaker.rdp_sm_execution import _terminal
from polymaker.userstream.parse import normalize_order
from test_rdp_sm_ca_entry import confirm_ca
from test_rdp_sm_ca_entry import env as env
from test_rdp_sm_ca_entry_price_filter import set_ask
from test_rdp_sm_ca_taker import setup_taker, taker_fill
from test_rdp_sm_cached_reads import attach, finish
from test_rdp_sm_m1 import CID
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_obi_execution import cleanup


def test_partial_matched_is_terminal_only_for_proven_native_fak_buy():
    row = {'id': 'owned', 'market': CID, 'asset_id': '1', 'side': 'BUY',
           'price': '.96', 'original_size': '5', 'size_matched': '3.95',
           'status': 'MATCHED', 'associate_trades': ['trade']}
    ev = normalize_order(row)
    assert not _terminal(ev)
    assert not _terminal(ev, taker_buy=True)
    assert not _terminal(ev, fak_buy=True)
    assert _terminal(ev, taker_buy=True, fak_buy=True)
    for change in [{'side': 'SELL'}, {'size_matched': '0'}, {'status': 'LIVE'}]:
        assert not _terminal(normalize_order(row | change), taker_buy=True, fak_buy=True)


@pytest.mark.parametrize('cached', [False, True])
@pytest.mark.parametrize('fault', ['none', 'live_contradiction', 'missing_trade', 'wrong_type'])
async def test_partial_matched_fak_retries_only_fully_proved_original_remainder(
    env, monkeypatch, tmp_path, cached, fault
):
    setup = await setup_taker(env, monkeypatch, tp=True)
    v, e, feed, _, normal, wire = setup[:6]
    e.cfg.rdp_sm.ba.signal_lifetime_sec = 3
    e.cfg.rdp_sm.ba.max_entry_age_ms = 1000
    e.cfg.rdp_sm.ca_matched_tp.taker_exit_on_expiry = True
    e.cfg.rdp_sm.ca_matched_tp.ttl_sec = .08
    e.cfg.rdp_sm.repair.ba_enabled = False
    v.ticks['2'] = '.01'
    feed.apply({'event_type': 'tick_size_change', 'market': CID, 'asset_id': '2',
                'old_tick_size': '.001', 'new_tick_size': '.01', 'timestamp': str(feed.wall()*1000)})
    feed.verify_ticks(v.ticks)
    set_ask(feed, '2', '.22')
    state = {'partial': None, 'exits': []}
    original_wire = type(wire).__call__

    def read(request):
        oid = state['partial']
        if oid and fault == 'live_contradiction' and request.url.path == '/data/orders':
            return httpx.Response(200, json={'data': [v.rows[oid]], 'next_cursor': 'LTE='}, request=request)
        return None

    def response(self, request):
        if self is not wire:
            return original_wire(self, request)
        oid = state['partial']
        if request.method == 'DELETE' and oid and oid in json.loads(request.content):
            # Actual venue response: repeated DELETE cannot change MATCHED to CANCELED.
            return httpx.Response(200, json={'canceled': [], 'not_canceled': {
                oid: "order can't be found - already canceled or matched"}}, request=request)
        override = read(request)
        return override if override is not None else original_wire(self, request)

    monkeypatch.setattr(type(wire), '__call__', response)
    companion = (await attach(setup, tmp_path, hook=read))[0] if cached else None
    native = e.gateway.send_prepared

    async def send(signed, **kwargs):
        first = not v.post_requests
        is_exit = not first and not signed.post_only
        if is_exit and state['exits']:
            prior = state['partial']
            assert prior in e.proofs and prior in e.terminal
            assert v.rows[prior]['status'] == 'MATCHED' and e.processor.is_fak_buy(prior)
            assert D(str(signed.quotes[0].size)) == D('1.0520')
            assert D(signed.orders[0].order.makerAmount)/1_000_000 == D('1.01')
        raw = await native(signed, **kwargs)
        oid = signed.order_ids[0]
        if first:
            taker_fill(setup, oid)
        elif signed.post_only:
            set_ask(feed, '1', '.96')
        elif is_exit:
            state['exits'].append(oid)
            if state['partial'] is None:
                state['partial'] = oid
                taker_fill(setup, oid, quantity='3.95', price='.96')
                v.rows[oid]['status'] = 'MATCHED'
                if fault == 'missing_trade':
                    v.trades.pop()
                if fault == 'wrong_type':
                    e.attempts[-1].order_type = 'FOK'
                if fault != 'none':
                    e.cfg.rdp_sm.repair.cutoff_sec = (e.official_time+e.monotonic()-e.time_anchor
                                                    - int(e.slug.rsplit('-', 1)[1])+.6)
            else:
                taker_fill(setup, oid, quantity='1.052083', price='.96')
        return raw

    monkeypatch.setattr(e.gateway, 'send_prepared', send)
    try:
        await confirm_ca(feed, '2')
        entered = await asyncio.wait_for(normal.enter(D(0), D(0), 'session:w1'), 8)
        assert entered is not None, (normal.quote_block_reason, normal.signal_block_reason)
        result, sizing = entered
        if fault != 'none':
            assert result.close_barrier == 'BLOCKED'
            assert len(state['exits']) == 1 and e.fatal
            return
        assert result.close_barrier == 'SAFE_TO_CONTINUE', result.block_reason
        assert e.fatal is None and len(state['exits']) == 2
        assert e.economic_inventory() == (D('5.002083'), D(5))
        assert sizing['ca_matched_tp']['expiry_exit_reason'] == 'HEDGED_WITH_EXCESS'
        await e.journal.flush()
        events = journal_records(e.journal.path)
        retry = next(r['data']['payload'] for r in events if r['kind'] == 'sm_ca_expiry_exit_retry')
        assert D(retry['remaining_shares']) == D('1.05')
        assert retry['terminal_proven'] and retry['previous_order_id'] == state['partial']
    finally:
        await finish(setup, companion) if companion else await cleanup(setup)
