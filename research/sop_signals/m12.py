"""M12 baseline: strict local rounds, causal pairing and non-renewable guards.

No exchange client or execution dependency. Received times measure windows;
availability keys order publication. Call publish only at an atomic input boundary.
"""

import hashlib
import json
from collections import Counter
from dataclasses import asdict, dataclass
from decimal import Decimal

D = Decimal
EPS = 1e-9
VERSION = "m12-fixed-anchor-entry-v1/raw-path-r3"


@dataclass(frozen=True)
class Parameters:
    round_seconds: float = 0.5
    stable_seconds: float = 0.1
    stable_recovery: D = D(".50")
    bid_recovery: D = D(".50")
    ask_recovery: D = D(".30")
    ask_loss: D = D(".05")
    ratio_min: D = D(".25")
    ratio_max: D = D("4")
    pair_gap: float = 0.750
    ttl: float = 3.0


def parameter_identity(parameters):
    values = {k: str(v) if isinstance(v, D) else v for k, v in asdict(parameters).items()}
    fingerprint = hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()
    return values, fingerprint


def signal_version(parameters):
    return VERSION + "/p-" + parameter_identity(parameters)[1][:12]


@dataclass
class Round:
    id: str
    token: str
    side: str
    price: D
    generation: int
    start: float
    end: float
    q0: D
    qmin: D
    qend: D
    hits: D
    stable_since: float | None = None
    missing: bool = False
    post_low: D | None = None
    post_high: D | None = None
    post_missing: bool = False

    def depth(self, received, quantity, params):
        if received + EPS < self.start:
            return
        if received + EPS >= self.end:
            self.post_low = quantity if self.post_low is None else min(self.post_low, quantity)
            self.post_high = quantity if self.post_high is None else max(self.post_high, quantity)
            self.post_missing |= quantity <= 0
            return
        self.qend = quantity
        self.missing |= quantity <= 0
        if quantity < self.qmin:
            self.qmin, self.stable_since = quantity, None
        loss = self.q0 - self.qmin
        recovered = loss > 0 and quantity - self.qmin >= params.stable_recovery * loss
        if not recovered:
            self.stable_since = None
        elif self.stable_since is None:
            self.stable_since = received


@dataclass
class Component:
    id: str
    token: str
    side: str
    price: D
    generation: int
    key: tuple
    deadline: float
    window_end: float
    q0: D
    qmin: D
    hits: D
    loss: D
    u: D
    d: D
    r: D
    guard: D


class M12:
    strategy = "M12"

    def __init__(self, emit, parameters=None):
        self.emit = emit
        self.p = parameters or Parameters()
        self.version = signal_version(self.p)
        self.parameter_sha256 = parameter_identity(self.p)[1]
        self.now, self.key, self.serial = 0.0, (0.0, 0, -1), 0
        self.rounds, self.components, self.signals, self.last_close = {}, {}, {}, {}
        self.used_asks = set()
        self.counts = Counter()
        self.quality = {}

    def event(self, kind, **payload):
        self.counts[kind] += 1
        self.emit(
            kind,
            {
                "strategy": self.strategy,
                "version": self.version,
                "parameter_sha256": self.parameter_sha256,
                "available": self.now,
                "key": self.key,
                **payload,
            },
        )

    def clock(self, now, seq, subindex=-1):
        if now + EPS < self.now:
            raise ValueError("SOP_AVAILABLE_CLOCK_REGRESSION")
        self.now, self.key = now, (now, seq, subindex)
        for identity, c in tuple(self.components.items()):
            if now + EPS >= c.deadline:
                self.end_component(identity, "TTL_EXPIRED")
        self.last_close = {k: v for k, v in self.last_close.items() if v >= now - 4}

    def invalidate(self, token, reason):
        self.quality[token] = reason
        for k, r in tuple(self.rounds.items()):
            if r.token == token:
                self.event("ROUND_UNKNOWN", id=r.id, reason=reason)
                del self.rounds[k]
        for identity, c in tuple(self.components.items()):
            if c.token == token:
                self.end_component(identity, reason)
        self.event("QUALITY_UNKNOWN", token=token, reason=reason)

    def new_generation(self, token, generation):
        """A reconstructed book cannot inherit an old round/handoff clock."""
        if any(r.token == token for r in self.rounds.values()) or any(
            c.token == token for c in self.components.values()
        ):
            self.invalidate(token, "BOOK_RECONSTRUCTED")
        self.last_close = {k: v for k, v in self.last_close.items() if k[0] != token}
        self.quality.pop(token, None)
        self.event("BOOK_GENERATION_STARTED", token=token, generation=generation)

    def end_component(self, identity, reason):
        if self.components.pop(identity, None) is None:
            return
        self.used_asks.discard(identity)
        self.event("COMPONENT_ENDED", id=identity, reason=reason)
        for sid, s in tuple(self.signals.items()):
            if identity in s["component_ids"]:
                del self.signals[sid]
                self.event("SIGNAL_ENDED", id=sid, reason=reason)

    def depth(self, token, side, price, quantity, received):
        if received > self.now + EPS:
            raise ValueError("SOP_RECEIVED_AFTER_AVAILABLE")
        for r in self.rounds.values():
            if (r.token, r.side, r.price) == (token, side, price):
                r.depth(received, quantity, self.p)
        for identity, c in tuple(self.components.items()):
            if (c.token, c.side, c.price) != (token, side, price):
                continue
            if received + EPS < c.window_end:
                self.invalidate(token, "LATE_INPUT_TO_CLOSED_ROUND")
                break
            if quantity <= 0 or (quantity < c.guard if side == "BID" else quantity >= c.guard):
                self.end_component(identity, "ORIGINAL_LEVEL_GUARD")

    def round_duration(self, side):
        """Default COMMON observation window; specialised roles can freeze their own."""
        return self.p.round_seconds

    def trade(self, *, token, side, price, quantity, received, q0, generation):
        if received > self.now + EPS:
            raise ValueError("SOP_RECEIVED_AFTER_AVAILABLE")
        side = {"SELL": "BID", "BUY": "ASK"}[side]
        k = token, side, price
        r = self.rounds.get(k)
        if r is not None:
            if r.start - EPS <= received < r.end - EPS:
                r.hits += quantity
            return
        last = self.last_close.get(k)
        if q0 <= 0 or (last is not None and received <= last + EPS):
            self.event(
                "ROUND_NOT_STARTED",
                token=token,
                side=side,
                reason="NO_PRE_HIT_LEVEL_OR_NOT_NEW_RECEIPT",
            )
            return
        if len(self.rounds) >= 512:
            raise RuntimeError("SOP_ROUND_CAPACITY")
        self.serial += 1
        identity = f"{self.strategy}:{generation}:{token}:{side}:{self.serial}"
        self.rounds[k] = Round(
            identity,
            token,
            side,
            price,
            generation,
            received,
            received + self.round_duration(side),
            q0,
            q0,
            q0,
            quantity,
        )
        self.event(
            "ROUND_STARTED",
            id=identity,
            token=token,
            side=side,
            price=price,
            received=received,
            q0=q0,
            generation=generation,
        )

    def close(self, watermark, covered):
        for k, r in tuple(self.rounds.items()):
            if watermark <= r.end + EPS:
                continue
            del self.rounds[k]
            self.last_close[k] = self.now
            if self.now - r.end > 0.050 + EPS or not covered(r.token, r.start, r.end):
                self.event("ROUND_UNKNOWN", id=r.id, reason="LATE_CLOSE_OR_COVERAGE")
                continue
            loss = r.q0 - r.qmin
            hmin = max(D(".5"), D(".0001") * r.q0)
            qualified = not r.missing and loss >= hmin and r.hits >= hmin
            recovery = max(D(0), min(D(1), (r.qend - r.qmin) / loss)) if loss else D(0)
            stable = (
                qualified
                and r.stable_since is not None
                and r.end - r.stable_since + EPS >= self.p.stable_seconds
                and recovery >= self.p.stable_recovery
            )
            # A later failed qualified BID round / stable ASK invalidates before pairing.
            for identity, c in tuple(self.components.items()):
                if (
                    (c.token, c.side, c.price) == k
                    and qualified
                    and ((r.side == "BID" and not stable) or (r.side == "ASK" and stable))
                ):
                    self.end_component(identity, "SUBSEQUENT_ROUND_COUNTEREVIDENCE")
            self.event(
                "ROUND_CLOSED",
                **asdict(r),
                loss=loss,
                recovery=recovery,
                qualified=qualified,
                stable=stable,
            )
            if not qualified:
                continue
            is_bid = r.side == "BID"
            if not (
                (stable and recovery >= self.p.bid_recovery)
                if is_bid
                else (recovery <= self.p.ask_recovery and loss / r.q0 >= self.p.ask_loss)
            ):
                continue
            guard = r.qmin + (D(".40") if is_bid else D(".50")) * loss
            if (
                r.post_missing
                or (is_bid and r.post_low is not None and r.post_low < guard)
                or (not is_bid and r.post_high is not None and r.post_high >= guard)
            ):
                self.event("PUBLICATION_REJECTED", id=r.id, reason="POST_WINDOW_GUARD")
                continue
            c = Component(
                r.id,
                r.token,
                r.side,
                r.price,
                r.generation,
                self.key,
                self.now + self.p.ttl,
                r.end,
                r.q0,
                r.qmin,
                r.hits,
                loss,
                r.hits / r.q0,
                loss / r.q0,
                recovery,
                guard,
            )
            self.components[c.id] = c
            self.event("COMPONENT_CONFIRMED", **asdict(c))
        # Used roots stay consumed while their component can still be referenced.

    def publish(self):
        pairs = []
        for a in self.components.values():
            if a.side != "ASK" or a.id in self.used_asks:
                continue
            for b in self.components.values():
                if (
                    b.side != "BID"
                    or b.token != a.token
                    or b.generation != a.generation
                    or b.price >= a.price
                    or abs(a.key[0] - b.key[0]) > self.p.pair_gap + EPS
                ):
                    continue
                if all(
                    self.p.ratio_min <= ratio <= self.p.ratio_max
                    for ratio in (a.u / b.u, a.d / b.d)
                ):
                    pairs.append(((max(a.key, b.key), a.key, b.key), a, b))
        for _, a, b in sorted(pairs, key=lambda p: p[0]):
            if a.id in self.used_asks:
                continue
            deadline = min(a.deadline, b.deadline)
            if self.now + EPS >= deadline:
                continue
            self.used_asks.add(a.id)
            identity = f"{self.strategy}:pair:{a.id}"
            s = {
                "id": identity,
                "token": a.token,
                "root_id": a.id,
                "version": self.version,
                "component_ids": [b.id, a.id],
                "confirmed": self.now,
                "deadline": deadline,
                "anchor_price": b.price,
                "bid": asdict(b),
                "ask": asdict(a),
                "quality_status": "PROVEN",
            }
            self.signals[identity] = s
            self.event("SIGNAL_CONFIRMED", **s)

    def release(self):
        for values in (
            self.rounds,
            self.components,
            self.signals,
            self.last_close,
            self.used_asks,
            self.quality,
        ):
            values.clear()
