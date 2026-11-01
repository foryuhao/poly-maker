"""Opt-in CA Taker through real signing/HTTP guards and exact-ID fill proof."""
import asyncio
from dataclasses import asdict
from decimal import ROUND_HALF_UP
from decimal import Decimal as D
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from polymaker.config import RDPSMConfig
from polymaker.domain import Side, TradeState
from polymaker.rdp_sm import ReadOnlyClobClient
from polymaker.rdp_sm_ca_taker import fee_parameters, taker_event
from polymaker.rdp_sm_logging import HumanRunLog
from polymaker.rdp_sm_merge import validate_merge_history
from polymaker.rdp_sm_sl_ledger import fifo_residual
from polymaker.state.tracker import TradeEvent, fill_cost
from polymaker.userstream.client import UserStream
from polymaker.userstream.parse import parse_trade
from test_rdp_sm_ba import trade
from test_rdp_sm_ca import buy
from test_rdp_sm_ca_entry import ca_setup, confirm_ca
from test_rdp_sm_ca_entry import env as env
from test_rdp_sm_ca_higher_bid import set_prices
from test_rdp_sm_ca_matched_tp import fill, tp_setup
from test_rdp_sm_cached_reads import attach, finish
from test_rdp_sm_entry import boundary
from test_rdp_sm_m1 import CID
from test_rdp_sm_obi_execution import cleanup


async def setup_taker(env, monkeypatch, *, tp=False, fee_unknown=False):
    setup = await (tp_setup(env, monkeypatch) if tp else ca_setup(env, monkeypatch))
    v, e = setup[:2]
    e.cfg.rdp_sm.ca_entry_execution = "taker"
    native_wire = setup[5]

    def wire(request):
        if request.method == 'GET' and request.url.path == '/clob-markets/'+CID:
            v.calls.append((request.url.host, request.url.path, dict(request.url.params)))
            return httpx.Response(200, json={
                'mts': str(min(D(v.ticks[t]) for t in ('1', '2'))), 'nr': False,
                't': [{'t': '1'}, {'t': '2'}],
                'fd': None if fee_unknown else {'r': .07, 'e': 1, 'to': True}}, request=request)
        return native_wire(request)

    boundary(monkeypatch, v, wire)
    # tp_setup changes repair.policy after the generic account warmup. Refresh
    # its policy-dependent session budget, as a real startup already does.
    if tp:
        setup[3].account = (await e._snapshot(include_books=False))[3]
    return setup


def taker_fill(setup, oid, *, quantity=None, price=None, status='CONFIRMED'):
    v, e, feed = setup[:3]
    row = v.rows[oid]
    qty = D(str(quantity if quantity is not None else row['original_size']))
    price = D(str(price if price is not None else row['price']))
    tid = 'taker-'+str(len(v.trades))
    raw = {'id': tid, 'status': status, 'market': CID, 'trader_side': 'TAKER',
           'taker_order_id': oid, 'asset_id': row['asset_id'], 'side': 'BUY',
           'price': str(price), 'size': str(qty), 'timestamp': str(feed.wall()),
           'maker_orders': [{'order_id': 'foreign-maker-'+tid, 'asset_id': row['asset_id'],
                             'side': 'SELL', 'price': str(price), 'matched_amount': str(qty)}]}
    v.trades.append(raw)
    row.update(size_matched=str(qty), status='MATCHED' if qty == D(row['original_size']) else 'CANCELED')
    row['associate_trades'].append(tid)
    reflect(setup)
    return raw


def reflect(setup):
    v, e = setup[:2]
    quantities, cost = {}, D(0)
    for raw in v.trades:
        if raw.get('trader_side') == 'TAKER':
            event = taker_event(parse_trade(raw), e.processor.taker_orders[raw['taker_order_id']])
            quantities[event.token_id] = quantities.get(event.token_id, D(0)) + D(str(event.size))
            cost += fill_cost(event)
        else:
            for leg in raw['maker_orders']:
                qty, price = D(leg['matched_amount']), D(leg['price'])
                quantities[leg['asset_id']] = quantities.get(leg['asset_id'], D(0)) + qty
                cost += qty*price
    v.positions = [{'token_id': token, 'current_size': str(qty), 'avg_price': '.5'}
                   for token, qty in quantities.items()]
    v.capital['balance'] = str(int((D(100)-cost)*1_000_000))


def test_configuration_defaults_and_incompatible_modes():
    assert RDPSMConfig().ca_entry_execution == 'maker'
    with pytest.raises(ValidationError):
        RDPSMConfig(ca_entry_execution='taker')
    base = dict(normal_entry_mode='ca_v1', ca_observe={'enabled': True}, ca_entry_execution='taker')
    assert RDPSMConfig(**base).ca_taker.order_type == 'FOK'
    for invalid in [{'order_type': 'GTC'}, {'max_slippage_ticks': -1},
                    {'max_slippage_ticks': True}, {'max_price': 1}]:
        with pytest.raises(ValidationError):
            RDPSMConfig(**base, ca_taker=invalid)
    with pytest.raises(ValidationError):
        RDPSMConfig(**base, ca_pullback={'enabled': True})


@pytest.mark.parametrize('cached', [False, True])
@pytest.mark.parametrize('token', ['1', '2'])
@pytest.mark.parametrize('order_type,quantity', [('FOK', 5), ('FAK', 2)])
async def test_native_taker_fill_exact_id_cost_partial_and_no_repeat(env, monkeypatch, tmp_path, cached, token, order_type, quantity):
    setup = await setup_taker(env, monkeypatch)
    v, e, feed, _, normal = setup[:5]
    e.cfg.rdp_sm.ca_taker.order_type = order_type
    companion = (await attach(setup, tmp_path))[0] if cached else None
    native = e.gateway.send_prepared

    async def send(signed, **kwargs):
        assert signed.post_only is False
        raw = await native(signed, **kwargs)
        taker_fill(setup, signed.order_ids[0], quantity=quantity)
        return raw

    monkeypatch.setattr(e.gateway, 'send_prepared', send)
    try:
        await confirm_ca(feed, token)
        episode = feed.ca.states[token]
        result, sizing = await asyncio.wait_for(normal.enter(D(0), D(0), 'session:w1'), 7)
        assert result.close_barrier == 'SAFE_TO_CONTINUE', result.block_reason
        assert len(v.post_requests) == 1
        request = v.post_requests[0][0]
        assert request['postOnly'] is False and request['orderType'] == order_type
        assert request['order']['tokenId'] == token
        event = e.confirmed()[0]
        assert event.liquidity_role == 'TAKER' and event.size == quantity
        assert event.maker_order_id == e.attempts[0].expected_order_id
        assert event.price == float(episode.confirming_buy['pre_ask'])
        fee = (D(quantity)*D('.07')*D(str(event.price))*(1-D(str(event.price)))).quantize(D('.00001'), rounding=ROUND_HALF_UP)
        assert D(event.fee_usdc) == fee
        assert e.committed() == D(quantity)*D(str(event.price)) + fee
        assert sizing['ca_entry_execution'] == 'taker'
        assert episode.episode_id in feed.ca_entry.consumed
        assert result.t0_monotonic == episode.t0 and result.fill_deadline_monotonic == episode.deadline
        assert await normal.enter(*e.economic_inventory(), 'session:w2') is None
        assert len(v.post_requests) == 1
    finally:
        await finish(setup, companion) if companion else await cleanup(setup)


@pytest.mark.parametrize('cached', [False, True])
async def test_taker_opening_tp_stays_maker_and_targets_fee_inclusive_cost(env, monkeypatch, tmp_path, cached):
    setup = await setup_taker(env, monkeypatch, tp=True)
    v, e, feed, _, normal = setup[:5]
    e.cfg.rdp_sm.ca_matched_tp.profit_per_share = D('.05')
    e.cfg.rdp_sm.ca_matched_tp.ttl_sec = .08
    e.cfg.rdp_sm.repair.ba_enabled = False
    companion = (await attach(setup, tmp_path))[0] if cached else None
    native = e.gateway.send_prepared

    async def send(signed, **kwargs):
        raw = await native(signed, **kwargs)
        if not signed.post_only:
            taker_fill(setup, signed.order_ids[0])
        else:
            fill(v, signed.order_ids[0], 'maker-tp')
            reflect(setup)
        return raw

    monkeypatch.setattr(e.gateway, 'send_prepared', send)
    try:
        await confirm_ca(feed)
        result, _ = await asyncio.wait_for(normal.enter(D(0), D(0), 'session:w1'), 8)
        assert result.close_barrier == 'SAFE_TO_CONTINUE', result.block_reason
        assert len(v.post_requests) == 2
        assert v.post_requests[0][0]['postOnly'] is False
        assert v.post_requests[1][0]['postOnly'] is True and v.post_requests[1][0]['orderType'] == 'GTC'
        assert e.attempts[1].quote.price <= .42  # 1 - (.51 + fee/share) - .05, floored to tick.
        assert e.economic_inventory() == (D(5), D(5))
        assert e.committed() <= D('4.75')  # At least .05/share after the opening fee.
    finally:
        await finish(setup, companion) if companion else await cleanup(setup)


async def test_taker_preserves_higher_bid_selection(env, monkeypatch):
    setup = await setup_taker(env, monkeypatch)
    v, e, feed, _, normal = setup[:5]
    e.cfg.rdp_sm.ca_entry_direction = 'higher_bid'
    await set_prices(setup, '2')
    try:
        await confirm_ca(feed, '1')
        result, sizing = await asyncio.wait_for(normal.enter(D(0), D(0), 'session:w1'), 5)
        assert result.close_barrier == 'SAFE_TO_CONTINUE'
        assert result.requested_quotes[0].token_id == '2' and result.requested_quotes[0].price == .68
        assert sizing['ca_signal_side'] == 'YES' and sizing['ca_opening_side'] == 'NO'
        assert v.post_requests[0][0]['postOnly'] is False
    finally:
        await cleanup(setup)


@pytest.mark.parametrize('fault', ['ask_above_cap', 'max_price', 'fee_unknown'])
async def test_taker_does_not_send_when_constraints_fail(env, monkeypatch, fault):
    setup = await setup_taker(env, monkeypatch, fee_unknown=fault == 'fee_unknown')
    v, e, feed, _, normal = setup[:5]
    if fault == 'max_price':
        e.cfg.rdp_sm.ca_taker.max_price = D('.50')
    elif fault == 'ask_above_cap':
        native = e.gateway.send_prepared

        async def send(signed, **kwargs):
            setup[-2]['yes_ask'] = '.53'
            feed.apply({'event_type': 'book', 'market': CID, 'asset_id': '1', 'timestamp': str(feed.wall()*1000),
                        'bids': [{'price': '.50', 'size': '80'}], 'asks': [{'price': '.53', 'size': '20'}]})
            return await native(signed, **kwargs)

        monkeypatch.setattr(e.gateway, 'send_prepared', send)
    try:
        await confirm_ca(feed)
        if fault == 'fee_unknown':
            with pytest.raises(ValueError, match='FEE_PARAMETERS_UNAVAILABLE'):
                await normal.enter(D(0), D(0), 'session:w1')
        else:
            await asyncio.wait_for(normal.enter(D(0), D(0), 'session:w1'), 5)
        assert not v.post_requests and not feed.ca_entry.consumed
    finally:
        await cleanup(setup)


async def test_market_info_uses_real_sdk_read_boundary_and_caches_fee_parameters(env, monkeypatch):
    setup = await setup_taker(env, monkeypatch)
    v, e, _, _, normal = setup[:5]
    try:
        assert e.gateway._client.session_market_id == CID
        await normal.prepare_execution_constraints()
        assert normal.taker_fees == {'rate': '0.07', 'exponent': 1}
        await normal.prepare_execution_constraints()
        assert [path for _, path, _ in v.calls].count('/clob-markets/'+CID) == 1
        assert e.gateway._client._ClobClient__fee_infos['1'].rate == .07
        assert not v.post_requests
    finally:
        await cleanup(setup)


@pytest.mark.parametrize('fault', ['other_market', 'wrong_host', 'query', 'fragment', 'params',
                                 'maker', 'unbound_market', 'active_post', 'read_only', 'other_path'])
async def test_market_info_read_permission_stays_narrow_and_preparation_only(env, monkeypatch, fault):
    setup = await setup_taker(env, monkeypatch)
    v, e = setup[:2]
    client = e.gateway._client
    url, params = 'https://clob.polymarket.com/clob-markets/'+CID, None
    try:
        with monkeypatch.context() as scoped:
            if fault == 'other_market':
                url = 'https://clob.polymarket.com/clob-markets/0x'+'cd'*32
            elif fault == 'wrong_host':
                url = url.replace('clob.polymarket.com', 'example.invalid')
            elif fault == 'query':
                url += '?unexpected=1'
            elif fault == 'fragment':
                url += '#unexpected'
            elif fault == 'params':
                params = {'unexpected': '1'}
            elif fault == 'maker':
                scoped.setattr(e.cfg.rdp_sm, 'ca_entry_execution', 'maker')
            elif fault == 'unbound_market':
                scoped.setattr(client, 'session_market_id', None)
            elif fault == 'active_post':
                scoped.setattr(client, 'operation_local', SimpleNamespace(no_get=True), raising=False)
            elif fault == 'other_path':
                url = 'https://clob.polymarket.com/balance-allowance/update'
            def call():
                return (ReadOnlyClobClient._get(client, url, params=params)
                        if fault == 'read_only' else client._get(url, params=params))
            with pytest.raises(ValueError, match='UNAPPROVED|OPERATION_EXTRA_GET_FORBIDDEN'):
                await e.gateway._io(call)
        assert not any(path.startswith('/clob-markets/') for _, path, _ in v.calls)
        assert not v.post_requests
    finally:
        await cleanup(setup)


def test_fee_parameters_and_taker_trade_identity_fail_closed():
    with pytest.raises(ValueError):
        fee_parameters({'t': [{'t': '1'}, {'t': '2'}], 'fd': {'r': .07, 'e': 0, 'to': True}}, ['1', '2'])
    spec = {'market_id': CID, 'token_id': '1', 'opposite_token': '2', 'price': D('.60'),
            'quantity': D(5), 'fees': {'rate': '.07', 'exponent': 1}}
    raw = {'id': 't', 'status': 'CONFIRMED', 'market': CID, 'taker_order_id': 'ours',
           'asset_id': '1', 'side': 'BUY', 'trader_side': 'TAKER', 'price': '.6', 'size': '5',
           'maker_orders': [{'order_id': 'foreign', 'asset_id': '2', 'side': 'BUY',
                             'price': '.4', 'matched_amount': '5'}]}
    event = taker_event(parse_trade(raw), spec)
    assert event.status is TradeState.CONFIRMED and event.our_side is Side.BUY
    assert fill_cost(event) == D('3.08400')
    for replacement in [{'asset_id': '2'}, {'side': 'SELL'}, {'price': '.61'},
                        {'size': '6'}, {'trader_side': 'MAKER'}, {'fee_usdc': '0'}]:
        with pytest.raises(ValueError):
            taker_event(parse_trade({**raw, **replacement}), spec)


def test_multilevel_taker_uses_component_execution_prices_and_costs():
    spec = {'market_id': CID, 'token_id': '1', 'opposite_token': '2', 'price': D('.60'),
            'quantity': D(5), 'fees': {'rate': '.07', 'exponent': 1}}
    raw = {'id': 't', 'status': 'CONFIRMED', 'market': CID, 'taker_order_id': 'ours',
           'asset_id': '1', 'side': 'BUY', 'trader_side': 'TAKER', 'price': '.6', 'size': '5',
           'maker_orders': [{'order_id': 'a', 'asset_id': '1', 'side': 'SELL', 'price': '.58', 'matched_amount': '2'},
                            {'order_id': 'b', 'asset_id': '2', 'side': 'BUY', 'price': '.4', 'matched_amount': '3'}]}
    event = taker_event(parse_trade(raw), spec)
    assert event.price == .592 and D(event.principal_usdc) == D('2.96')
    assert D(event.fee_usdc) == D('.08450')


@pytest.mark.parametrize('fault', ['reject', 'lost_ack', 'unknown_absent'])
async def test_taker_post_outcomes_never_retry_same_signal_or_release_unknown(env, monkeypatch, fault):
    setup = await setup_taker(env, monkeypatch)
    v, e, feed, _, normal, wire = setup[:6]
    if fault == 'reject':
        wire.reject_index = 0
    else:
        wire.fail_ack = True
        if fault == 'unknown_absent':
            native_send = e.gateway.send_prepared

            async def lose(signed, **kwargs):
                try:
                    return await native_send(signed, **kwargs)
                finally:
                    v.rows.clear()

            monkeypatch.setattr(e.gateway, 'send_prepared', lose)
            async def no_timeout_promotion():
                return None
            monkeypatch.setattr(e, '_wait_for_confirmation', no_timeout_promotion)
    try:
        await confirm_ca(feed)
        result, _ = await asyncio.wait_for(normal.enter(D(0), D(0), 'session:w1'), 6)
        assert len(v.post_requests) == 1 and feed.ca_entry.consumed
        if fault == 'unknown_absent':
            assert result.close_barrier == 'BLOCKED'
            assert e.attempts[0].outcome == 'UNKNOWN'
            assert e.committed() > D('2.60')  # Full principal and fee reserve retained.
        else:
            assert result.close_barrier == 'SAFE_TO_CONTINUE', result.block_reason
        if fault == 'unknown_absent':
            with pytest.raises(Exception, match='PREVIOUS_WINDOW_UNRESOLVED'):
                await normal.enter(D(0), D(0), 'session:w2')
        else:
            await normal.enter(D(0), D(0), 'session:w2')
        assert len(v.post_requests) == 1
    finally:
        await cleanup(setup)


async def test_user_ws_taker_before_ack_is_buffered_then_applied_once(env, monkeypatch):
    setup = await setup_taker(env, monkeypatch)
    v, e, feed, _, normal = setup[:5]
    native = e.gateway.send_prepared

    async def send(signed, **kwargs):
        raw = await native(signed, **kwargs)
        event = taker_fill(setup, signed.order_ids[0])
        ws = UserStream.__new__(UserStream)
        ws._proc, ws._journal = e.processor, None
        ws._condition_of_token = lambda _: CID
        ws._receipt_us = ws._receipt_ns = None
        ws.connection_generation = 1
        ws._on_trade(event)
        assert not e.processor.owned_record(signed.order_ids[0])
        assert e.processor._pending_events
        return raw

    monkeypatch.setattr(e.gateway, 'send_prepared', send)
    try:
        await confirm_ca(feed)
        result, _ = await asyncio.wait_for(normal.enter(D(0), D(0), 'session:w1'), 5)
        assert result.close_barrier == 'SAFE_TO_CONTINUE', result.block_reason
        assert len(e.confirmed()) == 1 and e.confirmed()[0].size == 5
        assert not e.processor._pending_events
    finally:
        await cleanup(setup)


async def test_taker_slippage_cap_allows_better_fill_and_reserves_only_actual_cash_after_close(env, monkeypatch):
    setup = await setup_taker(env, monkeypatch)
    v, e, feed, _, normal = setup[:5]
    e.cfg.rdp_sm.ca_taker.max_slippage_ticks = 2
    native = e.gateway.send_prepared

    async def send(signed, **kwargs):
        assert signed.quotes[0].price == .54
        raw = await native(signed, **kwargs)
        taker_fill(setup, signed.order_ids[0], price='.51')
        return raw

    monkeypatch.setattr(e.gateway, 'send_prepared', send)
    try:
        await confirm_ca(feed)
        result, _ = await asyncio.wait_for(normal.enter(D(0), D(0), 'session:w1'), 5)
        assert result.close_barrier == 'SAFE_TO_CONTINUE', result.block_reason
        assert e.confirmed()[0].price == .51
        assert e.committed() == D('2.63747')
        assert e.attempts[0].maximum > e.committed()
        assert len(v.post_requests) == 1
    finally:
        await cleanup(setup)


@pytest.mark.parametrize('formed', [False, True])
async def test_taker_does_not_relax_ca_confirmation_or_formation_time(env, monkeypatch, formed):
    setup = await setup_taker(env, monkeypatch)
    v, _, feed, _, normal = setup[:5]
    try:
        if formed:
            feed.apply(trade(feed, '1'))
            feed.apply(buy(feed, '1'))  # Below the existing 100ms formation gate.
        assert await normal.enter(D(0), D(0), 'session:w1') is None
        assert not v.post_requests and not feed.ca_entry.consumed
    finally:
        await cleanup(setup)


@pytest.mark.parametrize('fault', ['maker_attempt', 'unregistered_id'])
async def test_native_post_requires_explicit_taker_attempt_and_exact_id(env, monkeypatch, fault):
    setup = await setup_taker(env, monkeypatch)
    v, e, feed, _, normal = setup[:5]
    native = e.gateway.send_prepared

    async def send(signed, **kwargs):
        if fault == 'maker_attempt':
            e.attempts[-1].entry_execution = 'maker'
        else:
            e.gateway._client.ca_taker_ids.clear()
        with pytest.raises(Exception, match='SM_MAKER_ONLY'):
            await native(signed, **kwargs)
        return None

    monkeypatch.setattr(e.gateway, 'send_prepared', send)
    try:
        await confirm_ca(feed)
        await asyncio.wait_for(normal.enter(D(0), D(0), 'session:w1'), 5)
        assert not v.post_requests and not feed.ca_entry.consumed
    finally:
        await cleanup(setup)


def test_partial_pair_keeps_proportional_taker_fee_in_residual_cost():
    opening = TradeEvent('1', Side.BUY, .6, 5, 'open', TradeState.CONFIRMED, 0,
                         'ours', CID, 'TAKER', '3', '.084')
    closing = TradeEvent('2', Side.BUY, .3, 2, 'tp', TradeState.CONFIRMED, 1, 'close', CID)
    remaining, = fifo_residual([opening, closing], {
        ('open', 'ours'): {'fill_monotonic': 0}, ('tp', 'close'): {'fill_monotonic': 1}}, '1')
    assert remaining.size == 3
    assert fill_cost(remaining) == D('1.8504')
    assert D(remaining.fee_usdc) == D('.0504')


def test_taker_merge_history_and_console_use_fee_inclusive_cost():
    opening = TradeEvent('1', Side.BUY, .6, 5, 'open', TradeState.CONFIRMED, 0,
                         'ours', CID, 'TAKER', '3', '.084')
    closing = TradeEvent('2', Side.BUY, .3, 5, 'tp', TradeState.CONFIRMED, 1, 'close', CID)
    fills = [asdict(opening), asdict(closing)]
    quotes = [{'token_id': f.token_id, 'outcome': side, 'side': 'BUY', 'price': f.price,
               'size': f.size, 'window_id': 'session:w1'}
              for f, side in [(opening, 'YES'), (closing, 'NO')]]
    def event(kind, payload):
        return {'kind': kind, 'data': {'payload': payload}}
    intent = dict(merge_id='merge', condition_id=CID, wallet='wallet', elapsed_s=240,
                  amount_raw=5_000_000, before_yes_raw=5_000_000, before_no_raw=5_000_000,
                  collateral_before_raw=90_000_000, yes_token_id='1', no_token_id='2')
    result = {**intent, 'status': 'CONFIRMED', 'tx_hash': '0x'+'1'*64, 'receipt_status': 1,
              'after_yes_raw': 0, 'after_no_raw': 0, 'collateral_after_raw': 95_000_000,
              'released_cost_usdc': '4.584'}
    group = [event('sm_placement_ack', {'order_id': f.maker_order_id, 'quote': q})
             for f, q in zip([opening, closing], quotes, strict=True)]
    group += [event('sm_close_barrier', {'result': 'SAFE_TO_CONTINUE', 'confirmed_fills': fills}),
              event('sm_merge_intent', intent), event('sm_merge_submitted', intent),
              event('sm_merge_result', result)]
    def check(condition):
        assert condition
    summary = {'market_id': CID, 'merged_pairs': 5, 'recovered_collateral_usdc': 5}
    validate_merge_history(group, {'funder': 'wallet'}, summary, check)
    result['released_cost_usdc'] = '4.5'
    with pytest.raises(AssertionError):
        validate_merge_history(group, {'funder': 'wallet'}, summary, check)

    log = HumanRunLog()
    try:
        log.planned = {'quotes': [quotes[0]]}
        text = '\n'.join(log._window({
            'placement_results': [{'quote': quotes[0], 'outcome': 'ACKNOWLEDGED', 'order_id': 'ours',
                                   'post_started': True, 'entry_execution': 'taker'}],
            'confirmed_fills': [fills[0]], 'yes_inventory': 5, 'no_inventory': 0,
            'close_barrier': 'SAFE_TO_CONTINUE', 'capital_committed': 3.084}))
        assert text.count('Maker/Post-only=NO') == 2
        assert 'Capital Used (confirmed cost): 3.084 USDC' in text
    finally:
        log.close()
