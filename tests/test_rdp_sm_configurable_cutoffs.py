"""Configured phase boundaries reach real admission, POST guards and cancellation."""

import asyncio
import math
from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from polymaker.config import Config, RDPRepairConfig, RDPSMConfig
from polymaker.rdp_sm_logging import HumanRunLog
from polymaker.repair_policy import PathPoint, RepairPolicy
from test_rdp_sm_ca_entry import ca_setup, confirm_ca
from test_rdp_sm_ca_entry import env as env
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_obi_execution import cleanup
from test_rdp_sm_opposite_ba import absorption
from test_rdp_sm_repair_ba_tolerance import tolerant_setup


async def at_elapsed(setup, monkeypatch, elapsed):
    venue, e, feed = setup[:3]
    now = e.monotonic()
    target = int(e.slug.rsplit('-', 1)[1]) + elapsed
    # Rebuild the simulated socket at the new official time, rather than
    # presenting the clock jump as an in-sequence market event.
    venue.time += target - feed.wall()
    previous = tuple(feed.books.items())
    feed.connect()
    feed.apply([{
        'event_type': 'book', 'market': e.meta.condition_id, 'asset_id': token,
        'timestamp': str(feed.wall() * 1000),
        'bids': [{'price': str(book.best_bid), 'size': str(book.best_bid_size)}],
        'asks': [{'price': str(book.best_ask), 'size': str(book.best_ask_size)}],
    } for token, book in previous])
    e.official_time, e.time_anchor = target, now

    async def server_time():
        return target + e.monotonic() - now

    monkeypatch.setattr(e.reads, 'server_time', server_time)
    await asyncio.sleep(.025)


def test_cutoffs_load_from_toml_without_changing_defaults(tmp_path):
    assert RDPSMConfig().normal_cutoff_sec == 120
    assert RDPRepairConfig().cutoff_sec == 240
    (tmp_path / 'config.toml').write_text('''[rdp_sm]
normal_entry_mode = "ca_v1"
normal_cutoff_sec = 150.5
[rdp_sm.ca_observe]
enabled = true
[rdp_sm.repair]
policy = "opposite_ba_v1"
cutoff_sec = 270.5
''')
    cfg = Config.load(tmp_path, load_env=False)
    assert cfg.rdp_sm.normal_cutoff_sec == 150.5
    assert cfg.rdp_sm.repair.cutoff_sec == 270.5
    # Operator settings are intentionally editable; defaults must be tested
    # against an empty config rather than the current live TOML.
    (tmp_path / 'config.toml').write_text('')
    defaults = Config.load(tmp_path, load_env=False)
    assert defaults.rdp_sm.normal_cutoff_sec == 120
    assert defaults.rdp_sm.repair.cutoff_sec == 240


@pytest.mark.parametrize('value', [0, -1, 300.001, math.inf, math.nan])
@pytest.mark.parametrize('phase', ['NORMAL', 'REPAIR'])
def test_invalid_cutoffs_cannot_cross_five_minute_market_end(phase, value):
    with pytest.raises(ValidationError):
        if phase == 'NORMAL':
            RDPSMConfig(normal_cutoff_sec=value)
        else:
            RDPRepairConfig(cutoff_sec=value)


@pytest.mark.parametrize('cutoff,elapsed,allowed', [(90, 95, False), (150, 130, True), (150, 150, False)])
async def test_ca_normal_entry_uses_configured_boundary(env, monkeypatch, cutoff, elapsed, allowed):
    setup = await ca_setup(env, monkeypatch)
    v, e, feed, _, normal = setup[:5]
    try:
        e.cfg.rdp_sm.normal_cutoff_sec = cutoff
        await at_elapsed(setup, monkeypatch, elapsed)
        await confirm_ca(feed)
        result = await normal.enter(D(0), D(0), 'configured-normal')
        assert bool(v.post_requests) is allowed
        assert normal.cutoff == cutoff
        if allowed:
            assert result[0].close_barrier == 'SAFE_TO_CONTINUE'
            assert v.post_requests[0][0]['postOnly'] is True
    finally:
        await cleanup(setup)


async def test_normal_signing_crossing_configured_boundary_aborts_native_post(env, monkeypatch):
    setup = await ca_setup(env, monkeypatch)
    v, e, feed, lifecycle, normal = setup[:5]
    client = e.gateway._client
    try:
        e.cfg.rdp_sm.normal_cutoff_sec = 150
        await at_elapsed(setup, monkeypatch, 130)
        native = client.create_order
        crossed = []

        def cross(*args, **kwargs):
            signed = native(*args, **kwargs)
            crossed.append(True)
            e.official_time = int(e.slug.rsplit('-', 1)[1]) + 150
            e.time_anchor = e.monotonic()
            return signed

        monkeypatch.setattr(client, 'create_order', cross)
        await confirm_ca(feed)
        await normal.enter(D(0), D(0), 'normal-cross-signing')
        assert crossed and not v.post_requests
        assert e.attempts and all(a.outcome == 'ABORTED' and not a.post_started for a in e.attempts)
        await e.journal.flush()
        logs = journal_records(e.journal.path)
        assert lifecycle.guard_reason == 'MARKET_SUBMISSION_CUTOFF' or any(
            r['kind'].endswith('_close_requested')
            and r['data']['payload']['reason'] == 'MARKET_SUBMISSION_CUTOFF' for r in logs
        )
    finally:
        await cleanup(setup)


@pytest.mark.parametrize('cutoff,elapsed,allowed', [(90, 95, False), (270, 250, True), (270, 270, False)])
async def test_ba_repair_entry_uses_configured_boundary(env, monkeypatch, cutoff, elapsed, allowed):
    setup, c = await tolerant_setup(env, monkeypatch)
    v, e, feed = setup[:3]
    try:
        e.cfg.rdp_sm.repair.cutoff_sec = cutoff
        await at_elapsed(setup, monkeypatch, elapsed)
        absorption(feed, '2')
        result = await c.repair(*e.economic_inventory(), 'configured-repair')
        assert len(v.post_requests) == (2 if allowed else 1)
        assert c.cutoff == cutoff
        if allowed:
            assert result[0].close_barrier == 'SAFE_TO_CONTINUE'
            assert v.post_requests[-1][0]['postOnly'] is True
        assert c.boundary_close_reason(None, cutoff) == 'MARKET_SUBMISSION_CUTOFF'
    finally:
        await cleanup(setup)


async def test_configured_repair_cutoff_cancels_existing_fixed_order(env, monkeypatch):
    setup, c = await tolerant_setup(env, monkeypatch)
    v, e, feed, _, _, wire = setup[:6]
    try:
        e.cfg.rdp_sm.repair.cutoff_sec = 100.18
        await at_elapsed(setup, monkeypatch, 100)
        absorption(feed, '2')
        episode = next(reversed(feed.opposite_ba.opportunities.values()))
        result, _ = await c.repair(*e.economic_inventory(), 'cutoff-active')
        assert len(v.post_requests) == 2
        assert result.close_barrier == 'SAFE_TO_CONTINUE'
        await e.journal.flush()
        events = [r['data']['payload'] for r in journal_records(e.journal.path)
                  if r['kind'] in {'sm_ba_close_requested', 'sm_obi_close_requested'}
                  and r['data']['payload']['window_id'] == 'cutoff-active']
        assert events[0]['reason'] == 'MARKET_SUBMISSION_CUTOFF'
        cancel_at = next(row[2] for row in wire.timeline if row[0] == 'DELETE'
                         and row[2] >= result.t0_monotonic)
        assert cancel_at < episode.order_deadline
    finally:
        await cleanup(setup)


def test_path_policy_and_display_use_the_configured_limits():
    cfg = RDPRepairConfig(cutoff_sec=270)
    policy = RepairPolicy(cfg.path, mode='SL')
    point = PathPoint(250, .5, .02, 1, 5, -.3)
    args = {'original_side': 'YES', 'original_qty': 5, 'first_fill_elapsed': 90, 'cutoff_sec': cfg.cutoff_sec}
    assert policy.decide(point, **args)['reason'] != 'CUTOFF'
    assert policy.decide(PathPoint(270, .5, .02, 1, 5, -.3), **args)['reason'] == 'CUTOFF'
    log = HumanRunLog(repair_mode=True, normal_cutoff_sec=150, repair_cutoff_sec=270)
    try:
        lines = '\n'.join(log._market_lines({}))
        assert 'NORMAL 15 <= T < 150s' in lines and 'REPAIR 15 <= T < 270s' in lines
    finally:
        log.close()


@pytest.mark.parametrize('cutoff,expected_posts', [(240, 1), (270, 2)])
async def test_matched_default_tp_obeys_repair_cutoff_after_t240(
    env, monkeypatch, cutoff, expected_posts
):
    from test_rdp_sm_ca_matched_tp import fill, tp_setup
    from test_rdp_sm_repair import reflect_positions

    setup = await tp_setup(env, monkeypatch)
    v, e, feed, _, normal = setup[:5]
    e.cfg.rdp_sm.normal_cutoff_sec = 280
    e.cfg.rdp_sm.repair.cutoff_sec = cutoff
    native = e.gateway.send_prepared

    async def fill_each(signed, **kwargs):
        response = await native(signed, **kwargs)
        oid = signed.order_ids[0]
        fill(v, oid, 'fill-' + str(len(v.post_requests)))
        v.rows[oid]['status'] = 'MATCHED'
        reflect_positions(v)
        return response

    try:
        await at_elapsed(setup, monkeypatch, 250)
        monkeypatch.setattr(e.gateway, 'send_prepared', fill_each)
        await confirm_ca(feed)
        result, _ = await asyncio.wait_for(normal.enter(D(0), D(0), 'late-tp'), 5)
        assert result.close_barrier == 'SAFE_TO_CONTINUE'
        assert len(v.post_requests) == expected_posts
        assert e.economic_inventory() == (D(5), D(5 if expected_posts == 2 else 0))
    finally:
        await cleanup(setup)
