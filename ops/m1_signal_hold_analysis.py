"""Read-only signal-price full-fill ledger with explicit order reconciliation.

This estimates returns conditional on fills, not achievable Maker fills. It does
not replay unobserved signal episodes or change the live study or fill model.
"""

import argparse
import csv
import hashlib
import importlib.util
import json
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path


def analyze(state, plan, *, min_price=None, max_price=None):
    if (min_price is None) != (max_price is None):
        raise ValueError("Both price bounds are required")
    if min_price is not None and not D(0) < min_price <= max_price < D(1):
        raise ValueError("Invalid inclusive price bounds")
    approved = {(e["epoch"], e["original_order"]["episode_id"]) for e in plan["orders"]}
    markets, trades = [], []
    cumulative = total_cost = total_payout = D(0)
    included, skipped, unobserved = set(), set(), set(approved)
    included_profit = included_cost = included_payout = D(0)
    pending = incomplete = budget_skipped = price_filtered = 0
    rows = sorted((r for r in state["sessions"] if r["phase"] == "EVALUATION"), key=lambda r: r["epoch"])
    for index, row in enumerate(rows, 1):
        keys = {(row["epoch"], s["episode_id"]) for s in row.get("paper", {}).get("signals", [])}
        unobserved -= keys
        info = {"index": index, "epoch": row["epoch"],
                "time": datetime.fromtimestamp(row["epoch"], timezone(timedelta(hours=8))).strftime("%m/%d %H:%M"),
                "reclassified_orders": len(keys & approved)}
        if not row.get("complete_window") or not row.get("audit_valid") or not row.get("paper"):
            incomplete += 1
            markets.append({**info, "status": "异常", "pnl": None})
            continue
        resolution = row.get("settlement", {}).get("resolution")
        if not resolution or resolution.get("resolution_status") != "resolved" or resolution.get("closed") is not True:
            pending += 1
            markets.append({**info, "status": "待结算", "pnl": None})
            continue
        spent = payout = D(0)
        count = reclassified_count = 0
        for signal in row["paper"]["signals"]:
            key = (row["epoch"], signal["episode_id"])
            price, quantity = D(signal["price"]), D(signal["size"])
            cost = price*quantity
            assert price.is_finite() and quantity.is_finite() and 0 < price < 1 and quantity > 0 and cost <= 5
            if min_price is not None and not min_price <= price <= max_price:
                price_filtered += 1
                continue
            if spent+cost > 20:
                budget_skipped += 1
                if key in approved:
                    skipped.add(key)
                continue
            spent += cost
            received = quantity if signal["outcome"] == resolution["winner"] else D(0)
            payout += received
            count += 1
            is_approved = key in approved
            if is_approved:
                included.add(key)
                reclassified_count += 1
                included_cost += cost
                included_payout += received
                included_profit += received-cost
            trades.append({**info, "id": len(trades)+1, "episode_id": signal["episode_id"],
                           "side": signal["outcome"], "price": str(price), "quantity": str(quantity),
                           "cost": str(cost), "payout": str(received), "pnl": str(received-cost),
                           "reclassified": is_approved, "winner": resolution["winner"]})
        start = cumulative
        value = payout-spent
        cumulative += value
        total_cost += spent
        total_payout += payout
        markets.append({**info, "status": "已结算", "pnl": str(value), "start": str(start), "end": str(cumulative),
                        "signals": count, "cost": str(spent), "payout": str(payout),
                        "included_reclassified_orders": reclassified_count})
    assert len(included) == sum(t["reclassified"] for t in trades)
    assert cumulative == total_payout-total_cost == sum((D(t["pnl"]) for t in trades), D(0))
    return {"as_of_utc": datetime.now(UTC).isoformat(), "recorded_markets": len(rows), "target": state["evaluation_target"],
            "scope": "RECORDED_M1_SIGNALS_FULL_FILL_AT_MAKER_QUOTE_HOLD_TO_OFFICIAL_SETTLEMENT",
            "window": [15, 240], "per_order_budget_usdc": "5", "per_market_budget_usdc": "20",
            "summary": {"markets": len(rows)-pending-incomplete, "pending": pending, "incomplete": incomplete,
                        "signals": len(trades), "budget_skipped": budget_skipped, "cost": str(total_cost),
                        "payout": str(total_payout), "pnl": str(cumulative),
                        "wins": sum(D(t["pnl"]) > 0 for t in trades), "losses": sum(D(t["pnl"]) < 0 for t in trades)},
            "price_filter": {"minimum_inclusive": str(min_price) if min_price is not None else None,
                             "maximum_inclusive": str(max_price) if max_price is not None else None,
                             "excluded_settled_signals": price_filtered, "applied_before_budget": True},
            "reclassified": {"orders": len(approved), "distinct_markets": len({k[0] for k in approved}),
                             "included": len(included), "budget_skipped": len(skipped), "unobserved": len(unobserved),
                             "cost": str(included_cost), "payout": str(included_payout), "pnl": str(included_profit),
                             "already_included_in_previous_signal_price_assumption": True},
            "markets": markets, "trades": trades}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study_dir", type=Path)
    parser.add_argument("--min-price", type=D)
    parser.add_argument("--max-price", type=D)
    args = parser.parse_args()
    directory = args.study_dir
    raw = (directory/"study.json").read_bytes()
    state = json.loads(raw)
    plan = json.loads((directory/"paper-ooo10-applied-plan.json").read_text())
    baseline = analyze(state, plan)
    result = analyze(state, plan, min_price=args.min_price, max_price=args.max_price)
    # Reconcile to the existing counterfactual report, using one immutable read.
    spec = importlib.util.spec_from_file_location("m1_hold_report", Path(__file__).with_name("m1_study_report.py"))
    reporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reporter)
    expected = reporter.ideal_hold([r for r in state["sessions"] if r["phase"] == "EVALUATION"])
    for key in ("cost", "payout", "pnl"):
        assert D(baseline["summary"][key]) == expected[key]
    for key in ("markets", "pending", "incomplete", "signals", "budget_skipped", "wins", "losses"):
        assert baseline["summary"][key] == expected[key]
    if args.min_price is not None:
        result["comparison"] = {"unfiltered": baseline["summary"],
            "delta_pnl_usdc": str(D(result["summary"]["pnl"])-D(baseline["summary"]["pnl"])),
            "same_market_cohort": True,
            "newly_included_after_budget_replay": len(
                {(t["epoch"], t["episode_id"]) for t in result["trades"]}
                - {(t["epoch"], t["episode_id"]) for t in baseline["trades"]})}
    result["source_study_sha256"] = hashlib.sha256(raw).hexdigest()
    result["analysis_source_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    prefix = "signal-hold-price-filter" if args.min_price is not None else "signal-hold-waterfall"
    (directory/f"{prefix}-snapshot.json").write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n")
    for key in ("markets", "trades"):
        columns = list(dict.fromkeys(name for row in result[key] for name in row))
        with (directory/f"{prefix}-{key}.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            writer.writerows(result[key])
    print(json.dumps({key: result[key] for key in ("summary", "price_filter", "comparison", "reclassified") if key in result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
