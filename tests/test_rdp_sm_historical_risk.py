"""Legacy history variations cannot gate an independent Session."""

import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from polymaker.rdp_sm_entry import run_current_market
from polymaker.rdp_sm_execution import RiskJournal, SMWindowExecutor
from polymaker.rdp_sm_loop import RunResult
from test_rdp_sm_entry import boundary, history_boundary, old_trade
from test_rdp_sm_entry import env as entry_env
from test_rdp_sm_m1 import ADDRESS, CID, SLUG
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_m3_loop import on_windows

RECORDED_AT = 1_791_789_000


@pytest.fixture
def env(monkeypatch, tmp_path):
    yield from entry_env.__wrapped__(monkeypatch, tmp_path)


def safe_records(sid="78f615c16be44892874bb33b30e44fd5", *, retained=False, offset=0):
    # Equivalent to the diagnosed complete triplet. Only public dummy account,
    # market and timestamps replace private runtime context; no counters alone.
    costs = ("3.250", "3.50", "3.400") if retained else ()
    history = {
        f"historical-{i}": {"status": "MATCHED", "decision": "HISTORICAL_MATCHED_NONBLOCKING",
                            "pending_cost_reserved_usd": cost, "redeem": "NOT_VERIFIED"}
        for i, cost in enumerate(costs)
    }
    reserve = sum(map(Decimal, costs), Decimal(0))
    session = {
        "session_id": sid, "slug": SLUG, "signer": ADDRESS, "funder": ADDRESS,
        "signature_type": 0, "live_authority": "LOCAL_EOA_KEY_VERIFIED",
        "starting_spendable_usdc": float(Decimal("36.064169") - reserve),
        "starting_official_balance_usdc": 36.064169, "test_capital_limit_usdc": "30",
        "historical_pending_cost_reserved_usd": float(reserve), "historical_matched": history,
        "preflight_observed_order_ids": [], "preflight_observed_trade_ids": [],
        "preflight_account_incomplete": None,
    }
    result = RunResult(
        CID, sid, start_time=RECORDED_AT + offset, stop_time=RECORDED_AT + offset + 3,
        stop_reason="BOOK_SIDE_MISSING", final_close_barrier="SAFE_TO_CONTINUE",
        live_authority="LOCAL_EOA_KEY_VERIFIED",
    ).to_dict()
    return [
        {"ts": RECORDED_AT + offset + i + 1, "kind": kind,
         "data": {"session_id": sid, "payload": payload}}
        for i, (kind, payload) in enumerate([
            ("sm_session", session),
            ("sm_close_barrier", {"result": "SAFE_TO_CONTINUE", "proofs": {}, "confirmed_fills": []}),
            ("sm_run_result", result),
        ])
    ]


def write_records(directory, records):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"rdp_sm_{ADDRESS.lower()}.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    return path


def assert_history_ignored(directory, path):
    # These legacy history fixtures now prove isolation, including malformed
    # or unfinished previous runs. No prior file is read or appended.
    before = path.read_bytes()
    journal = RiskJournal(directory, ADDRESS)
    try:
        assert Path(journal.path) != path and journal.sequence == 0
    finally:
        journal.close()
    assert path.read_bytes() == before


def test_t01_equivalent_actual_history_nonblocking(tmp_path):
    path = write_records(tmp_path, safe_records(retained=True))
    assert_history_ignored(tmp_path, path)


def test_t02_multiple_complete_sessions_and_append_preserves_bytes(tmp_path):
    path = write_records(tmp_path, safe_records("first", retained=True) + safe_records("second", offset=5))
    before = path.read_bytes()
    for sid in ("third", "fourth"):
        journal = RiskJournal(tmp_path, ADDRESS, session_id=sid)
        try:
            assert journal.sequence == 0
            for row in safe_records(sid, offset=10):
                journal.write(row["kind"], row["data"], row["ts"])
            assert journal.sequence == 3
        finally:
            journal.close()
    assert path.read_bytes() == before


def test_t02_existing_float_budget_rounding_does_not_create_false_risk(tmp_path):
    rows = safe_records(retained=True)
    rows[0]["data"]["payload"]["starting_spendable_usdc"] = 1.57 - 1.2
    assert_history_ignored(tmp_path, write_records(tmp_path, rows))


@pytest.mark.parametrize("kind,payload", [
    ("sm_placement_intent", {"outcome": "INTENT", "post_started": False}),  # T08
    ("sm_placement_ack", {"outcome": "ACK", "post_started": True, "order_id": "unconfirmed"}),  # T03
    ("sm_placement_unknown", {"outcome": "UNKNOWN", "post_started": True}),  # T04/T07
    ("sm_placement_result", {"outcome": "REJECTED", "post_started": True}),  # T08: no recovery
    ("sm_trade", {"status": "MATCHED", "size": 5}),  # T05
    ("sm_trade", {"status": "CONFIRMED", "size": 5}),
    ("sm_order", {"status": "LIVE", "order_id": "pending"}),
    ("sm_window_result", {"capital_committed": 2.5, "close_barrier": "BLOCKED"}),
    ("sm_unknown_official_lookup", {"resolution": "unknown"}),
    ("unexpected_kind", {}),
])
def test_t03_t04_t05_t07_t08_prior_attempts_are_not_session_inputs(tmp_path, kind, payload):
    records = safe_records()
    records.insert(1, {"ts": records[0]["ts"], "kind": kind,
                       "data": {"session_id": records[0]["data"]["session_id"], "payload": payload}})
    path = write_records(tmp_path, records)
    assert_history_ignored(tmp_path, path)


@pytest.mark.parametrize("record,key,value", [
    (0, "preflight_observed_order_ids", ["external-order"]),
    (0, "preflight_observed_trade_ids", ["pending-trade"]),
    (0, "preflight_account_incomplete", "TRADES_PAGINATION_INCOMPLETE"),
    (0, "funder", "0x" + "02" * 20),
    (0, "session_id", "conflict"),
    (0, "starting_spendable_usdc", 100),
    (0, "starting_official_balance_usdc", -1),
    (0, "test_capital_limit_usdc", "31"),
    (0, "signature_type", True),
    (1, "result", "BLOCKED"),
    (1, "proofs", {"order": {"status": "UNKNOWN"}}),
    (1, "confirmed_fills", [{"status": "MATCHED"}]),
    (1, "capital_committed", "2.5"),
    (2, "session_id", "conflict"),
    (2, "confirmed_fills", 1),
    (2, "confirmed_yes", 5),
    (2, "delta", True),
    (2, "total_windows", 1),
    (2, "total_windows", False),
    (2, "windows", [{"placement_results": [{"outcome": "UNKNOWN"}]}]),
    (2, "skipped_quotes", [{"reason": "unknown"}]),
    (2, "average_yes_cost", 0.5),
    (2, "final_close_barrier", "BLOCKED"),
    (2, "execution_status", "INCOMPLETE"),
    (2, "stop_reason", "UNKNOWN_PLACEMENT_EXPOSURE"),
    (2, "market_id", None),
    (2, "stop_time", RECORDED_AT),
    (2, "capital_committed", 2.5),
    (2, "submitted_orders", 0),  # an extra counter cannot override event semantics
])
def test_t05_t06_t07_prior_conflicting_states_and_unknown_fields_are_ignored(tmp_path, record, key, value):
    records = safe_records()
    records[record]["data"]["payload"][key] = value
    path = write_records(tmp_path, records)
    assert_history_ignored(tmp_path, path)


@pytest.mark.parametrize("fault", [
    "empty_line", "bad_json", "missing_newline", "partial_session", "missing_session",
    "missing_close", "missing_result", "reversed", "duplicate_session", "wrong_wrapper_sid",
    "missing_field", "duplicate_json_key", "nan", "infinity", "bad_utf8", "backward_time",
    "extra_envelope_field", "nonobject", "bad_payload",
])
def test_t06_corrupt_or_incomplete_prior_history_is_ignored(tmp_path, fault):
    records = safe_records()
    if fault == "missing_session":
        records = records[1:]
    if fault == "missing_close":
        records.pop(1)
    if fault in {"missing_result", "partial_session"}:
        records = records[:1 if fault == "partial_session" else 2]
    if fault == "reversed":
        records.reverse()
    if fault == "duplicate_session":
        records += safe_records(offset=5)
    if fault == "wrong_wrapper_sid":
        records[1]["data"]["session_id"] = "wrong"
    if fault == "missing_field":
        del records[0]["data"]["payload"]["preflight_observed_trade_ids"]
    if fault == "backward_time":
        records[1]["ts"] = RECORDED_AT - 10
    if fault == "extra_envelope_field":
        records[0]["orders"] = ["unknown"]
    if fault == "nonobject":
        records[0] = []
    if fault == "bad_payload":
        records[0]["data"]["payload"] = None
    path = write_records(tmp_path, records)
    raw = path.read_bytes()
    if fault == "empty_line":
        raw = b"\n"
    if fault == "bad_json":
        raw += b"{broken\n"
    if fault == "missing_newline":
        raw = raw.rstrip(b"\n")
    if fault == "duplicate_json_key":
        raw = raw.replace(b'"proofs": {}', b'"proofs": {"order": "UNKNOWN"}, "proofs": {}')
    if fault in {"nan", "infinity"}:
        raw = raw.replace(b'"confirmed_yes": 0', b'"confirmed_yes": ' +
                          (b"NaN" if fault == "nan" else b"Infinity"))
    if fault == "bad_utf8":
        raw += b"\xff\n"
    path.write_bytes(raw)
    assert_history_ignored(tmp_path, path)


def test_t09_mixed_prior_sessions_do_not_gate_new_session(tmp_path):
    records = safe_records("safe") + safe_records("risky", offset=5)
    records[-1]["data"]["payload"]["confirmed_fills"] = 1
    path = write_records(tmp_path, records)
    assert_history_ignored(tmp_path, path)


@pytest.mark.parametrize("conflict", ["market", "slug", "signer"])
def test_t09_prior_identity_conflicts_are_not_imported(tmp_path, conflict):
    records = safe_records("first") + safe_records("second", offset=5)
    if conflict == "market":
        records[-1]["data"]["payload"]["market_id"] = "0x" + "ef" * 32
    if conflict == "slug":
        records[3]["data"]["payload"]["slug"] = "btc-updown-5m-1800000300"
    if conflict == "signer":
        records[3]["data"]["payload"]["signer"] = "0x" + "02" * 20
    path = write_records(tmp_path, records)
    assert_history_ignored(tmp_path, path)


@pytest.mark.parametrize("fault", ["missing", "cost_changed", "bad_total", "redeemed", "confirmed", "bad_cost"])
def test_t10_old_reserve_metadata_is_not_imported(tmp_path, fault):
    records = safe_records("first", retained=True) + safe_records("second", retained=True, offset=5)
    session = records[3]["data"]["payload"]
    evidence = session["historical_matched"]["historical-0"]
    if fault == "missing":
        session["historical_matched"] = {}
        session["historical_pending_cost_reserved_usd"] = 0
    if fault == "cost_changed":
        evidence["pending_cost_reserved_usd"] = "3.00"
        session["historical_pending_cost_reserved_usd"] = 9.90
    if fault == "bad_total":
        session["historical_pending_cost_reserved_usd"] = 0
    if fault == "redeemed":
        evidence["redeem"] = "REDEEM_VERIFIED"
    if fault == "confirmed":
        evidence["status"] = "CONFIRMED"
    if fault == "bad_cost":
        evidence["pending_cost_reserved_usd"] = "NaN"
    path = write_records(tmp_path, records)
    assert_history_ignored(tmp_path, path)


def historical_trades(v):
    rows = []
    for i, price in enumerate((".65", ".70", ".68")):
        row = old_trade(v)
        row["id"] = f"historical-{i}"
        row["maker_orders"][0].update(order_id=f"historical-order-{i}", price=price, matched_amount="5")
        rows.append(row)
    return rows


async def test_t01_production_composition_reopens_safe_history_zero_posts(env, monkeypatch):
    v, cfg = env
    path = write_records(cfg.paths.journal_dir, safe_records())
    before = path.read_bytes()
    calls = [0]

    def missing_book_after_m1(request):
        if request.url.path == "/book":
            calls[0] += 1
            if calls[0] >= 7 and request.url.params["token_id"] == "1":
                # Persistent failure keeps this zero-POST history test scoped
                # to admission and safe close, while the loop now retries.
                book = deepcopy(v.books["1"])
                book["bids"] = []
                return httpx.Response(200, json=book, request=request)
        return None

    with boundary(monkeypatch, v, missing_book_after_m1):
        result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    assert result.stop_reason == "BOOK_SIDE_MISSING", result.stop_reason
    assert result.stop_elapsed_s == 300
    assert result.final_close_barrier == "SAFE_TO_CONTINUE"
    assert result.total_windows == result.confirmed_fills == 0
    assert not v.posts and not v.cancels
    assert path.read_bytes().startswith(before)
    assert len(journal_records(path)) == 3
    again = RiskJournal(cfg.paths.journal_dir, ADDRESS)
    assert again.sequence == 0
    again.close()


async def test_t10_historical_evidence_is_not_imported_into_m1_m2(env, monkeypatch):
    v, cfg = env
    v.capital["balance"] = "36064169"
    v.trades = historical_trades(v)
    path = write_records(cfg.paths.journal_dir, safe_records(retained=True))
    before = path.read_bytes()
    snapshots = []
    real_snapshot = SMWindowExecutor._snapshot

    async def observe_snapshot(executor, **kwargs):
        snapshot = await real_snapshot(executor, **kwargs)
        snapshots.append(dict(snapshot[3]))
        return snapshot

    monkeypatch.setattr(SMWindowExecutor, "_snapshot", observe_snapshot)
    on_windows(v, lambda *_: None, stop_after=1)
    with history_boundary(monkeypatch, v)[0]:
        result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert result.final_close_barrier == "SAFE_TO_CONTINUE" and len(v.posts) == 2
    assert all(row["status"] == "MATCHED" for row in v.trades)
    assert result.confirmed_fills == result.confirmed_yes == result.confirmed_no == 0
    assert path.read_bytes().startswith(before)
    session = [r["data"]["payload"] for r in journal_records(v.executor.journal.path) if r["kind"] == "sm_session"][-1]
    assert session["starting_official_balance_usdc"] == 36.064169
    assert session["historical_pending_cost_reserved_usd"] == 0
    assert session["historical_pending_cost_observed_usd"] == 0
    assert session["capital_basis"] == "OFFICIAL_BALANCE_AT_SESSION_START"
    assert session["starting_spendable_usdc"] == 36.064169
    assert session["historical_matched"] == {}
    assert all(s["historical_pending_cost_reserved_usd"] == 0 for s in snapshots)
    assert all(s["historical_pending_cost_observed_usd"] == 0 for s in snapshots)
    account = snapshots[-1]
    assert account["historical_pending_cost_reserved_usd"] == 0
    assert account["execution_reserved_usd"] == 0
    assert account["basic_spendable_budget_usd"] == 15
    reopened = RiskJournal(cfg.paths.journal_dir, ADDRESS)
    assert reopened.sequence == 0
    reopened.close()
    w = result.windows[0]["result"]
    assert w["fill_deadline_monotonic"] - w["t0_monotonic"] == 3


@pytest.mark.parametrize("fault", ["missing", "confirmed", "changed", "partial"])
async def test_t10_starting_balance_does_not_depend_on_old_trade_snapshot(env, monkeypatch, fault):
    v, cfg = env
    v.trades = historical_trades(v)
    if fault == "missing":
        v.trades = []
    if fault == "confirmed":
        for row in v.trades:
            row["status"] = "CONFIRMED"
    if fault == "changed":
        v.trades[0]["maker_orders"][0]["price"] = ".60"
    if fault == "partial":
        v.trades.pop()
    path = write_records(cfg.paths.journal_dir, safe_records(retained=True))
    before = path.read_bytes()
    on_windows(v, lambda *_: None, stop_after=1)
    with history_boundary(monkeypatch, v)[0]:
        result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert len(v.posts) == 2 and result.confirmed_fills == 0
    assert path.read_bytes().startswith(before)
    session = [r["data"]["payload"] for r in journal_records(v.executor.journal.path) if r["kind"] == "sm_session"][-1]
    assert session["historical_matched"] == {}
    assert session["historical_pending_cost_reserved_usd"] == 0


async def test_t10_library_startup_has_no_retained_reserve_dependency(env):
    v, cfg = env
    path = write_records(cfg.paths.journal_dir, safe_records(retained=True))
    before = path.read_bytes()
    ex = await SMWindowExecutor.open(cfg, SLUG, "new-session", sleep=v.sleep, monotonic=lambda: v.mono)
    try:
        assert ex.confirmed() == [] and ex.committed() == 0
        assert Path(ex.journal.path) != path and path.read_bytes() == before
    finally:
        ex.close()


async def test_reused_session_id_cannot_append_conflicting_history(env):
    v, cfg = env
    sid = "78f615c16be44892874bb33b30e44fd5"
    path = write_records(cfg.paths.journal_dir, safe_records(sid))
    before = path.read_bytes()
    ex = await SMWindowExecutor.open(cfg, SLUG, sid, sleep=v.sleep, monotonic=lambda: v.mono)
    try:
        assert Path(ex.journal.path) != path and path.read_bytes() == before
    finally:
        ex.close()
    with pytest.raises(FileExistsError):
        RiskJournal(cfg.paths.journal_dir, ADDRESS, session_id=sid)


def test_each_journal_excludes_duplicate_session_writer_only(tmp_path):
    first = RiskJournal(tmp_path, ADDRESS, session_id="first")
    second = RiskJournal(tmp_path, ADDRESS, session_id="second")
    try:
        with pytest.raises(FileExistsError):
            RiskJournal(tmp_path, ADDRESS, session_id="first")
        assert second.path != first.path
    finally:
        first.close()
        second.close()


def test_completed_zero_attempt_cutoff_is_nonblocking(tmp_path):
    records = safe_records()
    records[-1]["data"]["payload"].update(stop_reason="MARKET_CUTOFF", execution_status="COMPLETED")
    path = write_records(tmp_path, records)
    assert_history_ignored(tmp_path, path)
    assert len(journal_records(path)) == 3


def test_duplicate_keys_in_prior_journal_are_never_parsed(tmp_path):
    path = write_records(tmp_path, [])
    path.write_text('{"data": {"session_id": "s", "session_id": "other"}}\n')
    assert_history_ignored(tmp_path, path)
