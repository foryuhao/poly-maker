"""M1 retention removes only completed old raw logs, never execution evidence stores."""

import importlib.util
import json
import os
import sqlite3
from pathlib import Path

import pytest

NOW = 2_000_000_000
OLD = NOW - 25 * 3600
MARKET = "0x" + "a" * 64


def module():
    source = Path(__file__).resolve().parents[1] / "ops/ca_log_retention.py"
    spec = importlib.util.spec_from_file_location("m1_retention_test", source)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def write(path, content="log\n", *, at=OLD):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    os.utime(path, (at, at))
    return path


def session(
    root, sid="old-m1", *, mode="m1_v1", at=OLD, complete=True, state="ACK", uncertain=False
):
    directory = root / "journal"
    directory.mkdir(parents=True, exist_ok=True)
    ledger = directory / ("m1_live_" + sid.encode().hex().ljust(64, "0") + ".sqlite3")
    with sqlite3.connect(ledger) as db:
        db.executescript(
            "CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);"
            "CREATE TABLE opportunities(state TEXT);CREATE TABLE uncertain_posts(order_id TEXT);"
        )
        db.executemany(
            "INSERT INTO metadata VALUES(?,?)",
            [
                ("complete", json.dumps(complete)),
                ("session_id", json.dumps(sid)),
                ("identity", json.dumps({"market_id": MARKET})),
            ],
        )
        db.execute("INSERT INTO opportunities VALUES(?)", (state,))
        if uncertain:
            db.execute("INSERT INTO uncertain_posts VALUES('retained-first-buy')")
    journal = write(
        directory / f"rdp_sm_account_{sid}.jsonl",
        json.dumps(
            {
                "kind": "sm_session",
                "data": {
                    "session_id": sid,
                    "payload": {
                        "normal_entry_mode": mode,
                        "strategy": "m1_live_v1",
                        "market_id": MARKET,
                        "state_path": str(ledger),
                    },
                },
            }
        )
        + "\n",
        at=at,
    )
    return journal, ledger


def virtual(root, *, state="CLOSED", at=OLD):
    epoch = int((NOW - 36 * 3600) // 300) * 300
    slug = f"btc-updown-5m-{epoch}"
    directory = root / "research_outputs/m1_virtual"
    directory.mkdir(parents=True, exist_ok=True)
    dbpath = directory / "state.sqlite3"
    with sqlite3.connect(dbpath) as db:
        db.execute("CREATE TABLE markets(slug TEXT,state TEXT,epoch INTEGER)")
        db.execute("INSERT INTO markets VALUES(?,?,?)", (slug, state, epoch))
    log = write(directory / "sessions" / f"{slug}-123456789" / "signals.jsonl", at=at)
    return log, dbpath


def cleaner(root, opened=lambda: set()):
    return module().M1Cleaner(root, now=NOW, open_reader=opened)


def test_dry_run_and_apply_only_remove_completed_old_m1_logs(tmp_path):
    journal, ledger = session(tmp_path)
    signal, virtual_db = virtual(tmp_path)
    recent, _ = session(tmp_path, "recent", at=NOW - 3600)
    ca, _ = session(tmp_path, "old-ca", mode="ca_v1")
    study = write(tmp_path / "research_outputs/m1_isolated/studies/300/markets/events.jsonl.gz")
    old_study = write(tmp_path / "research_outputs/m1_isolated/old-observation/events.jsonl")
    config = write(tmp_path / "config/m1.toml")
    dashboard = write(tmp_path / "research_outputs/dashboard/archives/day/snapshot.json")
    prepost = write(tmp_path / "research_outputs/m1_virtual/prepost/old.sqlite3")
    risk_bytes = ledger.read_bytes()
    preview = cleaner(tmp_path).run()
    assert preview["candidate_files"] == 2 and preview["deleted_files"] == 0
    assert journal.exists() and signal.exists()
    result = cleaner(tmp_path).run(apply=True)
    assert result["scope"] == "M1_COMPLETED_RAW_LOGS_ONLY" and result["deleted_files"] == 2
    assert not journal.exists() and not signal.exists()
    assert ledger.read_bytes() == risk_bytes
    assert all(
        p.exists() for p in (recent, ca, study, old_study, config, dashboard, prepost, virtual_db)
    )


def test_native_m1_state_path_marker_no_longer_crashes_legacy_recovery_parser(tmp_path):
    journal, ledger = session(tmp_path)
    marker = write(
        tmp_path / "journal/rdp_obi_market_native.json",
        json.dumps({"strategy": "m1_live_v1", "state_path": str(ledger)}),
    )
    before = marker.read_bytes()
    assert cleaner(tmp_path).run(apply=True)["deleted_files"] == 1
    assert not journal.exists() and ledger.exists() and marker.read_bytes() == before


@pytest.mark.parametrize(
    "state,complete,uncertain",
    [
        ("ACK", False, False),
        ("UNKNOWN", True, False),
        ("RESERVED", True, False),
        ("PREPARED", True, False),
        ("ACK", True, True),
    ],
)
def test_pending_or_incomplete_risk_preserves_native_journal(tmp_path, state, complete, uncertain):
    journal, ledger = session(tmp_path, state=state, complete=complete, uncertain=uncertain)
    result = cleaner(tmp_path).run(apply=True)
    assert result["deleted_files"] == 0 and result["protected_recovery_files"] == 1
    assert journal.exists() and ledger.exists()


@pytest.mark.parametrize(
    "key,value",
    [
        ("session_id", "another-session"),
        ("identity", {"market_id": "another-market"}),
        ("complete", "true"),
    ],
)
def test_ledger_identity_or_completion_mismatch_is_preserved(tmp_path, key, value):
    journal, ledger = session(tmp_path)
    with sqlite3.connect(ledger) as db:
        db.execute("UPDATE metadata SET value=? WHERE key=?", (json.dumps(value), key))
    assert cleaner(tmp_path).run(apply=True)["deleted_files"] == 0 and journal.exists()


def test_missing_or_symlinked_ledger_and_legacy_header_are_not_deletion_proof(tmp_path):
    journal, ledger = session(tmp_path)
    ledger.unlink()
    assert cleaner(tmp_path).run(apply=True)["deleted_files"] == 0
    outside = write(tmp_path / "outside/ledger.sqlite3")
    ledger.symlink_to(outside)
    assert cleaner(tmp_path).run(apply=True)["deleted_files"] == 0
    assert journal.exists() and outside.exists()


def test_legacy_recovery_reference_is_preserved(tmp_path):
    journal, ledger = session(tmp_path)
    fence = write(
        tmp_path / "journal/rdp_obi_market_old.json",
        json.dumps({"session_id": "old-m1", "journal_path": str(journal)}),
    )
    result = cleaner(tmp_path).run(apply=True)
    assert result["deleted_files"] == 0 and journal.exists() and ledger.exists() and fence.exists()


def test_open_files_and_exact_24_hour_boundary_are_preserved(tmp_path):
    journal, _ = session(tmp_path, at=NOW - 24 * 3600)
    signal, _ = virtual(tmp_path)
    obj = cleaner(tmp_path, lambda: {(signal.stat().st_dev, signal.stat().st_ino)})
    assert obj.run(apply=True)["deleted_files"] == 0 and journal.exists() and signal.exists()


@pytest.mark.parametrize("state", ["ACTIVE", "UNKNOWN"])
def test_virtual_log_requires_closed_market_not_only_old_mtime(tmp_path, state):
    signal, _ = virtual(tmp_path, state=state)
    assert cleaner(tmp_path).run(apply=True)["deleted_files"] == 0 and signal.exists()


def test_unknown_files_symlinks_and_hardlinks_are_preserved(tmp_path):
    journal, _ = session(tmp_path)
    os.link(journal, journal.with_name("hardlink"))
    signal, _ = virtual(tmp_path)
    pending = write(signal.parent / "pending.json")
    outside = write(tmp_path / "outside/events.jsonl")
    linked = tmp_path / "research_outputs/m1_virtual/sessions/btc-updown-5m-1999870400-999"
    linked.symlink_to(outside.parent, target_is_directory=True)
    assert cleaner(tmp_path).run(apply=True)["deleted_files"] == 0
    assert all(p.exists() for p in (journal, signal, pending, outside))


def test_malformed_recovery_state_aborts_before_any_deletion(tmp_path):
    journal, _ = session(tmp_path)
    signal, _ = virtual(tmp_path)
    write(tmp_path / "journal/rdp_obi_account_bad.json", "{")
    with pytest.raises(ValueError):
        cleaner(tmp_path).run(apply=True)
    assert journal.exists() and signal.exists()


def test_new_recovery_reference_is_rechecked_after_open_file_scan(tmp_path):
    journal, _ = session(tmp_path)

    def opened():
        write(
            tmp_path / "journal/rdp_obi_market_new.json",
            json.dumps({"session_id": "old-m1", "journal_path": str(journal)}),
        )
        return set()

    result = cleaner(tmp_path, opened).run(apply=True)
    assert result["skipped_groups"] == 1 and journal.exists()


def test_virtual_market_state_rechecked_before_unlink(tmp_path):
    signal, dbpath = virtual(tmp_path)

    def opened():
        with sqlite3.connect(dbpath) as db:
            db.execute("UPDATE markets SET state='ACTIVE'")
        return set()

    result = cleaner(tmp_path, opened).run(apply=True)
    assert result["skipped_groups"] == 1 and signal.exists()


def test_changed_file_preserved_and_inaccessible_processes_abort(tmp_path):
    journal, _ = session(tmp_path)

    def changed():
        with journal.open("a") as f:
            f.write("new line\n")
        return set()

    assert cleaner(tmp_path, changed).run(apply=True)["deleted_files"] == 0 and journal.exists()
    os.utime(journal, (OLD, OLD))

    def denied():
        raise PermissionError("cannot inspect another process")

    with pytest.raises(PermissionError):
        cleaner(tmp_path, denied).run(apply=True)
    assert journal.exists()


def test_study300_and_database_paths_are_outside_deletion_scope(tmp_path):
    obj = cleaner(tmp_path)
    for p in (
        tmp_path / "research_outputs/m1_isolated/studies/300",
        tmp_path / "research_outputs/m1_virtual/prepost",
        tmp_path / "research_outputs/dashboard",
    ):
        with pytest.raises(ValueError), obj.directory(p):
            pass
