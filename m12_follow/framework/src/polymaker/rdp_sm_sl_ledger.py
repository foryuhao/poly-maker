"""SL execution quantities and reservations; economic truth stays in the SM.

All amounts use Decimal. Terminal proofs and stable UNKNOWN recovery can release
unfilled reservations. MATCHED stays possible exposure until economically proved.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from decimal import Decimal

from polymaker.domain import TradeState
from polymaker.rdp_sm import MIN_REPAIR_EXPOSURE
from polymaker.state.tracker import fill_cost

ZERO = Decimal(0)
SMALL = MIN_REPAIR_EXPOSURE


@dataclass(frozen=True)
class Exposure:
    actual: Decimal
    execution: Decimal
    yes_pending: Decimal
    no_pending: Decimal
    spent: Decimal
    reserved: Decimal

    @property
    def interval(self):
        return self.actual-self.no_pending, self.actual+self.yes_pending

    def allows(self, side, quantity, price, capital, limit=Decimal(5)):
        return self.admission_reason(side, quantity, price, capital, limit) is None

    def admission_reason(self, side, quantity, price, capital, limit=Decimal(5)):
        lo, hi = self.interval
        lo -= quantity if side == "NO" else ZERO
        hi += quantity if side == "YES" else ZERO
        before = max(abs(self.interval[0]), abs(self.interval[1]))
        bounds = (max(abs(lo), abs(hi)) <= limit if abs(self.actual) <= limit
                  else max(abs(lo), abs(hi)) <= before
                  and lo >= min(self.interval[0], -limit)
                  and hi <= max(self.interval[1], limit))
        signed = quantity if side == "YES" else -quantity
        if self.spent+self.reserved+quantity*price > capital:
            return "WAIT_CAPITAL_CAPACITY"
        if not bounds:
            return "WAIT_DELTA_CAPACITY"
        if abs(self.execution+signed) >= abs(self.execution):
            return "REPAIR_QUANTITY_NOT_REDUCING"
        return None


def exposure(executor, *, retain_unknown_reservations=False):
    actual_fills = executor.economic_fills()
    proved = {(f.trade_id, f.maker_order_id) for f in actual_fills}
    y, n = executor.economic_inventory()
    execution = y-n
    for fill in executor.processor.economic_fills_for_orders(executor.known):
        if ((fill.trade_id, fill.maker_order_id) not in proved
                and fill.status in {TradeState.MATCHED, TradeState.MINED, TradeState.RETRYING}):
            execution += Decimal(str(fill.size)) * (1 if fill.token_id == executor.meta.yes.token_id else -1)
    spent = sum((fill_cost(f) for f in actual_fills), ZERO)
    pending = {"YES": ZERO, "NO": ZERO}
    reserved = ZERO
    releases = getattr(executor, "sl_terminal_quantities", {})
    recovery = getattr(executor, "unknown_repair_recovery", None)
    # Recovery validates its account facts against the unreleased ledger, so
    # checking a release cannot recursively authorize itself.
    unknown_releases = (recovery.released_order_ids()
                        if recovery is not None and not retain_unknown_reservations else set())
    for a in executor.attempts:
        if a.outcome in {"ABORTED", "REJECTED"}:
            continue
        oid = a.order_id or a.expected_order_id
        filled = sum((Decimal(str(f.size)) for f in actual_fills if f.maker_order_id == oid), ZERO)
        if oid in unknown_releases or oid in executor.proofs and oid in executor.terminal:
            possible = ZERO
        else:
            ceiling = releases.get(oid, Decimal(str(a.quote.size)))
            possible = max(ZERO, ceiling-filled)
        pending[a.quote.outcome] += possible
        reserved += possible*Decimal(str(a.quote.price))
        if possible:
            reserved += Decimal(getattr(a, "fee_reserve_usdc", "0"))*possible/Decimal(str(a.quote.size))
    return Exposure(y-n, execution, pending["YES"], pending["NO"], spent, reserved)


def _fifo(fills, stamps, yes_token, *, pairs=None):
    """Shared FIFO allocation for residual inventory and completed pair costs."""
    queues = {True: deque(), False: deque()}
    paired = {}
    ordered = sorted(fills, key=lambda f: (
        stamps.get((f.trade_id, f.maker_order_id), {}).get("fill_monotonic",
            stamps.get((f.trade_id, f.maker_order_id), {}).get("elapsed", float("inf"))),
        stamps.get((f.trade_id, f.maker_order_id), {}).get("observed_monotonic",
            stamps.get((f.trade_id, f.maker_order_id), {}).get("observed_elapsed", float("inf"))),
        f.trade_id, f.maker_order_id))
    for fill in ordered:
        yes = fill.token_id == yes_token
        qty = Decimal(str(fill.size))
        opposite = queues[not yes]
        while qty > 0 and opposite:
            old, remaining = opposite[0]
            used = min(qty, remaining)
            if pairs is not None:
                pairs.append((old, fill, used))
            for leg in (old, fill):
                key = (leg.trade_id, leg.maker_order_id)
                paired[key] = paired.get(key, ZERO)+used
            qty -= used
            remaining -= used
            if remaining:
                opposite[0] = (old, remaining)
            else:
                opposite.popleft()
        if qty:
            queues[yes].append((fill, qty))
    return queues, paired


def fifo_paired_quantities(fills, stamps, yes_token):
    """Actual paired quantity per fill identity, excluding every residual share."""
    return _fifo(fills, stamps, yes_token)[1]


def fifo_pairs(fills, stamps, yes_token):
    """Actual FIFO opening/closing legs and shares for auditable role PnL."""
    pairs = []
    _fifo(fills, stamps, yes_token, pairs=pairs)
    return pairs


def fifo_residual(fills, stamps, yes_token):
    """Return the unpaired actual fill lots, retaining their identity and time."""
    queues, _ = _fifo(fills, stamps, yes_token)
    result = []
    for q in queues.values():
        for fill, qty in q:
            changes = {"size": float(qty)}
            for name in ("principal_usdc", "fee_usdc"):
                value = getattr(fill, name, None)
                if value is not None:
                    changes[name] = str(Decimal(str(value))*qty/Decimal(str(fill.size)))
            result.append(replace(fill, **changes))
    return result


def install_reverse_episode(executor, *, mode="HD"):
    """A proven reverse residual above tolerance starts a new leg in the same round."""
    ep = executor.repair_episode
    if ep is None:
        return False
    y, n = executor.economic_inventory()
    side = "YES" if y > n else "NO"
    if abs(y-n) <= SMALL or side == ep["side"]:
        return False
    chain_windows = ep.get("chain_windows", ep.get("windows", [ep["opening_window"]]))
    # Session net inventory includes proven dust carried from a previous loop.
    # FIFO all actual session lots; filtering to the latest chain would lose
    # that quantity/cost and could fail handoff after a minimum-notional flip.
    lots = fifo_residual(executor.economic_fills(), executor.fill_observed_at,
                         executor.meta.yes.token_id)
    qty = sum((Decimal(str(f.size)) for f in lots), ZERO)
    if qty != abs(y-n) or not lots:
        raise ValueError("REVERSE_INVENTORY_LOTS_UNPROVEN")
    times = [executor.fill_observed_at[(f.trade_id, f.maker_order_id)] for f in lots]
    first = min(times, key=lambda t: t.get("fill_monotonic", t["elapsed"]))
    leg = ep.get("direction_leg", 0)+1
    chain = ep.get("chain_id", ep["opening_window"])
    executor.repair_episode = {
        "opening_window": f"{chain}:direction:{leg}", "chain_id": chain,
        "normal_entry_side": ep.get("normal_entry_side", ep["side"]),
        "chain_windows": list(chain_windows), "direction_leg": leg, "side": side,
        "opening_monotonic": min(executor.order_post_elapsed.get(f.maker_order_id, first["elapsed"])
                                 for f in lots)+executor.time_anchor
                             +int(executor.slug.rsplit("-", 1)[1])-executor.official_time,
        "path_clock_offset": ep.get("path_clock_offset", 0),
        "original_qty": str(qty), "original_cost": str(sum(
            (fill_cost(f) for f in lots), ZERO)),
        "source_lots": lots, "first_fill_elapsed": first["elapsed"],
        "first_fill_time_source": first["source"], "windows": [], "submissions": 0,
    }
    if "fill_monotonic" in first:
        executor.repair_episode["first_fill_monotonic"] = first["fill_monotonic"]
        executor.repair_episode["opening_monotonic"] = first["fill_monotonic"]
    executor.record("sm_repair_reverse", {
        "chain_id": chain, "direction_leg": leg, "mode": mode, "side": side,
        "original_qty": str(qty), "cost_basis": "FIFO", "first_fill_elapsed": first["elapsed"],
        "original_cost": executor.repair_episode["original_cost"],
        "sources": [{"trade_id": f.trade_id, "order_id": f.maker_order_id,
                     "quantity": f.size, "price": f.price} for f in lots]})
    return True
