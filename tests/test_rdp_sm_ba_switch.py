"""BA disable switch preserves CA/TP while closing both BA admission routes."""

import asyncio
from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from polymaker.config import Config, RDPRepairConfig
from polymaker.rdp_sm_ca_matched_tp import CAMatchedTP
from test_rdp_sm_ca_tp_direction_handoff import delayed_tp, observe
from test_rdp_sm_cached_reads import attach, finish
from test_rdp_sm_obi_execution import cleanup
from test_rdp_sm_opposite_ba import absorption, controller_fixture, setup_exit
from test_rdp_sm_repair import env as env
from test_rdp_sm_restored_tp import filled, restored_setup, until


def test_switch_is_strict_and_current_config_disables_only_ba():
    assert RDPRepairConfig().ba_enabled is True
    for value in ("false", 0, None):
        with pytest.raises(ValidationError):
            RDPRepairConfig(ba_enabled=value)
    cfg = Config.load("config", load_env=False).rdp_sm
    assert cfg.repair.policy == "opposite_ba_v1" and cfg.repair.ba_enabled is False
    assert cfg.normal_entry_mode == "ca_v1"
    assert cfg.ca_matched_tp.enabled and cfg.ca_matched_tp.restore_after_ba


def test_reenable_restores_ba_selection_without_reconfiguring_detector():
    controller, feed, clock = controller_fixture()
    clock.now = .01
    absorption(feed, "2")
    controller.cfg.ba_enabled = False
    assert controller.select(feed.snapshot())[0] is None
    controller.cfg.ba_enabled = True
    assert controller.select(feed.snapshot())[0] == "NO"


async def test_disabled_plain_repair_never_prepares_posts_or_consumes_ba(env, monkeypatch):
    setup, controller = await setup_exit(env, monkeypatch)
    v, e, feed = setup[:3]
    try:
        e.cfg.rdp_sm.repair.ba_enabled = False
        absorption(feed, "2")
        assert controller.select(feed.snapshot())[0] is None
        assert controller.signal_block_reason == "BA_REPAIR_DISABLED"
        assert not controller.candidates(feed.snapshot(), D(5), D(0), "disabled")
        assert await controller.repair(D(5), D(0), "disabled") is None
        assert len(v.post_requests) == 1  # The fixture's opening only.
        assert not feed.opposite_ba.states["2"].consumed
        assert e.economic_inventory() == (D(5), D(0)) and e.repair_episode is not None
    finally:
        await cleanup(setup)


async def test_disable_at_plain_native_post_blocks_prepared_ba(env, monkeypatch):
    setup, controller = await setup_exit(env, monkeypatch)
    v, e, feed = setup[:3]
    native = e.gateway.send_prepared

    async def switch_off(*args, **kwargs):
        e.cfg.rdp_sm.repair.ba_enabled = False
        return await native(*args, **kwargs)

    try:
        absorption(feed, "2")
        monkeypatch.setattr(e.gateway, "send_prepared", switch_off)
        result = await controller.repair(D(5), D(0), "disable-before-post")
        assert len(v.post_requests) == 1
        assert not feed.opposite_ba.states["2"].consumed
        if result is not None:
            assert not any(a["post_started"] for a in result[0].placement_results)
    finally:
        await cleanup(setup)


async def test_disable_after_plain_post_cancels_ba_and_preserves_close_proofs(env, monkeypatch):
    setup, controller = await setup_exit(env, monkeypatch)
    v, e, feed = setup[:3]
    native = e.gateway.send_prepared

    async def disable_after_ack(*args, **kwargs):
        response = await native(*args, **kwargs)
        e.cfg.rdp_sm.repair.ba_enabled = False
        return response

    try:
        absorption(feed, "2")
        monkeypatch.setattr(e.gateway, "send_prepared", disable_after_ack)
        result, _ = await controller.repair(D(5), D(0), "disable-active-ba")
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert len(v.post_requests) == 2
        assert e.attempts[-1].order_id in e.terminal
        assert e.economic_inventory() == (D(5), D(0))
        assert not any(row["status"] == "LIVE" for row in v.rows.values())
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("cached", [False, True])
async def test_disabled_ba_keeps_original_ca_tp_live_and_tp_can_fill(env, monkeypatch, tmp_path, cached):
    async with delayed_tp(env, monkeypatch, tmp_path, cached, ba_enabled=False) as c:
        await c.confirm()
        absorption(c.feed, c.opposite)
        await asyncio.sleep(.05)
        assert len(c.v.post_requests) == 2  # CA and initial TP remain operational.
        assert c.v.rows[c.tp]["status"] == "LIVE"
        assert not any(c.tp in ids for ids in c.v.cancels)
        assert not c.task.done()
        assert CAMatchedTP.ba_handoff(c.owner) is False  # Legacy TP route too.
        observe(c.v, c.e, c.tp, "tp-without-ba")
        result, _ = await asyncio.wait_for(c.task, 5)
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert c.e.economic_inventory() == (D(5), D(5))
        assert len(c.v.post_requests) == 2


@pytest.mark.parametrize("cached", [False, True])
async def test_disabled_ba_restores_tp_in_ordinary_repair_despite_valid_ba(env, monkeypatch, tmp_path, cached):
    setup, controller = await restored_setup(env, monkeypatch)
    v, e, feed = setup[:3]
    companion = (await attach(setup, tmp_path))[0] if cached else None
    task = None
    try:
        e.cfg.rdp_sm.repair.ba_enabled = False
        absorption(feed, "2")
        assert controller.tp_ba_signal(feed.snapshot()) is None
        task = asyncio.create_task(controller.repair(*e.economic_inventory(), "tp-only"))
        await until(lambda: len(v.post_requests) == 2 and e.attempts[-1].outcome == "ACK")
        attempt = e.attempts[-1]
        assert attempt.quote.window_id.endswith(":restored-tp")
        absorption(feed, "2")
        await asyncio.sleep(.03)
        assert len(v.post_requests) == 2 and v.rows[attempt.order_id]["status"] == "LIVE"
        filled(v, attempt.order_id, "restored-without-ba")
        result, sizing = await asyncio.wait_for(task, 5)
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert sizing["execution_role"] == "RESTORED_TP"
        assert e.economic_inventory() == (D(5), D(5))
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if companion:
            await finish(setup, companion)
        else:
            await cleanup(setup)


async def test_disable_after_tp_handoff_preparation_preserves_tp_and_aborts_ba(env, monkeypatch, tmp_path):
    async with delayed_tp(env, monkeypatch, tmp_path) as c:
        await c.confirm()
        native = c.e.gateway.prepare

        async def prepare(*args, **kwargs):
            signed = await native(*args, **kwargs)
            if ":ba-handoff:" in c.e.attempts[-1].quote.window_id:
                c.e.cfg.rdp_sm.repair.ba_enabled = False
            return signed

        monkeypatch.setattr(c.e.gateway, "prepare", prepare)
        absorption(c.feed, c.opposite)
        await until(lambda: len(c.e.attempts) == 3 and c.e.attempts[-1].outcome == "ABORTED")
        assert len(c.v.post_requests) == 2
        assert not c.e.attempts[-1].post_started
        assert c.v.rows[c.tp]["status"] == "LIVE"
        assert not any(c.tp in ids for ids in c.v.cancels)
        observe(c.v, c.e, c.tp, "tp-after-disable")
        result, _ = await asyncio.wait_for(c.task, 5)
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
