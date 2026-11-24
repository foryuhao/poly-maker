"""M16 received-clock recovery intervals from explicit fixed-Bid observations.

This accumulator does not establish input coverage, pre-hit Q0 or clock
resolution. The input adapter must prove those separately. Original frames stay
in the recorder; only sufficient statistics and the selected raw event references
are retained here. Availability/source clocks never enter latency subtraction.
"""

from dataclasses import asdict, dataclass
from decimal import Decimal as D


DEPTH_KINDS = frozenset({"BID_PRICE_CHANGE", "BID_BOOK"})
ROUND_SECONDS = D(".500")
STABLE_SECONDS = D(".100")
RECOVERY_MIN = D(".50")
BRACKET_MAX = D(".050")
CLOSE_LATENESS_MAX = D(".050")
QUANTUM_MAX = D(".001")


def number(value):
    if isinstance(value, bool) or value is None:
        raise ValueError("M16_FINITE_NUMBER_REQUIRED")
    result = D(str(value))
    if not result.is_finite():
        raise ValueError("M16_FINITE_NUMBER_REQUIRED")
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


class RecoveryPath:
    """A strict 500ms SELL round with O(1) retained timing state.

    ``receiver_quantum`` is an externally verified local-clock error bound in
    seconds, including float representation/recording. None is explicitly
    unavailable, never a zero padding assumption. Matching quantity updates must
    be raw P observations, including same-quantity updates, not cache callbacks.
    """

    def __init__(self, *, token, price, generation, start, q0, receiver_quantum=None):
        self.token, self.price, self.generation = token, number(price), generation
        self.start, self.q0 = number(start), number(q0)
        if (self.q0 <= 0 or not 0 < self.price < 1 or type(generation) is not int
                or generation < 1):
            raise ValueError("M16_PRE_HIT_LEVEL_REQUIRED")
        self.end = self.start + ROUND_SECONDS
        self.quantum = None if receiver_quantum is None else number(receiver_quantum)
        self.qmin = self.qend = self.q0
        self.hits = D(0)
        self.tq = self.tm = self.base = self.minus = self.plus = self.last = None
        self.base_quantity = None
        self.failure = None
        self.missing = False
        self.post_low, self.post_missing = None, False
        self.observations = self.depth_observations = self.sell_observations = 0
        self.closed = False

    def fail(self, reason):
        if self.failure is None:
            self.failure = reason

    def observe(self, point):
        if self.closed:
            raise ValueError("M16_TIMING_PATH_ALREADY_CLOSED")
        # Exclude foreign levels/tokens and non-quantity messages entirely.
        if (point.token, point.price) != (self.token, self.price):
            return False
        if point.kind not in DEPTH_KINDS and point.kind != "DIRECT_SELL":
            return False
        if type(point.generation) is not int or point.generation != self.generation:
            self.fail("GENERATION_CHANGED")
            return False
        try:
            received, available, quantity = map(number, (point.received, point.available, point.quantity))
            number(point.source)  # Retained provenance, never elapsed-time input.
            if (type(point.frame) is not int or point.frame < 1
                    or type(point.row) is not int or point.row < 0
                    or type(point.change) is not int or point.change < 0
                    or available < received or received < self.start
                    or quantity < 0 or (point.kind == "DIRECT_SELL" and quantity == 0)):
                raise ValueError("INVALID_OBSERVATION")
            if self.last is not None:
                if point.key <= self.last.key:
                    raise ValueError("DUPLICATE_OR_UNORDERABLE_INPUT_KEY")
                if received < number(self.last.received):
                    raise ValueError("RECEIVED_CLOCK_REGRESSION")
                if available < number(self.last.available):
                    raise ValueError("AVAILABLE_CLOCK_REGRESSION")
                if point.frame == self.last.frame and (
                        received != number(self.last.received)
                        or available != number(self.last.available)):
                    raise ValueError("PARENT_FRAME_CLOCK_DISAGREEMENT")
            elif point.kind != "DIRECT_SELL" or received != self.start:
                raise ValueError("ROUND_START_NOT_DIRECT_SELL")
        except (TypeError, ValueError, ArithmeticError) as error:
            self.fail("INVALID_TIMING_PATH:" + str(error))
            return False
        self.last = point
        self.observations += 1
        if received >= self.end:
            if point.kind in DEPTH_KINDS:
                self.post_low = quantity if self.post_low is None else min(self.post_low, quantity)
                self.post_missing |= quantity <= 0
            return False
        if point.kind == "DIRECT_SELL":
            self.hits += quantity
            self.sell_observations += 1
        else:
            self.depth_observations += 1
            self.qend = quantity
            self.missing |= quantity <= 0
            if quantity < self.qmin:
                self.qmin, self.tm = quantity, point
                # A new final trough resets the recovery segment; it does not
                # reset first material qualification or invent a new SELL round.
                self.plus = None
            loss = self.q0 - self.qmin
            if loss <= 0 or quantity - self.qmin < RECOVERY_MIN * loss:
                self.minus, self.plus = point, None
            elif self.plus is None:
                self.plus = point
        threshold = max(D(".5"), D(".0001") * self.q0)
        if self.tq is None and self.hits >= threshold and self.q0 - self.qmin >= threshold:
            self.tq = point
        if self.tq is not None and self.tm is not None:
            # Keys distinguish explicit order at an equal received timestamp.
            # They never interpolate time between updates in one parent frame.
            base = max((self.tq, self.tm), key=lambda x: (number(x.received), x.key))
            if self.base is not base:
                self.base, self.base_quantity = base, self.qend
        return True

    def finish(self, *, available, watermark, coverage_proven):
        if self.closed:
            raise ValueError("M16_TIMING_PATH_ALREADY_CLOSED")
        self.closed = True
        loss = self.q0 - self.qmin
        threshold = max(D(".5"), D(".0001") * self.q0)
        recovery = max(D(0), min(D(1), (self.qend - self.qmin) / loss)) if loss else D(0)
        qualified = not self.missing and loss >= threshold and self.hits >= threshold
        stable = bool(qualified and self.plus is not None
                      and self.end - number(self.plus.received) >= STABLE_SECONDS
                      and recovery >= RECOVERY_MIN)
        result = {
            "status": "TIMING_UNAVAILABLE", "reason": None,
            "token": self.token, "price": self.price, "generation": self.generation,
            "start": self.start, "end": self.end,
            "q0": self.q0, "qmin": self.qmin, "qend": self.qend,
            "hits": self.hits, "loss": loss, "recovery": recovery,
            "qualified": qualified, "stable": stable, "missing": self.missing,
            "receiver_clock_quantum": self.quantum,
            "clock_padding_quanta": 2, "tau_low": None, "tau_high": None,
            "post_window_min": self.post_low, "post_window_missing": self.post_missing,
            "observations": self.observations, "depth_observations": self.depth_observations,
            "sell_observations": self.sell_observations,
            **{name: p.record() if p is not None else None for name, p in (
                ("t_qualified", self.tq), ("t_final_trough", self.tm),
                ("t_base", self.base), ("t_minus", self.minus), ("t_plus", self.plus))},
        }

        def unavailable(reason):
            result["reason"] = reason
            return result

        if self.failure:
            return unavailable(self.failure)
        if (coverage_proven is not True or number(watermark) <= self.end
                or number(available) < number(watermark)
                or (self.last is not None and number(available) < number(self.last.available))
                or number(available) - self.end > CLOSE_LATENESS_MAX):
            return unavailable("LATE_CLOSE_OR_COVERAGE")
        if self.quantum is None or not 0 < self.quantum <= QUANTUM_MAX:
            return unavailable("RECEIVER_CLOCK_QUANTUM_UNPROVEN")
        if self.missing:
            return unavailable("ORIGINAL_LEVEL_MISSING")
        if not qualified:
            result.update(status="NOT_QUALIFIED", reason="MATERIAL_HIT_AND_LOSS_NOT_BOTH_PROVEN")
            return result
        if not stable:
            result.update(status="NOT_STABLE", reason="FINAL_CONTINUOUS_100MS_HALF_RECOVERY_NOT_PROVEN")
            return result
        if self.base is None:
            return unavailable("RESPONSE_BASE_UNAVAILABLE")
        if self.base_quantity - self.qmin >= RECOVERY_MIN * loss:
            return unavailable("RESPONSE_ALREADY_RECOVERED_AT_BASE")
        if (self.minus is None or self.minus.key < self.base.key
                or number(self.minus.received) < number(self.base.received)):
            return unavailable("EXPLICIT_BELOW_RECOVERY_EVENT_AFTER_BASE_MISSING")
        if (self.base.frame == self.plus.frame
                or number(self.plus.received) <= number(self.base.received)):
            return unavailable("BASE_AND_CROSSING_NOT_DISTINGUISHABLE")
        bracket = number(self.plus.received) - number(self.minus.received)
        if self.plus.frame == self.minus.frame or bracket <= 0 or bracket > BRACKET_MAX:
            return unavailable("EXPLICIT_CROSSING_BRACKET_UNPROVEN_OR_OVER_50MS")
        padding = 2 * self.quantum
        result.update(status="TIMING_PROVEN", reason=None,
                      tau_low=max(D(0), number(self.minus.received) - number(self.base.received) - padding),
                      tau_high=number(self.plus.received) - number(self.base.received) + padding)
        return result

    def release(self):
        self.tq = self.tm = self.base = self.minus = self.plus = self.last = None
