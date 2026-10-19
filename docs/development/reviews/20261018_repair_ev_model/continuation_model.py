"""Offline two-order continuation and common-policy parameter stress tests.

No gateway, live data, or production imports. A replacement waits for effective
cancellation, observes resulting price/fills, then uses a fixed sizing rule and
deadline. The same contingent rule is evaluated in every parameter scenario.
"""

from dataclasses import asdict, dataclass, replace
import json
import math
from pathlib import Path

from model_examples import Parameters, State, evaluate_order, hold_value, terminal_moments, market_bid


@dataclass(frozen=True)
class Order:
    side: str
    price: float
    remaining: float
    queue_age_s: float


@dataclass(frozen=True)
class Book:
    bid: float
    ask: float
    tick: float = .01


@dataclass(frozen=True)
class EpisodeContext:
    repair_side: str
    submissions_used: int
    max_submissions: int = 2  # restricted research family, not a live default
    facts_complete: bool = True


def bind_book(state, side, book, models):
    """Use observed L1 now; assume fixed bid/ask offsets in future scenarios.

    This is a book adapter, NOT an empirical future spread/depth model.
    Midpoints on half-tick grids are supported. Misaligned/inconsistent inputs
    fail explicitly rather than being silently rounded to invented quotes.
    """
    fair = state.p if side == "YES" else 1-state.p
    if not (0 < book.bid < book.ask <= 1 and book.bid <= fair <= book.ask):
        raise ValueError("book inconsistent with this fair-price model")
    if any(abs(x/book.tick-round(x/book.tick)) > 1e-7 for x in (book.bid, book.ask)):
        raise ValueError("book off tick")
    if abs(state.p/(book.tick/2)-round(state.p/(book.tick/2))) > 1e-7:
        raise ValueError("fair price outside supported half-tick grid")
    return {name: replace(p, tick=book.tick, half_spread=fair-book.bid,
                          ask_spread=book.ask-fair, price_step=book.tick/2)
            for name, p in models.items()}


def moments_from_increment(state, row):
    """Translate cost/inventory-independent fill moments to actual PnL."""
    offset, delta = state.no-state.cost, state.yes-state.no
    p = state.p
    mean = hold_value(state)["expected_pnl"]+row["incremental_ev"]
    base_second = offset**2+2*offset*delta*p+delta**2*p
    second = (base_second+2*offset*row["incremental_ev"]+
              2*delta*row["increment_yes_joint_moment"]+
              row["increment_second_moment"])
    return mean, second


def repair_spec(state, params, expiry, side_required=None):
    """Same observable-state sizing rule in all parameter scenarios.

    Cover the gap if legal/funded; otherwise the largest legal partial size.
    A venue minimum may force controlled excess. No re-repair of a reversed
    minimum-size residue within this two-order continuation.
    """
    delta = state.yes-state.no
    if abs(delta) < 1e-9 or state.elapsed+params.submit_delay_s >= min(expiry, params.stop_s)-1e-9:
        return None
    side = "NO" if delta > 0 else "YES"
    if side_required and side != side_required:
        return None
    fair = 1-state.p if side == "NO" else state.p
    price = market_bid(fair, params)
    if not params.tick-1e-9 <= price <= 1-params.tick+1e-9:
        return None
    if abs(price/params.tick-round(price/params.tick)) > 1e-7:
        return None
    minimum = math.ceil(100*max(5, 1/price)-1e-9)/100
    max_funded = math.floor(100*(params.capital_limit-state.cost-state.other_reserved)/price+1e-9)/100
    quantity = max(minimum, math.ceil(100*abs(delta)-1e-9)/100)
    if quantity > max_funded:
        quantity = min(math.floor(100*abs(delta)+1e-9)/100, max_funded)
    after = delta+(quantity if side == "YES" else -quantity)
    if quantity < minimum or max(abs(delta), abs(after)) > state.original_qty+1e-9:
        return None
    return side, price, quantity


class Planner:
    def __init__(self, models, horizon_grid=(2, 5, 10, 20, 40, 80, 120, 140)):
        self.models = models
        self.horizons = horizon_grid
        self.cache = {}
        self.cache_hits = 0
        self.cache_evaluations = 0
        reference = next(iter(models.values()))
        for params in models.values():
            for field in ("stop_s", "capital_limit", "tick", "half_spread", "ask_spread", "price_step", "step_s",
                          "submit_delay_s", "cancel_delay_s"):
                if getattr(params, field) != getattr(reference, field):
                    raise ValueError("parameter scenarios must share observable constraints/book")

    def expiries(self, state):
        stop = next(iter(self.models.values())).stop_s
        return sorted({min(stop, state.elapsed+h) for h in self.horizons if state.elapsed+h > state.elapsed} | {stop})

    def new_stats(self, state, spec, expiry, params, expiries):
        side, price, quantity = spec
        # The increment distribution depends on the order, not sunk cost or
        # pre-existing inventory. Actual feasibility is checked by repair_spec.
        key = (round(state.p, 10), state.sigma, round(state.elapsed, 10),
               side, price, quantity, params, tuple(expiries))
        if key not in self.cache:
            template = replace(state, yes=quantity if side == "NO" else 0,
                               no=quantity if side == "YES" else 0, cost=0,
                               other_reserved=0, original_qty=quantity)
            valid = [e for e in expiries if repair_spec(state, params, e, side)]
            profiles = evaluate_order(template, side, price, quantity,
                                      [e-state.elapsed for e in valid], params)
            self.cache[key] = {round(state.elapsed+r["cancel_request_after_s"], 8): r
                               for r in profiles["candidates"]}
            self.cache_evaluations += 1
        else:
            self.cache_hits += 1
        return self.cache[key][round(expiry, 8)]

    def replacement(self, state, order, cancelled, expiry, params, expiries):
        ended = cancelled["completed_paths"]
        mean, second = ended["pnl_mean_contribution"], ended["pnl_second_contribution"]
        mass = ended["probability"]
        after_old = state.yes-state.no+(order.remaining if order.side == "YES" else -order.remaining)
        residual = ended["probability"] if abs(after_old) > 1e-9 else 0.0
        submit_probability = requested_qty = new_gain = 0.0
        branches = []
        clear_time = state.elapsed+cancelled["modeled_cancel_effective_after_s"]
        for branch in cancelled["after_cancel_states"]:
            weight, filled = branch["probability"], branch["filled_shares"]
            child = replace(state, p=branch["p"], elapsed=clear_time,
                            yes=state.yes+(filled if order.side == "YES" else 0),
                            no=state.no+(filled if order.side == "NO" else 0),
                            cost=state.cost+filled*order.price)
            spec = repair_spec(child, params, expiry, order.side)
            if spec:
                row = self.new_stats(child, spec, expiry, params, expiries)
                branch_mean, branch_second = moments_from_increment(child, row)
                sign = 1 if spec[0] == "YES" else -1
                child_residual = sum(x["probability"] for x in row["filled_share_distribution"]
                                     if abs(child.yes-child.no+sign*x["shares"]) > 1e-9)
                submit_probability += weight
                requested_qty += weight*spec[2]
                new_gain += weight*row["incremental_ev"]
                branches.append({"probability": weight, "old_late_fill_shares": filled,
                                 "p_after_cancel": child.p, "side": spec[0],
                                 "price": spec[1], "new_order_shares": spec[2],
                                 "earliest_submit_elapsed": clear_time})
            else:
                branch_mean, branch_second = terminal_moments(child, "NO", 0, 0, child.p)
                child_residual = float(abs(child.yes-child.no) > 1e-9)
            mass += weight
            mean += weight*branch_mean
            second += weight*branch_second
            residual += weight*child_residual
        assert abs(mass-1) < 1e-9
        assert abs(mean-cancelled["expected_pnl"]-new_gain) < 1e-9
        return {"expected_pnl": mean, "settlement_sd": math.sqrt(max(0, second-mean**2)),
                "nonzero_residual_probability": residual,
                "replacement_submission_probability": submit_probability,
                "expected_replacement_requested_shares": requested_qty,
                "old_fills_during_cancel": cancelled["expected_filled_shares"],
                "expected_new_order_increment": new_gain,
                "replacement_branches": branches}

    def existing(self, state, order, allow_replacement=True):
        expiries = self.expiries(state)
        delta = state.yes-state.no
        still_repairing = (delta > 1e-9 and order.side == "NO") or (delta < -1e-9 and order.side == "YES")
        candidates = {}
        for name, params in self.models.items():
            keeper = evaluate_order(state, order.side, order.price, order.remaining,
                                    [0]+([max(0, e-state.elapsed) for e in expiries] if still_repairing else []), params,
                                    existing=True, queue_age_s=order.queue_age_s)
            cancelled = evaluate_order(state, order.side, order.price, order.remaining,
                                       [0], params, existing=True,
                                       queue_age_s=order.queue_age_s, export_states=True)["candidates"][0]
            for row in keeper["candidates"]:
                horizon = row["cancel_request_after_s"]
                action = "CANCEL" if horizon == 0 else "KEEP"
                key = (action, round(state.elapsed+horizon, 8))
                candidates.setdefault(key, {"action": action, "cancel_request_elapsed": key[1], "models": {}})
                candidates[key]["models"][name] = {
                    "expected_pnl": row["expected_pnl"], "settlement_sd": row["settlement_sd"],
                    "advantage_vs_cancel": row["expected_pnl"]-cancelled["expected_pnl"],
                    "nonzero_residual_probability": row["nonzero_residual_probability"]}
            for expiry in expiries:
                if not allow_replacement or not still_repairing or expiry <= state.elapsed or state.elapsed >= params.stop_s:
                    continue
                key = ("REPLACE_AFTER_CANCEL", round(expiry, 8))
                candidates.setdefault(key, {"action": key[0], "replacement_cancel_elapsed": key[1],
                                             "old_cancel_request_elapsed": state.elapsed, "models": {}})
                row = self.replacement(state, order, cancelled, expiry, params, expiries)
                row["advantage_vs_cancel"] = row["expected_pnl"]-cancelled["expected_pnl"]
                candidates[key]["models"][name] = row
        result = self.summarize(state, order, list(candidates.values()))
        if not still_repairing:
            result["intent_status"] = "ORIGINAL_GAP_COVERED_CANCEL_REMAINDER"
        return result

    def recheck(self, state, context, order=None):
        """Observed-state entry point: history never resets at a price review.

        Execution facts, not this valuation function, advance submissions_used.
        Unknown order/fill facts cannot be treated as an empty order book.
        """
        if context.repair_side not in {"YES", "NO"} or not 0 <= context.submissions_used <= context.max_submissions:
            raise ValueError("invalid episode context")
        if order and context.submissions_used == 0:
            raise ValueError("active repair order without recorded submission")
        reason = None
        if not context.facts_complete or (order and order.side != context.repair_side):
            action, reason = "CANCEL_AND_RECONCILE", "UNKNOWN_OR_INCONSISTENT_EXECUTION_FACTS"
        elif order:
            result = self.existing(state, order,
                                   allow_replacement=context.submissions_used < context.max_submissions)
            result["episode_context"] = asdict(context)
            return result
        else:
            delta = state.yes-state.no
            needed = "NO" if delta > 0 else "YES" if delta < 0 else None
            if not needed:
                action, reason = "HOLD", "PAIRED_NO_NEW_ORDER"
            elif needed != context.repair_side:
                action, reason = "HOLD", "ACCEPT_MINIMUM_SIZE_REVERSE_RESIDUE"
            elif context.submissions_used >= context.max_submissions:
                action, reason = "HOLD", "RESEARCH_SUBMISSION_BUDGET_EXHAUSTED"
            else:
                result = self.fresh(state)
                result["episode_context"] = asdict(context)
                return result
        record = {"action": action, "reason": reason}
        if action == "HOLD":
            record.update(hold_value(state))
        return {"state": asdict(state), "episode_context": asdict(context),
                "nominal_decision": record, "common_policy_stress_decision": record,
                "evaluation_status": "NOT_VALUED_UNKNOWN_FACTS" if action != "HOLD" else "KNOWN_HOLD_VALUE"}

    def fresh(self, state):
        expiries = self.expiries(state)
        hold = hold_value(state)
        candidates = [{"action": "HOLD", "models": {name: {
            **hold, "advantage_vs_cancel": 0.0} for name in self.models}}]
        for expiry in expiries:
            row = {"action": "BUY", "cancel_request_elapsed": expiry, "models": {}}
            common_spec = None
            for name, params in self.models.items():
                spec = repair_spec(state, params, expiry)
                if spec:
                    common_spec = spec
                    stats = self.new_stats(state, spec, expiry, params, expiries)
                    mean, second = moments_from_increment(state, stats)
                    row["models"][name] = {"expected_pnl": mean,
                        "settlement_sd": math.sqrt(max(0, second-mean**2)),
                        "advantage_vs_cancel": mean-hold["expected_pnl"]}
                else:
                    row["models"][name] = {**hold, "advantage_vs_cancel": 0.0,
                                            "no_legal_submission": True}
            if common_spec:
                row.update(side=common_spec[0], price=common_spec[1], quantity=common_spec[2])
                candidates.append(row)
        return self.summarize(state, None, candidates)

    def summarize(self, state, order, candidates):
        nominal_name = next(iter(self.models))
        priority = {"HOLD": 4, "CANCEL": 3, "KEEP": 2, "BUY": 1, "REPLACE_AFTER_CANCEL": 0}
        for row in candidates:
            advantages = [r["advantage_vs_cancel"] for r in row["models"].values()]
            row["min_model_advantage"] = min(advantages)
            row["max_model_advantage"] = max(advantages)
        # Tie at ~1e-10 avoids manufacturing extra submissions from numerical
        # noise. This tolerance is numerical, not a calibrated trading margin.
        nominal = max(candidates, key=lambda r: (
            round(r["models"][nominal_name]["advantage_vs_cancel"], 10), priority[r["action"]]))
        robust = max(candidates, key=lambda r: (
            round(r["min_model_advantage"], 10), priority[r["action"]]))
        return {"state": asdict(state), "order": asdict(order) if order else None,
                "nominal_model": nominal_name, "nominal_decision": nominal,
                "common_policy_stress_decision": robust, "candidates": candidates}


def model_set():
    base = Parameters()
    return {"nominal": base, "low_liquidity": replace(base, liquidity_rate=.5),
            "high_liquidity": replace(base, liquidity_rate=5),
            "limited_cross_depth": replace(base, cross_fill_shares=1,
                                            cross_fill_probability=.5)}


def checks():
    params = Parameters()
    # Exported cancellation states preserve all probability mass.
    state = State(.30, .02, 102)
    row = evaluate_order(state, "NO", .68, 5, [0], params, existing=True,
                         queue_age_s=1.8, export_states=True)["candidates"][0]
    assert abs(sum(b["probability"] for b in row["after_cancel_states"])+
               row["completed_paths"]["probability"]-1) < 1e-10
    # Cached increment moments reproduce direct values for different inventory
    # and sunk cost, including minimum-size reversal after a partial fill.
    actual = replace(state, no=3, cost=3.54)
    direct = evaluate_order(actual, "NO", .69, 5, [5], params)["candidates"][0]
    template = evaluate_order(replace(state, p=.30, cost=0), "NO", .69, 5,
                              [5], params)["candidates"][0]
    mean, second = moments_from_increment(actual, template)
    assert abs(mean-direct["expected_pnl"]) < 1e-9
    assert abs(math.sqrt(max(0, second-mean**2))-direct["settlement_sd"]) < 1e-9
    # Book adapter uses supplied prices, including an actual half-tick midpoint.
    half = State(.305, 0, 100)
    bound = bind_book(half, "NO", Book(.69, .70), {"nominal": params})
    plan = Planner(bound, (2,)).fresh(half)
    assert all(c.get("price", .69) == .69 for c in plan["candidates"])
    assert plan["nominal_decision"]["action"] == "BUY"
    try:
        bind_book(half, "NO", Book(.71, .70), {"nominal": params})
    except ValueError:
        pass
    else:
        raise AssertionError("crossed input book silently accepted")
    # Tick-round future quotes when a half-tick price lattice changes parity.
    moving = replace(half, sigma=.02)
    bound_moving = bind_book(moving, "NO", Book(.69, .70), {"nominal": params})
    moving_plan = Planner(bound_moving, (.5,)).fresh(moving)
    assert moving_plan["candidates"][1]["price"] == .69
    # A market-through quote need not fully fill under finite-depth stress.
    shallow = replace(params, cross_fill_shares=1, cross_fill_probability=.5)
    crossed = evaluate_order(State(.35, 0, 102), "NO", .68, 5, [0], shallow,
                             existing=True, queue_age_s=2)["candidates"][0]
    assert crossed["full_fill_probability"] == 0
    assert 0 < crossed["expected_filled_shares"] < 5
    # Closed trading boundary: cancellation can have late fills; no replacement.
    closed = Planner({"nominal": params}, (2,)).existing(
        State(.30, 0, 240), Order("NO", .68, 5, 2))
    assert closed["nominal_decision"]["action"] == "CANCEL"
    assert all(c["action"] != "REPLACE_AFTER_CANCEL" for c in closed["candidates"])
    assert repair_spec(State(.31, 0, 239.9), params, 240) is None
    # Reversed minimum-size residue is accepted, not automatically bought back.
    assert repair_spec(State(.85, 0, 102, no=6, cost=2.4), params, 240, "NO") is None
    flipped = Planner({"nominal": params}, (2,)).existing(
        State(.85, 0, 102, no=6, cost=2.34), Order("NO", .14, 1.15, 5))
    assert flipped["nominal_decision"]["action"] == "CANCEL"
    assert flipped["intent_status"] == "ORIGINAL_GAP_COVERED_CANCEL_REMAINDER"
    context = EpisodeContext("NO", 1)
    reversed_idle = Planner({"nominal": params}, (2,)).recheck(
        State(.85, 0, 102, no=6, cost=2.34), context)
    assert reversed_idle["nominal_decision"]["action"] == "HOLD"
    assert reversed_idle["nominal_decision"]["reason"] == "ACCEPT_MINIMUM_SIZE_REVERSE_RESIDUE"
    unknown = Planner({"nominal": params}, (2,)).recheck(
        State(.31, 0, 102), replace(context, facts_complete=False))
    assert unknown["nominal_decision"]["action"] == "CANCEL_AND_RECONCILE"
    exhausted = Planner({"nominal": params}, (2,)).recheck(
        State(.31, 0, 102), replace(context, submissions_used=2))
    assert exhausted["nominal_decision"]["reason"] == "RESEARCH_SUBMISSION_BUDGET_EXHAUSTED"
    # YES/NO complement symmetry includes price-correlated execution.
    left = evaluate_order(State(.31, .02, 100), "NO", .68, 5, [5], params)["candidates"][0]
    right = evaluate_order(State(.69, .02, 100, yes=0, no=5), "YES", .68, 5, [5], params)["candidates"][0]
    assert abs(left["expected_pnl"]-right["expected_pnl"]) < 1e-9
    assert abs(left["settlement_sd"]-right["settlement_sd"]) < 1e-9
    # Preserve available funds while translating historic cost; continuation
    # choices and variance must not acquire a sunk-cost trigger.
    planner = Planner({"nominal": params}, (2, 5))
    order = Order("NO", .68, 5, 1.8)
    a = planner.existing(State(.30, .02, 102, other_reserved=3), order)
    b = planner.existing(State(.30, .02, 102, cost=3.75, other_reserved=.75), order)
    for first, second in zip(a["candidates"], b["candidates"]):
        x, y = first["models"]["nominal"], second["models"]["nominal"]
        assert abs(x["expected_pnl"]-y["expected_pnl"]-2.25) < 1e-9
        assert abs(x["settlement_sd"]-y["settlement_sd"]) < 1e-8
    assert a["nominal_decision"]["action"] == b["nominal_decision"]["action"]
    # Fully filled during cancellation means no replacement can be submitted.
    filled_late = planner.existing(State(.35, .02, 102), order)
    for candidate in filled_late["candidates"]:
        if candidate["action"] == "REPLACE_AFTER_CANCEL":
            assert candidate["models"]["nominal"]["replacement_submission_probability"] == 0
    for candidate in a["candidates"]:
        if candidate["action"] == "REPLACE_AFTER_CANCEL":
            for branch in candidate["models"]["nominal"]["replacement_branches"]:
                assert branch["earliest_submit_elapsed"] >= 102.2-1e-9
    last_order = planner.recheck(State(.30, .02, 102), EpisodeContext("NO", 2), order)
    assert all(c["action"] != "REPLACE_AFTER_CANCEL" for c in last_order["candidates"])


def main():
    checks()
    models = model_set()
    planner = Planner(models)
    scenarios = [{"label": "fresh_T100", "result": planner.fresh(State(.31, .02, 100))}]
    for p in (.25, .30, .35):
        for filled in (0, 1, 3):
            scenarios.append({"label": f"T102_p{p}_filled{filled}", "result": planner.existing(
                State(p, .02, 102, no=filled, cost=1.5+filled*.68),
                Order("NO", .68, 5-filled, 1.8))})
    scenarios.append({"label": "T220_old_quote_far_from_book", "result": planner.existing(
        State(.31, .02, 220), Order("NO", .40, 5, 20))})
    scenarios.append({"label": "minimum_notional_repair_partially_reversed", "result": planner.existing(
        State(.85, .02, 102, no=6, cost=2.34), Order("NO", .14, 1.15, 5))})
    # A *structural sensitivity*, not an observed/fitted regime: beneficial
    # liquidity for an old quote fades sharply after age 20s. The optimizer
    # should be able to choose an interior duration rather than always T240.
    fades = replace(Parameters(), flow_decay_after_s=20, flow_decay_multiplier=.03)
    duration_case = Planner({"nominal": fades}).fresh(State(.31, .02, 100))
    assert duration_case["nominal_decision"]["cancel_request_elapsed"] < 240
    sequence_planner = Planner({"nominal": Parameters()})
    sequence = []
    for elapsed, price, filled in ((102, .25, 1), (104, .30, 3), (106, .35, 5)):
        observed = State(price, .02, elapsed, no=filled, cost=1.5+filled*.68)
        active_order = Order("NO", .68, 5-filled, elapsed-100.2) if filled < 5 else None
        result = sequence_planner.recheck(observed, EpisodeContext("NO", 1), active_order)
        sequence.append({"observed_fills": filled, "result": result})
    output = {"status": "UNCALIBRATED_TWO_ORDER_MODEL_NOT_A_LIVE_RECOMMENDATION",
              "model_parameters": {k: asdict(v) for k, v in models.items()},
              "scope": "one cancellation plus contingent replacement; then hold to settlement",
              "scenarios": scenarios, "cache_evaluations": planner.cache_evaluations,
              "cache_hits": planner.cache_hits,
              "finite_duration_stress": {"parameters": asdict(fades), "result": duration_case},
              "observed_state_rechecks": sequence}
    Path(__file__).with_name("continuation_examples.json").write_text(json.dumps(output, indent=2)+"\n")
    print(json.dumps({"checks": "passed", "cache_evaluations": planner.cache_evaluations,
        "finite_duration_stress": {"action": duration_case["nominal_decision"]["action"],
            "cancel_request_elapsed": duration_case["nominal_decision"]["cancel_request_elapsed"],
            "value": duration_case["nominal_decision"]["models"]["nominal"]["expected_pnl"]},
        "scenarios": [{"label": x["label"], "nominal": {
            "action": x["result"]["nominal_decision"]["action"],
            "value": x["result"]["nominal_decision"]["models"]["nominal"]["expected_pnl"]},
            "stress": {"action": x["result"]["common_policy_stress_decision"]["action"],
                       "min_advantage": x["result"]["common_policy_stress_decision"]["min_model_advantage"]}}
            for x in scenarios]}, indent=2))


if __name__ == "__main__":
    main()
