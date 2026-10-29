"""Causal HD/TP/SL policy. No exchange, wallet, simulation or implicit fitted values."""
from __future__ import annotations

import math
from collections import deque
from dataclasses import asdict, dataclass
from decimal import ROUND_CEILING, Decimal

from polymaker.config import RDPRepairConfig, RDPRepairPathConfig

DUST = Decimal("0.1")


def repair_readiness(cfg: RDPRepairConfig) -> dict:
    if cfg.policy == "opposite_ba_v1":
        return {"policy": cfg.policy, "selected_policy": cfg.policy,
                "parameters_complete": True, "ready_for_live": True,
                "scope": "CONFIGURATION_ONLY; NOT_STRATEGY_PERFORMANCE_VALIDATION",
                "parameters": cfg.opposite_ba.model_dump(mode="json"), "reasons": []}
    missing = [name for name in ("tp_profit_rate", "shock_window_sec", "shock_z",
                                 "drawdown_per_share") if getattr(cfg.path, name) is None]
    reasons = [f"MISSING_{name.upper()}" for name in missing]
    if cfg.path.calibration_status != "REPLAY_VALIDATED":
        reasons.append("PARAMETERS_UNCALIBRATED" if cfg.path.calibration_status == "UNCALIBRATED"
                       else "EXECUTION_REPLAY_NOT_VALIDATED")
    if not cfg.path.validation_reference or not cfg.path.validation_reference.strip():
        reasons.append("REPLAY_VALIDATION_REFERENCE_MISSING")
    return {"policy": "hd_tp_sl_v1", "selected_policy": cfg.policy,
            "parameters_complete": not missing, "ready_for_live": not reasons,
            "scope": "PARAMETERS_AND_DECLARED_REPLAY_VALIDATION",
            "parameters": cfg.path.model_dump(mode="json"),
            "calibration_status": cfg.path.calibration_status, "reasons": reasons,
            "validation_reference": cfg.path.validation_reference}


@dataclass(frozen=True)
class PathPoint:
    elapsed: float
    p: float
    sigma: float | None
    generation: int
    delta: float
    pnl_per_share: float
    episode_delta: float | None = None
    tick: float = .01
    path_segment: int = 0
    inventory_path_valid: bool = True


class RepairPolicy:
    """One episode, forward-only modes; order execution remains separate.

    Confirmed starting rules: pre-event sigma AND tick floor on a bounded
    short window, OR a fixed price-contribution drawdown per original share.
    Independent reliable segments supply lower bounds after missing history.
    """

    def __init__(self, cfg: RDPRepairPathConfig, *, mode="HD", peak=0., price_pnl=0.):
        self.cfg, self.mode = cfg, mode
        self.peak, self.price_pnl = Decimal(str(peak)), Decimal(str(price_pnl))
        self.points = deque(maxlen=8192)
        self.path_complete = True
        self.path_breaks = []
        self._price_gap = False
        self._inventory_gap = False
        self._original_qty = None
        self.last_reason = "DEFAULT_HOLD"
        self.tp_price: Decimal | None = None
        self.tp_tick: Decimal | None = None
        self.last_transition = None
        self.apply_switches()

    def force_hold(self, elapsed, reason):
        previous, self.mode = self.mode, "HD"
        self.tp_price = self.tp_tick = None
        self.last_reason = reason
        if previous != "HD":
            self.last_transition = {"from": previous, "to": "HD", "reason": reason,
                                    "observations": {"elapsed": elapsed, "source": "EXIT_CONTROL"}}

    def apply_switches(self):
        """A restored disabled exit cannot retain an executable mode/TP price."""
        previous = self.mode
        if self.mode in {"TP", "SL"} and not self.cfg.exit_enabled(self.mode):
            self.force_hold(None, self.mode+"_DISABLED")
        return self.mode != previous

    def _reset_contribution(self, reason):
        self.path_complete = False
        self.path_breaks.append({"reason": reason, "price_pnl": str(self.price_pnl),
                                 "peak": str(self.peak),
                                 "elapsed": self.points[-1].elapsed if self.points else None})
        self.path_breaks = self.path_breaks[-32:]
        self.peak = self.price_pnl = Decimal(0)

    def break_path(self, reason):
        """No invented price return across a gap; preserve exit intent/evidence."""
        if not self._price_gap:
            self._reset_contribution(reason)
        self._price_gap = True
        self.points.clear()

    def break_inventory(self, reason):
        """Unknown economic timing invalidates DD, not the observed price shock."""
        if not self._inventory_gap:
            self._reset_contribution(reason)
        self._inventory_gap = True

    def force_sl(self, elapsed, reason="TIME_FORCE_SL"):
        if not self.cfg.sl_enabled:
            return
        if self.mode != "SL":
            previous, self.mode = self.mode, "SL"
            self.last_reason = reason
            self.last_transition = {"from": previous, "to": "SL", "reason": reason,
                                    "observations": {"elapsed": elapsed, "source": "TIME_BOUNDARY"}}

    def observe(self, point: PathPoint, *, original_side: str, original_qty: float):
        if not (0 <= point.p <= 1 and math.isfinite(point.elapsed)
                and math.isfinite(point.delta) and math.isfinite(point.pnl_per_share)
                and math.isfinite(original_qty) and original_qty > 0
                and math.isfinite(point.tick) and point.tick > 0
                and (point.episode_delta is None or math.isfinite(point.episode_delta))
                and (point.sigma is None
                                         or math.isfinite(point.sigma) and point.sigma >= 0)):
            raise ValueError("REPAIR_PATH_INPUT_INVALID")
        previous = self.points[-1] if self.points else None
        if previous and point.elapsed < previous.elapsed:
            raise ValueError("REPAIR_PATH_TIME_REVERSED")
        if self._original_qty is not None and original_qty != self._original_qty:
            self.break_inventory("ORIGINAL_FILL_QUANTITY_RECONCILED")
        self._original_qty = original_qty
        if previous and (point.generation != previous.generation
                         or point.path_segment != previous.path_segment):
            self.break_path("MARKET_PATH_DISCONTINUITY")
            previous = None
        if not point.inventory_path_valid:
            self.break_inventory("INVENTORY_PATH_UNKNOWN")
        if (previous and not self._price_gap and not self._inventory_gap
                and point.inventory_path_valid):
            delta = previous.delta if previous.episode_delta is None else previous.episode_delta
            self.price_pnl += (Decimal(str(delta))*(Decimal(str(point.p))-Decimal(str(previous.p)))
                               / Decimal(str(original_qty)))
        # The price-only path starts at its first observed point, never at the
        # sunk opening cost or at a mark change created by a repair fill.
        self.peak = max(self.peak, self.price_pnl)
        self.points.append(point)
        self._price_gap = False
        self._inventory_gap = not point.inventory_path_valid
        return self._features(point, original_side)

    def _features(self, point, original_side):
        window = self.cfg.shock_window_sec
        baseline = (next((p for p in reversed(self.points)
                          if p.elapsed <= point.elapsed-window
                          and p.generation == point.generation
                          and p.path_segment == point.path_segment), None)
                    if window is not None else None)
        if baseline and point.elapsed-baseline.elapsed > self.cfg.shock_max_age_sec:
            baseline = None
        z = None
        adverse = None
        threshold = None
        shock = False
        if baseline is not None and baseline.sigma is not None:
            direction = 1 if original_side == "YES" else -1
            adverse = float(max(Decimal(0), direction*(Decimal(str(baseline.p))-Decimal(str(point.p)))))
            scale = baseline.sigma*math.sqrt(point.elapsed-baseline.elapsed)
            z = adverse/scale if scale > 0 else None
            if self.cfg.shock_z is not None:
                threshold = max(float(Decimal(str(point.tick))*self.cfg.shock_min_ticks),
                                self.cfg.shock_z*scale)
                shock = adverse >= threshold
        return {"short_adverse": adverse, "short_z": z,
                "short_threshold": threshold, "shock_triggered": shock,
                "sigma_pre": baseline.sigma if baseline else None,
                "short_elapsed": point.elapsed-baseline.elapsed if baseline else None,
                "price_pnl_per_share": float(self.price_pnl),
                "price_peak_per_share": float(self.peak),
                "drawdown_per_share": float(max(Decimal(0), self.peak-self.price_pnl)),
                "drawdown_valid": point.inventory_path_valid,
                "path_status": "COMPLETE" if self.path_complete else "PARTIAL_PATH",
                "path_breaks": list(self.path_breaks)}

    def decide(self, point: PathPoint, *, original_side: str, original_qty: float,
               first_fill_elapsed: float, delta_limit=5., features=None, session_elapsed=None,
               cutoff_sec=240.):
        features = features if features is not None else self.observe(
            point, original_side=original_side, original_qty=original_qty)
        previous = self.mode
        self.apply_switches()
        boundary_elapsed = point.elapsed if session_elapsed is None else session_elapsed
        if boundary_elapsed >= cutoff_sec:
            reason = "CUTOFF"
        elif abs(point.delta) < float(DUST):
            reason = "PAIRED"
        elif not self.cfg.tp_enabled and not self.cfg.sl_enabled:
            self.mode, reason = "HD", "TP_SL_DISABLED"
        elif self.mode == "SL":
            reason = self.last_reason
        elif self.cfg.sl_enabled and abs(point.delta) > delta_limit:
            self.mode, reason = "SL", "SESSION_DELTA_LIMIT"
        elif boundary_elapsed >= 180:
            if self.cfg.sl_enabled:
                self.mode, reason = "SL", "TIME_FORCE_SL"
            else:
                self.force_hold(boundary_elapsed, "SL_DISABLED_AFTER_T180")
                reason = self.last_reason
        elif self.cfg.sl_enabled and features["shock_triggered"]:
            self.mode, reason = "SL", "ADVERSE_SHOCK"
        elif (self.cfg.sl_enabled and self.cfg.drawdown_per_share is not None
              and features["drawdown_valid"]
              and Decimal(str(features["drawdown_per_share"])) >= self.cfg.drawdown_per_share):
            self.mode, reason = "SL", "CUMULATIVE_DRAWDOWN"
        elif self.mode == "TP":
            reason = self.last_reason
        elif (self.cfg.tp_enabled and self.cfg.tp_profit_rate is not None and point.elapsed > first_fill_elapsed
              and Decimal(str(point.pnl_per_share)) > self.cfg.tp_profit_rate
              * Decimal(str(point.elapsed-first_fill_elapsed))):
            self.mode, reason = "TP", "PROFIT_RATE_TARGET"
        else:
            reason = "DEFAULT_HOLD" if self.cfg.tp_enabled else "TP_DISABLED"
        self.last_reason = reason
        if self.mode != previous:
            self.last_transition = {"from": previous, "to": self.mode, "reason": reason,
                                    "observations": {**asdict(point), **features}}
        return {"mode": self.mode, "previous_mode": previous,
                "transition": self.mode != previous, "reason": reason,
                "observations": {**asdict(point), **features},
                "target_remaining_delta": "0" if self.mode != "HD" else str(point.delta),
                "hold_seconds": max(0., point.elapsed-first_fill_elapsed),
                "tp_enabled": self.cfg.tp_enabled, "sl_enabled": self.cfg.sl_enabled,
                "last_transition": self.last_transition}

    def price(self, *, bid: Decimal, ask: Decimal, tick: Decimal, sigma: float | None):
        if not (tick.is_finite() and tick > 0 and bid.is_finite() and ask.is_finite()):
            raise ValueError("REPAIR_BOOK_INVALID")
        if self.mode == "HD" or not self.cfg.exit_enabled(self.mode):
            return None
        if self.mode == "SL":
            price = ask-tick
        elif self.tp_price is not None:
            if tick != self.tp_tick:
                raise ValueError("TP_TICK_CHANGED")
            price = self.tp_price
        elif sigma is None or not math.isfinite(sigma) or sigma < 0:
            raise ValueError("TP_VOLATILITY_UNKNOWN")
        else:
            n = max(1, int((Decimal(str(sigma))/tick).to_integral_value(rounding=ROUND_CEILING)))
            price = bid-n*tick
        if not (tick > 0 and 0 < price < 1 and price % tick == 0):
            raise ValueError("REPAIR_PRICE_OUT_OF_RANGE")
        # Resting orders are not invalidated just because an ask moves through
        # their price; this check is for a NEW post-only submission only.
        if price >= ask:
            raise ValueError("REPAIR_PRICE_WOULD_TAKE")
        return price

    def accepted_tp(self, price: Decimal, tick: Decimal):
        if self.mode == "TP":
            self.tp_price, self.tp_tick = price, tick

    def payload(self):
        return {"schema_version": 2, "mode": self.mode, "reason": self.last_reason,
                "tp_enabled": self.cfg.tp_enabled, "sl_enabled": self.cfg.sl_enabled,
                "peak": str(self.peak), "price_pnl": str(self.price_pnl),
                "path_complete": self.path_complete, "path_breaks": list(self.path_breaks),
                "price_gap": self._price_gap, "inventory_gap": self._inventory_gap,
                "original_qty": self._original_qty,
                "tp_price": str(self.tp_price) if self.tp_price is not None else None,
                "tp_tick": str(self.tp_tick) if self.tp_tick is not None else None,
                "last_transition": self.last_transition,
                "points": [asdict(p) for p in self.points]}

    @classmethod
    def restore(cls, cfg, payload):
        mode = payload["mode"]
        if mode not in {"HD", "TP", "SL"}:
            raise ValueError("REPAIR_MODE_INVALID")
        policy = cls(cfg, mode=mode, peak=payload["peak"], price_pnl=payload["price_pnl"])
        # Restore evidence first, then apply the current configuration once.
        policy.mode = mode
        policy.last_reason = payload["reason"]
        policy.last_transition = payload.get("last_transition")
        policy.tp_price = Decimal(payload["tp_price"]) if payload["tp_price"] else None
        policy.tp_tick = Decimal(payload["tp_tick"]) if payload["tp_tick"] else None
        policy.points.extend(PathPoint(**p) for p in payload["points"])
        if payload.get("schema_version") != 2:
            policy.break_path("LEGACY_SNAPSHOT_PATH_UNKNOWN")
        else:
            policy.path_complete = payload.get("path_complete", False)
            policy.path_breaks = payload.get("path_breaks", [])
            policy._price_gap = payload.get("price_gap", False)
            policy._inventory_gap = payload.get("inventory_gap", False)
            policy._original_qty = payload.get("original_qty")
        policy.apply_switches()
        return policy
