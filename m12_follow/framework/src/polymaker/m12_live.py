"""Adapt the frozen M12 overlap detector to M421's common signal contract."""

import hashlib
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from polymaker.m21_live import RESEARCH, Feed, M21Feed
from polymaker.m4_live_numeric import age_us
from polymaker.m4_types import Opportunity

from sop_signals import m12_overlap

M12_VERSION = "m12-live-support-weak-ask-overlap-v1/raw-path-v1/p-9c8515b130c7"
M12_SOURCE_SHA256 = "4b0924deeba337c08d643e68b81b4088c8a38782cce8f57b9194f96f30a7285f"


def detector_manifest():
    # M421 also verifies the shared Feed and inherited M12 core via M21's
    # manifest. Pin the overlap implementation and its baseline parameters.
    source = Path(m12_overlap.__file__).resolve()
    if (source != RESEARCH / "sop_signals/m12_overlap.py"
            or hashlib.sha256(source.read_bytes()).hexdigest() != M12_SOURCE_SHA256
            or m12_overlap.signal_version(m12_overlap.Parameters()) != M12_VERSION):
        raise ValueError("M12_DETECTOR_SOURCE_OR_VERSION_CONFLICT")
    return {"version": M12_VERSION, "source_sha256": M12_SOURCE_SHA256}


class M12Signals:
    def __init__(self, feed, settings):
        self.feed, self.settings = feed, settings
        self.record = None
        self.run_id = uuid4().hex
        self.opportunities, self.used_roots = {}, set()
        self.tick_changes = set()
        self.research = Feed(feed.meta.condition_id, feed.outcomes,
                             {token: book.tick for token, book in feed.books.items()},
                             self.evidence, m12_overlap.Parameters(), strategy="M12")
        for book in self.research.books.values():
            book.tick_verified = False

    @property
    def counts(self):
        counts = self.research.counts.copy()
        counts["OPPORTUNITY_IDENTIFIED"] = counts["SIGNAL_CONFIRMED"]
        return counts

    def evidence(self, _kind, evidence):
        event, payload = evidence["event"], evidence["payload"]
        if event == "QUALITY_UNKNOWN" and payload["reason"] == "TICK_CHANGED":
            self.tick_changes.add(payload["token"])
        if event not in {"SIGNAL_CONFIRMED", "SIGNAL_ENDED"}:
            return
        key = self.run_id + ":" + payload["id"]
        row = {"event": "SOP_EVIDENCE", "evidence": evidence,
               "strategy": self.settings.strategy, "generation": self.feed.generation,
               "available_monotonic": evidence["available"]}
        if event == "SIGNAL_CONFIRMED":
            token = payload["token"]
            root = self.run_id + ":" + payload["root_id"]
            opportunity = Opportunity(
                key, token, self.feed.outcomes[token], payload["anchor_price"],
                payload["confirmed"], payload["deadline"], root, root, self.feed.generation,
                price_risk=self.settings.live_taker_price_risk,
                taker_strategy=self.settings.taker_strategy, market_id=self.feed.meta.condition_id,
            )
            self.opportunities[key] = opportunity
            row.update(event="OPPORTUNITY_IDENTIFIED", opportunity=asdict(opportunity),
                       signal_evidence=evidence)
            del row["evidence"]
        else:
            self.opportunities.pop(key, None)
        if self.record is not None:
            self.record(row)

    def tick(self, *, now, wall, pending):
        if not self.feed.stop_event.is_set() and not self.feed.runtime_released:
            self.research.timer(now=now, wall=wall, seq=self.feed.m1_frame, pending=pending)

    def active_reason(self, claim, snapshot=None):
        current = self.opportunities.get(claim[0])
        if current is None or not self.feed.connected or current.generation != self.feed.generation:
            return "M12_EVIDENCE_ENDED"
        now = self.feed.monotonic()
        if age_us(current.order_deadline, now) <= 0:
            return "M12_SIGNAL_DEADLINE"
        token = current.token_id
        if not (self.research.good[token] and self.research.books[token].legal(
                now, self.feed.wall(), self.research.generation)):
            return "M12_BOOK_QUALITY_EXPIRED"
        return None

    def take(self, opportunity):
        if opportunity.root_id in self.used_roots or self.active_reason((opportunity.episode_id,)):
            return False
        self.used_roots.add(opportunity.root_id)
        return True


class M42112Feed(M21Feed):
    """Keep M421's execution book and consume the same decoded frame for M12."""

    def __init__(self, *args, taker_settings, **kwargs):
        self.m12_manifest = detector_manifest()
        super().__init__(*args, taker_settings=taker_settings, **kwargs)
        self.m12 = M12Signals(self, taker_settings)

    def connect(self):
        with self.lock:
            super().connect()
            self.m12.research.connect(now=self.monotonic(), wall=self.wall(), seq=self.m1_frame)

    def invalidate(self, reason):
        with self.lock:
            super().invalidate(reason)
            research = self.m12.research
            research.clock(now=self.monotonic(), wall=self.wall(), seq=self.m1_frame)
            research.gap(reason)

    def verify_ticks(self, ticks):
        with self.lock:
            super().verify_ticks(ticks)
            research = self.m12.research
            research.clock(now=self.monotonic(), wall=self.wall(), seq=self.m1_frame)
            for token, value in ticks.items():
                book, tick = research.books[token], Decimal(str(value))
                if token in self.m12.tick_changes and book.tick != tick:
                    continue
                book.tick, book.tick_verified = tick, True
                self.m12.tick_changes.discard(token)

    def apply_signal_frame(self, raw, *, now, wall, received, pending):
        # Consume in the same order as publication, with the same receive clock,
        # availability clock and pending watermark for the two research feeds.
        super().apply_signal_frame(raw, now=now, wall=wall, received=received, pending=pending)
        self.m12.research.apply(raw, now=now, wall=wall, received=received,
                                seq=self.m1_frame, pending=pending)

    def release_signal_memory(self):
        with self.lock:
            released = super().release_signal_memory()
            self.m12.research.release_runtime()
            self.m12.opportunities.clear()
            self.m12.used_roots.clear()
            return released
