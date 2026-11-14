"""Unfiltered M1 simulations share M234 sizing without widening live settings."""

import importlib.util
import json
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path

import pytest
from pydantic import ValidationError

from m1234_fixtures import fixture_events
from polymaker.m1_live_config import M1LiveConfig
from polymaker.m1_live_state import M1Ledger
from polymaker.m1234_sim.audit import reconcile
from polymaker.m1234_sim.config import M1SimulationConfig
from polymaker.m1234_sim.m1_ledger import M1SimulationLedger
from polymaker.m1234_sim.store import read_audit
from polymaker.rdp_sm_m1_policy import baseline_quantity, budget_quantity
from test_m1234_sim_engine import drive, policy_setup


def unfiltered():
    return M1SimulationConfig(order_budget_usdc=5, minimum_price=0, maximum_price=1)


def test_contract_preserves_signals_and_m234_but_removes_both_m1_bands():
    contract = json.loads(Path('config/m1234-sim-contract.json').read_text())
    profiles = contract['profiles']
    first = profiles['config']
    assert first == profiles['config/m1-live']
    assert first['order_budget_usdc'] == D(profiles['m234']['risk_usdc']) == 5
    assert M1SimulationConfig.model_validate(first) == unfiltered()
    assert first['signal'] == M1LiveConfig().model_dump(mode='json')['signal']
    assert (M1LiveConfig().minimum_price, M1LiveConfig().maximum_price) == (D('.01'), D('.19'))
    for change in ({'order_budget_usdc': 5}, {'maximum_price': 1}, {'minimum_price': 0}):
        with pytest.raises(ValidationError):
            M1LiveConfig(**change)


@pytest.mark.parametrize('change', [
    {'minimum_price': '-.01'}, {'maximum_price': '1.01'},
    {'minimum_price': '.8', 'maximum_price': '.2'}, {'maximum_price': 'NaN'},
    {'order_budget_usdc': 6},
])
def test_simulation_config_still_rejects_invalid_economics(change):
    with pytest.raises(ValidationError):
        M1SimulationConfig.model_validate({**unfiltered().model_dump(), **change})


@pytest.mark.parametrize('price', ['.005', '.15', '.50', '.95'])
async def test_native_post_uses_five_usdc_floor_and_no_strategy_band(tmp_path, price):
    feed, _, broker, policy, _, tick = await policy_setup(
        tmp_path, price=price, settings=unfiltered())
    if price == '.005':
        feed.books['1'] = replace(feed.books['1'], tick=D('.001'))
        feed.tick_verified['1'] = D('.001')
        broker.update_books(feed.books)
    try:
        await tick(0)
        await tick(.01)
        assert len(broker.orders) == 1
        order = next(iter(broker.orders.values()))
        qty = budget_quantity(D(price), 5, 5)
        assert D(order.intent.price) == D(price)
        assert D(order.intent.quantity) == qty
        assert D(price)*qty <= 5
        assert policy.e.ledger.committed() == D(price)*qty
        assert policy.e.ledger.get('identity')['order_budget'] == '5'
        assert policy.e.ledger.sizing_policy == 'BUDGET_FLOOR_001_MAX5_V1'
        await tick(.21)
        broker.advance(.3)
        broker.trade({'asset_id': '1', 'side': 'SELL', 'price': price,
                     'size': '82', 'trade_id': 'partial'}, 'direct')
        assert order.filled == 2  # 80 ahead first, then a partial fill of our order.
        assert order.filled < qty
        broker.check()
    finally:
        await policy.close()


async def test_uncertain_retry_retains_two_five_usdc_exposures_and_reopens(tmp_path):
    _, _, broker, policy, _, tick = await policy_setup(
        tmp_path, price='.5', settings=unfiltered(), fault='UNKNOWN')
    ledger = policy.e.ledger
    identity = {'mode': 'SIMULATION_ONLY', 'market': policy.e.slug,
                'settings': unfiltered().fingerprint}
    path = ledger.path
    try:
        for at in (0, .01, .21, .22, .33, .34):
            await tick(at)
        assert len(broker.orders) == 2
        assert ledger.committed() == 10
        assert broker.account('M1-test').reserved == 10
        assert len(ledger.uncertain_posts()) == 1
    finally:
        await policy.close()
    reopened = M1SimulationLedger(path, identity, order_budget=5)
    assert reopened.committed() == 10
    reopened.close()
    with pytest.raises(ValueError, match='IDENTITY_OR_SETTINGS_CHANGED'):
        M1Ledger(path, identity, order_budget=5)


def test_live_ledger_keeps_one_usdc_sizing_and_minimum_order_rule(tmp_path):
    ledger = M1Ledger(tmp_path/'live.sqlite3', {})
    try:
        assert ledger.order_quantity('.15', 5) == baseline_quantity('.15', 5) == D('6.7')
        with pytest.raises(ValueError, match='AMOUNT_NOT_LEGAL'):
            ledger.order_quantity('.50', 5)
    finally:
        ledger.close()
    sim = M1SimulationLedger(tmp_path/'sim.sqlite3', {}, order_budget=5)
    try:
        for price in ('0', '1', 'NaN'):
            with pytest.raises(ValueError):
                sim.order_quantity(price, 5)
        with pytest.raises(ValueError):
            sim.order_quantity('.95', 6)  # Still respect venue minimum quantity.
    finally:
        sim.close()


async def test_both_profiles_accept_high_price_from_same_raw_tape_and_reconcile(tmp_path):
    tape = fixture_events(m1_price='.50', m1_ask='.51')
    for key in tape[0]['definition']['settings']['profiles']:
        tape[0]['definition']['settings']['profiles'][key] = unfiltered().model_dump(mode='json')
    await drive(tape, tmp_path/'replay')
    database = tmp_path/'replay/simulation.sqlite3'
    assert reconcile(database)['status'] == 'PASS'
    submits = [r for r in read_audit(database) if r['kind'] == 'SUBMIT'
               and r['payload']['intent']['strategy'] == 'M1']
    assert len(submits) == 4  # Two profiles, two independently simulated queues.
    assert {r['payload']['intent']['profile'] for r in submits} == {'M1-virtual', 'M1-live-mirror'}
    assert all(D(r['payload']['intent']['price']) == D('.5') for r in submits)
    assert all(D(r['payload']['intent']['quantity']) == 10 for r in submits)


def test_new_launch_keeps_chat_monitoring_disabled_and_drops_old_sidecar():
    spec = importlib.util.spec_from_file_location('sim_start', 'ops/m1234_sim_start.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    old = {'enabled': False, 'notify_every_check': False, 'disabled_at': 123,
           'disabled_reason': 'USER_REQUEST_STOP_MONITORING_THIS_CHAT',
           'signal_validation': {'source_run': 'old'}, 'unrelated': 'preserved'}
    new = module.next_control(old, unit='new.service', run='/new', thread='same', started_at=456)
    assert new['enabled'] is False and new['notify_every_check'] is False
    assert new['disabled_at'] == 123 and new['unrelated'] == 'preserved'
    assert 'signal_validation' not in new and 'signal_validation' in old
    assert new['run_directory'] == '/new'
