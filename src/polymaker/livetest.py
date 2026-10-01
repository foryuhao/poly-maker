"""Live wallet round-trip test against an exact, active BTC 5-minute market."""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from rich.console import Console

from polymaker.config import Config
from polymaker.domain import MarketMeta, Quote, Side
from polymaker.execution.gateway import ExecutionGateway

LIVETEST_MIN_REMAINING_S = 60.0
PRICE_MIN = 0.01
PRICE_MAX = 0.99


async def resolve_livetest_market(
    cfg: Config,
    *,
    now: Callable[[], float] = time.time,
    resolver: Callable[[Config, str], Awaitable[MarketMeta]] | None = None,
) -> MarketMeta:
    """Resolve the current exact BTC5m market, moving forward near expiry."""
    from polymaker.btc5m import PERIOD_S, current_epoch, resolve_btc5m_market, slug_for_epoch

    resolve = resolver or resolve_btc5m_market
    selected_now = now()
    epoch = current_epoch(selected_now)
    current = await resolve(cfg, slug_for_epoch(epoch))
    end_ts = _end_timestamp(current)
    if end_ts - now() < LIVETEST_MIN_REMAINING_S:
        epoch += PERIOD_S
        return await resolve(cfg, slug_for_epoch(epoch))
    return current


def _end_timestamp(meta: MarketMeta) -> float:
    if not meta.end_date_iso:
        raise ValueError("market has no expiry metadata")
    return datetime.fromisoformat(meta.end_date_iso.replace("Z", "+00:00")).astimezone(UTC).timestamp()


def _valid_market(meta: MarketMeta, now: float) -> tuple[bool, str]:
    if not meta.condition_id:
        return False, "missing condition ID"
    if not meta.yes.token_id or not meta.no.token_id:
        return False, "missing YES/NO token ID"
    if not math.isfinite(meta.tick_size) or meta.tick_size <= 0:
        return False, "invalid tick size"
    try:
        if _end_timestamp(meta) <= now:
            return False, "market is expired"
    except (ValueError, TypeError) as exc:
        return False, str(exc)
    return True, ""


def _deep_bid(best_bid: float, best_ask: float, tick: float) -> float | None:
    """Choose a deep, tick-aligned bid below the live ask and within bounds."""
    if not all(math.isfinite(v) for v in (best_bid, best_ask, tick)) or tick <= 0:
        return None
    if not PRICE_MIN <= best_ask <= PRICE_MAX or best_bid < 0 or best_bid >= best_ask:
        return None
    raw = max(2 * tick, best_bid - 0.10)
    # Floor to tick so rounding can never move the order closer to/cross the ask.
    price = math.floor((raw + 1e-12) / tick) * tick
    price = round(price, max(0, min(8, int(math.ceil(-math.log10(tick))) + 2)))
    if price < PRICE_MIN or price > PRICE_MAX or price >= best_ask:
        return None
    if not math.isclose(price / tick, round(price / tick), abs_tol=1e-8):
        return None
    return price


async def run_livetest(
    cfg: Config,
    console: Console,
    notional_usdc: float = 5.0,
    *,
    now: Callable[[], float] = time.time,
    resolver: Callable[[Config, str], Awaitable[MarketMeta]] | None = None,
    gateway_factory: Callable[[Config], ExecutionGateway] = ExecutionGateway,
) -> bool:
    if not cfg.secrets.has_wallet:
        console.print("[red]No wallet in .env. Set PK and BROWSER_ADDRESS first.[/red]")
        return False

    try:
        meta = await resolve_livetest_market(cfg, now=now, resolver=resolver)
        valid, reason = _valid_market(meta, now())
        if not valid:
            raise ValueError(reason)
    except Exception as exc:  # noqa: BLE001 - fail closed on every resolver/metadata error
        console.print(f"[red]✗ BTC5m market resolution/validation failed — no order submitted: {exc}[/red]")
        return False

    console.print(f"[bold]Live round-trip test[/bold] on: {meta.question[:60]}")
    gw = gateway_factory(cfg)
    try:
        await gw.connect()
        console.print(f"  [green]✓[/green] wallet auth — address {gw.address[:12]}…")
    except Exception as e:  # noqa: BLE001
        console.print(f"  [red]✗ wallet auth failed:[/red] {e}")
        console.print("  [yellow]Check the configured signature type and wallet address.[/yellow]")
        return False

    try:
        # Recheck expiry after connection, then require a live CLOB book for this token.
        valid, reason = _valid_market(meta, now())
        if not valid:
            raise ValueError(reason)
        book = await gw.get_book(meta.yes.token_id)
        if not book or "best_bid" not in book or "best_ask" not in book:
            raise ValueError("CLOB book lookup failed or returned malformed data")
        best_bid, best_ask = float(book["best_bid"]), float(book["best_ask"])
        if not math.isfinite(best_bid) or not math.isfinite(best_ask) or best_bid <= 0 or best_ask >= 1 or best_bid >= best_ask:
            raise ValueError("CLOB returned an empty or invalid token book")
        price = _deep_bid(best_bid, best_ask, meta.tick_size)
        if price is None:
            raise ValueError("no valid deep post-only price is available")
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]✗ selected BTC5m token is not currently tradeable — no order submitted: {exc}[/red]")
        return False

    ba = await gw.balance_allowance()
    console.print(f"  balance/allowance: {ba}")
    size = round(max(meta.min_order_size, notional_usdc / price), 2)
    console.print(f"  placing post-only BUY {size} @ {price} on YES token "
                  f"(~${price * size:.2f}, deep — will not fill)")

    placed = await gw.place([Quote(meta.yes.token_id, Side.BUY, price, size)], meta)
    if not placed:
        console.print("  [red]✗ order not placed (see logs for the API error)[/red]")
        return False
    oid = placed[0].order_id
    console.print(f"  [green]✓[/green] placed — order id {oid[:16]}…")

    await asyncio.sleep(2.0)
    live = await gw.open_orders()
    found = any(o.order_id == oid for o in live)
    console.print(f"  [{'green' if found else 'yellow'}]{'✓' if found else '?'}[/] "
                  f"read back open orders: {len(live)} live, ours {'present' if found else 'not seen yet'}")

    await gw.cancel([oid])
    console.print("  [green]✓[/green] cancel sent")
    await asyncio.sleep(1.5)
    after = await gw.open_orders()
    still = any(o.order_id == oid for o in after)
    console.print(f"  [{'green' if not still else 'red'}]{'✓' if not still else '✗'}[/] "
                  f"order {'cancelled' if not still else 'STILL LIVE — cancel manually!'}")
    ok = bool(placed) and not still
    console.print(f"\n[bold]{'ROUND-TRIP OK' if ok else 'CHECK LOGS'}[/bold]")
    return ok
