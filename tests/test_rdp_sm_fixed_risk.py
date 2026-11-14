"""Principal-budget CA sizing through native signed BUY, TP and expiry recovery."""
import asyncio
from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from polymaker.config import Config, RDPSMConfig
from polymaker.rdp_sm_sizing import fixed_risk_size
from test_rdp_sm_ca_entry import confirm_ca
from test_rdp_sm_ca_entry import env as env
from test_rdp_sm_ca_entry_price_filter import set_ask
from test_rdp_sm_ca_matched_tp import fill
from test_rdp_sm_ca_taker import reflect, setup_taker, taker_fill
from test_rdp_sm_obi_execution import cleanup


@pytest.mark.parametrize('price,risk,maximum,expected', [
    ('.50', '5', '15', '10'), ('.79', '5', '15', '6.32'),
    ('.20', '5', '15', '15'), ('.35', '2', '15', '5.71'),
])
def test_budget_quantity_rounds_down_and_respects_share_cap(price, risk, maximum, expected):
    ideal, qty, reasons = fixed_risk_size(D(price), D(risk), D(5), D(maximum))
    assert qty == D(expected) and qty*D(price) <= D(risk)
    assert ('MAX_ORDER_SIZE' in reasons) == (ideal > D(maximum))


def test_small_budget_is_not_rounded_up_to_market_minimum():
    with pytest.raises(ValueError, match='BELOW_MARKET_MINIMUM'):
        fixed_risk_size(D('.79'), D(1), D(5), D(15))


def test_config_modes_and_amount_load_without_changing_default(tmp_path):
    assert RDPSMConfig().sizing_mode == 'fixed_shares'
    (tmp_path/'config.toml').write_text('''[rdp_sm]
normal_entry_mode = "ca_v1"
sizing_mode = "fixed_risk"
risk_per_trade_usdc = "5"
[rdp_sm.ca_observe]
enabled = true
''')
    cfg = Config.load(tmp_path, load_env=False).rdp_sm
    assert cfg.sizing_mode == 'fixed_risk' and cfg.risk_per_trade_usdc == D(5)
    for bad in [0, -1, 'NaN', 'Infinity']:
        with pytest.raises(ValidationError):
            RDPSMConfig(risk_per_trade_usdc=bad)
    with pytest.raises(ValidationError):
        RDPSMConfig(sizing_mode='fixed_risk')


@pytest.mark.parametrize('taking', [False, True])
async def test_fixed_risk_native_order_principal_and_tp_cover_larger_opening(env, monkeypatch, taking):
    setup = await setup_taker(env, monkeypatch, tp=True)
    v, e, feed, _, normal = setup[:5]
    e.cfg.rdp_sm.ca_entry_execution = 'taker' if taking else 'maker'
    e.cfg.rdp_sm.sizing_mode = 'fixed_risk'
    e.cfg.rdp_sm.risk_per_trade_usdc = D(5)
    e.cfg.rdp_sm.ca_matched_tp.profit_per_share = D('.05')
    set_ask(feed, '1', '.51')
    native = e.gateway.send_prepared

    async def send(signed, **kwargs):
        opening = not v.post_requests
        if opening:
            assert D(signed.orders[0].order.makerAmount)/1_000_000 <= 5
            assert D(str(signed.quotes[0].size)) > 5
        response = await native(signed, **kwargs)
        oid = signed.order_ids[0]
        if opening and taking:
            taker_fill(setup, oid)
        else:
            fill(v, oid, 'fill-'+str(len(v.post_requests)), qty=float(signed.quotes[0].size))
            v.rows[oid]['status'] = 'MATCHED'
            reflect(setup)
        return response

    try:
        monkeypatch.setattr(e.gateway, 'send_prepared', send)
        await confirm_ca(feed)
        result, sizing = await asyncio.wait_for(normal.enter(D(0), D(0), 'session:w1'), 8)
        assert result.close_barrier == 'SAFE_TO_CONTINUE', result.block_reason
        assert len(v.post_requests) == 2
        assert e.attempts[0].principal_limit <= 5
        assert sizing['sizing_mode'] == 'fixed_risk'
        assert abs(e.economic_inventory()[0]-e.economic_inventory()[1]) <= D('.01')
    finally:
        await cleanup(setup)


async def test_price_improved_risk_buy_tp_covers_actual_quantity_above_planned_share_cap(env, monkeypatch):
    setup = await setup_taker(env, monkeypatch, tp=True)
    v, e, feed, _, normal = setup[:5]
    e.cfg.rdp_sm.sizing_mode = 'fixed_risk'
    e.cfg.rdp_sm.risk_per_trade_usdc = D(5)
    e.cfg.rdp_sm.ca_matched_tp.profit_per_share = D('.05')
    set_ask(feed, '1', '.34')
    actual = D('15.145454')
    native = e.gateway.send_prepared

    async def send(signed, **kwargs):
        opening = not v.post_requests
        response = await native(signed, **kwargs)
        oid = signed.order_ids[0]
        if opening:
            taker_fill(setup, oid, quantity=actual, price=D('.33'))
        else:
            fill(v, oid, 'improved-tp', qty=float(signed.quotes[0].size))
            v.rows[oid]['status'] = 'MATCHED'
            reflect(setup)
        return response

    try:
        monkeypatch.setattr(e.gateway, 'send_prepared', send)
        await confirm_ca(feed)
        entered = await asyncio.wait_for(normal.enter(D(0), D(0), 'session:w1'), 8)
        assert entered is not None, {'quote': normal.quote_block_reason,
            'guard': normal.lifecycle.guard_reason, 'fatal': e.fatal,
            'signal': normal.signal_block_reason, 'posts': len(v.post_requests)}
        result, _ = entered
        assert result.close_barrier == 'SAFE_TO_CONTINUE', result.block_reason
        assert len(v.post_requests) == 2
        assert D(str(e.attempts[0].quote.size)) <= 15
        tp_qty = D(str(e.attempts[1].quote.size))
        assert actual <= tp_qty <= actual+D('.01') and tp_qty > 15
    finally:
        await cleanup(setup)


async def test_risk_sized_opening_has_expiry_taker_cover(env, monkeypatch):
    setup = await setup_taker(env, monkeypatch, tp=True)
    v, e, feed, _, normal = setup[:5]
    e.cfg.rdp_sm.sizing_mode = 'fixed_risk'
    e.cfg.rdp_sm.risk_per_trade_usdc = D('2.60')
    set_ask(feed, '1', '.51')
    e.cfg.rdp_sm.ca_matched_tp.taker_exit_on_expiry = True
    e.cfg.rdp_sm.ca_matched_tp.ttl_sec = .06
    e.cfg.rdp_sm.ca_matched_tp.profit_per_share = D('.05')
    e.cfg.rdp_sm.repair.ba_enabled = False
    native = e.gateway.send_prepared

    async def send(signed, **kwargs):
        response = await native(signed, **kwargs)
        if not signed.post_only:
            taker_fill(setup, signed.order_ids[0])
        return response

    try:
        monkeypatch.setattr(e.gateway, 'send_prepared', send)
        await confirm_ca(feed)
        entered = await asyncio.wait_for(normal.enter(D(0), D(0), 'session:w1'), 8)
        assert entered is not None, {'quote': normal.quote_block_reason,
            'guard': normal.lifecycle.guard_reason, 'fatal': e.fatal,
            'signal': normal.signal_block_reason, 'posts': len(v.post_requests)}
        result, _ = entered
        assert result.close_barrier == 'SAFE_TO_CONTINUE', result.block_reason
        assert len(v.post_requests) == 3
        assert e.attempts[0].principal_limit <= D('2.60')
        yes, no = e.economic_inventory()
        assert no >= yes > 5  # Cent rounding may leave explicit extra reverse shares.
        assert e.attempts[-1].quote.window_id.endswith(':ca-expiry-exit')
    finally:
        await cleanup(setup)


async def test_risk_mode_preserves_exit_capital_admission_and_repair_share_limit(env, monkeypatch):
    setup = await setup_taker(env, monkeypatch, tp=True)
    v, e, feed, _, normal = setup[:5]
    e.cfg.rdp_sm.sizing_mode = 'fixed_risk'
    e.cfg.rdp_sm.risk_per_trade_usdc = D(5)
    e.cfg.rdp_sm.ca_matched_tp.taker_exit_on_expiry = True
    e.cfg.rdp_sm.repair.ba_enabled = False
    try:
        assert e.delta_candidate_allowed(D(0), 'YES', D(10), normal_entry=True)
        assert not e.delta_candidate_allowed(D(0), 'YES', D(10))
        await confirm_ca(feed)
        assert await normal.enter(D(0), D(0), 'session:w1') is None
        assert not v.post_requests and not feed.ca_entry.consumed
    finally:
        await cleanup(setup)


async def test_risk_budget_below_taker_minimum_is_skipped_without_topup(env, monkeypatch):
    setup = await setup_taker(env, monkeypatch)
    v, e, feed, _, normal = setup[:5]
    e.cfg.rdp_sm.sizing_mode = 'fixed_risk'
    e.cfg.rdp_sm.risk_per_trade_usdc = D('.50')
    set_ask(feed, '1', '.05')
    try:
        await confirm_ca(feed)
        assert await normal.enter(D(0), D(0), 'session:w1') is None
        assert normal.quote_block_reason == 'CA_TAKER_MIN_NOTIONAL'
        assert not v.post_requests and not feed.ca_entry.consumed
    finally:
        await cleanup(setup)
