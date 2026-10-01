"""Bounded, asynchronous Research persistence with explicit incomplete-session evidence."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from queue import Empty
from threading import Event, Lock, Thread
from typing import Any, TextIO

from polymaker.research.observer import BoundedResearchQueue, ResearchItem
from polymaker.research.schema import DatasetManifest, Episode, ResearchEvent, StateSnapshot


class ResearchWriter:
    """The only Research disk owner. Queue admission never waits for this thread."""

    def __init__(self, output_root: Path, dataset_id: str, session_id: str,
                 queue: BoundedResearchQueue, *, manifest: DatasetManifest | None = None,
                 flush_interval_s: float = 1.0, fsync_interval_s: float = 5.0) -> None:
        for name in (dataset_id, session_id):
            if not name or name in (".", "..") or "/" in name or "\\" in name:
                raise ValueError("dataset and session IDs must be single path components")
        if flush_interval_s <= 0 or fsync_interval_s <= 0:
            raise ValueError("flush and fsync intervals must be positive")
        if manifest is not None and (manifest.dataset_id != dataset_id
                                     or manifest.session_id != session_id):
            raise ValueError("manifest identity differs from writer identity")
        self.directory = Path(output_root) / dataset_id / session_id
        self.queue = queue
        self.manifest = manifest or DatasetManifest(
            schema_version=1, dataset_id=dataset_id, session_id=session_id,
            code_commit="UNKNOWN", strategy_config_id="UNKNOWN",
            effective_strategy_config={}, config_sha256="UNKNOWN",
            feature_version=None, label_version=None, policy_version=None,
            execution_version=None, payload_versions={}, condition_id="",
            market_slug="", yes_token_id="", no_token_id="",
            split_boundaries_us={}, started_at_us=time.time_ns() // 1000,
            ended_at_us=None, file_sha256={}, event_watermark=0,
            gap_count=0, close_status="OPEN")
        self.flush_interval_s = flush_interval_s
        self.fsync_interval_s = fsync_interval_s
        self._stop = Event()
        self.ready = Event()
        self._thread: Thread | None = None
        self._close_lock = Lock()
        self.error: Exception | None = None
        self._written = 0
        self._persisted = 0
        self._watermark = 0
        self._durable_watermark = 0
        self._gap_rows = 0
        self._certified_complete = False
        self.failure_count_provider: Callable[[], int] | None = None

    @staticmethod
    def _atomic_json(path: Path, value: dict[str, Any]) -> None:
        temporary = path.with_name(path.name + ".tmp")
        with temporary.open("x", encoding="utf-8") as file:
            json.dump(value, file, sort_keys=True, separators=(",", ":"), allow_nan=False)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("writer already started")
        # Thread creation is the entire live-loop startup path. All filesystem
        # initialization, including the OPEN manifest fsync, belongs to _run.
        self._thread = Thread(target=self._run, name="research-writer", daemon=True)
        self._thread.start()

    @staticmethod
    def _commit_manifest(path: Path, value: dict[str, Any]) -> None:
        """Last-step certification: no fallible operation follows os.replace.

        The temp content and its directory entry are fsynced before replace.
        A crash may retain the earlier OPEN manifest rather than the final one;
        any visible COMPLETE content was fsynced before this commit point.
        """
        temporary = path.with_name(path.name + ".commit.tmp")
        with temporary.open("x", encoding="utf-8") as file:
            json.dump(value, file, sort_keys=True, separators=(",", ":"), allow_nan=False)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, path)

    def close(self, timeout: float = 5.0) -> bool:
        if self._thread is None:
            raise RuntimeError("writer not started")
        with self._close_lock:
            self.queue.close_producer()
            self._stop.set()
        self._thread.join(max(0.0, timeout))
        return not self._thread.is_alive() and self._certified_complete

    def _sync(self, files: dict[str, TextIO]) -> None:
        for file in files.values():
            file.flush()
            os.fsync(file.fileno())
        self._persisted = self._written
        self._durable_watermark = self._watermark

    def _run(self) -> None:
        files: dict[str, TextIO] = {}
        try:
            if self.manifest.code_commit == "UNKNOWN":
                try:
                    commit = subprocess.run(
                        ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                        check=True, timeout=1).stdout.strip()
                    self.manifest = self.manifest.model_copy(update={"code_commit": commit})
                except Exception:
                    pass
            self.directory.mkdir(parents=True, exist_ok=True)
            self._atomic_json(self.directory / "manifest.json",
                              self.manifest.model_dump(mode="json"))
            for name in ("events", "episodes", "states"):
                files[name] = (self.directory / f"{name}.jsonl").open("x", encoding="utf-8")
            self.ready.set()
            last_flush = last_fsync = time.monotonic()
            while not self._stop.is_set() or not self.queue.empty():
                try:
                    item = self.queue.get(timeout=0.05)
                except Empty:
                    item = None
                if item is not None:
                    try:
                        self._write(item, files)
                        self._written += 1
                        if isinstance(item, ResearchEvent):
                            self._watermark = max(self._watermark, item.event_seq)
                            if item.kind == "GAP":
                                self._gap_rows += 1
                    finally:
                        self.queue.task_done()
                now = time.monotonic()
                if now - last_flush >= self.flush_interval_s:
                    for file in files.values():
                        file.flush()
                    last_flush = now
                if now - last_fsync >= self.fsync_interval_s:
                    self._sync(files)
                    last_fsync = now
            self._sync(files)
        except Exception as exc:
            self.error = exc
            self.ready.set()
            # Stop admission before declaring the writer finished. Keep the
            # thread alive until session close so later rejected attempts are
            # included in the final loss evidence.
            self.queue.close_producer()
            self._stop.wait()
        finally:
            # Wait only for in-memory capture/admission work. After this point,
            # new callbacks are outside this Research session, so final loss
            # counters cannot change while disk metadata is written.
            self.queue.seal_session()
            for file in files.values():
                try:
                    file.close()
                except Exception as exc:
                    self.error = self.error or exc
            try:
                self._finalize()
            except Exception as exc:
                self.error = self.error or exc

    def _finalize(self) -> None:
        counters = self.queue.counters
        lost_ranges = self.queue.lost_event_ranges
        lost_reason_counts = self.queue.lost_reason_counts
        observer_failures = (self.failure_count_provider()
                             if self.failure_count_provider is not None else 0)
        incomplete = (self.error is not None or self._persisted != counters.accepted
                      or observer_failures > 0
                      or counters.rejected_full > 0 or counters.rejected_closed > 0
                      or counters.lost_event_count > 0)
        hashes: dict[str, str] = {}
        if not incomplete:
            try:
                for name in ("events", "episodes", "states"):
                    path = self.directory / f"{name}.jsonl"
                    digest = hashlib.sha256()
                    with path.open("rb") as file:
                        for chunk in iter(lambda: file.read(1024 * 1024), b""):
                            digest.update(chunk)
                    hashes[path.name] = digest.hexdigest()
            except Exception as exc:
                self.error = self.error or exc
                incomplete = True
                hashes = {}
        reasons = []
        if self.error is not None:
            reasons.append("WRITER_FAILURE")
        if counters.rejected_full or counters.rejected_closed:
            reasons.append("QUEUE_REJECTION")
        if counters.lost_event_count:
            reasons.append("CAUSAL_EVENT_LOSS")
        if self._persisted < counters.accepted:
            reasons.append("ACCEPTED_RECORD_NOT_DURABLE")
        if observer_failures:
            reasons.append("OBSERVER_FAILURE")
        update = dict(ended_at_us=time.time_ns() // 1000, file_sha256=hashes,
                      event_watermark=self._durable_watermark,
                      gap_count=counters.lost_range_count,
                      close_status="INCOMPLETE" if incomplete else "COMPLETE",
                      accepted_count=counters.accepted, persisted_count=self._persisted,
                      rejected_full=counters.rejected_full,
                      rejected_closed=counters.rejected_closed,
                      lost_event_ranges=lost_ranges,
                      writer_error=type(self.error).__name__ if self.error else None,
                      queue_high_water=counters.high_water,
                      observer_failures=observer_failures,
                      lost_event_count=counters.lost_event_count,
                      lost_ranges_truncated=counters.lost_ranges_truncated,
                      lost_reason_counts=lost_reason_counts)
        final = self.manifest.model_copy(update=update)
        quality = dict(schema_version=1, dataset_id=self.manifest.dataset_id,
                       session_id=self.manifest.session_id,
                       status="DEGRADED" if incomplete else "FINALIZING",
                       reasons=reasons, accepted=counters.accepted,
                       persisted=self._persisted, rejected_full=counters.rejected_full,
                       rejected_closed=counters.rejected_closed,
                       queue_high_water=counters.high_water,
                       gap_count=counters.lost_range_count,
                       gap_rows_persisted=self._gap_rows,
                       lost_event_ranges=lost_ranges,
                       lost_event_count=counters.lost_event_count,
                       lost_ranges_truncated=counters.lost_ranges_truncated,
                       lost_reason_counts=lost_reason_counts,
                       durable_event_watermark=self._durable_watermark,
                       clean_close=False if incomplete else None)
        quality["observer_failures"] = observer_failures
        try:
            self._atomic_json(self.directory / "quality.json", quality)
        except Exception as exc:
            self.error = self.error or exc
            final = final.model_copy(update={"close_status": "INCOMPLETE",
                                             "file_sha256": {},
                                             "writer_error": type(self.error).__name__})
        try:
            self._commit_manifest(self.directory / "manifest.json",
                                  final.model_dump(mode="json"))
            self._certified_complete = final.close_status == "COMPLETE"
        except Exception as exc:
            self.error = self.error or exc

    @staticmethod
    def _write(item: ResearchItem, files: dict[str, TextIO]) -> None:
        if isinstance(item, ResearchEvent):
            name = "events"
        elif isinstance(item, Episode):
            name = "episodes"
        elif isinstance(item, StateSnapshot):
            name = "states"
        else:
            raise TypeError("unsupported research record")
        files[name].write(item.model_dump_json() + "\n")
