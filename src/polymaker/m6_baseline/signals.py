"""Frozen M6 RF+LW signals bridged to the independent baseline contract.

SessionFeed supplies only socket lifecycle/queueing. Its strategy parser is
overridden: every raw input is interpreted by the frozen M6 research detector.
"""
from collections import Counter, deque
from dataclasses import asdict
from decimal import Decimal

from polymaker.maker_queue import legal
from polymaker.rdp_sm_marketdata import SessionFeed, TokenBook

from ._signal_v7.feed import ResearchFeed
from ._signal_v7.strategies import M5_M10_LOCAL_AP, Strategies
from .types import Opportunity


class ObservedResearchFeed(ResearchFeed):
    def _depth(self, token, row, changes, received):
        super()._depth(token, row, changes, received)
        if self.good[token] and self.legal(token):
            self.owner.observed_books.append(self.owner.book_view(token))


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
        return legal(self, book, now) and c.good[book.token_id] and c.legal(book.token_id)

    def __init__(self, feed, record=None):
        self.feed, self.record = feed, record
        self.counts, self.events = Counter(), deque(maxlen=4096)
        self.failure = self.selected = None
        self.used_roots, self.opportunities = set(), {}

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
                opportunity = Opportunity(candidate["candidate_id"], candidate["token"], candidate["outcome"],
                    Decimal(candidate["anchor_price"]), candidate["confirmed"], candidate["deadline"],
                    candidate["primitive_ids"][0], candidate["root_id"], candidate["generation"])
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


class M6Feed(SessionFeed):
    def __init__(self, meta, cfg, *, record=None, **kwargs):
        super().__init__(meta, cfg, normal_entry_mode="ba_v1", book_only=True, **kwargs)
        self.fill_model, self.observed_books = None, []
        self.runtime_released = False
        self.m1 = M6Signals(self, record)
        tokens = (meta.yes.token_id, meta.no.token_id)
        self.research = ObservedResearchFeed(meta.condition_id, tokens,
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

    def book_view(self, token):
        b = self.research.books[token]
        return TokenBook(token, self.meta.condition_id,
            bids=tuple(sorted(b.bids.items(), reverse=True)), asks=tuple(sorted(b.asks.items())),
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
            self.research.apply(raw, now=self.monotonic(), wall=self.wall(), received=received,
                                seq=self.m1_frame, pending=self.pending_receipts())
            for token in self.books:
                self.books[token] = self.book_view(token)
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
