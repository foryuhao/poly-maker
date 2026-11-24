"""Launch one frozen M12 development cohort after tests and disk preflight."""

import argparse
import hashlib
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from m1223_gates import frequency_reporting_contract
from m1223_prepare import write

PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT / "research_outputs/m1223_sim"


def phase_configuration(phase_kind, batches_used, *, has_selection, has_independent_report,
                        has_user_direct_admission=False):
    if phase_kind == "USER_DIRECT_24H_TEST":
        if not has_user_direct_admission or has_selection or has_independent_report:
            raise ValueError("EXPLICIT_USER_DIRECT_ADMISSION_REQUIRED")
        return {"label": "user24h", "folder": "24h", "markets": 288,
                "config": "m1223-24h.toml", "batch_number": None}
    if has_user_direct_admission:
        raise ValueError("USER_DIRECT_AUTHORIZATION_ONLY_APPLIES_TO_ITS_EXPLICIT_PHASE")
    if phase_kind == "DEVELOPMENT_CAPTURE":
        if has_selection or has_independent_report:
            raise ValueError("DEVELOPMENT_HAS_NO_INDEPENDENT_ADMISSION")
        return {
            "label": "development24",
            "folder": "development",
            "markets": 24,
            "config": "m1223-development.toml",
            "batch_number": None,
        }
    if not has_selection:
        raise ValueError("FROZEN_FREQUENCY_SELECTION_REQUIRED")
    if phase_kind == "INDEPENDENT_VALIDATION":
        if type(batches_used) is not int or not 0 <= batches_used < 2:
            raise ValueError("INDEPENDENT_VALIDATION_BATCH_LIMIT")
        return {
            "label": "independent24",
            "folder": "independent",
            "markets": 24,
            "config": "m1223-development.toml",
            "batch_number": batches_used + 1,
        }
    if phase_kind == "CONTINUOUS_24H_TEST" and has_independent_report:
        return {
            "label": "full24h",
            "folder": "24h",
            "markets": 288,
            "config": "m1223-24h.toml",
            "batch_number": None,
        }
    raise ValueError("INDEPENDENT_REPORT_REQUIRED_FOR_24H")


def start(validation, *, phase_kind="DEVELOPMENT_CAPTURE", selection=None, independent_report=None,
          user_direct_document=None, state_file=None):
    state_file = Path(state_file or ROOT / "state.json").resolve()
    if not state_file.is_relative_to(ROOT.resolve()):
        raise ValueError("STATE_MUST_BELONG_TO_THIS_SIMULATION_TASK")
    state = json.loads(state_file.read_text())
    if (not state.get("enabled") or state["strategy"] not in {"M12", "M13", "M14", "M15", "M16", "M17", "M18", "M19", "M21"}
            or state.get("active_run") or state.get("active_job")):
        raise ValueError("SOP_STATE_NOT_IDLE_IMPLEMENTED_STRATEGY")
    strategy = state["strategy"]
    phase_spec = phase_configuration(
        phase_kind,
        state["independent_validation_batches_used"],
        has_selection=selection is not None,
        has_independent_report=independent_report is not None,
        has_user_direct_admission=user_direct_document is not None,
    )
    active = subprocess.run(
        [
            "systemctl",
            "--user",
            "list-units",
            "--type=service",
            "--state=active,activating",
            "--no-pager",
            "--no-legend",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    if "polymaker-m1223-" in active:
        raise ValueError("EXISTING_OWN_SIMULATION_MUST_BE_RECONCILED")
    runtime = Path(state["source_root"])
    manifest = json.loads((runtime / "frozen-manifest.json").read_text())
    for rel, expected in manifest.items():
        if hashlib.sha256((runtime / rel).read_bytes()).hexdigest() != expected:
            raise ValueError("FROZEN_SOURCE_CHANGED:" + rel)
    verification = json.loads(validation.read_text())
    if (
        verification["status"] != "PASS"
        or verification["runtime_sha256"] != state["runtime_sha256"]
    ):
        raise ValueError("MATCHING_FROZEN_DIRECTED_VALIDATION_REQUIRED")
    env = {**os.environ, "PYTHONPATH": str(runtime / "extension") + ":" + str(runtime / "src")}
    script = """import asyncio,json
from polymaker.m1_virtual_api import VirtualPublicAPI
async def main():
    api=VirtualPublicAPI()
    try: print(json.dumps(await api.clock()))
    finally: await api.close()
asyncio.run(main())
"""
    clock_read = subprocess.run(
        [str(PROJECT / ".venv/bin/python"), "-c", script],
        cwd=runtime,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    clock = json.loads(clock_read.stdout)
    server = clock[0]
    first = int(server // 300) * 300
    if server - first >= 5:
        first += 300
    if first - server < 20:
        raise ValueError("WAIT_NEXT_BOUNDARY_FOR_20S_STARTUP_BUDGET")
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    label = (
        strategy.lower() + "-" + phase_spec["label"] + "-" + now.strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:6]
    )
    market_count = phase_spec["markets"]
    phase = ROOT / strategy / label
    cohort_path = phase / "cohort.json"
    cohort = {
        "schema": "sop-predeclared-cohort-v1",
        "strategy": strategy,
        "stage": phase_kind,
        "selected_before_collection_beijing": now.isoformat(),
        "selection": f"NEXT_{market_count}_CONSECUTIVE_5MIN_MARKETS_NOT_SELECTED_BY_SIGNALS_OR_PNL",
        "development_only_not_independent_validation": phase_kind == "DEVELOPMENT_CAPTURE",
        "market_epochs": list(range(first, first + market_count * 300, 300)),
        "markets": [f"btc-updown-5m-{e}" for e in range(first, first + market_count * 300, 300)],
        "window_start_epoch": first,
        "window_end_epoch": first + market_count * 300,
        "denominator": market_count,
        "batch_number": phase_spec["batch_number"],
        "frequency_gate_distinct_primary_markets": 10,
        "frequency_gate_denominator": 24,
        "frequency_gate_scope": "USER_DIRECT_WITHOUT_INDEPENDENT_VALIDATION"
        if phase_kind == "USER_DIRECT_24H_TEST"
        else "PRECEDING_INDEPENDENT_VALIDATION"
        if phase_kind == "CONTINUOUS_24H_TEST"
        else "THIS_FIXED_24_MARKET_COHORT",
        "frequency_reporting_contract": frequency_reporting_contract(phase_kind, market_count),
        "runtime_sha256": state["runtime_sha256"],
        "predeclared_public_clock": clock,
    }
    write(cohort_path, cohort)
    output = runtime / "research_outputs/m1223_sim" / phase_spec["folder"] / label
    command = [
        str(PROJECT / ".venv/bin/python"),
        "-u",
        str(runtime / "extension/m1223_sim_forward.py"),
        "--source-root",
        str(runtime),
        "--config",
        str(runtime / "config" / phase_spec["config"]),
        "--contract",
        str(runtime / "config/m1234-sim-contract.json"),
        "--output",
        str(output),
        "--cohort",
        str(cohort_path),
    ]
    for flag, path in (("--selection", selection), ("--independent-report", independent_report),
                       ("--user-direct-admission", user_direct_document)):
        if path is not None:
            command.extend([flag, str(path)])
    check = subprocess.run(
        [*command, "--mode", "check"],
        cwd=runtime,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    preflight = json.loads(check.stdout)
    write(phase / "preflight.json", preflight)
    if preflight["status"] != "PASS":
        raise ValueError("SOP_DISK_PREFLIGHT_FAILED")
    unit = "polymaker-m1223-" + label.lower() + ".service"
    command += ["--mode", "run"]
    launch = {
        "command": command,
        "unit": unit,
        "source_root": str(runtime),
        "output": str(output),
        "cohort": str(cohort_path),
        "runtime_sha256": state["runtime_sha256"],
        "started_beijing": now.isoformat(),
        "validation": str(validation),
        "resource_limits": {"MemoryMax": "512M", "Nice": 10, "CPUWeight": 10, "Restart": "no"},
        "real_orders": 0,
        "phase_kind": phase_kind,
        "selection": str(selection) if selection else None,
        "independent_report": str(independent_report) if independent_report else None,
        "user_direct_admission": str(user_direct_document) if user_direct_document else None,
        "independent_realtime_validation_completed": phase_kind == "CONTINUOUS_24H_TEST",
        "state_file": str(state_file),
    }
    write(phase / "launch.json", launch)
    state.update(
        stage={
            "DEVELOPMENT_CAPTURE": "DEVELOPMENT_STARTING",
            "INDEPENDENT_VALIDATION": "INDEPENDENT_VALIDATION_STARTING",
            "CONTINUOUS_24H_TEST": "CONTINUOUS_24H_STARTING",
            "USER_DIRECT_24H_TEST": "USER_DIRECT_24H_STARTING",
        }[phase_kind],
        active_run=launch,
        market_list=str(cohort_path),
        updated_beijing=now.isoformat(),
        service=None,
        current_counts=None,
        current_scoped_counts=None,
        current_counts_scope="NEW_RUN_AWAITING_FIRST_COMMITTED_OBSERVATION",
        latest_report=None,
        last_observation=None,
        reporting={"status": "FIXED_COMPATIBILITY_IN_NEW_FROZEN_RUN"},
    )
    write(state_file, state)
    invocation = [
        "systemd-run",
        "--user",
        "--unit=" + unit,
        "--collect",
        "--property=WorkingDirectory=" + str(runtime),
        "--property=MemoryMax=512M",
        "--property=Nice=10",
        "--property=CPUWeight=10",
        "--property=Restart=no",
        "--property=KillSignal=SIGINT",
        "--property=TimeoutStopSec=90",
        "--property=NoNewPrivileges=yes",
        "--property=RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
        "--setenv=PYTHONPATH=" + env["PYTHONPATH"],
        *command,
    ]
    result = subprocess.run(invocation, capture_output=True, text=True)
    write(
        phase / "service-start-result.json",
        {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr},
    )
    if result.returncode:
        state.update(stage="START_FAILED", start_error=result.stderr)
        write(state_file, state)
        raise ValueError("SOP_SERVICE_START_FAILED:" + result.stderr)
    if phase_kind == "INDEPENDENT_VALIDATION":
        state["independent_validation_batches_used"] = phase_spec["batch_number"]
        write(state_file, state)
    current = {
        "strategy": strategy,
        "stage": state["stage"],
        "state": str(state_file),
        "source_root": str(runtime),
        "run": str(output),
        "unit": unit,
        "cohort": str(cohort_path),
        "updated_beijing": now.isoformat(),
    }
    write(ROOT / "current.json", current)
    print(
        json.dumps(
            {
                **current,
                "window_start_beijing": datetime.fromtimestamp(
                    first, ZoneInfo("Asia/Shanghai")
                ).isoformat(),
                "window_end_beijing": datetime.fromtimestamp(
                    first + market_count * 300, ZoneInfo("Asia/Shanghai")
                ).isoformat(),
            },
            ensure_ascii=False,
        )
    )
    return launch


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validation", required=True, type=Path)
    parser.add_argument(
        "--phase",
        choices=("DEVELOPMENT_CAPTURE", "INDEPENDENT_VALIDATION", "CONTINUOUS_24H_TEST", "USER_DIRECT_24H_TEST"),
        default="DEVELOPMENT_CAPTURE",
    )
    parser.add_argument("--selection", type=Path)
    parser.add_argument("--independent-report", type=Path)
    parser.add_argument("--user-direct-admission", type=Path)
    parser.add_argument("--state", type=Path)
    args = parser.parse_args()
    start(
        args.validation.resolve(),
        phase_kind=args.phase,
        selection=args.selection.resolve() if args.selection else None,
        independent_report=args.independent_report.resolve() if args.independent_report else None,
        user_direct_document=args.user_direct_admission.resolve() if args.user_direct_admission else None,
        state_file=args.state,
    )
