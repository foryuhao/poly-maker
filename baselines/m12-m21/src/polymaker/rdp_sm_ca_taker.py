"""CA immediate-entry constraints and exact-ID taker trade normalization."""
from decimal import ROUND_HALF_UP, Decimal

from polymaker.domain import Side
from polymaker.execution.taker_fees import (
    buy_amount_fee_reserve,
    fee_reserve,
)
from polymaker.execution.taker_fees import (
    buy_fee_reserve as buy_fee_reserve,
)
from polymaker.execution.taker_fees import (
    fee_parameters as fee_parameters,
)
from polymaker.state.tracker import TradeEvent
from polymaker.userstream.parse import strict_number

MICRO = Decimal("0.000001")


def validate_buy_fills(fills, spec):
    """Exact-ID callers supply distinct economic fills for one signed BUY."""
    budget = spec.get('buy_amount_usdc', spec['price']*spec['quantity'])
    principal = sum((Decimal(f.principal_usdc) for f in fills), Decimal(0))
    cost = sum((Decimal(f.principal_usdc)+Decimal(f.fee_usdc) for f in fills), Decimal(0))
    maximum = budget+buy_amount_fee_reserve(budget, spec['fees'])
    if principal > budget+MICRO or cost > maximum+MICRO:
        raise ValueError('CA_TAKER_FILL_EXCEEDS_LIMIT')


def taker_event(trade, spec):
    raw = trade.raw
    if (trade.market != spec["market_id"] or trade.taker_asset_id != spec["token_id"]
            or trade.taker_side != "BUY" or raw.get("trader_side") != "TAKER"
            or not trade.maker_legs):
        raise ValueError("CA_TAKER_TRADE_IDENTITY_INVALID")
    price, qty = (Decimal(str(strict_number(raw[k]))) for k in ("price", "size"))
    if not 0 < price <= spec["price"] or not qty.is_finite() or qty <= 0:
        raise ValueError("CA_TAKER_FILL_EXCEEDS_LIMIT")
    principal, fee = Decimal(0), Decimal(0)
    for leg in trade.maker_legs:
        execution_price = (Decimal(str(leg.price)) if leg.asset_id == spec["token_id"] and leg.side is Side.SELL
                           else 1-Decimal(str(leg.price)) if leg.asset_id == spec["opposite_token"] and leg.side is Side.BUY
                           else None)
        if execution_price is None or not 0 < execution_price <= spec["price"]:
            raise ValueError("CA_TAKER_MAKER_LEGS_CONFLICT")
        amount = Decimal(str(leg.matched_amount))
        principal += amount * execution_price
        fee += (amount * Decimal(spec["fees"]["rate"]) * (execution_price*(1-execution_price))**spec["fees"]["exponent"]).quantize(
            Decimal("0.00001"), rounding=ROUND_HALF_UP)
    if abs(sum((Decimal(str(leg.matched_amount)) for leg in trade.maker_legs), Decimal(0))-qty) > MICRO:
        raise ValueError("CA_TAKER_TRADE_QUANTITY_CONFLICT")
    if raw.get("fee_usdc") is not None:
        observed = Decimal(str(raw["fee_usdc"]))
        if not observed.is_finite() or observed < 0 or abs(observed-fee) > len(trade.maker_legs)*Decimal("0.00001"):
            raise ValueError("CA_TAKER_FEE_CONFLICT")
        fee = observed
    if fee > fee_reserve(qty, spec["fees"]):
        raise ValueError("CA_TAKER_FEE_EXCEEDS_RESERVE")
    # Complementary maker prices can carry binary float noise. Settlement
    # collateral has six decimals; use that exact unit for budget/account proof.
    principal = principal.quantize(MICRO, rounding=ROUND_HALF_UP)
    event = TradeEvent(spec["token_id"], Side.BUY, float(principal/qty), float(qty), trade.trade_id,
                      trade.status, trade.timestamp, trade.taker_order_id, trade.market,
                      "TAKER", str(principal), str(fee))
    validate_buy_fills([event], spec)
    return event


def cache_trade_legs(row):
    """Reported IDs/quantities for cache completeness, never a fill permission."""
    legs = list(row["maker_legs"])
    if row.get("taker_order_id") and row.get("trader_side") == "TAKER":
        legs.append({"order_id": row["taker_order_id"], "asset_id": row.get("taker_asset_id"),
                     "side": row.get("taker_side"), "matched_amount": row.get("size"),
                     "price": row.get("price"), "maker_address": None})
    return legs
