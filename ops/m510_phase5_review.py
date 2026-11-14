"""Validate target-specific profiles against the fixed independent public control."""

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from contextlib import closing
from copy import deepcopy
from pathlib import Path

FIELDS = ("strategy", "token", "confirmed", "deadline", "anchor_price")
GROUPS = tuple(f"M{i}" for i in range(5, 11))


def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def physics(state, strategy):
    broker = state["brokers"]["research/trade-through"]
    orders = []
    for order in broker["orders"].values():
        intent = order["intent"]
        if intent["strategy"] == strategy:
            orders.append(tuple(intent[k] for k in (
                "token", "outcome", "price", "quantity", "available", "deadline"))
                + tuple(order[k] for k in ("filled", "spent", "reserved", "uncertain", "state")))
    return broker["accounts"][strategy], sorted(orders)


def review(root, phase=5):
    if phase not in (5, 6):
        raise ValueError("UNKNOWN_CALIBRATION_PHASE")
    cohort = root / "cohort25.json"
    members = read(cohort)["markets"]
    expected = [m["market"] for m in members]
    rule = read(root / ("phase5-window-rule.json" if phase == 5 else "phase6-local-ap-rule.json"))
    freeze = read(root / f"phase{phase}-freeze.json")
    source = Path(freeze["source_root"])
    hashes_file = source / "frozen-source.json"
    hashes = read(hashes_file)
    errors, checks = [], []
    if (hashlib.sha256(hashes_file.read_bytes()).hexdigest() != freeze["manifest_sha256"]
            or any(hashlib.sha256((source / p).read_bytes()).hexdigest() != h for p, h in hashes.items())):
        errors.append("FROZEN_SOURCE_CHANGED")
    run = root / f"frequency-grid-phase{phase}"
    plan, proof = read(run / "plan.json", {}), read(run / "import-proof.json", {})
    if (hashlib.sha256(cohort.read_bytes()).hexdigest() != rule["cohort_sha256"]
            or plan.get("source_manifest_sha256") != rule["cohort_sha256"]
            or plan.get("profiles") != rule["profiles"]
            or plan.get("profile_targets") != rule["profile_targets"]
            or plan.get("primary_manager") != rule["primary"]
            or plan.get("signal_variant") != rule["signal_variant"]
            or plan.get("profiles_input_sha256") != hashes[f"config/m510-phase{phase}-profiles.json"]
            or plan.get("profile_targets_input_sha256") != hashes[f"config/m510-phase{phase}-targets.json"]):
        errors.append("PLAN_OR_PARAMETER_SCOPE_CHANGED")
    if (proof.get("status") != "MATCH" or proof.get("expected_source_root") != str(source)
            or proof.get("loaded_modules") != {
                "m511_sim": str(source / "extension/m511_sim.py"),
                "runner": str(source / "src/polymaker/m1234_sim/runner.py")}):
        errors.append("SOURCE_IMPORT_MISMATCH")
    rows = read(run / "progress.json", {"markets": []})["markets"]
    seen = [r["market"] for r in rows]
    if seen != expected[:len(seen)] or len(set(seen)) != len(seen):
        errors.append("COHORT_ORDER_CHANGED")
    totals = {s: Counter({p: 0 for p in priority}) for s, priority in rule["priority_by_strategy"].items()}
    for row in rows:
        market = row["market"]
        if set(row["profiles"]) != set(rule["profiles"]):
            errors.append("PROFILE_SET_CHANGED:"+market)
            continue
        primary = row["profiles"][rule["primary"]]
        if row["peak_rss_mib"] > 512:
            errors.append("MEMORY_CAP_EXCEEDED:"+market)
        for name, p in row["profiles"].items():
            eligible = set(GROUPS) if name == rule["primary"] else set(rule["profile_targets"][name])
            resolved = deepcopy(primary["parameters"])
            for s, overrides in rule["profiles"][name].items():
                resolved[s].update(overrides)
            counted = Counter(s["strategy"] for s in p["signals"])
            if (set(p["eligible_strategies"]) != eligible or p["parameters"] != resolved
                    or counted != Counter(p["counts"]) or set(counted)-eligible):
                errors.append("ELIGIBILITY_OR_RESOLVED_CONFIGURATION_CHANGED:"+market+"/"+name)
            if name != rule["primary"]:
                for s in eligible:
                    totals[s][name] += counted[s]
        database = run / market / "simulation.sqlite3"
        with closing(sqlite3.connect(database.resolve().as_uri()+"?mode=ro", uri=True)) as db:
            state = json.loads(db.execute("SELECT payload FROM snapshots WHERE name='state'").fetchone()[0])
            archive = json.loads(db.execute("SELECT payload FROM metadata WHERE name='diagnostic_archive'").fetchone()[0])
            settings = json.loads(db.execute(
                "SELECT payload FROM audit WHERE kind='DEFINITION' ORDER BY seq LIMIT 1"
            ).fetchone()[0])["definition"]["settings"]
        member = members[expected.index(market)]
        complete = (state["input_seq"] == member["input_seq"] and state["coverage"]["complete_window"]
                    and state["resolution"] and state["resolution"]["winner"] == member["winner"]
                    and state["validity"]["status"] == "VALID" and archive["closed"]
                    and archive["input_seq"] == state["input_seq"]
                    and hashlib.sha256((database.parent / archive["file"]).read_bytes()).hexdigest() == archive["sha256"])
        definitions = (settings["parameter_grid"] == rule["profiles"]
                       and settings["parameter_profile_targets"] == rule["profile_targets"]
                       and settings["m5_m11"]["frequency_parameters"] == primary["parameters"])
        old_database = root / "selected25-v4" / market / "simulation.sqlite3"
        with closing(sqlite3.connect(old_database.resolve().as_uri()+"?mode=ro", uri=True)) as db:
            old_signals = [json.loads(p)["payload"] for p, in db.execute(
                "SELECT payload FROM audit WHERE kind='SIGNAL' AND json_extract(payload,'$.event')='CANDIDATE'")]
        same = (sorted(tuple(p[k] for k in FIELDS) for p in old_signals)
                == sorted(tuple(p[k] for k in FIELDS) for p in primary["signals"]))
        old_state = read(old_database.parent / "replay-state.json")
        same_orders = all(physics(state, s) == physics(old_state, s) for s in GROUPS)
        if not complete or not definitions or not same or not same_orders:
            errors.append("MEMBER_OR_INDEPENDENT_CONTROL_CHANGED:"+market)
        checks.append({"market": market, "complete_valid_settled_and_closed": bool(complete),
                       "audited_configuration_matches": definitions, "same_primary_candidates": same,
                       "same_primary_all6_accounts_and_orders": same_orders})
    terminal = read(run / "completion.json", {})
    if terminal and (terminal.get("status") != "PASS" or terminal.get("markets") != 25
                     or terminal.get("run_identity") != plan.get("run_identity")):
        errors.append("TERMINAL_MISMATCH")
    complete = len(checks) == 25 and terminal.get("status") == "PASS"
    selection = {}
    if complete and not errors:
        for s, priority in rule["priority_by_strategy"].items():
            passing = [p for p in priority if totals[s][p] >= 10]
            chosen = passing[0] if passing else max(priority, key=totals[s].__getitem__)
            selection[s] = {"profile": chosen, "signals": totals[s][chosen],
                            "frequency_target_met": bool(passing),
                            "parameters": rows[0]["profiles"][chosen]["parameters"][s],
                            "requires_complete_combined_economic_replay": True}
    result = {"status": "FAIL" if errors else "COMPLETE" if complete else "PARTIAL",
              "verifier_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "counted_markets": len(checks), "expected_markets": 25, "counts": totals if rows else None,
              "untargeted_shadow_counts": None, "selection": selection, "selection_uses_pnl": False,
              "source_unknown_intervals_retained": True, "errors": errors, "checks": checks}
    (root / f"phase{phase}-grid-review.json").write_text(json.dumps(result, indent=2)+"\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--phase", choices=(5, 6), type=int, default=5)
    args = parser.parse_args()
    value = review(args.directory.resolve(), args.phase)
    print(json.dumps({k: v for k, v in value.items() if k != "checks"}, indent=2))
