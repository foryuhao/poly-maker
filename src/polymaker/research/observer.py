"""Default-off, observation-only boundary; no live service imports or I/O."""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from queue import Full, Queue
from threading import RLock
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from polymaker.research.schema import Episode, ResearchEvent, StateSnapshot

ResearchItem = ResearchEvent | Episode | StateSnapshot


class ResearchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    output_root: str = "research_data"
    snapshot_interval_s: float = Field(default=1.0, gt=0, allow_inf_nan=False)
    prefill_ring_duration_s: float = Field(default=20.0, gt=0, allow_inf_nan=False)
    queue_capacity: int = Field(default=4096, gt=0)
    flush_interval_s: float = Field(default=1.0, gt=0, allow_inf_nan=False)
    fsync_interval_s: float = Field(default=5.0, gt=0, allow_inf_nan=False)


@dataclass(frozen=True, slots=True)
class QueueCounters:
    accepted: int
    rejected_full: int
    rejected_closed: int
    high_water: int
    lost_event_count: int
    lost_range_count: int
    lost_ranges_truncated: int


class BoundedResearchQueue:
    """Single writer queue. Producer path never waits for capacity."""

    MAX_LOST_RANGES = 128

    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._queue: Queue[ResearchItem] = Queue(maxsize=capacity)
        self._accepted = 0
        self._rejected_full = 0
        self._rejected_closed = 0
        self._closed = False
        self._session_sealed = False
        self._high_water = 0
        self._lost_event_ranges: list[tuple[int, int]] = []
        self._lost_event_count = 0
        self._lost_range_count = 0
        self._lost_ranges_truncated = 0
        self._lost_reason_counts: dict[str, int] = {}
        # Capture sequencing and final session sealing share this lock. It is
        # reentrant because capture admits records while holding it.
        self._admission_lock = RLock()

    @property
    def session_lock(self) -> AbstractContextManager[bool]:
        return self._admission_lock

    @property
    def session_sealed(self) -> bool:
        with self._admission_lock:
            return self._session_sealed

    def put_nowait(self, item: ResearchItem) -> bool:
        # Serialize the admission decision with close, never with writer I/O.
        # Queue.put_nowait cannot wait for space, even while this short guard is held.
        with self._admission_lock:
            if self._session_sealed:
                return False
            if self._closed:
                self._rejected_closed += 1
                self._note_rejected_event(item, "ADMISSION_CLOSED")
                return False
            try:
                self._queue.put_nowait(item)
            except Full:
                self._rejected_full += 1
                self._note_rejected_event(item, "QUEUE_CAPACITY")
                return False
            self._accepted += 1
            self._high_water = max(self._high_water, self._queue.qsize())
            return True

    def _note_rejected_event(self, item: ResearchItem, reason: str) -> None:
        if isinstance(item, ResearchEvent) and item.kind != "GAP":
            self._record_lost_seq(item.event_seq, reason)

    def _record_lost_seq(self, seq: int, reason: str) -> None:
        if self._lost_event_ranges and self._lost_event_ranges[-1][1] >= seq:
            return
        self._lost_event_count += 1
        self._lost_reason_counts[reason] = self._lost_reason_counts.get(reason, 0) + 1
        if self._lost_event_ranges and self._lost_event_ranges[-1][1] + 1 == seq:
            first, _ = self._lost_event_ranges[-1]
            self._lost_event_ranges[-1] = (first, seq)
        else:
            self._lost_range_count += 1
            self._lost_event_ranges.append((seq, seq))
            if len(self._lost_event_ranges) > self.MAX_LOST_RANGES:
                self._lost_event_ranges.pop(0)
                self._lost_ranges_truncated += 1

    def note_lost_event(self, seq: int, reason: str = "OBSERVER_REJECTION") -> None:
        with self._admission_lock:
            if not self._session_sealed:
                self._record_lost_seq(seq, reason)

    @property
    def lost_reason_counts(self) -> dict[str, int]:
        with self._admission_lock:
            return dict(self._lost_reason_counts)

    @property
    def lost_event_ranges(self) -> tuple[tuple[int, int], ...]:
        with self._admission_lock:
            return tuple(self._lost_event_ranges)

    def get(self, timeout: float | None = None) -> ResearchItem:
        return self._queue.get(timeout=timeout)

    def task_done(self) -> None:
        self._queue.task_done()

    def empty(self) -> bool:
        return self._queue.empty()

    def close_producer(self) -> None:
        with self._admission_lock:
            self._closed = True

    def seal_session(self) -> None:
        """Freeze session accounting after in-flight admission decisions finish."""
        with self._admission_lock:
            self._closed = True
            self._session_sealed = True

    @property
    def counters(self) -> QueueCounters:
        with self._admission_lock:
            return QueueCounters(self._accepted, self._rejected_full,
                                 self._rejected_closed, self._high_water,
                                 self._lost_event_count, self._lost_range_count,
                                 self._lost_ranges_truncated)


class ResearchObserver:
    """Consumes already-copied research records; assigns no Fill Truth authority."""

    def __init__(self, config: ResearchConfig | None = None,
                 queue: BoundedResearchQueue | None = None) -> None:
        self.config = config or ResearchConfig()
        self.queue = queue if self.config.enabled else None
        if self.config.enabled and self.queue is None:
            self.queue = BoundedResearchQueue(self.config.queue_capacity)
        self.failures = 0

    def observe(self, record: ResearchItem | dict[str, Any]) -> bool:
        if not self.config.enabled:
            return False
        assert self.queue is not None
        with self.queue.session_lock:
            if self.queue.session_sealed:
                return False
            try:
                if not isinstance(record, (ResearchEvent, Episode, StateSnapshot)):
                    raise TypeError("observation must be a validated Research record")
                return self.queue.put_nowait(record)
            except Exception:
                self.failures += 1
                return False
