"""Pure finite REPAIR rollout. No network, execution, or journal dependencies.

The price kernel is a bounded martingale, anchored at the supplied probability
(no rounding the fair price to an order tick). Local variance is sigma**2 in
the interior; it is capped near absorbing endpoints to keep probabilities valid.
The fill/price joint law, not a current-price spread times a fill rate, values
the orders. All parameters remain explicitly UNCALIBRATED.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, replace
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

import numpy as np

from polymaker.config import RDPRepairConfig


@dataclass(frozen=True)
class RepairState:
    p: float
    sigma: float
    elapsed: float
    yes: float
    no: float
    cost: float
    cash: float
    bid: float
    ask: float
    tick: float
    minimum: float = 5.0
    maximum: float = 15.0
    original_side: str | None = None

    @property
    def delta(self):
        return self.yes - self.no

    @property
    def mark(self):
        return self.p * self.yes + (1-self.p) * self.no - self.cost


@dataclass(frozen=True)
class RepairOrder:
    side: str
    price: float
    remaining: float
    age: float
    cancel_at: float


def quantity_candidates(state: RepairState, cfg: RDPRepairConfig):
    """Search legal whole shares plus exact inward/venue boundaries."""
    d = abs(Decimal(str(state.yes))-Decimal(str(state.no)))
    price = Decimal(str(state.bid))
    if not price.is_finite() or price <= 0:
        return ()
    minimum = max(Decimal("5"), Decimal(str(state.minimum)), Decimal(1)/price).quantize(
        Decimal(".01"), rounding=ROUND_CEILING)
    limit = Decimal(str(cfg.delta_limit))
    # The whole session Delta, including old dust, bounds opposite-side fills.
    bound = min(d + limit, Decimal(str(state.maximum)),
                (Decimal(str(state.cash))/price).quantize(Decimal(".01"), rounding=ROUND_FLOOR))
    if bound < minimum:
        return ()
    # Cross the pairing point only when the venue minimum forces it.
    maximum = min(bound, max(minimum, d.quantize(Decimal(".01"), rounding=ROUND_FLOOR)))
    points = {minimum, maximum, d.quantize(Decimal(".01"), rounding=ROUND_FLOOR)}
    points.update(Decimal(n) for n in range(math.ceil(minimum), math.floor(maximum)+1))
    return tuple(float(q) for q in sorted(points) if minimum <= q <= maximum)


def _grid(p, spacing):
    nodes = np.array(sorted({0., 1., p, *(p+k*spacing for k in
                       range(-math.ceil(1/spacing), math.ceil(1/spacing)+1)
                       if 1e-12 < p+k*spacing < 1-1e-12)}))
    # Deduplicate representation noise at the anchor/endpoints.
    keep = np.concatenate(([True], np.diff(nodes) > 1e-10))
    return nodes[keep]


class OrderValuer:
    def __init__(self, cfg: RDPRepairConfig):
        self.cfg = cfg

    def profiles(self, state, side, price, quantities, horizons, *, existing=False,
                 age=0., export=False, deadline=math.inf):
        cfg = self.cfg
        grid = _grid(state.p, cfg.grid_step)
        dt = cfg.time_step_sec
        jump = max(1, math.ceil(state.sigma*math.sqrt(dt)/cfg.grid_step-1e-12))
        indexes = np.arange(len(grid))
        lo, hi = np.maximum(0, indexes-jump), np.minimum(len(grid)-1, indexes+jump)
        down_distance, up_distance = grid-grid[lo], grid[hi]-grid
        down = np.zeros(len(grid))
        up = down.copy()
        inside = (down_distance > 0) & (up_distance > 0)
        variance = state.sigma**2 * dt
        down[inside] = variance/(down_distance[inside]*(down_distance[inside]+up_distance[inside]))
        up[inside] = variance/(up_distance[inside]*(down_distance[inside]+up_distance[inside]))
        scale = np.maximum(1., down+up)
        down /= scale
        up /= scale
        stay = 1-down-up
        fair = grid if side == "YES" else 1-grid
        ask_offset = state.ask - (state.p if side == "YES" else 1-state.p)
        asks = np.minimum(1., np.ceil((fair+ask_offset-1e-12)/state.tick)*state.tick)
        crossed = price >= asks-1e-12
        edge = fair-price
        baseline_edge = (state.p if side == "YES" else 1-state.p)-state.bid
        chunks = math.ceil(max(quantities)/cfg.fill_chunk-1e-12)
        fills = np.minimum(np.arange(chunks+1)*cfg.fill_chunk, max(quantities))
        amounts = np.minimum(np.array(quantities)[:, None], fills[None, :])
        mass = np.zeros((chunks+1, len(grid)))
        mass[0, np.argmin(abs(grid-state.p))] = 1.
        rejected = np.zeros(len(grid))
        gain = np.zeros(len(quantities))
        points = {}
        for h in sorted(set(horizons)):
            if h < 0:
                raise ValueError("negative cancellation horizon")
            step = max(1, math.ceil((h+cfg.cancel_delay_sec)/dt-1e-10))
            points.setdefault(step, []).append(h)
        post_step = 0 if existing else math.ceil(cfg.submit_delay_sec/dt-1e-10)
        if not existing and post_step == 0 and price >= state.ask-1e-12:
            rejected[:] = mass[0]
            mass[0] = 0
        results = []
        rate = cfg.liquidity_rate * np.exp(np.clip(-cfg.depth_decay*(edge-baseline_edge), -50, 50))
        rate[[0, -1]] = 0
        for step in range(1, max(points)+1):
            if step % 32 == 0 and time.monotonic() > deadline:
                raise TimeoutError("REPAIR_SOLVE_TIMEOUT")
            moved = mass*stay
            if jump < len(grid):
                moved[:, jump:] += mass[:, :-jump]*up[:-jump]
                moved[:, :-jump] += mass[:, jump:]*down[jump:]
                moved[:, 0] += (mass[:, :jump]*down[:jump]).sum(axis=1)
                moved[:, -1] += (mass[:, -jump:]*up[-jump:]).sum(axis=1)
            else:
                moved[:, 0] += (mass*down).sum(axis=1)
                moved[:, -1] += (mass*up).sum(axis=1)
            mass = moved
            # Rejected paths don't trade again. Their contribution to gain is 0.
            if not existing and step == post_step:
                rejected[crossed] += mass[0, crossed]
                mass[0, crossed] = 0
            if existing or step > post_step:
                queue = -math.expm1(-(age+max(0, step-post_step)*dt)/cfg.queue_scale_sec)
                chance = -np.expm1(-rate*queue*dt)
                hit = mass[:-1]*chance
                hit[:, crossed] = mass[:-1, crossed]*cfg.cross_fill_probability
                next_mass = mass.copy()
                next_mass[:-1] -= hit
                # Every flow event fills one chunk; crossing depth is configurable.
                normal = hit.copy()
                normal[:, crossed] = 0
                next_mass[1:] += normal
                gain += (np.diff(amounts, axis=1) @ (normal*edge).sum(axis=1))
                shift = chunks if cfg.cross_fill_shares is None else max(1, round(cfg.cross_fill_shares/cfg.fill_chunk))
                for k in range(chunks):
                    target = min(chunks, k+shift)
                    crossing_mass = hit[k]*crossed
                    next_mass[target] += crossing_mass
                    gain += (amounts[:, target]-amounts[:, k]) * float(crossing_mass @ edge)
                mass = next_mass
            if step not in points:
                continue
            probability = float(mass.sum()+rejected.sum())
            if abs(probability-1) > 1e-8:
                raise ArithmeticError("REPAIR_PROBABILITY_MASS")
            for qi, q in enumerate(quantities):
                xs = amounts[qi, :, None]
                y = state.yes+(xs if side == "YES" else 0)
                n = state.no+(xs if side == "NO" else 0)
                c = state.cost+xs*price
                second = float((mass*(grid*(y-c)**2+(1-grid)*(n-c)**2)).sum())
                # Rejected prices need not be advanced for conditional means;
                # using their arrival probability is valid optional stopping.
                second += float(rejected @ (grid*(state.yes-state.cost)**2+
                                             (1-grid)*(state.no-state.cost)**2))
                mean = state.mark+float(gain[qi])
                residual = float((mass*(abs(y-n) >= .1-1e-12)).sum())
                residual += float(rejected.sum())*float(abs(state.delta) >= .1)
                for horizon in points[step]:
                    row = {"quantity": q, "cancel_at": state.elapsed+horizon,
                           "expected_pnl": mean, "incremental_ev": float(gain[qi]),
                           "settlement_sd": math.sqrt(max(0., second-mean*mean)),
                           "residual_probability": residual,
                           "expected_filled": float((mass*xs).sum()),
                           "fill_second_side": float((mass*xs*xs*fair).sum()),
                           "fill_second_opposite": float((mass*xs*xs*(1-fair)).sum()),
                           "reject_probability": float(rejected.sum())}
                    if export:
                        row["states"] = [(float(weight), float(grid[j]), float(amounts[qi, k]))
                                         for k, j in zip(*np.nonzero(mass > 1e-15), strict=True)
                                         if (weight := mass[k, j]) > 1e-15]
                        row["clear_after"] = step*dt
                    results.append(row)
        return results


class RepairPlanner:
    """Versioned finite rollout, continually replanned by the real controller.

    v2 gives every root action the same one-new-attempt allowance and one
    future review; v1 retains the earlier cancellation/replacement reference.
    Neither is a global closed-loop optimal EV or an actual submission cap.
    """
    def __init__(self, cfg=None):
        self.cfg = cfg or RDPRepairConfig()
        self.valuer = OrderValuer(self.cfg)
        self.cache = {}

    def decide(self, state: RepairState, order: RepairOrder | None = None):
        if self.cfg.model_version == "review_lease_v2":
            return self._review_decide(state, order)
        return self._lease_decide(state, order)

    def _lease_decide(self, state: RepairState, order: RepairOrder | None = None):
        cfg = self.cfg
        if not all(math.isfinite(x) for x in (state.p, state.sigma, state.elapsed, state.yes,
                    state.no, state.cost, state.cash, state.bid, state.ask, state.tick,
                    state.minimum, state.maximum)):
            raise ValueError("invalid REPAIR state")
        if (not 0 <= state.p <= 1 or state.sigma < 0 or not 0 < state.bid < state.ask <= 1
                or state.tick <= 0 or min(state.yes, state.no, state.cost, state.cash) < 0
                or state.minimum <= 0 or state.maximum < state.minimum
                or Decimal(str(state.bid)) % Decimal(str(state.tick)) != 0):
            raise ValueError("invalid REPAIR book/probability")
        if order and (order.side not in {"YES", "NO"} or
                not all(math.isfinite(x) for x in (order.price, order.remaining, order.age, order.cancel_at))
                or not 0 < order.price < 1 or order.remaining <= 0 or order.age < 0):
            raise ValueError("invalid REPAIR owned order")
        recovery = abs(state.delta) > float(cfg.delta_limit)+1e-9
        side = "NO" if state.delta > 0 else "YES"
        if state.elapsed >= cfg.cutoff_sec or abs(state.delta) < .1-1e-12:
            return self._simple(state, "CANCEL" if order else "HOLD", "CUTOFF" if state.elapsed >= cfg.cutoff_sec else "PAIRED")
        if state.original_side and side == state.original_side and not recovery:
            return self._simple(state, "CANCEL" if order else "HOLD", "ACCEPT_REVERSE_RESIDUE")
        end = time.monotonic()+cfg.solve_timeout_sec
        horizons = sorted({min(h, cfg.cutoff_sec-state.elapsed) for h in cfg.horizon_sec} |
                          {cfg.cutoff_sec-state.elapsed})
        if order and order.cancel_at > state.elapsed:
            horizons = sorted(set(horizons) | {min(order.cancel_at, cfg.cutoff_sec)-state.elapsed})
        key = (state, order, cfg.model_dump_json())
        if key in self.cache:
            return self.cache[key]
        hold = {"action": "HOLD", "expected_pnl": state.mark, "incremental_ev": 0.,
                "settlement_sd": abs(state.delta)*math.sqrt(state.p*(1-state.p))}
        candidates = [hold] if not order else []
        if order:
            profiles = self.valuer.profiles(state, order.side, order.price, (order.remaining,),
                        [0, *horizons], existing=True, age=order.age, export=True, deadline=end)
            cancel = next(p for p in profiles if p["cancel_at"] == state.elapsed)
            candidates = [{**p, "action": "CANCEL" if p["cancel_at"] == state.elapsed else "KEEP",
                           "side": order.side, "price": order.price} for p in profiles]
            baseline = cancel["expected_pnl"]
            if order.side != side:
                return self._result(state, candidates[0], candidates, baseline, recovery)
            # A single adaptive replacement, including late fills before release.
            continuations = {}
            for expiry in horizons if cfg.model_version == "bounded_martingale_v1" else ():
                gain = mean = second = 0.
                feasible = False
                branches = []
                for weight, p, old_fill in cancel["states"]:
                    child = replace(state, p=p, elapsed=state.elapsed+cancel["clear_after"],
                        yes=state.yes+(old_fill if order.side == "YES" else 0),
                        no=state.no+(old_fill if order.side == "NO" else 0),
                        cost=state.cost+old_fill*order.price,
                        cash=state.cash+order.remaining*order.price-old_fill*order.price)
                    # Completed order / covered or reversed gap: no new quote.
                    spec = None
                    if old_fill < order.remaining-1e-9 and child.delta*state.delta > 1e-9:
                        fair = p if side == "YES" else 1-p
                        offset = (state.p if side == "YES" else 1-state.p)-state.bid
                        bid = math.floor((fair-offset+1e-12)/state.tick)*state.tick
                        ask_offset = state.ask-(state.p if side == "YES" else 1-state.p)
                        ask = min(1., math.ceil((fair+ask_offset-1e-12)/state.tick)*state.tick)
                        child = replace(child, bid=bid, ask=ask)
                        qs = quantity_candidates(child, cfg)
                        if qs and child.elapsed+cfg.submit_delay_sec < state.elapsed+expiry and bid > 0:
                            spec = (min(qs, key=lambda q: abs(q-abs(child.delta))),)
                    if spec:
                        if child not in continuations:
                            valid = [h for h in horizons if state.elapsed+h >
                                     child.elapsed+cfg.submit_delay_sec]
                            continuations[child] = {round(r["cancel_at"], 8): r for r in
                                self.valuer.profiles(child, side, child.bid, spec,
                                    [state.elapsed+h-child.elapsed for h in valid], deadline=end)}
                        stats = continuations[child][round(state.elapsed+expiry, 8)]
                        g = stats["incremental_ev"]
                        feasible = True
                        m = stats["expected_pnl"]
                        var = stats["settlement_sd"]**2
                        branches.append({"p": p, "old_fill": old_fill, "quantity": spec[0], "price": child.bid})
                    else:
                        g = 0.
                        m = child.mark
                        var = child.delta**2*p*(1-p)
                    gain += weight*g
                    mean += weight*m
                    second += weight*(var+m*m)
                if feasible:
                    candidates.append({"action": "REPLACE_AFTER_CANCEL", "cancel_at": state.elapsed+expiry,
                        "expected_pnl": baseline+gain, "incremental_ev": baseline+gain-state.mark,
                        "settlement_sd": math.sqrt(max(0, second-mean*mean)), "replacement_branches": branches})
        else:
            baseline = state.mark
            qs = quantity_candidates(state, cfg)
            if qs:
                candidates += [{**p, "action": "BUY", "side": side, "price": state.bid}
                               for p in self.valuer.profiles(state, side, state.bid, qs, horizons, deadline=end)]
        viable = candidates
        if recovery:
            viable = [c for c in candidates if c["action"] in {"BUY", "KEEP", "REPLACE_AFTER_CANCEL"}]
            if not viable:
                return self._simple(state, "CANCEL" if order else "HOLD", "LIMIT_RECOVERY_UNRESOLVED")
            def violation(c):
                if c["action"] == "REPLACE_AFTER_CANCEL":
                    return max((max(0., abs(abs(state.delta)-b["old_fill"]-b["quantity"])-
                                        float(cfg.delta_limit)) for b in c["replacement_branches"]), default=math.inf)
                return max(0., abs(abs(state.delta)-c["quantity"])-float(cfg.delta_limit))
            smallest = min(violation(c) for c in viable)
            viable = [c for c in viable if violation(c) <= smallest+1e-9]
        priority = {"HOLD": 5, "CANCEL": 4, "KEEP": 3, "BUY": 2, "REPLACE_AFTER_CANCEL": 1}
        best = max(viable, key=lambda c: (round(c["expected_pnl"]-baseline, 10), priority[c["action"]]))
        result = self._result(state, best, candidates, baseline, recovery)
        if len(self.cache) > 128:
            self.cache.clear()
        self.cache[key] = result
        return result

    def _review_decide(self, state, order):
        """A common, observable continuation rule for every root action.

        Each rollout permits one new POST attempt. At the next scheduled review,
        honour an active lease; if no lease remains and no new POST was attempted
        at root, optimize one new lease or HOLD using that review's price/fills.
        A rejected root POST consumes this numerical allowance too. After the
        review, submit no further orders in this rollout.
        The real controller continues to replan without a submission-count cap.
        Price projection uses linear weights, preserving conditional probability
        and its first moment, including its dependence on cancellation fills.
        """
        start = time.monotonic()
        root = self._lease_decide(state, order)
        if (root.get("reason") or root["mode"] == "LIMIT_RECOVERY"
                or state.elapsed+self.cfg.review_sec >= self.cfg.cutoff_sec):
            return root
        candidates = root["candidates"]
        flat = next(c for c in candidates if c["action"] == ("CANCEL" if order else "HOLD"))
        end = start+self.cfg.solve_timeout_sec
        if order:
            row = self.valuer.profiles(state, order.side, order.price, (order.remaining,),
                (0,), existing=True, age=order.age, export=True, deadline=end)[0]
            branches = row["states"]
            delay = row["clear_after"]
        else:
            branches, delay = [(1., state.p, 0.)], 0.
        # Finish price evolution up to the next information arrival after release.
        review = max(self.cfg.review_sec, delay)
        grid = _grid(state.p, self.cfg.grid_step)
        fills = sorted({b[2] for b in branches})
        mass = np.zeros((len(fills), len(grid)))
        for w, p, x in branches:
            mass[fills.index(x), np.argmin(abs(grid-p))] += w
        dt = self.cfg.time_step_sec
        jump = max(1, math.ceil(state.sigma*math.sqrt(dt)/self.cfg.grid_step-1e-12))
        indexes = np.arange(len(grid))
        lo, hi = np.maximum(0, indexes-jump), np.minimum(len(grid)-1, indexes+jump)
        down_distance, up_distance = grid-grid[lo], grid[hi]-grid
        down, up = np.zeros(len(grid)), np.zeros(len(grid))
        inside = (down_distance > 0) & (up_distance > 0)
        down[inside] = state.sigma**2*dt/(down_distance[inside]*(down_distance[inside]+up_distance[inside]))
        up[inside] = state.sigma**2*dt/(up_distance[inside]*(down_distance[inside]+up_distance[inside]))
        scale = np.maximum(1., down+up)
        down, up = down/scale, up/scale
        for _ in range(math.ceil(max(0., review-delay)/dt-1e-10)):
            moved = mass*(1-down-up)
            for j in indexes:
                moved[:, lo[j]] += mass[:, j]*down[j]
                moved[:, hi[j]] += mass[:, j]*up[j]
            mass = moved
        spacing = self.cfg.continuation_grid_step
        coarse = np.unique(np.append(np.arange(0., 1., spacing), 1.))
        projected = np.zeros((len(fills), len(coarse)))
        for j, p in enumerate(grid):
            high = min(len(coarse)-1, max(1, int(np.searchsorted(coarse, p))))
            low = high-1
            ratio = (p-coarse[low])/(coarse[high]-coarse[low])
            projected[:, low] += mass[:, j]*(1-ratio)
            projected[:, high] += mass[:, j]*ratio
        # Gains do not depend on sunk cost or existing paired inventory. Reuse
        # a profile for branches sharing p, quote, and legal quantity candidates.
        profiles = {}
        future_gain = 0.
        future_second = 0.
        for k, j in zip(*np.nonzero(projected > 1e-15), strict=True):
            if time.monotonic() > end:
                raise TimeoutError("REPAIR_SOLVE_TIMEOUT")
            w, p, x = float(projected[k, j]), float(coarse[j]), fills[k]
            side = order.side if order else ("NO" if state.delta > 0 else "YES")
            child = replace(state, p=p, elapsed=state.elapsed+review,
                yes=state.yes+(x if side == "YES" else 0),
                no=state.no+(x if side == "NO" else 0), cost=state.cost+x*(order.price if order else 0),
                cash=state.cash+(order.remaining-x)*order.price if order else state.cash)
            # The same original-direction guard applies to every future branch.
            new_side = "NO" if child.delta > 0 else "YES"
            gain = 0.
            moment = p*(child.yes-child.cost)**2+(1-p)*(child.no-child.cost)**2
            if abs(child.delta) >= .1 and (not child.original_side or new_side != child.original_side
                                          or abs(child.delta) > float(self.cfg.delta_limit)):
                fair = p if new_side == "YES" else 1-p
                initial_fair = state.p if new_side == "YES" else 1-state.p
                bid = math.floor((fair+state.bid-initial_fair+1e-12)/state.tick)*state.tick
                ask = min(1., math.ceil((fair+state.ask-initial_fair-1e-12)/state.tick)*state.tick)
                child = replace(child, bid=bid, ask=ask)
                if 0 < bid < ask <= 1:
                    qs = quantity_candidates(child, self.cfg)
                    if qs:
                        key = (p, new_side, bid, ask, qs)
                        if key not in profiles:
                            horizons = sorted({min(h, self.cfg.cutoff_sec-child.elapsed)
                                for h in self.cfg.horizon_sec} | {self.cfg.cutoff_sec-child.elapsed})
                            profiles[key] = self.valuer.profiles(child, new_side, bid, qs, horizons, deadline=end)
                        rows = profiles[key]
                        if abs(child.delta) > float(self.cfg.delta_limit):
                            least = min(max(0., abs(abs(child.delta)-r["quantity"])-float(self.cfg.delta_limit))
                                        for r in rows)
                            rows = [r for r in rows if max(0., abs(abs(child.delta)-r["quantity"])-
                                                float(self.cfg.delta_limit)) <= least+1e-9]
                            gain = max(r["incremental_ev"] for r in rows)
                        else:
                            gain = max(0., max(r["incremental_ev"] for r in rows))
                        if gain != 0.:
                            chosen = max(rows, key=lambda r: r["incremental_ev"])
                            ex = chosen["expected_filled"]
                            ex_side = gain+bid*ex
                            ex_other = ex-ex_side
                            a, b = ((child.yes-child.cost, child.no-child.cost) if new_side == "YES"
                                    else (child.no-child.cost, child.yes-child.cost))
                            moment += (2*(a*(1-bid)*ex_side-b*bid*ex_other)
                                +(1-bid)**2*chosen["fill_second_side"]
                                +bid**2*chosen["fill_second_opposite"])
            future_gain += w*gain
            future_second += w*moment
        mean = flat["expected_pnl"]+future_gain
        flat = {**flat, "expected_pnl": flat["expected_pnl"]+future_gain,
                "incremental_ev": flat.get("incremental_ev", 0.)+future_gain,
                "settlement_sd": math.sqrt(max(0., future_second-mean*mean)),
                "residual_probability": None, "continuation_gain": future_gain}
        candidates = [flat if c["action"] == flat["action"] else c for c in candidates]
        recovery = abs(state.delta) > float(self.cfg.delta_limit)+1e-9
        if recovery:
            # Waiting is not an alternative to a feasible immediate hard-limit
            # recovery; use the recovery ranking already established at root.
            best = {k: v for k, v in root.items() if k != "candidates"}
        else:
            priority = {"HOLD": 5, "CANCEL": 4, "KEEP": 3, "BUY": 2}
            best = max(candidates, key=lambda c: (round(c["expected_pnl"]-flat["expected_pnl"], 10),
                                                   priority[c["action"]]))
        return self._result(state, best, candidates, flat["expected_pnl"], recovery)

    def _simple(self, state, action, reason):
        return self._result(state, {"action": action, "reason": reason,
                            "expected_pnl": state.mark}, [], state.mark,
                            abs(state.delta) > float(self.cfg.delta_limit))

    def _result(self, state, best, candidates, baseline, recovery):
        projected = None
        if best["action"] in {"BUY", "KEEP"}:
            projected = state.delta+(best["quantity"] if best["side"] == "YES" else -best["quantity"])
        return {**best, "mode": "LIMIT_RECOVERY" if recovery else "REPAIR",
                "baseline_pnl": baseline, "advantage": best["expected_pnl"]-baseline,
                "model_version": self.cfg.model_version, "parameter_status": self.cfg.parameter_status,
                "valuation_scope": ("NEXT_REVIEW_HONOUR_LEASE_ELSE_OPTIMIZE_ONCE"
                    if self.cfg.model_version == "review_lease_v2" else "FINITE_ORDER_ROLLOUT_NOT_CLOSED_LOOP_EV"),
                "delta": state.delta, "delta_limit": float(self.cfg.delta_limit),
                "current_mark_pnl": state.mark,
                "current_settlement_sd": abs(state.delta)*math.sqrt(state.p*(1-state.p)),
                "review_volatility_sd_local": abs(state.delta)*state.sigma*math.sqrt(self.cfg.review_sec),
                "projected_delta_if_fully_filled": projected,
                "actual_limit_excess": max(0., abs(state.delta)-float(self.cfg.delta_limit)),
                "planned_limit_excess_if_fully_filled": (max(0., abs(projected)-float(self.cfg.delta_limit))
                                                        if projected is not None else None),
                "numerical_resolution": {"price_grid": self.cfg.grid_step,
                    "time_step": self.cfg.time_step_sec, "continuation_grid": self.cfg.continuation_grid_step,
                    "tie_rounding_decimals": 10, "certified_error_bound": None},
                "candidates": [{k: v for k, v in c.items() if k != "states"} for c in candidates]}
