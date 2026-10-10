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


@dataclass(frozen=True, slots=True)
class RDPInitialBackingResult:
    """Read-only evidence for backing an initially empty $30 RDP run."""

    eligible: bool
    reason: str | None
    capital: Decimal
    account_buy_commitment: Decimal | None
    observed_cash_backing: Decimal | None
    observed_allowance_backing: Decimal | None
    read_age_s: float | None
    selected_state_empty: bool


@dataclass(frozen=True, slots=True)
class RDPCurrentBackingResult:
    """Physical cash/allowance support for a factual post-cycle candidate."""

    eligible: bool
    reason: str | None
    account_buy_commitment: Decimal | None
    observed_cash_backing: Decimal | None
    observed_allowance_backing: Decimal | None
    required_backing: Decimal
    read_age_s: float | None
    selected_inventory_matches: bool


def evaluate_initial_rdp_backing(
    bootstrap: object,
    account: object,
    *,
    now: float,
    max_age_s: float,
) -> RDPInitialBackingResult:
    """Check startup cash/allowance backing without adopting account state."""
    now_value = _finite_timestamp_decimal(now)
    age_limit = _finite_timestamp_decimal(max_age_s)
    if now_value is None or age_limit is None or age_limit <= 0:
        return _initial_backing_failure("INVALID_AGE_LIMIT")

    market = getattr(bootstrap, "market", None)
    condition_id = getattr(market, "condition_id", None)
    end_epoch = _finite_timestamp_decimal(getattr(bootstrap, "end_epoch", None))
    tokens = getattr(market, "tokens", None)
    if (not isinstance(condition_id, str) or not condition_id.strip()
            or end_epoch is None or not isinstance(tokens, tuple) or len(tokens) != 2):
        return _initial_backing_failure("INVALID_MARKET_SCOPE")
    token_outcomes: dict[str, str] = {}
    for token in tokens:
        token_id = getattr(token, "token_id", None)
        outcome = getattr(token, "outcome", None)
        if (not isinstance(token_id, str) or not token_id.strip()
                or not isinstance(outcome, str) or not outcome.strip()
                or token_id in token_outcomes):
            return _initial_backing_failure("INVALID_MARKET_SCOPE")
        token_outcomes[token_id] = outcome

    if getattr(account, "condition_id", None) != condition_id:
        return _initial_backing_failure("ACCOUNT_SCOPE_MISMATCH")
    orders = getattr(account, "all_open_orders", None)
    order_count = getattr(account, "total_open_order_count", None)
    selected_orders = getattr(account, "selected_open_orders", None)
    positions = getattr(account, "selected_positions", None)
    spenders = getattr(account, "required_spenders", None)
    if (not isinstance(orders, tuple) or isinstance(order_count, bool)
            or not isinstance(order_count, int) or order_count != len(orders)
            or not isinstance(selected_orders, tuple) or not isinstance(positions, tuple)
            or not isinstance(spenders, tuple) or not spenders
            or any(not isinstance(item, str) or not item.strip() for item in spenders)):
        return _initial_backing_failure("INCOMPLETE_ACCOUNT_EVIDENCE")
    from web3 import Web3

    normalized_spenders = tuple(item.strip().lower() for item in spenders)
    if (len(set(normalized_spenders)) != len(normalized_spenders)
            or any(not Web3.is_address(item) for item in spenders)):
        return _initial_backing_failure("INVALID_SPENDER_EVIDENCE")

    position_map: dict[str, str] = {}
    selected_state_empty = True
    for position in positions:
        token_id = getattr(position, "token_id", None)
        outcome = getattr(position, "outcome", None)
        quantity = _initial_decimal(getattr(position, "quantity", None))
        average_price = _initial_decimal(getattr(position, "average_price", None))
        if (not isinstance(token_id, str) or token_id not in token_outcomes
                or token_id in position_map or outcome != token_outcomes.get(token_id)
                or quantity is None or quantity < _ZERO
                or average_price is None or not _ZERO <= average_price <= _ONE):
            return _initial_backing_failure("INVALID_SELECTED_POSITION")
        position_map[token_id] = outcome
        if quantity != _ZERO:
            selected_state_empty = False
    if position_map != token_outcomes:
        return _initial_backing_failure("INVALID_SELECTED_POSITION_SCOPE")

    receipt_times = tuple(_finite_timestamp_decimal(getattr(account, field, None)) for field in (
        "orders_receipt_ts", "positions_receipt_ts", "collateral_receipt_ts"))
    if (any(value is None or value < 0 for value in receipt_times)
            or tuple(sorted(receipt_times)) != receipt_times
            or any(value > now_value for value in receipt_times)):
        return _initial_backing_failure("INVALID_READ_TIMESTAMPS")
    try:
        read_age = max(_exact_decimal_sum((now_value, value.copy_negate()))
                       for value in receipt_times if value is not None)
        read_age_s = float(read_age)
    except (DecimalException, ValueError, OverflowError):
        return _initial_backing_failure("INVALID_READ_TIMESTAMPS")
    if not math.isfinite(read_age_s):
        return _initial_backing_failure("INVALID_READ_TIMESTAMPS")
    if read_age > age_limit:
        return _initial_backing_failure("STALE_ACCOUNT_EVIDENCE", read_age_s=read_age_s)
    if now_value >= end_epoch or any(value >= end_epoch for value in receipt_times if value is not None):
        return _initial_backing_failure("MARKET_EXPIRED", read_age_s=read_age_s)

    account_ids: set[str] = set()
    commitment_terms: list[Decimal] = []
    selected_from_all = []
    balance = _initial_decimal(getattr(account, "balance_usd", None))
    allowance = _initial_decimal(getattr(account, "allowance_usd", None))
    if balance is None or balance < _ZERO or allowance is None or allowance < _ZERO:
        return _initial_backing_failure("INVALID_COLLATERAL_EVIDENCE", read_age_s=read_age_s)
    try:
        for order in orders:
            order_id = getattr(order, "order_id", None)
            token_id = getattr(order, "token_id", None)
            side = getattr(order, "side", None)
            price = _initial_decimal(getattr(order, "price", None))
            quantity = _initial_decimal(getattr(order, "remaining_size", None))
            if (not isinstance(order_id, str) or not order_id.strip() or order_id in account_ids
                    or not isinstance(token_id, str) or not token_id.strip()
                    or side not in ("BUY", "SELL") or price is None
                    or not _ZERO < price < _ONE or quantity is None or quantity < _ZERO):
                return _initial_backing_failure("INVALID_ACCOUNT_ORDER", read_age_s=read_age_s)
            account_ids.add(order_id)
            if token_id in token_outcomes:
                selected_from_all.append(order)
            if side == "BUY":
                commitment_terms.append(_exact_decimal_product(price, quantity))
        if len(account_ids) != order_count:
            return _initial_backing_failure("INCOMPLETE_ACCOUNT_EVIDENCE", read_age_s=read_age_s)
        if tuple(selected_from_all) != selected_orders:
            return _initial_backing_failure("INCONSISTENT_SELECTED_ORDERS", read_age_s=read_age_s)
        commitment = _exact_decimal_sum(commitment_terms)
        cash_backing = _exact_decimal_sum((balance, commitment.copy_negate()))
        allowance_backing = _exact_decimal_sum((allowance, commitment.copy_negate()))
    except (DecimalException, ValueError, OverflowError):
        return _initial_backing_failure("INVALID_BACKING_ARITHMETIC", read_age_s=read_age_s)

    if selected_orders:
        selected_state_empty = False
    if not selected_state_empty:
        return _initial_backing_result(
            False, "SELECTED_STATE_NOT_EMPTY", commitment, cash_backing,
            allowance_backing, read_age_s, False)
    if cash_backing < CAPITAL:
        return _initial_backing_result(
            False, "INSUFFICIENT_CASH_BACKING", commitment, cash_backing,
            allowance_backing, read_age_s, True)
    if allowance_backing < CAPITAL:
        return _initial_backing_result(
            False, "INSUFFICIENT_ALLOWANCE_BACKING", commitment, cash_backing,
            allowance_backing, read_age_s, True)
    return _initial_backing_result(
        True, None, commitment, cash_backing, allowance_backing, read_age_s, True)


def evaluate_current_rdp_backing(
    bootstrap: object,
    account: object,
    projection: RDPCapitalProjection,
    mode: RDPMode | str,
    candidate_notional: Decimal,
    run_order_ids: tuple[str, ...],
    *,
    now: float,
    max_age_s: float,
) -> RDPCurrentBackingResult:
    """Check physical account support without replacing model inventory/cost.

    The initial evaluator remains the single validator for account scope,
    spender identity, timestamps, collateral, selected positions and exact
    account-order commitments. Its empty-start rejection is expected here;
    selected positions are checked against confirmed processor inventory below.
    """
    parsed_mode = _mode(mode)
    notional = _decimal(candidate_notional)
    if parsed_mode is None or notional is None or notional < _ZERO:
        return _current_backing_failure("INVALID_CURRENT_BACKING_INPUT")
    if (not isinstance(projection, RDPCapitalProjection) or not projection.certified
            or projection.snapshot is None):
        return _current_backing_failure("UNCERTIFIED_CAPITAL")
    if (not isinstance(run_order_ids, tuple)
            or any(not isinstance(order_id, str) or not order_id for order_id in run_order_ids)
            or len(set(run_order_ids)) != len(run_order_ids)):
        return _current_backing_failure("INVALID_RUN_SCOPE")
    if projection.snapshot.run_order_ids != tuple(sorted(run_order_ids)):
        return _current_backing_failure("RUN_SCOPE_MISMATCH")

    initial = evaluate_initial_rdp_backing(
        bootstrap, account, now=now, max_age_s=max_age_s,
    )
    accepted_empty_reasons = {
        None, "SELECTED_STATE_NOT_EMPTY", "INSUFFICIENT_CASH_BACKING",
        "INSUFFICIENT_ALLOWANCE_BACKING",
    }
    if initial.reason not in accepted_empty_reasons:
        return _current_backing_failure(
            initial.reason or "CURRENT_ACCOUNT_EVIDENCE_REJECTED",
            read_age_s=initial.read_age_s,
        )
    if (initial.account_buy_commitment is None
            or initial.observed_cash_backing is None
            or initial.observed_allowance_backing is None
            or initial.read_age_s is None):
        return _current_backing_failure("INCOMPLETE_CURRENT_ACCOUNT_EVIDENCE")

    inventory = projection.snapshot.inventory
    positions = getattr(account, "selected_positions", None)
    token_quantities: dict[str, Decimal] = {}
    try:
        for position in positions:
            token_id = getattr(position, "token_id", None)
            quantity = _decimal(getattr(position, "quantity", None))
            if (not isinstance(token_id, str) or token_id in token_quantities
                    or quantity is None or quantity < _ZERO):
                return _current_backing_failure(
                    "INVALID_SELECTED_POSITION", initial.read_age_s,
                )
            token_quantities[token_id] = quantity
        expected = {
            inventory.yes_token_id: _decimal(inventory.qy),
            inventory.no_token_id: _decimal(inventory.qn),
        }
        if (set(token_quantities) != set(expected)
                or any(value is None for value in expected.values())):
            return _current_backing_failure(
                "INVALID_SELECTED_POSITION_SCOPE", initial.read_age_s,
            )
        # The existing processor derives quantities from float-based user
        # events; allow one micro-share of serialization noise when matching
        # those confirmed quantities to exact Decimal account reads.
        tolerance = Decimal("0.000001")
        if any(abs(token_quantities[token] - expected[token]) > tolerance
               for token in expected):
            return _current_backing_failure(
                "SELECTED_POSITION_INVENTORY_MISMATCH", initial.read_age_s,
            )
    except (DecimalException, TypeError, ValueError, OverflowError):
        return _current_backing_failure("INVALID_SELECTED_POSITION", initial.read_age_s)

    all_orders = getattr(account, "all_open_orders", None)
    if not isinstance(all_orders, tuple):
        return _current_backing_failure("INCOMPLETE_ACCOUNT_EVIDENCE", initial.read_age_s)
    for order in all_orders:
        order_id = getattr(order, "order_id", None)
        if order_id in run_order_ids:
            return _current_backing_failure(
                "CLOSED_RUN_ORDER_REAPPEARED", initial.read_age_s,
            )

    reserve = NORMAL_REPAIR_RESERVE if parsed_mode is RDPMode.NORMAL else _ZERO
    try:
        with localcontext() as context:
            context.prec = 64
            required = notional + reserve
    except DecimalException:
        return _current_backing_failure("INVALID_BACKING_ARITHMETIC", initial.read_age_s)
    if initial.observed_cash_backing < required:
        return _current_backing_result(
            False, "INSUFFICIENT_CURRENT_CASH", initial, required,
        )
    if initial.observed_allowance_backing < required:
        return _current_backing_result(
            False, "INSUFFICIENT_CURRENT_ALLOWANCE", initial, required,
        )
    return _current_backing_result(True, None, initial, required)


def _current_backing_result(
    eligible: bool, reason: str | None, initial: RDPInitialBackingResult,
    required: Decimal,
) -> RDPCurrentBackingResult:
    return RDPCurrentBackingResult(
        eligible, reason, initial.account_buy_commitment,
        initial.observed_cash_backing, initial.observed_allowance_backing,
        required, initial.read_age_s, True,
    )


def _current_backing_failure(
    reason: str, read_age_s: float | None = None,
) -> RDPCurrentBackingResult:
    return RDPCurrentBackingResult(
        False, reason, None, None, None, _ZERO, read_age_s, False,
    )


def _finite_timestamp_decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (DecimalException, TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed.is_finite() else None


def _initial_decimal(value: object) -> Decimal | None:
    if not isinstance(value, Decimal):
        return None
    try:
        parsed = value
    except (DecimalException, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _decimal_coefficient(value: Decimal) -> tuple[int, int]:
    sign, digits, exponent = value.as_tuple()
    if len(digits) > 256:
        raise ValueError("backing evidence precision is excessive")
    coefficient = int("".join(str(digit) for digit in digits) or "0")
    return (-coefficient if sign else coefficient), exponent


def _decimal_from_coefficient(coefficient: int, exponent: int) -> Decimal:
    sign = 1 if coefficient < 0 else 0
    digits = tuple(int(digit) for digit in str(abs(coefficient)))
    return Decimal((sign, digits, exponent))


def _exact_decimal_product(left: Decimal, right: Decimal) -> Decimal:
    left_coefficient, left_exponent = _decimal_coefficient(left)
    right_coefficient, right_exponent = _decimal_coefficient(right)
    return _decimal_from_coefficient(left_coefficient * right_coefficient,
                                     left_exponent + right_exponent)


def _exact_decimal_sum(values: Iterable[Decimal]) -> Decimal:
    parts = tuple(_decimal_coefficient(value) for value in values)
    if not parts:
        return _ZERO
    exponent = min(item[1] for item in parts)
    if max(item[1] for item in parts) - exponent > 4096:
        raise ValueError("backing evidence exponent range is excessive")
    coefficient = sum(value * 10 ** (item_exponent - exponent)
                      for value, item_exponent in parts)
    return _decimal_from_coefficient(coefficient, exponent)


def _initial_backing_result(
    eligible: bool,
    reason: str | None,
    commitment: Decimal,
    cash_backing: Decimal,
    allowance_backing: Decimal,
    read_age: float,
    selected_state_empty: bool,
) -> RDPInitialBackingResult:
    return RDPInitialBackingResult(eligible, reason, CAPITAL, commitment, cash_backing,
                                  allowance_backing, read_age, selected_state_empty)


def _initial_backing_failure(
    reason: str, *, read_age_s: float | None = None,
) -> RDPInitialBackingResult:
    return RDPInitialBackingResult(False, reason, CAPITAL, None, None, None, read_age_s, False)


def project_rdp_capital(
    market: MarketMeta,
    processor: UserEventProcessor,
    run_order_ids: set[str],
    open_orders: list[OpenOrder],
    *,
    market_state_reliable: bool,
    order_state_reliable: bool,
    close_evidence: RDPCloseResult | tuple[RDPCloseResult, ...] | None = None,
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
        proofs = (close_evidence,) if isinstance(close_evidence, RDPCloseResult) else close_evidence
        if not isinstance(proofs, tuple) or not proofs:
            return _projection_failure("INVALID_CLOSE_EVIDENCE")
        for proof in proofs:
            evidence_result = _validate_close_evidence(
                market, processor, run_order_ids, open_by_id, proof)
            if isinstance(evidence_result, tuple):
                return _projection_failure(*evidence_result)
            if close_ids & evidence_result:
                return _projection_failure(
                    "CONTRADICTORY_CLOSE_PROOFS", tuple(sorted(close_ids & evidence_result)),
                )
            close_ids.update(evidence_result)

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
