"""Instant fills are an independent, restart-safe cohort, never exchange-fill evidence."""

import json
import sqlite3
from decimal import Decimal

import pytest
from ops.m1234_sim_instant_fill import InstantFill


def fixture(tmp_path):
    run = tmp_path / "source"
    market = run / "btc-updown-5m-1000"
    market.mkdir(parents=True)
    owner = {"run_id": "test-run", "model": "test-model"}
    (run / "plan.json").write_text(
        json.dumps(
            {
                "run_identity": owner,
                "mode": "SIMULATION_ONLY",
                "real_order_transport": "ABSENT",
                "started_utc_epoch": 1000,
                "max_seconds": 87300,
            }
        )
    )
    binding = {"database_id": "database-1", "market": market.name}
    tracker = market / "database-1.validity.json"
    tracker.write_text(
        json.dumps({"evidence_schema": 1, "binding": binding, "validity": {"status": "TRACKING"}})
    )
    path = market / "simulation.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript(
            "CREATE TABLE audit(seq INTEGER PRIMARY KEY,input_seq INT,kind TEXT,payload TEXT);"
            "CREATE TABLE metadata(name TEXT PRIMARY KEY,payload TEXT);"
        )
        db.executemany(
            "INSERT INTO metadata VALUES(?,?)",
            [("identity", json.dumps(binding)), ("run_owner", json.dumps(owner))],
        )
    append(
        path,
        [
            (
                "DEFINITION",
                {
                    "definition": {
                        "market": {
                            "slug": market.name,
                            "condition_id": "condition",
                            "tokens": [{"token_id": "yes-token"}, {"token_id": "no-token"}],
                        }
                    }
                },
            )
        ]
        + [
            (
                "ACCOUNT_OPEN",
                {"profile": s, "scenario": "trades-only", "initial_cash": "20", "at": 1},
            )
            for s in ("M2", "M3", "M4")
        ],
    )
    return run, path, tracker, tmp_path / "baseline"


def append(path, rows):
    with sqlite3.connect(path) as db:
        db.executemany(
            "INSERT INTO audit(input_seq,kind,payload) VALUES(1,?,?)",
            [(k, json.dumps(p)) for k, p in rows],
        )


def submit(
    scenario="trades-only", strategy="M4", oid="order", price=".4", quantity="10", outcome="YES"
):
    return "SUBMIT", {
        "order": oid,
        "scenario": scenario,
        "at": 123.5,
        "reserve": str(Decimal(price) * Decimal(quantity)),
        "intent": {
            "strategy": strategy,
            "profile": strategy,
            "market": "btc-updown-5m-1000",
            "side": "BUY",
            "outcome": outcome,
            "token": "yes-token" if outcome == "YES" else "no-token",
            "price": price,
            "quantity": quantity,
            "available": 123.5,
            "opportunity": "candidate-1",
        },
    }


def candidate(strategy="M4", key="candidate-1"):
    return "SIGNAL", {
        "strategy": strategy,
        "event": "M2_CANDIDATE" if strategy == "M2" else "CANDIDATE",
        "payload": {"generation": 1, "candidate_id": key}
        if strategy == "M2"
        else {"generation": 1, "id": key},
    }


def resolution(winner="YES", slug="btc-updown-5m-1000"):
    return "OFFICIAL_RESULT", {
        "raw": {
            "slug": slug,
            "conditionId": "condition",
            "clobTokenIds": ["yes-token", "no-token"],
            "closed": True,
            "umaResolutionStatus": "resolved",
            "outcomePrices": ["1", "0"] if winner == "YES" else ["0", "1"],
        },
        "result": {"winner": winner, "observed_at": 1400},
    }


def read_accounts(report, strategy="M4"):
    return next(x for x in report["accounts"] if x["strategy"] == strategy)


def test_fill_at_submit_ignore_queue_fate_and_do_not_double_count_scenarios(tmp_path):
    run, path, _, out = fixture(tmp_path)
    append(
        path,
        [
            candidate(),
            candidate(),
            submit(),
            submit("cancel-half"),
            ("REJECT", {"scenario": "trades-only", "order": "order"}),
            ("CANCEL_EFFECTIVE", {"scenario": "cancel-half", "order": "order"}),
            ("MODELLED_FILL", {"scenario": "cancel-half", "order": "order", "quantity": "2"}),
            ("UNKNOWN_TAIL", {"scenario": "cancel-half", "order": "order"}),
        ],
    )
    ledger = InstantFill(run, out)
    try:
        r = ledger.refresh()
        a = read_accounts(r)
        assert r["status"] == "OK"
        assert r["strategies"]["M4"]["signals"] == 1
        assert r["strategies"]["M4"]["assumed_filled_orders"] == 1
        assert r["strategies"]["M4"]["pending_settlement_orders"] == 1
        assert a["cash"] == "16.0" and Decimal(a["spent"]) == 4 and a["reserved"] == "0"
        assert a["gross_pnl"] is None and a["inventory"]["YES"] == "10"
        row = json.loads(
            ledger.db.execute("SELECT payload FROM audit WHERE kind='ASSUMED_FILL'").fetchone()[0]
        )
        assert row["fill_at"] == row["submit_at"] == 123.5 and row["wait_seconds"] == 0
        assert row["filled"] == "10" and row["evidence"] == "ASSUMED_FILL"
        assert not ledger.db.execute("SELECT 1 FROM audit WHERE kind='MODELLED_FILL'").fetchone()
        q = r["execution_comparison"]["M4"]
        assert q["trades-only"]["filled_orders"] == 0
        assert q["cancel-half"]["filled_shares"] == "2"
        assert q["cancel-half"]["unresolved_orders"] == 1
        assert q["cancel-half"]["settled_gross_pnl_usdc"] is None
    finally:
        ledger.close()


def test_committed_only_restart_and_partial_fills_do_not_duplicate_baseline(tmp_path):
    run, path, _, out = fixture(tmp_path)
    append(path, [candidate(), submit()])
    ledger = InstantFill(run, out)
    first = ledger.refresh()
    count = ledger.db.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
    with sqlite3.connect(path) as writer:
        writer.execute(
            "INSERT INTO audit(input_seq,kind,payload) VALUES(2,?,?)",
            ("SIGNAL", json.dumps(candidate(key="next")[1])),
        )
        assert ledger.refresh()["strategies"] == first["strategies"]
    assert ledger.refresh()["strategies"]["M4"]["signals"] == 2
    ledger.close()
    append(
        path,
        [("MODELLED_FILL", {"scenario": "trades-only", "order": "order", "quantity": "1"})] * 2,
    )
    ledger = InstantFill(run, out)
    try:
        r = ledger.refresh()
        assert r["strategies"]["M4"]["assumed_filled_orders"] == 1
        assert r["execution_comparison"]["M4"]["trades-only"]["filled_orders"] == 1
        assert ledger.db.execute("SELECT COUNT(*) FROM audit").fetchone()[0] == count
    finally:
        ledger.close()


@pytest.mark.parametrize(
    ("winner", "pnl", "cash"), [("YES", "6.0", "26.0"), ("NO", "-4.0", "16.0")]
)
def test_official_settlement_preserves_money_and_pending_result(tmp_path, winner, pnl, cash):
    run, path, _, out = fixture(tmp_path)
    append(path, [submit(), ("OFFICIAL_RESULT", {"raw": None, "result": None})])
    ledger = InstantFill(run, out)
    try:
        assert ledger.refresh()["strategies"]["M4"]["pending_settlement_orders"] == 1
        append(path, [resolution(winner)])
        r = ledger.refresh()
        a = read_accounts(r)
        assert a["gross_pnl"] == pnl and a["cash"] == cash
        assert r["strategies"]["M4"]["settled_orders"] == 1
        assert r["strategies"]["M4"]["pending_settlement_orders"] == 0
        append(path, [resolution(winner)])
        assert ledger.refresh()["strategies"] == r["strategies"]
        assert (
            ledger.db.execute(
                "SELECT COUNT(*) FROM audit WHERE kind='OFFICIAL_SETTLEMENT'"
            ).fetchone()[0]
            == 1
        )
    finally:
        ledger.close()


def test_late_invalidity_excludes_profit_but_preserves_fill_evidence(tmp_path):
    run, path, tracker, out = fixture(tmp_path)
    append(path, [submit(), resolution()])
    ledger = InstantFill(run, out)
    try:
        assert ledger.refresh()["strategies"]["M4"]["settled_orders"] == 1
        record = json.loads(tracker.read_text())
        record["validity"]["status"] = "INVALID"
        tracker.write_text(json.dumps(record))
        r = ledger.refresh()
        assert r["status"] == "PARTIAL" and r["excluded_invalid_markets"] == [path.parent.name]
        assert r["strategies"]["M4"]["assumed_filled_orders"] == 0
        assert (
            ledger.db.execute("SELECT COUNT(*) FROM audit WHERE kind='ASSUMED_FILL'").fetchone()[0]
            == 1
        )
        tracker.unlink()
        assert ledger.refresh()["errors"]
    finally:
        ledger.close()


@pytest.mark.parametrize(
    "fault",
    [
        "foreign_owner",
        "foreign_settlement",
        "overbudget",
        "unjoined_fill",
        "wrong_token",
        "replaced_database",
    ],
)
def test_invalid_source_is_explicit_partial_and_transaction_rolls_back(tmp_path, fault):
    run, path, _, out = fixture(tmp_path)
    ledger = InstantFill(run, out)
    try:
        ledger.refresh()
        if fault == "foreign_owner":
            with sqlite3.connect(path) as db:
                db.execute(
                    "UPDATE metadata SET payload=? WHERE name='run_owner'",
                    (json.dumps({"run_id": "other"}),),
                )
        elif fault == "foreign_settlement":
            append(path, [submit(), resolution(slug="another-market")])
        elif fault == "overbudget":
            append(path, [submit(quantity="100")])
        elif fault == "wrong_token":
            row = submit()
            row[1]["intent"]["token"] = "no-token"
            append(path, [row])
        elif fault == "unjoined_fill":
            append(
                path,
                [
                    (
                        "MODELLED_FILL",
                        {"scenario": "trades-only", "order": "missing", "quantity": "1"},
                    )
                ],
            )
        else:
            with sqlite3.connect(path) as db:
                db.execute(
                    "UPDATE metadata SET payload=? WHERE name='identity'",
                    (json.dumps({"database_id": "replaced"}),),
                )
        r = ledger.refresh()
        assert r["status"] == "PARTIAL" and r["errors"]
        assert not ledger.db.execute("SELECT 1 FROM audit WHERE kind='ASSUMED_FILL'").fetchone()
    finally:
        ledger.close()


def test_unadmitted_candidates_are_not_filled_and_strategy_accounts_are_separate(tmp_path):
    run, path, _, out = fixture(tmp_path)
    append(
        path,
        [
            candidate(),
            candidate(key="not-admitted"),
            submit(),
            submit(strategy="M2", oid="m2", price=".2", quantity="25"),
            submit(strategy="M3", oid="m3", price=".5", quantity="10"),
        ],
    )
    ledger = InstantFill(run, out)
    try:
        r = ledger.refresh()
        assert (
            r["strategies"]["M4"]["signals"] == 2 and r["strategies"]["M4"]["submitted_orders"] == 1
        )
        assert all(r["strategies"][s]["assumed_filled_orders"] == 1 for s in ("M2", "M3", "M4"))
        assert [Decimal(x["spent"]) for x in r["accounts"]] == [5, 5, 4]
        with pytest.raises(ValueError, match="ALREADY_RUNNING"):
            InstantFill(run, out)
    finally:
        ledger.close()
    with pytest.raises(ValueError, match="IDENTITY_MISMATCH"):
        InstantFill(run, out, "cancel-half")


def test_empty_missing_market_and_source_terminal_are_not_successful_zero(tmp_path):
    run, path, tracker, out = fixture(tmp_path)
    ledger = InstantFill(run, out)
    try:
        ledger.refresh()
        path.rename(path.with_suffix(".removed"))
        r = ledger.refresh()
        assert r["status"] == "PARTIAL" and r["errors"][0]["error"] == "SOURCE_DATABASE_MISSING"
        path.with_suffix(".removed").rename(path)
        owner = json.loads((run / "plan.json").read_text())["run_identity"]
        (run / "completion.json").write_text(
            json.dumps(
                {
                    "run_identity": owner,
                    "status": "FAIL",
                    "reason": "FAILED",
                    "invalid_markets": [path.parent.name],
                }
            )
        )
        r = ledger.refresh()
        assert r["status"] == "PARTIAL"
        assert r["excluded_invalid_markets"] == [path.parent.name]
    finally:
        ledger.close()


def test_new_market_reuses_order_ids_without_cross_market_money_or_deduplication(tmp_path):
    run, path, _, out = fixture(tmp_path)
    append(path, [candidate(), submit(), resolution()])
    # A second market may reuse order/candidate IDs; its cash and outcome are independent.
    other_run, other_path, tracker, _ = fixture(tmp_path / "other")
    new_slug = "btc-updown-5m-1300"
    with sqlite3.connect(other_path) as db:
        for seq, payload in db.execute("SELECT seq,payload FROM audit").fetchall():
            db.execute(
                "UPDATE audit SET payload=? WHERE seq=?",
                (payload.replace(path.parent.name, new_slug), seq),
            )
        binding = {"database_id": "database-1", "market": new_slug}
        db.execute("UPDATE metadata SET payload=? WHERE name='identity'", (json.dumps(binding),))
    record = json.loads(tracker.read_text())
    record["binding"] = binding
    tracker.write_text(json.dumps(record))
    row = submit()
    row[1]["intent"]["market"] = new_slug
    append(other_path, [candidate(), row, resolution("NO", new_slug)])
    other_path.parent.rename(run / new_slug)
    ledger = InstantFill(run, out)
    try:
        r = ledger.refresh()
        assert r["status"] == "OK"
        m4 = r["strategies"]["M4"]
        assert m4["signals"] == m4["assumed_filled_orders"] == 2
        assert Decimal(m4["settled_gross_pnl_usdc"]) == 2
        assert [Decimal(a["cash"]) for a in r["accounts"] if a["strategy"] == "M4"] == [26, 16]
    finally:
        ledger.close()
