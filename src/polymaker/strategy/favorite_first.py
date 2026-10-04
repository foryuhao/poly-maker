"""One-cycle, buy-only BTC5m quote policy. No I/O or economic ledger lives here."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import Enum

from polymaker.domain import Quote, Side
from polymaker.marketdata.orderbook import BookView


class StrategyState(str, Enum):
    OBSERVE = "OBSERVE"
    ENTRY_CONFIRM = "ENTRY_CONFIRM"
    ENTRY_QUOTING = "ENTRY_QUOTING"
    FIRST_LEG_RISK_WINDOW = "FIRST_LEG_RISK_WINDOW"
    RUNNER = "RUNNER"
    PAIR_STOP = "PAIR_STOP"
    FORCED_PAIR = "FORCED_PAIR"
    UNPAIRABLE_RESIDUAL = "UNPAIRABLE_RESIDUAL"
    COMPLETE = "COMPLETE"
    NO_TRADE = "NO_TRADE"
    HALTED = "HALTED"


class PairQuoteMode(str, Enum):
    PASSIVE = "PASSIVE"
    TRAILING = "TRAILING"
    AGGRESSIVE = "AGGRESSIVE"
    FORCED = "FORCED"


class PairTrigger(str, Enum):
    EMERGENCY_STOP = "EMERGENCY_STOP"
    PROBATION_FAILED = "PROBATION_FAILED"
    TRAILING_STOP = "TRAILING_STOP"
    TIME_FORCED = "TIME_FORCED"


@dataclass(frozen=True)
class FavoriteFirstConfig:
    max_pair_loss_usd: float
    # Deprecated compatibility field. The single-leg risk window never quotes
    # the opposite token, so live callers need not supply this value.
    probation_passive_offset_ticks: int = 0
    runner_passive_offset_ticks: int = 2
    normal_reprice_min_s: float = 0.75
    urgent_reprice_min_s: float = 0.30
    first_leg_qty: float = 5.0
    entry_start_s: float = 30.0
    last_new_entry_s: float = 180.0
    favorite_min: float = 0.60
    favorite_max: float = 0.70
    favorite_confirm_s: float = 2.0
    probation_max_s: float = 20.0
    trailing_arm_usd: float = 0.05
    trailing_drawdown_fraction: float = 0.50
    forced_pair_start_s: float = 240.0
    hard_order_cutoff_s: float = 270.0

    def __post_init__(self) -> None:
        if (
            self.max_pair_loss_usd <= 0
            or not 0 < self.first_leg_qty <= 5
            or min(self.probation_passive_offset_ticks, self.runner_passive_offset_ticks) < 0
            or min(self.normal_reprice_min_s, self.urgent_reprice_min_s) < 0
            or not 0 <= self.trailing_drawdown_fraction <= 1
        ):
            raise ValueError("invalid Favorite-First risk or quoting configuration")


@dataclass(frozen=True)
class StrategyMemory:
    state: StrategyState = StrategyState.OBSERVE
    confirming_side: str | None = None
    confirming_since: float | None = None
    first_leg_side: str | None = None
    first_fill_ts: float | None = None
    peak_locked_pnl: float | None = None
    pair_trigger: PairTrigger | None = None
    first_leg_final_qty: float | None = None
    residual_reason: str | None = None
    first_leg_replacement_pending: bool = False


@dataclass(frozen=True)
class StrategySnapshot:
    now: float
    market_start_ts: float
    yes: BookView
    no: BookView
    yes_qty: float
    no_qty: float
    yes_vwap: float
    no_vwap: float
    yes_token: str
    no_token: str
    tick: float
    ready: bool = True
    first_fill_ts: float | None = None
    first_leg_side: str | None = None
    first_leg_working_remaining: float = 0.0
    first_leg_working_price: float | None = None
    first_leg_finalized: bool = False
    pair_working_remaining: float = 0.0
    pair_working_price: float | None = None
    pair_terminal_authoritative: bool = False
    venue_min_order_size: float = 0.0
    account_position_mismatch: bool = False


@dataclass(frozen=True)
class StrategyDecision:
    memory: StrategyMemory
    quote: Quote | None
    favorite_side: str | None
    favorite_probability: float | None
    favorite_confirm_age: float
    first_leg_qty: float
    first_leg_vwap: float | None
    paired_qty: float
    unpaired_qty: float
    unpaired_opposite_qty: float
    current_locked_pnl: float | None
    peak_locked_pnl: float | None
    trailing_floor: float | None
    aggressive_opposite_price: float | None
    pair_quote_mode: PairQuoteMode | None
    paired_locked_pnl: float | None
    residual_lockable_pnl: float | None
    first_leg_finalize_needed: bool
    pair_finalize_needed: bool
    submin_working_remainder: bool


def _legal_buy(price: float, ask: float, tick: float) -> float | None:
    if tick <= 0 or not math.isfinite(price):
        return None
    ticks = math.floor((price + 1e-10) / tick)
    result = round(ticks * tick, 10)
    return result if 0 < result < ask and result < 1 else None


def evaluate(
    snapshot: StrategySnapshot, memory: StrategyMemory, config: FavoriteFirstConfig
) -> StrategyDecision:
    """Return the next explicit state and at most one maker BUY target."""
    s, m, c = snapshot, memory, config
    elapsed = s.now - s.market_start_ts
    views = {"YES": s.yes, "NO": s.no}
    tokens = {"YES": s.yes_token, "NO": s.no_token}
    quantities = {"YES": s.yes_qty, "NO": s.no_qty}
    vwaps = {"YES": s.yes_vwap, "NO": s.no_vwap}
    valid = (
        s.ready
        and s.tick > 0
        and all(
            v.best_bid is not None and v.best_ask is not None and 0 < v.best_bid < v.best_ask < 1
            for v in views.values()
        )
    )
    favorite = probability = None
    if valid:
        mids = {name: view.mid for name, view in views.items()}
        favorite = max(mids, key=lambda name: mids[name])
        probability = mids[favorite]
    # The snapshot quantities are accepted fills scoped to this strategy's
    # registered order IDs. Account positions never establish strategy identity.
    first = m.first_leg_side or s.first_leg_side
    state = m.state
    if first is not None and state in (
        StrategyState.OBSERVE,
        StrategyState.ENTRY_CONFIRM,
        StrategyState.ENTRY_QUOTING,
        StrategyState.NO_TRADE,
    ):
        state = StrategyState.FIRST_LEG_RISK_WINDOW
        m = replace(
            m,
            first_leg_side=first,
            first_fill_ts=m.first_fill_ts if m.first_fill_ts is not None else s.first_fill_ts,
            confirming_side=None,
            confirming_since=None,
        )
    first_qty = quantities[first] if first else 0.0
    opposite = "NO" if first == "YES" else "YES"
    opposite_qty = quantities[opposite] if first else 0.0
    paired = min(first_qty, opposite_qty)
    unpaired = max(0.0, first_qty - opposite_qty)
    unpaired_opposite = max(0.0, opposite_qty - first_qty)
    quote = None
    mode = None
    aggressive = current = floor = paired_pnl = residual_pnl = None
    confirm_age = 0.0
    finalize_first = finalize_pair = submin_working = False
    venue_min = s.venue_min_order_size
    if s.account_position_mismatch or (
        first and m.first_leg_final_qty is not None and first_qty > m.first_leg_final_qty + 1e-8
    ):
        state = StrategyState.HALTED
        m = replace(m, residual_reason="STRATEGY_ACCOUNT_POSITION_MISMATCH")
    elif elapsed >= c.hard_order_cutoff_s:
        state = (
            StrategyState.COMPLETE
            if first and unpaired <= 1e-8
            else (StrategyState.NO_TRADE if not first else StrategyState.HALTED)
        )
        if first and unpaired > 1e-8:
            m = replace(m, residual_reason="T270_RESIDUAL_INVENTORY")
    elif state in (
        StrategyState.HALTED,
        StrategyState.UNPAIRABLE_RESIDUAL,
        StrategyState.COMPLETE,
        StrategyState.NO_TRADE,
    ):
        pass
    elif first and state != StrategyState.FIRST_LEG_RISK_WINDOW and unpaired <= 1e-8:
        state = StrategyState.COMPLETE
        paired_pnl = paired * (1 - vwaps[first] - vwaps[opposite])
        residual_pnl = 0.0
        current = paired_pnl
    elif first:
        view = views[opposite]
        if valid:
            aggressive = _legal_buy(view.best_ask - s.tick, view.best_ask, s.tick)
        if opposite_qty > first_qty + 1e-8 or first_qty > c.first_leg_qty + 1e-8:
            state = StrategyState.HALTED
            m = replace(m, residual_reason="STRATEGY_FILL_QUANTITY_EXCEEDED")
        elif state == StrategyState.FIRST_LEG_RISK_WINDOW:
            if m.first_fill_ts is None:
                state = StrategyState.HALTED
                m = replace(m, residual_reason="FIRST_FILL_TIME_MISSING")
            else:
                if aggressive is not None:
                    residual_pnl = first_qty * (1 - vwaps[first] - aggressive)
                    current = residual_pnl
                age = s.now - m.first_fill_ts
                emergency = current is not None and current <= -c.max_pair_loss_usd + 1e-9
                if emergency and m.pair_trigger is None:
                    m = replace(m, pair_trigger=PairTrigger.EMERGENCY_STOP)
                end_window = (
                    emergency
                    or m.pair_trigger is PairTrigger.EMERGENCY_STOP
                    or age >= c.probation_max_s
                )
                if end_window:
                    if not s.first_leg_finalized:
                        finalize_first = True
                    elif first_qty + 1e-8 < venue_min:
                        state = StrategyState.UNPAIRABLE_RESIDUAL
                        m = replace(
                            m,
                            first_leg_final_qty=first_qty,
                            residual_reason="FINAL_FIRST_LEG_BELOW_VENUE_MINIMUM",
                        )
                    elif current is not None:
                        m = replace(m, first_leg_final_qty=first_qty)
                        state = (
                            StrategyState.PAIR_STOP
                            if m.pair_trigger is PairTrigger.EMERGENCY_STOP or current <= 0
                            else StrategyState.RUNNER
                        )
                        if state is StrategyState.PAIR_STOP and m.pair_trigger is None:
                            m = replace(m, pair_trigger=PairTrigger.PROBATION_FAILED)
                elif (
                    valid
                    and favorite == first
                    and c.favorite_min - 1e-10 <= probability <= c.favorite_max + 1e-10
                    and (s.first_leg_working_remaining > 1e-8 or m.first_leg_replacement_pending)
                ):
                    # A replacement is possible only when Engine explicitly
                    # verified the cancellation of a legal working remainder.
                    price = _legal_buy(views[first].best_bid, views[first].best_ask, s.tick)
                    if price is not None:
                        remaining = (
                            s.first_leg_working_remaining
                            if s.first_leg_working_remaining > 1e-8
                            else max(0.0, c.first_leg_qty - first_qty)
                        )
                        if remaining + 1e-8 >= venue_min or s.first_leg_working_remaining > 1e-8:
                            quote = Quote(tokens[first], Side.BUY, price, remaining)
                elif m.first_leg_replacement_pending:
                    m = replace(m, first_leg_replacement_pending=False)
        if state in (StrategyState.RUNNER, StrategyState.PAIR_STOP, StrategyState.FORCED_PAIR):
            paired_pnl = paired * (1 - vwaps[first] - vwaps[opposite])
            if unpaired <= 1e-8:
                residual_pnl = 0.0
                current = paired_pnl
                state = StrategyState.COMPLETE
            elif aggressive is not None:
                residual_pnl = unpaired * (1 - vwaps[first] - aggressive)
                current = paired_pnl + residual_pnl
                peak = max(m.peak_locked_pnl if m.peak_locked_pnl is not None else current, current)
                m = replace(m, peak_locked_pnl=peak)
                if peak >= c.trailing_arm_usd:
                    floor = peak * (1 - c.trailing_drawdown_fraction)
                if elapsed >= c.forced_pair_start_s:
                    state = StrategyState.FORCED_PAIR
                    if m.pair_trigger is None:
                        m = replace(m, pair_trigger=PairTrigger.TIME_FORCED)
                elif (
                    state == StrategyState.RUNNER and floor is not None and current <= floor + 1e-9
                ):
                    state = StrategyState.PAIR_STOP
                    m = replace(m, pair_trigger=PairTrigger.TRAILING_STOP)
            mode = {
                StrategyState.RUNNER: PairQuoteMode.TRAILING,
                StrategyState.PAIR_STOP: PairQuoteMode.AGGRESSIVE,
                StrategyState.FORCED_PAIR: PairQuoteMode.FORCED,
            }.get(state)
            if mode is not None and unpaired > 1e-8:
                if unpaired + 1e-8 < venue_min and s.pair_working_remaining <= 1e-8:
                    if s.pair_terminal_authoritative:
                        state = StrategyState.UNPAIRABLE_RESIDUAL
                        m = replace(m, residual_reason="PAIR_RESIDUAL_BELOW_VENUE_MINIMUM")
                        mode = None
                    else:
                        finalize_pair = True
                elif valid and aggressive is not None:
                    offset = c.runner_passive_offset_ticks if mode is PairQuoteMode.TRAILING else 0
                    price = _legal_buy(aggressive - offset * s.tick, view.best_ask, s.tick)
                    if price is not None:
                        quote = Quote(tokens[opposite], Side.BUY, price, unpaired)
    elif state not in (StrategyState.NO_TRADE, StrategyState.HALTED, StrategyState.COMPLETE):
        if elapsed > c.last_new_entry_s:
            state = StrategyState.NO_TRADE
            m = replace(m, confirming_side=None, confirming_since=None)
        elif elapsed < c.entry_start_s:
            state = StrategyState.OBSERVE
        elif valid and c.favorite_min - 1e-10 <= probability <= c.favorite_max + 1e-10:
            if m.confirming_side != favorite or m.confirming_since is None:
                m = replace(m, confirming_side=favorite, confirming_since=s.now)
            confirm_age = max(0.0, s.now - m.confirming_since)
            state = (
                StrategyState.ENTRY_QUOTING
                if confirm_age >= c.favorite_confirm_s
                else StrategyState.ENTRY_CONFIRM
            )
            if state == StrategyState.ENTRY_QUOTING:
                view = views[favorite]
                price = _legal_buy(view.best_bid, view.best_ask, s.tick)
                if price is not None:
                    quote = Quote(tokens[favorite], Side.BUY, price, c.first_leg_qty)
        else:
            state = StrategyState.ENTRY_CONFIRM
            m = replace(m, confirming_side=None, confirming_since=None)
    m = replace(m, state=state)
    if quote is not None and quote.size + 1e-8 < venue_min:
        working = (
            (
                s.first_leg_working_remaining
                if quote.token_id == tokens[first]
                else s.pair_working_remaining
            )
            if first
            else 0.0
        )
        working_price = (
            (s.first_leg_working_price if quote.token_id == tokens[first] else s.pair_working_price)
            if first
            else None
        )
        if working > 1e-8 and working_price is not None:
            quote = Quote(quote.token_id, Side.BUY, working_price, working)
            submin_working = True
        else:
            quote = None
    return StrategyDecision(
        m,
        quote,
        favorite,
        probability,
        confirm_age,
        first_qty,
        vwaps[first] if first else None,
        paired,
        unpaired,
        unpaired_opposite,
        current,
        m.peak_locked_pnl,
        floor,
        aggressive,
        mode,
        paired_pnl,
        residual_pnl,
        finalize_first,
        finalize_pair,
        submin_working,
    )
