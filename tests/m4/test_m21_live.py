"""Offline M21 signal and execution acceptance against the shared Taker path."""

import asyncio
import json
import os
from contextlib import closing
from dataclasses import asdict
from decimal import ROUND_HALF_UP, Decimal as D, localcontext
from uuid import uuid4

import httpx
import pytest
from m4_helpers import close, feed_fixture
from py_clob_client_v2.http_helpers import helpers
from test_m4_live_budget import fixture
from test_m4_live_sdk_tick import real_sdk
from test_m4_live_task import evidence, serve
from test_multi_strategy_taker import decide, project, settings

from polymaker.config import RDPObiConfig
from polymaker.m4_journal import M4RiskJournal
from polymaker.m4_live_budget import BudgetLedger
from polymaker.m4_live_budget_market import BudgetMarket
from polymaker.m4_live_numeric import age_us
from polymaker.m4_live_pricing import origin
from polymaker.m4_run import Executor
from polymaker.m4_taker_coverage import rolling_coverage
from polymaker.m4_types import M4_LEDGER, M6_LEDGER, M21_LEDGER
from polymaker.rdp_sm_writer import AsyncRiskWriter
from polymaker.taker_pool import TakerPool, membership
from polymaker.taker_runner import SharedTakerFeed, StrategyReads, run_secondary


@pytest.fixture
def cfg(project):
    return settings(project, M21_LEDGER)


class Wire:
    def __init__(self, feed, clock, *, bid=".50", ask=".52", tick=".01", token="1"):
        self.feed, self.clock = feed, clock
        self.lane = feed.secondary[M21_LEDGER]
        self.zero = clock.now
        self.bid, self.ask, self.token = bid, ask, token
        feed.connect()
        feed.verify_ticks({"1": D(tick), "2": D(tick)})

    def send(self, when, *rows, source_lag_us=0, receipt_lag_us=0):
        self.clock.now = round(self.zero + when, 6)
        received = self.clock.now - receipt_lag_us / 1_000_000
        wall = self.feed.wall()
        envelope = {"market": self.feed.meta.condition_id,
                    "timestamp": str(D(str(wall)) * 1000 - D(source_lag_us) / 1000)}
        raw = json.dumps([{**envelope, **row} for row in rows])
        target = self.lane if receipt_lag_us < 0 else self.feed
        target._apply(raw, received_mono=received, received_wall=wall - receipt_lag_us / 1_000_000)

    def refresh(self, when, **kwargs):
        self.send(when, *({"event_type": "best_bid_ask", "asset_id": t, "best_bid": self.bid, "best_ask": self.ask}
                          for t in ("1", "2")), **kwargs)

    def trade(self, side, **extra):
        return {"event_type": "last_trade_price", "asset_id": self.token, "side": side, "size": "5",
                "price": self.bid if side == "SELL" else self.ask, "trade_id": side, **extra}

    def depth(self, when, bid, ask=None, *, extra=()):
        changes = [{"asset_id": self.token, "side": "BUY", "price": self.bid, "size": bid}]
        if ask is not None:
            changes.append({"asset_id": self.token, "side": "SELL", "price": self.ask, "size": ask})
        self.send(when, {"event_type": "price_change", "price_changes": changes + list(extra)})

    def form(self, *, same_frame_buy=False):
        self.send(.99, *({"event_type": "book", "asset_id": t,
                          "bids": [{"price": self.bid, "size": "100"}],
                          "asks": [{"price": self.ask, "size": "100"}]} for t in ("1", "2")))
        self.send(1., self.trade("SELL"))
        self.depth(1.10, "90")
        self.refresh(1.20)
        self.refresh(1.35)
        self.depth(1.48, "96", "80")
        if same_frame_buy:
            self.send(1.501, {"event_type": "best_bid_ask", "asset_id": self.token,
                              "best_bid": self.bid, "best_ask": self.ask}, self.trade("BUY"))
        else:
            self.refresh(1.501)


def shared(cfg, parent, m6=None):
    return SharedTakerFeed(parent.meta, RDPObiConfig(), monotonic=parent.monotonic,
                           wall=parent.wall, m6_settings=m6, m21_settings=cfg)


def test_m21_settings_membership_and_opening_reservations(project, cfg):
    assert membership(project) == (M4_LEDGER, M6_LEDGER)
    policies = {k: settings(project, k) for k in membership(project)} | {M21_LEDGER: cfg}
    pool = TakerPool(project / "pool.sqlite3", project)
    assert decide(pool, 900, policies, 23).payload["reason"] == "TAKER_POOL_INSUFFICIENT_FUNDS"
    first = decide(pool, 1200, policies, "23.01")
    assert first.settings(M21_LEDGER) == cfg
    assert first.payload["reserved"] == {M4_LEDGER: "10", M6_LEDGER: "3.00", M21_LEDGER: "10"}
    assert first.entry_block(M21_LEDGER) == "TAKER_STRATEGY_REMOVED"
    assert decide(pool, 1500, policies, 46).payload["reason"] == "TAKER_POOL_INSUFFICIENT_FUNDS"
    with closing(pool.connect()) as db:
        assert db.execute("SELECT release_at FROM market_openings WHERE epoch=1200").fetchone()[0] == 1620
        pool.release("offline", 1619)
        assert db.execute("SELECT released_at FROM market_openings WHERE epoch=1200").fetchone()[0] is None
        pool.release("offline", 1620)
        assert db.execute("SELECT released_at FROM market_openings WHERE epoch=1200").fetchone()[0] == 1620


def test_m21_addition_preserves_existing_opening_and_primary_membership_file(project, cfg):
    primary = project / "config/m4-live/strategies.toml"
    previous = primary.read_bytes()
    pool = TakerPool(project / "pool.sqlite3", project)
    current = {key: settings(project, key) for key in membership(project)}
    opened = decide(pool, 900, current, 40)
    additions = primary.with_name("strategies-additions.toml")
    additions.write_text('enabled = ["M21"]\n')
    assert primary.read_bytes() == previous
    assert membership(project) == (M4_LEDGER, M6_LEDGER, M21_LEDGER)
    assert opened.entry_block(M4_LEDGER) is None and opened.entry_block(M6_LEDGER) is None
    assert opened.settings(M21_LEDGER) is None
    assert opened.entry_block(M21_LEDGER) == "TAKER_STRATEGY_NOT_IN_OPENING"
    following = decide(pool, 1200, {**current, M21_LEDGER: cfg}, 40)
    assert following.settings(M21_LEDGER) == cfg and following.entry_block(M21_LEDGER) is None
    additions.write_text('enabled = ["M4", "M21"]\n')
    with pytest.raises(ValueError, match="TAKER_POOL_MEMBERSHIP_INVALID"):
        membership(project)


@pytest.mark.parametrize("lag,invalid", [(0, False), (19_999, True), (20_000, True), (20_001, True)])
def test_strict_m21_order_keeps_m6_independent(project, cfg, lag, invalid):
    parent, clock, _ = feed_fixture()
    wire = Wire(shared(cfg, parent, settings(project, M6_LEDGER)), clock)
    wire.form()
    assert wire.lane.m1.counts["OPPORTUNITY_IDENTIFIED"] == 1
    wire.refresh(1.501, source_lag_us=lag)
    assert bool(wire.lane.m1.opportunities) is not invalid
    if invalid:
        assert wire.lane.research.reasons["1"] == "UNRECONSTRUCTABLE_SOURCE_ORDER"
        assert not wire.lane.research.books["1"].complete
    assert wire.feed.m6.connected and not wire.feed.m6.m1.failure


@pytest.mark.parametrize("source_age,receipt_age,valid", [
    (-2001, 0, False), (-2000, 0, True), (-1999, 0, True), (499999, 0, True),
    (500000, 0, True), (500001, 0, False), (0, -1, False),
    (0, 0, True), (0, 199999, True), (0, 200000, True), (0, 200001, False),
])
def test_boundaries_with_epoch_and_monotonic_clocks(cfg, source_age, receipt_age, valid):
    parent, clock, _ = feed_fixture()
    wire = Wire(shared(cfg, parent), clock)
    wire.send(1.0, {"event_type": "book", "asset_id": "1", "bids": [{"price": ".50", "size": "100"}],
                    "asks": [{"price": ".52", "size": "100"}]},
              source_lag_us=source_age, receipt_lag_us=receipt_age)
    assert wire.lane.research.good["1"] is valid


def test_raw_parent_frame_and_run_identity(cfg):
    parent, clock, _ = feed_fixture()
    blocked = Wire(shared(cfg, parent), clock)
    blocked.form(same_frame_buy=True)
    assert blocked.lane.m1.counts["CANDIDATE"] == 1
    assert not blocked.lane.m1.opportunities
    wires = []
    for _ in range(2):
        parent, clock, _ = feed_fixture()
        wire = Wire(shared(cfg, parent), clock)
        wire.form()
        wires.append(wire)
    a, b = wires
    [left] = a.lane.m1.opportunities.values()
    [right] = b.lane.m1.opportunities.values()
    assert left.episode_id != right.episode_id and left.root_id != right.root_id
    assert b.lane.m1.active_reason((left.episode_id,)) == "M21_EVIDENCE_ENDED"


def test_no_uses_connection_generation_separately_from_book_epoch(cfg):
    parent, clock, _ = feed_fixture()
    wire = Wire(shared(cfg, parent), clock, token="2")
    wire.form()
    [opportunity] = wire.lane.m1.opportunities.values()
    assert wire.lane.research.book_epochs["2"] > wire.lane.generation
    assert opportunity.outcome == "NO" and opportunity.generation == wire.lane.generation
    assert wire.lane.m1.take(opportunity)
    wire.refresh(1.501, source_lag_us=20_000)
    assert wire.lane.m1.active_reason((opportunity.episode_id,)) == "M21_EVIDENCE_ENDED"


def test_m21_does_not_verify_stale_rest_tick_after_ws_change(cfg):
    parent, clock, _ = feed_fixture()
    wire = Wire(shared(cfg, parent), clock)
    wire.form()
    wire.send(1.52, {"event_type": "tick_size_change", "asset_id": "1",
                     "old_tick_size": ".01", "new_tick_size": ".001"})
    wire.lane.verify_ticks({"1": D(".01")})
    assert "1" not in wire.lane.tick_verified and not wire.lane.m1.opportunities
    assert wire.lane.m1.events[-1]["reason"] == "M21_WS_OFFICIAL_TICK_CONFLICT"
    wire.lane.verify_ticks({"1": D(".001")})
    assert wire.lane.tick_verified["1"] == D(".001")
    assert not wire.lane.snapshot().valid


def test_shared_decode_preserves_m21_depth_and_raw_frame_order(cfg):
    parent, clock, _ = feed_fixture()
    wire = Wire(shared(cfg, parent), clock)
    wire.form()
    [signal] = wire.lane.m1.opportunities.values()
    unchanged = wire.lane.books["2"].asks
    wire.refresh(1.510)
    assert wire.lane.books["2"].asks is unchanged
    wire.depth(1.52, "96", "80", extra=[
        {"asset_id": "1", "side": "SELL", "price": ".51", "size": "3"},
    ])
    assert wire.lane.books["1"].asks == ((D(".51"), D(3)), (D(".52"), D(80)))
    assert wire.lane.books["2"].asks is unchanged
    assert wire.lane.m1.active_reason((signal.episode_id,)) is None
    with pytest.raises(ValueError, match="WS_FRAME_REBUILD"):
        wire.feed._apply('"PING"', received_mono=clock.now, received_wall=wire.feed.wall())


def test_m21_only_bypasses_m4_processing_and_reports_actual_frame_ages(cfg, monkeypatch):
    from polymaker.m4_signals import M4Feed

    parent, clock, _ = feed_fixture()
    feed = SharedTakerFeed(parent.meta, RDPObiConfig(), monotonic=parent.monotonic,
                           wall=parent.wall, m21_settings=cfg, m21_only=True)

    def forbidden(*args, **kwargs):
        pytest.fail("M4 detection ran in M21-only mode")

    monkeypatch.setattr(M4Feed, "_apply", forbidden)
    monkeypatch.setattr(feed.m1, "tick", forbidden)
    wire = Wire(feed, clock)
    wire.form()
    feed.maintain_signals()
    assert tuple(feed.secondary) == (M21_LEDGER,)
    assert wire.lane.m1.counts["OPPORTUNITY_IDENTIFIED"] == 1
    wire.refresh(1.800, receipt_lag_us=210_000)
    health = wire.lane.market_input_health()
    assert health["application_frames"] == 8
    assert health["application_queue_peak_us"] == 210_000
    assert health["application_queue_over_200ms"] == 1


async def test_m21_only_observes_after_t240_without_creating_a_task(project, cfg, monkeypatch):
    from test_m4_taker_watch import watch

    (project / "config/m4-live/strategies.toml").write_text("enabled = []\n")
    (project / "config/m4-live/strategies-additions.toml").write_text('enabled = ["M21"]\n')
    assert membership(project) == (M21_LEDGER,)
    primary, lead, parent, clock, posts, transport = await fixture(project, monkeypatch)
    root = project / "research_outputs/m4_live"
    root.mkdir(parents=True)
    pool = TakerPool(root / "taker-pool.sqlite3", project)
    epoch = int(lead.slug.rsplit("-", 1)[1])
    opening = pool.decide(lead.gateway.funder, epoch, lead.meta.condition_id, {M21_LEDGER: cfg},
                          {"official_balance_usd": "11", "applicable_allowance_usd": "11"},
                          clock=lambda: (epoch + 1, epoch + 2))
    assert opening.payload["reserved"] == {M21_LEDGER: "10"}
    feed = SharedTakerFeed(parent.meta, RDPObiConfig(), monotonic=parent.monotonic,
                           wall=parent.wall, m21_settings=cfg, m21_only=True)
    finished = []

    async def run(market):
        clock.now += cfg.cutoff_sec + 1 - market.elapsed()
        wire = Wire(feed, clock)
        await market._advance()
        assert market.ledger.get("buy_closed") and not market.feed_closed
        wire.form()
        assert wire.lane.m1.counts["OPPORTUNITY_IDENTIFIED"] == 1
        await market._advance()
        assert not market.ledger.rows() and not market.ledger.attempts() and not posts
        clock.now += cfg.close_sec + .001 - market.elapsed()
        await market.recycle_after_cutoff()
        assert market.feed_closed
        market.complete_window()
        await market.e.journal.flush()
        finished.append(True)

    monkeypatch.setattr(BudgetMarket, "run", run)

    def make_executor(*args, **kwargs):
        return Executor(*args, monotonic=parent.monotonic, **kwargs)

    try:
        await run_secondary(primary, opening, feed.secondary[M21_LEDGER], root,
                            make_executor, lead.control, asyncio.Event(), strategy=M21_LEDGER)
        assert finished
        report = watch.inspect_market(project, epoch, epoch + 320, strategy="M21")
        assert report["identified_signal_count"] == 1
        assert report["durable_task_count"] == report["native_posts"] == 0
        assert list(report["explicit_skips"].values()) == ["M21_FORMAL_AFTER_T240"]
    finally:
        await close(primary, lead)
        transport.close()


def test_price_band_and_lifetime_boundaries(cfg):
    for price, allowed in ((".499", False), (".500", True), (".501", True),
                           (".949", True), (".950", False), (".951", False)):
        assert BudgetLedger.post_price_allowed(D(price), {"post_price_band": cfg.post_price_band}) == allowed
    for deadline in (3.999999, 4.0):
        plan = origin(1, 1, 20, deadline=deadline, ledger_strategy=M21_LEDGER)
        assert plan["lifetime_us"] <= 3_000_000
        with pytest.raises(ValueError):
            origin(1, 1, 20, deadline=deadline, ledger_strategy=M6_LEDGER)
    with pytest.raises(ValueError):
        origin(1, 1, 20, deadline=4.000001, ledger_strategy=M21_LEDGER)


def test_reused_clock_conversions_preserve_integer_microsecond_boundaries():
    epoch = 1795096625.0
    offsets = (-.002001, -.002, -.001999, .0000005, .199999, .2, .200001,
               .499999, .5, .500001, 2.999999, 3., 3.000001)
    for precision in (12, 28):
        with localcontext() as ctx:
            ctx.prec = precision
            for offset in offsets:
                later = epoch + offset
                expected = tuple(int((D(str(t)) * 1_000_000).to_integral_value(rounding=ROUND_HALF_UP))
                                 for t in (later, epoch))
                assert age_us(later, epoch) == age_us(later, D(str(epoch))) == expected[0] - expected[1]


@pytest.mark.parametrize("action", ["zero_retry", "partial", "deadline", "reprice"])
async def test_m21_native_fak_lifecycle(project, cfg, monkeypatch, action):
    m4, lead, parent, clock, primary_posts, primary_transport = await fixture(project, monkeypatch)
    price = {"bid": ".93", "ask": ".95", "tick": ".001"} if action == "reprice" else {}
    wire = Wire(shared(cfg, parent, settings(project, M6_LEDGER)), clock, **price)
    feed = wire.lane
    market_root = project / "research_outputs/m4_live"
    path = market_root / "m21-ledgers" / f"{lead.slug}.sqlite3"
    ident = {"strategy": M21_LEDGER, "mode": "LIVE", "funder": lead.gateway.funder,
             "market_id": lead.meta.condition_id, "settings_hash": cfg.fingerprint}
    ledger = BudgetLedger(path, ident, settings=cfg, fees=m4.ledger.fees)
    sid = "m4_live_" + uuid4().hex
    ledger.initialize_session(sid, cfg.model_dump(mode="json"), asdict(lead.meta))
    reads = StrategyReads(lead.reads, feed, ledger, cfg)
    journal = AsyncRiskWriter(M4RiskJournal(market_root / "m21-journal", lead.gateway.funder, session_id=sid))
    ex = Executor(None, lead.slug, sid, reads, journal, ledger=ledger, control=lead.control,
                  live=True, monotonic=parent.monotonic)
    ex.meta, ex.official_time, ex.time_anchor, ex.stop_event = lead.meta, lead.official_time, lead.time_anchor, lead.stop_event
    market = BudgetMarket(ex, cfg, ex.stop_event, account_owner=m4, display=lambda _: None)
    real_sdk(reads.gateway._client, monkeypatch)
    calls, queries = [], None
    feed.m1.record = lambda row: ex.record("sm_m21_signal", row, event_wall=feed.wall()) or ex.journal.ready

    def respond(request):
        body, = json.loads(request.content)
        calls.append(body)
        client = reads.gateway._client
        oid = client._operation_value("attempt").expected_order_id
        reply = {"success": True, "orderID": oid}
        if action == "zero_retry":
            reply.update(errorMsg="no orders found to match with FAK order. FAK orders are partially filled or killed if no match is found.",
                         status="", makingAmount="", takingAmount="", tradeIDs=[], transactionsHashes=[])
        return httpx.Response(200, json=[reply])

    with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
        monkeypatch.setattr(helpers, "_http_client", transport)
        try:
            wire.form()
            market.collect()
            [opportunity] = market.pending.values()
            assert ledger.root_plan(opportunity.episode_id)["pricing_origin"]["lifetime_us"] == 3_000_000
            if action == "reprice":
                await market.submit(opportunity)
                assert not calls and not ledger.attempts()
                assert ledger.get("m21_execution_wait:" + opportunity.episode_id)["reason"] == "M4_POST_PRICE_OUTSIDE_BAND"
                wire.depth(1.52, "96", "80", extra=[
                    {"asset_id": "1", "side": "SELL", "price": ".947", "size": "100"}])
                await market.submit(opportunity)
                assert not calls and not ledger.attempts()
                assert ledger.get("m21_execution_wait:" + opportunity.episode_id)["reason"] == "M21_NO_LEGAL_FAK_QUANTITY"
                wire.depth(1.53, "96", "80", extra=[
                    {"asset_id": "1", "side": "SELL", "price": ".94", "size": "100"}])
            if action == "deadline":
                def late(attempt):
                    clock.now = opportunity.order_deadline
                    return market.native_guard(attempt)
                monkeypatch.setattr(reads.gateway._client, "m4_native_guard", late)
            await market.submit(opportunity)
            [attempt] = ledger.attempts()
            assert not primary_posts
            if action == "deadline":
                assert not calls and attempt["state"] == "UNSENT"
                assert json.loads(attempt["proof"])["reason"] == "M21_SIGNAL_DEADLINE"
            else:
                assert len(calls) == 1 and calls[0]["orderType"] == "FAK"
                assert ledger.owns_order(attempt["order_id"]) and not m4.ledger.owns_order(attempt["order_id"])
                plan = json.loads(attempt["plan"])
                assert plan["post_price_band_source"] == "M21_OPPORTUNITY_PRICE_RISK_V1"
                assert D(plan["commitment_usdc"]) <= D(5)
                signed = calls[0]["order"]
                assert D(signed["makerAmount"]) % 10_000 == 0 and D(signed["makerAmount"]) >= 1_000_000
                assert D(signed["takerAmount"]) % 100_000 == 0
                if action == "zero_retry":
                    wire.depth(1.53, "96", "80", extra=[
                        {"asset_id": "1", "side": "SELL", "price": ".51", "size": "100"}])
                    await market.submit(opportunity)
                    assert len(calls) == 2 and ledger.attempts()[1]["order_id"] != attempt["order_id"]
                elif action == "partial":
                    order, trade = evidence(market, ex, attempt["order_id"], "2", status="MINED", price=".49")
                    queries, _ = serve(monkeypatch, transport, order, trade)
                    await market.reconcile(attempt["order_id"])
                    assert ledger.attempt(attempt["order_id"])["state"] == "SETTLING"
                    assert ledger.pending_cost(opportunity.episode_id) > 0
                    await market.submit(opportunity)
                    trade["status"] = "CONFIRMED"
                    await market.reconcile(attempt["order_id"])
                    await market.submit(opportunity)
                    row = ledger.row(opportunity.episode_id)
                    assert len(calls) == 1 and row["reason"] == "M21_CONFIRMED_FILL_NO_TOP_UP"
                    assert D(row["filled"]) == 2 and D(row["spent"]) < D("1.04")
            ledger.validate_recovery()
            await journal.flush()
            monkeypatch.setattr("polymaker.m4_taker_coverage.FUNDER", lead.gateway.funder.lower())
            for log in (market_root / "m21-journal").glob("*.jsonl"):
                os.utime(log, (feed.wall(), feed.wall()))
            rollup = rolling_coverage(project, {}, int(lead.slug.rsplit("-", 1)[-1]) + 300, 300, strategy="M21")
            assert [item["ledger"] for item in rollup["ledger_sources"]] == [str(path)]
            assert rollup["identified_signals"] == 1
        finally:
            await close(market, ex)
            await close(m4, lead)
            primary_transport.close()
            if queries:
                queries.close()
