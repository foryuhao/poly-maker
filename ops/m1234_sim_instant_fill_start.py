"""Attach the M234 signal-validation mode to a running simulator without restarting it."""

import argparse
import fcntl
import hashlib
import json
import subprocess
import time
from pathlib import Path

from m1234_sim_instant_fill import InstantFill, atomic_json

PROJECT = Path(__file__).resolve().parents[1]
MONITOR = PROJECT / "research_outputs/m1234_sim/monitor"


def service(unit):
    reply = subprocess.run(
        ["systemctl", "--user", "show", unit, "-p", "ActiveState", "-p", "MainPID"],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    return dict(line.split("=", 1) for line in reply.stdout.splitlines() if "=" in line)


def launch(source, source_unit, output):
    source, output = source.resolve(), output.resolve()
    source_state = service(source_unit)
    if source_state.get("ActiveState") != "active":
        raise RuntimeError("SOURCE_SIMULATOR_NOT_RUNNING")
    words = (Path("/proc") / source_state["MainPID"] / "cmdline").read_bytes().split(b"\0")
    if (
        b"polymaker.m1234_sim" not in words
        or b"--output" not in words
        or words[words.index(b"--output") + 1].decode() != str(source)
    ):
        raise RuntimeError("SOURCE_UNIT_OUTPUT_MISMATCH")
    label = hashlib.sha256(str(output).encode()).hexdigest()[:12]
    unit = f"polymaker-m234-instant-fill-{label}.service"
    archive = output / "launch"
    archive.mkdir(parents=True, exist_ok=True)
    with (archive / "start.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        record_path = archive / "launch.json"
        if service(unit).get("ActiveState") == "active":
            record = json.loads(record_path.read_text())
            if record["source_run"] != str(source) or record["source_unit"] != source_unit:
                raise RuntimeError("RUNNING_MODE_SOURCE_MISMATCH")
            return record
        script = Path(__file__).with_name("m1234_sim_instant_fill.py").read_bytes()
        archived = archive / "m1234_sim_instant_fill.py"
        if archived.exists() and archived.read_bytes() != script:
            raise RuntimeError("MODE_IMPLEMENTATION_CHANGED_USE_NEW_OUTPUT")
        archived.write_bytes(script)
        ledger = InstantFill(source, output)
        try:
            report = ledger.refresh()
            if report["status"] != "OK":
                raise RuntimeError("BASELINE_PREFLIGHT_FAILED:" + json.dumps(report["errors"]))
        finally:
            ledger.close()
        command = [
            str(PROJECT / ".venv/bin/python"),
            "-u",
            str(archived),
            "--source-run",
            str(source),
            "--output",
            str(output),
            "--follow",
            "--interval",
            "15",
            "--source-unit",
            source_unit,
        ]
        record = {
            "unit": unit,
            "source_unit": source_unit,
            "source_run": str(source),
            "directory": str(output),
            "command": command,
            "started_at": time.time(),
            "status": "START_REQUESTED",
            "mode": "SIGNAL_VALIDATION",
            "evidence": "ASSUMED_FILL",
            "real_orders": 0,
        }
        atomic_json(record_path, record)
        reply = subprocess.run(
            [
                "systemd-run",
                "--user",
                "--unit=" + unit,
                "--description=M234 instant-fill signal validation",
                "--service-type=exec",
                "--working-directory=" + str(PROJECT),
                "--setenv=PYTHONPATH=" + str(PROJECT / "src"),
                "--property=Restart=on-failure",
                "--property=RestartSec=30",
                "--property=StartLimitBurst=3",
                "--property=StartLimitIntervalSec=300",
                "--property=Nice=15",
                "--property=CPUWeight=10",
                "--property=MemoryMax=128M",
                "--property=RestrictAddressFamilies=AF_UNIX",
                "--property=TimeoutStopSec=30",
                "--property=StandardOutput=append:" + str(output / "console.log"),
                "--property=StandardError=inherit",
                *command,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        record.update(
            status="STARTED" if reply.returncode == 0 else "START_FAILED",
            systemd_exit_code=reply.returncode,
            systemd_output=reply.stdout + reply.stderr,
        )
        atomic_json(record_path, record)
        if reply.returncode:
            raise RuntimeError("MODE_START_FAILED:" + reply.stderr)
        # Discovery pointer only: preserve the user's disabled chat monitoring settings.
        with (MONITOR / "watch.lock").open("a+") as monitor_lock:
            fcntl.flock(monitor_lock, fcntl.LOCK_EX)
            control_path = MONITOR / "control.json"
            control = json.loads(control_path.read_text())
            if control.get("run_directory") == str(source):
                control["signal_validation"] = {
                    k: record[k] for k in ("unit", "source_run", "directory", "mode", "evidence")
                }
                atomic_json(control_path, control)
        return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", type=Path)
    parser.add_argument("--source-unit")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    control = json.loads((MONITOR / "control.json").read_text())
    source = (args.source_run or Path(control["run_directory"])).resolve()
    source_unit = args.source_unit or control["unit"]
    output = args.output or PROJECT / "research_outputs/m1234_sim/signal_baseline" / source.name
    print(json.dumps(launch(source, source_unit, output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
