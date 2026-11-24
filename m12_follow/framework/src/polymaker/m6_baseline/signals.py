"""Frozen M6 RF+LW signals bridged to the independent baseline contract.

SessionFeed supplies neutral book/lifecycle helpers. M6 owns its atomic socket
queue and parser: every raw input goes through the frozen research detector.
"""
from collections import Counter, deque
from dataclasses import asdict
from decimal import Decimal

from polymaker.m4_live_numeric import age_us, live_book_legal
from polymaker.maker_queue import legal
from polymaker.rdp_sm_marketdata import SessionFeed, TokenBook

from ._signal_v7.feed import ResearchFeed
from ._signal_v7.model import decimal
from ._signal_v7.strategies import M5_M10_LOCAL_AP, Strategies
from .capture import AtomicCapture
from .types import Opportunity


class ObservedResearchFeed(ResearchFeed):
    def _depth(self, token, row, changes, received):
        self.owner.updated_depth.add(token)
        super()._depth(token, row, changes, received)
        if self.owner.fill_model is not None and self.good[token] and self.legal(token):
            self.owner.observed_books.append(self.owner.book_view(token))


class LiveResearchFeed(ObservedResearchFeed):
    source_order_policy = "IGNORE_PROVEN_NOOP_QUOTES_WITHIN_20MS_V1"

    def legal(self, token):
        book = self.books[token]
        return (
            self.connected
            and token not in self.self_contaminated
            and book.complete
            and book.tick_verified
            and book.generation == self.generation
            and 0 < book.bid < book.ask < 1
            and 0 <= age_us(self.now, book.received) <= 200_000
            and -2_000 <= age_us(self.wall, book.source) <= 500_000
        )

    def source(self, token, row):
        stamp = decimal(row["timestamp"]) / 1000
        if not -2_000 <= age_us(self.wall, stamp) <= 500_000:
            self.unavailable(token, "EVENT_SOURCE_AGE")
            return False
        previous = self.sources.get(token)
        if previous is not None and (regression := age_us(previous, stamp)) > 0:
            if (regression <= 20_000 and self.good[token] and self.legal(token)
                    and age_us(stamp, self.books[token].source) > 0
                    and self.quote_unchanged(token, row)):
                # A match against the current book cannot prove that an update
                # preceding that book was a no-op. Never refresh coverage here.
                self.event("LATE_QUOTE_NOOP_IGNORED", {
                    "token": token, "kind": row["event_type"], "regression_us": regression,
                })
            else:
                self.unavailable(token, "UNRECONSTRUCTABLE_SOURCE_ORDER")
            return False
        self.sources[token] = float(stamp)
        return True

    def quote_unchanged(self, token, row):
        book = self.books[token]
        kind = row["event_type"]
        if kind == "best_bid_ask":
            return decimal(row["best_bid"]) == book.bid and decimal(row["best_ask"]) == book.ask
        if kind != "price_change":
            # A trade (including a complementary view), tick, or snapshot may
            # carry independent evidence even when the best prices agree.
            return False
        for change in row["price_changes"]:
            if change["asset_id"] != token:
                continue
            side, price = change["side"], decimal(change["price"])
            if side not in {"BUY", "SELL"} or not 0 < price < 1 or price % book.tick:
                return False
            levels = book.bids if side == "BUY" else book.asks
            if decimal(change["size"]) != levels.get(price, 0):
                return False
            for field, best in (("best_bid", book.bid), ("best_ask", book.ask)):
                if change.get(field) is not None and decimal(change[field]) != best:
                    return False
        return True


class M6OnlyStrategies(Strategies):
    """Frozen v7 predates strategy selection; filter at campaign creation."""
    def start(self, strategy, token, root, deadline, anchor=None):
        if strategy != "M6":
            return None
        return super().start(strategy, token, root, deadline, anchor)


class M6Signals:
    m6_only = True

    def legal(self, book, now):
        c = self.feed.research
        legal_book = live_book_legal if self.taker_settings is not None else legal
        return legal_book(self, book, now) and c.good[book.token_id] and c.legal(book.token_id)

    def __init__(self, feed, record=None, *, taker_settings=None):
        self.feed, self.record = feed, record
        self.counts, self.events = Counter(), deque(maxlen=4096)
        self.failure = self.selected = None
        self.used_roots, self.opportunities = set(), {}
        self.taker_settings = taker_settings
        self.price_risk = taker_settings.live_taker_price_risk if taker_settings else None

    def evidence(self, kind, payload):
        self.counts[kind+":"+str(payload.get("event", ""))] += 1
        event = "CANDIDATE" if kind == "SIGNAL" and payload.get("event") == "CANDIDATE" else kind
        self.emit(event, evidence=payload)

    def emit(self, event, **payload):
        self.counts[event] += 1
        row = {"event": event, "strategy": "m6-withdrawal-supported-v1.1",
               "available_monotonic": self.feed.monotonic(), "generation": self.feed.generation, **payload}
        self.events.append(row)
        if self.record is not None and self.record(row) is False:
            self.failure = "M6_EVENT_RECORDING_FAILED"
            self.selected = None

    def tick(self, **kwargs):
        if self.failure or self.feed.stop_event.is_set() or self.feed.runtime_released:
            return
        with self.feed.lock:
            self.feed.research.timer(now=self.feed.monotonic(), wall=self.feed.wall(),
                                     seq=self.feed.m1_frame, pending=self.feed.pending_receipts())

    def invalidate(self, reason, token=None):
        with self.feed.lock:
            if self.feed.runtime_released:
                self.selected = None
                return
            if token is None:
                self.feed.research.gap(reason)
            else:
                self.feed.research.unavailable(token, reason)
            self.selected = None
            if self.feed.fill_model is not None:
                self.feed.fill_model.gap(reason, token)

    def reset(self):
        self.invalidate("GENERATION_CHANGED")

    def evaluate(self, snapshot=None):
        self.tick()
        choices = []
        if not self.failure:
            for candidate in self.feed.strategies.candidates():
                if candidate["root_id"] in self.used_roots:
                    continue
                fields = (candidate["candidate_id"], candidate["token"], candidate["outcome"],
                    Decimal(candidate["anchor_price"]), candidate["confirmed"], candidate["deadline"],
                    candidate["primitive_ids"][0], candidate["root_id"], candidate["generation"])
                if self.taker_settings:
                    from polymaker.m4_types import Opportunity as TakerOpportunity
                    opportunity = TakerOpportunity(*fields, price_risk=self.price_risk,
                                                   taker_strategy=self.taker_settings.taker_strategy,
                                                   market_id=self.feed.meta.condition_id)
                else:
                    opportunity = Opportunity(*fields)
                if opportunity.episode_id not in self.opportunities:
                    self.opportunities[opportunity.episode_id] = opportunity
                    self.emit("OPPORTUNITY_IDENTIFIED", opportunity=asdict(opportunity), signal_evidence=candidate)
                choices.append(opportunity)
        self.selected = min(choices, key=lambda p: (p.signal_start_mono, p.outcome, p.episode_id)) if choices and not self.failure else None

    def active_reason(self, claim, snapshot=None):
        self.tick()
        if self.failure:
            return self.failure
        saved = self.opportunities.get(claim[0])
        if saved is None or saved.generation != self.feed.generation:
            return "M6_EPISODE_CHANGED"
        if self.feed.monotonic() >= saved.order_deadline:
            return "M6_OPPORTUNITY_EXPIRED"
        current = {c["candidate_id"] for c in self.feed.strategies.candidates()}
        return None if saved.episode_id in current else "M6_EVIDENCE_ENDED"

    def take(self, opportunity):
        with self.feed.lock:
            if opportunity.root_id in self.used_roots or self.active_reason((opportunity.episode_id,)):
                return False
            self.used_roots.add(opportunity.root_id)
            return True

    def diagnostics(self):
        return {"strategy": "M6", "failure": self.failure, "counts": dict(self.counts)}


class M6Feed(AtomicCapture, SessionFeed):
    def __init__(self, meta, cfg, *, record=None, taker_settings=None, **kwargs):
        super().__init__(meta, cfg, normal_entry_mode="ba_v1", book_only=True, **kwargs)
        self.fill_model, self.observed_books = None, []
        self.updated_depth = set()
        self.runtime_released = False
        self.input_revision = 0
        self.m1 = M6Signals(self, record, taker_settings=taker_settings)
        tokens = (meta.yes.token_id, meta.no.token_id)
        research_feed = LiveResearchFeed if taker_settings is not None else ObservedResearchFeed
        self.research = research_feed(meta.condition_id, tokens,
            {t: b.tick for t, b in self.books.items()}, self.m1.evidence)
        self.research.owner = self
        self.strategies = M6OnlyStrategies(self.research, dict(zip(tokens, ("YES", "NO"), strict=True)),
            signal_variant=M5_M10_LOCAL_AP, parent_filters=False)

    def observe_ca(self, method, *args):
        return None

    def pending_receipts(self):
        result = list(self.m1_pending_received)
        if self.m1_processing_received is not None:
            result.append(self.m1_processing_received)
        return result

    def connect(self):
        super().connect()
        self.research.connect(now=self.monotonic(), wall=self.wall(), seq=self.m1_frame)
        if self.research.generation != self.generation:
            raise ValueError("M6_FEED_GENERATION_CONFLICT")

    def verify_ticks(self, ticks):
        super().verify_ticks(ticks)
        for token, tick in ticks.items():
            self.research.books[token].tick = Decimal(str(tick))
            self.research.books[token].tick_verified = True

    def book_view(self, token, *, reuse_depth=False):
        b = self.research.books[token]
        bids, asks = self.books[token].bids, self.books[token].asks
        if not reuse_depth:
            bids, asks = tuple(sorted(b.bids.items(), reverse=True)), tuple(sorted(b.asks.items()))
        return TokenBook(token, self.meta.condition_id,
            bids=bids, asks=asks,
            tick=b.tick, snapshot_generation=self.generation if self.research.good[token] else 0,
            source_time=b.source, received_wall=self.wall()-(self.monotonic()-b.received),
            received_mono=b.received, applied_mono=self.monotonic(), frame=self.m1_frame)

    def _apply(self, raw, *, received_mono=None, received_wall=None):
        received = self.monotonic() if received_mono is None else received_mono
        wall = self.wall() if received_wall is None else received_wall
        with self.lock:
            if not self.connected or self.runtime_released:
                return
            self.m1_frame += 1
            self.observed_books = []
            self.updated_depth.clear()
            self.research.apply(raw, now=self.monotonic(), wall=self.wall(), received=received,
                                seq=self.m1_frame, pending=self.pending_receipts())
            for token in self.books:
                self.books[token] = self.book_view(token, reuse_depth=token not in self.updated_depth)
            if self.fill_model is not None:
                for token in self.books:
                    if not self.research.good[token]:
                        self.fill_model.gap(self.research.reasons.get(token, "M6_BOOK_UNAVAILABLE"), token)
                for book in self.observed_books:
                    if self.research.good[book.token_id] and self.research.legal(book.token_id):
                        self.fill_model.on_book(book)
            self.observed_books = []
            self.sequence += 1
            self.last_receipt = self.last_transport_receipt = received
            self.last_wall, self.reason = wall, ""
            self.changed.set()

    def release_signal_memory(self):
        with self.lock:
            if not self.stop_event.is_set() or (self.task is not None and not self.task.done()):
                raise RuntimeError("M6_MEMORY_FEED_NOT_STOPPED")
            released = self.research.release_runtime()
            self.runtime_released = True
            self.m1.events.clear()
            self.m1.opportunities.clear()
            self.m1_pending_received.clear()
            self.m1_processing_received = None
            self.task = None
            return released
