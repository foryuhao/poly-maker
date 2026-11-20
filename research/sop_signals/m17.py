"""M17: direct selling with retained original-Bid depth, not an RF round.

Received time fixes the window and per-print recheck budgets. Availability
orders decisions; a later input cannot repair a root already ended at a budget.
Only the parent adapter may publish, after processing every invalidation.
"""

from dataclasses import dataclass, field
from decimal import Decimal as D

from .m12 import M12, parameter_identity

VERSION = "m17-fixed-anchor-entry-v1/raw-retained-depth-r1"


def number(value):
    if value is None or isinstance(value, bool):
        raise ValueError("M17_FINITE_NUMBER_REQUIRED")
    value = D(str(value))
    if not value.is_finite():
        raise ValueError("M17_FINITE_NUMBER_REQUIRED")
    return value


@dataclass(frozen=True)
class Parameters:
    window_seconds: float = .750
    subwindow_seconds: float = .250
    stable_seconds: float = .100
    minimum_sell_shares: D = D("5")
    minimum_sell_to_initial_depth: D = D(".50")
    minimum_distinct_sell_records: int = 3
    occupied_subwindows_required: int = 3
    max_single_sell_fraction: D = D(".80")
    minimum_visible_to_initial_depth: D = D(".70")
    terminal_visible_to_initial_depth: D = D(".90")
    recheck_seconds: float = .100
    publication_lateness_seconds: float = .050
    ttl: float = 3.0
    wait_floor_to_initial_depth: D = D(".70")


def signal_version(parameters):
    return VERSION + "/p-" + parameter_identity(parameters)[1][:12]


@dataclass
class Pending:
    identity: tuple
    ref: tuple
    received: D
    source: D
    deadline: D
    forming: bool


@dataclass
class Root:
    id: str
    token: str
    price: D
    generation: int
    start: D
    end: D
    q0: D
    quantity_ref: dict
    start_ref: tuple
    current_quantity: D
    minimum_quantity: D
    stable_from: D | None
    last_received: D
    hits: D = D(0)
    buckets: list = field(default_factory=lambda: [D(0), D(0), D(0)])
    largest_hit: D = D(0)
    forming_records: int = 0
    seen: set = field(default_factory=set)
    pending: dict = field(default_factory=dict)
    rechecked_records: int = 0
    closed: bool = False
    closed_available: D | None = None
    end_quantity: D | None = None
    signal_id: str | None = None
    confirmed: float | None = None
    deadline: D | None = None
    pending_since: D | None = None
    pending_exposure: D = D(0)


class M17(M12):
    strategy = "M17"

    def __init__(self, emit, parameters=None):
        if parameters is not None and not isinstance(parameters, Parameters):
            raise ValueError("M17_PARAMETERS_REQUIRED")
        super().__init__(emit, parameters or Parameters())
        p = self.p
        if (p.window_seconds, p.subwindow_seconds, p.stable_seconds, p.recheck_seconds,
                p.publication_lateness_seconds, p.ttl, p.minimum_distinct_sell_records,
                p.occupied_subwindows_required) != (.750, .250, .100, .100, .050, 3., 3, 3):
            raise ValueError("M17_FIXED_CAUSAL_WINDOW_OR_RECHECK_CONTRACT_CHANGED")
        if not (0 < p.minimum_sell_shares and 0 < p.minimum_sell_to_initial_depth
                and 0 < p.max_single_sell_fraction < 1
                and 0 < p.minimum_visible_to_initial_depth <= p.terminal_visible_to_initial_depth <= 1
                and p.wait_floor_to_initial_depth == p.minimum_visible_to_initial_depth):
            raise ValueError("M17_QUANTITY_PARAMETER_SEMANTICS")
        self.version = signal_version(p)
        self.roots, self.last_end = {}, {}
        # Common health/retirement consumers count the strategy-owned windows;
        # these are never fed to the 500ms RF accumulator.
        self.rounds = self.roots

    @staticmethod
    def ref(value):
        if (not isinstance(value, tuple) or len(value) != 3
                or any(type(x) is not int or x < 0 for x in value) or value[0] < 1):
            raise ValueError("M17_RAW_EVENT_REFERENCE_REQUIRED")
        return value

    def clock(self, now, seq, subindex=-1):
        if number(now) < number(self.now):
            raise ValueError("SOP_AVAILABLE_CLOCK_REGRESSION")
        self.now, self.key = now, (now, seq, subindex)
        for token, r in tuple(self.roots.items()):
            if any(number(now) >= item.deadline for item in r.pending.values()):
                self.end(token, "TRADE_DEPTH_RECHECK_UNAVAILABLE", unknown=True)
            elif r.deadline is not None and number(now) >= r.deadline:
                self.end(token, "SIGNAL_TTL_EXPIRED")
            elif r.closed and r.signal_id is None and number(now) != r.closed_available:
                self.end(token, "FIRST_PUBLICATION_BOUNDARY_NOT_COMPLETED", unknown=True)
            elif not r.closed and number(now) > r.end + number(self.p.publication_lateness_seconds):
                self.end(token, "PUBLICATION_LATENESS_EXCEEDED", unknown=True)

    def exposure(self, r):
        return r.pending_exposure + (number(self.now) - r.pending_since
                                     if r.pending_since is not None else D(0))

    def end(self, token, reason, *, unknown=False):
        r = self.roots.pop(token, None)
        if r is not None:
            self.last_end[token] = (number(self.now), self.key[1:])
            self.event("QR_WINDOW_ENDED", id=r.id, token=token, reason=reason,
                       pending_records=len(r.pending), pending_exposure_seconds=self.exposure(r))
            if r.signal_id and self.signals.pop(r.signal_id, None) is not None:
                self.event("SIGNAL_ENDED", id=r.signal_id, reason=reason)
        if unknown:
            self.quality[token] = reason
            self.event("QUALITY_UNKNOWN", token=token, reason=reason)

    def invalidate(self, token, reason):
        self.end(token, reason, unknown=True)

    def new_generation(self, token, generation):
        if token in self.roots:
            self.end(token, "BOOK_RECONSTRUCTED", unknown=True)
        self.quality.pop(token, None)
        self.event("BOOK_GENERATION_STARTED", token=token, generation=generation)

    def requires_pre_hit_for_trade(self, *, token, side, price, received, pre_event_best_bid=None):
        return (side == "SELL" and token not in self.roots and price == pre_event_best_bid)

    def reference_state(self, r):
        if not r.signal_id:
            return
        s = self.signals[r.signal_id]
        old = s["can_reference_for_new_action"]
        ready = not r.pending
        s.update(can_reference_for_new_action=ready, pending_rechecks=len(r.pending),
                 pending_exposure_seconds=self.exposure(r))
        if ready and not s["ever_reference_ready"]:
            s.update(ever_reference_ready=True, first_reference_ready=self.now)
        if old != ready:
            self.event("SIGNAL_REFERENCE_READY" if ready else "SIGNAL_REFERENCE_PENDING",
                       id=r.signal_id, token=r.token, confirmed=r.confirmed,
                       deadline=float(r.deadline), pending_rechecks=len(r.pending),
                       pending_exposure_seconds=self.exposure(r))

    def level_missing(self, token, side, price, received, **_):
        r = self.roots.get(token)
        if r is not None and side == "BID" and price == r.price:
            self.end(token, "ORIGINAL_LEVEL_MISSING")

    def depth(self, token, side, price, quantity, received, *, generation=None,
              input_ref=None, source=None, kind=None):
        r = self.roots.get(token)
        if r is None or side != "BID" or price != r.price:
            return
        ref = self.ref(input_ref)
        t, source, q = number(received), number(source), number(quantity)
        if ref[:2] != self.key[1:] or t > number(self.now) or q < 0:
            raise ValueError("M17_QUANTITY_PROVENANCE_OR_CLOCK_CONFLICT")
        if kind not in {"BID_PRICE_CHANGE", "BID_BOOK"}:
            self.end(token, "EXPLICIT_ANCHOR_QUANTITY_EVENT_REQUIRED", unknown=True)
            return
        if generation != r.generation or q <= 0:
            self.end(token, "ORIGINAL_LEVEL_LOST_OR_GENERATION_CHANGED")
            return
        if t < r.last_received or (r.closed and t < r.end):
            self.end(token, "LATE_INPUT_TO_CLOSED_OR_ADVANCED_WINDOW", unknown=True)
            return
        floor = (self.p.wait_floor_to_initial_depth if r.signal_id
                 else self.p.minimum_visible_to_initial_depth) * r.q0
        if q < floor:
            self.end(token, "ORIGINAL_QUANTITY_FLOOR_BROKEN")
            return
        r.current_quantity, r.last_received = q, t
        if t < r.end and not r.closed:
            r.minimum_quantity = min(r.minimum_quantity, q)
            if q < self.p.terminal_visible_to_initial_depth * r.q0:
                r.stable_from = None
            elif r.stable_from is None:
                r.stable_from = t
        for identity, hit in tuple(r.pending.items()):
            if (ref > hit.ref and source > hit.source and t >= hit.received
                    and t < hit.deadline and number(self.now) < hit.deadline
                    and (not hit.forming or t < r.end)):
                del r.pending[identity]
                r.rechecked_records += 1
                self.event("TRADE_DEPTH_RECHECKED", id=r.id, token=token,
                           trade_ref=hit.ref, quantity_ref=ref, quantity_source=source,
                           received=t, deadline=hit.deadline, forming=hit.forming)
        if not r.pending and r.pending_since is not None:
            r.pending_exposure += number(self.now) - r.pending_since
            r.pending_since = None
        self.reference_state(r)

    def trade(self, *, token, side, price, quantity, received, q0, generation,
              input_ref=None, source=None, economic_identity=None,
              initial_quantity_ref=None, pre_event_best_bid=None):
        if side != "SELL" or token in self.quality:
            return
        r = self.roots.get(token)
        if r is None and price != pre_event_best_bid:
            return
        if r is not None and price != r.price:
            return
        ref = self.ref(input_ref)
        t, source, q = number(received), number(source), number(quantity)
        if ref[:2] != self.key[1:] or t > number(self.now) or q <= 0:
            raise ValueError("M17_TRADE_PROVENANCE_OR_CLOCK_CONFLICT")
        if not isinstance(economic_identity, tuple) or not economic_identity:
            self.invalidate(token, "DIRECT_TRADE_IDENTITY_MISSING")
            return
        if r is None:
            previous = self.last_end.get(token)
            if previous and (t <= previous[0] or self.key[1:] <= previous[1]):
                return
            try:
                qr, qs, qref = initial_quantity_ref
                self.ref(qref)
                if number(qr) > t or number(qs) >= source or qref >= ref or number(q0) <= 0:
                    raise ValueError("INITIAL_DEPTH_ORDER_UNKNOWN")
            except (TypeError, ValueError, ArithmeticError):
                self.invalidate(token, "INITIAL_DEPTH_ORDER_UNKNOWN")
                return
            self.serial += 1
            identity = f"M17:{token}:{generation}:{ref}:{economic_identity}"
            r = Root(identity, token, price, generation, t, t + number(self.p.window_seconds),
                     number(q0), {"received": number(qr), "source": number(qs), "ref": qref},
                     ref, number(q0), number(q0), t, t)
            self.roots[token] = r
            self.event("QR_WINDOW_STARTED", id=r.id, token=token, price=price, generation=generation,
                       initial_quantity=r.q0, quantity_ref=r.quantity_ref,
                       start=t, end=r.end, first_trade_ref=ref)
        if generation != r.generation or t < r.last_received or (r.closed and t < r.end):
            self.end(token, "LATE_OR_FOREIGN_GENERATION_TRADE", unknown=True)
            return
        if economic_identity in r.seen:
            self.event("DUPLICATE_DIRECT_TRADE", id=r.id, token=token)
            return
        r.seen.add(economic_identity)
        r.last_received = t
        forming = t < r.end
        if forming:
            bucket = int((t - r.start) / number(self.p.subwindow_seconds))
            r.hits += q
            r.buckets[bucket] += q
            r.largest_hit = max(r.largest_hit, q)
            r.forming_records += 1
        deadline = t + number(self.p.recheck_seconds)
        if number(self.now) >= deadline:
            self.end(token, "TRADE_DEPTH_RECHECK_UNAVAILABLE", unknown=True)
            return
        if r.pending_since is None:
            r.pending_since = number(self.now)
        r.pending[economic_identity] = Pending(economic_identity, ref, t, source, deadline, forming)
        self.event("TRADE_DEPTH_RECHECK_PENDING", id=r.id, token=token, trade_ref=ref,
                   deadline=deadline, forming=forming, pending_records=len(r.pending))
        self.reference_state(r)

    def close(self, watermark, covered):
        for token, r in tuple(self.roots.items()):
            if r.closed or number(watermark) < r.end:
                continue
            if number(self.now) > r.end + number(self.p.publication_lateness_seconds):
                self.end(token, "PUBLICATION_LATENESS_EXCEEDED", unknown=True)
                continue
            if any(hit.forming for hit in r.pending.values()):
                self.end(token, "TRADE_DEPTH_RECHECK_UNAVAILABLE", unknown=True)
                continue
            if not covered(token, float(r.start), float(r.end)):
                self.end(token, "WINDOW_COVERAGE_UNAVAILABLE", unknown=True)
                continue
            checks = {
                "sell_quantity": r.hits >= max(self.p.minimum_sell_shares,
                                               self.p.minimum_sell_to_initial_depth * r.q0),
                "distinct_records": r.forming_records >= self.p.minimum_distinct_sell_records,
                "occupied_subwindows": sum(x > 0 for x in r.buckets) == self.p.occupied_subwindows_required,
                "single_print_fraction": r.largest_hit <= self.p.max_single_sell_fraction * r.hits,
                "terminal_continuous_hold": r.stable_from is not None
                    and r.stable_from <= r.end - number(self.p.stable_seconds),
            }
            self.event("QR_WINDOW_REVIEWED", id=r.id, token=token, checks=checks, H=r.hits,
                       subwindow_H=r.buckets, distinct_records=r.forming_records,
                       largest_hit=r.largest_hit, Q0=r.q0, Qmin=r.minimum_quantity,
                       Qend=r.current_quantity, stable_from=r.stable_from,
                       rechecked_records=r.rechecked_records, end=r.end)
            if not all(checks.values()):
                self.end(token, "FORMATION_CONDITIONS_NOT_MET")
                continue
            r.closed, r.closed_available, r.end_quantity = True, number(self.now), r.current_quantity

    def publish(self):
        for token, r in tuple(self.roots.items()):
            if not r.closed or r.signal_id:
                continue
            if r.current_quantity < self.p.terminal_visible_to_initial_depth * r.q0:
                self.end(token, "PUBLICATION_RETENTION_FAILED")
                continue
            r.signal_id = r.id + ":signal"
            r.confirmed, r.deadline = self.now, number(self.now) + number(self.p.ttl)
            s = {"id": r.signal_id, "root_id": r.id, "token": token, "version": self.version,
                 "component_ids": [r.id], "confirmed": self.now, "deadline": float(r.deadline),
                 "anchor_price": r.price, "quality_status": "PROVEN",
                 "initial_quantity": r.q0, "formation_H": r.hits, "subwindow_H": list(r.buckets),
                 "floor": self.p.wait_floor_to_initial_depth * r.q0,
                 "can_reference_for_new_action": not r.pending, "ever_reference_ready": not r.pending,
                 "first_reference_ready": self.now if not r.pending else None,
                 "pending_rechecks": len(r.pending), "pending_exposure_seconds": self.exposure(r),
                 "end_quantity": r.end_quantity,
                 "quantity_residual_diagnostic": r.end_quantity - r.q0 + r.hits,
                 "residual_is_independent_evidence": False}
            self.signals[r.signal_id] = s
            self.event("SIGNAL_CONFIRMED", **s)
            if not r.pending:
                self.event("SIGNAL_REFERENCE_READY", id=r.signal_id, token=token,
                           confirmed=r.confirmed, deadline=float(r.deadline),
                           pending_rechecks=0, pending_exposure_seconds=self.exposure(r))

    def release(self):
        super().release()
        self.last_end.clear()
