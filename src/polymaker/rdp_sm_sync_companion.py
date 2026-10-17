"""Per-invocation pinned cache and diagnostics owned by rdp-sm-run.

Execution may consume immutable snapshots when use_for_execution is enabled;
the diagnostic recorder is never on the trade or HTTP path.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import queue
import resource
import threading
import time
from dataclasses import asdict
from pathlib import Path

from polymaker.rdp_sm_sync import SyncObserver, SyncSettings
from polymaker.rdp_sm_sync_observe import ObserverStats
from polymaker.rdp_sm_sync_reads import ObservedMarket, ObserverReader

log = logging.getLogger("polymaker.sync_observe")
REFERENCE_KINDS = frozenset({
    "sm_session", "sm_strategy_window", "sm_obi_final_decision", "sm_obi_close_requested",
    "sm_cancel_ack", "sm_close_barrier", "sm_trade", "sm_window_result", "sm_run_result",
    "sm_placement_intent", "sm_placement_ack", "sm_placement_unknown", "sm_placement_result",
    "entry_result",
    "sm_sync_cache_read",
})
SCALARS = (
    "window_id", "phase", "state", "result", "reason", "stop_reason", "execution_status",
    "order_id", "expected_order_id", "trade_id", "maker_order_id", "token_id", "outcome",
    "status", "price", "size", "confirmed_yes", "confirmed_no", "delta", "final_close_barrier",
    "purpose", "snapshot_version", "operation_after_monotonic",
)
TIME_FIELDS = (
    "post_started", "post_returned", "requested_monotonic", "delete_requested",
    "delete_sdk_started", "delete_ack", "prepared_at", "persisted_at", "activated_at",
)


def numeric(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def slim_reference(kind, payload, monotonic):
    """Whitelist only public execution facts; never enqueue raw auth/replies."""
    result = {"kind": kind, "recorded_monotonic": monotonic, "recorded_wall": time.time(),
              "fields": {}, "milestones": {"recorded": monotonic}}
    for source in (payload, payload.get("intent", {})):
        if not isinstance(source, dict):
            continue
        for key in SCALARS:
            if key not in source:
                continue
            value = source.get(key)
            if ((value is None or isinstance(value, (str, int, float, bool)))
                    and (not isinstance(value, float) or math.isfinite(value))):
                result["fields"][key] = value
        for key in TIME_FIELDS:
            if numeric(source.get(key)):
                result["milestones"][key] = source[key]
        for key in ("ids", "known_order_ids", "observed_order_ids"):
            values = source.get(key)
            if isinstance(values, (list, tuple)):
                result["fields"][key] = [v for v in values if isinstance(v, str)][:128]
    return result


class SyncCompanion:
    def __init__(self, cfg, session_id, *, reader_factory=None):
        options = cfg.rdp_sm.sync_observe
        self.session_id = session_id
        self.use_for_execution = options.use_for_execution
        self.directory = Path(options.output_dir).resolve() / session_id
        self.journal_file = str(Path(cfg.paths.journal_dir).resolve()
            / f"rdp_sm_{cfg.secrets.browser_address.lower()}_{session_id}.jsonl")
        self._main_market = None
        self._closing = threading.Event()
        self._writer_stop = threading.Event()
        self._lifecycle = threading.Lock()
        self._references = queue.Queue(maxsize=4096)
        self.dropped_references = 0
        self.failure = None
        self.summary = None
        self.io_drained = None
        self._thread = None
        self.service = SyncObserver(
            reader_factory or (lambda emit, stop: ObserverReader(cfg, emit=emit, stop=stop)),
            SyncSettings(options.order_interval_s, options.account_interval_s),
            follow_main=True, session_id=session_id,
        )

    def start(self):
        self._thread = threading.Thread(target=self._run, name="sync-observe-recorder")
        self._thread.start()

    def bind_market(self, meta, start):
        market = ObservedMarket(meta.slug, meta.condition_id, start,
                                (meta.yes.token_id, meta.no.token_id))
        self.service.bind_market(market)
        self._main_market = market

    def capture(self, kind, payload, *, monotonic=None):
        if kind not in REFERENCE_KINDS or self.failure or self._closing.is_set():
            return
        reference = slim_reference(kind, payload, time.monotonic() if monotonic is None else monotonic)
        try:
            self._references.put_nowait(reference)
        except queue.Full:
            self.dropped_references += 1

    def close(self):
        # Serialize start/stop so an early preflight failure cannot leave a late
        # recorder starting a worker after the owning invocation has exited.
        with self._lifecycle:
            self._closing.set()
        drained = self.service.stop()
        self.io_drained = drained
        if not drained:
            self.failure = "OBSERVER_DRAIN_TIMEOUT"
        self._writer_stop.set()
        if self._thread is not None:
            self._thread.join(30)
            if self._thread.is_alive():
                self.failure = "RECORDER_DRAIN_TIMEOUT"
        if self.failure:
            log.warning("SYNC_OBSERVER_FAILED session=%s reason=%s", self.session_id, self.failure)
        return not self.failure and drained

    def _reference_record(self, reference):
        linked = {}
        for name, stamp in reference["milestones"].items():
            snapshot = self.service.snapshot_at(stamp)
            context = json.loads(snapshot.market_json) if snapshot and snapshot.market_json else None
            matching = bool(context and self._main_market
                            and context["condition_id"] == self._main_market.condition_id
                            and snapshot.session_id == self.session_id)
            linked[name] = {
                "event_monotonic": stamp,
                "snapshot_version": snapshot.version if snapshot else None,
                "matching_main_market": matching,
                "snapshot_age_ms": (stamp - snapshot.published_monotonic) * 1000 if snapshot else None,
                "sources": {r.name: {"read_id": r.read_id,
                                      "age_ms": (stamp - r.started_monotonic) * 1000,
                                      "completed_before_event": r.completed_monotonic <= stamp}
                            for r in snapshot.last_good} if snapshot else {},
            }
        return {**reference, "session_id": self.session_id,
                "market_id": self._main_market.condition_id if self._main_market else None,
                "snapshot_links": linked, "trading_authorized": False}

    def _run(self):
        began, cpu = time.monotonic(), time.process_time()
        stats = ObserverStats()
        snapshots = references = market_snapshots = 0
        reference_gaps = 0
        try:
            self.directory.mkdir(parents=True, exist_ok=False)
            manifest = {"mode": "MAIN_PROCESS_COMPANION", "trading_authorized": False,
                        "execution_reads_from_cache": self.use_for_execution,
                        "pid": os.getpid(), "session_id": self.session_id,
                        "started_wall": time.time(), "settings": asdict(self.service.settings),
                        "market_selection": "MAIN_PROCESS_ONLY", "strategy_journal": self.journal_file,
                        "source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                            for p in [*Path(__file__).parent.glob("rdp_sm_sync*.py"),
                                      Path(__file__).with_name("rdp_sm_cached_reads.py")]}}
            manifest_path = self.directory / "manifest.json"
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
            with (self.directory / "events.jsonl").open("x", buffering=1) as events, (
                    self.directory / "snapshots.jsonl").open("x", buffering=1) as snapshot_file, (
                    self.directory / "strategy_refs.jsonl").open("x", buffering=1) as reference_file:
                with self._lifecycle:
                    if not self._closing.is_set():
                        self.service.start()
                        log.info("SYNC_OBSERVER_STARTED session=%s snapshots=%s",
                                 self.session_id, self.directory / "snapshots.jsonl")
                finishing = False
                while True:
                    for event in self.service.drain_events():
                        stats.observe(event)
                        snapshot = event.pop("full_snapshot", None)
                        if snapshot is not None:
                            snapshot_file.write(json.dumps(snapshot, allow_nan=False) + "\n")
                            snapshots += 1
                            market_snapshots += int(bool(snapshot["market"] and self._main_market
                                and snapshot["market"]["condition_id"] == self._main_market.condition_id))
                        events.write(json.dumps({**event, "session_id": self.session_id}, allow_nan=False) + "\n")
                    while True:
                        try:
                            reference = self._references.get_nowait()
                        except queue.Empty:
                            break
                        row = self._reference_record(reference)
                        reference_gaps += sum(not v["matching_main_market"]
                                              for v in row["snapshot_links"].values())
                        reference_file.write(json.dumps(row, allow_nan=False) + "\n")
                        references += 1
                    if self._writer_stop.is_set():
                        if finishing:
                            break
                        finishing = True
                        continue  # Drain once more after the publisher is stopped.
                    self._writer_stop.wait(.05)
            latest = self.service.latest()
            manifest["market"] = asdict(self._main_market) if self._main_market else None
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
            self.summary = {**manifest, "actual_duration_s": time.monotonic() - began,
                            "cpu_seconds_process_delta": time.process_time() - cpu,
                            "process_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                            "resource_scope": "WHOLE_MAIN_PROCESS_INCLUDING_STRATEGY",
                            "snapshot_count": snapshots, "main_market_snapshot_count": market_snapshots,
                            "strategy_reference_count": references,
                            "reference_market_or_history_gaps": reference_gaps,
                            "dropped_events": self.service.dropped_events,
                            "dropped_references": self.dropped_references,
                            "observer_status": latest.status, "observer_fatal_type": latest.fatal_type,
                            "io_drained": self.io_drained, "failure": self.failure,
                            "metrics": stats.to_dict(),
                            "status": "RECORDED" if (market_snapshots and self.io_drained and not self.failure
                                      and not latest.fatal_type
                                      and not self.service.dropped_events and not self.dropped_references)
                                      else "INCOMPLETE"}
            (self.directory / "summary.json").write_text(json.dumps(self.summary, indent=2, allow_nan=False) + "\n")
            log.info("SYNC_OBSERVER_STOPPED session=%s status=%s snapshots=%s",
                     self.session_id, self.summary["status"], snapshots)
        except Exception as exc:
            self.failure = type(exc).__name__  # Never leak credential/HTTP bodies.
            log.warning("SYNC_OBSERVER_RECORDER_FAILED session=%s error=%s",
                        self.session_id, self.failure)
        finally:
            # Also stop on disk/serialization failure; failures do not affect
            # the strategy's ledger, gate, cancellation or return status.
            self.service.stop()


async def close_companion(companion):
    task = asyncio.create_task(asyncio.to_thread(companion.close))
    interrupted = False
    while True:
        try:
            await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            interrupted = True
    if interrupted:
        raise asyncio.CancelledError()
