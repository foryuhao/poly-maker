"""Small, failure-isolated adapter from applied live facts to ResearchEvent."""

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from threading import Lock
from typing import Any

from polymaker.research.observer import ResearchObserver
from polymaker.research.schema import ResearchEvent


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
        self._lock = Lock()
        self._seq = 0
        self._last_us = 0
        self.failures = 0

    @property
    def enabled(self) -> bool:
        return self.observer.config.enabled

    def emit(self, kind: str, condition_id: str, payload: Mapping[str, Any], *,
             source: str = "ENGINE", exchange_at_us: int | None = None,
             received_at_us: int | None = None,
             received_monotonic_ns: int | None = None,
             connection_generation: int | None = None) -> None:
        if not self.enabled:
            return
        try:
            # Serialization and queue admission share this short ordering boundary.
            # The lock never covers disk or network I/O.
            with self._lock:
                available, mono = clock()
                available = max(available, self._last_us)
                self._last_us = available
                self._seq += 1
                record = ResearchEvent.model_validate({
                    "schema_version": 1, "session_id": self.session_id, "event_seq": self._seq,
                    "condition_id": condition_id, "episode_id": None, "kind": kind,
                    "exchange_at_us": exchange_at_us, "received_at_us": received_at_us,
                    "available_at_us": available, "monotonic_ns": mono, "source": source,
                    "connection_generation": connection_generation, "source_ref": None,
                    "payload": ({**payload, "received_monotonic_ns": received_monotonic_ns}
                                if received_monotonic_ns is not None else payload),
                })
                self.observer.observe(record)
        except Exception:
            self.failures += 1
