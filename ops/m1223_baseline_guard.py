"""Guard an already running public M23 worker; never start a successor."""
import argparse
import fcntl
import json
import os
import shutil
import signal
import time
from contextlib import suppress
from pathlib import Path

from m1223_baseline_contract import atomic_json, completed

MINIMUM_FREE_BYTES = 10 * 1024**3


def process_identity(pid):
    try:
        stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        if stat[0] == "Z":
            return None
        return int(stat[19])
    except FileNotFoundError:
        return None


def disk_inhibited(free_bytes, control):
    floor = max(MINIMUM_FREE_BYTES, int(control.get("minimum_free_bytes", MINIMUM_FREE_BYTES)))
    return free_bytes <= floor, floor


def record_state(root, *, free_bytes, floor, reason=None, terminal=None):
    with (root / "agent-state.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.loads((root / "state.json").read_text())
        state["auto_start_next_strategy"] = False
        state["stop_after_strategy"] = "M23"
        state["strategies"]["M12"].update(stage="HELD_BY_USER", reason="USER_REQUEST_NO_AUTO_START")
        state["disk_guard"] = {"observed_at": time.time(), "free_bytes": free_bytes,
                               "minimum_free_bytes": floor, "stop_reason": reason}
        state["reviewed_at"] = time.time()
        state["guard_pid"] = os.getpid()
        state["pid"] = os.getpid()
        state["supervisor_unit"] = "polymaker-m23-baseline-guard.service"
        state["worker_scope"] = "polymaker-m23-baseline-active.scope"
        if reason:
            state["stage"] = "STOPPING_BASELINE_DISK_GUARD" if reason == "DISK_RESERVE_REACHED" else "STOPPING_BASELINE_BY_CONTROL"
        if terminal is not None:
            stage = ("COMPLETED" if terminal["complete"] else
                     "STOPPED_DISK_PROTECTION" if reason == "DISK_RESERVE_REACHED" else
                     "STOPPED_BY_CONTROL" if reason else "FAILED_REVIEW_REQUIRED")
            state["strategies"]["M23"].update(stage=stage, result=terminal["result"],
                                               finished_at=time.time())
            state.update(stage="COMPLETED_M23_M12_HELD" if stage == "COMPLETED" else stage,
                         current_strategy=None)
        atomic_json(root / "state.json", state)
        atomic_json(root / "latest.json", state)


def run(root, pid, start_time):
    root = root.resolve()
    state = json.loads((root / "state.json").read_text())
    row = state["strategies"]["M23"]
    output = Path(row["output"])
    if row["pid"] != pid or not output.resolve().is_relative_to(root / "runs"):
        raise ValueError("GUARD_WORKER_OWNERSHIP_MISMATCH")
    if process_identity(pid) != start_time:
        raise ValueError("GUARD_WORKER_START_TIME_MISMATCH")
    args = Path(f"/proc/{pid}/cmdline").read_bytes().decode().split("\0")
    if ("--strategy" not in args or args[args.index("--strategy") + 1] != "M23"
            or "--output" not in args or Path(args[args.index("--output") + 1]).resolve() != output.resolve()
            or not any(p.endswith("/extension/m1223_baseline_forward.py") for p in args)):
        raise ValueError("GUARD_NOT_OWN_M23_PUBLIC_WORKER")
    pidfd = os.pidfd_open(pid)
    if process_identity(pid) != start_time:
        os.close(pidfd)
        raise ValueError("GUARD_WORKER_CHANGED_DURING_ATTACH")
    stopping = None

    def stop_worker(reason):
        nonlocal stopping
        if stopping is None:
            stopping = reason
            with suppress(ProcessLookupError):
                signal.pidfd_send_signal(pidfd, signal.SIGINT)

    def interrupted(*_):
        stop_worker("GUARD_SERVICE_STOPPED")

    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    try:
        with (root / "runner.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            while True:
                control = json.loads((root / "control.json").read_text())
                free = shutil.disk_usage(root).free
                low, floor = disk_inhibited(free, control)
                if low:
                    stop_worker("DISK_RESERVE_REACHED")
                elif not control.get("enabled", False):
                    stop_worker("USER_STOPPED_BASELINE")
                if process_identity(pid) != start_time:
                    path = output / "completion.json"
                    result = json.loads(path.read_text()) if path.exists() else {}
                    member = json.loads(Path(row["cohort"]).read_text())
                    terminal = {"complete": stopping is None and completed(result, member), "result": result}
                    record_state(root, free_bytes=free, floor=floor, reason=stopping, terminal=terminal)
                    print(json.dumps({"event": "M23_GUARD_FINISHED", "complete": terminal["complete"],
                                      "stop_reason": stopping, "m12": "HELD_BY_USER"}), flush=True)
                    return
                record_state(root, free_bytes=free, floor=floor, reason=stopping)
                time.sleep(5)
    except BaseException:
        # A broken protector must not leave the public capture unsupervised.
        stop_worker("GUARD_FAILED")
        raise
    finally:
        os.close(pidfd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--start-time", type=int, required=True)
    args = parser.parse_args()
    run(args.root, args.pid, args.start_time)


if __name__ == "__main__":
    main()
