"""Freeze training selection prospectively while the capture continues, then audit holdout."""

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def snapshot(src, dst, end):
    dst.mkdir(exist_ok=False)
    for name in ("markets.json", "discovery_raw.json", "fees_provenance.json"):
        shutil.copy2(src / name, dst / name)
    for name in ("raw.jsonl", "trades.jsonl", "quotes.jsonl"):
        with (dst / name).open("w") as f:
            for line in (src / name).open():
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    break
                if r["received"] <= end:
                    f.write(line)


def run(script, *args):
    subprocess.run([sys.executable, str(HERE / script), *map(str, args)], check=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("root", type=Path)
    a = p.parse_args()
    root = a.root
    while not (root / "raw.jsonl").exists() or not (root / "raw.jsonl").stat().st_size:
        time.sleep(10)
    start = json.loads((root / "raw.jsonl").open().readline())["received"]
    # Wait for receipt-time boundary without reading any validation prices.
    while time.time() < start + 905:
        time.sleep(min(20, start + 905 - time.time()))
    train = root.with_name(root.name + "_training_frozen")
    if not (root / "frozen_selection.json").exists():
        snapshot(root, train, start + 900)
        run("audit.py", train)
        run("execution_study.py", train, "--train-only")
        with (root / "frozen_selection.json").open("x") as out:
            row = json.loads((train / "frozen_selection.json").read_text())
            row["frozen_at"] = time.time()
            row["training_snapshot"] = str(train)
            json.dump(row, out, indent=2)
    while not (root / "collection.json").exists():
        time.sleep(20)
    run("execution_study.py", root)
    run("validate_edge.py", root)
    (root / "staged_complete.json").write_text(
        json.dumps(
            dict(
                completed_at=time.time(),
                goal_status="ACTIVE_UNLESS_INDEPENDENT_CONFIRMATION_AND_COSTS_PASS",
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
