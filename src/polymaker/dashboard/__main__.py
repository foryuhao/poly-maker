"""Standalone entry point; never imports the trading CLI or loads .env/config."""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from pathlib import Path

from polymaker.dashboard.archive import Archiver
from polymaker.dashboard.collector import Collector, atomic_json, public_snapshot
from polymaker.dashboard.view import watch


def run_archive(project: Path, output: Path, *, once: bool = False, history_hours: float = 24,
                journal_dirs: list[Path] | None = None, refresh: float = 1) -> int:
    if history_hours <= 0 or refresh < .25:
        raise ValueError("history hours must be positive and refresh must be at least 0.25 seconds")
    # Only this independent observer's scheduling priority changes.
    priority = os.getpriority(os.PRIO_PROCESS, 0)
    if priority < 10:
        os.nice(10 - priority)
    collector = Collector(project, output, history_hours=history_hours, journal_dirs=journal_dirs)
    archiver = Archiver(collector)
    stopping = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    handlers = {}
    for signum in (signal.SIGTERM, signal.SIGINT):
        handlers[signum] = signal.signal(signum, stop)
    try:
        while not stopping:
            started = time.monotonic()
            try:
                snapshot = collector.poll()
                archiver.tick(snapshot)
                atomic_json(collector.output / "latest.json", public_snapshot(snapshot))
                if archiver.last_error:
                    print(f"dashboard archive error: {archiver.last_error}", file=sys.stderr, flush=True)
            except (OSError, ValueError) as exc:
                # Last good snapshot becomes visibly stale; never fabricate a fresh one.
                print(f"dashboard observer error: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
                if once:
                    return 2
            if once:
                return 2 if archiver.last_error else 0
            remaining = max(0, refresh - (time.monotonic() - started))
            until = time.monotonic() + remaining
            while not stopping and time.monotonic() < until:
                time.sleep(min(.2, until - time.monotonic()))
    finally:
        collector.close()
        for signum, handler in handlers.items():
            signal.signal(signum, handler)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PolyMaker independent read-only dashboard")
    parser.add_argument("mode", nargs="?", choices=("view", "archive"), default="view")
    parser.add_argument("--project-dir", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--journal-dir", type=Path, action="append", default=None)
    parser.add_argument("--history-hours", type=float, default=24)
    parser.add_argument("--refresh", type=float, default=1)
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    output = args.output_dir or args.project_dir / "research_outputs/dashboard"
    try:
        if args.mode == "archive":
            return run_archive(args.project_dir, output, once=args.once, history_hours=args.history_hours,
                               journal_dirs=args.journal_dir, refresh=args.refresh)
        if args.history_hours <= 0 or args.refresh < .25:
            parser.error("history hours must be positive and refresh must be at least 0.25 seconds")
        return watch(output, once=args.once, json_output=args.json_output,
                     session_id=args.session_id, hours=args.history_hours, refresh=args.refresh)
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"dashboard: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
