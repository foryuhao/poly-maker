"""Observer isolation, exact economics, streaming recovery and immutable archives."""

import hashlib
import json
import os
import pty
import select
import subprocess
import sys
import time
from decimal import Decimal

import pytest
from rich.console import Console

from polymaker.dashboard.archive import Archiver
from polymaker.dashboard.collector import Collector
from polymaker.dashboard.model import compact, summarize
from polymaker.dashboard.view import read_snapshot, render

SID = "session-test"
NOW = int(time.time() // 300) * 300


def event(kind, payload, offset=0, source_kind="risk"):
    return dict(kind=kind, payload=payload, offset=offset, ts=NOW + offset,
                source_kind=source_kind, sequence=offset + 1, monotonic=100 + offset)


def header(ca_path=None):
    runtime = {"snapshot": {"outcomes": {"YES": {"token_id": "yes"}, "NO": {"token_id": "no"}}}}
    if ca_path:
        runtime["writer"] = {"path": str(ca_path)}
    return {"session_id": SID, "slug": f"btc-updown-5m-{NOW}",
            "normal_entry_mode": "ca_v1", "ca_observation": {"runtime": runtime}}


def placement(oid, side, wid="w1"):
    return dict(order_id=oid, expected_order_id=oid, outcome="ACK", post_started=True,
                quote=dict(token_id=side.lower(), outcome=side, side="BUY", price=.4,
                           size=5, window_id=SID + ":" + wid))


def fill(oid, side, price, *, trade=None, quantity=5, status="CONFIRMED", **extra):
    return dict(maker_order_id=oid, trade_id=trade or "trade-" + oid,
                token_id=side.lower(), our_side="BUY", price=price, size=quantity,
                status=status, **extra)


def source():
    return {"kind": "risk", "caught_up": True, "last_ts": NOW}


def ledger_events():
    return [event("sm_session", header()),
            event("sm_placement_result", placement("open", "YES"), 1),
            event("sm_trade", fill("open", "YES", ".4", status="MATCHED"), 2),
            event("sm_ba_taker_post_returned", {"order_id": "exit", "post_started": True}, 3),
            event("sm_trade", fill("open", "YES", ".4"), 4),
            event("sm_ba_taker_trade", fill("exit", "NO", ".65", principal_usdc="3.25", fee_usdc=".03"), 5)]


def test_exact_taker_fee_dedup_and_no_merge_double_count():
    events = ledger_events()
    events += [event("sm_ba_taker_trade", events[-1]["payload"], 6),
               event("sm_merge_result", dict(status="CONFIRMED", merge_id="merge", amount_raw=5000000,
                                             yes_token_id="yes", no_token_id="no"), 7),
               event("sm_ba_taker_result", {"orders": [{"order_id": "exit", "complete": True}]}, 8)]
    s = summarize(SID, events, [source()])
    assert Decimal(s["pair_pnl"]) == Decimal("-.28")
    assert s["pnl_by_role"] == {"MAKER": "0", "TAKER": "-0.280", "OTHER": "0"}
    assert s["inventory"] == {"YES": "0", "NO": "0"}
    assert s["filled_orders"] == 2
    assert Decimal(s["fees"]) == Decimal(".03")
    assert s["residual"] == [] and Decimal(s["trading_pnl"]) == Decimal("-.28")


def test_unconfirmed_or_inexact_taker_never_books_pnl():
    events = ledger_events()
    events[-1]["payload"]["status"] = "MATCHED"
    s = summarize(SID, events, [source()])
    assert Decimal(s["pair_pnl"]) == 0 and s["trading_pnl"] is None
    assert s["pending_fills"] == 1 and s["pending_orders"] == 1
    events[-1]["payload"]["status"] = "CONFIRMED"
    del events[-1]["payload"]["fee_usdc"]
    s = summarize(SID, events, [source()])
    assert "TAKER_EXACT_COST_PENDING" in s["issues"] and not s["pairs"]


def test_confirmed_partial_taker_keeps_order_uncertainty():
    events = ledger_events()
    events[-1]["payload"]["size"] = 2
    s = summarize(SID, events, [source()])
    assert s["pending_orders"] == 1 and s["trading_pnl"] is None


def test_risk_writer_flush_sequence_holes_do_not_claim_missing_fills(files):
    root, path, output = files
    rows = ledger_events()
    for row in rows[3:]:
        row["sequence"] += 1
    write_risk(path, rows)
    c = Collector(root, output)
    try:
        c.poll()
        assert c.sources(SID)[0].get("writer_sequence_holes") == 1
        assert c.sources(SID)[0]["gaps"] == 0
    finally:
        c.close()


def test_diagnostic_fast_path_preserves_watermark_but_not_fake_nested_evidence(files):
    root, path, output = files
    write_risk(path, [event("sm_session", header())])
    row = {"ts": NOW + 1, "kind": "sm_sync_cache_read", "data": {
        "session_id": SID, "payload": {"nested": {"kind": "sm_trade", "payload": fill("fake", "YES", ".2")}},
        "writer_sequence": 2, "diagnostics_dropped": 0}}
    with path.open("a") as f:
        f.write(json.dumps(row) + "\n")
    c = Collector(root, output)
    try:
        c.poll()
        assert c.sources(SID)[0]["seq"] == 2
        assert c.sources(SID)[0]["diagnostic_payloads_not_decoded"] == 1
        assert c.snapshots()[0]["filled_orders"] == 0
    finally:
        c.close()


def test_fifo_partial_pairs_and_reverse_residual():
    events = ledger_events()[:3]
    events[2]["payload"]["status"] = "CONFIRMED"
    events.extend([event("sm_placement_result", placement("close", "NO", "ba:1:maker:1"), 3),
                   event("sm_trade", fill("close", "NO", ".5", quantity=6), 4)])
    s = summarize(SID, events, [source()])
    assert Decimal(s["pnl_by_role"]["MAKER"]) == Decimal(".5")
    assert s["residual"] == [{"outcome": "NO", "quantity": "1", "cost": "0.5"}]
    assert s["delta"] == "-1" and s["trading_pnl"] is None


def test_late_status_retains_first_observed_fifo_order():
    events = ledger_events()
    # NO confirms before YES; economic FIFO still follows first observation.
    events[4]["offset"] = 8
    s = summarize(SID, events, [source()])
    assert s["pairs"][0]["category"] == "TAKER"


def test_final_inventory_mismatch_is_pending():
    events = ledger_events() + [event("sm_run_result", dict(execution_status="COMPLETED",
                confirmed_yes=7, confirmed_no=5, merged_pairs=0, final_close_barrier="SAFE"), 7)]
    s = summarize(SID, events, [source()])
    assert "FINAL_INVENTORY_MISMATCH" in s["issues"] and s["trading_pnl"] is None


def test_ca_final_drain_does_not_mark_archive_complete_early():
    h = header()
    h["ca_observation"]["runtime"]["writer"] = {"accepted": 20, "written": 19}
    s = summarize(SID, [event("sm_session", h)], [source(), {"kind": "ca", "caught_up": True, "seq": 19}])
    assert "CA_RECORDS_PENDING" in s["issues"]


def test_signal_counts_are_distinct_from_execution_and_expire():
    events = [event("sm_session", header()),
              event("CA_CONFIRMED", {"episode": dict(episode_id="ca-1", token_id="yes", outcome="YES", deadline=105)}, 1, "ca"),
              event("sm_opposite_ba_execution", dict(event="CONFIRMED", episode_id="ba-1"), 2)]
    s = summarize(SID, events, [source()])
    assert s["signal_counts"] == {"CA": 1, "BA": 1} and s["submitted_orders"] == 0
    assert s["signals"]["CA"]["YES"]["wall_deadline"] == NOW + 5


def write_risk(path, rows, mode="w"):
    with path.open(mode) as stream:
        for e in rows:
            stream.write(json.dumps({"kind": e["kind"], "ts": e["ts"], "data": {
                "session_id": SID, "payload": e["payload"], "writer_sequence": e["sequence"],
                "occurred_monotonic": e["monotonic"]}}) + "\n")


@pytest.fixture
def files(tmp_path):
    journal = tmp_path / "journal"
    journal.mkdir()
    path = journal / f"rdp_sm_wallet_{SID}.jsonl"
    output = tmp_path / "derived"
    return tmp_path, path, output


def test_partial_line_restart_and_exactly_once(files):
    root, path, output = files
    write_risk(path, ledger_events())
    with path.open("a") as f:
        f.write('{"kind":')
    collector = Collector(root, output)
    try:
        collector.poll()
        assert collector.sources(SID)[0]["partial"]
        first_count = len(collector.events(SID))
    finally:
        collector.close()
    with path.open("a") as f:
        f.write(' "bad", "ts": 1}\n')
    collector = Collector(root, output)
    try:
        collector.poll()
        assert len(collector.events(SID)) == first_count
        assert collector.sources(SID)[0]["errors"] == 1
        assert not collector.sources(SID)[0]["partial"]
        collector.poll()
        assert len(collector.events(SID)) == first_count
    finally:
        collector.close()


def test_ca_path_is_read_from_header_not_guessed_session_id(files):
    root, path, output = files
    ca_path = root / "different-ca-name.jsonl"
    ca_path.write_text(json.dumps(dict(event="CA_CONFIRMED", sequence=1, session_id="other-ca-id",
                         observed_wall=NOW, observed_monotonic=100,
                         payload={"episode": dict(episode_id="ca1", outcome="YES", token_id="yes")})) + "\n")
    write_risk(path, [event("sm_session", header(ca_path))])
    c = Collector(root, output)
    try:
        c.poll()
        c.poll()
        assert c.snapshots()[0]["signal_counts"]["CA"] == 1
        assert len(c.sources(SID)) == 2
    finally:
        c.close()


def test_source_replacement_is_explicit_and_cannot_duplicate_fills(files):
    root, path, output = files
    write_risk(path, ledger_events())
    c = Collector(root, output)
    try:
        c.poll()
        replacement = root / "replace.jsonl"
        write_risk(replacement, [event("sm_session", header())])
        replacement.replace(path)
        c.poll()
        s = c.snapshots()[0]
        assert s["filled_orders"] == 0 and "RISK_REPLACED:1" in s["issues"]
    finally:
        c.close()


def test_observer_lock_and_protected_outputs(files):
    root, path, output = files
    c = Collector(root, output)
    try:
        with pytest.raises(RuntimeError, match="already owns"):
            Collector(root, output)
        for p in [root, root / "journal", root / "config/dashboard"]:
            with pytest.raises(ValueError):
                Collector(root, p)
    finally:
        c.close()


def test_archive_versions_hashes_and_idle_five_minute_windows(files):
    root, path, output = files
    write_risk(path, ledger_events()[:-1])
    before = path.read_bytes()
    c = Collector(root, output)
    try:
        a = Archiver(c)
        snap = c.poll(NOW + 301)
        a.tick(snap)
        versions = list((output / "archives").glob("*/*/v*"))
        assert len(versions) == 1
        v1 = versions[0]
        old_manifest = (v1 / "manifest.json").read_bytes()
        assert json.loads(old_manifest)["status"] == "PROVISIONAL"
        a.tick(snap)
        assert len(list((output / "archives").glob("*/*/v*"))) == 1
        write_risk(path, [ledger_events()[-1]], "a")
        snap = c.poll(NOW + 302)
        a.tick(snap)
        assert len(list((output / "archives").glob("*/*/v*"))) == 2
        assert (v1 / "manifest.json").read_bytes() == old_manifest
        assert path.read_bytes().startswith(before)
        for p in (output / "archives").glob("*/*/v*/manifest.json"):
            manifest = json.loads(p.read_text())
            for name, info in manifest["files"].items():
                assert hashlib.sha256((p.parent / name).read_bytes()).hexdigest() == info["sha256"]
        snap = c.poll(NOW + 601)
        a.tick(snap)
        assert len(list((output / "intervals").glob("*/*/manifest.json"))) == 2
    finally:
        c.close()


def test_empty_market_does_not_stop_interval_archival(files):
    root, _, output = files
    c = Collector(root, output)
    try:
        a = Archiver(c)
        a.tick(c.poll(NOW + 301))
        a.tick(c.poll(NOW + 601))
        assert len(list((output / "intervals").glob("*/*/manifest.json"))) == 2
    finally:
        c.close()


def test_archive_write_failure_is_visible_and_retried(files, monkeypatch):
    root, path, output = files
    write_risk(path, ledger_events())
    c = Collector(root, output)
    try:
        a = Archiver(c)
        import polymaker.dashboard.archive as module
        publish = module.publish
        monkeypatch.setattr(module, "publish", lambda *args: (_ for _ in ()).throw(OSError("disk full")))
        snap = c.poll(NOW + 301)
        assert a.tick(snap)["error"] and a.last_boundary == 0
        monkeypatch.setattr(module, "publish", publish)
        assert a.tick(snap)["error"] is None and a.last_boundary == NOW + 300
    finally:
        c.close()


def test_snapshot_staleness_and_render_without_demo_data(files):
    _, _, output = files
    assert read_snapshot(output).get("error")
    s = summarize(SID, ledger_events(), [source()])
    snapshot = {"sessions": [s], "observed_at": NOW - 60, "current_session_id": SID}
    console = Console(record=True, width=100, force_terminal=False)
    console.print(render(snapshot, now=NOW, width=100))
    text = console.export_text()
    assert "STALE" in text and SID in text and "demo-" not in text
    assert "Taker" in text and "-0.280" in text


def test_compaction_removes_secrets_and_raw_responses():
    value = compact("sm_placement_result", {"quote": {"api_key": "secret", "price": .5},
                                           "raw_response": "unsafe", "signer": "wallet"})
    assert value == {"quote": {"price": .5}}


def test_standalone_entry_does_not_import_trading_or_config():
    code = "import sys; import polymaker.dashboard.__main__; assert not any(x in sys.modules for x in ['polymaker.config','polymaker.rdp_sm','polymaker.execution.gateway'])"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_cli_reads_cache_without_configuration_and_rejects_missing_cache(files, monkeypatch):
    from typer.testing import CliRunner

    from polymaker.cli import app
    from polymaker.config import Config

    def forbidden(*args, **kwargs):
        raise AssertionError("dashboard must never load trading config")

    monkeypatch.setattr(Config, "load", forbidden)
    _, _, output = files
    runner = CliRunner()
    result = runner.invoke(app, ["dashboard", "--output-dir", str(output), "--once"])
    assert result.exit_code == 2 and "dashboard-archive" in result.output
    output.mkdir()
    (output / "latest.json").write_text(json.dumps({"sessions": [], "observed_at": time.time()}))
    result = runner.invoke(app, ["dashboard", "--output-dir", str(output), "--json"])
    assert result.exit_code == 0 and json.loads(result.output)["sessions"] == []


def test_terminal_archive_key_then_quit_keeps_background_independent(files):
    _, _, output = files
    output.mkdir()
    snapshot = {"observed_at": time.time(), "sessions": [summarize(SID, ledger_events(), [source()])],
                "current_session_id": SID}
    (output / "latest.json").write_text(json.dumps(snapshot))
    master, slave = pty.openpty()
    child = subprocess.Popen([sys.executable, "-m", "polymaker.dashboard", "--output-dir", str(output)],
                             stdin=slave, stdout=slave, stderr=slave, env={**os.environ, "TERM": "xterm-256color"})
    try:
        received = b""
        until = time.monotonic() + 5
        while b"POLYMAKER" not in received and time.monotonic() < until:
            if select.select([master], [], [], .1)[0]:
                received += os.read(master, 16384)
        assert b"POLYMAKER" in received
        os.write(master, b"aq")
        assert child.wait(timeout=5) == 0
        assert (output / "latest.json").exists()
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)
        os.close(master)
        os.close(slave)


def test_terminal_h_cycles_history_periods_including_archive_view(files):
    _, _, output = files
    output.mkdir()
    snapshot = {"observed_at": time.time(), "sessions": [summarize(SID, ledger_events(), [source()])],
                "current_session_id": SID}
    (output / "latest.json").write_text(json.dumps(snapshot))
    master, slave = pty.openpty()
    child = subprocess.Popen([sys.executable, "-m", "polymaker.dashboard", "--output-dir", str(output)],
                             stdin=slave, stdout=slave, stderr=slave, env={**os.environ, "TERM": "xterm-256color"})

    def expect(text):
        received = b""
        until = time.monotonic() + 5
        while text.encode() not in received and time.monotonic() < until:
            if select.select([master], [], [], .1)[0]:
                received += os.read(master, 16384)
        assert text.encode() in received, received.decode(errors="replace")

    try:
        expect("历史 24h")
        for key, label in [(b"h", "历史 4h"), (b"h", "历史 12h"), (b"H", "历史 24h"),
                           (b"a", "市场归档索引"), (b"h", "历史范围 4h")]:
            os.write(master, key)
            expect(label)
        os.write(master, b"q")
        assert child.wait(timeout=5) == 0
        assert json.loads((output / "latest.json").read_text()) == snapshot
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)
        os.close(master)
        os.close(slave)


@pytest.mark.parametrize("height", [24, 50])
def test_history_period_filters_pnl_distribution_and_archive_together(height):
    import copy
    import io

    current = summarize(SID, ledger_events(), [source()])
    sessions = []
    for age, pnl in [(1, "1"), (6, "10"), (18, "100")]:
        s = copy.deepcopy(current)
        s.update(session_id=f"history-{age}h", market_start=NOW - age * 3600,
                 market_end=NOW - age * 3600 + 300, pair_pnl=pnl,
                 pnl_by_role={"MAKER": pnl, "TAKER": "0", "OTHER": "0"},
                 pairs=[{"category": "MAKER", "pnl": pnl}])
        sessions.append(s)
    snapshot = {"observed_at": NOW, "sessions": sessions, "current_session_id": "history-1h"}
    for hours, total in [(4, "+1.000"), (12, "+11.000"), (24, "+111.000")]:
        console = Console(width=140, record=True, file=io.StringIO())
        console.print(render(snapshot, now=NOW, width=140, height=height, hours=hours))
        text = console.export_text()
        assert f"{hours}h" in text and f"Total {total}" in text
        assert f"M {total}" in text or f"Maker {total}" in text
        assert "history-1h" in text  # Current session stays visible.
        console.print(render(snapshot, now=NOW, width=140, height=height, hours=hours, archive_view=True))
        archive = console.export_text()
        assert ("history-" in archive) and ("+100.000" in archive) == (hours == 24)


@pytest.mark.parametrize("pnl,bucket", [
    ("2", 0), ("1.000001", 0), ("1", 1), ("0.500001", 1),
    ("0.5", 2), ("0.000001", 2), ("0", 3), ("-0.499999", 3),
    ("-0.5", 4), ("-0.999999", 4), ("-1", 5), ("-2", 5),
])
def test_pnl_histogram_six_descending_buckets_include_other_and_exact_edges(pnl, bucket):
    import io
    import re

    from polymaker.dashboard.view import distribution

    roles = [("MAKER", 2), ("TAKER", 3), ("OTHER", 5)]
    pairs = [{"category": role, "pnl": pnl} for role, count in roles for _ in range(count)]
    console = Console(width=120, record=True, file=io.StringIO())
    console.print(distribution([{"pairs": pairs[:4]}, {"pairs": pairs[4:]}]))
    text = console.export_text()
    labels = ["P>1.0", "0.5<P≤1", "0<P≤0.5", "−0.5<P≤0", "−1<P≤−0.5", "P≤−1.0"]
    rows = [line for line in text.splitlines() if any(label in line for label in labels)]
    assert len(rows) == 6 and "OTHER" in text
    for index, (row, label) in enumerate(zip(rows, labels, strict=True)):
        tail = row.partition(label)[2]
        assert [int(value) for value in re.findall(r"\d+", tail)] == ([2, 3, 5] if index == bucket else [0, 0, 0])
        # OTHER is the largest column and still uses the same six-character scale.
        assert max((len(bar) for bar in re.findall("━+", tail)), default=0) <= 6
