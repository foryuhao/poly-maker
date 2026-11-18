"""Shared cash and isolated M4/M6 execution, with mock native HTTP."""
import asyncio
import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import asdict, replace
from decimal import Decimal as D
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from m4_helpers import close
from py_clob_client_v2.http_helpers import helpers
from test_m4_live_budget import fixture
from test_m4_live_task import evidence, serve

from polymaker.config import RDPObiConfig
from polymaker.m4_journal import M4RiskJournal
from polymaker.m4_live_budget import BudgetLedger
from polymaker.m4_live_budget_market import BudgetMarket
from polymaker.m4_live_config import M4LiveSettings
from polymaker.m4_run import Executor
from polymaker.m4_types import M4_LEDGER, M6_LEDGER
from polymaker.rdp_sm_writer import AsyncRiskWriter
from polymaker.taker_pool import TakerPool
from polymaker.taker_runner import SharedTakerFeed, StrategyReads, strategy_settings

SOURCE = Path(__file__).resolve().parents[2]


@pytest.fixture
def project(tmp_path):
    for name in ("m4.toml", "m6.toml", "m4-live/execution.toml", "m4-live/strategies.toml"):
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
    assert decide(pool, 1800, policies, 100, elapsed=15).payload["reason"] == "TAKER_POOL_T15_DEADLINE"
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

    from polymaker.taker_runner import prepare_opening

    h = harness(project, monkeypatch)
    prepared = await h.startup.prepare(M4LiveSettings.load(project), h.reads_type, None)

    async def cash(*_):
        return {"official_balance_usd": "40", "applicable_allowance_usd": "40", "source": "OFFLINE"}

    monkeypatch.setattr("polymaker.taker_runner.account_snapshot", cash)
    opened, account, _ = await prepare_opening(project, h.startup.root, prepared, h.startup.control)
    assert opened.payload["status"] == "OPEN" and account["source"] == "OFFLINE"
    assert not (h.startup.root / "ledgers").exists()
    (project / "config/m4-live/strategies.toml").write_text('enabled = ["M4"]\n')

    async def unexpected(*_):
        pytest.fail("Original opening must not re-read capital or reallocate")

    monkeypatch.setattr("polymaker.taker_runner.account_snapshot", unexpected)
    restored, account, _ = await prepare_opening(project, h.startup.root, prepared, h.startup.control)
    assert restored.payload == opened.payload and account is None
    assert restored.entry_block(M6_LEDGER) == "TAKER_STRATEGY_REMOVED"
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
