"""Freeze a frequency-only choice after full development replay, without starting it."""

import argparse
import hashlib
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from m1223_gates import strategy_schema, validate_selection
from m1223_prepare import write
from m1223_replay import file_hash, validate_dataset


def freeze_selection(dataset_path, frequency_path, runtime, *, reason):
    if not reason.strip():
        raise ValueError("EXPLICIT_SEMANTIC_AND_FREQUENCY_SELECTION_REASON_REQUIRED")
    dataset = json.loads(dataset_path.read_text())
    records = validate_dataset(dataset)
    frequency = json.loads(frequency_path.read_text())
    manifest = json.loads((runtime / "frozen-manifest.json").read_text())
    for rel, expected in manifest.items():
        source = (runtime / rel).resolve()
        if not source.is_relative_to(runtime.resolve()) or file_hash(source) != expected:
            raise ValueError("SELECTION_FROZEN_SOURCE_CHANGED:" + rel)
    fingerprint = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    if (
        frequency.get("owner", {}).get("mode") != "OFFLINE_DEVELOPMENT_REPLAY"
        or frequency["owner"].get("dataset_sha256") != file_hash(dataset_path)
        or Path(frequency["owner"].get("runtime", "")).resolve() != runtime.resolve()
        or runtime.name != fingerprint[:16]
    ):
        raise ValueError("MATCHING_COMPLETE_DEVELOPMENT_REPLAY_AND_FROZEN_RUNTIME_REQUIRED")
    env = {
        **os.environ,
        "PYTHONPATH": str(runtime / "extension") + ":" + str(runtime / "src"),
    }
    # This imports only the selected immutable engine, and creates no API client.
    check = subprocess.run(
        [
            str(Path("/home/ubuntu/polymaker/.venv/bin/python")),
            "-c",
            "import json; from pathlib import Path; from m1223_sim import definition; "
            "from sop_signals.parameters import load_revision; "
            "p=Path('config/m1223-parameters.json'); "
            "d=json.loads(p.read_text()); parameters,revision=load_revision(d); "
            "print(json.dumps({'sop':definition(parameters),'revision':revision}))",
        ],
        cwd=runtime,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    selected = json.loads(check.stdout)
    if frequency.get("revision") != selected["revision"]:
        raise ValueError("REPLAYED_PARAMETERS_DIFFER_FROM_FROZEN_SELECTION")
    if selected["sop"]["strategy"] != dataset.get("strategy", "M12"):
        raise ValueError("SELECTION_DATASET_STRATEGY_MISMATCH")
    result = {
        "schema": strategy_schema(selected["sop"], "frequency-selection-v1"),
        "sample_role": "DEVELOPMENT_ONLY",
        "selection_uses_pnl": False,
        "reason": reason,
        "selected_sop_definition": selected["sop"],
        "development_market_epochs": [int(r["market"].rsplit("-", 1)[1]) for r in records],
        "frequency_report": {"path": str(frequency_path), "sha256": file_hash(frequency_path)},
        "dataset": {"path": str(dataset_path), "sha256": file_hash(dataset_path)},
        "selected_runtime": str(runtime),
        "selected_runtime_sha256": fingerprint,
        "revision": selected["revision"],
        "selected_beijing": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "is_independent_validation": False,
        "admission_granted_for_24h": False,
    }
    validate_selection(result, selected["sop"])
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--frequency", required=True, type=Path)
    parser.add_argument("--runtime", required=True, type=Path)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("NEW_SELECTION_IDENTITY_REQUIRED")
    result = freeze_selection(
        args.dataset.resolve(), args.frequency.resolve(), args.runtime.resolve(), reason=args.reason
    )
    write(args.output, result)
    print(json.dumps({"selection": str(args.output), "revision": result["revision"]}))
