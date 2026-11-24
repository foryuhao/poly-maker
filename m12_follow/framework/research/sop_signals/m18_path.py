"""Bounded M18 round evidence from explicit, source-preserving Bid events.

Pre-hit quantity provenance, direct-print identity, and coverage are obligations
of the adapter. This accumulator checks the supplied fixed-level path, strict
100ms stability, and a terminal quantity event after all counted SELL records.
It deliberately requires neither M16 speed measurement nor M17 per-print ACKs.
"""

from dataclasses import asdict, dataclass
from decimal import Decimal as D


def number(value):
    if value is None or isinstance(value, bool):
        raise ValueError("M18_FINITE_NUMBER_REQUIRED")
    result = D(str(value))
    if not result.is_finite():
        raise ValueError("M18_FINITE_NUMBER_REQUIRED")
    return result


@dataclass(frozen=True)
class Observation:
    kind: str
    token: str
    price: D
    generation: int
    received: float
    available: float
    frame: int
    row: int
    change: int
    source: float
    quantity: D

    @property
    def key(self):
        return self.frame, self.row, self.change

    def record(self):
        return asdict(self)


class RoundPath:
    """Sufficient statistics only; raw events remain in the external recorder."""

    depth_kinds = frozenset({"BID_PRICE_CHANGE", "BID_BOOK"})

    def __init__(self, *, token, price, generation, start, q0, round_seconds=.5):
        self.token, self.price = token, number(price)
        self.generation, self.start, self.q0 = generation, number(start), number(q0)
        if not (0 < self.price < 1 and self.q0 > 0 and type(generation) is int and generation > 0):
            raise ValueError("M18_FIXED_PRE_HIT_LEVEL_REQUIRED")
        window = number(round_seconds)
        if window not in {D(".250"), D(".500")}:
            raise ValueError("M18_EXPLICIT_250_OR_ORIGINAL_500MS_REQUIRED")
        self.end = self.start + window
        self.qmin = self.qend = self.q0
        self.hits = D(0)
        self.stable_since = None
        self.last = self.last_trade = self.last_depth = None
        self.maximum_trade_source = None
        self.failure = None
        self.missing = self.post_missing = False
        self.post_min = None
        self.observations = self.depth_observations = self.sell_observations = 0
        self.closed = False

    def fail(self, reason):
        if self.failure is None:
            self.failure = reason

    def observe(self, point):
        if self.closed:
            raise ValueError("M18_ROUND_ALREADY_CLOSED")
        if (point.token, point.price) != (self.token, self.price):
            return False
        if point.kind not in self.depth_kinds and point.kind != "DIRECT_SELL":
            return False
        if type(point.generation) is not int or point.generation != self.generation:
            self.fail("GENERATION_CHANGED")
            return False
        try:
            received, available, source, quantity = map(
                number, (point.received, point.available, point.source, point.quantity)
            )
            if (type(point.frame) is not int or point.frame < 1
                    or type(point.row) is not int or point.row < 0
                    or type(point.change) is not int or point.change < 0
                    or available < received or received < self.start or quantity < 0
                    or (point.kind == "DIRECT_SELL" and quantity == 0)):
                raise ValueError("INVALID_OBSERVATION")
            if self.last is not None:
                if point.key <= self.last.key:
                    raise ValueError("DUPLICATE_OR_UNORDERABLE_INPUT_KEY")
                if received < number(self.last.received):
                    raise ValueError("RECEIVED_CLOCK_REGRESSION")
                if available < number(self.last.available):
                    raise ValueError("AVAILABLE_CLOCK_REGRESSION")
                if source < number(self.last.source):
                    raise ValueError("SOURCE_CLOCK_REGRESSION")
                if point.frame == self.last.frame and (
                        received != number(self.last.received)
                        or available != number(self.last.available)):
                    raise ValueError("PARENT_FRAME_CLOCK_DISAGREEMENT")
            elif point.kind != "DIRECT_SELL" or received != self.start:
                raise ValueError("ROUND_START_NOT_DIRECT_SELL")
        except (TypeError, ValueError, ArithmeticError) as error:
            self.fail("INVALID_ROUND_PATH:" + str(error))
            return False
        self.last = point
        self.observations += 1
        if received >= self.end:
            if point.kind in self.depth_kinds:
                self.post_min = quantity if self.post_min is None else min(self.post_min, quantity)
                self.post_missing |= quantity <= 0
            return False
        if point.kind == "DIRECT_SELL":
            self.hits += quantity
            self.sell_observations += 1
            self.last_trade = point
            self.maximum_trade_source = source if self.maximum_trade_source is None else max(
                self.maximum_trade_source, source
            )
        else:
            self.depth_observations += 1
            self.last_depth = point
            self.qend = quantity
            self.missing |= quantity <= 0
            if quantity < self.qmin:
                self.qmin, self.stable_since = quantity, None
            loss = self.q0 - self.qmin
            if loss <= 0 or quantity - self.qmin < D(".50") * loss:
                self.stable_since = None
            elif self.stable_since is None:
                self.stable_since = received
        return True

    def finish(self, *, available, watermark, coverage_proven):
        if self.closed:
            raise ValueError("M18_ROUND_ALREADY_CLOSED")
        self.closed = True
        loss = self.q0 - self.qmin
        threshold = max(D(".5"), D(".0001") * self.q0)
        recovery = max(D(0), min(D(1), (self.qend - self.qmin) / loss)) if loss else D(0)
        qualified = not self.missing and self.hits >= threshold and loss >= threshold
        stable = bool(qualified and self.stable_since is not None
                      and self.end - self.stable_since >= D(".100")
                      and recovery >= D(".50"))
        terminal_proven = bool(
            self.last_trade is not None and self.last_depth is not None
            and self.last_depth.key > self.last_trade.key
            and number(self.last_depth.source) > self.maximum_trade_source
            and self.start <= number(self.last_depth.received) < self.end
        )
        result = {
            "status": "UNKNOWN", "reason": None, "data_proven": False,
            "token": self.token, "price": self.price, "generation": self.generation,
            "start": self.start, "end": self.end, "q0": self.q0,
            "qmin": self.qmin, "qend": self.qend, "hits": self.hits,
            "loss": loss, "recovery": recovery, "qualified": qualified, "stable": stable,
            "stable_since": self.stable_since, "missing": self.missing,
            "terminal_quantity_proven": terminal_proven,
            "maximum_trade_source": self.maximum_trade_source,
            "last_trade": self.last_trade.record() if self.last_trade else None,
            "terminal_depth": self.last_depth.record() if self.last_depth else None,
            "post_window_min": self.post_min, "post_window_missing": self.post_missing,
            "observations": self.observations, "depth_observations": self.depth_observations,
            "sell_observations": self.sell_observations,
        }

        def unknown(reason):
            result["reason"] = reason
            return result

        if self.failure:
            return unknown(self.failure)
        now = number(available)
        if (coverage_proven is not True or number(watermark) <= self.end
                or now < number(watermark) or now - self.end > D(".050")
                or (self.last is not None and now < number(self.last.available))):
            return unknown("LATE_CLOSE_OR_COVERAGE")
        if self.missing:
            return unknown("ORIGINAL_LEVEL_MISSING")
        if not terminal_proven:
            return unknown("TERMINAL_ANCHOR_QUANTITY_UNAVAILABLE")
        result["data_proven"] = True
        if not qualified:
            result.update(status="NOT_QUALIFIED", reason="MATERIAL_HIT_AND_LOSS_NOT_BOTH_PROVEN")
        elif not stable:
            result.update(status="NOT_STABLE", reason="FINAL_CONTINUOUS_100MS_HALF_RECOVERY_NOT_PROVEN")
        else:
            result.update(status="ROUND_PROVEN", reason=None)
        return result

    def release(self):
        self.last = self.last_trade = self.last_depth = None
