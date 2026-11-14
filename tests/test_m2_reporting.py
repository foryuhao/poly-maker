"""A slow/failing reporter cannot block capture or mutate its public ledger."""
import asyncio
import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from polymaker.m2_config import M2Config
from polymaker.m2_store import M2Store
from polymaker.m2_study import M2Study
from test_m2_study import META


async def test_slow_reports_coalesce_and_leave_event_loop_responsive(tmp_path, monkeypatch):
    study = M2Study(M2Config(), tmp_path, api=object())
    entered, release = threading.Event(), threading.Event()
    written = []

    def slow(observed_at, runtime):
        written.append(runtime)
        if len(written) == 1:
            entered.set()
            assert release.wait(3)

    monkeypatch.setattr(study, "_write_report", slow)
    try:
        study.runtime = {"state": "FIRST", "counts": {"n": 1}}
        study.publish()
        async with asyncio.timeout(2):
            while not entered.is_set():
                await asyncio.sleep(.005)
        # A hypothetical socket/timer task still runs while reporting is blocked.
        ticks = []
        for n in range(20):
            study.runtime["state"] = f"UPDATE_{n}"
            study.runtime["counts"]["n"] = n
            study.publish()
            await asyncio.sleep(0)
            ticks.append(n)
        assert len(ticks) == 20 and len(written) == 1
        assert written[0] == {"state": "FIRST", "counts": {"n": 1}}
        release.set()
        await study.close_reports()
        assert [r["state"] for r in written] == ["FIRST", "UPDATE_19"]
    finally:
        release.set()
        await study.close_reports()
        study.store.close()


async def test_report_worker_uses_thread_owned_readonly_connection(tmp_path, monkeypatch):
    study = M2Study(M2Config(), tmp_path, api=object())
    study.store.start("r", META)
    original = M2Store.report
    threads = []

    def inspect(reader, runtime):
        assert reader is not study.store
        threads.append(threading.get_ident())
        assert reader.db.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            reader.db.execute("DELETE FROM markets")
        return original(reader, runtime)

    monkeypatch.setattr(M2Store, "report", inspect)
    try:
        study.publish()
        await study.close_reports()
        assert threads and threads[0] != threading.get_ident()
        report = json.loads((tmp_path/"latest.json").read_text())
        assert report["market_count"] == 1 and report["real_posts"] == 0
        assert study.store.db.execute("SELECT state FROM runs").fetchone()[0] == "ACTIVE"
    finally:
        await study.close_reports()
        study.store.close()


async def test_report_failure_retries_without_affecting_writer(tmp_path, monkeypatch):
    study = M2Study(M2Config(), tmp_path, api=object())
    original = study._write_report
    calls = []

    def first_failure(observed_at, runtime):
        calls.append(runtime)
        if len(calls) == 1:
            raise OSError("report destination temporarily unavailable")
        original(observed_at, runtime)

    monkeypatch.setattr(study, "_write_report", first_failure)
    try:
        study.publish()
        await study.close_reports()
        study.store.start("r", META)
        study.runtime = {"state": "RECOVERED", "real_posts": 0}
        study.publish()
        await study.close_reports()
        assert json.loads((tmp_path/"latest.json").read_text())["runtime"]["state"] == "RECOVERED"
    finally:
        await study.close_reports()
        study.store.close()


def test_pending_partial_index_preserves_unresolved_states_and_epoch_order(tmp_path):
    store = M2Store(tmp_path, M2Config(), "v")
    try:
        for n, state, winner in [(3, "CLOSED", None), (1, "INTERRUPTED", None),
                                 (2, "ACTIVE", None), (0, "CLOSED", "YES")]:
            meta = {**META, "slug": f"btc-updown-5m-{n*300}"}
            store.start(f"run{n}", meta)
            store.db.execute("UPDATE markets SET state=?,winner=? WHERE slug=?",
                             (state, winner, meta["slug"]))
        store.db.commit()
        assert [r["slug"] for r in store.pending()] == ["btc-updown-5m-300", "btc-updown-5m-900"]
        plan = store.db.execute("EXPLAIN QUERY PLAN SELECT slug,metadata FROM markets "
                                "WHERE state!='ACTIVE' AND winner IS NULL ORDER BY epoch LIMIT 32").fetchall()
        assert any("m2_pending_markets" in r[3] for r in plan)
        assert not any("TEMP B-TREE" in r[3] for r in plan)
    finally:
        store.close()


async def test_resolution_scan_throttled_but_official_result_and_identity_preserved(tmp_path, monkeypatch):
    received = []

    class API:
        async def resolution(self, expected):
            received.append(expected)
            return None

    study = M2Study(M2Config(), tmp_path, api=API())
    clock = SimpleNamespace(now=100.)
    # Replace only this study module's clock, leaving asyncio's timer untouched.
    import polymaker.m2_study as module
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock.now))
    scans = []
    original = study.store.pending
    monkeypatch.setattr(study.store, "pending", lambda: scans.append(clock.now) or original())
    try:
        study.store.start("r", META)
        study.store.finish("r", META["slug"], full_window=True, complete=True, counts={})
        await study.resolutions()
        await study.resolve_task
        for _ in range(20):
            await study.resolutions()
        assert scans == [100.]
        assert received[0]["token_ids"] == {"YES": "1", "NO": "2"}
        assert study.store.db.execute("SELECT winner FROM markets").fetchone()[0] is None
        clock.now = 101.
        await study.resolutions()
        assert scans == [100., 101.]
        assert len(received) == 1  # Existing 30-second per-market retry remains.
    finally:
        await study.close_reports()
        study.store.close()
