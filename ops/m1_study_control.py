"""Extend a frozen public study between markets without changing model code."""

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from polymaker.rdp_sm_m1_observe import lower_priority, observer_lock
from polymaker.rdp_sm_m1_study import atomic_json, code_hashes, run_study, save
from polymaker.rdp_sm_m1_writer import M1ObserverWriter


class BufferedStudyWriter(M1ObserverWriter):
    """Keep strategy callbacks unchanged; absorb short disk/CPU scheduling bursts."""

    def __init__(self, path, *, capacity=2048, listener=None):
        super().__init__(path, capacity=max(8192, capacity), listener=listener)

    def close(self):
        self.stopping.set()
        self.thread.join(3)
        if self.thread.is_alive():
            self.failure = "M1_LOG_DRAIN_TIMEOUT"


def read(directory):
    return json.loads((directory/"study.json").read_text())


def matches(pid, token):
    try:
        return token.encode() in Path(f"/proc/{pid}/cmdline").read_bytes()
    except FileNotFoundError:
        return False


def change_target(state, target, budget, reserve, *, storage_only=False):
    previous = state["evaluation_target"]
    if state["phase"] != "EVALUATION" or (target != previous if storage_only else target <= previous):
        raise ValueError("Only extend the horizon of an already frozen evaluation")
    state.setdefault("target_history", []).append({
        "previous": state["evaluation_target"], "target": target,
        "changed_utc": datetime.now(UTC).isoformat(),
        "reason": "OBSERVED_LOG_QUEUE_OVERFLOW_BUFFER_FIX" if storage_only else "EXPLICIT_USER_REQUEST_MORE_DATA",
        "evaluation_markets_already_recorded": sum(r["phase"] == "EVALUATION" for r in state["sessions"])})
    state["evaluation_target"] = target
    state["run_controls"] = {"log_budget_mib": budget, "disk_reserve_gib_for_ca": reserve,
        "log_queue_capacity": 8192, "log_drain_timeout_sec": 3,
        "control_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "changes_strategy_or_fill_model": False}
    return state


async def guarded_run(directory, target, budget, reserve):
    from polymaker import rdp_sm_m1_observe as market_observer

    # Recorder-only substitution in this isolated process. Public input queue,
    # freshness/ordering guards, signal policy and fill model stay unchanged.
    market_observer.M1ObserverWriter = BufferedStudyWriter
    task = asyncio.create_task(run_study(directory/"config", directory, evaluation_sessions=target,
                                         output_budget_mib=budget, resume=True))
    reason = None

    async def guard():
        nonlocal reason
        while not task.done():
            free = shutil.disk_usage(directory).free
            state = read(directory)
            if free < reserve*1024**3 and (not state.get("active_market") or free < 2*1024**3):
                reason = f"M1_DISK_RESERVE_FOR_CA: free={free/1024**3:.3f}GiB reserve={reserve}GiB"
                task.cancel()
                return
            await asyncio.sleep(5)

    monitor = asyncio.create_task(guard())
    try:
        return await task
    except asyncio.CancelledError:
        if reason is None:
            raise
        state = read(directory)
        state.update(status="FAILED", error=reason)
        save(state, directory)
        print(reason, flush=True)
        return state
    finally:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)


def extend(directory, target, budget, reserve, *, storage_only=False):
    root = directory.parent
    runtime_path = root/(directory.name+".runtime.json")
    runtime = json.loads(runtime_path.read_text())
    state = read(directory)
    if state["source_sha256"] != code_hashes():
        raise RuntimeError("Frozen model source does not match; extension refused")
    frozen = hashlib.sha256((directory/"frozen-config.toml").read_bytes()).hexdigest()
    if frozen != state["frozen_config_sha256"]:
        raise RuntimeError("Frozen configuration does not match; extension refused")
    while True:
        state = read(directory)
        idle = not state.get("active_market")
        safe_boundary = 241 <= time.time()%300 <= 295
        if idle and (safe_boundary or state["status"] != "RUNNING"):
            break
        print(f"M1 EXTEND waiting for market close | target={target} active={(state.get('active_market') or {}).get('epoch')}", flush=True)
        time.sleep(5)
    pid = state["pid"]
    if matches(pid, "polymaker.rdp_sm_m1_study") or matches(pid, "ops/m1_study_control.py"):
        report_pid = runtime.get("report_pid")
        if report_pid and matches(report_pid, "ops/m1_study_report.py"):
            os.kill(report_pid, signal.SIGTERM)
        os.kill(pid, signal.SIGINT)
        deadline = time.monotonic()+25
        while time.monotonic() < deadline and (matches(pid, "polymaker.rdp_sm_m1_study") or matches(pid, "ops/m1_study_control.py")):
            time.sleep(.1)
        if matches(pid, "polymaker.rdp_sm_m1_study") or matches(pid, "ops/m1_study_control.py"):
            raise RuntimeError("Old observer did not close; no replacement launched")
    state = read(directory)
    if state.get("active_market"):
        raise RuntimeError("Unfinished market remains; no data was discarded")
    change_target(state, target, budget, reserve, storage_only=storage_only)
    archive = directory/"operator_sources"
    archive.mkdir(exist_ok=True)
    (archive/("control-"+state["run_controls"]["control_source_sha256"]+".py")).write_bytes(Path(__file__).read_bytes())
    save(state, directory)
    command = ["nice", "-n", "15", "ionice", "-c", "3", sys.executable,
               str(Path(__file__)), "resume-run", str(directory), "--target", str(target),
               "--log-budget-mib", str(budget), "--reserve-gib", str(reserve)]
    with Path(runtime["console"]).open("a") as output:
        output.write(f"\nUSER_EXTENDED_EVALUATION target={target} storage_mib={budget} reserve_gib={reserve}\n")
        output.flush()
        child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output,
                                 stderr=subprocess.STDOUT, start_new_session=True)
    runtime.setdefault("previous_pids", []).append(pid)
    runtime.update(pid=child.pid, command=command)
    atomic_json(runtime_path, runtime)
    deadline = time.monotonic()+15
    while time.monotonic() < deadline:
        state = read(directory)
        if state.get("pid") == child.pid and state["status"] == "RUNNING":
            break
        if child.poll() is not None:
            raise RuntimeError("Extended observer failed to start; inspect its console")
        time.sleep(.2)
    else:
        raise RuntimeError("Extended observer did not publish RUNNING")
    report_command = ["nice", "-n", "19", "ionice", "-c", "3", sys.executable,
                      "ops/m1_study_report.py", str(directory)]
    with Path(runtime["report_log"]).open("a") as output:
        reporter = subprocess.Popen(report_command, stdin=subprocess.DEVNULL, stdout=output,
                                    stderr=subprocess.STDOUT, start_new_session=True)
    runtime.update(report_pid=reporter.pid, report_command=report_command)
    atomic_json(runtime_path, runtime)
    print(json.dumps({"status": state["status"], "target": state["evaluation_target"],
        "preserved_evaluation_markets": sum(r["phase"] == "EVALUATION" for r in state["sessions"]),
        "frozen_config_sha256": state["frozen_config_sha256"], "pid": child.pid,
        "log_budget_mib": budget, "disk_reserve_gib": reserve}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("extend", "resume-run"))
    parser.add_argument("study_dir", type=Path)
    parser.add_argument("--target", type=int, required=True)
    parser.add_argument("--log-budget-mib", type=int, default=6144)
    parser.add_argument("--reserve-gib", type=int, default=8)
    parser.add_argument("--storage-only", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.target <= 1000 or args.log_budget_mib <= 0 or args.reserve_gib < 2:
        parser.error("Invalid sampling or storage limits")
    if args.action == "extend":
        extend(args.study_dir, args.target, args.log_budget_mib, args.reserve_gib, storage_only=args.storage_only)
        return 0
    lower_priority()
    try:
        with observer_lock():
            state = asyncio.run(guarded_run(args.study_dir, args.target, args.log_budget_mib, args.reserve_gib))
    except KeyboardInterrupt:
        return 130
    return 0 if state["status"].startswith("COMPLETE") else 1


if __name__ == "__main__":
    raise SystemExit(main())
