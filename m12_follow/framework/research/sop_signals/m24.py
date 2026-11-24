"""M24 fixed-price, two-sided replenishment signal, with no order transport."""

from collections import Counter
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from hashlib import sha256
import json

from .m12 import EPS

SOP_VERSION = "m24-two-sided-net-replenishment-v1"
ALIGNMENT_VERSION = "strict-per-level-source-v1"
VARIANT = "M24_TWO_SIDED_NET_REPLENISHMENT"
SOP_FILE = "M24.md"
SOP_SHA256 = "dee3a91f24670823e92e985fafe9a9d306e8c3c195d9489b48cde17e70e1ea1d"
DOCUMENT_PARAMETERS_SHA256 = "0fa7cdeb73aa70c0581e13648d9d971c88e537d3922d4cdc62830db6cf0c9367"
CLOSE_CAP = 0.050
SIGNAL_TTL = 3.0


@dataclass(frozen=True)
class Parameters:
    name: str = "sop"
    window_ms: int = 500
    minimum_shares: str = "0.5"
    depth_fraction: str = "0.0001"

    def __post_init__(self):
        if not 0 < self.window_ms <= 1000:
            raise ValueError("M24_FORMATION_WINDOW")
        if any(not Decimal(0) < Decimal(x) <= 1
               for x in (self.minimum_shares, self.depth_fraction)):
            raise ValueError("M24_MATERIALITY")

    @property
    def digest(self):
        return sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()


@dataclass
class Level:
    price: Decimal
    initial: Decimal
    initial_ref: tuple
    shares: Decimal = Decimal(0)
    last_hit: tuple | None = None
    point: tuple | None = None

    def aligned(self):
        return self.point is not None and self.point[1] > self.last_hit[1] + EPS


@dataclass
class Root:
    id: str
    token: str
    generation: int
    start: float
    end: float
    first: tuple
    bid: Level
    ask: Level
    formation: dict | None = None
    published: bool = False
    pending_since: float | None = None
    changed: bool = False


class M24:
    def __init__(self, emit, parameters):
        self.emit = emit
        self.parameters = parameters
        self.version = f"{SOP_VERSION}/{ALIGNMENT_VERSION}/p-{parameters.digest[:12]}"
        self.minimum = Decimal(parameters.minimum_shares)
        self.fraction = Decimal(parameters.depth_fraction)
        self.roots = {}
        self.ended_at = {}
        self.counts = Counter()
        self.now = 0.0
        self.key = (0.0, 0, -1)

    def clock(self, now, seq, subindex):
        self.now, self.key = now, (now, seq, subindex)
        for token, root in tuple(self.roots.items()):
            if now >= root.end + SIGNAL_TTL:
                self.end(token, "TTL_EXPIRED", unknown=root.pending_since is not None)
            elif root.pending_since is not None and now >= root.pending_since + CLOSE_CAP:
                self.end(token, "MAINTENANCE_PATH_UNPROVEN", unknown=True)
            elif not root.published and now > root.end + CLOSE_CAP:
                self.end(token, "PUBLICATION_WATERMARK_LATE", unknown=True)

    def end(self, token, reason, *, unknown=False):
        root = self.roots.pop(token, None)
        if root is None:
            return
        self.ended_at[token] = self.key
        self.counts[("UNKNOWN:" if unknown else "END:") + reason] += 1
        if root.published:
            self.emit("SIGNAL_ENDED", {"id": root.id, "reason": reason, "unknown": unknown})

    def start(self, token, generation, received, source, ref, bid, ask, book_source):
        prior = self.ended_at.get(token)
        if prior is not None and (received <= prior[0] or self.key <= prior):
            return None
        if book_source >= source - EPS or any(
            point is None or point[0] > received + EPS or point[1] >= source - EPS
            for _, _, point in (bid, ask)
        ):
            self.counts["UNKNOWN:PRE_TRADE_FIXED_LEVEL_ORDER_UNPROVEN"] += 1
            self.ended_at[token] = self.key
            return None
        root_id = ":".join(map(str, ("M24", generation, token, bid[0], ask[0], *ref)))
        root = Root(root_id, token, generation, received,
                    received + self.parameters.window_ms / 1000, ref, Level(*bid), Level(*ask))
        self.roots[token] = root
        self.counts["ROOT_STARTED"] += 1
        return root

    def trade(self, token, side, price, quantity, received, source, ref,
              generation, bid, ask, book_source):
        root = self.roots.get(token)
        if root is None:
            expected = bid if side == "SELL" else ask
            if price != expected[0]:
                return
            self.counts["TRIGGER"] += 1
            root = self.start(token, generation, received, source, ref, bid, ask, book_source)
            if root is None:
                return
        level = root.bid if side == "SELL" else root.ask
        if price != level.price:
            return
        # A same-source-time print and already-available absolute depth have
        # no proved order. A later snapshot cannot fix that intermediate order.
        if level.point and source <= level.point[1] + EPS:
            self.end(token, "HIT_AND_FIXED_DEPTH_ORDER_UNPROVEN", unknown=True)
            return
        if received >= root.end and root.formation is None:
            self.end(token, "FORMATION_WATERMARK_UNPROVEN", unknown=True)
            return
        level.shares += quantity
        level.last_hit = (received, source, ref)
        root.changed = True

    def depth(self, token, side, price, quantity, received, source, ref):
        root = self.roots.get(token)
        if root is None:
            return
        level = root.bid if side == "BID" else root.ask
        if price != level.price:
            return
        if quantity == 0:
            self.end(token, "FIXED_LEVEL_REMOVED")
        elif (level.point and source <= level.point[1] + EPS
              and quantity != level.point[3]):
            self.end(token, "FIXED_DEPTH_ORDER_UNPROVEN", unknown=True)
        elif received >= root.end and root.formation is None:
            self.end(token, "FORMATION_WATERMARK_UNPROVEN", unknown=True)
        else:
            level.point = (received, source, ref, quantity)
            root.changed = True

    def calculate(self, root):
        bid, ask = root.bid, root.ask
        hb, ha = bid.shares, ask.shares
        qb, qa = bid.point[3], ask.point[3]
        jb, ja = qb - bid.initial + hb, qa - ask.initial + ha
        flags = (jb > 0, jb > ja, jb * ha > ja * hb)
        numbers = dict(B0=bid.initial, A0=ask.initial, QB=qb, QA=qa,
                       HB=hb, HA=ha, JB=jb, JA=ja, bid_rate=jb / hb, ask_rate=ja / ha)
        return dict(values={k: str(v) for k, v in numbers.items()},
                    flags=flags, ask_net_sign=(ja > 0) - (ja < 0),
                    bid_initial=bid.initial_ref, ask_initial=ask.initial_ref,
                    bid_hit=bid.last_hit, ask_hit=ask.last_hit,
                    bid_endpoint=bid.point[:3], ask_endpoint=ask.point[:3])

    def close(self, watermark, covered):
        for token, root in tuple(self.roots.items()):
            if root.formation is not None or watermark < root.end:
                continue
            self.counts["FORMATION_REACHED"] += 1
            if not covered(token, root.start, root.end):
                self.end(token, "FORMATION_COVERAGE_UNKNOWN", unknown=True)
            elif any(level.shares < max(self.minimum, self.fraction * level.initial)
                     for level in (root.bid, root.ask)):
                self.end(token, "DUAL_MATERIALITY_FAILED")
            else:
                self.counts["DUAL_MATERIALITY_PASSED"] += 1
                if not all(level.aligned() for level in (root.bid, root.ask)):
                    self.end(token, "FORMATION_ENDPOINT_UNALIGNED", unknown=True)
                    continue
                self.counts["FORMATION_ENDPOINT_ALIGNED"] += 1
                outcome = self.calculate(root)
                if all(outcome["flags"]):
                    self.counts["FORMATION_PREDICATE_PASSED"] += 1
                    root.formation, root.changed = outcome, False
                else:
                    self.end(token, "NET_REPLENISHMENT_COMPARISON_FAILED")

    def finish_boundary(self, token):
        root = self.roots.get(token)
        if root is None or root.formation is None or not root.changed:
            return
        root.changed = False
        if root.pending_since is not None:
            # A subsequent independently observed endpoint cannot prove the
            # missing earlier boundary. This input version carries no explicit
            # exchange atomic trade/depth batch linkage for such recovery.
            return
        if not all(level.aligned() for level in (root.bid, root.ask)):
            if not root.published:
                self.end(token, "PREPUBLICATION_PATH_UNPROVEN", unknown=True)
            else:
                root.pending_since = self.now
                self.counts["MAINTENANCE_SUSPENDED"] += 1
                self.emit("SIGNAL_SUSPENDED", {"id": root.id, "available": self.now})
        elif not all(self.calculate(root)["flags"]):
            self.end(token, "MAINTENANCE_COMPARISON_FAILED")

    def publish(self, watermark, covered):
        for token, root in tuple(self.roots.items()):
            if root.published or root.formation is None or watermark < root.end:
                continue
            if watermark < self.now:
                # A received but unprocessed frame may contain a veto.
                # Use the first boundary with the availability prefix drained.
                continue
            if not covered(token, root.start, self.now):
                self.end(token, "PREPUBLICATION_COVERAGE_UNKNOWN", unknown=True)
                continue
            root.published = True
            self.counts["SIGNAL_CONFIRMED"] += 1
            self.emit("SIGNAL_CONFIRMED", {
                "id": root.id, "token": token, "generation": root.generation,
                "price": str(root.bid.price), "ask": str(root.ask.price),
                "first": root.first, "t0": root.start, "e": root.end, "c": self.now,
                "d": root.end + SIGNAL_TTL, "key": self.key, "version": self.version,
                "parameter_sha256": self.parameters.digest, **root.formation,
            })


class Sweep:
    """Share the same verified input boundaries across frozen parameter sets."""

    def __init__(self, emit, parameters):
        self.variants = [M24(
            lambda event, payload, name=p.name: emit(event, {"candidate": name, **payload}), p
        ) for p in parameters]
        self.watermark = self.covered = None

    def clock(self, now, seq, subindex=-1):
        for model in self.variants:
            model.clock(now, seq, subindex)

    def invalidate(self, token, reason):
        for model in self.variants:
            model.end(token, reason, unknown=True)

    def new_generation(self, token, generation):
        self.invalidate(token, "BOOK_GENERATION_CHANGED")

    def depth(self, token, side, price, quantity, received, *, input_ref, source, **_):
        for model in self.variants:
            model.depth(token, side, price, quantity, received, source, input_ref)

    def level_missing(self, token, side, price, received, *, source, frame, row):
        self.depth(token, side, price, Decimal(0), received,
                   input_ref=(frame, row, None), source=source)

    def trade(self, **trade):
        for model in self.variants:
            model.trade(**trade)

    def finish_boundary(self, token):
        for model in self.variants:
            model.finish_boundary(token)

    def close(self, watermark, covered):
        self.watermark, self.covered = watermark, covered
        for model in self.variants:
            model.close(watermark, covered)

    def publish(self):
        for model in self.variants:
            model.publish(self.watermark, self.covered)

    def release(self):
        for model in self.variants:
            model.roots.clear()
            model.ended_at.clear()


class ExecutionFeed(Sweep):
    """Expose only the frozen single variant to the simulation order policy."""

    def __init__(self, emit, parameters):
        self.signals = {}
        self.emit = emit
        super().__init__(self.signal_event, (parameters,))

    def signal_event(self, event, payload):
        fact = dict(payload)
        signal_id = fact["id"]
        if event == "SIGNAL_CONFIRMED":
            fact.update(anchor_price=fact["price"], confirmed=fact["c"], deadline=fact["d"],
                        can_reference_for_new_action=True, variant_id=VARIANT)
            self.signals[signal_id] = fact
        elif event == "SIGNAL_SUSPENDED":
            # This input version cannot recover the unproved earlier boundary.
            signal = self.signals[signal_id]
            self.signals[signal_id] = {**signal, "can_reference_for_new_action": False}
            fact["token"] = signal["token"]
        elif event == "SIGNAL_ENDED":
            signal = self.signals.pop(signal_id)
            fact.update(token=signal["token"], available=self.variants[0].now)
        self.emit(event, fact)

    def release(self):
        self.signals.clear()
        super().release()
