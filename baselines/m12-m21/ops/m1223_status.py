"""Observe the current goal-owned run; never starts/stops a process or a timer."""

import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from m1223_prepare import write
from m1223_report import market_result, summary

PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT / "research_outputs/m1223_sim"


def observe():
    pointer = ROOT / "current.json"
    current = json.loads(pointer.read_text()) if pointer.exists() else {}
    state_path = Path(current.get("state") or ROOT / "state.json").resolve()
    if not state_path.is_relative_to(ROOT.resolve()):
        raise ValueError("CURRENT_STATE_OUTSIDE_SIMULATION_TASK")
    state = json.loads(state_path.read_text())
    run = state.get("active_run")
    if run is None:
        print(
            json.dumps(
                {
                    "strategy": state["strategy"],
                    "stage": state["stage"],
                    "active_run": None,
                    "enabled": state.get("enabled"),
                },
                ensure_ascii=False,
            )
        )
        return state
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    output = Path(run["output"])
    props = subprocess.run(
        [
            "systemctl",
            "--user",
            "show",
            run["unit"],
            "-p",
            "LoadState",
            "-p",
            "ActiveState",
            "-p",
            "SubState",
            "-p",
            "MainPID",
            "-p",
            "Result",
            "-p",
            "ExecMainStatus",
            "-p",
            "MemoryCurrent",
            "-p",
            "MemoryPeak",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    service = dict(row.split("=", 1) for row in props.splitlines() if "=" in row)
    live = service.get("ActiveState") in {"active", "activating"} and service.get("MainPID") != "0"
    terminal_path = output / "completion.json"
    terminal = json.loads(terminal_path.read_text()) if terminal_path.exists() else None
    plan = (
        json.loads((output / "plan.json").read_text()) if (output / "plan.json").exists() else None
    )
    cohort = json.loads(Path(run["cohort"]).read_text())
    phase_kind = cohort["stage"]
    markets, orders, errors = [], [], []
    for database in sorted(output.glob("btc-*/simulation.sqlite3")):
        try:
            m, rows = market_result(database, PROJECT)
            markets.append(m)
            orders.extend(rows)
        except Exception as exc:
            # A stat/read failure does not become a simulator restart request.
            errors.append(
                {
                    "market": database.parent.name,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
    primary = [r for r in orders if r["primary_eligible"]]
    available = [m for m in markets if m.get("status") != "UNAVAILABLE"]
    primary_markets = [m for m in available if m["primary_eligible"]]
    sealed = [m for m in available if m["status"] == "SEALED"]
    counts = {
        "all_recorded_signals": sum(m["signal_count"] for m in available),
        "primary_signals": sum(m["signal_count"] for m in primary_markets),
        "all_recorded_signal_markets": sum(bool(m["signal_ids"]) for m in available),
        "primary_signal_markets": sum(bool(m.get("admission_signal_ids", m["signal_ids"])) for m in primary_markets),
        "submitted_orders": len(orders),
        "assumed_orders": len(orders),
        "positive_modelled_fill_orders": sum(r["has_positive_modelled_fill"] for r in orders),
        "unresolved_orders": sum(r["unresolved"] for r in orders),
        "pending_settlement_orders": sum(r["official_winner"] is None for r in orders),
        "observed_markets": len(markets),
        "sealed_markets": len(sealed),
        "complete_observed_windows": sum(m["complete"] for m in sealed),
        "predeclared_denominator": len(cohort["markets"]),
    }
    unavailable = [m["market"] for m in markets if m.get("status") == "UNAVAILABLE"]
    incomplete_stats = bool(errors or unavailable)
    stage = state["stage"]
    if not state.get("enabled"):
        stage = "DISABLED"
    elif live:
        stage = phase_kind if markets else phase_kind + "_STARTING"
    elif terminal:
        stage = (
            phase_kind + "_COMPLETE_AWAITING_REPORT"
            if terminal.get("status") == "PASS"
            else "STOPPED_REQUIRES_REVIEW"
        )
    else:
        stage = "NO_LIVE_HANDLE_OR_VERIFIED_TERMINAL_REQUIRES_REVIEW"
    scopes = {
        label: {mode: summary(rows, signal_count, mode) for mode in ("assumed", "simulated")}
        for label, rows, signal_count in (
            ("all_recorded", orders, counts["all_recorded_signals"]),
            ("primary", primary, counts["primary_signals"]),
        )
    }
    scoped_counts = {}
    for label, values in scopes.items():
        relevant = primary_markets if label == "primary" else available
        scoped_counts[label] = {
            "status": "UNAVAILABLE"
            if not available
            else "NO_ELIGIBLE_SAMPLES"
            if not relevant
            else "PARTIAL"
            if live or incomplete_stats or any(m["unknown"] for m in relevant)
            else "OK",
            "signals": values["assumed"]["signal_count"] if relevant else None,
            "signal_markets": counts[
                "primary_signal_markets" if label == "primary" else "all_recorded_signal_markets"
            ]
            if relevant
            else None,
            "submitted_orders": values["assumed"]["submitted_orders"] if relevant else None,
            "assumed_orders": values["assumed"]["orders"] if relevant else None,
            "positive_modelled_fill_orders": values["simulated"]["orders"]
            if relevant
            else None,
            "unresolved_orders": values["simulated"]["unresolved_orders"]
            if relevant
            else None,
            "pending_settlement_orders": values["assumed"]["pending_settlement_orders"]
            if relevant
            else None,
        }
    result = {
        "status": "PARTIAL"
        if live or incomplete_stats or any(m["unknown"] for m in available)
        else "OK",
        "partial_causes": (["LIVE_COMMITTED_PREFIX_ONLY"] if live else [])
        + (["STATISTICS_MISSING"] if incomplete_stats else [])
        + (["SOURCE_QUALITY_UNKNOWN_RECORDS"] if any(m["unknown"] for m in available) else []),
        "as_of_beijing": now.isoformat(),
        "strategy": state["strategy"],
        "stage": stage,
        "run": str(output),
        "unit": run["unit"],
        "service": service,
        "implementation_version": plan["settings"]["sop"]["version"] if plan else None,
        "cohort": str(run["cohort"]),
        "sample_role": cohort["stage"],
        "window_end_beijing": datetime.fromtimestamp(
            cohort["window_end_epoch"], ZoneInfo("Asia/Shanghai")
        ).isoformat(),
        "committed_counts": counts,
        "scoped_committed_counts": scoped_counts,
        "count_scope_note": "Legacy order totals are all-recorded; scoped counts distinguish primary eligibility. Pending settlement covers the same SUBMIT population as assumed fills.",
        "committed_prefix_only_while_running": live,
        "primary_eligibility_provisional_for_active_markets": live,
        "counts_complete_for_available_committed_prefix": not incomplete_stats,
        "errors": errors,
        "unavailable_markets": unavailable,
        "unknown_markets": [m["market"] for m in available if m["unknown"]],
        "isolated_markets": [m["market"] for m in available if not m["primary_eligible"]],
        "markets": markets,
        **scopes,
        "terminal_status": terminal.get("status") if terminal else None,
        "terminal_reason": terminal.get("reason") if terminal else None,
        "free_disk_bytes": shutil.disk_usage(PROJECT).free,
        "run_bytes": sum(p.stat().st_size for p in output.rglob("*") if p.is_file()),
        "frequency_admission_granted": bool(
            plan
            and plan["settings"].get("sop_phase_admission", {}).get("admission_granted_for_24h")
        ),
        "admission_note": (plan["settings"].get("sop_phase_admission", {}).get("note")
                           if plan else None) or "Development observations cannot grant independent validation or24h admission",
    }
    folder = (
        ROOT
        / state["strategy"]
        / Path(run["cohort"]).parent.name
        / "observations"
        / now.strftime("%Y%m%dT%H%M%S")
    )
    write(folder / "report.json", result)
    write(
        folder / "observation.json",
        {k: v for k, v in result.items() if k not in ("markets", "all_recorded", "primary")},
    )
    state.update(
        stage=stage,
        updated_beijing=now.isoformat(),
        service=service,
        current_counts=counts,
        current_scoped_counts=scoped_counts,
        current_counts_scope="ALL_RECORDED_ORDER_TOTALS; USE_CURRENT_SCOPED_COUNTS_FOR_PRIMARY",
        latest_report=str(folder / "report.json"),
        last_observation=str(folder / "observation.json"),
    )
    write(state_path, state)
    current_path = ROOT / "current.json"
    current = json.loads(current_path.read_text()) if current_path.exists() else {}
    current.update(
        strategy=state["strategy"],
        stage=stage,
        run=str(output),
        unit=run["unit"],
        latest_report=state["latest_report"],
        updated_beijing=now.isoformat(),
        service=service,
        current_counts=counts,
        current_scoped_counts=scoped_counts,
        current_counts_scope=state["current_counts_scope"],
        last_observation=state["last_observation"],
    )
    write(current_path, current)
    print(
        json.dumps(
            {k: v for k, v in result.items() if k not in ("markets", "all_recorded", "primary")},
            ensure_ascii=False,
            indent=2,
        )
    )
    return result


if __name__ == "__main__":
    observe()
