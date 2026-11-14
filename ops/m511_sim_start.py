"""Start a validated frozen shadow follower. Never stops a legacy service."""

import argparse
import hashlib
import json
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

PROJECT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)


def verify(bundle):
    for name, expected in read(bundle / "extension-manifest.json").items():
        if hashlib.sha256((bundle / "extension" / name).read_bytes()).hexdigest() != expected:
            raise ValueError("EXTENSION_IDENTITY_CHANGED:" + name)


def start(validation, *, min_epoch=None):
    next_policy = PROJECT / 'config/m5-m6-m9-m10-next-test.json'
    if next_policy.exists():
        policy = read(next_policy)
        if {'M7', 'M8'} <= set(policy.get('retired_strategies', ())):
            raise ValueError('LEGACY_ADDITIVE_FOLLOWER_RETIRED_USE_FOUR_STRATEGY_DIRECT_ENTRY')
    complete = read(validation / "completion.json")
    verified = list(validation.glob("btc-*/replay-validation.json"))
    if complete["status"] != "CAUGHT_UP" or complete["unfinished_markets"] or not verified:
        raise ValueError("COMPLETE_FROZEN_REPLAY_REQUIRED")
    if any(
        read(p)["status"] != "VALID" or not read(p)["legacy_brokers_identical"] for p in verified
    ):
        raise ValueError("LEGACY_EQUIVALENCE_REQUIRED")
    tests = read(validation / "launch/directed-validation.json")
    if tests["status"] != "PASS":
        raise ValueError("FROZEN_DIRECTED_TESTS_REQUIRED")
    if read(validation / "launch/economic-validation.json")["status"] != "PASS":
        raise ValueError("CANDIDATE_AND_ECONOMIC_REVIEW_REQUIRED")
    verify(validation / "launch")
    previous = read(validation / "launch/launch.json")
    variant_settings = read(validation / "plan.json").get("m5_m11", {})
    signal_variant = variant_settings.get("signal_variant")
    source = Path(previous["source_run"])
    control_path = PROJECT / "research_outputs/m1234_sim/monitor/control.json"
    control = read(control_path)
    if control["run_directory"] != str(source):
        raise ValueError("CURRENT_SOURCE_CHANGED")
    status = subprocess.run(
        ["systemctl", "--user", "show", control["unit"], "-p", "ActiveState", "-p", "MainPID"],
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    if "ActiveState=active" not in status:
        raise ValueError("SOURCE_MUST_STILL_BE_RUNNING")
    minimum_epoch = (int(time.time()) // 300 + 1) * 300
    if min_epoch is not None:
        if min_epoch < 0 or min_epoch % 300 or min_epoch > minimum_epoch:
            raise ValueError("INVALID_MINIMUM_MARKET_EPOCH")
        if not (source / f"btc-updown-5m-{min_epoch}" / "input.jsonl.gz").is_file():
            raise ValueError("RECOVERY_MARKET_RECORDING_MISSING")
        minimum_epoch = min_epoch
    label = (
        datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:6]
    )
    out = PROJECT / "research_outputs/m511_sim/follow" / label
    launch = out / "launch"
    shutil.copytree(validation / "launch", launch)
    command = [
        str(PROJECT / ".venv/bin/python"),
        "-u",
        str(launch / "extension/m511_sim_follow.py"),
        "--source-run",
        str(source),
        "--source-root",
        previous["source_root"],
        "--output",
        str(out),
        "--min-epoch",
        str(minimum_epoch),
    ]
    if signal_variant is not None:
        command.extend(("--signal-variant", signal_variant))
    unit = "polymaker-m511-shadow-" + label.lower() + ".service"
    record = {
        **previous,
        "status": "STARTING",
        "directory": str(out),
        "unit": unit,
        "command": command,
        "minimum_market_epoch": minimum_epoch,
        "validation_directory": str(validation),
        "legacy_services_changed": False,
        "legacy_source_status_before": status,
        "started_at": time.time(),
    }
    if signal_variant is not None:
        record["signal_variant"] = signal_variant
    if "strategy_versions" in variant_settings:
        record["strategy_versions"] = variant_settings["strategy_versions"]
    write(launch / "launch.json", record)
    verify(launch)

    def run_unit(name, cmd, log, memory):
        subprocess.run(
            [
                "systemd-run",
                "--user",
                "--unit=" + name,
                "--service-type=exec",
                "--working-directory=" + str(PROJECT),
                "--setenv=PYTHONPATH=" + previous["source_root"] + "/src",
                "--property=Restart=no",
                "--property=Nice=15",
                "--property=CPUWeight=10",
                "--property=MemoryMax=" + memory,
                "--property=RestrictAddressFamilies=AF_UNIX",
                "--property=KillSignal=SIGINT",
                "--property=TimeoutStopSec=45",
                "--property=StandardOutput=append:" + str(log),
                "--property=StandardError=inherit",
                *cmd,
            ],
            check=True,
        )

    run_unit(unit, command, out / "console.log", "640M")
    deadline = time.monotonic() + 20
    while not (out / "plan.json").exists() and time.monotonic() < deadline:
        time.sleep(0.2)
    if not (out / "plan.json").exists():
        raise RuntimeError("FOLLOWER_PLAN_NOT_CREATED")
    # Compatibility with the first validated follower bundle: ledger follow
    # mode uses the source recording's original runtime horizon.
    plan = read(out / "plan.json")
    source_plan = read(source / "plan.json")
    for field in ("started_utc_epoch", "max_seconds"):
        plan.setdefault(field, source_plan[field])
    write(out / "plan.json", plan)
    assumed = PROJECT / "research_outputs/m511_sim/assumed_fill" / label
    assumed.mkdir(parents=True)
    assumed_unit = "polymaker-m511-assumed-" + label.lower() + ".service"
    assumed_command = [
        str(PROJECT / ".venv/bin/python"),
        "-u",
        str(launch / "extension/m511_sim_instant_fill.py"),
        "--source-run",
        str(out),
        "--output",
        str(assumed),
        "--source-scenario",
        "research/trades-only",
        "--follow",
        "--interval",
        "15",
        "--source-unit",
        unit,
    ]
    run_unit(assumed_unit, assumed_command, assumed / "console.log", "128M")
    record.update(
        status="STARTED",
        assumed_fill_directory=str(assumed),
        assumed_fill_unit=assumed_unit,
        assumed_fill_command=assumed_command,
    )
    write(launch / "launch.json", record)
    write(PROJECT / "research_outputs/m511_sim/current.json", record)
    # Existing chat patrol remains disabled; this is only a pointer to the new
    # authorized test service. Never modify its run/unit or create a timer.
    current = read(control_path)
    if current["run_directory"] == str(source):
        current["m5_m11_signal_test"] = {
            k: record[k]
            for k in ("unit", "directory", "assumed_fill_directory", "assumed_fill_unit")
        }
        write(control_path, current)
    return record


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--validation", type=Path, required=True)
    p.add_argument("--min-epoch", type=int, help="Start from an existing recorded market.")
    args = p.parse_args()
    print(json.dumps(start(args.validation.resolve(), min_epoch=args.min_epoch), ensure_ascii=False, indent=2))
