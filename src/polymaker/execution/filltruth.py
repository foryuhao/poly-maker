"""Shared targeted REST recovery for owned maker orders."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from enum import Enum
from typing import Any

from polymaker.execution.gateway import GatewayReadError
from polymaker.logging import get_logger
from polymaker.research.capture import clock
from polymaker.state.tracker import TradeEvent, UserEventProcessor
from polymaker.userstream.parse import (
    MalformedMakerLeg,
    maker_order_id_evidence,
    normalize_order,
    parse_trade,
)

log = get_logger("execution.filltruth")
_PROCESS_BATCH_SIZE = 32


class _ActiveOrderDeadlineExpired(Exception):
    """Cooperative stop for CPU work performed inside an active reconciliation."""


def _check_active_order_deadline(deadline: float | None) -> None:
    if deadline is not None and asyncio.get_running_loop().time() >= deadline:
        raise _ActiveOrderDeadlineExpired


async def _processing_checkpoint(deadline: float | None, index: int) -> None:
    _check_active_order_deadline(deadline)
    if index and index % _PROCESS_BATCH_SIZE == 0:
        await asyncio.sleep(0)
        _check_active_order_deadline(deadline)


async def _parse_trade_cooperatively(
    raw: dict[str, Any], deadline: float | None,
) -> Any:
    """Parse one trade while yielding/checking between large maker-leg batches."""
    maker_rows = raw.get("maker_orders")
    if not isinstance(maker_rows, list) or not maker_rows:
        _check_active_order_deadline(deadline)
        return parse_trade(raw)

    # Parse the shared envelope once, then parse each leg independently. This
    # preserves parse_trade's validation while allowing large maker lists to
    # yield between bounded batches.
    parsed = parse_trade({**raw, "maker_orders": []})
    maker_legs = []
    for index, maker_row in enumerate(maker_rows):
        await _processing_checkpoint(deadline, index)
        one_leg = parse_trade({**raw, "maker_orders": [maker_row]})
        maker_legs.extend(one_leg.maker_legs)
    return replace(parsed, maker_legs=tuple(maker_legs), raw=dict(raw))


class ReconciliationOutcome(str, Enum):
    COMPLETE = "COMPLETE"
    ORDER_STATE_LAG = "ORDER_STATE_LAG"
    ACTIVE_DEADLINE = "ACTIVE_DEADLINE"
    UNRESOLVED = "UNRESOLVED"


async def reconcile_owned_order_once(order_id: str, gateway: Any,
                                     processor: UserEventProcessor, *,
                                     active_order_deadline: float | None = None) -> ReconciliationOutcome:
    """Perform one fresh targeted read. Returns COMPLETE, ORDER_STATE_LAG or UNRESOLVED."""
    owned = processor.owned_record(order_id)
    if owned is None:
        raise ValueError("cannot reconcile an order outside OwnedOrderRegistry")
    log.info("order_reconcile_started", order_id=order_id)
    try:
        row = await gateway.get_order(order_id)
        _check_active_order_deadline(active_order_deadline)
        trades = await gateway.get_trades(market=str(owned["condition_id"]))
        research_receipt = clock() if processor._research is not None else (None, None)
        _check_active_order_deadline(active_order_deadline)
        order_ev = normalize_order(row)
        _check_active_order_deadline(active_order_deadline)
        if order_ev is None or order_ev.order_id != order_id:
            raise GatewayReadError("order snapshot malformed or mismatched")
        if (order_ev.token_id != owned["token_id"] or order_ev.side.value != owned["side"]
                or abs(order_ev.original_size-float(owned["submitted_qty"])) > 1e-6
                or abs(order_ev.price-float(owned["submitted_price"])) > 1e-6):
            processor.quarantine_order(order_id, "authoritative order economics contradict registry", notify=False)
            return ReconciliationOutcome.UNRESOLVED
        rest_fills: dict[tuple[str, str], float] = {}
        for trade_index, raw in enumerate(trades):
            await _processing_checkpoint(active_order_deadline, trade_index)
            if not isinstance(raw, dict):
                raise GatewayReadError("trade row is not an object")
            try:
                parsed = await _parse_trade_cooperatively(raw, active_order_deadline)
                _check_active_order_deadline(active_order_deadline)
            except (MalformedMakerLeg, ValueError) as exc:
                _check_active_order_deadline(active_order_deadline)
                maker_rows = raw.get("maker_orders")
                # A malformed row is ignorable only if every maker identity is
                # present and proves this row cannot belong to the target order.
                if (isinstance(maker_rows, list) and maker_rows
                        and all(isinstance(m, dict) for m in maker_rows)
                        and all(_proven_foreign_maker(m, order_id) for m in maker_rows)):
                    continue
                _check_active_order_deadline(active_order_deadline)
                processor.quarantine_order(order_id,
                    f"trade history contains unknown relevant shape: {exc}", notify=False)
                return ReconciliationOutcome.UNRESOLVED
            for leg_index, leg in enumerate(parsed.maker_legs):
                await _processing_checkpoint(active_order_deadline, leg_index)
                if leg.order_id != order_id:
                    continue
                # A market supplied by REST is observed source evidence. The
                # registry remains the expected value; never substitute it.
                if parsed.market and parsed.market != str(owned["condition_id"]):
                    _check_active_order_deadline(active_order_deadline)
                    processor.quarantine_order(order_id,
                        "REST trade market contradicts registered condition", notify=False)
                    return ReconciliationOutcome.UNRESOLVED
                before_qty = processor.tracked_trade_qty(order_id)
                _check_active_order_deadline(active_order_deadline)
                key = (parsed.trade_id, leg.order_id)
                rest_fills[key] = leg.matched_amount
                _check_active_order_deadline(active_order_deadline)
                processor.on_trade(TradeEvent(leg.asset_id, leg.side, leg.price,
                    leg.matched_amount, parsed.trade_id, parsed.status,
                    parsed.timestamp, leg.order_id, parsed.market or None),
                    parsed.market or str(owned["condition_id"]), source="REST",
                    received_at_us=research_receipt[0],
                    received_monotonic_ns=research_receipt[1])
                _check_active_order_deadline(active_order_deadline)
                after_qty = processor.tracked_trade_qty(order_id)
                _check_active_order_deadline(active_order_deadline)
                if after_qty > before_qty:
                    log.info("order_reconcile_fill_recovered", order_id=order_id,
                        trade_id=parsed.trade_id, token=leg.asset_id[:12],
                        side=leg.side.value, qty=leg.matched_amount,
                        price=leg.price, source="REST", status=parsed.status.value)
        qty = processor.tracked_trade_qty(order_id)
        _check_active_order_deadline(active_order_deadline)
        failed_fill = processor.has_failed_fill(order_id)
        unresolved_fill = processor.has_unresolved_fill_key(order_id)
        _check_active_order_deadline(active_order_deadline)
        if failed_fill or unresolved_fill:
            log.error("order_reconcile_unresolved", order_id=order_id,
                      reason="trade lifecycle remains FAILED")
            return ReconciliationOutcome.UNRESOLVED
        if order_ev.associate_trades:
            known_trade_ids = set()
            for index, event in enumerate(processor.economic_fills_for_order(order_id)):
                await _processing_checkpoint(active_order_deadline, index)
                known_trade_ids.add(event.trade_id)
            missing = []
            for index, trade_id in enumerate(order_ev.associate_trades):
                await _processing_checkpoint(active_order_deadline, index)
                if trade_id not in known_trade_ids:
                    missing.append(trade_id)
            if missing:
                _check_active_order_deadline(active_order_deadline)
                processor.quarantine_order(order_id,
                    "authoritative order references unrecovered associate_trades", notify=False)
                log.error("order_reconcile_unresolved", order_id=order_id,
                          missing_associate_trades=sorted(missing))
                return ReconciliationOutcome.UNRESOLVED
        if abs(order_ev.size_matched - qty) <= 1e-6:
            # The final gate is immediately before every state transition that
            # would report authoritative completion or clear recovery.
            _check_active_order_deadline(active_order_deadline)
            processor.quarantined_orders.discard(order_id)
            _check_active_order_deadline(active_order_deadline)
            processor._store.mark_owned_order_unresolved(order_id, False)
            _check_active_order_deadline(active_order_deadline)
            log.info("order_reconcile_complete", order_id=order_id, tracked_qty=qty,
                     venue_qty=order_ev.size_matched)
            return ReconciliationOutcome.COMPLETE
        rest_trade_qty = 0.0
        for index, trade_qty in enumerate(rest_fills.values()):
            await _processing_checkpoint(active_order_deadline, index)
            rest_trade_qty += trade_qty
        tracked_keys = set()
        for index, event in enumerate(processor.economic_fills_for_order(order_id)):
            await _processing_checkpoint(active_order_deadline, index)
            tracked_keys.add((event.trade_id, order_id))
        if (qty > 0 and order_ev.size_matched < qty - 1e-6
                and set(rest_fills) == tracked_keys
                and abs(rest_trade_qty - qty) <= 1e-6):
            _check_active_order_deadline(active_order_deadline)
            processor.quarantine_order(order_id, "REST order cumulative state lags exact maker trades",
                                       notify=False)
            log.warning("order_reconcile_lag", order_id=order_id, tracked_qty=qty,
                        rest_trade_qty=rest_trade_qty, venue_qty=order_ev.size_matched)
            return ReconciliationOutcome.ORDER_STATE_LAG
        _check_active_order_deadline(active_order_deadline)
        log.error("order_reconcile_unresolved", order_id=order_id, tracked_qty=qty,
                  venue_qty=order_ev.size_matched)
        return ReconciliationOutcome.UNRESOLVED
    except _ActiveOrderDeadlineExpired:
        processor.quarantine_order(order_id, "active order reconciliation deadline reached",
                                   notify=False)
        return ReconciliationOutcome.ACTIVE_DEADLINE
    except Exception as exc:
        processor.quarantine_order(order_id, f"REST reconciliation failed: {exc}", notify=False)
        log.error("order_reconcile_unresolved", order_id=order_id, err=str(exc))
        return ReconciliationOutcome.UNRESOLVED


async def reconcile_owned_order(order_id: str, gateway: Any,
                                processor: UserEventProcessor, *,
                                sleep: Any = asyncio.sleep,
                                retry_delays: tuple[float, ...] = (0.5, 1.0, 2.0, 4.0),
                                deadline: float | None = None,
                                stopping: Any = None) -> ReconciliationOutcome:
    """Boundedly retry only exact REST-trade evidence ahead of order cumulative state."""
    stop_reason = "order_state_lag_timeout"
    for attempt in range(len(retry_delays) + 1):
        loop = asyncio.get_running_loop()
        # Check when this coroutine actually runs: a task queued before cutoff
        # may not begin until after the active-order deadline.
        remaining = deadline - loop.time() if deadline is not None else None
        if remaining is not None and remaining <= 0:
            stop_reason = "active_order_deadline"
            log.error("order_reconcile_active_deadline", order_id=order_id,
                      attempt=attempt, deadline=deadline, remaining=remaining)
            processor.quarantine_order(order_id, "active order reconciliation deadline reached",
                                       notify=False)
            return ReconciliationOutcome.ACTIVE_DEADLINE
        try:
            if deadline is None:
                status = await reconcile_owned_order_once(order_id, gateway, processor)
            else:
                # Bound the entire attempt, including both fresh REST reads.
                # asyncio.timeout cancels and awaits the current operation on
                # exit, so no abandoned reconciliation continues in the background.
                async with asyncio.timeout_at(deadline):
                    status = await reconcile_owned_order_once(
                        order_id, gateway, processor,
                        active_order_deadline=deadline)
        except TimeoutError:
            stop_reason = "active_order_deadline"
            log.error("order_reconcile_active_deadline", order_id=order_id,
                      attempt=attempt, deadline=deadline, remaining=0.0)
            processor.quarantine_order(order_id, "active order reconciliation deadline reached",
                                       notify=False)
            return ReconciliationOutcome.ACTIVE_DEADLINE
        # Preserve a substantive contradiction/read failure that completed as
        # the cutoff arrived; the deadline is not a way to downgrade it.
        if status is ReconciliationOutcome.UNRESOLVED:
            return ReconciliationOutcome.UNRESOLVED
        if status is ReconciliationOutcome.ACTIVE_DEADLINE:
            return status
        if status is ReconciliationOutcome.COMPLETE:
            return ReconciliationOutcome.COMPLETE
        # A lag result is retryable only if the active window remains open.
        if deadline is not None and loop.time() >= deadline:
            stop_reason = "active_order_deadline"
            log.error("order_reconcile_active_deadline", order_id=order_id,
                      attempt=attempt, deadline=deadline, remaining=0.0)
            processor.quarantine_order(order_id, "active order reconciliation deadline reached",
                                       notify=False)
            return ReconciliationOutcome.ACTIVE_DEADLINE
        if status is not ReconciliationOutcome.ORDER_STATE_LAG:
            return ReconciliationOutcome.UNRESOLVED
        if attempt >= len(retry_delays):
            break
        delay = retry_delays[attempt]
        if deadline is not None:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                stop_reason = "active_order_deadline"
                log.error("order_reconcile_active_deadline", order_id=order_id,
                          attempt=attempt + 1, deadline=deadline, remaining=remaining)
                return ReconciliationOutcome.ACTIVE_DEADLINE
            delay = min(delay, remaining)
        log.info("order_reconcile_retry", order_id=order_id, attempt=attempt + 1,
                 delay_s=delay)
        if stopping is not None and stopping.is_set():
            break
        await sleep(delay)
        if stopping is not None and stopping.is_set():
            break
        # Capped sleep can wake at or after the cutoff (including late
        # scheduler wakeups). Return to filltest cleanup without another REST
        # read; final post-cleanup reconciliation has its own grace period.
        if deadline is not None and asyncio.get_running_loop().time() >= deadline:
            stop_reason = "active_order_deadline"
            log.error("order_reconcile_active_deadline", order_id=order_id,
                      attempt=attempt + 1, deadline=deadline, remaining=0.0)
            return ReconciliationOutcome.ACTIVE_DEADLINE
    log.error("order_reconcile_unresolved", order_id=order_id,
              reason=stop_reason)
    return ReconciliationOutcome.UNRESOLVED


def _proven_foreign_maker(row: dict[str, Any], target_order_id: str) -> bool:
    evidence = maker_order_id_evidence(row)
    # A malformed alias group can still contain this exact owned ID. Ignore a
    # malformed REST leg only when every supplied alias is usable and all
    # explicitly named IDs prove it belongs to another order.
    return (evidence.present and evidence.all_usable
            and target_order_id not in evidence.ids)
