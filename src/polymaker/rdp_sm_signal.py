"""Causal L1 OBI and session-scoped, one-shot signal episodes."""

from dataclasses import dataclass
from decimal import Decimal
from threading import RLock


def number(value):
    if isinstance(value, bool):
        raise ValueError("BOOLEAN_NUMBER")
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("NONFINITE_NUMBER")
    return result


@dataclass(frozen=True)
class Signal:
    label: str
    snapshot_id: str
    quantities: tuple = ()
    iy: Decimal | None = None
    inn: Decimal | None = None
    imbalance: Decimal | None = None
    event_mono: float | None = None

    @property
    def direction(self):
        return self.label.removeprefix("VALID_") if self.label.startswith("VALID_") else None


def evaluate(snapshot, threshold=Decimal(".30")):
    try:
        if not snapshot.valid:
            raise ValueError("INVALID_SNAPSHOT")
        quantities = tuple(
            number(v)
            for b in (snapshot.yes, snapshot.no)
            for v in (b.best_bid_size, b.best_ask_size)
        )
        if any(v <= 0 for v in quantities):
            raise ValueError("EMPTY_L1")
        by, ay, bn, an = quantities
        iy, inn = (by - ay) / (by + ay), (bn - an) / (bn + an)
        imbalance = (iy - inn) / 2
        label = (
            "VALID_YES"
            if imbalance >= threshold
            else "VALID_NO"
            if imbalance <= -threshold
            else "NONE"
        )
        return Signal(
            label, snapshot.snapshot_id, quantities, iy, inn, imbalance, snapshot.signal_event
        )
    except (ValueError, TypeError, ArithmeticError, AttributeError):
        return Signal("INVALID", snapshot.snapshot_id)


class Episodes:
    """INVALID never rearms. Claims and HTTP consumption share a short lock."""

    def __init__(self):
        self.lock = RLock()
        self.episode_id = 0
        self.direction = None
        self.claimed = None
        self.consumed = False
        self.last_event = None
        self.last_attempt_event = None
        self.episode_start = None

    def observe(self, signal):
        with self.lock:
            if signal.label == "INVALID":
                return
            if signal.direction != self.direction:
                self.episode_id += 1
                self.direction = signal.direction
                self.consumed = False
                self.episode_start = signal.event_mono
            self.last_event = signal.snapshot_id

    def claim(self, signal):
        with self.lock:
            if (
                not signal.direction
                or signal.direction != self.direction
                or self.consumed
                or self.claimed is not None
                or signal.snapshot_id != self.last_event
                or signal.snapshot_id == self.last_attempt_event
            ):
                return None
            self.claimed = (self.episode_id, signal.direction, signal.snapshot_id)
            self.last_attempt_event = signal.snapshot_id
            return self.claimed

    def current(self, claim):
        with self.lock:
            return (
                claim is not None
                and claim == self.claimed
                and claim[:2] == (self.episode_id, self.direction)
                and not self.consumed
            )

    def consume(self, claim):
        with self.lock:
            if not self.current(claim):
                raise ValueError("SIGNAL_EPISODE_CHANGED")
            self.consumed = True

    def release(self, claim):
        with self.lock:
            if claim == self.claimed:
                self.claimed = None
