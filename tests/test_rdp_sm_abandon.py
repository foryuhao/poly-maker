"""High entry abandonment through actual Maker gateway and close evidence."""
import asyncio
from decimal import Decimal as D
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from polymaker.config import RDPRepairAbandonConfig
from polymaker.rdp_sm_abandon import REASON, eligible
from polymaker.rdp_sm_loop import RunResult, SMTradingLoop
from polymaker.rdp_sm_path_repair import PathRepairController
from polymaker.rdp_sm_repair import RepairController
from polymaker.rdp_sm_sl import SLExecution
from polymaker.rdp_sm_tp import TPExecution
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_obi_execution import cleanup, setup_obi
from test_rdp_sm_repair import env as env
from test_rdp_sm_repair import reflect_positions
from test_rdp_sm_residual_exit import book, mature_sigma


@pytest.mark.parametrize("price,elapsed,expected", [
    (".80", 120, True), (".799999", 120, False), (".80", 119.999999, False),
    (".800001", 120.000001, True), (None, 120, False)])
def test_inclusive_decimal_and_time_boundaries(price, elapsed, expected):
    assert eligible(RDPRepairAbandonConfig(), elapsed, D(price) if price else None) is expected


@pytest.mark.parametrize("kwargs", [{"min_elapsed_sec": -1}, {"min_elapsed_sec": float("nan")},
    {"entry_price_threshold": "NaN"}, {"entry_price_threshold": "1.01"}, {"enabled": 1}])
def test_invalid_parameters(kwargs):
    with pytest.raises(ValidationError):
        RDPRepairAbandonConfig(**kwargs)


def at(e, elapsed):
    e.official_time = int(e.slug.rsplit("-", 1)[1])+elapsed
    e.time_anchor = e.monotonic()


async def opening(setup, monkeypatch, *, side="YES", prices=((5, ".80"),), status="CONFIRMED", actual=True):
    v, e, feed, _, normal = setup[:5]
    await book(setup, side, .81)
    mature_sigma(feed)

    async def server_time():
        e.reads.network.observe("GET", .001, endpoint="/time")
        return e.official_time+e.monotonic()-e.time_anchor

    monkeypatch.setattr(e.reads, "server_time", server_time)

    def fill(venue):
        for a in e.attempts:
            oid = a.order_id or a.expected_order_id
            if a.quote.window_id == "opening" and oid in venue.rows and not venue.rows[oid]["associate_trades"]:
                for i, (qty, price) in enumerate(prices):
                    trade = venue.fill(oid, "opening-"+str(i), qty, status)
                    trade["maker_orders"][0]["price"] = price
                reflect_positions(venue, include_pending=actual)

    v.on_cancel = fill
    result, _ = await normal.enter(*e.economic_inventory(), "opening")
    v.on_cancel = None
    return result


@pytest.mark.parametrize("side", ["YES", "NO"])
@pytest.mark.parametrize("prices,expected", [(((5, ".80"),), True),
    (((2, ".785"), (3, ".81")), True), (((5, ".799999"),), False)])
async def test_actual_normal_vwap_both_sides_not_limit_or_mid(env, monkeypatch, side, prices, expected):
    setup = await setup_obi(env, monkeypatch)
    v, e, feed, lifecycle, normal = setup[:5]
    try:
        result = await opening(setup, monkeypatch, side=side, prices=prices)
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert not e.check_repair_abandonment()
        at(e, 120)
        before = len(v.post_requests)
        assert e.check_repair_abandonment() is expected
        if not expected:
            return
        assert D(e.repair_abandonment["entry_vwap"]) == D(".8")
        assert e.trading_state == "HOLD_TO_SETTLEMENT"
        e.update_repair_episode()
        e.finalize_repair_abandonment()
        assert e.repair_abandonment["event"] == "RECONCILED"
        assert e.repair_abandonment["round_paired_shares"] == "0"
        assert D(e.repair_abandonment["round_pair_pnl"]) == 0
        assert e.repair_abandonment["residual_settlement_pnl"] == "PENDING_SETTLEMENT"
        controller = PathRepairController(e, lifecycle)
        for _ in range(2):
            assert await normal.enter(*e.economic_inventory(), "blocked") is None
            assert await controller.repair(*e.economic_inventory(), "blocked-repair") is None
            assert await controller.execute_sl("blocked-sl") is None
            assert await controller.execute_tp("blocked-tp") is None
            assert await e.verify_close(cancel=True)
        actor = SLExecution(controller, "old")
        goal = ("NO", D(".2"), D(5), feed.snapshot())
        assert actor.admission_reason(goal) == REASON
        assert TPExecution.admission_reason(actor, goal) == REASON
        assert not lifecycle.guard(normal, SimpleNamespace(), e.revision, [None])
        assert lifecycle.guard_reason == REASON
        assert not e.resume_cooldown(feed.snapshot(), e.network_health())
        assert len(v.post_requests) == before
        await e.journal.flush()
        events = [r["data"]["payload"]["event"] for r in journal_records(e.journal.path)
                  if r["kind"] == "sm_repair_abandoned"]
        assert events == ["TRIGGERED", "RECONCILED"]
    finally:
        await cleanup(setup)


async def test_disabled_and_preboundary_complete_do_not_latch(env, monkeypatch):
    setup = await setup_obi(env, monkeypatch)
    e = setup[1]
    try:
        await opening(setup, monkeypatch)
        at(e, 120)
        e.cfg.rdp_sm.repair.abandon_high_entry.enabled = False
        assert not e.check_repair_abandonment()
        e.cfg.rdp_sm.repair.abandon_high_entry.enabled = True
        e.repair_episode = None  # An already completed round is no longer REPAIR.
        assert not e.check_repair_abandonment()
    finally:
        await cleanup(setup)


async def test_partial_repair_retained_fifo_cost_and_market_never_resumes(env, monkeypatch):
    setup = await setup_obi(env, monkeypatch)
    v, e, feed, lifecycle = setup[:4]
    try:
        await opening(setup, monkeypatch)
        await book(setup, "NO", .24)
        controller = RepairController(e, lifecycle)
        monkeypatch.setattr(controller, "sigma", lambda: .02)
        cancel_at = controller.state(feed.snapshot()).elapsed+.3

        async def solve(_state, order=None):
            return {"action": "KEEP" if order else "BUY", "side": "NO", "price": .24,
                    "quantity": 5., "cancel_at": cancel_at, "candidates": []}

        monkeypatch.setattr(controller, "solve", solve)

        def partial(venue, oid):
            if venue.rows[oid]["asset_id"] == "2" and not venue.rows[oid]["associate_trades"]:
                venue.fill(oid, "partial-repair", 1)
                reflect_positions(venue)

        v.on_order_read = partial
        lifecycle.account = (await e._snapshot(include_books=False))[3]
        result, _ = await controller.repair(*e.economic_inventory(), "partial")
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert e.economic_inventory() == (D(5), D(1)) and e.trading_state == "REPAIR"
        at(e, 120)
        assert e.check_repair_abandonment()
        e.finalize_repair_abandonment()
        attribution = e.repair_abandonment
        assert D(attribution["round_paired_shares"]) == 1
        assert D(attribution["round_pair_pnl"]) == D("-.04")
        assert D(attribution["session_residual_yes_cost"]) == D("3.20")
        assert e.committed() == D("4.24") and e.cooldown.last_result is None
        posts, ticks = len(v.post_requests), setup[7]["count"]
        loop = SMTradingLoop(e.cfg, e.slug, e.session_id)
        loop.executor, loop.production = e, True
        result = RunResult(e.meta.condition_id, e.session_id)
        native_snapshot = e._snapshot
        reads = []

        async def snapshot(**kwargs):
            reads.append(e.trading_state)
            value = await native_snapshot(**kwargs)
            if len(reads) == 3:
                at(e, 300)
            return value

        async def sleep(_seconds):
            await asyncio.sleep(.02)

        monkeypatch.setattr(e, "_snapshot", snapshot)
        loop.sleep = sleep
        await loop._run_obi_session(result)
        assert result.repair_abandonment == attribution and result.execution_status == "COMPLETED"
        assert reads == ["HOLD_TO_SETTLEMENT"]*3
        assert len(v.post_requests) == posts and setup[7]["count"] > ticks
        assert e.merged_pairs == 0 and e.economic_inventory() == (D(5), D(1))
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("mode", ["TP", "SL"])
@pytest.mark.parametrize("boundary", ["before_post", "on_post_start", "working_order"])
async def test_prepared_and_working_orders_stop_at_boundary(env, monkeypatch, mode, boundary):
    setup = await setup_obi(env, monkeypatch)
    v, e, feed, lifecycle = setup[:4]
    try:
        await opening(setup, monkeypatch)
        await book(setup, "NO", .24)
        controller = PathRepairController(e, lifecycle)
        price = D(".24") if mode == "TP" else feed.snapshot().no.best_ask-feed.snapshot().no.tick
        if mode == "TP":
            controller._policy().mode = "TP"
            controller.policy.tp_price = price
            controller.plan = {"side": "NO", "price": float(price), "quantity": 5.,
                               "tick": str(feed.snapshot().no.tick)}
            monkeypatch.setattr(controller, "observe", lambda *_args, **_kwargs: ({"mode": "TP"}, None))
        actor = (TPExecution if mode == "TP" else SLExecution)(controller, "boundary")
        goal = ("NO", price, D(5), feed.snapshot())
        # Isolate time/cancel behavior from TP/SL price detection, leaving their
        # final SDK guards, signed payload and real mocked HTTP path intact.
        monkeypatch.setattr(actor, "goal", lambda: (goal, None))
        native_send = e.gateway.send_prepared

        async def send(*args, **kwargs):
            if boundary == "before_post":
                at(e, 120)
            elif boundary == "on_post_start":
                context = kwargs["operation_context"]
                native_started = context["on_post_start"]

                def started():
                    at(e, 120)
                    native_started()

                context["on_post_start"] = started
            return await native_send(*args, **kwargs)

        monkeypatch.setattr(e.gateway, "send_prepared", send)
        if boundary == "working_order":
            def working(venue, oid):
                if venue.rows[oid]["asset_id"] == "2":
                    # Partial fill racing cancellation must be retained too.
                    if not venue.rows[oid]["associate_trades"]:
                        venue.fill(oid, "race", 1)
                        reflect_positions(venue)
                    at(e, 120)

            v.on_order_read = working
        # TP owns a fixed target only after its policy has identified TP.
        if mode == "TP":
            monkeypatch.setattr(controller, "boundary_close_reason", lambda *_: None)
        before = len(v.post_requests)
        created = await asyncio.wait_for(actor.run(), 5)
        assert created and e.repair_abandonment is not None
        assert len(v.post_requests) == before+(boundary == "working_order")
        v.on_order_read = None
        assert await e.verify_close(cancel=True), e.reason
        e.finalize_repair_abandonment()
        assert e.trading_state == "HOLD_TO_SETTLEMENT"
        assert all(row["status"] in {"CANCELED", "MATCHED"} for row in v.rows.values())
        assert e.economic_inventory() == (D(5), D(1) if boundary == "working_order" else D(0))
        if boundary != "working_order":
            assert created[0].outcome == "ABORTED" and not created[0].post_started
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("status,actual", [("MATCHED", False), ("MINED", True)])
async def test_opening_requires_actual_confirmation_or_existing_position_proof(env, monkeypatch, status, actual):
    setup = await setup_obi(env, monkeypatch)
    v, e = setup[:2]
    try:
        async def no_promotion():
            return None

        monkeypatch.setattr(e, "_wait_for_confirmation", no_promotion)
        # Existing position-proof policy requires the fill to match this
        # order's submitted price; confirmed fills separately test improvement.
        await opening(setup, monkeypatch, prices=((5, ".81"),), status=status, actual=actual)
        at(e, 120)
        assert e.check_repair_abandonment() is actual, e.reason
        assert (e.trading_state == "HOLD_TO_SETTLEMENT") is actual
        if not actual:
            assert e.repair_abandonment is None and not e.safe_to_continue
            for trade in v.trades:
                trade["status"] = "CONFIRMED"
            reflect_positions(v)
            assert await e.verify_close(cancel=True)
            assert e.check_repair_abandonment()
    finally:
        await cleanup(setup)


async def test_reverse_leg_still_uses_original_normal_entry(env, monkeypatch):
    setup = await setup_obi(env, monkeypatch)
    e = setup[1]
    try:
        await opening(setup, monkeypatch)
        # Reverse legs have independent acquisition costs and window IDs. Their
        # prices must not replace the original NORMAL first-leg VWAP.
        e.repair_episode.update(opening_window="opening:direction:1", chain_id="opening",
                                side="NO", original_cost=".65", original_qty="5")
        at(e, 120)
        assert e.check_repair_abandonment()
        assert e.repair_abandonment["entry_side"] == "YES"
        assert D(e.repair_abandonment["entry_vwap"]) == D(".80")
        e.cfg.rdp_sm.repair.abandon_high_entry.enabled = False
        e.repair_episode = None
        at(e, 30)
        assert e.check_repair_abandonment() and e.trading_state == "HOLD_TO_SETTLEMENT"
    finally:
        await cleanup(setup)
