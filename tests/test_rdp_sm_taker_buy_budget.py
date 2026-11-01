"""21:35 production shape: BUY 5 @ .83 spends 4.15 and gets 5.123457 @ .81."""
import asyncio
from decimal import Decimal as D

import pytest

from polymaker.domain import Side
from polymaker.rdp_sm_ca_taker import taker_event, validate_buy_fills
from polymaker.rdp_sm_execution import _terminal
from polymaker.rdp_sm_sync_reads import ObserverReader
from polymaker.state.tracker import fill_cost
from polymaker.userstream.client import UserStream
from polymaker.userstream.parse import normalize_order, parse_trade
from test_rdp_sm_ca_entry import confirm_ca
from test_rdp_sm_ca_entry import env as env
from test_rdp_sm_ca_entry_price_filter import set_ask
from test_rdp_sm_ca_matched_tp import fill
from test_rdp_sm_ca_taker import reflect, setup_taker, taker_fill
from test_rdp_sm_cached_reads import attach, finish
from test_rdp_sm_m1 import CID
from test_rdp_sm_obi_execution import cleanup


def production_trade(oid='owned', tid='t'):
    return {'id': tid, 'status': 'CONFIRMED', 'market': CID, 'trader_side': 'TAKER',
            'taker_order_id': oid, 'asset_id': '2', 'side': 'BUY', 'price': '.81', 'size': '5.123457',
            'maker_orders': [{'order_id': 'other-'+tid, 'asset_id': '1', 'side': 'BUY',
                              'price': '0.1900000331807215', 'matched_amount': '5.123457'}]}


def spec():
    return {'market_id': CID, 'token_id': '2', 'opposite_token': '1', 'price': D('.83'),
            'quantity': D(5), 'fees': {'rate': '.07', 'exponent': 1}}


def test_real_trade_and_order_shape_with_unchanged_signed_identity():
    f = taker_event(parse_trade(production_trade()), spec())
    assert f.size == 5.123457 and f.our_side is Side.BUY
    assert D(f.principal_usdc) == D('4.15') and D(f.fee_usdc) == D('.05520')
    row = {'id': 'owned', 'asset_id': '2', 'market': CID, 'side': 'BUY', 'price': '.83',
           'original_size': '5', 'size_matched': '5.123457', 'status': 'MATCHED', 'associate_trades': ['t']}
    assert normalize_order(row) is None  # Generic Maker/foreign-order parser stays strict.
    ev = normalize_order(row, allow_taker_buy_overfill=True)
    assert ev.original_size == 5 and ev.size_matched == 5.123457 and ev.remaining_size == 0
    assert _terminal(ev, taker_buy=True) and not _terminal(ev)
    assert ObserverReader.order_row(row)['size_matched'] == 5.123457
    assert normalize_order(row | {'side': 'SELL'}, allow_taker_buy_overfill=True) is None


def test_cumulative_budget_not_nominal_shares_and_fee_bound():
    f = taker_event(parse_trade(production_trade()), spec())
    validate_buy_fills([f], spec())
    second = taker_event(parse_trade(production_trade(tid='t2')), spec())
    with pytest.raises(ValueError, match='CA_TAKER_FILL_EXCEEDS_LIMIT'):
        validate_buy_fills([f, second], spec())
    for change in [{'size': '5.2'}, {'asset_id': '1'}, {'side': 'SELL'}]:
        with pytest.raises(ValueError):
            taker_event(parse_trade(production_trade() | change), spec())
    raw = production_trade()
    raw['maker_orders'][0]['price'] = '.18'  # Actual cost exceeds signed 4.15.
    with pytest.raises(ValueError, match='CA_TAKER_FILL_EXCEEDS_LIMIT'):
        taker_event(parse_trade(raw), spec())


async def test_tracker_accepts_cumulative_extra_shares_but_quarantines_overspend(env, monkeypatch):
    setup = await setup_taker(env, monkeypatch)
    _, e = setup[:2]
    oid = 'cash-bound-owned'
    e.processor.taker_orders = {oid: spec()}
    e.processor.register_order(oid, CID, '2', Side.BUY, 5, .83, 'rdp_sm_prepared', quiet=True)

    def event(tid, qty, price):
        raw = production_trade(oid, tid) | {'size': str(qty), 'price': str(price), 'status': 'MATCHED'}
        raw['maker_orders'] = [{'order_id': 'other-'+tid, 'asset_id': '2', 'side': 'SELL',
                                'price': str(price), 'matched_amount': str(qty)}]
        return taker_event(parse_trade(raw), spec())

    try:
        first, second = event('a', 3, '.5'), event('b', 3, '.4')
        for f in [first, second, first]:
            e.processor.on_trade(f, CID, source='WS')
        assert e.processor.tracked_trade_qty(oid) == 6
        assert oid not in e.processor.quarantined_orders
        assert len(e.processor.fill_records) == 2
        e.processor.on_trade(event('c', 4, '.5'), CID, source='WS')
        assert oid in e.processor.quarantined_orders
        assert ('c', oid) in e.processor.quarantined_fill_keys
        assert e.processor.tracked_trade_qty(oid) == 6
    finally:
        await cleanup(setup)


@pytest.mark.parametrize('cached', [False, True])
@pytest.mark.parametrize('delivery', ['rest', 'ws_before_ack', 'lost_ack'])
@pytest.mark.parametrize('tp_full', [False, True])
async def test_improved_buy_runs_real_signing_filltruth_tp_and_expiry(
    env, monkeypatch, tmp_path, cached, delivery, tp_full
):
    setup = await setup_taker(env, monkeypatch, tp=True)
    v, e, feed, _, normal = setup[:5]
    # Real market at admission has a common cent tick. Sign .83 * 5 = 4.15.
    v.ticks['2'] = '.01'
    feed.apply({'event_type': 'tick_size_change', 'market': CID, 'asset_id': '2',
                'old_tick_size': '.001', 'new_tick_size': '.01', 'timestamp': str(feed.wall()*1000)})
    feed.verify_ticks(v.ticks)
    e.cfg.rdp_sm.ba.signal_lifetime_sec = 3
    e.cfg.rdp_sm.ba.max_entry_age_ms = 1000
    e.cfg.rdp_sm.ca_matched_tp.taker_exit_on_expiry = True
    e.cfg.rdp_sm.ca_matched_tp.profit_per_share = D('.05')
    e.cfg.rdp_sm.ca_matched_tp.ttl_sec = .08
    e.cfg.rdp_sm.repair.ba_enabled = False
    set_ask(feed, '2', '.83')
    companion = (await attach(setup, tmp_path))[0] if cached else None
    native = e.gateway.send_prepared

    async def send(signed, **kwargs):
        first = not v.post_requests
        raw = await native(signed, **kwargs)
        oid = signed.order_ids[0]
        if first:
            taker_fill(setup, oid, quantity='5.123457', price='.81')
            v.rows[oid]['status'] = 'MATCHED'  # Original 5, matched 5.123457, exactly as venue.
            if delivery in {'ws_before_ack', 'lost_ack'}:
                ws = UserStream.__new__(UserStream)
                ws._proc, ws._journal = e.processor, None
                ws._condition_of_token = lambda _: CID
                ws._receipt_us = ws._receipt_ns = None
                ws.connection_generation = 1
                ws._on_trade(v.trades[-1])
                ws._on_order(v.rows[oid])
            if delivery == 'lost_ack':
                import httpx
                await asyncio.sleep(.12)  # Known fill lease expires before ACK recovery.
                raise httpx.ReadTimeout('venue executed improved BUY; ACK was lost')
        elif signed.post_only:
            assert signed.quotes[0].size == 5.13 and signed.quotes[0].price == .12
            if tp_full:
                fill(v, oid, 'tp', qty=5.13)
                reflect(setup)
        else:
            taker_fill(setup, oid)
        return raw

    monkeypatch.setattr(e.gateway, 'send_prepared', send)
    try:
        await confirm_ca(feed, '2')
        entered = await asyncio.wait_for(normal.enter(D(0), D(0), 'session:w1'), 8)
        assert entered is not None, (normal.quote_block_reason, normal.signal_block_reason)
        result, sizing = entered
        assert result.close_barrier == 'SAFE_TO_CONTINUE', result.block_reason
        opening = e.attempts[0]
        assert opening.quote.size == 5 and opening.quote.price == .83
        opened = [f for f in e.economic_fills() if f.maker_order_id == opening.order_id]
        assert len(opened) == 1 and opened[0].size == 5.123457
        assert fill_cost(opened[0]) == D('4.205200')
        assert opening.order_id in e.terminal and not e.processor.quarantined_orders
        assert e.processor.tracked_trade_qty(opening.order_id) == 5.123457
        if delivery == 'lost_ack':
            # At recovery this deliberately short lease has expired. It must
            # exit immediately instead of resetting TP/CA or losing the fill.
            assert len(v.post_requests) == 2
            assert sizing['ca_matched_tp']['expiry_exit_reason'] == 'HEDGED_WITH_EXCESS'
        else:
            assert len(v.post_requests) == (2 if tp_full else 3)
            assert sizing['ca_matched_tp']['expiry_exit_reason'] == (
                None if tp_full else 'HEDGED_WITH_EXCESS')
    finally:
        await finish(setup, companion) if companion else await cleanup(setup)
