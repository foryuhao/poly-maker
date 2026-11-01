"""UserStream: authenticated user WS for our order/trade lifecycle events.

Subscribes with L2 creds and the condition_ids we trade; routes fills and order
updates into the StateStore via the UserEventProcessor. Reconnects with backoff.
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
from polymaker.research.capture import CausalCapture, clock
from polymaker.state.tracker import UserEventProcessor
from polymaker.userstream.parse import (
    maker_order_id_evidence,
    parse_trade,
)

log = get_logger("userstream.client")


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
        async with websockets.connect(self._url, **kwargs) as ws:
            await ws.send(json.dumps(sub))
            self.connected = True
            self.connection_generation += 1
            self._observe_connection("UP")
            self._on_connection(True)
            is_reconnect = self._ever_connected
            self._ever_connected = True
            log.info("user_ws_subscribed", markets=len(self._markets), reconnect=is_reconnect)
            if is_reconnect:
                # events during the gap are LOST (no replay) — the engine must
                # force a REST reconcile to recover any missed fills/cancels
                self._on_reconnect()
            try:
                async for raw in ws:
                    if self._research is not None:
                        self._receipt_us, self._receipt_ns = clock()
                    try:
                        self._handle(raw)
                    finally:
                        self._receipt_us = None
                        self._receipt_ns = None
            finally:
                self.connected = False
                self.disconnected_since = time.time()
                self._observe_connection("DOWN")
                self._on_connection(False)

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
            trade = parse_trade(msg)
            events = self._proc.events_from_trade(trade)
        except ValueError as exc:
            rows = msg.get("maker_orders")
            exact_ids: set[str] = set()
            taker_id = msg.get("taker_order_id")
            if taker_id in getattr(self._proc, "taker_orders", {}):
                exact_ids.add(taker_id)
            if isinstance(rows, list):
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    exact_ids.update(maker_order_id_evidence(row).ids)
            self._proc.on_malformed_trade(exact_ids, f"malformed WS trade: {exc}")
            return
        for ev in events:
            cond = self._condition_of_token(ev.token_id) or str(msg.get("market", ""))
            self._proc.on_trade(ev, cond, received_at_us=self._receipt_us,
                received_monotonic_ns=self._receipt_ns,
                connection_generation=self.connection_generation)

    def _on_order(self, msg: dict[str, Any]) -> None:
        self._journal_write("user_order", msg)
        ev = self._proc.normalize_owned_order(msg)
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
