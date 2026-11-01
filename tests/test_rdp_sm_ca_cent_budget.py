"""Real signed expiry orders budget cents, including the 22:20 price improvement."""
import asyncio
import hashlib
from dataclasses import replace
from decimal import Decimal as D

import pytest

from polymaker.domain import Side
from polymaker.rdp_sm_ca_taker import taker_event, validate_buy_fills
from polymaker.userstream.parse import parse_trade
from test_rdp_sm_ca_entry import confirm_ca
from test_rdp_sm_ca_entry import env as env
from test_rdp_sm_ca_entry_price_filter import set_ask
from test_rdp_sm_ca_taker import setup_taker, taker_fill
from test_rdp_sm_cached_reads import attach, finish
from test_rdp_sm_m1 import CID
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_obi_execution import cleanup


@pytest.mark.parametrize('cached', [False, True])
@pytest.mark.parametrize('mode', ['at_ask', 'improved', 'partial', 'fine_tick'])
async def test_actual_remaining_cash_budget_signing_and_fill_reconciliation(
    env, monkeypatch, tmp_path, cached, mode
):
    setup = await setup_taker(env, monkeypatch, tp=True)
    v, e, feed, _, normal = setup[:5]
    v.ticks['2'] = '.01'
    feed.apply({'event_type': 'tick_size_change', 'market': CID, 'asset_id': '2',
                'old_tick_size': '.001', 'new_tick_size': '.01', 'timestamp': str(feed.wall()*1000)})
    feed.verify_ticks(v.ticks)
    e.cfg.rdp_sm.ba.signal_lifetime_sec = 3
    e.cfg.rdp_sm.ba.max_entry_age_ms = 1000
    e.cfg.rdp_sm.ca_matched_tp.taker_exit_on_expiry = True
    e.cfg.rdp_sm.ca_matched_tp.ttl_sec = .08
    e.cfg.rdp_sm.repair.ba_enabled = False
    set_ask(feed, '2', '.73')
    companion = (await attach(setup, tmp_path))[0] if cached else None
    native = e.gateway.send_prepared
    exits = []

    async def send(signed, **kwargs):
        first = not v.post_requests
        if not first and not signed.post_only:
            a = e.attempts[-1]
            amount = D(signed.orders[0].order.makerAmount)/1_000_000
            quantity = D(signed.orders[0].order.takerAmount)/1_000_000
            expected = D(1) if mode == 'fine_tick' or exits else D('1.37')
            assert amount == expected == a.principal_limit
            assert a.maximum == amount*D('1.14')+D('.000001')
            assert quantity == D(str(a.quote.size)) and quantity % D('.0001') == 0
            if mode != 'fine_tick' and not exits:
                assert quantity == D('5.0740')  # No six-share/$1.62 inflation.
            if exits:
                assert exits[-1] in e.proofs and exits[-1] in e.terminal
            exits.append(signed.order_ids[0])
        raw = await native(signed, **kwargs)
        oid = signed.order_ids[0]
        if first:
            taker_fill(setup, oid, quantity='5.069442', price='.72')
            v.rows[oid]['status'] = 'MATCHED'
        elif signed.post_only:
            if mode == 'fine_tick':
                v.ticks['1'] = '.001'
                feed.apply({'event_type': 'tick_size_change', 'market': CID, 'asset_id': '1',
                            'old_tick_size': '.01', 'new_tick_size': '.001',
                            'timestamp': str(feed.wall()*1000)})
                feed.verify_ticks(v.ticks)
            feed.apply({'event_type': 'book', 'market': CID, 'asset_id': '1',
                        'timestamp': str(feed.wall()*1000),
                        'bids': [{'price': '.002' if mode == 'fine_tick' else '.26', 'size': '1000'}],
                        'asks': [{'price': '.003' if mode == 'fine_tick' else '.27', 'size': '1000'}]})
        else:
            qty, price = ('333.333333', '.003') if mode == 'fine_tick' else (
                ('2', '.27') if mode == 'partial' and len(exits) == 1 else
                ('3.846154', '.26') if mode == 'partial' else
                ('5.269231', '.26') if mode == 'improved' else ('5.074074', '.27'))
            taker_fill(setup, oid, quantity=qty, price=price)
        return raw

    monkeypatch.setattr(e.gateway, 'send_prepared', send)
    try:
        await confirm_ca(feed, '2')
        entered = await asyncio.wait_for(normal.enter(D(0), D(0), 'session:w1'), 8)
        assert entered is not None, (normal.quote_block_reason, normal.signal_block_reason)
        result, sizing = entered
        assert result.close_barrier == 'SAFE_TO_CONTINUE', result.block_reason
        assert e.fatal is None and not e.processor.quarantined_orders
        expected = {'at_ask': '5.074074', 'improved': '5.269231',
                    'partial': '5.846154', 'fine_tick': '333.333333'}[mode]
        assert e.economic_inventory() == (D(expected), D('5.069442'))
        assert len(exits) == (2 if mode == 'partial' else 1)
        assert sizing['ca_matched_tp']['expiry_exit_reason'] == 'HEDGED_WITH_EXCESS'
        assert all(a.quote.side is Side.BUY for a in e.attempts)
        await e.journal.flush()
        records = journal_records(e.journal.path)
        signals = [r['data']['payload'] for r in records if r['kind'] == 'sm_ca_expiry_exit_signal']
        assert D(signals[-1]['original_remaining_shares']) == (
            D('3.069442') if mode == 'partial' else D('5.069442'))
        prepared = [r['data']['payload'] for r in records if r['kind'] == 'sm_prepared']
        assert any(c.get('buy_amount_usdc') in {'1.37', '1'} for p in prepared for c in p['candidates'])
    finally:
        await finish(setup, companion) if companion else await cleanup(setup)


def test_exact_signed_cents_are_the_cumulative_fill_bound():
    spec = {'market_id': CID, 'token_id': '1', 'opposite_token': '2', 'price': D('.27'),
            'quantity': D('5.0740'), 'buy_amount_usdc': D('1.37'),
            'fees': {'rate': '.07', 'exponent': 1}}

    def event(tid, qty):
        return taker_event(parse_trade({'id': tid, 'status': 'CONFIRMED', 'market': CID,
            'trader_side': 'TAKER', 'taker_order_id': 'owned', 'asset_id': '1', 'side': 'BUY',
            'price': '.27', 'size': qty, 'maker_orders': [{'order_id': 'maker-'+tid,
                'asset_id': '1', 'side': 'SELL', 'price': '.27', 'matched_amount': qty}]}), spec)

    # $1.37 is slightly above nominal 5.0740 * .27. Keep the exact signed budget.
    filled = event('full', '5.074074')
    assert filled.principal_usdc == '1.370000'
    validate_buy_fills([filled], spec)
    with pytest.raises(ValueError, match='CA_TAKER_FILL_EXCEEDS_LIMIT'):
        validate_buy_fills([filled, event('extra', '.01')], spec)
    with pytest.raises(ValueError, match='CA_TAKER_FILL_EXCEEDS_LIMIT'):
        event('overspend', '5.111111')


@pytest.mark.parametrize('field', ['makerAmount', 'takerAmount'])
async def test_native_post_rejects_changed_budget_or_quantity(env, monkeypatch, field):
    setup = await setup_taker(env, monkeypatch, tp=True)
    v, e, feed, _, normal = setup[:5]
    e.cfg.rdp_sm.ba.signal_lifetime_sec = 3
    e.cfg.rdp_sm.ba.max_entry_age_ms = 1000
    e.cfg.rdp_sm.ca_matched_tp.taker_exit_on_expiry = True
    e.cfg.rdp_sm.ca_matched_tp.ttl_sec = .06
    e.cfg.rdp_sm.repair.ba_enabled = False
    native, errors = e.gateway.send_prepared, []

    async def send(signed, **kwargs):
        first = not v.post_requests
        if not first and not signed.post_only:
            order = signed.orders[0].order
            changed = replace(order, **{field: str(int(getattr(order, field))+10000)})
            rows = (replace(signed.orders[0], order=changed),)
            signed = replace(signed, orders=rows, body_hash=hashlib.sha256(
                e.gateway._prepared_body(rows, False).encode()).hexdigest())
            try:
                return await native(signed, **kwargs)
            except Exception as exc:
                errors.append(str(exc))
                e.cfg.rdp_sm.repair.cutoff_sec = (e.official_time+e.monotonic()-e.time_anchor
                                                - int(e.slug.rsplit('-', 1)[1])-.01)
                raise
        raw = await native(signed, **kwargs)
        if first:
            taker_fill(setup, signed.order_ids[0])
        return raw

    monkeypatch.setattr(e.gateway, 'send_prepared', send)
    try:
        await confirm_ca(feed)
        result, _ = await asyncio.wait_for(normal.enter(D(0), D(0), 'session:w1'), 8)
        assert result.close_barrier == 'BLOCKED'
        assert errors == ['SIGNED_QUOTE_ECONOMICS_CHANGED']
        assert len(v.post_requests) == 2 and e.economic_inventory() == (D(5), D(0))
        assert not e.attempts[-1].post_started
    finally:
        await cleanup(setup)
