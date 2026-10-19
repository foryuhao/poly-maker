"""Dedicated one-market production composition and official account-risk reads.

Importing this module performs no I/O. Only rdp-sm-run calls run_current_market.
Historical labels never become fills. The CLI uses official balance as its
starting capital; the operator manages pre-existing account commitments.
"""

from __future__ import annotations

import asyncio
import math
import time
import uuid
from contextlib import suppress

from polymaker.btc5m import current_epoch, slug_for_epoch
from polymaker.catalog.gamma import GammaClient
from polymaker.domain import TradeState
from polymaker.rdp_sm import (
    NORMAL_END_S,
    OFFICIAL_HOSTS,
    TRADING_END_S,
    TRADING_START_S,
    ReadOnlyClobClient,
    _book,
    _Issue,
    _market,
    _require,
)
from polymaker.rdp_sm_loop import RunResult, SMTradingLoop
from polymaker.rdp_sm_recovery import reason_code, retry_read
from polymaker.userstream.parse import parse_trade, strict_number


async def exact_market(gamma, *, slug):
    """Read only the selected market; reject ambiguous discovery responses."""
    response = await gamma._client.get("/markets", params={"slug": slug, "limit": 2})
    response.raise_for_status()
    rows = response.json()
    _require(rows != [], "MARKET_NOT_AVAILABLE")
    _require(isinstance(rows, list) and len(rows) == 1, "MARKET_IDENTITY_AMBIGUOUS")
    _require(isinstance(rows[0], dict), "MARKET_RESPONSE_INVALID")
    return rows[0]


async def discover_current_market(cfg, *, observer=None, _pinned=None, _monotonic=time.monotonic):
    _require(
        all(getattr(cfg.wallet, k).rstrip("/") == v for k, v in OFFICIAL_HOSTS.items())
        and cfg.wallet.chain_id == 137,
        "OFFICIAL_ENDPOINT_CONFIGURATION_REQUIRED",
    )
    began = time.monotonic()
    client = ReadOnlyClobClient(host=cfg.wallet.clob_host, chain_id=137)
    # Public official time; credentials are unnecessary before the startup rule.
    now = strict_number(await asyncio.to_thread(client.get_server_time))
    _require(now > 0, "OFFICIAL_TIME_INVALID")
    slug = (_pinned or {}).get("slug") or slug_for_epoch(current_epoch(now))
    if _pinned is not None:
        start = int(slug.rsplit("-", 1)[1])
        _pinned.setdefault("slug", slug)
        _pinned.setdefault("deadline", _monotonic() + start + 300 - now)
        _require(now < start + 300, "DISCOVERY_MARKET_EXPIRED")
    async with GammaClient(cfg.wallet.gamma_host) as gamma:
        raw = await exact_market(gamma, slug=slug)
    meta, start = _market(raw, slug)
    if _pinned is not None:
        previous = _pinned.setdefault("meta", meta)
        _require(previous.condition_id == meta.condition_id and previous.tokens == meta.tokens,
                 "DISCOVERED_MARKET_CHANGED")
    elapsed = now - start
    if observer is not None:
        observer.emit("discovery", {"slug": meta.slug, "condition_id": meta.condition_id,
                                     "start": start, "end": start + 300,
                                     "elapsed_s": elapsed, "official_time": now})
    if cfg.rdp_sm.normal_entry_mode == "obi_v1" or not TRADING_START_S <= elapsed < NORMAL_END_S:
        return meta, start, now, {}
    books = {}
    for token in (meta.yes.token_id, meta.no.token_id):
        book = await asyncio.to_thread(client.get_order_book, token)
        tick = await asyncio.to_thread(client.get_tick_size, token)
        books[token] = _book(book, tick, token, meta)
    latest = strict_number(await asyncio.to_thread(client.get_server_time))
    age = cfg.risk.ws_stale_halt_s
    _require(
        math.isfinite(age)
        and age > 0
        and time.monotonic() - began <= age
        and latest >= now
        and current_epoch(latest) == start
        and all(-2 <= latest - b["timestamp"] <= age for b in books.values()),
        "DISCOVERY_TIME_OR_DATA_UNRELIABLE",
    )
    return meta, start, latest, books


class AccountRiskPolicy:
    """Current-market pending protection and existing startup funding basis.

    Observations live only for this Session. Other markets are never queried
    for history or resolution. M2 retains ownership and confirmed-fill checks.
    """

    def __init__(self, *, use_starting_balance=False, sync_observer=None):
        self.pending = {}
        self.market_id = None
        self.use_starting_balance = use_starting_balance
        self.starting_order_ids = None
        self.sync_observer = sync_observer

    def observe_trade(self, trade, known_ids):
        # Also called at each official page, before a later page can fail.
        # Partial reads may retain risk, but cannot resolve it.
        if trade.market != self.market_id:
            return

        def identity(value):
            return (value.market, value.raw.get("trader_side"), value.taker_order_id,
                    value.taker_asset_id, value.taker_side,
                    tuple(sorted(value.maker_legs, key=lambda leg: leg.order_id)))

        if trade.trade_id in self.pending:
            _require(identity(parse_trade(self.pending[trade.trade_id])) == identity(trade),
                     "PENDING_TRADE_EVIDENCE_CHANGED")
        if trade.status in (TradeState.CONFIRMED, TradeState.FAILED):
            return
        ids = {leg.order_id for leg in trade.maker_legs}
        if ids & known_ids:
            return
        self.pending.setdefault(trade.trade_id, trade.raw)

    async def inspect(self, reads, meta, positions, orders):
        client, gateway = reads.gateway._client, reads.gateway
        if self.starting_order_ids is None:
            self.starting_order_ids = {o.order_id for o in orders}
        self.market_id = meta.condition_id
        rows = await gateway.get_trades(market=meta.condition_id)
        seen = set()
        resolved = set()
        for row in rows:
            trade = parse_trade(row)
            # The official query is scoped to this market. Unrelated account
            # history does not become a Session dependency or pending reserve.
            if trade.market != meta.condition_id:
                continue
            _require(trade.trade_id not in seen, "ACCOUNT_TRADE_ID_CONFLICT")
            seen.add(trade.trade_id)
            self.observe_trade(trade, client.known_ids)
            if trade.status in (TradeState.CONFIRMED, TradeState.FAILED):
                resolved.add(trade.trade_id)
            elif {leg.order_id for leg in trade.maker_legs} & client.known_ids:
                ours = {
                    leg.order_id for leg in trade.maker_legs
                    if leg.maker_address and leg.maker_address.lower() == gateway.funder.lower()
                }
                _require(row.get("trader_side") == "MAKER" and bool(ours) and ours <= client.known_ids,
                         "OWNED_TRADE_ROLE_UNKNOWN")
        # Only explicit, consistent terminal evidence in a complete read can
        # resolve pending risk. Omission and status/metadata updates cannot.
        resolved_pending = resolved & self.pending.keys()
        for trade_id in resolved_pending:
            self.pending.pop(trade_id)
        # Unknown pending exposure in the current market remains fail-closed,
        # even when a later page omits it. Owned fills use the original M2 gate.
        # Positions and balance were fetched before this trade read. Require
        # another full account snapshot after newly resolved exposure.
        _require(not self.pending and not resolved_pending, "CURRENT_MARKET_PENDING_EXPOSURE")


async def run_current_market(cfg, *, sleep=None, monotonic=None, observer=None, shadow_mode=False):
    """One market per invocation; optionally own a read-only sync companion."""
    if cfg.rdp_sm.normal_entry_mode == "obi_v1" and cfg.rdp_sm.repair.policy == "hd_tp_sl_v1":
        from polymaker.repair_policy import repair_readiness

        readiness = repair_readiness(cfg.rdp_sm.repair)
        if not readiness["ready_for_live"]:
            if observer is not None:
                observer.emit("diagnostic", {"reason": "REPAIR_NOT_READY_FOR_LIVE",
                                              "repair_readiness": readiness})
            return RunResult(None, "", stop_time=time.time(),
                             stop_reason="REPAIR_NOT_READY_FOR_LIVE", repair_readiness=readiness)
    companion = None
    session = None
    if cfg.rdp_sm.sync_observe.enabled:
        try:
            from polymaker.rdp_sm_sync_companion import SyncCompanion

            session = uuid.uuid4().hex
            companion = SyncCompanion(cfg, session)
            companion.start()
        except Exception as exc:
            import logging

            logging.getLogger("polymaker.sync_observe").warning(
                "SYNC_OBSERVER_START_FAILED error=%s", type(exc).__name__)
    try:
        result = await _run_current_market(cfg, sleep=sleep, monotonic=monotonic,
            observer=observer, shadow_mode=shadow_mode, session_id=session, sync_observer=companion)
        if companion is not None:
            with suppress(Exception):
                companion.capture("entry_result", result.to_dict())
        return result
    finally:
        if companion is not None:
            from polymaker.rdp_sm_sync_companion import close_companion

            try:
                await close_companion(companion)
            except Exception as exc:
                import logging

                logging.getLogger("polymaker.sync_observe").warning(
                    "SYNC_OBSERVER_CLOSE_FAILED error=%s", type(exc).__name__)


async def _run_current_market(cfg, *, sleep=None, monotonic=None, observer=None, shadow_mode=False,
                              session_id=None, sync_observer=None):
    """Retry read failures within one pinned market; never select a next market."""
    sleep = asyncio.sleep if sleep is None else sleep
    monotonic = time.monotonic if monotonic is None else monotonic
    session = session_id or uuid.uuid4().hex
    pinned = {}
    discovery_attempts = 0
    discovery_error = None
    try:
        while True:
            if (discovery_error is not None and "deadline" in pinned
                    and monotonic() >= pinned["deadline"]):
                raise discovery_error
            try:
                meta, start, now, _ = await discover_current_market(
                    cfg, observer=observer, _pinned=pinned, _monotonic=monotonic)
                break
            except Exception as exc:
                discovery_error = exc
                discovery_attempts += 1
                deadline = pinned.get("deadline")
                if (not retry_read(exc) or deadline is not None and monotonic() >= deadline
                        or deadline is None and discovery_attempts >= 3):
                    raise
                if observer is not None:
                    observer.emit("recovery", {"status": "PAUSED", "stage": "discovery",
                        "reason": reason_code(exc), "context": getattr(exc, "context", None),
                        "elapsed_s": 300 - (deadline - monotonic()) if deadline is not None else None})
                await sleep(min(2, max(0, deadline - monotonic())) if deadline is not None else 2)
        if sync_observer is not None:
            try:
                sync_observer.bind_market(meta, start)
            except Exception as exc:
                import logging

                logging.getLogger("polymaker.sync_observe").warning(
                    "SYNC_OBSERVER_BIND_FAILED error=%s", type(exc).__name__)
        if shadow_mode:
            # Independent read-only process; no executor/Gateway capability is handed off.
            try:
                from polymaker.risk_shadow_launch import launch_shadow

                launch_shadow(cfg, meta, session)
            except Exception as exc:
                import logging

                logging.getLogger("polymaker.risk_shadow").error(
                    "SHADOW_ERROR launch=%s session=%s", type(exc).__name__, session)
        # Pin discovery to this market while waiting; never select the next one.
        clock = ReadOnlyClobClient(host=cfg.wallet.clob_host, chain_id=137)
        if now - start < TRADING_START_S and observer is not None:
            observer.emit("phase", {"elapsed_s": now - start, "state": "WAIT_UNTIL_T15"})
        while now - start < TRADING_START_S:
            await sleep(min(1, TRADING_START_S - (now - start)))
            try:
                latest = strict_number(await asyncio.to_thread(clock.get_server_time))
            except Exception as exc:
                if not retry_read(exc) or monotonic() >= pinned["deadline"]:
                    raise
                if observer is not None:
                    observer.emit("recovery", {"status": "PAUSED", "stage": "wait_until_t15",
                        "reason": reason_code(exc), "elapsed_s": 300 - (pinned["deadline"] - monotonic())})
                await sleep(min(2, pinned["deadline"] - monotonic()))
                continue
            _require(latest >= now, "OFFICIAL_TIME_INVALID")
            now = latest
        if now - start >= TRADING_END_S:
            return RunResult(
                meta.condition_id,
                session,
                stop_time=time.time(),
                stop_elapsed_s=now - start,
                stop_reason="STARTUP_OUTSIDE_TRADING_WINDOW",
                execution_status="COMPLETED",
                final_close_barrier="SAFE_TO_CONTINUE",
            )
        loop = SMTradingLoop(cfg, meta.slug, session, sleep=sleep, monotonic=monotonic,
                             observer=observer)
        loop.market_deadline = pinned["deadline"]
        return await loop.run_production(expected_market=meta,
            account_policy=AccountRiskPolicy(use_starting_balance=True, sync_observer=sync_observer))
    except Exception as exc:
        if observer is not None:
            observer.emit("diagnostic", {"reason": str(exc) if isinstance(exc, _Issue)
                                         else f"ENTRY_FAILED:{type(exc).__name__}",
                                         "context": getattr(exc, "context", None)})
        return RunResult(
            pinned["meta"].condition_id if "meta" in pinned else None,
            session,
            stop_time=time.time(),
            stop_elapsed_s=300 - (pinned["deadline"] - monotonic()) if "deadline" in pinned else None,
            stop_reason=str(exc)
            if isinstance(exc, _Issue)
            else f"ENTRY_FAILED:{type(exc).__name__}",
        )
