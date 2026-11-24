"""BTC 5-minute Polymarket market selection and paper session lifecycle."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from polymaker.catalog.gamma import GammaClient, parse_market
from polymaker.catalog.store import CatalogStore
from polymaker.config import Config, MarketEntry
from polymaker.domain import MarketMeta
from polymaker.logging import get_logger

log = get_logger("btc5m")
PERIOD_S = 300
PROFILE = "btc5m-survival"
RESOLUTION_RETRY_S = 1.5


def current_epoch(now: float | None = None) -> int:
    """UTC Unix timestamp of the current 5-minute market's start."""
    ts = time.time() if now is None else now
    return int(ts // PERIOD_S) * PERIOD_S


def next_epoch(now: float | None = None) -> int:
    """UTC Unix timestamp of the next 5-minute boundary (strictly after now)."""
    return current_epoch(now) + PERIOD_S


def slug_for_epoch(epoch: int) -> str:
    return f"btc-updown-5m-{int(epoch)}"


def seconds_to_boundary(now: float | None = None) -> float:
    ts = time.time() if now is None else now
    return (current_epoch(ts) + PERIOD_S) - ts


def choose_epoch(now: float, shutdown_buffer_s: float = 15.0) -> int:
    """Choose current interval unless too close to its end, then the next."""
    epoch = current_epoch(now)
    return epoch + PERIOD_S if epoch + PERIOD_S - now <= shutdown_buffer_s else epoch


async def resolve_btc5m_market(cfg: Config, slug: str) -> MarketMeta:
    """Resolve an exact BTC5m slug through Gamma and cache its parsed metadata."""
    async with GammaClient(cfg.wallet.gamma_host) as gamma:
        raw = await gamma.market_by_slug(slug)
    if raw is None:
        raise LookupError(f"Gamma has no active market for {slug}")
    meta = parse_market(raw)
    if meta is None or meta.slug != slug:
        raise LookupError(f"Gamma market {slug} is unavailable or unusable")
    store = CatalogStore(cfg.paths.db)
    try:
        store.upsert_market(meta)
    finally:
        store.close()
    return meta


async def run_btc5m_paper(
    config_dir: str,
    *,
    once: bool = False,
    shutdown_buffer_s: float = 15.0,
    now: Callable[[], float] = time.time,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    config_loader: Callable[[str], Config] = Config.load,
    resolver: Callable[[Config, str], Awaitable[MarketMeta]] = resolve_btc5m_market,
    engine_factory: Callable[..., Any] | None = None,
) -> None:
    """Run one paper Engine per market, closing it before its interval ends."""
    from polymaker.engine import Engine

    factory = engine_factory or Engine
    while True:
        selected_at = now()
        epoch = choose_epoch(selected_at, shutdown_buffer_s)
        if epoch != current_epoch(selected_at):
            wait_s = epoch - selected_at
            log.info("btc5m_wait_next_boundary", seconds=wait_s)
            await sleep(wait_s)
            continue

        slug = slug_for_epoch(epoch)
        cfg = config_loader(config_dir)
        end_epoch = epoch + PERIOD_S
        meta: MarketMeta | None = None
        while meta is None:
            remaining = end_epoch - now()
            if remaining <= shutdown_buffer_s:
                log.info("btc5m_wait_next_boundary", slug=slug,
                         seconds=max(0.0, remaining))
                await sleep(max(0.0, remaining))
                break
            try:
                meta = await resolver(cfg, slug)
            except LookupError as exc:
                log.warning("btc5m_market_unavailable", slug=slug, err=str(exc),
                            seconds_remaining=remaining)
            if meta is None:
                retry_wait = min(RESOLUTION_RETRY_S,
                                 max(0.0, end_epoch - now() - shutdown_buffer_s))
                if retry_wait > 0.0:
                    await sleep(retry_wait)
        if meta is None:
            continue

        remaining = end_epoch - now()
        if remaining <= shutdown_buffer_s:
            log.info("btc5m_wait_next_boundary", slug=slug, seconds=max(0.0, remaining))
            await sleep(max(0.0, remaining))
            continue

        cfg.markets = [MarketEntry(slug=slug, profile=PROFILE, enabled=True)]
        end_iso = datetime.fromtimestamp(end_epoch, UTC).isoformat()
        log.info("btc5m_session_selected", slug=slug, epoch=epoch,
                 condition_id=meta.condition_id, end_time=end_iso,
                 seconds_remaining=remaining)
        engine = factory(cfg, paper=True)
        started = False
        try:
            await engine.start()
            started = True
            log.info("btc5m_session_started", slug=slug, condition_id=meta.condition_id)
            duration = max(0.0, end_epoch - now() - shutdown_buffer_s)
            await sleep(duration)
        finally:
            log.info("btc5m_session_stopping", slug=slug)
            await engine.shutdown()
            log.info("btc5m_session_stopped", slug=slug, started=started)

        if once:
            return
        remaining = seconds_to_boundary(now())
        log.info("btc5m_wait_next_boundary", seconds=remaining)
        await sleep(remaining)
