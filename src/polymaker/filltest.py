"""One-market BTC5m maker-only round-trip smoke test."""
from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable
from typing import Any

from polymaker.btc5m import PERIOD_S, current_epoch, resolve_btc5m_market, slug_for_epoch
from polymaker.config import Config
from polymaker.domain import Fill, MarketMeta, Quote, Side
from polymaker.execution.gateway import ExecutionGateway, PlacementUnknown
from polymaker.execution.filltruth import ReconciliationOutcome, reconcile_owned_order
from polymaker.logging import get_logger
from polymaker.marketdata.service import MarketDataService
from polymaker.state.store import StateStore
from polymaker.state.tracker import TradeEvent, UserEventProcessor
from polymaker.userstream.client import UserStream

log = get_logger("filltest")
OPEN_START, OPEN_END, CLOSE_END = 30.0, 210.0, 270.0
ATTEMPT_S, MAX_NOTIONAL = 15.0, 5.0
WS_STARTUP_TIMEOUT_S, BOOK_SNAPSHOT_TIMEOUT_S = 10.0, 3.0
POSITION_GRACE_DELAYS_S = (0.5, 1.0, 2.0, 3.0, 3.0)


def eligible_elapsed(elapsed: float) -> bool:
    return OPEN_START <= elapsed < OPEN_END


def attempt_wait_seconds(now: float, epoch: int, timeout: float = ATTEMPT_S,
                         hard_end: float = CLOSE_END) -> float:
    return max(0.0, min(timeout, epoch + hard_end - now))


def _tick_price(raw: float, tick: float, *, above: bool = False) -> float | None:
    if not math.isfinite(raw) or not math.isfinite(tick) or tick <= 0:
        return None
    units = (raw / tick)
    n = math.ceil(units - 1e-10) if above else math.floor(units + 1e-10)
    price = round(n * tick, max(0, min(8, int(math.ceil(-math.log10(tick))) + 2)))
    if price <= 0 or not math.isclose(price / tick, round(price / tick), abs_tol=1e-8):
        return None
    return price


def candidate_price(best_ask: float, tick: float, max_price: float | None = None) -> float | None:
    """Best ask minus a tick, rounded down to a valid tick; optional legacy cap."""
    if not math.isfinite(best_ask) or not math.isfinite(tick) or tick <= 0:
        return None
    price = _tick_price(best_ask - tick, tick)
    if price is None or price >= best_ask or (max_price is not None and price > max_price):
        return None
    return price


def choose_candidate(meta: MarketMeta, books: dict[str, dict[str, float]],
                     max_price: float | None = None) -> tuple[str, str, float, float] | None:
    candidates: list[tuple[str, str, float, float]] = []
    shares = max(5.0, meta.min_order_size)
    for token_meta in (meta.yes, meta.no):
        token, outcome = token_meta.token_id, token_meta.outcome.upper()
        book = books.get(token, {})
        ask = book.get("best_ask", float("nan"))
        price = candidate_price(ask, meta.tick_size, max_price)
        if price is not None and price * shares <= MAX_NOTIONAL + 1e-9:
            candidates.append((token, outcome, price, shares))
    return min(candidates, key=lambda c: (c[2] * c[3], c[1]), default=None)


def safe_remaining_qty(opening_test_qty: float, cumulative_sell_fill_qty: float,
                       authoritative_position: float, baseline_position: float) -> float:
    """Conservative inventory bound for a new SELL after a completed attempt."""
    if cumulative_sell_fill_qty > opening_test_qty + 1e-9:
        raise ValueError("attributed SELL fills exceed opening test inventory")
    remaining_by_fills = max(0.0, opening_test_qty - cumulative_sell_fill_qty)
    remaining_by_position = max(0.0, authoritative_position - baseline_position)
    return min(remaining_by_fills, remaining_by_position)


async def _run_filltest_market(
    cfg: Config, *, now: Callable[[], float] = time.time,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    resolver: Callable[[Config, str], Awaitable[MarketMeta]] = resolve_btc5m_market,
    gateway_factory: Callable[[Config], ExecutionGateway] = ExecutionGateway,
    market_factory: Callable[..., MarketDataService] = MarketDataService,
    stream_factory: Callable[..., UserStream] = UserStream,
    startup_timeout_s: float = WS_STARTUP_TIMEOUT_S,
    snapshot_timeout_s: float = BOOK_SNAPSHOT_TIMEOUT_S,
) -> bool:
    if not cfg.secrets.has_wallet:
        log.error("filltest_aborted", reason="wallet unavailable")
        return False
    epoch = current_epoch(now())
    if not eligible_elapsed(now() - epoch):
        log.info("filltest_no_order", result="OUTSIDE OPENING WINDOW", elapsed=now()-epoch)
        return False
    gw = gateway_factory(cfg)
    market = stream = None
    tasks: list[asyncio.Task[Any]] = []
    recovery_tasks: dict[str, asyncio.Task[ReconciliationOutcome]] = {}
    reported_fill_keys: set[tuple[str, str]] = set()
    current_order_id: str | None = None
    store = None
    try:
        await gw.connect()
        store = StateStore(":memory:")
        processor: UserEventProcessor
        def schedule_reconcile(order_id: str) -> None:
            if now() - epoch >= CLOSE_END:
                return
            if order_id not in recovery_tasks or recovery_tasks[order_id].done():
                deadline = (asyncio.get_running_loop().time()
                    + max(0.0, epoch + CLOSE_END - now()))
                recovery_tasks[order_id] = asyncio.create_task(
                    reconcile_owned_order(order_id, gw, processor,
                        deadline=deadline))
        processor = UserEventProcessor(store, on_reconcile=schedule_reconcile)

        async def await_order_truth(
            order_id: str, *, final: bool = False,
        ) -> ReconciliationOutcome:
            # A final check is mandatory for every accepted controlled order:
            # cancellation/open-order cleanup proves no resting order remains,
            # but says nothing about execution.
            task = recovery_tasks.get(order_id)
            if final:
                # Let operational recovery finish before starting the final
                # read, then always issue a fresh post-cleanup order/trade read.
                if task is not None and not task.done():
                    await task
                task = asyncio.create_task(reconcile_owned_order(order_id, gw, processor))
                recovery_tasks[order_id] = task
                outcome = await task
                if outcome is not ReconciliationOutcome.COMPLETE \
                        or order_id in processor.quarantined_orders:
                    raise RuntimeError(f"fill reconciliation unresolved for {order_id}")
                return outcome
            elif order_id in processor.quarantined_orders:
                if task is None:
                    if now() - epoch >= CLOSE_END:
                        return ReconciliationOutcome.ACTIVE_DEADLINE
                    schedule_reconcile(order_id)
                    task = recovery_tasks.get(order_id)
                    if task is None:
                        return ReconciliationOutcome.ACTIVE_DEADLINE
                outcome = await task
                if outcome is ReconciliationOutcome.ACTIVE_DEADLINE:
                    # Keep quarantine in place and return control to the active
                    # order loop so it can hit its cutoff, cancel, and verify
                    # open orders before final reconciliation.
                    return outcome
                if outcome is ReconciliationOutcome.UNRESOLVED:
                    raise RuntimeError(f"fill reconciliation unresolved for {order_id}")
                if order_id in processor.quarantined_orders:
                    raise RuntimeError(f"fill reconciliation unresolved for {order_id}")
                return outcome
            return ReconciliationOutcome.COMPLETE

        def projected(order_id: str) -> list[TradeEvent]:
            events = processor.economic_fills_for_order(order_id)
            for ev in events:
                key = (ev.trade_id, order_id)
                if key not in reported_fill_keys:
                    reported_fill_keys.add(key)
                    log.info("filltest_%s_fill" % ("open" if ev.our_side is Side.BUY else "close"),
                        order_id=order_id, trade_id=ev.trade_id, side=ev.our_side.value,
                        token=ev.token_id, matched_amount=ev.size, price=ev.price,
                        source="shared_fill_tracker")
            return events

        def projected_sells(order_ids: set[str]) -> list[TradeEvent]:
            return [ev for oid in order_ids for ev in projected(oid)]
        selected = await resolver(cfg, slug_for_epoch(epoch))
        if not eligible_elapsed(now() - epoch):
            log.info("filltest_no_order", result="OUTSIDE OPENING WINDOW", reason="resolution delay")
            return False
        log.info("filltest_market_selected", slug=selected.slug, elapsed=now()-epoch,
                 seconds_to_end=epoch+PERIOD_S-now())
        market_ready, user_ready = asyncio.Event(), asyncio.Event()
        market = market_factory(on_connection=lambda ok: market_ready.set() if ok else None,
                                proxy=cfg.proxy)
        tokens = (selected.yes.token_id, selected.no.token_id)
        market.set_markets([(selected.condition_id, list(tokens))])
        tasks.append(asyncio.create_task(market.run()))
        cond_of = lambda token: selected.condition_id if token in tokens else None
        stream = stream_factory(gw.creds, gw.address, processor, other_token=selected.other_token,
            condition_of_token=cond_of, proxy=cfg.proxy,
            on_connection=lambda ok: user_ready.set() if ok else None)
        stream.set_markets([selected.condition_id]); tasks.append(asyncio.create_task(stream.run()))
        if market.connected: market_ready.set()
        if stream.connected: user_ready.set()
        await asyncio.wait_for(asyncio.gather(market_ready.wait(), user_ready.wait()), startup_timeout_s)
        if not market.connected or not stream.connected:
            raise RuntimeError("Market WS or User WS unhealthy")
        snap_deadline = asyncio.get_running_loop().time() + snapshot_timeout_s
        while True:
            ready = market.connected and all(
                (state := market.book(t)) is not None
                and state.snapshot_local_ts >= market.connected_since
                and state.snapshot_generation == market.connection_generation
                and (view := market.view(t)).best_bid is not None
                and view.best_ask is not None and view.best_bid < view.best_ask for t in tokens)
            if ready: break
            rem = snap_deadline - asyncio.get_running_loop().time()
            if rem <= 0: raise RuntimeError("Market WS books not ready")
            await sleep(min(.05, rem))

        # Authoritative pre-trade boundary: no mixed inventory or existing token order.
        live = await gw.open_orders()
        positions = await gw.positions()
        baselines = {t: float(positions.get(t, (0.0, 0.0))[0]) for t in tokens}
        clean = [t for t in tokens if baselines[t] <= 1e-9
                 and not any(o.token_id == t for o in live)]
        if not clean:
            log.error("filltest_aborted", result="NO CLEAN TEST POSITION", positions=baselines)
            return False
        books = {}
        for token in tokens:
            b = await gw.get_book(token)
            if not b or not 0 < b.get("best_ask", 0) < 1 or not 0 < b.get("best_bid", 0) < 1:
                raise RuntimeError("unusable fresh REST book")
            books[token] = b
        eligible_meta = selected
        if len(clean) == 1:
            from dataclasses import replace
            yes, no = selected.yes, selected.no
            if clean[0] == yes.token_id:
                no = replace(no, token_id="__ineligible__")
            else:
                yes = replace(yes, token_id="__ineligible__")
            eligible_meta = replace(selected, tokens=(yes, no))
        for token in tokens:
            b = books.get(token, {})
            p = candidate_price(b.get("best_ask", float("nan")), selected.tick_size)
            q = max(5.0, selected.min_order_size)
            n = p*q if p is not None else None
            reason = "accepted" if p and token in clean and n <= MAX_NOTIONAL else (
                "nonzero baseline or live order" if token not in clean else
                "invalid/post-only-unsafe price" if p is None else "notional exceeds $5")
            log.info("filltest_candidate", outcome=next(x.outcome for x in (selected.yes, selected.no) if x.token_id == token),
                     token=token, best_bid=b.get("best_bid"), best_ask=b.get("best_ask"),
                     tick=selected.tick_size, candidate_price=p, shares=q,
                     candidate_notional=n, decision=reason)
        choice = choose_candidate(eligible_meta, books)
        if choice is None:
            log.info("filltest_no_order", result="NO SAFE CANDIDATE", slug=selected.slug)
            return False
        token, outcome, buy_price, buy_size = choice
        if not eligible_elapsed(now()-epoch):
            log.info("filltest_no_order", result="OUTSIDE OPENING WINDOW")
            return False
        opening_guard = lambda: (now()-epoch < OPEN_END and market.connected and stream.connected
                                 and buy_price*buy_size <= MAX_NOTIONAL+1e-9)
        if not opening_guard(): return False
        processor.arm_pending(token, Side.BUY)
        try:
            placed = await gw.place([Quote(token, Side.BUY, buy_price, buy_size)], selected,
                submit_guard=opening_guard, force_post_only=True, strict_placement=True)
        except PlacementUnknown as exc:
            processor.discard_pending()
            return await _unknown_cleanup(gw, selected.condition_id, token, exc)
        if not placed:
            processor.discard_pending()
            return False
        opening = placed[0]; current_order_id = opening.order_id
        processor.register_order(opening.order_id, selected.condition_id, token, Side.BUY, opening.size, opening.price, "filltest")
        _order_log("filltest_open_order_placed", opening, outcome, epoch, now)
        opening_deadline = min(now()+ATTEMPT_S, epoch+OPEN_END)
        buys: list[TradeEvent] = []
        while now() < opening_deadline:
            if not market.connected or not stream.connected: raise RuntimeError("WS disconnected during opening")
            await sleep(min(.1, opening_deadline-now()))
            truth = await await_order_truth(opening.order_id)
            if truth is ReconciliationOutcome.ACTIVE_DEADLINE:
                break
            buys = projected(opening.order_id)
        if not market.connected or not stream.connected: raise RuntimeError("WS disconnected during opening")
        await _cancel_clean(gw, opening.order_id)
        await await_order_truth(opening.order_id, final=True)
        buys = projected(opening.order_id)
        if opening.order_id in processor.quarantined_orders:
            raise RuntimeError("opening order fill truth is quarantined")
        current_order_id = None

        buy_qty, buy_cost = _totals(buys)
        log.info("filltest_open_cleanup", order_id=opening.order_id, filled_qty=buy_qty)
        if buy_qty <= 1e-9:
            log.info("filltest_inconclusive", result="NO FILL — TEST INCONCLUSIVE")
            return False
        position = None
        verify_until = min(now()+5.0, epoch+CLOSE_END)
        while now() < verify_until:
            position = await _position(gw, token)
            if position is not None and position > baselines[token]+1e-9 \
                    and abs(position-baselines[token]-buy_qty) <= 1e-5:
                break
            await sleep(min(.5, verify_until-now()))
        if position is None or position <= baselines[token]+1e-9 or abs(position-baselines[token]-buy_qty) > 1e-5:
            log.error("filltest_incomplete", result="OPEN FILL CONFIRMED — POSITION VERIFICATION INCOMPLETE",
                      observed_buy_qty=buy_qty, position=position)
            return False
        opening_test_qty = position-baselines[token]
        log.info("filltest_position_open_confirmed", token=token, baseline=baselines[token],
                 position=position, test_position=opening_test_qty)
        if opening_test_qty + 1e-9 < selected.min_order_size:
            return await _incomplete(gw, token, buys, [], opening_test_qty,
                "residual position below minimum legal maker SELL size", epoch, now)

        sells: list[TradeEvent] = []
        closing_order_ids: set[str] = set()
        close_attempt = 0
        while now()-epoch < CLOSE_END:
            if not market.connected or not stream.connected: raise RuntimeError("WS disconnected during closing")
            position = await _position(gw, token)
            if position is None: raise RuntimeError("authoritative position unavailable")
            sells = projected_sells(closing_order_ids)
            cumulative_sell_fill_qty, _ = _totals(sells)
            safe_remaining = safe_remaining_qty(opening_test_qty, cumulative_sell_fill_qty,
                                                 position, baselines[token])
            if safe_remaining <= 1e-9:
                break
            if safe_remaining + 1e-9 < selected.min_order_size:
                return await _incomplete(gw, token, buys, sells, safe_remaining,
                    "residual position below minimum legal maker SELL size", epoch, now)
            book = await gw.get_book(token)
            if not book or not 0 < book.get("best_bid", 0) < 1:
                raise RuntimeError("unusable fresh closing book")
            close_price = _tick_price(book["best_bid"]+selected.tick_size, selected.tick_size, above=True)
            if (close_price is None or close_price <= book["best_bid"] or close_price > 1
                    or (book.get("best_ask") is not None and close_price > book["best_ask"])):
                raise RuntimeError("no valid non-crossing maker SELL price")
            quantity = safe_remaining
            log.info("filltest_close_candidate", outcome=outcome, token=token,
                     best_bid=book["best_bid"], best_ask=book.get("best_ask"),
                     tick=selected.tick_size, price=close_price, quantity=quantity,
                     residual=safe_remaining, elapsed=now()-epoch, seconds_to_end=epoch+PERIOD_S-now())
            guard = lambda: (now()-epoch < CLOSE_END and market.connected and stream.connected
                             and quantity <= max(0.0, position-baselines[token])+1e-9
                             and quantity <= opening_test_qty-cumulative_sell_fill_qty+1e-9)
            if not guard(): raise RuntimeError("closing pre-submit guard failed")
            close_attempt += 1
            processor.arm_pending(token, Side.SELL)
            try:
                placed = await gw.place([Quote(token, Side.SELL, close_price, quantity)], selected,
                    submit_guard=guard, force_post_only=True, strict_placement=True)
            except PlacementUnknown as exc:
                processor.discard_pending()
                return await _unknown_cleanup(gw, selected.condition_id, token, exc)
            if not placed:
                processor.discard_pending()
                raise RuntimeError("maker SELL placement rejected")
            order = placed[0]; current_order_id = order.order_id
            closing_order_ids.add(order.order_id)
            processor.register_order(order.order_id, selected.condition_id, token, Side.SELL, order.size, order.price, "filltest")
            _order_log("filltest_close_order_placed", order, outcome, epoch, now)
            deadline = min(now()+ATTEMPT_S, epoch+CLOSE_END)
            while now() < deadline:
                if not market.connected or not stream.connected: raise RuntimeError("WS disconnected during close attempt")
                await sleep(min(.1, deadline-now()))
                truth = await await_order_truth(order.order_id)
                if truth is ReconciliationOutcome.ACTIVE_DEADLINE:
                    break
                sells = projected_sells(closing_order_ids)
                if sum(e.size for e in sells if e.maker_order_id == order.order_id) + 1e-9 >= quantity:
                    break
            await _cancel_clean(gw, order.order_id)
            current_order_id = None
            await await_order_truth(order.order_id, final=True)
            sells = projected_sells(closing_order_ids)
            if order.order_id in processor.quarantined_orders:
                raise RuntimeError("closing order fill truth is quarantined")
            cumulative_sell_fill_qty, _ = _totals(sells)
            if cumulative_sell_fill_qty > opening_test_qty + 1e-9:
                raise RuntimeError("attributed SELL fills exceed opening test inventory")
            position = await _position(gw, token)
            if position is None: raise RuntimeError("authoritative position unavailable after close")
            safe_remaining = safe_remaining_qty(opening_test_qty, cumulative_sell_fill_qty,
                                                 position, baselines[token])
            if safe_remaining <= 1e-9: break
            if now()-epoch >= CLOSE_END: break
            log.info("filltest_close_reprice", attempt=close_attempt, residual=safe_remaining)

        # Hard stop: no new submissions. Ensure no test order and authoritative final inventory.
        if now()-epoch >= CLOSE_END and current_order_id:
            await _cancel_clean(gw, current_order_id); current_order_id = None
        live = await gw.open_orders()
        if any(o.token_id == token for o in live): raise RuntimeError("filltest order remains live")
        await await_order_truth(opening.order_id)
        buys = projected(opening.order_id)
        for owned_id in closing_order_ids:
            await await_order_truth(owned_id)
        sells = projected_sells(closing_order_ids)
        final_position = await _position(gw, token)
        if final_position is None: raise RuntimeError("final authoritative position unavailable")
        residual = max(0.0, final_position-baselines[token])
        buy_qty, buy_cost = _totals(buys)
        sell_qty, sell_proceeds = _totals(sells)
        sell_attribution_complete = abs(sell_qty-opening_test_qty) <= 1e-5 and bool(sells)
        position_at_baseline = abs(final_position-baselines[token]) <= 1e-5
        local_position = store.position(token).size
        # Position API convergence is only a final inventory check after all
        # exact fill truth and cleanup requirements have already succeeded.
        if not position_at_baseline and buy_qty > 0 and sell_attribution_complete \
                and abs(local_position) <= 1e-5 and not processor.quarantined_orders \
                and not any(processor.has_failed_fill(oid) for oid in closing_order_ids) \
                and not any(processor.has_unresolved_fill_key(oid) for oid in closing_order_ids) \
                and not any(o.token_id == token for o in live):
            expected = baselines[token]
            previous = final_position
            for attempt, delay in enumerate(POSITION_GRACE_DELAYS_S, start=1):
                log.info("filltest_position_lag", token=token, baseline=expected,
                         observed_position=previous, expected_position=expected, attempt=attempt)
                await sleep(delay)
                if processor.quarantined_orders or any(
                        processor.has_failed_fill(oid) or processor.has_unresolved_fill_key(oid)
                        for oid in closing_order_ids):
                    break
                observed = await _position(gw, token)
                if observed is None:
                    break
                # User-stream events can arrive while the position request is
                # in flight. Recheck Fill Truth after the await so a baseline
                # response cannot mask a newly failed or quarantined fill.
                if processor.quarantined_orders or any(
                        processor.has_failed_fill(oid)
                        or processor.has_unresolved_fill_key(oid)
                        for oid in closing_order_ids):
                    break
                # A lagging long inventory may converge downward to baseline.
                # Movement away from baseline is unexplained account activity.
                if observed > previous + 1e-5 or observed > expected + buy_qty + 1e-5 \
                        or observed < expected - 1e-5:
                    log.error("filltest_position_convergence_unresolved", token=token,
                              baseline=expected, observed_position=observed,
                              previous_position=previous, attempt=attempt)
                    break
                final_position = observed
                previous = observed
                if abs(observed-expected) <= 1e-5:
                    position_at_baseline = True
                    log.info("filltest_position_converged", token=token, baseline=expected,
                             observed_position=observed, attempt=attempt)
                    break
            if not position_at_baseline and not processor.quarantined_orders \
                    and not any(processor.has_failed_fill(oid) for oid in closing_order_ids):
                log.error("filltest_position_convergence_timeout", token=token,
                          baseline=expected, observed_position=final_position,
                          attempts=len(POSITION_GRACE_DELAYS_S))
        closing_truth_blocked = bool(processor.quarantined_orders) or any(
            processor.has_failed_fill(oid) or processor.has_unresolved_fill_key(oid)
            for oid in closing_order_ids)
        if position_at_baseline and sell_attribution_complete and buy_qty > 0 \
                and not closing_truth_blocked:
            pnl = sell_proceeds-buy_cost
            log.info("filltest_position_closed", token=token, final_position=final_position)
            log.info("filltest_round_trip_pass", result="MAKER ROUND TRIP PASS", outcome=outcome,
                     buy_qty=buy_qty, buy_vwap=buy_cost/buy_qty, sell_qty=sell_qty,
                     sell_vwap=sell_proceeds/sell_qty if sell_qty else 0, realized_pnl=pnl,
                     final_position=final_position, final_open_order_count=0)
            return True
        if position_at_baseline and not sell_attribution_complete:
            log.error("filltest_round_trip_verification_incomplete",
                      result="ROUND TRIP VERIFICATION INCOMPLETE",
                      reason="position closed but SELL fill attribution incomplete",
                      opening_qty=opening_test_qty, attributed_sell_qty=sell_qty,
                      pnl_verification="incomplete", final_position=final_position,
                      final_open_order_count=0)
            return False
        reason = ("T+270 hard stop" if now()-epoch >= CLOSE_END else
                  "position_convergence_timeout" if sell_attribution_complete
                  and abs(local_position) <= 1e-5 and not position_at_baseline
                  else "closing incomplete")
        return await _incomplete(gw, token, buys, sells, residual, reason,
                                 epoch, now)
    except Exception as exc:
        if current_order_id:
            try: await _cancel_clean(gw, current_order_id)
            except Exception as cleanup_exc:
                log.critical("filltest_cleanup_unknown", order_id=current_order_id, err=str(cleanup_exc))
        log.error("filltest_aborted", reason=str(exc))
        return False
    finally:
        if stream: stream.stop()
        if market: market.stop()
        for task in tasks: task.cancel()
        if tasks: await asyncio.gather(*tasks, return_exceptions=True)
        for task in recovery_tasks.values():
            if not task.done(): task.cancel()
        if recovery_tasks:
            await asyncio.gather(*recovery_tasks.values(), return_exceptions=True)
        gw.close()
        if store: store.close()


async def _unknown_cleanup(gw: Any, market: str, token: str, exc: Exception) -> bool:
    log.critical("filltest_placement_unknown", market=market, token=token, err=str(exc))
    try:
        if not await gw.cancel_market_asset(market, token):
            log.critical("filltest_unknown_cleanup_unconfirmed", reason="market/asset cancel failed")
            return False
        if any(o.token_id == token for o in await gw.open_orders()):
            log.critical("filltest_unknown_cleanup_unconfirmed", reason="live order remains")
            return False
    except Exception as error:
        log.critical("filltest_unknown_cleanup_unconfirmed", err=str(error)); return False
    return False


async def _cancel_clean(gw: Any, order_id: str) -> None:
    if not await gw.cancel([order_id]): raise RuntimeError("uncertain cancel")
    if any(o.order_id == order_id for o in await gw.open_orders()):
        raise RuntimeError("order remains in authoritative open_orders")


async def _position(gw: Any, token: str) -> float | None:
    positions = await gw.positions()
    return float(positions.get(token, (0.0, 0.0))[0])


def _totals(events: list[TradeEvent]) -> tuple[float, float]:
    qty = sum(e.size for e in events)
    value = sum(e.size*e.price for e in events)
    return qty, value


def _order_log(event: str, order: Any, outcome: str, epoch: int,
               now: Callable[[], float]) -> None:
    log.info(event, order_id=order.order_id, side=order.side.value, outcome=outcome,
             token=order.token_id, price=order.price, quantity=order.size,
             elapsed=now()-epoch, seconds_to_end=epoch+PERIOD_S-now())


async def _incomplete(gw: Any, token: str, buys: list[TradeEvent], sells: list[TradeEvent],
                      residual: float, reason: str, epoch: int,
                      now: Callable[[], float]) -> bool:
    live = await gw.open_orders()
    if any(o.token_id == token for o in live):
        raise RuntimeError("test order remains live during incomplete exit")
    position = await _position(gw, token)
    bq, bc = _totals(buys); sq, sp = _totals(sells)
    log.error("filltest_round_trip_incomplete", result="ROUND TRIP INCOMPLETE", reason=reason,
              opening_qty=bq, opening_vwap=bc/bq if bq else 0, closing_qty=sq,
              closing_vwap=sp/sq if sq else 0, residual_position=residual,
              final_position=position, pnl_verification="incomplete", elapsed=now()-epoch)
    return False


async def run_filltest(cfg: Config, *, now: Callable[[], float] = time.time,
                       sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
                       **kwargs: Any) -> bool:
    """Run one BTC5m maker round trip; never selects another market."""
    return await _run_filltest_market(cfg, now=now, sleep=sleep, **kwargs)
