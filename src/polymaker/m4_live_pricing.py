"""Versioned live-only repricing and inspectable, immutable quote evidence."""

from pathlib import Path

from polymaker.m4_live_numeric import age_us
from polymaker.m4_live_price_band import price_in_band
from polymaker.m4_state import decimal

FIXED = "ANCHOR_FIXED_V1"
DYNAMIC = "ANCHOR_10S_THEN_BEST_ASK_V1"
WAIT_US = 10_000_000
BOOT_ID = Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def origin(signal_mono, now, elapsed):
    age = age_us(now, signal_mono)
    if age < 0:
        raise ValueError("M4_SIGNAL_CLOCK_INVALID")
    return {"policy": DYNAMIC, "wait_us": WAIT_US, "boot_id": BOOT_ID,
            "signal_monotonic": signal_mono,
            "signal_elapsed_us": age_us(elapsed, 0) - age}


def signal_age(root, now, elapsed, boot_id=BOOT_ID):
    start = root["pricing_origin"]
    if start["policy"] != DYNAMIC or start["wait_us"] != WAIT_US:
        raise ValueError("M4_PRICING_ORIGIN_INVALID")
    # CLOCK_MONOTONIC survives a process restart. After a host reboot, use
    # official market time, never a new process-relative ten-second timer.
    return (age_us(now, start["signal_monotonic"]) if start["boot_id"] == boot_id
            else age_us(elapsed, 0) - start["signal_elapsed_us"])


def quote_valid(root, plan, token, evidence=None):
    """Pure check usable for live recovery and the read-only audit collector.

    Freshness is checked at quote capture; replay never compares an old quote
    with today's clock. Signed prices stay immutable through transport/recovery.
    """
    try:
        price = decimal(plan["limit_price"])
        if root.get("post_price_band") and not price_in_band(price, root["post_price_band"]):
            return False
        if "pricing_origin" not in root:
            return price == decimal(root["limit_price"])
        q = plan["pricing"]
        if (evidence != q or plan["pricing_origin"] != root["pricing_origin"]
                or q["opportunity_id"] != root["opportunity_id"] or q["token_id"] != token):
            return False
        age = signal_age(root, q["captured_monotonic"], q["captured_elapsed_s"], q["boot_id"])
        if age != q["signal_age_us"] or age < 0:
            return False
        if q["stage"] == "ANCHOR":
            return age < WAIT_US and price == decimal(root["limit_price"])
        tick, bid, ask = (decimal(q[k]) for k in ("tick", "best_bid", "best_ask"))
        return (q["stage"] == "BEST_ASK" and age >= WAIT_US and price == ask
                and 0 < bid < ask < 1 and tick > 0 and ask % tick == 0
                and tick <= ask <= 1 - tick and q["connected"] is True
                and q["generation"] == q["snapshot_generation"]
                and decimal(q["verified_tick"]) == tick
                and 0 <= age_us(q["captured_monotonic"], q["received_monotonic"]) <= 200_000
                and -2_000 <= age_us(q["captured_wall"], q["source_wall"]) <= 500_000
                and q["reported_prices_agree"] is True)
    except (KeyError, ValueError, TypeError, ArithmeticError):
        return False


def rejected_best_ask_valid(root, quote, token):
    """Validate immutable rejection evidence; never grants POST permission."""
    try:
        if (quote["stage"] != "BEST_ASK" or not root.get("post_price_band")
                or price_in_band(quote["limit_price"], root["post_price_band"])):
            return False
        # Reuse every clock/token/grid/freshness check. Only the band predicate
        # is inverted for this rejection fact; executable quotes still require it.
        capture_root = {k: v for k, v in root.items() if k != "post_price_band"}
        plan = {**capture_root, "pricing": quote, "limit_price": quote["limit_price"]}
        return quote_valid(capture_root, plan, token, quote)
    except (KeyError, ValueError, TypeError):
        return False
