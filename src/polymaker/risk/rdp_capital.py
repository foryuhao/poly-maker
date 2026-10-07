"""Read-only capital projection and admission gate for RDP Phase 0.

The projection consumes caller-supplied authoritative order evidence and the
existing processor/accounting state. It does not reserve, place, cancel, or
mutate orders. NORMAL admission protects the human-approved $5 future repair
reserve; REBALANCE may use it.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, DecimalException, localcontext
from enum import Enum
from numbers import Real

from polymaker.domain import MarketMeta, OpenOrder, OrderState, Quote, Side, TradeState
from polymaker.execution.filltruth import ReconciliationOutcome
from polymaker.execution.rdp_lifecycle import RDPCloseResult
from polymaker.state.tracker import UserEventProcessor
from polymaker.strategy.rdp_accounting import RDPInventorySnapshot, snapshot_inventory
from polymaker.strategy.rdp_quotes import RDPQuoteDecision

CAPITAL = Decimal("30.00")
NORMAL_REPAIR_RESERVE = Decimal("5.00")
_ZERO = Decimal("0")
_ONE = Decimal("1")
_ORDER_QTY_TOLERANCE = 1e-6
_ORDER_PRICE_TOLERANCE = 1e-6


class RDPMode(str, Enum):
    NORMAL = "NORMAL"
    REBALANCE = "REBALANCE"


@dataclass(frozen=True, slots=True)
class RDPCapitalSnapshot:
    """Certified read-only capital state for one explicit RDP market scope."""

    capital: Decimal
    inventory_cost: Decimal
    reserved_cash: Decimal
    free_cash: Decimal
    inventory: RDPInventorySnapshot
    run_order_ids: tuple[str, ...]
    resting_order_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RDPCapitalProjection:
    """Either a certified projection or a fail-closed reason, never both."""

    snapshot: RDPCapitalSnapshot | None
    reason: str | None
    affected_order_ids: tuple[str, ...] = ()

    @property
    def certified(self) -> bool:
        return self.snapshot is not None and self.reason is None


@dataclass(frozen=True, slots=True)
class RDPCapitalAdmission:
    """Immutable CapitalGate result; admission is capital eligibility only."""

    admitted: bool
    reason: str | None
    new_notional: Decimal | None
    free_cash_after_new: Decimal | None
    committed_capital_after_new: Decimal | None
    protected_amount: Decimal


def project_rdp_capital(
    market: MarketMeta,
    processor: UserEventProcessor,
    run_order_ids: set[str],
    open_orders: list[OpenOrder],
    *,
    market_state_reliable: bool,
    order_state_reliable: bool,
    close_evidence: RDPCloseResult | None = None,
) -> RDPCapitalProjection:
    """Certify capital from confirmed fills, live reservations and close proof.

    `open_orders` must be a fresh authoritative account snapshot limited to the
    two tokens in `market`. The caller separately owns feed/read freshness and
    must explicitly certify both market and order state as reliable.
    """
    if not isinstance(market, MarketMeta) or not isinstance(processor, UserEventProcessor):
        return _projection_failure("INVALID_INPUT")
    if (market_state_reliable is not True or order_state_reliable is not True):
        return _projection_failure("UNRELIABLE_STATE")
    if not isinstance(run_order_ids, set) or not isinstance(open_orders, list):
        return _projection_failure("INVALID_INPUT")
    if (not market.condition_id or not market.yes.token_id or not market.no.token_id
            or market.yes.token_id == market.no.token_id):
        return _projection_failure("INVALID_MARKET")
    if any(not isinstance(oid, str) or not oid for oid in run_order_ids):
        return _projection_failure("INVALID_SCOPE")

    tokens = {market.yes.token_id, market.no.token_id}
    registered: dict[str, tuple[Decimal, Decimal, str]] = {}
    for order_id in run_order_ids:
        row = processor.owned_record(order_id)
        if row is None:
            return _projection_failure("UNKNOWN_OWNED_ORDER", (order_id,))
        if row.get("condition_id") != market.condition_id:
            return _projection_failure("FOREIGN_CONDITION", (order_id,))
        token_id = row.get("token_id")
        if token_id not in tokens:
            return _projection_failure("FOREIGN_TOKEN", (order_id,))
        if row.get("side") != Side.BUY.value:
            return _projection_failure("NON_BUY_ORDER", (order_id,))
        submitted_qty = _decimal(row.get("submitted_qty"))
        submitted_price = _decimal(row.get("submitted_price"))
        if (submitted_qty is None or submitted_qty <= _ZERO
                or submitted_price is None or not _ZERO < submitted_price < _ONE):
            return _projection_failure("INVALID_SUBMITTED_ECONOMICS", (order_id,))
        registered[order_id] = (submitted_qty, submitted_price, str(token_id))

    failure = _validate_processor_evidence(market, processor, run_order_ids)
    if failure is not None:
        return _projection_failure(*failure)

    # snapshot_inventory remains the authoritative accounting projection and
    # validates market identity/economics. Decimal money is then accumulated
    # from its same accepted confirmed-fill events to avoid float-sum artifacts.
    try:
        inventory = snapshot_inventory(market, processor, set(run_order_ids))
    except (TypeError, ValueError, OverflowError):
        return _projection_failure("INVALID_INVENTORY")

    open_by_id: dict[str, OpenOrder] = {}
    for order in open_orders:
        if not isinstance(order, OpenOrder):
            return _projection_failure("MALFORMED_OPEN_ORDER")
        if not isinstance(order.order_id, str) or not order.order_id or order.order_id in open_by_id:
            return _projection_failure("DUPLICATE_OR_INVALID_OPEN_ORDER", (str(order.order_id),))
        if order.token_id not in tokens:
            return _projection_failure("OPEN_ORDER_OUTSIDE_MARKET", (order.order_id,))
        if order.order_id not in registered:
            return _projection_failure("FOREIGN_OPEN_ORDER", (order.order_id,))
        if order.side is not Side.BUY or order.state not in (OrderState.LIVE, OrderState.PARTIALLY_FILLED):
            return _projection_failure("INVALID_OPEN_ORDER_STATE", (order.order_id,))
        price = _decimal(order.price)
        remaining = _decimal(order.size)
        if (price is None or not _ZERO < price < _ONE
                or remaining is None or remaining <= _ZERO):
            return _projection_failure("INVALID_OPEN_ORDER_ECONOMICS", (order.order_id,))
        submitted_qty, submitted_price, token_id = registered[order.order_id]
        if order.token_id != token_id:
            return _projection_failure("OPEN_ORDER_IDENTITY_MISMATCH", (order.order_id,))
        if not _within_tolerance(price, submitted_price, _ORDER_PRICE_TOLERANCE):
            return _projection_failure("OPEN_ORDER_PRICE_MISMATCH", (order.order_id,))
        confirmed_qty = _confirmed_qty(processor, order.order_id)
        if confirmed_qty is None:
            return _projection_failure("INVALID_FILL_ECONOMICS", (order.order_id,))
        expected_remaining = submitted_qty - confirmed_qty
        if (expected_remaining <= _ZERO
                or not _within_tolerance(remaining, expected_remaining, _ORDER_QTY_TOLERANCE)):
            return _projection_failure("OPEN_ORDER_CHECKSUM_MISMATCH", (order.order_id,))
        open_by_id[order.order_id] = order

    close_ids: set[str] = set()
    if close_evidence is not None:
        evidence_result = _validate_close_evidence(
            market, processor, run_order_ids, open_by_id, close_evidence)
        if isinstance(evidence_result, tuple):
            return _projection_failure(*evidence_result)
        close_ids = evidence_result

    missing = run_order_ids - set(open_by_id) - close_ids
    if missing:
        return _projection_failure("UNPROVED_ORDER_ABSENCE", tuple(sorted(missing)))

    try:
        with localcontext() as context:
            context.prec = 64
            inventory_cost = _confirmed_cost(processor, run_order_ids)
            reserved_cash = sum(
                (_decimal(order.size) * registered[order.order_id][1]
                 for order in open_by_id.values()), _ZERO
            )
            free_cash = CAPITAL - inventory_cost - reserved_cash
    except (DecimalException, TypeError, ValueError, OverflowError):
        return _projection_failure("INVALID_CAPITAL_ARITHMETIC")
    if inventory_cost < _ZERO or reserved_cash < _ZERO:
        return _projection_failure("INVALID_CAPITAL_ARITHMETIC")
    if free_cash < _ZERO:
        return _projection_failure("OVERCOMMITTED")

    snapshot = RDPCapitalSnapshot(
        capital=CAPITAL,
        inventory_cost=inventory_cost,
        reserved_cash=reserved_cash,
        free_cash=free_cash,
        inventory=inventory,
        run_order_ids=tuple(sorted(run_order_ids)),
        resting_order_ids=tuple(sorted(open_by_id)),
    )
    return RDPCapitalProjection(snapshot, None)


def evaluate_rdp_capital_gate(
    mode: RDPMode | str,
    candidate: RDPQuoteDecision,
    projection: RDPCapitalProjection,
) -> RDPCapitalAdmission:
    """Evaluate capital only; this result grants no execution authority."""
    parsed_mode = _mode(mode)
    protected = NORMAL_REPAIR_RESERVE if parsed_mode is RDPMode.NORMAL else _ZERO
    if parsed_mode is None:
        return _admission_failure("INVALID_MODE", _ZERO)
    if (not isinstance(projection, RDPCapitalProjection) or not projection.certified
            or projection.snapshot is None):
        return _admission_failure("UNCERTIFIED_CAPITAL", protected)
    snapshot = projection.snapshot
    if not _valid_snapshot(snapshot):
        return _admission_failure("INVALID_CAPITAL_SNAPSHOT", protected)
    if not isinstance(candidate, RDPQuoteDecision) or candidate.reason is not None:
        return _admission_failure("NO_CANDIDATE", protected)
    if candidate.post_only_required is not True:
        return _admission_failure("POST_ONLY_REQUIRED", protected)
    if not isinstance(candidate.quotes, tuple) or not candidate.quotes:
        return _admission_failure("NO_CANDIDATE", protected)

    inventory = snapshot.inventory
    delta = _decimal(inventory.delta)
    qy = _decimal(inventory.qy)
    qn = _decimal(inventory.qn)
    try:
        delta_float, qy_float, qn_float = (float(inventory.delta), float(inventory.qy),
                                           float(inventory.qn))
    except (OverflowError, TypeError, ValueError):
        return _admission_failure("INVALID_INVENTORY", protected)
    if (delta is None or qy is None or qn is None
            or not all(math.isfinite(value) for value in (delta_float, qy_float, qn_float))
            or delta_float != qy_float - qn_float):
        return _admission_failure("INVALID_INVENTORY", protected)
    if parsed_mode is RDPMode.NORMAL and abs(delta) >= Decimal("5"):
        return _admission_failure("MODE_INVENTORY_MISMATCH", protected)
    if parsed_mode is RDPMode.REBALANCE and abs(delta) < Decimal("5"):
        return _admission_failure("MODE_INVENTORY_MISMATCH", protected)

    tokens = {inventory.yes_token_id, inventory.no_token_id}
    notional = _ZERO
    try:
        with localcontext() as context:
            context.prec = 64
            for quote in candidate.quotes:
                if not isinstance(quote, Quote):
                    return _admission_failure("MALFORMED_CANDIDATE", protected)
                price = _decimal(quote.price)
                quantity = _decimal(quote.size)
                if (quote.token_id not in tokens or quote.side is not Side.BUY
                        or price is None or not _ZERO < price < _ONE
                        or quantity is None or quantity <= _ZERO):
                    return _admission_failure("INVALID_CANDIDATE_ECONOMICS", protected)
                notional += price * quantity
            free_after = snapshot.free_cash - notional
            committed_after = snapshot.inventory_cost + snapshot.reserved_cash + notional
    except DecimalException:
        return _admission_failure("INVALID_CANDIDATE_ECONOMICS", protected)
    if snapshot.free_cash < notional:
        return _admission_result(False, "INSUFFICIENT_FREE_CASH", notional,
                                 free_after, committed_after, protected)
    if committed_after > snapshot.capital:
        return _admission_result(False, "CAPITAL_LIMIT_EXCEEDED", notional,
                                 free_after, committed_after, protected)
    if parsed_mode is RDPMode.NORMAL and free_after < NORMAL_REPAIR_RESERVE:
        return _admission_result(False, "NORMAL_REPAIR_RESERVE", notional,
                                 free_after, committed_after, protected)
    return _admission_result(True, None, notional, free_after, committed_after, protected)


def _validate_processor_evidence(
    market: MarketMeta, processor: UserEventProcessor, order_ids: set[str],
) -> tuple[str, tuple[str, ...]] | None:
    for order_id in order_ids:
        if order_id in processor.quarantined_orders or processor.has_failed_fill(order_id):
            return "FAILED_OR_QUARANTINED_EVIDENCE", (order_id,)
        if processor.has_unresolved_fill_key(order_id):
            return "UNRESOLVED_EVIDENCE", (order_id,)
    for trade_id, order_id in processor.applied_fill_keys:
        if order_id in order_ids and (trade_id, order_id) not in processor.fill_records:
            return "UNADMITTED_FILL", (order_id,)
    for key, event in processor.fill_records.items():
        trade_id, order_id = key
        if order_id not in order_ids:
            continue
        if event.status in (TradeState.MATCHED, TradeState.MINED, TradeState.RETRYING):
            return "PENDING_FILL", (order_id,)
        if event.status is TradeState.FAILED:
            return "FAILED_FILL", (order_id,)
        if event.status is not TradeState.CONFIRMED:
            return "INVALID_FILL_STATE", (order_id,)
        if key not in processor.applied_fill_keys:
            return "UNADMITTED_FILL", (order_id,)
        if key in processor.quarantined_fill_keys:
            return "UNRESOLVED_EVIDENCE", (order_id,)
        if (event.trade_id != trade_id or event.maker_order_id != order_id
                or event.market_id not in (None, market.condition_id)):
            return "FILL_IDENTITY_MISMATCH", (order_id,)
    return None


def _validate_close_evidence(
    market: MarketMeta,
    processor: UserEventProcessor,
    run_ids: set[str],
    open_by_id: dict[str, OpenOrder],
    evidence: RDPCloseResult,
) -> set[str] | tuple[str, tuple[str, ...]]:
    if (not isinstance(evidence, RDPCloseResult) or evidence.complete is not True
            or evidence.reason is not None or evidence.inventory is None
            or evidence.affected_order_ids):
        return "INCOMPLETE_CLOSE_EVIDENCE", ()
    if not isinstance(evidence.inventory, RDPInventorySnapshot):
        return "INVALID_CLOSE_EVIDENCE", ()
    outcomes = evidence.reconciliation_outcomes
    if not isinstance(outcomes, tuple):
        return "INVALID_CLOSE_EVIDENCE", ()
    ids: list[str] = []
    for pair in outcomes:
        if (not isinstance(pair, tuple) or len(pair) != 2
                or not isinstance(pair[0], str) or not pair[0]
                or pair[1] is not ReconciliationOutcome.COMPLETE):
            return "INCOMPLETE_CLOSE_EVIDENCE", ()
        ids.append(pair[0])
    covered = set(ids)
    if len(ids) != len(covered) or not covered.issubset(run_ids):
        return "INVALID_CLOSE_SCOPE", tuple(sorted(covered - run_ids))
    overlap = covered & set(open_by_id)
    if overlap:
        return "CLOSED_ORDER_STILL_OPEN", tuple(sorted(overlap))
    # A prior close result is not current authority if this processor has
    # since observed the same owned order as live again. Inspect StateStore's
    # existing local order map without consuming or mutating that evidence.
    local_orders = getattr(processor._store, "orders", None)
    if not isinstance(local_orders, dict):
        return "INVALID_LOCAL_ORDER_STATE", tuple(sorted(covered))
    locally_open = covered & set(local_orders)
    if locally_open:
        return "CLOSED_ORDER_LOCAL_OPEN", tuple(sorted(locally_open))
    try:
        current = snapshot_inventory(market, processor, covered)
    except (TypeError, ValueError, OverflowError):
        return "INVALID_CLOSE_EVIDENCE", tuple(sorted(covered))
    if evidence.inventory != current:
        return "STALE_CLOSE_EVIDENCE", tuple(sorted(covered))
    return covered


def _confirmed_qty(processor: UserEventProcessor, order_id: str) -> Decimal | None:
    total = _ZERO
    try:
        with localcontext() as context:
            context.prec = 64
            for event in processor.economic_fills_for_order(order_id):
                if event.status is TradeState.CONFIRMED:
                    qty = _decimal(event.size)
                    if qty is None or qty <= _ZERO:
                        return None
                    total += qty
    except (DecimalException, TypeError, ValueError):
        return None
    return total


def _confirmed_cost(processor: UserEventProcessor, order_ids: set[str]) -> Decimal:
    total = _ZERO
    for event in processor.economic_fills_for_orders(order_ids):
        if event.status is not TradeState.CONFIRMED:
            continue
        quantity = _decimal(event.size)
        price = _decimal(event.price)
        if quantity is None or quantity <= _ZERO or price is None or not _ZERO < price < _ONE:
            raise ValueError("invalid confirmed fill for capital projection")
        total += quantity * price
    return total


def _within_tolerance(left: Decimal, right: Decimal, tolerance: float) -> bool:
    try:
        left_float, right_float = float(left), float(right)
    except (OverflowError, ValueError):
        return False
    return (math.isfinite(left_float) and math.isfinite(right_float)
            and abs(left_float - right_float) <= tolerance)


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (Real, Decimal, str)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (DecimalException, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _mode(value: RDPMode | str) -> RDPMode | None:
    if isinstance(value, RDPMode):
        return value
    if not isinstance(value, str):
        return None
    try:
        return RDPMode(value)
    except ValueError:
        return None


def _valid_snapshot(snapshot: RDPCapitalSnapshot) -> bool:
    values = (snapshot.capital, snapshot.inventory_cost,
              snapshot.reserved_cash, snapshot.free_cash)
    if any(not isinstance(value, Decimal) or not value.is_finite() for value in values):
        return False
    try:
        with localcontext() as context:
            context.prec = 64
            if (snapshot.capital != CAPITAL or snapshot.inventory_cost < _ZERO
                    or snapshot.reserved_cash < _ZERO or snapshot.free_cash < _ZERO
                    or snapshot.inventory_cost + snapshot.reserved_cash
                    + snapshot.free_cash != snapshot.capital):
                return False
    except DecimalException:
        return False
    return isinstance(snapshot.inventory, RDPInventorySnapshot)


def _projection_failure(reason: str, affected: Iterable[str] = ()) -> RDPCapitalProjection:
    return RDPCapitalProjection(None, reason, tuple(sorted(set(affected))))


def _admission_failure(reason: str, protected: Decimal) -> RDPCapitalAdmission:
    return RDPCapitalAdmission(False, reason, None, None, None, protected)


def _admission_result(
    admitted: bool, reason: str | None, notional: Decimal,
    free_after: Decimal, committed: Decimal, protected: Decimal,
) -> RDPCapitalAdmission:
    return RDPCapitalAdmission(admitted, reason, notional, free_after, committed, protected)
