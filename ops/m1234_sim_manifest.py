"""Hash frozen simulation evidence and explicitly selected public source files."""

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path


def file_hash(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    root = Path(args.evidence).resolve()
    project = Path(__file__).resolve().parents[1]
    output = root / "manifest.json"
    files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path == output or path.name.endswith(("-wal", "-shm", ".tmp")):
            continue
        files.append(
            {
                "path": str(path.relative_to(root)),
                "bytes": path.stat().st_size,
                "sha256": file_hash(path),
            }
        )
    selected = list((project / "src/polymaker").rglob("*.py"))
    selected += list((project / "tests").glob("test_m1234_sim*.py"))
    selected += [
        project / p
        for p in (
            "tests/m1234_fixtures.py",
            "config/m1234-sim.toml",
            "pyproject.toml",
            "ops/m1234_sim_stop.py",
            "ops/m1234_sim_verify.py",
            "ops/m1234_sim_manifest.py",
            "ops/m1234_sim_progress.py",
            "ops/m1234_sim_compare.py",
            "docs/development/M1234_TRADING_SIMULATION_PROGRESS.md",
            "docs/development/M1234_TRADING_SIMULATION_DESIGN_20261105.md",
            "docs/development/M1234_TRADING_SIMULATION_WORK_ITEMS_20261105.md",
        )
    ]
    sources = [
        {"path": str(p), "bytes": p.stat().st_size, "sha256": file_hash(p)}
        for p in sorted(selected)
    ]
    value = {
        "schema": 1,
        "mode": "SIMULATION_ONLY",
        "created_utc": datetime.now(UTC).isoformat(),
        "evidence_root": str(root),
        "selected_public_run": args.run_id,
        "git_head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=project, text=True
        ).strip(),
        "baseline": "baseline.json",
        "contract": "contract.json",
        "source_archive": f"sources/{args.run_id}-source.tar.gz",
        "files": files,
        "current_public_source_files": sources,
        "sqlite": "snapshot.sqlite3 files produced with SQLite backup after committing; never raw copies omitting WAL",
        "excluded": "mutable WAL/SHM/temp sidecars, credentials, wallet files, environment values; this manifest is not self-hashed",
        "calibration_status": "UNCALIBRATED",
    }
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(output)
    print(
        json.dumps(
            {"manifest": str(output), "evidence_files": len(files), "source_files": len(sources)}
        )
    )


if __name__ == "__main__":
    main()
