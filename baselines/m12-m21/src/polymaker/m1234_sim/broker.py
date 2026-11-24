"""Exchange state, shared FIFO queues, and exact cash/inventory conservation.

The public book is never modified. External queue position is an assumption at
local availability time, not a measurement of exchange order-level priority.
"""

from __future__ import annotations

import heapq
import math
from collections import defaultdict
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from decimal import Decimal

from polymaker.m1234_sim.events import digest

ZERO = Decimal(0)
TERMINAL = {"FILLED", "CANCELLED", "REJECTED"}
ATTRIBUTION_WINDOW = 1.0
ATTRIBUTION_EPSILON = 1e-6
MAX_ATTRIBUTION_RECORDS = 16384
CONSTRAINT_CONFLICT = "SIM_CONSTRAINT_ORDERING_CONTRADICTS_KNOWN_FILL"


class ConstraintConflict(ValueError):
    """A recorded fill contradicts an authoritative historical constraint."""

    def __init__(self, conflicts):
        super().__init__(CONSTRAINT_CONFLICT)
        self.conflicts = conflicts


def dec(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("SIM_NONFINITE_AMOUNT")
    return result


@dataclass(frozen=True)
class OrderIntent:
    profile: str
    strategy: str
    signal_version: str
    opportunity: str
    market: str
    token: str
    outcome: str
    price: str
    quantity: str
    available: float
    deadline: float
    lifetime: str
    retry_of: str | None = None
    order_type: str = "POST_ONLY_GTC"
    side: str = "BUY"


@dataclass
class Order:
    id: str
    intent: OrderIntent
    state: str = "SUBMITTING"
    client: str = "PENDING"
    filled: Decimal = ZERO
    reserved: Decimal = ZERO
    spent: Decimal = ZERO
    uncertain: bool = False
    live_at: float | None = None
    first_fill: float | None = None
    last_fill: float | None = None
    cancel_requested: float | None = None
    ack_at: float | None = None
    arrival_at: float | None = None
    arrival_constraint: dict | None = None
    reject_reason: str | None = None

    @property
    def remaining(self):
        return dec(self.intent.quantity) - self.filled


@dataclass
class Account:
    initial: Decimal = Decimal(20)
    cash: Decimal = Decimal(20)
    reserved: Decimal = ZERO
    spent: Decimal = ZERO
    payout: Decimal = ZERO
    inventory: dict = field(default_factory=dict)
    winner: str | None = None


class SimBroker:
    def __init__(
        self,
        scenario,
        emit,
        *,
        post_ms=100,
        ack_ms=200,
        cancel_ms=200,
        ahead_cancel_fraction="0",
        seed=1234,
        initial_cash="20",
    ):
        if min(post_ms, ack_ms, cancel_ms) < 0 or not 0 <= dec(ahead_cancel_fraction) <= 1:
            raise ValueError("SIM_INVALID_SCENARIO")
        self.scenario, self.emit = scenario, emit
        self.post_delay, self.ack_delay, self.cancel_delay = (
            x / 1000 for x in (post_ms, ack_ms, cancel_ms)
        )
        self.cancel_fraction = dec(ahead_cancel_fraction)
        self.seed = seed  # v1 has no stochastic draw; explicit for future models.
        self.initial_cash = dec(initial_cash)
        if self.initial_cash < 0:
            raise ValueError("SIM_INVALID_INITIAL_CASH")
        self.orders, self.accounts, self.queues = {}, {}, {}
        self.timers, self.timer_seq, self.now = [], 0, 0.0
        self.books, self.book_sources, self.source_floor = {}, {}, {}
        self.receipts = []
        self.seen_trades, self.ambiguous = {}, []
        self.trade_credits, self.depth_decreases = [], []
        self.attribution_frontier = None
        self.managed_frontier = False
        self.input_seq = self.input_received = None
        self.constraints, self.managed_constraints = {}, False
        self.constraint_conflicts = {}

    def event(self, kind, **data):
        self.emit(kind, {"scenario": self.scenario, "at": self.now, **data})

    def account(self, profile):
        if profile not in self.accounts:
            self.accounts[profile] = Account(initial=self.initial_cash, cash=self.initial_cash)
            self.event("ACCOUNT_OPEN", profile=profile, initial_cash=str(self.initial_cash))
        return self.accounts[profile]

    @staticmethod
    def queue_key(order):
        return (order.intent.profile, order.intent.token, str(dec(order.intent.price).normalize()))

    def schedule(self, when, kind, oid):
        self.timer_seq += 1
        heapq.heappush(self.timers, (when, self.timer_seq, kind, oid))

    def submit(self, intent, oid=None, *, fault=None):
        price, size = dec(intent.price), dec(intent.quantity)
        if (
            intent.side != "BUY"
            or intent.order_type != "POST_ONLY_GTC"
            or not 0 < price < 1
            or size <= 0
        ):
            raise ValueError("SIM_INTENT_INVALID")
        oid = oid or digest({"intent": asdict(intent), "physical": len(self.orders)})
        if oid in self.orders:
            raise ValueError("SIM_DUPLICATE_PHYSICAL_ORDER")
        account, cost = self.account(intent.profile), price * size
        if cost > account.cash - account.reserved:
            raise ValueError("SIM_INSUFFICIENT_CASH")
        order = Order(oid, intent, reserved=cost)
        self.orders[oid] = order
        account.reserved += cost
        self.event("SUBMIT", order=oid, intent=asdict(intent), reserve=str(cost), fault=fault)
        self.schedule(
            self.now + self.post_delay,
            "REJECT" if fault in {"REJECT", "CROSS_REJECT"} else "LIVE",
            oid,
        )
        self.schedule(self.now + self.ack_delay, "TIMEOUT" if fault == "UNKNOWN" else "ACK", oid)
        if fault == "CROSS_REJECT":
            order.client = "CROSS_REJECT_PENDING"
            order.reject_reason = "POST_ONLY_CROSS"
        elif fault == "REJECT":
            order.reject_reason = "INJECTED_EXPLICIT_REJECTION"
        self.check()
        return order

    def advance(self, now, *, delivery=False, attribution_frontier=None):
        if now < self.now:
            raise ValueError("SIM_BROKER_TIME_REGRESSION")
        if attribution_frontier is not None:
            if (
                self.attribution_frontier is not None
                and attribution_frontier + ATTRIBUTION_EPSILON < self.attribution_frontier
            ):
                raise ValueError("SIM_ATTRIBUTION_WATERMARK_REGRESSION")
            self.managed_frontier = True
            self.attribution_frontier = attribution_frontier
        elif not self.managed_frontier:
            # Direct synchronous callers assert that all inputs through now
            # have been delivered. Engine always supplies an explicit frontier.
            self.attribution_frontier = now
        # Exchange actions precede the arriving public frame at equal time.
        # Client receipts are delivered only by recorded TIMER/POLL actions.
        while self.timers and self.timers[0][0] <= now:
            due, _, kind, oid = heapq.heappop(self.timers)
            self.now = due
            order = self.orders[oid]
            if kind == "LIVE" and order.state == "SUBMITTING":
                order.arrival_at = due
                constraint = self.constraints.get(order.intent.token)
                order.arrival_constraint = deepcopy(constraint)
                if (constraint is None or not constraint.get("known")
                        or constraint.get("available_mono", due) > due + ATTRIBUTION_EPSILON):
                    self.unknown_arrival(order, "EXCHANGE_CONSTRAINT_UNKNOWN")
                    continue
                tick = dec(constraint["tick"])
                if not 0 < tick < 1:
                    self.unknown_arrival(order, "EXCHANGE_CONSTRAINT_INVALID")
                    continue
                if dec(order.intent.price) % tick:
                    order.client = "PRECISION_REJECT_PENDING"
                    order.reject_reason = "PRICE_INCREMENT"
                    self.schedule(due, "REJECT", oid)
                    continue
                book = self.books.get(order.intent.token)
                if (
                    book is None
                    or book.best_ask is None
                    or book.snapshot_generation != constraint.get("generation")
                ):
                    self.unknown_arrival(order, "ARRIVAL_BOOK_UNAVAILABLE")
                    continue
                if dec(order.intent.price) >= book.best_ask:
                    order.client = "CROSS_REJECT_PENDING"
                    order.reject_reason = "POST_ONLY_CROSS"
                    self.schedule(due, "REJECT", oid)
                    continue
                if order.uncertain:
                    order.state = (
                        "CANCEL_IN_FLIGHT" if order.cancel_requested is not None else "LIVE"
                    )
                else:
                    self.join_live_order(order, book, due)
                    order.state = (
                        "CANCEL_IN_FLIGHT" if order.cancel_requested is not None else "LIVE"
                    )
                order.live_at = due
                self.event("LIVE", order=oid)
            elif kind == "REJECT" and order.state == "SUBMITTING":
                self.release(order)
                order.state = "REJECTED"
                self.event("REJECT", order=oid, reason=order.reject_reason,
                           price=order.intent.price, constraint=order.arrival_constraint)
            elif kind == "CANCEL" and order.state not in TERMINAL:
                if order.state == "SUBMITTING":
                    # A cancel that reaches the exchange before POST cannot
                    # tombstone a future order or prove the future POST rejected.
                    self.event("CANCEL_NOT_FOUND", order=oid, requested_at=order.cancel_requested)
                    order.cancel_requested = None
                    continue
                if not order.uncertain:
                    self.release(order)
                order.state = "CANCELLED"
                self.remove(order)
                self.event("CANCEL_EFFECTIVE", order=oid, unresolved=order.uncertain)
            elif kind in {"ACK", "TIMEOUT"}:
                if kind == "TIMEOUT":
                    order.client = "UNKNOWN"
                elif order.state == "REJECTED":
                    order.client = (
                        "CROSS_REJECT" if order.client == "CROSS_REJECT_PENDING"
                        else "PRECISION_REJECT" if order.client == "PRECISION_REJECT_PENDING"
                        else "REJECTED"
                    )
                elif order.client == "ARRIVAL_UNKNOWN_PENDING":
                    order.client = "UNKNOWN"
                else:
                    order.client = "ACK"
                order.ack_at = due
                self.receipts.append((oid, order.client))
                self.event("RECEIPT_READY", order=oid, client=order.client)
        self.now = now
        self.apply_cancellations()
        if delivery:
            result, self.receipts = self.receipts, []
            return result
        return []

    def join_live_order(self, order, book, due):
        """Queue-model arrival hook; alternative models retain venue checks."""
        key = self.queue_key(order)
        queue = self.queues.setdefault(key, [])
        visible = sum((q for p, q in book.bids if p == dec(order.intent.price)), ZERO)
        external = sum((seg[1] for seg in queue if seg[0] is None), ZERO)
        known_since = max(due, self.book_sources.get(order.intent.token, due))
        if visible > external:
            queue.append([None, visible - external, known_since])
        queue.append([order.id, order.remaining, known_since])
        self.event(
            "QUEUE_JOIN", order=order.id, visible=str(visible),
            available_book_age_ms=(due - book.received_mono) * 1000,
            segments=[[k, str(v), stamp] for k, v, stamp in queue],
            assumption="last_locally_available_book_at_model_arrival",
        )

    def unknown_arrival(self, order, reason):
        # Missing constraints are distinct from a definite exchange rejection.
        # No LIVE event or matching queue is created, and funds remain held.
        self.uncertain(order, reason)
        order.state, order.client = "ARRIVAL_UNKNOWN", "ARRIVAL_UNKNOWN_PENDING"
        self.event("ARRIVAL_UNKNOWN", order=order.id, reason=reason,
                   constraint=order.arrival_constraint, risk=str(order.reserved))

    def update_constraints(self, constraints):
        self.managed_constraints = True
        # Keep an owned copy, but do not deep-copy unchanged tick facts on every
        # market-data frame. Order conflict checks below still run every time.
        if constraints != self.constraints:
            self.constraints = deepcopy(constraints)
        conflicts = []
        unknown_arrivals = []
        for order in list(self.orders.values()):
            current = self.constraints.get(order.intent.token)
            if not self.contradicts_arrival(order, current):
                continue
            if order.filled:
                identity = digest({
                    "scenario": self.scenario, "order": order.id,
                    "generation": current.get("generation"),
                    "source_mono": round(current["source_mono"], 6),
                    "tick": str(dec(current["tick"])),
                })
                if identity not in self.constraint_conflicts:
                    conflict = {
                        "id": identity, "reason": CONSTRAINT_CONFLICT,
                        "input_seq": self.input_seq, "available_mono": self.now,
                        "scenario": self.scenario, "profile": order.intent.profile,
                        "market": order.intent.market, "token": order.intent.token,
                        "order": order.id, "order_state": order.state,
                        "price": order.intent.price, "filled": str(order.filled),
                        "arrival_at": order.arrival_at, "live_at": order.live_at,
                        "arrival_constraint": deepcopy(order.arrival_constraint),
                        "received_constraint": deepcopy(current),
                        "account_at_failure": asdict(self.account(order.intent.profile)),
                    }
                    self.constraint_conflicts[identity] = conflict
                    self.event("SIMULATION_INVALID", conflict=conflict)
                conflicts.append(self.constraint_conflicts[identity])
            elif order.state in {"LIVE", "PARTIAL", "CANCEL_IN_FLIGHT"}:
                unknown_arrivals.append((order, current))
        if conflicts:
            raise ConstraintConflict(conflicts)
        for order, current in unknown_arrivals:
            previous = order.arrival_constraint
            self.unknown_arrival(order, "QUEUED_TICK_OVERLAPS_ARRIVAL")
            self.event("ARRIVAL_CONSTRAINT_UNCERTAIN", order=order.id,
                       arrival=order.arrival_at, previous=previous, received=current)

    @staticmethod
    def contradicts_arrival(order, current):
        if (order.live_at is None or current is None or not current.get("known")
                or current.get("authority") != "WS"):
            return False
        previous = order.arrival_constraint or {}
        if (current.get("generation") is None
                or current["generation"] != previous.get("generation")
                or current.get("token", order.intent.token) != order.intent.token
                or current.get("market", order.intent.market) != order.intent.market):
            return False
        source = current.get("source_mono")
        available = current.get("available_mono")
        if not all(isinstance(value, (float, int)) and math.isfinite(value)
                   for value in (source, available)):
            return False
        return (source < order.live_at - ATTRIBUTION_EPSILON
                and available > order.live_at + ATTRIBUTION_EPSILON
                and bool(dec(order.intent.price) % dec(current["tick"])))

    def cancel(self, oid):
        order = self.orders[oid]
        if order.state in TERMINAL or order.cancel_requested is not None:
            return
        order.cancel_requested = self.now
        if order.state != "SUBMITTING":
            order.state = "CANCEL_IN_FLIGHT"
        self.schedule(self.now + self.cancel_delay, "CANCEL", oid)
        self.event("CANCEL_REQUEST", order=oid)

    def remove(self, order):
        key = self.queue_key(order)
        if key in self.queues:
            self.queues[key] = [x for x in self.queues[key] if x[0] != order.id]
            if not any(seg[0] is not None and seg[1] > 0 for seg in self.queues[key]):
                del self.queues[key]
                self.event(
                    "QUEUE_ABANDONED",
                    profile=order.intent.profile,
                    token=order.intent.token,
                    price=order.intent.price,
                )

    def release(self, order):
        account = self.account(order.intent.profile)
        amount = order.reserved
        if amount < 0 or amount > account.reserved:
            raise ValueError("SIM_OVER_RELEASE")
        account.reserved -= amount
        order.reserved = ZERO
        self.event("RELEASE", order=order.id, amount=str(amount))

    def update_books(self, books, *, source_times=None):
        if self.cancel_fraction:
            for token, book in books.items():
                old = self.books.get(token)
                if old is None or old.bids is book.bids:
                    continue
                watched = {}
                for (profile, t, price), segments in self.queues.items():
                    source = (source_times or {}).get(token, self.now)
                    active = [
                        seg[0]
                        for seg in segments
                        if seg[0] is not None
                        and seg[1] > 0
                        and self.orders[seg[0]].live_at is not None
                        and self.orders[seg[0]].live_at <= source + 1e-6
                    ]
                    if t == token and active:
                        watched.setdefault(dec(price), {})[profile] = active
                if not watched:
                    continue
                previous, current = dict(old.bids), dict(book.bids)
                for price, eligible in watched.items():
                    decrease = previous.get(price, ZERO) - current.get(price, ZERO)
                    if decrease > 0:
                        if len(self.depth_decreases) >= MAX_ATTRIBUTION_RECORDS:
                            raise RuntimeError("SIM_DEPTH_ATTRIBUTION_CAPACITY")
                        source = (source_times or {}).get(token, self.now)
                        self.depth_decreases.append(
                            {
                                "token": token,
                                "price": price,
                                "quantity": decrease,
                                "at": source,
                                "received_at": self.input_received,
                                "processed_at": self.now,
                                "input_seq": self.input_seq,
                                "eligible": eligible,
                            }
                        )
                        self.event(
                            "DEPTH_DECREASE", token=token, price=str(price),
                            quantity=str(decrease), source_at=source,
                            received_at=self.input_received, input_seq=self.input_seq,
                            attribution_frontier=self.attribution_frontier,
                        )
        self.books = dict(books)
        self.book_sources = {t: (source_times or {}).get(t, self.now) for t in books}
        if not self.managed_constraints:
            for token, book in books.items():
                previous = self.constraints.get(token)
                changed = previous is None or dec(previous["tick"]) != book.tick
                self.constraints[token] = {
                    "tick": str(book.tick), "known": book.tick.is_finite() and book.tick > 0
                    and book.snapshot_generation > 0,
                    "generation": book.snapshot_generation,
                    "revision": (previous["revision"] if previous else 0) + int(changed),
                    "authority": "SYNCHRONOUS_BOOK_CONSTRAINT",
                    "received_mono": self.now if changed else previous["received_mono"],
                    "available_mono": self.now if changed else previous["available_mono"],
                    "source_mono": self.now if changed else previous["source_mono"],
                }

    def apply_cancellations(self):
        frontier = self.attribution_frontier
        if frontier is None:
            return
        pending = []
        for decrease in self.depth_decreases:
            if decrease["at"] + ATTRIBUTION_WINDOW > frontier:
                pending.append(decrease)
                continue
            residual = decrease["quantity"]
            if any(
                credit["token"] == decrease["token"]
                and credit["price"] < decrease["price"]
                and abs(credit["at"] - decrease["at"])
                <= ATTRIBUTION_WINDOW + ATTRIBUTION_EPSILON
                for credit in self.trade_credits
            ):
                # A lower-price print may already have advanced this higher
                # counterfactual queue. Aggregate depth cannot attribute it
                # uniquely; give no additional cancellation credit in this window.
                self.event(
                    "DEPTH_ATTRIBUTION_AMBIGUOUS",
                    token=decrease["token"],
                    price=str(decrease["price"]),
                    depth_at=decrease["at"],
                    assumption="NO_CANCEL_CREDIT_WITH_CROSSING_PRINT_WITHIN_1S",
                )
                residual = ZERO
            for credit in self.trade_credits:
                if (
                    credit["token"] == decrease["token"]
                    and credit["price"] == decrease["price"]
                    and abs(credit["at"] - decrease["at"])
                    <= ATTRIBUTION_WINDOW + ATTRIBUTION_EPSILON
                ):
                    used = min(residual, credit["quantity"])
                    residual -= used
                    credit["quantity"] -= used
                    if used:
                        self.event(
                            "DEPTH_TRADE_ATTRIBUTION", token=decrease["token"],
                            price=str(decrease["price"]), quantity=str(used),
                            depth_input=decrease["input_seq"], depth_source_at=decrease["at"],
                            trade_input=credit["input"], trade_source_at=credit["at"],
                            attribution_frontier=frontier,
                        )
            if residual and self.cancel_fraction:
                for (profile, token, price), queue in self.queues.items():
                    if token != decrease["token"] or dec(price) != decrease["price"]:
                        continue
                    eligible = decrease["eligible"].get(profile, [])
                    if not any(seg[0] in eligible for seg in queue if seg[0] is not None):
                        continue
                    amount = residual * self.cancel_fraction
                    removed = ZERO
                    for segment in queue:
                        if segment[0] is not None:
                            break  # only external quantity before our oldest order
                        used = min(amount, segment[1])
                        segment[1] -= used
                        amount -= used
                        removed += used
                    self.event(
                        "ASSUMED_AHEAD_CANCEL",
                        profile=profile,
                        token=token,
                        price=price,
                        quantity=str(removed),
                        depth_at=decrease["at"],
                        depth_input=decrease["input_seq"],
                        attribution_frontier=frontier,
                        hypothesis="half_unattributed_net_decrease_after_1s_trade_matching",
                    )
        self.depth_decreases = pending
        # A depth change up to one window AFTER a print is itself finalized a
        # window later. Processing time cannot retire that print's credit.
        self.trade_credits = [
            x for x in self.trade_credits
            if x["at"] + 2 * ATTRIBUTION_WINDOW + ATTRIBUTION_EPSILON >= frontier
        ]

    def trade(self, row, identity, *, source_mono=None):
        """Only direct SELL prints can advance BUY queues; complementary flow cannot."""
        if row.get("derived") or row.get("side") != "SELL":
            return
        token, price, quantity = row["asset_id"], dec(row["price"]), dec(row["size"])
        if not 0 < price < 1 or quantity <= 0:
            raise ValueError("SIM_TRADE_INVALID")
        match = row.get("match_id") or row.get("trade_id") or row.get("id")
        key = (token, str(match)) if match else ("ambiguous", digest(row))
        signature = digest(row)
        if key in self.seen_trades:
            if match and self.seen_trades[key] == signature:
                self.event("DUPLICATE_TRADE", input=identity, match=match)
                return
            self.ambiguous.append(identity)
            self.event("TRADE_IDENTITY_AMBIGUOUS", input=identity, raw=row)
            self.gap("TRADE_IDENTITY_AMBIGUOUS", token)
            return
        if len(self.seen_trades) >= 50000:
            raise RuntimeError("SIM_TRADE_IDENTITY_CAPACITY")
        self.seen_trades[key] = signature
        source_mono = self.now if source_mono is None else source_mono
        for order in self.orders.values():
            if (
                order.state == "CANCELLED"
                and order.intent.token == token
                and dec(order.intent.price) >= price
                and order.live_at is not None
                and order.cancel_requested is not None
                and order.live_at - 1e-6
                <= source_mono
                <= order.cancel_requested + self.cancel_delay + 1e-6
            ):
                # The queue was retired before this delayed pre-cancel fact
                # became available. Do not invent an exact retrospective fill;
                # reopen unresolved historical-fill risk and retain its funds.
                self.uncertain(order, "LATE_PRE_CANCEL_TRADE")
        if source_mono + 1e-6 < self.source_floor.get(token, float("-inf")):
            self.event(
                "TRADE_SOURCE_REGRESSION",
                input=identity,
                source_mono=source_mono,
                previous=self.source_floor[token],
            )
            self.gap("TRADE_SOURCE_REGRESSION", token, source_mono=source_mono)
            return
        self.source_floor[token] = max(source_mono, self.source_floor.get(token, source_mono))
        if len(self.trade_credits) >= MAX_ATTRIBUTION_RECORDS:
            raise RuntimeError("SIM_TRADE_ATTRIBUTION_CAPACITY")
        self.trade_credits.append({
            "token": token, "price": price, "quantity": quantity, "at": source_mono,
            "input": identity, "received_at": self.input_received, "processed_at": self.now,
        })
        by_profile = defaultdict(list)
        for key in self.queues:
            if key[1] == token and dec(key[2]) >= price:
                by_profile[key[0]].append(key)
        for profile, keys in by_profile.items():
            available = quantity
            for key in sorted(keys, key=lambda k: dec(k[2]), reverse=True):
                queue = self.queues[key]
                for segment in queue:
                    if available <= 0:
                        break
                    oid, remaining, known_since = segment
                    if source_mono + 1e-6 < known_since:
                        self.event(
                            "TRADE_BEFORE_QUEUE_ENTRY",
                            input=identity,
                            profile=profile,
                            token=token,
                            price=key[2],
                            source_mono=source_mono,
                            queue_entry=known_since,
                            unallocated=str(available),
                        )
                        break
                    if oid is not None and self.orders[oid].uncertain:
                        continue
                    used = min(available, remaining)
                    available -= used
                    segment[1] -= used
                    if oid is not None and used:
                        self.fill(self.orders[oid], used, identity)
                self.queues[key] = [s for s in queue if s[1] > 0]
            self.event(
                "TRADE_ALLOCATION",
                input=identity,
                profile=profile,
                available=str(quantity),
                consumed=str(quantity - available),
            )
        self.check()

    def fill(self, order, quantity, identity):
        if (
            order.state not in {"LIVE", "PARTIAL", "CANCEL_IN_FLIGHT"}
            or order.uncertain
            or not 0 < quantity <= order.remaining
        ):
            raise ValueError("SIM_OVERFILL_OR_NOT_LIVE")
        cost = quantity * dec(order.intent.price)
        if cost > order.reserved:
            raise ValueError("SIM_OVERSPEND")
        account = self.account(order.intent.profile)
        account.cash -= cost
        account.reserved -= cost
        account.spent += cost
        account.inventory[order.intent.outcome] = (
            account.inventory.get(order.intent.outcome, ZERO) + quantity
        )
        order.reserved -= cost
        order.spent += cost
        order.filled += quantity
        order.first_fill = self.now if order.first_fill is None else order.first_fill
        order.last_fill = self.now
        order.state = (
            "FILLED"
            if not order.remaining
            else ("CANCEL_IN_FLIGHT" if order.cancel_requested is not None else "PARTIAL")
        )
        self.event(
            "MODELLED_FILL",
            order=order.id,
            input=identity,
            quantity=str(quantity),
            price=order.intent.price,
            cost=str(cost),
            fee="0",
            cancel_in_flight=order.cancel_requested is not None,
        )
        self.check()

    def uncertain(self, order, reason):
        if order.uncertain:
            return
        required = order.remaining * dec(order.intent.price)
        delta = required - order.reserved
        if delta > 0:
            account = self.account(order.intent.profile)
            if delta > account.cash - account.reserved:
                raise ValueError("SIM_UNCERTAIN_RESERVE_INSOLVENCY")
            account.reserved += delta
            order.reserved += delta
            self.event("RISK_RERESERVE", order=order.id, amount=str(delta), reason=reason)
        order.uncertain = True
        self.remove(order)
        self.event("ORDER_UNCERTAIN", order=order.id, reason=reason, risk=str(order.reserved))

    def gap(self, reason, token=None, *, source_mono=None, missing_since=None):
        # A genuine missing interval cannot be decomposed into cancellations.
        # Retire its attribution hypotheses before rebuilding any new queue.
        def affected(row):
            return token is None or row["token"] == token

        discarded = sum(affected(row) for row in self.depth_decreases)
        self.depth_decreases = [row for row in self.depth_decreases if not affected(row)]
        self.trade_credits = [row for row in self.trade_credits if not affected(row)]
        if discarded:
            self.event("ATTRIBUTION_GAP", reason=reason, token=token, discarded_depth=discarded)
        for order in self.orders.values():
            relevant = token is None or order.intent.token == token
            if not relevant:
                continue
            historical = (
                source_mono is not None
                and order.state == "CANCELLED"
                and order.live_at is not None
                and order.cancel_requested is not None
                and order.live_at - 1e-6
                <= source_mono
                <= order.cancel_requested + self.cancel_delay + 1e-6
            )
            # A transport gap also covers source events not yet received. With
            # the frozen 500ms admissible source-age rule, queued receipts may
            # hide eligible pre-cancel prints. Preserve that historical risk.
            gap_start = (self.now if missing_since is None else missing_since) - 0.502
            overlap = (
                source_mono is None
                and order.state == "CANCELLED"
                and order.live_at is not None
                and order.cancel_requested is not None
                and order.cancel_requested + self.cancel_delay >= gap_start
            )
            if order.state not in TERMINAL or historical or overlap:
                self.uncertain(order, reason)
        self.event(
            "DATA_GAP",
            reason=reason,
            token=token,
            source_mono=source_mono,
            missing_since_received_mono=missing_since,
            admissible_source_gap_start=(self.now if missing_since is None else missing_since)
            - 0.502,
        )
        self.check()

    def settle(self, winner):
        if winner not in {"YES", "NO"}:
            raise ValueError("SIM_NONBINARY_SETTLEMENT")
        for profile, account in self.accounts.items():
            if account.winner is not None:
                if account.winner != winner:
                    raise ValueError("SIM_SETTLEMENT_CONFLICT")
                continue
            account.winner = winner
            account.payout = account.inventory.get(winner, ZERO)
            account.cash += account.payout
            self.event("SETTLE", profile=profile, winner=winner, payout=str(account.payout))
        self.check()

    def check(self):
        for profile, account in self.accounts.items():
            orders = [o for o in self.orders.values() if o.intent.profile == profile]
            if (
                min(account.cash, account.reserved) < 0
                or account.cash < account.reserved
                or account.cash != account.initial - account.spent + account.payout
                or account.reserved != sum((o.reserved for o in orders), ZERO)
                or account.spent != sum((o.spent for o in orders), ZERO)
            ):
                raise ValueError("SIM_CASH_CONSERVATION_FAILED")
            for outcome in ("YES", "NO"):
                if account.inventory.get(outcome, ZERO) != sum(
                    (o.filled for o in orders if o.intent.outcome == outcome), ZERO
                ):
                    raise ValueError("SIM_INVENTORY_CONSERVATION_FAILED")

    def snapshot(self):
        return {
            "scenario": self.scenario,
            "seed": self.seed,
            "orders": {k: asdict(o) for k, o in self.orders.items()},
            "accounts": {k: asdict(a) for k, a in self.accounts.items()},
            "timers": self.timers,
            "queues": {"|".join(k): v for k, v in self.queues.items()},
            "ambiguous_trades": self.ambiguous,
            "source_floor": self.source_floor,
            "book_sources": self.book_sources,
            "exchange_constraints": self.constraints,
            "constraint_conflicts": list(self.constraint_conflicts.values()),
            "attribution": {
                "source_frontier": self.attribution_frontier,
                "managed_frontier": self.managed_frontier,
                "trade_credits": self.trade_credits,
                "depth_decreases": self.depth_decreases,
                "record_limit_per_list": MAX_ATTRIBUTION_RECORDS,
            },
        }
