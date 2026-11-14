"""Reconcile combined final parameters to independently calibrated candidates."""

import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

TARGETS = ("M7", "M9", "M10")
FIELDS = ("strategy", "token", "confirmed", "deadline", "anchor_price")


def review(directory, name="selected25-v4"):
    selection = json.loads((directory / "final-parameter-selection.json").read_text())
    expected = json.loads((directory / "final-expected-signatures.json").read_text())
    members = json.loads((directory / "cohort25.json").read_text())["markets"]
    root = Path(json.loads((directory / "selected-v4-jobs.json").read_text())["source_root"])
    changed = [p for p, sha in json.loads((root / "frozen-source.json").read_text()).items()
               if hashlib.sha256((root / p).read_bytes()).hexdigest() != sha]
    run = directory / name
    proof = json.loads((run / "import-proof.json").read_text())
    source_match = (proof["status"] == "MATCH" and proof["expected_source_root"] == str(root)
                    and proof["loaded_modules"]["runner"] == str(root / "src/polymaker/m1234_sim/runner.py")
                    and proof["loaded_modules"]["m511_sim"] == str(root / "extension/m511_sim.py"))
    checks, totals = [], {s: 0 for s in TARGETS}
    for member in members:
        market = member["market"]
        file = run / market / "replay-state.json"
        if not file.exists():
            continue
        state = json.loads(file.read_text())
        assert state["coverage"]["complete_window"] and state["resolution"]
        assert state["validity"]["status"] == "VALID"
        assert set(state["brokers"]) == {"research/trade-through"}
        assert set(state["brokers"]["research/trade-through"]["accounts"]) == {f"M{i}" for i in range(5, 11)}
        with closing(sqlite3.connect(file.with_name("simulation.sqlite3").resolve().as_uri()+"?mode=ro", uri=True)) as db:
            definition = json.loads(db.execute(
                "SELECT payload FROM audit WHERE kind='DEFINITION' ORDER BY seq LIMIT 1"
            ).fetchone()[0])["definition"]
            settings = definition["settings"]["m5_m11"]
            assert settings["signal_variant"] == selection["final_signal_variant"]
            assert settings["frequency_parameters"] == selection["resolved_parameters"]
            actual = [json.loads(p)["payload"] for p, in db.execute(
                "SELECT payload FROM audit WHERE kind='SIGNAL' AND json_extract(payload,'$.event')='CANDIDATE'")]
        row = {"market": market, "same_selected_candidates": {}}
        for strategy in TARGETS:
            a = sorted(tuple(p[k] for k in FIELDS) for p in actual if p["strategy"] == strategy)
            b = sorted(tuple(p[k] for k in FIELDS) for p in expected[strategy][market])
            row["same_selected_candidates"][strategy] = a == b
            totals[strategy] += len(a)
        checks.append(row)
    terminal_path, plan_path = run / "completion.json", run / "plan.json"
    terminal = json.loads(terminal_path.read_text()) if terminal_path.exists() else {}
    plan = json.loads(plan_path.read_text()) if plan_path.exists() else {}
    terminal_ok = (terminal.get("status") == "PASS" and terminal.get("markets") == 25
                   and terminal.get("run_identity") == plan.get("run_identity"))
    bad = bool(changed) or not source_match or any(
        not all(c["same_selected_candidates"].values()) for c in checks)
    if len(checks) == 25 and totals != selection["expected_confirmed_counts"]:
        bad = True
    result = {"status": "FAIL" if bad or terminal and not terminal_ok else
              "PASS_MATCHED_CALIBRATION" if len(checks) == 25 and terminal_ok else "PARTIAL",
              "checked_markets": len(checks), "expected_markets": 25,
              "signals": totals if checks else None,
              "expected_signals": selection["expected_confirmed_counts"],
              "source_matches_selected_bundle": source_match, "changed_frozen_files": changed,
              "frequency_target_met": len(checks) == 25 and all(n >= 10 for n in totals.values()),
              "scope": "Combined final profile candidate signatures match independently calibrated profiles; this does not establish frequency target or profitability.",
              "checks": checks}
    (directory / (name+"-signature-review.json")).write_text(json.dumps(result, indent=2)+"\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--run", default="selected25-v4")
    args = parser.parse_args()
    result = review(args.directory.resolve(), args.run)
    print(json.dumps({k: v for k, v in result.items() if k != "checks"}, indent=2))
