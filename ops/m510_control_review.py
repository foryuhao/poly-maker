"""Check explicit configuration transport and an independent same-v5 control."""

import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path


def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def signatures(database):
    with closing(sqlite3.connect(database.resolve().as_uri()+"?mode=ro", uri=True)) as db:
        rows = [json.loads(p)["payload"] for p, in db.execute(
            "SELECT payload FROM audit WHERE kind='SIGNAL' AND json_extract(payload,'$.event')='CANDIDATE'")]
    return sorted(tuple(r[k] for k in ("strategy", "token", "confirmed", "deadline", "anchor_price"))
                  for r in rows)


def review(root):
    frozen_plan = read(root / "frequency-control-v5-plan.json")
    freeze = read(root / "frequency-configuration-freeze.json")
    source = Path(freeze["source_root"])
    changed = [name for name, digest in read(source / "frozen-source.json").items()
               if hashlib.sha256((source / name).read_bytes()).hexdigest() != digest]
    params_path = root / frozen_plan["parameters"]
    parameter_hash_matches = hashlib.sha256(params_path.read_bytes()).hexdigest() == frozen_plan["parameters_sha256"]
    parameters = read(params_path)
    members = read(root / "cohort25.json")["markets"]
    grid = {r["market"]: r for r in read(root / "frequency-grid-phase3-v2/progress.json",
                                       {"markets": []})["markets"]}
    run = root / "control25-config-v5"
    proof = read(run / "import-proof.json", {})
    imports_ok = (proof.get("status") == "MATCH" and proof.get("expected_source_root") == str(source)
                  and proof.get("loaded_modules") == {
                      "m511_sim": str(source / "extension/m511_sim.py"),
                      "runner": str(source / "src/polymaker/m1234_sim/runner.py")})
    checks, errors = [], []
    if changed or not imports_ok or not parameter_hash_matches:
        errors.append("SOURCE_IMPORT_OR_PARAMETER_HASH_MISMATCH")
    for member in members:
        market = member["market"]
        state = read(run / market / "replay-state.json")
        if state is None:
            continue
        dbfile = run / market / "simulation.sqlite3"
        with closing(sqlite3.connect(dbfile.resolve().as_uri()+"?mode=ro", uri=True)) as db:
            definition = json.loads(db.execute(
                "SELECT payload FROM audit WHERE kind='DEFINITION' ORDER BY seq LIMIT 1"
            ).fetchone()[0])["definition"]["settings"]["m5_m11"]
        configuration_ok = (definition["frequency_parameters"] == parameters
                            and definition["signal_variant"] == frozen_plan["signal_variant"])
        member_ok = (state["input_seq"] == member["input_seq"] and state["resolution"]
                     and state["resolution"]["winner"] == member["winner"]
                     and state["coverage"]["complete_window"] and state["validity"]["status"] == "VALID"
                     and set(state["brokers"]) == {"research/trade-through"})
        actual = signatures(dbfile)
        current_grid = grid.get(market)
        same_grid = (actual == sorted(tuple(p[k] for k in (
            "strategy", "token", "confirmed", "deadline", "anchor_price"))
            for p in current_grid["profiles"]["control_v4"]["signals"])) if current_grid else None
        same_v4 = actual == signatures(root / "selected25-v4" / market / "simulation.sqlite3")
        if not configuration_ok or not member_ok or same_grid is False:
            errors.append("MEMBER_OR_INDEPENDENT_CONTROL_MISMATCH:"+market)
        checks.append({"market": market, "configuration_matches_frozen_file": configuration_ok,
                       "complete_valid_settled": bool(member_ok), "same_grid_primary": same_grid,
                       "same_previous_v4_physical_candidates": same_v4})
    terminal, plan = read(run / "completion.json", {}), read(run / "plan.json", {})
    if terminal and (terminal.get("status") != "PASS" or terminal.get("markets") != 25
                     or terminal.get("run_identity") != plan.get("run_identity")
                     or plan.get("frequency_parameters_input_sha256") != frozen_plan["parameters_sha256"]):
        errors.append("TERMINAL_OR_CAPTURED_PARAMETER_HASH_MISMATCH")
    all_grid = len(checks) == 25 and all(c["same_grid_primary"] is True for c in checks)
    complete = all_grid and terminal.get("status") == "PASS"
    result = {"status": "FAIL" if errors else "PASS" if complete else "PARTIAL",
              "checked_members": len(checks), "expected_members": 25,
              "checked_grid_controls": sum(c["same_grid_primary"] is not None for c in checks),
              "source_and_parameter_file_verified": not changed and imports_ok and parameter_hash_matches,
              "previous_v4_physical_changes": [c["market"] for c in checks
                                               if not c["same_previous_v4_physical_candidates"]],
              "previous_v4_changes_require_explanation_before_promotion": True,
              "selection_uses_pnl": False, "errors": errors, "checks": checks}
    (root / "control25-config-v5-review.json").write_text(json.dumps(result, indent=2)+"\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    result = review(args.directory.resolve())
    print(json.dumps({k: v for k, v in result.items() if k != "checks"}, indent=2))
