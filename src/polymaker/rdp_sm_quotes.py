"""Pure pricing/sizing shared by independent controllers and legacy callers."""

from dataclasses import replace
from decimal import Decimal

from polymaker.config import RDPSMConfig
from polymaker.domain import Side
from polymaker.rdp_sm import inventory_delta, inventory_trading_state
from polymaker.rdp_sm_execution import MIN_MAKER_BUY_SHARES, WindowQuote
from polymaker.rdp_sm_sizing import (
    adjust_normal_pair,
    adjusted_normal_size,
    calculate_rebalance_size,
    normal_sizes,
    resolve_favorite_target,
    validate_market_sizing,
)
from polymaker.rdp_sm_volatility import WINDOW_SECONDS


class QuoteUnavailable(ValueError):
    """Known quote illegality; no placement or exposure was created."""


def make_quotes(
    meta,
    books,
    yes: Decimal,
    no: Decimal,
    window_id: str,
    sizing: RDPSMConfig | None = None,
    *,
    now=None,
    max_age=10,
    evidence=None,
    official_time=None,
    official_time_read_duration_s=0,
    signal_side=None,
    normal_sigma=None,
):
    cfg = sizing if sizing is not None else RDPSMConfig()
    validate_market_sizing(cfg, Decimal(str(meta.min_order_size)))
    delta = inventory_delta(yes, no)
    state = inventory_trading_state(delta)
    favorite = resolve_favorite_target(
        meta,
        books,
        cfg,
        now=now,
        max_age=max_age,
        official_time=official_time,
        official_time_read_duration_s=official_time_read_duration_s,
    )
    target = Decimal(favorite["signed_target_delta"])
    details = {
        "base_size": str(cfg.base_size),
        "configured_target_delta": str(cfg.target_delta),
        "favorite_threshold": str(cfg.favorite_threshold),
        **favorite,
        "actual_delta_before": str(yes - no),
        "decision_delta_before": str(delta),
        "sizing_adjustment_reason": "FAVORITE_TARGET" if target else "LEGACY_INVENTORY_SKEW",
        "sizing_constraint_reason": [],
        "minimum_order_size": str(meta.min_order_size),
        "favorite_at_decision": favorite["favorite_side"],
        "final_winning_outcome": "UNKNOWN",
        "favorite_prediction_correct": "UNKNOWN",
        "target_sizing_applied": False,
        "planned_yes_size": "0",
        "planned_no_size": "0",
        "submitted_yes_size": "0",
        "submitted_no_size": "0",
        "confirmed_yes_fill": "0",
        "confirmed_no_fill": "0",
        "actual_delta_after": str(yes - no),
    }
    if evidence is not None:
        evidence.clear()
        evidence.update(details)
    if state == "NORMAL":
        sizes = normal_sizes(delta, cfg.base_size)
        if signal_side is not None:
            if signal_side not in sizes:
                raise QuoteUnavailable("OBI_SIGNAL_MISSING")
            sigma = Decimal(str(normal_sigma)) if normal_sigma is not None else None
            if sigma is None or not sigma.is_finite() or sigma < 0:
                raise QuoteUnavailable("NORMAL_VOLATILITY_UNKNOWN")
            # Select before pricing/validation: the unused opposite leg must
            # neither create exposure nor veto a legal signal-side order.
            sizes = {signal_side: sizes[signal_side]}
            details.update(order_policy="SIGNAL_SIDE_ONLY",
                           normal_signal_side=signal_side, normal_sigma=str(sigma),
                           sigma_window_seconds=WINDOW_SECONDS)
    else:
        sizes = {"NO" if delta > 0 else "YES": abs(delta)}
    quotes = []
    for outcome, size in sizes.items():
        token = meta.yes.token_id if outcome == "YES" else meta.no.token_id
        book = books[token]
        tick, bid, ask = (Decimal(str(book[k])) for k in ("tick", "best_bid", "best_ask"))
        price = (bid if signal_side == outcome else bid-tick) if state == "NORMAL" else bid
        if (
            not all(n.is_finite() for n in (tick, bid, ask, price, size))
            or tick <= 0
            or not tick <= price <= 1 - tick
            or price % tick != 0
            or price >= ask
        ):
            raise QuoteUnavailable("QUOTE_PRICE_TICK_OR_SIZE_INVALID")
        if state == "REBALANCE":
            try:
                ideal, size, reasons = calculate_rebalance_size(
                    delta, target, price, Decimal(str(meta.min_order_size)), cfg.max_order_size
                )
            except ValueError as exc:
                raise QuoteUnavailable(str(exc)) from exc
            details.update(
                current_delta=str(delta),
                ideal_repair_size=str(ideal),
                actual_repair_size=str(size),
                repair_side=outcome,
                repair_sizing_fallback=reasons or ["NONE"],
            )
            neutral_size = calculate_rebalance_size(
                delta, Decimal(0), price, Decimal(str(meta.min_order_size)), cfg.max_order_size
            )[1]
        else:
            neutral_size = adjusted_normal_size(
                outcome,
                size,
                Decimal(0),
                price,
                Decimal(str(meta.min_order_size)),
                cfg.max_order_size,
            )[0]
            size, reasons = adjusted_normal_size(
                outcome, size, target, price, Decimal(str(meta.min_order_size)), cfg.max_order_size
            )
        details["target_sizing_applied"] |= size != neutral_size
        details["sizing_constraint_reason"].extend(reasons)
        if (
            size < max(MIN_MAKER_BUY_SHARES, Decimal(str(meta.min_order_size)))
            or size % Decimal(".01") != 0
        ):
            raise QuoteUnavailable("QUOTE_PRICE_TICK_OR_SIZE_INVALID")
        quotes.append(
            WindowQuote(
                meta.condition_id, token, outcome, Side.BUY, float(price), float(size), window_id
            )
        )
    if state == "NORMAL" and cfg.target_delta != 0 and signal_side is None:
        current = {q.outcome: Decimal(str(q.size)) for q in quotes}
        closest = adjust_normal_pair(
            current,
            {q.outcome: Decimal(str(q.price)) for q in quotes},
            delta,
            target,
            Decimal(str(meta.min_order_size)),
        )
        if closest != current:
            details["sizing_constraint_reason"].append("TARGET_AFTER_CONSTRAINTS")
            quotes = [replace(q, size=float(closest[q.outcome])) for q in quotes]
            details["target_sizing_applied"] = True
    details.update(
        planned_yes_size=str(next((q.size for q in quotes if q.outcome == "YES"), 0)),
        planned_no_size=str(next((q.size for q in quotes if q.outcome == "NO"), 0)),
    )
    if evidence is not None:
        evidence.clear()
        evidence.update(details)
    return state, quotes
