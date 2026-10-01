"""Default-off, observation-only boundary; no live service imports or I/O."""

from __future__ import annotations

from dataclasses import dataclass
from queue import Full, Queue
from threading import Lock
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


class BoundedResearchQueue:
    """Single writer queue. Producer path never waits for capacity."""

    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._queue: Queue[ResearchItem] = Queue(maxsize=capacity)
        self._accepted = 0
        self._rejected_full = 0
        self._rejected_closed = 0
        self._closed = False
        self._admission_lock = Lock()

    def put_nowait(self, item: ResearchItem) -> bool:
        # Serialize the admission decision with close, never with writer I/O.
        # Queue.put_nowait cannot wait for space, even while this short guard is held.
        with self._admission_lock:
            if self._closed:
                self._rejected_closed += 1
                return False
            try:
                self._queue.put_nowait(item)
            except Full:
                self._rejected_full += 1
                return False
            self._accepted += 1
            return True

    def get(self, timeout: float | None = None) -> ResearchItem:
        return self._queue.get(timeout=timeout)

    def task_done(self) -> None:
        self._queue.task_done()

    def empty(self) -> bool:
        return self._queue.empty()

    def close_producer(self) -> None:
        with self._admission_lock:
            self._closed = True

    @property
    def counters(self) -> QueueCounters:
        with self._admission_lock:
            return QueueCounters(self._accepted, self._rejected_full, self._rejected_closed)


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
        try:
            if not isinstance(record, (ResearchEvent, Episode, StateSnapshot)):
                raise TypeError("observation must be a validated Research record")
            assert self.queue is not None
            return self.queue.put_nowait(record)
        except Exception:
            self.failures += 1
            return False
