"""M13 COMMON-LW / strict support / first new SELL retest state machine.

The raw consumer supplies observed fixed-level paths, not exchange-order proof.
LW keeps cumulative BUY evidence from its covered prehistory through termination.
No execution, settlement, or future-data dependency is allowed here.
"""

from collections import deque
from dataclasses import asdict, dataclass
from decimal import Decimal as D

from .m12 import EPS, M12, parameter_identity

VERSION = "m13-fixed-anchor-entry-v1/raw-path-r1"


@dataclass(frozen=True)
class Parameters:
    round_seconds: float = 0.5
    stable_seconds: float = 0.1
    stable_recovery: D = D(".50")
    bid_recovery: D = D(".50")
    ttl: float = 8.0  # Prior strict-support lifetime, not the signal deadline.
    ratio_min: D = D(".25")
    ratio_max: D = D("4")
    lw_formation_seconds: float = 0.750
    lw_hold_seconds: float = 0.100
    lw_prehistory_seconds: float = 0.500
    lw_loss_min: D = D(".30")
    lw_residual_min: D = D(".20")
    lw_residual_loss_min: D = D(".50")
    lw_keep_residual_min: D = D(".10")
    lw_keep_residual_loss_min: D = D(".25")
    lw_native_ttl: float = 0.750
    formation_after_lw_max: float = 1.250
    post_retest_ttl: float = 3.0
    lw_root_hard_ttl: float = 3.0


def signal_version(parameters):
    return VERSION + "/p-" + parameter_identity(parameters)[1][:12]


@dataclass
class Withdrawal:
    id: str
    token: str
    generation: int
    levels: tuple
    q0: D
    start: float
    start_key: tuple
    buy: D
    depth_ref: tuple | None = None
    hold: float | None = None
    hold_end: float | None = None
    pending_key: tuple | None = None
    key: tuple | None = None
    native_ended: bool = False
    seed: dict | None = None
    retest_id: str | None = None
    qualified_key: tuple | None = None
    signal_id: str | None = None


class M13(M12):
    strategy = "M13"

    def __init__(self, emit, parameters=None):
        super().__init__(emit, parameters or Parameters())
        self.version = signal_version(self.p)
        self.withdrawals, self.ask_books, self.buy_history = {}, {}, {}
        self.lw_last_end, self.ready_retests = {}, {}

    @staticmethod
    def requires_pre_hit(side):
        # BUY contributes known direct volume to LW; it does not start an ASK
        # recovery round and does not need a fictitious pre-hit Ask quantity.
        return side == "SELL"

    def clock(self, now, seq, subindex=-1):
        super().clock(now, seq, subindex)
        for history in self.buy_history.values():
            # Keep the consumer's bounded 2s coverage horizon, including input
            # processing delay; each active root has its own cumulative total.
            while history and history[0][0] < now - 2:
                history.popleft()
        for token, w in tuple(self.withdrawals.items()):
            if w.key is None:
                if now + EPS >= w.start + self.p.lw_formation_seconds:
                    self.end_withdrawal(token, "LW_FORMATION_EXPIRED")
                continue
            if not w.native_ended and now + EPS >= w.key[0] + self.p.lw_native_ttl:
                w.native_ended = True
                self.event("LW_NATIVE_EXPIRED", id=w.id, retained_as_history=True)
            if w.signal_id:
                s = self.signals.get(w.signal_id)
                if s is None or now + EPS >= s["deadline"]:
                    self.end_withdrawal(token, "SIGNAL_TTL_EXPIRED")
            elif w.seed:
                if now + EPS >= w.seed["support"]["deadline"]:
                    self.end_withdrawal(token, "PRIOR_SUPPORT_EXPIRED")
                elif w.qualified_key is None and now + EPS >= w.seed["qualification_deadline"]:
                    self.end_withdrawal(token, "RETEST_NOT_QUALIFIED_BEFORE_ORIGINAL_DEADLINE")
                elif now + EPS >= w.key[0] + self.p.formation_after_lw_max:
                    self.end_withdrawal(token, "RETEST_FORMATION_EXPIRED")
            elif w.native_ended:
                self.end_withdrawal(token, "LW_NO_SUPPORT_OVERLAP")

    def end_withdrawal(self, token, reason):
        w = self.withdrawals.pop(token, None)
        if w is None:
            return
        self.lw_last_end[token] = self.key
        self.event("LW_ROOT_ENDED", id=w.id, token=token, reason=reason)
        if w.signal_id and self.signals.pop(w.signal_id, None) is not None:
            self.event("SIGNAL_ENDED", id=w.signal_id, reason=reason)

    def end_component(self, identity, reason):
        super().end_component(identity, reason)
        for token, w in tuple(self.withdrawals.items()):
            if w.signal_id:
                if w.signal_id not in self.signals:
                    self.end_withdrawal(token, reason)
            elif w.seed and w.seed["support"]["id"] == identity:
                self.end_withdrawal(token, reason)

    def invalidate(self, token, reason):
        super().invalidate(token, reason)
        self.end_withdrawal(token, reason)
        self.ask_books.pop(token, None)
        self.buy_history.pop(token, None)

    def new_generation(self, token, generation):
        self.invalidate(token, "BOOK_RECONSTRUCTED")
        super().new_generation(token, generation)

    def book(self, token, asks, received, generation, covered, *, input_ref=None):
        """One healthy snapshot, or one original ordered Ask delta, at a time."""
        old = self.ask_books.get(token)
        self.ask_books[token] = dict(asks)
        w = self.withdrawals.get(token)
        if w is None and old:
            last = self.lw_last_end.get(token)
            levels = tuple(sorted(old)[:3])
            # COMMON's existing profile freezes the available nearest levels
            # (up to three); standalone P_LW adds an exact-three requirement and
            # is explicitly not a drop-in replacement for M13's component.
            if not levels:
                return
            q0 = sum((old[p] for p in levels), D(0))
            if q0 <= sum((asks.get(p, D(0)) for p in levels), D(0)):
                return
            if last and (received <= last[0] + EPS or self.key <= last):
                return
            if not covered(token, received - self.p.lw_prehistory_seconds, received):
                self.event("LW_UNKNOWN", token=token, reason="PRE_START_BUY_COVERAGE_UNPROVEN")
                return
            self.serial += 1
            w = Withdrawal(
                f"{self.strategy}:LW:{generation}:{token}:{self.serial}", token, generation,
                levels, q0, received, self.key,
                sum((q for t, p, q in self.buy_history.get(token, ())
                     if received - self.p.lw_prehistory_seconds - EPS <= t <= received + EPS
                     and p in levels), D(0)), depth_ref=input_ref,
            )
            self.withdrawals[token] = w
            self.event("LW_STARTED", **asdict(w))
        if w is not None:
            if w.key and received + EPS < (w.hold_end or w.start):
                self.end_withdrawal(token, "LATE_INPUT_TO_CLOSED_LW_FORMATION")
                self.event("LW_UNKNOWN", id=w.id, token=token, reason="LATE_DEPTH")
            else:
                self.update_withdrawal(token, received)

    def withdrawal_values(self, w):
        asks = self.ask_books.get(w.token, {})
        loss = max(D(0), w.q0 - sum((asks.get(p, D(0)) for p in w.levels), D(0)))
        residual = max(D(0), loss - w.buy)
        return loss, residual

    def update_withdrawal(self, token, received):
        w = self.withdrawals.get(token)
        if w is None:
            return
        if self.ask_books.get(token, {}).get(w.levels[0], D(0)) <= 0:
            self.end_withdrawal(token, "LW_ORIGINAL_FRONT_ASK_LOST")
            return
        loss, residual = self.withdrawal_values(w)
        if w.key:
            if not (loss > 0 and residual / w.q0 >= self.p.lw_keep_residual_min
                    and residual / loss >= self.p.lw_keep_residual_loss_min):
                self.end_withdrawal(token, "LW_SUPPLY_RESTORED")
            return
        good = (loss / w.q0 >= self.p.lw_loss_min
                and residual / w.q0 >= self.p.lw_residual_min
                and loss > 0 and residual / loss >= self.p.lw_residual_loss_min)
        if not good:
            # COMMON resets this continuous formation streak without changing
            # its original root. Do not import P_LW's additional first-completed
            # publication rule into the differently bound COMMON profile.
            w.hold = None
            w.hold_end = w.pending_key = None
            return
        if w.hold is None:
            w.hold = received

    def depth(self, token, side, price, quantity, received):
        super().depth(token, side, price, quantity, received)
        if side == "BID":
            self.qualify_retest(token)

    def trade(self, *, token, side, price, quantity, received, q0, generation):
        if received > self.now + EPS:
            raise ValueError("SOP_RECEIVED_AFTER_AVAILABLE")
        if side == "BUY":
            history = self.buy_history.setdefault(token, deque())
            history.append((received, price, quantity))
            if len(history) > 2048:
                raise RuntimeError("SOP_BUY_HISTORY_CAPACITY")
            w = self.withdrawals.get(token)
            if w and price in w.levels and received >= w.start - self.p.lw_prehistory_seconds - EPS:
                if w.key and received + EPS < (w.hold_end or w.start):
                    self.end_withdrawal(token, "LATE_INPUT_TO_CLOSED_LW_FORMATION")
                    self.event("LW_UNKNOWN", id=w.id, token=token, reason="LATE_BUY")
                else:
                    w.buy += quantity
                    self.update_withdrawal(token, received)
            return
        k = token, "BID", price
        starting = k not in self.rounds
        super().trade(token=token, side=side, price=price, quantity=quantity,
                      received=received, q0=q0, generation=generation)
        w = self.withdrawals.get(token)
        r = self.rounds.get(k)
        if (w and w.seed and not w.signal_id and w.retest_id is None and starting
                and r is not None and r.start == received
                and price == w.seed["support"]["price"]
                and received > w.seed["key"][0] + EPS and self.key > w.seed["key"]):
            w.retest_id = r.id
            s = w.seed["support"]
            minimum = max(D(".5"), D(".0001") * q0)
            available = q0 - s["guard"]
            bounds = {
                "loss_low": max(minimum, self.p.ratio_min * s["d"] * q0),
                "loss_high": min(available, self.p.ratio_max * s["d"] * q0),
                "hit_low": max(minimum, self.p.ratio_min * s["u"] * q0),
                "hit_high": self.p.ratio_max * s["u"] * q0,
            }
            self.event("RETEST_STARTED", root_id=w.id, round_id=r.id, q0=q0, **bounds)
            if available < minimum:
                self.end_withdrawal(token, "RETEST_NO_DEPLETION_HEADROOM")
            elif bounds["loss_low"] > bounds["loss_high"] or bounds["hit_low"] > bounds["hit_high"]:
                self.end_withdrawal(token, "RETEST_REFERENCE_COMPARISON_NO_HEADROOM")
        self.qualify_retest(token)

    def qualify_retest(self, token):
        w = self.withdrawals.get(token)
        if not w or not w.seed or not w.retest_id or w.qualified_key:
            return
        r = next((r for r in self.rounds.values() if r.id == w.retest_id), None)
        if r is None:
            return
        minimum = max(D(".5"), D(".0001") * r.q0)
        if r.hits >= minimum and r.q0 - r.qmin >= minimum:
            if self.now + EPS >= w.seed["qualification_deadline"]:
                self.end_withdrawal(token, "RETEST_QUALIFICATION_TOO_LATE")
            else:
                w.qualified_key = self.key
                self.event("RETEST_QUALIFIED", root_id=w.id, round_id=r.id)

    def close(self, watermark, covered):
        pending = {w.retest_id: w.token for w in self.withdrawals.values() if w.retest_id and not w.signal_id}
        super().close(watermark, covered)
        for rid, token in pending.items():
            if any(r.id == rid for r in self.rounds.values()):
                continue
            w = self.withdrawals.get(token)
            if w and w.retest_id == rid:
                # Confirmation waits for the complete raw parent frame. A later
                # row in this same frame must still be able to invalidate it.
                self.ready_retests[rid] = token
        for token, w in tuple(self.withdrawals.items()):
            if w.key is not None or w.hold is None:
                continue
            end = w.hold + self.p.lw_hold_seconds
            if watermark <= end + EPS:
                continue
            if self.now - end > 0.050 + EPS or not covered(token, w.start - self.p.lw_prehistory_seconds, end):
                self.event("LW_UNKNOWN", id=w.id, token=token, reason="LATE_CLOSE_OR_COVERAGE")
                self.end_withdrawal(token, "LW_FORMATION_UNKNOWN")
                continue
            w.hold_end, w.pending_key = end, self.key
        if len(self.components) > 512:
            raise RuntimeError("SOP_COMPONENT_CAPACITY")

    def publish(self):
        for w in tuple(self.withdrawals.values()):
            if w.key is None and w.pending_key is not None:
                w.key, w.pending_key = self.key, None
                self.event("LW_CONFIRMED", **asdict(w), loss=self.withdrawal_values(w)[0],
                           residual=self.withdrawal_values(w)[1], native_deadline=self.now + self.p.lw_native_ttl)
        ready, self.ready_retests = self.ready_retests, {}
        for rid, token in ready.items():
            w = self.withdrawals.get(token)
            if w and w.retest_id == rid and not w.signal_id:
                self.finish_retest(w, self.components.get(rid))
        for w in tuple(self.withdrawals.values()):
            if not w.key or w.seed or w.native_ended:
                continue
            eligible = [c for c in self.components.values()
                        if c.token == w.token and c.generation == w.generation
                        and c.side == "BID" and c.price < w.levels[0]
                        and self.now + EPS < c.deadline]
            if not eligible:
                continue
            support = max(eligible, key=lambda c: c.key)
            if sum(c.key == support.key for c in eligible) > 1:
                self.event("LW_UNKNOWN", token=w.token, id=w.id,
                           reason="LATEST_SUPPORT_KEY_TIED_UNPROVEN_SELECTION")
                self.end_withdrawal(w.token, "SUPPORT_SELECTION_UNKNOWN")
                continue
            w.seed = {"key": max(w.key, support.key), "support": asdict(support),
                      "qualification_deadline": min(w.key[0] + self.p.lw_native_ttl, support.deadline)}
            self.event("SEED_FROZEN", root_id=w.id, **w.seed)

    def finish_retest(self, w, component):
        s = w.seed["support"]
        if (component is None or w.qualified_key is None or s["id"] not in self.components
                or self.now + EPS >= min(w.key[0] + self.p.formation_after_lw_max, s["deadline"])):
            self.end_withdrawal(w.token, "FIRST_RETEST_FAILED_OR_EXPIRED")
            return
        if not all(self.p.ratio_min <= ratio <= self.p.ratio_max
                   for ratio in (component.u / s["u"], component.d / s["d"])):
            self.end_withdrawal(w.token, "RETEST_REFERENCE_COMPARISON_FAILED")
            return
        if component.guard < s["guard"]:
            raise RuntimeError("M13_RETEST_FLOOR_LOWER_THAN_OLD_SUPPORT")
        identity = f"M13:retest:{w.id}"
        w.signal_id = identity
        signal = {
            "id": identity, "token": w.token, "root_id": w.id,
            "strategy": self.strategy, "version": self.version,
            "component_ids": [component.id, w.id], "confirmed": self.now,
            "deadline": min(self.now + self.p.post_retest_ttl, w.key[0] + self.p.lw_root_hard_ttl),
            "anchor_price": component.price, "generation": component.generation,
            "prior_support": s, "bid": asdict(component), "lw": asdict(w),
            "quality_status": "PROVEN",
        }
        self.signals[identity] = signal
        self.event("SIGNAL_CONFIRMED", **signal)

    def release(self):
        super().release()
        for values in (self.withdrawals, self.ask_books, self.buy_history, self.lw_last_end, self.ready_retests):
            values.clear()
