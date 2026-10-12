"""Dedicated one-market production composition and official account-risk reads.

Importing this module performs no I/O. Only rdp-sm-run calls run_current_market.
Historical labels never become fills. The CLI uses official balance as its
starting capital; the operator manages pre-existing account commitments.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
import uuid
from datetime import datetime
from decimal import Decimal

from polymaker.btc5m import current_epoch, slug_for_epoch
from polymaker.catalog.gamma import GammaClient
from polymaker.domain import Side, TradeState
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
from polymaker.userstream.parse import parse_trade, strict_number


async def exact_market(gamma, **query):
    """Use the existing Gamma transport, retaining conflicting rows for rejection."""
    # Gamma defaults to closed=false. Historical condition-ID lookups must
    # explicitly include closed markets; an expired market may also still be
    # awaiting closure/resolution, so try the open set only on an empty result.
    historical = "condition_ids" in query and "closed" not in query
    params = {**query, **({"closed": "true"} if historical else {}), "limit": 2}
    response = await gamma._client.get("/markets", params=params)
    response.raise_for_status()
    rows = response.json()
    if historical and rows == []:
        response = await gamma._client.get("/markets", params={**params, "closed": "false"})
        response.raise_for_status()
        rows = response.json()
    _require(isinstance(rows, list) and len(rows) == 1, "MARKET_IDENTITY_AMBIGUOUS")
    _require(isinstance(rows[0], dict), "MARKET_RESPONSE_INVALID")
    return rows[0]


async def discover_current_market(cfg, *, observer=None):
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
    slug = slug_for_epoch(current_epoch(now))
    async with GammaClient(cfg.wallet.gamma_host) as gamma:
        raw = await exact_market(gamma, slug=slug)
    meta, start = _market(raw, slug)
    elapsed = now - start
    if observer is not None:
        observer.emit("discovery", {"slug": meta.slug, "condition_id": meta.condition_id,
                                     "start": start, "end": start + 300,
                                     "elapsed_s": elapsed, "official_time": now})
    if not TRADING_START_S <= elapsed < NORMAL_END_S:
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
    """Retained read evidence for the existing M1/M2 collateral calculation.

    This adds neither inventory nor order ownership. M2 remains the only executor.
    The CLI explicitly uses startup balance without pre-existing deductions.
    Library callers retain conservative accounting by default. Both paths
    keep current-market pending exposure and account identity checks.
    """

    def __init__(self, *, use_starting_balance=False):
        self.pending = {}
        self.historical = {}
        self.use_starting_balance = use_starting_balance
        self.starting_order_ids = None

    async def inspect(self, reads, meta, positions, orders):
        client, gateway = reads.gateway._client, reads.gateway
        if self.starting_order_ids is None:
            self.starting_order_ids = {o.order_id for o in orders}
        rows = await gateway.get_trades()  # SDK complete pagination, sticky failures
        seen = set()
        for row in rows:
            trade = parse_trade(row)
            _require(trade.trade_id not in seen, "ACCOUNT_TRADE_ID_CONFLICT")
            seen.add(trade.trade_id)
            if trade.status in (TradeState.CONFIRMED, TradeState.FAILED):
                continue
            # Owned pending economics belong to M2's confirmed-only close barrier.
            ids = {leg.order_id for leg in trade.maker_legs}
            if ids & client.known_ids:
                ours = {
                    leg.order_id
                    for leg in trade.maker_legs
                    if leg.maker_address and leg.maker_address.lower() == gateway.funder.lower()
                }
                _require(
                    row.get("trader_side") == "MAKER" and bool(ours) and ours <= client.known_ids,
                    "OWNED_TRADE_ROLE_UNKNOWN",
                )
                continue
            prior = self.pending.setdefault(trade.trade_id, row)
            _require(prior == row, "PENDING_TRADE_EVIDENCE_CHANGED")
        reserved = Decimal(0)
        async with GammaClient(reads.cfg.wallet.gamma_host) as gamma:
            for tid, row in self.pending.items():
                trade = parse_trade(row)
                _require(
                    re.fullmatch(r"0x[0-9a-fA-F]{64}", trade.market) is not None,
                    "PENDING_MARKET_IDENTITY_UNKNOWN",
                )
                _require(trade.market != meta.condition_id, "CURRENT_MARKET_PENDING_EXPOSURE")
                stamp = strict_number(row.get("match_time", row.get("timestamp")))
                _require(stamp > 0, "PENDING_TRADE_TIME_UNKNOWN")
                raw = await exact_market(gamma, condition_ids=trade.market)
                historical_slug = raw.get("slug", "")
                btc5m = re.fullmatch(r"btc-updown-5m-([0-9]+)", historical_slug)
                previous_btc5m = (
                    btc5m is not None and int(btc5m[1]) > 0 and int(btc5m[1]) % 300 == 0
                    and int(btc5m[1]) <= stamp <= int(btc5m[1]) + 300
                    and int(btc5m[1]) + 300 <= int(meta.slug.rsplit("-", 1)[1]))
                _require(
                    raw.get("conditionId") == trade.market
                    and (previous_btc5m or (raw.get("closed") is True
                         and raw.get("acceptingOrders") is False
                         and raw.get("umaResolutionStatus") == "resolved")),
                    "ACCOUNT_PENDING_EXPOSURE_UNRESOLVED",
                )
                end = datetime.fromisoformat(raw["endDate"].replace("Z", "+00:00"))
                _require(
                    end.tzinfo is not None
                    and stamp <= end.timestamp() <= int(meta.slug.rsplit("-", 1)[1])
                    and (not previous_btc5m or end.timestamp() == int(btc5m[1]) + 300),
                    "HISTORICAL_MARKET_TIME_UNPROVEN",
                )
                if row.get("trader_side") == "MAKER":
                    legs = [
                        leg
                        for leg in trade.maker_legs
                        if leg.maker_address and leg.maker_address.lower() == gateway.funder.lower()
                    ]
                    _require(bool(legs), "PENDING_ACCOUNT_ATTRIBUTION_UNKNOWN")
                    _require(all(leg.side is Side.BUY for leg in legs), "PENDING_SIDE_UNSUPPORTED")
                    ids = {leg.order_id for leg in legs}
                    tokens = {leg.asset_id for leg in legs}
                    cost = sum(
                        (
                            Decimal(str(leg.price)) * Decimal(str(leg.matched_amount))
                            for leg in legs
                        ),
                        Decimal(0),
                    )
                elif row.get("trader_side") == "TAKER":
                    _require(
                        row.get("side") == "BUY"
                        and bool(trade.taker_order_id)
                        and bool(trade.taker_asset_id),
                        "PENDING_ACCOUNT_ATTRIBUTION_UNKNOWN",
                    )
                    ids, tokens = {trade.taker_order_id}, {trade.taker_asset_id}
                    cost = Decimal(str(strict_number(row.get("price")))) * Decimal(
                        str(strict_number(row.get("size")))
                    )
                else:
                    _require(False, "PENDING_ACCOUNT_ATTRIBUTION_UNKNOWN")
                market_tokens = raw.get("clobTokenIds")
                market_tokens = (
                    json.loads(market_tokens) if isinstance(market_tokens, str) else market_tokens
                )
                _require(
                    isinstance(market_tokens, list)
                    and len(market_tokens) == 2
                    and tokens <= set(market_tokens)
                    and not tokens & {meta.yes.token_id, meta.no.token_id},
                    "HISTORICAL_TOKEN_IDENTITY_UNPROVEN",
                )
                _require(cost.is_finite() and cost > 0, "PENDING_COMMITMENT_UNKNOWN")
                _require(
                    not any((o.order_id in ids or o.token_id in tokens)
                            and not (self.use_starting_balance and o.order_id in self.starting_order_ids)
                            for o in orders)
                    and (self.use_starting_balance or previous_btc5m or not any(t in positions for t in tokens))
                    and not (ids & (client.observed_open_rows.keys() -
                                    (self.starting_order_ids if self.use_starting_balance else set()))),
                    "HISTORICAL_CURRENT_COMMITMENT_OBSERVED",
                )
                # Retain the cost even if the next trade snapshot omits this row.
                reserved += cost
                self.historical[tid] = {
                    "status": row["status"],
                    "decision": "HISTORICAL_MATCHED_NONBLOCKING",
                    "pending_cost_reserved_usd": str(cost),
                    "redeem": "NOT_VERIFIED",
                }
        return reserved


async def run_current_market(cfg, *, sleep=None, monotonic=None, observer=None):
    """Called once by the dedicated CLI. No retries or next-market selection."""
    sleep = asyncio.sleep if sleep is None else sleep
    monotonic = time.monotonic if monotonic is None else monotonic
    session = uuid.uuid4().hex
    try:
        meta, start, now, _ = await discover_current_market(cfg, observer=observer)
        # Pin discovery to this market while waiting; never select the next one.
        clock = ReadOnlyClobClient(host=cfg.wallet.clob_host, chain_id=137)
        if now - start < TRADING_START_S and observer is not None:
            observer.emit("phase", {"elapsed_s": now - start, "state": "WAIT_UNTIL_T15"})
        while now - start < TRADING_START_S:
            await sleep(min(1, TRADING_START_S - (now - start)))
            latest = strict_number(await asyncio.to_thread(clock.get_server_time))
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
        return await loop.run_production(expected_market=meta,
            account_policy=AccountRiskPolicy(use_starting_balance=True))
    except Exception as exc:
        if observer is not None:
            observer.emit("diagnostic", {"reason": str(exc) if isinstance(exc, _Issue)
                                         else f"ENTRY_FAILED:{type(exc).__name__}",
                                         "context": getattr(exc, "context", None)})
        return RunResult(
            None,
            session,
            stop_time=time.time(),
            stop_reason=str(exc)
            if isinstance(exc, _Issue)
            else f"ENTRY_FAILED:{type(exc).__name__}",
        )
