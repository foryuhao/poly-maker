"""Prospective V3 freeze, initial holdout, and an explicit incomplete-proof verdict."""

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

from run_staged import snapshot
from validate_edge import validate

HERE = Path(__file__).resolve().parent


def run(script, *args):
    subprocess.run([sys.executable, str(HERE / script), *map(str, args)], check=True)


def assess(root):
    data = json.loads((root / "passive_holdout.json").read_text())
    result = validate(data["rows"], selected_tests=4)
    summaries = data["summary"]
    result["execution_screens_pass"] = (
        bool(summaries)
        and all(
            s["n"] >= 20
            and s["blocks"] >= 6
            and s["mean_double_reserve_net"] > 0
            and s["largest_positive_block_fraction"] <= 0.5
            and s["fee_parameters_verified"]
            for s in summaries
        )
        and {s["delay"] for s in summaries} == {0.5, 1.0, 2.0}
    )
    result["independent_confirmation_complete"] = False
    result["research_goal_complete"] = False
    result["reason"] = (
        "Initial passive paper screen only; independent confirmation and fill-assumption audit remain mandatory even if statistics pass."
    )
    (root / "passive_initial_validation.json").write_text(json.dumps(result, indent=2))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("root", type=Path)
    a = p.parse_args()
    root = a.root
    while not (root / "raw.jsonl").exists() or not (root / "raw.jsonl").stat().st_size:
        time.sleep(10)
    start = json.loads((root / "raw.jsonl").open().readline())["received"]
    while time.time() < start + 1805:
        time.sleep(min(20, start + 1805 - time.time()))
    selected = root / "passive_frozen_selection.json"
    train = root.with_name(root.name + "_training_frozen")
    if not selected.exists():
        snapshot(root, train, start + 1800)
        run("passive_study.py", train, "--train-only")
        row = json.loads((train / "passive_frozen_selection.json").read_text())
        row["frozen_at"] = time.time()
        row["training_snapshot"] = str(train)
        row["dependency_sha256"] = {
            name: hashlib.sha256((HERE / name).read_bytes()).hexdigest()
            for name in ("study.py", "execution_study.py", "validate_edge.py")
        }
        with selected.open("x") as f:
            json.dump(row, f, indent=2)
    while not (root / "collection.json").exists():
        time.sleep(20)
    selection = json.loads(selected.read_text())
    for name, digest in selection.get("dependency_sha256", {}).items():
        if hashlib.sha256((HERE / name).read_bytes()).hexdigest() != digest:
            raise RuntimeError("Frozen dependency changed: " + name)
    run("passive_study.py", root)
    assess(root)
    (root / "passive_stage_complete.json").write_text(
        json.dumps(
            dict(
                completed_at=time.time(),
                goal_status="ACTIVE_UNTIL_INDEPENDENT_CONFIRMATION_AND_FILL_AUDIT",
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
