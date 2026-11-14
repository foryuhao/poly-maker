"""Market-local faults: synthetic SDK/HTTP, restart and rollover; no real sockets."""

import asyncio
import json
import time
from decimal import Decimal as D
from types import SimpleNamespace

import httpx
import pytest
from m4_helpers import close
from test_m4_live_budget import fixture
from test_m4_live_startup import EPOCH, SETTINGS, harness
from test_m4_live_task import evidence, serve

from polymaker.config import RDPObiConfig
from polymaker.m4_live_budget_market import BudgetMarket
from polymaker.m4_live_halt import MarketHalt
from polymaker.rdp_sm_network import NetworkHealth


async def test_journal_fatal_survives_same_market_restart(tmp_path, monkeypatch):
    original_collect = BudgetMarket.collect
    with monkeypatch.context() as patch:
        patch.setattr(BudgetMarket, "collect", lambda self: None)
        m, e, f, c, calls, transport = await fixture(tmp_path, patch)
        original_write = e.journal.write

        def fail_signal(kind, *args, **kwargs):
            if kind == "sm_m4_live_signal":
                raise OSError("synthetic journal failure")
            return original_write(kind, *args, **kwargs)

        patch.setattr(e.journal, "write", fail_signal)
        try:
            with pytest.raises(OSError, match="synthetic journal"):
                original_collect(m)
            assert e.fatal == "M4_JOURNAL_WRITE_FAILED"
            assert not calls
        finally:
            await close(m, e)
            transport.close()

    m, e, f, c, calls, transport = await fixture(tmp_path, monkeypatch, recover=True)
    try:
        assert e.fatal == "M4_JOURNAL_WRITE_FAILED"
        assert m.ledger.get("buy_closed") is True
        opportunity = next(iter(m.pending.values()))
        assert not m.guard(opportunity)
        await m.submit(opportunity)
        await m.advance_entries(c.now)
        assert not calls and not m.post_jobs
        e.fatal = None
        assert e.fatal == "M4_JOURNAL_WRITE_FAILED"
    finally:
        await close(m, e)
        transport.close()


async def test_halted_restart_reconciles_partial_without_replenishment(tmp_path, monkeypatch):
    old, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, response="TIMEOUT")
    ep = next(iter(old.pending.values()))
    await old.submit(ep)
    oid = old.ledger.row(ep.episode_id)["order_id"]
    order, trade = evidence(old, e, oid, "2", price=".70")
    e.fatal = "SYNTHETIC_FATAL"
    assert old.ledger.attempt(oid)["state"] == "UNKNOWN"
    await close(old, e)
    t.close()

    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, recover=True)
    q = None
    try:
        order["maker_address"] = e.gateway.funder
        q, reads = serve(monkeypatch, t, order, trade)
        await m.read_order(oid)
        assert m.ledger.attempt(oid)["state"] == "TERMINAL"
        assert D(m.ledger.row(ep.episode_id)["filled"]) == 2
        assert m.ledger.row(ep.episode_id)["state"] == "CUTOFF"
        await m.advance_entries(c.now)
        assert not calls and not m.post_jobs and len(reads) == 2
        assert m.ledger.get("market_halt_reason") == "SYNTHETIC_FATAL"
    finally:
        await close(m, e)
        t.close()
        if q:
            q.close()


async def test_async_writer_failure_latches_before_submit(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch)
    try:
        ep = next(iter(m.pending.values()))
        await e.journal.flush()
        # Worker failures do not synchronously throw from journal.write().
        e.journal.failure = "SYNTHETIC_ASYNC_WRITE_FAILURE"
        assert not m.guard(ep)
        assert e.fatal == "M4_JOURNAL_WRITE_FAILED"
        assert e.market_halt.reason == "M4_JOURNAL_WRITE_FAILED"
        assert not calls
    finally:
        e.journal.failure = None  # Test writer only, so cleanup can flush.
        await close(m, e)
        t.close()


async def test_fault_blocks_already_scheduled_post_at_native_boundary(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch)
    original = m.native_guard
    try:
        def fault_before_transport(attempt):
            e.fatal = "SYNTHETIC_CONCURRENT_FATAL"
            return original(attempt)
        e.gateway._client.m4_native_guard = fault_before_transport
        await m.submit(next(iter(m.pending.values())))
        assert not calls
        assert all(a["state"] == "UNSENT" for a in m.ledger.attempts())
        assert e.market_halt.reason == "SYNTHETIC_CONCURRENT_FATAL"
    finally:
        await close(m, e)
        t.close()


async def test_unhandled_job_fault_latches_before_draining_other_jobs(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch)
    async def failed():
        raise RuntimeError("synthetic worker failure")
    job = asyncio.create_task(failed())
    m.read_jobs["synthetic"] = job
    await asyncio.sleep(0)
    try:
        with pytest.raises(RuntimeError, match="synthetic worker"):
            await m.advance()
        assert e.market_halt.reason == "M4_MARKET_RUNTIME_FAILED"
        assert not calls
    finally:
        m.read_jobs.clear()  # Failure already awaited; let fixture cleanup drain.
        await close(m, e)
        t.close()


async def test_heartbeat_fault_blocks_queued_post_before_drain(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch)
    ep = next(iter(m.pending.values()))
    async def queue_entry():
        m.post_jobs[ep.episode_id] = asyncio.create_task(m.submit(ep))
    def fail_heartbeat():
        raise OSError("synthetic status write failed")
    monkeypatch.setattr(m, "advance", queue_entry)
    monkeypatch.setattr(m, "heartbeat", fail_heartbeat)
    try:
        with pytest.raises(OSError, match="synthetic status"):
            await m.run()
        assert not calls
        assert e.market_halt.reason == "M4_MARKET_RUNTIME_FAILED"
    finally:
        await close(m, e)
        t.close()


@pytest.mark.parametrize("content", ["", "{", '{"slug":"other","reason":"bad"}'])
def test_damaged_market_halt_never_reopens_market(tmp_path, content):
    halt = MarketHalt(tmp_path / "market.sqlite3", "market")
    halt.path.write_text(content)
    assert halt.reason
    halt.latch("SECOND_FAILURE")
    assert halt.path.read_text() == content
    assert MarketHalt(tmp_path / "next.sqlite3", "next").reason is None


def test_crash_between_latch_and_details_is_fail_closed(tmp_path, monkeypatch):
    from polymaker import m4_live_halt

    halt = MarketHalt(tmp_path / "market.sqlite3", "market")
    def broken_details(*args):
        raise OSError("synthetic details write failure")
    monkeypatch.setattr(m4_live_halt, "atomic_json", broken_details)
    with pytest.raises(OSError):
        halt.latch("FATAL")
    assert MarketHalt(tmp_path / "market.sqlite3", "market").reason


async def test_archive_timeout_does_not_pause_new_market(tmp_path, monkeypatch):
    old, e0, f0, c0, calls0, t0 = await fixture(tmp_path / "past", monkeypatch)
    await old.submit(next(iter(old.pending.values())))
    old.complete_window()
    await close(old, e0)
    t0.close()
    (tmp_path / "past/risk.sqlite3").rename(tmp_path / "ended.sqlite3")
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch)
    try:
        health = NetworkHealth(RDPObiConfig())
        e.reads.network = e.gateway._client.network = health
        async def archive_timeout(context, oid):
            client = context.e.gateway._client
            assert client.network is not health
            assert client.m4_read_only
            assert client.operation_local is not e.gateway._client.operation_local
            client._observe_http("GET", "https://clob.polymarket.com/data/order/" + oid,
                time.monotonic() - .5, time.time() - .5,
                error=httpx.ReadTimeout("synthetic archive timeout"))
            raise httpx.ReadTimeout("synthetic archive timeout")
        monkeypatch.setattr("polymaker.m4_live_budget_market.reconcile_owned", archive_timeout)
        await m.reconcile_archive()
        assert health.state == "NORMAL" and not health.samples["GET"]
        assert m.archive_network.state == "PAUSED" and not calls
        # Current-market failures still close the current admission circuit.
        e.gateway._client._observe_http("GET", "https://clob.polymarket.com/time",
            time.monotonic() - .5, time.time() - .5,
            error=httpx.ReadTimeout("synthetic current timeout"))
        assert health.snapshot().state == "PAUSED" and not health.snapshot().allowed
    finally:
        await close(m, e)
        t.close()


@pytest.mark.parametrize("failure_phase", ["runtime", "ledger"])
async def test_continuous_runner_recovers_halted_market_then_admits_next(
    tmp_path, monkeypatch, failure_phase
):
    from dataclasses import asdict

    from polymaker import m4_live_http, m4_live_startup, m4_live_taker, m4_run
    from polymaker.config import Config
    from polymaker.rdp_sm import OFFICIAL_HOSTS

    h = harness(tmp_path, monkeypatch)
    (tmp_path / "config/m4-live").mkdir(parents=True)
    (tmp_path / "config/m4.toml").write_text("")
    (tmp_path / "config/m4-live/execution.toml").write_text('mode="taker"\n')
    config = SimpleNamespace(wallet=SimpleNamespace(**OFFICIAL_HOSTS, chain_id=137),
                             paths=SimpleNamespace(), proxy=None)
    config.model_copy = lambda **_: config
    monkeypatch.setattr(Config, "load", lambda *_: config)
    monkeypatch.setattr(m4_live_startup, "LivePublicAPI", lambda: h.startup.api)
    monkeypatch.setattr(m4_live_startup, "LiveStartup", lambda *args: h.startup)
    monkeypatch.setattr(m4_live_http, "M4LiveReads", h.reads_type)
    h.clock_values[:] = [EPOCH+1, EPOCH+2, EPOCH+20, EPOCH+21, EPOCH+301, EPOCH+302]
    original_ledger = m4_live_taker.M4LiveLedger
    ledger_failed = False
    def failing_ledger(path, identity, *, settings, **kwargs):
        nonlocal ledger_failed
        ledger = original_ledger(path, identity, settings=settings, **kwargs)
        if failure_phase == "ledger" and not ledger_failed:
            ledger_failed = True
            ledger.initialize_session("m4_live_" + "1" * 32,
                                      settings.model_dump(mode="json"), asdict(h.meta))
            ledger.close()
            raise ValueError("synthetic ledger recovery failure")
        return ledger
    monkeypatch.setattr(m4_live_taker, "M4LiveLedger", failing_ledger)
    runs = []
    async def no_socket(self):
        await asyncio.Event().wait()
    async def no_wait(*args):
        pass
    class Market:
        def __init__(self, e, settings, stop, **kwargs):
            self.e = e
        async def run(self):
            runs.append((self.e.slug, self.e.fatal))
            if len(runs) == 1 and failure_phase == "runtime":
                raise RuntimeError("synthetic unexpected market fault")
            if self.e.slug == h.meta.slug:
                assert self.e.fatal == ("M4_MARKET_RUNTIME_FAILED" if failure_phase == "runtime"
                                        else "M4_MARKET_LEDGER_RECOVERY_FAILED")
                self.e.ledger.set("complete", True)
                return True
            assert self.e.fatal is None and self.e.ledger.committed() == 0
            return False
    monkeypatch.setattr(m4_run.M4Feed, "run", no_socket)
    monkeypatch.setattr(m4_run, "wait", no_wait)
    monkeypatch.setattr(m4_live_taker, "M4TakerMarket", Market)
    result = await m4_run.run(tmp_path, live=True)
    assert result["status"] == "RETAINED"
    assert len(runs) == (3 if failure_phase == "runtime" else 2)
    assert runs[0][0] == h.meta.slug != runs[-1][0]
    assert all(r.closed for r in h.readers)
    halt = MarketHalt(h.startup.root / "ledgers" / (runs[0][0] + ".sqlite3"), runs[0][0])
    assert json.loads(halt.path.read_text())["scope"] == "MARKET"


@pytest.mark.parametrize("continuous", [True, False])
async def test_unreadable_ledger_is_quarantined_without_erasing_facts(tmp_path, monkeypatch, continuous):
    import sqlite3

    h = harness(tmp_path, monkeypatch)
    h.startup.continue_market_faults = continuous
    path = h.startup.root / "ledgers" / f"{h.meta.slug}.sqlite3"
    path.parent.mkdir()
    original = b"synthetic corrupt ledger; retain for investigation"
    path.write_bytes(original)
    h.clock_values[:] = [EPOCH+20, EPOCH+301, EPOCH+302]
    if continuous:
        ready = await h.startup.prepare(SETTINGS, h.reads_type, None)
        assert ready.slug == f"btc-updown-5m-{EPOCH+300}"
        ready.reads.close()
    else:
        with pytest.raises(sqlite3.DatabaseError):
            await h.startup.prepare(SETTINGS, h.reads_type, None)
    assert path.read_bytes() == original
    assert MarketHalt(path, h.meta.slug).reason == "M4_MARKET_RECOVERY_INVALID"


async def test_ended_halted_preparation_does_not_pin_next_epoch(tmp_path, monkeypatch):
    h = harness(tmp_path, monkeypatch)
    old_slug = f"btc-updown-5m-{EPOCH}"
    MarketHalt(h.startup.root / "ledgers" / (old_slug + ".sqlite3"), old_slug).latch("FATAL")
    h.startup.recovery_slug = old_slug
    h.clock_values[:] = [EPOCH+301, EPOCH+302]
    ready = await h.startup.prepare(SETTINGS, h.reads_type, None)
    assert ready.slug == f"btc-updown-5m-{EPOCH+300}"
    assert any(e["status"] == "HALTED_MARKET_ARCHIVED" for e in h.events)
    ready.reads.close()
