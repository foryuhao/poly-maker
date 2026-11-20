"""Bridge the frozen raw M21 detector to the shared live Taker feed."""

import hashlib
import sys
from collections import Counter, deque
from dataclasses import asdict
from decimal import Decimal
from importlib import import_module
from pathlib import Path
from uuid import uuid4

from polymaker.m4_live_numeric import age_us, live_book_legal
from polymaker.m4_types import M21_VERSION, Opportunity
from polymaker.rdp_sm_marketdata import SessionFeed, TokenBook

# The frozen detector and its raw feed share the research package layout.
RESEARCH = Path(__file__).resolve().parents[2] / "research"
if str(RESEARCH) not in sys.path:
    sys.path.append(str(RESEARCH))

from sop_signals.feed import Feed
from sop_signals.m21 import Parameters, signal_version

# Baseline anchor72-20261119T033533, implementation 14bf9299742807f7.
DETECTOR_SHA256 = {
    "sop_signals.feed": "db56445ddfd0200f8416cba58057fc728da8b03737b392a69909adf31964c127",
    "sop_signals.m21": "77d00c877e52964041599cb44ee74ab3f6c387b696745d97133d944ca2f9905d",
    "sop_signals.m19_path": "0d56dab774c8b8b28de35373bea1f428de534156bde4bde0dda1883449719386",
    "sop_signals.m12": "9aa915d225900edfebdae53151b323a696831659d53ffdc1330a856c735641b6",
    "maker_signals.model": "41a0e8e3c976cd447f58dbe99de2d3407756e60b89e42f163c41e0b3353d7b1e",
}


def detector_manifest():
    for module, expected in DETECTOR_SHA256.items():
        source = Path(import_module(module).__file__).resolve()
        if (source != RESEARCH / (module.replace(".", "/") + ".py")
                or hashlib.sha256(source.read_bytes()).hexdigest() != expected):
            raise ValueError(f"M21_DETECTOR_SOURCE_CONFLICT:{module}")
    if signal_version(Parameters()) != M21_VERSION:
        raise ValueError("M21_DETECTOR_VERSION_CONFLICT")
    return DETECTOR_SHA256.copy()


class LiveResearchFeed(Feed):
    source_order_policy = "M21_STRICT_SOURCE_ORDER_RAW_BUY_PRESENCE_V1"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tick_changes = set()
        self.updated_depth = set()
        self.record_input_rejections = False
        self.frame_received = None
        self.last_rejection_sample = {}

    def depth(self, token, *args, **kwargs):
        self.updated_depth.add(token)
        super().depth(token, *args, **kwargs)

    def connect(self, **kwargs):
        self.tick_changes.clear()
        super().connect(**kwargs)

    def unavailable(self, token, reason, *, rebuild=False):
        if reason == "TICK_CHANGED":
            self.tick_changes.add(token)
        super().unavailable(token, reason, rebuild=rebuild)

    def legal(self, token):
        book = self.books[token]
        return (self.connected and book.complete and book.tick_verified
                and book.generation == self.generation and 0 < book.bid < book.ask < 1
                and 0 <= age_us(self.now, book.received) <= 200_000
                and -2_000 <= age_us(self.wall, book.source) <= 500_000)

    def source(self, token, row):
        if "timestamp" not in row:
            self.unavailable(token, "SOURCE_TIME_MISSING", rebuild=True)
            return None
        stamp = float(Decimal(str(row["timestamp"])) / 1000)
        source_age = age_us(self.wall, stamp)
        if not -2_000 <= source_age <= 500_000:
            self.record_clock_rejection(token, "EVENT_SOURCE_AGE", stamp, source_age)
            self.unavailable(token, "EVENT_SOURCE_AGE", rebuild=True)
            return None
        previous = self.sources.get(token)
        if previous is not None and age_us(stamp, previous) < 0:
            self.record_clock_rejection(token, "UNRECONSTRUCTABLE_SOURCE_ORDER", stamp, source_age)
            self.unavailable(token, "UNRECONSTRUCTABLE_SOURCE_ORDER", rebuild=True)
            return None
        self.sources[token] = stamp
        return stamp

    def record_clock_rejection(self, token, reason, stamp, source_age):
        if not self.record_input_rejections:
            return
        key = token, reason
        if self.now - self.last_rejection_sample.get(key, -float("inf")) < 1:
            return
        self.last_rejection_sample[key] = self.now
        self.event("LIVE_INPUT_CLOCK_REJECTION", {
            "token": token, "reason": reason, "source_wall": stamp, "consumed_wall": self.wall,
            "source_age_us": source_age, "received_monotonic": self.frame_received,
            "application_queue_us": age_us(self.now, self.frame_received),
            "previous_source_wall": self.sources.get(token),
        })


class M21Signals:
    m21_only = True

    def __init__(self, feed, settings):
        self.feed, self.settings = feed, settings
        self.price_risk = settings.live_taker_price_risk
        self.record = self.failure = self.selected = None
        self.counts, self.events = Counter(), deque(maxlen=4096)
        self.used_roots, self.opportunities = set(), {}
        self.run_id = uuid4().hex

    def emit(self, event, **payload):
        self.counts[event] += 1
        row = {"event": event, "strategy": self.settings.strategy, "generation": self.feed.generation,
               "available_monotonic": self.feed.monotonic(), **payload}
        self.events.append(row)
        if self.record is not None and self.record(row) is False:
            self.failure = "M21_EVENT_RECORDING_FAILED"
            self.selected = None

    def evidence(self, kind, evidence):
        event = evidence["event"]
        payload = evidence["payload"]
        if event == "JOINT_ROUND_STARTED":
            root = self.run_id + ":" + payload["id"]
            self.emit("CANDIDATE", evidence={**evidence, "payload": {**payload, "id": root, "root_id": root}})
        elif event == "SIGNAL_CONFIRMED" and not self.failure:
            token = payload["token"]
            episode = self.run_id + ":" + payload["id"]
            root = self.run_id + ":" + payload["root_id"]
            opportunity = Opportunity(
                episode, token, self.feed.outcomes[token], payload["anchor_price"],
                # The detector's per-token book epoch remains in signal_evidence.
                payload["confirmed"], payload["deadline"], root, root, self.feed.generation,
                price_risk=self.price_risk, taker_strategy=self.settings.taker_strategy,
                market_id=self.feed.meta.condition_id,
            )
            self.opportunities[payload["id"]] = opportunity
            self.emit("OPPORTUNITY_IDENTIFIED", opportunity=asdict(opportunity), signal_evidence=evidence)
        else:
            self.emit(kind, evidence=evidence)
            if event == "SIGNAL_ENDED":
                self.opportunities.pop(payload["id"], None)

    def legal(self, book, now):
        research = self.feed.research
        return (live_book_legal(self, book, now) and research.good[book.token_id]
                and research.legal(book.token_id))

    def tick(self, **kwargs):
        if self.failure or self.feed.stop_event.is_set() or self.feed.runtime_released:
            return
        with self.feed.lock:
            self.feed.research.timer(now=self.feed.monotonic(), wall=self.feed.wall(),
                                     seq=self.feed.m1_frame, pending=self.feed.pending_receipts())

    def invalidate(self, reason, token=None):
        self.selected = None
        if self.feed.runtime_released:
            return
        if token is None:
            self.feed.research.gap(reason)
        else:
            self.feed.research.unavailable(token, reason, rebuild=True)

    def reset(self):
        self.invalidate("GENERATION_CHANGED")

    def evaluate(self, snapshot=None):
        self.tick()
        choices = (p for p in self.opportunities.values() if p.root_id not in self.used_roots
                   and age_us(p.order_deadline, self.feed.monotonic()) > 0)
        self.selected = None if self.failure else min(
            choices, key=lambda p: (p.signal_start_mono, p.outcome, p.episode_id), default=None,
        )

    def active_reason(self, claim, snapshot=None):
        self.tick()
        if self.failure:
            return self.failure
        current = next((p for p in self.opportunities.values() if p.episode_id == claim[0]), None)
        if current is None or not self.feed.connected or current.generation != self.feed.generation:
            return "M21_EVIDENCE_ENDED"
        if age_us(current.order_deadline, self.feed.monotonic()) <= 0:
            return "M21_SIGNAL_DEADLINE"
        return None

    def take(self, opportunity):
        with self.feed.lock:
            if opportunity.root_id in self.used_roots or self.active_reason((opportunity.episode_id,)):
                return False
            self.used_roots.add(opportunity.root_id)
            return True

    def diagnostics(self):
        return {"strategy": "M21", "failure": self.failure, "counts": dict(self.counts)}


class M21Feed(SessionFeed):
    def __init__(self, meta, cfg, *, taker_settings, **kwargs):
        detector_manifest()  # Restored openings take saved settings directly.
        super().__init__(meta, cfg, book_only=True, **kwargs)
        self.runtime_released = False
        self.observe_until_market_close = False
        self.input_revision = 0
        self.frame_age_sample_us = deque(maxlen=2048)
        self.frame_age_peak_us = self.frame_age_over_200ms = self.observed_frames = 0
        self.outcomes = {meta.yes.token_id: "YES", meta.no.token_id: "NO"}
        self.m1 = M21Signals(self, taker_settings)
        self.research = LiveResearchFeed(meta.condition_id, self.outcomes,
            {token: book.tick for token, book in self.books.items()},
            self.m1.evidence, Parameters(), strategy="M21")
        for book in self.research.books.values():
            book.tick_verified = False

    def connect(self):
        super().connect()
        self.research.connect(now=self.monotonic(), wall=self.wall(), seq=self.m1_frame)
        if self.generation != self.research.generation:
            raise ValueError("M21_FEED_GENERATION_CONFLICT")

    def verify_ticks(self, ticks):
        with self.lock:
            for token, tick in ticks.items():
                book = self.research.books[token]
                if token in self.research.tick_changes and book.tick != Decimal(str(tick)):
                    self.m1.emit("LIVE_OBSERVATION_GAP", reason="M21_WS_OFFICIAL_TICK_CONFLICT", token_id=token)
                    continue
                super().verify_ticks({token: tick})
                book.tick, book.tick_verified = self.tick_verified[token], True
                self.research.tick_changes.discard(token)

    def _apply(self, raw, *, received_mono=None, received_wall=None):
        with self.lock:
            if not self.connected or self.runtime_released:
                return
            now, wall = self.monotonic(), self.wall()
            received = now if received_mono is None else received_mono
            self.research.frame_received = received
            if self.observe_until_market_close:
                age = age_us(now, received)
                self.observed_frames += 1
                self.frame_age_sample_us.append(age)
                self.frame_age_peak_us = max(self.frame_age_peak_us, age)
                self.frame_age_over_200ms += age > 200_000
            self.m1_frame += 1
            self.research.updated_depth.clear()
            self.research.apply(raw, now=now, wall=wall, received=received, seq=self.m1_frame,
                                pending=self.pending_receipts())
            for token, book in self.research.books.items():
                if not book.tick_verified:
                    self.tick_verified.pop(token, None)
                bids, asks = self.books[token].bids, self.books[token].asks
                if token in self.research.updated_depth:
                    bids, asks = tuple(sorted(book.bids.items(), reverse=True)), tuple(sorted(book.asks.items()))
                self.books[token] = TokenBook(
                    token, self.meta.condition_id, bids=bids, asks=asks,
                    tick=book.tick, snapshot_generation=self.generation if self.research.good[token] else 0,
                    source_time=book.source, received_wall=wall - (now - book.received),
                    received_mono=book.received, applied_mono=now, frame=self.m1_frame,
                )
            self.sequence += 1
            self.last_receipt = self.last_transport_receipt = received
            self.last_wall = wall if received_wall is None else received_wall
            self.reason = ""
            self.changed.set()

    def market_input_health(self):
        with self.lock:
            ages = sorted(self.frame_age_sample_us)
            return {"application_frames": self.observed_frames, "connection_generation": self.generation,
                    "recent_application_queue_p95_us": ages[(len(ages) - 1) * 95 // 100] if ages else None,
                    "recent_frame_sample_count": len(ages), "application_queue_peak_us": self.frame_age_peak_us,
                    "application_queue_over_200ms": self.frame_age_over_200ms}

    def release_signal_memory(self):
        with self.lock:
            if not self.stop_event.is_set():
                raise RuntimeError("M21_MEMORY_FEED_NOT_STOPPED")
            released = self.research.release_runtime()
            self.runtime_released = True
            self.m1.events.clear()
            self.m1.opportunities.clear()
            self.m1.used_roots.clear()
            return released
