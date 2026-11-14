"""Public-only execution acceptance, durable accounting and recovery boundaries."""

# ruff: noqa: ASYNC240 -- Isolated local test files.

import asyncio
import json
import os
import time
from dataclasses import asdict, replace
from decimal import Decimal as D
from types import SimpleNamespace

import httpx
import pytest
from typer.testing import CliRunner

from polymaker.cli import app
from polymaker.m1_live_config import M1LiveConfig
from polymaker.m1_live_signal import M1LiveSignals
from polymaker.m1_virtual import VirtualPolicy, VirtualService, run_virtual, write_report
from polymaker.m1_virtual_api import ReadOnlyTransport, VirtualPublicAPI
from polymaker.m1_virtual_state import VirtualStore
from test_rdp_sm_m1_strategy import m1_feed, seed_opportunity

EPOCH = 1793650500


def meta(epoch=EPOCH):
    return {"slug": f"btc-updown-5m-{epoch}", "condition_id": "0x"+"a"*64,
            "tokens": [{"token_id": "1", "outcome": "Up"}, {"token_id": "2", "outcome": "Down"}]}


@pytest.mark.parametrize("initially_enabled", [True, False])
def test_gc_window_defers_collection_and_restores_original_setting(monkeypatch, initially_enabled):
    from polymaker.m1_virtual import GCDiagnostics, GCWindow

    state = {"enabled": initially_enabled}
    collections = []
    diagnostics = GCDiagnostics()
    monkeypatch.setattr("polymaker.m1_virtual.gc.isenabled", lambda: state["enabled"])
    monkeypatch.setattr("polymaker.m1_virtual.gc.disable", lambda: state.update(enabled=False))
    monkeypatch.setattr("polymaker.m1_virtual.gc.enable", lambda: state.update(enabled=True))
    monkeypatch.setattr("polymaker.m1_virtual.gc.collect", lambda: collections.append(diagnostics.during_entry))
    window = GCWindow(diagnostics)
    assert not state["enabled"] and diagnostics.during_entry
    assert collections == [False]
    window.release()
    window.release()
    assert state["enabled"] == initially_enabled and not diagnostics.during_entry
    assert collections == ([False, False] if initially_enabled else [False])


def command(store, index=1, *, epoch=EPOCH, outcome="YES", price=".05", quantity="20"):
    return store.issue({"slug": meta(epoch)["slug"], "episode_id": f"op-{epoch}-{index}",
        "rf_id": f"rf-{epoch}-{index}", "ex_id": f"ex-{epoch}-{index}", "outcome": outcome},
        price=price, quantity=quantity, eligible=True, reason=None, elapsed=30)


def closed(store, epoch=EPOCH, *, settle=True, full=True):
    slug = meta(epoch)["slug"]
    store.add_market(meta(epoch), 1)
    store.finish_market(slug, full_window=full, counts={})
    if settle:
        store.settle(slug, {"winner": "YES"})
    return slug


@pytest.mark.parametrize("method,url", [
    ("POST", "https://clob.polymarket.com/order"),
    ("DELETE", "https://clob.polymarket.com/order"),
    ("GET", "https://example.com/time"),
    ("GET", "http://clob.polymarket.com/time"),
])
async def test_write_and_unapproved_host_blocked_before_network(method, url):
    called = []
    transport = ReadOnlyTransport(httpx.MockTransport(lambda r: called.append(r)))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(RuntimeError, match="NETWORK_WRITE_OR_HOST_DENIED"):
            await client.request(method, url)
    assert called == []


async def test_public_clock_and_official_route_failover():
    calls, incidents = [], []
    slug = meta()["slug"]

    def respond(request):
        calls.append(str(request.url))
        if request.url.path == "/time":
            return httpx.Response(200, json=EPOCH)
        if request.url.path.startswith("/markets/slug/"):
            return httpx.Response(400, text="<html>cloudflare</html>")
        return httpx.Response(200, json=[{"slug": slug}])

    api = VirtualPublicAPI(transport=httpx.MockTransport(respond),
                           on_failover=lambda *args: incidents.append(args))
    try:
        assert (await api.clock())[0] == EPOCH
        assert await api.raw_market(slug) == {"slug": slug}
        assert incidents == [("OFFICIAL_ROUTE_FAILOVER", "HTTPStatusError", {"slug": slug, "purpose": "observation"})]
        assert all(url.startswith(("https://clob.polymarket.com/", "https://gamma-api.polymarket.com/")) for url in calls)
    finally:
        await api.close()


async def test_resolution_validates_token_identity_and_finality():
    slug = meta()["slug"]
    raw = {"slug": slug, "conditionId": meta()["condition_id"], "clobTokenIds": '["2","1"]',
           "closed": False, "outcomePrices": '["0","1"]', "umaResolutionStatus": "resolved"}
    api = VirtualPublicAPI(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=raw)))
    expected = {"slug": slug, "condition_id": meta()["condition_id"], "token_ids": {"YES": "1", "NO": "2"}}
    try:
        assert await api.resolution(expected) is None
        raw["closed"] = True
        assert (await api.resolution(expected))["winner"] == "YES"
        raw["clobTokenIds"] = '["3","1"]'
        with pytest.raises(ValueError, match="TOKEN_IDENTITY"):
            await api.resolution(expected)
    finally:
        await api.close()


async def test_closed_market_uses_exact_slug_route_without_false_failover():
    calls, incidents = [], []
    expected = {"slug": meta()["slug"], "condition_id": meta()["condition_id"], "token_ids": {"YES": "1", "NO": "2"}}
    raw = {"slug": meta()["slug"], "conditionId": meta()["condition_id"], "clobTokenIds": '["1","2"]',
           "closed": True, "outcomePrices": '["1","0"]', "umaResolutionStatus": "resolved"}

    def response(request):
        calls.append(request.url.path)
        return httpx.Response(200, json=raw if "/slug/" in request.url.path else [])

    api = VirtualPublicAPI(transport=httpx.MockTransport(response), on_failover=lambda *args: incidents.append(args))
    try:
        assert (await api.resolution(expected))["winner"] == "YES"
        assert calls == [f"/markets/slug/{meta()['slug']}"]
        assert incidents == []
    finally:
        await api.close()


def test_old_settlement_route_retry_does_not_reset_current_observation(tmp_path):
    service = VirtualService(M1LiveConfig(), tmp_path, display=lambda *args: None,
                             api_factory=lambda **kwargs: SimpleNamespace())
    old = closed(service.store)
    current = meta(EPOCH+300)
    service.store.add_market(current, 0)
    service.current_slug = current["slug"]
    service.failover("OFFICIAL_ROUTE_FAILOVER", "HTTPStatusError", {"slug": old, "purpose": "settlement"})
    report = service.store.report()
    assert report["consecutive_complete_reports"] == 1
    assert all(m["issues"] == 0 for m in report["markets"])
    assert not report["markets"][0]["timeline"][-1]["data"]["streak_reset"]
    service.store.close()


def test_observation_route_failure_still_resets_its_own_epoch(tmp_path):
    service = VirtualService(M1LiveConfig(), tmp_path, display=lambda *args: None,
                             api_factory=lambda **kwargs: SimpleNamespace())
    closed(service.store)
    target = meta(EPOCH+300)["slug"]
    service.failover("OFFICIAL_ROUTE_FAILOVER", "HTTPStatusError", {"slug": target, "purpose": "observation"})
    assert service.store.get("reset_through_epoch") == EPOCH+300
    assert service.store.report()["consecutive_complete_reports"] == 0
    service.store.close()


def test_durable_dedup_budget_and_full_fill_settlement(tmp_path):
    store = VirtualStore(tmp_path)
    store.add_market(meta(), 0)
    assert command(store)["status"] == "VIRTUAL_SUCCESS"
    assert command(store) is None
    store.close()
    store = VirtualStore(tmp_path)
    assert command(store) is None
    command(store, 2, outcome="NO")
    store.issue({"slug": meta()["slug"], "episode_id": "skipped", "rf_id": "skipped-rf",
        "ex_id": "skipped-ex", "outcome": "YES"}, price=".8", quantity=0,
        eligible=False, reason="PRICE_OUTSIDE_TEST_BAND", elapsed=30)
    for i in range(3, 6):
        command(store, i)
    assert command(store, 6)["reason"] == "SESSION_INSTRUCTION_LIMIT"
    store.close()
    store = VirtualStore(tmp_path)
    assert command(store, 7)["reason"] == "SESSION_INSTRUCTION_LIMIT"
    assert sum(r["status"] == "VIRTUAL_SUCCESS" for r in store.instructions(meta()["slug"])) == 5
    store.finish_market(meta()["slug"], full_window=True, counts={})
    assert store.report()["settled_pnl"] == "0"  # Aggregate has no resolved positions.
    assert store.report()["markets"][0]["pnl"] is None
    store.settle(meta()["slug"], {"winner": "YES"})
    report = store.report()
    assert D(report["settled_pnl"]) == D(4*19-1)
    assert D(report["markets"][0]["instructions"][1]["pnl"]) == -1
    assert report["real_posts"] == 0 and not report["stage_complete"]
    write_report(tmp_path, report)
    assert "VIRTUAL_OPEN_INSTRUCTION" in (tmp_path/"report.md").read_text()
    assert "OFFICIAL_RESOLUTION" in (tmp_path/"report.md").read_text()
    assert json.loads((tmp_path/"latest.json").read_text())["real_posts"] == 0
    store.close()


def test_twelve_consecutive_reports_requires_official_resolution_and_user_acceptance(tmp_path):
    store = VirtualStore(tmp_path)
    for i in range(12):
        closed(store, EPOCH+i*300, settle=i != 11)
    report = store.report()
    assert report["continuous_normal_markets"] == 12
    assert report["consecutive_complete_reports"] == 11
    assert not report["awaiting_user_confirmation"]
    store.settle(meta(EPOCH+11*300)["slug"], {"winner": "NO"})
    report = store.report()
    assert report["consecutive_complete_reports"] == 12
    assert report["awaiting_user_confirmation"] and not report["stage_complete"]
    store.incident("TEST_FAULT", "reset", meta(EPOCH+11*300)["slug"])
    assert store.report()["consecutive_complete_reports"] == 0
    closed(store, EPOCH+12*300)
    assert store.report()["consecutive_complete_reports"] == 1
    store.close()


def test_partial_window_and_missing_market_break_streak(tmp_path):
    store = VirtualStore(tmp_path)
    closed(store, EPOCH, full=False)
    closed(store, EPOCH+300)
    assert store.report()["consecutive_complete_reports"] == 1
    closed(store, EPOCH+900)
    assert store.report()["consecutive_complete_reports"] == 1
    store.close()


async def test_report_worker_reads_consistent_durable_state_and_diagnostics_are_bounded(tmp_path):
    from polymaker.m1_virtual import refresh_report

    store = VirtualStore(tmp_path)
    slug = closed(store)
    store.observation_batch([("INPUT_REJECTED", {"reason": "test"}, slug, EPOCH+1)],
                            [("DATA_INTEGRITY_RESET", "test", slug, EPOCH+1)])
    metrics = store.report(details=False)
    assert metrics["markets"] == [] and metrics["consecutive_complete_reports"] == 0
    await asyncio.to_thread(refresh_report, tmp_path, {"mode": "VIRTUAL_ONLY"})
    report = json.loads((tmp_path/"latest.json").read_text())
    assert report["markets"][0]["issues"] == 1
    assert not any(e["kind"] == "INPUT_REJECTED" for e in report["markets"][0]["timeline"])
    assert store.db.execute("SELECT count(*) FROM timeline WHERE kind='INPUT_REJECTED'").fetchone()[0] == 1
    store.close()


async def test_reporting_has_an_independent_process_and_python_interpreter(tmp_path):
    service = VirtualService(M1LiveConfig(), tmp_path, display=lambda *args: None,
                             api_factory=lambda **kwargs: SimpleNamespace())
    closed(service.store)
    service.publish()
    await service.report_task
    worker_pid = await asyncio.get_running_loop().run_in_executor(service.report_pool, os.getpid)
    assert worker_pid != os.getpid()
    assert json.loads((tmp_path/"latest.json").read_text())["markets"][0]["pnl"] == "0"
    await service.close_reporter()
    service.store.close()


def test_restart_preserves_instruction_marks_interruption_and_resets_deployment(tmp_path, monkeypatch):
    monkeypatch.setattr("polymaker.m1_virtual_state.time.time", lambda: EPOCH+30)
    store = VirtualStore(tmp_path)
    store.boot("a")
    closed(store, EPOCH-300)
    store.add_market(meta(), 0)
    command(store)
    store.close()
    store = VirtualStore(tmp_path)
    store.boot("a")
    report = store.report()
    assert report["consecutive_complete_reports"] == 0
    assert report["markets"][-1]["state"] == "INTERRUPTED"
    assert len(report["markets"][-1]["instructions"]) == 1
    closed(store, EPOCH+300)
    assert store.report()["consecutive_complete_reports"] == 1
    monkeypatch.setattr("polymaker.m1_virtual_state.time.time", lambda: EPOCH+330)
    store.boot("b")
    assert store.report()["consecutive_complete_reports"] == 0
    store.close()


@pytest.mark.parametrize("price,quantity", [(".01", "100.0"), (".03", "33.4"),
                                           (".15", "6.7"), (".19", "5.3")])
async def test_public_opportunity_creates_one_dollar_maker_instruction(tmp_path, price, quantity):
    feed, _ = m1_feed()
    feed.meta = replace(feed.meta, slug=meta()["slug"])
    feed.m1 = M1LiveSignals(feed)
    feed.books["1"] = replace(feed.books["1"], bids=((D(price), D(100)),),
                               asks=((D(price)+D(".01"), D(100)),), reported_bid=None, reported_ask=None)
    store = VirtualStore(tmp_path)
    store.add_market(asdict(feed.meta), 0)
    events = []
    policy = VirtualPolicy(feed, M1LiveConfig(), store, lambda *args: events.append(args))
    seed_opportunity(feed)
    await policy.poll(15, 16)
    await policy.market.drain()
    await policy.poll(15, 16)
    await policy.market.drain()
    policy.flush()
    row = store.instructions(feed.meta.slug)[0]
    assert row["status"] == "VIRTUAL_SUCCESS"
    assert D(row["quantity"]) == D(quantity)
    assert D(1) <= D(row["cost"]) < D(1)+D(price)*D(".1")
    assert D(row["quantity"]).as_tuple().exponent == -1
    assert len(events) == 1 and events[0][0] == "VIRTUAL_POST"
    assert not feed.m1.own_active  # No invented virtual liquidity in public book.
    await policy.close()
    store.close()


def test_baseline_rounding_all_test_prices_and_minimum():
    from polymaker.rdp_sm_m1_policy import baseline_quantity

    for tick in range(1, 20):
        price = D(tick)/100
        shares = baseline_quantity(price, 5)
        assert shares >= 5 and shares.as_tuple().exponent == -1
        assert shares*price >= 1
        assert (shares-D(".1"))*price < 1
    with pytest.raises(ValueError, match="AMOUNT_NOT_LEGAL"):
        baseline_quantity(".19", 6)
    for price in ("0", "1", "NaN", "Infinity"):
        with pytest.raises(ValueError, match="PRICE_INVALID"):
            baseline_quantity(price, 5)


def test_baseline_rejects_underfunded_or_oversized_quantity(tmp_path):
    store = VirtualStore(tmp_path)
    store.add_market(meta(), 0)
    for index, quantity in enumerate(("5.26", "5.2", "5.4", "100"), 1):
        with pytest.raises(ValueError, match="EXCEEDS_CONTRACT"):
            command(store, index, price=".19", quantity=quantity)
    row = command(store, 5, price=".19", quantity="5.3")
    assert row["status"] == "VIRTUAL_SUCCESS" and D(row["cost"]) == D("1.007")
    store.finish_market(meta()["slug"], full_window=True, counts={})
    store.settle(meta()["slug"], {"winner": "NO"})
    assert D(store.report()["settled_pnl"]) == D("-1.007")
    store.close()


def test_baseline_report_settings_describe_target_and_count_limit():
    from polymaker.m1_virtual import baseline_settings

    settings = baseline_settings(M1LiveConfig())
    assert settings["max_successful_instructions"] == 10
    assert settings["max_opportunities"] == 5
    assert settings["max_opportunities_per_direction"] == 1
    assert settings["max_uncertain_retries_per_opportunity"] == 1
    assert settings["target_spend_usdc"] == "1" and settings["quantity_step"] == "0.1"
    assert settings["quantity_rounding"] == "CEILING"
    assert "order_budget_usdc" not in settings and "session_budget_usdc" not in settings


async def test_cli_is_virtual_and_cannot_load_account(monkeypatch, tmp_path):
    from polymaker.config import Config

    calls = []

    async def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return {"mode": "VIRTUAL_ONLY", "real_posts": 0}

    monkeypatch.setattr(Config, "load", lambda *a: pytest.fail("Private configuration loaded"))
    monkeypatch.setattr("polymaker.m1_virtual.run_virtual", fake_run)
    monkeypatch.setattr("polymaker.m4_control.legacy_entry_guard", lambda: None)
    # CliRunner owns its own asyncio.run, so run it outside the test loop.
    result = await asyncio.to_thread(CliRunner().invoke, app, ["m1-run", "--preflight-only"])
    assert result.exit_code == 0, result.output
    assert calls[0][1]["preflight_only"] is True
    result = CliRunner().invoke(app, ["m1-live-run"])
    assert result.exit_code != 0 and "REAL_POST_DISABLED" in result.output


@pytest.mark.parametrize("exception", [TimeoutError("timeout"),
    httpx.HTTPStatusError("cloudflare", request=httpx.Request("GET", "https://clob.polymarket.com/time"),
                         response=httpx.Response(400)),
    httpx.ConnectError("connection reset")])
async def test_public_discovery_rebuilds_connection_and_keeps_running(tmp_path, exception):
    instances, events = [], []
    stop = asyncio.Event()

    class API:
        def __init__(self, **kwargs):
            self.closed = False
            instances.append(self)

        async def clock(self):
            if len(instances) == 1:
                raise exception
            stop.set()
            return EPOCH+245, time.monotonic(), time.monotonic()

        async def close(self):
            self.closed = True

    service = VirtualService(M1LiveConfig(), tmp_path, api_factory=API,
                             stop=stop, display=lambda *args: events.append(args))

    async def fast_wait(seconds):
        await asyncio.sleep(0)

    service.wait = fast_wait
    await service.run()
    assert len(instances) == 2 and all(api.closed for api in instances)
    assert any(kind == "PUBLIC_READ_RECOVERY" for kind, _ in events)
    report = json.loads((tmp_path/"latest.json").read_text())
    assert report["real_posts"] == 0 and report["runtime"]["state"] == "STOPPED"


async def test_resolution_read_failure_is_retried_and_never_counted_as_zero(tmp_path):
    service = VirtualService(M1LiveConfig(), tmp_path, display=lambda *args: None,
                             api_factory=lambda **kwargs: SimpleNamespace(close=None))
    slug = closed(service.store, settle=False)

    async def error():
        raise httpx.ReadTimeout("timeout")

    service.resolve_slug, service.resolve_task = slug, asyncio.create_task(error())
    await asyncio.sleep(0)
    await service.resolutions(schedule=False)
    report = service.store.report()
    assert report["markets"][0]["pnl"] is None
    assert report["consecutive_complete_reports"] == 0
    assert service.resolve_retry[slug] > time.monotonic()
    if service.report_task:
        await service.report_task
    await service.close_reporter()
    service.store.close()


async def test_preflight_does_not_load_secrets(monkeypatch, tmp_path):
    from polymaker.config import Config

    monkeypatch.setattr(Config, "load", lambda *args: pytest.fail("Private config loaded"))

    class API:
        async def clock(self):
            return EPOCH, 0, 0

        async def close(self):
            pass

    monkeypatch.setattr("polymaker.m1_virtual.VirtualPublicAPI", API)
    report = await run_virtual(tmp_path, preflight_only=True)
    assert not report["real_post_enabled"] and not list(tmp_path.iterdir())


async def test_slow_public_clock_does_not_delay_signal_or_virtual_instruction(tmp_path):
    feed, _ = m1_feed()
    feed.meta = replace(feed.meta, slug=meta()["slug"])
    feed.books["1"] = replace(feed.books["1"], bids=((D(".05"), D(100)),),
                               asks=((D(".06"), D(100)),), reported_bid=None, reported_ask=None)
    stop, clock_started, clock_finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    events = []

    class API:
        async def ticks(self, tokens):
            return {t: feed.books[t].tick for t in tokens}

        async def clock(self):
            clock_started.set()
            await asyncio.Event().wait()  # A real network read can be slow.
            clock_finished.set()

        async def close(self):
            pass

    def emit(kind, data):
        events.append((kind, data))
        if kind == "VIRTUAL_POST":
            stop.set()

    async def run_feed():
        seed_opportunity(feed)
        await stop.wait()

    feed.run = run_feed
    service = VirtualService(M1LiveConfig(), tmp_path, stop=stop, display=emit,
        api_factory=lambda **kwargs: API(), feed_factory=lambda *a, **kw: feed)
    service.clock = (EPOCH+15, time.monotonic(), time.monotonic())
    service.clock_poll_sec = 0
    service.bounds = lambda epoch: (15., 16.)
    await asyncio.wait_for(service.market(feed.meta), 2)
    assert clock_started.is_set() and not clock_finished.is_set()
    assert any(kind == "VIRTUAL_POST" for kind, _ in events)
    assert len(service.store.instructions(feed.meta.slug)) == 1
    if service.report_task:
        await service.report_task
    await service.close_reporter()
    service.store.close()


@pytest.mark.parametrize("failure", [TimeoutError("clock timeout"), httpx.ConnectError("reset"),
    httpx.HTTPStatusError("cloudflare 400", request=httpx.Request("GET", "https://clob.polymarket.com/time"),
                         response=httpx.Response(400))])
async def test_market_background_read_failure_reconnects_and_next_signal_is_not_lost(tmp_path, failure):
    feed, _ = m1_feed()
    feed.meta = replace(feed.meta, slug=meta()["slug"])
    feed.books["1"] = replace(feed.books["1"], bids=((D(".05"), D(100)),),
                               asks=((D(".06"), D(100)),), reported_bid=None, reported_ask=None)
    stop, recovered = asyncio.Event(), asyncio.Event()
    clients, events = [], []

    class API:
        def __init__(self, **kwargs):
            self.closed = False
            clients.append(self)
            if len(clients) > 1:
                recovered.set()

        async def ticks(self, tokens):
            return {t: feed.books[t].tick for t in tokens}

        async def clock(self):
            if self is clients[0]:
                raise failure
            return EPOCH+15, time.monotonic(), time.monotonic()

        async def close(self):
            self.closed = True

    def emit(kind, data):
        events.append((kind, data))
        if kind == "VIRTUAL_POST":
            stop.set()

    async def run_feed():
        await recovered.wait()
        seed_opportunity(feed)
        await stop.wait()

    async def wait(seconds):
        await asyncio.sleep(0)

    feed.run = run_feed
    service = VirtualService(M1LiveConfig(), tmp_path, stop=stop, display=emit,
        api_factory=API, feed_factory=lambda *a, **kw: feed)
    service.wait = wait
    service.clock = (EPOCH+15, time.monotonic(), time.monotonic())
    service.clock_poll_sec = 0
    service.bounds = lambda epoch: (15., 16.)
    await asyncio.wait_for(service.market(feed.meta), 2)
    kinds = [kind for kind, _ in events]
    assert clients[0].closed and len(clients) == 2
    assert kinds.index("PUBLIC_READ_RECOVERY") < kinds.index("VIRTUAL_POST")
    assert service.store.report()["markets"][0]["issues"] >= 1
    assert service.store.report()["real_posts"] == 0
    if service.report_task:
        await service.report_task
    await service.close_reporter()
    await service.api.close()
    service.store.close()


def test_uncertain_retry_adds_instruction_without_creating_sixth_signal(tmp_path):
    store = VirtualStore(tmp_path)
    store.add_market(meta(), 0)
    try:
        roots = [command(store, i) for i in range(1, 6)]
        root = roots[0]
        original = next(r for r in store.instructions(meta()['slug']) if r['id'] == root['id'])
        evidence = {'slug': meta()['slug'], **json.loads(original['evidence'])}
        extra = store.issue(evidence, price='.1', quantity=10, eligible=True, reason=None,
                            elapsed=30, execution_id='extra-order', duplicate_of=root['id'])
        assert extra['status'] == 'VIRTUAL_SUCCESS'
        assert store.issue(evidence, price='.1', quantity=10, eligible=True, reason=None,
                           elapsed=30, execution_id='extra-order', duplicate_of=root['id']) is None
        with pytest.raises(ValueError, match='DUPLICATE_RETRY_NOT_AUTHORIZED'):
            store.issue(evidence, price='.1', quantity=10, eligible=True, reason=None,
                        elapsed=30, execution_id='second-extra-order', duplicate_of=root['id'])
        assert command(store, 6)['reason'] == 'SESSION_INSTRUCTION_LIMIT'
        assert sum(r['status'] == 'VIRTUAL_SUCCESS' for r in store.instructions(meta()['slug'])) == 6
        assert store.db.execute("SELECT count(*) FROM timeline WHERE kind='SIGNAL_IDENTIFIED'").fetchone()[0] == 6  # Five admitted, one skipped.
    finally:
        store.close()
