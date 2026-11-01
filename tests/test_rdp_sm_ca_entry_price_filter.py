"""CA Taker entry ceiling, including signed price and native POST admission."""
import asyncio
from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from polymaker.config import Config, RDPCATakerConfig
from test_rdp_sm_ca_entry import confirm_ca
from test_rdp_sm_ca_entry import env as env
from test_rdp_sm_ca_taker import setup_taker
from test_rdp_sm_m1 import CID
from test_rdp_sm_obi_execution import cleanup


def test_deployed_price_filter_and_strict_configuration():
    cfg = Config.load('config', load_env=False).rdp_sm.ca_taker
    assert cfg.entry_price_ceiling_enabled and cfg.entry_price_ceiling == D('.90')
    assert not RDPCATakerConfig(entry_price_ceiling_enabled=False).entry_price_ceiling_enabled
    for invalid in [{'entry_price_ceiling_enabled': 'false'}, {'entry_price_ceiling_enabled': 1},
                    {'entry_price_ceiling': 0}, {'entry_price_ceiling': 1},
                    {'entry_price_ceiling': 'NaN'}]:
        with pytest.raises(ValidationError):
            RDPCATakerConfig(**invalid)


def set_ask(feed, token, ask):
    feed.apply({'event_type': 'book', 'market': CID, 'asset_id': token,
                'timestamp': str(feed.wall()*1000),
                'bids': [{'price': str(D(ask)-D('.01')), 'size': '80'}],
                'asks': [{'price': ask, 'size': '20'}]})


@pytest.mark.parametrize('token', ['1', '2'])
@pytest.mark.parametrize('enabled', [True, False])
@pytest.mark.parametrize('ask', ['.89', '.90', '.91'])
async def test_entry_ceiling_boundary_and_disabled_native_post(env, monkeypatch, token, enabled, ask):
    setup = await setup_taker(env, monkeypatch)
    v, e, feed, _, normal = setup[:5]
    e.cfg.rdp_sm.ca_taker.entry_price_ceiling_enabled = enabled
    set_ask(feed, token, ask)
    try:
        await confirm_ca(feed, token)
        result = await asyncio.wait_for(normal.enter(D(0), D(0), 'session:w1'), 5)
        if enabled and D(ask) >= D('.90'):
            assert result is None
            assert normal.quote_block_reason == 'CA_TAKER_ENTRY_PRICE_CEILING'
            assert not v.post_requests and not feed.ca_entry.consumed
        else:
            window, _ = result
            assert window.close_barrier == 'SAFE_TO_CONTINUE', window.block_reason
            assert len(v.post_requests) == 1 and feed.ca_entry.consumed
            assert window.requested_quotes[0].price == float(ask)
            assert v.post_requests[0][0]['postOnly'] is False
    finally:
        await cleanup(setup)


@pytest.mark.parametrize('ask,slippage,ceiling,enabled,reason', [
    ('.89', 2, '.90', True, 'CA_TAKER_ENTRY_PRICE_CEILING'),
    ('.80', 0, '.80', True, 'CA_TAKER_ENTRY_PRICE_CEILING'),
    ('.99', 1, '.90', False, 'CA_TAKER_MAX_PRICE'),
])
async def test_ceiling_uses_signed_price_and_preserves_absolute_max(
    env, monkeypatch, ask, slippage, ceiling, enabled, reason
):
    setup = await setup_taker(env, monkeypatch)
    v, e, feed, _, normal = setup[:5]
    e.cfg.rdp_sm.ca_taker.entry_price_ceiling_enabled = enabled
    e.cfg.rdp_sm.ca_taker.entry_price_ceiling = D(ceiling)
    e.cfg.rdp_sm.ca_taker.max_slippage_ticks = slippage
    set_ask(feed, '1', ask)
    try:
        await confirm_ca(feed)
        assert await normal.enter(D(0), D(0), 'session:w1') is None
        assert normal.quote_block_reason == reason
        assert not v.post_requests and not feed.ca_entry.consumed
    finally:
        await cleanup(setup)


@pytest.mark.parametrize('bypass_coordinator', [False, True])
async def test_enabling_ceiling_after_signing_is_checked_before_post(env, monkeypatch, bypass_coordinator):
    setup = await setup_taker(env, monkeypatch)
    v, e, feed, _, normal = setup[:5]
    e.cfg.rdp_sm.ca_taker.entry_price_ceiling_enabled = False
    set_ask(feed, '1', '.91')
    native = e.gateway.send_prepared
    checked = []

    async def send(signed, **kwargs):
        e.cfg.rdp_sm.ca_taker.entry_price_ceiling_enabled = True
        checked.append(True)
        if bypass_coordinator:
            kwargs['submit_guard'] = lambda: True
            with pytest.raises(ValueError, match='SM_MAKER_ONLY'):
                await native(signed, **kwargs)
            return None
        return await native(signed, **kwargs)

    monkeypatch.setattr(e.gateway, 'send_prepared', send)
    try:
        await confirm_ca(feed)
        await asyncio.wait_for(normal.enter(D(0), D(0), 'session:w1'), 5)
        assert checked
        assert not v.post_requests and not feed.ca_entry.consumed
    finally:
        await cleanup(setup)
