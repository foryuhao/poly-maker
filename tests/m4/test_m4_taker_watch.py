"""Local-only observer: real schema-shaped fixtures, no trading/network client."""
import hashlib
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location("m4_taker_watch", Path(__file__).parents[2] / "ops/m4_taker_watch.py")
watch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(watch)
EPOCH = 1800000000


def fixture(tmp_path, *, state="ACK", filled="0", spent="0", size="8", price=".51", elapsed=30,
            extra_signal=False, duplicate=False, prior="0", planned="4.219944"):
    root = tmp_path / "research_outputs/m4_live"
    path = root / "ledgers" / f"btc-updown-5m-{EPOCH}.sqlite3"
    path.parent.mkdir(parents=True)
    db = sqlite3.connect(path)
    db.executescript('''
        CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE opportunities(id TEXT,root TEXT,outcome TEXT,price TEXT,state TEXT,reason TEXT,posts INT,filled TEXT,spent TEXT,reserved TEXT);
        CREATE TABLE taker_attempts(seq INTEGER PRIMARY KEY,order_id TEXT,task_id TEXT,plan TEXT,quote TEXT,state TEXT,started INT,filled TEXT,spent TEXT);
        CREATE TABLE facts(kind TEXT,identity TEXT,payload TEXT);
        CREATE TABLE taker_trade_evidence(trade_id TEXT,order_id TEXT,status TEXT);
    ''')
    settings = {"execution": {"mode": "taker"}, "minimum_price": ".50", "start_sec": 15, "cutoff_sec": 240}
    meta = {"identity": {"mode": "LIVE", "settings_hash": "saved"}, "settings": settings,
            "complete": True, "session_id": "offline"}
    db.executemany("INSERT INTO metadata VALUES (?,?)", [(k, json.dumps(v)) for k, v in meta.items()])
    db.execute("INSERT INTO opportunities VALUES (?,?,?,?,?,?,?,?,?,?)",
               ("signal", "signal", "YES", ".50", "CUTOFF", "M4_T240_ENTRY_ENDED", 1, filled, spent, "0" if state == "TERMINAL" else "4.3"))
    quote = {"side": "BUY", "price": price, "size": size}
    plan = {"planned_cost_usdc": planned, "prior_spent_usdc": prior}
    db.execute("INSERT INTO taker_attempts VALUES (?,?,?,?,?,?,?,?,?)",
               (1, "owned", "signal", json.dumps(plan), json.dumps(quote), state, 1, filled, spent))
    db.execute("INSERT INTO facts VALUES (?,?,?)", ("taker_plan", "signal", json.dumps({"limit_price": ".51"})))
    if state == "TERMINAL" and filled != "0":
        db.execute("INSERT INTO taker_trade_evidence VALUES (?,?,?)", ("trade", "owned", "CONFIRMED"))
    db.commit()
    db.close()
    journal = root / "journal/rdp_offline.jsonl"
    journal.parent.mkdir()
    records = [
        ("sm_m4_signal", {"event": "CANDIDATE", "id": "candidate", "root": "signal", "price": ".50"}),
        ("sm_m4_signal", {"event": "OPPORTUNITY_IDENTIFIED", "opportunity": {"episode_id": "signal", "root_id": "signal"}}),
        ("sm_m4_native_post", {"order_id": "owned", "opportunity_id": "signal", "quote": quote, "elapsed_s": elapsed}),
        ("sm_m4_live_post", {"order_id": "owned", "outcome": "ACK"}),
    ]
    if duplicate:
        records.append(records[2])
    if extra_signal:
        records.append(("sm_m4_signal", {"event": "OPPORTUNITY_IDENTIFIED", "opportunity": {"episode_id": "lost", "root_id": "lost"}}))
    journal.write_text("".join(json.dumps({"ts": EPOCH+30, "kind": k, "data": {"payload": p}})+"\n" for k, p in records))
    return path, journal


@pytest.mark.parametrize("state", ["ACK", "UNKNOWN", "SETTLING"])
def test_ack_unknown_and_unconfirmed_matches_never_count_as_fills(tmp_path, state):
    path, journal = fixture(tmp_path, state=state)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    result = watch.inspect_market(tmp_path, EPOCH, EPOCH+320)
    assert result["confirmed_filled_signal_count"] == 0
    assert result["signals"][0]["unresolved_order_ids"] == ["owned"]
    assert result["native_posts"] == result["posted_signal_count"] == 1
    assert not result["alerts"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_confirmed_partial_fill_and_unallocated_remainder(tmp_path):
    fixture(tmp_path, state="TERMINAL", filled="3", spent="1.58244")
    result = watch.inspect_market(tmp_path, EPOCH, EPOCH+320)
    assert result["confirmed_filled_signal_count"] == 1
    assert result["signals"][0]["remaining_cash_usdc"] == "3.41756"
    assert result["trade_states"] == {"CONFIRMED": 1}
    assert not result["alerts"]


def test_signal_without_durable_task_is_visible_even_when_another_filled(tmp_path):
    fixture(tmp_path, state="TERMINAL", filled="8", spent="4.219944", extra_signal=True)
    result = watch.inspect_market(tmp_path, EPOCH, EPOCH+320)
    assert result["identified_signal_count"] == 2
    assert result["confirmed_filled_signal_count"] == 1
    assert result["signals_without_task"][0]["episode_id"] == "lost"
    assert "IDENTIFIED_SIGNAL_WITHOUT_TASK" in result["observations"]


@pytest.mark.parametrize("size,valid", [("1.9", False), ("2", True), ("5", True)])
def test_minimum_or_rule_allows_under_five_shares_only_above_one_dollar(tmp_path, size, valid):
    fixture(tmp_path, size=size)
    result = watch.inspect_market(tmp_path, EPOCH, EPOCH+320)
    assert ("POST_PRICE_SIZE_RULE_VIOLATION" not in result["alerts"]) == valid


@pytest.mark.parametrize("elapsed,flag", [(239.999, False), (240, True), (240.001, True)])
def test_t240_boundary(tmp_path, elapsed, flag):
    fixture(tmp_path, elapsed=elapsed)
    result = watch.inspect_market(tmp_path, EPOCH, EPOCH+320)
    assert ("POST_AT_OR_AFTER_T240" in result["alerts"]) == flag


def test_remaining_cash_budget_includes_prior_spend(tmp_path):
    fixture(tmp_path, prior="2", planned="4.2")
    assert "PLANNED_ALL_IN_BUDGET_OVER_LIMIT_REVIEW" in watch.inspect_market(tmp_path, EPOCH, EPOCH+320)["alerts"]


def test_duplicate_native_submission_and_torn_tail(tmp_path):
    _, journal = fixture(tmp_path, duplicate=True)
    with journal.open("a") as stream:
        stream.write('{"partially_written":')
    result = watch.inspect_market(tmp_path, EPOCH, EPOCH+320)
    assert "REPEATED_NATIVE_POST_ID_REVIEW_RESTART_EVIDENCE" in result["alerts"]
    assert "JOURNAL_PARSE_ERRORS" not in result["alerts"]


def test_native_guard_proven_unsent_is_not_a_real_post_or_response(tmp_path):
    path, _ = fixture(tmp_path, state="UNSENT")
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE taker_attempts ADD COLUMN proof TEXT")
        db.execute("UPDATE taker_attempts SET proof=?", (json.dumps({"kind": "LOCAL_NATIVE_POST_BLOCKED", "native_post_sent": False}),))
    result = watch.inspect_market(tmp_path, EPOCH, EPOCH+320)
    assert result["durable_post_intents"] == 1 and result["native_posts"] == 0
    assert result["posted_signal_count"] == 0 and result["responses"] == {}
    assert result["unsent_response_events"] == 1
    assert "NATIVE_POST_LEDGER_MISMATCH" not in result["alerts"]


@pytest.mark.parametrize("local_errors,posted,flag", [(219, False, True), (0, False, False), (2, True, False)])
def test_unsigned_failures_survive_diagnostics_tail_without_counting_as_posts(tmp_path, local_errors, posted, flag):
    path, journal = fixture(tmp_path, state="TERMINAL")
    if not posted:
        with sqlite3.connect(path) as db:
            db.execute("DELETE FROM taker_attempts")
            db.execute("UPDATE opportunities SET posts=0,reserved='0'")
        records = [r for r in journal.read_text().splitlines()
                   if json.loads(r)["kind"] == "sm_m4_signal"]
        journal.write_text("\n".join(records)+"\n")
    reason = "invalid tick size (0.001), minimum for the market is 0.01"
    with journal.open("a") as stream:
        for kind, payload, count in [
            ("sm_m4_live_post_error", {"order_id": None, "opportunity_id": "signal", "reason": reason}, local_errors),
            ("sm_m4_wait", {"reason": "M4_T240_RESIDUAL"}, 150),
        ]:
            for _ in range(count):
                stream.write(json.dumps({"ts": EPOCH+230, "kind": kind, "data": {"payload": payload}})+"\n")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    result = watch.inspect_market(tmp_path, EPOCH, EPOCH+320)
    assert result["native_posts"] == result["posted_signal_count"] == int(posted)
    assert result["confirmed_filled_signal_count"] == 0
    assert result["unsigned_preparation_failures"] == local_errors
    signal = result["signals"][0]
    assert signal["unsigned_preparation_failures"] == local_errors
    assert signal["unsigned_preparation_error_reasons"] == ({reason: local_errors} if local_errors else {})
    assert ("SIGNAL_UNPOSTED_WITH_LOCAL_PREPARATION_ERRORS" in result["alerts"]) == flag
    assert all(e["kind"] == "sm_m4_wait" for e in result["errors_and_waits"])
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_same_thread_delivery_coalesces_and_retains_review_fields(tmp_path):
    (tmp_path / "ops").mkdir()
    (tmp_path / "ops/m4_taker_watch_prompt.txt").write_text("read-only Taker review")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    watch.atomic_json(state_dir / "agent-state.json", {"custom": "keep", "reviewed_at": 0})
    calls = []
    def runner(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="queued", stderr="")
    report = {"bucket": 1, "report_path": "report1", "previous_market_epoch": EPOCH}
    assert watch.wake(tmp_path, state_dir, {"thread": "authorized-chat"}, report, runner)["status"] == "QUEUED_AND_RESUME_REQUESTED"
    assert watch.wake(tmp_path, state_dir, {"thread": "authorized-chat"}, report, runner)["status"] == "ALREADY_QUEUED"
    report.update(bucket=2, report_path="report2", previous_market_epoch=EPOCH+300)
    assert watch.wake(tmp_path, state_dir, {"thread": "authorized-chat"}, report, runner)["status"] == "COALESCED_WITH_PENDING_REVIEW"
    state = watch.read_json(state_dir / "agent-state.json")
    assert state["requested_report"] == "report2" and state["custom"] == "keep"
    assert len(calls) == 1 and calls[0][3] == "authorized-chat"


def test_incomplete_and_missing_session_are_never_marked_successful(tmp_path):
    path, _ = fixture(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE metadata SET value='false' WHERE key='complete'")
    assert "MARKET_CLOSE_NOT_COMPLETE" in watch.inspect_market(tmp_path, EPOCH, EPOCH+340)["alerts"]
    assert watch.inspect_market(tmp_path, EPOCH+300)["record_status"] == "NO_SESSION"


@pytest.mark.parametrize("added_during_collection", [False, True])
def test_observer_preserves_open_repair_issue_after_orders_are_resolved(tmp_path, monkeypatch, added_during_collection):
    state_dir = tmp_path / "research_outputs/m4_taker_watch"
    state_dir.mkdir(parents=True)
    issue = {"market_epoch": EPOCH, "status": "FIXED_OFFLINE_PENDING_USER_RESTART"}
    state = {"custom": "keep", "reviewed_through_epoch": EPOCH+300,
             "pending_market_epochs": [EPOCH], "open_issues": [] if added_during_collection else [issue]}
    watch.atomic_json(state_dir/"agent-state.json", state)
    watch.atomic_json(state_dir/"control.json", {"enabled": True})
    def collect(*args):
        if added_during_collection:
            state["open_issues"] = [issue]
            watch.atomic_json(state_dir/"agent-state.json", state)
        return {"observed_at": EPOCH+620, "alerts": [],
                "markets": [{"epoch": EPOCH, "record_status": "AVAILABLE", "complete": True, "signals": []}]}
    monkeypatch.setattr(watch, "collect", collect)
    monkeypatch.setattr(sys, "argv", ["watch", "--project-dir", str(tmp_path), "--no-wake"])
    watch.main()
    saved = watch.read_json(state_dir/"agent-state.json")
    assert saved["pending_market_epochs"] == [EPOCH]
    assert saved["custom"] == "keep" and saved["open_issues"] == [issue]
    assert saved["reviewed_through_epoch"] == EPOCH+300


def test_collect_includes_open_issue_even_if_pending_list_was_already_lost(tmp_path, monkeypatch):
    state_dir = tmp_path/"state"
    state_dir.mkdir()
    watch.atomic_json(state_dir/"agent-state.json", {
        "reviewed_through_epoch": EPOCH+900, "pending_market_epochs": [EPOCH+300],
        "open_issues": [{"market_epoch": EPOCH, "status": "FIXED_OFFLINE_PENDING_USER_RESTART"},
                        {"market_epoch": EPOCH-300, "status": "RESOLVED"}],
    })
    seen = []
    def inspect(project, epoch, now):
        seen.append(epoch)
        return {"epoch": epoch}
    monkeypatch.setattr(watch, "inspect_market", inspect)
    monkeypatch.setattr(watch, "service_status", lambda: {"ActiveState": "active"})
    watch.collect(tmp_path, state_dir, {"start_epoch": EPOCH+600}, EPOCH+1200)
    assert seen == [EPOCH, EPOCH+300, EPOCH+900, EPOCH+1200]


def test_pending_merge_retains_failed_reads_and_orders_but_clears_verified_resolution():
    state = {"pending_market_epochs": [EPOCH, EPOCH+300],
             "open_issues": [{"market_epoch": EPOCH+600, "status": "LOADED_AWAITING_LIVE_SIGNAL"}]}
    markets = [{"epoch": EPOCH, "record_status": "AVAILABLE", "signals": []},
               {"epoch": EPOCH+300, "record_status": "READ_ERROR"},
               {"epoch": EPOCH+900, "record_status": "AVAILABLE", "signals": [{"unresolved_order_ids": ["owned"]}]}]
    assert watch.pending_epochs(state, markets) == [EPOCH+300, EPOCH+600, EPOCH+900]
