"""Shared cash and isolated M4/M6 execution, with mock native HTTP."""
import asyncio
import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import asdict, replace
from decimal import Decimal as D
from pathlib import Path
from types import MethodType
from uuid import uuid4

import httpx
import pytest
from m4_helpers import close
from py_clob_client_v2.exceptions import PolyApiException
from py_clob_client_v2.http_helpers import helpers
from test_m4_live_budget import fixture
from test_m4_live_task import evidence, serve

from polymaker.config import RDPObiConfig
from polymaker.execution.gateway import ExecutionGateway
from polymaker.m4_journal import M4RiskJournal
from polymaker.m4_live_budget import BudgetLedger
from polymaker.m4_live_budget_market import BudgetMarket
from polymaker.m4_live_config import M4LiveSettings
from polymaker.m4_run import Executor
from polymaker.m4_types import M4_LEDGER, M6_LEDGER, M21_LEDGER
from polymaker.rdp_sm_writer import AsyncRiskWriter
from polymaker.taker_pool import POLICY, TakerPool
from polymaker.taker_runner import (
    SharedTakerFeed,
    StrategyReads,
    prepare_opening,
    strategy_settings,
)

SOURCE = Path(__file__).resolve().parents[2]


@pytest.fixture
def project(tmp_path):
    for name in ("m4.toml", "m6.toml", "m4-live/execution.toml", "m4-live/strategies.toml", "m4-live/m21.toml"):
        path = tmp_path / "config" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SOURCE / "config" / name, path)
    (tmp_path / "config/m4-live/strategies.toml").write_text('enabled = ["M4", "M6"]\n')
    return tmp_path


def settings(project, key):
    return strategy_settings(project, key, M4LiveSettings.load(project))


def decide(pool, epoch, policies, cash, *, elapsed=10):
    return pool.decide("offline", epoch, f"condition-{epoch}", policies,
                       {"official_balance_usd": str(cash), "applicable_allowance_usd": str(cash)},
                       clock=lambda: (epoch+elapsed-1, epoch+elapsed))


def test_strict_open_atomic_resume_and_release(project):
    policies = {key: settings(project, key) for key in (M4_LEDGER, M6_LEDGER)}
    pool = TakerPool(project / "pool.sqlite3", project)
    skipped = decide(pool, 900, policies, 13)
    assert skipped.payload["reason"] == "TAKER_POOL_INSUFFICIENT_FUNDS"
    assert decide(pool, 900, policies, 60).payload == skipped.payload
    with ThreadPoolExecutor(2) as threads:
        results = list(threads.map(lambda _: decide(pool, 1200, policies, "13.01").payload, range(2)))
    opened = pool.existing("offline", 1200)
    assert results[0] == results[1] == opened.payload
    assert opened.payload["reserved"] == {M4_LEDGER: "10", M6_LEDGER: "3.00"}
    assert opened.settings(M6_LEDGER) == policies[M6_LEDGER]
    hold = decide(pool, 1500, policies, "26.01")
    assert hold.payload["older"] == [{"epoch": 1200, "release_at": 1620, "reserved": "13.00"}]
    assert hold.strategy_cash(M6_LEDGER, D("26.01"), 119.999) == D("3.01")
    assert hold.strategy_cash(M6_LEDGER, D("26.01"), 120) == D("16.01")
    with closing(pool.connect()) as db:
        for now in (1619, 1620, 1621):
            pool.release("offline", now)
            value = db.execute("SELECT released_at FROM market_openings WHERE epoch=1200").fetchone()[0]
            assert value == (1620 if now >= 1620 else None)
    assert decide(pool, 1800, policies, 100, elapsed=15).payload["status"] == "OPEN"
    assert decide(pool, 2100, policies, 100, elapsed=239.999).payload["status"] == "OPEN"
    assert decide(pool, 2400, policies, 100, elapsed=240).payload["reason"] == "TAKER_POOL_T240_DEADLINE"
    path = project / "config/m4-live/strategies.toml"
    path.write_text('enabled = ["M4"]\n')
    assert opened.entry_block(M6_LEDGER) == "TAKER_STRATEGY_REMOVED"
    path.write_text('enabled = ["M4", "M6"]\n')
    assert opened.entry_block(M6_LEDGER) == "TAKER_STRATEGY_REMOVED"
    assert opened.entry_block(M4_LEDGER) is None


async def m6_fixture(project, monkeypatch, primary, e, parent_feed, *, kill):
    cfg = settings(project, M6_LEDGER)
    shared = SharedTakerFeed(e.meta, RDPObiConfig(), monotonic=parent_feed.monotonic,
                            wall=parent_feed.wall, m6_settings=cfg)
    shared.connect()
    feed = shared.m6
    path = project / "m6-ledgers" / f"{e.slug}.sqlite3"
    identity = {"strategy": M6_LEDGER, "mode": "LIVE", "funder": e.gateway.funder,
                "market_id": e.meta.condition_id, "settings_hash": cfg.fingerprint}
    ledger = BudgetLedger(path, identity, settings=cfg, fees=primary.ledger.fees)
    sid = "m4_live_"+uuid4().hex
    ledger.initialize_session(sid, cfg.model_dump(mode="json"), asdict(e.meta))
    reads = StrategyReads(e.reads, feed, ledger, cfg)
    client = reads.gateway._client
    writer = AsyncRiskWriter(M4RiskJournal(project / "m6-journal", "0x"+"1"*40, session_id=sid))
    ex = Executor(None, e.slug, sid, reads, writer, ledger=ledger, control=e.control,
                  live=True, monotonic=parent_feed.monotonic)
    ex.meta, ex.official_time, ex.time_anchor = e.meta, e.official_time, e.time_anchor
    ex.stop_event = e.stop_event
    market = BudgetMarket(ex, cfg, ex.stop_event, account_owner=primary, display=lambda _: None)
    calls = []

    def wire(request):
        assert request.method == "POST" and request.url.path == "/orders"
        calls.extend(json.loads(request.content))
        oid = client._operation_value("attempt").expected_order_id
        if kill:
            return httpx.Response(200, json=[{"success": True, "orderID": oid,
                "errorMsg": "no orders found to match with FAK order. FAK orders are partially filled or killed if no match is found.",
                "status": "", "makingAmount": "", "takingAmount": "", "tradeIDs": [], "transactionsHashes": []}])
        return httpx.Response(200, json=[{"success": True, "orderID": oid}])

    transport = httpx.Client(transport=httpx.MockTransport(wire))
    monkeypatch.setattr(helpers, "_http_client", transport)
    now = ex.monotonic()
    candidate = {"candidate_id": "m6-signal", "token": "1", "outcome": "YES",
                 "anchor_price": ".10", "confirmed": now, "deadline": now+.75,
                 "primitive_ids": ["rf", "lw"], "root_id": "rf|lw", "generation": feed.generation}
    monkeypatch.setattr(feed.strategies, "candidates", lambda: [candidate])
    monkeypatch.setattr(feed.m1, "legal", lambda book, now: feed.connected)
    return market, ex, shared, calls, transport


def quote(shared, ask, tick=".01"):
    feed = shared.m6
    ask, tick = D(ask), D(tick)
    feed.books["1"] = replace(feed.books["1"], bids=((ask-tick, D(100)),), asks=((ask, D(100)),),
                              tick=tick, source_time=feed.wall(), received_mono=feed.monotonic(),
                              snapshot_generation=feed.generation)
    shared.verify_ticks({"1": tick})


def test_shared_public_frames_keep_frozen_m6_book(project):
    from m4_helpers import feed_fixture

    from polymaker.m6_baseline.signals import M6Feed

    base, _, frames = feed_fixture()
    kwargs = {"monotonic": base.monotonic, "wall": base.wall}
    standalone = M6Feed(base.meta, base.cfg, **kwargs)
    shared = SharedTakerFeed(base.meta, base.cfg, m6_settings=settings(project, M6_LEDGER), **kwargs)
    ticks = {"1": D(".01"), "2": D(".01")}
    for feed in (standalone, shared):
        feed.verify_ticks(ticks)
        feed.connect()
        feed.apply(frames)
    assert shared.m6.connected and shared.m6.m1.failure is None
    assert shared.m6.research.good == standalone.research.good == {"1": True, "2": True}
    assert shared.m6.books == standalone.books
    for feed in (standalone, shared):
        feed.invalidate("TEST_DISCONNECT")
        feed.connect()
    assert shared.m6.generation == standalone.generation == 2
    assert shared.m6.research.good == standalone.research.good


def test_shared_pending_receipts_returns_only_the_earliest_time(project):
    from m4_helpers import feed_fixture

    base, _, _ = feed_fixture()
    shared = SharedTakerFeed(base.meta, base.cfg, m6_settings=settings(project, M6_LEDGER))
    assert shared.m6.pending_receipts() == ()
    shared.m1_processing_received = 1.0
    assert shared.m6.pending_receipts() == (1.0,)
    shared.m1_pending_received.extend((2.0, 2.0, 3.0))
    assert shared.m6.pending_receipts() == (1.0,)
    shared.m1_processing_received = 2.0
    assert shared.m6.pending_receipts() == (2.0,)
    shared.m1_processing_received = 4.0
    assert shared.m6.pending_receipts() == (2.0,)


@pytest.mark.parametrize("include_m21", [False, True])
async def test_shared_reader_drains_burst_and_reconnects_with_fresh_books(project, monkeypatch, include_m21):
    from m4_helpers import feed_fixture

    base, _, books = feed_fixture()
    shared = SharedTakerFeed(base.meta, base.cfg, m6_settings=settings(project, M6_LEDGER),
                            m21_settings=settings(project, M21_LEDGER) if include_m21 else None,
                            monotonic=base.monotonic, wall=base.wall)
    shared.verify_ticks({"1": D(".01"), "2": D(".01")})
    burst = [books, *["PONG"] * 2200]
    processed, buffered = [], []
    ready = asyncio.Event()
    original_apply = shared.apply

    def apply(raw, **kwargs):
        buffered.append(len(shared.m1_pending_received))
        original_apply(raw, **kwargs)
        processed.append(raw)
        if len(processed) == len(burst):
            assert shared.snapshot().valid and all(child.snapshot().valid for child in shared.secondary.values())
            ready.set()

    class Socket:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def send(self, _):
            pass

        async def __aiter__(self):
            for raw in burst:
                yield raw
            await ready.wait()

    monkeypatch.setattr(shared, "apply", apply)
    monkeypatch.setattr("polymaker.rdp_sm_marketdata.websockets.connect", lambda *_a, **_kw: Socket())
    for generation in (1, 2):
        await asyncio.wait_for(shared._connection(), 3)
        assert shared.generation == generation
        assert all(child.generation == generation for child in shared.secondary.values())
        assert processed == burst
        assert max(buffered) == shared.reader_backpressure_levels[0]
        assert not shared.connected and not shared.m1_pending_received
        assert all(not child.connected for child in shared.secondary.values())
        processed.clear()
        buffered.clear()
        ready.clear()


@pytest.mark.parametrize("stale_tokens", [("1", "2"), ("1",), ("2",)])
def test_shared_m6_waits_for_fresh_books_after_stale_first_frame(project, stale_tokens):
    from m4_helpers import feed_fixture

    base, clock, frames = feed_fixture()
    shared = SharedTakerFeed(base.meta, base.cfg, m6_settings=settings(project, M6_LEDGER),
                            monotonic=base.monotonic, wall=base.wall)
    shared.verify_ticks({"1": D(".01"), "2": D(".01")})
    shared.connect()
    shared.apply([
        {**frame, "timestamp": str(int(shared.wall()*1000) - 1000)}
        if frame["asset_id"] in stale_tokens else frame
        for frame in frames
    ])
    for token in stale_tokens:
        assert shared.m6.research.reasons[token] == "EVENT_SOURCE_AGE"
    waiting = shared.m6.snapshot()
    assert not waiting.valid and waiting.reason == "WS_REBUILD_REQUIRED"
    shared.m6.m1.evaluate(waiting)
    assert not shared.m6.m1.failure and shared.m6.m1.selected is None

    clock.now = .025
    shared.apply([{**frame, "timestamp": str(round(shared.wall()*1000))} for frame in frames])
    assert shared.m6.snapshot().valid and shared.m6.research.good == {"1": True, "2": True}
    shared.m6.m1.evaluate(shared.m6.snapshot())
    assert shared.m6.connected and not shared.m6.m1.failure


@pytest.mark.parametrize("kill", [True, False])
async def test_m6_inside_window_retry_or_partial_has_no_topup(project, monkeypatch, kill):
    m4, parent, parent_feed, clock, primary_posts, primary_transport = await fixture(project, monkeypatch)
    primary_signal = next(iter(m4.pending.values()))
    assert primary_signal.token_id == "1" and primary_signal.outcome == "YES"
    await m4.submit(primary_signal)
    primary_order = m4.ledger.attempts()[0]["order_id"]
    m6, e, feed, calls, transport = await m6_fixture(project, monkeypatch, m4, parent, parent_feed, kill=kill)
    queries = None
    try:
        quote(feed, ".20")
        m6.collect()
        ep = m6.pending["m6-signal"]
        assert ep.taker_strategy.ledger == M6_LEDGER
        await m6.submit(ep)
        assert calls == [] and not m6.ledger.attempts()
        quote(feed, ".187", ".001")
        await m6.submit(ep)
        assert calls == [] and m6.ledger.get("m6_execution_wait:m6-signal")["reason"] == "M6_NO_LEGAL_FAK_QUANTITY"
        quote(feed, ".19")
        await m6.submit(ep)
        assert len(calls) == 1
        a = m6.ledger.attempts()[0]
        order_id = a["order_id"]
        assert D(json.loads(a["plan"])["commitment_usdc"]) <= D("1.50")
        assert not m4.ledger.owns_order(order_id) and m6.ledger.owns_order(order_id)
        assert m4.ledger.owns_order(primary_order) and not m6.ledger.owns_order(primary_order)
        assert len(primary_posts) == 1
        if not kill:
            order, trade = evidence(m6, e, order_id, "2", status="MINED")
            queries, _ = serve(monkeypatch, transport, order, trade)
            await m6.reconcile(order_id)
            assert m6.ledger.attempt(order_id)["state"] == "SETTLING"
            await m6.submit(ep)
            trade["status"] = "CONFIRMED"
            await m6.reconcile(order_id)
            await m6.submit(ep)
            assert len(calls) == 1
            assert m6.ledger.row(ep.episode_id)["reason"] == "M6_CONFIRMED_FILL_NO_TOP_UP"
        else:
            quote(feed, ".18")
            await m6.submit(ep)
            assert len(calls) == 2 and m6.ledger.attempts()[1]["order_id"] != order_id
        m6.ledger.validate_recovery()
        clock.now = ep.order_deadline
        await m6.submit(ep)
        assert len(calls) == (2 if kill else 1)
    finally:
        await close(m6, e)
        await close(m4, parent)
        transport.close()
        primary_transport.close()
        if queries:
            queries.close()


async def test_opening_precedes_ledger_and_survives_changed_configuration(project, monkeypatch):
    from test_m4_live_startup import harness

    h = harness(project, monkeypatch)
    prepared = await h.startup.prepare(M4LiveSettings.load(project), h.reads_type, None)

    async def cash(*_, raise_on_error):
        assert raise_on_error is True
        return {"official_balance_usd": "40", "applicable_allowance_usd": "40", "source": "OFFLINE"}

    monkeypatch.setattr("polymaker.taker_runner.account_snapshot", cash)
    opened, account, _ = await prepare_opening(project, h.startup.root, prepared, h.startup)
    assert opened.payload["status"] == "OPEN" and account["source"] == "OFFLINE"
    assert not (h.startup.root / "ledgers").exists()
    (project / "config/m4-live/strategies.toml").write_text('enabled = ["M4"]\n')

    async def unexpected(*_):
        pytest.fail("Original opening must not re-read capital or reallocate")

    monkeypatch.setattr("polymaker.taker_runner.account_snapshot", unexpected)
    restored, account, _ = await prepare_opening(project, h.startup.root, prepared, h.startup)
    assert restored.payload == opened.payload and account is None
    assert restored.entry_block(M6_LEDGER) == "TAKER_STRATEGY_REMOVED"
    prepared.reads.close()


async def test_late_opening_recovers_balance_and_clock_without_signal_history(project, monkeypatch):
    from test_m4_live_startup import EPOCH, harness

    h = harness(project, monkeypatch)
    h.clock_values[:] = [EPOCH+1, EPOCH+14]
    prepared = await h.startup.prepare(M4LiveSettings.load(project), h.reads_type, None)
    assert prepared.startup["late_new_market_start"]
    reads, clocks = [], []

    async def balance_io(_):
        reads.append("balance")
        assert not (h.startup.root/"ledgers").exists()
        assert not (h.startup.root/"m6-ledgers").exists()
        if len(reads) == 1:
            raise PolyApiException(error_msg="Request exception!")
        return {"balance": "40000000", "allowances": {"exchange": "40000000"}}

    async def spenders(_, *, raise_on_error):
        assert raise_on_error
        return ("exchange",)

    real_clock = h.startup.api.clock
    h.clock_values[:] = [EPOCH+22]

    async def clock():
        clocks.append("clock")
        if len(clocks) == 1:
            raise httpx.ConnectTimeout("synthetic clock timeout")
        return await real_clock()

    gateway = prepared.reads.gateway
    gateway._client = object()
    gateway._io = balance_io
    gateway.balance_allowance = MethodType(ExecutionGateway.balance_allowance, gateway)
    gateway.required_collateral_spenders = spenders
    monkeypatch.setattr(h.startup.api, "clock", clock)
    opened, account, began = await prepare_opening(project, h.startup.root, prepared, h.startup)
    assert len(reads) == len(clocks) == 2
    assert h.pauses == [.5, 1.]
    assert account["read_started_monotonic"] == began
    assert prepared.clock[0] == EPOCH+22
    assert opened.payload["status"] == "OPEN"
    assert opened.payload["policy"] == POLICY
    assert opened.payload["opening_read_retries"] == 2
    assert opened.payload["reserved"] == {M4_LEDGER: "10", M6_LEDGER: "3.00"}
    assert 23 <= opened.payload["official_upper"]-EPOCH < 240
    assert h.events[-1]["pool_status"] == "OPEN"
    assert account["official_balance_usd"] == account["applicable_allowance_usd"] == "40"
    prepared.reads.close()


@pytest.mark.parametrize("cutoff", [False, True])
async def test_opening_retry_ends_at_verified_insufficiency_or_t240(project, monkeypatch, cutoff):
    from test_m4_live_startup import EPOCH, harness

    h = harness(project, monkeypatch)
    h.clock_values[:] = [EPOCH+1, EPOCH+14, EPOCH+(239 if cutoff else 22)]
    prepared = await h.startup.prepare(M4LiveSettings.load(project), h.reads_type, None)
    reads = []

    async def cash(*_, raise_on_error):
        assert raise_on_error is True
        reads.append("balance")
        if len(reads) == 1:
            raise TimeoutError
        return {"official_balance_usd": "60", "applicable_allowance_usd": "13"}

    monkeypatch.setattr("polymaker.taker_runner.account_snapshot", cash)
    opening, account, _ = await prepare_opening(project, h.startup.root, prepared, h.startup)
    assert opening.payload["status"] == "SKIPPED" and opening.payload["reserved"] == {}
    assert opening.payload["reason"] == ("TAKER_POOL_T240_DEADLINE" if cutoff else "TAKER_POOL_INSUFFICIENT_FUNDS")
    assert len(reads) == (1 if cutoff else 2)
    assert account is None
    if not cutoff:
        assert opening.payload["available"] == "13"

    async def unexpected(*_):
        pytest.fail("Terminal skips cannot be reopened after a restart")

    monkeypatch.setattr("polymaker.taker_runner.account_snapshot", unexpected)
    restored, account, _ = await prepare_opening(project, h.startup.root, prepared, h.startup)
    assert restored.payload == opening.payload and account is None
    prepared.reads.close()


def test_pool_stale_snapshot_defers_decision_without_reserving(project):
    policies = {key: settings(project, key) for key in (M4_LEDGER, M6_LEDGER)}
    pool = TakerPool(project/"pool.sqlite3", project)
    output = pool.decide("offline", 900, "condition-900", policies,
                         {"official_balance_usd": "60", "applicable_allowance_usd": "60"},
                         clock=lambda: (920, 921), account_valid_until=0)
    assert output is None and pool.existing("offline", 900) is None
    assert decide(pool, 900, policies, 60, elapsed=22).payload["status"] == "OPEN"


async def test_future_market_cannot_read_capital_or_commit_reservation(project, monkeypatch):
    from test_m4_live_startup import EPOCH, FUNDER, harness

    h = harness(project, monkeypatch)
    prepared = await h.startup.prepare(M4LiveSettings.load(project), h.reads_type, None)
    epoch = EPOCH+300
    slug = f"btc-updown-5m-{epoch}"
    prepared = replace(prepared, slug=slug, meta=replace(prepared.meta, slug=slug))

    async def unexpected(*_, **__):
        pytest.fail("Cannot read admission capital before the target T0")

    monkeypatch.setattr("polymaker.taker_runner.account_snapshot", unexpected)
    with pytest.raises(ValueError, match="TAKER_POOL_BEFORE_T0"):
        await prepare_opening(project, h.startup.root, prepared, h.startup)
    pool = TakerPool(h.startup.root/"taker-pool.sqlite3", project)
    policies = {key: settings(project, key) for key in (M4_LEDGER, M6_LEDGER)}
    with pytest.raises(ValueError, match="TAKER_POOL_BEFORE_T0"):
        pool.decide(FUNDER, epoch, prepared.meta.condition_id, policies,
                    {"official_balance_usd": "60", "applicable_allowance_usd": "60"},
                    clock=lambda: (epoch-.01, epoch+1))
    assert pool.existing(FUNDER, epoch) is None
    prepared.reads.close()


async def test_legacy_t15_skip_is_restored_without_capital_read(project, monkeypatch):
    from test_m4_live_startup import EPOCH, FUNDER, harness

    h = harness(project, monkeypatch)
    prepared = await h.startup.prepare(M4LiveSettings.load(project), h.reads_type, None)
    pool = TakerPool(h.startup.root/"taker-pool.sqlite3", project)
    policy = {key: settings(project, key) for key in (M4_LEDGER, M6_LEDGER)}
    old = pool.decide(FUNDER, EPOCH, prepared.meta.condition_id, policy, None,
                      clock=lambda: (EPOCH+14, EPOCH+15)).payload
    old.pop("entry_cutoff_sec")
    old.pop("opening_read_retries")
    old.update(policy="SHARED_TAKER_POOL_T15_CLOSE_PLUS_120_V1", reason="TAKER_POOL_T15_DEADLINE")
    with closing(pool.connect()) as db, db:
        db.execute("UPDATE market_openings SET payload=?", (json.dumps(old),))

    async def unexpected(*_, **__):
        pytest.fail("A saved opening is immutable even under the new pool policy")

    monkeypatch.setattr("polymaker.taker_runner.account_snapshot", unexpected)
    restored, account, _ = await prepare_opening(project, h.startup.root, prepared, h.startup)
    assert restored.payload == old and account is None
    assert restored.entry_block(M4_LEDGER) == "TAKER_POOL_T15_DEADLINE"
    assert restored.entry_block(M6_LEDGER) == "TAKER_POOL_T15_DEADLINE"
    prepared.reads.close()


async def test_stop_during_pool_retry_never_reserves(project, monkeypatch):
    from test_m4_live_startup import EPOCH, FUNDER, harness

    from polymaker.m4_live_startup import PreparationStopped

    h = harness(project, monkeypatch)
    prepared = await h.startup.prepare(M4LiveSettings.load(project), h.reads_type, None)
    runs = []

    async def cash(*_, raise_on_error):
        assert raise_on_error
        runs.append(True)
        raise TimeoutError

    async def pause(_):
        h.startup.stop.set()

    monkeypatch.setattr("polymaker.taker_runner.account_snapshot", cash)
    monkeypatch.setattr(h.startup, "pause", pause)
    with pytest.raises(PreparationStopped):
        await prepare_opening(project, h.startup.root, prepared, h.startup)
    assert runs == [True]
    assert TakerPool(h.startup.root/"taker-pool.sqlite3", project).existing(FUNDER, EPOCH) is None
    prepared.reads.close()


async def test_m6_deadline_at_native_boundary_proves_no_send(project, monkeypatch):
    m4, parent, parent_feed, clock, _, primary_transport = await fixture(project, monkeypatch)
    m6, e, feed, calls, transport = await m6_fixture(project, monkeypatch, m4, parent, parent_feed, kill=True)
    try:
        quote(feed, ".19")
        m6.collect()
        ep = m6.pending["m6-signal"]
        native_guard = m6.native_guard

        def expire_at_transport(attempt):
            clock.now = ep.order_deadline
            return native_guard(attempt)

        monkeypatch.setattr(e.gateway._client, "m4_native_guard", expire_at_transport)
        await m6.submit(ep)
        assert calls == []
        [attempt] = m6.ledger.attempts()
        assert attempt["state"] == "UNSENT"
        assert json.loads(attempt["proof"])["reason"] == "M6_SIGNAL_DEADLINE"
        assert m6.ledger.row(ep.episode_id)["reason"] == "M6_SIGNAL_DEADLINE"
        m6.ledger.validate_recovery()
    finally:
        await close(m6, e)
        await close(m4, parent)
        transport.close()
        primary_transport.close()


async def test_secondary_runner_uses_separate_owned_audit(project, monkeypatch):
    from test_m4_taker_watch import watch

    from polymaker.taker_runner import run_secondary

    m4, parent, parent_feed, _, calls, transport = await fixture(project, monkeypatch)
    cfg = settings(project, M6_LEDGER)
    root = project / "research_outputs/m4_live"
    root.mkdir(parents=True)
    pool = TakerPool(root / "taker-pool.sqlite3", project)
    epoch = int(parent.slug.rsplit("-", 1)[1])
    funder = parent.gateway.funder
    opening = pool.decide(funder, epoch, parent.meta.condition_id, {M6_LEDGER: cfg},
                          {"official_balance_usd": "40", "applicable_allowance_usd": "40"},
                          clock=lambda: (epoch+1, epoch+2))
    shared = SharedTakerFeed(parent.meta, RDPObiConfig(), m6_settings=cfg,
                            monotonic=parent_feed.monotonic, wall=parent_feed.wall)

    async def run(market):
        assert market.account_owner is m4 and market.e.gateway._pool is parent.gateway._pool
        assert market.e.gateway._client.known_ids is not parent.gateway._client.known_ids
        assert market.opening.entry_block(M6_LEDGER) is None
        market.complete_window()
        await market.e.journal.flush()

    monkeypatch.setattr(BudgetMarket, "run", run)
    try:
        await run_secondary(m4, opening, shared.m6, root, Executor, parent.control, asyncio.Event())
        report = watch.inspect_market(project, epoch, epoch+320, strategy="M6")
        assert report["record_status"] == "AVAILABLE" and report["complete"]
        assert report["settings"]["taker_strategy"]["ledger"] == M6_LEDGER
        assert not report["alerts"] and calls == []
    finally:
        await close(m4, parent)
        transport.close()
