"""Reconcile completed offline replay members to their economic audit evidence."""

import argparse
import json
import sqlite3
from collections import defaultdict
from contextlib import closing
from decimal import Decimal as D
from pathlib import Path

GROUPS = tuple("M" + str(i) for i in range(5, 11))


def audit_rows(path, query):
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        return db.execute(query).fetchall()


def physical_root(payload, facts):
    """Match initiating public evidence across versions, not registry counters."""
    def fact_key(f):
        return tuple(f.get(k) for k in (
            "kind", "token", "generation", "start", "end", "confirmed", "input_seq", "anchor"))

    parts = []
    for identity in payload["root_id"].split("|"):
        fact = facts[identity]
        parts.append((fact_key(fact), tuple(fact_key(facts[r]) for r in fact["refs"])))
    return json.dumps(parts, sort_keys=True)


def review_run(run, markets):
    plan = run / "plan.json"
    if plan.exists() and json.loads(plan.read_text()).get("mode") != "SIMULATION_ONLY":
        raise ValueError("ECONOMIC_REVIEW_REQUIRES_SIMULATION_ONLY_RUN_NOT_PARAMETER_SHADOWS")
    aggregates = {}
    members = []
    semantic = {}
    roots = {}
    for market in markets:
        path = run / market / "replay-state.json"
        if not path.exists():
            continue
        state = json.loads(path.read_text())
        if not state["coverage"]["complete_window"] or state["validity"]["status"] != "VALID" or not state["resolution"]:
            raise ValueError("INELIGIBLE_REPLAY_MEMBER")
        database = path.with_name("simulation.sqlite3")
        signals = {s: set() for s in GROUPS}
        semantic[market] = []
        roots[market] = {s: {} for s in ("M7", "M9", "M10")}
        facts = {p["id"]: p for p in (
            json.loads(raw)["payload"] for raw, in audit_rows(database,
                "SELECT payload FROM audit WHERE kind='RESEARCH_EVIDENCE' AND json_extract(payload,'$.event')='FACT_CONFIRMED'"))}
        for raw, in audit_rows(database, "SELECT payload FROM audit WHERE kind='SIGNAL' AND json_extract(payload,'$.event')='CANDIDATE'"):
            row = json.loads(raw)
            s, p = row["strategy"], row["payload"]
            signals[s].add((p.get("generation"), p["id"]))
            if s in roots[market]:
                roots[market][s][p["id"]] = physical_root(p, facts)
            semantic[market].append(tuple(p.get(k) for k in (
                "strategy", "token", "outcome", "anchor_price", "confirmed", "deadline")))
        fill_qty = defaultdict(lambda: D(0))
        fill_orders = set()
        for raw, in audit_rows(database, "SELECT payload FROM audit WHERE kind='MODELLED_FILL'"):
            p = json.loads(raw)
            quantity = D(p["quantity"])
            assert quantity > 0
            fill_qty[p["scenario"], p["order"]] += quantity
            fill_orders.add((p["scenario"], p["order"]))
        for scenario, broker in state["brokers"].items():
            for s in GROUPS:
                key = s + "/" + scenario
                a = aggregates.setdefault(key, {
                    "strategy": s, "scenario": scenario, "signals": 0, "submitted": 0,
                    "assumed_filled": 0, "modelled_filled": 0, "unresolved": 0,
                    "rule_unfillable_orders": 0, "unresolved_rule_unfillable_orders": 0,
                    "assumed_by_market": {}, "modelled_by_market": {},
                    "assumed_trading_markets": [], "modelled_trading_markets": [],
                    "assumed_low_price_pnl": D(0), "modelled_low_price_pnl": D(0),
                    "modelled_pnl_lower": D(0), "modelled_pnl_upper": D(0),
                })
                orders = [o for o in broker["orders"].values() if o["intent"]["strategy"] == s]
                a["signals"] += len(signals[s])
                a["submitted"] += len(orders)
                a["assumed_filled"] += len(orders)
                assumed = modelled = D(0)
                unknown_lower = unknown_upper = D(0)
                for order in orders:
                    i = order["intent"]
                    q, filled, price = D(i["quantity"]), D(order["filled"]), D(i["price"])
                    assert fill_qty[scenario, order["id"]] == filled
                    assert ((scenario, order["id"]) in fill_orders) == (filled > 0)
                    assert 0 <= filled <= q and D(order["spent"]) == price*filled
                    a["modelled_filled"] += int(filled > 0)
                    a["unresolved"] += int(order["uncertain"])
                    unfillable = (scenario == "research/trade-through"
                                  and price-D(broker["submitted_ticks"][order["id"]]) <= 0)
                    a["rule_unfillable_orders"] += int(unfillable)
                    a["unresolved_rule_unfillable_orders"] += int(unfillable and order["uncertain"])
                    if unfillable:
                        assert filled == 0
                    payoff = int(i["outcome"] == state["resolution"]["winner"])-price
                    assumed += q*payoff
                    modelled += filled*payoff
                    if order["uncertain"] and not unfillable:
                        possible = (q-filled)*payoff
                        unknown_lower += min(D(0), possible)
                        unknown_upper += max(D(0), possible)
                    if D(".01") <= price <= D(".02"):
                        a["assumed_low_price_pnl"] += q*payoff
                        a["modelled_low_price_pnl"] += filled*payoff
                account = broker["accounts"][s]
                assert D(account["payout"])-D(account["spent"]) == modelled
                a["assumed_by_market"][market] = assumed
                a["modelled_by_market"][market] = modelled
                a["modelled_pnl_lower"] += modelled+unknown_lower
                a["modelled_pnl_upper"] += modelled+unknown_upper
                if orders:
                    a["assumed_trading_markets"].append(market)
                if any(D(o["filled"]) > 0 for o in orders):
                    a["modelled_trading_markets"].append(market)
        members.append(market)
    for a in aggregates.values():
        for mode in ("assumed", "modelled"):
            values = a[mode+"_by_market"]
            total = sum(values.values(), D(0))
            best = sorted((v for v in values.values() if v > 0), reverse=True)
            a[mode+"_pnl"] = total
            a[mode+"_without_best_1"] = total-sum(best[:1], D(0))
            a[mode+"_without_best_3"] = total-sum(best[:3], D(0))
            a[mode+"_trading_market_count"] = len(a[mode+"_trading_markets"])
    return {"status": "COMPLETE" if len(members) == len(markets) else "PARTIAL",
            "counted_markets": len(members), "expected_markets": len(markets),
            "missing_markets": sorted(set(markets)-set(members)),
            "results": aggregates, "semantic_candidates": semantic, "signal_roots": roots}


def incremental_cohorts(root, old, new, new_name="next25-v3b"):
    totals = {s: {"added_candidates": 0, "lost_candidates": 0, "common_candidates": 0,
                  "orders": {}} for s in ("M7", "M9", "M10")}
    markets = sorted(set(old["signal_roots"]) & set(new["signal_roots"]))
    for market in markets:
        for strategy in totals:
            a = set(old["signal_roots"][market][strategy].values())
            b = set(new["signal_roots"][market][strategy].values())
            totals[strategy]["added_candidates"] += len(b-a)
            totals[strategy]["lost_candidates"] += len(a-b)
            totals[strategy]["common_candidates"] += len(a & b)
            for label, run, roots in (("old", "oldsignals-through25", old), ("new", new_name, new)):
                state = json.loads((root / run / market / "replay-state.json").read_text())
                for order in state["brokers"]["research/trade-through"]["orders"].values():
                    intent = order["intent"]
                    if intent["strategy"] != strategy:
                        continue
                    physical = roots["signal_roots"][market][strategy][intent["opportunity"]]
                    cohort = "common" if physical in a & b else "added" if label == "new" else "lost"
                    key = label+"/"+cohort
                    counts = totals[strategy]["orders"].setdefault(key, {
                        "submitted": 0, "modelled_filled": 0, "unresolved": 0,
                        "assumed_pnl": D(0), "known_modelled_pnl": D(0),
                        "markets": [],
                    })
                    counts["submitted"] += 1
                    counts["modelled_filled"] += int(D(order["filled"]) > 0)
                    counts["unresolved"] += int(order["uncertain"])
                    payoff = int(intent["outcome"] == state["resolution"]["winner"])-D(intent["price"])
                    counts["assumed_pnl"] += D(intent["quantity"])*payoff
                    counts["known_modelled_pnl"] += D(order["filled"])*payoff
                    if market not in counts["markets"]:
                        counts["markets"].append(market)
    return {"compared_markets": len(markets), "results": totals,
            "definition": "Match the same physical initiating AP and support/EX evidence timestamps/input positions, ignoring registry ID counters and TTL changes. Common roots may submit at different times/prices. This is an in-sample frequency calibration, not independent profitability validation."}


def unchanged_execution(root, markets, new_name="next25-v3b"):
    results = {s: True for s in ("M5", "M6", "M8")}

    def signature(state, strategy):
        broker = state["brokers"]["research/trade-through"]
        orders = []
        for order in broker["orders"].values():
            i = order["intent"]
            if i["strategy"] == strategy:
                orders.append(tuple(i[k] for k in (
                    "token", "outcome", "price", "quantity", "available", "deadline"))
                    + tuple(order[k] for k in ("filled", "spent", "reserved", "uncertain", "state")))
        return broker["accounts"][strategy], sorted(orders)

    for market in markets:
        a = json.loads((root / "oldsignals-through25" / market / "replay-state.json").read_text())
        b = json.loads((root / new_name / market / "replay-state.json").read_text())
        for strategy in results:
            results[strategy] &= signature(a, strategy) == signature(b, strategy)
    return {"compared_markets": len(markets), "strategies": results}


def same_submission_cohort(root, markets):
    result = {s: True for s in GROUPS}
    compared = 0
    def intents(state, scenario, strategy):
        return sorted(tuple(o["intent"][k] for k in (
            "token", "outcome", "price", "quantity", "available", "deadline", "opportunity"))
            for o in state["brokers"][scenario]["orders"].values()
            if o["intent"]["strategy"] == strategy)
    for market in markets:
        paths = [root / name / market / "replay-state.json" for name in (
            "baseline25", "oldsignals-through25")]
        if not all(p.exists() for p in paths):
            continue
        old, new = (json.loads(p.read_text()) for p in paths)
        for strategy in GROUPS:
            target = intents(new, "research/trade-through", strategy)
            result[strategy] &= all(intents(old, scenario, strategy) == target for scenario in (
                "research/trades-only", "research/cancel-half"))
        compared += 1
    return {"compared_markets": compared, "strategies": result,
            "definition": "Same token, outcome, limit, quantity, submission time, T240 deadline and candidate; each legacy queue cohort versus new through cohort"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--new-run", default="next25-v3b")
    args = parser.parse_args()
    root = args.directory.resolve()
    markets = [r["market"] for r in json.loads((root / "cohort25.json").read_text())["markets"]]
    runs = {name: review_run(root / name, markets) for name in (
        "baseline25", "oldsignals-through25", args.new_run)}
    old, new = runs["oldsignals-through25"], runs[args.new_run]
    compared = set(old["semantic_candidates"]) & set(new["semantic_candidates"])
    unchanged = {
        s: all(sorted(x for x in old["semantic_candidates"][m] if x[0] == s)
               == sorted(x for x in new["semantic_candidates"][m] if x[0] == s) for m in compared)
        for s in ("M5", "M6", "M8")}
    result = {"status": "COMPLETE" if all(r["status"] == "COMPLETE" for r in runs.values()) else "PARTIAL",
              "completion_scope": "Replay processing completion, not proof of wholly reconstructable source windows",
              "source_quality_evidence": "cohort-quality.json",
              "signal_count_interpretation": "Confirmed candidates within provable intervals; ambiguous intervals remain unavailable, never evidence of zero missed signals",
              "definitions": {"pnl": "USDC, gross, official settlement only",
                              "assumed": "full quantity of admitted SUBMITs; not all candidates",
                              "modelled": "known positive MODELLED_FILL quantity; unresolved remainder separate",
                              "remove_best": "remove only largest positive market contributions; retain all losses",
                              "scope": "same 25 markets, all submitted prices; no M6 price filter"},
              "unchanged_M5_M6_M8_candidate_semantics": unchanged,
              "compared_markets_for_unchanged_check": len(compared), "runs": runs,
              "incremental_signal_cohorts": incremental_cohorts(root, old, new, args.new_run),
              "unchanged_M5_M6_M8_execution": unchanged_execution(root, compared, args.new_run)}
    result["submission_cohort_equivalence_oldqueue_to_through"] = same_submission_cohort(root, markets)
    filename = "comparison.json" if args.new_run == "next25-v3b" else f"comparison-{args.new_run}.json"
    (root / filename).write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str)+"\n")
    print(json.dumps({"status": result["status"], "markets": {k:v["counted_markets"] for k,v in runs.items()},
                      "unchanged": unchanged}, ensure_ascii=False))


if __name__ == "__main__":
    main()
