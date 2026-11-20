"""M15: COMMON withdrawal, old strict Bid, then first new weak BUY round.

The old Bid never hands over to a new Bid. Every relevant BUY first consumes
the original withdrawal's cumulative residual budget, including pre-seed flow.
No market outcome or execution object is available to this state machine.
"""

from dataclasses import asdict, dataclass
from decimal import Decimal as D

from .m12 import EPS, M12, parameter_identity
from .m13 import M13

VERSION = "m15-fixed-anchor-entry-v1/raw-path-r1"


@dataclass(frozen=True)
class Parameters:
    round_seconds: float = .500
    stable_seconds: float = .100
    stable_recovery: D = D(".50")
    bid_recovery: D = D(".50")
    ttl: float = 8.0  # Original strict support, never reset by the BUY test.
    ask_recovery: D = D(".30")
    ask_loss: D = D(".05")
    lw_formation_seconds: float = .750
    lw_hold_seconds: float = .100
    lw_prehistory_seconds: float = .500
    lw_loss_min: D = D(".30")
    lw_residual_min: D = D(".20")
    lw_residual_loss_min: D = D(".50")
    lw_keep_residual_min: D = D(".10")
    lw_keep_residual_loss_min: D = D(".25")
    lw_native_ttl: float = .750
    formation_after_lw_max: float = 1.250
    post_retest_ttl: float = 3.0
    lw_root_hard_ttl: float = 3.0


def signal_version(parameters):
    return VERSION + "/p-" + parameter_identity(parameters)[1][:12]


class M15(M13):
    strategy = "M15"

    def __init__(self, emit, parameters=None):
        if parameters is not None and not isinstance(parameters, Parameters):
            raise ValueError("M15_PARAMETERS_REQUIRED")
        super().__init__(emit, parameters or Parameters())
        self.version = signal_version(self.p)
        # These intervals identify an already-started physical BUY round only.
        # They do not assert a proven pre-hit Q0 or emit qualified components.
        # This prevents relabelling a pre-seed round as a post-seed test, while
        # requiring pre-hit quantity proof only for an actual new target test.
        self.buy_intervals, self.closed_buy_intervals = {}, {}

    def clock(self, now, seq, subindex=-1):
        super().clock(now, seq, subindex)
        self.closed_buy_intervals = {
            k: v for k, v in self.closed_buy_intervals.items() if v[1] >= now - 4
        }

    def requires_pre_hit_for_trade(self, *, token, side, price, received):
        if side == "SELL":
            return True
        w = self.withdrawals.get(token)
        last = self.closed_buy_intervals.get((token, price))
        return bool(
            w and w.seed and not w.signal_id and w.retest_id is None
            and price == w.levels[0]
            and received > w.seed["key"][0] + EPS and self.key > w.seed["key"]
            and (token, price) not in self.buy_intervals
            and (last is None or received > last[1] + EPS)
        )

    def observe_buy_interval(self, token, price, received):
        k = token, price
        last = self.closed_buy_intervals.get(k)
        if last is not None and received < last[0] - EPS:
            self.invalidate(token, "LATE_BUY_TO_CLOSED_ASK_WINDOW")
            return False
        if k in self.buy_intervals or (last is not None and received <= last[1] + EPS):
            return False
        if len(self.buy_intervals) >= 512:
            raise RuntimeError("SOP_BUY_INTERVAL_CAPACITY")
        self.buy_intervals[k] = (received, received + self.p.round_seconds)
        return True

    def trade(self, *, token, side, price, quantity, received, q0, generation):
        if side == "SELL":
            # SELL creates/maintains strict Bid evidence. It is never the M15
            # retest, and M13's SELL headroom/strength comparison does not apply.
            M12.trade(self, token=token, side=side, price=price, quantity=quantity,
                      received=received, q0=q0, generation=generation)
            return
        # The inherited BUY branch counts ALL original fixed-set consumption
        # and checks LW before this same event can start/qualify a new R.
        M13.trade(self, token=token, side=side, price=price, quantity=quantity,
                  received=received, q0=q0, generation=generation)
        fresh = self.observe_buy_interval(token, price, received)
        w = self.withdrawals.get(token)
        if not w or not w.seed or w.signal_id or price != w.levels[0]:
            return
        if w.retest_id is None:
            if not (fresh and received > w.seed["key"][0] + EPS and self.key > w.seed["key"]):
                self.event("BUY_NOT_NEW_FOR_SEED", root_id=w.id, token=token,
                           received=received, seed_key=w.seed["key"],
                           active_interval=self.buy_intervals.get((token, price)))
                return
            M12.trade(self, token=token, side=side, price=price, quantity=quantity,
                      received=received, q0=q0, generation=generation)
            r = self.rounds.get((token, "ASK", price))
            if r is None:
                self.end_withdrawal(token, "FIRST_BUY_RETEST_NOT_STARTABLE")
                return
            w.retest_id = r.id
            self.event("RETEST_STARTED", root_id=w.id, round_id=r.id, token=token,
                       side="ASK", q0=q0, prior_support_id=w.seed["support"]["id"])
        else:
            M12.trade(self, token=token, side=side, price=price, quantity=quantity,
                      received=received, q0=q0, generation=generation)
        self.qualify_retest(token)

    def depth(self, token, side, price, quantity, received):
        M12.depth(self, token, side, price, quantity, received)
        # Ask qualification waits for book(), after this ordered Ask update
        # has also checked the original LW residual. A full snapshot is atomic.

    def book(self, token, asks, received, generation, covered, *, input_ref=None):
        super().book(token, asks, received, generation, covered, input_ref=input_ref)
        self.qualify_retest(token)

    def update_withdrawal(self, token, received):
        w = self.withdrawals.get(token)
        if w and w.key:
            loss, residual = self.withdrawal_values(w)
            if not (loss > 0 and residual / w.q0 >= self.p.lw_keep_residual_min
                    and residual / loss >= self.p.lw_keep_residual_loss_min):
                self.event(
                    "LW_MAINTENANCE_FAILED", id=w.id, token=token,
                    initial_ask_quantity=w.q0, loss=loss, cumulative_buy=w.buy,
                    residual=residual,
                    remaining_buy_capacity=min(
                        loss - self.p.lw_keep_residual_min * w.q0 - w.buy,
                        (D(1) - self.p.lw_keep_residual_loss_min) * loss - w.buy,
                    ),
                )
        super().update_withdrawal(token, received)

    def close(self, watermark, covered):
        super().close(watermark, covered)
        for k, (_, end) in tuple(self.buy_intervals.items()):
            if watermark > end + EPS:
                del self.buy_intervals[k]
                self.closed_buy_intervals[k] = (end, self.now)

    def finish_retest(self, w, component):
        s = w.seed["support"]
        if (component is None or component.side != "ASK" or w.qualified_key is None
                or component.price != w.levels[0] or component.token != w.token
                or component.generation != w.generation or s["id"] not in self.components
                or self.now + EPS >= min(w.key[0] + self.p.formation_after_lw_max, s["deadline"])):
            self.end_withdrawal(w.token, "FIRST_RETEST_FAILED_OR_EXPIRED")
            return
        identity = f"M15:retest:{w.id}"
        w.signal_id = identity
        signal = {
            "id": identity, "token": w.token, "root_id": w.id,
            "strategy": self.strategy, "version": self.version,
            "component_ids": [s["id"], component.id, w.id], "confirmed": self.now,
            "deadline": min(self.now + self.p.post_retest_ttl,
                            w.key[0] + self.p.lw_root_hard_ttl, s["deadline"]),
            "anchor_price": s["price"], "generation": w.generation,
            "prior_support": s, "bid": s, "ask": asdict(component), "lw": asdict(w),
            "quality_status": "PROVEN",
        }
        self.signals[identity] = signal
        self.event("SIGNAL_CONFIRMED", **signal)

    def invalidate(self, token, reason):
        super().invalidate(token, reason)
        for values in (self.buy_intervals, self.closed_buy_intervals):
            for k in tuple(values):
                if k[0] == token:
                    del values[k]

    def release(self):
        super().release()
        self.buy_intervals.clear()
        self.closed_buy_intervals.clear()
