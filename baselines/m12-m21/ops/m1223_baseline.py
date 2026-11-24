"""Freeze, launch and supervise M23 then M12, one public-only 24h cohort each."""
import argparse
import asyncio
import fcntl
import hashlib
import json
import os
import signal
import subprocess
import time
from pathlib import Path

from m1223_baseline_contract import (
    ORDER,
    atomic_json,
    cohort,
    completed,
    parameter_document,
    validate_plan,
    verify_runtime,
)

UNIT = "polymaker-m1223-baseline.service"


def environment(runtime):
    return {**os.environ, "PYTHONPATH": str(runtime / "extension") + ":" + str(runtime / "src"),
            "PYTHONDONTWRITEBYTECODE": "1"}


def command(project, runtime, strategy):
    return [str(project / ".venv/bin/python"), "-u",
            str(runtime / "extension/m1223_baseline_forward.py"),
            "--runtime", str(runtime), "--strategy", strategy]


def freeze(project, root):
    from m1223_prepare import clean_base_source

    base, provenance = clean_base_source()
    entries = {str(p.relative_to(base)): p.read_bytes() for folder in ("src", "extension")
               for p in (base / folder).rglob("*.py")}
    for p in (project / "research/sop_signals").glob("*.py"):
        entries["extension/sop_signals/" + p.name] = p.read_bytes()
    # The shared SOP selector imports the preserved M7 type even when only
    # M23/M12 is selected; its frozen primitive package reads this data at import.
    for p in (project / "research/maker_signals").glob("*.json"):
        entries["extension/maker_signals/" + p.name] = p.read_bytes()
    for p in (project / "ops").glob("m1223_*.py"):
        entries["extension/" + p.name] = p.read_bytes()
    # Bind the canonical documents as well as the implementation. No workspace
    # trading client or current M4 code is copied into the operational base.
    for p in (project / "research/signals").glob("*.md"):
        entries["documents/research/signals/" + p.name] = p.read_bytes()
    for name in ("m1234-sim-contract.json", "m1234-sim-24h.toml"):
        entries["config/" + name] = (base / "config" / name).read_bytes()
    for name in ("m1223-24h.toml", "m1223-development.toml", "m1223-input.json",
                 "m1223-baseline-plan.json"):
        entries["config/" + name] = (project / "config" / name).read_bytes()
    execution = json.loads((project / "config/m1223-execution.json").read_text())
    execution.update(quantity_step="0.1", session_budget_usdc="10",
                     sizing_revision="baseline-floor-0.1-5usdc-v1")
    entries["config/m1223-execution.json"] = (json.dumps(execution, indent=2) + "\n").encode()
    for strategy in ORDER:
        entries[f"config/m1223-parameters-{strategy}.json"] = (
            json.dumps(parameter_document(strategy), ensure_ascii=False, indent=2) + "\n").encode()
    inventory = {n: hashlib.sha256(data).hexdigest() for n, data in sorted(entries.items())}
    fingerprint = hashlib.sha256(json.dumps(inventory, sort_keys=True).encode()).hexdigest()
    runtime = root / "runtime" / fingerprint[:16]
    if not runtime.exists():
        for name, content in entries.items():
            path = runtime / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        atomic_json(runtime / "frozen-manifest.json", inventory)
    if verify_runtime(runtime) != fingerprint:
        raise ValueError("BASELINE_RUNTIME_IDENTITY_CONFLICT")
    return {"source_root": str(runtime), "runtime_sha256": fingerprint,
            "base_provenance": provenance, "files": len(inventory)}


def checks(project, runtime):
    result = {}
    for strategy in ORDER:
        output = subprocess.run([*command(project, runtime, strategy), "--check"],
                                env=environment(runtime), cwd=runtime,
                                capture_output=True, text=True, timeout=60)
        if output.returncode:
            raise ValueError("BASELINE_PREFLIGHT_ERROR:" + output.stderr[-4000:])
        result[strategy] = json.loads(output.stdout)
        if result[strategy]["status"] != "PASS":
            raise ValueError("BASELINE_DISK_PREFLIGHT_FAILED:" + output.stdout)
    return result


def prepare(project, root):
    plan = validate_plan(json.loads((project / "config/m1223-baseline-plan.json").read_text()))
    if (root / "state.json").exists():
        raise ValueError("BASELINE_PLAN_ALREADY_EXISTS_PRESERVE_PREVIOUS_EXECUTION")
    frozen = freeze(project, root)
    preflight = checks(project, Path(frozen["source_root"]))
    atomic_json(root / "plan.json", plan)
    atomic_json(root / "preflight.json", preflight)
    state = {**frozen, "schema": plan["schema"], "stage": "PREPARED_AWAITING_OFFLINE_VALIDATION",
             "created_at": time.time(), "current_strategy": None,
             "strategies": {s: {"stage": "PENDING"} for s in ORDER}, "real_orders": 0}
    atomic_json(root / "state.json", state)
    atomic_json(root / "control.json", {"enabled": True, "scope": "M23_THEN_M12_PUBLIC_BASELINE_ONLY",
                                        "created_at": time.time()})
    return state


def start(project, root, validation):
    state = json.loads((root / "state.json").read_text())
    proof = json.loads(validation.read_text())
    if (state["stage"] != "PREPARED_AWAITING_OFFLINE_VALIDATION"
            or proof.get("status") != "PASS" or proof.get("runtime_sha256") != state["runtime_sha256"]
            or proof.get("network_disabled") is not True):
        raise ValueError("BASELINE_MATCHING_OFFLINE_VALIDATION_REQUIRED")
    runtime = Path(state["source_root"])
    if verify_runtime(runtime) != state["runtime_sha256"]:
        raise ValueError("BASELINE_RUNTIME_HASH_CHANGED")
    checks(project, runtime)
    active = subprocess.run(["systemctl", "--user", "is-active", UNIT], capture_output=True, text=True)
    if active.stdout.strip() in {"active", "activating", "reloading"}:
        raise ValueError("BASELINE_ALREADY_RUNNING")
    launch = [str(project / ".venv/bin/python"), "-u", str(runtime / "extension/m1223_baseline.py"),
              "--project", str(project), "--root", str(root), "run"]
    invocation = ["systemd-run", "--user", "--unit=" + UNIT, "--collect",
                  "--property=WorkingDirectory=" + str(runtime), "--property=Restart=no",
                  "--property=MemoryMax=768M", "--property=Nice=10", "--property=CPUWeight=10",
                  "--property=KillSignal=SIGINT", "--property=TimeoutStopSec=45",
                  "--setenv=PYTHONPATH=" + environment(runtime)["PYTHONPATH"],
                  "--setenv=PYTHONDONTWRITEBYTECODE=1", *launch]
    atomic_json(root / "launch.json", {"unit": UNIT, "command": invocation,
                                        "validation": str(validation), "at": time.time(), "real_orders": 0})
    subprocess.run(invocation, check=True, capture_output=True, text=True)
    return {"unit": UNIT, "source_root": str(runtime), "strategy_order": ORDER}


async def public_first(stop):
    from polymaker.m1_virtual_api import VirtualPublicAPI

    api = VirtualPublicAPI()
    try:
        while not stop():
            try:
                stamp = await api.clock()
                epoch = int(stamp[0] // 300) * 300
                # Leave at least20s, and avoid native current-market admission
                # in the first5s. A retry does not silently move a saved cohort.
                if 5 <= stamp[0] - epoch <= 275:
                    return epoch + 300, stamp
            except Exception as exc:
                print(json.dumps({"event": "BASELINE_PUBLIC_CLOCK_RETRY",
                                  "error_type": type(exc).__name__}), flush=True)
            await asyncio.sleep(2)
    finally:
        await api.close()
    return None


def execute(project, root):
    state_path = root / "state.json"
    with (root / "runner.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = json.loads(state_path.read_text())
        runtime = Path(state["source_root"])
        if verify_runtime(runtime) != state["runtime_sha256"]:
            raise ValueError("BASELINE_RUNTIME_HASH_CHANGED")
        plan = validate_plan(json.loads((root / "plan.json").read_text()))
        if plan != json.loads((runtime / "config/m1223-baseline-plan.json").read_text()):
            raise ValueError("BASELINE_SAVED_PLAN_CHANGED")
        stopping, child = False, None

        def stop(*_):
            nonlocal stopping
            stopping = True
            if child is not None and child.poll() is None:
                child.send_signal(signal.SIGINT)

        def inhibited():
            return stopping or not json.loads((root / "control.json").read_text()).get("enabled", False)

        def save():
            state["reviewed_at"] = time.time()
            atomic_json(state_path, state)
            atomic_json(root / "latest.json", state)

        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)
        try:
            for strategy in ORDER:
                row = state["strategies"][strategy]
                control = json.loads((root / "control.json").read_text())
                if strategy == "M12" and control.get("auto_start_next_strategy") is False:
                    row.update(stage="HELD_BY_USER", reason="USER_REQUEST_NO_AUTO_START")
                    state.update(stage="COMPLETED_M23_M12_HELD", current_strategy=None)
                    save()
                    return
                if row["stage"] == "COMPLETED":
                    evidence = json.loads((Path(row["output"]) / "completion.json").read_text())
                    if not completed(evidence, json.loads(Path(row["cohort"]).read_text())):
                        raise ValueError("BASELINE_COMPLETION_EVIDENCE_CHANGED")
                    continue
                # A process restart must not start the same cohort again.
                if row["stage"] != "PENDING":
                    row["stage"] = "INTERRUPTED_REVIEW_REQUIRED"
                    raise ValueError("BASELINE_PRIOR_START_NOT_REPLAYED")
                if inhibited():
                    state["stage"] = "STOPPED_BY_CONTROL"
                    save()
                    return
                preflight = checks(project, runtime)
                atomic_json(root / f"preflight-before-{strategy}.json", preflight)
                state.update(stage="WAITING_NEXT_FULL_MARKET", current_strategy=strategy)
                save()
                initial = asyncio.run(public_first(inhibited))
                if initial is None:
                    state["stage"] = "STOPPED_BY_CONTROL"
                    save()
                    return
                first, clock = initial
                selected = cohort(strategy, first, plan, state["runtime_sha256"])
                output = root / "runs" / f"{strategy.lower()}-{first}"
                cohort_path = root / "cohorts" / f"{strategy.lower()}-{first}.json"
                atomic_json(cohort_path, selected)
                row.update(stage="STARTING", output=str(output), cohort=str(cohort_path),
                           window_start_epoch=first, window_end_epoch=first + 86400,
                           public_clock=clock, started_at=time.time())
                state["stage"] = "STARTING"
                save()
                if inhibited():
                    state["stage"] = "STOPPED_BY_CONTROL"
                    row["stage"] = "INTERRUPTED_REVIEW_REQUIRED"
                    save()
                    return
                run_command = [*command(project, runtime, strategy), "--output", str(output),
                               "--cohort", str(cohort_path)]
                child = subprocess.Popen(run_command, cwd=runtime, env=environment(runtime))
                row.update(stage="RUNNING", pid=child.pid)
                state.update(stage="RUNNING", pid=os.getpid())
                save()
                while child.poll() is None:
                    if inhibited():
                        stop()
                    try:
                        child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        save()
                result_path = output / "completion.json"
                result = json.loads(result_path.read_text()) if result_path.exists() else {}
                if child.returncode != 0 or inhibited() or not completed(result, selected):
                    row.update(stage="INTERRUPTED_REVIEW_REQUIRED" if inhibited() else "FAILED",
                               exit_code=child.returncode, result=result)
                    state["stage"] = row["stage"]
                    save()
                    return
                row.update(stage="COMPLETED", finished_at=time.time(), result=result)
                save()
                child = None
            state.update(stage="COMPLETED", current_strategy=None, completed_at=time.time())
            save()
        except BaseException as exc:
            if child is not None and child.poll() is None:
                stop()
                child.wait(timeout=35)
            state.update(stage="FAILED", error_type=type(exc).__name__, error=str(exc))
            save()
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path("/home/ubuntu/polymaker"))
    parser.add_argument("--root", type=Path)
    parser.add_argument("action", choices=("prepare", "start", "run", "status"))
    parser.add_argument("--validation", type=Path)
    args = parser.parse_args()
    project = args.project.resolve()
    root = args.root.resolve() if args.root else project / "research_outputs/m1223_baseline"
    if root != project / "research_outputs/m1223_baseline" or root.is_symlink():
        raise ValueError("BASELINE_REQUIRES_INDEPENDENT_OUTPUT_ROOT")
    if args.action == "prepare":
        value = prepare(project, root)
    elif args.action == "start":
        if args.validation is None:
            raise ValueError("BASELINE_VALIDATION_REQUIRED")
        value = start(project, root, args.validation.resolve())
    elif args.action == "run":
        execute(project, root)
        value = json.loads((root / "state.json").read_text())
    else:
        value = json.loads((root / "state.json").read_text())
    print(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
