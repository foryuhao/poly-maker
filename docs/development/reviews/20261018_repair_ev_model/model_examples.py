"""Offline, uncalibrated order-value model; no trading/API dependencies.

Bounded fair-price lattice, delayed post-only acceptance, age-dependent
liquidity fills in one-share chunks, stale-quote fills, delayed cancellation.
Each candidate is ONE order followed by holding to settlement. This is a
restricted rollout model, not a globally optimal multi-order controller.
"""

from dataclasses import asdict, dataclass, replace
import json
import math
from pathlib import Path


@dataclass(frozen=True)
class Parameters:
    tick: float = 0.01
    step_s: float = 0.1
    liquidity_rate: float = 2.0
    queue_scale_s: float = 2.0
    depth_decay: float = 50.0
    half_spread: float = 0.01
    submit_delay_s: float = 0.2
    cancel_delay_s: float = 0.2
    partial_chunk: float = 1.0
    stop_s: float = 240.0
    capital_limit: float = 15.0
    cross_fill_shares: float | None = None
    cross_fill_probability: float = 1.0
    ask_spread: float | None = None
    price_step: float | None = None
    flow_decay_after_s: float | None = None
    flow_decay_multiplier: float = 1.0


@dataclass(frozen=True)
class State:
    p: float
    sigma: float
    elapsed: float
    yes: float = 5.0
    no: float = 0.0
    cost: float = 1.50
    original_qty: float = 5.0
    other_reserved: float = 0.0


def terminal_moments(state, side, filled, price, p):
    yes = state.yes + (filled if side == "YES" else 0)
    no = state.no + (filled if side == "NO" else 0)
    cost = state.cost + filled * price
    win, lose = yes - cost, no - cost
    return p * win + (1 - p) * lose, p * win**2 + (1 - p) * lose**2


def hold_value(state):
    mean, second = terminal_moments(state, "NO", 0, 0, state.p)
    return {"expected_pnl": mean, "settlement_sd": math.sqrt(max(0, second-mean**2))}


def evaluate_order(state, side, price, quantity, horizons, params=Parameters(), *,
                   existing=False, queue_age_s=0.0, export_states=False):
    """Exact probability propagation in this specified DISCRETE model.

    Existing=True evaluates KEEP against CANCEL_NOW (h=0), including late
    fills during cancel delay. No instantaneous cancel/replacement is assumed.
    Completed/rejected paths are valued by their conditional binary payoff.
    Unfilled residuals after effective cancellation are held to settlement.
    """
    if not (0 <= state.p <= 1 and state.sigma >= 0):
        raise ValueError("invalid fair price or volatility")
    if side not in {"YES", "NO"} or not (0 < price < 1) or quantity <= 0:
        raise ValueError("invalid order")
    if params.tick <= 0 or params.partial_chunk <= 0 or params.queue_scale_s <= 0:
        raise ValueError("invalid model parameters")
    if params.price_step is not None and params.price_step <= 0:
        raise ValueError("nonpositive price grid")
    if params.liquidity_rate < 0 or min(params.submit_delay_s, params.cancel_delay_s) < 0:
        raise ValueError("negative rate/delay")
    if not 0 <= params.cross_fill_probability <= 1:
        raise ValueError("invalid cross fill probability")
    if params.flow_decay_multiplier < 0 or (params.flow_decay_after_s is not None and params.flow_decay_after_s < 0):
        raise ValueError("invalid flow decay hypothesis")
    if params.cross_fill_shares is not None and (params.cross_fill_shares <= 0 or
            abs(params.cross_fill_shares/params.partial_chunk-
                round(params.cross_fill_shares/params.partial_chunk)) > 1e-9):
        raise ValueError("cross depth must be positive whole fill chunks")
    delta = state.yes-state.no
    if not existing and ((side == "NO" and delta <= 0) or (side == "YES" and delta >= 0)):
        raise ValueError("repair must buy the current opposite side")
    after = delta + (quantity if side == "YES" else -quantity)
    if max(abs(delta), abs(after)) > state.original_qty+1e-9:
        raise ValueError("directional quantity cap")
    if state.cost + state.other_reserved + quantity*price > params.capital_limit+1e-9:
        raise ValueError("capital cap")
    if not existing and (quantity < 5-1e-9 or quantity*price < 1-1e-9):
        raise ValueError("new-order minimum")
    if not existing and state.elapsed >= params.stop_s:
        raise ValueError("new order at/after T240")
    if abs(price/params.tick-round(price/params.tick)) > 1e-7:
        raise ValueError("off-tick quote")
    if not existing:
        initial_fair = state.p if side == "YES" else 1-state.p
        expected_bid = market_bid(initial_fair, params)
        if abs(price-expected_bid) > 1e-9:
            raise ValueError("new order must use this example book's best bid")

    spacing = params.price_step or params.tick
    cells = round(1/spacing)
    if spacing <= 0 or abs(cells*spacing-1) > 1e-9:
        raise ValueError("price grid must divide unit payoff")
    start = round(state.p/spacing)
    if abs(start*spacing-state.p) > 1e-9:
        raise ValueError("example price must lie on lattice")
    per_second = math.ceil(max(1/params.step_s, state.sigma**2/spacing**2))
    dt = 1/per_second
    move = state.sigma**2*dt/(2*spacing**2)
    assert move <= 0.5+1e-12
    post_steps = 0 if existing else math.ceil(params.submit_delay_s*per_second-1e-12)
    chunks = math.ceil(quantity/params.partial_chunk-1e-12)
    fills = [min(k*params.partial_chunk, quantity) for k in range(chunks+1)]
    checkpoints = {}
    for requested in sorted(set(horizons)):
        if requested < 0:
            raise ValueError("negative planned horizon")
        horizon = min(requested, max(0, params.stop_s-state.elapsed))
        step = max(1, math.ceil((horizon+params.cancel_delay_s)*per_second-1e-12))
        checkpoints.setdefault(step, []).append(horizon)
    if not checkpoints:
        raise ValueError("empty horizon set")

    active = [[0.0]*(cells+1) for _ in range(chunks)]
    active[0][start] = 1.0
    stopped_mass = stopped_mean = stopped_second = 0.0
    full_mass = rejected_mass = expected_filled = gain = 0.0
    stopped_gain_second = stopped_gain_yes = 0.0
    stopped_fill_mass = [0.0]*(chunks+1)
    max_mass_error = max_value_error = 0.0
    baseline = hold_value(state)["expected_pnl"]
    rows = []

    def stop(weight, k, i):
        nonlocal stopped_mass, stopped_mean, stopped_second
        nonlocal stopped_gain_second, stopped_gain_yes
        mean, second = terminal_moments(state, side, fills[k], price, i*spacing)
        stopped_mass += weight
        stopped_mean += weight*mean
        stopped_second += weight*second
        stopped_fill_mass[k] += weight
        _, gain_second, gain_yes = gain_moments(side, fills[k], price, i*spacing)
        stopped_gain_second += weight*gain_second
        stopped_gain_yes += weight*gain_yes

    # New quotes are at best bid in the example book, so acceptance at zero
    # delay is legal. Positive delay rechecks the *arrival* ask below.
    if not existing and post_steps == 0:
        fair = state.p if side == "YES" else 1-state.p
        if price >= market_ask(fair, params)-1e-12:
            stop(1.0, 0, start)
            rejected_mass = 1.0
            active[0][start] = 0.0

    for step in range(1, max(checkpoints)+1):
        moved = [[0.0]*(cells+1) for _ in range(chunks)]
        for k, row in enumerate(active):
            out = moved[k]
            for i, weight in enumerate(row):
                if not weight:
                    continue
                if i in (0, cells):
                    out[i] += weight
                else:
                    out[i-1] += weight*move
                    out[i] += weight*(1-2*move)
                    out[i+1] += weight*move
        if not existing and step == post_steps:
            for i, weight in enumerate(moved[0]):
                fair = i*spacing if side == "YES" else 1-i*spacing
                if weight and price >= market_ask(fair, params)-1e-12:
                    stop(weight, 0, i)
                    rejected_mass += weight
                    moved[0][i] = 0.0

        if existing or step > post_steps:
            after_fill = [[0.0]*(cells+1) for _ in range(chunks)]
            age = queue_age_s + max(0, step-post_steps)*dt
            queue_factor = -math.expm1(-age/params.queue_scale_s)
            for k, row in enumerate(moved):
                for i, weight in enumerate(row):
                    if not weight:
                        continue
                    fair = i*spacing if side == "YES" else 1-i*spacing
                    edge = fair-price
                    if price >= market_ask(fair, params)-1e-12:
                        # Default reproduces the original full-cross example.
                        # Finite depth/probability provide structural stresses.
                        next_k = (chunks if params.cross_fill_shares is None else
                                  min(chunks, k+round(params.cross_fill_shares/params.partial_chunk)))
                        amount = fills[next_k]-fills[k]
                        hit = weight*params.cross_fill_probability
                        gain += hit*amount*edge
                        expected_filled += hit*amount
                        after_fill[k][i] += weight-hit
                        if next_k == chunks:
                            full_mass += hit
                            stop(hit, chunks, i)
                        else:
                            after_fill[next_k][i] += hit
                        continue
                    rate = (params.liquidity_rate*queue_factor*
                            math.exp(-params.depth_decay*(edge-params.half_spread)))
                    if params.flow_decay_after_s is not None and age >= params.flow_decay_after_s:
                        rate *= params.flow_decay_multiplier
                    if i in (0, cells):
                        rate = 0.0  # no noise sale of an already certain payoff
                    probability = -math.expm1(-rate*dt)
                    hit = weight*probability
                    amount = fills[k+1]-fills[k]
                    gain += hit*amount*edge
                    expected_filled += hit*amount
                    after_fill[k][i] += weight-hit
                    if k+1 == chunks:
                        full_mass += hit
                        stop(hit, chunks, i)
                    else:
                        after_fill[k+1][i] += hit
            moved = after_fill
        active = moved

        if step in checkpoints:
            mass, mean, second = stopped_mass, stopped_mean, stopped_second
            gain_second, gain_yes = stopped_gain_second, stopped_gain_yes
            fill_mass = stopped_fill_mass.copy()
            nonzero_residual_prob = 0.0
            end_states = []
            for k, row in enumerate(active):
                for i, weight in enumerate(row):
                    if not weight:
                        continue
                    local_mean, local_second = terminal_moments(
                        state, side, fills[k], price, i*spacing)
                    mass += weight
                    fill_mass[k] += weight
                    mean += weight*local_mean
                    second += weight*local_second
                    _, local_gain_second, local_gain_yes = gain_moments(side, fills[k], price, i*spacing)
                    gain_second += weight*local_gain_second
                    gain_yes += weight*local_gain_yes
                    if export_states:
                        end_states.append({"probability": weight, "p": i*spacing,
                                           "filled_shares": fills[k]})
                    if abs(delta+(fills[k] if side == "YES" else -fills[k])) > 1e-9:
                        nonzero_residual_prob += weight
            # Rejected paths retain the original nonzero inventory.
            nonzero_residual_prob += rejected_mass
            if abs(after) > 1e-9:
                nonzero_residual_prob += full_mass
            max_mass_error = max(max_mass_error, abs(mass-1))
            max_value_error = max(max_value_error, abs(mean-baseline-gain))
            assert abs(mass-1) < 1e-9, (mass, step)
            assert abs(mean-baseline-gain) < 1e-9, (mean, baseline, gain)
            for horizon in checkpoints[step]:
                record = {
                    "cancel_request_after_s": horizon,
                    "modeled_cancel_effective_after_s": step*dt,
                    "expected_pnl": mean,
                    "incremental_ev": gain,
                    "settlement_sd": math.sqrt(max(0, second-mean**2)),
                    "expected_filled_shares": expected_filled,
                    "full_fill_probability": full_mass,
                    "post_only_rejection_probability": rejected_mass,
                    "nonzero_residual_probability": nonzero_residual_prob,
                    "increment_second_moment": gain_second,
                    "increment_yes_joint_moment": gain_yes,
                    "filled_share_distribution": [
                        {"shares": fills[k], "probability": weight}
                        for k, weight in enumerate(fill_mass) if weight > 0],
                }
                if export_states:
                    record["after_cancel_states"] = end_states
                    record["completed_paths"] = {"probability": stopped_mass,
                                                   "pnl_mean_contribution": stopped_mean,
                                                   "pnl_second_contribution": stopped_second,
                                                   "full_fill_probability": full_mass,
                                                   "rejection_probability": rejected_mass}
                rows.append(record)
    return {"state": asdict(state), "side": side, "limit_price": price,
            "quantity": quantity, "existing": existing,
            "queue_age_s": queue_age_s, "hold": hold_value(state),
            "candidates": rows, "probability_mass_error": max_mass_error,
            "payoff_identity_error": max_value_error}


def gain_moments(side, filled, price, p):
    """E[g], E[g²], E[Z*g] conditional on a stopping state."""
    yes_gain = filled*((1 if side == "YES" else 0)-price)
    no_gain = filled*((1 if side == "NO" else 0)-price)
    return (p*yes_gain+(1-p)*no_gain,
            p*yes_gain**2+(1-p)*no_gain**2, p*yes_gain)


def market_ask(fair, params):
    raw = fair+(params.half_spread if params.ask_spread is None else params.ask_spread)
    return min(1, round(math.ceil((raw-1e-12)/params.tick)*params.tick, 12))


def market_bid(fair, params):
    return round(math.floor((fair-params.half_spread+1e-12)/params.tick)*params.tick, 12)


def select(result):
    best = max(result["candidates"], key=lambda r: (r["expected_pnl"], -r["settlement_sd"]))
    if result["existing"]:
        baseline = next(r for r in result["candidates"] if r["cancel_request_after_s"] == 0)
        action = "KEEP" if best["expected_pnl"] > baseline["expected_pnl"]+1e-10 else "CANCEL"
        if action == "CANCEL":
            best = baseline
    else:
        action = "BUY" if best["incremental_ev"] > 1e-10 else "HOLD"
    advantage = (best["expected_pnl"]-baseline["expected_pnl"] if result["existing"]
                 else best["incremental_ev"] if action == "BUY" else 0.0)
    return {"action": action, "side": result["side"] if action != "HOLD" else None,
            "price": result["limit_price"] if action != "HOLD" else None,
            "remaining_order_shares": result["quantity"] if action != "HOLD" else 0,
            "cancel_request_after_s": best["cancel_request_after_s"] if action in {"BUY", "KEEP"} else 0,
            "expected_pnl": best["expected_pnl"] if action != "HOLD" else result["hold"]["expected_pnl"],
            "incremental_ev": best["incremental_ev"] if action != "HOLD" else 0.0,
            "comparison_baseline": "CANCEL_WITH_LATE_FILLS" if result["existing"] else "NO_ACTIVE_ORDER_HOLD",
            "advantage_vs_baseline": advantage}


def new_order_candidates(state, horizons, params=Parameters()):
    """Search a declared, finite size grid; no claim of continuous optimality.

    Sizes include the minimum legal size, whole-share interior points,
    exact inventory gap if legal, and maximum feasible size rounded inward.
    A reversal is a candidate only when minimum order rules force it; the
    model does not enlarge a repair into a fresh opposite-direction bet.
    The caller must replace the synthetic book with actual book observations
    before interpreting any price as a real quote.
    """
    delta = state.yes-state.no
    if abs(delta) < 1e-9:
        return []
    side = "NO" if delta > 0 else "YES"
    fair = 1-state.p if side == "NO" else state.p
    price = market_bid(fair, params)
    if not params.tick-1e-9 <= price <= 1-params.tick+1e-9:
        return []
    lower = math.ceil(100*max(5.0, 1/price)-1e-9)/100
    feasible_max = math.floor(100*min(abs(delta)+state.original_qty,
                      (params.capital_limit-state.cost-state.other_reserved)/price)+1e-9)/100
    if lower > abs(delta):
        return ([evaluate_order(state, side, price, lower, horizons, params)]
                if lower <= feasible_max else [])
    upper = math.floor(100*min(abs(delta), feasible_max)+1e-9)/100
    if lower > upper:
        return []
    sizes = {lower, upper}
    sizes.update(float(k) for k in range(math.ceil(lower), math.floor(upper)+1))
    if lower <= abs(delta) <= upper and abs(abs(delta)*100-round(abs(delta)*100)) < 1e-7:
        sizes.add(abs(delta))
    return [evaluate_order(state, side, price, size, horizons, params)
            for size in sorted(sizes)]


def choose_new(results, state):
    if not results:
        return {"action": "HOLD", "expected_pnl": hold_value(state)["expected_pnl"],
                "incremental_ev": 0.0, "reason": "NO_LEGAL_CANDIDATE"}
    winner = max(results, key=lambda r: (select(r)["expected_pnl"], -r["quantity"]))
    return select(winner)


def checks(params):
    state = State(.31, .02, 100)
    # Fully specified lattice conserves probability and the fair-value PnL identity.
    result = evaluate_order(state, "NO", .68, 5, [2, 20, 140], params)
    altered = evaluate_order(replace(state, cost=3.75), "NO", .68, 5, [2, 20, 140], params)
    for a, b in zip(result["candidates"], altered["candidates"]):
        assert abs(a["incremental_ev"]-b["incremental_ev"]) < 1e-12
        assert abs(a["expected_pnl"]-b["expected_pnl"]-2.25) < 1e-9
    assert select(result)["cancel_request_after_s"] == select(altered)["cancel_request_after_s"]
    no_flow = evaluate_order(replace(state, sigma=0), "NO", .68, 5, [2, 20],
                             replace(params, liquidity_rate=0))
    assert all(abs(r["incremental_ev"]) < 1e-12 for r in no_flow["candidates"])
    fair_static = evaluate_order(replace(state, sigma=0), "NO", .68, 5, [20], params)
    assert fair_static["candidates"][0]["incremental_ev"] > 0
    adverse = evaluate_order(state, "NO", .68, 5, [20], replace(params, liquidity_rate=0))
    assert adverse["candidates"][0]["incremental_ev"] < 0
    low_flow = evaluate_order(state, "NO", .68, 5, [2, 20, 140],
                              replace(params, liquidity_rate=.5))
    assert select(low_flow)["action"] == "HOLD"
    # Oversizing is allowed for a venue minimum, not simply to turn the
    # repair into a profitable-looking reverse position.
    family = new_order_candidates(replace(state, sigma=0), [2], params)
    assert [r["quantity"] for r in family] == [5]
    forced = new_order_candidates(replace(state, p=.85, sigma=0), [2], params)
    assert len(forced) == 1 and forced[0]["quantity"] == 7.15
    through = evaluate_order(replace(state, p=.35, sigma=0), "NO", .68, 5, [0], params,
                             existing=True, queue_age_s=1.8)
    assert abs(through["candidates"][0]["expected_pnl"]-.1) < 1e-9
    assert abs(through["candidates"][0]["full_fill_probability"]-1) < 1e-9
    # A partial-fill remainder is legal on an existing order, but cannot be
    # submitted as a fresh sub-minimum order.
    partial = replace(state, no=1, cost=2.18)
    evaluate_order(partial, "NO", .68, 4, [0, 2], params, existing=True, queue_age_s=2)
    for invalid_state, quantity in ((partial, 4), (state, 11), (replace(state, cost=14), 5)):
        try:
            evaluate_order(invalid_state, "NO", .68, quantity, [2], params)
        except ValueError:
            pass
        else:
            raise AssertionError("illegal order accepted")
    try:
        evaluate_order(replace(state, elapsed=240), "NO", .68, 5, [2], params)
    except ValueError:
        pass
    else:
        raise AssertionError("post-cutoff order accepted")


def main():
    params = Parameters()
    checks(params)
    grid = [2, 5, 10, 20, 40, 80, 120, 140]
    rows = []
    for elapsed, p in ((100, .31), (102, .25), (102, .30), (102, .35)):
        state = State(p, .02, elapsed)
        results = new_order_candidates(state, grid, params)
        rows.append({"label": "fresh_state_without_active_order", "results": results,
                     "decision": choose_new(results, state)})
    existing = []
    for p in (.25, .30):
        for already_filled in (0, 1, 3):
            state = State(p, .02, 102, no=already_filled, cost=1.5+already_filled*.68)
            result = evaluate_order(state, "NO", .68, 5-already_filled,
                                    [0, 2, 5, 10, 20, 40, 80, 138], params,
                                    existing=True, queue_age_s=1.8)
            existing.append({"already_filled": already_filled, "result": result, "decision": select(result)})
    sensitivity = []
    for rate in (.1, .5, 1, 2, 5):
        for sigma in (.02, .03):
            state = State(.31, sigma, 100)
            results = new_order_candidates(state, grid, replace(params, liquidity_rate=rate))
            sensitivity.append({"liquidity_rate": rate, "sigma": sigma,
                                "decision": choose_new(results, state),
                                "max_candidate_incremental_ev": max(
                                    r["incremental_ev"] for result in results for r in result["candidates"])})
    output = {"status": "UNCALIBRATED_SYNTHETIC_MODEL_NOT_A_LIVE_RECOMMENDATION",
            "parameters": asdict(params), "fresh_states": rows,
              "existing_order_states": existing, "sensitivity": sensitivity}
    Path(__file__).with_name("examples.json").write_text(json.dumps(output, indent=2)+"\n")
    print(json.dumps({"checks": "passed", "fresh_states": [r["decision"] for r in rows],
                      "existing_orders": [{"p": r["result"]["state"]["p"], "filled": r["already_filled"],
                                           **r["decision"]} for r in existing],
                      "sensitivity": sensitivity}, indent=2))


if __name__ == "__main__":
    main()
