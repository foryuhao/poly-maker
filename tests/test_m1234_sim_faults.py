"""Visible failures, recovery, public transport boundary and calibration coverage."""

import asyncio
import json
import threading
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from m1234_fixtures import fixture_events
from polymaker.m1_virtual_api import ReadOnlyTransport
from polymaker.m1234_sim.calibration import assess, load_records
from polymaker.m1234_sim.events import Clock, Recorder, read_events
from polymaker.m1234_sim.policy import SimGateway
from polymaker.m1234_sim.runner import LiveRun
from polymaker.m1234_sim.signals import SignalView
from test_m1234_sim_core import broker, intent, sell
from test_m1234_sim_engine import policy_setup


def test_record_buffer_overflow_keeps_explicit_missing_interval(tmp_path):
    ready = threading.Event()
    release = threading.Event()
    import gzip

    def blocked(*args, **kwargs):
        ready.set()
        release.wait(5)
        return gzip.open(*args, **kwargs)

    r = Recorder(tmp_path / "overflow.gz", capacity=1, opener=blocked)
    ready.wait(5)
    try:
        r.append("FRAME", Clock(), raw="first")
        with pytest.raises(RuntimeError, match="OVERFLOW"):
            r.append("FRAME", Clock(), raw="missing")
        gap = json.loads((tmp_path / "overflow.failure.json").read_text())
        assert gap["first_missing_seq"] == 2
    finally:
        release.set()
        with pytest.raises(RuntimeError, match="OVERFLOW"):
            r.close()


@pytest.mark.parametrize("resource_fault", ["disk", "memory"])
def test_capacity_is_enforced_without_deleting_files(tmp_path, monkeypatch, resource_fault):
    run = LiveRun.__new__(LiveRun)
    run.directory, run.last_resource, run.resources, run.recorders = tmp_path, 0, [], {}
    run.cfg = {"max_output_mib": 4096, "min_free_disk_mib": 2048, "max_rss_mib": 512}
    history = tmp_path / "history"
    history.write_text("keep")
    monkeypatch.setattr("polymaker.m1234_sim.runner.time.monotonic", lambda: 10)
    monkeypatch.setattr(
        "polymaker.m1234_sim.runner.shutil.disk_usage",
        lambda p: SimpleNamespace(free=0 if resource_fault == "disk" else 10**12),
    )
    monkeypatch.setattr(
        "polymaker.m1234_sim.runner.resource.getrusage",
        lambda p: SimpleNamespace(ru_maxrss=1024 * (600 if resource_fault == "memory" else 100)),
    )
    with pytest.raises(RuntimeError, match="CAPACITY"):
        run.capacity()
    assert history.read_text() == "keep"


async def test_t239_submission_t245_fill_cancel_after_t240_and_gtc_kept():
    b, _ = broker(cancel_ms=10000)
    b.advance(239)
    o = b.submit(intent())
    b.advance(239.1)
    b.cancel(o.id)
    b.advance(245)
    b.trade(sell("85"), "late-T245")
    assert o.filled == 5 and o.state == "CANCEL_IN_FLIGHT"
    b.advance(249.2)
    assert o.state == "CANCELLED" and o.reserved == 0
    gtc = b.submit(intent())
    b.advance(250)
    b.advance(300)
    assert gtc.state == "LIVE" and gtc.reserved == 5


async def test_m1_profile_consumption_is_separate_and_does_not_reset_other_signals(tmp_path):
    feed, _, b, p, _, tick = await policy_setup(tmp_path)
    other = SignalView(feed.m1)
    try:
        await tick(0)
        await tick(0.01)
        assert p.feed.m1.used_rf and not other.used_rf and not feed.m1.used_rf
        assert feed.m1.states["1"]["rf"] is not None
        before = p.e.ledger.committed()
        b.advance(0.11)
        b.trade(
            {"asset_id": "1", "side": "SELL", "price": ".05", "size": "100", "trade_id": "actual"},
            "known",
        )
        assert b.account("M1-test").reserved == 0
        assert p.e.ledger.committed() == before  # admission intentionally retained
    finally:
        await p.close()


async def test_explicit_cross_rejection_reprices_and_keeps_direction_root(tmp_path):
    feed, clock, b, p, _, tick = await policy_setup(tmp_path, fault="CROSS_REJECT")
    try:
        await tick(0)
        await tick(0.01)
        feed.books["1"] = replace(feed.books["1"], bids=((D(".04"), D(80)),))
        b.update_books(feed.books)
        await tick(0.21)
        await tick(0.22)
        await tick(0.33)
        await tick(0.34)
        assert len(b.orders) == 2
        assert [o.intent.price for o in b.orders.values()] == ["0.05", "0.04"]
        assert len({o.intent.opportunity for o in b.orders.values()}) == 1
        assert sum(o.reserved for o in b.orders.values()) == 1
    finally:
        await p.close()


async def test_simulation_does_not_read_credentials_or_call_real_order_transport(
    tmp_path, monkeypatch
):
    from py_clob_client_v2.client import ClobClient

    import polymaker.m1_live_account
    from polymaker.config import Config

    def forbidden(*a, **k):
        pytest.fail("real account/config/order port accessed")

    monkeypatch.setattr(Config, "load", forbidden)
    monkeypatch.setattr(ClobClient, "_post", forbidden)
    monkeypatch.setattr(polymaker.m1_live_account, "account_snapshot", forbidden)
    _, _, b, p, _, tick = await policy_setup(tmp_path)
    try:
        await tick(0)
        await tick(0.01)
        assert len(b.orders) == 1
        assert type(p.e.gateway) is SimGateway and not hasattr(p.e.gateway._client, "_post")
    finally:
        await p.close()


async def test_public_http_boundary_rejects_write_and_unapproved_hosts():
    transport = ReadOnlyTransport(httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    try:
        for method, url in [
            ("POST", "https://clob.polymarket.com/order"),
            ("DELETE", "https://clob.polymarket.com/order"),
            ("GET", "https://data-api.polymarket.com/positions"),
        ]:
            with pytest.raises(RuntimeError, match="DENIED"):
                await transport.handle_async_request(httpx.Request(method, url))
        assert (
            await transport.handle_async_request(
                httpx.Request("GET", "https://clob.polymarket.com/time")
            )
        ).status_code == 200
    finally:
        await transport.aclose()


async def test_recovery_reconstructs_pending_reservations_and_marks_gap(tmp_path, monkeypatch):
    from polymaker.m1234_sim import runner

    monkeypatch.setattr(runner, "output_path", lambda p: Path(p))
    tape = fixture_events()
    # Stop before normal observation END, after naturally generated submissions.
    prefix = tape[: next(i for i, e in enumerate(tape) if e["kind"] == "GAP")]
    source = tmp_path / "crash-input.gz"
    r = Recorder(source)
    for event in prefix:
        r.append(
            event["kind"],
            Clock(event["available_mono"], event["available_wall"]),
            event["generation"],
            **{
                k: v
                for k, v in event.items()
                if k
                not in {"schema", "seq", "kind", "available_mono", "available_wall", "generation"}
            },
        )
    r.close()
    result = await runner.recover(source, tmp_path / "recovered")
    assert result["status"] == "PASS"
    from polymaker.m1234_sim.report import read_state

    state = read_state(tmp_path / "recovered/simulation.sqlite3")
    assert not state["coverage"]["complete_window"]
    assert any(
        g["reason"] == "RESTART_MISSING_INTERVAL_UNCERTAIN" for g in state["coverage"]["gaps"]
    )
    assert list(read_events(tmp_path / "recovered/input.jsonl.gz"))[-2]["kind"] == "GAP"


def test_calibration_missing_actuals_and_offline_sample(tmp_path):
    sample = {
        "market": "m",
        "token": "1",
        "order_id": "o",
        "physical_submit_id": "p",
        "submitted_at": 100,
        "fill_id": "f",
        "fill_at": 101,
        "fill_quantity": "5",
        "fill_price": ".1",
        "evidence": "ACTUAL_FILL",
    }
    path = tmp_path / "actual.jsonl"
    path.write_text(json.dumps(sample) + "\n")
    result = assess(
        load_records(path), [{**sample, "fill_quantity": "4", "fill_at": 102}], split_at=150
    )
    assert result["calibration_status"] == "UNCALIBRATED"
    assert result["usable_actual_fills"] == 1 and result["differences"][0]["quantity_delta"] == "-1"
    assert result["differences"][0]["partition"] == "CALIBRATION"
    missing = assess([{"order_id": "ack-only", "status": "ACK"}])
    assert missing["usable_actual_fills"] == 0 and missing["missing_fields"]["fill_quantity"] == 1


async def test_public_socket_burst_fairness_keeps_bounded_internal_queue(monkeypatch):
    import polymaker.rdp_sm_marketdata as md
    from polymaker.m1234_sim.runner import CaptureFeed
    from test_rdp_sm_obi import feed_fixture

    class Stream:
        def __init__(self):
            self.remaining = 5000

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def send(self, raw):
            return None

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self.remaining:
                raise StopAsyncIteration
            self.remaining -= 1
            return "PONG"

    monkeypatch.setattr(md.websockets, "connect", lambda *a, **k: Stream())
    old, _, _ = feed_fixture()
    target = asyncio.Queue(8192)
    capture = CaptureFeed(old.meta, target)
    await capture._connection()
    frames = [payload for kind, payload in list(target._queue) if kind == "FRAME"]
    assert len(frames) >= 4990 and capture.failure is None
    assert not any(
        kind == "GAP" and payload["reason"] == "WS_QUEUE_OVERFLOW"
        for kind, payload in list(target._queue)
    )


async def test_real_model_arrival_crossing_enters_shared_m1_reprice_retry(tmp_path):
    feed, _, b, p, _, tick = await policy_setup(tmp_path)
    try:
        await tick(0)
        await tick(0.01)
        feed.books["1"] = replace(
            feed.books["1"], bids=((D(".03"), D(80)),), asks=((D(".04"), D(80)),)
        )
        b.update_books(feed.books)
        await tick(0.21)
        await tick(0.22)
        await tick(0.33)
        await tick(0.34)
        assert len(b.orders) == 2
        orders = list(b.orders.values())
        assert orders[0].state == "REJECTED" and orders[0].reserved == 0
        assert orders[1].intent.price == "0.03"
        assert orders[1].intent.opportunity == orders[0].intent.opportunity
    finally:
        await p.close()


async def test_reporting_streams_large_diagnostics_with_bounded_memory(tmp_path):
    import tracemalloc

    from polymaker.m1234_sim.engine import Engine
    from polymaker.m1234_sim.report import summary

    tape = fixture_events()
    engine = Engine(tmp_path)
    await engine.ingest(tape[0])
    for _ in range(5000):
        engine.emit(
            "SIGNAL", {"strategy": "M1", "event": "UNAVAILABLE", "payload": {"reason": "x" * 4096}}
        )
    await engine.ingest({**tape[1], "kind": "END", "reason": "ARTIFICIAL_MEMORY_TEST"})
    await engine.close()
    tracemalloc.start()
    result = summary(tmp_path / "simulation.sqlite3")
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert peak < 16 * 1024**2
    assert result["event_counts"]["SIGNAL"] >= 5000
    assert any(r["count"] == 5000 for r in result["input_quality_diagnostics"])


async def test_slow_report_is_bounded_background_work_and_not_input_blocking(tmp_path, monkeypatch):
    from polymaker.m1234_sim import runner

    run = LiveRun.__new__(LiveRun)
    run.directory = tmp_path
    run.report_cache, run.report_tasks = {}, {}
    run.cfg = {"markout_seconds": [1, 5, 30]}
    run.engines = [
        SimpleNamespace(
            ended=True, meta=SimpleNamespace(slug="m"), resolution=None, directory=tmp_path
        )
    ]
    release = threading.Event()

    def slow(*args):
        release.wait(5)
        return {
            "market": {"slug": "m"},
            "coverage": {"complete_window": True, "gaps": []},
            "official_result": None,
            "accounts": [],
        }

    monkeypatch.setattr(runner, "summary", slow)
    try:
        run.update_report()
        assert "m" in run.report_tasks and not run.report_tasks["m"][1].done()
        run.update_report()
        assert len(run.report_tasks) == 1
    finally:
        release.set()
        await run.finish_reports()
    assert json.loads((tmp_path / "latest.json").read_text())["complete_windows"] == 1


async def test_m1_admission_uses_official_lower_bound_instead_of_host_wall(tmp_path):
    feed, _, b, p, _, _ = await policy_setup(tmp_path)
    try:
        assert feed.wall() - int(feed.meta.slug.rsplit("-", 1)[1]) == 20
        for _ in range(3):
            await p.poll(1.0, elapsed_lower=14.5)
            for _ in range(10):
                await asyncio.sleep(0)
        assert p.market.elapsed() == 14.5 and len(b.orders) == 0
        for _ in range(3):
            await p.poll(1.0, elapsed_lower=15.0)
            for _ in range(10):
                await asyncio.sleep(0)
        assert p.market.elapsed() == 15 and len(b.orders) == 1
    finally:
        await p.close()


async def test_engine_passes_official_bounds_to_shared_m1_market(tmp_path):
    from copy import deepcopy

    from polymaker.m1234_sim.engine import Engine

    tape = fixture_events()
    init = deepcopy(tape[0])
    init["definition"]["official_clock"][0] -= 10
    engine = Engine(tmp_path)
    await engine.ingest(init)
    await engine.ingest(tape[1])
    await engine.ingest(tape[2])
    event = {**tape[3], "seq": 4, "kind": "TIMER"}
    await engine.ingest(event)
    lower, upper = engine.bounds()
    assert all(p.market.elapsed() == lower for p in engine.m1_policies)
    assert all(p.e.official_time_uncertainty_s == upper - lower for p in engine.m1_policies)
    await engine.close()


def test_m234_live_candidate_keeps_existing_order_after_new_order_cutoff():
    from polymaker.m2_config import M2Config
    from polymaker.m1234_sim.policy import M234Policy
    from test_rdp_sm_obi import feed_fixture

    b, _ = broker()
    f, _, _ = feed_fixture()
    b.advance(239)
    candidate = {
        "strategy": "M2",
        "id": "late-root",
        "root": "AP",
        "token": "1",
        "outcome": "YES",
        "price": ".50",
        "confirmed": 239.0,
        "deadline": 247.0,
        "generation": 1,
        "observation_valid": True,
    }
    feed = SimpleNamespace(
        meta=f.meta,
        books=f.books,
        monotonic=lambda: b.now,
        m1_watermark=lambda: b.now,
        m1=SimpleNamespace(legal=lambda *a: True),
        hub=SimpleNamespace(candidates=lambda: [candidate] if b.now < 247 else []),
    )
    policy = M234Policy(SimpleNamespace(m234=feed), b, lambda *a: None, M2Config())
    policy.poll(239)
    o = next(iter(b.orders.values()))
    b.advance(239.1)
    policy.poll(245)
    assert o.cancel_requested is None
    b.advance(245.3)
    b.trade(sell("85"), "T245-after-cutoff")
    assert o.filled == 5
    b.advance(247)
    policy.poll(247)
    assert o.cancel_requested == 247
    b.advance(247.21)
    assert o.state == "CANCELLED" and o.reserved == 0
