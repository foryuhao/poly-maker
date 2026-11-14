"""Immutable source/extension bundle for an additive recording follower."""

import argparse
import hashlib
import json
import shutil
import tarfile
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from m1234_sim_frozen_source import materialize

PROJECT = Path(__file__).resolve().parents[1]
FILES = (
    "m510_trade_through.py",
    "m511_sim.py",
    "m511_sim_follow.py",
    "m511_sim_forward.py",
    "m511_sim_metrics.py",
    "m511_sim_instant_fill.py",
    "m511_sample_quality.py",
    "m234_sim_main.py",
    "m234_sim_main_storage.py",
    "m1234_sim_lifetime.py",
    "m1234_sim_metrics.py",
    "m234_sim_main_instant_fill.py",
)


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def prepare(source):
    cache = PROJECT / "research_outputs/m1234_sim/runtime"
    base, _ = materialize(source, cache)
    identity = json.loads((source / "launch/implementation.json").read_text())
    changed = "src/polymaker/m1234_sim/report.py"
    identity[changed] = sha(PROJECT / changed)
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    root = cache / digest[:16]
    for name in (*identity, "pyproject.toml"):
        target = root / name
        content = (PROJECT / name if name == changed else base / name).read_bytes()
        if target.exists() and target.read_bytes() != content:
            raise ValueError("SOURCE_COLLISION:" + name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    label = (
        datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:6]
    )
    out = PROJECT / "research_outputs/m511_sim/follow" / label
    launch = out / "launch"
    ext = launch / "extension"
    ext.mkdir(parents=True)
    for name in FILES:
        shutil.copyfile(PROJECT / "ops" / name, ext / name)
    shutil.copytree(
        PROJECT / "research/maker_signals",
        ext / "maker_signals",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    manifest = {str(p.relative_to(ext)): sha(p) for p in ext.rglob("*") if p.is_file()}
    (launch / "extension-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (launch / "implementation.json").write_text(json.dumps(identity, indent=2) + "\n")
    with tarfile.open(launch / "source.tar.gz", "w:gz") as a:
        for name in (*identity, "pyproject.toml"):
            a.add(root / name, arcname=name)
    cmd = [
        str(PROJECT / ".venv/bin/python"),
        "-u",
        str(ext / "m511_sim_follow.py"),
        "--source-run",
        str(source),
        "--source-root",
        str(root),
        "--output",
        str(out),
    ]
    record = {
        "status": "PREPARED",
        "source_run": str(source),
        "source_root": str(root),
        "directory": str(out),
        "command": cmd,
        "unit": "polymaker-m511-shadow-" + label.lower() + ".service",
        "legacy_services_changed": False,
        "real_orders": 0,
    }
    (launch / "launch.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--source-run", type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(prepare(a.source_run.resolve()), indent=2))
