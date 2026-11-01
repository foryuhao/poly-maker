"""M1 quote arithmetic, usable without importing account/execution modules."""

from decimal import ROUND_FLOOR, Decimal


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
