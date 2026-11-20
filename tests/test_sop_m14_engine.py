"""Original M14 raw frames through its own account, fills and official ledgers."""

import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path

import pytest
from m1223_sim import SopEngine, definition
from m1223_sim_forward import execution_configuration
from sop_signals.m14 import Parameters
from sop_signals.parameters import load_revision

from m1234_fixtures import fixture_events


@pytest.mark.asyncio
@pytest.mark.parametrize('ask_seconds', [None, .25, .15])
@pytest.mark.parametrize('mode', ['settled', 'unsettled', 'parent_guard_break', 'tick_change'])
async def test_m14_raw_to_order_partial_duplicate_cancel_race_settlement_and_retirement(tmp_path, mode, ask_seconds):
    selected = Path.cwd() / 'config/m1223-parameters.json'
    document = json.loads(selected.read_text()) if selected.exists() else {}
    parameters = load_revision(document)[0] if document.get('strategy') == 'M14' else Parameters()
    if ask_seconds is not None:
        parameters = replace(parameters, round_seconds=ask_seconds)
    initial = deepcopy(fixture_events()[0])
    initial['definition']['settings'].update(
        profiles={}, sop=definition(parameters), sop_execution=execution_configuration(),
        scenarios={'trade-through': {'post_ms': 100, 'ack_ms': 200,
                                     'cancel_ms': 200, 'ahead_cancel_fraction': '0'}},
    )
    engine = SopEngine(tmp_path, parameters=parameters)
    await engine.ingest(initial)
    start_wall, seq = initial['available_wall'], 1

    async def event(t, kind, raw=None, **payload):
        nonlocal seq
        seq += 1
        if raw is not None:
            if kind == 'FRAME':
                for row in raw if isinstance(raw, list) else [raw]:
                    row.update(market=engine.meta.condition_id,
                               timestamp=str(round((start_wall + t) * 1000)))
                payload.update(received_mono=t, received_wall=start_wall + t)
            payload['raw'] = raw
        await engine.ingest({'schema': initial['schema'], 'seq': seq, 'kind': kind,
                             'available_mono': t, 'available_wall': start_wall + t,
                             'generation': 1, **payload})

    def bbo(token='1'):
        return {'event_type': 'best_bid_ask', 'asset_id': token,
                'best_bid': '.50', 'best_ask': '.52'}

    def delta(side, price, size):
        return {'event_type': 'price_change', 'price_changes': [
            {'asset_id': '1', 'side': side, 'price': price, 'size': size}]}

    def trade(side, price, size, identity):
        return {'event_type': 'last_trade_price', 'asset_id': '1', 'side': side,
                'price': price, 'size': size, 'trade_id': identity,
                'transaction_hash': '0x' + hashlib.sha256(identity.encode()).hexdigest()}

    async def refresh(t):
        await event(t, 'FRAME', [bbo('1'), bbo('2')])

    await event(0, 'CONNECT')
    await event(0, 'FRAME', [
        {'event_type': 'book', 'asset_id': token,
         'bids': [{'price': '.50', 'size': '100'}, {'price': '.49', 'size': '100'}],
         'asks': [{'price': '.52', 'size': '100'}]}
        for token in ('1', '2')])
    core = engine.sop_runtime.core
    if parameters.round_seconds == .5:
        await event(.01, 'FRAME', [trade('SELL', '.50', '10', 'bid'), trade('BUY', '.52', '10', 'a1')])
        await event(.02, 'FRAME', [delta('BUY', '.50', '80'), delta('SELL', '.52', '80')])
        await refresh(.1)
        await event(.2, 'FRAME', [delta('BUY', '.50', '95'), delta('SELL', '.52', '82')])
        for t in (.3, .4, .5, .511, .6):
            await refresh(t)
        assert core.roots['1', D('.52')].phase == 'WAIT_A2' and not engine.signal_history
        await event(.61, 'FRAME', trade('BUY', '.52', '10', 'a2'))
        await event(.62, 'FRAME', delta('SELL', '.52', '70'))
        await refresh(.7)
        await event(.8, 'FRAME', delta('SELL', '.52', '71.2'))
        for t in (.9, 1.0, 1.1):
            await refresh(t)
        final_parent = [bbo('1'), bbo('2')]
        if mode == 'parent_guard_break':
            final_parent += [delta('SELL', '.52', '80'), delta('SELL', '.52', '71.2')]
        elif mode == 'tick_change':
            final_parent.append({'event_type': 'tick_size_change', 'asset_id': '1',
                                 'old_tick_size': '.01', 'new_tick_size': '.02'})
        await event(1.111, 'FRAME', final_parent)
    else:
        # Synthetic role-clock fixtures. Bid keeps the original500ms and100ms hold.
        first_close = .01 + parameters.round_seconds + .001
        second_start = first_close + .01
        second_close = second_start + parameters.round_seconds + .001
        frames = {}

        def at(t, rows):
            frames.setdefault(t, []).extend(rows)

        for i in range(1, 12):
            at(i / 10, [bbo('1'), bbo('2')])
        at(.01, [trade('SELL', '.50', '10', 'bid'), trade('BUY', '.52', '10', 'a1')])
        at(.02, [delta('BUY', '.50', '80'), delta('SELL', '.52', '80')])
        at(.06, [delta('SELL', '.52', '82')])
        at(.2, [delta('BUY', '.50', '95')])
        # Publication closes before consuming its parent row. Supply coverage
        # in the final250ms bucket before each close, including a .260s end.
        at(first_close - .006, [bbo('1'), bbo('2')])
        at(first_close, [bbo('1'), bbo('2')])
        at(second_start, [trade('BUY', '.52', '10', 'a2')])
        at(second_start + .01, [delta('SELL', '.52', '70')])
        at(second_start + .03, [delta('SELL', '.52', '71.2')])
        at(second_close - .006, [bbo('1'), bbo('2')])
        at(.511, [bbo('1'), bbo('2')])
        final_parent = [bbo('1'), bbo('2')]
        if mode == 'parent_guard_break':
            final_parent += [delta('SELL', '.52', '80'), delta('SELL', '.52', '71.2')]
        elif mode == 'tick_change':
            final_parent.append({'event_type': 'tick_size_change', 'asset_id': '1',
                                 'old_tick_size': '.01', 'new_tick_size': '.02'})
        at(second_close, final_parent)
        for t, rows in sorted(frames.items()):
            await event(t, 'FRAME', rows)
            if t == first_close:
                assert core.roots['1', D('.52')].phase == 'WAIT_A2' and not engine.signal_history
        await refresh(1.111)
    await event(1.112, 'POLL')
    broker = engine.brokers['sop/trade-through']
    if mode in {'parent_guard_break', 'tick_change'}:
        assert not engine.signal_history and not broker.orders and not engine.assumed_orders
        assert not core.roots and core.counts['SIGNAL_CONFIRMED'] == 0
        await event(300, 'END', reason='T300_OBSERVED')
        await engine.close()
        engine.retire_runtime()
        assert engine.sop_runtime is None
        return
    assert len(engine.signal_history) == len(broker.orders) == len(engine.assumed_orders) == 1
    order = next(iter(broker.orders.values()))
    assert order.intent.strategy == order.intent.profile == 'M14'
    assert set(broker.accounts) == {'M14'}
    assert D(order.intent.price) == D('.50') and D(order.intent.quantity) == 10
    await refresh(1.18)
    await event(1.3, 'FRAME', trade('SELL', '.49', '3', 'valid-fill'))
    assert order.filled == 3
    await event(1.3, 'FRAME', trade('SELL', '.49', '3', 'valid-fill'))
    assert order.filled == 3 and engine.sop_runtime.counts['DUPLICATE_DIRECT_TRADE'] == 1
    await event(1.4, 'FRAME', delta('SELL', '.52', '80'))
    assert order.cancel_requested == pytest.approx(1.4) and not core.signals
    await event(1.5, 'FRAME', trade('SELL', '.49', '4', 'cancel-race'))
    assert order.filled == 7
    await refresh(1.55)
    await event(1.61, 'POLL')
    assert order.state == 'CANCELLED'
    if mode == 'settled':
        await event(1.62, 'SETTLEMENT', {'slug': engine.meta.slug,
                    'conditionId': engine.meta.condition_id, 'clobTokenIds': ['1', '2'],
                    'outcomePrices': ['1', '0'], 'closed': True, 'resolutionStatus': 'resolved'})
        assert broker.account('M14').payout == 7
        assert D(next(iter(engine.assumed_orders.values()))['gross_pnl']) == 5
    else:
        await event(1.62, 'GAP', reason='SYNTHETIC_LATE_KNOWN_DATA_GAP')
        assert order.uncertain and order.filled == 7 and engine.resolution is None
        assert not next(iter(engine.assumed_orders.values()))['settled']
    assert broker.account('M14').spent == D('3.50')
    engine.store.commit()
    attribution = [json.loads(row[0])['classification'] for row in engine.store.db.execute(
        "SELECT payload FROM audit WHERE kind='SOP_FILL_ATTRIBUTION' ORDER BY seq")]
    assert attribution == ['WITHIN_SIGNAL_VALIDITY', 'AFTER_SIGNAL_END']
    await event(300, 'END', reason='T300_OBSERVED')
    await engine.close()
    from m1223_report import install_report_compatibility, market_result

    from polymaker.m1234_sim.report import read_state, summary

    install_report_compatibility()
    assert summary(engine.store.path)['accounts'][0]['strategy'] == 'M14'
    market, orders = market_result(engine.store.path, tmp_path)
    assert market['signal_count'] == market['positive_modelled_fill_orders'] == 1
    if mode == 'settled':
        assert D(orders[0]['known_simulated_gross_pnl']) == D('3.50')
        assert D(orders[0]['assumed_gross_pnl']) == 5
    else:
        assert market['pending_settlement_orders'] == market['unresolved_orders'] == 1
        assert orders[0]['known_simulated_gross_pnl'] is None and orders[0]['assumed_gross_pnl'] is None
        assert D(orders[0]['known_filled_quantity']) == 7 and orders[0]['unresolved']
    assert D(orders[0]['within_signal_validity_quantity']) == 3
    assert D(orders[0]['cancel_in_flight_quantity']) == 4
    durable = read_state(engine.store.path)
    engine.retire_runtime()
    assert engine.sop_runtime is None and engine.signals is None and not engine.extra_policies
    assert read_state(engine.store.path) == durable
