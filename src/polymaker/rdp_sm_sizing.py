"""Pure sizing helpers for the existing SM quote decision, never execution."""

from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from polymaker.config import RDPSMConfig
from polymaker.rdp_sm_execution import MIN_MAKER_BUY_SHARES, maker_buy_size

GRID = Decimal(".01")
UNKNOWN = "UNKNOWN"


def validate_market_sizing(cfg: RDPSMConfig, minimum: Decimal) -> None:
    """Reject configuration against known venue rules before opening a session."""
    RDPSMConfig.model_validate(cfg.model_dump())
    if not minimum.is_finite() or minimum <= 0:
        raise ValueError("SIZING_MARKET_MINIMUM_UNKNOWN")
    if cfg.base_size < max(MIN_MAKER_BUY_SHARES, minimum):
        raise ValueError("SIZING_BASE_BELOW_MARKET_MINIMUM")


def resolve_favorite_target(meta, books, cfg: RDPSMConfig, *, now=None, max_age=10, official_time=None,
                            official_time_read_duration_s=0):
    result = {"favorite_side": "NONE", "favorite_probability": UNKNOWN,
              "favorite_data_validity": "DISABLED" if cfg.target_delta == 0 else "INVALID",
              "favorite_data_reason": "TARGET_DISABLED" if cfg.target_delta == 0 else "CLOCK_MISSING",
              "favorite_clock_resolution_s": "0", "favorite_clock_read_duration_s": "0",
              "favorite_clock_uncertainty_s": "0", "favorite_max_book_ahead_s": "0",
              "signed_target_delta": "0"}
    if cfg.target_delta == 0:
        return result
    try:
        from py_clob_client_v2.order_builder.builder import ROUNDING_CONFIG

        if now is None:
            return result
        clock, age_limit = Decimal(str(now)), Decimal(str(max_age))
        read_duration = Decimal(str(official_time_read_duration_s))
        result["favorite_data_reason"] = "CLOCK_INVALID"
        if (not clock.is_finite() or clock <= 0 or not age_limit.is_finite() or age_limit <= 0
                or not read_duration.is_finite() or read_duration < 0
                or official_time is None and read_duration != 0):
            return result
        resolution = Decimal(0)
        if official_time is not None:
            reference = Decimal(str(official_time))
            if not reference.is_finite() or not 0 < reference <= clock:
                return result
            # /time may expose whole seconds while /book has milliseconds.
            # [reference, reference+1) is that official observation's precision
            # interval. Its read duration bounds time spent receiving that
            # observation; monotonic time since receipt is included in now.
            # Without coarse-clock/read evidence the zero-lookahead rule stays.
            if reference == reference.to_integral_value():
                resolution = Decimal(1)
        result["favorite_clock_resolution_s"] = str(resolution)
        result["favorite_clock_read_duration_s"] = str(read_duration)
        uncertainty = resolution + read_duration
        result["favorite_clock_uncertainty_s"] = str(uncertainty)
        mids, stamps = [], []
        for token in (meta.yes.token_id, meta.no.token_id):
            b = books[token]
            bid, ask, tick, stamp = (Decimal(str(b[k])) for k in
                                     ("best_bid", "best_ask", "tick", "timestamp"))
            if b.get("token_id") != token or b.get("market_id") != meta.condition_id:
                result["favorite_data_reason"] = "BOOK_IDENTITY_INVALID"
                return result
            if not all(v.is_finite() for v in (bid, ask, tick, stamp)) or not 0 < bid < ask < 1:
                result["favorite_data_reason"] = "BOOK_PRICE_OR_TIMESTAMP_INVALID"
                return result
            if f"{float(tick):g}" not in ROUNDING_CONFIG or bid % tick or ask % tick:
                result["favorite_data_reason"] = "BOOK_TICK_INVALID"
                return result
            age = clock - stamp
            result["favorite_max_book_ahead_s"] = str(max(
                Decimal(result["favorite_max_book_ahead_s"]), -age, Decimal(0)))
            if age < 0 and -age >= uncertainty:
                result["favorite_data_reason"] = "BOOK_FROM_FUTURE"
                return result
            if age > age_limit:
                result["favorite_data_reason"] = "BOOK_STALE"
                return result
            mids.append((bid + ask) / 2)
            stamps.append(stamp)
        if abs(stamps[0] - stamps[1]) > 1:
            result["favorite_data_reason"] = "BOOK_PAIR_TIME_GAP"
            return result
        if abs(mids[0] + mids[1] - 1) > Decimal(".02"):
            result["favorite_data_reason"] = "BOOK_PROBABILITY_INCONSISTENT"
            return result
        p = mids[0]
        side = "YES" if p > cfg.favorite_threshold else "NO" if 1 - p > cfg.favorite_threshold else "NONE"
        result.update(favorite_side=side, favorite_probability=str(max(p, 1 - p)),
                      favorite_data_validity="VALID",
                      favorite_data_reason="THRESHOLD_EXCEEDED" if side != "NONE" else "THRESHOLD_NOT_EXCEEDED",
                      signed_target_delta=str(cfg.target_delta * (1 if side == "YES" else -1 if side == "NO" else 0)))
    except (KeyError, TypeError, ValueError, ArithmeticError):
        result["favorite_data_reason"] = "MALFORMED_BOOK_OR_CLOCK"
    return result


def normal_sizes(delta: Decimal, base: Decimal):
    """The original inventory skew, including its original submission rounding."""
    return {side: q.quantize(GRID, rounding=ROUND_CEILING) for side, q in
            {"YES": base + max(-delta, 0), "NO": base + max(delta, 0)}.items()}


def adjusted_normal_size(side, legacy, target, price, minimum, maximum):
    trim = max(-target, 0) if side == "YES" else max(target, 0)
    legal_min = maker_buy_size(price, Decimal(0), minimum)
    # Never enlarge a legacy leg to accommodate the soft target. Its preexisting
    # illegality (including notional) is handled by make_quotes as before.
    adjusted = max(legacy - trim, min(legacy, legal_min))
    size = min(adjusted, maximum)
    reasons = []
    if adjusted != legacy - trim:
        reasons.append("MINIMUM_ORDER_FALLBACK")
    if size != adjusted:
        reasons.append("MAX_ORDER_SIZE")
    return size, reasons


def calculate_rebalance_size(delta, target, price, minimum, maximum):
    ideal = abs(delta) - (target if delta > 0 else -target)
    legal_min = maker_buy_size(price, Decimal(0), minimum)
    if legal_min > maximum:
        raise ValueError("MINIMUM_ORDER_EXCEEDS_MAXIMUM")
    legal = maker_buy_size(price, ideal, minimum)
    size = min(legal, maximum)
    reasons = []
    if legal != ideal:
        reasons.append("MINIMUM_NOTIONAL_OR_PRECISION_FALLBACK")
    if size != legal:
        reasons.append("MAX_ORDER_SIZE")
    return ideal, size, reasons


def adjust_normal_pair(sizes, prices, delta, target, minimum):
    """After hard caps, approach the target by reducing exposure only."""
    error = delta + sizes["YES"] - sizes["NO"] - target
    side = "YES" if error > 0 else "NO"
    legal_min = maker_buy_size(prices[side], Decimal(0), minimum)
    closest = max(legal_min, sizes[side] - abs(error)).quantize(GRID, rounding=ROUND_HALF_UP)
    adjusted = min(sizes[side], closest)
    return {**sizes, side: adjusted}


def empty_sizing_report():
    return {"final_target_delta": 0, "final_actual_delta": 0,
            "intentional_residual_side": "NONE", "intentional_residual_shares": 0,
            "unintentional_residual_shares": 0, "intentional_residual_pnl": UNKNOWN,
            "unintentional_residual_pnl": UNKNOWN, "pair_pnl": UNKNOWN,
            "trading_pnl_before_rebate": UNKNOWN, "actual_maker_rebate": UNKNOWN,
            "maker_filled_shares": 0, "maker_fill_count": 0, "safe_exit_status": "BLOCKED",
            "final_winning_outcome": UNKNOWN, "favorite_prediction_correct": UNKNOWN,
            "intentional_residual_evidence": {}}


def summarize_sizing(executor, windows, delta, barrier):
    """Project existing confirmed fills; conservative certificate, no new ledger.

    Every requested leg must be fully confirmed to create a certificate.
    Later opposing confirmed net fills consume it first; same-side extra fills
    never enlarge it. The projection is rebuilt from the existing fill evidence.
    """
    report = empty_sizing_report()
    fills = executor.confirmed()
    report.update(final_actual_delta=float(delta), unintentional_residual_shares=float(abs(delta)),
                  maker_filled_shares=float(sum((Decimal(str(f.size)) for f in fills), Decimal(0))),
                  maker_fill_count=len(fills), safe_exit_status=barrier)
    certified, certified_sign, certificate = Decimal(0), 0, {}
    for w in windows:
        e = w["sizing"]
        result = w["result"]
        placements = result["placement_results"]
        # Includes UNKNOWN once its identity/economic fill is actually confirmed.
        ids = {p["order_id"] for p in placements if p.get("order_id")}
        legs = [f for f in fills if f.maker_order_id in ids]
        sums = {side: sum((Decimal(str(f.size)) for f in legs if f.token_id == token), Decimal(0))
                for side, token in (("YES", executor.meta.yes.token_id), ("NO", executor.meta.no.token_id))}
        submitted = {side: sum((Decimal(str(p["quote"]["size"])) for p in placements
                               if p["quote"]["outcome"] == side and p.get("post_started")), Decimal(0))
                     for side in ("YES", "NO")}
        e.update(submitted_yes_size=str(submitted["YES"]), submitted_no_size=str(submitted["NO"]),
                 confirmed_yes_fill=str(sums["YES"]), confirmed_no_fill=str(sums["NO"]),
                 actual_delta_after=str(result["delta"]))
        if w["state"] == "REBALANCE":
            e["actual_delta_after_repair"] = e["actual_delta_after"]
        net = sums["YES"] - sums["NO"]
        if certified and net * certified_sign < 0:
            certified = max(Decimal(0), certified - abs(net))
        if certificate and legs:
            certificate["subsequent_confirmed_windows"].append({"window_id": result["window_id"],
                "confirmed_net_change": str(net)})
        target = Decimal(e["signed_target_delta"])
        all_filled = all(
            sum((Decimal(str(f.size)) for f in legs if f.maker_order_id == p.get("order_id")), Decimal(0))
            == Decimal(str(p["quote"]["size"]))
            for p in result["placement_results"])
        if (target != 0 and e["target_sizing_applied"] and e["favorite_data_validity"] == "VALID"
                and Decimal(e["actual_delta_after"]) == target and all_filled
                and Decimal(e["actual_delta_before"]) + net == target
                and len(result["placement_results"]) == len(result["requested_quotes"])
                and result["close_barrier"] == "SAFE_TO_CONTINUE"):
            certified, certified_sign = abs(target), 1 if target > 0 else -1
            certificate = {"window_id": result["window_id"], "signed_target_delta": str(target),
                "confirmed_fills": [{"trade_id": f.trade_id, "maker_order_id": f.maker_order_id,
                                      "size": str(f.size)} for f in legs],
                "subsequent_confirmed_windows": []}
    if windows:
        report["final_target_delta"] = float(Decimal(windows[-1]["sizing"]["signed_target_delta"]))
    if certified and delta * certified_sign > 0 and barrier == "SAFE_TO_CONTINUE":
        certified = min(certified, abs(delta))
        report.update(intentional_residual_side="YES" if certified_sign > 0 else "NO",
                      intentional_residual_shares=float(certified),
                      unintentional_residual_shares=float(abs(delta) - certified),
                      intentional_residual_evidence=certificate)
    return report


def validate_zero_sizing_report(report, check, barrier):
    """Explicit new report schema; never relax old close/capital evidence."""
    check(isinstance(report, dict) and set(report) == set(empty_sizing_report()))
    check(type(report["final_target_delta"]) in (int, float) and report["final_target_delta"] in (-1, 0, 1))
    for key in ("final_actual_delta", "intentional_residual_shares", "unintentional_residual_shares",
                "maker_filled_shares", "maker_fill_count"):
        check(type(report[key]) in (int, float) and report[key] == 0)
    check(report["intentional_residual_side"] == "NONE" and report["intentional_residual_evidence"] == {})
    check(report["safe_exit_status"] == barrier)
    for key in ("intentional_residual_pnl", "unintentional_residual_pnl", "pair_pnl",
                "trading_pnl_before_rebate", "actual_maker_rebate", "final_winning_outcome",
                "favorite_prediction_correct"):
        check(report[key] == UNKNOWN)
