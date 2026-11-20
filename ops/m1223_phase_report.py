"""Source-bound end-of-phase evidence for the current single-SOP real-time run."""

import argparse
import csv
import hashlib
import json
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from m1223_gates import frequency_reporting_contract, strategy_schema, validate_cohort
from m1223_prepare import write
from m1223_report import market_result, summary


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_binding(run, cohort_path):
    plan = json.loads((run / "plan.json").read_text())
    cohort = json.loads(cohort_path.read_text())
    validate_cohort(cohort, plan["settings"])
    target = frequency_reporting_contract(cohort["stage"], len(cohort["markets"]))
    if cohort.get("frequency_reporting_contract", target) != target:
        raise ValueError("SOP_REPORTING_TARGET_DIFFERS_FROM_PHASE")
    if (
        plan.get("mode") != "SIMULATION_ONLY"
        or plan.get("real_order_transport") != "ABSENT"
        or plan["settings"].get("sop_cohort_sha256") != sha256(cohort_path)
        or plan["settings"].get("sop_cohort") != cohort
        or plan["settings"].get("sop", {}).get("groups") != [cohort["strategy"]]
    ):
        raise ValueError("REAL_PUBLIC_CAPTURE_AND_MATCHING_COHORT_REQUIRED")
    return plan, cohort


def read_definition(database):
    """Inspect the actual audit, rather than accepting a report's source label."""
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=1) as db:
        db.execute("PRAGMA query_only=ON")
        row = db.execute("SELECT payload FROM audit WHERE kind='DEFINITION'").fetchone()
    if row is None:
        raise ValueError("MARKET_DEFINITION_UNAVAILABLE")
    definition = json.loads(row[0])["definition"]
    if definition.get("artificial_fixture"):
        raise ValueError("SYNTHETIC_FIXTURE_IS_NOT_REALTIME_VALIDATION")
    return definition


def collect(run, cohort_path, *, project):
    from polymaker.m1234_sim.runstate import inspect_run

    plan, cohort = source_binding(run, cohort_path)
    names = cohort["markets"]
    actual = {p.name for p in run.glob("btc-*") if p.is_dir()}
    if actual - set(names):
        raise ValueError("UNDECLARED_MARKET_IN_PHASE_NO_MIXING")
    inspection = inspect_run(run)
    terminal = (
        json.loads((run / "completion.json").read_text())
        if (run / "completion.json").exists()
        else None
    )
    markets, orders, errors = [], [], []
    for market in names:
        db_path = run / market / "simulation.sqlite3"
        if db_path.is_file():
            try:
                definition = read_definition(db_path)
                if definition["settings"] != plan["settings"]:
                    raise ValueError("MARKET_SETTINGS_DIFFER_FROM_FROZEN_RUN")
                m, o = market_result(db_path, project)
                if m.get("market") != market or "audit_validity" not in m:
                    raise ValueError("MARKET_AUDIT_MISSING_OR_WRONG_IDENTITY")
                m["database_sha256"] = sha256(db_path)
                markets.append(m)
                orders.extend(o)
                continue
            except (ValueError, sqlite3.Error, KeyError, TypeError) as exc:
                errors.append(
                    {"market": market, "error_type": type(exc).__name__, "error": str(exc)}
                )
        markets.append(
            {
                "market": market,
                "status": "UNAVAILABLE",
                "signal_ids": [],
                "signal_count": None,
                "primary_eligible": False,
                "complete": False,
                "audit_validity": "UNAVAILABLE",
                "unknown": True,
            }
        )
    complete = (
        inspection["status"] == "PASS"
        and terminal is not None
        and terminal.get("reason") == "PLANNED_COMPLETE"
        and all(m["status"] == "SEALED" and m["audit_validity"] == "VALID" for m in markets)
        and not errors
    )
    available = [m for m in markets if m["status"] != "UNAVAILABLE"]
    primary_markets = [m for m in available if m["primary_eligible"]]
    raw_signals = sum(m.get("signal_count") or 0 for m in available) if available else None
    primary_signals = (
        sum(m.get("signal_count") or 0 for m in available if m["primary_eligible"])
        if primary_markets
        else None
    )
    proven = [
        m["market"]
        for m in markets
        if m["primary_eligible"] and m["audit_validity"] == "VALID"
        and m.get("admission_signal_ids", m["signal_ids"])
    ]
    primary = [r for r in orders if r["primary_eligible"]]
    mode = cohort["stage"]
    resources_path = run / "resources.json"
    resources = json.loads(resources_path.read_text()) if resources_path.exists() else None
    rss_values = [r["rss_peak_mib"] for r in resources.get("samples", [])] if resources else []
    return {
        "schema": strategy_schema(plan["settings"]["sop"], "independent-validation-report-v1")
        if mode == "INDEPENDENT_VALIDATION"
        else strategy_schema(plan["settings"]["sop"], "realtime-phase-report-v1"),
        "strategy": cohort["strategy"],
        "mode": "REALTIME_PUBLIC_DATA_SIMULATION",
        "sample_role": mode,
        "frequency_reporting_contract": frequency_reporting_contract(mode, len(names)),
        "phase_complete": complete,
        "metrics_status": "UNAVAILABLE"
        if not available
        else "PARTIAL"
        if errors or len(available) < len(names) or any(m["unknown"] for m in markets)
        else "OK",
        "run_status": inspection["status"],
        "run": str(run),
        "run_identity": plan["run_identity"],
        "sop_definition": plan["settings"]["sop"],
        "execution_contract": plan["settings"]["sop_execution"],
        "batch_number": cohort.get("batch_number"),
        "market_epochs": cohort["market_epochs"],
        "market_list_sha256": sha256(cohort_path),
        "markets": markets,
        "denominator": len(names),
        "proven_primary_signal_markets": len(proven),
        "proven_primary_coverage": len(proven) / len(names),
        "known_raw_signal_count": raw_signals,
        "known_primary_signal_count": primary_signals,
        **({"frequency_signal_basis": "EVER_REFERENCE_READY_BEFORE_ORIGINAL_DEADLINE",
            "known_raw_reference_ready_signal_count": sum(m.get("admission_signal_count", 0) for m in available)
                if available else None,
            "known_primary_reference_ready_signal_count": sum(m.get("admission_signal_count", 0) for m in primary_markets)
                if primary_markets else None}
           if plan["settings"]["sop"].get("strategy", "M12") == "M17" else {}),
        "primary_metrics_status": "NO_ELIGIBLE_SAMPLES" if available and not primary_markets else "AVAILABLE" if primary_markets else "UNAVAILABLE",
        "missing_or_unavailable_markets": [
            m["market"] for m in markets if m["status"] == "UNAVAILABLE"
        ],
        "unknown_markets": [m["market"] for m in markets if m["unknown"]],
        "isolated_markets": [m["market"] for m in available if not m["primary_eligible"]],
        "errors": errors,
        "run_inspection_errors": inspection.get("errors", []),
        "all_recorded": {
            which: summary(orders, raw_signals, which) for which in ("assumed", "simulated")
        }
        if available
        else None,
        "primary": {
            which: summary(primary, primary_signals, which) for which in ("assumed", "simulated")
        }
        if primary_markets
        else None,
        "observed_complete_window_flags": sum(m["complete"] for m in markets),
        "window_flag_definition": "warmed before T15 and observed through T300; not proof of gap-free 0-300s or complete reconstruction",
        "configured_memory_limit_mib": plan["settings"]["max_rss_mib"],
        "observed_rss_peak_mib": max(rss_values) if rss_values else None,
        "run_bytes_at_report": sum(p.stat().st_size for p in run.rglob("*") if p.is_file()),
        "evidence": {
            "cohort_path": str(cohort_path),
            "plan_sha256": sha256(run / "plan.json"),
            "terminal_sha256": sha256(run / "completion.json") if terminal else None,
            "resources_sha256": sha256(resources_path) if resources else None,
        },
        "reported_beijing": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "orders": orders,
        "limitations": [
            "Only committed audit; unknown intervals do not prove no signal or no fill",
            "Pending/uncertain orders may include known partial fills; known PnL is not full execution PnL",
            "No fill probability guarantee; gross PnL excludes unverified fees/rebates",
        ],
    }


def save_report(report, output):
    output.mkdir(parents=True, exist_ok=True)
    write(output / "stage-report.json", {k: v for k, v in report.items() if k != "orders"})
    with (output / "orders.csv").open("w", newline="") as f:
        orders = report["orders"]
        fields = list(orders[0]) if orders else ["market", "order_id", "price", "quantity"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(orders)
    with (output / "market-watermarks.csv").open("w", newline="") as f:
        fields = [
            "market",
            "status",
            "input_seq",
            "signal_count",
            "submitted_orders",
            "positive_modelled_fill_orders",
            "unresolved_orders",
            "complete",
            "audit_validity",
            "primary_eligible",
            "unknown",
            "source_db",
        ]
        if report.get("sop_definition", {}).get("strategy") == "M17":
            fields.extend(["admission_signal_count", "admission_signal_basis"])
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for m in report["markets"]:
            w.writerow({key: m.get(key) for key in fields})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--cohort", required=True, type=Path)
    parser.add_argument("--unit", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    props = subprocess.run(
        ["systemctl", "--user", "show", args.unit, "-p", "ActiveState", "-p", "MainPID"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    if (
        "MainPID=0" not in props
        or "ActiveState=active" in props
        or "ActiveState=activating" in props
    ):
        raise ValueError("FINAL_PHASE_REPORT_REQUIRES_SOURCE_SERVICE_FINISHED")
    report = collect(
        args.run.resolve(), args.cohort.resolve(), project=Path("/home/ubuntu/polymaker")
    )
    if args.output.exists():
        raise ValueError("NEW_PHASE_REPORT_OUTPUT_REQUIRED")
    save_report(report, args.output)
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "sample_role",
                    "phase_complete",
                    "run_status",
                    "denominator",
                    "proven_primary_signal_markets",
                    "known_raw_signal_count",
                )
            },
            ensure_ascii=False,
        )
    )
