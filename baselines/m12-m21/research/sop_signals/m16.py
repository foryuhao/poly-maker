"""M16: historical slow recovery -> first comparable, faster stable recovery.

Only received-clock raw evidence can establish speed. R1's historical floor is
not a guard on R2 formation. Execution and order budgets are separate policies.
"""

from dataclasses import dataclass
from decimal import Decimal as D

from .m12 import M12, parameter_identity
from .m16_timing import Observation, RecoveryPath, number

VERSION = "m16-fixed-anchor-entry-v1/raw-path-r1"


@dataclass(frozen=True)
class Parameters:
    round_seconds: float = .5
    stable_seconds: float = .1
    stable_recovery: D = D(".50")
    pair_gap: float = 2.0
    ttl: float = 3.0
    absolute_hit_min: D = D("1")
    absolute_hit_max: D = D("2")
    normalized_hit_min: D = D("1")
    normalized_hit_max: D = D("2")
    normalized_loss_min: D = D(".80")
    normalized_loss_max: D = D("1.25")
    reference_latency_min: float = .1
    second_speed_ratio_max: D = D(".50")
    post_recovery_ratio_min: D = D(".80")
    post_speed_ratio_max: D = D("1")


def signal_version(parameters):
    return VERSION + "/p-" + parameter_identity(parameters)[1][:12]


@dataclass
class ObservedRound:
    id: str
    token: str
    price: D
    generation: int
    start_key: tuple
    path: RecoveryPath
    role: str
    current_quantity: D
    side: str = "BID"


@dataclass
class Campaign:
    id: str
    token: str
    price: D
    generation: int
    reference: dict
    pair_deadline: float
    current_quantity: D
    second_id: str | None = None
    ready: dict | None = None
    signal_id: str | None = None


class M16(M12):
    strategy = "M16"

    def __init__(self, emit, parameters=None, *, receiver_quantum=None):
        if parameters is not None and not isinstance(parameters, Parameters):
            raise ValueError("M16_PARAMETERS_REQUIRED")
        super().__init__(emit, parameters or Parameters())
        if (self.p.round_seconds, self.p.stable_seconds, self.p.stable_recovery) != (.5, .1, D(".50")):
            raise ValueError("M16_FIXED_STRICT_ROUND_CONTRACT_CHANGED")
        self.version = signal_version(self.p)
        self.receiver_quantum = receiver_quantum
        self.campaigns, self.campaign_ends = {}, {}
        self.closed_windows, self.blocked_rounds = {}, {}

    @staticmethod
    def requires_pre_hit(side):
        return side == "SELL"

    def boundary_key(self):
        return *self.key, -1

    def clock(self, now, seq, subindex=-1):
        if number(now) < number(self.now):
            raise ValueError("SOP_AVAILABLE_CLOCK_REGRESSION")
        self.now, self.key = now, (now, seq, subindex)
        for token, c in tuple(self.campaigns.items()):
            if c.signal_id:
                if number(now) >= number(self.signals[c.signal_id]["deadline"]):
                    self.end_campaign(token, "SIGNAL_TTL_EXPIRED")
            elif c.ready is not None and now != c.ready["key"][0]:
                self.end_campaign(token, "PUBLICATION_BOUNDARY_NOT_COMPLETED")
            elif c.ready is None and number(now) >= number(c.pair_deadline):
                self.end_campaign(token, "SECOND_CONFIRMATION_GAP_EXPIRED")
        self.closed_windows = {k: v for k, v in self.closed_windows.items() if v[1] >= now - 4}
        self.campaign_ends = {k: v for k, v in self.campaign_ends.items() if v[0] >= now - 4}
        self.blocked_rounds = {k: v for k, v in self.blocked_rounds.items() if v >= number(now) - 4}

    def end_campaign(self, token, reason):
        c = self.campaigns.pop(token, None)
        if c is None:
            return
        self.campaign_ends[token] = self.boundary_key()
        if c.ready:
            self.components.pop(c.ready["id"], None)
        self.event("SPEED_CAMPAIGN_ENDED", id=c.id, token=token, reason=reason)
        if c.signal_id and self.signals.pop(c.signal_id, None) is not None:
            self.event("SIGNAL_ENDED", id=c.signal_id, reason=reason)

    def end_component(self, identity, reason):
        for token, c in tuple(self.campaigns.items()):
            if c.ready and c.ready["id"] == identity:
                self.end_campaign(token, reason)

    def invalidate(self, token, reason):
        self.quality[token] = reason
        for k, r in tuple(self.rounds.items()):
            if r.token == token:
                r.path.release()
                del self.rounds[k]
                self.event("ROUND_UNKNOWN", id=r.id, reason=reason)
        self.end_campaign(token, reason)
        self.blocked_rounds = {k: v for k, v in self.blocked_rounds.items() if k[0] != token}
        self.event("QUALITY_UNKNOWN", token=token, reason=reason)

    def new_generation(self, token, generation):
        self.invalidate(token, "BOOK_RECONSTRUCTED")
        self.closed_windows = {k: v for k, v in self.closed_windows.items() if k[0] != token}
        self.quality.pop(token, None)
        self.event("BOOK_GENERATION_STARTED", token=token, generation=generation)

    def point(self, *, token, price, quantity, generation, received, input_ref, source, kind):
        if (not isinstance(input_ref, tuple) or len(input_ref) != 3
                or input_ref[:2] != self.key[1:]
                or any(type(x) is not int or x < 0 for x in input_ref)
                or input_ref[0] < 1 or source is None
                or type(generation) is not int or generation < 1):
            self.invalidate(token, "EXPLICIT_TIMING_PROVENANCE_MISSING_OR_CONFLICT")
            return None
        if number(received) > number(self.now):
            raise ValueError("SOP_RECEIVED_AFTER_AVAILABLE")
        try:
            if number(quantity) < 0:
                raise ValueError("NEGATIVE_QUANTITY")
            number(source)
        except (TypeError, ValueError, ArithmeticError):
            self.invalidate(token, "EXPLICIT_TIMING_PROVENANCE_INVALID_NUMBER")
            return None
        return Observation(kind, token, price, generation, received, self.now,
                           *input_ref, source, quantity)

    def level_missing(self, token, side, price, received, *, frame, row, source):
        if side == "BID" and ((token, side, price) in self.rounds
                              or (token in self.campaigns and self.campaigns[token].price == price)):
            self.event("ORIGINAL_LEVEL_ABSENT", token=token, price=price, received=received,
                       raw_frame=frame, raw_row=row, source=source, quantity_endpoint=False)
            self.invalidate(token, "ORIGINAL_LEVEL_MISSING_IN_FULL_BOOK")

    def depth(self, token, side, price, quantity, received, *, generation=None,
              input_ref=None, source=None, kind=None):
        if side != "BID":
            return
        k = token, side, price
        r, c = self.rounds.get(k), self.campaigns.get(token)
        if r is None and (c is None or c.price != price):
            return
        point = self.point(token=token, price=price, quantity=quantity, generation=generation,
                           received=received, input_ref=input_ref, source=source, kind=kind)
        if point is None:
            return
        if kind not in {"BID_PRICE_CHANGE", "BID_BOOK"}:
            self.invalidate(token, "EXPLICIT_ANCHOR_QUANTITY_EVENT_REQUIRED")
            return
        last = self.closed_windows.get(k)
        if last and number(received) < last[0]:
            self.invalidate(token, "LATE_INPUT_TO_CLOSED_ROUND")
            return
        if r is not None:
            r.path.observe(point)
            r.current_quantity = quantity
            if r.path.failure:
                self.invalidate(token, r.path.failure)
                return
        if c is not None and c.price == price:
            c.current_quantity = quantity
            if generation != c.generation or quantity <= 0:
                self.end_campaign(token, "ORIGINAL_LEVEL_LOST_OR_GENERATION_CHANGED")
            elif c.ready and quantity < c.ready["guard"]:
                self.end_campaign(token, "ORIGINAL_LEVEL_GUARD")

    def trade(self, *, token, side, price, quantity, received, q0, generation,
              input_ref=None, source=None):
        if side != "SELL":
            return
        point = self.point(token=token, price=price, quantity=quantity, generation=generation,
                           received=received, input_ref=input_ref, source=source, kind="DIRECT_SELL")
        if point is None:
            return
        k = token, "BID", price
        start_key = (self.now, *input_ref)
        r = self.rounds.get(k)
        if r is not None:
            if number(received) < r.path.end:
                r.path.observe(point)
                if r.path.failure:
                    self.invalidate(token, r.path.failure)
            else:
                # A physical SELL round already started before R1 was available.
                # Remember its window so a later hit cannot relabel it as R2.
                end = self.blocked_rounds.get(k)
                if end is None or number(received) >= end:
                    self.blocked_rounds[k] = number(received) + number(self.p.round_seconds)
                self.event("ROUND_NOT_STARTED", token=token, side="BID",
                           reason="PRIOR_WINDOW_NOT_CLOSED_NEW_PHYSICAL_ROUND_INELIGIBLE")
            return
        if number(received) < self.blocked_rounds.get(k, D("-Infinity")):
            self.event("ROUND_NOT_STARTED", token=token, side="BID",
                       reason="PHYSICAL_ROUND_STARTED_BEFORE_REFERENCE_AVAILABLE")
            return
        last = self.closed_windows.get(k)
        if last and number(received) < last[0]:
            self.invalidate(token, "LATE_INPUT_TO_CLOSED_ROUND")
            return
        if q0 <= 0:
            self.event("ROUND_NOT_STARTED", token=token, side="BID", reason="NO_PRE_HIT_LEVEL")
            return
        if len(self.rounds) >= 512:
            raise RuntimeError("SOP_ROUND_CAPACITY")
        self.serial += 1
        identity = f"M16:{generation}:{token}:BID:{self.serial}"
        role = "INELIGIBLE"
        c = self.campaigns.get(token)
        if c is not None and generation != c.generation:
            self.invalidate(token, "BOOK_GENERATION_CHANGED")
            return
        after_previous_close = last is None or number(received) > number(last[1])
        if c and (c.price, c.generation) == (price, generation):
            if c.signal_id:
                role = "FOLLOWUP"
            elif (c.second_id is None and number(received) > number(c.reference["key"][0])
                  and start_key > c.reference["key"] and after_previous_close):
                role, c.second_id = "SECOND", identity
        elif c is None:
            ended = self.campaign_ends.get(token)
            if after_previous_close and (ended is None or (number(received) > number(ended[0])
                                                            and start_key > ended)):
                role = "REFERENCE_CANDIDATE"
        path = RecoveryPath(token=token, price=price, generation=generation, start=received,
                            q0=q0, receiver_quantum=self.receiver_quantum)
        r = ObservedRound(identity, token, price, generation, start_key, path, role, q0)
        self.rounds[k] = r
        path.observe(point)
        self.event("ROUND_STARTED", id=identity, token=token, side="BID", price=price,
                   generation=generation, received=received, q0=q0, role=role,
                   raw_input_ref=input_ref)
        if path.failure:
            self.invalidate(token, path.failure)

    def close(self, watermark, covered):
        for k, r in tuple(self.rounds.items()):
            if self.rounds.get(k) is not r or number(watermark) <= r.path.end:
                continue
            del self.rounds[k]
            result = r.path.finish(available=self.now, watermark=watermark,
                                   coverage_proven=covered(r.token, float(r.path.start), float(r.path.end)))
            self.closed_windows[k] = r.path.end, self.now
            fact = {**result, "id": r.id, "side": "BID", "role": r.role,
                    "key": self.boundary_key(), "window_end": float(r.path.end),
                    "u": result["hits"] / result["q0"],
                    "d": result["loss"] / result["q0"], "r": result["recovery"],
                    "guard": result["qmin"] + D(".40") * result["loss"],
                    "start_key": r.start_key, "current_quantity": r.current_quantity}
            r.path.release()
            self.event("ROUND_CLOSED", **fact)
            if result["status"] == "TIMING_UNAVAILABLE":
                self.event("ROUND_UNKNOWN", id=r.id, reason=result["reason"],
                           timing_evidence_event="ROUND_CLOSED", timing_evidence_id=r.id)
            c = self.campaigns.get(r.token)
            if c is not None and c.price == r.price and c.generation == r.generation:
                if (c.ready is not None and r.id != c.ready["id"]
                        and r.path.start >= number(c.ready["window_end"])
                        and r.start_key > c.ready["start_key"]):
                    self.followup(c, fact)
                elif c.second_id == r.id:
                    self.second(c, fact)
                continue
            ended = self.campaign_ends.get(r.token)
            if (c is not None or r.role != "REFERENCE_CANDIDATE"
                    or (ended is not None and (r.start_key <= ended
                                               or number(r.path.start) <= number(ended[0])))):
                continue
            if (result["status"] != "TIMING_PROVEN"
                    or result["tau_low"] < number(self.p.reference_latency_min)
                    or result["post_window_missing"] or r.current_quantity <= 0):
                self.event("SPEED_REFERENCE_REJECTED", id=r.id, reason="STRICT_TIMED_SLOW_REFERENCE_NOT_PROVEN")
                continue
            deadline = float(number(self.now) + number(self.p.pair_gap))
            self.campaigns[r.token] = Campaign(r.id, r.token, r.price, r.generation,
                                              fact, deadline, r.current_quantity)
            self.event("SPEED_REFERENCE_CONFIRMED", **fact, reference_only=True,
                       historical_floor_is_not_a_formation_guard=True, pair_deadline=deadline)

    def second(self, c, fact):
        if number(self.now) >= number(c.pair_deadline):
            self.end_campaign(c.token, "SECOND_CONFIRMATION_GAP_EXPIRED")
            return
        if fact["status"] != "TIMING_PROVEN":
            self.end_campaign(c.token, "FIRST_SECOND_ROUND_FAILED_OR_TIMING_UNAVAILABLE")
            return
        ref = c.reference
        ratios = {"absolute_hit": fact["hits"] / ref["hits"],
                  "normalized_hit": fact["u"] / ref["u"],
                  "normalized_loss": fact["d"] / ref["d"]}
        checks = {
            "absolute_hit": self.p.absolute_hit_min <= ratios["absolute_hit"] <= self.p.absolute_hit_max,
            "normalized_hit": self.p.normalized_hit_min <= ratios["normalized_hit"] <= self.p.normalized_hit_max,
            "normalized_loss": self.p.normalized_loss_min <= ratios["normalized_loss"] <= self.p.normalized_loss_max,
            "not_shallower": fact["r"] >= ref["r"],
            "conservative_speed": fact["tau_high"] <= self.p.second_speed_ratio_max * ref["tau_low"],
        }
        self.event("SPEED_PAIR_REVIEW", root_id=c.id, second_id=fact["id"], ratios=ratios, checks=checks)
        if not all(checks.values()):
            self.end_campaign(c.token, "FIRST_SECOND_ROUND_COMPARISON_FAILED")
            return
        if (fact["post_window_missing"] or fact["current_quantity"] <= 0
                or (fact["post_window_min"] is not None and fact["post_window_min"] < fact["guard"])):
            self.event("PUBLICATION_REJECTED", id=fact["id"], reason="POST_WINDOW_GUARD")
            self.end_campaign(c.token, "POST_WINDOW_GUARD")
            return
        c.ready = {**fact, "deadline": float(number(self.now) + number(self.p.ttl)),
                   "strength_ratios": ratios}
        self.components[fact["id"]] = c.ready

    def followup(self, c, fact):
        if not fact["qualified"] and fact["status"] != "TIMING_UNAVAILABLE":
            return
        if fact["status"] == "TIMING_UNAVAILABLE":
            self.invalidate(c.token, "SUBSEQUENT_TIMING_UNAVAILABLE:" + str(fact["reason"]))
        elif not fact["stable"] or fact["r"] < max(D(".50"), self.p.post_recovery_ratio_min * c.ready["r"]):
            self.end_campaign(c.token, "SUBSEQUENT_RECOVERY_WEAKENED")
        elif fact["tau_high"] > self.p.post_speed_ratio_max * c.reference["tau_low"]:
            self.end_campaign(c.token, "RECOVERY_SPEED_ADVANTAGE_ENDED")

    def publish(self):
        for token, c in tuple(self.campaigns.items()):
            if c.ready is None or c.signal_id:
                continue
            second = c.ready
            if (self.now != second["key"][0] or number(self.now) >= number(c.pair_deadline)
                    or number(self.now) >= number(second["deadline"])
                    or c.current_quantity <= 0 or c.current_quantity < second["guard"]):
                self.end_campaign(token, "PUBLICATION_BOUNDARY_OR_ORIGINAL_LEVEL_GUARD")
                continue
            identity = f"M16:pair:{c.id}"
            signal = {"id": identity, "token": token, "root_id": c.id, "version": self.version,
                      "component_ids": [c.id, second["id"]], "confirmed": self.now,
                      "deadline": second["deadline"], "anchor_price": c.price,
                      "generation": c.generation, "bid": dict(second),
                      "prior_reference": dict(c.reference), "quality_status": "PROVEN",
                      "reference_only_before_confirmation": True,
                      "post_window_prepublication_guard_status": "PROVEN",
                      "strength_ratios": second["strength_ratios"]}
            c.signal_id = identity
            self.signals[identity] = signal
            self.event("SIGNAL_CONFIRMED", **signal)

    def release(self):
        for r in self.rounds.values():
            r.path.release()
        super().release()
        for values in (self.campaigns, self.campaign_ends, self.closed_windows, self.blocked_rounds):
            values.clear()
