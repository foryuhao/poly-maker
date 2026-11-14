"""A growing native journal must not be starved by diagnostics or old history."""

import json
import os
from concurrent.futures import Future
from pathlib import Path

from polymaker.dashboard.collector import Collector
from polymaker.dashboard.m1 import VirtualReader, runtime_report
from polymaker.dashboard.model import summarize
from polymaker.dashboard.settlement import SettlementObserver, validate_resolution
from polymaker.m1_virtual_state import VirtualStore
from test_dashboard import NOW, SID, event, header, source, write_risk
from test_dashboard_m1 import metadata, prepare
from test_dashboard_m1_native import live_events
from test_dashboard_settlement import market, residual_events


def write_native(path: Path, rows):
    with path.open("w") as stream:
        for i, e in enumerate(rows, 1):
            stream.write(json.dumps({"ts": e["ts"], "kind": e["kind"], "data": {
                "session_id": SID, "payload": e["payload"], "writer_sequence": i,
                "diagnostics_dropped": 0}}) + "\n")


def test_large_m1_diagnostic_burst_keeps_signal_and_order_evidence_in_same_poll(tmp_path, monkeypatch):
    journal = tmp_path / "journal"
    journal.mkdir()
    path = journal / f"rdp_sm_wallet_{SID}.jsonl"
    rows = live_events()
    noise = [event("sm_m1_signal", {"event": kind, "detail": "x" * 400,
        "nested": {"event": "RF_CONFIRMED", "order_id": "not-a-post"}}, 1)
        for kind in ("UNAVAILABLE", "SIGNAL_INVALIDATED", "AP_CONFIRMED") for _ in range(1100)]
    write_native(path, [rows[0], *noise, *rows[1:]])
    assert 1024 * 1024 < path.stat().st_size < 4 * 1024 * 1024
    # Bound by bytes deterministically; do not make this a wall-clock benchmark.
    monkeypatch.setattr("polymaker.dashboard.collector.time.monotonic", lambda: 0)
    c = Collector(tmp_path, tmp_path / "derived")
    try:
        s = c.poll(now=NOW + 20)["sessions"][0]
        state = c.sources(SID)[0]
        assert state["caught_up"] and state["offset"] == path.stat().st_size
        assert state["diagnostic_payloads_not_decoded"] == len(noise)
        assert state["seq"] == len(noise) + len(rows)
        assert state["errors"] == state["gaps"] == 0
        assert s["m1_components"] == {"RF": 1, "EX": 1}
        assert s["real_submitted_orders"] == 1 and s["signal_counts"]["M1"] == 2
        assert s["filled_orders"] == 0
        assert all(e["payload"].get("event") not in
                   {"UNAVAILABLE", "SIGNAL_INVALIDATED", "AP_CONFIRMED"} for e in c.events(SID))
    finally:
        c.close()


def test_expired_sources_and_summaries_leave_hot_path_but_remain_available(tmp_path):
    journal = tmp_path / "journal"
    journal.mkdir()
    old_path = journal / "rdp_sm_wallet_past.jsonl"
    old_path.write_text("old journal must remain untouched\n")
    os.utime(old_path, (NOW - 48 * 3600, NOW - 48 * 3600))
    recent_path = journal / f"rdp_sm_wallet_{SID}.jsonl"
    write_risk(recent_path, [event("sm_session", header())])
    c = Collector(tmp_path, tmp_path / "derived")
    try:
        old = summarize("past", [event("sm_session", {
            **header(), "session_id": "past", "slug": f"btc-updown-5m-{NOW - 48 * 3600}"})], [source()])
        c.add_source(old_path, "past", "risk")
        c.db.execute("INSERT INTO summaries VALUES(?,?)", ("past", json.dumps(old)))
        c.db.commit()
        snapshot = c.poll(now=NOW + 20)
        assert [s["session_id"] for s in snapshot["sessions"]] == [SID]
        assert snapshot["source_count"] == 1 and snapshot["catching_up_sources"] == 0
        assert c.sources("past")[0]["offset"] == 0
        assert {s["session_id"] for s in c.snapshots()} == {"past", SID}
        assert old_path.read_text() == "old journal must remain untouched\n"
        recent_path.unlink()
        current = c.poll(now=NOW + 21)["sessions"][0]
        assert "RISK_SOURCE_MISSING" in current["issues"]
    finally:
        c.close()


def test_mismatched_session_in_m1_diagnostic_is_still_a_source_error(tmp_path):
    journal = tmp_path / "journal"
    journal.mkdir()
    path = journal / f"rdp_sm_wallet_{SID}.jsonl"
    write_native(path, [event("sm_session", header()),
                        event("sm_m1_signal", {"event": "UNAVAILABLE"}, 1)])
    text = path.read_text().splitlines()
    row = json.loads(text[1])
    row["data"]["session_id"] = "wrong-session"
    path.write_text(text[0] + "\n" + json.dumps(row) + "\n")
    c = Collector(tmp_path, tmp_path / "derived")
    try:
        c.poll(now=NOW + 10)
        assert c.sources(SID)[0]["errors"] == 1
        assert c.sources(SID)[0].get("diagnostic_payloads_not_decoded", 0) == 0
    finally:
        c.close()


def test_cached_summary_does_not_keep_observer_root_fields_or_warnings(tmp_path):
    journal = tmp_path / "journal"
    journal.mkdir()
    write_risk(journal / f"rdp_sm_wallet_{SID}.jsonl", [event("sm_session", header())])
    c = Collector(tmp_path, tmp_path / "derived")
    try:
        old = c.poll(now=NOW + 10)["sessions"][0]
        old["known_pnl"] = "999"
        old["issues"].append("EXTERNAL_ACCOUNT_WARNING")
        current = c.poll(now=NOW + 11)["sessions"][0]
        assert current["known_pnl"] == "0" and "EXTERNAL_ACCOUNT_WARNING" not in current["issues"]
        refreshed = {**current, "known_pnl": "1.25"}
        c.db.execute("UPDATE summaries SET snapshot=? WHERE sid=?", (json.dumps(refreshed), SID))
        c.db.commit()
        assert c.poll(now=NOW + 12)["sessions"][0]["known_pnl"] == "1.25"
    finally:
        c.close()


def test_virtual_incremental_cursor_keeps_late_events_and_wider_history(tmp_path):
    directory = prepare(tmp_path)
    store = VirtualStore(directory)
    past = NOW - 6 * 3600
    store.add_market(metadata(past), 1)
    reader = VirtualReader(tmp_path)
    initial = reader.read(NOW + 60, 1)
    old_slug = metadata(NOW - 300)["slug"]
    assert next(s for s in initial if s["slug"] == old_slug)["m1_components"]["RF"] == 1
    for i in range(2):
        store.event("RF_CONFIRMED", {"token_id": "1"}, old_slug, wall=NOW - 299 + i)
    store.close()
    refreshed = reader.read(NOW + 61, 1)
    assert next(s for s in refreshed if s["slug"] == old_slug)["m1_components"]["RF"] == 2
    assert len(reader.read(NOW + 62, 24)) == 3


def test_virtual_source_replacement_resets_cursor_even_with_higher_global_sequence(tmp_path):
    directory = prepare(tmp_path)
    reader = VirtualReader(tmp_path)
    reader.read(NOW + 60, 24)
    store = VirtualStore(directory)
    slug = metadata(NOW - 300)["slug"]
    for i in range(2):
        store.event("RF_CONFIRMED", {}, slug, wall=NOW + 10 + i)
    store.close()
    assert reader.read(NOW + 61, 24)[0]["m1_components"]["RF"] == 2
    replacement = prepare(tmp_path / "replacement")
    store = VirtualStore(replacement)
    for _ in range(20):
        store.event("UNRELATED", {}, wall=NOW + 1)
    store.close()
    os.replace(replacement / "state.sqlite3", directory / "state.sqlite3")
    assert reader.read(NOW + 62, 24)[0]["m1_components"]["RF"] == 1


def test_large_report_reads_only_runtime_tail_and_ignores_nested_lookalike_keys(tmp_path, monkeypatch):
    report = {"markets": [{"observed_at": 999, "runtime": {"state": "WRONG"},
                           "text": "x" * 200000}],
              "observed_at": NOW, "runtime": {"state": "OBSERVING", "connected": True}}
    path = tmp_path / "latest.json"
    path.write_text(json.dumps(report, indent=2))
    # The legacy full-file fallback must not be used for the live report format.
    monkeypatch.setattr(Path, "read_text", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("full report read")))
    assert runtime_report(path) == {"observed_at": NOW, "runtime": report["runtime"]}


def test_compact_legacy_runtime_report_remains_supported(tmp_path):
    report = {"observed_at": NOW, "runtime": {"state": "WAIT_NEXT_MARKET"}}
    path = tmp_path / "latest.json"
    path.write_text(json.dumps(report))
    assert runtime_report(path) == report


def test_late_settlement_does_not_restore_expired_sessions_to_live_snapshot(tmp_path, monkeypatch):
    journal = tmp_path / "journal"
    journal.mkdir()
    write_risk(journal / f"rdp_sm_wallet_{SID}.jsonl", residual_events())
    c = Collector(tmp_path, tmp_path / "derived", history_hours=1)
    observer = SettlementObserver(c)
    future = Future()
    monkeypatch.setattr(observer.pool, "submit", lambda *_args: future)
    try:
        snapshot = c.poll(now=NOW + 301)
        observer.tick(snapshot)
        future.set_result(validate_resolution(market(), snapshot["sessions"][0]))
        later = c.poll(now=NOW + 4000)
        observer.tick(later)
        assert not later["sessions"]
        assert len(c.snapshots()) == 1
    finally:
        observer.close()
        c.close()
