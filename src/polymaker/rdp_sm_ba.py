"""Ordered, generation-scoped Bid Absorption evidence. No trading or REPAIR policy.

Only market-channel last_trade_price SELL prints at a fresh noncrossed bid are
accepted. price_change.side is a book side and is NEVER used as trade direction.
The socket receive order and nondecreasing venue milliseconds must both agree;
equal timestamps use the socket ordinal, never a sort or inferred reorder.
"""

import math
from collections import Counter, deque
from contextlib import suppress
from dataclasses import asdict, dataclass
from decimal import Decimal

from polymaker.rdp_sm_network import RecentWrites
from polymaker.rdp_sm_signal import Signal, number


@dataclass(frozen=True)
class BAEntryAdmission:
    reason: str | None
    t0: float
    deadline: float
    observed_monotonic: float
    entry_age_ms: float
    max_entry_age_ms: float
    remaining_ms: float
    post_budget_ms: float
    min_live_budget_ms: float
    max_cancel_rtt_ms: float
    writes: RecentWrites


def entry_admission(cfg, *, t0, deadline, writes):
    """BA new-POST budget. Does not alter active leases or network health."""
    now = writes.observed_monotonic
    age, remaining = (now-t0)*1000, (deadline-now)*1000
    post_budget = max(cfg.network.post_budget_floor_ms, writes.post.max_ms or 0.)
    reason = next((name for name, failed in (
        ("BA_ENTRY_STALE", not 0 <= age <= cfg.max_entry_age_ms),
        ("BA_CANCEL_LATENCY_HIGH", writes.delete.max_ms is not None
         and writes.delete.max_ms > cfg.network.max_cancel_rtt_ms),
        ("BA_EXECUTION_BUDGET_INSUFFICIENT", not remaining >= post_budget+cfg.network.min_live_budget_ms),
    ) if failed), None)
    return BAEntryAdmission(reason, t0, deadline, now, age, cfg.max_entry_age_ms,
                            remaining, post_budget, cfg.network.min_live_budget_ms,
                            cfg.network.max_cancel_rtt_ms, writes)


@dataclass
class BAEpisode:
    normal_entry_mode: str
    market_id: str
    episode_id: str
    token_id: str
    outcome: str
    anchor_bid_price: Decimal
    anchor_bid_size: Decimal
    signal_start_mono: float
    generation: int
    order_deadline: float
    sell_volume: Decimal = Decimal(0)
    depletion_shares: Decimal = Decimal(0)
    refill_shares: Decimal = Decimal(0)
    ba_confirmed_mono: float | None = None
    invalid_reason: str | None = None
    last_sell_mono: float = 0
    last_sell_source: float = 0
    depleted_ordinal: int | None = None
    consumed: bool = False


class BidAbsorption:
    """Access under SessionFeed.lock, also used by submit guards in SDK workers."""

    trade_history_limit = 4096

    def __init__(self, feed, cfg):
        self.feed, self.cfg = feed, cfg
        self.states = {}
        self.ordinal = self.counter = 0
        self.source_times = {}
        self.tops = {}
        self.seen_trades = set()
        self.trade_keys = deque()
        self.trade_floor = {}
        self.events = deque()
        self.claimed = None
        self.selected = None
        self.episode_id = self.episode_start = None
        self.counts = Counter()
        self.rejected = Counter()
        self.last_rejections = {}

    def diagnostics(self):
        with self.feed.lock:
            return {"counts": dict(self.counts), "rejected": dict(self.rejected),
                    "last_rejections": {k: dict(v) for k, v in self.last_rejections.items()},
                    "active_episodes": [asdict(ep) for ep in self.states.values()]}

    def reject(self, kind, reason, row, book, mono, wall):
        """Bounded summaries also cover rejections BEFORE an episode exists.

        Never retain raw frames or write one Journal row per public trade.
        Timestamp/price deltas expose which freshness/anchor check rejected it.
        """
        key = f"{kind}:{reason}"
        self.rejected[key] += 1
        evidence = {"reason": reason, "event_type": kind, "token_id": book.token_id,
            "generation": self.feed.generation, "event_ordinal": self.ordinal,
            "received_mono": mono, "received_wall": wall,
            "processed_mono": self.feed.monotonic(),
            "book_source_time": book.source_time,
            "book_source_age_ms": (wall-book.source_time)*1000,
            "book_receipt_age_ms": (mono-book.received_mono)*1000,
            "book_processing_age_ms": (self.feed.monotonic()-book.received_mono)*1000,
            "best_bid": str(book.best_bid), "best_ask": str(book.best_ask),
            "book_generation": book.snapshot_generation,
            "last_ordered_source_time": str(self.source_times.get(book.token_id)),
            **{name: str(row.get(name))[:80] for name in ("timestamp", "side", "price", "size")}}
        with suppress(KeyError, ValueError, TypeError, ArithmeticError):
            age = (wall-float(number(row["timestamp"])/1000))*1000
            if math.isfinite(age):
                evidence["trade_source_age_ms"] = age
        self.last_rejections[key] = evidence

    def record(self, ep, event):
        self.counts[f"episode_{event.lower()}"] += 1
        # Small episode diagnostics only. Never retain raw frames/depth history.
        if len(self.events) >= 1024:
            self.events.clear()
            for state in self.states.values():
                state.invalid_reason = "BA_LOG_OVERFLOW"
        self.events.append({"event": event, **asdict(ep)})

    def drain(self):
        with self.feed.lock:
            result = list(self.events)
            self.events.clear()
            return result

    def invalidate(self, reason, token=None):
        for ep in self.states.values():
            if (token is None or ep.token_id == token) and ep.invalid_reason is None:
                ep.invalid_reason = reason
                self.record(ep, "INVALIDATED")

    def reset(self):
        self.invalidate("BA_WS_GENERATION_CHANGED")
        self.states.clear()
        self.source_times.clear()
        self.tops.clear()
        self.seen_trades.clear()
        self.trade_keys.clear()
        self.trade_floor.clear()
        self.selected = self.claimed = None

    def ordered(self, token, row, mono, wall):
        self.ordinal += 1
        stamp = number(row["timestamp"]) / 1000
        if stamp <= 0 or stamp > number(wall) + Decimal(".002"):
            raise ValueError("BA_SOURCE_CLOCK_INVALID")
        previous = self.source_times.get(token)
        if previous is not None and stamp < previous:
            raise ValueError("BA_EVENT_OUT_OF_ORDER")
        self.source_times[token] = stamp
        return float(stamp)

    def legal(self, book, mono):
        # Match SessionFeed's book-age clock: time since local WS receipt.
        # Venue -> local transport latency belongs to the source-evidence
        # budget, not the 100ms local book lease. Keep source evidence bounded
        # by the configured SELL age, including duplicates with fresh receipts.
        return (book.snapshot_generation == self.feed.generation
                and book.best_bid is not None and book.best_ask is not None
                and 0 < book.best_bid < book.best_ask < 1
                and book.best_bid % book.tick == 0
                and self.feed.tick_verified.get(book.token_id) == book.tick
                and (book.reported_bid is None or book.reported_bid == book.best_bid)
                and (book.reported_ask is None or book.reported_ask == book.best_ask)
                and self.tops.get(book.token_id, (book.best_bid, book.best_ask))
                    == (book.best_bid, book.best_ask)
                and 0 <= (self.feed.wall()-book.source_time)*1000 <= self.cfg.max_sell_age_ms
                and 0 <= (max(mono, self.feed.monotonic())-book.received_mono)*1000
                    <= self.cfg.max_book_age_ms)

    def top(self, row, book, mono, wall):
        try:
            self.ordered(book.token_id, row, mono, wall)
            bid, ask = number(row["best_bid"]), number(row["best_ask"])
            self.tops[book.token_id] = (bid, ask)
            if (bid, ask) != (book.best_bid, book.best_ask):
                self.invalidate("BA_TOP_CHANGED", book.token_id)
        except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
            self.reject("best_bid_ask", str(exc), row, book, mono, wall)
            self.invalidate(str(exc), book.token_id)

    def expire(self, ep, now):
        if ep.invalid_reason:
            return
        reason = ("BA_ORDER_DEADLINE" if now >= ep.order_deadline else
                  "BA_OBSERVATION_TIMEOUT" if ep.ba_confirmed_mono is None
                  and now >= ep.signal_start_mono+self.cfg.observation_sec else None)
        if reason:
            self.invalidate(reason, ep.token_id)

    def trade(self, row, book, mono, wall):
        token = book.token_id
        self.counts["trade_events"] += 1
        self.counts["sell_events" if row.get("side") == "SELL" else
                    "buy_events" if row.get("side") == "BUY" else "unknown_side_events"] += 1
        try:
            stamp = self.ordered(token, row, mono, wall)
            price, qty = number(row["price"]), number(row["size"])
            if row.get("side") not in {"SELL", "BUY"} or qty <= 0 or not 0 < price < 1:
                raise ValueError("BA_TRADE_DIRECTION_OR_QUANTITY_UNKNOWN")
            key = (token, stamp, row["side"], price, qty, row.get("transaction_hash"))
            if stamp <= self.trade_floor.get(token, 0):
                raise ValueError("BA_TRADE_HISTORY_UNCERTAIN")
            if key in self.seen_trades:
                raise ValueError("BA_DUPLICATE_TRADE")
            self.seen_trades.add(key)
            self.trade_keys.append(key)
            if len(self.trade_keys) > self.trade_history_limit:
                old = self.trade_keys.popleft()
                self.seen_trades.remove(old)
                self.trade_floor[old[0]] = max(old[1], self.trade_floor.get(old[0], 0))
                if stamp <= self.trade_floor.get(token, 0):
                    raise ValueError("BA_TRADE_HISTORY_UNCERTAIN")
            if row["side"] != "SELL":
                return
            # A SELL print must execute at the pre-event resting bid, never an
            # ask or crossed/unknown book. This is the executable aggressor check.
            if (not self.legal(book, mono) or price != book.best_bid
                    or not 0 <= (wall-stamp)*1000 <= self.cfg.max_sell_age_ms):
                raise ValueError("BA_SELL_NOT_FRESH_AT_BID")
            ep = self.states.get(token)
            if ep:
                self.expire(ep, mono)
            if ep is None or ep.invalid_reason:
                self.counter += 1
                ep = BAEpisode("ba_v1", self.feed.meta.condition_id,
                    f"ba:{self.feed.generation}:{self.counter}", token,
                    "YES" if token == self.feed.meta.yes.token_id else "NO",
                    price, book.best_bid_size, mono, self.feed.generation,
                    mono+self.cfg.signal_lifetime_sec)
                self.states[token] = ep
                self.record(ep, "STARTED")
            if ep.anchor_bid_price != price:
                raise ValueError("BA_ANCHOR_CHANGED")
            if ep.ba_confirmed_mono is None:
                ep.sell_volume += qty
                ep.last_sell_mono, ep.last_sell_source = mono-max(0, wall-stamp), stamp
                self.record(ep, "SELL")
        except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
            self.reject("last_trade_price", str(exc), row, book, mono, wall)
            self.invalidate(str(exc), token)

    def depth(self, row, old, new, mono, wall):
        token = new.token_id
        try:
            self.ordered(token, row, mono, wall)
            self.tops.pop(token, None)  # A later depth event supersedes a BBO-only notice.
            ep = self.states.get(token)
            if ep is None:
                return
            self.expire(ep, self.feed.monotonic())
            if ep.invalid_reason:
                return
            if not self.legal(new, mono) or new.best_bid != ep.anchor_bid_price:
                raise ValueError("BA_ANCHOR_INVALID")
            if ep.ba_confirmed_mono is not None or ep.sell_volume < self.cfg.min_sell_shares:
                return
            before, after = (dict(b.bids).get(ep.anchor_bid_price, Decimal(0)) for b in (old, new))
            if after < before:
                ep.depletion_shares += before-after
                if ep.depletion_shares >= self.cfg.min_depletion_shares and ep.depleted_ordinal is None:
                    ep.depleted_ordinal = self.ordinal
            elif after > before and ep.depleted_ordinal is not None and self.ordinal > ep.depleted_ordinal:
                ep.refill_shares += after-before
                if ep.refill_shares >= self.cfg.min_refill_shares:
                    ep.ba_confirmed_mono = self.feed.monotonic()
                    self.record(ep, "CONFIRMED")
        except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
            self.reject("depth", str(exc), row, new, mono, wall)
            self.invalidate(str(exc), token)

    def evaluate(self, snapshot):
        with self.feed.lock:
            now = self.feed.monotonic()
            valid = []
            for ep in self.states.values():
                self.expire(ep, now)
                if (not ep.invalid_reason and ep.ba_confirmed_mono is not None
                        and ep.generation == snapshot.generation):
                    valid.append(ep)
            # Retire both conflicting opportunities, rather than entering a
            # surviving stale opportunity when the other one times out.
            if len(valid) > 1:
                self.invalidate("BA_BOTH_SIDES_VALID")
                valid = []
            ep = valid[0] if len(valid) == 1 else None
            if (ep and (not snapshot.valid or not self.legal(self.feed.books[ep.token_id], now)
                        or self.feed.books[ep.token_id].best_bid != ep.anchor_bid_price)):
                self.invalidate(snapshot.reason or "BA_ANCHOR_INVALID", ep.token_id)
                ep = None
            self.selected = ep
            self.episode_id = ep.episode_id if ep else None
            self.episode_start = ep.signal_start_mono if ep else None
            fresh = ep and 0 <= (now-ep.last_sell_mono)*1000 <= self.cfg.max_sell_age_ms
            return Signal("VALID_"+ep.outcome if fresh and not ep.consumed else "NONE",
                          snapshot.snapshot_id, event_mono=ep.signal_start_mono if ep else None)

    def claim(self, signal):
        with self.feed.lock:
            ep = self.selected
            if self.claimed or not ep or ep.consumed or ep.invalid_reason or not signal.direction:
                return None
            self.claimed = (ep.episode_id, ep.outcome, signal.snapshot_id)
            return self.claimed

    def current(self, claim):
        with self.feed.lock:
            return (claim is not None and claim == self.claimed and self.selected is not None
                    and claim[0] == self.selected.episode_id and not self.selected.consumed
                    and self.selected.invalid_reason is None)

    def consume(self, claim):
        with self.feed.lock:
            if not self.current(claim):
                raise ValueError("BA_EPISODE_CHANGED")
            self.selected.consumed = True
            self.record(self.selected, "SUBMITTED")

    def release(self, claim):
        with self.feed.lock:
            if claim == self.claimed:
                self.claimed = None

    def active_reason(self, claim, snapshot):
        with self.feed.lock:
            ep = next((ep for ep in self.states.values() if ep.episode_id == claim[0]), None)
            if ep is None:
                return "BA_EPISODE_CHANGED"
            self.expire(ep, self.feed.monotonic())
            if ep.invalid_reason:
                return ep.invalid_reason
            book = self.feed.books[ep.token_id]
            if not snapshot.valid or not self.legal(book, self.feed.monotonic()):
                return snapshot.reason or "BA_BOOK_INVALID"
            return "BA_ANCHOR_INVALID" if book.best_bid != ep.anchor_bid_price else None
