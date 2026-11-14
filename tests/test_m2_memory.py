"""Reclamation must stay inside the closed-feed quiet phase."""

import asyncio
import json
import time
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from polymaker import m2_memory
from polymaker.m2_config import M2Config
from polymaker.m2_study import M2Study
from polymaker.rdp_sm_marketdata import SessionFeed
from test_m2_study import META
from test_rdp_sm_m1_strategy import m1_feed


def test_collector_measures_gc_then_allocator_trim(monkeypatch):
    calls = []
    snapshots = iter([{"rss_bytes": 200, "swap_bytes": 40},
                      {"rss_bytes": 100, "swap_bytes": 40}])
    monkeypatch.setattr(m2_memory, "memory_snapshot", lambda: next(snapshots))
    monkeypatch.setattr(m2_memory.gc, "collect", lambda: calls.append("gc") or 12)

    class Trim:
        def __call__(self, pad):
            calls.append(("trim", pad))
            return 1

    monkeypatch.setattr(m2_memory.ctypes, "CDLL", lambda _: SimpleNamespace(malloc_trim=Trim()))
    result = m2_memory.recycle_idle_memory()
    assert calls == ["gc", ("trim", 0)]
    assert result["collected_objects"] == 12 and result["allocator_trimmed"] is True
    assert result["before"]["rss_bytes"] == 200
    assert result["after"]["swap_bytes"] == 40


def test_gc_still_runs_when_libc_trimming_is_unavailable(monkeypatch):
    monkeypatch.setattr(m2_memory.gc, "collect", lambda: 3)
    monkeypatch.setattr(m2_memory.ctypes, "CDLL", lambda _: SimpleNamespace())
    result = m2_memory.recycle_idle_memory()
    assert result["collected_objects"] == 3
    assert result["allocator_trimmed"] is None


def test_prune_preserves_unknown_results_and_inflight_resolution(tmp_path, monkeypatch):
    study = M2Study(M2Config(), tmp_path, api=object())
    clock = time.monotonic()
    study.clock = (241, clock, clock)
    logs = []
    monkeypatch.setattr(study, "log", lambda event, **payload: logs.append((event, payload)))
    monkeypatch.setattr("polymaker.m2_study.recycle_idle_memory", lambda: {})
    try:
        for n, winner in enumerate(["YES", None, "NO"]):
            meta = {**META, "slug": f"btc-updown-5m-{n*300}"}
            study.store.start(str(n), meta)
            study.store.finish(str(n), meta["slug"], full_window=True, complete=True, counts={})
            study.store.db.execute("UPDATE markets SET winner=? WHERE slug=?", (winner, meta["slug"]))
            study.retry_resolution[meta["slug"]] = 123
        study.store.db.commit()
        study.resolve_slug = "btc-updown-5m-600"
        study.retry_resolution["unknown-market"] = 456
        study.recycle_memory(0, "current", "run")
        assert study.retry_resolution == {"btc-updown-5m-300": 123,
                                          "btc-updown-5m-600": 123, "unknown-market": 456}
        assert logs[0][1]["settled_retry_keys_removed"] == 1
        assert study.store.db.execute("SELECT winner FROM markets WHERE slug='btc-updown-5m-300'").fetchone()[0] is None
    finally:
        study.store.close()


@pytest.mark.parametrize("phases,expected_calls,fail,fresh", [
    ([239.9, 240.1, 250, 289, 295, 300], [240.1], False, True),
    ([295, 300], [], False, True),
    ([241, 250, 300], [241], True, True),
    ([241, 300], [], False, False),
])
async def test_market_recycles_once_after_shutdown_and_persistence(
        tmp_path, monkeypatch, phases, expected_calls, fail, fresh):
    base, _ = m1_feed()
    epoch = int(time.time()//300)*300
    meta = replace(base.meta, slug=f"btc-updown-5m-{epoch}")
    clock = SimpleNamespace(elapsed=20.)
    feeds, collected, logs = [], [], []

    class API:
        async def ticks(self, tokens):
            return {token: Decimal(".01") for token in tokens}

        async def resolution(self, expected):
            return None

    class Feed(SessionFeed):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            feeds.append(self)

        async def run(self):
            self.connect()
            await self.stop_event.wait()

    study = M2Study(M2Config(min_free_disk_mib=256, host_cpu_guard_enabled=False,
                             m1_cpu_guard_enabled=False),
                    tmp_path, api=API(), feed_factory=Feed, resource_probe=lambda: 0)
    now = time.monotonic()
    clock_sample = now if fresh else now-120
    study.clock = (epoch+20, clock_sample, clock_sample)
    steps = iter(phases)

    async def advance(seconds):
        clock.elapsed = next(steps)
        await asyncio.sleep(0)

    def collect():
        assert not feeds[0].connected and feeds[0].task.done()
        assert not feeds[0].m1.events
        # Shutdown records reach the ledger before any collection.
        assert study.store.db.execute("SELECT count(*) FROM events").fetchone()[0] > 0
        collected.append(clock.elapsed)
        if fail:
            raise RuntimeError("injected cleanup failure")
        return {"collected_objects": 1}

    monkeypatch.setattr(study, "bounds", lambda epoch, mono=None: (clock.elapsed, clock.elapsed+.2))
    monkeypatch.setattr(study, "wait", advance)
    monkeypatch.setattr(study, "log", lambda event, **payload: logs.append((event, payload)))
    monkeypatch.setattr("polymaker.m2_study.recycle_idle_memory", collect)
    try:
        await study.market(meta, stop_at=float("inf"))
        await study.close_reports()
        assert collected == expected_calls
        assert study.store.db.execute("SELECT state FROM runs").fetchone()[0] == "CLOSED"
        assert sum(event == "MEMORY_RECYCLE_FAILED" for event, _ in logs) == int(fail)
        assert sum(event == "MEMORY_RECYCLED" for event, _ in logs) == int(bool(expected_calls) and not fail)
    finally:
        if study.resolve_task is not None:
            study.resolve_task.cancel()
            await asyncio.gather(study.resolve_task, return_exceptions=True)
        await study.close_reports()
        study.store.close()
