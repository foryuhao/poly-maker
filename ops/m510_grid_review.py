"""Choose bounded signal parameters by frozen-cohort counts, never by PnL."""

import argparse
import json
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path

TARGETS = ("M7", "M9", "M10")
# Fixed before reading any completed grid result; one changed timing knob first.
PRIORITY = ("control_v3", "hold25", "fast250", "recovery40", "recovery40_hold25",
            "recovery40_hold25_pause60")


def signature(row):
    return tuple(row[k] for k in ("strategy", "token", "confirmed", "deadline", "anchor_price"))


def review(directory):
    manifest = json.loads((directory / "cohort25.json").read_text())
    expected = [r["market"] for r in manifest["markets"]]
    rule_path = directory / "frequency-selection-rule-expanded.json"
    priority = json.loads(rule_path.read_text())["priority"] if rule_path.exists() else list(PRIORITY)
    grids = ["frequency-grid-v1"]+(["frequency-grid-extra-v1"] if rule_path.exists() else [])
    counts = {name: Counter({s: 0 for s in TARGETS}) for name in priority}
    controls, progress, bad_terminals = [], {}, []
    parameters = {}
    owners = {}
    complete = True
    for grid_name in grids:
        grid = directory / grid_name
        path = grid / "progress.json"
        rows = json.loads(path.read_text())["markets"] if path.exists() else []
        seen = [r["market"] for r in rows]
        if seen != expected[:len(seen)] or len(set(seen)) != len(seen):
            raise ValueError("GRID_COHORT_OR_ORDER_CHANGED")
        progress[grid_name] = len(rows)
        terminal_path = grid / "completion.json"
        terminal = json.loads(terminal_path.read_text()) if terminal_path.exists() else {}
        plan_path = grid / "plan.json"
        plan = json.loads(plan_path.read_text()) if plan_path.exists() else {}
        if terminal and (terminal.get("status") != "PASS"
                         or terminal.get("run_identity") != plan.get("run_identity")):
            bad_terminals.append(grid_name)
        complete &= seen == expected and terminal.get("status") == "PASS" and terminal.get("markets") == 25
        for row in rows:
            for name, p in row["profiles"].items():
                if name == "control_v3" and grid_name != grids[0]:
                    continue  # Validate each control, never add duplicate counts.
                if name not in counts or (name in owners and owners[name] != grid_name):
                    raise ValueError("UNDECLARED_OR_REUSED_GRID_PROFILE")
                owners[name] = grid_name
                counts[name].update({s: p["counts"].get(s, 0) for s in TARGETS})
                parameters[name] = p["parameters"]
            database = directory / "next25-v3b" / row["market"] / "simulation.sqlite3"
            if not database.with_name("replay-state.json").exists():
                continue
            with closing(sqlite3.connect(database.resolve().as_uri()+"?mode=ro", uri=True)) as db:
                originals = [json.loads(p)["payload"] for p, in db.execute(
                    "SELECT payload FROM audit WHERE kind='SIGNAL' AND json_extract(payload,'$.event')='CANDIDATE'")]
            a = sorted(signature(p) for p in originals)
            b = sorted(signature(p) for p in row["profiles"]["control_v3"]["signals"])
            controls.append({"grid": grid_name, "market": row["market"], "same_primary_candidates": a == b})
    complete &= len(controls) == 25*len(grids) and set(owners) == set(priority)
    control_ok = all(c["same_primary_candidates"] for c in controls)
    selection = {}
    if complete and control_ok:
        for strategy in TARGETS:
            eligible = [p for p in priority if counts[p][strategy] >= 10]
            chosen = eligible[0] if eligible else max(priority, key=lambda p: counts[p][strategy])
            selection[strategy] = {
                "status": "FREQUENCY_TARGET_MET" if eligible else
                    "TARGET_NOT_MET_WITH_BOUNDED_SEMANTIC_PRESERVING_GRID",
                "profile": chosen, "signals": counts[chosen][strategy],
                "parameters": parameters[chosen][strategy],
                "purpose": "full economic comparison; unmet remains unmet, not permission to launch",
            }
    result = {"status": "FAIL" if not control_ok or bad_terminals else "COMPLETE" if complete else "PARTIAL",
        "counted_markets": progress, "expected_markets_per_grid": 25,
        "selection_rule": "first profile in predeclared priority reaching 10 candidates; no PnL consulted",
        "priority": priority,
        "counts": {name: counts[name] if name in owners else None for name in priority},
        "profile_sample_markets": {name: progress.get(owners.get(name), 0) for name in priority},
        "bad_terminals": bad_terminals, "control_checks": controls, "selection": selection,
        "source_quality_evidence": "cohort-quality.json; complete recordings include unavailable intervals",
        "frequency_count_scope": "Confirmed candidates in provable intervals, not an assertion that ambiguous intervals contained no signals",
        "not_profitability_validation": True}
    (directory / "frequency-grid-review.json").write_text(json.dumps(result, indent=2)+"\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    result = review(args.directory.resolve())
    print(json.dumps({k: v for k, v in result.items() if k != "control_checks"}, indent=2))
