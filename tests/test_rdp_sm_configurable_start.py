"""Early CA openings must retain immediate TP and expiry-exit protection."""
import asyncio
from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from polymaker.config import RDPSMConfig
from polymaker.rdp_sm_logging import HumanRunLog
from test_rdp_sm_ca_entry import confirm_ca
from test_rdp_sm_ca_entry import env as env
from test_rdp_sm_ca_taker import setup_taker, taker_fill
from test_rdp_sm_configurable_cutoffs import at_elapsed
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_obi_execution import cleanup


@pytest.mark.parametrize('start,cutoff', [(-1, 200), (300, 300), (200, 200), (201, 200)])
def test_invalid_start_or_empty_entry_window_is_rejected(start, cutoff):
    with pytest.raises(ValidationError):
        RDPSMConfig(normal_start_sec=start, normal_cutoff_sec=cutoff)


async def test_early_taker_opening_posts_tp_and_expiry_exit_before_t15(env, monkeypatch):
    setup = await setup_taker(env, monkeypatch, tp=True)
    v, e, feed, _, normal = setup[:5]
    e.cfg.rdp_sm.normal_start_sec = 10
    e.cfg.rdp_sm.normal_cutoff_sec = 200
    e.cfg.rdp_sm.ca_matched_tp.taker_exit_on_expiry = True
    e.cfg.rdp_sm.ca_matched_tp.profit_per_share = D('.05')
    e.cfg.rdp_sm.ca_matched_tp.ttl_sec = .06
    e.cfg.rdp_sm.repair.ba_enabled = False
    native = e.gateway.send_prepared
    sent = []

    async def send(signed, **kwargs):
        sent.append((e.official_time+e.monotonic()-e.time_anchor-int(e.slug.rsplit('-', 1)[1]),
                     signed.post_only))
        response = await native(signed, **kwargs)
        # Opening and final FAK fill. The intervening Maker TP remains unfilled.
        if not signed.post_only:
            taker_fill(setup, signed.order_ids[0])
        return response

    try:
        await at_elapsed(setup, monkeypatch, 11)
        monkeypatch.setattr(e.gateway, 'send_prepared', send)
        await confirm_ca(feed)
        result, _ = await asyncio.wait_for(normal.enter(D(0), D(0), 'early-ca:w1'), 8)
        assert result.close_barrier == 'SAFE_TO_CONTINUE', result.block_reason
        assert [maker for _, maker in sent] == [False, True, False]
        assert all(10 <= elapsed < 15 for elapsed, _ in sent)
        assert e.economic_inventory() == (D(5), D(5))
        await e.journal.flush()
        logs = journal_records(e.journal.path)
        assert any(r['kind'] == 'sm_ca_matched_tp_lease_started' for r in logs)
        assert any(r['kind'] == 'sm_ca_expiry_exit_result'
                   and r['data']['payload']['reason'] == 'PAIRED' for r in logs)
    finally:
        await cleanup(setup)


def test_console_reports_configured_start_and_cutoff():
    log = HumanRunLog(repair_mode=True, normal_start_sec=10, normal_cutoff_sec=200)
    try:
        lines = '\n'.join(log._market_lines({}))
        assert 'NORMAL 10 <= T < 200s' in lines
    finally:
        log.close()
