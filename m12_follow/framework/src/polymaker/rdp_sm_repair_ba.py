"""Strict BA formation with a separate, bounded REPAIR execution opportunity.

Formation still ends on any anchor move. Once confirmed, REPAIR prices from
the current ask without an anchor-relative upper cap. Down-breaks still end
execution; neither price moves nor later BA episodes renew its original
first-SELL deadline or consume it more than once.
"""

from collections import deque
from dataclasses import asdict, replace

from polymaker.rdp_sm_ba import BidAbsorption
from polymaker.rdp_sm_signal import Signal


class RepairBidAbsorption(BidAbsorption):
    fixed_ask_quote = True
    # A sent fixed quote does not depend on depth and BBO messages staying
    # aligned. These checks still invalidate every unsubmitted opportunity.
    post_consistency_reasons = frozenset(
        {"REPAIR_BA_BOOK_INVALID", "REPAIR_BA_TOP_CHANGED", "WS_TOP_PENDING"}
    )

    def __init__(self, feed, cfg, execution):
        super().__init__(feed, cfg)
        self.execution = execution.model_copy(deep=True)
        self.opportunities = {}
        self.opportunity_ticks = {}
        self.execution_events = deque(maxlen=1024)
        self.execution_overflow = False
        self.ignored_book_checks = {}

    def execution_record(self, ep, event, book=None, *, check_reason=None):
        if len(self.execution_events) == self.execution_events.maxlen:
            self.execution_events.clear()
            self.execution_overflow = True
            for opportunity in self.opportunities.values():
                opportunity.invalid_reason = "REPAIR_BA_EXECUTION_LOG_OVERFLOW"
        self.execution_events.append(
            {
                "event": event,
                **asdict(ep),
                "observed_monotonic": self.feed.monotonic(),
                "price_tolerance": None,
                "max_price": None,
                **({"check_reason": check_reason} if check_reason else {}),
                **(
                    {
                        "best_bid": str(book.best_bid),
                        "best_ask": str(book.best_ask),
                        "tick": str(book.tick),
                        "reported_bid": str(book.reported_bid),
                        "reported_ask": str(book.reported_ask),
                        "verified_tick": str(self.feed.tick_verified.get(ep.token_id)),
                        "verified_top": str(self.tops.get(ep.token_id)),
                    }
                    if book is not None
                    else {}
                ),
            }
        )

    def finish(self, ep, reason, book=None):
        if ep.invalid_reason is None:
            if ep.consumed and reason in self.post_consistency_reasons:
                seen = self.ignored_book_checks.setdefault(ep.episode_id, set())
                if reason not in seen:
                    seen.add(reason)
                    self.execution_record(
                        ep, "BOOK_CHECK_IGNORED_AFTER_SUBMIT", book, check_reason=reason
                    )
                return
            ep.invalid_reason = reason
            self.execution_record(ep, "INVALIDATED", book)

    def record(self, ep, event):
        overflow = len(self.events) >= 1024
        super().record(ep, event)
        if overflow:
            for opportunity in self.opportunities.values():
                self.finish(opportunity, "REPAIR_BA_FORMATION_LOG_OVERFLOW")
        if event == "CONFIRMED" and ep.invalid_reason is None and not self.execution_overflow:
            now = self.feed.monotonic()
            for key, old in list(self.opportunities.items()):
                if old.order_deadline <= now:
                    self.opportunities.pop(key)
                    self.opportunity_ticks.pop(key)
                    self.ignored_book_checks.pop(key, None)
            if len(self.opportunities) >= 256:
                self.execution_overflow = True
                for old in self.opportunities.values():
                    self.finish(old, "REPAIR_BA_HISTORY_OVERFLOW")
                return
            opportunity = replace(ep)
            self.opportunities[ep.episode_id] = opportunity
            self.opportunity_ticks[ep.episode_id] = self.feed.books[ep.token_id].tick
            self.execution_record(opportunity, "CONFIRMED", self.feed.books[ep.token_id])

    def invalidate(self, reason, token=None):
        super().invalidate(reason, token)
        # These terminate strict formation segments. Execution separately checks
        # the actual new book and its own original deadline, without renewing it.
        if reason in {"BA_ANCHOR_INVALID", "BA_ORDER_DEADLINE", "BA_OBSERVATION_TIMEOUT"}:
            return
        for ep in self.opportunities.values():
            if token is None or token == ep.token_id:
                self.finish(ep, "REPAIR_" + reason)

    def reset(self):
        super().reset()
        self.opportunities.clear()
        self.opportunity_ticks.clear()
        self.execution_overflow = False
        self.ignored_book_checks.clear()

    def validate_opportunity(self, ep, book):
        if ep.invalid_reason:
            return ep.invalid_reason
        now = self.feed.monotonic()
        trusted = self.legal(book, now)
        for reason, bad in (
            ("REPAIR_BA_EXECUTION_LOG_OVERFLOW", self.execution_overflow),
            ("REPAIR_BA_ORDER_DEADLINE", now >= ep.order_deadline),
            (
                "REPAIR_BA_GENERATION_CHANGED",
                ep.generation != self.feed.generation
                or book.snapshot_generation != self.feed.generation,
            ),
            ("REPAIR_BA_TICK_CHANGED", book.tick != self.opportunity_ticks[ep.episode_id]),
            (
                "REPAIR_BA_TICK_UNVERIFIED",
                self.feed.tick_verified.get(ep.token_id) != book.tick,
            ),
            ("REPAIR_BA_BOOK_EMPTY", book.best_bid is None or book.best_ask is None),
            (
                "REPAIR_BA_BOOK_CROSSED",
                book.best_bid is not None
                and book.best_ask is not None
                and book.best_bid >= book.best_ask,
            ),
            (
                "REPAIR_BA_BOOK_STALE",
                not 0 <= (now - book.received_mono) * 1000 <= self.cfg.max_book_age_ms,
            ),
            (
                "REPAIR_BA_SOURCE_STALE",
                not 0 <= (self.feed.wall() - book.source_time) * 1000 <= self.cfg.max_sell_age_ms,
            ),
            ("REPAIR_BA_BOOK_INVALID", not trusted),
            (
                "REPAIR_BA_BID_BELOW_ANCHOR",
                trusted and book.best_bid < ep.anchor_bid_price,
            ),
        ):
            if not bad:
                continue
            self.finish(ep, reason, book)
            if ep.consumed and reason in self.post_consistency_reasons:
                continue
            return reason
        return None

    def depth(self, row, old, new, mono, wall, *, source_floor=0):
        for ep in self.opportunities.values():
            if ep.token_id == old.token_id:
                self.validate_opportunity(ep, old)
        super().depth(row, old, new, mono, wall, source_floor=source_floor)
        # Validate every intermediate depth, before a later same-frame update can
        # hide a down-break, crossed book or an untrusted data segment.
        for ep in self.opportunities.values():
            if ep.token_id == new.token_id:
                self.validate_opportunity(ep, new)

    def evaluate(self, snapshot, *, outcome=None):
        with self.feed.lock:
            valid = []
            for ep in self.opportunities.values():
                self.validate_opportunity(ep, self.feed.books[ep.token_id])
                if (
                    not ep.invalid_reason
                    and not ep.consumed
                    and snapshot.valid
                    and ep.generation == snapshot.generation
                    and (outcome is None or ep.outcome == outcome)
                    and 0
                    <= (self.feed.monotonic() - ep.last_sell_mono) * 1000
                    <= self.cfg.max_sell_age_ms
                ):
                    valid.append(ep)
            if outcome is None and len({e.outcome for e in valid}) > 1:
                for ep in valid:
                    self.finish(ep, "REPAIR_BA_BOTH_SIDES_VALID")
                valid = []
            claimed = next(
                (ep for ep in valid if self.claimed and ep.episode_id == self.claimed[0]), None
            )
            self.selected = claimed or max(valid, key=lambda ep: ep.signal_start_mono, default=None)
            self.episode_id = self.selected.episode_id if self.selected else None
            self.episode_start = self.selected.signal_start_mono if self.selected else None
            return Signal(
                "VALID_" + self.selected.outcome if self.selected else "NONE",
                snapshot.snapshot_id,
                event_mono=self.episode_start,
            )

    def current(self, claim):
        with self.feed.lock:
            return (
                super().current(claim)
                and not self.execution_overflow
                and self.validate_opportunity(
                    self.selected, self.feed.books[self.selected.token_id]
                )
                is None
            )

    def consume(self, claim):
        with self.feed.lock:
            super().consume(claim)
            source = self.states.get(self.selected.token_id)
            if source is not None and source.episode_id == claim[0]:
                source.consumed = True
            self.execution_record(
                self.selected, "SUBMITTED", self.feed.books[self.selected.token_id]
            )
            if self.execution_overflow or self.selected.invalid_reason:
                raise ValueError(self.selected.invalid_reason or "REPAIR_BA_EXECUTION_LOG_OVERFLOW")

    def active_reason(self, claim, snapshot):
        with self.feed.lock:
            ep = self.opportunities.get(claim[0]) if claim else None
            if ep is None:
                return "REPAIR_BA_OPPORTUNITY_CHANGED"
            if not snapshot.valid:
                self.finish(
                    ep, snapshot.reason or "REPAIR_BA_BOOK_INVALID", self.feed.books[ep.token_id]
                )
            return self.validate_opportunity(ep, self.feed.books[ep.token_id])

    def drain_execution(self):
        with self.feed.lock:
            rows = list(self.execution_events)
            self.execution_events.clear()
            return rows

    def diagnostics(self):
        with self.feed.lock:
            return {
                **super().diagnostics(),
                "execution_policy": "FIXED_ASK_MINUS_TICK",
                "price_tolerance": None,
                "execution_overflow": self.execution_overflow,
                "execution_opportunities": [asdict(ep) for ep in self.opportunities.values()],
            }
