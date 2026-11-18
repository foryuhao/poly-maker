"""Versioned signed-limit risk band; historical anchor contracts stay intact."""

from decimal import Decimal

from polymaker.m4_types import (
    ABANDON_FIRST_SIGNED_BAND as ABANDON_FIRST_SIGNED_BAND,
    CLOSED_SIGNED_LIMIT_BAND,
    POST_SIGNED_LIMIT_BAND as POST_SIGNED_LIMIT_BAND,
)

ANCHOR_PRICE_FLOOR = "ANCHOR_PRICE_FLOOR_V1"
POST_PRICE_OUTSIDE_BAND = "M4_POST_PRICE_OUTSIDE_BAND"
WAIT_FOR_PRICE_BAND = "WAIT_UNTIL_T240_V1"
ABANDON_BEST_ASK_BAND = "ABANDON_ON_BEST_ASK_BAND_REJECTION_V1"
BEST_ASK_BAND_ABANDONED = "M4_BEST_ASK_PRICE_BAND_ABANDONED"
SIGNED_LIMIT_BAND_ABANDONED = "M4_SIGNED_LIMIT_PRICE_BAND_ABANDONED"


def price_in_band(price, band):
    """BUY fills may improve below the minimum; this constrains signed limits."""
    try:
        price = Decimal(str(price))
        low, high = (Decimal(str(band[k])) for k in ("minimum", "maximum"))
        return (band["policy"] in {POST_SIGNED_LIMIT_BAND, CLOSED_SIGNED_LIMIT_BAND}
                and all(x.is_finite() for x in (price, low, high))
                and 0 <= low < high <= 1 and 0 < price < 1 and low <= price
                and (price <= high if band["policy"] == CLOSED_SIGNED_LIMIT_BAND else price < high))
    except (KeyError, ValueError, TypeError, ArithmeticError):
        return False


def configured_band(taker):
    if taker.price_band_policy == ANCHOR_PRICE_FLOOR:
        return None
    return {"policy": taker.price_band_policy,
            "minimum": str(taker.post_minimum_price), "maximum": str(taker.post_maximum_price)}
