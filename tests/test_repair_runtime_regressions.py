"""Regressions from the 21:20–23:50 live review; exchange I/O is simulated."""

import asyncio
import time
from collections import deque
from dataclasses import replace
from decimal import Decimal as D
from threading import RLock
from types import SimpleNamespace

import pytest

from polymaker.config import RDPSMConfig
from polymaker.domain import Side, TradeState
from polymaker.rdp_sm_loop import RunResult, SMTradingLoop
from polymaker.rdp_sm_marketdata import RepairPricePoint
from polymaker.rdp_sm_path_repair import PathRepairController
from polymaker.rdp_sm_sl_ledger import fifo_residual
from polymaker.repair_policy import RepairPolicy
from polymaker.state.tracker import TradeEvent
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_obi_execution import cleanup, setup_obi
from test_rdp_sm_path_repair import env as env
from test_rdp_sm_path_repair import setup_policy
from test_rdp_sm_repair import open_yes, reflect_positions


def mature_sigma(feed):
    """Seed the documented 10-second/5-pair readiness window without sleeping."""
    now, wall = feed.monotonic(), feed.wall()
    with feed.lock:
        feed.probability_points.extendleft(reversed([
            (int(wall)-age, now-age, .51, feed.generation)
            for age in range(11, 0, -1)
        ]))


async def test_tp_minimum_notional_reverse_enters_hd_with_fifo_cost(env, monkeypatch):
    setup = await setup_obi(env, monkeypatch)
    v, e, feed, lifecycle, normal, _, _, pump, _ = setup
    mature_sigma(feed)
    official, anchor = e.official_time, e.time_anchor

    async def server_time():
        e.reads.network.observe("GET", 0.001, endpoint="/time")
        return official + time.monotonic() - anchor

    monkeypatch.setattr(e.reads, "server_time", server_time)
    pump.update(direction="NO", bid=".12", yes_ask=".13", no_bid=".87", no_ask=".88")
    await asyncio.sleep(0.025)

    def opening(venue):
        for oid, row in venue.rows.items():
            if row["asset_id"] == "2" and not row["associate_trades"]:
                venue.fill(oid, "original-no", 5)
        reflect_positions(venue)

    v.on_cancel = opening
    try:
        result, _ = await normal.enter(D(0), D(0), "opening-no")
        assert result.close_barrier == "SAFE_TO_CONTINUE"
        v.on_cancel = None
        e.cfg.rdp_sm.repair.path.tp_profit_rate = D(".001")
        controller = PathRepairController(e, lifecycle)
        monkeypatch.setattr(controller, "sigma", lambda: 0.01)
        lifecycle.account = (await e._snapshot(include_books=False))[3]

        def tp_fill(venue, oid):
            row = venue.rows[oid]
            if row["asset_id"] == "1" and not row["associate_trades"]:
                venue.fill(oid, "tp-nine-point-one", 9.1)
                reflect_positions(venue)

        v.on_order_read = tp_fill
        result, _ = await controller.repair(*e.economic_inventory(), "tp-flip")
        assert result.close_barrier == "SAFE_TO_CONTINUE"
        assert result.requested_quotes[0].price == 0.11
        assert result.requested_quotes[0].size == 9.1
        assert e.economic_inventory() == (D("9.1"), D(5))
        ep = e.repair_episode
        assert ep["side"] == "YES" and ep["direction_leg"] == 1
        assert D(ep["original_qty"]) == D("4.1")
        assert D(ep["original_cost"]) == D(".451")
        assert controller._policy().mode == "HD"
        assert (
            ep["first_fill_monotonic"]
            == e.fill_observed_at[
                (ep["source_lots"][0].trade_id, ep["source_lots"][0].maker_order_id)
            ]["fill_monotonic"]
        )
        assert e.committed() == D("5.351")
        assert len(v.post_requests) == 2
        # Repeated proofs/CONFIRMED observations must not start another leg.
        assert await e.verify_close(cancel=True)
        e.update_repair_episode()
        assert not controller.reconcile_episode() and e.repair_episode is ep
        assert (
            len([r for r in journal_records(e.journal.path) if r["kind"] == "sm_repair_reverse"])
            == 1
        )
    finally:
        await cleanup(setup)


def clock_controller():
    clock = SimpleNamespace(now=102.0)
    cfg = RDPSMConfig()
    cfg.repair.path.tp_profit_rate = None
    cfg.repair.path.shock_z = 100
    fill = SimpleNamespace(trade_id="t", maker_order_id="o", token_id="1", size=5, price=0.3)
    ep = dict(
        opening_window="open",
        windows=["open"],
        original_qty="5",
        side="YES",
        opening_monotonic=100.0,
        first_fill_elapsed=100.0,
        first_fill_monotonic=100.0,
    )
    e = SimpleNamespace(
        cfg=SimpleNamespace(rdp_sm=cfg),
        official_time=1000.0,
        time_anchor=0.0,
        monotonic=lambda: clock.now,
        slug="market-1000",
        repair_episode=ep,
        meta=SimpleNamespace(yes=SimpleNamespace(token_id="1"), no=SimpleNamespace(token_id="2")),
        attempts=[SimpleNamespace(order_id="o", quote=SimpleNamespace(window_id="open"))],
        economic_fills=lambda: [fill],
        record=lambda *_: None,
        fill_observed_at={
            ("t", "o"): dict(
                elapsed=100.0,
                fill_monotonic=100.0,
                economic_observed_monotonic=100.0,
                source="VENUE_TRADE_TIME",
            )
        },
        processor=SimpleNamespace(economic_fills_for_orders=lambda _: [fill]),
    )
    feed = SimpleNamespace(
        lock=RLock(),
        probability_points=[],
        repair_path_segment=1,
        repair_price_points=deque(),
        wall=lambda: 1000 + clock.now,
    )
    controller = PathRepairController(e, SimpleNamespace(feed=feed))
    controller.sigma = lambda: None

    def observe(at, p, official_offset=0.0, project=False):
        clock.now = at
        e.official_time = 1000 + official_offset
        seq = len(feed.repair_price_points) + 1
        feed.repair_price_points.append(
            RepairPricePoint(
                seq,
                at,
                1000 + at,
                1,
                p,
                str(p - 0.005),
                str(p + 0.005),
                str(1 - p - 0.005),
                str(1 - p + 0.005),
                ".01",
                ".01",
                1,
            )
        )
        yes = SimpleNamespace(
            best_bid=D(str(p)) - D(".005"), best_ask=D(str(p)) + D(".005"), tick=D(".01")
        )
        no = SimpleNamespace(
            best_bid=1 - D(str(p)) - D(".005"), best_ask=1 - D(str(p)) + D(".005"), tick=D(".01")
        )
        return controller.observe(
            SimpleNamespace(sequence=seq, generation=1, yes=yes, no=no), project=project
        )

    return controller, observe


def test_official_clock_jitter_preserves_monotonic_path_and_drawdown():
    controller, observe = clock_controller()
    observe(100, 0.5)
    observe(102, 0.55)
    decision, point = observe(102.1, 0.54, official_offset=-0.75)
    assert point.elapsed == pytest.approx(102.1)
    assert decision["observations"]["drawdown_per_share"] == pytest.approx(0.01)
    assert controller.policy.path_complete
    decision, _ = observe(102.2, 0.49, official_offset=0.4)
    assert decision["mode"] == "SL"
    assert decision["reason"] == "CUMULATIVE_DRAWDOWN"
    assert decision["observations"]["drawdown_per_share"] == pytest.approx(0.06)
    assert decision["hold_seconds"] == pytest.approx(2.2)
    before = controller.policy.payload()
    observe(102.3, 0.48, official_offset=-0.5, project=True)
    assert controller.policy.payload() == before


def test_stable_path_clock_does_not_force_sl_before_official_t180():
    controller, observe = clock_controller()
    observe(100, 0.5)
    decision, _ = observe(180.2, 0.5, official_offset=-0.5)
    assert decision["mode"] == "HD" and decision["session_elapsed"] == pytest.approx(179.7)
    decision, _ = observe(180.3, 0.5)
    assert decision["mode"] == "SL" and decision["reason"] == "TIME_FORCE_SL"


def test_fifo_does_not_reorder_fills_when_official_clock_moves_back():
    fills = [
        TradeEvent(token, Side.BUY, p, 5, t, TradeState.CONFIRMED, 0, t, "market")
        for t, token, p in (("a", "Y", 0.3), ("b", "N", 0.7), ("c", "N", 0.71))
    ]
    stamps = {
        (f.trade_id, f.maker_order_id): dict(elapsed=at, fill_monotonic=100 + i)
        for i, (f, at) in enumerate(zip(fills, (100, 101, 100.5), strict=True))
    }
    residual = fifo_residual(fills, stamps, "Y")
    assert len(residual) == 1 and residual[0].trade_id == "c" and residual[0].price == 0.71


async def test_future_venue_timestamp_uses_causal_local_anchor(env, monkeypatch):
    setup = await setup_obi(env, monkeypatch)
    e = setup[1]
    original = e.processor.on_trade

    def future_stamp(event, condition_id, **kwargs):
        now = e.official_time + e.monotonic() - e.time_anchor
        return original(replace(event, ts=now + 0.25), condition_id, **kwargs)

    monkeypatch.setattr(e.processor, "on_trade", future_stamp)
    try:
        await open_yes(setup)
        assert e.fill_observed_at
        for stamp in e.fill_observed_at.values():
            assert stamp["source"] == "LOCAL_FIRST_TRADE_OBSERVATION"
            assert stamp["fill_monotonic"] <= stamp["observed_monotonic"]
        controller = PathRepairController(e, setup[3])
        decision, point = controller.observe(setup[3].snapshot(controller, *e.economic_inventory()))
        assert decision["hold_seconds"] >= 0
        assert point.elapsed >= controller._first_fill_time()
    finally:
        await cleanup(setup)


async def test_infeasible_plan_ignores_depth_frames_but_wakes_for_viable_price(env, monkeypatch):
    setup, controller = await setup_policy(env, monkeypatch)
    v, e, feed, _, _, _, _, pump, _ = setup
    e.cfg.rdp_sm.repair.review_sec = 0.3
    pump.update(bid=".94", yes_ask=".96", no_bid=".04", no_ask=".06")
    await asyncio.sleep(0.025)
    calls = []
    native = controller._make_plan

    def count(*args):
        calls.append(time.monotonic())
        return native(*args)

    monkeypatch.setattr(controller, "_make_plan", count)
    try:
        began = time.monotonic()
        assert await controller.repair(*e.economic_inventory(), "blocked") is None
        assert time.monotonic() - began >= 0.25  # frame traffic cannot busy-loop
        assert len(calls) == 1 and len(v.post_requests) == 1

        async def improve():
            await asyncio.sleep(0.04)
            pump.update(bid=".50", yes_ask=".52", no_bid=".48", no_ask=".50")

        task = asyncio.create_task(improve())
        began = time.monotonic()
        assert await controller.repair(*e.economic_inventory(), "still-blocked") is None
        await task
        assert time.monotonic() - began < 0.25  # do not sit out a feasible update

        def fill(venue, oid):
            if venue.rows[oid]["asset_id"] == "2" and not venue.rows[oid]["associate_trades"]:
                venue.fill(oid, "viable-repair", 5)
                reflect_positions(venue)

        v.on_order_read = fill
        result, _ = await controller.repair(*e.economic_inventory(), "viable")
        assert result.close_barrier == "SAFE_TO_CONTINUE"
        assert e.economic_inventory() == (D(5), D(5))
    finally:
        await cleanup(setup)


def test_blocked_log_storm_keeps_counts_and_active_plan():
    records = []
    e = SimpleNamespace(
        cfg=SimpleNamespace(rdp_sm=RDPSMConfig()),
        monotonic=lambda: 100.0,
        record=lambda k, p: records.append((k, p)),
    )
    controller = PathRepairController(e, SimpleNamespace())
    controller.policy = SimpleNamespace(mode="SL")
    controller.episode_id = "open"
    controller.plan = {"mode": "TP"}
    for i in range(10000):
        controller._blocked(
            "MINIMUM_ORDER_EXCEEDS_MAXIMUM" if i % 2 else "REPAIR_PRICE_OUT_OF_RANGE",
            discard_plan=False,
        )
    assert controller.plan == {"mode": "TP"}
    assert len(records) == 2
    controller.flush_blocked("CUTOFF")
    summaries = [p for k, p in records if k == "sm_repair_block_summary"]
    assert sum(p["occurrences"] for p in summaries) == 10000
    assert len(summaries) == 2


@pytest.mark.parametrize("boundary", [180, 240])
async def test_loop_time_boundaries_run_while_snapshot_blocks_and_book_is_invalid(
    env, monkeypatch, boundary
):
    setup, _ = await setup_policy(env, monkeypatch)
    v, e, feed, _, _, _, _, pump, _ = setup
    e.cfg.rdp_sm.repair.policy = "hd_tp_sl_v1"
    e.repair_episode["policy_state"] = RepairPolicy(e.cfg.rdp_sm.repair.path, mode="TP").payload()
    start = int(e.slug.rsplit("-", 1)[1])
    e.official_time, e.time_anchor = start + boundary - 0.1, time.monotonic()
    pump["running"] = False
    feed.invalidate("WS_DISCONNECTED")
    entered = asyncio.Event()
    released = asyncio.Event()
    real_snapshot = e._snapshot

    async def blocked_snapshot(**kwargs):
        entered.set()
        await released.wait()
        return await real_snapshot(**kwargs)

    monkeypatch.setattr(e, "_snapshot", blocked_snapshot)
    loop = SMTradingLoop(e.cfg, e.slug, e.session_id)
    loop.executor = e
    loop.production = True
    loop.market_deadline = time.monotonic() + 300 - (boundary - 0.1)
    task = asyncio.create_task(loop._run_obi_session(RunResult(e.meta.condition_id, e.session_id)))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.sleep(0.25)
        await e.journal.flush()
        records = journal_records(e.journal.path)
        events = [r["data"]["payload"] for r in records if r["kind"] == "sm_repair_time_boundary"]
        assert any(p["boundary"] == boundary for p in events)
        assert loop.rebalance_controller.policy.mode == "SL"
        assert len(v.post_requests) == 1  # no blind order while admission is invalid
    finally:
        task.cancel()
        released.set()
        monkeypatch.setattr(e, "_snapshot", real_snapshot)
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not [
            t
            for t in asyncio.all_tasks()
            if t.get_name() == "rdp-repair-time-boundaries" and not t.done()
        ]
        await cleanup(setup)


async def test_time_force_does_not_override_confirmed_small_exposure_rule(env, monkeypatch):
    setup = await setup_obi(env, monkeypatch)
    try:
        await open_yes(setup, size=2.5)
        e = setup[1]
        controller = PathRepairController(e, setup[3])
        assert controller._policy().mode == "HD"
        assert controller.boundary_close_reason(None, 180) is None
        assert controller.policy.mode == "HD" and len(setup[0].post_requests) == 1
    finally:
        await cleanup(setup)


async def test_failed_boundary_task_propagates_after_network_task_cleanup(env, monkeypatch):
    setup, _ = await setup_policy(env, monkeypatch)
    e = setup[1]
    e.cfg.rdp_sm.repair.policy = "hd_tp_sl_v1"
    loop = SMTradingLoop(e.cfg, e.slug, e.session_id)
    loop.executor = e
    loop.production = True
    loop.market_deadline = time.monotonic() + 300

    async def failed_boundary(_):
        raise RuntimeError("BOUNDARY_TASK_FAILED")

    monkeypatch.setattr(PathRepairController, "watch_time_boundaries", failed_boundary)
    try:
        with pytest.raises(RuntimeError, match="BOUNDARY_TASK_FAILED"):
            async with asyncio.timeout(3):
                await loop._run_obi_session(RunResult(e.meta.condition_id, e.session_id))
        assert not [
            task for task in asyncio.all_tasks()
            if task.get_name() in {"rdp-repair-time-boundaries", "rdp-network-warmup"}
            and not task.done()
        ]
    finally:
        await cleanup(setup)
