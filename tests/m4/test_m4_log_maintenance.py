"""Compression and report cleanup must preserve source and recovery evidence."""

import gzip
import json
import os
import sqlite3
from pathlib import Path

import pytest

with pytest.MonkeyPatch.context() as import_paths:
    import_paths.syspath_prepend(str(Path(__file__).resolve().parents[2] / "ops"))
    import m4_log_maintenance as maintenance

EPOCH = 1794279600
NOW = EPOCH + 10000


def journal(tmp_path, *, complete=True, resolved=True):
    runtime = tmp_path / "research_outputs/m4_virtual"
    for name in ["journal", "ledgers"]:
        (runtime / name).mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / "research_outputs/m4_pnl_hourly_watch"
    (state_dir / "reports").mkdir(parents=True, exist_ok=True)
    sid = "m4_virtual_" + "a" * 32
    slug = f"btc-updown-5m-{EPOCH}"
    path = runtime / "journal" / f"rdp_sm_virtual_{sid}.jsonl"
    header = {
        "kind": "sm_session",
        "data": {
            "session_id": sid,
            "payload": {"mode": "VIRTUAL", "slug": slug, "market_id": "market"},
        },
    }
    path.write_text(json.dumps(header) + "\n" + ('{"kind":"event"}\n' * 1000))
    os.utime(path, (EPOCH + 300, EPOCH + 300))
    metadata = {
        "complete": complete,
        "session_id": sid,
        "identity": {"mode": "VIRTUAL", "market_id": "market"},
    }
    if resolved:
        metadata["modeled_settlement"] = {
            "resolution": {"closed": True, "resolution_status": "resolved"}
        }
    with sqlite3.connect(runtime / "ledgers" / (slug + ".sqlite3")) as db:
        db.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY,value TEXT)")
        db.executemany(
            "INSERT INTO metadata VALUES (?,?)", [(k, json.dumps(v)) for k, v in metadata.items()]
        )
    return runtime, state_dir, path


def test_archive_roundtrip_is_lossless_and_idempotent(tmp_path):
    runtime, state, source = journal(tmp_path)
    original = source.read_bytes()
    ledger = next((runtime / "ledgers").glob("*.sqlite3"))
    ledger_before = ledger.read_bytes()
    live = tmp_path / "research_outputs/m4_live"
    live.mkdir()
    (live / "journal.jsonl").write_text("preserve actual submission evidence")
    result = maintenance.maintain(tmp_path, state, {"min_free_disk_bytes": 1}, None, now=NOW)
    assert len(result["compressed"]) == 1 and not result["warnings"]
    assert not source.exists()
    with gzip.open(result["compressed"][0]["archive"], "rb") as packed:
        assert packed.read() == original
    assert ledger.read_bytes() == ledger_before
    assert (live / "journal.jsonl").read_text() == "preserve actual submission evidence"
    assert not maintenance.maintain(tmp_path, state, {}, None, now=NOW)["compressed"]


@pytest.mark.parametrize(
    "complete,resolved,recent", [(False, True, False), (True, False, False), (True, True, True)]
)
def test_incomplete_unsettled_and_recent_journals_survive(tmp_path, complete, resolved, recent):
    _, state, source = journal(tmp_path, complete=complete, resolved=resolved)
    if recent:
        os.utime(source, (NOW - 10, NOW - 10))
    result = maintenance.maintain(tmp_path, state, {}, None, now=NOW)
    assert source.exists() and not result["compressed"]


def test_open_journal_is_protected(tmp_path):
    _, state, source = journal(tmp_path)
    with source.open("ab"):
        result = maintenance.maintain(tmp_path, state, {}, os.getpid(), now=NOW)
    assert source.exists() and not result["compressed"]


def test_conflicting_archive_preserves_both_inputs(tmp_path):
    runtime, state, source = journal(tmp_path)
    archive = runtime / "journal-archive"
    archive.mkdir()
    target = archive / (source.name + ".gz")
    with gzip.open(target, "wb") as packed:
        packed.write(b"different previous evidence")
    original = source.read_bytes()
    result = maintenance.maintain(tmp_path, state, {}, None, now=NOW)
    assert result["warnings"] and source.read_bytes() == original
    with gzip.open(target, "rb") as packed:
        assert packed.read() == b"different previous evidence"


def test_source_changed_while_compressing_is_not_unlinked(tmp_path, monkeypatch):
    runtime, _, source = journal(tmp_path)
    archive = runtime / "journal-archive"
    archive.mkdir()
    copy = maintenance.shutil.copyfileobj

    def changed(reader, writer):
        copy(reader, writer)
        with source.open("ab") as stream:
            stream.write(b"new event\n")

    monkeypatch.setattr(maintenance.shutil, "copyfileobj", changed)
    with pytest.raises(ValueError, match="CHANGED_DURING_COMPRESSION"):
        maintenance.compress_verified(source, archive)
    assert source.exists() and not list(archive.iterdir())


@pytest.mark.parametrize("status", ["OK", "ATTENTION"])
def test_old_duplicate_inputs_expire_but_images_summaries_and_latest_survive(tmp_path, status):
    _, state, _ = journal(tmp_path)
    for name in ["old", "latest"]:
        directory = state / "reports" / name
        directory.mkdir()
        report = {"directory": str(directory), "observed_at": NOW - 90000, "status": status}
        (directory / "report.json").write_text(json.dumps(report))
        for filename in maintenance.REPORT_DETAILS | {
            "chart.png",
            "summary.json",
            "validation.json",
        }:
            path = directory / filename
            path.write_text("frozen artifact")
            os.utime(path, (NOW - 90000, NOW - 90000))
    (state / "latest.json").write_text(json.dumps({"directory": str(state / "reports/latest")}))
    result = maintenance.maintain(tmp_path, state, {}, None, now=NOW)
    assert len(result["removed_report_details"]) == len(maintenance.REPORT_DETAILS)
    assert {p.name for p in (state / "reports/old").iterdir()} == {
        "report.json",
        "chart.png",
        "summary.json",
        "validation.json",
    }
    assert all((state / "reports/latest" / name).exists() for name in maintenance.REPORT_DETAILS)
