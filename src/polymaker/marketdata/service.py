"""MarketDataService: owns the market WS, maintains a book per token.

Subscribes to every YES+NO token of the markets we quote and routes each frame
to that token's OrderBook. We do NOT set `custom_feature_enabled` (verified to
broaden the feed beyond our assets); resolution is detected via catalog flags.

On every book mutation it wakes the owning market's quoter via `on_dirty`, and
feeds trade prints to `on_trade` for the flow estimator. Reconnects re-snapshot
automatically because the server sends a fresh `book` on (re)subscribe.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from typing import Any

import websockets

from polymaker.journal import Journal
from polymaker.logging import get_logger
from polymaker.marketdata.orderbook import BookView, OrderBook
from polymaker.marketdata.parse import (
    TradePrint,
    parse_book,
    parse_last_trade,
    parse_price_changes,
    parse_tick_size_change,
)
from polymaker.research.capture import CausalCapture, clock, exchange_us

log = get_logger("marketdata.service")

DirtyCb = Callable[[str, str], None]  # (condition_id, token_id)
TradeCb = Callable[[TradePrint], None]


class MarketDataService:
    def __init__(
        self,
        url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market",
        *,
        on_dirty: DirtyCb | None = None,
        on_trade: TradeCb | None = None,
        journal: Journal | None = None,
        proxy: str | None = None,
        on_connection: Callable[[bool], None] | None = None,
        queue_maxsize: int = 2048,
        research: CausalCapture | None = None,
    ) -> None:
        self._url = url
        self._on_dirty = on_dirty or (lambda _c, _t: None)
        self._on_trade = on_trade or (lambda _tp: None)
        self._journal = journal
        self._proxy = proxy
        self._on_connection = on_connection or (lambda _connected: None)
        self.books: dict[str, OrderBook] = {}
        self._token_condition: dict[str, str] = {}
        self._subs: list[str] = []
        self._ws: Any = None
        self._stop = asyncio.Event()
        self.connected: bool = False
        # wall-clock when the link last went down (0 until the first run). Used
        # for staleness: a QUIET market with a live link is NOT stale — only a
        # genuinely down connection is. Book-mutation recency can't tell the two
        # apart on a thin market, so we gate on connection liveness instead.
        self.disconnected_since: float = 0.0
        self.connected_since: float = 0.0
        self.connection_generation: int = 0
        self._queue_maxsize = queue_maxsize
        self._research = research
        self._receipt_us: int | None = None
        self._receipt_ns: int | None = None
        # Monotonic socket receipt time for the frame currently being applied.
        # RDP may consume this in synchronous callbacks; other strategies ignore it.
        self._processing_receipt_monotonic: float | None = None
        self.metrics = {"received": 0, "processed": 0, "queue_full": 0, "processing_lag_ms": 0.0}

    # ── subscription management ─────────────────────────────────────────
    def set_markets(self, markets: list[tuple[str, list[str]]]) -> None:
        """markets = [(condition_id, [token_ids...])]. Rebuilds the desired set."""
        subs: list[str] = []
        for cond, tokens in markets:
            for tok in tokens:
                self._token_condition[tok] = cond
                self.books.setdefault(tok, OrderBook())
                subs.append(tok)
        self._subs = subs

    def view(self, token_id: str) -> BookView:
        book = self.books.get(token_id)
        return book.view() if book else _empty_view()

    def book(self, token_id: str) -> OrderBook | None:
        return self.books.get(token_id)

    def last_update_ts(self, token_id: str) -> float:
        b = self.books.get(token_id)
        return b.last_update_ts if b else 0.0

    def last_local_ts(self, token_id: str) -> float:
        """Local receive time of the last book mutation (skew-proof staleness)."""
        b = self.books.get(token_id)
        return b.local_ts if b else 0.0

    # ── run loop ────────────────────────────────────────────────────────
    async def run(self) -> None:
        backoff = 1.0
        self.disconnected_since = time.time()  # start the grace clock for first connect
        while not self._stop.is_set():
            try:
                await self._connect_and_listen()
                backoff = 1.0
            except (websockets.ConnectionClosed, OSError) as exc:
                log.warning("market_ws_dropped", err=str(exc), backoff=backoff)
            except Exception as exc:  # noqa: BLE001
                log.error("market_ws_error", err=str(exc))
            if self._stop.is_set():
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    async def _connect_and_listen(self) -> None:
        if not self._subs:
            await asyncio.sleep(1.0)
            return
        # ping_timeout matters: with None a half-dead TCP connection hangs
        # forever. The server answers protocol pings (verified live), so a
        # missing pong within 10s means the link is dead -> reconnect.
        kwargs: dict[str, Any] = {"ping_interval": 5, "ping_timeout": 10, "open_timeout": 10}
        if self._proxy:
            kwargs["proxy"] = self._proxy
        async with websockets.connect(self._url, **kwargs) as ws:
            self._ws = ws
            await ws.send(json.dumps({"assets_ids": self._subs, "type": "market"}))
            self.connected = True
            self.connection_generation += 1
            self.connected_since = time.time()
            self._observe("CONNECTION", "", {"stream": "market", "state": "UP", "subscriptions": tuple(self._subs)})
            self._on_connection(True)
            log.info("market_ws_subscribed", n=len(self._subs))
            queue: asyncio.Queue[tuple[int, float, str | bytes] | tuple[int, float, str | bytes, int]] = asyncio.Queue(maxsize=self._queue_maxsize)
            generation = self.connection_generation
            consumer = asyncio.create_task(self._consume(queue, generation), name="market-ws-consumer")
            try:
                async for raw in ws:
                    self.metrics["received"] += 1
                    try:
                        if self._research is None:
                            queue.put_nowait((generation, time.monotonic(), raw))
                        else:
                            receipt_us, receipt_ns = clock()
                            queue.put_nowait((generation, receipt_ns / 1e9, raw, receipt_us))
                    except asyncio.QueueFull as exc:
                        self.metrics["queue_full"] += 1
                        log.error("market_ws_queue_full", depth=queue.qsize(), maxsize=self._queue_maxsize,
                                  received=self.metrics["received"], processed=self.metrics["processed"])
                        raise RuntimeError("market WS local processing queue full") from exc
            finally:
                consumer.cancel()
                await asyncio.gather(consumer, return_exceptions=True)
                self.connected = False
                self.disconnected_since = time.time()
                self._observe("CONNECTION", "", {"stream": "market", "state": "DOWN"})
                self._on_connection(False)

    async def _consume(self, queue: asyncio.Queue[tuple[int, float, str | bytes] | tuple[int, float, str | bytes, int]], generation: int) -> None:
        last_stats = time.monotonic()
        while True:
            item = await queue.get()
            if len(item) == 3:  # existing direct-consumer callers lack receipt wall time
                queued_generation, received_at, raw = item
                receipt_us = None
            else:
                queued_generation, received_at, raw, receipt_us = item
            try:
                if queued_generation == generation == self.connection_generation and self.connected:
                    self._receipt_us = receipt_us
                    self._receipt_ns = int(received_at * 1e9) if receipt_us is not None else None
                    self._processing_receipt_monotonic = received_at
                    try:
                        await self._process_raw(raw)
                    finally:
                        self._receipt_us = None
                        self._receipt_ns = None
                        self._processing_receipt_monotonic = None
                    self.metrics["processed"] += 1
                    self.metrics["processing_lag_ms"] = (time.monotonic() - received_at) * 1000
            finally:
                queue.task_done()
            now = time.monotonic()
            if now - last_stats >= 5:
                log.info("market_ws_ingest_stats", received=self.metrics["received"],
                         processed=self.metrics["processed"], queue_depth=queue.qsize(),
                         processing_lag_ms=round(self.metrics["processing_lag_ms"], 1),
                         queue_full=self.metrics["queue_full"])
                last_stats = now
            # Let the socket reader run even when the bounded queue stays nonempty.
            await asyncio.sleep(0)

    async def _process_raw(self, raw: str | bytes) -> None:
        self._handle(raw)

    def stop(self) -> None:
        self._stop.set()

    # ── message handling ────────────────────────────────────────────────
    def _handle(self, raw: str | bytes) -> None:
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return
        for msg in data if isinstance(data, list) else [data]:
            if not isinstance(msg, dict):
                continue
            self._dispatch(msg)

    def _dispatch(self, msg: dict[str, Any]) -> None:
        et = msg.get("event_type")
        if et == "book":
            self._on_book(msg)
        elif et == "price_change":
            self._on_price_change(msg)
        elif et == "last_trade_price":
            self._on_last_trade(msg)
        elif et == "tick_size_change":
            self._on_tick_change(msg)

    def _on_book(self, msg: dict[str, Any]) -> None:
        upd = parse_book(msg)
        if upd is None or upd.asset_id not in self.books:
            return
        book = self.books[upd.asset_id]
        if upd.tick_size:
            book.set_tick_size(upd.tick_size)
        book.apply_snapshot(upd.bids, upd.asks, upd.ts, upd.book_hash)
        book.snapshot_generation = self.connection_generation
        if self._research is not None:
            self._observe("BOOK", upd.condition_id, {"token_id": upd.asset_id,
                "bids": upd.bids, "asks": upd.asks, "book_hash": upd.book_hash,
                "tick_size": book.tick_size, "snapshot_generation": book.snapshot_generation}, upd.ts)
        self._journal_write("book", msg, upd.ts)
        self._wake(upd.asset_id)

    def _on_price_change(self, msg: dict[str, Any]) -> None:
        changes = parse_price_changes(msg)
        touched: set[str] = set()
        for ch in changes:
            book = self.books.get(ch.asset_id)
            if book is None:
                continue
            book.apply_delta(ch.side, ch.price, ch.size, ch.ts)
            if self._research is not None:
                self._observe("DELTA", ch.condition_id, {"token_id": ch.asset_id,
                    "side": ch.side.value, "price": ch.price, "size": ch.size}, ch.ts)
            touched.add(ch.asset_id)
        if changes:
            self._journal_write("price_change", msg, changes[0].ts)
        for tok in touched:
            self._wake(tok)

    def _on_last_trade(self, msg: dict[str, Any]) -> None:
        tp = parse_last_trade(msg)
        if tp is None or tp.asset_id not in self.books:
            return
        self._journal_write("last_trade_price", msg, tp.ts)
        if self._research is not None:
            self._observe("TRADE", tp.condition_id, {"token_id": tp.asset_id,
                "side": tp.aggressor.value, "price": tp.price, "size": tp.size}, tp.ts)
        self._on_trade(tp)

    def _on_tick_change(self, msg: dict[str, Any]) -> None:
        tc = parse_tick_size_change(msg)
        if tc and tc.asset_id in self.books:
            self.books[tc.asset_id].set_tick_size(tc.tick_size)
            if self._research is not None:
                self._observe("TICK", self._token_condition.get(tc.asset_id, ""),
                    {"token_id": tc.asset_id, "tick_size": tc.tick_size})
            log.info("tick_size_change", token=tc.asset_id[:12], tick=tc.tick_size)

    def _wake(self, token_id: str) -> None:
        cond = self._token_condition.get(token_id)
        if cond:
            self._on_dirty(cond, token_id)

    def _journal_write(self, kind: str, payload: dict[str, Any], ts: float) -> None:
        if self._journal is not None:
            self._journal.write(kind, payload, ts)

    def _observe(self, kind: str, condition_id: str, payload: dict[str, Any],
                 event_ts: float | None = None) -> None:
        if self._research is not None:
            try:
                self._research.emit(kind, condition_id, payload, source="MARKET_WS",
                    exchange_at_us=exchange_us(event_ts), received_at_us=self._receipt_us,
                    received_monotonic_ns=self._receipt_ns,
                    connection_generation=self.connection_generation)
            except Exception:
                # Only Research preparation is inside this boundary. The applied
                # market mutation and its normal wake must always continue.
                self._research.failures += 1


def _empty_view() -> BookView:
    return BookView(None, 0.0, None, 0.0, None, None, 0.0, 0.0)
