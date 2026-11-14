"""RR1 lifecycle history, durable failure and healthy-entry controls."""
from copy import deepcopy
from decimal import Decimal

import pytest

from polymaker.m1234_sim.audit import reconcile
from polymaker.m1234_sim.broker import CONSTRAINT_CONFLICT, ConstraintConflict
from polymaker.m1234_sim.report import publish, read_state, summary
from polymaker.m1234_sim.store import read_audit
from test_m1234_sim_core import intent
from test_m1234_sim_ticks import change, initial, send


async def filled_engine(path, quantity='10', cancelled=False, scenario=None):
    engine = await initial(path)
    await change(engine, .005)
    orders = {name: broker.submit(intent(price='.095'))
              for name, broker in engine.brokers.items() if scenario is None or name == scenario}
    await send(engine, 'FRAME', .12, raw={
        'event_type': 'last_trade_price', 'market': engine.meta.condition_id, 'asset_id': '1',
        'timestamp': str((engine.test_wall + .12) * 1000), 'price': '.09', 'size': quantity,
        'side': 'SELL', 'trade_id': 'known-before-notice',
    })
    if cancelled:
        await send(engine, 'RESOURCE', .13)
        for name, order in orders.items():
            engine.brokers[name].cancel(order.id)
        await send(engine, 'RESOURCE', .34)
    return engine, orders


async def late_constraint(engine, at=.4, source=.055):
    await send(engine, 'FRAME', at, raw={
        'event_type': 'tick_size_change', 'market': engine.meta.condition_id,
        'asset_id': '1', 'timestamp': str((engine.test_wall + source) * 1000),
        'old_tick_size': '.001', 'new_tick_size': '.01',
    })


@pytest.mark.parametrize('scenario', ['trades-only', 'cancel-half', None])
@pytest.mark.parametrize('quantity,cancelled', [('5', False), ('10', False), ('5', True)])
async def test_all_fill_history_fails_durably_without_economic_rewrite(tmp_path, scenario, quantity, cancelled):
    engine, orders = await filled_engine(tmp_path, quantity, cancelled, scenario)
    before = {name: deepcopy(engine.brokers[name].snapshot()) for name in orders}
    try:
        with pytest.raises(ConstraintConflict, match=CONSTRAINT_CONFLICT):
            await late_constraint(engine)
        assert engine.validity['status'] == 'INVALID'
        failures = engine.validity['failures']
        assert {row['scenario'] for row in failures} == set(orders)
        assert len(failures) == len(orders)
        for name, order in orders.items():
            assert order.filled == Decimal(quantity) and not order.uncertain
            assert engine.brokers[name].snapshot()['accounts'] == before[name]['accounts']
            expected_orders = deepcopy(before[name]['orders'])
            expected_orders[order.id]['client'] = 'ACK'
            expected_orders[order.id]['ack_at'] = .005 + .2
            assert order.ack_at == pytest.approx(.205)
            assert engine.brokers[name].snapshot()['orders'] == expected_orders
        state = read_state(engine.store.path)
        assert state['validity']['status'] == 'INVALID'
        assert not state['coverage']['complete_window']
        assert len([row for row in read_audit(engine.store.path) if row['kind'] == 'SIMULATION_INVALID']) == len(orders)
        await send(engine, 'END', 281., reason='T300_OBSERVED')
        assert engine.ended and engine.validity['status'] == 'INVALID'
        assert not engine.coverage['complete_window']
        for name in orders:
            assert engine.brokers[name].snapshot()['accounts'] == before[name]['accounts']
        await engine.close()
        report = summary(engine.store.path)
        assert report['status'] == 'FAIL'
        assert all(not row['performance_eligible'] and row['gross_pnl_usdc'] is None
                   and row['filled_shares'] is None for row in report['accounts'])
        result = reconcile(engine.store.path)
        assert result['arithmetic_status'] == 'PASS'
        assert result['status'] == result['acceptance_status'] == 'FAIL'
        published = publish(tmp_path, [report])
        assert published['status'] == 'FAIL' and published['valid_complete_windows'] == 0
    finally:
        await engine.close()


async def test_repeat_constraint_and_cleanup_do_not_duplicate_facts_or_release_risk(tmp_path):
    engine, orders = await filled_engine(tmp_path, '5', True)
    with pytest.raises(ConstraintConflict):
        await late_constraint(engine)
    initial_failures = deepcopy(engine.validity['failures'])
    economics = {name: deepcopy(broker.snapshot()['accounts']) for name, broker in engine.brokers.items()}
    for _ in range(2):
        with pytest.raises(ConstraintConflict):
            engine.update_constraints()
    assert engine.validity['failures'] == initial_failures
    assert len([row for row in read_audit(engine.store.path) if row['kind'] == 'SIMULATION_INVALID']) == 2
    with pytest.raises(ConstraintConflict):
        await send(engine, 'SETTLEMENT', .5, raw={})
    engine.gap('CLEANUP_GAP')
    await engine.close()
    assert {name: broker.snapshot()['accounts'] for name, broker in engine.brokers.items()} == economics
    assert all(order.filled == 5 and order.state == 'CANCELLED' and not order.uncertain
               for order in orders.values())


@pytest.mark.parametrize('mutation', ['unknown', 'generation', 'token', 'market', 'source_equal', 'later_source'])
async def test_non_conflicting_scope_or_time_does_not_invalidate_fills(tmp_path, mutation):
    engine, orders = await filled_engine(tmp_path)
    try:
        for broker in engine.brokers.values():
            current = deepcopy(broker.constraints)
            current['1'].update(tick='.01', authority='WS', source_mono=.055,
                                available_mono=.4, received_mono=.4)
            updates = {
                'unknown': {'known': False}, 'generation': {'generation': 2},
                'token': {'token': '2'}, 'market': {'market': 'another-market'},
                'source_equal': {'source_mono': next(iter(orders.values())).live_at},
                'later_source': {'source_mono': .11},
            }
            current['1'].update(updates[mutation])
            broker.update_constraints(current)
        assert engine.validity['status'] == 'VALID'
        assert all(order.filled == 10 and order.state == 'FILLED' for order in orders.values())
    finally:
        await engine.close()


async def test_snapshot_writer_failure_stays_invalid_and_raises(tmp_path, monkeypatch):
    engine, _ = await filled_engine(tmp_path)
    original = engine.store.snapshot

    def fail_write(*args):
        raise OSError('INJECTED_SNAPSHOT_WRITE_FAILURE')

    monkeypatch.setattr(engine.store, 'snapshot', fail_write)
    with pytest.raises(OSError, match='INJECTED_SNAPSHOT_WRITE_FAILURE'):
        await late_constraint(engine)
    assert engine.validity['status'] == 'INVALID'
    monkeypatch.setattr(engine.store, 'snapshot', original)
    await engine.close()
    assert read_state(engine.store.path)['validity']['status'] == 'INVALID'


async def test_legacy_report_cannot_create_valid_acceptance_window(tmp_path):
    legacy = {'market': {'slug': 'legacy'}, 'coverage': {'complete_window': True, 'gaps': []},
              'official_result': None, 'accounts': []}
    result = publish(tmp_path, [legacy])
    assert result['complete_windows'] == 1
    assert result['valid_complete_windows'] == 0 and result['status'] == 'FAIL'
    assert result['markets'][0]['validity']['status'] == 'UNVERIFIED_LEGACY'


@pytest.mark.parametrize('scenario', ['trades-only', 'cancel-half'])
async def test_partial_cancel_in_flight_still_checks_known_fill_history(tmp_path, scenario):
    engine, orders = await filled_engine(tmp_path, '5', scenario=scenario)
    try:
        await send(engine, 'RESOURCE', .13)
        broker = engine.brokers[scenario]
        order = orders[scenario]
        broker.cancel(order.id)
        before = deepcopy(broker.snapshot()['accounts'])
        with pytest.raises(ConstraintConflict):
            await late_constraint(engine, at=.2)
        assert order.state == 'CANCEL_IN_FLIGHT' and order.filled == 5
        assert not order.uncertain and broker.snapshot()['accounts'] == before
        assert engine.validity['failures'][0]['order_state'] == 'CANCEL_IN_FLIGHT'
    finally:
        await engine.close()


async def test_multiple_physical_orders_and_tokens_keep_correct_conflict_identity(tmp_path):
    from dataclasses import replace

    engine = await initial(tmp_path)
    try:
        await change(engine, .005)
        targets = {}
        controls = {}
        for name, broker in engine.brokers.items():
            targets[name] = broker.submit(intent(price='.095'))
            aligned = broker.submit(intent(price='.09'))
            other_token = broker.submit(replace(intent(price='.095'), token='2', outcome='NO'))
            controls[name] = [aligned, other_token]
        for at, token, size in [(.12, '1', '20'), (.121, '2', '10')]:
            await send(engine, 'FRAME', at, raw={
                'event_type': 'last_trade_price', 'market': engine.meta.condition_id,
                'asset_id': token, 'timestamp': str((engine.test_wall + at) * 1000),
                'price': '.09', 'size': size, 'side': 'SELL', 'trade_id': 'token-' + token,
            })
        before = {name: deepcopy(broker.snapshot()['accounts'])
                  for name, broker in engine.brokers.items()}
        with pytest.raises(ConstraintConflict):
            await late_constraint(engine)
        failures = engine.validity['failures']
        assert len(failures) == 2
        assert {(row['scenario'], row['order'], row['token']) for row in failures} == {
            (name, order.id, '1') for name, order in targets.items()}
        for name, broker in engine.brokers.items():
            assert broker.snapshot()['accounts'] == before[name]
            assert all(order.state == 'FILLED' and order.filled == 10 and not order.uncertain
                       for order in controls[name])
    finally:
        await engine.close()
