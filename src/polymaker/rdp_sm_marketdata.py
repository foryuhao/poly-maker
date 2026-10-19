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
    def __init__(self, meta, cfg, *, monotonic=time.monotonic, wall=None, proxy=None):
        self.meta, self.cfg, self.monotonic, self.wall = meta, cfg, monotonic, wall or time.time
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
        self.signal_change_count = 0
        self.last_signal_label = None
        self.last_failure = None
        self.failure_count = 0
        self.notices = Counter()
        self.last_notice = None

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
            }

    def _track_signal(self, signal, event_mono):
        self.episodes.observe(signal)
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

    def changes_since(self, event_mono):
        with self.lock:
            return {
                "changes": [row for row in self.signal_changes if row[1] >= event_mono],
                "history_overwritten": max(0, self.signal_change_count - len(self.signal_changes)),
            }

    def connect(self):
        with self.lock:
            self.generation += 1
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
            self.connected = False
            self.repair_path_valid = False
            self.reason = reason
            self.books = {t: replace(b, snapshot_generation=0) for t, b in self.books.items()}
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

    def snapshot(self, *, repair_token=None, repair=False):
        now = self.monotonic()
        with self.lock:
            yes, no = (self.books[t] for t in (self.meta.yes.token_id, self.meta.no.token_id))
            reason = self.reason if not self.connected else ""
            if any(b.snapshot_generation != self.generation for b in (yes, no)):
                reason = reason or "WS_REBUILD_REQUIRED"
            needed = (self.books[repair_token],) if repair_token and not repair else (yes, no)
            for b in needed:
                if not b.bids or not b.asks:
                    reason = reason or "WS_EMPTY_BOOK"
                if b.best_bid is not None and b.best_ask is not None and b.best_bid >= b.best_ask:
                    reason = reason or "WS_CROSSED_BOOK"
                if ((b.reported_bid is not None and b.reported_bid != (b.best_bid or Decimal(0)))
                        or (b.reported_ask is not None and b.reported_ask != (b.best_ask or Decimal(1)))):
                    reason = reason or "WS_TOP_PENDING"
                if not repair and not 0 <= (now - b.received_mono) * 1000 <= self.cfg.max_book_age_ms:
                    reason = reason or "WS_BOOK_STALE"
                if self.tick_verified.get(b.token_id) != b.tick:
                    reason = reason or "WS_TICK_UNVERIFIED"
            if repair and (self.last_transport_receipt is None or
                           not 0 <= now-self.last_transport_receipt <= 2*WS_HEARTBEAT_SEC):
                reason = reason or "WS_TRANSPORT_STALE"
            if not repair and not repair_token and (
                abs(yes.received_mono - no.received_mono) * 1000 > self.cfg.max_pair_skew_ms
                or int(abs(yes.source_time - no.source_time)) * 1000 > self.cfg.max_pair_skew_ms
            ):
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

    def apply(self, raw, *, received_mono=None, received_wall=None):
        """Only the consumer calls apply; SDK workers only call snapshot()."""
        received_mono = self.monotonic() if received_mono is None else received_mono
        received_wall = self.wall() if received_wall is None else received_wall
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
                if (
                    not all(math.isfinite(v) for v in (now, received_mono, received_wall))
                    or received_mono > now
                    or self.last_receipt is not None
                    and received_mono < self.last_receipt
                ):
                    raise ValueError("WS_CLOCK_INVALID")
                if (
                    self.last_receipt is not None
                    and self.last_wall is not None
                    and abs((received_wall - self.last_wall) - (received_mono - self.last_receipt))
                    > 2
                ):
                    raise ValueError("WS_WALL_CLOCK_JUMP")
                rows = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
                rows = rows if isinstance(rows, list) else [rows]
                if not rows:
                    raise ValueError("WS_EMPTY_FRAME")
                updated, touched, tick_changed = dict(self.books), set(), False
                for row in rows:
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
                    for ch in changes:
                        token = ch["asset_id"]
                        if token not in updated:
                            raise ValueError("WS_TOKEN_IDENTITY")
                        b = updated[token]
                        if kind in {"last_trade_price", "best_bid_ask"}:
                            continue
                        if kind == "tick_size_change":
                            tick = number(ch["new_tick_size"])
                            if tick == b.tick:
                                continue  # Repeated venue notification is idempotent.
                            if tick <= 0 or number(ch["old_tick_size"]) != b.tick:
                                raise ValueError("WS_TICK_INVALID")
                            updated[token] = replace(b, tick=tick)
                            tick_changed = True
                            continue
                        stamp = float(number(row["timestamp"]) / 1000)
                        if stamp <= 0 or int(stamp) > int(received_wall) + 2:
                            raise ValueError("WS_SOURCE_CLOCK_INVALID")
                        if int(b.source_time - stamp) > 0:
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
                # Explicit venue BBO bounds supersede cached contradictory depth.
                # Do this after every change in the frame; never invent top quantities.
                for token in touched:
                    b = updated[token]
                    bids = tuple(level for level in b.bids
                                 if b.reported_bid is None or level[0] <= b.reported_bid)
                    asks = tuple(level for level in b.asks
                                 if b.reported_ask is None or level[0] >= b.reported_ask)
                    if (bids, asks) != (b.bids, b.asks):
                        self.notice("WS_TOP_RECONCILED", token_id=token,
                                    cached_bid=str(b.best_bid), cached_ask=str(b.best_ask),
                                    reported_bid=str(b.reported_bid), reported_ask=str(b.reported_ask))
                    updated[token] = replace(b, bids=bids, asks=asks)
                    if ((b.reported_bid is not None and b.reported_bid != (bids[0][0] if bids else Decimal(0)))
                            or (b.reported_ask is not None and b.reported_ask != (asks[0][0] if asks else Decimal(1)))):
                        self.notice("WS_TOP_PENDING", token_id=token,
                                    available_bid=str(bids[0][0]) if bids else None,
                                    available_ask=str(asks[0][0]) if asks else None,
                                    reported_bid=str(b.reported_bid), reported_ask=str(b.reported_ask))
                    if bids and asks and bids[0][0] >= asks[0][0]:
                        self.notice("WS_CROSSED_BOOK", token_id=token,
                                    best_bid=str(bids[0][0]), best_ask=str(asks[0][0]))
                depth_changed = any((updated[t].bids, updated[t].asks) !=
                                    (self.books[t].bids, self.books[t].asks) for t in touched)
                self.last_receipt = received_mono
                self.last_transport_receipt = received_mono
                self.last_wall = received_wall
                self.books = updated
                y = updated[self.meta.yes.token_id]
                if (self.meta.yes.token_id in touched and y.best_bid is not None
                        and y.best_ask is not None and y.best_bid < y.best_ask):
                    point = (math.floor(received_wall), received_mono,
                             float((y.best_bid+y.best_ask)/2), self.generation)
                    if self.probability_points and self.probability_points[-1][0] == point[0]:
                        self.probability_points[-1] = point
                    else:
                        self.probability_points.append(point)
                if touched or tick_changed:
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

            async def reader():
                async for raw in ws:
                    try:
                        queue.put_nowait((raw, self.monotonic(), self.wall()))
                    except asyncio.QueueFull as exc:
                        self.invalidate("WS_QUEUE_OVERFLOW")
                        raise ValueError("WS_QUEUE_OVERFLOW") from exc

            async def consumer():
                while True:
                    raw, mono, wall = await queue.get()
                    self.apply(raw, received_mono=mono, received_wall=wall)
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
                self.invalidate("WS_DISCONNECTED")
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                self.ws = None

    async def run(self):
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

    async def close(self):
        self.stop_event.set()
        self.invalidate("WS_STOPPED")
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
