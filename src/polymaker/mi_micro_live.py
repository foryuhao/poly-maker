"""One BTC5m MI maker order lifecycle. No rollover or implicit live authority."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any

from polymaker.btc5m import PERIOD_S, current_epoch, resolve_btc5m_market, slug_for_epoch
from polymaker.config import Config
from polymaker.domain import MarketMeta, OpenOrder, Quote, Side
from polymaker.execution.gateway import (
    ExecutionGateway,
    GatewayReadError,
    OrderNotFound,
    OrderReadMismatch,
    normalize_signing_price,
)
from polymaker.live_authorization import (
    LiveAuthorizationResult,
    LiveExecutionIdentity,
    authorize_live_write,
    live_authorization_request,
    validate_live_identity,
)
from polymaker.logging import get_logger
from polymaker.marketdata.service import MarketDataService
from polymaker.mi_cash_reservation import (
    MI_REQUIRED_CAPITAL_MICRO,
    MICashReservation,
    MICashReservationBook,
    ReservationState,
    read_confirmed_spendable_collateral,
)
from polymaker.mi_live import build_snapshot, validate_market, validate_official_hosts
from polymaker.mi_readonly_account import OfficialReadOnlyAccount
from polymaker.strategy.maker_inventory import (
    Action,
    MakerInventoryConfig,
    MakerInventoryDecision,
    MakerInventorySnapshot,
    Outcome,
    evaluate,
)
from polymaker.userstream.parse import normalize_order

log = get_logger("mi.micro_live")


class MicroLiveError(RuntimeError):
    """Safety evidence is insufficient; stop further writes."""


class MicroMode(str, Enum):
    MICRO_LIVE = "MICRO_LIVE"


@dataclass(frozen=True, slots=True)
class MicroConfig:
    min_start_remaining_seconds: int = 180
    final_no_buy_seconds: int = 15
    clip_size: int = 5
    max_abs_target_inventory: int = 5
    required_capital_usd: int = 25
    max_working_orders: int = 1
    max_filled_clips: int = 1
    observation_seconds: int = 30
    poll_seconds: float = 1.0

    def __post_init__(self) -> None:
        if (self.min_start_remaining_seconds != 180 or self.final_no_buy_seconds != 15
                or self.clip_size != 5 or self.max_abs_target_inventory != 5
                or self.required_capital_usd != 25 or self.max_working_orders != 1
                or self.max_filled_clips != 1 or self.observation_seconds <= 0
                or self.poll_seconds <= 0):
            raise ValueError("M3 limits are fixed")


LIMITS = MicroConfig()
STRATEGY = MakerInventoryConfig(max_abs_inventory=Decimal(5))


def selected_epoch(now: float, limits: MicroConfig = LIMITS) -> int:
    epoch = current_epoch(now)
    return epoch if epoch + PERIOD_S - now >= limits.min_start_remaining_seconds else epoch + PERIOD_S


def authorize(config: Config, gateway: ExecutionGateway, armed: bool) -> LiveAuthorizationResult:
    result = authorize_live_write(live_authorization_request(
        config, gateway, explicit_write_mode=True, armed=armed,
    ))
    if not result.authorized or result.identity is None:
        raise MicroLiveError(f"GENERIC_GATE_{result.reason}")
    return result


def current_identity(config: Config, gateway: ExecutionGateway) -> LiveExecutionIdentity:
    result = validate_live_identity(live_authorization_request(
        config, gateway, explicit_write_mode=False, armed=False,
    ))
    if not result.authorized or result.identity is None:
        raise MicroLiveError(f"IDENTITY_{result.reason}")
    return result.identity


def require_reservation(
    reservation: MICashReservation | None, identity: LiveExecutionIdentity,
    condition_id: str, limits: MicroConfig = LIMITS,
) -> None:
    if (reservation is None or reservation.state is not ReservationState.ACTIVE
            or reservation.identity != identity
            or reservation.condition_id != condition_id.strip().lower()
            or reservation.amount_micro != limits.required_capital_usd * 1_000_000
            or reservation.amount_micro != MI_REQUIRED_CAPITAL_MICRO):
        raise MicroLiveError("ACTIVE_BOUND_RESERVATION_REQUIRED")


def candidate_quote(
    decision: MakerInventoryDecision, snapshot: MakerInventorySnapshot,
    meta: MarketMeta, reservation: MICashReservation, identity: LiveExecutionIdentity,
    *, now: float, epoch: int, working_orders: int = 0,
    filled_clips: int = 0, limits: MicroConfig = LIMITS,
) -> Quote:
    require_reservation(reservation, identity, meta.condition_id, limits)
    if working_orders != 0 or filled_clips != 0:
        raise MicroLiveError("ONE_ORDER_ONE_FILL_LIMIT")
    if now < epoch or now >= epoch + PERIOD_S - limits.final_no_buy_seconds:
        raise MicroLiveError("MARKET_CUTOFF")
    if not snapshot.data_valid:
        raise MicroLiveError("BOOK_STALE")
    if decision.action not in (Action.PLACE_BUY_YES, Action.PLACE_BUY_NO) or decision.side is not Side.BUY:
        raise MicroLiveError("NO_VALID_MI_CANDIDATE")
    if decision.outcome not in (Outcome.YES, Outcome.NO) or decision.price is None or decision.qty is None:
        raise MicroLiveError("CANDIDATE_FIELDS_INVALID")
    if ((decision.action is Action.PLACE_BUY_YES and decision.outcome is not Outcome.YES)
            or (decision.action is Action.PLACE_BUY_NO and decision.outcome is not Outcome.NO)):
        raise MicroLiveError("CANDIDATE_ACTION_OUTCOME_MISMATCH")
    if decision.qty != limits.clip_size or decision.n_target is None or abs(decision.n_target) > limits.max_abs_target_inventory:
        raise MicroLiveError("M3_TARGET_OR_CLIP_INVALID")
    price = decision.price
    tick = Decimal(str(snapshot.tick_size))
    if not (price.is_finite() and Decimal(0) < price < Decimal(1)
            and tick.is_finite() and tick > 0 and price % tick == 0):
        raise MicroLiveError("PRICE_OR_COMMITMENT_INVALID")
    try:
        signed_price = normalize_signing_price(float(price), meta.tick_size)
    except (TypeError, ValueError, OverflowError) as exc:
        raise MicroLiveError("SIGNING_PRICE_OR_TICK_INVALID") from exc
    if Decimal(str(signed_price)) * decision.qty > Decimal(reservation.amount_micro) / 1_000_000:
        raise MicroLiveError("PRICE_OR_COMMITMENT_INVALID")
    token = meta.yes if decision.outcome is Outcome.YES else meta.no
    book = snapshot.yes if decision.outcome is Outcome.YES else snapshot.no
    if (not token.token_id or book.best_bid is None or book.best_ask is None
            or price != Decimal(str(book.best_bid))
            or Decimal(str(signed_price)) >= Decimal(str(book.best_ask))):
        raise MicroLiveError("MAKER_OR_TOKEN_INVALID")
    return Quote(token.token_id, Side.BUY, signed_price, limits.clip_size)


def market_feed_ready(md: Any, meta: MarketMeta) -> bool:
    """FavoriteFirst-equivalent market-feed readiness for the current market.

    This reuses the same live-verified primitives the FavoriteFirst real-money
    path gates on; it does not introduce a new MI readiness model:

    * ``MarketDataService.books_fresh(meta)`` — the precise gate the
      FavoriteFirst engine uses as ``_market_books_fresh``/preflight
      ``market_ws_ready``. It already requires the WS to be connected and each
      side's snapshot to come from the *current* connection generation.
    * ``metrics["queue_full"] == 0`` — the ingest-backlog condition the
      FavoriteFirst preflight adds on top of ``books_fresh``.

    Subscription is not readiness: a book that exists but is not from the
    current connection generation (or whose local queue overflowed) is not
    ready, so MI must not evaluate an actionable candidate yet.
    """
    if getattr(md, "connected", True) is not True:
        return False
    metrics = getattr(md, "metrics", None)
    if isinstance(metrics, dict) and metrics.get("queue_full", 0) != 0:
        return False
    return bool(md.books_fresh(meta))


def _readiness_timeout_s() -> float:
    """Reuse the FavoriteFirst live preflight timeout for the MI readiness wait."""
    from polymaker.favorite_live import PREFLIGHT_TIMEOUT_S

    return float(PREFLIGHT_TIMEOUT_S)


def order_evidence(row: dict[str, Any], order: OpenOrder) -> tuple[Decimal, bool]:
    ids = [row[key] for key in ("id", "orderID", "order_id") if key in row]
    if not ids or any(not isinstance(value, str) or value.strip() != order.order_id for value in ids):
        raise MicroLiveError("ORDER_STATE_UNKNOWN")
    event = normalize_order({"id": row.get("id", row.get("orderID", row.get("order_id"))), **row})
    if (event is None or event.order_id != order.order_id or event.token_id != order.token_id
            or event.side is not Side.BUY or Decimal(str(event.original_size)) != Decimal(5)
            or Decimal(str(event.price)) != Decimal(str(order.price))):
        raise MicroLiveError("ORDER_STATE_UNKNOWN")
    matched = Decimal(str(event.size_matched))
    return matched, event.is_cancel


async def reconcile(
    gateway: ExecutionGateway, order: OpenOrder | None, matched: Decimal,
    meta: MarketMeta,
) -> dict[str, Any]:
    orders = await gateway.open_orders()
    if not isinstance(orders, list) or orders:
        raise MicroLiveError("OPEN_COMMITMENT_UNRESOLVED")
    raw_cash = await gateway.balance_allowance()
    if not isinstance(raw_cash, dict) or "balance" not in raw_cash:
        raise MicroLiveError("COLLATERAL_RECONCILIATION_UNKNOWN")
    try:
        cash_micro = int(raw_cash["balance"])
    except (ValueError, TypeError) as exc:
        raise MicroLiveError("COLLATERAL_RECONCILIATION_UNKNOWN") from exc
    if cash_micro < 0:
        raise MicroLiveError("COLLATERAL_RECONCILIATION_UNKNOWN")
    positions = await gateway.positions()
    if not isinstance(positions, dict):
        raise MicroLiveError("POSITION_RECONCILIATION_UNKNOWN")
    if order is not None:
        actual = Decimal(str(positions.get(order.token_id, (0, 0))[0]))
        if actual < matched:
            raise MicroLiveError("POSITION_FILL_MISMATCH")
    return {"open_orders": len(orders), "collateral_micro": cash_micro,
            "yes_shares": positions.get(meta.yes.token_id, (0, 0))[0],
            "no_shares": positions.get(meta.no.token_id, (0, 0))[0]}


async def prove_terminal_from_account_truth(
    gateway: ExecutionGateway, order: OpenOrder, limits: MicroConfig = LIMITS,
) -> tuple[str, Decimal]:
    """Prove the order left no open commitment and no fill, from official reads.

    Used only when the primary ``get_order`` read cannot yield a usable row
    (absent/not-found or differently shaped). It never guesses: every official
    read must succeed, and the order must be provably terminal. A single
    unresolved or conflicting read raises ``MicroLiveError`` so the caller
    fails closed and retains the reservation. Returns ``(state, matched)``.
    """
    orders = await gateway.open_orders()
    if not isinstance(orders, list) or orders:
        raise MicroLiveError("OPEN_COMMITMENT_UNRESOLVED")
    trades = await gateway.get_trades(asset_id=order.token_id)
    if not isinstance(trades, list):
        raise MicroLiveError("FILL_READ_UNKNOWN")
    positions = await gateway.positions()
    if not isinstance(positions, dict):
        raise MicroLiveError("POSITION_RECONCILIATION_UNKNOWN")
    pos = Decimal(str(positions.get(order.token_id, (0, 0))[0]))
    clip = Decimal(limits.clip_size)
    if not pos.is_finite() or pos < 0:
        raise MicroLiveError("POSITION_RECONCILIATION_UNKNOWN")
    if pos == 0:
        if trades:
            # A fill record with a flat position is contradictory evidence; do
            # not classify it as "no fill".
            raise MicroLiveError("FILL_EVIDENCE_UNRECONCILED")
        return "NOT_OPEN_UNFILLED", Decimal(0)
    if pos <= clip:
        return ("FILLED" if pos == clip else "PARTIAL"), pos
    raise MicroLiveError("FILL_EVIDENCE_UNRECONCILED")


async def settle_order(
    gateway: ExecutionGateway, order: OpenOrder, meta: MarketMeta,
    *, deadline: float, now: Callable[[], float] = time.time,
    sleep: Callable[[float], Any] = asyncio.sleep, limits: MicroConfig = LIMITS,
) -> tuple[str, Decimal, dict[str, Any]]:
    """Observe one order; require official terminal evidence plus no open commitment.

    The primary ``get_order`` read is one evidence source, not a permanent
    authoritative lookup. If the venue reports the order as absent/not-found,
    the order state is proven from official account truth (open orders + fills
    + positions) instead of blocking forever — but only when that proof is
    complete. A malformed/different read is never reconciled: it fails closed.
    """
    matched = Decimal(0)
    primary_absent = False
    while now() < deadline:
        try:
            row = await gateway.get_order(order.order_id)
        except OrderReadMismatch:
            raise  # response was a different/unverifiable order: fail closed
        except OrderNotFound:
            primary_absent = True
            break  # definitive absence; try reconcile from account truth
        except GatewayReadError:
            # Malformed/transient primary read is never release evidence;
            # retry inside the observation window, then fail closed.
            await sleep(min(limits.poll_seconds, max(0, deadline - now())))
            continue
        matched, cancelled = order_evidence(row, order)
        if str(row.get("status", "")).upper() == "REJECTED" and matched == 0:
            return "REJECTED", matched, await reconcile(gateway, order, matched, meta)
        if matched == limits.clip_size:
            return "FILLED", matched, await reconcile(gateway, order, matched, meta)
        if cancelled:
            return ("PARTIAL" if matched else "CANCELLED"), matched, await reconcile(gateway, order, matched, meta)
        if matched > 0:
            break  # A partial fill ends observation; cancel its remainder now.
        await sleep(min(limits.poll_seconds, max(0, deadline - now())))
    # Request cancellation, then require terminal proof. A cancel ACK alone
    # cannot prove terminal state, and neither can primary-read absence alone.
    if not await gateway.cancel([order.order_id]):
        # No cancel ACK is not release evidence, but it is not authoritative
        # either: terminal truth is still proven below from open orders/fills.
        log.warning("order_cancel_not_acked", order_id_prefix=order.order_id[:12])
    saw_live_row = False
    primary_malformed = False
    for _ in range(3):
        try:
            row = await gateway.get_order(order.order_id)
        except OrderReadMismatch:
            raise
        except OrderNotFound:
            row = None
            primary_absent = True
        except GatewayReadError:
            row = None
            primary_malformed = True
        if row is not None:
            matched, cancelled = order_evidence(row, order)
            if str(row.get("status", "")).upper() == "REJECTED" and matched == 0:
                return "REJECTED", matched, await reconcile(gateway, order, matched, meta)
            if matched == limits.clip_size:
                return "FILLED", matched, await reconcile(gateway, order, matched, meta)
            if cancelled:
                return ("PARTIAL" if matched else "CANCELLED"), matched, await reconcile(gateway, order, matched, meta)
            saw_live_row = True
        await sleep(limits.poll_seconds)
    if saw_live_row:
        # The order was still readable and non-terminal; cancel is unconfirmed.
        raise MicroLiveError("MANUAL ATTENTION REQUIRED: CANCEL_UNCONFIRMED")
    if not primary_absent or primary_malformed:
        # The primary read was malformed/unavailable rather than a definitive
        # absence; there is no reliable trigger to reconcile from, so fail
        # closed and retain the reservation.
        raise MicroLiveError("MANUAL ATTENTION REQUIRED: ORDER_STATE_UNKNOWN")
    # The venue reported the order absent: prove terminal truth from official
    # account state. Uncertainty here raises and the reservation is retained.
    log.info("order_read_not_found", order_id_prefix=order.order_id[:12],
             action="account_reconciliation_started")
    state, matched = await prove_terminal_from_account_truth(gateway, order, limits)
    return state, matched, await reconcile(gateway, order, matched, meta)


async def run_market(
    config: Config, gateway: ExecutionGateway, account: OfficialReadOnlyAccount,
    meta: MarketMeta, epoch: int, *, mode: MicroMode, arm: Callable[[], bool],
    limits: MicroConfig = LIMITS, emit: Callable[[str], None] = print,
) -> str:
    """One selected market; a POST can occur at most once."""
    if mode is not MicroMode.MICRO_LIVE or getattr(gateway, "_paper", None) is not False:
        raise MicroLiveError("EXPLICIT_MICRO_LIVE_MODE_REQUIRED")
    validate_market(meta, epoch)
    if epoch + PERIOD_S - time.time() < limits.min_start_remaining_seconds:
        raise MicroLiveError("STARTUP_MARKET_TOO_OLD")
    identity = current_identity(config, gateway)
    # Identity proof before reservation; write authorization is repeated at submit.
    preliminary = LiveAuthorizationResult(True, "IDENTITY_VALID", identity)
    evidence = await read_confirmed_spendable_collateral(config, gateway, preliminary)
    book = MICashReservationBook()
    reservation = book.reserve(evidence, meta.condition_id, current_identity=identity)
    require_reservation(reservation, identity, meta.condition_id, limits)
    emit(f"MI MICRO-LIVE mode={MicroMode.MICRO_LIVE.value} condition_id={meta.condition_id} "
         f"clip=5 target_cap=±5 max_working=1 max_filled_clips=1 reserved=$25 "
         f"reservation_id={reservation.reservation_id}")
    dirty = asyncio.Event()
    md = MarketDataService(on_dirty=lambda cid, _token: dirty.set() if cid == meta.condition_id else None,
                           on_connection=lambda _up: dirty.set(), proxy=config.proxy)
    md.set_markets([(meta.condition_id, [meta.yes.token_id, meta.no.token_id])])
    task = asyncio.create_task(md.run(), name="mi-micro-market-ws")
    order: OpenOrder | None = None
    safe_to_release = False
    result = "UNKNOWN"
    try:
        cutoff = epoch + PERIOD_S - limits.final_no_buy_seconds
        # FavoriteFirst waits for feed/connectivity readiness with a bounded
        # live-preflight timeout before it will write. MI reuses that same
        # timeout (never a fixed warmup sleep) and fails closed if readiness
        # cannot be established before the M3 cutoff.
        readiness_deadline = min(cutoff, time.time() + _readiness_timeout_s())
        ready = False
        announced_waiting = False
        saw_stale = False
        while time.time() < cutoff:
            try:
                await asyncio.wait_for(dirty.wait(), timeout=min(limits.poll_seconds, max(0.001, cutoff - time.time())))
                dirty.clear()
            except TimeoutError:
                pass
            # FL-equivalent startup/connectivity gate: subscription is not
            # readiness. Do not evaluate an actionable candidate until the
            # current market's books are fresh on the current WS connection and
            # the ingest queue is clear. Ingest/state updates continue while we
            # wait; only real candidate->place is withheld.
            if not market_feed_ready(md, meta):
                if ready or not announced_waiting:
                    if ready:
                        saw_stale = True
                    emit(f"waiting_for_feed_readiness condition_id={meta.condition_id}")
                    announced_waiting = True
                ready = False
                if time.time() >= readiness_deadline:
                    result = "STALE_CONNECTIVITY" if saw_stale else "FEED_READINESS_TIMEOUT"
                    break
                continue
            if not ready:
                ready = True
                announced_waiting = False
                emit(f"feed_ready condition_id={meta.condition_id} "
                     f"connectivity_ready=1 book_fresh=1")
            account_state = await account.snapshot(meta)
            if account_state.open_order_tokens or any(
                account_state.positions.get(t, Decimal(0)) != 0
                for t in (meta.yes.token_id, meta.no.token_id)
            ):
                raise MicroLiveError("ACCOUNT_READINESS_CHANGED")
            now = time.time()
            snapshot = build_snapshot(meta, md, account_state, epoch, now)
            decision = evaluate(snapshot, config=STRATEGY)
            if decision.action not in (Action.PLACE_BUY_YES, Action.PLACE_BUY_NO):
                continue
            quote = candidate_quote(decision, snapshot, meta, reservation, identity, now=now, epoch=epoch,
                                    limits=limits)
            assert decision.outcome is not None
            emit(f"CANDIDATE condition_id={meta.condition_id} outcome={decision.outcome.value} "
                 f"BUY qty={limits.clip_size} maker_price={quote.price} "
                 f"reservation_id={reservation.reservation_id}")
            if arm() is not True:
                result = "NOT_ARMED"
                break
            authorize(config, gateway, armed=True)
            if current_identity(config, gateway) != identity:
                raise MicroLiveError("IDENTITY_CHANGED")
            require_reservation(reservation, identity, meta.condition_id, limits)
            if not book.is_active(reservation):
                raise MicroLiveError("RESERVATION_RELEASED")
            submit_cash = await read_confirmed_spendable_collateral(
                config, gateway, preliminary,
            )
            if book.effective_available_micro(submit_cash) < 0:
                raise MicroLiveError("CASH_BELOW_ACTIVE_RESERVATIONS")
            if time.time() >= cutoff:
                continue
            if not market_feed_ready(md, meta):
                # Connectivity/freshness was lost after the candidate was
                # built. Never reuse the stale candidate: re-establish
                # readiness and re-evaluate from the latest state.
                ready = False
                saw_stale = True
                continue
            assert decision.price is not None

            def quote_still_maker(token_id: str = quote.token_id,
                                  raw_price: Decimal = decision.price,
                                  signed_price: float = quote.price) -> bool:
                book_now = md.book(token_id)
                if book_now is None:
                    return False
                bid, ask = book_now.best_bid(), book_now.best_ask()
                return (bid is not None and ask is not None
                        and Decimal(str(bid.price)) == raw_price
                        and Decimal(str(ask.price)) > Decimal(str(signed_price))
                        and normalize_signing_price(signed_price, meta.tick_size) == signed_price)

            guard_aborted = False

            def submit_guard() -> bool:
                nonlocal guard_aborted
                try:
                    ok = (authorize(config, gateway, armed=True).identity == identity
                          and current_identity(config, gateway) == identity
                          and time.time() < cutoff and market_feed_ready(md, meta)
                          and quote_still_maker() and book.is_active(reservation))
                except (MicroLiveError, ValueError, TypeError, OverflowError):
                    ok = False
                if not ok:
                    guard_aborted = True
                return ok

            placed = await gateway.place([quote], meta, submit_guard=submit_guard,
                                         force_post_only=True, strict_placement=True)
            if guard_aborted and not placed:
                # The final FL-equivalent connectivity/freshness guard aborted
                # before any POST (zero exposure). Re-establish readiness and
                # re-evaluate; a real POST is never retried.
                ready = False
                saw_stale = True
                continue
            if len(placed) != 1 or not placed[0].order_id or placed[0].token_id != quote.token_id:
                raise MicroLiveError("OFFICIAL_ORDER_ID_REQUIRED")
            order = placed[0]
            emit(f"PLACED condition_id={meta.condition_id} outcome={decision.outcome} BUY qty=5 price={quote.price} official_order_id={order.order_id} reservation_id={reservation.reservation_id}")
            result, matched, summary = await settle_order(
                gateway, order, meta, deadline=min(time.time() + limits.observation_seconds, cutoff),
                limits=limits,
            )
            emit(f"RECONCILIATION {summary} matched={matched} result={result}")
            safe_to_release = True
            break
        else:
            result = "STALE_CONNECTIVITY" if saw_stale else "NO_VALID_MI_CANDIDATE"
        if order is None and result in ("NO_VALID_MI_CANDIDATE", "NOT_ARMED",
                                        "STALE_CONNECTIVITY", "FEED_READINESS_TIMEOUT"):
            summary = await reconcile(gateway, None, Decimal(0), meta)
            emit(f"RECONCILIATION {summary} result={result}")
            safe_to_release = True
    except BaseException as exc:
        cancel_uncertain = isinstance(exc, MicroLiveError) and "CANCEL_" in str(exc)
        if order is not None and not safe_to_release and not cancel_uncertain:
            try:
                await gateway.cancel([order.order_id])
                row = None
                primary_absent = False
                try:
                    row = await gateway.get_order(order.order_id)
                except OrderNotFound:
                    primary_absent = True
                except GatewayReadError:
                    primary_absent = False
                if row is not None:
                    matched, cancelled = order_evidence(row, order)
                    if cancelled or matched == limits.clip_size:
                        summary = await reconcile(gateway, order, matched, meta)
                        emit(f"RECONCILIATION {summary} matched={matched}")
                        safe_to_release = True
                elif primary_absent:
                    # The venue reported the order absent; prove terminal truth
                    # from official account state, else fail closed.
                    state, matched = await prove_terminal_from_account_truth(gateway, order, limits)
                    summary = await reconcile(gateway, order, matched, meta)
                    emit(f"RECONCILIATION {summary} matched={matched} result={state}")
                    safe_to_release = True
            except Exception:
                pass
        if not safe_to_release:
            emit(f"MANUAL ATTENTION REQUIRED reservation_id={reservation.reservation_id}")
        raise
    finally:
        md.stop()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if safe_to_release:
            book.release(reservation.reservation_id, current_identity=current_identity(config, gateway),
                         condition_id=meta.condition_id)
            emit(f"RESERVATION RELEASED reservation_id={reservation.reservation_id}")
    return result


async def run_micro_live(config_dir: str, *, arm: Callable[[], bool],
                         emit: Callable[[str], None] = print) -> str:
    cfg = Config.load(config_dir)
    validate_official_hosts(cfg)
    gateway = ExecutionGateway(cfg, paper=False)
    account = OfficialReadOnlyAccount(cfg)
    try:
        await gateway.connect()
        await account.connect()
        epoch = selected_epoch(time.time())
        if epoch > current_epoch(time.time()):
            emit("SKIP current BTC5m market: remaining < 180s; waiting for next")
        await asyncio.sleep(max(0, epoch - time.time()))
        while True:
            if time.time() >= epoch + PERIOD_S - LIMITS.min_start_remaining_seconds:
                raise MicroLiveError("SELECTED_MARKET_EXPIRED")
            try:
                meta = await resolve_btc5m_market(cfg, slug_for_epoch(epoch), cache=False,
                                                 strict_metadata=True)
                break
            except LookupError:
                await asyncio.sleep(1.5)
        return await run_market(cfg, gateway, account, meta, epoch,
                                mode=MicroMode.MICRO_LIVE, arm=arm, emit=emit)
    finally:
        gateway.close()
