"""Verify M7 failed-root experiments; untargeted shadows are not zero samples."""

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from contextlib import closing
from copy import deepcopy
from pathlib import Path

FIELDS = ("strategy", "token", "confirmed", "deadline", "anchor_price")


def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def review(root):
    cohort = root / "cohort25.json"
    members = read(cohort)["markets"]
    expected = [m["market"] for m in members]
    rule = read(root / "phase4-failed-root-rule.json")
    freeze = read(root / "phase4-freeze.json")
    source = Path(freeze["source_root"])
    run = root / "frequency-grid-phase4"
    plan, proof = read(run / "plan.json", {}), read(run / "import-proof.json", {})
    errors, checks = [], []
    hashes = read(source / "frozen-source.json")
    if any(hashlib.sha256((source / p).read_bytes()).hexdigest() != digest for p, digest in hashes.items()):
        errors.append("FROZEN_SOURCE_CHANGED")
    if (hashlib.sha256(cohort.read_bytes()).hexdigest() != rule["cohort_sha256"]
            or plan.get("source_manifest_sha256") != rule["cohort_sha256"]
            or plan.get("profiles") != rule["profiles"] or plan.get("primary_manager") != rule["primary"]
            or plan.get("signal_variant") != rule["signal_variant"]
            or plan.get("shadow_targets") != ["M7"]):
        errors.append("UNDECLARED_PLAN_OR_TARGET_SCOPE")
    if (proof.get("status") != "MATCH" or proof.get("expected_source_root") != str(source)
            or proof.get("loaded_modules") != {
                "m511_sim": str(source / "extension/m511_sim.py"),
                "runner": str(source / "src/polymaker/m1234_sim/runner.py")}):
        errors.append("SOURCE_IMPORT_MISMATCH")
    rows = read(run / "progress.json", {"markets": []})["markets"]
    seen = [r["market"] for r in rows]
    if seen != expected[:len(seen)] or len(set(seen)) != len(seen):
        errors.append("COHORT_ORDER_CHANGED")
    counts, params = Counter({p: 0 for p in rule["priority"]}), {}
    for row in rows:
        market = row["market"]
        member = members[expected.index(market)]
        profiles = row["profiles"]
        if set(profiles) != set(rule["profiles"]):
            errors.append("MISSING_OR_EXTRA_PROFILE:"+market)
            continue
        primary = profiles[rule["primary"]]
        for name, p in profiles.items():
            eligible = {"M5", "M6", "M7", "M8", "M9", "M10"} if name == rule["primary"] else {"M7"}
            resolved = deepcopy(primary["parameters"])
            for strategy, overrides in rule["profiles"][name].items():
                resolved[strategy].update(overrides)
            if set(p["eligible_strategies"]) != eligible or p["parameters"] != resolved:
                errors.append("PROFILE_PARAMETERS_OR_ELIGIBILITY_CHANGED:"+market+"/"+name)
            actual = Counter(s["strategy"] for s in p["signals"])
            if actual != Counter(p["counts"]) or set(actual)-eligible:
                errors.append("PROFILE_COUNT_OR_TARGET_MISMATCH:"+market+"/"+name)
            counts[name] += actual["M7"]
            params[name] = p["parameters"]["M7"]
        database = run / market / "simulation.sqlite3"
        with closing(sqlite3.connect(database.resolve().as_uri()+"?mode=ro", uri=True)) as db:
            state = json.loads(db.execute("SELECT payload FROM snapshots WHERE name='state'").fetchone()[0])
            archive = json.loads(db.execute(
                "SELECT payload FROM metadata WHERE name='diagnostic_archive'").fetchone()[0])
        valid = (state["input_seq"] == member["input_seq"] and state["coverage"]["complete_window"]
                 and state["resolution"] and state["resolution"]["winner"] == member["winner"]
                 and state["validity"]["status"] == "VALID" and archive["closed"]
                 and archive["input_seq"] == state["input_seq"]
                 and hashlib.sha256((database.parent / archive["file"]).read_bytes()).hexdigest() == archive["sha256"])
        with closing(sqlite3.connect((root / "selected25-v4" / market / "simulation.sqlite3").resolve().as_uri()
                                     +"?mode=ro", uri=True)) as db:
            old = [json.loads(p)["payload"] for p, in db.execute(
                "SELECT payload FROM audit WHERE kind='SIGNAL' AND json_extract(payload,'$.event')='CANDIDATE'")]
        same = sorted(tuple(p[k] for k in FIELDS) for p in old) == sorted(
            tuple(p[k] for k in FIELDS) for p in primary["signals"])
        if not valid or not same:
            errors.append("MEMBER_OR_PRIMARY_CONTROL_MISMATCH:"+market)
        checks.append({"market": market, "complete_valid_settled_and_closed": bool(valid),
                       "same_independent_v4_control_candidates": same})
    terminal = read(run / "completion.json", {})
    if terminal and (terminal.get("status") != "PASS" or terminal.get("markets") != 25
                     or terminal.get("run_identity") != plan.get("run_identity")):
        errors.append("TERMINAL_MISMATCH")
    complete = len(checks) == 25 and terminal.get("status") == "PASS"
    selection = None
    if complete and not errors:
        passing = [name for name in rule["priority"] if counts[name] >= 10]
        chosen = passing[0] if passing else max(rule["priority"], key=lambda p: counts[p])
        selection = {"profile": chosen, "signals": counts[chosen], "parameters": params[chosen],
                     "frequency_target_met": bool(passing), "requires_full_economic_replay": True,
                     "scope": "Unmet fallback is for economic review only, never a target pass or promotion."}
    result = {"status": "FAIL" if errors else "COMPLETE" if complete else "PARTIAL",
              "counted_markets": len(checks), "expected_markets": 25,
              "M7_signals_by_profile": counts if rows else None,
              "untargeted_shadow_strategy_counts": None,
              "untargeted_scope": "M9/M10 not evaluated in shadows; do not interpret as zero signals.",
              "selection": selection, "selection_uses_pnl": False,
              "source_quality_limitations_retained": True, "errors": errors, "checks": checks}
    (root / "phase4-grid-review.json").write_text(json.dumps(result, indent=2)+"\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    result = review(args.directory.resolve())
    print(json.dumps({k: v for k, v in result.items() if k != "checks"}, indent=2))
