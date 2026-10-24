"""Confirmed small residuals complete a round without erasing exposure or cost."""
import asyncio
import time
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from polymaker.rdp_sm import repair_required
from polymaker.rdp_sm_logging import HumanRunLog
from polymaker.rdp_sm_loop import RunResult, SMTradingLoop
from polymaker.rdp_sm_normal import NormalController
from polymaker.rdp_sm_path_repair import PathRepairController
from polymaker.rdp_sm_repair import RepairController
from polymaker.rdp_sm_sl_ledger import exposure
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_obi_execution import cleanup, setup_obi
from test_rdp_sm_repair import env as env
from test_rdp_sm_repair import reflect_positions


async def book(setup, side, price):
    pump = setup[7]
    own, other = D(str(price)), 1-D(str(price))-D(".02")
    yes, no = (own, other) if side == "YES" else (other, own)
    pump.update(direction=side, bid=str(yes), yes_ask=str(yes+D(".02")),
                no_bid=str(no), no_ask=str(no+D(".02")))
    await asyncio.sleep(.025)


def mature_sigma(feed):
    now, wall = feed.monotonic(), feed.wall()
    with feed.lock:
        feed.probability_points.extendleft(reversed([
            (int(wall)-age, now-age, .51, feed.generation) for age in range(11, 0, -1)
        ]))


async def partial_round(env, monkeypatch, *, side="NO", residual="1.28",
                        status="CONFIRMED", actual=True, max_sec=20, block_close=False, path=False):
    setup = await setup_obi(env, monkeypatch)
    v, e, feed, lifecycle, normal = setup[:5]
    official, anchor = e.official_time, e.time_anchor

    async def server_time():
        e.reads.network.observe("GET", .001, endpoint="/time")
        return official+time.monotonic()-anchor

    monkeypatch.setattr(e.reads, "server_time", server_time)
    e.cfg.rdp_sm.cooldown.max_sec = max_sec
    try:
        await book(setup, side, .44)
        mature_sigma(feed)

        def opening(venue):
            for a in e.attempts:
                oid = a.order_id or a.expected_order_id
                if (a.quote.window_id == "opening" and oid in venue.rows
                        and not venue.rows[oid]["associate_trades"]):
                    venue.fill(oid, "opening-fill", 5)
            reflect_positions(venue)

        v.on_cancel = opening
        result, _ = await normal.enter(*e.economic_inventory(), "opening")
        v.on_cancel = None
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert e.trading_state == "REPAIR"
        opposite = "YES" if side == "NO" else "NO"
        await book(setup, opposite, .64)
        repair = (PathRepairController if path else RepairController)(e, lifecycle)
        repair.cfg.review_sec = .03
        monkeypatch.setattr(repair, "sigma", lambda: .02)
        if path:
            token = e.meta.yes.token_id if opposite == "YES" else e.meta.no.token_id
            tick = D(str(v.ticks[token]))
            setup[7]["yes_ask" if opposite == "YES" else "no_ask"] = str(D(".64")+tick)
            await asyncio.sleep(.025)
            repair._policy().force_sl(repair.elapsed(), "TEST_PARTIAL_SL")
            repair._persist()
        cancel_at = (e.official_time+e.monotonic()-e.time_anchor-int(e.slug.rsplit("-", 1)[1])+.35)

        async def solve(_state, order=None):
            return {"action": "KEEP" if order else "BUY", "side": opposite, "price": .64,
                    "quantity": 5., "cancel_at": cancel_at, "candidates": []}

        monkeypatch.setattr(repair, "solve", solve)

        def partial(venue, oid):
            owned = next((a for a in e.attempts if (a.order_id or a.expected_order_id) == oid), None)
            if (owned is not None and owned.quote.window_id.startswith("repair")
                    and not venue.rows[oid]["associate_trades"]):
                venue.fill(oid, "partial-fill", float(5-D(residual)), status)
                reflect_positions(venue, include_pending=actual)

        v.on_order_read = partial
        native_verify = e._verify_close
        if block_close:
            async def unresolved_close(**kwargs):
                safe = await native_verify(**kwargs)
                if safe:
                    e.invalidate("TEST_CLOSE_PENDING")
                return False

            monkeypatch.setattr(e, "_verify_close", unresolved_close)
        lifecycle.account = (await e._snapshot(include_books=False))[3]
        result, _ = await repair.repair(*e.economic_inventory(), "repair")
        v.on_order_read = None
        return setup, result, native_verify
    except BaseException:
        await cleanup(setup)
        raise


@pytest.mark.parametrize("side", ["YES", "NO"])
@pytest.mark.parametrize("residual", ["1.28", "2.5", "2.500001"])
async def test_exact_threshold_both_sides_and_residual_costs(env, monkeypatch, side, residual):
    setup, result, _ = await partial_round(env, monkeypatch, side=side, residual=residual)
    v, e = setup[:2]
    try:
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        y, n = e.economic_inventory()
        expected = D(residual)*(1 if side == "YES" else -1)
        assert y-n == expected
        assert e.committed() == D(5)*D(".44")+(5-D(residual))*D(".64")
        ledger = exposure(e)
        assert ledger.actual == ledger.execution == expected
        assert ledger.spent == e.committed() and ledger.reserved == 0
        assert not e.delta_candidate_allowed(expected, side, D(5))
        if D(residual) <= D("2.5"):
            assert e.repair_episode is None and e.trading_state == "COOLDOWN"
            paired = 5-D(residual)
            assert D(e.cooldown.last_result["paired_shares"]) == paired
            assert D(e.cooldown.last_result["pair_pnl"]) == -paired*D(".08")
            assert e.cooldown.last_result["cooldown_sec"] == 8
            deadline = e.cooldown.deadline
            for _ in range(3):
                assert await e.verify_close(cancel=True)
                e.update_repair_episode()
            assert e.cooldown.deadline == deadline and len(v.post_requests) == 2
        else:
            assert repair_required(y, n) and e.repair_episode is not None
            assert e.trading_state == "REPAIR" and e.cooldown.last_result is None
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("obstacle", ["unconfirmed", "order_proof", "inventory"])
async def test_small_residual_requires_complete_fill_and_order_evidence(env, monkeypatch, obstacle):
    if obstacle == "unconfirmed":
        from polymaker.rdp_sm_execution import SMWindowExecutor

        async def no_promotion(_self):
            return None

        monkeypatch.setattr(SMWindowExecutor, "_wait_for_confirmation", no_promotion)
    setup, result, native_verify = await partial_round(env, monkeypatch,
        status="MATCHED" if obstacle == "unconfirmed" else "CONFIRMED",
        actual=obstacle != "unconfirmed", block_close=obstacle != "unconfirmed")
    v, e = setup[:2]
    try:
        assert result.close_barrier == "BLOCKED"
        if obstacle == "inventory":
            monkeypatch.setattr(e, "_verify_close", native_verify)
            assert await e.verify_close(cancel=True)
            e.inventory_mismatch = {"reason": "TEST_INVENTORY_NOT_EXPLAINED"}
        e.update_repair_episode()
        assert e.repair_episode is not None and e.cooldown.last_result is None
        assert e.trading_state == "REPAIR"
        for trade in v.trades:
            trade["status"] = "CONFIRMED"
        reflect_positions(v)
        monkeypatch.setattr(e, "_verify_close", native_verify)
        assert await e.verify_close(cancel=True)
        await e._snapshot(include_books=False)  # refresh the separate inventory admission evidence
        e.update_repair_episode()
        assert e.trading_state == "COOLDOWN" and e.cooldown.last_result["cooldown_sec"] == 8
        assert e.economic_inventory() == (D("3.72"), D(5))
    finally:
        await cleanup(setup)


async def test_matched_actual_position_proof_and_late_confirmation_complete_only_once(env, monkeypatch):
    setup, result, _ = await partial_round(env, monkeypatch, status="MATCHED")
    v, e = setup[:2]
    try:
        assert result.close_barrier == "SAFE_TO_CONTINUE"
        assert e.repair_episode is None and e.cooldown.last_result["cooldown_sec"] == 8
        deadline = e.cooldown.deadline
        v.trades[-1]["status"] = "CONFIRMED"
        assert await e.verify_close(cancel=True)
        e.update_repair_episode()
        assert e.cooldown.deadline == deadline and len(e.economic_fills()) == 2
    finally:
        await cleanup(setup)


async def test_next_normal_retains_residual_and_fifo_counts_only_new_pairs(env, monkeypatch):
    setup, _, _ = await partial_round(env, monkeypatch, max_sec=0)
    v, e, feed, _, normal = setup[:5]
    try:
        assert e.trading_state == "NORMAL" and e.economic_inventory() == (D("3.72"), D(5))
        assert D(e.cooldown.last_result["pair_pnl"]) == D("-.2976")
        await book(setup, "YES", .30)
        mature_sigma(feed)

        def next_fill(venue):
            for a in e.attempts:
                oid = a.order_id or a.expected_order_id
                if (a.quote.window_id == "next" and oid in venue.rows
                        and not venue.rows[oid]["associate_trades"]):
                    venue.fill(oid, "next-fill", 2)
            reflect_positions(venue)

        v.on_cancel = next_fill
        result, _ = await normal.enter(*e.economic_inventory(), "next")
        v.on_cancel = None
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert result.requested_quotes[0].size == 6.28  # existing NORMAL skew still sees -1.28
        assert e.economic_inventory() == (D("5.72"), D(5))
        assert e.committed() == D("5.1808")
        assert D(e.cooldown.last_result["paired_shares"]) == D("1.28")
        assert D(e.cooldown.last_result["paired_yes_cost"]) == D(".384")
        assert D(e.cooldown.last_result["paired_no_cost"]) == D(".5632")
        assert D(e.cooldown.last_result["pair_pnl"]) == D(".3328")
        assert e.trading_state == "NORMAL" and e.cooldown.last_result["cooldown_sec"] == 0
    finally:
        await cleanup(setup)


async def test_coordinator_resumes_normal_with_residual_after_cooldown(env, monkeypatch):
    setup, _, _ = await partial_round(env, monkeypatch, max_sec=.15)
    v, e = setup[:2]
    loop = SMTradingLoop(e.cfg, e.slug, e.session_id)
    loop.executor, loop.production = e, True
    result = RunResult(e.meta.condition_id, e.session_id)
    deadline, before = e.cooldown.deadline, len(v.post_requests)
    native_enter = NormalController.enter
    entries = []

    async def observed_enter(controller, *args):
        entries.append(e.monotonic())
        assert e.monotonic() >= deadline and e.trading_state == "NORMAL"
        assert e.economic_inventory() == (D("3.72"), D(5))
        outcome = await native_enter(controller, *args)
        if outcome is not None:
            loop.market_deadline = e.monotonic()-1
        return outcome

    monkeypatch.setattr(NormalController, "enter", observed_enter)
    monkeypatch.setattr(loop, "_merge", lambda *_: asyncio.sleep(0, result=False))
    try:
        await asyncio.wait_for(loop._run_obi_session(result), 4)
        assert entries and len(v.post_requests) == before+1
        assert result.windows[0]["state"] == "NORMAL"
        assert result.windows[0]["result"]["requested_quotes"][0]["size"] == 6.28
        assert e.economic_inventory() == (D("3.72"), D(5)) and e.committed() == D("4.5808")
        assert result.final_close_barrier == "SAFE_TO_CONTINUE"
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("side", ["YES", "NO"])
async def test_real_sl_actor_finishes_partial_repair_with_residual(env, monkeypatch, side):
    setup, result, _ = await partial_round(env, monkeypatch, side=side, path=True)
    v, e = setup[:2]
    try:
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert abs(e.economic_inventory()[0]-e.economic_inventory()[1]) == D("1.28")
        assert e.repair_episode is None and e.trading_state == "COOLDOWN"
        assert D(e.cooldown.last_result["paired_shares"]) == D("3.72")
        assert D(e.cooldown.last_result["pair_pnl"]) == D("-.2976")
        assert e.cooldown.last_result["cooldown_sec"] == 8
        before = len(v.post_requests)
        assert await setup[4].enter(*e.economic_inventory(), "blocked") is None
        assert len(v.post_requests) == before
    finally:
        await cleanup(setup)


async def test_small_residual_wait_is_time_bounded_even_on_hot_feed(env, monkeypatch):
    setup, _, _ = await partial_round(env, monkeypatch, block_close=True)
    v, e, feed, lifecycle = setup[:4]
    controller = PathRepairController(e, lifecycle)
    pauses = []

    async def tick_independent_sleep(seconds):
        pauses.append(seconds)
        await asyncio.sleep(.001)

    async def no_socket_wait(*_args):
        raise AssertionError("small residual closure must not poll on every price event")

    monkeypatch.setattr(e, "sleep", tick_independent_sleep)
    monkeypatch.setattr(feed, "wait_after", no_socket_wait)
    try:
        before = len(v.post_requests)
        for i in range(5):
            assert await controller.repair(*e.economic_inventory(), f"wait-{i}") is None
        assert pauses == [.1]*5 and len(v.post_requests) == before
        assert e.repair_episode is not None and e.cooldown.last_result is None
        records = journal_records(e.journal.path)
        waits = [r["data"]["payload"] for r in records if r["kind"] == "sm_repair_small_exposure"]
        assert len(waits) == 1 and waits[0]["action"] == "WAIT_CLOSE_BARRIER"
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("max_sec", [0, 20])
async def test_residual_after_normal_cutoff_uses_one_second_account_cadence(env, monkeypatch, max_sec):
    setup, _, _ = await partial_round(env, monkeypatch, max_sec=max_sec)
    v, e, feed = setup[:3]
    clock = SimpleNamespace(now=e.monotonic()+200)
    origin = clock.now
    start = int(e.slug.rsplit("-", 1)[1])
    official = start+296.25
    sleeps, reads = [], []
    before = len(v.post_requests)
    native_snapshot = e._snapshot

    async def server_time():
        e.reads.network.observe("GET", .001, endpoint="/time")
        return official+clock.now-origin

    async def advancing_sleep(seconds):
        sleeps.append(seconds)
        clock.now += seconds
        await asyncio.sleep(0)

    async def observed_snapshot(**kwargs):
        reads.append(clock.now)
        return await native_snapshot(**kwargs)

    async def no_book_driven_wait(*_args):
        raise AssertionError("the small residual must not keep the coordinator in REPAIR")

    monkeypatch.setattr(e, "monotonic", lambda: clock.now)
    monkeypatch.setattr(e, "_snapshot", observed_snapshot)
    monkeypatch.setattr(e.reads, "server_time", server_time)
    monkeypatch.setattr(feed, "wait_after", no_book_driven_wait)
    e.official_time, e.time_anchor = official, clock.now
    loop = SMTradingLoop(e.cfg, e.slug, e.session_id,
                         sleep=advancing_sleep, monotonic=lambda: clock.now)
    loop.executor, loop.production = e, True
    loop.market_deadline = clock.now+3.75
    monkeypatch.setattr(loop, "_merge", lambda *_: asyncio.sleep(0, result=False))
    result = RunResult(e.meta.condition_id, e.session_id)
    try:
        await asyncio.wait_for(loop._run_obi_session(result), 3)
        assert sleeps == [1, 1, 1, .75] and len(reads) == 4
        assert len(v.post_requests) == before and result.total_windows == 0
        assert result.stop_reason == "MARKET_CUTOFF" and result.final_close_barrier == "SAFE_TO_CONTINUE"
        assert e.economic_inventory() == (D("3.72"), D(5)) and e.committed() == D("4.5808")
        if max_sec:
            assert e.cooldown.resume_block_reason == "NORMAL_SUBMISSION_CUTOFF"
        else:
            assert e.trading_state == "NORMAL" and e.repair_episode is None
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("delta,state", [(2.5, "NORMAL"), (-2.5, "NORMAL"), (2.500001, "REPAIR")])
def test_human_log_matches_residual_state_without_flattening_inventory(delta, state):
    log = HumanRunLog(repair_mode=True)
    try:
        lines = log.render("events", [("sm_window_result", {"close_barrier": "SAFE_TO_CONTINUE",
            "yes_inventory": max(delta, 0), "no_inventory": max(-delta, 0), "delta": delta})])
        assert any(f"Next Trading State: {state}" in line for line in lines)
        assert any(f"Updated Delta={delta:g}" in line for line in lines)
    finally:
        log.close()
