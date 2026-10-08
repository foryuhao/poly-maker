"""Read-only exact-market metadata bootstrap for RDP Phase 0."""

from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

import httpx

from polymaker.btc5m import PERIOD_S, slug_for_epoch
from polymaker.catalog.gamma import GammaClient, parse_market
from polymaker.config import Config
from polymaker.domain import MarketMeta
from polymaker.logging import get_logger

log = get_logger("rdp.live")

_SLUG_RE = re.compile(r"btc-updown-5m-(0|[1-9][0-9]*)\Z")
_GAMMA_ORIGIN = "https://gamma-api.polymarket.com"
_CLOB_ORIGIN = "https://clob.polymarket.com"


class RDPBootstrapError(ValueError):
    """A categorized fail-closed error from public RDP bootstrap."""

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


@dataclass(frozen=True, slots=True)
class TokenBookMetadata:
    outcome: str
    token_id: str
    tick_size: float
    min_order_size: float
    neg_risk: bool


@dataclass(frozen=True, slots=True)
class RDPBootstrap:
    market: MarketMeta
    epoch: int
    end_epoch: int
    token_metadata: tuple[TokenBookMetadata, TokenBookMetadata]
    receipt_ts: float


@dataclass(frozen=True, slots=True)
class RDPMarketStatusEvidence:
    """One exact Gamma status response, bound to the retained BTC5m identity."""

    slug: str
    condition_id: str
    token_ids: tuple[str, str]
    outcomes: tuple[str, str]
    neg_risk: bool
    end_epoch: int
    accepting_orders: bool
    closed: bool
    receipt_ts: float
    age_s: float | None = None
    max_age_s: float | None = None


@dataclass(frozen=True, slots=True)
class RDPPreflight:
    bootstrap: RDPBootstrap
    feed: Any
    account: Any | None = None
    account_backing: Any | None = None
    user_channel: Any | None = None


def _validate_official_origin(host: str, expected: str, category: str) -> None:
    try:
        parsed = urlsplit(host)
        expected_parts = urlsplit(expected)
        valid = (
            parsed.scheme == "https"
            and parsed.hostname == expected_parts.hostname
            and parsed.port is None
            and parsed.username is None
            and parsed.password is None
            and parsed.path in ("", "/")
            and parsed.query == ""
            and parsed.fragment == ""
        )
    except ValueError:
        valid = False
    if not valid:
        raise RDPBootstrapError(category, f"unapproved official API origin: {host!r}")


def _parse_slug(slug: str) -> tuple[int, int]:
    match = _SLUG_RE.fullmatch(slug) if isinstance(slug, str) else None
    if match is None:
        raise RDPBootstrapError("INVALID_MARKET_SLUG", "market must be a canonical BTC5m slug")
    try:
        epoch = int(match.group(1))
    except ValueError as exc:
        raise RDPBootstrapError("INVALID_MARKET_SLUG", "BTC5m slug epoch is invalid") from exc
    if epoch <= 0 or epoch % PERIOD_S or slug_for_epoch(epoch) != slug:
        raise RDPBootstrapError("INVALID_MARKET_SLUG", "BTC5m slug epoch must be positive and aligned")
    return epoch, epoch + PERIOD_S


def _raw_pair(value: Any, field: str) -> list[Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError) as exc:
            raise RDPBootstrapError("INVALID_GAMMA_MARKET", f"Gamma {field} is malformed") from exc
    if not isinstance(value, list) or len(value) != 2:
        raise RDPBootstrapError("INVALID_GAMMA_MARKET", f"Gamma {field} must contain two values")
    return value


def _validate_market(raw: Any, slug: str, epoch: int, end_epoch: int) -> MarketMeta:
    if not isinstance(raw, dict):
        raise RDPBootstrapError("INVALID_GAMMA_MARKET", "Gamma market response is not an object")
    if raw.get("slug") != slug:
        raise RDPBootstrapError("MARKET_IDENTITY_MISMATCH", "Gamma returned a different market slug")
    condition_id = raw.get("conditionId")
    if not isinstance(condition_id, str) or not condition_id.strip():
        raise RDPBootstrapError("INVALID_GAMMA_MARKET", "Gamma condition ID is missing")
    if raw.get("acceptingOrders") is not True or raw.get("closed") is not False:
        raise RDPBootstrapError("MARKET_NOT_TRADABLE", "Gamma market is not explicitly open for orders")
    if not isinstance(raw.get("negRisk"), bool):
        raise RDPBootstrapError("INVALID_GAMMA_MARKET", "Gamma negRisk metadata is missing or invalid")

    token_ids = _raw_pair(raw.get("clobTokenIds"), "token IDs")
    outcomes = _raw_pair(raw.get("outcomes"), "outcomes")
    if (any(not isinstance(token, str) or not token.strip() for token in token_ids)
            or len(set(token_ids)) != 2):
        raise RDPBootstrapError("INVALID_GAMMA_MARKET", "Gamma outcome token IDs must be distinct strings")
    if outcomes != ["Up", "Down"]:
        raise RDPBootstrapError(
            "INVALID_OUTCOME_MAPPING", "BTC5m token positions must map Up then Down")

    end_value = raw.get("endDate")
    if not isinstance(end_value, str) or not end_value.strip():
        raise RDPBootstrapError("INVALID_END_TIME", "Gamma market end timestamp is missing")
    try:
        end_dt = datetime.fromisoformat(end_value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise RDPBootstrapError("INVALID_END_TIME", "Gamma market end timestamp is malformed") from exc
    if end_dt.tzinfo is None or end_dt.utcoffset() is None:
        raise RDPBootstrapError("INVALID_END_TIME", "Gamma market end timestamp must be timezone-aware")
    try:
        timestamp = end_dt.timestamp()
    except (OverflowError, OSError, ValueError) as exc:
        raise RDPBootstrapError("INVALID_END_TIME", "Gamma market end timestamp is out of range") from exc
    if timestamp != end_epoch or end_epoch != epoch + PERIOD_S:
        raise RDPBootstrapError("INVALID_END_TIME", "Gamma market end does not match the selected interval")

    market = parse_market(raw)
    if (market is None or market.slug != slug or market.condition_id != condition_id
            or tuple(token.token_id for token in market.tokens) != tuple(token_ids)
            or tuple(token.outcome for token in market.tokens) != tuple(outcomes)):
        raise RDPBootstrapError("INVALID_GAMMA_MARKET", "Gamma market could not be parsed consistently")
    return market


def _validate_status_market(raw: Any, slug: str, epoch: int, end_epoch: int) -> MarketMeta:
    """Preflight parser-converted metadata before delegating to the shared validator."""
    if isinstance(raw, dict):
        fee = raw.get("feeSchedule") or {}
        if not isinstance(fee, dict):
            raise RDPBootstrapError("INVALID_GAMMA_MARKET", "Gamma market metadata is malformed")
        numeric_fields = [
            ("feeSchedule.rate", fee.get("rate", 0.0), 0.0),
            ("feeSchedule.rebateRate", fee.get("rebateRate", 0.0), 0.0),
            ("orderPriceMinTickSize", raw.get("orderPriceMinTickSize", 0.001), 0.001),
            ("orderMinSize", raw.get("orderMinSize", 5), 5),
            ("rewardsMinSize", raw.get("rewardsMinSize", 0), 0),
            ("rewardsMaxSpread", raw.get("rewardsMaxSpread", 0), 0),
            ("bestBid", raw.get("bestBid", 0), 0),
            ("bestAsk", raw.get("bestAsk", 0), 0),
            ("liquidityNum", raw.get("liquidityNum", 0), 0),
            ("volumeNum", raw.get("volumeNum", 0), 0),
            ("volume24hr", raw.get("volume24hrClob") or raw.get("volume24hr") or 0, 0),
        ]
        for _field, value, default in numeric_fields:
            try:
                float(value or default)
            except (TypeError, ValueError, OverflowError):
                raise RDPBootstrapError(
                    "INVALID_GAMMA_MARKET", "Gamma market metadata is malformed",
                ) from None
    return _validate_market(raw, slug, epoch, end_epoch)


def validate_rdp_market_status_config(cfg: Config) -> tuple[str, float]:
    """Freeze the approved Gamma origin and status-age limit before startup awaits."""
    origin = cfg.wallet.gamma_host
    _validate_official_origin(origin, _GAMMA_ORIGIN, "UNAPPROVED_GAMMA_ORIGIN")
    limit = cfg.risk.ws_stale_halt_s
    if (isinstance(limit, bool) or not isinstance(limit, (int, float))
            or not math.isfinite(limit) or limit <= 0):
        raise RDPBootstrapError(
            "INVALID_MARKET_STATUS_MAX_AGE", "market-status freshness limit must be finite and positive",
        )
    return str(origin), float(limit)


async def read_rdp_market_status(
    bootstrap: RDPBootstrap,
    gamma_origin: str,
    *,
    now: Callable[[], float] = time.time,
) -> RDPMarketStatusEvidence:
    """Read and validate fresh tradability for the already selected exact market."""
    _validate_official_origin(gamma_origin, _GAMMA_ORIGIN, "UNAPPROVED_GAMMA_ORIGIN")
    if now() >= bootstrap.end_epoch:
        raise RDPBootstrapError("MARKET_INTERVAL_ELAPSED", "selected BTC5m interval has ended")
    try:
        async with GammaClient(gamma_origin, safe_diagnostics=True) as gamma:
            raw = await gamma.market_by_slug(bootstrap.market.slug)
            receipt_ts = now()
    except RDPBootstrapError:
        raise
    except Exception as exc:  # noqa: BLE001 - status failure is categorical and sanitized
        raise RDPBootstrapError(
            "MARKET_STATUS_READ_FAILED", f"official Gamma status read failed ({type(exc).__name__})",
        ) from None
    if raw is None:
        raise RDPBootstrapError("MARKET_STATUS_LOOKUP_FAILED", "exact Gamma status lookup returned no market")
    epoch, end_epoch = _parse_slug(bootstrap.market.slug)
    market = _validate_status_market(raw, bootstrap.market.slug, epoch, end_epoch)
    expected_tokens = tuple(token.token_id for token in bootstrap.market.tokens)
    expected_outcomes = tuple(token.outcome for token in bootstrap.market.tokens)
    if (market.slug != bootstrap.market.slug
            or market.condition_id != bootstrap.market.condition_id
            or tuple(token.token_id for token in market.tokens) != expected_tokens
            or tuple(token.outcome for token in market.tokens) != expected_outcomes
            or market.neg_risk is not bootstrap.market.neg_risk
            or end_epoch != bootstrap.end_epoch):
        raise RDPBootstrapError(
            "MARKET_STATUS_IDENTITY_MISMATCH",
            "fresh Gamma status does not match the retained market identity",
        )
    if (isinstance(receipt_ts, bool) or not isinstance(receipt_ts, (int, float))
            or not math.isfinite(receipt_ts)):
        raise RDPBootstrapError("INVALID_MARKET_STATUS_RECEIPT", "Gamma status receipt time is invalid")
    if receipt_ts >= bootstrap.end_epoch:
        raise RDPBootstrapError("MARKET_INTERVAL_ELAPSED", "selected BTC5m interval ended during status read")
    return RDPMarketStatusEvidence(
        slug=market.slug,
        condition_id=market.condition_id,
        token_ids=expected_tokens,
        outcomes=expected_outcomes,
        neg_risk=market.neg_risk,
        end_epoch=end_epoch,
        accepting_orders=True,
        closed=False,
        receipt_ts=float(receipt_ts),
    )


def _positive_number(value: Any, *, field: str, token_id: str, upper_bound: Decimal | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise RDPBootstrapError("INVALID_BOOK_METADATA", f"CLOB {field} is invalid for token {token_id}")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise RDPBootstrapError("INVALID_BOOK_METADATA", f"CLOB {field} is invalid for token {token_id}") from exc
    if (not number.is_finite() or number <= 0
            or (upper_bound is not None and number >= upper_bound)):
        raise RDPBootstrapError("INVALID_BOOK_METADATA", f"CLOB {field} is out of range for token {token_id}")
    try:
        result = float(number)
    except (OverflowError, ValueError) as exc:
        raise RDPBootstrapError("INVALID_BOOK_METADATA", f"CLOB {field} is not representable for token {token_id}") from exc
    if not math.isfinite(result) or result <= 0:
        raise RDPBootstrapError("INVALID_BOOK_METADATA", f"CLOB {field} is not representable for token {token_id}")
    if Decimal(str(result)) != number:
        raise RDPBootstrapError(
            "INVALID_BOOK_METADATA", f"CLOB {field} loses precision for token {token_id}")
    return result


def _validate_book_metadata(raw: Any, market: MarketMeta, token_id: str) -> TokenBookMetadata:
    if not isinstance(raw, dict):
        raise RDPBootstrapError("INVALID_BOOK_METADATA", f"CLOB book is not an object for token {token_id}")
    if raw.get("market") != market.condition_id or raw.get("asset_id") != token_id:
        raise RDPBootstrapError("BOOK_IDENTITY_MISMATCH", f"CLOB book identity mismatch for token {token_id}")
    neg_risk = raw.get("neg_risk")
    if not isinstance(neg_risk, bool) or neg_risk is not market.neg_risk:
        raise RDPBootstrapError("INVALID_BOOK_METADATA", f"CLOB neg_risk mismatch for token {token_id}")
    tick = _positive_number(raw.get("tick_size"), field="tick_size", token_id=token_id,
                            upper_bound=Decimal(1))
    minimum = _positive_number(raw.get("min_order_size"), field="min_order_size",
                               token_id=token_id)
    outcome = next(token.outcome for token in market.tokens if token.token_id == token_id)
    return TokenBookMetadata(outcome, token_id, tick, minimum, neg_risk)


async def bootstrap_rdp_market(
    cfg: Config,
    slug: str,
    *,
    now: Callable[[], float] = time.time,
) -> RDPBootstrap:
    """Select one exact BTC5m market and read its two public CLOB books."""
    try:
        epoch, end_epoch = _parse_slug(slug)
        gamma_origin = cfg.wallet.gamma_host
        clob_origin = cfg.wallet.clob_host
        _validate_official_origin(gamma_origin, _GAMMA_ORIGIN, "UNAPPROVED_GAMMA_ORIGIN")
        _validate_official_origin(clob_origin, _CLOB_ORIGIN, "UNAPPROVED_CLOB_ORIGIN")
        if now() >= end_epoch:
            raise RDPBootstrapError("MARKET_INTERVAL_ELAPSED", "selected BTC5m interval has ended")

        async with GammaClient(gamma_origin) as gamma:
            try:
                raw = await gamma.market_by_slug(slug)
            except (AttributeError, IndexError) as exc:
                raise RDPBootstrapError(
                    "INVALID_GAMMA_RESPONSE", "Gamma market response has an invalid envelope"
                ) from exc
        if raw is None:
            raise RDPBootstrapError("GAMMA_LOOKUP_FAILED", "exact Gamma market lookup returned no market")
        market = _validate_market(raw, slug, epoch, end_epoch)
        if now() >= end_epoch:
            raise RDPBootstrapError("MARKET_INTERVAL_ELAPSED", "selected BTC5m interval ended during lookup")

        timeout = httpx.Timeout(10.0, connect=5.0)
        limits = httpx.Limits(max_connections=2, max_keepalive_connections=2)
        async with httpx.AsyncClient(base_url=clob_origin.rstrip("/"),
                                     timeout=timeout, limits=limits) as client:
            metadata: list[TokenBookMetadata] = []
            for token in market.tokens:
                try:
                    response = await client.get("/book", params={"token_id": token.token_id})
                    response.raise_for_status()
                    body = json.loads(response.content, parse_float=Decimal, parse_int=Decimal)
                except (httpx.HTTPError, ValueError) as exc:
                    raise RDPBootstrapError(
                        "CLOB_BOOK_READ_FAILED", f"public CLOB book read failed for token {token.token_id}"
                    ) from exc
                metadata.append(_validate_book_metadata(body, market, token.token_id))

        receipt_ts = now()
        if receipt_ts >= end_epoch:
            raise RDPBootstrapError("MARKET_INTERVAL_ELAPSED", "selected BTC5m interval ended during metadata reads")
        result = RDPBootstrap(market, epoch, end_epoch,
                              (metadata[0], metadata[1]), receipt_ts)
        log.info(
            "rdp_bootstrap_result", result="SUCCESS", category="BOOTSTRAP_COMPLETE",
            slug=slug, condition_id=market.condition_id, epoch=epoch, end_epoch=end_epoch,
            outcome_tokens=[{
                "outcome": item.outcome, "token_id": item.token_id,
                "tick_size": item.tick_size, "min_order_size": item.min_order_size,
            } for item in result.token_metadata],
            receipt_ts=receipt_ts, real_money_trading_performed=False,
        )
        return result
    except RDPBootstrapError as exc:
        log.error("rdp_bootstrap_result", result="FAILURE", category=exc.category,
                  slug=slug, error=str(exc), real_money_trading_performed=False)
        raise
    except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
        error = RDPBootstrapError("BOOTSTRAP_INTERNAL_OR_HTTP_FAILURE",
                                  f"bootstrap failed: {type(exc).__name__}")
        log.error("rdp_bootstrap_result", result="FAILURE", category=error.category,
                  slug=slug, error=str(error), real_money_trading_performed=False)
        raise error from exc


async def preflight_rdp_market(
    cfg: Config,
    slug: str,
    *,
    now: Callable[[], float] = time.time,
    feed_timeout_s: float = 15.0,
    account_check: bool = False,
) -> RDPPreflight:
    """Compose exact HTTP metadata bootstrap with bounded public WS readiness."""
    from polymaker.rdp_marketdata import RDPFeedError, await_rdp_feed

    # For the optional authenticated path, freeze all configured authorities
    # before public bootstrap/feed awaits so later caller mutation cannot steer
    # the account-read client to a different endpoint or identity.
    runtime_cfg = cfg.model_copy(deep=True) if account_check else cfg

    bootstrap = await bootstrap_rdp_market(runtime_cfg, slug, now=now)
    try:
        feed = await await_rdp_feed(bootstrap, timeout_s=feed_timeout_s, now=now)
        log.info(
            "rdp_feed_readiness", result="SUCCESS", category="TWO_BOOKS_CURRENT",
            condition_id=feed.condition_id, connection_generation=feed.connection_generation,
            tokens=[{
                "outcome": item.outcome, "token_id": item.token_id,
                "best_bid": item.view.best_bid, "best_ask": item.view.best_ask,
                "tick_size": item.tick_size, "tick_source": item.tick_source,
            } for item in feed.tokens],
            receipt_ts=feed.receipt_ts, real_money_trading_performed=False,
        )
        account = None
        account_backing = None
        user_channel = None
        if account_check:
            from polymaker.rdp_userstream import observe_rdp_account_and_user_channel
            from polymaker.risk.rdp_capital import evaluate_initial_rdp_backing

            try:
                observation = await observe_rdp_account_and_user_channel(
                    runtime_cfg, bootstrap, now=now,
                )
                account = observation.account
                user_channel = observation.user_channel
                log.info(
                    "rdp_user_channel_readiness", result="SUCCESS",
                    category="CURRENT_GENERATION_APPLICATION_PONG_AND_ACCOUNT_READS",
                    condition_id=user_channel.condition_id,
                    subscription_frame_sent=user_channel.subscription_frame_sent,
                    connection_generation=user_channel.connection_generation,
                    application_pong_received=user_channel.application_pong_received,
                    heartbeat_receipt_ts=user_channel.heartbeat_receipt_ts,
                    capture_ts=user_channel.capture_ts,
                    explicit_server_authentication_acknowledgment=False,
                    real_money_trading_performed=False,
                )
                log.info(
                    "rdp_account_readiness", result="SUCCESS", category="ACCOUNT_SNAPSHOT_COMPLETE",
                    signer=account.signer, funder=account.funder,
                    balance_usd=str(account.balance_usd), allowance_usd=str(account.allowance_usd),
                    required_spenders=list(account.required_spenders),
                    selected_positions=[{
                        "outcome": item.outcome, "token_id": item.token_id,
                        "quantity": str(item.quantity), "average_price": str(item.average_price),
                    } for item in account.selected_positions],
                    selected_open_order_ids=[item.order_id for item in account.selected_open_orders],
                    total_open_order_count=account.total_open_order_count,
                    receipt_times={
                        "orders": account.orders_receipt_ts,
                        "positions": account.positions_receipt_ts,
                        "collateral": account.collateral_receipt_ts,
                    }, real_money_trading_performed=False,
                )
                account_backing = evaluate_initial_rdp_backing(
                    bootstrap, account, now=now(),
                    max_age_s=runtime_cfg.risk.user_ws_blind_halt_s,
                )
                log.info(
                    "rdp_initial_capital_backing", result="SUCCESS",
                    eligible=account_backing.eligible, reason=account_backing.reason,
                    capital_usd=str(account_backing.capital),
                    account_buy_commitment_usd=(str(account_backing.account_buy_commitment)
                                                if account_backing.account_buy_commitment is not None
                                                else None),
                    observed_cash_backing_usd=(str(account_backing.observed_cash_backing)
                                              if account_backing.observed_cash_backing is not None
                                              else None),
                    observed_allowance_backing_usd=(
                        str(account_backing.observed_allowance_backing)
                        if account_backing.observed_allowance_backing is not None else None),
                    read_age_s=account_backing.read_age_s,
                    selected_state_empty=account_backing.selected_state_empty,
                    real_money_trading_performed=False,
                )
            except RDPBootstrapError as exc:
                log.error(
                    "rdp_user_channel_readiness", result="FAILURE", category=exc.category,
                    condition_id=bootstrap.market.condition_id,
                    subscription_frame_sent=getattr(exc, "subscription_frame_sent", False),
                    connection_generation=getattr(exc, "connection_generation", None),
                    application_pong_received=getattr(exc, "application_pong_received", False),
                    heartbeat_receipt_ts=getattr(exc, "heartbeat_receipt_ts", None),
                    capture_ts=None,
                    real_money_trading_performed=False,
                )
                log.error("rdp_account_readiness", result="FAILURE", category=exc.category,
                          slug=slug, error=str(exc), real_money_trading_performed=False)
                raise
        return RDPPreflight(bootstrap, feed, account, account_backing, user_channel)
    except RDPFeedError as exc:
        log.error("rdp_feed_readiness", result="FAILURE", category=exc.category,
                  slug=slug, condition_id=bootstrap.market.condition_id, error=str(exc),
                  real_money_trading_performed=False)
        raise
