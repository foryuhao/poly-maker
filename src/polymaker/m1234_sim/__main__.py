"""Simulation-only commands. No command permits live trading."""

import argparse
import asyncio
import json
from pathlib import Path

from polymaker.m1234_sim.events import write_json


def main():
    parser = argparse.ArgumentParser(description="M1234 SIMULATION ONLY; no wallets or real orders")
    sub = parser.add_subparsers(dest="command", required=True)
    live = sub.add_parser("run")
    live.add_argument("--config", required=True)
    live.add_argument("--contract", required=True)
    live.add_argument("--output", required=True)
    check = sub.add_parser("check", help="Validate settings and planned disk space; no network")
    check.add_argument("--config", required=True)
    check.add_argument("--contract", required=True)
    replay = sub.add_parser("replay")
    replay.add_argument("--input", required=True)
    replay.add_argument("--output", required=True)
    for name in ("recover", "settle"):
        continuation = sub.add_parser(name)
        continuation.add_argument("--input", required=True)
        continuation.add_argument("--output", required=True)
    audit = sub.add_parser("audit")
    audit.add_argument("--database", required=True)
    report = sub.add_parser("report")
    report.add_argument("--database", nargs="+", required=True)
    report.add_argument("--output", required=True)
    calibration = sub.add_parser("calibrate")
    calibration.add_argument("--actual", required=True)
    calibration.add_argument("--modelled")
    calibration.add_argument("--split-at", type=float)
    calibration.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.command == "check":
        from polymaker.m1234_sim.runner import preflight

        value = preflight(args.config, args.contract)
    elif args.command == "run":
        from polymaker.m1234_sim.runner import LiveRun, settings

        value = asyncio.run(LiveRun(args.output, settings(args.config, args.contract)).run())
    elif args.command == "replay":
        from polymaker.m1234_sim.runner import replay

        value = asyncio.run(replay(args.input, args.output))
    elif args.command in {"recover", "settle"}:
        from polymaker.m1234_sim.runner import recover

        value = asyncio.run(recover(args.input, args.output, settle=args.command == "settle"))
    elif args.command == "audit":
        from polymaker.m1234_sim.audit import reconcile

        value = reconcile(args.database)
    elif args.command == "report":
        from polymaker.m1234_sim.report import publish, summary

        Path(args.output).mkdir(parents=True, exist_ok=True)
        value = publish(args.output, [summary(p) for p in args.database])
        value = {"status": value["status"], "markets": len(value["markets"]),
                 "invalid_markets": value["invalid_markets"], "output": args.output}
    else:
        from polymaker.m1234_sim.calibration import assess, load_records

        value = assess(
            load_records(args.actual),
            load_records(args.modelled) if args.modelled else None,
            split_at=args.split_at,
        )
        write_json(args.output, value)
    print(json.dumps(value, indent=2, default=str))
    if value.get("status") == "FAIL":
        raise SystemExit(1)
    if args.command == "audit" and value.get("acceptance_status") != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
