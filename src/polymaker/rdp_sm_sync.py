"""Independent periodic cache; trading authorization stays with the executor."""

from __future__ import annotations

import asyncio
import json
import math
import queue
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import asdict, dataclass
from decimal import Decimal

from polymaker.rdp_sm_recovery import retry_read
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
    session_id: str | None = None
    execution_cache: bool = False

    def to_dict(self):
        return {"mode": "READ_ONLY_EXECUTION_CACHE" if self.execution_cache else "OBSERVE_ONLY",
                "trading_authorized": False,
                "version": self.version, "status": self.status, "session_id": self.session_id,
                "published_monotonic": self.published_monotonic,
                "published_wall": self.published_wall,
                "market": json.loads(self.market_json) if self.market_json else None,
                "attempts": {r.name: r.to_dict() for r in self.attempts},
                "last_good": {r.name: r.to_dict() for r in self.last_good},
                "pending_ids": list(self.pending_ids), "fatal_type": self.fatal_type}


class SyncObserver:
    """One coordinator thread owns publication; its six I/O workers only read.

    Repair proof requests only advance bounded read-only rounds. Stop drains real SDK reads
    before closing the private transport; cancelling an awaiter cannot own I/O.
    """

    def __init__(self, reader_factory, settings=None, *, follow_main=False, session_id=None):
        self.settings, self.reader_factory = settings or SyncSettings(), reader_factory
        self.follow_main, self.session_id = follow_main, session_id
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
        self._target = None
        self._history = deque(maxlen=1024)
        self._detail_requirements = {}
        self._retired_rejections = set()
        self._detail_retries = {}
        self._position_proofs = {}
        self.network = None
        self.account_failure_revision = 0
        self._refresh_pending = set()
        self._refresh_events = {}
        self._async_waiters = {}

    def request_repair_refresh(self, lanes=("orders",)):
        """Coalesce executor repair-proof work; never perform I/O or grant authority.

        Observe-only services retain their ordinary cadence. The coordinator
        enforces the probe cadence, lane serialization and all retry backoffs.
        """
        lanes = set(lanes)
        if not lanes <= {"orders", "account"}:
            raise ValueError("SYNC_REFRESH_LANE_INVALID")
        with self._condition:
            if self.network is None or self._stop.is_set():
                return False
            self._refresh_pending.update(lanes)
            events = [self._refresh_events[lane] for lane in lanes
                      if lane in self._refresh_events]
        if self._loop is not None:
            for event in events:
                with suppress(RuntimeError):
                    self._loop.call_soon_threadsafe(event.set)
        return True

    @staticmethod
    def position_proof_signature(oid, order, trades, capital, positions):
        legs = sorted(((t["trade_id"], t["status"], leg) for t in trades
                       for leg in t["maker_legs"] if leg["order_id"] == oid), key=lambda x: x[0])
        return json.dumps([order, legs, capital, positions], sort_keys=True)

    def accept_position_proofs(self, snapshot, ids):
        """Retire detail polling only after the executor's economic proof.

        This is diagnostic work scheduling, never execution admission. Any
        subsequent order, trade, position or capital change invalidates it.
        """
        if snapshot.session_id != self.session_id:
            raise ValueError("SYNC_POSITION_PROOF_SESSION_MISMATCH")
        good = {r.name: r for r in snapshot.last_good}
        if not all(n in good for n in ("trades", "capital", "positions")):
            return
        with self._condition:
            for oid in ids:
                if "order:"+oid in good:
                    self._position_proofs[oid] = self.position_proof_signature(oid,
                        good["order:"+oid].data, good["trades"].data,
                        good["capital"].data, good["positions"].data)

    def track_orders(self, requirements):
        """Passive pending-proof work, consumed only by the next periodic round.

        These IDs are supplied by the executor's existing operation registry.
        They confer GET permission, never acceptance or order ownership.
        No timer is woken and no HTTP is started here.
        """
        with self._condition:
            for oid, after in requirements.items():
                self._retired_rejections.discard(oid)
                self._detail_requirements[oid] = max(
                    after, self._detail_requirements.get(oid, float("-inf")))

    def retire_rejected_orders(self, ids):
        """Executor-proven nonacceptance retires detail work, never observations.

        Passive scheduling only. Fresh open/trade facts can queue the ID again;
        the executor retains ownership and independently revokes its proof.
        """
        with self._condition:
            self._retired_rejections.update(ids)
            for oid in ids:
                self._detail_requirements.pop(oid, None)

    def bind_market(self, market):
        """Passive main-process context, never an out-of-cycle refresh request."""
        if not self.follow_main or not isinstance(market, ObservedMarket):
            raise ValueError("SYNC_MAIN_CONTEXT_REQUIRED")
        with self._condition:
            if self._target is not None and self._target != market:
                raise ValueError("SYNC_MAIN_MARKET_ALREADY_BOUND")
            self._target = market

    def snapshot_at(self, monotonic):
        with self._condition:
            return next((s for s in reversed(self._history)
                         if s.published_monotonic <= monotonic), None)

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

    async def wait_after_async(self, version, timeout):
        """Publication notification with atomic registration and no worker thread."""
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        with self._condition:
            if self._snapshot.version > version:
                return self._snapshot
            self._async_waiters[future] = (loop, version)
        try:
            with suppress(TimeoutError):
                await asyncio.wait_for(future, timeout)
            return self.latest()
        finally:
            with self._condition:
                self._async_waiters.pop(future, None)

    @staticmethod
    def _notify_waiter(future):
        if not future.done():
            future.set_result(None)

    def drain_events(self):
        result = []
        while True:
            try:
                result.append(self._events.get_nowait())
            except queue.Empty:
                return result

    def _emit(self, event):
        if (self.network is not None and event.get("kind") == "sync_http"
                and event.get("host") == "clob.polymarket.com"
                and event.get("scope") != "bootstrap"):
            status, error = event.get("http_status"), event.get("error_type")
            outcome = ("SUCCESS" if error is None else "RATE_LIMITED" if status == 429
                       else "SERVER_ERROR" if status == 408 or status and status >= 500
                       else "HTTP_REJECTION" if status else "TRANSPORT_ERROR")
            self.network.observe(
                "GET", event["completed_monotonic"] - event["started_monotonic"],
                error is None, operation_id=event["read_id"], endpoint=event["endpoint"],
                started_at=event["started_monotonic"], started_wall=event["started_wall"],
                outcome=outcome, http_status=status, error_type=error)
        try:
            self._events.put_nowait(event)
        except queue.Full:
            with self._event_lock:
                self.dropped_events += 1

    def _publish(self, *, status="RUNNING", fatal=None):
        # Only the coordinator calls this; consumers only get immutable objects.
        if status == "RUNNING" and self.follow_main and self._market is None:
            status = "AWAITING_MAIN_MARKET"
        current = SyncSnapshot(
            version=self._snapshot.version + 1, published_monotonic=time.monotonic(),
            published_wall=time.time(), status=status,
            market_json=json.dumps(asdict(self._market)) if self._market else None,
            attempts=tuple(self._attempts.values()), last_good=tuple(self._good.values()),
            pending_ids=tuple(sorted(self._pending)), fatal_type=fatal, session_id=self.session_id,
            execution_cache=self.network is not None,
        )
        with self._condition:
            self._snapshot = current
            self._history.append(current)
            self._condition.notify_all()
            ready = [(future, loop) for future, (loop, version) in self._async_waiters.items()
                     if current.version > version]
            for future, _ in ready:
                self._async_waiters.pop(future, None)
        for future, loop in ready:
            with suppress(RuntimeError):
                loop.call_soon_threadsafe(self._notify_waiter, future)
        changed = [r for r in current.attempts if self._emitted_reads.get(r.name) != r.read_id]
        self._emitted_reads.update({r.name: r.read_id for r in changed})
        event = {"kind": "sync_snapshot",
                    "mode": "READ_ONLY_EXECUTION_CACHE" if current.execution_cache else "OBSERVE_ONLY",
                    "trading_authorized": False,
                    "session_id": self.session_id,
                    "version": current.version, "status": current.status,
                    "shutdown": self._stop.is_set(),
                    "published_monotonic": current.published_monotonic,
                    "published_wall": current.published_wall,
                    "market": json.loads(current.market_json) if current.market_json else None,
                    "attempt_updates": {r.name: r.to_dict() for r in changed},
                    "last_good_refs": {r.name: r.read_id for r in current.last_good},
                    "pending_ids": list(current.pending_ids), "fatal_type": fatal}
        if self.follow_main:
            event["full_snapshot"] = current.to_dict()
        self._emit(event)

    def _accept(self, evidence):
        if (evidence.name in {"time", "open_orders", "trades", "capital", "positions"}
                and not evidence.complete):
            self.account_failure_revision += 1
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

    async def _initialize(self):
        failures = 0
        while not self._stop.is_set():
            error = None

            for name, scope, read in (("bootstrap", "bootstrap", self.reader.open),
                                      ("time", "account", self.reader.server_time)):
                def read_initial(read=read):
                    nonlocal error
                    try:
                        return read()
                    except Exception as exc:
                        # read() keeps sanitized evidence; classification needs
                        # the original exception, including HTTP auth status.
                        error = exc
                        raise

                evidence = await self._read(name, scope, read_initial)
                if self._stop.is_set():
                    return False
                if not evidence.complete:
                    break
            else:
                self._anchor = (evidence.data, evidence.completed_monotonic)
                self._publish()
                return True
            self.reader.close()
            if error is None:
                raise RuntimeError("SYNC_INITIAL_CLOCK_UNAVAILABLE")
            if not retry_read(error):
                raise error
            failures += 1
            backoff = max(evidence.retry_after_s, min(8.0, 2.0 ** min(failures, 3)))
            self._publish(status="CREATED")
            self._emit({"kind": "sync_startup_retry", "error_type": evidence.error_type,
                        "attempt": failures, "backoff_s": backoff})
            await self._sleep_until(time.monotonic() + backoff)
        return False

    async def _run(self):
        self._loop = asyncio.get_running_loop()
        self._async_stop = asyncio.Event()
        self._refresh_events = {lane: asyncio.Event() for lane in ("orders", "account")}
        self.reader = self.reader_factory(self._emit, self._stop)
        tasks = []
        try:
            with ThreadPoolExecutor(max_workers=6, thread_name_prefix="sync-read") as pool:
                self._pool = pool
                try:
                    if await self._initialize():
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
            period = interval
            if (lane == "orders" and self.network is not None
                    and self.network.snapshot().state != "NORMAL"):
                period = min(interval, self.network.cfg.network_probe_interval_sec)
            if self.follow_main and self._target is None:
                await self._sleep_until(began + interval)
                continue
            with self._condition:
                repair_refresh = lane in self._refresh_pending
                self._refresh_pending.discard(lane)
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
                        "target_interval_s": period, "overrun": ended - began > period,
                        "repair_proof_refresh": repair_refresh,
                        "complete": complete and covered, "reads_complete": complete,
                        "detail_coverage_complete": covered, "error_type": error,
                        "shutdown": self._stop.is_set(),
                        "market_slug": self._market.slug if self._market else None})
            # Missing/invalid reads back off without overlapping this lane.
            backoff = max(retry_after, min(8.0, 2.0 ** min(failures, 3))) if failures else 0.0
            probe = (min(period, self.network.cfg.network_probe_interval_sec)
                     if self.network is not None else period)
            await self._wait_round(lane, max(began + period, ended + backoff),
                                   max(began + probe, ended + backoff))

    async def _wait_round(self, lane, regular, earliest):
        """A request can shorten idle cadence, never a retry embargo or live read."""
        event = self._refresh_events.setdefault(lane, asyncio.Event())
        while not self._stop.is_set():
            event.clear()
            with self._condition:
                pending = lane in self._refresh_pending
            target = min(regular, earliest) if pending else regular
            delay = target - time.monotonic()
            if delay <= 0:
                return
            tasks = [asyncio.create_task(event.wait()),
                     asyncio.create_task(self._async_stop.wait())]
            try:
                await asyncio.wait(tasks, timeout=delay, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    def _change_market(self, market):
        self._market = market
        self._pending, self._details, self._legs = {}, {}, {}
        self._detail_retries.clear()
        with self._condition:
            self._position_proofs.clear()
            self._retired_rejections.clear()
        self.reader.observed_ids = frozenset()
        for cache in (self._attempts, self._good):
            for name in list(cache):
                if name == "trades" or name.startswith("order:"):
                    del cache[name]
        self._emit({"kind": "sync_market", **asdict(market), "wall": time.time()})

    def _observe_rows(self, opened, trade_evidence):
        market = self._market
        with self._condition:
            retired = set(self._retired_rejections)
        rows = opened.data if opened.complete else json.loads(opened.partial_json)
        for row in rows:
            if row["market_id"] == market.condition_id:
                oid = row["order_id"]
                with self._condition:
                    self._retired_rejections.discard(oid)
                self._pending.setdefault(oid, 0)
        trades = (trade_evidence.data if trade_evidence.complete
                  else json.loads(trade_evidence.partial_json))
        for trade in trades:
            for leg in trade["maker_legs"]:
                oid = leg["order_id"]
                if (oid not in self._pending and oid not in self._details
                        and oid not in retired
                        and (leg["maker_address"] or "").lower() != self.reader.funder.lower()):
                    continue
                with self._condition:
                    self._retired_rejections.discard(oid)
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
        if self.follow_main:
            if self._market is None:
                self._change_market(self._target)
        elif self._market is None or self._market.start != epoch:
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
        with self._condition:
            requirements = dict(self._detail_requirements)
            position_proofs = dict(self._position_proofs)
            retired = set(self._retired_rejections)
        for oid in retired:
            self._pending.pop(oid, None)
            self._detail_retries.pop(oid, None)
        for oid, signature in position_proofs.items():
            sources = [self._good.get(n) for n in ("order:"+oid, "trades", "capital", "positions")]
            if not all(sources) or self.position_proof_signature(oid, *(s.data for s in sources)) != signature:
                self._pending.setdefault(oid, 0)
                # Invalidation queues ONE new reconciliation, not a permanent
                # priority-zero job on every round. Otherwise four retired
                # proofs can monopolize the detail budget and starve a newer
                # filled order forever after account inventory changes.
                with self._condition:
                    if self._position_proofs.get(oid) == signature:
                        del self._position_proofs[oid]
        for oid, after in requirements.items():
            known = self._good.get("order:" + oid)
            if known is None or known.started_monotonic < after:
                self._pending.setdefault(oid, 0)
        self.reader.observed_ids = (frozenset(self._pending) | frozenset(self._details)
                                    | frozenset(requirements))
        now = time.monotonic()
        selected = sorted((oid for oid in self._pending
                           if now >= self._detail_retries.get(oid, (0, 0))[1]),
                          key=lambda oid: (self._pending[oid], oid))[
            :self.settings.detail_limit]
        backlog = len(self._pending) - len(selected)
        details = await asyncio.gather(*[
            self._read("order:" + oid, market.condition_id,
                       lambda oid=oid: self.reader.order(oid, market))
            for oid in selected
        ])
        for oid, evidence in zip(selected, details, strict=True):
            with self._condition:
                if oid in self._retired_rejections:
                    self._pending.pop(oid, None)
                    self._detail_retries.pop(oid, None)
                    continue
            self._detail_sequence += 1
            self._pending[oid] = self._detail_sequence
            if not evidence.complete:
                failures = self._detail_retries.get(oid, (0, 0))[0] + 1
                delay = max(evidence.retry_after_s, min(8.0, 2.0 ** min(failures, 3)))
                self._detail_retries[oid] = (failures, time.monotonic() + delay)
                self._emit({"kind": "sync_detail_retry", "order_id": oid,
                            "failures": failures, "retry_delay_s": delay,
                            "error_type": evidence.error_type, "error_code": evidence.error_code,
                            "http_status": evidence.http_status})
                continue
            self._detail_retries.pop(oid, None)
            row = evidence.data
            self._details[oid] = row
            agrees = False
            if trades.complete:
                legs = {(trade["trade_id"], leg["order_id"]): leg
                        for trade in trades.data for leg in trade["maker_legs"] if leg["order_id"] == oid}
                quantity = sum((Decimal(str(leg["matched_amount"])) for leg in legs.values()), Decimal(0))
                delta = Decimal(str(row["size_matched"])) - quantity
                refs_match = (set(row["associate_trades"]) == {key[0] for key in legs}
                              if row["associate_trades_present"] else None)
                agrees = abs(delta) <= Decimal("0.000001") and refs_match is not False
                self._emit({"kind": "sync_consistency", "market_slug": market.slug, "order_id": oid,
                            "quantity_delta_shares": float(delta), "associate_ids_match": refs_match,
                            "observations_agree": abs(delta) <= Decimal("0.000001") and refs_match is not False,
                            "trades_completed_monotonic": trades.completed_monotonic,
                            "order_started_monotonic": evidence.started_monotonic})
            statuses = [signature[0] for (_, known), signature in self._legs.items() if known == oid]
            terminal = row["status"] in {"CANCELED", "CANCELLED", "EXPIRED"} or (
                row["status"] == "MATCHED" and row["remaining_size"] == 0)
            proven = (all(n in self._good for n in ("capital", "positions")) and trades.complete
                and position_proofs.get(oid) == self.position_proof_signature(oid, row, trades.data,
                    self._good["capital"].data, self._good["positions"].data))
            if terminal and agrees and (all(status == "CONFIRMED" for status in statuses) or proven):
                del self._pending[oid]
        self._publish()
        reads = [clock, opened, trades]
        # Coverage backlog is reported separately: it should not induce fault backoff.
        self._emit({"kind": "sync_detail_coverage", "market_slug": market.slug,
                    "selected": len(selected), "unread_pending": backlog})
        # Individual detail errors keep their own retry and unresolved facts.
        # They must not starve /time probes or healthy complete collections.
        complete = all(r.complete for r in reads)
        return complete, max(r.retry_after_s for r in reads), (
            backlog == 0 and all(r.complete for r in details))

    async def _account_round(self):
        reads = await asyncio.gather(
            self._read("capital", "account", self.reader.capital),
            self._read("positions", "account", self.reader.positions),
        )
        self._publish()
        return all(r.complete for r in reads), max(r.retry_after_s for r in reads), True
