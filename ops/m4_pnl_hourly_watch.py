"""Generate the authorized M4 baseline report and queue it to the existing chat."""

from __future__ import annotations

import argparse
import fcntl
import json
import subprocess
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from m4_log_maintenance import maintain

from polymaker.m4_control import atomic_json

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_STATE = PROJECT / "research_outputs/m4_pnl_hourly_watch"
OFFSET_SECONDS = 20
SERVICES = (
    "polymaker-m4-baseline.service",
    "polymaker-m4-live.service",
    "polymaker-m4-watch.timer",
)


def read_json(path):
    return json.loads(Path(path).read_text())


def service_status(unit):
    try:
        reply = subprocess.run(
            [
                "systemctl",
                "--user",
                "show",
                unit,
                "-p",
                "ActiveState",
                "-p",
                "SubState",
                "-p",
                "MainPID",
                "-p",
                "NRestarts",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return {"ActiveState": "unavailable", "error_type": type(error).__name__}
    return dict(line.split("=", 1) for line in reply.stdout.splitlines() if "=" in line)


def generate_report(state_dir, control):
    now = time.time()
    stamp = datetime.fromtimestamp(now, ZoneInfo("Asia/Shanghai")).strftime("%Y%m%dT%H%M%S")
    output = state_dir / "reports" / stamp
    output.mkdir(parents=True)
    report = {
        "observed_at": now,
        "bucket": int((now - OFFSET_SECONDS) // 3600),
        "directory": str(output),
        "status": "OK",
        "problems": [],
        "services": {unit: service_status(unit) for unit in SERVICES},
        "stop_markers": sorted(
            p.name for p in (PROJECT / "runtime/trading-control").glob("*stop*.json")
        ),
    }
    try:
        commands = [
            (
                "snapshot",
                [
                    str(PROJECT / ".venv/bin/python"),
                    "-B",
                    str(PROJECT / "ops/m4_pnl_snapshot.py"),
                    "--project-dir",
                    str(PROJECT),
                    "--output",
                    str(output),
                ],
            ),
            (
                "render",
                [
                    control["plot_python"],
                    "-B",
                    str(PROJECT / "ops/m4_pnl_render.py"),
                    "--project-dir",
                    str(PROJECT),
                    "--directory",
                    str(output),
                ],
            ),
        ]
        for name, command in commands:
            reply = subprocess.run(
                command, cwd=PROJECT, capture_output=True, text=True, timeout=180
            )
            (output / f"{name}.log").write_text(reply.stdout + reply.stderr)
            reply.check_returncode()
        report["summary"] = read_json(output / "summary.json")
        report["png"] = str(output / "m4-pnl-waterfall-dual-cumulative-drawdown.png")
        if not Path(report["png"]).is_file():
            raise ValueError("M4_PNL_PNG_MISSING")
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        report["problems"].append("PNL_REPORT_GENERATION_FAILED")
        report["error_type"] = type(error).__name__
    runtime = PROJECT / "research_outputs/m4_virtual/latest.json"
    try:
        report["baseline_latest"] = read_json(runtime)
        report["baseline_heartbeat_age_sec"] = max(0, time.time() - runtime.stat().st_mtime)
    except (OSError, ValueError) as error:
        report["baseline_latest"] = {"error_type": type(error).__name__}
        report["baseline_heartbeat_age_sec"] = None
        report["problems"].append("BASELINE_HEARTBEAT_UNAVAILABLE")
    if report["services"][SERVICES[0]]["ActiveState"] != "active":
        report["problems"].append("BASELINE_INACTIVE")
    if (
        report["baseline_heartbeat_age_sec"] is not None
        and report["baseline_heartbeat_age_sec"] > 90
    ):
        report["problems"].append("BASELINE_HEARTBEAT_STALE")
    if report["baseline_latest"].get("signal_failure"):
        report["problems"].append("BASELINE_SIGNAL_FAILURE")
    if report["services"][SERVICES[1]]["ActiveState"] != "inactive":
        report["problems"].append("LIVE_UNEXPECTEDLY_ACTIVE")
    try:
        report["log_maintenance"] = maintain(
            PROJECT,
            state_dir,
            control.get("log_maintenance", {}),
            report["services"][SERVICES[0]].get("MainPID"),
        )
        if report["log_maintenance"]["warnings"]:
            report["problems"].append("M4_LOG_MAINTENANCE_WARNING")
    except (OSError, ValueError) as error:
        report["log_maintenance"] = {"error_type": type(error).__name__}
        report["problems"].append("M4_LOG_MAINTENANCE_FAILED")
    if report["problems"]:
        report["status"] = "ATTENTION"
    return report


def queue_report(control, report, state_dir, runner):
    path = state_dir / "agent-state.json"
    state = read_json(path) if path.exists() else {}
    token = f"{control['thread']}:{report['bucket']}"
    if token in state.get("notifications", []):
        return {"status": "ALREADY_QUEUED"}
    prompt = (PROJECT / "ops/m4_pnl_hourly_prompt.txt").read_text()
    prompt += f"\n本轮巡检记录文件 {report['directory']}/report.json\n"
    prompt += f"最新巡检索引文件 {state_dir}/latest.json\n"
    reply = runner(
        [
            str(Path.home() / ".local/bin/codex"),
            "queue",
            "--thread",
            control["thread"],
            "--message",
            prompt,
            "--resume",
        ],
        cwd=PROJECT,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    if reply.returncode:
        return {"status": "WAKE_FAILED", "returncode": reply.returncode}
    warning = "warning" in (reply.stdout + reply.stderr).lower()
    state.update(thread=control["thread"], last_queued_at=time.time(), resume_unconfirmed=warning)
    state["notifications"] = (state.get("notifications", []) + [token])[-48:]
    atomic_json(path, state)
    return {
        "status": "QUEUED_RESUME_UNCONFIRMED" if warning else "QUEUED_AND_RESUME_REQUESTED",
        "thread": control["thread"],
    }


def wake(control, report, state_dir, runner=subprocess.run):
    with (state_dir / "agent-state.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return queue_report(control, report, state_dir, runner)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--no-wake", action="store_true")
    args = parser.parse_args()
    args.state_dir.mkdir(parents=True, exist_ok=True)
    with (args.state_dir / "watch.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('{"status":"ALREADY_RUNNING"}')
            return
        control = read_json(args.state_dir / "control.json")
        if not control.get("enabled"):
            print('{"status":"DISABLED"}')
            return
        report = generate_report(args.state_dir, control)
        atomic_json(Path(report["directory"]) / "report.json", report)
        atomic_json(args.state_dir / "latest.json", report)
        control = read_json(args.state_dir / "control.json")
        if not args.no_wake and control.get("enabled"):
            try:
                report["wakeup"] = wake(control, report, args.state_dir)
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                report["wakeup"] = {"status": "WAKE_FAILED", "error_type": type(error).__name__}
            atomic_json(Path(report["directory"]) / "report.json", report)
            atomic_json(args.state_dir / "latest.json", report)
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "problems": report["problems"],
                    "directory": report["directory"],
                    "wakeup": report.get("wakeup"),
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
