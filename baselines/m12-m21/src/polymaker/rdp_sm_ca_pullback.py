"""One-shot CA history -> two-sided quote advance -> fixed maker pullback.

The CA detector remains unchanged. Its old anchor lease is deliberately not the
lease for this formation: a successful advance naturally ends that CA lease.
All callbacks run under SessionFeed.lock, including native submission guards.
"""

from dataclasses import asdict, dataclass
from decimal import Decimal

from polymaker.rdp_sm_ca_entry import CounterAttackEntry, confirmation_allowed
from polymaker.rdp_sm_signal import Signal


@dataclass
class CAPullbackEpisode:
    episode_id: str
    token_id: str
    outcome: str
    generation: int
    anchor_bid_price: Decimal
    confirmation_ask: Decimal
    tick: Decimal
    ca_start_mono: float
    confirmed_mono: float
    advance_deadline: float
    entry_price: Decimal
    state: str = "WAIT_ADVANCE"
    advance_mono: float | None = None
    order_deadline: float = 0.
    advance_bid: Decimal | None = None
    advance_ask: Decimal | None = None
    advance_book: dict | None = None
    end_reason: str | None = None
    ended_mono: float | None = None

    @property
    def signal_start_mono(self):
        return self.advance_mono if self.advance_mono is not None else self.confirmed_mono


class CAPullbackEntry(CounterAttackEntry):
    def __init__(self, feed, cfg):
        super().__init__(feed)
        self.cfg = cfg.model_copy(deep=True)
        self.states = {}
        self.seen = set()

    def record(self, ep, event):
        self.feed.ca.emit("CA_PULLBACK_"+event, {
            "setup": asdict(ep), "parameters": self.cfg.model_dump(mode="json")})

    def finish(self, ep, reason):
        if ep.end_reason is None:
            ep.end_reason, ep.ended_mono = reason, self.feed.monotonic()
            ep.state = "ENDED"
            self.record(ep, "CLOSED")

    def invalidate(self, reason):
        for ep in self.states.values():
            self.finish(ep, reason)

    def capture_confirmations(self):
        ca = self.feed.ca
        if not ca.healthy():
            return
        for source in ca.states.values():
            identity = (source.generation, source.episode_id)
            if (source.tC is None or source.end_reason or identity in self.seen):
                continue
            self.seen.add(identity)
            if ca.phase != "NORMAL" or not confirmation_allowed(asdict(source)):
                continue
            current = self.states.get(source.token_id)
            if current is not None and current.end_reason is None:
                continue  # Never renew an existing formation with another CA.
            book = self.feed.books[source.token_id]
            ep = CAPullbackEpisode(
                source.episode_id, source.token_id, source.outcome, source.generation,
                source.anchor_bid_price, Decimal(source.confirming_buy["pre_ask"]),
                book.tick, source.t0, source.tC,
                source.tC+self.cfg.advance_timeout_sec,
                source.anchor_bid_price+self.cfg.entry_offset_ticks*book.tick,
            )
            self.states[source.token_id] = ep
            self.record(ep, "STARTED")
            if any(other.token_id != ep.token_id and other.end_reason is None
                   for other in self.states.values()):
                self.invalidate("CA_PULLBACK_BOTH_SIDES_VALID")

    def check(self, ep, book):
        now, ca = self.feed.monotonic(), self.feed.ca
        if ca is None or not ca.healthy() or ca.closed:
            return "CA_PULLBACK_OBSERVER_UNAVAILABLE"
        if ep.generation != self.feed.generation:
            return "CA_PULLBACK_GENERATION_CHANGED"
        if book.tick != ep.tick:
            return "CA_PULLBACK_TICK_CHANGED"
        if not ca.legal(book, now):
            return "CA_PULLBACK_BOOK_INVALID"
        if book.best_bid < ep.anchor_bid_price:
            return "CA_PULLBACK_FLOOR_BROKEN"
        if ep.state == "WAIT_ADVANCE" and now >= ep.advance_deadline:
            return "CA_PULLBACK_ADVANCE_TIMEOUT"
        if ep.advance_mono is not None and now >= ep.order_deadline:
            return "CA_PULLBACK_ORDER_EXPIRED"
        return None

    def book_updated(self, book):
        ep = self.states.get(book.token_id)
        if ep is None or ep.end_reason:
            return
        reason = self.check(ep, book)
        if reason:
            self.finish(ep, reason)
            return
        distance = self.cfg.advance_ticks*ep.tick
        if (ep.state == "WAIT_ADVANCE"
                and book.best_bid >= ep.anchor_bid_price+distance
                and book.best_ask >= ep.confirmation_ask+distance):
            ep.advance_mono = self.feed.monotonic()
            ep.order_deadline = ep.advance_mono+self.cfg.order_ttl_sec
            ep.advance_bid, ep.advance_ask = book.best_bid, book.best_ask
            ep.advance_book = {
                "source_time": book.source_time, "received_monotonic": book.received_mono,
                "applied_monotonic": book.applied_mono, "generation": book.snapshot_generation,
                "frame": book.frame,
            }
            ep.state = "WAIT_MAKER"
            self.record(ep, "ADVANCED")
        # Once sent, a return to the quote is the intended fill opportunity.
        # Before submission, do not turn a missed retracement into a late entry.
        if ep.state == "WAIT_MAKER" and book.best_bid <= ep.entry_price:
            self.finish(ep, "CA_PULLBACK_RETRACE_BEFORE_POST")

    def tick(self):
        for ep in self.states.values():
            if ep.end_reason is None:
                reason = self.check(ep, self.feed.books[ep.token_id])
                if reason:
                    self.finish(ep, reason)

    def evaluate(self, snapshot):
        with self.feed.lock:
            self.tick()
            self.block_reason = "CA_PULLBACK_WAIT_ADVANCE"
            valid = []
            for ep in self.states.values():
                if ep.end_reason or ep.state != "WAIT_MAKER" or ep.episode_id in self.consumed:
                    continue
                book = self.feed.books[ep.token_id]
                if book.best_bid <= ep.entry_price:
                    self.finish(ep, "CA_PULLBACK_RETRACE_BEFORE_POST")
                elif snapshot.valid and snapshot.generation == ep.generation:
                    valid.append(ep)
            if len(valid) > 1:
                self.invalidate("CA_PULLBACK_BOTH_SIDES_VALID")
                valid = []
            self.selected = valid[0] if valid else None
            self.episode_id = self.selected.episode_id if self.selected else None
            self.episode_start = self.selected.signal_start_mono if self.selected else None
            if self.selected:
                self.block_reason = None
            elif any(ep.end_reason for ep in self.states.values()):
                self.block_reason = next(ep.end_reason for ep in reversed(list(self.states.values()))
                                         if ep.end_reason)
            return Signal("VALID_"+self.selected.outcome if self.selected else "NONE",
                          snapshot.snapshot_id, event_mono=self.episode_start)

    def active_reason(self, claim, snapshot):
        with self.feed.lock:
            self.tick()
            ep = next((e for e in self.states.values() if claim and e.episode_id == claim[0]
                       and e.outcome == claim[1]), None)
            if ep is None:
                return "CA_PULLBACK_EPISODE_CHANGED"
            if ep.end_reason:
                return ep.end_reason
            if not snapshot.valid:
                return snapshot.reason or "CA_PULLBACK_BOOK_INVALID"
            return self.check(ep, self.feed.books[ep.token_id])

    def current(self, claim):
        with self.feed.lock:
            if claim is None or claim != self.claimed or self.selected is None:
                return False
            snapshot = self.feed.snapshot()
            if self.active_reason(claim, snapshot):
                return False
            ep = self.selected
            return (ep.episode_id == claim[0] and ep.state == "WAIT_MAKER"
                    and ep.episode_id not in self.consumed
                    and self.feed.books[ep.token_id].best_bid > ep.entry_price)

    def consume(self, claim):
        with self.feed.lock:
            if not self.current(claim):
                raise ValueError("CA_PULLBACK_EPISODE_CHANGED")
            self.consumed.add(claim[0])
            self.selected.state = "POSTED"
            self.record(self.selected, "SUBMITTED")

    def evidence(self, claim):
        with self.feed.lock:
            ep = next((e for e in self.states.values() if claim and e.episode_id == claim[0]
                       and e.outcome == claim[1]), None)
            if ep is None or ep.end_reason or self.check(ep, self.feed.books[ep.token_id]):
                return None
            return {"setup": asdict(ep), "parameters": self.cfg.model_dump(mode="json")}

    def diagnostics(self):
        return {"parameters": self.cfg.model_dump(mode="json"),
                "setups": [asdict(ep) for ep in self.states.values()]}
