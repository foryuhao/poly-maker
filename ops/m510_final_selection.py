"""Select a combined offline candidate from completed, predeclared count grids."""

import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

TARGETS = ("M7", "M9", "M10")
FIELDS = ("strategy", "token", "confirmed", "deadline", "anchor_price")


def read(path):
    return json.loads(path.read_text())


def signature(rows):
    return sorted(tuple(row[k] for k in FIELDS) for row in rows)


def choose(priority, counts):
    passing = [name for name in priority if counts[name] >= 10]
    return passing[0] if passing else max(priority, key=counts.__getitem__)


def m7_pause_is_exhaustion(parameters):
    """A shorter pause must not admit a higher average BUY rate than AP1.

    Shared Pressure observes AP1 over 750ms; an opt-in local AP declares its
    actual duration. This is a necessary semantic screen,
    not a substitute for either bin cap or the fresh RF/AP2 evidence stages.
    """
    duration = Decimal(str(parameters.get("pause_seconds", .5)))
    total = Decimal(str(parameters.get("pause_total_fraction", ".50")))
    attack = Decimal(str(parameters.get("ap_window_seconds", .750)))
    return (duration.is_finite() and total.is_finite() and attack.is_finite()
            and 0 < attack*total < duration)


def select(root, *, emit=True, include_local=True):
    """No PnL is read; a below-target fallback is explicitly review-only."""
    phase3, phase4 = (read(root / f"phase{i}-grid-review.json") for i in (3, 4))
    has_phase5 = (root / "phase5-window-rule.json").exists()
    phase5 = read(root / "phase5-grid-review.json") if has_phase5 else None
    has_phase6 = include_local and (root / "phase6-local-ap-rule.json").exists()
    phase6 = read(root / "phase6-grid-review.json") if has_phase6 else None
    control = read(root / "control25-config-v5-review.json")
    for report, expected_status in ((phase3, "COMPLETE"), (phase4, "COMPLETE"), (control, "PASS")):
        if report["status"] != expected_status or report.get("errors"):
            raise ValueError("COMPLETE_INDEPENDENT_GRIDS_AND_CONTROL_REQUIRED")
    if phase5 is not None and (phase5["status"] != "COMPLETE" or phase5.get("errors")):
        raise ValueError("COMPLETE_INDEPENDENT_PHASE5_REQUIRED")
    if phase6 is not None and (phase6["status"] != "COMPLETE" or phase6.get("errors")):
        raise ValueError("COMPLETE_INDEPENDENT_PHASE6_REQUIRED")
    rule3 = read(root / "phase3-frequency-rule.json")
    rule4 = read(root / "phase4-failed-root-rule.json")
    rule5 = read(root / "phase5-window-rule.json") if has_phase5 else None
    rule6 = read(root / "phase6-local-ap-rule.json") if has_phase6 else None
    cohort_path = root / "cohort25.json"
    cohort_sha = hashlib.sha256(cohort_path.read_bytes()).hexdigest()
    if cohort_sha != rule3["cohort_sha256"] or cohort_sha != rule4["cohort_sha256"]:
        raise ValueError("FIXED_COHORT_CHANGED")
    if rule5 is not None and cohort_sha != rule5["cohort_sha256"]:
        raise ValueError("FIXED_COHORT_CHANGED")
    if rule6 is not None and cohort_sha != rule6["cohort_sha256"]:
        raise ValueError("FIXED_COHORT_CHANGED")
    rows3 = read(root / "frequency-grid-phase3-v2/progress.json")["markets"]
    rows4 = read(root / "frequency-grid-phase4/progress.json")["markets"]
    rows5 = read(root / "frequency-grid-phase5/progress.json")["markets"] if has_phase5 else None
    rows6 = read(root / "frequency-grid-phase6/progress.json")["markets"] if has_phase6 else None
    markets = [m["market"] for m in read(cohort_path)["markets"]]
    if ([r["market"] for r in rows3] != markets or [r["market"] for r in rows4] != markets):
        raise ValueError("EXACT_25_MARKETS_IN_ORIGINAL_ORDER_REQUIRED")
    if rows5 is not None and [r["market"] for r in rows5] != markets:
        raise ValueError("EXACT_25_MARKETS_IN_ORIGINAL_ORDER_REQUIRED")
    if rows6 is not None and [r["market"] for r in rows6] != markets:
        raise ValueError("EXACT_25_MARKETS_IN_ORIGINAL_ORDER_REQUIRED")
    # Prefer the already-declared numeric profiles if they meet the target,
    # before using the additional explicit state-machine experiment. A failed
    # numeric profile must not hide a passing numeric configuration elsewhere.
    selected, source_grids, expected_counts, excluded = {}, {}, {}, []
    for strategy in TARGETS:
        options = [("phase3", p, phase3["counts"][p][strategy]) for p in rule3["priority"]]
        if has_phase5:
            options += [("phase5", p, phase5["counts"][strategy][p])
                        for p in rule5["priority_by_strategy"][strategy]]
        if strategy == "M7":
            options += [("phase4", p, phase4["M7_signals_by_profile"][p]) for p in rule4["priority"]]
        if has_phase6 and strategy in rule6["priority_by_strategy"]:
            options += [("phase6", p, phase6["counts"][strategy][p])
                        for p in rule6["priority_by_strategy"][strategy]]
        if strategy == "M7":
            eligible = []
            for grid, profile, count in options:
                parameters = {"phase3": rows3, "phase4": rows4, "phase5": rows5,
                              "phase6": rows6}[grid][0]["profiles"][profile]["parameters"]["M7"]
                if m7_pause_is_exhaustion(parameters):
                    eligible.append((grid, profile, count))
                else:
                    excluded.append({"strategy": "M7", "grid": grid, "profile": profile,
                                     "reason": "PAUSE_CAN_ADMIT_NONDECREASING_AVERAGE_BUY_RATE",
                                     "parameters": parameters})
            options = eligible
            if not options:
                raise ValueError("NO_SEMANTICALLY_ELIGIBLE_M7_PROFILE")
        passing = [option for option in options if option[2] >= 10]
        grid, profile, count = passing[0] if passing else max(options, key=lambda v: v[2])
        selected[strategy], source_grids[strategy], expected_counts[strategy] = profile, grid, count

    def source_rows(strategy):
        return {"phase3": rows3, "phase4": rows4, "phase5": rows5, "phase6": rows6}[source_grids[strategy]]

    def final_parameters(strategy, profile):
        result = deepcopy(profile["parameters"][strategy])
        if strategy == "M7":
            result.setdefault("restart_after_failed_attack", False)
        if has_phase6 and strategy in ("M7", "M10"):
            result.setdefault("ap_window_seconds", .75)
        return result

    parameters = {s: final_parameters(s, source_rows(s)[0]["profiles"][selected[s]]) for s in TARGETS}
    expected, totals = {}, {s: 0 for s in TARGETS}
    for index, market in enumerate(markets):
        primary_row = rows6[index] if has_phase6 else rows5[index] if has_phase5 else rows4[index]
        primary_name = rule6["primary"] if has_phase6 else rule5["primary"] if has_phase5 else rule4["primary"]
        combined = [s for s in primary_row["profiles"][primary_name]["signals"]
                    if s["strategy"] not in TARGETS]
        for strategy in TARGETS:
            row = source_rows(strategy)[index]
            p = row["profiles"][selected[strategy]]
            if final_parameters(strategy, p) != parameters[strategy]:
                raise ValueError("PARAMETERS_CHANGED_BETWEEN_MEMBERS")
            values = [s for s in p["signals"] if s["strategy"] == strategy]
            totals[strategy] += len(values)
            combined.extend(values)
        expected[market] = signature(combined)
    if totals != expected_counts:
        raise ValueError("SELECTED_COUNTS_DISAGREE_WITH_REVIEWED_PROFILES")
    parameter_text = json.dumps(parameters, indent=2)+"\n"
    freeze = read(root / ("phase6-freeze.json" if has_phase6 else "phase5-freeze.json" if has_phase5 else "phase4-freeze.json"))
    record = {"status": "SELECTED_FOR_FULL_ECONOMIC_REVIEW", "cohort_sha256": cohort_sha,
              "selection_tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "source_root": freeze["source_root"], "source_manifest_sha256": freeze["manifest_sha256"],
              "parameters_sha256": hashlib.sha256(parameter_text.encode()).hexdigest(),
              "signal_variant": rule6["signal_variant"] if has_phase6 else rule4["signal_variant"],
              "profiles": selected, "source_grids": source_grids,
              "frequency_parameters": parameters, "expected_counts": totals,
              "frequency_target_met": all(n >= 10 for n in totals.values()),
              "semantic_exclusions": excluded,
              "selection_uses_pnl": False, "new_long_test_authorized": False,
              "fallback_rule": "First predeclared profile >=10; otherwise highest count, earlier priority tie. Below-target selections remain review-only.",
              "expected_signatures": expected}
    output = root / "combined-selection-v6.json"
    if output.exists() and read(output) != json.loads(json.dumps(record)):
        raise ValueError("EXISTING_SELECTION_MUST_NOT_BE_OVERWRITTEN")
    parameter_path = root / "combined-parameters-v6.json"
    if parameter_path.exists() and parameter_path.read_text() != parameter_text:
        raise ValueError("EXISTING_PARAMETERS_CHANGED")
    if emit:
        output.write_text(json.dumps(record, indent=2)+"\n")
        parameter_path.write_text(parameter_text)
    return record


def verify(root, run_name):
    selection = read(root / "combined-selection-v6.json")
    run = root / run_name
    completion, plan = read(run / "completion.json"), read(run / "plan.json")
    if (completion.get("status") != "PASS" or completion.get("markets") != 25
            or completion.get("run_identity") != plan.get("run_identity")):
        raise ValueError("COMPLETE_COMBINED_REPLAY_REQUIRED")
    source = Path(selection["source_root"])
    hashes_file = source / "frozen-source.json"
    hashes = read(hashes_file)
    proof = read(run / "import-proof.json")
    if (hashlib.sha256(hashes_file.read_bytes()).hexdigest() != selection["source_manifest_sha256"]
            or any(hashlib.sha256((source / p).read_bytes()).hexdigest() != h for p, h in hashes.items())
            or proof.get("status") != "MATCH" or proof.get("expected_source_root") != str(source)
            or proof.get("loaded_modules") != {
                "m511_sim": str(source / "extension/m511_sim.py"),
                "runner": str(source / "src/polymaker/m1234_sim/runner.py")}
            or plan.get("frequency_parameters_input_sha256") != selection["parameters_sha256"]):
        raise ValueError("COMBINED_SOURCE_OR_PARAMETER_HASH_MISMATCH")
    cohort = read(root / "cohort25.json")["markets"]
    if list(selection["expected_signatures"]) != [m["market"] for m in cohort]:
        raise ValueError("EXPECTED_COHORT_CHANGED")
    checks = []
    for member in cohort:
        market = member["market"]
        expected = selection["expected_signatures"][market]
        database = run / market / "simulation.sqlite3"
        with closing(sqlite3.connect(database.resolve().as_uri()+"?mode=ro", uri=True)) as db:
            actual = [json.loads(p)["payload"] for p, in db.execute(
                "SELECT payload FROM audit WHERE kind='SIGNAL' AND json_extract(payload,'$.event')='CANDIDATE'")]
            definition = json.loads(db.execute(
                "SELECT payload FROM audit WHERE kind='DEFINITION' ORDER BY seq LIMIT 1"
            ).fetchone()[0])["definition"]["settings"]["m5_m11"]
        state = read(run / market / "replay-state.json")
        configured = (definition["frequency_parameters"] == selection["frequency_parameters"]
                      and definition["signal_variant"] == selection["signal_variant"])
        eligible = (state["input_seq"] == member["input_seq"] and state["coverage"]["complete_window"]
                    and state["resolution"] and state["resolution"]["winner"] == member["winner"]
                    and state["validity"]["status"] == "VALID"
                    and set(state["brokers"]) == {"research/trade-through"})
        matches = signature(actual) == [tuple(v) for v in expected]
        checks.append({"market": market, "same_independent_selected_candidates": matches,
                       "configuration_matches": configured, "complete_valid_settled": bool(eligible)})
    result = {"status": "PASS_MATCHED_CALIBRATION" if all(
        c["same_independent_selected_candidates"] and c["configuration_matches"]
        and c["complete_valid_settled"] for c in checks) else "FAIL",
        "run": run_name, "checked_markets": len(checks), "checks": checks,
        "frequency_target_met": selection["frequency_target_met"]}
    (root / f"{run_name}-signature-review.json").write_text(json.dumps(result, indent=2)+"\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--verify-run")
    mode.add_argument("--preview", action="store_true", help="Require all declared grids; return selection without creating parameter/selection files")
    args = parser.parse_args()
    value = (verify(args.directory.resolve(), args.verify_run) if args.verify_run
             else select(args.directory.resolve(), emit=not args.preview))
    print(json.dumps({k: v for k, v in value.items() if k not in ("expected_signatures", "checks")}, indent=2))
