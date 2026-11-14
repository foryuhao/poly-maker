"""Refresh the explicitly required progress file from the current public run."""

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    root = Path(args.evidence).resolve()
    run = root / "live" / args.run_id
    report = (
        json.loads((run / "latest.json").read_text())
        if (run / "latest.json").exists()
        else {"complete_windows": 0, "markets": []}
    )
    replay_file = root / "replay/public-full-01/comparison.json"
    replay = json.loads(replay_file.read_text()) if replay_file.exists() else {}
    replay_status = (
        replay.get("status", "NOT_RUN")
        if replay.get("run_id") == args.run_id
        else "NOT_RUN_FOR_CURRENT_RUN"
    )
    acceptance_file = root / "reconciliation/public-acceptance.json"
    acceptance = json.loads(acceptance_file.read_text()) if acceptance_file.exists() else {}
    accepted = (
        acceptance.get("status") == "PASS"
        and Path(acceptance.get("run", ".")).resolve() == run
        and replay_status.startswith("PASS")
    )
    final_selection = root / "final/selection.json"
    if final_selection.exists() and (root / "final/latest.json").exists():
        selection = json.loads(final_selection.read_text())
        if selection.get("run_id") == args.run_id:
            report = json.loads((root / "final/latest.json").read_text())
    value = {
        "beijing": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "run": args.run_id,
        "complete_windows": report["complete_windows"],
        "target": 12,
        "official_results": sum(m["official_result"] is not None for m in report["markets"]),
        "status": "PUBLIC_ENGINEERING_ACCEPTED"
        if accepted
        else "COLLECTING"
        if not (run / "completion.json").exists()
        else "COLLECTION_ENDED_VALIDATION_PENDING",
        "public_full_replay": replay_status,
        "calibration": "UNCALIBRATED",
    }
    (root / "live/public-progress.json").write_text(json.dumps(value, indent=2) + "\n")
    progress = (
        Path(__file__).resolve().parents[1]
        / "docs/development/M1234_TRADING_SIMULATION_PROGRESS.md"
    )
    marker = "\n## 当前公共试运行状态\n"
    text = progress.read_text().split(marker)[0]
    progress.write_text(
        text + marker + "\n" + json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(value))


if __name__ == "__main__":
    main()
