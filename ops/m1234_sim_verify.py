"""Read-only acceptance checks for a stopped public simulation run."""

import argparse
import json
import sqlite3
from pathlib import Path

from polymaker.m1234_sim.audit import reconcile
from polymaker.m1234_sim.events import Clock, digest, read_events, write_json
from polymaker.m1234_sim.report import read_state, summary
from polymaker.m1234_sim.runner import validate_run_limits
from polymaker.m1234_sim.runstate import inspect_run


def verify(directory, *, full_inputs=False):
    try:
        return verify_stopped(directory, full_inputs=full_inputs)
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        return {"status": "INCOMPLETE", "errors": ["Acceptance evidence unavailable: " + str(exc)],
                "run": str(directory), "calibration_status": "UNCALIBRATED"}


def verify_stopped(directory, *, full_inputs=False):
    directory = Path(directory).resolve()
    evidence = inspect_run(directory)
    if evidence["status"] != "PASS":
        errors = list(evidence["errors"])
        if evidence["invalid_markets"]:
            errors.append("invalid simulation members: " + ",".join(evidence["invalid_markets"]))
        if evidence["status"] == "FAIL":
            errors.append("bound run failure or invalid member prohibits acceptance")
        else:
            errors.append("No verified complete terminal; active/stopped state is not established")
        return {"status": evidence["status"], "errors": errors, "run": str(directory),
                "run_evidence": evidence, "calibration_status": "UNCALIBRATED"}
    plan = json.loads((directory / "plan.json").read_text())
    completion = json.loads((directory / "completion.json").read_text())
    errors = []
    continuous = plan["settings"].get("continuous", False)
    target = plan["settings"]["max_markets"] if continuous else 12
    validate_run_limits(plan["settings"])
    if completion["reason"] != "PLANNED_COMPLETE":
        errors.append("run did not finish normally: " + completion["reason"])
    if completion.get("status") != "PASS" or completion.get("invalid_markets"):
        errors.append("run simulation validity failed or was not recorded")
    if completion["complete_windows"] < target:
        errors.append(f"fewer than {target} complete windows")
    if completion["recording_errors"]:
        errors.append("recording errors")
    if completion["real_orders"] != 0:
        errors.append("unexpected real order count")
    if (plan["predeclared_max_markets"] != target
            or plan.get("target_complete_windows", target) != target
            or plan["max_seconds"] != plan["settings"]["max_seconds"]
            or not continuous and plan["max_seconds"] > 4500):
        errors.append("predeclared scope mismatch")
    markets = []
    versions = set()
    for market_dir in sorted(directory.glob("btc-updown-5m-*")):
        database = market_dir / "snapshot.sqlite3"
        if not database.exists():
            errors.append("missing consistent database snapshot: " + market_dir.name)
            continue
        state = read_state(database)
        result = reconcile(database)
        report = summary(database)
        versions.add(report["implementation_hash"])
        if result["status"] != "PASS":
            errors.append("reconciliation failed: " + market_dir.name)
        if result["acceptance_status"] != "PASS" or report["validity"]["status"] != "VALID":
            errors.append("simulation invalid or legacy-unverified: " + market_dir.name)
        if not state["coverage"]["complete_window"]:
            errors.append("incomplete observation: " + market_dir.name)
        if state["market"]["slug"] != market_dir.name:
            errors.append("market identity mismatch")
        if any("OVERFLOW" in g["reason"] for g in state["coverage"]["gaps"]):
            errors.append("input overflow: " + market_dir.name)
        for account in report["accounts"]:
            if account["pnl_status"] != "SETTLED" and account["gross_pnl_usdc"] is not None:
                errors.append("unresolved PnL represented as a point value")
        raw = None
        if full_inputs:
            clock = Clock()
            seq = frames = connects = 0
            for event in read_events(market_dir / "input.jsonl.gz"):
                seq = event["seq"]
                clock.set(event["available_mono"], event["available_wall"])
                frames += event["kind"] == "FRAME"
                connects += event["kind"] == "CONNECT"
                if event["kind"] == "INIT" and event["definition"].get("artificial_fixture"):
                    errors.append("artificial fixture passed as public input")
            raw = {"last_seq": seq, "frames": frames, "connections": connects}
            if (
                seq != state["input_seq"]
                or frames != state["coverage"]["frames"]
                or connects != state["coverage"]["generations"]
            ):
                errors.append("raw input watermarks differ: " + market_dir.name)
        markets.append(
            {
                "market": market_dir.name,
                "complete_window": state["coverage"]["complete_window"],
                "reconciliation": result["status"],
                "validity": report["validity"],
                "official_winner": (state["resolution"] or {}).get("winner"),
                "frames": state["coverage"]["frames"],
                "generations": state["coverage"]["generations"],
                "gaps": state["coverage"]["gaps"],
                "diagnostics": report["input_quality_diagnostics"],
                "modelled_orders": sum(a["submissions"] for a in report["accounts"]),
                "accounts": [
                    {
                        k: a[k]
                        for k in (
                            "profile",
                            "scenario",
                            "submissions",
                            "partial",
                            "full",
                            "unknown",
                            "filled_shares",
                            "pnl_status",
                            "gross_pnl_usdc",
                        )
                    }
                    for a in report["accounts"]
                ],
                "raw": raw,
                "database": str(database),
            }
        )
    if len(markets) != target or len(versions) != 1:
        errors.append("market count or implementation versions differ")
    resources = json.loads((directory / "resources.json").read_text())
    if resources["errors"]:
        errors.append("resource/recorder errors")
    return {
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "run": str(directory),
        "completion": completion,
        "markets": markets,
        "implementation_hashes": sorted(versions),
        "accounting_scope": "independent counterfactual accounts; no portfolio PnL sum",
        "full_inputs_checked": full_inputs,
        "calibration_status": "UNCALIBRATED",
        "verification_hash": digest(markets),
        "run_evidence": evidence,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--full-inputs", action="store_true")
    args = parser.parse_args()
    result = verify(args.run_dir, full_inputs=args.full_inputs)
    write_json(args.output, result)
    print(
        json.dumps({"status": result["status"], "errors": result["errors"], "output": args.output})
    )
    if result["status"] == "FAIL":
        raise SystemExit(1)
    if result["status"] != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
