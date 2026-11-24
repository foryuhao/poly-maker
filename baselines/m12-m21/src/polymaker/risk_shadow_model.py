"""Frozen RDP-RISK-002 research arithmetic. No execution or network dependencies."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal

UNKNOWN = "UNKNOWN"
NA = "N/A"
SCHEMA = "RDP_RISK_SHADOW_V2"
CHECKPOINTS = (30, 60, 120, 160, 180, 210, 240, 270)


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


@dataclass(frozen=True)
class Market:
    slug: str
    condition_id: str
    yes_token_id: str
    no_token_id: str
    start: float
    tick: float
    min_size: float = 5


@dataclass(frozen=True)
class Inventory:
    q_yes: float | str = UNKNOWN
    q_no: float | str = UNKNOWN
    validity: str = UNKNOWN
    updated_at: float | str = UNKNOWN
    state: str = UNKNOWN
    revision: int = 0

    @property
    def delta(self):
        return self.q_yes - self.q_no if self.validity == "VALID" else UNKNOWN

    def fields(self):
        d = self.delta
        return {
            "q_yes": self.q_yes,
            "q_no": self.q_no,
            "delta": d,
            "inventory_side": side(d),
            "inventory_validity": self.validity,
            "inventory_updated_at": self.updated_at,
            "actual_strategy_state": self.state,
        }


def side(delta):
    if not number(delta):
        return UNKNOWN
    return "YES" if delta > 0 else "NO" if delta < 0 else "NEUTRAL"


def variance_rate(points):
    """Pure formula, without the production sample-size gate."""
    intervals = [
        (b[0] - a[0], b[1] - a[1])
        for a, b in zip(points, points[1:], strict=False)
        if 0 < b[0] - a[0] <= 5
    ]
    duration = sum(dt for dt, _ in intervals)
    v = sum(dp * dp for _, dp in intervals) / duration if duration else UNKNOWN
    return v, math.sqrt(v) if number(v) else UNKNOWN


def settlement(delta, p):
    fields = (
        "r_settle",
        "settlement_sd",
        "inventory_win_prob",
        "inventory_lose_prob",
        "favorite_side",
        "favorite_probability",
        "inventory_aligned",
        "inventory_market_value",
        "terminal_win_value",
        "terminal_lose_value",
        "win_payoff_change",
        "lose_payoff_change",
        "conditional_downside_loss",
        "expected_terminal_payoff_change",
    )
    out = dict.fromkeys(fields, UNKNOWN)
    out["settlement_model_version"] = "SETTLEMENT_RISK_V1"
    out["settlement_risk_validity"] = UNKNOWN
    if number(p) and 0 <= p <= 1:
        out.update(
            favorite_side="YES" if p > 0.5 else "NO" if p < 0.5 else "TIE",
            favorite_probability=max(p, 1 - p),
        )
    if not number(delta):
        return out
    if delta == 0:
        out.update(
            {
                k: 0
                for k in fields
                if k
                not in {
                    "inventory_win_prob",
                    "inventory_lose_prob",
                    "favorite_side",
                    "favorite_probability",
                    "inventory_aligned",
                }
            }
        )
        out.update(
            inventory_win_prob=NA,
            inventory_lose_prob=NA,
            inventory_aligned=NA,
            settlement_risk_validity="VALID",
        )
        return out
    if not number(p) or not 0 <= p <= 1:
        return out
    win = p if delta > 0 else 1 - p
    n = abs(delta)
    value = n * win
    out.update(
        r_settle=delta * delta * p * (1 - p),
        settlement_sd=math.sqrt(delta * delta * p * (1 - p)),
        inventory_win_prob=win,
        inventory_lose_prob=1 - win,
        inventory_aligned=NA if p == 0.5 else side(delta) == out["favorite_side"],
        inventory_market_value=value,
        terminal_win_value=n,
        terminal_lose_value=0,
        win_payoff_change=n - value,
        lose_payoff_change=-value,
        conditional_downside_loss=value,
        expected_terminal_payoff_change=win * (n - value) - (1 - win) * value,
        settlement_risk_validity="VALID",
    )
    return out


def volatility_risk(delta, v_hat, remaining):
    h = min(30, max(0, remaining))
    r = (
        0
        if delta == 0
        else delta * delta * v_hat * h
        if number(delta) and number(v_hat)
        else UNKNOWN
    )
    return {
        "vol_risk_model_version": "VOL_RISK_V1",
        "risk_horizon_sec": h,
        "risk_model": "FIXED_INVENTORY_LOCAL_VARIANCE_PROXY_V1",
        "r_vol_proxy": r,
        "volatility_sd": math.sqrt(r) if number(r) else UNKNOWN,
        "vol_risk_validity": "VALID" if number(r) else UNKNOWN,
    }


@dataclass(frozen=True)
class Quote:
    bid: float | str = UNKNOWN
    ask: float | str = UNKNOWN
    received: float | str = UNKNOWN
    mono: float | str = UNKNOWN
    source: float | str = UNKNOWN
    tick: float | str = UNKNOWN
    generation: int = 0
    identity: bool = True
    event_id: str = ""

    def fields(self, now):
        age = (now - self.received) * 1000 if number(self.received) else UNKNOWN
        valid_prices = number(self.bid) and number(self.ask) and 0 <= self.bid <= self.ask <= 1
        validity = (
            "INVALID_IDENTITY"
            if not self.identity
            else "MISSING"
            if not valid_prices or not number(age)
            else "CLOCK_RESET"
            if age < 0
            else "STALE"
            if age > 5000
            else "VALID"
        )
        return {
            "best_bid": self.bid,
            "best_ask": self.ask,
            "mid": (self.bid + self.ask) / 2 if valid_prices else UNKNOWN,
            "spread": self.ask - self.bid if valid_prices else UNKNOWN,
            "quote_source_timestamp": self.source,
            "local_receive_timestamp": self.received,
            "quote_age_ms": age,
            "quote_validity": validity,
            "official_tick_size": self.tick,
            "event_id": self.event_id,
        }


@dataclass(frozen=True)
class Point:
    wall: float
    mono: float
    mid: float
    generation: int
    inventory: Inventory
    event_id: str


@dataclass
class Volatility:
    # Only a last point per real one-second bucket; never sample the cache here.
    buckets: dict[int, Point] = field(default_factory=dict)
    last_final: Point | None = None
    realized: float = 0
    realized_count: int = 0
    realized_duration: float = 0
    realized_gaps: int = 0
    transitions: int = 0
    gap_reasons: dict[str, int] = field(default_factory=dict)
    finalized_bucket: int = -1000000000

    def finalize(self, before_bucket):
        for key in sorted(k for k in self.buckets if k < before_bucket):
            point = self.buckets[key]
            if key > self.finalized_bucket:
                self.finalized_bucket = key
                a = self.last_final
                if a is not None:
                    dt = point.mono - a.mono
                    reason = (
                        "PRICE_GAP"
                        if dt > 5 or dt <= 0 or a.generation != point.generation
                        else "INVENTORY_UNKNOWN"
                        if a.inventory.validity != "VALID" or point.inventory.validity != "VALID"
                        else "INVENTORY_TRANSITION_GAP"
                        if a.inventory.revision != point.inventory.revision
                        else None
                    )
                    if reason:
                        self.realized_gaps += 1
                        self.transitions += reason == "INVENTORY_TRANSITION_GAP"
                        self.gap_reasons[reason] = self.gap_reasons.get(reason, 0) + 1
                    else:
                        self.realized += a.inventory.delta**2 * (point.mid - a.mid) ** 2
                        self.realized_count += 1
                        self.realized_duration += dt
                self.last_final = point

    def observe(self, bucket, point):
        self.finalize(bucket)
        if bucket <= self.finalized_bucket:
            self.realized_gaps += 1
            self.gap_reasons["PRICE_GAP"] = self.gap_reasons.get("PRICE_GAP", 0) + 1
        self.buckets[bucket] = point
        # Bounded history. last_final retains only the cumulative endpoint.
        self.buckets = {k: v for k, v in self.buckets.items() if k >= bucket - 32}

    def estimate(self, wall, mono, start, quote_validity, latest_age):
        self.finalize(math.floor(wall - start))
        pts = sorted(
            (p for p in self.buckets.values() if mono - 30 <= p.mono <= mono), key=lambda p: p.mono
        )
        intervals = [
            (a, b)
            for a, b in zip(pts, pts[1:], strict=False)
            if 0 < b.mono - a.mono <= 5 and a.generation == b.generation
        ]
        duration = sum(b.mono - a.mono for a, b in intervals)
        count = len(intervals)
        validity = (
            "MISSING_PRICE"
            if quote_validity != "VALID" and quote_validity != "STALE"
            else "STALE_PRICE"
            if quote_validity == "STALE"
            else "INSUFFICIENT_DATA"
            if count < 10 or duration < 20
            else "VALID"
        )
        v = (
            sum((b.mid - a.mid) ** 2 for a, b in intervals) / duration
            if validity == "VALID"
            else UNKNOWN
        )
        return {
            "volatility_model_version": "VOLATILITY_V1",
            "vol_lookback_sec": 30,
            "vol_interval_count": count,
            "vol_valid_duration_sec": duration,
            "vol_gap_count": max(0, len(pts) - 1 - count),
            "vol_latest_quote_age_ms": latest_age,
            "v_hat": v,
            "sigma_hat": math.sqrt(v) if number(v) else UNKNOWN,
            "volatility_validity": validity,
            **self.realized_fields(),
        }

    def realized_fields(self):
        return {
            "realized_model_version": "REALIZED_VOL_RISK_V1",
            "realized_risk_interpretation": "DIRECTIONAL_PRICE_SQUARED_EXPOSURE_PROXY",
            "r_vol_realized": self.realized if self.realized_count else UNKNOWN,
            "realized_interval_count": self.realized_count,
            "realized_valid_duration_sec": self.realized_duration,
            "realized_gap_count": self.realized_gaps,
            "realized_inventory_transition_count": self.transitions,
            "realized_gap_reasons": dict(self.gap_reasons),
            "realized_coverage_status": "PARTIAL"
            if self.realized_gaps
            else "OBSERVED_INTERVALS_ONLY"
            if self.realized_count
            else UNKNOWN,
        }


class ShadowModel:
    def __init__(self, market, session_id=NA):
        self.market, self.session_id = market, session_id
        self.quotes = {"YES": Quote(tick=market.tick), "NO": Quote(tick=market.tick)}
        self.inventory = Inventory()
        self.tick_updated_at = {"YES": UNKNOWN, "NO": UNKNOWN}
        self.vol = Volatility()
        self.slots = set()

    def inventory_update(
        self, yes=UNKNOWN, no=UNKNOWN, *, validity=UNKNOWN, timestamp=UNKNOWN, state=UNKNOWN
    ):
        old = self.inventory
        valid = validity == "VALID" and all(number(x) and x >= 0 for x in (yes, no))
        validity = "VALID" if valid else UNKNOWN
        changed = (old.q_yes, old.q_no, old.validity) != (yes, no, validity)
        self.inventory = Inventory(
            yes if valid else UNKNOWN,
            no if valid else UNKNOWN,
            validity,
            timestamp,
            state,
            old.revision + int(changed),
        )

    def base(self, now):
        return {
            "schema_version": SCHEMA,
            "market_slug": self.market.slug,
            "condition_id": self.market.condition_id,
            "session_id": self.session_id,
            "timestamp": now,
        }

    def quote_update(self, outcome, quote):
        if outcome not in self.quotes:
            return False
        previous = self.quotes[outcome]
        if quote.event_id and quote.event_id == previous.event_id:
            return False
        if number(previous.mono) and quote.mono < previous.mono:
            return False
        self.quotes[outcome] = quote
        if quote.tick != previous.tick:
            self.tick_updated_at[outcome] = quote.received
        if (
            outcome == "YES"
            and quote.fields(quote.received)["quote_validity"] == "VALID"
            and self.market_snapshot(quote.received)["probability_validity"] == "VALID"
        ):
            inv = self.inventory
            if number(inv.updated_at) and inv.updated_at > quote.received:
                inv = Inventory(revision=inv.revision)
            self.vol.observe(
                math.floor(quote.received - self.market.start),
                Point(
                    quote.received,
                    quote.mono,
                    (quote.bid + quote.ask) / 2,
                    quote.generation,
                    inv,
                    quote.event_id,
                ),
            )
        return True

    def market_snapshot(self, now):
        y, n = (self.quotes[s].fields(now) for s in ("YES", "NO"))
        cross, error = "NOT_AVAILABLE", UNKNOWN
        if y["quote_validity"] == n["quote_validity"] == "VALID":
            if abs(y["local_receive_timestamp"] - n["local_receive_timestamp"]) > 1:
                cross = "ASYNCHRONOUS"
            else:
                # Decimal prices preserve the exact frozen 0.02 threshold.
                y_mid = (Decimal(str(y["best_bid"])) + Decimal(str(y["best_ask"]))) / 2
                n_mid = (Decimal(str(n["best_bid"])) + Decimal(str(n["best_ask"]))) / 2
                exact_error = abs(y_mid - (1 - n_mid))
                error = float(exact_error)
                cross = "LOW_CONFIDENCE" if exact_error > Decimal("0.02") else "VALID"
        validity = "LOW_CONFIDENCE" if cross == "LOW_CONFIDENCE" else y["quote_validity"]
        return {
            **self.base(now),
            "market_start_utc": self.market.start,
            "yes_token_id": self.market.yes_token_id,
            "no_token_id": self.market.no_token_id,
            "snapshot_utc": now,
            "elapsed_sec": now - self.market.start,
            "remaining_sec": max(0, 300 - (now - self.market.start)),
            **{f"yes_{k}": v for k, v in y.items()},
            **{f"no_{k}": v for k, v in n.items()},
            "p_yes": y["mid"] if y["quote_validity"] == "VALID" else UNKNOWN,
            "p_yes_from_no": 1 - n["mid"] if n["quote_validity"] == "VALID" else UNKNOWN,
            "probability_crosscheck_error": error,
            "crosscheck_status": cross,
            "official_tick_size": {"YES": y["official_tick_size"], "NO": n["official_tick_size"]},
            "quote_validity": validity,
            "probability_validity": "VALID" if validity == "VALID" else UNKNOWN,
            "quote_gap_reason": NA if validity == "VALID" else validity,
        }

    def risk(self, snapshot, mono):
        now = snapshot["timestamp"]
        v = self.vol.estimate(
            now, mono, self.market.start, snapshot["quote_validity"], snapshot["yes_quote_age_ms"]
        )
        p = snapshot["p_yes"] if snapshot["probability_validity"] == "VALID" else UNKNOWN
        d = self.inventory.delta
        return {
            **self.base(now),
            "elapsed_sec": snapshot["elapsed_sec"],
            "remaining_sec": snapshot["remaining_sec"],
            "p_yes": p,
            "probability_validity": snapshot["probability_validity"],
            **self.inventory.fields(),
            **v,
            **volatility_risk(d, v["v_hat"], snapshot["remaining_sec"]),
            **settlement(d, p),
        }

    def hypothetical(self, risk):
        result = {
            **self.base(risk["timestamp"]),
            "inventory_source": "HYPOTHETICAL",
            "virtual_delta_plus5": 5,
            "virtual_delta_minus5": -5,
            "elapsed_sec": risk["elapsed_sec"],
            "p_yes": risk["p_yes"],
            "volatility_model_version": "VOLATILITY_V1",
            "vol_risk_model_version": "VOL_RISK_V1",
            "settlement_model_version": "SETTLEMENT_RISK_V1",
        }
        for name, delta in (("plus5", 5), ("minus5", -5)):
            vol = volatility_risk(delta, risk["v_hat"], risk["remaining_sec"])
            settle = settlement(delta, risk["p_yes"])
            for field_name, value in (
                ("r_vol", vol["r_vol_proxy"]),
                ("r_settle", settle["r_settle"]),
                ("settlement_sd", settle["settlement_sd"]),
                ("win_prob", settle["inventory_win_prob"]),
            ):
                result[f"virtual_{name}_{field_name}"] = value
        return result

    def sample(self, slot, now, mono):
        if slot in self.slots or not 0 <= slot < 300:
            return None
        self.slots.add(slot)
        scheduled = self.market.start + slot
        snapshot = self.market_snapshot(now)
        missed = now < scheduled or now >= scheduled + 1
        snapshot.update(
            slot_index=slot,
            scheduled_snapshot_time=scheduled,
            actual_snapshot_time=now,
            snapshot_delay_ms=(now - scheduled) * 1000,
            snapshot_monotonic=mono,
            slot_validity="MISSED" if missed else "OBSERVED",
            checkpoint=slot in CHECKPOINTS,
            snapshot_id=f"{self.market.slug}:{self.session_id}:{slot}",
        )
        if missed:
            # No retrospective cache/inventory use for an unobserved slot.
            for prefix in ("yes", "no"):
                for key in self.quotes[prefix.upper()].fields(now):
                    snapshot[f"{prefix}_{key}"] = UNKNOWN
                snapshot[f"{prefix}_quote_validity"] = "MISSING"
            snapshot.update(
                p_yes=UNKNOWN,
                p_yes_from_no=UNKNOWN,
                probability_crosscheck_error=UNKNOWN,
                crosscheck_status="NOT_AVAILABLE",
                quote_validity="MISSING",
                probability_validity=UNKNOWN,
                quote_gap_reason="SLOT_MISSED",
            )
        risk = self.risk(snapshot, mono)
        if missed:
            risk.update(Inventory().fields())
            risk.update(settlement(UNKNOWN, UNKNOWN))
            risk.update(volatility_risk(UNKNOWN, UNKNOWN, snapshot["remaining_sec"]))
        risk["risk_snapshot_id"] = snapshot["snapshot_id"]
        return snapshot, risk, self.hypothetical(risk)


def hold_candidate(snapshot, risk):
    if (
        snapshot["probability_validity"] != "VALID"
        or risk["inventory_validity"] != "VALID"
        or not number(risk["favorite_probability"])
    ):
        return UNKNOWN
    return (
        snapshot["elapsed_sec"] >= 240
        and risk["delta"] != 0
        and risk["favorite_probability"] >= 0.95
        and risk["inventory_aligned"] is True
    )


def candidate_prices(snapshot, outcome, size, min_size):
    tick = snapshot.get(f"{outcome.lower()}_official_tick_size", UNKNOWN)
    bid = snapshot.get(f"{outcome.lower()}_best_bid", UNKNOWN)
    ask = snapshot.get(f"{outcome.lower()}_best_ask", UNKNOWN)
    out = {}
    for offset in (0, 1, 2, 5):
        reason, price = "TICK_UNKNOWN", UNKNOWN
        if number(tick) and tick in (0.1, 0.01, 0.001, 0.0001):
            reason = "INVALID_CANDIDATE_QUOTE"
            if all(number(x) for x in (bid, ask)):
                t = Decimal(str(tick))
                q = Decimal(str(bid)) - offset * t
                size_ok = (
                    number(size)
                    and size >= max(5, min_size)
                    and Decimal(str(size)) % Decimal(".01") == 0
                    and q * Decimal(str(size)) >= 1
                )
                if t <= q <= 1 - t and q % t == 0 and q < Decimal(str(ask)) and size_ok:
                    price, reason = float(q), "VALID"
        out[f"candidate_bid_{offset}tick"] = price
        out[f"candidate_bid_{offset}tick_validity"] = reason
    return out


def compare_decision(actual, snapshot, risk, min_size=5):
    """Immutable, causal comparison. Never returns an instruction to execution."""
    elapsed = snapshot["elapsed_sec"]
    offset = 5 if elapsed < 60 else 2 if elapsed < 150 else 0
    delta = risk["delta"]
    outcome = (
        "NO" if number(delta) and delta > 0 else "YES" if number(delta) and delta < 0 else UNKNOWN
    )
    size = actual.get("actual_intended_size", UNKNOWN)
    candidates = candidate_prices(snapshot, outcome, size, min_size)
    quote = candidates[f"candidate_bid_{offset}tick"]
    action = "PASSIVE_REBALANCE" if elapsed < 150 else "ACTIVE_REBALANCE"
    reason = "SAME_QUOTE"
    if risk["inventory_validity"] != "VALID":
        reason = "INSUFFICIENT_INVENTORY_DATA"
    elif delta == 0:
        action, reason = "NONE", "ACTUAL_NO_QUOTE"
    elif (
        snapshot["probability_validity"] != "VALID"
        or snapshot.get(f"{outcome.lower()}_quote_validity") != "VALID"
    ):
        reason = "INSUFFICIENT_MARKET_DATA"
    elif not 0 <= elapsed < 300 or not number(quote):
        reason = "INVALID_CANDIDATE_QUOTE"
    elif not number(actual.get("actual_intended_price")):
        reason = "ACTUAL_NO_QUOTE"
    elif actual.get("actual_side") != f"BUY {outcome}":
        reason = "SIDE_DIFFERENCE"
    elif actual["actual_intended_price"] != quote:
        reason = "PRICE_DIFFERENCE"
    valid = reason in {"SAME_QUOTE", "PRICE_DIFFERENCE", "SIDE_DIFFERENCE"}
    if not valid and action != "NONE":
        action = "UNDETERMINED"
    quote = quote if valid else UNKNOWN
    comparable = reason in {"SAME_QUOTE", "PRICE_DIFFERENCE"}
    diff = (
        float(Decimal(str(actual["actual_intended_price"])) - Decimal(str(quote)))
        if comparable
        else UNKNOWN
    )
    tick = snapshot.get(f"{outcome.lower()}_official_tick_size", UNKNOWN)
    return {
        **actual,
        **{
            k: risk[k]
            for k in (
                "p_yes",
                "delta",
                "sigma_hat",
                "r_vol_proxy",
                "r_vol_realized",
                "r_settle",
                "settlement_sd",
                "inventory_win_prob",
                "inventory_lose_prob",
                "risk_snapshot_id",
            )
        },
        "shadow_policy_version": "SHADOW_TIME_POLICY_V0",
        "shadow_action": action,
        "shadow_side": f"BUY {outcome}" if outcome != UNKNOWN else UNKNOWN,
        "shadow_quote": quote,
        "shadow_size": size,
        "shadow_tick_offset": offset,
        "hold_candidate": hold_candidate(snapshot, risk),
        **candidates,
        "reason_code": candidates[f"candidate_bid_{offset}tick_validity"]
        if reason == "INVALID_CANDIDATE_QUOTE"
        else reason,
        "decision_difference_type": reason,
        "price_difference": diff,
        "price_difference_ticks": diff / tick if comparable else UNKNOWN,
        "price_difference_usdc": diff * size if comparable and number(size) else UNKNOWN,
        "comparison_interpretation": "QUOTE_DIFFERENCE_ONLY_NOT_PNL_OR_FILL",
    }
