"""UserStream: authenticated user WS for our order/trade lifecycle events.

Subscribes with L2 creds and the condition_ids we trade; routes fills and order
updates into the StateStore via the UserEventProcessor. Reconnects with backoff.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sys
import time
from collections.abc import Callable
from typing import Any

import websockets

from polymaker.journal import Journal
from polymaker.logging import get_logger
from polymaker.research.capture import CausalCapture, clock
from polymaker.state.tracker import UserEventProcessor
from polymaker.userstream.parse import (
    maker_order_id_evidence,
    normalize_order,
    normalize_trade,
    parse_trade,
)

log = get_logger("userstream.client")


class _SanitizedWebSocketLogger:
    """Per-connection WebSockets logger that never formats wire payloads."""

    def __init__(self, target: logging.Logger) -> None:
        self._target = target

    def isEnabledFor(self, level: int) -> bool:
        return self._target.isEnabledFor(level)

    def log(self, level: int, message: str, *args: Any, **kwargs: Any) -> None:
        """Implement LoggerLike.log while discarding untrusted message content."""
        if level >= logging.ERROR:
            self.error(message, *args, **kwargs)
        elif level >= logging.WARNING:
            self.warning(message, *args, **kwargs)
        elif level >= logging.INFO:
            self.info(message, *args, **kwargs)
        else:
            self.debug(message, *args, **kwargs)

    def debug(self, message: str, *args: Any, **kwargs: Any) -> None:
        direction = {"< %s": "INBOUND", "> %s": "OUTBOUND"}.get(message)
        if direction is not None:
            self._target.debug("user_ws_protocol_frame_redacted", extra={"direction": direction})
        else:
            self._target.debug("user_ws_protocol_diagnostic_redacted")

    def info(self, message: str, *args: Any, **kwargs: Any) -> None:
        self._target.info("user_ws_protocol_diagnostic_redacted")

    def warning(self, message: str, *args: Any, **kwargs: Any) -> None:
        self._target.warning("user_ws_protocol_diagnostic_redacted")

    def error(self, message: str, *args: Any, **kwargs: Any) -> None:
        exception_type = None
        info = kwargs.get("exc_info")
        if isinstance(info, tuple) and info and isinstance(info[0], type):
            exception_type = info[0].__name__
        elif info:
            current = sys.exc_info()
            exception_type = current[0].__name__ if current[0] is not None else "WEBSOCKET_PROTOCOL_EXCEPTION"
        self._target.error(
            "user_ws_protocol_error_redacted",
            extra={"exception_type": exception_type or "UNKNOWN"},
        )

    def exception(self, message: str, *args: Any, **kwargs: Any) -> None:
        self.error(message, *args, **kwargs)


class UserStreamError(RuntimeError):
    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


class UserStream:
    def __init__(
        self,
        creds: Any,
        our_address: str,
        processor: UserEventProcessor,
        *,
        other_token: Callable[[str], str | None],
        condition_of_token: Callable[[str], str | None],
        url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/user",
        journal: Journal | None = None,
        proxy: str | None = None,
        on_reconnect: Callable[[], None] | None = None,
        on_connection: Callable[[bool], None] | None = None,
        research: CausalCapture | None = None,
        application_heartbeat: bool = False,
        heartbeat_interval_s: float = 10.0,
        heartbeat_timeout_s: float = 15.0,
        sanitize_errors: bool = False,
    ) -> None:
        self._creds = creds
        self._address = our_address
        self._proc = processor
        self._other_token = other_token
        self._condition_of_token = condition_of_token
        self._url = url
        self._journal = journal
        self._proxy = proxy
        self._on_reconnect = on_reconnect or (lambda: None)
        self._on_connection = on_connection or (lambda _connected: None)
        self._markets: list[str] = []
        self._stop = asyncio.Event()
        # Connection health. ping_timeout guarantees a dead link flips
        # `connected` to False within ~15s, so the engine can go blind-safe.
        self.connected: bool = False
        self.disconnected_since: float = time.time()
        self._ever_connected = False
        self.connection_generation = 0
        self._research = research
        self._receipt_us: int | None = None
        self._receipt_ns: int | None = None
        self._application_heartbeat = application_heartbeat
        self._heartbeat_interval_s = heartbeat_interval_s
        self._heartbeat_timeout_s = heartbeat_timeout_s
        self._sanitize_errors = sanitize_errors
        self._heartbeat_event = asyncio.Event()
        self._heartbeat_pending = False
        self._heartbeat_pending_generation: int | None = None
        self.heartbeat_receipt_ts: float | None = None
        self.heartbeat_receipt_monotonic: float | None = None
        self.heartbeat_generation: int | None = None
        self.failure_category: str | None = None
        self.subscription_sent = False
        self._active_ws: Any | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._protocol_logger = (
            _SanitizedWebSocketLogger(
                logging.getLogger(f"polymaker.userstream.rdp_protocol.{id(self):x}")
            )
            if self._application_heartbeat and self._sanitize_errors else None
        )

    def set_markets(self, condition_ids: list[str]) -> None:
        self._markets = condition_ids

    async def run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                await self._connect_and_listen()
                backoff = 1.0
            except (websockets.ConnectionClosed, OSError) as exc:
                log.warning("user_ws_dropped", err=str(exc))
            except Exception as exc:  # noqa: BLE001
                log.error("user_ws_error", err=str(exc))
            if self._stop.is_set():
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    async def run_once(self) -> None:
        """Own one connection attempt without reconnecting; intended for bounded probes."""
        try:
            await self._connect_and_listen()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - sanitize only opt-in probe diagnostics
            if self._application_heartbeat:
                if self.failure_category is None:
                    self.failure_category = "USER_STREAM_CONNECTION_FAILED"
                self._heartbeat_event.set()
                if self._sanitize_errors:
                    log.error("user_ws_observation_failed", category=self.failure_category,
                              exception_type=type(exc).__name__)
                    raise UserStreamError(self.failure_category) from None
            raise
        finally:
            if (self._application_heartbeat and self.failure_category is None
                    and self.heartbeat_generation != self.connection_generation):
                self.failure_category = "USER_STREAM_ENDED_BEFORE_HEARTBEAT"
                self._heartbeat_event.set()

    async def wait_for_application_pong(self, timeout_s: float) -> None:
        if not self._application_heartbeat:
            raise UserStreamError("APPLICATION_HEARTBEAT_DISABLED")
        try:
            await asyncio.wait_for(self._heartbeat_event.wait(), timeout=timeout_s)
        except TimeoutError as exc:
            raise UserStreamError("APPLICATION_PONG_TIMEOUT") from exc
        if self.failure_category is not None:
            raise UserStreamError(self.failure_category)
        if not self.heartbeat_current:
            raise UserStreamError("CURRENT_GENERATION_PONG_UNAVAILABLE")

    @property
    def heartbeat_current(self) -> bool:
        receipt = self.heartbeat_receipt_monotonic
        return (
            self._application_heartbeat and self.connected and self.failure_category is None
            and self.heartbeat_generation == self.connection_generation
            and receipt is not None
            and time.monotonic() - receipt <= self._heartbeat_timeout_s
        )

    async def _connect_and_listen(self) -> None:
        sub = {
            "type": "user",
            "auth": {
                "apiKey": self._creds.api_key,
                "secret": self._creds.api_secret,
                "passphrase": self._creds.api_passphrase,
            },
            "markets": self._markets,
        }
        kwargs: dict[str, Any] = {"ping_interval": 5, "ping_timeout": 10, "open_timeout": 10}
        if self._proxy:
            kwargs["proxy"] = self._proxy
        if self._protocol_logger is not None:
            kwargs["logger"] = self._protocol_logger
        async with websockets.connect(self._url, **kwargs) as ws:
            self._active_ws = ws
            self.connection_generation += 1
            generation = self.connection_generation
            self.subscription_sent = False
            self.heartbeat_receipt_ts = None
            self.heartbeat_receipt_monotonic = None
            self.heartbeat_generation = None
            self.failure_category = None
            self._heartbeat_pending = False
            self._heartbeat_pending_generation = None
            self._heartbeat_event.clear()
            is_connected = False
            try:
                await ws.send(json.dumps(sub))
                self.subscription_sent = True
                self.connected = True
                is_connected = True
                self._observe_connection("UP")
                self._on_connection(True)
                is_reconnect = self._ever_connected
                self._ever_connected = True
                log.info("user_ws_subscribed", markets=len(self._markets), reconnect=is_reconnect)
                if is_reconnect:
                    # events during the gap are LOST (no replay) — the engine must
                    # force a REST reconcile to recover any missed fills/cancels
                    self._on_reconnect()
                if self._application_heartbeat:
                    self._heartbeat_task = asyncio.create_task(
                        self._application_heartbeat_loop(ws, generation),
                        name=f"user-ws-app-heartbeat-{generation}",
                    )
                async for raw in ws:
                    if self._application_heartbeat and await self._handle_probe_control(raw, ws, generation):
                        continue
                    if self._research is not None:
                        self._receipt_us, self._receipt_ns = clock()
                    try:
                        self._handle(raw)
                    finally:
                        self._receipt_us = None
                        self._receipt_ns = None
            finally:
                self.connected = False
                if is_connected:
                    self.disconnected_since = time.time()
                    self._observe_connection("DOWN")
                    self._on_connection(False)
                if self._application_heartbeat and self.failure_category is None:
                    self.failure_category = "USER_STREAM_DISCONNECTED"
                    self._heartbeat_event.set()
                heartbeat_task = self._heartbeat_task
                self._heartbeat_task = None
                if heartbeat_task is not None:
                    heartbeat_task.cancel()
                    await _drain_cancelled_task(heartbeat_task)
                self._active_ws = None

    async def _application_heartbeat_loop(self, ws: Any, generation: int) -> None:
        next_send = time.monotonic()
        outstanding_since: float | None = None
        while self.connected and generation == self.connection_generation:
            # A PONG may be received after the cadence wait expires but before
            # this task is scheduled again. Consume that receipt before a new
            # send can clear the event or inherit the answered PING's deadline.
            if self._heartbeat_event.is_set() and self.failure_category is None:
                self._heartbeat_event.clear()
                if not self._heartbeat_pending and self.heartbeat_generation == generation:
                    outstanding_since = None

            now = time.monotonic()
            if now >= next_send:
                self._heartbeat_event.clear()
                if outstanding_since is None:
                    outstanding_since = now
                self._heartbeat_pending = True
                self._heartbeat_pending_generation = generation
                try:
                    await ws.send("PING")
                except asyncio.CancelledError:
                    raise
                except Exception:
                    await self._fail_probe_connection("APPLICATION_PING_SEND_FAILED", ws)
                    return
                # Preserve fixed cadence. If the event loop was delayed, skip
                # missed slots instead of emitting a burst of catch-up PINGs.
                next_send += self._heartbeat_interval_s
                now = time.monotonic()
                if next_send <= now:
                    missed = int((now - next_send) // self._heartbeat_interval_s) + 1
                    next_send += missed * self._heartbeat_interval_s

            response_deadline = (
                outstanding_since + self._heartbeat_timeout_s
                if outstanding_since is not None else None
            )
            wake_at = next_send if response_deadline is None else min(next_send, response_deadline)
            timeout = max(0.0, wake_at - time.monotonic())
            try:
                await asyncio.wait_for(self._heartbeat_event.wait(), timeout=timeout)
            except TimeoutError:
                if (response_deadline is not None
                        and time.monotonic() >= response_deadline):
                    await self._fail_probe_connection("APPLICATION_PONG_TIMEOUT", ws)
                    return
                continue
            if self.failure_category is not None:
                return
            self._heartbeat_event.clear()
            outstanding_since = None

    async def _handle_probe_control(self, raw: str | bytes, ws: Any, generation: int) -> bool:
        frame: str | None
        if isinstance(raw, bytes):
            try:
                frame = raw.decode("utf-8")
            except UnicodeDecodeError:
                frame = None
        else:
            frame = raw if isinstance(raw, str) else None
        if frame is not None and frame.strip() == "PONG":
            if (self._heartbeat_pending and self._heartbeat_pending_generation == generation
                    and self.connection_generation == generation):
                self._heartbeat_pending = False
                self.heartbeat_generation = generation
                self.heartbeat_receipt_ts = time.time()
                self.heartbeat_receipt_monotonic = time.monotonic()
                self._heartbeat_event.set()
            return True
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return False
        rows = payload if isinstance(payload, list) else [payload]
        for row in rows:
            if not isinstance(row, dict):
                continue
            kind = str(row.get("type", row.get("event_type", ""))).strip().lower()
            if (kind in {"error", "auth_error", "authentication_error", "subscription_error"}
                    or kind.startswith(("auth_", "subscription_"))
                    or (kind == "" and row.get("error") not in (None, "", False))):
                await self._fail_probe_connection("USER_SUBSCRIPTION_REJECTED", ws)
                return True
        return False

    async def _fail_probe_connection(self, category: str, ws: Any) -> None:
        if self.failure_category is None:
            self.failure_category = category
        self._heartbeat_pending = False
        self._heartbeat_event.set()
        with contextlib.suppress(Exception):
            await ws.close()

    def stop(self) -> None:
        self._stop.set()

    def _handle(self, raw: str | bytes) -> None:
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return
        for msg in data if isinstance(data, list) else [data]:
            if not isinstance(msg, dict):
                continue
            et = msg.get("event_type")
            if et == "trade":
                self._on_trade(msg)
            elif et == "order":
                self._on_order(msg)

    def _on_trade(self, msg: dict[str, Any]) -> None:
        self._journal_write("user_trade", msg)
        try:
            parse_trade(msg)
        except ValueError as exc:
            rows = msg.get("maker_orders")
            exact_ids: set[str] = set()
            if isinstance(rows, list):
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    exact_ids.update(maker_order_id_evidence(row).ids)
            self._proc.on_malformed_trade(exact_ids, f"malformed WS trade: {exc}")
            return
        for ev in normalize_trade(msg, self._address, self._other_token):
            cond = self._condition_of_token(ev.token_id) or str(msg.get("market", ""))
            self._proc.on_trade(ev, cond, received_at_us=self._receipt_us,
                received_monotonic_ns=self._receipt_ns,
                connection_generation=self.connection_generation)

    def _on_order(self, msg: dict[str, Any]) -> None:
        self._journal_write("user_order", msg)
        ev = normalize_order(msg)
        if ev is not None:
            cond = self._condition_of_token(ev.token_id) or str(msg.get("market", ""))
            self._proc.on_order(ev, cond, received_at_us=self._receipt_us,
                received_monotonic_ns=self._receipt_ns,
                connection_generation=self.connection_generation)
        else:
            oid = msg.get("id", msg.get("order_id"))
            if isinstance(oid, str) and oid.strip():
                self._proc.quarantine_order(oid.strip(), "malformed owned order event")

    def _journal_write(self, kind: str, payload: dict[str, Any]) -> None:
        if self._journal is not None:
            self._journal.write(kind, payload, _ts(payload))

    def _observe_connection(self, state: str) -> None:
        if self._research is not None:
            self._research.emit("CONNECTION", "", {"stream": "user", "state": state},
                source="USER_WS", connection_generation=self.connection_generation)


def _ts(msg: dict[str, Any]) -> float:
    raw = msg.get("timestamp")
    try:
        v = float(raw)  # type: ignore[arg-type]
        return v / 1000.0 if v > 1e12 else v
    except (ValueError, TypeError):
        return 0.0


async def _drain_cancelled_task(task: asyncio.Task[Any]) -> None:
    """Keep awaiting an owned task if its parent receives repeated cancellation."""
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.done():
                break
