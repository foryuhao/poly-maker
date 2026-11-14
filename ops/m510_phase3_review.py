"""Validate the third frozen count-only grid against independent v4 replay."""

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path

TARGETS = ("M7", "M9", "M10")
FIELDS = ("strategy", "token", "confirmed", "deadline", "anchor_price")


def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def signatures(rows):
    return sorted(tuple(row[k] for k in FIELDS) for row in rows)


def review(directory, run_name="frequency-grid-phase3-v2", freeze_file="phase3-v2-freeze.json"):
    manifest = directory / "cohort25.json"
    members = read(manifest)["markets"]
    expected = [row["market"] for row in members]
    rule = read(directory / "phase3-frequency-rule.json")
    frozen = read(directory / freeze_file)
    root = Path(frozen["source_root"])
    changed = [p for p, digest in read(root / "frozen-source.json").items()
               if hashlib.sha256((root / p).read_bytes()).hexdigest() != digest]
    run = directory / run_name
    plan, proof = read(run / "plan.json", {}), read(run / "import-proof.json", {})
    errors = []
    if (run / "superseded.json").exists():
        errors.append("SUPERSEDED_GRID_NOT_ELIGIBLE")
    if changed:
        errors.append("FROZEN_SOURCE_CHANGED")
    manifest_sha = hashlib.sha256(manifest.read_bytes()).hexdigest()
    if (manifest_sha != rule["cohort_sha256"] or plan.get("source_manifest_sha256") != manifest_sha
            or plan.get("profiles") != rule["profiles"]
            or plan.get("signal_variant") != rule["signal_variant_to_add"]
            or plan.get("primary_manager") != "control_v4"):
        errors.append("GRID_PLAN_MISMATCH")
    if (proof.get("status") != "MATCH" or proof.get("expected_source_root") != str(root)
            or proof.get("loaded_modules") != {
                "m511_sim": str(root / "extension/m511_sim.py"),
                "runner": str(root / "src/polymaker/m1234_sim/runner.py")}):
        errors.append("SOURCE_IMPORT_MISMATCH")
    rows = read(run / "progress.json", {"markets": []})["markets"]
    seen = [row["market"] for row in rows]
    if seen != expected[:len(seen)] or len(set(seen)) != len(seen):
        errors.append("COHORT_OR_ORDER_CHANGED")
    totals = {name: Counter({s: 0 for s in TARGETS}) for name in rule["priority"]}
    controls, parameters, committed = [], {}, []
    for row in rows:
        market = row["market"]
        if set(row["profiles"]) != set(totals):
            errors.append("UNDECLARED_OR_MISSING_PROFILE:" + market)
            continue
        for name, profile in row["profiles"].items():
            calculated = Counter(p["strategy"] for p in profile["signals"])
            if calculated != Counter(profile["counts"]):
                errors.append("COUNT_AUDIT_MISMATCH:" + market + "/" + name)
            totals[name].update({s: calculated[s] for s in TARGETS})
            parameters[name] = profile["parameters"]
        database = run / market / "simulation.sqlite3"
        with closing(sqlite3.connect(database.resolve().as_uri()+"?mode=ro", uri=True)) as db:
            state = json.loads(db.execute("SELECT payload FROM snapshots WHERE name='state'").fetchone()[0])
            archive = json.loads(db.execute(
                "SELECT payload FROM metadata WHERE name='diagnostic_archive'").fetchone()[0])
        member = members[expected.index(market)]
        archive_path = database.parent / archive["file"]
        archive_ok = (archive.get("closed") is True and archive["input_seq"] == state["input_seq"]
                      and archive_path.is_file() and
                      hashlib.sha256(archive_path.read_bytes()).hexdigest() == archive["sha256"])
        member_ok = (state.get("coverage", {}).get("complete_window") and state.get("resolution")
                     and state["resolution"]["winner"] == member["winner"]
                     and state["input_seq"] == member["input_seq"]
                     and state.get("validity", {}).get("status") == "VALID" and archive_ok)
        committed.append({"market": market, "complete_valid_settled": bool(member_ok),
                          "input_seq": state["input_seq"], "closed_diagnostic_hash_matches": archive_ok})
        if not member_ok:
            errors.append("INCOMPLETE_OR_UNSETTLED_GRID_MEMBER:" + market)
        independent = directory / "selected25-v4" / market
        if not (independent / "replay-state.json").exists():
            continue
        with closing(sqlite3.connect((independent / "simulation.sqlite3").resolve().as_uri()
                                     + "?mode=ro", uri=True)) as db:
            originals = [json.loads(p)["payload"] for p, in db.execute(
                "SELECT payload FROM audit WHERE kind='SIGNAL' AND json_extract(payload,'$.event')='CANDIDATE'")]
        same = signatures(originals) == signatures(row["profiles"]["control_v4"]["signals"])
        controls.append({"market": market, "same_primary_candidates": same})
        if not same:
            errors.append("PRIMARY_CONTROL_CHANGED:" + market)
    terminal = read(run / "completion.json", {})
    if terminal and (terminal.get("status") != "PASS" or terminal.get("markets") != 25
                     or terminal.get("run_identity") != plan.get("run_identity")):
        errors.append("BAD_TERMINAL")
    complete = seen == expected and terminal.get("status") == "PASS" and len(controls) == 25
    selection = {}
    if complete and not errors:
        for strategy in TARGETS:
            eligible = [name for name in rule["priority"] if totals[name][strategy] >= 10]
            chosen = eligible[0] if eligible else None
            selection[strategy] = {
                "frequency_target_met": bool(chosen), "profile": chosen,
                "signals": totals[chosen][strategy] if chosen else None,
                "maximum_observed": max(count[strategy] for count in totals.values()),
                "parameters": parameters[chosen][strategy] if chosen else None,
                "requires_full_economic_replay": True}
    result = {"status": "FAIL" if errors else "COMPLETE" if complete else "PARTIAL",
              "run": run_name, "freeze_evidence": freeze_file,
              "verifier_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "counted_markets": len(rows), "expected_markets": 25,
              "counts": totals if rows else None, "errors": errors,
              "control_checks": controls, "committed_member_checks": committed, "selection": selection,
              "source_quality_evidence": "cohort-quality.json; unavailable intervals remain excluded",
              "selection_uses_pnl": False, "long_run_authorized_by_result": False}
    (directory / "phase3-grid-review.json").write_text(json.dumps(result, indent=2)+"\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--run", default="frequency-grid-phase3-v2")
    parser.add_argument("--freeze", default="phase3-v2-freeze.json")
    args = parser.parse_args()
    result = review(args.directory.resolve(), args.run, args.freeze)
    print(json.dumps({k: v for k, v in result.items()
                      if k not in {"control_checks", "committed_member_checks"}}, indent=2))
