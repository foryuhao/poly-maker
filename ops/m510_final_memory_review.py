"""Verify final-config resource evidence and strict same-rule retirement invariance."""

import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from m510_audit_equivalence import archive, compare


def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def database_state(database):
    with closing(sqlite3.connect(database.resolve().as_uri()+"?mode=ro", uri=True)) as db:
        state = json.loads(db.execute("SELECT payload FROM snapshots WHERE name='state'").fetchone()[0])
        definition = json.loads(db.execute(
            "SELECT payload FROM audit WHERE kind='DEFINITION' ORDER BY seq LIMIT 1"
        ).fetchone()[0])["definition"]
        owner = json.loads(db.execute("SELECT payload FROM metadata WHERE name='run_owner'").fetchone()[0])
    return state, definition, owner


def configuration(root, run_name):
    run = root / run_name
    selection = read(root / "combined-selection-v6.json")
    plan, proof = read(run / "plan.json", {}), read(run / "import-proof.json", {})
    completion = read(run / "completion.json", {})
    source = Path(selection["source_root"])
    manifest = source / "frozen-source.json"
    hashes = read(manifest)
    valid = (hashlib.sha256(manifest.read_bytes()).hexdigest() == selection["source_manifest_sha256"]
             and all(hashlib.sha256((source / name).read_bytes()).hexdigest() == digest
                     for name, digest in hashes.items())
             and proof.get("status") == "MATCH" and proof.get("expected_source_root") == str(source)
             and proof.get("loaded_modules") == {
                 "m511_sim": str(source / "extension/m511_sim.py"),
                 "runner": str(source / "src/polymaker/m1234_sim/runner.py")}
             and plan.get("execution") == "trade-through"
             and plan.get("frequency_parameters_input_sha256") == selection["parameters_sha256"]
             and completion.get("status") == "PASS" and completion.get("markets") == 25
             and completion.get("run_identity") == plan.get("run_identity"))
    return selection, plan, valid


def member_check(member, state, definition, selection):
    settings = definition["settings"]["m5_m11"]
    return bool(state["input_seq"] == member["input_seq"]
                and state["coverage"]["complete_window"] and state["validity"]["status"] == "VALID"
                and state["resolution"] and state["resolution"]["winner"] == member["winner"]
                and set(state["brokers"]) == {"research/trade-through"}
                and settings["signal_variant"] == selection["signal_variant"]
                and settings["frequency_parameters"] == selection["frequency_parameters"])


def resource_review(root, run_name, selected_run):
    selection, _selected_plan, selected_valid = configuration(root, selected_run)
    cohort_path = root / "cohort288-final.json"
    cohort = read(cohort_path)["markets"]
    cohort_proof = read(root / "cohort288-final-freeze.json")
    run = root / run_name
    proof, plan = read(run / "import-proof.json", {}), read(run / "plan.json", {})
    rows = read(run / "progress.json", {"markets": []})["markets"]
    completion = read(run / "completion.json", {})
    errors, checks, owners = [], [], set()
    if hashlib.sha256(cohort_path.read_bytes()).hexdigest() != cohort_proof["sha256"]:
        errors.append("FROZEN_288_INPUTS_OR_WATERMARKS_CHANGED")
    if len(cohort) != 288 or len({m["market"] for m in cohort}) != 288 or len(rows) > 288:
        raise ValueError("EXACT_288_UNIQUE_FIXED_MARKETS_REQUIRED")
    if not selected_valid or proof.get("expected_source_root") != selection["source_root"] or proof.get("status") != "MATCH":
        errors.append("SELECTED_CONFIGURATION_OR_RESOURCE_SOURCE_MISMATCH")
    selected_proof = read(root / selected_run / "import-proof.json", {})
    if proof.get("loaded_modules") != selected_proof.get("loaded_modules"):
        errors.append("RESOURCE_IMPORTED_MODULES_DIFFER_FROM_SELECTED_BUILD")
    if [r["market"] for r in rows] != [m["market"] for m in cohort[:len(rows)]]:
        errors.append("FIXED_288_ORDER_MISMATCH")
    for index, row in enumerate(rows):
        member = cohort[index]
        database = run / member["market"] / "simulation.sqlite3"
        state, definition, owner = database_state(database)
        owners.add(owner)
        diagnostic = archive(database)
        complete = member_check(member, state, definition, selection)
        durable = (diagnostic["readable"] and diagnostic["rows_match_metadata"]
                   and diagnostic["closed_metadata"] and diagnostic["input_seq"] == member["input_seq"])
        replay = read(database.parent / "replay-state.json")
        resource_ok = (row["index"] == index+1 and row["input_seq"] == member["input_seq"]
                       and row["retained_engines"] == index+1 and row["retained_research_graphs"] == 0
                       and row["retirement_gc_seconds"] == 0 and row["retirement_collected_objects"] == 0
                       and 0 < row["rss_mib"] <= 512 and 0 < row["peak_rss_mib"] <= 512)
        if not (complete and durable and resource_ok and replay == state):
            errors.append("MEMBER_OR_RESOURCE_CHECK_FAILED:"+member["market"])
        checks.append({"market": member["market"], "complete_valid_officially_settled": complete,
                       "run_identity": owner,
                       "same_final_configuration": complete, "diagnostics_durably_closed": bool(durable),
                       "snapshot_matches_committed_state": replay == state, "resource_limits_met": resource_ok})
    if len(owners) > 1 or (owners and None in owners):
        errors.append("MULTIPLE_OR_MISSING_COMMITTED_RUN_IDENTITIES")
    if completion and (completion.get("status") != "PASS" or completion.get("markets") != 288
                       or completion.get("run_identity") != plan.get("run_identity")
                       or owners != {plan.get("run_identity")}
                       or completion.get("memory_release") is not True
                       or plan.get("execution") != "trade-through"
                       or plan.get("frequency_parameters_input_sha256") != selection["parameters_sha256"]
                       or Path(plan.get("source_manifest", "")).resolve() != cohort_path.resolve()):
        errors.append("TERMINAL_OR_FINAL_PARAMETER_IDENTITY_MISMATCH")
    complete = len(rows) == len(checks) == len(cohort) == 288 and completion.get("status") == "PASS"
    result = {"status": "FAIL" if errors else "PASS" if complete else "PARTIAL",
              "verifier_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "run": run_name, "configuration_from_run": selected_run, "execution": "trade-through",
              "checked_markets": len(checks), "expected_markets": 288,
              "source_root": proof.get("expected_source_root"),
              "frequency_parameters_input_sha256": selection["parameters_sha256"],
              "cohort_sha256": cohort_proof["sha256"],
              "peak_rss_mib": max((max(r["rss_mib"], r["peak_rss_mib"]) for r in rows), default=None),
              "first_rss_mib": rows[0]["rss_mib"] if rows else None,
              "last_rss_mib": rows[-1]["rss_mib"] if rows else None,
              "maximum_retained_research_graphs": max((r["retained_research_graphs"] for r in rows), default=None),
              "forced_gc": False, "continuous_single_process": complete,
              "continuous_24h_live_test": False,
              "limitation": "Original-clock accelerated replay across retained sources; artificial coordinator lifecycle is separate. Wall-time network and long scheduling remain untested.",
              "errors": errors, "checks": checks}
    (root / (run_name+"-review.json")).write_text(json.dumps(result, indent=2)+"\n")
    return result


def invariance_review(root, optimized, control):
    selection, plan, valid = configuration(root, optimized)
    _same_selection, control_plan, control_valid = configuration(root, control)
    cohort = read(root / "cohort25.json")["markets"]
    if len(cohort) != 25 or len({m["market"] for m in cohort}) != 25:
        raise ValueError("EXACT_25_UNIQUE_FIXED_MARKETS_REQUIRED")
    errors, checks = [], []
    if (not valid or not control_valid or plan["settings"] != control_plan["settings"]
            or plan["run_identity"] == control_plan["run_identity"]):
        errors.append("SAME_IMPLEMENTATION_AND_RULES_WITH_DISTINCT_RUN_IDENTITIES_REQUIRED")
    if (read(root / optimized / "completion.json").get("memory_release") is not True
            or read(root / control / "completion.json").get("memory_release") is not False):
        errors.append("RETIREMENT_COMPARISON_SCOPE_MISMATCH")
    for member in cohort:
        old_path = root / control / member["market"] / "simulation.sqlite3"
        new_path = root / optimized / member["market"] / "simulation.sqlite3"
        old, old_definition, old_owner = database_state(old_path)
        new, new_definition, new_owner = database_state(new_path)
        audit = compare(old_path, new_path, legacy_annotations=False)
        settled = (member_check(member, old, old_definition, selection)
                   and member_check(member, new, new_definition, selection))
        owners_match = old_owner == control_plan["run_identity"] and new_owner == plan["run_identity"]
        if not (old == new and old_definition == new_definition and settled and owners_match
                and audit["equivalent_after_explicit_annotation_normalization"]):
            errors.append("SAME_CONFIG_ECONOMIC_OR_AUDIT_DIFFERENCE:"+member["market"])
        checks.append({"market": member["market"], "snapshots_identical": old == new,
                       "committed_run_identities_match": owners_match,
                       "definitions_identical": old_definition == new_definition,
                       "both_complete_valid_settled": settled, "audit": audit})
    result = {"status": "FAIL" if errors else "PASS", "optimized_run": optimized,
              "verifier_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "control_run": control, "checked_markets": len(checks),
              "implementation_and_frequency_identical": valid and control_valid and plan["settings"] == control_plan["settings"],
              "normalization": "Candidate run identity and same-input fact-end sibling order only; no parameter/definition/economic differences ignored.",
              "errors": errors, "checks": checks}
    (root / ("invariance-"+optimized+".json")).write_text(json.dumps(result, indent=2)+"\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--configuration-run", default="selected25-v7")
    parser.add_argument("--resource-run", default="memory288-selected-v7")
    parser.add_argument("--control-run", default="unretired25-v7")
    parser.add_argument("--invariance", action="store_true")
    args = parser.parse_args()
    result = (invariance_review(args.directory.resolve(), args.configuration_run, args.control_run)
              if args.invariance else resource_review(args.directory.resolve(), args.resource_run, args.configuration_run))
    print(json.dumps({k: v for k, v in result.items() if k != "checks"}, indent=2))
