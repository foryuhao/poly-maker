"""Freeze a completed, predeclared 24-market development dataset without selection."""

import argparse
import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path


def sha256(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def validate_cohort(cohort):
    epochs = cohort.get("market_epochs")
    if (
        cohort.get("strategy") not in {"M12", "M13", "M14", "M15", "M16", "M17", "M18", "M19", "M23"}
        or cohort.get("stage") != "DEVELOPMENT_CAPTURE"
        or not isinstance(epochs, list)
        or len(epochs) != 24
        or any(type(e) is not int or e % 300 for e in epochs)
        or epochs != list(range(epochs[0], epochs[0] + 24 * 300, 300))
        or cohort.get("markets") != [f"btc-updown-5m-{e}" for e in epochs]
    ):
        raise ValueError("PREDECLARED_24_CONSECUTIVE_M12_DEVELOPMENT_MARKETS_REQUIRED")
    return cohort["markets"]


def freeze_dataset(capture, cohort_path):
    from polymaker.m1234_sim.runstate import inspect_run
    from polymaker.m1234_sim.validity import database_validity

    capture, cohort_path = capture.resolve(), cohort_path.resolve()
    cohort = json.loads(cohort_path.read_text())
    markets = validate_cohort(cohort)
    plan = json.loads((capture / "plan.json").read_text())
    terminal = json.loads((capture / "completion.json").read_text())
    cohort_hash = sha256(cohort_path)
    if (
        plan.get("mode") != "SIMULATION_ONLY"
        or plan["settings"].get("sop", {}).get("groups") != [cohort["strategy"]]
        or plan["settings"].get("sop", {}).get("strategy") != cohort["strategy"]
        or plan["settings"].get("sop_cohort_sha256") != cohort_hash
        or plan["settings"].get("sop_cohort") != cohort
        or plan["settings"]["max_markets"] != 24
        or terminal.get("status") != "PASS"
        or terminal.get("run_identity") != plan.get("run_identity")
        or terminal.get("primary_error")
        or terminal.get("cleanup_errors")
    ):
        raise ValueError("MATCHING_COMPLETED_CAPTURE_REQUIRED_NO_FAILED_RUN_STITCHING")
    if inspect_run(capture)["status"] != "PASS":
        raise ValueError("CAPTURE_RUN_AND_ALL_MEMBER_BINDINGS_MUST_VERIFY")
    actual = {p.name for p in capture.glob("btc-*") if p.is_dir()}
    if actual != set(markets):
        raise ValueError("DECLARED_MARKETS_MISSING_OR_EXTRA_NO_REPLACEMENTS")
    records = []
    for market in markets:
        directory = capture / market
        database = directory / "simulation.sqlite3"
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=1) as db:
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            row = db.execute("SELECT payload FROM snapshots WHERE name='state'").fetchone()
            if row is None:
                raise ValueError("SOURCE_NOT_SEALED:" + market)
            state = json.loads(row[0])
            validity = database_validity(database, state, connection=db)
            ended = db.execute("SELECT 1 FROM audit WHERE kind='OBSERVATION_END'").fetchone()
            audit_watermark = db.execute("SELECT MAX(input_seq),MAX(seq) FROM audit").fetchone()
            definition = json.loads(
                db.execute("SELECT payload FROM audit WHERE kind='DEFINITION'").fetchone()[0]
            )["definition"]
            gate_row = db.execute(
                "SELECT payload FROM metadata WHERE name='research_sample_eligibility'"
            ).fetchone()
        if (
            definition.get("artificial_fixture")
            or validity["status"] != "VALID"
            or not ended
            or state["market"]["slug"] != market
            or definition["settings"].get("sop_cohort_sha256") != cohort_hash
        ):
            raise ValueError("UNVERIFIED_OR_FOREIGN_MARKET:" + market)
        raw = directory / "input.jsonl.gz"
        if not raw.is_file():
            raise ValueError("ORIGINAL_RAW_DATA_UNAVAILABLE:" + market)
        records.append(
            {
                "market": market,
                "source": str(directory),
                "source_input": str(raw),
                "source_input_sha256": sha256(raw),
                "source_input_bytes": raw.stat().st_size,
                "source_database_sha256": sha256(database),
                "snapshot_input_seq": state["input_seq"],
                "audit_input_seq": audit_watermark[0],
                "audit_seq": audit_watermark[1],
                "complete_observed_window": state["coverage"].get("complete_window") is True,
                "source_validity": validity["status"],
                "official_result": state["resolution"],
                "recorded_eligibility": json.loads(gate_row[0]) if gate_row else None,
            }
        )
    return {
        "schema": cohort["strategy"].lower() + "-predeclared-development-dataset-v1",
        "sample_role": "DEVELOPMENT_ONLY",
        "strategy": cohort["strategy"],
        "capture_sop_definition": plan["settings"]["sop"],
        "denominator": 24,
        "capture": str(capture),
        "capture_run_identity": plan["run_identity"],
        "cohort_path": str(cohort_path),
        "cohort_sha256": cohort_hash,
        "capture_plan_sha256": sha256(capture / "plan.json"),
        "capture_terminal_sha256": sha256(capture / "completion.json"),
        "selection": "ALL_PREDECLARED_MARKETS_IN_ORIGINAL_ORDER_NO_PNL_OR_SIGNAL_SELECTION",
        "markets": records,
        "raw_data_copied": False,
        "retention": "ORIGINAL_INPUTS_CURRENTLY_PRESENT_REQUIRED_FOR_REPLAY_HASHES_ARE_NOT_DATA",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", required=True, type=Path)
    parser.add_argument("--cohort", required=True, type=Path)
    parser.add_argument("--unit", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    props = subprocess.run(
        ["systemctl", "--user", "show", args.unit, "-p", "ActiveState", "-p", "MainPID"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    if (
        "MainPID=0" not in props
        or "ActiveState=active" in props
        or "ActiveState=activating" in props
    ):
        raise ValueError("SOURCE_SERVICE_MUST_HAVE_FINISHED_BEFORE_DATASET_FREEZE")
    if args.output.exists():
        raise ValueError("DATASET_FREEZE_ALREADY_EXISTS_DO_NOT_OVERWRITE")
    dataset = freeze_dataset(args.capture, args.cohort)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "dataset": str(args.output),
                "markets": len(dataset["markets"]),
                "sha256": sha256(args.output),
                "role": dataset["sample_role"],
            }
        )
    )


if __name__ == "__main__":
    main()
