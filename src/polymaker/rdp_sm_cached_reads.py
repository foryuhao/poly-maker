"""One shared execution view of the periodic market/account cache.

The executor owns writes and risk. Repair proof requests advance bounded shared
read-only rounds; consumers never perform foreground state GETs. A scope pins
one immutable frame for all existing account and FillTruth checks.
"""
from __future__ import annotations

import asyncio
import copy
import json
import time
from contextlib import asynccontextmanager
from decimal import Decimal

from polymaker.domain import OpenOrder, OrderState, Side
from polymaker.execution.gateway import GatewayReadError
from polymaker.rdp_sm import SessionStatus, _Issue
from polymaker.state.tracker import TradeEvent
from polymaker.userstream.parse import normalize_order, parse_trade


def order_raw(row):
    result = {"id": row["order_id"], "asset_id": row["token_id"],
              "market": row["market_id"], "side": row["side"], "price": row["price"],
              "original_size": row["original_size"], "size_matched": row["size_matched"],
              "status": row["status"]}
    if row.get("associate_trades_present"):
        result["associate_trades"] = row["associate_trades"]
    if "maker_address" in row:
        result["maker_address"] = row["maker_address"]
    return result


def trade_raw(row):
    return {"id": row["trade_id"], "market": row["market"], "status": row["status"],
            "timestamp": row["timestamp"], "maker_orders": row["maker_legs"],
            "trader_side": row.get("trader_side"), "taker_order_id": row.get("taker_order_id"),
            "asset_id": row.get("taker_asset_id"), "side": row.get("taker_side")}


class CachedExecutionReads:
    def __init__(self, executor, companion):
        self.e, self.service = executor, companion.service
        if self.service.session_id != executor.session_id:
            raise ValueError("SYNC_SESSION_MISMATCH")
        self.companion = companion
        self.active = None
        self.last_version = 0
        self.last_acquired = None
        self.last_write = 0.0
        self.order_writes = {}
        self.post_returns = {}
        self.inflight = {}
        self.observed_reads = set()
        self.detail_waits = {}
        self.admission_data = None
        self.timeout_s = executor.cfg.risk.ws_stale_halt_s
        self.raw_market = copy.deepcopy(executor.reads.market_raw)
        self.meta = executor.reads.feed.meta
        self.service.network = executor.reads.network
        self.original_cancel = executor.gateway.cancel_detailed
        executor.gateway.cancel_detailed = self.cancel
        executor.gateway.get_order = self.get_order
        executor.gateway.get_trades = self.get_trades
        executor.gateway.open_orders = self.open_orders
        executor.gateway.positions = self.positions
        executor.gateway.balance_allowance = self.capital
        executor.reads.server_time = self.server_time
        executor.reads.market = self.market
        executor.gateway._client.cache_reads_only = True

    def begin_write(self, kind, ids=()):
        token = object()
        self.inflight[token] = (kind, tuple(ids))
        return token

    def end_write(self, token):
        kind, ids = self.inflight.pop(token)
        now = time.monotonic()
        self.last_write = max(self.last_write, now)
        for oid in ids:
            self.order_writes[oid] = now
            if kind == "POST":
                self.post_returns[oid] = now
        if set(ids) & getattr(self.e, "sl_order_ids", set()):
            self.refresh_repair(account=True)

    def refresh_repair(self, *, account=False):
        """Scheduling only: every original admission/proof check still applies."""
        e = self.e
        if (e.repair_episode is None or e.repair_abandonment is not None
                or e.cooldown.active):
            return False
        return self.service.request_repair_refresh(
            ("orders", "account") if account else ("orders",))

    async def wait_account_update(self, timeout):
        """Wake on relevant account/order proof progress, never book-only ticks."""
        def signature(snap):
            return tuple((r.name, r.read_id) for r in snap.attempts
                         if r.name in {"positions", "capital", "trades", "open_orders"}
                         or r.name.startswith("order:") and r.name[6:] in getattr(self.e, "known", ()))
        baseline = signature(self.last_acquired or self.service.latest())
        deadline = time.monotonic() + timeout
        while True:
            snap = self.service.latest()
            if (signature(snap) != baseline or snap.status in {"FAILED", "STOPPED"}
                    or self.companion.failure):
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            await self.service.wait_after_async(snap.version, remaining)

    def confirmed_inventory_changed(self):
        """Advance collection scheduling, never fill truth or trading authority."""
        e = self.e
        if e.repair_abandonment is not None or e.cooldown.active:
            return
        y, n = e.economic_inventory()
        if e.repair_episode is None and y == n:
            return
        if self.service.request_repair_refresh(("orders", "account")):
            e.record("sm_account_refresh", {"reason": "CONFIRMED_INVENTORY_CHANGED",
                "economic_revision": e.economic_revision,
                "observed_monotonic": e.last_economic_change_monotonic})

    async def cancel(self, ids):
        token = self.begin_write("DELETE", ids)
        task = asyncio.create_task(self.original_cancel(ids))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await task  # The real SDK DELETE must drain before advancing its boundary.
            raise
        finally:
            self.end_write(token)

    def context_matches(self, snap):
        if snap.session_id != self.e.session_id or not snap.market_json:
            return False
        context = json.loads(snap.market_json)
        return (context["condition_id"] == self.meta.condition_id
                and context["slug"] == self.meta.slug
                and context["start"] == int(self.meta.slug.rsplit("-", 1)[1])
                and tuple(context["tokens"]) == (self.meta.yes.token_id, self.meta.no.token_id))

    def terminal_observed(self, ids):
        """Stop redundant DELETE, not risk reservation or the close barrier."""
        from polymaker.rdp_sm_execution import _terminal
        if any(kind == "POST" for kind, _ in self.inflight.values()):
            return False
        snap = self.service.latest()
        if snap.status != "RUNNING" or not self.context_matches(snap):
            return False
        good = {r.name: r for r in snap.last_good}
        attempts = {r.name: r for r in snap.attempts}
        opened = attempts.get("open_orders")
        if not opened or not opened.complete:
            return False
        if set(ids) & {r["order_id"] for r in opened.data}:
            return False
        for oid in ids:
            read = good.get("order:" + oid)
            owned = self.e.processor.owned_record(oid)
            if (not read or not owned or attempts.get(read.name) != read
                    or read.started_monotonic < self.post_returns.get(oid, float("inf"))):
                return False
            raw = order_raw(read.data)
            if ("maker_address" in raw
                    and raw["maker_address"].lower() != self.e.gateway.funder.lower()):
                return False
            ev = normalize_order(raw)
            if not (ev and ev.order_id == oid and ev.market_id == self.meta.condition_id
                    and ev.token_id == owned["token_id"] and ev.side.value == owned["side"]
                    and Decimal(str(ev.original_size)) == Decimal(str(owned["submitted_qty"]))
                    and Decimal(str(ev.price)) == Decimal(str(owned["submitted_price"]))
                    and _terminal(ev, self.e.matched_remaining_tolerance_shares)):
                return False
        return True

    def cancel_ids(self, ids):
        return [oid for oid in ids if not self.terminal_observed([oid])]

    def admission_current(self):
        """Worker-safe final check; never applies state or starts a read."""
        snap = self.service.latest()
        if (snap.status != "RUNNING" or self.companion.failure or not self.context_matches(snap)
                or self.admission_data is None):
            return False
        good = {r.name: r for r in snap.last_good}
        attempts = {r.name: r for r in snap.attempts}
        clock = good.get("time")
        if (clock is None or attempts.get("time") != clock
                or not 0 <= time.monotonic() - clock.started_monotonic <= self.timeout_s):
            return False
        for name, payload in self.admission_data.items():
            read = good.get(name)
            if (read is None or attempts.get(name) != read or read.data_json != payload
                    or not 0 <= time.monotonic() - read.started_monotonic <= self.timeout_s):
                return False
        return True

    def _ingest(self, snap):
        """Keep observed page risk even when its collection later failed."""
        client, policy = self.e.gateway._client, self.e.reads.account_policy
        for r in snap.attempts:
            if r.read_id in self.observed_reads or r.name not in {"open_orders", "trades"}:
                continue
            self.observed_reads.add(r.read_id)
            data = r.data if r.complete else json.loads(r.partial_json)
            if r.name == "open_orders":
                client._observe_order_page([order_raw(row) for row in data])
            else:
                for row in data:
                    raw = trade_raw(row)
                    parsed = parse_trade(raw)
                    if policy is not None:
                        policy.observe_trade(parsed, client.known_ids)
                    for leg in parsed.maker_legs:
                        if leg.order_id in client.known_ids or leg.order_id in client.observed_open_rows:
                            client.observed_trade_legs.setdefault(
                                (parsed.trade_id, leg.order_id), (parsed.status, leg))
                        if leg.order_id in client.observed_open_rows:
                            client.observed_open_rows[leg.order_id]["_sm_fill_observed"] = True
                        if self.e.processor.owned_record(leg.order_id) is not None:
                            self.e.processor.on_trade(
                                TradeEvent(leg.asset_id, leg.side, leg.price, leg.matched_amount,
                                           parsed.trade_id, parsed.status, parsed.timestamp,
                                           leg.order_id, parsed.market), parsed.market, source="REST")
            key = ("get_open_orders" if r.name == "open_orders" else "get_trades", ("sync",))
            if not r.complete:
                if key not in client._pagination_failures:
                    client.pagination_revision += 1
                client._pagination_failures[key] = None
            else:
                client._pagination_failures.pop(key, None)

    def proof_only_rejections(self):
        from polymaker.rdp_sm_execution import _canceled_submission_response

        return {a.order_id for a in self.e.attempts if a.order_id
                and _canceled_submission_response(a)
                and not self.e._canceled_submission_observed(a.order_id)}

    async def observe_sl(self, owner):
        """Consume available per-order evidence without acquiring a global scope.

        This never starts a foreground GET or waits for capital/positions. The
        ordinary companion supplies complete pages and requested order details.
        Fixed TP and SL actors share this observation path. NORMAL still uses
        its full _ready/admission scope, and actor admission remains local.
        """
        snap = self.service.latest()
        if snap.status in {"FAILED", "STOPPED"} or self.companion.failure:
            self.e.fatal = "SYNC_CACHE_UNAVAILABLE"
            return
        if not self.context_matches(snap):
            self.e.fatal = "SYNC_MARKET_SCOPE_MISMATCH"
            return
        self._ingest(snap)
        self.e._check_canceled_submission_observations()
        good = {r.name: r for r in snap.last_good}
        latest = {r.name: r for r in snap.attempts}
        clock = good.get("time")
        if clock is not None and latest.get("time") == clock and clock.complete:
            now = time.monotonic()
            if 0 <= now-clock.started_monotonic <= self.timeout_s:
                self.e.official_time = clock.data+now-clock.completed_monotonic
                self.e.time_anchor = self.e.monotonic()
        opened = good.get("open_orders")
        if opened is not None and latest.get("open_orders") == opened and opened.complete:
            for row in opened.data:
                if (row["order_id"] in self.e.sl_terminal_quantities
                        and opened.started_monotonic >= owner.proof_after.get(row["order_id"], float("inf"))):
                    self.e.sl_terminal_quantities.pop(row["order_id"], None)
                    self.e.fatal = "SL_TERMINAL_EVIDENCE_CONTRADICTION"
                if (row["token_id"] in {self.meta.yes.token_id, self.meta.no.token_id}
                        and row["order_id"] not in self.e.gateway._client.known_ids):
                    self.e.fatal = "EXTERNAL_TARGET_ORDER"
        requirements = {}
        needs_refresh = False
        trades = good.get("trades")
        for a in list(owner.orders):
            if not a.post_started or not owner.post_done(a) or a.outcome in {"REJECTED", "ABORTED"}:
                continue
            oid = owner.oid(a)
            boundary = max(self.post_returns.get(oid, 0), self.order_writes.get(oid, 0),
                           owner.post_returned.get(a.attempt_id, 0),
                           owner.cancel_ack.get(a.attempt_id, 0))
            if await self.prove_sl_canceled_submission(a, snap, boundary):
                owner.last_progress = self.e.monotonic()
                continue
            read = good.get("order:"+oid)
            if (read is None or latest.get(read.name) != read or not read.complete
                    or read.started_monotonic < boundary):
                requirements[oid] = boundary
                needs_refresh = needs_refresh or owner.unresolved(a)
                continue
            if (trades is None or latest.get("trades") != trades or not trades.complete
                    or trades.started_monotonic < boundary
                    or time.monotonic()-trades.started_monotonic > self.timeout_s):
                needs_refresh = needs_refresh or owner.unresolved(a)
                continue
            owner.observe_order(a, order_raw(read.data), [trade_raw(t) for t in trades.data],
                                min(read.started_monotonic, trades.started_monotonic))
        if requirements:
            self.service.track_orders(requirements)
        unknown = owner.recovery.waiting()
        if needs_refresh or unknown:
            self.refresh_repair(account=unknown)

    async def prove_sl_canceled_submission(self, attempt, snap, boundary):
        """Use fresh complete cached pages and the existing exact POST proof.

        No scope acquisition or foreground GET; other SL writes may continue.
        DELETE not-found and malformed detail rows never establish rejection.
        """
        from polymaker.rdp_sm_execution import _canceled_submission_response

        if not _canceled_submission_response(attempt):
            return False
        good = {r.name: r for r in snap.last_good}
        latest = {r.name: r for r in snap.attempts}
        now = time.monotonic()
        for name in ("open_orders", "trades"):
            r = good.get(name)
            if (r is None or latest.get(name) != r or not r.complete
                    or r.started_monotonic < boundary
                    or not 0 <= now-r.started_monotonic <= self.timeout_s):
                return False
        oid = attempt.order_id
        detail = latest.get("order:"+oid)
        if detail is not None and detail.complete:
            return False  # Positive order evidence must use regular reconciliation.
        opened = [order_raw(r) for r in good["open_orders"].data]
        orders = [OpenOrder(r["id"], r["asset_id"], Side(r["side"]), float(r["price"]),
                            float(r["original_size"])-float(r["size_matched"]), OrderState.LIVE)
                  for r in opened]
        trades = [trade_raw(r) for r in good["trades"].data]
        proven = await self.e._verify_canceled_submission(attempt, evidence=(
            orders, opened, trades, self.e.external_revision))
        if proven:
            self.e.record("sm_sl_canceled_submission_proven", {
                "order_id": oid, "snapshot_version": snap.version,
                "operation_after_monotonic": boundary,
                "source": "IDENTITY_BOUND_POST_RESPONSE_AND_COMPLETE_COLLECTIONS",
                "sources": {name: {"read_id": good[name].read_id,
                                    "started_monotonic": good[name].started_monotonic}
                            for name in ("open_orders", "trades")}})
        return proven

    def _ready(self, snap, account, details):
        if not self.context_matches(snap):
            return False
        self._ingest(snap)
        good = {r.name: r for r in snap.last_good}
        attempts = {r.name: r for r in snap.attempts}
        # Queue missing proof work even while cancellation is still running.
        # Otherwise a short-lived order absent from open_orders could leave
        # the watcher waiting for a detail that was never scheduled.
        if details:
            rejected = self.proof_only_rejections()
            self.service.track_orders({
                oid: self.order_writes.get(oid, 0.0) for oid in self.e.known - rejected
                if "order:" + oid not in good or good["order:" + oid].started_monotonic
                < self.order_writes.get(oid, 0.0)
            })
        names = ["time", "open_orders", "trades"]
        if account:
            names += ["capital", "positions"]
        for name in names:
            r = good.get(name)
            if (r is None or attempts.get(name) != r or not r.complete
                    or r.started_monotonic < self.last_write
                    or not 0 <= time.monotonic() - r.started_monotonic <= self.timeout_s):
                return False
        for row in good["open_orders"].data:
            if row["order_id"] in self.e.terminal:
                self.e.terminal_conflict(row["order_id"], source="CACHED_OPEN_ORDERS",
                    observed_after=good["open_orders"].completed_monotonic)
                raise _Issue(SessionStatus.BLOCKED, "TERMINAL_LIVE_CONTRADICTION")
        if self.inflight:
            return False
        if details:
            requirements = self._detail_requirements(snap)
            if requirements:
                self.service.track_orders(requirements)
                return False
        return True

    def _detail_requirements(self, snap):
        """The same per-order evidence rules apply to admission and closeout."""
        good = {r.name: r for r in snap.last_good}
        attempts = {r.name: r for r in snap.attempts}
        requirements = {}
        trades = good["trades"].data
        opened = {row["order_id"] for row in good["open_orders"].data}
        for oid in self.e.known - self.proof_only_rejections():
            name = "order:" + oid
            r = good.get(name)
            after = self.order_writes.get(oid, 0.0)
            if not r or attempts.get(name) != r or r.started_monotonic < after:
                requirements[oid] = after
                continue
            row = r.data
            if oid in opened and row["status"] in {"CANCELED", "CANCELLED", "MATCHED"}:
                requirements[oid] = max(after, good["open_orders"].completed_monotonic)
                continue
            legs = {(t["trade_id"], leg["order_id"]): leg for t in trades
                    for leg in t["maker_legs"] if leg["order_id"] == oid}
            qty = sum((Decimal(str(leg["matched_amount"])) for leg in legs.values()), Decimal(0))
            if (abs(qty - Decimal(str(row["size_matched"]))) > Decimal(".000001")
                    or not row.get("associate_trades_present")
                    or set(row["associate_trades"]) != {key[0] for key in legs}):
                requirements[oid] = max(after, r.completed_monotonic)
        return requirements

    async def acquire(self, *, account=False, details=True, newer=False, partial=False):
        deadline = time.monotonic() + self.timeout_s
        version = self.last_version if newer else -1
        while True:
            snap = self.service.latest()
            if snap.status in {"FAILED", "STOPPED"} or self.companion.failure:
                raise _Issue(SessionStatus.BLOCKED, "SYNC_CACHE_UNAVAILABLE")
            if snap.version > version and self._ready(snap, account, details):
                self.last_version = snap.version
                self.last_acquired = snap
                return snap
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                # Closeout may make per-ID progress after the ordinary wait,
                # using fresh complete collections. This never grants account
                # admission or waives any individual order proof.
                if partial and snap.version > version and self._ready(snap, account, False):
                    self.last_version = snap.version
                    self.last_acquired = snap
                    return snap
                raise _Issue(SessionStatus.BLOCKED, "SYNC_CACHE_NOT_READY")
            await self.service.wait_after_async(snap.version, min(.25, remaining))

    @asynccontextmanager
    async def scope(self, purpose, *, account=False, details=True, partial=False):
        if self.active is not None:
            yield self.active
            return
        snap = await self.acquire(account=account, details=details, partial=partial)
        self.active = snap
        good = {r.name: r for r in snap.last_good}
        client = self.e.gateway._client
        client.last_trades = [trade_raw(r) for r in good["trades"].data]
        client.last_open_rows = [order_raw(r) for r in good["open_orders"].data]
        if account:
            self.admission_data = {name: good[name].data_json
                                   for name in ("open_orders", "trades", "capital", "positions")}
        self.e.record("sm_sync_cache_read", {
            "purpose": purpose, "snapshot_version": snap.version,
            "operation_after_monotonic": self.last_write,
            "sources": {name: {"read_id": r.read_id, "started_monotonic": r.started_monotonic,
                               "completed_monotonic": r.completed_monotonic}
                        for name, r in good.items() if name in {
                            "time", "open_orders", "trades", "capital", "positions"}},
        })
        try:
            yield snap
        finally:
            self.active = None

    async def source(self, name):
        snap = self.active or await self.acquire(account=name in {"capital", "positions"})
        read = next((r for r in snap.last_good if r.name == name), None)
        if name.startswith("order:"):
            oid = name.removeprefix("order:")
            requirements = self._detail_requirements(snap)
            latest = next((r for r in snap.attempts if r.name == name), None)
            if (oid in requirements or read is None or latest != read
                    or read.started_monotonic < self.order_writes.get(oid, 0.0)):
                self.service.track_orders({oid: requirements.get(oid, self.order_writes.get(oid, 0.0))})
                code = (latest.error_code or latest.error_type) if latest else None
                key = (latest.read_id if latest else None, requirements.get(oid))
                if self.detail_waits.get(oid) != key:
                    self.detail_waits[oid] = key
                    self.e.record("sm_sync_detail_wait", {
                        "order_id": oid, "snapshot_version": snap.version,
                        "source_error": code, "http_status": latest.http_status if latest else None,
                        "placement_outcome": next((a.outcome for a in self.e.attempts
                                                   if a.order_id == oid), None),
                        "risk_retained": True,
                    })
                raise GatewayReadError(f"order read {oid}:{code or 'UNRESOLVED'}:SYNC_DETAIL_NOT_READY")
            if oid in self.detail_waits:
                del self.detail_waits[oid]
                self.e.record("sm_sync_detail_recovered", {
                    "order_id": oid, "snapshot_version": snap.version,
                    "source_read_id": read.read_id, "close_barrier_still_required": True,
                })
        if read is None:
            raise GatewayReadError("SYNC_DETAIL_NOT_READY")
        return read

    async def get_order(self, oid):
        return order_raw((await self.source("order:" + oid)).data)

    async def get_trades(self, *, market=None, asset_id=None):
        if market not in (None, self.meta.condition_id):
            raise GatewayReadError("SYNC_MARKET_SCOPE_MISMATCH")
        data = [trade_raw(row) for row in (await self.source("trades")).data]
        if asset_id is not None:
            data = [r for r in data if any(x["asset_id"] == asset_id for x in r["maker_orders"])]
        self.e.gateway._client.last_trades = data
        return copy.deepcopy(data)

    async def open_orders(self):
        rows = [order_raw(row) for row in (await self.source("open_orders")).data]
        self.e.gateway._client.last_open_rows = rows
        return [OpenOrder(r["id"], r["asset_id"], Side(r["side"]), float(r["price"]),
                          float(r["original_size"]) - float(r["size_matched"]), OrderState.LIVE)
                for r in rows]

    async def positions(self):
        return {r["token_id"]: (r["size"], r["avg_price"])
                for r in (await self.source("positions")).data}

    async def capital(self):
        return (await self.source("capital")).data

    async def market(self, slug):
        if slug != self.meta.slug:
            raise GatewayReadError("SYNC_MARKET_SCOPE_MISMATCH")
        return copy.deepcopy(self.raw_market)

    async def server_time(self):
        read = await self.source("time")
        return read.data + time.monotonic() - read.completed_monotonic
