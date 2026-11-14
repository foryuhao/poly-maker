#!/usr/bin/env python3
"""Prepare and launch an isolated main-mode public simulator after validated replay."""
import argparse
import fcntl
import hashlib
import json
import shutil
import subprocess
import tarfile
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from m234_sim_main import experiment_definition
from m1234_sim_frozen_source import materialize

PROJECT = Path(__file__).resolve().parents[1]
MONITOR = PROJECT / "research_outputs/m1234_sim/monitor"
EXTENSIONS = ("m234_sim_main.py", "m234_sim_main_storage.py", "m234_sim_main_forward.py",
              "m1234_sim_lifetime.py", "m234_sim_main_metrics.py", "m1234_sim_metrics.py",
              "m234_sim_main_instant_fill.py")


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temp.replace(path)


def status(unit):
    reply = subprocess.run(["systemctl", "--user", "show", unit, "-p", "ActiveState",
                            "-p", "MainPID"], capture_output=True, text=True, check=True)
    return dict(row.split("=", 1) for row in reply.stdout.splitlines() if "=" in row)


def check_replay(replay):
    manifest, comparison = read(replay / "manifest.json"), read(replay / "comparison.json")
    validation = read(replay / "validation.json")
    if (manifest["experiment"] != experiment_definition() or comparison["status"] != "COMPLETE"
            or validation["status"] != "PASS" or validation["markets"] != len(manifest["sources"])
            or validation["comparison_sha256"] != sha(replay / "comparison.json")):
        raise RuntimeError("MAIN_REPLAY_NOT_VERIFIED")


def prepare(source, replay):
    cache = PROJECT / "research_outputs/m1234_sim/runtime"
    base, _ = materialize(source, cache)
    files = read(source / "launch/implementation.json")
    changed = "src/polymaker/m1234_sim/report.py"
    files[changed] = sha(PROJECT / changed)
    identity = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    root = cache / identity[:16]
    root.mkdir(parents=True, exist_ok=True)
    for name in (*files, "pyproject.toml"):
        target = root / name
        content = (PROJECT / name if name == changed else base / name).read_bytes()
        if target.exists() and target.read_bytes() != content:
            raise RuntimeError("FROZEN_SOURCE_COLLISION:" + name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    label = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:6]
    run = root / "research_outputs/m1234_sim/forward" / ("main24h-" + label)
    launch = run / "launch"
    extension = launch / "extension"
    extension.mkdir(parents=True)
    for name in EXTENSIONS:
        shutil.copyfile(PROJECT / "ops" / name, extension / name)
    write(launch / "extension-manifest.json", {name: sha(extension / name) for name in EXTENSIONS})
    write(launch / "implementation.json", files)
    with tarfile.open(launch / "source.tar.gz", "w:gz") as archive:
        for name in (*files, "pyproject.toml"):
            archive.add(root / name, arcname=name)
    shutil.copyfile(source / "launch/strategy-contract.json", launch / "strategy-contract.json")
    config = (source / "launch/24h.toml").read_text()
    lines = []
    for line in config.splitlines():
        if line.startswith("# Measured"):
            line = "# Lossless compressed diagnostics; 8 GiB output cap plus 4 GiB free reserve."
        elif line.startswith("max_output_mib"):
            line = "max_output_mib = 8192"
        lines.append(line)
    (launch / "24h.toml").write_text("\n".join(lines) + "\n")
    command = [str(PROJECT / ".venv/bin/python"), "-u", str(extension / "m234_sim_main_forward.py"),
               "--source-root", str(root), "--config", str(launch / "24h.toml"),
               "--contract", str(launch / "strategy-contract.json"), "--output", str(run)]
    check = subprocess.run([*command, "--mode", "check"], capture_output=True, text=True, check=True)
    write(launch / "preflight.json", json.loads(check.stdout))
    record = {"status": "PREPARED", "directory": str(run), "source_root": str(root),
              "source_run": str(source), "replay": str(replay), "command": command,
              "unit": "polymaker-m1234-sim-main24h-" + label.lower() + ".service",
              "source_changes_from_baseline": [changed], "real_orders": 0}
    write(launch / "launch.json", record)
    return record


def launch_sidecar(record):
    run = Path(record["directory"])
    extension = run / "launch/extension"
    output = PROJECT / "research_outputs/m1234_sim/signal_baseline" / run.name
    output.mkdir(parents=True, exist_ok=True)
    unit = "polymaker-m234-instant-fill-main-" + hashlib.sha256(str(run).encode()).hexdigest()[:12] + ".service"
    command = [str(PROJECT / ".venv/bin/python"), "-u", str(extension / "m234_sim_main_instant_fill.py"),
               "--source-run", str(run), "--output", str(output), "--follow", "--interval", "15",
               "--source-unit", record["unit"], "--source-scenario", "main/trades-only"]
    subprocess.run(["systemd-run", "--user", "--unit=" + unit, "--service-type=exec",
                    "--working-directory=" + str(record["source_root"]),
                    "--setenv=PYTHONPATH=" + str(Path(record["source_root"]) / "src"),
                    "--property=Restart=on-failure", "--property=RestartSec=30",
                    "--property=StartLimitBurst=3", "--property=StartLimitIntervalSec=300",
                    "--property=Nice=15", "--property=CPUWeight=10", "--property=MemoryMax=128M",
                    "--property=RestrictAddressFamilies=AF_UNIX",
                    "--property=StandardOutput=append:" + str(output / "console.log"),
                    "--property=StandardError=inherit", *command], check=True)
    evidence = {"unit": unit, "source_unit": record["unit"], "source_run": str(run),
                "directory": str(output), "mode": "SIGNAL_VALIDATION", "evidence": "ASSUMED_FILL",
                "source_scenario": "main/trades-only", "command": command, "started_at": time.time()}
    write(output / "launch.json", evidence)
    return evidence


def start(run):
    launch = run / "launch"
    record = read(launch / "launch.json")
    check_replay(Path(record["replay"]))
    for name, expected in read(launch / "extension-manifest.json").items():
        if sha(launch / "extension" / name) != expected:
            raise RuntimeError("EXTENSION_HASH_MISMATCH:" + name)
    materialize(run, PROJECT / "research_outputs/m1234_sim/runtime")
    check = subprocess.run([*record["command"], "--mode", "check"], capture_output=True,
                           text=True, check=True)
    write(launch / "preflight-at-start.json", json.loads(check.stdout))
    with (MONITOR / "watch.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        control = read(MONITOR / "control.json")
        if control["enabled"]:
            raise RuntimeError("CHAT_MONITOR_EXPECTED_DISABLED")
        if control["run_directory"] != record["source_run"]:
            raise RuntimeError("CURRENT_SIMULATOR_CHANGED_RECHECK_BEFORE_REPLACEMENT")
        old_unit = control["unit"]
        if not old_unit.startswith("polymaker-m1234-sim-"):
            raise RuntimeError("NOT_A_SIMULATOR_UNIT")
        old = status(old_unit)
        if old["ActiveState"] == "active":
            words = (Path("/proc") / old["MainPID"] / "cmdline").read_bytes().split(b"\0")
            if (b"polymaker.m1234_sim" not in words or b"--output" not in words
                    or words[words.index(b"--output") + 1].decode() != record["source_run"]):
                raise RuntimeError("OLD_SIMULATOR_COMMAND_MISMATCH")
        write(launch / "previous-monitor-control.json", control)
        record.setdefault("replacement_reason", "USER_AUTHORIZED_MAIN_POLICY")
        record.update(status="REPLACING_PUBLIC_SIMULATOR", started_at=time.time(),
                      replaces_unit=old_unit)
        write(launch / "launch.json", record)
        write(Path(record["source_run"]) / "launch/main-policy-replacement.json", record)
        if old["ActiveState"] == "active":
            subprocess.run(["systemctl", "--user", "stop", old_unit], check=True, timeout=150)
        subprocess.run(["systemd-run", "--user", "--unit=" + record["unit"], "--service-type=exec",
                        "--description=M1234 public simulation with M234 main and retained baseline",
                        "--working-directory=" + record["source_root"],
                        "--setenv=PYTHONPATH=" + str(Path(record["source_root"]) / "src"),
                        "--property=Restart=no", "--property=KillSignal=SIGINT",
                        "--property=TimeoutStopSec=120",
                        "--property=StandardOutput=append:" + str(run / "console.log"),
                        "--property=StandardError=inherit", *record["command"]], check=True)
        record["status"] = "STARTED"
        write(launch / "launch.json", record)
        control.update(run_directory=str(run), unit=record["unit"], started_at=record["started_at"],
                       primary_execution_mode="main", baseline_execution_mode="retained",
                       metrics_script=str(launch / "extension/m234_sim_main_metrics.py"))
        control.pop("signal_validation", None)
        write(MONITOR / "control.json", control)
    deadline = time.monotonic() + 30
    while not (run / "plan.json").exists() and time.monotonic() < deadline:
        time.sleep(.2)
    if status(record["unit"])["ActiveState"] != "active":
        raise RuntimeError("MAIN_SIMULATOR_DID_NOT_STAY_ACTIVE")
    sidecar = launch_sidecar(record)
    with (MONITOR / "watch.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        control = read(MONITOR / "control.json")
        if control["run_directory"] == str(run):
            control["signal_validation"] = sidecar
            write(MONITOR / "control.json", control)
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--source-run", type=Path, required=True)
    prep.add_argument("--replay", type=Path, required=True)
    launch = sub.add_parser("start")
    launch.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.source_run.resolve(), args.replay.resolve()) if args.action == "prepare" else start(args.run.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
