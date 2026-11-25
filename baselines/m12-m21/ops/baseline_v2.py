"""Run one public-data strategy in an independent Baseline V2 process."""

import argparse
import asyncio
import fcntl
import json
import math
import re
import shutil
import signal
import time
from datetime import datetime, timezone
from pathlib import Path

from m1223_shared72 import (
    BASELINE_V2,
    MARKET_SECONDS,
    PREPARE_SECONDS,
    SCHEMA,
    VARIANTS,
    SharedRun,
    configuration,
)
from m1223_shared_storage import storage_status

PROJECT = Path(__file__).resolve().parents[1]


class SingleStrategyRun(SharedRun):
    def plan_fields(self):
        strategy, = self.parameters
        return {"strategy": strategy, "baseline_architecture": BASELINE_V2}


def read_plan(output, strategy):
    plan = json.loads((output / "plan.json").read_text())
    if (plan["schema"] != SCHEMA or plan["mode"] != "SIMULATION_ONLY_PUBLIC_DATA"
            or plan["path"] != str(output) or plan.get("baseline_architecture") != BASELINE_V2
            or plan.get("strategy") != strategy
            or tuple(plan["settings"]["strategy_definitions"]) != (strategy,)):
        raise ValueError("BASELINE_V2_RUN_IDENTITY_MISMATCH")
    return plan


def resume_session(output, strategy, settings, now):
    plan = read_plan(output, strategy)
    epochs = plan["market_epochs"]
    if (not epochs or epochs[0] % MARKET_SECONDS
            or epochs != list(range(epochs[0], epochs[-1] + MARKET_SECONDS, MARKET_SECONDS))
            or len(epochs) > settings["max_markets"]):
        raise ValueError("BASELINE_V2_INVALID_MARKET_PLAN")
    if plan["settings"] != settings or plan.get("continuation_settings", settings) != settings:
        raise ValueError("BASELINE_V2_RESUME_SETTINGS_CHANGED")
    earliest = math.ceil((now + PREPARE_SECONDS + 5) / MARKET_SECONDS) * MARKET_SECONDS
    first = next((epoch for epoch in epochs if epoch >= earliest), None)
    if first is None:
        raise ValueError("BASELINE_V2_NO_FUTURE_MARKETS")
    if any((output / f"btc-updown-5m-{epoch}").exists() for epoch in epochs if epoch >= first):
        raise ValueError("BASELINE_V2_RESUME_OVERLAPS_EXISTING_MARKET")
    return {"market_epochs": [e for e in epochs if e < first], "settings": settings}, epochs[-1] + MARKET_SECONDS


async def supervise(session):
    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, session.request_stop, "MANUAL_STOP")
    return await session.run()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "run", "resume", "report"))
    parser.add_argument("--strategy", required=True, action="append", choices=tuple(VARIANTS))
    parser.add_argument("--project-root", type=Path, default=PROJECT)
    parser.add_argument("--storage-root", type=Path)
    parser.add_argument("--run-name", help="Single-instance directory name; required for resume or report")
    parser.add_argument("--end-epoch", type=int, help="Exclusive UTC epoch of the final market close for run")
    args = parser.parse_args()
    if len(args.strategy) != 1:
        parser.error("each Baseline V2 process requires exactly one --strategy")
    args.strategy, = args.strategy
    if args.end_epoch is not None and (args.action != "run" or args.end_epoch % MARKET_SECONDS):
        parser.error("--end-epoch requires run and a five-minute boundary")
    project = args.project_root.resolve(strict=True)
    root = (args.storage_root or project / "research_outputs/m122124_baseline").resolve(strict=True)
    if root.name != "m122124_baseline" or not root.is_dir():
        parser.error("use a dedicated existing m122124_baseline directory")
    settings, parameters = configuration((args.strategy,))
    if args.action == "check":
        disk = storage_status(root, project)
        passed = not disk["reason"] and shutil.which("zstd") is not None
        print(json.dumps({"status": "PASS" if passed else "FAIL",
                          "baseline_architecture": BASELINE_V2, "strategy": args.strategy,
                          "fill_model": settings["sop_execution"]["fill_model"],
                          "input_archive": False, "storage": disk, "real_orders": 0}))
        if not passed:
            raise SystemExit(2)
        return
    name = args.run_name
    if name is None and args.action == "run":
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        name = f"{args.strategy.lower()}-baseline-v2-{stamp}"
    if not name or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
        parser.error("provide a single directory name with --run-name")
    output = root / name
    if output.is_symlink():
        parser.error("--run-name must not resolve through a symlink")
    if args.action == "report":
        from m122124_hourly_watch import BEIJING, collect

        read_plan(output, args.strategy)
        print(json.dumps(collect(output, {}, {}, datetime.now(BEIJING)), ensure_ascii=False, indent=2))
        return
    disk = storage_status(root, project)
    if disk["reason"] or not shutil.which("zstd"):
        parser.error("baseline disk reserve and zstd are required")
    if args.end_epoch is not None:
        first = math.ceil((time.time() + PREPARE_SECONDS + 5) / MARKET_SECONDS) * MARKET_SECONDS
        if not MARKET_SECONDS <= args.end_epoch - first <= settings["max_markets"] * MARKET_SECONDS:
            parser.error("--end-epoch must leave at least one full market within the baseline duration")

    # The historic M12 continuation already owns this lock when it is running.
    lock_name = "pair-cutover.lock" if args.strategy == "M12" else f"{args.strategy.lower()}-baseline-v2.lock"
    with (root / lock_name).open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error(f"{args.strategy} already has a running baseline in this storage root")
        predecessor, end = None, args.end_epoch
        archived = original_plan = None
        if args.action == "resume":
            predecessor, end = resume_session(output, args.strategy, settings, time.time())
            original_plan = (output / "plan.json").read_bytes()
            stale = output / "continuation-completion.json"
            if stale.is_file():
                history = output / "restart-history"
                history.mkdir(exist_ok=True)
                archived = history / f"completion-{time.time_ns()}.json"
                stale.replace(archived)
        else:
            output.mkdir(exist_ok=False)
        session = SingleStrategyRun(output, project, settings, parameters,
                                    predecessor=predecessor, scheduled_end=end)
        print(json.dumps({"event": "BASELINE_V2_STARTING", "strategy": args.strategy,
                          "run_directory": str(output)}), flush=True)
        try:
            result = asyncio.run(supervise(session))
        finally:
            if (archived is not None and not stale.exists()
                    and (output / "plan.json").read_bytes() == original_plan):
                archived.replace(stale)
        if result["status"] != "PASS":
            raise SystemExit(2)


if __name__ == "__main__":
    main()
