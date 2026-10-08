"""RDP-local validation and bounded readiness over the shared market WS service."""

from __future__ import annotations

import asyncio
import json
import math
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from polymaker.marketdata.orderbook import BookView
from polymaker.marketdata.parse import TradePrint
from polymaker.marketdata.service import MarketDataService
from polymaker.rdp_live import RDPBootstrap, RDPBootstrapError
from polymaker.strategy.rdp_book_filter import RDPBookFilter


class RDPFeedError(RDPBootstrapError):
    """A categorized fail-closed error while observing the selected public feed."""


@dataclass(frozen=True, slots=True)
class TokenBookEvidence:
    outcome: str
    token_id: str
    view: BookView
    tick_size: float
    tick_source: str


@dataclass(frozen=True, slots=True)
class RDPFeedReadiness:
    condition_id: str
    connection_generation: int
    tokens: tuple[TokenBookEvidence, TokenBookEvidence]
    receipt_ts: float


def _number(value: Any, *, category: str, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise RDPFeedError(category, f"selected feed {field} must be numeric")
    try:
        exact = Decimal(str(value))
        number = float(exact)
    except (InvalidOperation, OverflowError, ValueError) as exc:
        raise RDPFeedError(category, f"selected feed {field} is not representable") from exc
    if not exact.is_finite() or not math.isfinite(number) or Decimal(str(number)) != exact:
        raise RDPFeedError(category, f"selected feed {field} is not finite and representable")
    return number


def _price(value: Any, *, category: str, field: str) -> float:
    result = _number(value, category=category, field=field)
    if not 0.0 < result < 1.0:
        raise RDPFeedError(category, f"selected feed {field} must be inside (0,1)")
    return result


def _tick(value: Any, *, category: str) -> float:
    return _price(value, category=category, field="tick size")


def _quantity(value: Any, *, category: str, field: str) -> float:
    result = _number(value, category=category, field=field)
    if result < 0:
        raise RDPFeedError(category, f"selected feed {field} cannot be negative")
    return result


def _validate_trade(msg: dict[str, Any], condition_id: str,
                    token_ids: tuple[str, ...]) -> str | None:
    """Validate only events attributable to this market before shared parsing."""
    token = msg.get("asset_id")
    market = msg.get("market")
    if token not in token_ids:
        if market == condition_id:
            raise RDPFeedError("MALFORMED_SELECTED_TRADE", "selected-market trade has invalid token identity")
        return None
    if market != condition_id:
        raise RDPFeedError("SELECTED_FRAME_IDENTITY_MISMATCH", "selected trade has wrong market identity")
    side = msg.get("side")
    if not isinstance(side, str) or side.upper() not in ("BUY", "SELL"):
        raise RDPFeedError("MALFORMED_SELECTED_TRADE", "selected trade side is malformed")
    _price(msg.get("price"), category="MALFORMED_SELECTED_TRADE", field="trade price")
    size = _number(msg.get("size"), category="MALFORMED_SELECTED_TRADE", field="trade size")
    if size <= 0:
        raise RDPFeedError("MALFORMED_SELECTED_TRADE", "selected trade size must be positive")
    return token


class RDPMarketDataService(MarketDataService):
    """Reuse shared transport/mutation while validating selected frames first."""

    def __init__(self, bootstrap: RDPBootstrap, *,
                 url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market",
                 single_attempt: bool = False) -> None:
        self.bootstrap = bootstrap
        self.single_attempt = single_attempt
        self.condition_id = bootstrap.market.condition_id
        self.token_ids = tuple(item.token_id for item in bootstrap.token_metadata)
        self._changed = asyncio.Event()
        self._fatal: RDPFeedError | None = None
        self._snapshot_seen: dict[str, int] = {}
        self._tick_source: dict[str, str] = {
            item.token_id: "REST_BOOTSTRAP" for item in bootstrap.token_metadata
        }
        self.book_filter = RDPBookFilter(bootstrap.market)

        def dirty(condition: str, token: str) -> None:
            if condition == self.condition_id and token in self.token_ids:
                book = self.book(token)
                if (book is not None and book.snapshot_generation == self.connection_generation):
                    self.book_filter.observe_book(
                        token, book, self._processing_receipt_monotonic,
                        self.connection_generation, condition_id=condition,
                    )
                self._changed.set()

        def trade(print_: TradePrint) -> None:
            if print_.condition_id == self.condition_id and print_.asset_id in self.token_ids:
                self.book_filter.observe_trade(
                    print_, self._processing_receipt_monotonic, self.connection_generation,
                )

        def connection(_connected: bool) -> None:
            self._changed.set()

        super().__init__(url, on_dirty=dirty, on_trade=trade, on_connection=connection)
        self.set_markets([(self.condition_id, list(self.token_ids))])
        for metadata in bootstrap.token_metadata:
            book = self.book(metadata.token_id)
            assert book is not None
            book.set_tick_size(metadata.tick_size)

    async def run(self) -> None:
        """Optionally fail this RDP observation on its first transport end."""
        if not self.single_attempt:
            await super().run()
            return
        self.disconnected_since = time.time()
        try:
            await self._connect_and_listen()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - health is surfaced by owner category
            self._fatal = RDPFeedError("FEED_SERVICE_FAILED", f"market WS service failed ({type(exc).__name__})")
            self._changed.set()

    def _selected_identity(self, msg: dict[str, Any], token: str) -> None:
        if msg.get("market") != self.condition_id:
            raise RDPFeedError("SELECTED_FRAME_IDENTITY_MISMATCH", "selected token frame has wrong market identity")
        if token not in self.token_ids:
            raise RDPFeedError("SELECTED_FRAME_IDENTITY_MISMATCH", "selected token is outside bootstrap pair")

    def _validate_book(self, msg: dict[str, Any]) -> str | None:
        token = msg.get("asset_id")
        if token not in self.token_ids:
            if isinstance(token, str):
                return None
            raise RDPFeedError("MALFORMED_SELECTED_BOOK", "book snapshot has no string asset_id")
        self._selected_identity(msg, token)
        for side in ("bids", "asks"):
            levels = msg.get(side)
            if not isinstance(levels, list):
                raise RDPFeedError("MALFORMED_SELECTED_BOOK", f"selected book {side} must be a list")
            for level in levels:
                if not isinstance(level, dict) or "price" not in level or "size" not in level:
                    raise RDPFeedError("MALFORMED_SELECTED_BOOK", "selected book level is malformed")
                _price(level["price"], category="MALFORMED_SELECTED_BOOK", field="level price")
                _quantity(level["size"], category="MALFORMED_SELECTED_BOOK", field="level quantity")
        if "tick_size" in msg:
            _tick(msg["tick_size"], category="MALFORMED_SELECTED_BOOK")
        return token

    def _validate_price_change(self, msg: dict[str, Any]) -> tuple[str, ...]:
        changes = msg.get("price_changes")
        if not isinstance(changes, list):
            raise RDPFeedError("MALFORMED_SELECTED_DELTA", "price_changes must be a list")
        selected: list[str] = []
        for entry in changes:
            token = entry.get("asset_id") if isinstance(entry, dict) else None
            if token not in self.token_ids:
                if token is None and msg.get("market") == self.condition_id:
                    raise RDPFeedError("MALFORMED_SELECTED_DELTA", "selected-market delta entry has no token")
                continue
            self._selected_identity(msg, token)
            if not isinstance(entry, dict) or entry.get("side") not in ("BUY", "SELL", "buy", "sell"):
                raise RDPFeedError("MALFORMED_SELECTED_DELTA", "selected delta side is malformed")
            _price(entry.get("price"), category="MALFORMED_SELECTED_DELTA", field="delta price")
            _quantity(entry.get("size"), category="MALFORMED_SELECTED_DELTA", field="delta quantity")
            selected.append(token)
        if selected and msg.get("market") != self.condition_id:
            raise RDPFeedError("SELECTED_FRAME_IDENTITY_MISMATCH", "selected delta has wrong market identity")
        return tuple(selected)

    def _validate_tick(self, msg: dict[str, Any]) -> str | None:
        token = msg.get("asset_id")
        if token not in self.token_ids:
            return None
        self._selected_identity(msg, token)
        raw_tick = msg.get("new_tick_size", msg.get("tick_size"))
        _tick(raw_tick, category="MALFORMED_SELECTED_TICK")
        return token

    async def _process_raw(self, raw: str | bytes) -> None:
        try:
            # Keep JSON number lexemes exact through validation. The shared parser
            # is invoked on the original payload only after all selected values
            # are proven representable by its float-based book model.
            data = json.loads(raw, parse_float=Decimal, parse_int=Decimal)
        except (json.JSONDecodeError, TypeError) as exc:
            self._fail("MALFORMED_SELECTED_FRAME", "market WS frame is not valid JSON", exc)
        messages = data if isinstance(data, list) else [data]
        if not all(isinstance(item, dict) for item in messages):
            # Non-object frames cannot be attributed safely to the selected market.
            self._fail("MALFORMED_SELECTED_FRAME", "market WS frame entries must be objects")

        validated: list[tuple[dict[str, Any], str | tuple[str, ...] | None]] = []
        try:
            for msg in messages:
                event = msg.get("event_type")
                detail: str | tuple[str, ...] | None = None
                if event == "book":
                    detail = self._validate_book(msg)
                elif event == "price_change":
                    detail = self._validate_price_change(msg)
                elif event == "tick_size_change":
                    detail = self._validate_tick(msg)
                elif event == "last_trade_price":
                    detail = _validate_trade(msg, self.condition_id, self.token_ids)
                validated.append((msg, detail))
        except RDPFeedError as exc:
            self._fatal = exc
            self.stop()
            self._changed.set()
            raise

        # Validation is complete for the whole frame before shared handlers mutate.
        await super()._process_raw(raw)
        for msg, detail in validated:
            event = msg.get("event_type")
            if event == "book" and isinstance(detail, str):
                if "tick_size" in msg and msg["tick_size"] is not None:
                    self._tick_source[detail] = "WS_BOOK_SNAPSHOT"
                self._snapshot_seen[detail] = self.connection_generation
                self._changed.set()
            elif event == "price_change" and detail:
                self._changed.set()
            elif event == "tick_size_change" and isinstance(detail, str):
                self._tick_source[detail] = "WS_TICK_SIZE_CHANGE"
                self._changed.set()

    def _on_tick_change(self, msg: dict[str, Any]) -> None:
        # The shared service intentionally does not wake dirty callbacks for ticks.
        super()._on_tick_change(msg)
        token = msg.get("asset_id")
        if token in self.token_ids:
            book = self.book(token)
            if book is not None and book.snapshot_generation == self.connection_generation:
                self.book_filter.observe_book(
                    token, book, self._processing_receipt_monotonic,
                    self.connection_generation, condition_id=self.condition_id,
                )

    def _fail(self, category: str, message: str, cause: Exception | None = None) -> None:
        error = RDPFeedError(category, message)
        self._fatal = error
        self.stop()
        self._changed.set()
        if cause is not None:
            raise error from cause
        raise error

    def _ready(self) -> bool:
        generation = self.connection_generation
        if not self.connected or generation <= 0:
            return False
        for token in self.token_ids:
            book = self.book(token)
            if (book is None or self._snapshot_seen.get(token) != generation
                    or book.snapshot_generation != generation or book.is_empty):
                return False
            bid, ask = book.best_bid(), book.best_ask()
            if bid is None or ask is None or bid.price >= ask.price:
                return False
        return True

    def _capture(self) -> RDPFeedReadiness:
        # No await from the final predicate through copying both views and ticks.
        if not self._ready():
            raise RDPFeedError("FEED_NOT_READY", "selected books are not current and ready")
        generation = self.connection_generation
        rows = []
        for metadata in self.bootstrap.token_metadata:
            book = self.book(metadata.token_id)
            assert book is not None
            rows.append(TokenBookEvidence(metadata.outcome, metadata.token_id, book.view(),
                                          book.tick_size, self._tick_source[metadata.token_id]))
        if not self.connected or generation != self.connection_generation:
            raise RDPFeedError("FEED_NOT_READY", "connection changed during readiness capture")
        return RDPFeedReadiness(self.condition_id, generation, (rows[0], rows[1]), time.time())


async def await_rdp_feed(
    bootstrap: RDPBootstrap,
    *,
    timeout_s: float = 15.0,
    now=time.time,
) -> RDPFeedReadiness:
    """Wait for same-generation books and always stop/await the owned WS task."""
    _readiness_deadline_s(bootstrap, timeout_s, now)
    session = RDPMarketDataSession(bootstrap)
    await session.start()
    try:
        return await session.wait_ready(timeout_s=timeout_s, now=now)
    finally:
        await session.aclose()


class RDPMarketDataSession:
    """Retain one selected-market service after its bounded readiness capture."""

    def __init__(self, bootstrap: RDPBootstrap, *, single_attempt: bool = False) -> None:
        self.service = RDPMarketDataService(bootstrap, single_attempt=single_attempt)
        self.task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self.task is not None:
            raise RuntimeError("RDP market-data session already started")
        self.task = asyncio.create_task(self.service.run(), name="rdp-market-ws")
        self.task.add_done_callback(lambda _task: self.service._changed.set())

    async def wait_ready(
        self,
        *,
        timeout_s: float = 15.0,
        now=time.time,
    ) -> RDPFeedReadiness:
        service = self.service
        task = self.task
        if task is None:
            raise RuntimeError("RDP market-data session is not started")
        bootstrap = service.bootstrap
        deadline_s = _readiness_deadline_s(bootstrap, timeout_s, now)
        deadline = asyncio.get_running_loop().time() + deadline_s
        while True:
            loop = asyncio.get_running_loop()
            if loop.time() >= deadline:
                if now() >= bootstrap.end_epoch:
                    raise RDPFeedError("MARKET_INTERVAL_ELAPSED", "selected BTC5m interval elapsed during WS readiness")
                raise RDPFeedError("FEED_STARTUP_TIMEOUT", "market WS did not produce two current books")
            if now() >= bootstrap.end_epoch:
                raise RDPFeedError("MARKET_INTERVAL_ELAPSED", "selected BTC5m interval elapsed during WS readiness")
            if task.done():
                if service._fatal is not None:
                    raise service._fatal
                if task.cancelled():
                    raise RDPFeedError("FEED_SERVICE_STOPPED", "market WS service was cancelled before readiness")
                failure = task.exception()
                if failure is not None:
                    raise RDPFeedError("FEED_SERVICE_FAILED", "market WS service failed before readiness") from failure
                raise RDPFeedError("FEED_SERVICE_STOPPED", "market WS service stopped before readiness")
            if service._fatal is not None:
                raise service._fatal
            if service._ready():
                if loop.time() >= deadline:
                    raise RDPFeedError("FEED_STARTUP_TIMEOUT", "market WS readiness deadline elapsed before capture")
                if now() >= bootstrap.end_epoch:
                    raise RDPFeedError("MARKET_INTERVAL_ELAPSED", "market expired before readiness capture")
                return service._capture()
            remaining = min(deadline - loop.time(),
                            bootstrap.end_epoch - now())
            if remaining <= 0:
                if now() >= bootstrap.end_epoch:
                    raise RDPFeedError("MARKET_INTERVAL_ELAPSED", "selected BTC5m interval elapsed during WS readiness")
                raise RDPFeedError("FEED_STARTUP_TIMEOUT", "market WS did not produce two current books")
            service._changed.clear()
            try:
                await asyncio.wait_for(service._changed.wait(), timeout=remaining)
            except TimeoutError as exc:
                raise RDPFeedError("FEED_STARTUP_TIMEOUT", "market WS did not produce two current books") from exc

    def failure_category(self, expected_generation: int | None = None) -> str | None:
        if self.service._fatal is not None:
            return self.service._fatal.category
        if self.task is None:
            return "FEED_SERVICE_NOT_STARTED"
        if self.task.done():
            return "FEED_SERVICE_STOPPED"
        if not self.service.connected:
            return "FEED_DISCONNECTED"
        if (expected_generation is not None
                and self.service.connection_generation != expected_generation):
            return "FEED_GENERATION_CHANGED"
        return None

    async def aclose(self) -> None:
        task = self.task
        self.task = None
        self.service.stop()
        if task is not None and not task.done():
            task.cancel()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)


def _readiness_deadline_s(bootstrap: RDPBootstrap, timeout_s: float, now) -> float:
    remaining = bootstrap.end_epoch - now()
    if remaining <= 0:
        raise RDPFeedError("MARKET_INTERVAL_ELAPSED", "selected BTC5m interval ended before WS readiness")
    if not math.isfinite(timeout_s) or timeout_s < 0:
        raise RDPFeedError("INVALID_FEED_TIMEOUT", "market WS readiness timeout must be finite and nonnegative")
    deadline_s = min(timeout_s, remaining)
    if deadline_s <= 0:
        raise RDPFeedError("FEED_STARTUP_TIMEOUT", "market WS readiness deadline is zero")
    return deadline_s
