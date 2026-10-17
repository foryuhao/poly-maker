"""Standalone one-hour observer: python -m polymaker.rdp_sm_sync_observe --help.

This command never starts a strategy or modifies production execution state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import resource
import signal
import threading
import time
import uuid
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from polymaker.rdp_sm_sync import SyncObserver, SyncSettings
from polymaker.rdp_sm_sync_reads import ObserverReader


def distribution(values):
    values = sorted(values)
    if not values:
        return {"n": 0}
    return {"n": len(values), "p50": values[(len(values) - 1) // 2],
            "p95": values[math.ceil(len(values) * .95) - 1],
            "p99": values[math.ceil(len(values) * .99) - 1], "max": values[-1]}


class ObserverStats:
    def __init__(self):
        self.http = defaultdict(list)
        self.http_status = Counter()
        self.rounds = defaultdict(list)
        self.intervals = defaultdict(list)
        self.last_start = {}
        self.round_counts = Counter()
        self.source_failures = Counter()
        self.markets = []
        self.confirmed_application_ms = []
        self.coverage_backlogs = []
        self.last_good = {}
        self.source_ages_ms = defaultdict(list)
        self.consistency = Counter()
        self.quantity_differences = []

    def observe(self, event):
        kind = event["kind"]
        if kind == "sync_http":
            self.http[event["endpoint"]].append(
                (event["completed_monotonic"] - event["started_monotonic"]) * 1000)
            self.http_status[(event["http_status"], event["error_type"])] += 1
        elif kind == "sync_round" and not event.get("shutdown"):
            lane = event["lane"]
            self.rounds[lane].append(event["duration_s"] * 1000)
            self.round_counts[lane, "total"] += 1
            self.round_counts[lane, "complete"] += int(event["complete"])
            self.round_counts[lane, "overrun"] += int(event["overrun"])
            if lane in self.last_start:
                self.intervals[lane].append((event["started_monotonic"] - self.last_start[lane]) * 1000)
            self.last_start[lane] = event["started_monotonic"]
        elif kind == "sync_snapshot":
            for name, read in event["attempt_updates"].items():
                if read["complete"]:
                    self.last_good[name] = read["started_monotonic"]
                elif not (event.get("shutdown") and read["error_type"] == "InterruptedError"):
                    self.source_failures[name.split(":")[0], read["error_type"]] += 1
            for name in ("time", "open_orders", "trades", "capital", "positions"):
                if name in self.last_good:
                    self.source_ages_ms[name].append(
                        (event["published_monotonic"] - self.last_good[name]) * 1000)
        elif kind == "sync_market":
            self.markets.append(event["slug"])
            self.last_good.pop("trades", None)
        elif kind == "sync_trade_observed" and event["status"] == "CONFIRMED":
            self.confirmed_application_ms.append(
                (event["applied_monotonic"] - event["observed_monotonic"]) * 1000)
        elif kind == "sync_detail_coverage":
            self.coverage_backlogs.append(event["unread_pending"])
        elif kind == "sync_consistency":
            self.consistency["total"] += 1
            self.consistency["agree" if event["observations_agree"] else "differ"] += 1
            self.quantity_differences.append(abs(event["quantity_delta_shares"]))

    def to_dict(self):
        return {
            "http_ms_by_endpoint": {k: distribution(v) for k, v in self.http.items()},
            "http_total": sum(len(v) for v in self.http.values()),
            "http_results": [{"status": k[0], "error_type": k[1], "count": v}
                             for k, v in self.http_status.items()],
            "round_ms": {k: distribution(v) for k, v in self.rounds.items()},
            "actual_start_interval_ms": {k: distribution(v) for k, v in self.intervals.items()},
            "round_counts": {lane: {key: self.round_counts[lane, key]
                                    for key in ("total", "complete", "overrun")}
                             for lane in self.rounds},
            "source_failures": [{"source": k[0], "error_type": k[1], "count": v}
                                for k, v in self.source_failures.items()],
            "source_age_at_publication_ms": {k: distribution(v) for k, v in self.source_ages_ms.items()},
            "observed_markets": self.markets,
            "confirmed_read_to_observer_apply_ms": distribution(self.confirmed_application_ms),
            "unread_detail_backlog": distribution(self.coverage_backlogs),
            "consistency_observations": dict(self.consistency),
            "absolute_quantity_difference_shares": distribution(self.quantity_differences),
        }


@contextmanager
def stop_signals(stop):
    previous = {}
    if threading.current_thread() is threading.main_thread():
        for name in (signal.SIGINT, signal.SIGTERM):
            previous[name] = signal.signal(name, lambda *_args: stop.set())
    try:
        yield
    finally:
        for name, handler in previous.items():
            signal.signal(name, handler)


def run_observation(service, duration_s, output_dir, *, stop=None):
    if not math.isfinite(duration_s) or duration_s <= 0:
        raise ValueError("SYNC_DURATION_INVALID")
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:12]
    directory = root / run_id
    directory.mkdir()
    stop = stop if stop is not None else threading.Event()
    started, wall, cpu = time.monotonic(), time.time(), time.process_time()
    stats = ObserverStats()
    manifest = {"mode": "OBSERVE_ONLY", "trading_authorized": False,
                "started_wall": wall, "requested_duration_s": duration_s,
                "order_interval_s": service.settings.order_interval_s,
                "account_interval_s": service.settings.account_interval_s,
                "detail_limit": service.settings.detail_limit,
                "production_integration": False,
                "source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in Path(__file__).parent.glob("rdp_sm_sync*.py")}}
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    drained = False
    with (directory / "events.jsonl").open("x", encoding="utf-8", buffering=1) as stream:
        def drain():
            for event in service.drain_events():
                stats.observe(event)
                stream.write(json.dumps(event, allow_nan=False) + "\n")

        with stop_signals(stop):
            try:
                service.start()
                version = 0
                while time.monotonic() - started < duration_s and not stop.is_set():
                    snapshot = service.wait_after(version, timeout=min(.25, duration_s))
                    version = snapshot.version
                    drain()
                    if snapshot.status in {"FAILED", "STOPPED"}:
                        break
            finally:
                drained = service.stop()
                drain()
    snapshot = service.latest()
    elapsed = time.monotonic() - started
    status = "COMPLETED"
    if not drained or snapshot.fatal_type or service.dropped_events:
        status = "FAILED"
    elif stop.is_set():
        status = "INTERRUPTED"
    elif time.monotonic() - started < duration_s:
        status = "INCOMPLETE"
    report = {**manifest, "status": status, "actual_duration_s": elapsed,
              "cpu_seconds": time.process_time() - cpu,
              "cpu_one_core_pct": 100 * (time.process_time() - cpu) / elapsed,
              "process_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
              "io_drained": drained, "dropped_events": service.dropped_events,
              "fatal_type": snapshot.fatal_type, "metrics": stats.to_dict(),
              "directory": str(directory.resolve())}
    (directory / "summary.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Independent GET-only BTC5m sync observer; no trading.")
    parser.add_argument("--config-dir", default="config")
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--duration-seconds", type=float, default=3600)
    parser.add_argument("--order-ms", type=float, default=1000)
    parser.add_argument("--account-ms", type=float, default=2000)
    parser.add_argument("--output-dir", default="research_outputs/periodic_sync")
    args = parser.parse_args(argv)
    from polymaker.config import Config, Secrets

    try:
        settings = SyncSettings(args.order_ms / 1000, args.account_ms / 1000)
        if not math.isfinite(args.duration_seconds) or args.duration_seconds <= 0:
            raise ValueError("SYNC_DURATION_INVALID")
        if args.env_file is not None:
            from dotenv import load_dotenv

            # The observer runs in its own process; preserve the configured proxy
            # route without touching the strategy's environment or SDK transport.
            load_dotenv(args.env_file)
        cfg = Config.load(args.config_dir, load_env=args.env_file is None)
        if args.env_file is not None:
            cfg.secrets = Secrets(_env_file=args.env_file)
        service = SyncObserver(lambda emit, stop: ObserverReader(cfg, emit=emit, stop=stop), settings)
        report = run_observation(service, args.duration_seconds, args.output_dir)
    except Exception as exc:
        # No exception message, config repr, request headers or auth body on stdout.
        print(json.dumps({"mode": "OBSERVE_ONLY", "status": "FAILED", "error_type": type(exc).__name__}))
        return 2
    print(json.dumps(report, allow_nan=False))
    return 0 if report["status"] == "COMPLETED" and report["metrics"]["round_counts"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
