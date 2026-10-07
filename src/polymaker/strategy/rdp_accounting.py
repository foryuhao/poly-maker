"""Read-only RDP accounting projection over accepted owned maker fills.

The existing UserEventProcessor remains the owner of order registration,
deduplication, lifecycle and quarantine. This module derives a fresh snapshot
for an explicit set of orders and counts confirmed fills only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from numbers import Real
from typing import TYPE_CHECKING

from polymaker.domain import MarketMeta, Side, TradeState

if TYPE_CHECKING:
    from polymaker.state.tracker import TradeEvent, UserEventProcessor


@dataclass(frozen=True, slots=True)
class RDPInventorySnapshot:
    condition_id: str
    yes_token_id: str
    no_token_id: str
    qy: float
    qn: float
    cy: float
    cn: float
    delta: float
    inventory_exposure: float
    c_total: float
    inventory_cost: float
    avg_yes_cost: float | None
    avg_no_cost: float | None
    balanced_qty: float
    residual_yes: float
    residual_no: float
    residual_qty: float
    balanced_yes_cost: float
    balanced_no_cost: float
    balanced_cost: float
    residual_cost: float
    locked_pnl: float
    residual_worst_loss: float
    residual_best_pnl: float
    worst_settlement_pnl: float
    best_settlement_pnl: float

    def market_pnl(self, winning_token_id: str) -> float:
        """Return final PnL for an explicitly supplied resolved winner token."""
        if winning_token_id == self.yes_token_id:
            winning_qty = self.qy
        elif winning_token_id == self.no_token_id:
            winning_qty = self.qn
        else:
            raise ValueError("winning token is outside this market")
        return winning_qty - self.c_total


def snapshot_inventory(
    market: MarketMeta,
    processor: UserEventProcessor,
    owned_order_ids: set[str],
) -> RDPInventorySnapshot:
    """Project confirmed BUY fills for the exact registered order scope."""
    if not isinstance(owned_order_ids, set):
        raise TypeError("owned_order_ids must be an explicit set of order IDs")
    yes_token, no_token = market.yes.token_id, market.no.token_id
    if not yes_token or not no_token or yes_token == no_token:
        raise ValueError("market must identify two distinct outcome tokens")

    for order_id in owned_order_ids:
        if not isinstance(order_id, str) or not order_id:
            raise ValueError("order IDs must be non-empty strings")
        record = processor.owned_record(order_id)
        if record is None:
            raise ValueError(f"unknown owned order: {order_id}")
        if record.get("condition_id") != market.condition_id:
            raise ValueError(f"order belongs to another condition: {order_id}")
        if record.get("token_id") not in (yes_token, no_token):
            raise ValueError(f"order token is outside this market: {order_id}")
        if record.get("side") != Side.BUY.value:
            raise ValueError(f"RDP accounting scope contains a non-BUY order: {order_id}")
        if order_id in processor.quarantined_orders or processor.has_failed_fill(order_id):
            raise ValueError(f"scoped order has failed or quarantined evidence: {order_id}")
        if processor.has_unresolved_fill_key(order_id):
            raise ValueError(f"scoped order has an unresolved fill key: {order_id}")

    # Validate any scoped confirmed record, including a CONFIRMED-only event
    # that the processor has not admitted into applied evidence. Such an event
    # is rejected when malformed and remains excluded from the totals below.
    for (trade_id, order_id), event in processor.fill_records.items():
        if order_id in owned_order_ids and event.status is TradeState.CONFIRMED:
            _validate_confirmed_event(market, event, order_id, processor)

    qy = qn = cy = cn = 0.0
    events = processor.economic_fills_for_orders(owned_order_ids)
    for event in events:
        order_id = event.maker_order_id
        if order_id not in owned_order_ids:
            raise ValueError("fill evidence escaped the explicit owned-order scope")
        if processor.fill_records.get((event.trade_id, order_id)) != event:
            raise ValueError("fill event identity contradicts its processor key")
        if event.status is not TradeState.CONFIRMED:
            continue
        qty, price = _validate_confirmed_event(market, event, order_id, processor)
        cost = qty * price
        if event.token_id == yes_token:
            qy += qty
            cy += cost
        elif event.token_id == no_token:
            qn += qty
            cn += cost
        else:
            raise ValueError("confirmed fill token is outside this market")

    if not all(math.isfinite(value) for value in (qy, qn, cy, cn)):
        raise ValueError("confirmed fill totals exceed finite accounting range")

    delta = qy - qn
    total = cy + cn
    balanced = min(qy, qn)
    avg_yes = cy / qy if qy > 0 else None
    avg_no = cn / qn if qn > 0 else None
    balanced_yes = balanced * avg_yes if avg_yes is not None else 0.0
    balanced_no = balanced * avg_no if avg_no is not None else 0.0
    balanced_cost = balanced_yes + balanced_no
    residual_cost = total - balanced_cost
    return RDPInventorySnapshot(
        condition_id=market.condition_id,
        yes_token_id=yes_token,
        no_token_id=no_token,
        qy=qy,
        qn=qn,
        cy=cy,
        cn=cn,
        delta=delta,
        inventory_exposure=abs(delta),
        c_total=total,
        inventory_cost=total,
        avg_yes_cost=avg_yes,
        avg_no_cost=avg_no,
        balanced_qty=balanced,
        residual_yes=max(delta, 0.0),
        residual_no=max(-delta, 0.0),
        residual_qty=abs(delta),
        balanced_yes_cost=balanced_yes,
        balanced_no_cost=balanced_no,
        balanced_cost=balanced_cost,
        residual_cost=residual_cost,
        locked_pnl=balanced - balanced_cost,
        residual_worst_loss=residual_cost,
        residual_best_pnl=abs(delta) - residual_cost,
        worst_settlement_pnl=balanced - total,
        best_settlement_pnl=max(qy, qn) - total,
    )


def _finite_real(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite number")
    return result


def _finite_positive(value: object, label: str) -> float:
    result = _finite_real(value, label)
    if result <= 0:
        raise ValueError(f"{label} must be positive")
    return result


def _validate_confirmed_event(
    market: MarketMeta, event: TradeEvent, order_id: str,
    processor: UserEventProcessor,
) -> tuple[float, float]:
    record = processor.owned_record(order_id)
    if record is None or record.get("condition_id") != market.condition_id:
        raise ValueError(f"fill order identity contradicts market scope: {order_id}")
    if (record.get("token_id") != event.token_id
            or record.get("side") != event.our_side.value
            or event.our_side is not Side.BUY
            or event.market_id not in (None, market.condition_id)):
        raise ValueError(f"fill identity contradicts its registered order: {order_id}")
    qty = _finite_positive(event.size, "confirmed fill quantity")
    price = _finite_real(event.price, "confirmed fill price")
    if not 0.0 < price < 1.0:
        raise ValueError("confirmed binary fill price must be between zero and one")
    return qty, price
