"""M1 quote arithmetic, usable without importing account/execution modules."""

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

BASELINE_TARGET_USDC = Decimal(1)
BASELINE_QUANTITY_STEP = Decimal(".1")
BASELINE_MAX_INSTRUCTIONS = 5


def baseline_quantity(price, minimum):
    """Target at least one USDC, rounding shares up to one decimal place."""
    price, minimum = Decimal(str(price)), Decimal(str(minimum))
    if not price.is_finite() or not 0 < price < 1:
        raise ValueError("M1_PRICE_INVALID")
    if not minimum.is_finite() or minimum <= 0:
        raise ValueError("M1_AMOUNT_NOT_LEGAL")
    quantity = (BASELINE_TARGET_USDC / price).quantize(BASELINE_QUANTITY_STEP, rounding=ROUND_CEILING)
    if quantity < max(Decimal(5), minimum):
        raise ValueError("M1_AMOUNT_NOT_LEGAL")
    return quantity


def budget_quantity(price, minimum, budget=Decimal(5)):
    price, minimum, budget = (Decimal(str(v)) for v in (price, minimum, budget))
    if not price.is_finite() or not 0 < price < 1:
        raise ValueError("M1_PRICE_INVALID")
    if not minimum.is_finite() or minimum <= 0 or not budget.is_finite() or budget <= 0:
        raise ValueError("M1_AMOUNT_NOT_LEGAL")
    quantity = (budget / price).quantize(Decimal(".01"), rounding=ROUND_FLOOR)
    if quantity < max(Decimal(5), minimum) or quantity * price > budget:
        raise ValueError("M1_AMOUNT_NOT_LEGAL")
    return quantity
