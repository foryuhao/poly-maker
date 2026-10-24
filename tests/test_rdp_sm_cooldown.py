"""FIFO round attribution and actual Maker gateway cooldown admission."""
import asyncio
import time
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from polymaker.config import RDPCooldownConfig
from polymaker.domain import Side, TradeState
from polymaker.rdp_sm_cooldown import Cooldown, pair_snapshot
from polymaker.rdp_sm_logging import HumanRunLog
from polymaker.rdp_sm_loop import RunResult, SMTradingLoop
from polymaker.rdp_sm_normal import NormalController
from polymaker.rdp_sm_path_repair import PathRepairController
from polymaker.rdp_sm_repair import RepairController
from polymaker.rdp_sm_sl import SLExecution
from polymaker.rdp_sm_tp import TPExecution
from polymaker.state.tracker import TradeEvent
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_obi_execution import cleanup, setup_obi
from test_rdp_sm_path_repair import setup_policy
from test_rdp_sm_repair import env as env
from test_rdp_sm_repair import open_yes, reflect_positions

D = Decimal


def fill(tid, token, size, price):
    return TradeEvent(token, Side.BUY, price, size, tid, TradeState.CONFIRMED,
                      0, "order-"+tid, "market")


def snapshot(fills):
    stamps = {(f.trade_id, f.maker_order_id): {"fill_monotonic": i}
              for i, f in enumerate(fills)}
    return pair_snapshot(fills, stamps, "Y", "N")


@pytest.mark.parametrize("price,seconds", [(.49, 0), (.50, 0), (.501, .1),
                                            (.515, 1.5), (.70, 20), (.90, 20)])
def test_formula_uses_per_share_loss_and_fractional_seconds(price, seconds):
    cd = Cooldown(RDPCooldownConfig())
    cd.begin("round", {})
    result = cd.complete(snapshot([fill("y", "Y", 7, .5), fill("n", "N", 7, price)]), "Y", 100)
    assert result["cooldown_sec"] == pytest.approx(seconds)
    assert result["state"] == ("COOLDOWN" if seconds else "NORMAL")
    assert cd.deadline == (100+seconds if seconds else None)
    with pytest.raises(ValueError, match="ROUND_NOT_ESTABLISHED"):
        cd.complete({}, "Y", 101)


def test_configurable_multiplier_and_cap():
    cd = Cooldown(RDPCooldownConfig(max_sec=3.25, loss_sec_per_dollar=50))
    cd.begin("r", {})
    assert cd.complete(snapshot([fill("y", "Y", 1, .6), fill("n", "N", 1, .5)]),
                       "Y", 0)["cooldown_sec"] == 3.25


@pytest.mark.parametrize("field", ["max_sec", "loss_sec_per_dollar"])
@pytest.mark.parametrize("value", [-1, float("nan"), float("inf")])
def test_config_rejects_unusable_parameters(field, value):
    with pytest.raises(ValidationError):
        RDPCooldownConfig(**{field: value})


@pytest.mark.parametrize("fills", [[], [fill("y", "Y", 5, .9)], [fill("n", "N", 5, .9)]])
def test_zero_pairs_never_triggers(fills):
    cd = Cooldown(RDPCooldownConfig())
    cd.begin("r", {})
    result = cd.complete(snapshot(fills), "Y", 100)
    assert D(result["paired_shares"]) == D(result["pair_pnl"]) == 0
    assert result["cooldown_sec"] == 0 and not cd.active


def test_fifo_excludes_previously_consumed_pairs_and_carries_actual_residual_cost():
    cd = Cooldown(RDPCooldownConfig(max_sec=0))
    fills = [fill("a", "Y", 10, .4), fill("b", "N", 5, .7)]
    cd.begin("first", {})
    first = cd.complete(snapshot(fills), "Y", 0)
    assert D(first["paired_shares"]) == 5
    assert D(first["pair_pnl"]) == D("-.5")
    cd.begin("second", snapshot(fills))
    fills += [fill("c", "N", 2, .8), fill("d", "N", 3, .9), fill("e", "N", 100, .99)]
    second = cd.complete(snapshot(fills), "Y", 5)
    assert D(second["paired_shares"]) == 5
    assert D(second["paired_yes_cost"]) == 2  # FIFO's old .4 residual, not a new quote
    assert D(second["paired_no_cost"]) == D("4.3")
    assert D(second["pair_pnl"]) == D("-1.3")
    assert D(second["pair_pnl_per_share"]) == D("-.26")


def test_all_partial_tp_sl_and_reverse_legs_stay_in_one_round():
    cd = Cooldown(RDPCooldownConfig())
    cd.begin("opening", {})
    fills = [fill("opening", "Y", 5, .6), fill("partial-tp", "N", 2, .3),
             fill("sl", "N", 6, .7), fill("reverse", "Y", 3, .5)]
    result = cd.complete(snapshot(fills), "Y", 10)
    assert D(result["paired_shares"]) == 8
    assert D(result["paired_yes_cost"]) == D("4.5")
    assert D(result["paired_no_cost"]) == D("4.8")
    assert D(result["pair_pnl"]) == D("-1.3")
    assert result["cooldown_sec"] == 16.25


@pytest.mark.parametrize("change", ["missing", "price", "quantity", "fifo"])
def test_changed_prior_pair_evidence_fails_closed_without_consumption(change):
    fills = [fill("a", "Y", 5, .6), fill("b", "N", 5, .5)]
    cd = Cooldown(RDPCooldownConfig())
    cd.begin("new-round", snapshot(fills))
    if change == "missing":
        fills.pop()
    elif change == "price":
        fills[0] = replace(fills[0], price=.61)
    elif change == "quantity":
        fills[0] = replace(fills[0], size=4)
    else:
        fills.insert(0, fill("earlier", "Y", 5, .2))
    with pytest.raises(ValueError, match="FIFO_EVIDENCE_CHANGED"):
        cd.complete(snapshot(fills), "Y", 1)
    assert cd.round_id == "new-round" and cd.deadline is None


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 0, -1])
def test_unverified_actual_prices_never_become_zero_cooldown(bad):
    with pytest.raises(ValueError, match="FILL_NOT_VERIFIED"):
        snapshot([fill("y", "Y", 5, bad), fill("n", "N", 5, .5)])


def test_human_log_reports_cooldown_instead_of_inventory_implied_normal():
    log = HumanRunLog(repair_mode=True)
    try:
        lines = log.render("events", [("sm_cooldown", {"state": "COOLDOWN", "pair_pnl": "-.1",
            "paired_shares": "5", "cooldown_sec": 2}),
            ("sm_window_result", {"close_barrier": "SAFE_TO_CONTINUE"})])
        assert any("Next Trading State: COOLDOWN" in line for line in lines)
        lines = log.render("events", [("sm_cooldown", {"state": "COOLDOWN",
            "event": "EXPIRED_WAITING_FOR_ADMISSION", "reason": "NORMAL_SUBMISSION_CUTOFF"}),
            ("sm_window_result", {"close_barrier": "SAFE_TO_CONTINUE"})])
        assert any("timer expired" in line and "NORMAL_SUBMISSION_CUTOFF" in line for line in lines)
        assert any("Next Trading State: COOLDOWN" in line for line in lines)
        lines = log.render("events", [("sm_cooldown", {"state": "NORMAL", "event": "EXPIRED"}),
            ("sm_window_result", {"close_barrier": "SAFE_TO_CONTINUE"})])
        assert any("Next Trading State: NORMAL" in line for line in lines)
    finally:
        log.close()


async def repaired_round(env, monkeypatch, *, actual_price=.715, status="CONFIRMED", actual=True):
    setup = await setup_obi(env, monkeypatch)
    v, e, feed, lifecycle, _, _, _, pump, _ = setup
    # Preserve a continuously advancing authoritative clock across account reads.
    official, anchor = e.official_time, e.time_anchor

    async def server_time():
        e.reads.network.observe("GET", .001, endpoint="/time")
        return official+time.monotonic()-anchor

    monkeypatch.setattr(e.reads, "server_time", server_time)
    await open_yes(setup)
    assert e.trading_state == "REPAIR"
    pump.update(bid=".26", yes_ask=".28", no_bid=".72", no_ask=".74")
    await asyncio.sleep(.025)
    controller = RepairController(e, lifecycle)
    controller.cfg.review_sec = .03
    monkeypatch.setattr(controller, "sigma", lambda: .02)
    cancel_at = controller.state(feed.snapshot()).elapsed+.5

    async def solve(_state, order=None):
        return {"action": "KEEP" if order else "BUY", "side": "NO", "price": .72,
                "quantity": 5., "cancel_at": cancel_at, "candidates": []}

    monkeypatch.setattr(controller, "solve", solve)

    def complete(venue, oid):
        row = venue.rows[oid]
        if row["asset_id"] == "2" and not row["associate_trades"]:
            trade = venue.fill(oid, "repair-actual", 5, status)
            trade["maker_orders"][0]["price"] = str(actual_price)
            reflect_positions(venue, include_pending=actual)

    v.on_order_read = complete
    lifecycle.account = (await e._snapshot(include_books=False))[3]
    result, _ = await controller.repair(*e.economic_inventory(), "repair")
    assert (result.close_barrier == "SAFE_TO_CONTINUE") is actual, result.block_reason
    assert (e.repair_episode is None) is actual
    return setup


@pytest.mark.parametrize("status,price,seconds", [("CONFIRMED", .68, 0),
    ("CONFIRMED", .7, 0), ("CONFIRMED", .715, 1.5), ("MATCHED", .72, 2)])
async def test_only_real_fill_prices_and_position_proof_drive_cooldown(env, monkeypatch, price, seconds, status):
    setup = await repaired_round(env, monkeypatch, actual_price=price, status=status)
    v, e = setup[:2]
    try:
        assert e.cooldown.last_result["cooldown_sec"] == pytest.approx(seconds)
        assert e.trading_state == ("COOLDOWN" if seconds else "NORMAL")
        assert e.attempts[-1].quote.price == .72  # actual .715 means 1.5s, not limit's 2s
        assert D(e.cooldown.last_result["paired_no_cost"]) == D(str(price))*5
        deadline = e.cooldown.deadline
        for _ in range(3):
            assert await e.verify_close(cancel=True)
            e.update_repair_episode()
        if status == "MATCHED":
            for trade in v.trades:
                trade["status"] = "CONFIRMED"
            assert await e.verify_close(cancel=True)
            e.update_repair_episode()
        assert e.cooldown.deadline == deadline
        records = journal_records(e.journal.path)
        assert len([r for r in records if r["kind"] == "sm_cooldown"]) == 1
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("mode", ["TP", "SL"])
async def test_path_actors_share_economic_eligibility(env, monkeypatch, mode):
    setup, controller = await setup_policy(env, monkeypatch)
    v, e, _, lifecycle, _, _, _, pump, _ = setup
    if mode == "SL":
        controller._policy().force_sl(controller.elapsed(), "TEST_SL")
        controller._persist()
        pump.update(bid=".28", yes_ask=".30", no_bid=".70", no_ask=".72")
        await asyncio.sleep(.025)

    def complete(venue, oid):
        row = venue.rows[oid]
        if row["asset_id"] == "2" and not row["associate_trades"]:
            venue.fill(oid, "path-"+mode, 5)
            reflect_positions(venue)

    v.on_order_read = complete
    try:
        lifecycle.account = (await e._snapshot(include_books=False))[3]
        result, _ = await controller.repair(*e.economic_inventory(), "path-actor")
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert e.repair_episode is None
        assert e.cooldown.last_result["cooldown_sec"] == pytest.approx(1.9 if mode == "SL" else 0)
        assert D(e.cooldown.last_result["paired_shares"]) == 5
    finally:
        await cleanup(setup)


async def test_real_tp_to_sl_includes_partial_tp_and_excludes_final_dust(env, monkeypatch):
    setup, controller = await setup_policy(env, monkeypatch)
    v, e, _, _, _, _, _, pump, _ = setup
    first_no = []

    def partial_then_sl(venue, oid):
        row = venue.rows[oid]
        if row["asset_id"] != "2" or row["associate_trades"]:
            return
        if not first_no:
            first_no.append(oid)
            venue.fill(oid, "tiny-partial-tp", .04)
            reflect_positions(venue)
            pump.update(bid=".10", yes_ask=".12", no_bid=".88", no_ask=".90")
        else:
            venue.fill(oid, "sl-completion", 5)
            reflect_positions(venue)

    v.on_order_read = partial_then_sl
    try:
        result, _ = await controller.repair(*e.economic_inventory(), "tp-to-sl")
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert controller.policy.mode == "SL"
        assert e.repair_episode is None and e.trading_state == "COOLDOWN"
        assert e.economic_inventory() == (D(5), D("5.04"))
        paired_no_cost = D(".04")*D(".46")+D("4.96")*D(".899")
        assert D(e.cooldown.last_result["paired_shares"]) == 5
        assert D(e.cooldown.last_result["paired_no_cost"]) == paired_no_cost
        assert D(e.cooldown.last_result["pair_pnl"]) == 5-D("1.5")-paired_no_cost
        assert e.cooldown.last_result["cooldown_sec"] == pytest.approx(19.5488)
    finally:
        await cleanup(setup)


async def test_cooldown_has_zero_maker_posts_while_feed_and_reconciliation_continue(env, monkeypatch):
    setup = await repaired_round(env, monkeypatch)
    v, e, feed, lifecycle, normal, _, _, pump, _ = setup
    controller = PathRepairController(e, lifecycle)
    try:
        deadline = e.cooldown.deadline
        before_posts, before_pump = len(v.post_requests), pump["count"]
        for i in range(3):
            assert await normal.enter(*e.economic_inventory(), f"blocked-{i}") is None
            assert await controller.repair(*e.economic_inventory(), f"blocked-repair-{i}") is None
            assert await controller.execute_sl(f"blocked-sl-{i}") is None
            assert await controller.execute_tp(f"blocked-tp-{i}") is None
            assert await e.verify_close(cancel=True)
        assert len(v.post_requests) == before_posts
        assert pump["count"] > before_pump and feed.snapshot().valid
        assert e.cooldown.deadline == deadline and e.trading_state == "COOLDOWN"
        # Final submit checks also reject an intent/actor created before entry.
        actor = SLExecution(controller, "old-actor")
        goal = ("NO", D(".72"), D(5), feed.snapshot())
        assert actor.admission_reason(goal) == "COOLDOWN_ACTIVE"
        assert TPExecution.admission_reason(actor, goal) == "COOLDOWN_ACTIVE"
        slot = SimpleNamespace(yes=D(5), no=D(5), snapshot=feed.snapshot(), phase="NORMAL")
        assert not lifecycle.guard(normal, slot, e.revision, [None])
        assert lifecycle.guard_reason == "COOLDOWN_ACTIVE"
    finally:
        await cleanup(setup)


async def test_unconfirmed_unreconciled_pair_waits_for_the_close_barrier(env, monkeypatch):
    async def no_timeout_promotion(_self):
        return None

    from polymaker.rdp_sm_execution import SMWindowExecutor

    monkeypatch.setattr(SMWindowExecutor, "_wait_for_confirmation", no_timeout_promotion)
    setup = await repaired_round(env, monkeypatch, actual_price=.72, status="MATCHED", actual=False)
    v, e = setup[:2]
    try:
        e.update_repair_episode()
        assert not e.safe_to_continue and e.trading_state == "REPAIR"
        assert e.cooldown.last_result is None and e.cooldown.deadline is None
        for trade in v.trades:
            trade["status"] = "CONFIRMED"
        reflect_positions(v)
        assert await e.verify_close(cancel=True)
        e.update_repair_episode()
        assert e.trading_state == "COOLDOWN"
        assert e.cooldown.last_result["cooldown_sec"] == 2
    finally:
        await cleanup(setup)


async def test_coordinator_keeps_reading_during_cooldown_and_then_resumes_real_normal(env, monkeypatch):
    setup = await repaired_round(env, monkeypatch)
    v, e, feed, _, _, _, _, pump, _ = setup
    loop = SMTradingLoop(e.cfg, e.slug, e.session_id)
    loop.executor, loop.production = e, True
    result = RunResult(e.meta.condition_id, e.session_id)
    deadline, before = e.cooldown.deadline, len(v.post_requests)
    reads, normal_entries = [], []
    native_snapshot, native_enter = e._snapshot, NormalController.enter

    async def observed_snapshot(**kwargs):
        reads.append((e.monotonic(), e.trading_state, len(v.post_requests)))
        return await native_snapshot(**kwargs)

    async def observed_normal(controller, *args):
        normal_entries.append(e.monotonic())
        assert e.trading_state == "NORMAL" and e.monotonic() >= deadline
        outcome = await native_enter(controller, *args)
        if outcome is not None:
            loop.market_deadline = e.monotonic()-1
        return outcome

    monkeypatch.setattr(e, "_snapshot", observed_snapshot)
    monkeypatch.setattr(NormalController, "enter", observed_normal)
    monkeypatch.setattr(loop, "_merge", lambda *_: asyncio.sleep(0, result=False))
    pump["direction"] = "NO"  # a fresh signal episode remains eligible after expiry
    try:
        await asyncio.wait_for(loop._run_obi_session(result), 5)
        during = [r for r in reads if r[1] == "COOLDOWN"]
        assert len(during) > 1 and all(r[2] == before for r in during)
        assert len(during) <= 25  # 1.5s cooldown; the 100Hz book must not set the read rate
        assert normal_entries and min(normal_entries) >= deadline
        assert len(v.post_requests) == before+1
        assert result.stop_reason == "MARKET_CUTOFF"
        assert result.windows[0]["state"] == "NORMAL"
        assert result.final_close_barrier == "SAFE_TO_CONTINUE"
        records = journal_records(e.journal.path)
        assert len([r for r in records if r["kind"] == "sm_cooldown"
                    and r["data"]["payload"].get("event") == "EXPIRED"]) == 1
        assert feed.snapshot().valid
    finally:
        await cleanup(setup)


async def test_expired_cooldown_after_normal_cutoff_waits_at_original_cadence(env, monkeypatch):
    setup = await repaired_round(env, monkeypatch)
    v, e, feed = setup[:3]
    clock = SimpleNamespace(now=e.cooldown.deadline+200)
    origin = clock.now
    start = int(e.slug.rsplit("-", 1)[1])
    official = start+296.25
    reads, sleeps = [], []
    before_posts, deadline = len(v.post_requests), e.cooldown.deadline
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
        raise AssertionError("COOLDOWN must not recheck the account on each book event")

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
        assert sleeps == [1, 1, 1, .75]
        assert len(reads) == 4 and len(v.post_requests) == before_posts
        assert result.stop_reason == "MARKET_CUTOFF"
        assert e.cooldown.deadline == deadline and e.trading_state == "COOLDOWN"
        records = journal_records(e.journal.path)
        blocked = [r["data"]["payload"] for r in records if r["kind"] == "sm_cooldown"
                   and r["data"]["payload"].get("event") == "EXPIRED_WAITING_FOR_ADMISSION"]
        assert len(blocked) == 1 and blocked[0]["reason"] == "NORMAL_SUBMISSION_CUTOFF"
        assert not any(r["kind"] == "sm_cooldown" and r["data"]["payload"].get("event") == "EXPIRED"
                       for r in records)
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("unsafe", ["timer", "proof", "inventory", "book", "network", "cutoff", "clock", "stale"])
async def test_expiry_requires_original_safety_conditions(env, monkeypatch, unsafe):
    setup = await repaired_round(env, monkeypatch)
    _, e, feed = setup[:3]
    try:
        healthy, valid = SimpleNamespace(allowed=True), SimpleNamespace(valid=True)
        assert not e.resume_cooldown(valid, healthy)
        assert e.cooldown.active
        now = e.cooldown.deadline+.001
        monkeypatch.setattr(e, "monotonic", lambda: now)
        if unsafe == "timer":
            now = e.cooldown.deadline-.001
        elif unsafe == "proof":
            e.reason = "PREVIOUS_WINDOW_UNRESOLVED"
        elif unsafe == "inventory":
            e.inventory_mismatch = {"reason": "unexplained"}
        elif unsafe == "book":
            valid.valid = False
        elif unsafe == "network":
            healthy.allowed = False
        elif unsafe == "cutoff":
            e.official_time = int(e.slug.rsplit("-", 1)[1])+120
            e.time_anchor = now
        elif unsafe == "clock":
            e.official_time = int(e.slug.rsplit("-", 1)[1])+10
            e.time_anchor = now
        elif unsafe == "stale":
            e.time_anchor = now-e.cfg.risk.ws_stale_halt_s-1
        assert not e.resume_cooldown(valid, healthy)
        assert e.trading_state == "COOLDOWN"
        e.reason, e.inventory_mismatch = None, None
        valid.valid = healthy.allowed = True
        now = e.cooldown.deadline+.001
        e.official_time = int(e.slug.rsplit("-", 1)[1])+30
        e.time_anchor = now
        assert e.resume_cooldown(valid, healthy)
        assert e.trading_state == "NORMAL"
        e.update_repair_episode()
        assert not e.cooldown.active
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("stop", ["cutoff", "fatal"])
async def test_market_cutoff_and_emergency_take_priority_over_cooldown(env, monkeypatch, stop):
    setup = await repaired_round(env, monkeypatch)
    v, e = setup[:2]
    loop = SMTradingLoop(e.cfg, e.slug, e.session_id)
    loop.executor, loop.production = e, True
    result = RunResult(e.meta.condition_id, e.session_id)
    monkeypatch.setattr(loop, "_merge", lambda *_: asyncio.sleep(0, result=False))
    try:
        if stop == "cutoff":
            e.official_time = int(e.slug.rsplit("-", 1)[1])+300
            e.time_anchor = e.monotonic()
        else:
            e.fatal = "TEST_EMERGENCY"
        before = len(v.post_requests)
        await loop._run_obi_session(result)
        assert result.stop_reason == ("MARKET_CUTOFF" if stop == "cutoff" else "TEST_EMERGENCY")
        assert len(v.post_requests) == before
    finally:
        await cleanup(setup)
