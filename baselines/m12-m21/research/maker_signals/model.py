"""Immutable evidence identities and explicit validity; execution is a separate consumer."""

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any

D = Decimal
ZERO = D(0)
EPS = 1e-9


def decimal(value):
    result = D(str(value))
    if not result.is_finite():
        raise ValueError("NONFINITE_NUMBER")
    return result


@dataclass
class Book:
    token: str
    bids: dict = field(default_factory=dict)
    asks: dict = field(default_factory=dict)
    tick: Decimal = D(".01")
    generation: int = 0
    received: float = float("-inf")
    source: float = float("-inf")
    complete: bool = False
    tick_verified: bool = False

    @property
    def bid(self):
        return max(self.bids, default=ZERO)

    @property
    def ask(self):
        return min(self.asks, default=D(1))

    def legal(self, now, wall, generation):
        return (
            self.complete
            and self.tick_verified
            and self.generation == generation
            and ZERO < self.bid < self.ask < 1
            and -EPS <= now - self.received <= 0.200 + EPS
            and -0.002 - EPS <= wall - self.source <= 0.500 + EPS
        )

    def quantity(self, side, price):
        return (self.bids if side == "BID" else self.asks).get(price, ZERO)

    def opposing_depth(self, side):
        levels = self.bids if side == "SELL" else self.asks
        return sum((levels[p] for p in sorted(levels, reverse=side == "SELL")[:3]), ZERO)


@dataclass(frozen=True)
class Trade:
    identity: str
    token: str
    side: str
    price: Decimal
    quantity: Decimal
    received: float
    source: float
    available: float
    input_seq: int
    subindex: int
    origin_token: str
    mirrored: bool
    mapping_version: str = "complement-top3-identity-v1"


@dataclass(frozen=True)
class Fact:
    id: str
    kind: str
    token: str
    generation: int
    confirmed: float
    deadline: float
    start: float
    end: float
    input_seq: int
    available_seq: int
    anchor: Decimal | None = None
    refs: tuple = ()
    values: dict[str, Any] = field(default_factory=dict)


class Registry:
    def __init__(self, emit):
        self.emit = emit
        self.facts, self.endings = {}, {}
        self.counter = 0
        self.active = set()

    def add(
        self,
        *,
        kind,
        token,
        generation,
        confirmed,
        deadline,
        start,
        end,
        input_seq,
        anchor=None,
        refs=(),
        values=None,
    ):
        self.counter += 1
        identity = f"sop11:{generation}:{kind}:{token}:{self.counter}"
        f = Fact(
            identity,
            kind,
            token,
            generation,
            confirmed,
            deadline,
            start,
            end,
            input_seq,
            self.counter,
            anchor,
            tuple(refs),
            values or {},
        )
        self.facts[identity] = f
        if deadline > confirmed:
            self.active.add(identity)
        self.emit("FACT_CONFIRMED", asdict(f))
        return f

    def end(self, fact, now, reason, reason_class="HARD_INVALID"):
        identity = fact.id if isinstance(fact, Fact) else fact
        if identity not in self.endings:
            self.endings[identity] = {"at": now, "reason": reason, "reason_class": reason_class}
            self.active.discard(identity)
            self.emit("FACT_ENDED", {"id": identity, **self.endings[identity]})

    def valid(self, fact, now):
        if fact is None:
            return False
        f = self.facts[fact] if isinstance(fact, str) else fact
        end = self.endings.get(f.id)
        return f.confirmed <= now < f.deadline and (end is None or now < end["at"])

    def expire(self, now):
        for identity in tuple(self.active):
            f = self.facts[identity]
            if f.id not in self.endings and now >= f.deadline:
                self.end(f, f.deadline, "TTL_EXPIRED", "SOFT_EXPIRED")

    def invalidate_token(self, token, now, reason, reason_class="UNKNOWN"):
        for identity in tuple(self.active):
            f = self.facts[identity]
            if f.token == token and f.id not in self.endings:
                self.end(f, now, reason, reason_class)
