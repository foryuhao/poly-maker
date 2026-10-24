"""Actual BA entry -> exit -> M3 inventory projection with mocked venue I/O."""
import asyncio
import time
from types import SimpleNamespace

import pytest

from polymaker.config import RDPBAConfig
from polymaker.rdp_sm_ba import BidAbsorption
from polymaker.rdp_sm_logging import HumanRunLog
from polymaker.rdp_sm_loop import RunResult, SMTradingLoop
from test_rdp_sm_cached_reads import attach, finish
from test_rdp_sm_obi_execution import cleanup, setup_obi
from test_rdp_sm_opposite_ba import absorption
from test_rdp_sm_repair import env as env
from test_rdp_sm_repair import reflect_positions


async def until(predicate):
    async with asyncio.timeout(5):
        while not predicate():  # noqa: ASYNC110 - observe state produced by the real M3 coordinator
            await asyncio.sleep(.005)


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("exit_result", ["rejected_retry", "partial", "full"])
async def test_main_loop_projects_ba_exit_and_continues(env, monkeypatch, tmp_path, cached, exit_result):
    setup = await setup_obi(env, monkeypatch)
    v, e, feed, _, _, wire = setup[:6]
    cfg = e.cfg.rdp_sm
    cfg.normal_entry_mode = "ba_v1"
    cfg.repair.policy = "opposite_ba_v1"
    cfg.ba = RDPBAConfig(observation_sec=.1, signal_lifetime_sec=.3,
        max_sell_age_ms=200, max_entry_age_ms=250, max_book_age_ms=200,
        network={"post_budget_floor_ms": 10, "min_live_budget_ms": 20})
    cfg.repair.opposite_ba = cfg.ba.model_copy(deep=True)
    feed.ba = BidAbsorption(feed, cfg.ba)
    feed.opposite_ba = BidAbsorption(feed, cfg.repair.opposite_ba)
    companion = None
    display = HumanRunLog(repair_mode=True)
    rendered = []

    def emit(kind, payload):
        rendered.extend(display.render(kind, payload))

    e.display_events = []
    loop = SMTradingLoop(e.cfg, e.slug, e.session_id, monotonic=e.monotonic,
                         observer=SimpleNamespace(emit=emit))
    loop.executor = e
    result = RunResult(e.meta.condition_id, e.session_id)
    session_task = driver = None

    def filled(venue):
        for oid, row in venue.rows.items():
            if row["status"] != "CANCELED" or row["associate_trades"]:
                continue
            if row["asset_id"] == "2":
                venue.fill(oid, "opening-no", 5)
            else:
                venue.fill(oid, "exit-yes", 3 if exit_result == "partial" else 5)
        reflect_positions(venue)

    async def market_events():
        # Entry and exit both use the real BA collector and native SDK POST.
        absorption(feed, "2")
        await until(lambda: result.normal_windows == 1)
        if exit_result == "rejected_retry":
            wire.reject_index = 0
        absorption(feed, "1")
        await until(lambda: result.repair_windows == 1)
        first = result.windows[-1]
        assert first["sizing"]["signed_target_delta"] == "0"
        assert first["sizing"]["target_sizing_applied"] is False
        assert first["sizing"]["favorite_data_validity"] == "NOT_USED"
        assert result.sizing_report["final_target_delta"] == 0
        if exit_result == "rejected_retry":
            assert first["result"]["placement_results"][0]["outcome"] == "REJECTED"
            assert e.repair_episode is not None
            assert result.sizing_report["final_actual_delta"] == -5
            wire.reject_index = None
            # A rejected episode is consumed. Only a new BA may submit again.
            await asyncio.sleep(max(0, feed.opposite_ba.states["1"].order_deadline-time.monotonic())+.01)
            absorption(feed, "1")
            await until(lambda: result.repair_windows == 2)
        assert e.repair_episode is None
        # End the fake socket before advancing the venue clock to market end;
        # an artificial 240s jump must not be delivered as a real WS frame.
        setup[7]["running"] = False
        await setup[-1]
        v.time = int(e.slug.rsplit("-", 1)[1])+300

    try:
        if cached:
            companion, _ = await attach(setup, tmp_path)
        v.on_cancel = filled
        session_task = asyncio.create_task(loop._run_obi_session(result))
        driver = asyncio.create_task(market_events())
        await asyncio.wait_for(asyncio.gather(session_task, driver), 8)
        assert result.execution_status == "COMPLETED", result.stop_reason
        assert result.stop_reason == "MARKET_CUTOFF"
        assert result.final_close_barrier == "SAFE_TO_CONTINUE"
        assert result.normal_windows == 1
        assert result.repair_windows == (2 if exit_result == "rejected_retry" else 1)
        assert result.confirmed_no == 5
        assert result.confirmed_yes == (3 if exit_result == "partial" else 5)
        assert result.sizing_report["final_target_delta"] == 0
        assert result.sizing_report["intentional_residual_shares"] == 0
        assert result.sizing_report["unintentional_residual_shares"] == (2 if exit_result == "partial" else 0)
        assert len(v.post_requests) == result.total_windows
        assert any("Trading State: REPAIR" in line for line in rendered)
    finally:
        for task in (driver, session_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*(task for task in (driver, session_task) if task is not None),
                             return_exceptions=True)
        display.close()
        if companion:
            await finish(setup, companion)
        else:
            await cleanup(setup)
