"""M14: two first-attempt weak Ask rounds, then current strict Bid support.

All facts come from the source-preserving consumer. Publication is deferred to
the complete parent frame so neither a guard handoff nor pairing hides a later
invalidation in that frame. Execution and settlement are independent consumers.
"""

from dataclasses import asdict, dataclass
from decimal import Decimal as D

from .m12 import EPS, M12, Component, parameter_identity

VERSION = "m14-fixed-anchor-entry-v1/raw-path-r1"


@dataclass(frozen=True)
class Parameters:
    round_seconds: float = .5
    stable_seconds: float = .1
    stable_recovery: D = D(".50")
    bid_recovery: D = D(".50")
    ask_recovery: D = D(".30")
    ask_loss: D = D(".05")
    second_recovery_increase: D = D(".10")
    ratio_min: D = D(".50")
    ratio_max: D = D("2.00")
    ask_gap: float = 2.0
    pair_gap: float = 1.5
    ttl: float = 3.0


def signal_version(parameters):
    profile = "/ask-window-bid500-stable100-v2" if parameters.round_seconds != .5 else ""
    return VERSION + profile + "/p-" + parameter_identity(parameters)[1][:12]


def observation_windows(parameters):
    return {"ASK_seconds": parameters.round_seconds, "BID_seconds": .5,
            "stable_hold_seconds": parameters.stable_seconds,
            "scope": "SHORT_ASK_WINDOW_WITH_ORIGINAL500MS_BID_AND100MS_STABILITY"}


@dataclass
class WeakSupply:
    first: Component
    phase: str = "PENDING_A1"
    second_round_id: str | None = None
    second: Component | None = None
    signal_id: str | None = None


class M14(M12):
    strategy = "M14"

    def __init__(self, emit, parameters=None):
        if parameters is not None and not isinstance(parameters, Parameters):
            raise ValueError("M14_PARAMETERS_REQUIRED")
        if parameters is not None and parameters.round_seconds <= parameters.stable_seconds:
            raise ValueError("M14_ASK_WINDOW_MUST_ALLOW_POSITIVE_STABLE_RECOVERY_MARGIN")
        super().__init__(emit, parameters or Parameters())
        self.version = signal_version(self.p)
        self.roots = {}
        self.pending_bids = set()
        # An attempted A2 stays consumed even if its A1 dies before it closes.
        self.second_rounds = {}

    def round_duration(self, side):
        return .5 if side == "BID" else self.p.round_seconds

    def end_root(self, key, reason):
        root = self.roots.pop(key, None)
        if root is None:
            return
        self.event("ASK_ROOT_ENDED", root_id=root.first.id, phase=root.phase,
                   second_round_id=root.second_round_id, reason=reason)
        if root.signal_id and self.signals.pop(root.signal_id, None) is not None:
            self.event("SIGNAL_ENDED", id=root.signal_id, reason=reason)

    def unknown_root(self, key, reason):
        root = self.roots.get(key)
        if root is not None:
            self.event("ROUND_UNKNOWN", id=root.first.id, token=root.first.token,
                       stage=root.phase, reason=reason)
            self.end_root(key, reason)

    def clock(self, now, seq, subindex=-1):
        super().clock(now, seq, subindex)
        for key, root in tuple(self.roots.items()):
            if root.phase == "PENDING_A1":
                if now - root.first.window_end > .050 + EPS:
                    self.unknown_root(key, "A1_PUBLICATION_LATE")
            elif root.phase in {"WAIT_A2", "PENDING_A2"}:
                if now + EPS >= root.first.key[0] + self.p.ask_gap:
                    self.end_root(key, "A2_FORMATION_EXPIRED")
            elif now + EPS >= root.second.deadline:
                self.end_root(key, "ASK2_TTL_EXPIRED")

    def end_component(self, identity, reason):
        if identity in self.pending_bids:
            self.pending_bids.discard(identity)
            if self.components.pop(identity, None) is not None:
                self.event("PUBLICATION_REJECTED", id=identity, reason=reason)
        else:
            super().end_component(identity, reason)
        for key, root in tuple(self.roots.items()):
            if root.signal_id and root.signal_id not in self.signals:
                self.end_root(key, reason)

    def invalidate(self, token, reason):
        super().invalidate(token, reason)
        for key, root in tuple(self.roots.items()):
            if root.first.token == token:
                self.end_root(key, reason)
        self.second_rounds = {rid: owner for rid, owner in self.second_rounds.items() if owner != token}

    def new_generation(self, token, generation):
        if any(root.first.token == token for root in self.roots.values()):
            self.invalidate(token, "BOOK_RECONSTRUCTED")
        super().new_generation(token, generation)

    def depth(self, token, side, price, quantity, received):
        super().depth(token, side, price, quantity, received)
        if side != "ASK":
            return
        key = token, price
        root = self.roots.get(key)
        if root is None:
            return
        guards = [root.first] if root.phase in {"PENDING_A1", "WAIT_A2", "PENDING_A2"} else []
        if root.second is not None:
            guards.append(root.second)
        for guard in guards:
            if received + EPS < guard.window_end:
                self.invalidate(token, "LATE_INPUT_TO_CLOSED_ASK_ROUND")
                return
            if quantity <= 0 or quantity >= guard.guard:
                self.end_root(key, "ASK_SUPPLY_CEILING_OR_LEVEL_LOST")
                return

    def trade(self, *, token, side, price, quantity, received, q0, generation):
        k = token, "ASK" if side == "BUY" else "BID", price
        root = self.roots.get((token, price)) if side == "BUY" else None
        reference = (root.second or root.first) if root else None
        closed = ([reference] if reference else []) + [
            c for c in self.components.values() if (c.token, c.side, c.price) == k
        ]
        if any(received + EPS < c.window_end for c in closed):
            self.invalidate(token, "LATE_TRADE_TO_CLOSED_ROUND")
            return
        starting = k not in self.rounds
        super().trade(token=token, side=side, price=price, quantity=quantity,
                      received=received, q0=q0, generation=generation)
        root = self.roots.get((token, price)) if side == "BUY" else None
        current = self.rounds.get(k)
        if (root and root.phase == "WAIT_A2" and root.second_round_id is None
                and starting and current is not None and current.start == received
                and generation == root.first.generation
                and received > root.first.key[0] + EPS and self.key > root.first.key):
            root.second_round_id = current.id
            self.second_rounds[current.id] = token
            self.event("ASK2_STARTED", root_id=root.first.id, round_id=current.id,
                       received=received, first_confirmed_key=root.first.key)

    def component(self, r, loss, recovery):
        return Component(r.id, r.token, r.side, r.price, r.generation,
                         self.key, self.now + self.p.ttl, r.end, r.q0, r.qmin,
                         r.hits, loss, r.hits / r.q0, loss / r.q0, recovery,
                         r.qmin + (D(".40") if r.side == "BID" else D(".50")) * loss)

    @staticmethod
    def post_window_guard_proven(r, c):
        return not (r.post_missing
                    or (r.side == "BID" and r.post_low is not None and r.post_low < c.guard)
                    or (r.side == "ASK" and r.post_high is not None and r.post_high >= c.guard))

    def close(self, watermark, covered):
        for k, r in tuple(self.rounds.items()):
            if watermark <= r.end + EPS:
                continue
            del self.rounds[k]
            self.last_close[k] = self.now
            was_second = self.second_rounds.pop(r.id, None) is not None
            ask_key = r.token, r.price
            if self.now - r.end > .050 + EPS or not covered(r.token, r.start, r.end):
                self.event("ROUND_UNKNOWN", id=r.id, reason="LATE_CLOSE_OR_COVERAGE")
                for identity, component in tuple(self.components.items()):
                    if (component.token, component.side, component.price) == k:
                        self.end_component(identity, "SUBSEQUENT_ROUND_UNKNOWN")
                if r.side == "ASK":
                    self.end_root(ask_key, "ASK_ROUND_UNKNOWN")
                continue
            loss = r.q0 - r.qmin
            minimum = max(D(".5"), D(".0001") * r.q0)
            qualified = not r.missing and loss >= minimum and r.hits >= minimum
            recovery = max(D(0), min(D(1), (r.qend - r.qmin) / loss)) if loss else D(0)
            stable = (qualified and r.stable_since is not None
                      and r.end - r.stable_since + EPS >= self.p.stable_seconds
                      and recovery >= self.p.stable_recovery)
            if r.side == "BID" and qualified and not stable:
                for identity, component in tuple(self.components.items()):
                    if (component.token, component.side, component.price) == k:
                        self.end_component(identity, "SUBSEQUENT_ROUND_COUNTEREVIDENCE")
            if r.side == "ASK" and qualified and stable:
                self.end_root(ask_key, "SUBSEQUENT_STABLE_ASK")
            self.event("ROUND_CLOSED", **asdict(r), loss=loss, recovery=recovery,
                       qualified=qualified, stable=stable)
            if r.side == "BID":
                if not (stable and recovery >= self.p.bid_recovery):
                    continue
                component = self.component(r, loss, recovery)
                if not self.post_window_guard_proven(r, component):
                    self.event("PUBLICATION_REJECTED", id=r.id, reason="POST_WINDOW_GUARD")
                    continue
                self.components[r.id] = component
                self.pending_bids.add(r.id)
                continue
            weak = qualified and recovery <= self.p.ask_recovery and loss / r.q0 >= self.p.ask_loss
            root = self.roots.get(ask_key)
            if was_second:
                if root is None or root.second_round_id != r.id:
                    continue
                if not (weak and self.now + EPS < root.first.key[0] + self.p.ask_gap):
                    self.end_root(ask_key, "FIRST_A2_FAILED_OR_EXPIRED")
                    continue
                second = self.component(r, loss, recovery)
                first = root.first
                if not (all(self.p.ratio_min <= ratio <= self.p.ratio_max
                            for ratio in (second.u / first.u, second.d / first.d))
                        and second.r <= first.r + self.p.second_recovery_increase):
                    self.end_root(ask_key, "FIRST_A2_STRENGTH_COMPARISON_FAILED")
                    continue
                if not self.post_window_guard_proven(r, second):
                    self.end_root(ask_key, "A2_POST_WINDOW_GUARD")
                    continue
                root.second, root.phase = second, "PENDING_A2"
                self.event("ASK2_FORMED", root_id=first.id, round_id=r.id,
                           publication_pending=True, **asdict(second))
            elif root is None and weak:
                first = self.component(r, loss, recovery)
                if not self.post_window_guard_proven(r, first):
                    self.event("PUBLICATION_REJECTED", id=r.id, reason="POST_WINDOW_GUARD")
                    continue
                self.roots[ask_key] = WeakSupply(first)
                self.event("ASK1_FORMED", root_id=first.id, publication_pending=True, **asdict(first))
        if len(self.components) + len(self.roots) > 512:
            raise RuntimeError("SOP_COMPONENT_CAPACITY")

    def publish(self):
        for identity in tuple(self.pending_bids):
            component = self.components.get(identity)
            if component is None:
                self.pending_bids.discard(identity)
                continue
            if self.now - component.window_end > .050 + EPS:
                self.event("ROUND_UNKNOWN", id=identity, reason="BID_PUBLICATION_LATE")
                self.end_component(identity, "BID_PUBLICATION_LATE")
                continue
            self.pending_bids.discard(identity)
            component.key, component.deadline = self.key, self.now + self.p.ttl
            self.event("COMPONENT_CONFIRMED", **asdict(component))
        for key, root in tuple(self.roots.items()):
            if root.phase == "PENDING_A1":
                if self.now - root.first.window_end > .050 + EPS:
                    self.unknown_root(key, "A1_PUBLICATION_LATE")
                    continue
                root.first.key = self.key
                root.first.deadline = self.now + self.p.ask_gap
                root.phase = "WAIT_A2"
                self.event("ASK1_CONFIRMED", root_id=root.first.id, **asdict(root.first))
            elif root.phase == "PENDING_A2":
                if self.now + EPS >= root.first.key[0] + self.p.ask_gap:
                    self.end_root(key, "A2_FORMATION_EXPIRED")
                    continue
                if self.now - root.second.window_end > .050 + EPS:
                    self.unknown_root(key, "A2_PUBLICATION_LATE")
                    continue
                root.second.key, root.second.deadline = self.key, self.now + self.p.ttl
                root.phase = "WAIT_BID"
                self.event("ASK2_CONFIRMED", root_id=root.first.id, **asdict(root.second),
                           previous_ceiling=root.first.guard, new_ceiling=root.second.guard)
            if root.phase != "WAIT_BID":
                continue
            second = root.second
            latest = {}
            for bid in self.components.values():
                if bid.token != second.token or bid.generation != second.generation:
                    continue
                old = latest.get(bid.price)
                if old is None or bid.key > old.key:
                    latest[bid.price] = bid
            eligible = [bid for bid in latest.values() if bid.price < second.price
                        and abs(second.key[0] - bid.key[0]) <= self.p.pair_gap + EPS
                        and self.now + EPS < min(second.deadline, bid.deadline)]
            if not eligible:
                continue
            earliest = min((max(second.key, bid.key), bid.key) for bid in eligible)
            choices = [bid for bid in eligible if (max(second.key, bid.key), bid.key) == earliest]
            if len(choices) != 1:
                self.unknown_root(key, "NON_UNIQUE_EARLIEST_BID_SELECTION")
                continue
            bid = choices[0]
            identity = "M14:pair:" + root.first.id
            signal = {"id": identity, "token": second.token, "root_id": root.first.id,
                      "version": self.version, "component_ids": [bid.id, root.first.id, second.id],
                      "confirmed": self.now, "deadline": min(second.deadline, bid.deadline),
                      "anchor_price": bid.price, "bid": asdict(bid), "ask": asdict(second),
                      "ask1": asdict(root.first), "ask2": asdict(second), "quality_status": "PROVEN"}
            root.signal_id, root.phase = identity, "SIGNAL"
            self.signals[identity] = signal
            self.event("SIGNAL_CONFIRMED", **signal)

    def release(self):
        super().release()
        self.roots.clear()
        self.pending_bids.clear()
        self.second_rounds.clear()
