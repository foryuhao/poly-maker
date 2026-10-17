"""Phase one: an independent observer thread, not a trading-state authority."""

from __future__ import annotations

import asyncio
import json
import math
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import asdict, dataclass
from decimal import Decimal

from polymaker.rdp_sm_sync_reads import ObservedMarket, ReadEvidence


@dataclass(frozen=True)
class SyncSettings:
    order_interval_s: float = 1.0
    account_interval_s: float = 2.0
    detail_limit: int = 4

    def __post_init__(self):
        if any(not math.isfinite(v) or v < .01 for v in (
                self.order_interval_s, self.account_interval_s)) or (
                type(self.detail_limit) is not int or not 1 <= self.detail_limit <= 4):
            raise ValueError("SYNC_SETTINGS_INVALID")


@dataclass(frozen=True)
class SyncSnapshot:
    version: int = 0
    published_monotonic: float = 0.0
    published_wall: float = 0.0
    status: str = "CREATED"
    market_json: str | None = None
    attempts: tuple[ReadEvidence, ...] = ()
    last_good: tuple[ReadEvidence, ...] = ()
    pending_ids: tuple[str, ...] = ()
    fatal_type: str | None = None

    def to_dict(self):
        return {"mode": "OBSERVE_ONLY", "trading_authorized": False,
                "version": self.version, "status": self.status,
                "published_monotonic": self.published_monotonic,
                "published_wall": self.published_wall,
                "market": json.loads(self.market_json) if self.market_json else None,
                "attempts": {r.name: r.to_dict() for r in self.attempts},
                "last_good": {r.name: r.to_dict() for r in self.last_good},
                "pending_ids": list(self.pending_ids), "fatal_type": self.fatal_type}


class SyncObserver:
    """One coordinator thread owns publication; its six I/O workers only read.

    Consumers have no refresh API. Stop wakes timers, but drains real SDK reads
    before closing the private transport; cancelling an awaiter cannot own I/O.
    """

    def __init__(self, reader_factory, settings=None):
        self.settings, self.reader_factory = settings or SyncSettings(), reader_factory
        self._stop = threading.Event()
        self._condition = threading.Condition()
        self._snapshot = SyncSnapshot()
        self._events = queue.Queue(maxsize=4096)
        self._event_lock = threading.Lock()
        self.dropped_events = 0
        self._thread = self._loop = self._async_stop = None
        self._attempts, self._good = {}, {}
        self._market = None
        self._pending, self._details, self._legs = {}, {}, {}
        self._detail_sequence = 0
        self._emitted_reads = {}

    def start(self):
        if self._thread is not None:
            raise RuntimeError("SYNC_ALREADY_STARTED")
        self._thread = threading.Thread(target=self._thread_main, name="rdp-sync-observer")
        self._thread.start()
        return self

    def stop(self, timeout=30.0):
        self._stop.set()
        if self._loop is not None and self._async_stop is not None:
            with suppress(RuntimeError):  # The thread may have closed its loop.
                self._loop.call_soon_threadsafe(self._async_stop.set)
        if self._thread is not None:
            self._thread.join(timeout)
            return not self._thread.is_alive()
        return True

    def latest(self):
        with self._condition:
            return self._snapshot

    def wait_after(self, version, timeout=None):
        with self._condition:
            self._condition.wait_for(lambda: self._snapshot.version > version, timeout)
            return self._snapshot

    def drain_events(self):
        result = []
        while True:
            try:
                result.append(self._events.get_nowait())
            except queue.Empty:
                return result

    def _emit(self, event):
        try:
            self._events.put_nowait(event)
        except queue.Full:
            with self._event_lock:
                self.dropped_events += 1

    def _publish(self, *, status="RUNNING", fatal=None):
        # Only the coordinator calls this; consumers only get immutable objects.
        current = SyncSnapshot(
            version=self._snapshot.version + 1, published_monotonic=time.monotonic(),
            published_wall=time.time(), status=status,
            market_json=json.dumps(asdict(self._market)) if self._market else None,
            attempts=tuple(self._attempts.values()), last_good=tuple(self._good.values()),
            pending_ids=tuple(sorted(self._pending)), fatal_type=fatal,
        )
        with self._condition:
            self._snapshot = current
            self._condition.notify_all()
        changed = [r for r in current.attempts if self._emitted_reads.get(r.name) != r.read_id]
        self._emitted_reads.update({r.name: r.read_id for r in changed})
        self._emit({"kind": "sync_snapshot", "mode": "OBSERVE_ONLY", "trading_authorized": False,
                    "version": current.version, "status": current.status,
                    "shutdown": self._stop.is_set(),
                    "published_monotonic": current.published_monotonic,
                    "published_wall": current.published_wall,
                    "market": json.loads(current.market_json) if current.market_json else None,
                    "attempt_updates": {r.name: r.to_dict() for r in changed},
                    "last_good_refs": {r.name: r.read_id for r in current.last_good},
                    "pending_ids": list(current.pending_ids), "fatal_type": fatal})

    def _accept(self, evidence):
        self._attempts[evidence.name] = evidence
        if evidence.complete:
            self._good[evidence.name] = evidence

    async def _read(self, name, scope, fn):
        future = self._loop.run_in_executor(self._pool, self.reader.read, name, scope, fn)
        evidence = await future
        self._accept(evidence)
        return evidence

    def _thread_main(self):
        if self._stop.is_set():
            self._publish(status="STOPPED")
            return
        try:
            asyncio.run(self._run())
        except Exception as exc:
            self._publish(status="FAILED", fatal=type(exc).__name__)

    async def _run(self):
        self._loop = asyncio.get_running_loop()
        self._async_stop = asyncio.Event()
        self.reader = self.reader_factory(self._emit, self._stop)
        tasks = []
        try:
            with ThreadPoolExecutor(max_workers=6, thread_name_prefix="sync-read") as pool:
                self._pool = pool
                try:
                    await self._loop.run_in_executor(pool, self.reader.open)
                    clock = await self._read("time", "account", self.reader.server_time)
                    if not clock.complete:
                        raise RuntimeError("SYNC_INITIAL_CLOCK_UNAVAILABLE")
                    self._anchor = (clock.data, clock.completed_monotonic)
                    self._publish()
                    tasks = [asyncio.create_task(self._periodic("orders", self.settings.order_interval_s,
                                                              self._orders_round)),
                             asyncio.create_task(self._periodic("account", self.settings.account_interval_s,
                                                              self._account_round))]
                    await asyncio.gather(*tasks)
                finally:
                    self._stop.set()
                    self._async_stop.set()
                    if tasks:
                        await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            # ThreadPoolExecutor.__exit__ drained in-flight jobs before close.
            self.reader.close()
        self._publish(status="STOPPED")

    async def _sleep_until(self, target):
        delay = target - time.monotonic()
        if delay > 0 and not self._stop.is_set():
            with suppress(TimeoutError):
                await asyncio.wait_for(self._async_stop.wait(), delay)

    async def _periodic(self, lane, interval, run_round):
        failures = 0
        while not self._stop.is_set():
            began, wall = time.monotonic(), time.time()
            before = self._snapshot.version
            retry_after = 0.0
            try:
                complete, retry_after, covered = await run_round()
                error = None
            except Exception as exc:
                complete, covered, error = False, False, type(exc).__name__
            ended = time.monotonic()
            failures = 0 if complete else failures + 1
            if self._snapshot.version == before:
                self._publish()
            self._emit({"kind": "sync_round", "lane": lane, "started_wall": wall,
                        "completed_wall": time.time(), "started_monotonic": began,
                        "completed_monotonic": ended, "duration_s": ended - began,
                        "target_interval_s": interval, "overrun": ended - began > interval,
                        "complete": complete and covered, "reads_complete": complete,
                        "detail_coverage_complete": covered, "error_type": error,
                        "shutdown": self._stop.is_set(),
                        "market_slug": self._market.slug if self._market else None})
            # Missing/invalid reads back off locally; never touch production health.
            backoff = max(retry_after, min(8.0, 2.0 ** min(failures, 3))) if failures else 0.0
            await self._sleep_until(max(began + interval, ended + backoff))

    def _change_market(self, market):
        self._market = market
        self._pending, self._details, self._legs = {}, {}, {}
        self.reader.observed_ids = frozenset()
        for cache in (self._attempts, self._good):
            for name in list(cache):
                if name == "trades" or name.startswith("order:"):
                    del cache[name]
        self._emit({"kind": "sync_market", **asdict(market), "wall": time.time()})

    def _observe_rows(self, opened, trade_evidence):
        market = self._market
        rows = opened.data if opened.complete else json.loads(opened.partial_json)
        for row in rows:
            if row["market_id"] == market.condition_id:
                oid = row["order_id"]
                self._pending.setdefault(oid, 0)
        trades = (trade_evidence.data if trade_evidence.complete
                  else json.loads(trade_evidence.partial_json))
        for trade in trades:
            for leg in trade["maker_legs"]:
                oid = leg["order_id"]
                if (oid not in self._pending and oid not in self._details
                        and (leg["maker_address"] or "").lower() != self.reader.funder.lower()):
                    continue
                key = (trade["trade_id"], oid)
                signature = (trade["status"], leg["asset_id"], leg["side"],
                             leg["matched_amount"], leg["price"])
                if self._legs.get(key) != signature:
                    self._legs[key] = signature
                    self._pending.setdefault(oid, 0)
                    self._emit({"kind": "sync_trade_observed", "market_slug": market.slug,
                                "trade_id": trade["trade_id"], "order_id": oid,
                                "status": trade["status"], "observed_monotonic":
                                trade_evidence.completed_monotonic,
                                "applied_monotonic": time.monotonic()})
        # These are observations for GET permission, never an OwnedOrderRegistry.
        self.reader.observed_ids = frozenset(self._pending) | frozenset(self._details)

    async def _orders_round(self):
        estimate = self._anchor[0] + time.monotonic() - self._anchor[1]
        epoch = int(estimate) // 300 * 300
        if self._market is None or self._market.start != epoch:
            # Metadata is fetched only at market changes, not for each consumer.
            metadata = await self._read("market", str(epoch), lambda: asdict(self.reader.market(epoch)))
            if not metadata.complete:
                return False, metadata.retry_after_s, False
            raw = metadata.data
            raw["tokens"] = tuple(raw["tokens"])
            self._change_market(ObservedMarket(**raw))
        market = self._market
        clock, opened, trades = await asyncio.gather(
            self._read("time", "account", self.reader.server_time),
            self._read("open_orders", "account", self.reader.open_orders),
            self._read("trades", market.condition_id, lambda: self.reader.trades(market)),
        )
        if clock.complete:
            self._anchor = (clock.data, clock.completed_monotonic)
        self._observe_rows(opened, trades)
        selected = sorted(self._pending, key=lambda oid: (self._pending[oid], oid))[
            :self.settings.detail_limit]
        backlog = len(self._pending) - len(selected)
        details = await asyncio.gather(*[
            self._read("order:" + oid, market.condition_id,
                       lambda oid=oid: self.reader.order(oid, market))
            for oid in selected
        ])
        for oid, evidence in zip(selected, details, strict=True):
            self._detail_sequence += 1
            self._pending[oid] = self._detail_sequence
            if not evidence.complete:
                continue
            row = evidence.data
            self._details[oid] = row
            if trades.complete:
                legs = {(trade["trade_id"], leg["order_id"]): leg
                        for trade in trades.data for leg in trade["maker_legs"] if leg["order_id"] == oid}
                quantity = sum((Decimal(str(leg["matched_amount"])) for leg in legs.values()), Decimal(0))
                delta = Decimal(str(row["size_matched"])) - quantity
                refs_match = (set(row["associate_trades"]) == {key[0] for key in legs}
                              if row["associate_trades_present"] else None)
                self._emit({"kind": "sync_consistency", "market_slug": market.slug, "order_id": oid,
                            "quantity_delta_shares": float(delta), "associate_ids_match": refs_match,
                            "observations_agree": abs(delta) <= Decimal("0.000001") and refs_match is not False,
                            "trades_completed_monotonic": trades.completed_monotonic,
                            "order_started_monotonic": evidence.started_monotonic})
            statuses = [signature[0] for (_, known), signature in self._legs.items() if known == oid]
            terminal = row["status"] in {"CANCELED", "CANCELLED", "EXPIRED"} or (
                row["status"] == "MATCHED" and row["remaining_size"] == 0)
            if terminal and all(status == "CONFIRMED" for status in statuses):
                del self._pending[oid]
        self._publish()
        reads = [clock, opened, trades, *details]
        # Coverage backlog is reported separately: it should not induce fault backoff.
        self._emit({"kind": "sync_detail_coverage", "market_slug": market.slug,
                    "selected": len(selected), "unread_pending": backlog})
        return all(r.complete for r in reads), max(r.retry_after_s for r in reads), backlog == 0

    async def _account_round(self):
        reads = await asyncio.gather(
            self._read("capital", "account", self.reader.capital),
            self._read("positions", "account", self.reader.positions),
        )
        self._publish()
        return all(r.complete for r in reads), max(r.retry_after_s for r in reads), True
