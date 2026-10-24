"""New policy through actual signing/WAL/gateway/reconciliation, fake venue only."""
import asyncio
import json
import time
from collections import deque
from decimal import Decimal
from threading import RLock
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from polymaker.cli import app
from polymaker.config import Config, RDPRepairPathConfig, RDPSMConfig
from polymaker.rdp_sm_entry import run_current_market
from polymaker.rdp_sm_execution import SMWindowExecutor
from polymaker.rdp_sm_logging import HumanRunLog
from polymaker.rdp_sm_marketdata import RepairPricePoint
from polymaker.rdp_sm_path_repair import PathRepairController
from polymaker.repair_policy import PathPoint, RepairPolicy
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_obi_execution import cleanup, setup_obi
from test_rdp_sm_repair import env as repair_env
from test_rdp_sm_repair import open_yes, reflect_positions


@pytest.fixture
def env(monkeypatch, tmp_path):
    yield from repair_env.__wrapped__(monkeypatch, tmp_path)


async def setup_policy(env, monkeypatch):
    setup = await setup_obi(env, monkeypatch)
    e = setup[1]
    official, anchor = e.official_time, e.time_anchor

    async def server_time():
        e.reads.network.observe("GET", .001, endpoint="/time")
        return official+time.monotonic()-anchor

    # These tests run a real monotonic clock; the legacy venue's server clock
    # otherwise remains frozen at T20 and jumps backwards on every account read.
    monkeypatch.setattr(e.reads, "server_time", server_time)
    e.cfg.rdp_sm.repair.path = RDPRepairPathConfig(
        tp_profit_rate=".001", shock_window_sec=2, shock_z=1.6,
        drawdown_per_share=".02")
    e.cfg.rdp_sm.repair.review_sec = .03
    await open_yes(setup)
    controller = PathRepairController(e, setup[3])
    monkeypatch.setattr(controller, "sigma", lambda: .02)
    setup[7].update(bid=".50", yes_ask=".52", no_bid=".48", no_ask=".50")
    await asyncio.sleep(.025)
    setup[3].account = (await e._snapshot(include_books=False))[3]
    return setup, controller


async def test_tp_posts_below_best_bid_without_waiting_for_another_event(env, monkeypatch):
    setup, controller = await setup_policy(env, monkeypatch)
    v, e, feed = setup[:3]
    waits = []
    native = feed.wait_after

    async def tracked(sequence, timeout):
        waits.append(timeout)
        await native(sequence, timeout)

    monkeypatch.setattr(feed, "wait_after", tracked)

    def fill(venue, oid):
        row = venue.rows[oid]
        if row["asset_id"] == "2" and not row["associate_trades"]:
            venue.fill(oid, "tp-complete", 5)
            reflect_positions(venue)

    v.on_order_read = fill
    try:
        result, _ = await controller.repair(*e.economic_inventory(), "tp")
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert len(v.post_requests) == 2
        quote = result.requested_quotes[0]
        assert quote.outcome == "NO" and quote.price == .46 and quote.size == 5
        assert .5 not in waits  # no fixed activation wait introduced after signing
        assert e.economic_inventory() == (Decimal(5), Decimal(5))
        assert e.repair_episode is None
        assert controller.policy.tp_price == Decimal(".46")
        records = journal_records(e.journal.path)
        assert any(r["kind"] == "sm_repair_market_path" for r in records)
        assert any(r["kind"] == "sm_repair_policy_state" for r in records)
    finally:
        await cleanup(setup)


async def test_partial_tp_hands_off_to_sl_without_waiting_for_another_loop(env, monkeypatch):
    setup, controller = await setup_policy(env, monkeypatch)
    v, e, _, lifecycle, _, _, _, pump, _ = setup
    first_no = []

    def partial(venue, oid):
        row = venue.rows[oid]
        if row["asset_id"] != "2" or row["associate_trades"]:
            return
        if not first_no:
            first_no.append(oid)
            venue.fill(oid, "partial-tp", 2)
            reflect_positions(venue)
            pump.update(bid=".44", yes_ask=".46", no_bid=".54", no_ask=".56")
        elif oid != first_no[0]:
            venue.fill(oid, "sl-minimum-five", 5)
            reflect_positions(venue)

    v.on_order_read = partial
    try:
        result, _ = await controller.repair(*e.economic_inventory(), "tp-partial")
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert controller.policy.mode == "SL"
        assert v.rows[first_no[0]]["status"] == "CANCELED"
        assert len(v.post_requests) == 3  # replacement is part of the same handoff
        quote = e.attempts[-1].quote
        assert quote.price == .559 and quote.size == 5  # venue NO tick is .001; remaining 3 cannot be new
        assert e.economic_inventory() == (Decimal(5), Decimal(7))
        restored = PathRepairController(e, lifecycle)
        assert e.repair_episode is None and restored._policy() is None
        assert Decimal(e.cooldown.last_result["paired_shares"]) == 5
        assert Decimal(e.cooldown.last_result["pair_pnl"]) == Decimal(".903")
        assert await restored.repair(*e.economic_inventory(), "no-ping-pong") is None
        assert len(v.post_requests) == 3
    finally:
        await cleanup(setup)


async def test_sl_cancel_happens_even_if_minimum_replacement_cannot_be_funded(env, monkeypatch):
    setup, controller = await setup_policy(env, monkeypatch)
    v, e, _, lifecycle, _, _, _, pump, _ = setup

    def partial(venue, oid):
        row = venue.rows[oid]
        if row["asset_id"] == "2" and not row["associate_trades"]:
            venue.fill(oid, "partial-before-block", 2)
            reflect_positions(venue)
            lifecycle.account = {"basic_spendable_budget_usd": .50}
            pump.update(bid=".44", yes_ask=".46", no_bid=".54", no_ask=".56")

    v.on_order_read = partial
    try:
        result, _ = await controller.repair(*e.economic_inventory(), "tp-before-block")
        assert result.close_barrier == "SAFE_TO_CONTINUE"
        assert controller.policy.mode == "SL"
        assert all(row["status"] == "CANCELED" for row in v.rows.values())
        lifecycle.account = {"basic_spendable_budget_usd": .50}
        assert await controller.repair(*e.economic_inventory(), "blocked-sl") is None
        assert len(v.post_requests) == 2
        assert controller.policy.mode == "SL"
        assert e.economic_inventory() == (Decimal(5), Decimal(2))
    finally:
        await cleanup(setup)


async def test_force_boundary_works_during_sigma_warmup_and_does_not_revert(env, monkeypatch):
    setup, controller = await setup_policy(env, monkeypatch)
    e = setup[1]
    try:
        controller._policy().mode = "TP"
        controller.plan = {"mode": "TP"}
        monkeypatch.setattr(controller, "sigma", lambda: None)
        assert controller.boundary_close_reason(None, 180) == "REPAIR_MODE_SL_CANCEL_OLD_STATE"
        assert controller.policy.mode == "SL"
        assert PathRepairController(e, setup[3])._policy().mode == "SL"
    finally:
        await cleanup(setup)


async def test_t180_cancels_tp_without_new_book_events_or_sigma(env, monkeypatch):
    setup, controller = await setup_policy(env, monkeypatch)
    v, e, feed, lifecycle, _, wire, _, pump, _ = setup
    start = int(e.slug.rsplit("-", 1)[1])
    e.official_time, e.time_anchor = start+179.3, time.monotonic()
    v.time = start+179.3
    feed.connect()  # the simulated 159-second wall jump is an explicit new generation
    official, anchor = e.official_time, e.time_anchor

    async def server_time():
        e.reads.network.observe("GET", .001, endpoint="/time")
        return official+time.monotonic()-anchor

    monkeypatch.setattr(e.reads, "server_time", server_time)
    await asyncio.sleep(.025)
    loop = asyncio.get_running_loop()

    def after_post():
        pump["running"] = False
        loop.call_soon_threadsafe(monkeypatch.setattr, controller, "sigma", lambda: None)

    wire.delay = after_post
    def fill(venue, oid):
        row = venue.rows[oid]
        if row["asset_id"] == "2" and Decimal(row["price"]) >= Decimal(".49") and not row["associate_trades"]:
            venue.fill(oid, "sl-after-warmup", 5)
            reflect_positions(venue)

    v.on_order_read = fill
    try:
        result, _ = await controller.repair(*e.economic_inventory(), "tp-before-t180")
        assert result.requested_quotes[0].price == .46
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert controller.policy.mode == "SL" and controller.policy.last_reason == "TIME_FORCE_SL"
        assert 180 <= controller.elapsed() < 181
        assert len(v.post_requests) == 3
        wire.delay = None
        assert e.attempts[-1].quote.price == .499  # actual tick .001, sigma UNKNOWN
        assert e.economic_inventory() == (Decimal(5), Decimal(5))
    finally:
        await cleanup(setup)


async def test_intrasecond_adverse_move_is_not_lost_when_price_recovers_before_review(env, monkeypatch):
    setup, controller = await setup_policy(env, monkeypatch)
    _, e, feed, _, _, _, _, pump, _ = setup
    e.cfg.rdp_sm.repair.path.shock_window_sec = .01
    monkeypatch.setattr(controller, "_sigma_at", lambda _: .01)
    try:
        pump.update(bid=".69", yes_ask=".71", no_bid=".29", no_ask=".31")
        await asyncio.sleep(.025)
        pump.update(bid=".64", yes_ask=".66", no_bid=".34", no_ask=".36")
        await asyncio.sleep(.025)
        pump.update(bid=".69", yes_ask=".71", no_bid=".29", no_ask=".31")
        await asyncio.sleep(.025)
        decision, point = controller.observe(feed.snapshot(repair=True))
        assert point.p == .7 and decision["mode"] == "SL"
        assert decision["last_transition"]["observations"]["p"] == .65
        assert decision["last_transition"]["reason"] == "ADVERSE_SHOCK"
    finally:
        await cleanup(setup)


async def test_unvalidated_new_live_policy_stops_before_any_exchange_read(env):
    v, cfg = env
    cfg.rdp_sm.normal_entry_mode = "obi_v1"
    cfg.rdp_sm.repair.policy = "hd_tp_sl_v1"
    result = await run_current_market(cfg)
    assert result.stop_reason == "REPAIR_NOT_READY_FOR_LIVE"
    assert not result.repair_readiness["ready_for_live"]
    assert not v.calls and not v.post_requests


async def test_offline_market_loop_selects_new_controller_and_owns_teardown(env, monkeypatch):
    from test_rdp_sm_obi_entry import (
        test_new_mode_entire_entry_has_no_rest_book_and_owns_ws_teardown,
    )

    _, cfg = env
    cfg.rdp_sm.repair.policy = "hd_tp_sl_v1"
    await test_new_mode_entire_entry_has_no_rest_book_and_owns_ws_teardown(
        env, monkeypatch, disconnect_preflight=False, cancel_before_order=False,
        prior_unresolved=False, cached=False)


def test_readonly_cli_reports_confirmed_parameters_and_pending_replay_without_credentials(env, monkeypatch):
    _, cfg = env
    cfg.rdp_sm.normal_entry_mode = "obi_v1"
    cfg.rdp_sm.repair.policy = "hd_tp_sl_v1"
    monkeypatch.setattr(Config, "load", lambda *_: cfg)
    result = CliRunner().invoke(app, ["rdp-sm-repair-check"])
    assert result.exit_code == 2
    data = json.loads(result.stdout)
    assert not data["ready_for_live"] and data["parameters_complete"]
    assert data["parameters"]["shock_z"] == 1.5
    assert "EXECUTION_REPLAY_NOT_VALIDATED" in data["reasons"]
    assert "pk" not in data and "secrets" not in data


def test_true_delta_breach_allows_affordable_partial_restoration():
    cfg = RDPSMConfig()
    e = SimpleNamespace(cfg=SimpleNamespace(rdp_sm=cfg),
                        meta=SimpleNamespace(min_order_size=5),
                        repair_episode={"side": "YES"},
                        economic_inventory=lambda: (Decimal(7), Decimal(0)),
                        committed=lambda: Decimal(11))
    e.delta_candidate_allowed = lambda d, side, q: SMWindowExecutor.delta_candidate_allowed(e, d, side, q)
    controller = PathRepairController(e, SimpleNamespace(account={"basic_spendable_budget_usd": 4}))
    controller.policy = RepairPolicy(cfg.repair.path, mode="SL")
    book = SimpleNamespace(best_bid=Decimal(".78"), best_ask=Decimal(".801"), tick=Decimal(".001"))
    plan = controller._make_plan(SimpleNamespace(no=book), {"mode": "SL"},
                                 PathPoint(100, .2, None, 1, 7, -.3))
    assert plan["price"] == .8 and plan["quantity"] == 5
    assert e.delta_candidate_allowed(Decimal(7), "NO", Decimal(5))


def test_human_log_displays_new_policy_without_legacy_q_fields():
    observer = HumanRunLog(repair_mode=True)
    try:
        lines = observer.render("events", [
            ("sm_repair_decision", {"policy": "hd_tp_sl_v1", "action": "BUY", "mode": "TP",
                                    "side": "NO", "price": .46, "reason": "PROFIT_RATE_TARGET"}),
            ("sm_repair_execution_blocked", {"mode": "SL", "reason": "MINIMUM_NOT_FEASIBLE"}),
        ])
        text = "\n".join(lines)
        assert "Mode=TP" in text and "PROFIT_RATE_TARGET" in text
        assert "Mode=SL" in text and "MINIMUM_NOT_FEASIBLE" in text
        assert "ExpectedPnL" not in text
    finally:
        observer.close()


@pytest.mark.parametrize("timing", ["native", "unknown", "late_native"])
@pytest.mark.parametrize("quiet_fill_anchor", [False, True, "unchanged"])
def test_controller_inserts_partial_fill_before_next_price_move(timing, quiet_fill_anchor):
    """Regression: previous-quote inventory must not price a post-fill move."""
    clock = SimpleNamespace(now=102.)
    def fill(tid, oid, token, qty, price):
        return SimpleNamespace(trade_id=tid, maker_order_id=oid, token_id=token,
                               size=qty, price=price)
    opening = fill('a', 'orig', '1', 5., .3)
    partial = fill('b', 'repair', '2', 2., .5)
    dust = fill('old', 'old', '2', .02, .5)
    def fills():
        return [dust, opening]+([partial] if clock.now >= 104 else [])
    cfg = RDPSMConfig()
    e = SimpleNamespace(
        cfg=SimpleNamespace(rdp_sm=cfg), official_time=1000., time_anchor=0., slug='btc-updown-5m-1000',
        monotonic=lambda: clock.now, meta=SimpleNamespace(yes=SimpleNamespace(token_id='1'),
                                                        no=SimpleNamespace(token_id='2')),
        attempts=[SimpleNamespace(order_id='orig', quote=SimpleNamespace(window_id='open')),
                  SimpleNamespace(order_id='repair', quote=SimpleNamespace(window_id='repair'))],
        repair_episode={'opening_window':'open', 'windows':['open','repair'], 'original_qty':'5',
                        'side':'YES','first_fill_elapsed':100., 'opening_monotonic':100.},
        economic_fills=fills,
        processor=SimpleNamespace(economic_fills_for_orders=lambda ids: [f for f in fills() if f.maker_order_id in ids]),
        fill_observed_at={('old','old'):{'elapsed':1.}, ('a','orig'):{'elapsed':100.,'source':'VENUE_TRADE_TIME'},
                          ('b','repair'):{'elapsed':103. if timing=='native' else 100.,
                                          'source':'LOCAL_FIRST_TRADE_OBSERVATION' if timing=='unknown' else 'VENUE_TRADE_TIME',
                                          'observed_elapsed':103., 'economic_observed_elapsed':103.}},
        record=lambda *_: None)
    def sample(seq, t, p):
        return RepairPricePoint(seq,t,1000+t,1,p,str(p-.005),str(p+.005),
                                str(1-p-.005),str(1-p+.005),'.01','.01',1)
    feed = SimpleNamespace(lock=RLock(), probability_points=[], repair_path_segment=1,
                           repair_price_points=deque([sample(1,99 if quiet_fill_anchor else 100,.5)]),
                           wall=lambda:1000+clock.now)
    moved_at_first_review = quiet_fill_anchor is True
    if moved_at_first_review:
        feed.repair_price_points.append(sample(2,102,.48))
    controller = PathRepairController(e, SimpleNamespace(feed=feed))
    controller.sigma = lambda: None
    def snapshot(seq, p):
        return SimpleNamespace(sequence=seq,generation=1,
            yes=SimpleNamespace(best_bid=Decimal(str(p))-Decimal('.005'),
                                best_ask=Decimal(str(p))+Decimal('.005'),tick=Decimal('.01')),
            no=SimpleNamespace(best_bid=1-Decimal(str(p))-Decimal('.005'),
                               best_ask=1-Decimal(str(p))+Decimal('.005'),tick=Decimal('.01')))
    controller.observe(snapshot(2,.48) if moved_at_first_review else snapshot(1,.5))
    assert controller.last_path_sequence == feed.repair_price_points[-1].sequence
    if timing != 'native':
        feed.repair_price_points.append(sample(3,102.5,.47 if moved_at_first_review else .49))
    clock.now = 104.
    end_price = (.43 if timing=='native' else .42) if moved_at_first_review else (.45 if timing=='native' else .44)
    feed.repair_price_points.append(sample(4,104,end_price))
    decision, point = controller.observe(snapshot(4,end_price))
    assert point.delta == pytest.approx(2.98) and point.episode_delta == 3
    assert decision['observations']['drawdown_per_share'] == pytest.approx(
        .05 if moved_at_first_review and timing=='native' else .03)
    assert decision['observations']['path_status'] == ('COMPLETE' if timing=='native' else 'PARTIAL_PATH')
    before = controller.policy.payload()
    controller.observe(snapshot(4,end_price), project=True)
    assert controller.policy.payload() == before  # final-submit guard has no state/journal side effects
    controller.policy.break_path('EXPLICIT_MARKET_GAP')
    resumed, _ = controller.observe(snapshot(4,end_price))
    assert resumed['mode'] == 'TP'
    assert resumed['observations']['drawdown_per_share'] == 0  # never reuse the original fill anchor after a gap
