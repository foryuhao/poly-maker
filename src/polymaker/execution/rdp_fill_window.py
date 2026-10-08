"""Receipt-time decision for the retained initial RDP NORMAL pair.

This module observes already accepted processor evidence. It has no gateway,
account, cancellation, or execution authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any


@dataclass(frozen=True, slots=True)
class RDPFillWindowDecision:
    attempt_id: int
    slug: str
    condition_id: str
    acknowledged_order_ids: tuple[str, ...]
    reliable: bool
    reason: str | None
    started: bool
    first_receipt_monotonic_ns: int | None
    duration_s: float
    deadline_monotonic_ns: int | None
    closure_due: bool
    inventory: Any | None
    execution_authorized: bool = field(default=False, init=False)


class RDPFillWindow:
    """Keep one immutable first-receipt deadline for a placement attempt."""

    def __init__(self, duration_s: float) -> None:
        self.duration_s = duration_s
        self.duration_ns = _duration_ns(duration_s)
        self.attempt_id: int | None = None
        self.first_receipt_monotonic_ns: int | None = None
        self._timing_contradiction = False
        self._logged_start = False
        self._logged_expiry = False

    def bind(self, attempt_id: int) -> None:
        if self.attempt_id is None:
            self.attempt_id = attempt_id
        elif self.attempt_id != attempt_id:
            raise ValueError("FillWindow is already bound to another attempt")

    def start(self, attempt_id: int, receipt_times_ns: tuple[int, ...]) -> int:
        self.bind(attempt_id)
        if receipt_times_ns:
            candidate = min(receipt_times_ns)
            if self.first_receipt_monotonic_ns is None:
                self.first_receipt_monotonic_ns = candidate
            elif candidate < self.first_receipt_monotonic_ns:
                # Preserve the established deadline but mark the evidence
                # contradictory; query history cannot silently certify a
                # later origin or fabricate a fresh FillWindow.
                self._timing_contradiction = True
        assert self.first_receipt_monotonic_ns is not None
        return self.first_receipt_monotonic_ns

    @property
    def timing_contradiction(self) -> bool:
        return self._timing_contradiction

    @property
    def deadline_monotonic_ns(self) -> int | None:
        if self.first_receipt_monotonic_ns is None:
            return None
        return self.first_receipt_monotonic_ns + self.duration_ns


def _duration_ns(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("FillWindow duration must be finite and positive")
    try:
        duration = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("FillWindow duration must be finite and positive") from None
    if not duration.is_finite() or duration <= 0:
        raise ValueError("FillWindow duration must be finite and positive")
    ns = duration * Decimal(1_000_000_000)
    if ns != ns.to_integral_value() or ns < 1:
        raise ValueError("FillWindow duration must resolve to whole nanoseconds")
    return int(ns)
