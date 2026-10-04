"""MakerInventory 1.0: a pure, single-market, BUY-only inventory controller.

The caller supplies authoritative inventory, cash, books, and order lifecycle.
This module does not fetch data, retain fill state, or submit/cancel orders.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from enum import Enum

from polymaker.domain import Side

type Number = Decimal | int | float | str
ZERO = Decimal("0")
ONE = Decimal("1")


def _decimal(value: Number) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("MI numeric inputs must be finite")
    return result


class Outcome(str, Enum):
    YES = "YES"
    NO = "NO"


class OrderLifecycle(str, Enum):
    SUBMIT_PENDING = "SUBMIT_PENDING"
    WORKING = "WORKING"
    PARTIAL_REMAINDER_WORKING = "PARTIAL_REMAINDER_WORKING"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCEL_PENDING = "CANCEL_PENDING"


class Action(str, Enum):
    WAIT = "WAIT"
    PLACE_BUY_YES = "PLACE_BUY_YES"
    PLACE_BUY_NO = "PLACE_BUY_NO"
    KEEP_EXISTING_ORDER = "KEEP_EXISTING_ORDER"
    CANCEL_EXISTING_ORDER = "CANCEL_EXISTING_ORDER"
    WAIT_FOR_ORDER_LIFECYCLE = "WAIT_FOR_ORDER_LIFECYCLE"


@dataclass(frozen=True, slots=True)
class MakerInventoryConfig:
    target_scale: Decimal = Decimal("50")
    max_abs_inventory: Decimal = Decimal("25")
    min_adjustment_qty: Decimal = Decimal("5")
    market_duration_s: Decimal = Decimal("300")
    final_no_buy_seconds: Decimal = Decimal("15")

    def __post_init__(self) -> None:
        if (
            any(
                _decimal(v) <= ZERO
                for v in (
                    self.target_scale,
                    self.max_abs_inventory,
                    self.min_adjustment_qty,
                    self.market_duration_s,
                    self.final_no_buy_seconds,
                )
            )
            or _decimal(self.max_abs_inventory) > Decimal("25")
            or _decimal(self.final_no_buy_seconds) >= _decimal(self.market_duration_s)
        ):
            raise ValueError("invalid MakerInventory config")


@dataclass(frozen=True, slots=True)
class MakerInventoryBook:
    best_bid: Number | None
    best_ask: Number | None


@dataclass(frozen=True, slots=True)
class MakerInventoryOrder:
    outcome: Outcome
    price: Number
    remaining_qty: Number
    reserved_cash: Number
    lifecycle: OrderLifecycle


@dataclass(frozen=True, slots=True)
class MakerInventoryState:
    """The caller's current single adjustment-order lifecycle, if any."""

    order: MakerInventoryOrder | None = None


@dataclass(frozen=True, slots=True)
class MakerInventorySnapshot:
    yes: MakerInventoryBook
    no: MakerInventoryBook
    confirmed_yes_qty: Number
    confirmed_no_qty: Number
    cash_available_for_new_order: Number
    elapsed_s: Number
    data_valid: bool
    tick_size: Number
    venue_min_order_size: Number
    quantity_step: Number


@dataclass(frozen=True, slots=True)
class MakerInventoryDecision:
    action: Action
    reason: str
    outcome: Outcome | None
    side: Side | None
    price: Decimal | None
    qty: Decimal | None
    p: Decimal | None
    n_target: Decimal | None
    n_actual: Decimal
    q_pair: Decimal
    q_residual: Decimal
    delta: Decimal | None
    tracking_error: Decimal | None
    potential_inventory_min: Decimal
    potential_inventory_max: Decimal
    reducing_old_exposure: bool = False


def _floor_step(qty: Decimal, step: Decimal) -> Decimal:
    return (qty / step).to_integral_value(rounding=ROUND_FLOOR) * step


def _valid_book(book: MakerInventoryBook, tick: Decimal) -> bool:
    if book.best_bid is None or book.best_ask is None:
        return False
    bid, ask = _decimal(book.best_bid), _decimal(book.best_ask)
    return ZERO < bid < ask < ONE and bid % tick == ZERO and ask % tick == ZERO


def evaluate(
    snapshot: MakerInventorySnapshot,
    state: MakerInventoryState | None = None,
    config: MakerInventoryConfig | None = None,
) -> MakerInventoryDecision:
    """Evaluate one market snapshot without side effects or order submission."""
    s, c, order = snapshot, config or MakerInventoryConfig(), state.order if state else None
    yes_qty = _decimal(s.confirmed_yes_qty)
    no_qty = _decimal(s.confirmed_no_qty)
    cash = _decimal(s.cash_available_for_new_order)
    elapsed = _decimal(s.elapsed_s)
    tick = _decimal(s.tick_size)
    venue_min = _decimal(s.venue_min_order_size)
    step = _decimal(s.quantity_step)
    max_inv = _decimal(c.max_abs_inventory)
    threshold = _decimal(c.min_adjustment_qty)
    min_qty = max(threshold, venue_min)
    if (
        min(yes_qty, no_qty, cash, elapsed) < ZERO
        or tick <= ZERO
        or venue_min <= ZERO
        or step <= ZERO
    ):
        raise ValueError("invalid MI snapshot quantities or venue metadata")

    actual = yes_qty - no_qty
    pair = min(yes_qty, no_qty)
    potential_min = potential_max = actual
    if order is not None:
        remaining = _decimal(order.remaining_qty)
        order_price = _decimal(order.price)
        reserved = _decimal(order.reserved_cash)
        if remaining < ZERO or reserved < ZERO or not ZERO < order_price < ONE:
            raise ValueError("invalid MI order state")
        if order.outcome is Outcome.YES:
            potential_max += remaining
        elif order.outcome is Outcome.NO:
            potential_min -= remaining
        else:
            raise ValueError("invalid MI order outcome")

    p: Decimal | None = None
    target: Decimal | None = None
    delta: Decimal | None = None
    valid = s.data_valid and _valid_book(s.yes, tick) and _valid_book(s.no, tick)
    if valid:
        assert (
            s.yes.best_bid is not None
            and s.yes.best_ask is not None
            and s.no.best_bid is not None
            and s.no.best_ask is not None
        )
        mid_yes = (_decimal(s.yes.best_bid) + _decimal(s.yes.best_ask)) / 2
        mid_no = (_decimal(s.no.best_bid) + _decimal(s.no.best_ask)) / 2
        p = (mid_yes + ONE - mid_no) / 2
        target = max(-max_inv, min(max_inv, _decimal(c.target_scale) * (p - Decimal("0.5"))))
        delta = target - actual

    def decision(
        action: Action,
        reason: str,
        *,
        outcome: Outcome | None = None,
        price: Decimal | None = None,
        qty: Decimal | None = None,
        lower: Decimal | None = None,
        upper: Decimal | None = None,
        reducing: bool = False,
    ) -> MakerInventoryDecision:
        return MakerInventoryDecision(
            action=action,
            reason=reason,
            outcome=outcome,
            side=Side.BUY if action in (Action.PLACE_BUY_YES, Action.PLACE_BUY_NO) else None,
            price=price,
            qty=qty,
            p=p,
            n_target=target,
            n_actual=actual,
            q_pair=pair,
            q_residual=abs(actual),
            delta=delta,
            tracking_error=abs(delta) if delta is not None else None,
            potential_inventory_min=potential_min if lower is None else lower,
            potential_inventory_max=potential_max if upper is None else upper,
            reducing_old_exposure=reducing,
        )

    if order is not None and order.lifecycle in (
        OrderLifecycle.CANCEL_REQUESTED,
        OrderLifecycle.CANCEL_PENDING,
    ):
        return decision(Action.WAIT_FOR_ORDER_LIFECYCLE, order.lifecycle.value)

    if not valid:
        return decision(Action.CANCEL_EXISTING_ORDER if order else Action.WAIT, "DATA_INVALID")
    if elapsed >= _decimal(c.market_duration_s) - _decimal(c.final_no_buy_seconds):
        return decision(
            Action.CANCEL_EXISTING_ORDER if order else Action.WAIT, "FINAL_NO_BUY_WINDOW"
        )
    if order is None and not -max_inv <= actual <= max_inv:
        return decision(Action.WAIT, "CONFIRMED_INVENTORY_OUT_OF_BOUNDS")

    assert delta is not None
    desired_outcome = (
        Outcome.YES if delta >= threshold else Outcome.NO if delta <= -threshold else None
    )
    if order is not None:
        if potential_min < -max_inv or potential_max > max_inv:
            return decision(Action.CANCEL_EXISTING_ORDER, "POTENTIAL_INVENTORY_BOUND")
        if _decimal(order.reserved_cash) + cash < (
            _decimal(order.price) * _decimal(order.remaining_qty)
        ):
            return decision(Action.CANCEL_EXISTING_ORDER, "WORKING_ORDER_CASH")
        if desired_outcome is None:
            return decision(Action.CANCEL_EXISTING_ORDER, "ORDER_NO_LONGER_NEEDED")
        if order.outcome is not desired_outcome:
            return decision(Action.CANCEL_EXISTING_ORDER, "DIRECTION_CHANGED")
        book = s.yes if order.outcome is Outcome.YES else s.no
        assert book.best_bid is not None
        if abs(_decimal(order.price) - _decimal(book.best_bid)) >= tick:
            return decision(Action.CANCEL_EXISTING_ORDER, "BEST_BID_CHANGED")
        if order.lifecycle is OrderLifecycle.SUBMIT_PENDING:
            return decision(Action.WAIT_FOR_ORDER_LIFECYCLE, "SUBMIT_PENDING")
        return decision(Action.KEEP_EXISTING_ORDER, "ORDER_STILL_VALID")

    if desired_outcome is None:
        return decision(Action.WAIT, "BELOW_ADJUSTMENT_THRESHOLD")
    book = s.yes if desired_outcome is Outcome.YES else s.no
    assert book.best_bid is not None
    price = _decimal(book.best_bid)
    desired = abs(delta)
    desired_legal = _floor_step(desired, step)
    inventory_capacity = max_inv - actual if desired_outcome is Outcome.YES else max_inv + actual
    cash_capacity = cash / price
    full_flip = (
        actual > ZERO and desired_outcome is Outcome.NO and target is not None and target < ZERO
    ) or (actual < ZERO and desired_outcome is Outcome.YES and target is not None and target > ZERO)
    full_legal = (
        desired_legal >= min_qty
        and desired_legal <= inventory_capacity
        and desired_legal <= cash_capacity
    )
    reduce_fallback = full_flip and not full_legal
    if reduce_fallback:
        desired = min(abs(actual), desired)
    qty = _floor_step(min(desired, inventory_capacity, cash_capacity), step)
    if qty < min_qty:
        reason = (
            "FLIP_REDUCTION_BELOW_MIN"
            if reduce_fallback and desired < min_qty
            else "INSUFFICIENT_SIZE_OR_CASH"
        )
        return decision(Action.WAIT, reason)
    lower = actual if desired_outcome is Outcome.YES else actual - qty
    upper = actual + qty if desired_outcome is Outcome.YES else actual
    reducing_old_exposure = (actual > ZERO and desired_outcome is Outcome.NO and lower >= ZERO) or (
        actual < ZERO and desired_outcome is Outcome.YES and upper <= ZERO
    )
    return decision(
        Action.PLACE_BUY_YES if desired_outcome is Outcome.YES else Action.PLACE_BUY_NO,
        "REDUCE_OLD_EXPOSURE" if reduce_fallback else "TRACK_TARGET",
        outcome=desired_outcome,
        price=price,
        qty=qty,
        lower=lower,
        upper=upper,
        reducing=reducing_old_exposure,
    )
