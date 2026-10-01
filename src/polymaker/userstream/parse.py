"""Strict parsers for current user-WS trade and order events.

Maker economics are taken directly from ``maker_orders``. Ownership is decided
later by exact order ID membership, never by wallet or taker fields.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import math
from typing import Any

from polymaker.domain import Side, TradeState
from polymaker.state.tracker import OrderEvent, TradeEvent

_STATUS = {
    "MATCHED": TradeState.MATCHED,
    "MINED": TradeState.MINED,
    "CONFIRMED": TradeState.CONFIRMED,
    "RETRYING": TradeState.RETRYING,
    "FAILED": TradeState.FAILED,
}


class MalformedMakerLeg(ValueError):
    """A trade contains a maker leg that cannot safely be attributed."""


MAKER_ORDER_ID_ALIASES = ("order_id", "orderID", "orderId", "id")


@dataclass(frozen=True, slots=True)
class MakerOrderIdEvidence:
    """Raw maker ID evidence, retaining usable values even when malformed."""

    ids: frozenset[str]
    present: bool
    all_usable: bool
    aliases_agree: bool

    @property
    def valid(self) -> bool:
        return self.present and self.all_usable and self.aliases_agree and len(self.ids) == 1


def maker_order_id_evidence(row: dict[str, Any]) -> MakerOrderIdEvidence:
    """Inspect every supported raw maker ID alias without choosing between them."""
    present = [key for key in MAKER_ORDER_ID_ALIASES if key in row]
    usable = [row[key].strip() for key in present
              if isinstance(row[key], str) and row[key].strip()]
    ids = frozenset(usable)
    return MakerOrderIdEvidence(
        ids=ids,
        present=bool(present),
        all_usable=len(usable) == len(present),
        aliases_agree=len(ids) <= 1,
    )


def maker_order_id(row: dict[str, Any]) -> str:
    """Return a maker ID only when all supplied ID aliases agree exactly."""
    evidence = maker_order_id_evidence(row)
    if not evidence.present:
        raise MalformedMakerLeg("maker leg has no order id")
    if not evidence.all_usable:
        raise MalformedMakerLeg("maker leg has malformed order id")
    if not evidence.aliases_agree or len(evidence.ids) != 1:
        raise MalformedMakerLeg("maker leg order ID aliases contradict")
    return next(iter(evidence.ids))


@dataclass(frozen=True, slots=True)
class NormalizedMakerLeg:
    order_id: str
    asset_id: str
    side: Side
    matched_amount: float
    price: float
    maker_address: str | None = None
    owner: str | None = None


@dataclass(frozen=True, slots=True)
class NormalizedTrade:
    trade_id: str
    status: TradeState
    market: str
    taker_order_id: str | None
    taker_asset_id: str | None
    taker_side: str | None
    timestamp: float
    maker_legs: tuple[NormalizedMakerLeg, ...]
    raw: dict[str, Any]


def strict_number(value: object) -> float:
    """Parse supported JSON numerics without Python's bool-as-int coercion."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError("numeric field has unsupported type")
    if isinstance(value, str) and not value.strip():
        raise ValueError("numeric field is blank")
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError("numeric field cannot be represented") from exc
    if not math.isfinite(number):
        raise ValueError("numeric field is not finite")
    return number


def _market_identity(msg: dict[str, Any]) -> str:
    values: list[str] = []
    for key in ("market", "condition_id"):
        if key not in msg:
            continue
        value = msg[key]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"trade/order has malformed {key} identity")
        values.append(value.strip())
    if len(set(values)) > 1:
        raise ValueError("trade/order market identities contradict")
    return values[0] if values else ""


def parse_trade(msg: dict[str, Any]) -> NormalizedTrade:
    status = _STATUS.get(str(msg.get("status", "")).upper())
    trade_id = msg.get("id")
    if status is None or not isinstance(trade_id, str) or not trade_id.strip():
        raise ValueError("trade has invalid status or id")
    if "maker_orders" not in msg:
        raise ValueError("trade omitted maker_orders")
    market_value = _market_identity(msg)
    rows = msg.get("maker_orders")
    if not isinstance(rows, list):
        raise ValueError("maker_orders is not a list")
    legs: list[NormalizedMakerLeg] = []
    for row in rows:
        if not isinstance(row, dict):
            raise MalformedMakerLeg("maker leg is not an object")
        oid = maker_order_id(row)
        asset = row.get("asset_id")
        if not isinstance(asset, str) or not asset.strip():
            raise MalformedMakerLeg("maker leg has no usable asset id")
        try:
            side = Side(str(row.get("side", "")).upper())
            qty, price = strict_number(row["matched_amount"]), strict_number(row["price"])
        except (KeyError, ValueError, TypeError) as exc:
            raise MalformedMakerLeg("maker leg has invalid economics") from exc
        if not math.isfinite(qty) or qty < 0 or not math.isfinite(price) or not 0 < price <= 1:
            raise MalformedMakerLeg("maker leg has out-of-range economics")
        legs.append(NormalizedMakerLeg(oid, asset.strip(), side, qty, price,
            row.get("maker_address") if isinstance(row.get("maker_address"), str) else None,
            row.get("owner") if isinstance(row.get("owner"), str) else None))
    taker_side = msg.get("side")
    return NormalizedTrade(trade_id.strip(), status, market_value,
        msg.get("taker_order_id") if isinstance(msg.get("taker_order_id"), str) else None,
        msg.get("asset_id") if isinstance(msg.get("asset_id"), str) else None,
        taker_side if isinstance(taker_side, str) else None, _ts(msg), tuple(legs), dict(msg))


def _ts(msg: dict[str, Any]) -> float:
    raw = msg.get("timestamp")
    try:
        v = float(raw)  # type: ignore[arg-type]
        return v / 1000.0 if v > 1e12 else v
    except (OverflowError, ValueError, TypeError):
        return 0.0


def normalize_trade(
    msg: dict[str, Any],
    our_address: str,
    other_token: Callable[[str], str | None],
) -> list[TradeEvent]:
    """Extract our maker fills from a `trade` event. Returns one TradeEvent per
    matching maker order (usually one)."""
    try:
        trade = parse_trade(msg)
    except ValueError:
        return []
    out: list[TradeEvent] = []
    for leg in trade.maker_legs:
        out.append(
            TradeEvent(
                token_id=leg.asset_id,
                our_side=leg.side,
                price=leg.price,
                size=leg.matched_amount,
                trade_id=trade.trade_id,
                status=trade.status,
                ts=trade.timestamp,
                maker_order_id=leg.order_id,
                market_id=trade.market or None,
            )
        )
    return out


def normalize_order(msg: dict[str, Any]) -> OrderEvent | None:
    """Map an `order` event to remaining-size tracking for the reconciler."""
    try:
        oid = msg.get("id", msg.get("order_id"))
        asset = msg.get("asset_id")
        if not isinstance(oid, str) or not oid.strip() or not isinstance(asset, str) or not asset.strip():
            return None
        side = _side(msg.get("side"))
        if "original_size" not in msg or "size_matched" not in msg or "price" not in msg:
            return None
        raw_original, raw_matched, raw_price = msg["original_size"], msg["size_matched"], msg["price"]
        if any(v is None or (isinstance(v, str) and not v.strip())
               for v in (raw_original, raw_matched, raw_price)):
            return None
        original, matched, price = (strict_number(raw_original), strict_number(raw_matched),
                                    strict_number(raw_price))
        if not all(math.isfinite(x) for x in (original, matched, price)) or original < 0 or matched < 0:
            return None
        if original <= 0 or matched > original or not 0 < price <= 1:
            return None
        remaining = original - matched
        status = str(msg.get("status", "")).upper()
        is_cancel = status in ("CANCELED", "CANCELLED") or msg.get("type") == "CANCELLATION"
        associate_trades: tuple[str, ...] = ()
        if "associate_trades" in msg:
            refs = msg["associate_trades"]
            if not isinstance(refs, list) or any(not isinstance(x, str) or not x.strip() for x in refs):
                return None
            associate_trades = tuple(x.strip() for x in refs)
        market_id = _market_identity(msg)
        return OrderEvent(
            order_id=oid.strip(),
            token_id=asset.strip(),
            side=side,
            price=price,
            remaining_size=remaining,
            is_cancel=is_cancel,
            original_size=original,
            size_matched=matched,
            status=status,
            associate_trades=associate_trades,
            market_id=market_id or None,
        )
    except (KeyError, ValueError, TypeError):
        return None


def _side(value: object) -> Side:
    return Side(str(value).upper())
