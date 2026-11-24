"""Exact collateral/share units for budget-sized immediate BUY orders."""
from decimal import ROUND_FLOOR, Decimal


def market_buy_quantity(amount: Decimal, price: Decimal) -> Decimal:
    """Match market BUY signing: cents out, at most four share decimals in.

    Round shares down, as the SDK does, so the signed ratio can reach the
    quoted Ask. The cash amount remains exact; fills are checked against it.
    Use four decimals even on fine ticks to avoid market-order precision rejects.
    """
    if (not amount.is_finite() or amount <= 0 or amount % Decimal('.01')
            or not price.is_finite() or not 0 < price < 1):
        raise ValueError('MARKET_BUY_AMOUNT_INVALID')
    return (amount/price).quantize(Decimal('.0001'), rounding=ROUND_FLOOR)
