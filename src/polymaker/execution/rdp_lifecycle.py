"""Scoped asynchronous close barrier for one RDP market cycle.

The barrier sequences existing gateway cancellation, filltruth reconciliation,
validated terminal observations, and confirmed-fill accounting. Callers must
serialize strategy decisions and block submissions while it runs. A complete
result describes this closure observation only; later actions must recheck
current safety gates.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import math
from numbers import Real
from typing import Any

from polymaker.domain import MarketMeta, OpenOrder, Side, TradeState
from polymaker.execution.filltruth import ReconciliationOutcome, reconcile_owned_order
from polymaker.state.tracker import UserEventProcessor
from polymaker.strategy.rdp_accounting import RDPInventorySnapshot, snapshot_inventory
from polymaker.userstream.parse import OrderEvent, normalize_order

_ORDER_TOLERANCE = 1e-6


class _OperationalDeadlineExpired(BaseException):
    """Synchronous deadline guard; bypasses broad gateway-error handlers."""


@dataclass(frozen=True, slots=True)
class RDPCloseResult:
    """Immutable evidence from one bounded close attempt."""

    complete: bool
    reason: str | None
    affected_order_ids: tuple[str, ...]
    reconciliation_outcomes: tuple[tuple[str, ReconciliationOutcome], ...]
    inventory: RDPInventorySnapshot | None


@dataclass(slots=True)
class _CloseAttempt:
    market: MarketMeta
    processor: UserEventProcessor
    gateway: Any
    run_ids: frozenset[str]
    closing_ids: frozenset[str]
    deadline: float
    outcomes: dict[str, ReconciliationOutcome] = field(default_factory=dict)
    terminal: dict[str, OrderEvent] = field(default_factory=dict)
    failures: list[tuple[str, str | None]] = field(default_factory=list)
    cancel_requested: set[str] = field(default_factory=set)

    def fail(self, reason: str, order_id: str | None = None) -> None:
        self.failures.append((reason, order_id))

    def result(self, *, inventory: RDPInventorySnapshot | None = None) -> RDPCloseResult:
        reason = self.failures[0][0] if self.failures else None
        affected = {oid for _reason, oid in self.failures if oid is not None}
        return RDPCloseResult(
            complete=not self.failures and inventory is not None,
            reason=reason,
            affected_order_ids=tuple(sorted(affected)),
            reconciliation_outcomes=tuple(sorted(self.outcomes.items())),
            inventory=inventory if not self.failures else None,
        )


async def close_rdp_cycle(
    market: MarketMeta,
    processor: UserEventProcessor,
    gateway: Any,
    run_order_ids: set[str],
    closing_order_ids: set[str],
    deadline: float,
) -> RDPCloseResult:
    """Cancel/reconcile a scoped cycle and expose inventory only on proof.

    `deadline` is an absolute monotonic time for this operational attempt. It
    is not a strategy timer or stop rule. No task is detached or shielded; task
    cancellation propagates and deadline timeout cancels/awaits the active
    operation before returning an incomplete result.
    """
    invalid = _validate_scope(market, processor, run_order_ids, closing_order_ids)
    if invalid is not None:
        return RDPCloseResult(False, invalid, (), (), None)
    if (isinstance(deadline, bool) or not isinstance(deadline, Real)
            or not math.isfinite(float(deadline))):
        return RDPCloseResult(False, "INVALID_DEADLINE", (), (), None)
    attempt = _CloseAttempt(market, processor, gateway, frozenset(run_order_ids),
                            frozenset(closing_order_ids), float(deadline))
    try:
        async with asyncio.timeout_at(attempt.deadline):
            result = await _close(attempt)
            _check_deadline(attempt)
            return result
    except _OperationalDeadlineExpired:
        attempt.fail("OPERATIONAL_DEADLINE")
        return attempt.result()
    except TimeoutError:
        attempt.fail("OPERATIONAL_DEADLINE")
        return attempt.result()


def _check_deadline(attempt: _CloseAttempt) -> None:
    if asyncio.get_running_loop().time() >= attempt.deadline:
        raise _OperationalDeadlineExpired


async def _await_gateway(attempt: _CloseAttempt, method: Any, *args: Any,
                         **kwargs: Any) -> Any:
    _check_deadline(attempt)
    result = await method(*args, **kwargs)
    _check_deadline(attempt)
    return result


class _DeadlineBoundReads:
    """Apply an explicit pre/post deadline check around filltruth reads."""

    def __init__(self, attempt: _CloseAttempt) -> None:
        self._attempt = attempt

    async def get_order(self, order_id: str) -> Any:
        return await _await_gateway(self._attempt, self._attempt.gateway.get_order, order_id)

    async def get_trades(self, *, market: str | None = None,
                         asset_id: str | None = None) -> Any:
        return await _await_gateway(self._attempt, self._attempt.gateway.get_trades,
                                    market=market, asset_id=asset_id)


def _validate_scope(
    market: MarketMeta,
    processor: UserEventProcessor,
    run_order_ids: set[str],
    closing_order_ids: set[str],
) -> str | None:
    if not isinstance(market, MarketMeta) or not isinstance(processor, UserEventProcessor):
        return "INVALID_SCOPE"
    if not isinstance(run_order_ids, set) or not isinstance(closing_order_ids, set):
        return "INVALID_SCOPE"
    if not closing_order_ids.issubset(run_order_ids):
        return "INVALID_SCOPE"
    if (not market.condition_id or not market.yes.token_id or not market.no.token_id
            or market.yes.token_id == market.no.token_id):
        return "INVALID_SCOPE"
    for order_id in run_order_ids:
        if not isinstance(order_id, str) or not order_id:
            return "INVALID_SCOPE"
        record = processor.owned_record(order_id)
        if record is None:
            return "INVALID_SCOPE"
        if (record.get("condition_id") != market.condition_id
                or record.get("token_id") not in (market.yes.token_id, market.no.token_id)
                or record.get("side") != Side.BUY.value):
            return "INVALID_SCOPE"
    return None


async def _close(attempt: _CloseAttempt) -> RDPCloseResult:
    try:
        initial = _read_open_orders(await _await_gateway(
            attempt, attempt.gateway.open_orders))
    except Exception:
        attempt.fail("OPEN_ORDER_READ_FAILED")
        return attempt.result()
    _record_open_conflicts(attempt, initial)
    initial_by_id = {row.order_id: row for row in initial}
    tokens = {attempt.market.yes.token_id, attempt.market.no.token_id}
    attempt.cancel_requested = {
        oid for oid in attempt.closing_ids
        if oid in initial_by_id and initial_by_id[oid].token_id in tokens
    }

    if attempt.cancel_requested:
        try:
            canceled = await _await_gateway(
                attempt, attempt.gateway.cancel, sorted(attempt.cancel_requested))
        except Exception:
            canceled = False
        if canceled is not True:
            for oid in sorted(attempt.cancel_requested):
                attempt.fail("CANCELLATION_UNCERTAIN", oid)

    try:
        after_cancel = _read_open_orders(await _await_gateway(
            attempt, attempt.gateway.open_orders))
    except Exception:
        after_cancel = None
        attempt.fail("OPEN_ORDER_READ_FAILED")
    if after_cancel is not None:
        _record_open_conflicts(attempt, after_cancel)
        remaining_after_cancel = {
            row.order_id for row in after_cancel if row.order_id in attempt.cancel_requested
        }
        for oid in sorted(remaining_after_cancel):
            attempt.fail("OPEN_ORDERS_REMAIN", oid)

    for oid in sorted(attempt.run_ids):
        try:
            outcome = await reconcile_owned_order(
                oid, _DeadlineBoundReads(attempt), attempt.processor,
                retry_delays=(), deadline=attempt.deadline,
            )
            _check_deadline(attempt)
        except Exception:
            outcome = ReconciliationOutcome.UNRESOLVED
        attempt.outcomes[oid] = outcome
        if outcome is not ReconciliationOutcome.COMPLETE:
            reason = ("OPERATIONAL_DEADLINE" if outcome is ReconciliationOutcome.ACTIVE_DEADLINE
                      else "RECONCILIATION_FAILED")
            attempt.fail(reason, oid)

    # Obtain and apply only exact, validated terminal order observations.
    for oid in sorted(attempt.run_ids):
        try:
            raw = await _await_gateway(attempt, attempt.gateway.get_order, oid)
        except Exception:
            attempt.fail("TERMINAL_STATE_UNCERTAIN", oid)
            continue
        try:
            event = normalize_order(raw) if isinstance(raw, dict) else None
        except (TypeError, ValueError, KeyError, OverflowError):
            event = None
        if event is None or not _matches_registered_order(attempt, oid, event):
            _quarantine(attempt, oid, "terminal order snapshot contradicts registered identity")
            attempt.fail("ORDER_STATE_CONFLICT", oid)
            continue
        if not _terminal_order(event):
            attempt.fail("TERMINAL_STATE_UNCERTAIN", oid)
            continue
        _check_deadline(attempt)
        attempt.processor.on_order(event, attempt.market.condition_id)
        _check_deadline(attempt)
        attempt.terminal[oid] = event
        if oid in attempt.processor.quarantined_orders:
            attempt.fail("ORDER_STATE_CONFLICT", oid)

    try:
        final_open = _read_open_orders(await _await_gateway(
            attempt, attempt.gateway.open_orders))
    except Exception:
        final_open = None
        attempt.fail("OPEN_ORDER_READ_FAILED")
    if final_open is not None:
        _record_open_conflicts(attempt, final_open)
        for row in final_open:
            if row.token_id in tokens:
                attempt.fail("OPEN_ORDERS_REMAIN", row.order_id)

    # Recheck the authoritative local projection after the final gateway await.
    # A late owned LIVE event or any other market-token order blocks release.
    _check_deadline(attempt)
    for token_id in tokens:
        for local_order in attempt.processor._store.orders_for(token_id):
            attempt.fail("LOCAL_ORDER_REMAINS", local_order.order_id)

    # No await follows this point. Recheck evidence that may have changed while
    # the final authoritative open-order read was in flight.
    for oid in sorted(attempt.run_ids):
        if attempt.outcomes.get(oid) is not ReconciliationOutcome.COMPLETE:
            continue
        event = attempt.terminal.get(oid)
        if event is None:
            continue
        if abs(event.size_matched - attempt.processor.tracked_trade_qty(oid)) > _ORDER_TOLERANCE:
            _quarantine(attempt, oid, "terminal cumulative quantity changed after reconciliation")
            attempt.fail("TERMINAL_CHECKSUM_CHANGED", oid)
        if (oid in attempt.processor.quarantined_orders
                or attempt.processor.has_unresolved_fill_key(oid)
                or attempt.processor.has_failed_fill(oid)):
            attempt.fail("UNRESOLVED_EVIDENCE", oid)
        for (trade_id, fill_oid), fill in attempt.processor.fill_records.items():
            if fill_oid != oid:
                continue
            key = (trade_id, oid)
            if key in attempt.processor.quarantined_fill_keys:
                attempt.fail("UNRESOLVED_EVIDENCE", oid)
            elif key not in attempt.processor.applied_fill_keys:
                attempt.fail("UNADMITTED_FILL", oid)
            elif fill.status is not TradeState.CONFIRMED:
                attempt.fail("PENDING_CONFIRMATION", oid)

    if attempt.failures:
        return attempt.result()
    _check_deadline(attempt)
    try:
        inventory = snapshot_inventory(attempt.market, attempt.processor, set(attempt.run_ids))
    except (TypeError, ValueError):
        attempt.fail("ACCOUNTING_REJECTED")
        return attempt.result()
    _check_deadline(attempt)
    return attempt.result(inventory=inventory)


def _read_open_orders(value: object) -> list[OpenOrder]:
    if not isinstance(value, list) or any(not isinstance(row, OpenOrder) for row in value):
        raise ValueError("open-order read is malformed")
    return value


def _record_open_conflicts(attempt: _CloseAttempt, rows: list[OpenOrder]) -> None:
    tokens = {attempt.market.yes.token_id, attempt.market.no.token_id}
    for row in rows:
        if row.token_id not in tokens:
            continue
        record = attempt.processor.owned_record(row.order_id)
        if (row.order_id not in attempt.run_ids or record is None
                or record.get("condition_id") != attempt.market.condition_id
                or record.get("token_id") != row.token_id
                or row.side is not Side.BUY):
            attempt.fail("OPEN_ORDER_CONFLICT", row.order_id)


def _matches_registered_order(attempt: _CloseAttempt, order_id: str, event: OrderEvent) -> bool:
    record = attempt.processor.owned_record(order_id)
    if record is None:
        return False
    try:
        return (
            event.order_id == order_id
            and event.token_id == record["token_id"]
            and event.side is Side.BUY
            and event.market_id in (None, attempt.market.condition_id)
            and abs(event.original_size - float(record["submitted_qty"])) <= _ORDER_TOLERANCE
            and abs(event.price - float(record["submitted_price"])) <= _ORDER_TOLERANCE
        )
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def _terminal_order(event: OrderEvent) -> bool:
    if event.is_cancel:
        return True
    return (event.status.upper() == "MATCHED"
            and abs(event.original_size - event.size_matched) <= _ORDER_TOLERANCE)


def _quarantine(attempt: _CloseAttempt, order_id: str, reason: str) -> None:
    _check_deadline(attempt)
    attempt.processor.quarantine_order(order_id, reason, notify=False)
    _check_deadline(attempt)
