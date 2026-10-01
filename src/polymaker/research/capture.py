"""Small, failure-isolated adapter from applied live facts to ResearchEvent."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping
from threading import Lock
from typing import Any, TypeVar

from polymaker.research.observer import QueueCounters, ResearchObserver
from polymaker.research.schema import ResearchEvent

T = TypeVar("T")


def clock() -> tuple[int, int]:
    """Paired local wall and monotonic observation clocks."""
    return time.time_ns() // 1000, time.monotonic_ns()


def exchange_us(seconds: float | None) -> int | None:
    if seconds is None or not math.isfinite(seconds) or seconds <= 0:
        return None
    microseconds = seconds * 1_000_000
    return int(microseconds) if math.isfinite(microseconds) else None


class CausalCapture:
    def __init__(self, observer: ResearchObserver, session_id: str) -> None:
        self.observer = observer
        self.session_id = session_id
        # This is the queue's session/admission lock when capture is enabled.
        # The writer seals that same boundary before sampling final counters.
        self._lock = observer.queue.session_lock if observer.queue is not None else Lock()
        self._seq = 0
        self._last_us = 0
        self.failures = 0
        self.lost_events = 0
        self._lost_range: tuple[int, int, str] | None = None
        self._listeners: list[Callable[[ResearchEvent], None]] = []

    def add_listener(self, listener: Callable[[ResearchEvent], None]) -> None:
        with self._lock:
            self._listeners.append(listener)

    def synchronized(self, callback: Callable[[int, int, int], T]) -> T:
        """Copy a projection at one causal event boundary."""
        with self._lock:
            available, mono = clock()
            available = max(available, self._last_us)
            self._last_us = available
            return callback(self._seq, available, mono)

    @property
    def enabled(self) -> bool:
        return self.observer.config.enabled

    def _rejection_reason(self, before: QueueCounters | None) -> str:
        queue = self.observer.queue
        if queue is None or before is None:
            return "OBSERVER_REJECTION"
        after = queue.counters
        if after.rejected_full > before.rejected_full:
            return "QUEUE_CAPACITY"
        if after.rejected_closed > before.rejected_closed:
            return "ADMISSION_CLOSED"
        return "OBSERVER_REJECTION"

    def _record_loss(self, seq: int, reason: str) -> None:
        """Called with the causal lock held, before any later sequence can enter."""
        self.lost_events += 1
        prior = self._lost_range
        self._lost_range = (
            prior[0] if prior else seq, seq,
            reason if prior is None or prior[2] == reason else "MIXED_REJECTION",
        )
        if self.observer.queue is not None:
            try:
                self.observer.queue.note_lost_event(seq, reason)
            except Exception:
                self.failures += 1

    def emit(self, kind: str, condition_id: str, payload: Mapping[str, Any], *,
             source: str = "ENGINE", exchange_at_us: int | None = None,
             received_at_us: int | None = None,
             received_monotonic_ns: int | None = None,
             connection_generation: int | None = None) -> None:
        if not self.enabled:
            return
        # Sequence creation, failed-sequence accounting and GAP admission use
        # one ordering boundary. No disk/network work or queue wait occurs here.
        with self._lock:
            if self.observer.queue is not None and self.observer.queue.session_sealed:
                # The causal session ended before this callback entered capture.
                return
            seq: int | None = None
            try:
                available, mono = clock()
                available = max(available, self._last_us)
                self._last_us = available
                self._seq += 1
                seq = self._seq
                record = ResearchEvent.model_validate({
                    "schema_version": 1, "session_id": self.session_id, "event_seq": seq,
                    "condition_id": condition_id, "episode_id": None, "kind": kind,
                    "exchange_at_us": exchange_at_us, "received_at_us": received_at_us,
                    "available_at_us": available, "monotonic_ns": mono, "source": source,
                    "connection_generation": connection_generation, "source_ref": None,
                    "payload": ({**payload, "received_monotonic_ns": received_monotonic_ns}
                                if received_monotonic_ns is not None else payload),
                })
            except Exception:
                self.failures += 1
                if seq is not None:
                    self._record_loss(seq, "SCHEMA_REJECTION")
                return
            try:
                if self._lost_range is not None:
                    first, last, reason = self._lost_range
                    gap = record.model_copy(update={
                        "event_seq": first, "kind": "GAP", "source": "OBSERVER",
                        "exchange_at_us": None, "received_at_us": None,
                        "connection_generation": None,
                        "payload": {"lost_start_seq": first, "lost_end_seq": last,
                                    "count": last - first + 1, "reason": reason},
                    })
                    before = self.observer.queue.counters if self.observer.queue else None
                    if self.observer.observe(gap):
                        self._lost_range = None
                    else:
                        self._record_loss(seq, self._rejection_reason(before))
                        return
                before = self.observer.queue.counters if self.observer.queue else None
                if not self.observer.observe(record):
                    self._record_loss(seq, self._rejection_reason(before))
                    return
                for listener in self._listeners:
                    try:
                        listener(record)
                    except Exception:
                        self.failures += 1
            except Exception:
                # Unexpected observer faults are Research-only, but an assigned
                # sequence must still be marked missing before releasing _lock.
                self.failures += 1
                self._record_loss(seq, "OBSERVER_REJECTION")
