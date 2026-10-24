"""Recent execution circuit; rolling p95 is telemetry, never a write permit."""

import math
import time
import uuid
from collections import deque
from dataclasses import dataclass, replace
from threading import Lock


@dataclass(frozen=True)
class Health:
    allowed: bool
    reason: str
    p95_ms: float
    samples: tuple
    write_proxy: bool
    state: str
    pause_until: float
    active_batch: str | None
    close_active: bool
    write_evidence: dict


@dataclass(frozen=True)
class RequestSample:
    method: str
    operation_id: str
    completed: float
    duration_ms: float
    outcome: str


@dataclass(frozen=True)
class WriteLatency:
    status: str
    operations: int
    max_ms: float | None
    latest_completed: float | None


@dataclass(frozen=True)
class RecentWrites:
    observed_monotonic: float
    window_sec: float
    post: WriteLatency
    delete: WriteLatency


class NetworkHealth:
    def __init__(self, cfg, monotonic=time.monotonic):
        self.cfg, self.monotonic = cfg, monotonic
        self.lock = Lock()
        self.samples = {k: deque(maxlen=512) for k in ("GET", "POST", "DELETE")}
        self.probes = deque(maxlen=max(512, cfg.network_recovery_samples))
        self.events = deque()
        self.state = "NORMAL"
        self.pause_until = 0.0
        self.pause_duration = 0.0
        self.evidence_after = -math.inf
        self.active_batch = None
        self.batch_writes = {}
        self.write_evidence = {}
        self.close_active = False
        self.failure_revision = 0

    def _event(self, kind, payload, now):
        self.events.append((kind, {**payload, "event_monotonic": now}, time.time()))

    def drain_events(self):
        with self.lock:
            result = list(self.events)
            self.events.clear()
            return result

    def recent_writes(self):
        """Immutable latency evidence only; never grants admission or recovery.

        HTTP replies include business rejections, not just accepted orders.
        Failed/unfinished transports provide no completed response latency.
        One operation (including cancel-batch retries) contributes its maximum.
        """
        with self.lock:
            now, window = self.monotonic(), self.cfg.network_recent_window_sec

            def summarize(method):
                operations, latest = {}, None
                for row in self.samples[method]:
                    if (not 0 <= now-row.completed <= window
                            or row.outcome not in {"SUCCESS", "HTTP_REJECTION",
                                "AUTHENTICATION_ERROR", "SERVER_ERROR", "RATE_LIMITED"}):
                        continue
                    operations[row.operation_id] = max(
                        operations.get(row.operation_id, 0.), row.duration_ms)
                    latest = max(latest, row.completed) if latest is not None else row.completed
                return WriteLatency("OBSERVED" if operations else "UNKNOWN", len(operations),
                                    max(operations.values()) if operations else None, latest)

            return RecentWrites(now, window, summarize("POST"), summarize("DELETE"))

    def _transition(self, state, reason, now, **evidence):
        previous, self.state = self.state, state
        self._event("sm_network_transition", {
            "previous": previous, "state": state, "reason": reason,
            "pause_until": self.pause_until, "pause_duration_sec": self.pause_duration,
            "active_batch": self.active_batch, **evidence,
        }, now)

    def _pause(self, reason, now, *, close_active=False, **evidence):
        self.failure_revision += 1
        self.close_active |= close_active
        # Recovery must use probes completed after the fault, not warmup history.
        self.probes.clear()
        if self.state != "PAUSED":
            self.pause_duration = min(
                self.cfg.network_max_pause_sec,
                self.pause_duration * 2 if self.pause_duration else self.cfg.network_pause_sec,
            )
            self.pause_until = now + self.pause_duration
            self.evidence_after = now
            self._transition("PAUSED", reason, now, **evidence)
        else:
            # Continued failures keep the current cooldown fresh without increasing
            # backoff on each retry. Only a failed recovery increases the backoff.
            self.pause_until = max(self.pause_until, now + self.pause_duration)

    def _slow_operations(self, method, now):
        operations = {}
        for row in reversed(self.samples[method]):
            if not (row.completed > self.evidence_after
                    and 0 <= now - row.completed <= self.cfg.network_recent_window_sec):
                continue
            if row.operation_id not in operations:
                if len(operations) >= self.cfg.network_slow_sample_count:
                    continue
                operations[row.operation_id] = False
            operations[row.operation_id] |= (
                row.outcome == "SUCCESS" and row.duration_ms > self.cfg.max_rtt_p95_ms
            )
        return [operation for operation, slow in operations.items() if slow]

    def observe(self, method, duration, success=True, *, operation_id=None, endpoint="/time",
                started_at=None, started_wall=None, outcome=None, batch_id=None,
                http_status=None, error_type=None):
        now = self.monotonic()
        duration_ms = duration * 1000
        if not math.isfinite(duration_ms) or duration_ms < 0:
            success, outcome, duration, duration_ms = False, "TRANSPORT_ERROR", 0.0, 0.0
        outcome = outcome or ("SUCCESS" if success else "TRANSPORT_ERROR")
        operation_id = operation_id or uuid.uuid4().hex
        request_scope_id = operation_id
        if method == "DELETE" and batch_id is not None:
            # The timer cancels current IDs while reconciliation may cancel
            # current + historical IDs. Both belong to the same close batch.
            operation_id = "cancel-batch:" + batch_id
        with self.lock:
            row = RequestSample(method, operation_id, now, duration_ms, outcome)
            self.samples[method].append(row)
            fast = outcome == "SUCCESS" and duration_ms <= self.cfg.max_rtt_p95_ms
            if method == "GET" and endpoint == "/time":
                self.probes.append((now, fast))
            self._event("sm_network_request", {
                "request_id": uuid.uuid4().hex, "operation_id": operation_id,
                "request_scope_id": request_scope_id,
                "batch_id": batch_id, "method": method, "endpoint": endpoint,
                "started_monotonic": now - duration if started_at is None else started_at,
                "completed_monotonic": now,
                "started_wall": time.time() - duration if started_wall is None else started_wall,
                "completed_wall": time.time(), "duration_ms": duration_ms, "outcome": outcome,
                "http_status": http_status, "error_type": error_type,
            }, now)
            if method in {"POST", "DELETE"}:
                status = "FAST" if fast else "SLOW" if outcome == "SUCCESS" else outcome
                self.write_evidence[method] = {"status": status, "observed_monotonic": now}
                if batch_id is not None and batch_id == self.active_batch:
                    # A retry cannot erase an earlier slow/failed write in the probe batch.
                    previous = self.batch_writes.get(method, "FAST")
                    self.batch_writes[method] = status if previous == "FAST" else previous
            if outcome in {"TRANSPORT_ERROR", "SERVER_ERROR", "RATE_LIMITED"}:
                self._pause("NETWORK_" + outcome, now,
                            close_active=outcome in {"TRANSPORT_ERROR", "SERVER_ERROR"},
                            method=method, operation_id=operation_id)
            else:
                slow = self._slow_operations(method, now)
                if len(slow) >= self.cfg.network_slow_trigger_count:
                    self._pause("NETWORK_REPEATED_SLOW", now,
                                method=method, operation_ids=slow)
                elif outcome == "SUCCESS" and not fast:
                    self._event("sm_network_slow_request", {
                        "method": method, "operation_id": operation_id,
                        "duration_ms": duration_ms, "threshold_ms": self.cfg.max_rtt_p95_ms,
                    }, now)

    def _probe_ready(self, now):
        rows = list(self.probes)[-self.cfg.network_recovery_samples:]
        return (len(rows) == self.cfg.network_recovery_samples
                and all(fast and 0 <= now - completed <= self.cfg.network_recent_window_sec
                        for completed, fast in rows)
                and now - rows[-1][0] <= self.cfg.network_sample_max_age_sec)

    def _snapshot(self, now, signal_age_ms, owner=None):
        groups, p95 = [], 0.0
        for method, rows in self.samples.items():
            recent = [r for r in rows if 0 <= now - r.completed <= self.cfg.network_window_sec]
            if recent:
                values = sorted(r.duration_ms for r in recent)
                value = values[math.ceil(.95 * len(values)) - 1]
                p95 = max(p95, value)
                groups.append((method, len(recent), value, now - recent[-1].completed))
        probe_ready = self._probe_ready(now)
        if (self.state == "PAUSED" and self.active_batch is None
                and now >= self.pause_until and probe_ready):
            self.close_active = False
            self._transition("PROBE", "NETWORK_PROBE_READY", now)
        reason = "HEALTHY" if self.state == "NORMAL" else "NETWORK_PROBE_READY"
        if self.active_batch is not None and self.active_batch != owner:
            reason = "NETWORK_BATCH_IN_FLIGHT"
        elif self.state == "PAUSED":
            reason = "NETWORK_PAUSED" if now < self.pause_until else "NETWORK_RECOVERING"
        elif len([r for r in self.samples["GET"]
                  if 0 <= now - r.completed <= self.cfg.network_window_sec]) < self.cfg.network_min_samples:
            reason = "NETWORK_SAMPLE_INSUFFICIENT"
        elif not probe_ready:
            reason = "NETWORK_RECOVERING"
        if not math.isfinite(signal_age_ms) or not 0 <= signal_age_ms <= self.cfg.max_signal_age_ms:
            reason = "SIGNAL_STALE"
        writes = {
            method: (dict(self.write_evidence[method])
                     if method in self.write_evidence
                     and 0 <= now - self.write_evidence[method]["observed_monotonic"] <= self.cfg.network_window_sec
                     else {"status": "UNKNOWN", "observed_monotonic": None})
            for method in ("POST", "DELETE")
        }
        return Health(reason in {"HEALTHY", "NETWORK_PROBE_READY"}, reason, p95, tuple(groups),
                      all(v["status"] == "UNKNOWN" for v in writes.values()), self.state,
                      self.pause_until, self.active_batch, self.close_active, writes)

    def snapshot(self, signal_age_ms=0):
        with self.lock:
            return self._snapshot(self.monotonic(), signal_age_ms)

    def continuation(self, batch_id):
        """SL requests may share their own batch, never bypass its circuit."""
        with self.lock:
            return self._snapshot(self.monotonic(), 0, owner=batch_id)

    def repair_probe(self, batch_id):
        """Transport readiness only; the repair evidence gate must also permit.

        No state/batch/close latch is cleared. NORMAL and other owners still
        see the original circuit, including the unresolved batch.
        """
        with self.lock:
            now = self.monotonic()
            health = self._snapshot(now, 0, owner=batch_id)
            samples = sum(0 <= now-r.completed <= self.cfg.network_window_sec
                          for r in self.samples["GET"])
            if (batch_id == self.active_batch and now >= self.pause_until
                    and self._probe_ready(now) and samples >= self.cfg.network_min_samples):
                return replace(health, allowed=True, close_active=False,
                               reason="RESTRICTED_REPAIR_PROBE_READY")
            return health

    def begin_batch(self, batch_id):
        """Reserve exactly one real batch, atomically with the final circuit check."""
        with self.lock:
            now = self.monotonic()
            if not self._snapshot(now, 0).allowed:
                return False
            self.active_batch, self.batch_writes, self.close_active = batch_id, {}, False
            self._event("sm_network_batch", {"action": "BEGIN", "batch_id": batch_id,
                                             "state": self.state}, now)
            return True

    def complete_batch(self, batch_id, *, safe):
        with self.lock:
            if batch_id is None or batch_id != self.active_batch:
                return
            now = self.monotonic()
            self._event("sm_network_batch", {"action": "COMPLETE" if safe else "UNRESOLVED",
                                             "batch_id": batch_id, "writes": dict(self.batch_writes)}, now)
            if not safe:
                return
            self.active_batch, self.close_active = None, False
            if (self.state == "PROBE" and self.batch_writes.get("POST") == "FAST"
                    and all(status == "FAST" for status in self.batch_writes.values())):
                self.pause_duration = 0.0
                self._transition("NORMAL", "NETWORK_WRITE_RECOVERED", now,
                                 writes=dict(self.batch_writes))
