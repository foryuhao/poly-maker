"""Session WS books: apply whole frames and reconcile explicit venue top quotes."""

import asyncio
import json
import math
import time
from collections import Counter, deque
from contextlib import suppress
from dataclasses import dataclass, replace
from decimal import Decimal
from threading import RLock

import websockets

from polymaker.rdp_sm_signal import Episodes, evaluate, number

WS_HEARTBEAT_SEC = 10.0
WS_CLOSE_TIMEOUT_SEC = 1.0


@dataclass(frozen=True)
class TokenBook:
    token_id: str
    market_id: str
    bids: tuple = ()
    asks: tuple = ()
    tick: Decimal = Decimal(".01")
    snapshot_generation: int = 0
    source_time: float = 0
    received_wall: float = 0
    received_mono: float = 0
    applied_mono: float = 0
    frame: int = 0
    reported_bid: Decimal | None = None
    reported_ask: Decimal | None = None

    @property
    def best_bid(self):
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self):
        return self.asks[0][0] if self.asks else None

    @property
    def best_bid_size(self):
        return self.bids[0][1] if self.bids else None

    @property
    def best_ask_size(self):
        return self.asks[0][1] if self.asks else None

    def as_book(self):
        return {
            "token_id": self.token_id,
            "market_id": self.market_id,
            "best_bid": self.best_bid,
            "best_ask": self.best_ask,
            "best_bid_size": self.best_bid_size,
            "best_ask_size": self.best_ask_size,
            "tick": float(self.tick),
            "timestamp": self.source_time,
            "bid_levels": len(self.bids),
            "ask_levels": len(self.asks),
            "received_mono": self.received_mono,
            "applied_mono": self.applied_mono,
        }


@dataclass(frozen=True)
class PairSnapshot:
    market_id: str
    generation: int
    sequence: int
    yes: TokenBook
    no: TokenBook
    valid: bool
    reason: str
    signal_event: float

    @property
    def snapshot_id(self):
        return f"{self.market_id}:{self.generation}:{self.sequence}"

    def books(self):
        return {b.token_id: b.as_book() for b in (self.yes, self.no)}

    def evidence(self):
        # Operation diagnostics copy fixed L1 fields, never the full depth.
        return {
            "snapshot_id": self.snapshot_id,
            "market_id": self.market_id,
            "generation": self.generation,
            "sequence": self.sequence,
            "valid": self.valid,
            "reason": self.reason,
            "signal_event": self.signal_event,
            **{
                label: {
                    **b.as_book(),
                    "snapshot_generation": b.snapshot_generation,
                    "received_wall": b.received_wall,
                    "frame": b.frame,
                }
                for label, b in (("yes", self.yes), ("no", self.no))
            },
        }


@dataclass(frozen=True)
class RepairPricePoint:
    sequence: int
    monotonic: float
    wall: float
    generation: int
    p: float
    yes_bid: str
    yes_ask: str
    no_bid: str
    no_ask: str
    yes_tick: str
    no_tick: str
    path_segment: int = 0


class SessionFeed:
    reader_backpressure_levels: tuple[int, int] | None = None

    def __init__(self, meta, cfg, *, monotonic=time.monotonic, wall=None, proxy=None, ba_cfg=None, opposite_ba_cfg=None,
                 ca_config=None, ca_log_path=None, ca_session_id=None, normal_entry_mode="ba_v1",
                 opposite_ba_execution=None, ca_pullback_config=None, m1_config=None, m1_log_path=None,
                 reader_yield_every=0, book_only=False):
        self.meta, self.cfg, self.monotonic, self.wall = meta, cfg, monotonic, wall or time.time
        self.reader_yield_every = reader_yield_every
        self.normal_entry_mode = normal_entry_mode
        self.book_only = book_only
        self.proxy = proxy
        self.lock = RLock()
        self.generation = self.sequence = 0
        self.connected = False
        self.reason = "WS_NOT_CONNECTED"
        self.books = {
            t: TokenBook(t, meta.condition_id, tick=number(meta.tick_size))
            for t in (meta.yes.token_id, meta.no.token_id)
        }
        self.episodes = Episodes()
        self.ba = None
        if ba_cfg is not None:
            from polymaker.rdp_sm_ba import BidAbsorption

            self.ba = BidAbsorption(self, ba_cfg)
        self.opposite_ba = None
        if opposite_ba_cfg is not None:
            from polymaker.rdp_sm_ba import BidAbsorption

            if opposite_ba_execution is not None and opposite_ba_execution.enabled:
                from polymaker.rdp_sm_repair_ba import RepairBidAbsorption

                self.opposite_ba = RepairBidAbsorption(self, opposite_ba_cfg, opposite_ba_execution)
            else:
                self.opposite_ba = BidAbsorption(self, opposite_ba_cfg)
        self.changed = asyncio.Event()
        self.stop_event = asyncio.Event()
        self.ws = None
        self.task = None
        self.last_receipt = None
        self.last_transport_receipt = None
        self.probability_points = deque(maxlen=128)
        # Preserve intrasecond price changes independently of the sigma buckets.
        # Generations stay explicit; reconnect must not invent a continuous path.
        self.repair_price_points = deque(maxlen=16384)
        self.repair_path_segment = 0
        self.repair_path_valid = False
        self.last_wall = None
        self.tick_revision = 0
        self.tick_verified = {}
        self.signal_event = 0.0
        self.signal_changes = deque(maxlen=4096)
        self.normal_signals = deque(maxlen=max(
            cfg.normal_confirmations_before_60, cfg.normal_confirmations_from_60))
        self._normal_signal_event = None
        self.signal_change_count = 0
        self.last_signal_label = None
        self.last_failure = None
        self.failure_count = 0
        self.notices = Counter()
        self.last_notice = None
        self.ca = None
        self.m1 = self.m1_writer = None
        self.m1_frame = 0
        self.m1_pending_received = deque()
        self.m1_processing_received = None
        if normal_entry_mode == "m1_v1":
            from polymaker.rdp_sm_ca import plain
            from polymaker.rdp_sm_ca_writer import CAWriter
            from polymaker.rdp_sm_m1_signal import M1Signals

            if m1_config is not None and m1_config.record_events and m1_log_path is not None:
                self.m1_writer = CAWriter(m1_log_path, capacity=16384)
            def record_m1(row):
                return self.m1_writer.submit(plain(row)) if self.m1_writer else True
            self.m1 = M1Signals(self, record=record_m1,
                out_of_order_tolerance_ms=(m1_config.out_of_order_tolerance_ms if m1_config else 20),
                signal_config=m1_config.signal if m1_config else None)
        self.ca_start_failure = None
        if ca_config is not None and ca_config.enabled and ba_cfg is not None:
            writer = None
            try:
                from polymaker.rdp_sm_ca import CounterAttack
                from polymaker.rdp_sm_ca_writer import CAWriter

                writer = CAWriter(ca_log_path) if ca_log_path is not None else None
                self.ca = CounterAttack(self, ba_cfg, ca_config, writer=writer, session_id=ca_session_id)
            except Exception as exc:
                self.ca_start_failure = "CA_START_FAILED:"+type(exc).__name__
                if writer is not None:
                    writer.stopping.set()
        from polymaker.rdp_sm_ca_entry import CounterAttackEntry

        self.ca_entry = CounterAttackEntry(self)
        if ca_pullback_config is not None and ca_pullback_config.enabled:
            from polymaker.rdp_sm_ca_pullback import CAPullbackEntry

            self.ca_entry = CAPullbackEntry(self, ca_pullback_config)

    def observe_ca(self, method, *args):
        # Keep CA outside ba_detectors(): BA ordering/depth filtering and REPAIR
        # remain independent. ca_v1 explicitly uses the separate entry adapter.
        with self.lock:
            if self.ca is not None:
                if self.ca.closed and method not in {"diagnostics", "snapshot", "disable", "stop"}:
                    return None
                result = self.ca.safe(method, *args)
                if hasattr(self.ca_entry, "capture_confirmations"):
                    if method == "before_event":
                        self.ca_entry.capture_confirmations()
                        if args[0] == "tick_size_change":
                            self.ca_entry.invalidate("CA_PULLBACK_TICK_CHANGED")
                    elif method in {"depth", "book_updated"}:
                        self.ca_entry.book_updated(args[0])
                    elif method == "tick":
                        self.ca_entry.tick()
                    elif method in {"reset", "invalidate", "disable", "stop"}:
                        self.ca_entry.invalidate("CA_PULLBACK_"+method.upper())
                if method != "snapshot" or result is not None:
                    return result
            if method == "snapshot" and (self.ca is not None or self.ca_start_failure):
                return {"mode": ("NORMAL_ENTRY" if self.normal_entry_mode == "ca_v1" else "OBSERVATION_ONLY"), "data_complete": False,
                        "failure": self.ca.failure if self.ca is not None else self.ca_start_failure,
                        "observed_monotonic": self.monotonic(), "outcomes": {}}
        return None

    def ca_diagnostics(self):
        if self.ca is not None:
            result = self.observe_ca("diagnostics") or {"failure": self.ca.failure, "data_complete": False}
            if hasattr(self.ca_entry, "diagnostics"):
                result["pullback"] = self.ca_entry.diagnostics()
            return result
        return {"failure": self.ca_start_failure, "data_complete": False} if self.ca_start_failure else None

    def record_failure(self, exc, context=None):
        # Preserve the parser/transport cause across generic disconnect states.
        # Transport exception text may contain proxy credentials; retain its type only.
        reason = str(exc).removeprefix("WS_FRAME_REBUILD:")
        if not (reason.startswith("WS_") and reason.replace("_", "").isalnum()):
            reason = type(exc).__name__
        with self.lock:
            identity = (reason, self.generation, self.sequence)
            previous = self.last_failure
            if previous and identity == (previous["reason"], previous["generation"], previous["sequence"]):
                return
            self.failure_count += 1
            self.last_failure = {
                "reason": reason,
                "error_type": type(exc).__name__,
                "generation": self.generation,
                "sequence": self.sequence,
                "occurred_at": time.time(),
            }
            if context:
                self.last_failure["context"] = context

    def notice(self, reason, **context):
        self.notices[reason] += 1
        self.last_notice = {"reason": reason, **context}

    def diagnostics(self):
        with self.lock:
            return {
                "ws_check": True,
                "reason": self.snapshot().reason,
                "generation": self.generation,
                "sequence": self.sequence,
                "failure_count": self.failure_count,
                "last_failure": dict(self.last_failure) if self.last_failure else None,
                "notices": dict(self.notices),
                "last_notice": self.last_notice,
                **({"ba": self.ba.diagnostics()} if self.ba is not None else {}),
                **({"opposite_ba": self.opposite_ba.diagnostics()} if self.opposite_ba is not None else {}),
                **({"ca_observation": self.ca_diagnostics()}
                   if self.ca is not None or self.ca_start_failure else {}),
                **({"m1": self.m1.diagnostics()} if self.m1 is not None else {}),
            }

    def _track_signal(self, signal, event_mono):
        self.episodes.observe(signal)
        key = (self.generation, self.signal_event)
        if not signal.direction:
            self.normal_signals.clear()
        elif key != self._normal_signal_event:
            if self.normal_signals and self.normal_signals[-1].direction != signal.direction:
                self.normal_signals.clear()
            self.normal_signals.append(signal)
        self._normal_signal_event = key
        if signal.label != self.last_signal_label:
            self.last_signal_label = signal.label
            self.signal_change_count += 1
            self.signal_changes.append(
                (
                    self.signal_change_count,
                    event_mono,
                    signal.snapshot_id,
                    signal.label,
                    self.episodes.episode_id,
                )
            )

    def normal_confirmations(self, snapshot, since):
        """Distinct depth events; repeated snapshots cannot add a sample."""
        with self.lock:
            if not snapshot.valid:
                self.normal_signals.clear()
                return ()
            if (snapshot.generation, snapshot.signal_event) != self._normal_signal_event:
                return ()
            return tuple(s for s in self.normal_signals if s.event_mono >= since)

    def changes_since(self, event_mono):
        with self.lock:
            return {
                "changes": [row for row in self.signal_changes if row[1] >= event_mono],
                "history_overwritten": max(0, self.signal_change_count - len(self.signal_changes)),
            }

    def ba_detectors(self):
        return tuple(detector for detector in (self.ba, self.opposite_ba) if detector is not None)

    def connect(self):
        with self.lock:
            if self.m1 is not None:
                self.m1.reset()
                self.m1_pending_received.clear()
            self.observe_ca("reset")
            for detector in self.ba_detectors():
                detector.reset()
            self.generation += 1
            self.normal_signals.clear()
            self._normal_signal_event = None
            self.connected = True
            self.reason = "WS_REBUILD_REQUIRED"
            self.books = {
                t: replace(b, bids=(), asks=(), snapshot_generation=0, source_time=0,
                           received_mono=0, received_wall=0, applied_mono=0,
                           reported_bid=None, reported_ask=None)
                for t, b in self.books.items()
            }
            self.last_receipt = None
            self.last_transport_receipt = self.monotonic()
            self.probability_points.clear()
            self.repair_path_valid = False
            self.last_wall = None
            self.changed.set()

    def invalidate(self, reason):
        with self.lock:
            if self.m1 is not None:
                self.m1.invalidate(reason)
            self.observe_ca("invalidate", "CA_"+reason)
            for detector in self.ba_detectors():
                detector.invalidate(reason)
            self.connected = False
            self.repair_path_valid = False
            self.reason = reason
            self.books = {t: replace(b, snapshot_generation=0) for t, b in self.books.items()}
            if not self.book_only:
                self._track_signal(evaluate(self.snapshot(), self.cfg.threshold), self.monotonic())
            self.changed.set()

    def verify_ticks(self, ticks):
        with self.lock:
            for token, tick in ticks.items():
                tick = number(tick)
                b = self.books[token]
                # A newer WS constraint can never be overwritten by a REST cache.
                if b.snapshot_generation and tick != b.tick:
                    raise ValueError("WS_OFFICIAL_TICK_CONFLICT")
                self.books[token] = replace(b, tick=tick)
                self.tick_verified[token] = tick

    def snapshot(self, *, repair_token=None, repair=False, ba_cfg=None, allow_pending_top=False,
                 taker_buy_token=None):
        now = self.monotonic()
        with self.lock:
            yes, no = (self.books[t] for t in (self.meta.yes.token_id, self.meta.no.token_id))
            reason = self.reason if not self.connected else ""
            if any(b.snapshot_generation != self.generation for b in (yes, no)):
                reason = reason or "WS_REBUILD_REQUIRED"
            signal_cfg = ba_cfg or (self.ba.cfg if self.ba is not None else None)
            needed = ((self.books[taker_buy_token],) if taker_buy_token is not None else
                      (self.books[repair_token],) if repair_token and not repair else (yes, no))
            for b in needed:
                # An immediate BUY needs a current Ask. At .01 there may be no
                # legal Bid; keep this exception explicit and target-specific.
                if not b.asks or (not b.bids and b.token_id != taker_buy_token):
                    reason = reason or "WS_EMPTY_BOOK"
                if b.best_bid is not None and b.best_ask is not None and b.best_bid >= b.best_ask:
                    reason = reason or "WS_CROSSED_BOOK"
                if not allow_pending_top and (
                    (b.reported_bid is not None and b.reported_bid != (b.best_bid or Decimal(0)))
                    or (b.reported_ask is not None and b.reported_ask != (b.best_ask or Decimal(1)))
                ):
                    reason = reason or "WS_TOP_PENDING"
                book_age = (signal_cfg.max_book_age_ms if signal_cfg is not None
                            and (not repair_token or self.m1 is not None) else self.cfg.max_book_age_ms)
                if not repair and not 0 <= (now - b.received_mono) * 1000 <= book_age:
                    reason = reason or "WS_BOOK_STALE"
                if self.tick_verified.get(b.token_id) != b.tick:
                    reason = reason or "WS_TICK_UNVERIFIED"
            if repair and (self.last_transport_receipt is None or
                           not 0 <= now-self.last_transport_receipt <= 2*WS_HEARTBEAT_SEC):
                reason = reason or "WS_TRANSPORT_STALE"
            if not repair and not repair_token and taker_buy_token is None:
                if not (math.isfinite(yes.source_time) and math.isfinite(no.source_time)):
                    # M6 keeps missing source times at -inf until fresh depth arrives.
                    reason = reason or "WS_SOURCE_CLOCK_INVALID"
                else:
                    if signal_cfg is not None:
                        pair_skew = signal_cfg.max_pair_skew_ms
                        source_skew_ms = abs(number(yes.source_time)-number(no.source_time))*1000
                    else:
                        pair_skew = self.cfg.max_pair_skew_ms
                        # Preserve the existing OBI path; BA uses exact milliseconds.
                        source_skew_ms = int(abs(yes.source_time-no.source_time))*1000
                    if (abs(yes.received_mono-no.received_mono)*1000 > pair_skew
                            or source_skew_ms > pair_skew):
                        reason = reason or "WS_PAIR_TIME_SKEW"
            return PairSnapshot(
                self.meta.condition_id,
                self.generation,
                self.sequence,
                yes,
                no,
                not bool(reason),
                reason,
                self.signal_event,
            )

    def _identity(self, row):
        if row.get("market") != self.meta.condition_id:
            raise ValueError("WS_MARKET_IDENTITY")

    def _levels(self, rows, tick):
        if not isinstance(rows, list):
            raise ValueError("WS_LEVELS_INVALID")
        values = {}
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("WS_LEVEL_INVALID")
            p, q = number(row["price"]), number(row["size"])
            if not 0 < p < 1 or p % tick or q <= 0 or p in values:
                raise ValueError("WS_LEVEL_INVALID")
            values[p] = q
        return values

    def _reconcile_top(self, book):
        token = book.token_id
        bids = (book.bids if book.reported_bid is None or not book.bids
                or book.best_bid <= book.reported_bid else
                tuple(level for level in book.bids if level[0] <= book.reported_bid))
        asks = (book.asks if book.reported_ask is None or not book.asks
                or book.best_ask >= book.reported_ask else
                tuple(level for level in book.asks if level[0] >= book.reported_ask))
        if (bids, asks) != (book.bids, book.asks):
            self.notice("WS_TOP_RECONCILED", token_id=token,
                        cached_bid=str(book.best_bid), cached_ask=str(book.best_ask),
                        reported_bid=str(book.reported_bid), reported_ask=str(book.reported_ask))
        if ((book.reported_bid is not None and book.reported_bid != (bids[0][0] if bids else Decimal(0)))
                or (book.reported_ask is not None and book.reported_ask != (asks[0][0] if asks else Decimal(1)))):
            self.notice("WS_TOP_PENDING", token_id=token,
                        available_bid=str(bids[0][0]) if bids else None,
                        available_ask=str(asks[0][0]) if asks else None,
                        reported_bid=str(book.reported_bid), reported_ask=str(book.reported_ask))
        if bids and asks and bids[0][0] >= asks[0][0]:
            self.notice("WS_CROSSED_BOOK", token_id=token,
                        best_bid=str(bids[0][0]), best_ask=str(asks[0][0]))
        return book if bids is book.bids and asks is book.asks else replace(book, bids=bids, asks=asks)

    def m1_watermark(self):
        values = [self.monotonic()]
        if self.m1_pending_received:
            values.append(self.m1_pending_received[0])
        if self.m1_processing_received is not None:
            values.append(self.m1_processing_received)
        return min(values)

    def apply(self, raw, *, received_mono=None, received_wall=None):
        if self.m1 is None:
            return self._apply(raw, received_mono=received_mono, received_wall=received_wall)
        with self.lock:
            self.m1_processing_received = self.monotonic() if received_mono is None else received_mono
            try:
                return self._apply(raw, received_mono=received_mono, received_wall=received_wall)
            finally:
                self.m1_processing_received = None

    def validate_receipt_clock(self, now, received_mono, received_wall):
        if (not all(math.isfinite(v) for v in (now, received_mono, received_wall))
                or received_mono > now
                or (self.last_receipt is not None and received_mono < self.last_receipt)):
            raise ValueError("WS_CLOCK_INVALID")
        if (self.last_receipt is not None and self.last_wall is not None
                and abs((received_wall - self.last_wall) - (received_mono - self.last_receipt)) > 2):
            raise ValueError("WS_WALL_CLOCK_JUMP")

    def _apply(self, raw, *, received_mono=None, received_wall=None):
        """Only the consumer calls apply; SDK workers only call snapshot()."""
        received_mono = self.monotonic() if received_mono is None else received_mono
        received_wall = self.wall() if received_wall is None else received_wall
        if self.m1 is not None:
            self.m1_frame += 1
            if self.m1_writer is not None and not self.m1_writer.submit({
                    "event": "RAW_FRAME", "frame": self.m1_frame, "generation": self.generation,
                    "received_monotonic": received_mono, "received_wall": received_wall,
                    "processed_monotonic": self.monotonic(),
                    "raw": raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw}):
                self.m1.failure = "M1_EVENT_RECORDING_FAILED"
                self.m1.invalidate(self.m1.failure)
        with self.lock:
            if (self.last_transport_receipt is not None
                    and received_mono-self.last_transport_receipt > 2*WS_HEARTBEAT_SEC):
                self.repair_path_valid = False
        if raw in ("PONG", "PING"):
            if self.connected:
                self.last_transport_receipt = received_mono
            return
        row = None
        try:
            with self.lock:
                if not self.connected:
                    raise ValueError("WS_REBUILD_REQUIRED")
                now = self.monotonic()
                self.validate_receipt_clock(now, received_mono, received_wall)
                rows = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
                rows = rows if isinstance(rows, list) else [rows]
                if not rows:
                    raise ValueError("WS_EMPTY_FRAME")
                updated, touched, tick_changed = dict(self.books), set(), False
                # Before any depth can refresh receipts: stale gaps stay latched.
                self.observe_ca("tick")
                for row_index, row in enumerate(rows):
                    if not isinstance(row, dict):
                        raise ValueError("WS_FRAME_INVALID")
                    kind = row.get("event_type")
                    self._identity(row)
                    if kind not in {
                        "book",
                        "price_change",
                        "tick_size_change",
                        "last_trade_price",
                        "best_bid_ask",
                    }:
                        raise ValueError("WS_EVENT_UNKNOWN")
                    if kind == "price_change":
                        changes = row["price_changes"]
                        if not isinstance(changes, list) or not changes:
                            raise ValueError("WS_DELTA_INVALID")
                    else:
                        changes = [row]
                    m1_decisions, row_touched = {}, set()
                    if self.m1 is not None:
                        # Validate against coherent pre-message books. Reported
                        # BBO describes the whole delta message, not each of its
                        # transient intermediate level mutations.
                        for ch in changes:
                            token = ch["asset_id"]
                            if token not in updated:
                                raise ValueError("WS_TOKEN_IDENTITY")
                            if token not in m1_decisions:
                                accepted = self.m1.before_event(kind, row, updated[token],
                                    received_mono, received_wall, books=updated)
                                m1_decisions[token] = accepted, self.m1.decision_reason
                    for change_index, ch in enumerate(changes):
                        token = ch["asset_id"]
                        if token not in updated:
                            raise ValueError("WS_TOKEN_IDENTITY")
                        b = updated[token]
                        m1_ordered, m1_reason = m1_decisions.get(token, (False, None))
                        if self.m1 is not None:
                            self.m1.emit("EVENT_DECISION", frame=self.m1_frame, token_id=token,
                                row_index=row_index, change_index=change_index,
                                kind=kind, received_monotonic=received_mono,
                                accepted=m1_ordered, reason=m1_reason if not m1_ordered else None)
                            if not m1_ordered and kind in {"book", "price_change"} and m1_reason in {
                                    "M1_SOURCE_REGRESSION", "M1_SOURCE_AGE", "M1_PROCESSING_GAP",
                                    "M1_EVENT_OUT_OF_ORDER_IGNORED", "M4_SOURCE_REGRESSION", "M4_SOURCE_AGE",
                                    "M4_PROCESSING_GAP", "M4_EVENT_OUT_OF_ORDER_IGNORED"}:
                                continue  # Rejected old depth must not mutate the shared live book.
                        ca_ordered = self.observe_ca("before_event", kind, row, b,
                                                     received_mono, received_wall)
                        if kind == "last_trade_price":
                            for detector in self.ba_detectors():
                                detector.trade(row, b, received_mono, received_wall)
                            continue
                        if kind == "best_bid_ask":
                            for detector in self.ba_detectors():
                                detector.top(row, b, received_mono, received_wall)
                            continue
                        if kind == "tick_size_change":
                            for detector in self.ba_detectors():
                                detector.invalidate("BA_TICK_CHANGED", token)
                            tick = number(ch["new_tick_size"])
                            if tick == b.tick:
                                continue  # Repeated venue notification is idempotent.
                            if tick <= 0 or number(ch["old_tick_size"]) != b.tick:
                                raise ValueError("WS_TICK_INVALID")
                            updated[token] = replace(b, tick=tick)
                            tick_changed = True
                            continue
                        exact_stamp = number(row["timestamp"]) / 1000
                        stamp = float(exact_stamp)
                        if stamp <= 0 or int(stamp) > int(received_wall) + 2:
                            raise ValueError("WS_SOURCE_CLOCK_INVALID")
                        if self.book_only and (
                            not -.002 - 1e-9 <= self.wall() - stamp <= .500 + 1e-9
                            or stamp < b.source_time
                            or now - received_mono > .200
                        ):
                            continue
                        detectors = self.ba_detectors()
                        if detectors:
                            source_floor = max([number(b.source_time)] + [
                                detector.source_times.get(token, Decimal(0))
                                for detector in detectors])
                            if exact_stamp < source_floor:
                                # Never apply late depth to shared books. Each
                                # detector independently tolerates or retires BA.
                                for detector in detectors:
                                    detector.depth(row, b, b, received_mono, received_wall,
                                                   source_floor=source_floor)
                                self.notice("WS_OLD_SOURCE_IGNORED", token_id=token,
                                            incoming_timestamp=stamp,
                                            previous_timestamp=float(source_floor))
                                continue
                        if int(b.source_time - stamp) > 0:
                            for detector in self.ba_detectors():
                                detector.invalidate("BA_EVENT_OUT_OF_ORDER", token)
                            self.notice("WS_OLD_SOURCE_IGNORED", token_id=token,
                                        incoming_timestamp=stamp, previous_timestamp=b.source_time)
                            continue  # Old seconds do not overwrite newer depth or force reconnect.
                        if kind == "book":
                            bids, asks = (
                                self._levels(row["bids"], b.tick),
                                self._levels(row["asks"], b.tick),
                            )
                        else:
                            if b.snapshot_generation != self.generation:
                                self.notice("WS_DELTA_BEFORE_BOOK_IGNORED", token_id=token)
                                continue  # A live delta cannot replace the initial full snapshot.
                            bids, asks = dict(b.bids), dict(b.asks)
                            p, q = number(ch["price"]), number(ch["size"])
                            if (
                                ch["side"] not in {"BUY", "SELL"}
                                or not 0 < p < 1
                                or p % b.tick
                                or q < 0
                            ):
                                raise ValueError("WS_DELTA_INVALID")
                            side = bids if ch["side"] == "BUY" else asks
                            if q:
                                side[p] = q
                            else:
                                side.pop(p, None)
                        levels = (
                            tuple(sorted(bids.items(), reverse=True)),
                            tuple(sorted(asks.items())),
                        )
                        reported_bid = None if kind == "book" else b.reported_bid
                        reported_ask = None if kind == "book" else b.reported_ask
                        for key in ("best_bid", "best_ask"):
                            if key in ch and ch[key] is not None:
                                value = number(ch[key])
                                if not 0 <= value <= 1:
                                    raise ValueError("WS_TOP_INVALID")
                                if key == "best_bid":
                                    reported_bid = value
                                else:
                                    reported_ask = value
                        updated[token] = replace(
                            b,
                            bids=levels[0],
                            asks=levels[1],
                            source_time=max(stamp, b.source_time),
                            snapshot_generation=self.generation,
                            received_wall=received_wall,
                            received_mono=received_mono,
                            applied_mono=now,
                            frame=self.sequence + 1,
                            reported_bid=reported_bid,
                            reported_ask=reported_ask,
                        )
                        touched.add(token)
                        row_touched.add(token)
                        for detector in self.ba_detectors():
                            detector.depth(row, b, updated[token], received_mono, received_wall)
                        if ca_ordered:
                            self.observe_ca("depth", updated[token])
                        if m1_ordered:
                            self.m1.depth_quantity(updated[token])
                    if (self.m1 is not None and kind == "last_trade_price"
                            and getattr(self.m1.cfg, "mirror_complement_flow", False)):
                        self.m1.mirror_trade(row, updated, received_mono, received_wall)
                    if self.m1 is not None:
                        for token in row_touched:
                            updated[token] = self._reconcile_top(updated[token])
                            self.m1.depth(updated[token], received_mono)
                # Explicit venue BBO bounds supersede cached contradictory depth.
                # Do this after every change in the frame; never invent top quantities.
                if self.m1 is None:
                    for token in touched:
                        updated[token] = self._reconcile_top(updated[token])
                depth_changed = any((updated[t].bids, updated[t].asks) !=
                                    (self.books[t].bids, self.books[t].asks) for t in touched)
                self.last_receipt = received_mono
                self.last_transport_receipt = received_mono
                self.last_wall = received_wall
                self.books = updated
                for token in touched:
                    self.observe_ca("book_updated", updated[token])
                if self.m1 is not None or self.book_only:
                    # M1 never consumes sigma, OBI or the REPAIR price path.
                    # Keep its socket consumer free of these unrelated scans.
                    self.sequence += 1
                    self.tick_revision += tick_changed
                    if depth_changed:
                        self.signal_event = received_mono
                    self.reason = ""
                    self.changed.set()
                    return
                y = updated[self.meta.yes.token_id]
                if (self.meta.yes.token_id in touched and y.best_bid is not None
                        and y.best_ask is not None and y.best_bid < y.best_ask):
                    point = (math.floor(received_wall), received_mono,
                             float((y.best_bid+y.best_ask)/2), self.generation)
                    if self.probability_points and self.probability_points[-1][0] == point[0]:
                        self.probability_points[-1] = point
                    else:
                        self.probability_points.append(point)
                if touched or tick_changed or self.ba_detectors() or self.m1 is not None:
                    self.sequence += 1
                    self.tick_revision += tick_changed
                    if depth_changed:
                        self.signal_event = received_mono
                    self.reason = ""
                    self._track_signal(evaluate(self.snapshot(), self.cfg.threshold), received_mono)
                    snapshot = self.snapshot(repair=True)
                    coherent = (snapshot.valid and abs(
                        (snapshot.yes.best_bid+snapshot.yes.best_ask
                         + snapshot.no.best_bid+snapshot.no.best_ask)/2-1) <= Decimal(".02"))
                    if coherent:
                        if not self.repair_path_valid:
                            self.repair_path_segment += 1
                        self.repair_path_valid = True
                        y, n = snapshot.yes, snapshot.no
                        signature = (str(y.best_bid), str(y.best_ask), str(n.best_bid),
                                     str(n.best_ask), str(y.tick), str(n.tick))
                        previous = self.repair_price_points[-1] if self.repair_price_points else None
                        old_signature = ((previous.yes_bid, previous.yes_ask, previous.no_bid,
                                          previous.no_ask, previous.yes_tick, previous.no_tick)
                                         if previous else None)
                        if (previous is None or previous.generation != self.generation
                                or previous.path_segment != self.repair_path_segment or signature != old_signature):
                            self.repair_price_points.append(RepairPricePoint(
                                self.sequence, received_mono, received_wall, self.generation,
                                float((y.best_bid+y.best_ask)/2), *signature, self.repair_path_segment))
                    else:
                        self.repair_path_valid = False
                    self.changed.set()
        except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
            self.record_failure(exc, {"received_wall": received_wall,
                                     "event_type": row.get("event_type") if isinstance(row, dict) else None,
                                     "timestamp": row.get("timestamp") if isinstance(row, dict) else None,
                                     "previous_source_times": {t: b.source_time for t, b in self.books.items()}})
            self.invalidate(str(exc))
            raise ValueError(f"WS_FRAME_REBUILD:{exc}") from exc

    async def wait_after(self, sequence, timeout):
        self.changed.clear()
        if self.sequence != sequence:
            return
        with suppress(TimeoutError):
            await asyncio.wait_for(self.changed.wait(), max(0.001, timeout))

    async def _connection(self):
        kwargs = {"ping_interval": 5, "ping_timeout": 10, "open_timeout": 10,
                  "close_timeout": WS_CLOSE_TIMEOUT_SEC}
        if self.proxy:
            kwargs["proxy"] = self.proxy
        async with websockets.connect(
            "wss://ws-subscriptions-clob.polymarket.com/ws/market", **kwargs
        ) as ws:
            self.ws = ws
            self.connect()
            await ws.send(json.dumps({"assets_ids": list(self.books), "type": "market"}))
            queue = asyncio.Queue(maxsize=2048)
            high, low = self.reader_backpressure_levels or (0, 0)
            reader_resume = asyncio.Event() if high else None

            async def reader():
                received_count = 0
                async for raw in ws:
                    try:
                        with self.lock:
                            receipt = self.monotonic()
                            queue.put_nowait((raw, receipt, self.wall()))
                            if self.m1 is not None:
                                self.m1_pending_received.append(receipt)
                    except asyncio.QueueFull as exc:
                        self.invalidate("WS_QUEUE_OVERFLOW")
                        raise ValueError("WS_QUEUE_OVERFLOW") from exc
                    received_count += 1
                    if reader_resume is not None and queue.qsize() >= high:
                        # Stop pulling from the socket until the consumer
                        # drains to the low-water mark.
                        reader_resume.clear()
                        await reader_resume.wait()
                    elif self.reader_yield_every and received_count % self.reader_yield_every == 0:
                        # Opt-in fairness for public research burst capture. The
                        # default preserves existing services' scheduling.
                        await asyncio.sleep(0)

            async def consumer():
                while True:
                    raw, mono, wall = await queue.get()
                    try:
                        self.apply(raw, received_mono=mono, received_wall=wall)
                    finally:
                        if self.m1 is not None:
                            with self.lock:
                                self.m1_pending_received.popleft()
                    if reader_resume is not None and queue.qsize() <= low:
                        reader_resume.set()
                    await asyncio.sleep(0)

            async def heartbeat():
                # The market stream requires text PING/PONG in addition to RFC pings.
                while True:
                    await ws.send("PING")
                    await asyncio.sleep(WS_HEARTBEAT_SEC)

            tasks = [asyncio.create_task(reader()), asyncio.create_task(consumer()),
                     asyncio.create_task(heartbeat())]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
            finally:
                try:
                    self.invalidate("WS_DISCONNECTED")
                finally:
                    # Capture overflow may make invalidation itself fail. The
                    # socket still owns all three tasks and must drain them.
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    self.ws = None

    def maintain_signals(self):
        if self.m1 is not None:
            with self.lock:
                self.m1.tick()

    async def run(self):
        async def ca_maintenance():
            while not self.stop_event.is_set():
                self.observe_ca("tick")
                self.maintain_signals()
                await asyncio.sleep(.025)

        maintenance = asyncio.create_task(ca_maintenance(), name="signal-observe-timer") if self.ca or self.m1 else None
        try:
            while not self.stop_event.is_set():
                try:
                    await self._connection()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.record_failure(exc)
                    self.invalidate("WS_RECONNECT_REQUIRED")
                with suppress(TimeoutError):
                    await asyncio.wait_for(self.stop_event.wait(), 1)
        finally:
            if maintenance is not None:
                maintenance.cancel()
                await asyncio.gather(maintenance, return_exceptions=True)

    async def close(self):
        self.stop_event.set()
        self.invalidate("WS_STOPPED")
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        await self.close_ca()
        if self.m1_writer is not None:
            await asyncio.to_thread(self.m1_writer.close)

    async def close_ca(self):
        if self.ca is not None:
            # Freeze CA under the same lock as frame processing. Only disk drain
            # runs in a worker, after order cleanup and with no state mutations.
            self.observe_ca("stop")
            if self.ca.writer is not None:
                try:
                    await asyncio.to_thread(self.ca.writer.close)
                except Exception as exc:
                    self.observe_ca("disable", "CA_LOG_CLOSE_FAILED:"+type(exc).__name__)
