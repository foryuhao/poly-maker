"""New REPAIR through real signing, gateway, fills, cancellation and journal."""
import asyncio
import time
from dataclasses import replace
from decimal import Decimal

import pytest

from polymaker.domain import TradeState
from polymaker.rdp_sm_execution import InventoryMismatch
from polymaker.rdp_sm_repair import RepairController
from polymaker.rdp_sm_sizing import summarize_sizing
from test_rdp_sm_cached_reads import attach, finish
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_obi_execution import cleanup, setup_obi
from test_rdp_sm_obi_execution import env as obi_env


@pytest.fixture
def env(monkeypatch, tmp_path):
    yield from obi_env.__wrapped__(monkeypatch, tmp_path)


def reflect_positions(venue, *, include_pending=True):
    totals, cost = {}, Decimal(0)
    for trade in venue.trades:
        if not include_pending and trade["status"] != "CONFIRMED":
            continue
        for leg in trade["maker_orders"]:
            q, p = Decimal(leg["matched_amount"]), Decimal(leg["price"])
            totals[leg["asset_id"]] = totals.get(leg["asset_id"], Decimal(0))+q
            cost += q*p
    venue.positions = [{"token_id": t, "current_size": str(q), "avg_price": ".5"}
                       for t, q in totals.items()]
    venue.capital["balance"] = str(int((Decimal(100)-cost)*1000000))


async def open_yes(setup, size=5, status="CONFIRMED", actual=True, window="opening"):
    v, e, feed, _, normal, _, _, pump, _ = setup
    pump.update(bid=".30", yes_ask=".32", no_bid=".68", no_ask=".70")
    await asyncio.sleep(.02)
    now, wall = feed.monotonic(), feed.wall()
    with feed.lock:
        feed.probability_points.extendleft(reversed([
            (int(wall)-age, now-age, .51, feed.generation)
            for age in range(11, 0, -1)
        ]))

    def filled(venue):
        for oid, row in venue.rows.items():
            if row["asset_id"] == "1" and not row["associate_trades"]:
                venue.fill(oid, window, size, status)
        reflect_positions(venue, include_pending=actual)

    v.on_cancel = filled
    result = await normal.enter(*e.economic_inventory(), window)
    v.on_cancel = None
    return result


async def test_actual_matched_position_unlocks_and_late_confirmed_counts_once(env, monkeypatch):
    setup = await setup_obi(env, monkeypatch)
    v, e, _, _, _, *_ = setup
    try:
        result, _ = await open_yes(setup, status="MATCHED")
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert not e.confirmed() and len(e.economic_fills()) == 1
        assert e.economic_inventory() == (Decimal(5), Decimal(0))
        assert e.committed() == Decimal("1.5")
        assert e.repair_episode["original_qty"] == "5.0"
        assert any(p["outcome"] == "POSITION_RECONCILED" for p in e.proofs.values())
        v.trades[0]["status"] = "CONFIRMED"
        assert await e.verify_close(cancel=True), e.reason
        assert len(e.confirmed()) == len(e.economic_fills()) == 1
        assert e.committed() == Decimal("1.5")
        assert e.economic_inventory() == (Decimal(5), Decimal(0))
        assert any(r["kind"] == "sm_position_reconciled" for r in journal_records(e.journal.path))
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("status,actual,closed,qty", [("MATCHED", False, False, 0),
    ("FAILED", False, True, 0), ("FAILED", True, True, 5)])
async def test_pending_and_failed_use_actual_economics_without_timeout_promotion(
        env, monkeypatch, status, actual, closed, qty):
    setup = await setup_obi(env, monkeypatch)
    _, e = setup[:2]

    async def no_timeout_promotion():
        return None

    monkeypatch.setattr(e, "_wait_for_confirmation", no_timeout_promotion)
    try:
        result, _ = await open_yes(setup, status=status, actual=actual)
        assert (result.close_barrier == "SAFE_TO_CONTINUE") is closed, result.block_reason
        assert e.economic_inventory() == (Decimal(qty), Decimal(0))
        assert not e.confirmed()
        assert e.committed() == (Decimal(0) if status == "FAILED" and not actual else Decimal("1.5"))
        assert (e.repair_episode is None) is (closed and not qty)
    finally:
        await cleanup(setup)


async def test_cached_position_proof_retires_pending_details_and_invalidates_on_change(env, monkeypatch, tmp_path):
    setup = await setup_obi(env, monkeypatch)
    v, e = setup[:2]
    companion, _ = await attach(setup, tmp_path)
    try:
        result, _ = await open_yes(setup, status="MATCHED")
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        oid = next(iter(e.known))
        await asyncio.sleep(.12)
        assert oid not in companion.service.latest().pending_ids
        assert e.shared_reads.service._position_proofs
        # A new FAILED fact never inherits the old MATCHED proof's solved label.
        v.trades[0]["status"] = "FAILED"
        await asyncio.sleep(.12)
        assert oid in companion.service.latest().pending_ids
        assert await e.verify_close(cancel=True), e.reason
        assert len(e.economic_fills()) == 1 and not e.confirmed()
    finally:
        await finish(setup, companion)


async def test_partial_repair_keeps_only_remainder_and_preserves_absolute_deadline(env, monkeypatch):
    setup = await setup_obi(env, monkeypatch)
    v, e, _, lifecycle, _, _, _, _, _ = setup
    try:
        first, _ = await open_yes(setup)
        assert first.close_barrier == "SAFE_TO_CONTINUE"
        controller = RepairController(e, lifecycle)
        controller.cfg.review_sec = .04
        monkeypatch.setattr(controller, "sigma", lambda: .02)
        orders = []
        planned = []

        async def solve(s, order=None):
            if order:
                orders.append(order)
                return {"action": "KEEP", "cancel_at": planned[0]}
            planned.append(s.elapsed+.35)
            return {"action": "BUY", "side": "NO", "price": .68, "quantity": 5.,
                    "cancel_at": planned[0], "candidates": []}

        monkeypatch.setattr(controller, "solve", solve)

        def partial(venue, oid):
            row = venue.rows[oid]
            if row["asset_id"] == "2" and not row["associate_trades"]:
                venue.fill(oid, "repair-partial", 1)
                reflect_positions(venue)

        v.on_order_read = partial
        lifecycle.account = (await e._snapshot(include_books=False))[3]
        result, sizing = await controller.repair(Decimal(5), Decimal(0), "repair-1")
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert len(v.post_requests) == 2  # opening plus one repair; never another 5-share order
        posted = v.post_requests[-1][0]
        assert posted["postOnly"] is True and posted["orderType"] == "GTC"
        assert posted["order"]["side"] == "BUY" and posted["order"]["tokenId"] == "2"
        assert orders and all(o.remaining == 4 for o in orders)
        assert all(o.cancel_at == pytest.approx(planned[0]) for o in orders)
        assert all(o.age >= 0 for o in orders)
        assert e.economic_inventory() == (Decimal(5), Decimal(1))
        assert e.committed() == Decimal("2.18")
        report = summarize_sizing(e, [{"state": "REPAIR", "sizing": sizing,
            "result": result.to_dict()}], Decimal(4), result.close_barrier)
        assert report["maker_filled_shares"] == 6
    finally:
        await cleanup(setup)


async def test_t240_cancellation_is_independent_of_busy_model(env, monkeypatch):
    setup = await setup_obi(env, monkeypatch)
    v, e, _, lifecycle, _, _, _, _, _ = setup
    try:
        await open_yes(setup)
        controller = RepairController(e, lifecycle)
        monkeypatch.setattr(controller, "sigma", lambda: .02)
        reviews = []

        def slow_planner(s, order=None):
            if order:
                reviews.append(time.monotonic())
                time.sleep(.5)  # Runs through the real off-thread solver owner.
            return {"action": "KEEP" if order else "BUY", "side": "NO", "price": .68,
                    "quantity": 5., "cancel_at": 240., "candidates": [],
                    "mode": "REPAIR", "expected_pnl": .05, "advantage": .01,
                    "parameter_status": "UNCALIBRATED"}

        monkeypatch.setattr(controller.planner, "decide", slow_planner)
        lifecycle.account = (await e._snapshot(include_books=False))[3]
        start = int(e.slug.rsplit("-", 1)[1])
        # Shift only the authoritative session clock, keeping transport/SDK mocked.
        e.official_time, e.time_anchor = start+239.75, e.monotonic()
        result, _ = await controller.repair(Decimal(5), Decimal(0), "cutoff-repair")
        assert result.close_barrier == "SAFE_TO_CONTINUE"
        assert reviews
        records = journal_records(e.journal.path)
        closed = [r["data"]["payload"] for r in records if r["kind"] == "sm_obi_close_requested"
                  and r["data"]["payload"]["window_id"] == "cutoff-repair"]
        assert closed[0]["reason"] in {"ORDER_DEADLINE", "MARKET_SUBMISSION_CUTOFF"}
        assert closed[0]["requested_monotonic"]-closed[0]["deadline"] < .06
        assert len(v.post_requests) == 2
        assert await controller.repair(Decimal(5), Decimal(0), "after-cutoff") is None
        assert len(v.post_requests) == 2
    finally:
        await cleanup(setup)


async def test_session_delta_counts_dust_and_does_not_reset_per_episode(env, monkeypatch):
    setup = await setup_obi(env, monkeypatch)
    _, e, _, _, normal, *_ = setup
    try:
        assert not e.delta_candidate_allowed(Decimal(".02"), "YES", Decimal(5))
        assert e.delta_candidate_allowed(Decimal(".02"), "NO", Decimal(5))
        assert e.delta_candidate_allowed(Decimal("5.02"), "NO", Decimal(5))
        assert not e.delta_candidate_allowed(Decimal("5.02"), "YES", Decimal(5))
        assert not e.delta_candidate_allowed(Decimal("5.02"), "NO", Decimal("10.03"))
        await open_yes(setup, size=2)
        assert e.repair_episode is None and e.trading_state == "NORMAL"
        # Same-side entry still cannot bypass the existing session Delta cap.
        assert await normal.enter(Decimal(2), Decimal(0), "duplicate") is None
        assert e.committed() == Decimal(".6")
    finally:
        await cleanup(setup)


async def test_unexplained_sub_dust_position_cannot_create_a_hidden_risk_allowance(env, monkeypatch):
    setup = await setup_obi(env, monkeypatch)
    v, e, _, _, normal, *_ = setup
    try:
        v.positions = [{"token_id": "1", "current_size": ".02", "avg_price": ".3"}]
        with pytest.raises(InventoryMismatch):
            await e._snapshot(include_books=False)
        assert e.inventory_mismatch["tolerance_shares"] == .01
        assert await normal.enter(Decimal(0), Decimal(0), "hidden-dust") is None
        assert not v.post_requests
    finally:
        await cleanup(setup)


async def test_three_completed_episodes_share_one_15_usdc_budget(env, monkeypatch):
    setup = await setup_obi(env, monkeypatch)
    v, e, _, lifecycle, normal, _, _, pump, _ = setup
    try:
        controller = RepairController(e, lifecycle)
        monkeypatch.setattr(controller, "sigma", lambda: .02)

        async def solve(s, order=None):
            return {"action": "BUY", "side": "NO", "price": .68, "quantity": 5.,
                    "cancel_at": s.elapsed+.2, "candidates": []}

        monkeypatch.setattr(controller, "solve", solve)
        for episode in range(3):
            pump["direction"] = "NONE"
            await asyncio.sleep(.02)
            pump["direction"] = "YES"
            await asyncio.sleep(.02)
            lifecycle.account = (await e._snapshot(include_books=False))[3]
            result, _ = await open_yes(setup, window=f"opening-{episode}")
            assert result.close_barrier == "SAFE_TO_CONTINUE"

            def fill_no(venue, oid, episode=episode):
                row = venue.rows[oid]
                if row["asset_id"] == "2" and not row["associate_trades"]:
                    venue.fill(oid, f"pair-{episode}", 5)
                    reflect_positions(venue)

            v.on_order_read = fill_no
            lifecycle.account = (await e._snapshot(include_books=False))[3]
            result, _ = await controller.repair(*e.economic_inventory(), f"repair-{episode}")
            assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
            v.on_order_read = None
            assert e.repair_episode is None
            assert e.committed() == Decimal("4.9")*(episode+1)
        pump["direction"] = "NONE"
        await asyncio.sleep(.02)
        pump["direction"] = "YES"
        await asyncio.sleep(.02)
        lifecycle.account = (await e._snapshot(include_books=False))[3]
        assert await normal.enter(*e.economic_inventory(), "budget-exhausted") is None
        assert len(v.post_requests) == 6
        assert e.committed() == Decimal("14.7")
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("late", [False, True])
async def test_changed_opening_economics_cancel_oversized_repair_before_next_model_review(env, monkeypatch, late):
    setup = await setup_obi(env, monkeypatch)
    v, e, _, lifecycle, _, _, _, pump, _ = setup
    change = None
    try:
        await open_yes(setup)
        opening = e.economic_fills()[0]
        pump.update(bid=".84", yes_ask=".86", no_bid=".14", no_ask=".16")
        await asyncio.sleep(.02)
        controller = RepairController(e, lifecycle)
        monkeypatch.setattr(controller, "sigma", lambda: .02)

        async def solve(s, order=None):
            return {"action": "BUY", "side": "NO", "price": .14, "quantity": 7.15,
                    "cancel_at": s.elapsed+5, "candidates": []}

        monkeypatch.setattr(controller, "solve", solve)
        lifecycle.account = (await e._snapshot(include_books=False))[3]
        second_post = asyncio.Event()
        loop = asyncio.get_running_loop()
        setup[5].delay = lambda: loop.call_soon_threadsafe(second_post.set)
        if late:
            def late_fill(venue):
                for oid, row in venue.rows.items():
                    if row["asset_id"] == "2" and not row["associate_trades"]:
                        venue.fill(oid, "late-repair", 7.15)
                reflect_positions(venue, include_pending=False)

            v.on_cancel = late_fill

        async def fail_opening():
            await second_post.wait()
            await asyncio.sleep(.04)
            v.trades[0]["status"] = "FAILED"
            reflect_positions(v, include_pending=False)
            e.processor.on_trade(replace(opening, status=TradeState.FAILED), e.meta.condition_id)

        change = asyncio.create_task(fail_opening())
        result, _ = await controller.repair(*e.economic_inventory(), "oversized")
        await change
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        events = [r["data"]["payload"] for r in journal_records(e.journal.path)
                  if r["kind"] == "sm_obi_close_requested"
                  and r["data"]["payload"]["window_id"] == "oversized"]
        assert events[0]["reason"] == "SESSION_ECONOMICS_CHANGED"
        assert result.close_monotonic < result.fill_deadline_monotonic-3
        assert e.economic_inventory() == (Decimal(0), Decimal("7.15") if late else Decimal(0))
        assert e.committed() == (Decimal("1.001") if late else Decimal(0))
        if late:
            controller.cfg.liquidity_rate = .5
            s = controller.state(lifecycle.snapshot(controller, *e.economic_inventory()))
            plan = controller.planner.decide(s)
            assert plan["mode"] == "LIMIT_RECOVERY" and plan["action"] == "BUY"
            assert plan["side"] == "YES" and plan["actual_limit_excess"] > 0
            assert plan["planned_limit_excess_if_fully_filled"] == 0
            assert plan["incremental_ev"] < 0
    finally:
        if change and not change.done():
            change.cancel()
            await asyncio.gather(change, return_exceptions=True)
        await cleanup(setup)
