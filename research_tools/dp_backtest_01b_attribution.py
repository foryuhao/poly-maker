"""Post-hoc settlement and fill attribution for the unchanged Backtest 01 replay.

Run after dp_backtest_01_inventory_skew.py. Reads persisted outputs and market
events only; never changes quotes, fills, timing, or strategy inventory.
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean, median

from dp_backtest_01_inventory_skew import Book, QTY, RATE

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research_outputs/dp_backtest_01"
DATA = ROOT / "research_data/m4a"


def read_csv(name):
    return list(csv.DictReader((OUT / name).open()))


def write_csv(name, rows):
    if not rows:
        raise RuntimeError(f"No rows for {name}")
    with (OUT / name).open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def trade_key(row):
    return row["market"], row["side"], int(row["trade_event_seq"])


def prior_inventory(rows, t, seq):
    yes = no = 0
    for row in rows:
        if (int(row["fill_available_us"]), int(row["trade_event_seq"])) >= (t, seq):
            break
        if row["side"] == "YES":
            yes += QTY
        else:
            no += QTY
    return yes, no


def active_order(orders, t):
    live = [o for o in orders if int(o["activation_us"]) <= t
            and (not o["closed_us"] or t < int(o["closed_us"]))]
    if len(live) > 1:
        raise RuntimeError("More than one simulated live order on one side")
    return live[0] if live else None


def book_at_target_events(targets, audit):
    out = {}
    selected = {r["market"]: r["session"] for r in audit if r["execution_replay_usable"] == "True"}
    for market, seqs in targets.items():
        session = selected[market]
        path = DATA / session
        m = json.loads((path / "manifest.json").read_text())
        sides = {m["yes_token_id"]: "YES", m["no_token_id"]: "NO"}
        books = {"YES": Book(), "NO": Book()}
        start = int(market.rsplit("-", 1)[1]) * 1_000_000
        end = start + 300_000_000
        remaining = set(seqs)
        for line in (path / "events.jsonl").open():
            event = json.loads(line)
            t = event.get("available_at_us")
            if t is None or t < start or t >= end:
                continue
            payload = event.get("payload") or {}
            side = sides.get(payload.get("token_id"))
            if side is None:
                continue
            kind = event["kind"]
            if kind in ("BOOK", "REST_BOOK", "DELTA"):
                books[side].update(kind, payload)
            elif kind == "TICK":
                books[side].tick = float(payload["tick_size"])
            if kind == "TRADE" and event["event_seq"] in remaining:
                b = books[side]
                out[(market, event["event_seq"])] = {"side": side,
                    "best_bid": b.bid / 10000 if b.bid is not None else None,
                    "best_ask": b.ask / 10000 if b.ask is not None else None,
                    "tick": b.tick}
                remaining.remove(event["event_seq"])
                if not remaining:
                    break
        if remaining:
            raise RuntimeError(f"Missing target public trades: {market}, {sorted(remaining)}")
    return out


def main():
    fills = read_csv("fills.csv")
    orders = read_csv("orders.csv")
    markets = read_csv("market_results.csv")
    labels = read_csv("settlement_labels.csv")
    audit = read_csv("dataset_audit.csv")
    winner = {r["market"]: r["winning_outcome"] for r in labels
              if r["resolved"] == "True" and r["identity_validation"] == "MATCH"}
    selected = {r["market"] for r in audit if r["execution_replay_usable"] == "True"}
    if len(selected) != 20 or set(winner) != selected:
        raise RuntimeError("Attribution requires all 20 frozen markets resolved")
    by_policy = defaultdict(list)
    for f in fills:
        by_policy[(f["market"], f["strategy"])].append(f)
    enriched = []
    counts = defaultdict(lambda: defaultdict(int))
    for key, fs in by_policy.items():
        fs.sort(key=lambda f: (int(f["fill_available_us"]), int(f["trade_event_seq"])))
        yes = no = 0
        for f in fs:
            before_y, before_n = yes, no
            before = abs(yes - no)
            if f["side"] == "YES":
                yes += QTY
            else:
                no += QTY
            after = abs(yes - no)
            category = "BALANCING" if after < before else "IMBALANCE-INCREASING" if after > before else "NEUTRAL"
            counts[f["strategy"]][category] += 1
            contribution = QTY * ((1 if f["side"] == winner[f["market"]] else 0) - float(f["price"]))
            enriched.append({**f, "winner": winner[f["market"]],
                "inventory_before_yes": before_y, "inventory_before_no": before_n,
                "inventory_after_yes": yes, "inventory_after_no": no,
                "fill_class": category, "expost_settlement_contribution": round(contribution, 6)})
    write_csv("fill_attribution.csv", enriched)
    a_fills = {trade_key(f): f for f in enriched if f["strategy"] == "A"}
    b_fills = {trade_key(f): f for f in enriched if f["strategy"] == "B"}
    if len(a_fills) != 89 or len(b_fills) != 79:
        raise RuntimeError("Original 89/79 execution sample changed")
    a_only = [a_fills[k] for k in sorted(a_fills.keys() - b_fills.keys())]
    b_only = [b_fills[k] for k in sorted(b_fills.keys() - a_fills.keys())]
    order_index = defaultdict(list)
    for o in orders:
        order_index[(o["market"], o["strategy"], o["side"])].append(o)
    targets = defaultdict(set)
    for f in a_only:
        targets[f["market"]].add(int(f["trade_event_seq"]))
    books = book_at_target_events(targets, audit)
    avoided = []
    for f in a_only:
        market, side, seq = trade_key(f)
        t = int(f["fill_available_us"])
        b_order = active_order(order_index[(market, "B", side)], t)
        b_y, b_n = prior_inventory(by_policy[(market, "B")], t, seq)
        net = b_y - b_n
        skew_ticks = round(abs(net) * RATE)
        heavy = "YES" if net > 0 else "NO" if net < 0 else None
        book = books[(market, seq)]
        desired = None
        if book["best_bid"] is not None:
            desired = round(book["best_bid"] - (skew_ticks * book["tick"] if side == heavy else 0), 4)
        b_resting = float(b_order["price"]) if b_order else None
        a_quote = float(f["price"])
        mechanism = ("lower_B_resting_quote" if b_resting is not None and b_resting < a_quote else
                     "B_transition_with_lower_desired_quote" if b_resting is None and desired is not None and desired < a_quote else
                     "downstream_inventory_or_lifecycle_path")
        avoided.append({"market": market, "trade_event_seq": seq, "side": side,
            "A_order_id": f["order_id"], "A_fill_price": a_quote,
            "A_inventory_before_yes": f["inventory_before_yes"],
            "A_inventory_before_no": f["inventory_before_no"],
            "A_quote": a_quote, "B_resting_quote": b_resting if b_resting is not None else "",
            "B_desired_quote_at_event": desired if desired is not None else "",
            "B_inventory_before_yes": b_y, "B_inventory_before_no": b_n,
            "B_skew_ticks": skew_ticks, "B_order_id": b_order["order_id"] if b_order else "",
            "public_best_bid": book["best_bid"], "public_best_ask": book["best_ask"],
            "winning_side": winner[market], "A_fill_class": f["fill_class"],
            "A_expost_settlement_contribution": f["expost_settlement_contribution"],
            "mechanism": mechanism})
    if avoided:
        write_csv("avoided_fills.csv", avoided)
    by_market_policy = {(r["market"], r["strategy"]): r for r in markets}
    comparison = []
    decomposition = []
    for market in sorted(selected):
        a, b = by_market_policy[(market, "A")], by_market_policy[(market, "B")]
        af = by_policy[(market, "A")]
        bf = by_policy[(market, "B")]
        def economic_split(fs, row):
            y = [float(f["price"]) for f in fs if f["side"] == "YES"]
            n = [float(f["price"]) for f in fs if f["side"] == "NO"]
            matched = float(row["matched_qty"])
            avg_y, avg_n = mean(y) if y else 0, mean(n) if n else 0
            matched_pnl = matched * (1 - avg_y - avg_n)
            residual = float(row["final_residual"])
            heavy = "YES" if len(y) > len(n) else "NO" if len(n) > len(y) else None
            avg_heavy = avg_y if heavy == "YES" else avg_n if heavy == "NO" else 0
            residual_pnl = residual * ((1 if heavy == winner[market] else 0) - avg_heavy)
            if abs(matched_pnl + residual_pnl - float(row["pnl"])) > 1e-6:
                raise RuntimeError(f"Economic split does not reconcile: {market}")
            return matched_pnl, residual_pnl
        am, ar = economic_split(af, a)
        bm, br = economic_split(bf, b)
        delta = float(b["pnl"]) - float(a["pnl"])
        comparison.append({"market": market, "winner": winner[market],
            "fills_A": int(a["conservative_fills"]), "fills_B": int(b["conservative_fills"]),
            "pnl_A": float(a["pnl"]), "pnl_B": float(b["pnl"]), "delta_pnl": round(delta, 6),
            "final_residual_A": float(a["final_residual"]), "final_residual_B": float(b["final_residual"]),
            "delta_final_residual": float(b["final_residual"]) - float(a["final_residual"]),
            "max_residual_A": float(a["max_residual"]), "max_residual_B": float(b["max_residual"]),
            "delta_max_residual": float(b["max_residual"]) - float(a["max_residual"]),
            "matched_qty_A": float(a["matched_qty"]), "matched_qty_B": float(b["matched_qty"]),
            "matched_pnl_A": round(am, 6), "matched_pnl_B": round(bm, 6),
            "residual_pnl_A": round(ar, 6), "residual_pnl_B": round(br, 6)})
        a_only_pnl = sum(float(a_fills[k]["expost_settlement_contribution"]) for k in a_fills.keys() - b_fills.keys() if k[0] == market)
        b_only_pnl = sum(float(b_fills[k]["expost_settlement_contribution"]) for k in b_fills.keys() - a_fills.keys() if k[0] == market)
        common_price_delta = sum(float(b_fills[k]["expost_settlement_contribution"]) - float(a_fills[k]["expost_settlement_contribution"])
                                 for k in a_fills.keys() & b_fills.keys() if k[0] == market)
        if abs(b_only_pnl - a_only_pnl + common_price_delta - delta) > 1e-5:
            raise RuntimeError(f"Fill contribution decomposition mismatch: {market}")
        decomposition.append({"market": market, "A_only_contribution": round(a_only_pnl, 6),
                              "B_only_contribution": round(b_only_pnl, 6),
                              "common_trade_price_difference": round(common_price_delta, 6),
                              "delta_pnl": round(delta, 6)})
    write_csv("market_comparison.csv", comparison)
    write_csv("fill_difference_decomposition.csv", decomposition)
    deltas = [r["delta_pnl"] for r in comparison]
    positive = sorted((v for v in deltas if v > 0), reverse=True)
    total = sum(deltas)
    summary = {"A_fill_classes": dict(counts["A"]), "B_fill_classes": dict(counts["B"]),
        "A_only_fills": len(a_only), "B_only_fills": len(b_only),
        "A_only_expost_contribution": round(sum(float(f["expost_settlement_contribution"]) for f in a_only), 6),
        "B_only_expost_contribution": round(sum(float(f["expost_settlement_contribution"]) for f in b_only), 6),
        "A_only_balancing": sum(f["fill_class"] == "BALANCING" for f in a_only),
        "A_only_imbalance_increasing": sum(f["fill_class"] == "IMBALANCE-INCREASING" for f in a_only),
        "A_only_winning_side": sum(f["side"] == winner[f["market"]] for f in a_only),
        "A_only_losing_side": sum(f["side"] != winner[f["market"]] for f in a_only),
        "market_count_B_better": sum(v > 0 for v in deltas),
        "market_count_B_worse": sum(v < 0 for v in deltas),
        "market_count_tied": sum(v == 0 for v in deltas),
        "market_count_B_better_both_return_and_final_residual": sum(r["delta_pnl"] > 0 and r["delta_final_residual"] < 0 for r in comparison),
        "market_count_B_worse_both_return_and_final_residual": sum(r["delta_pnl"] < 0 and r["delta_final_residual"] > 0 for r in comparison),
        "total_delta_pnl": round(total, 6), "median_market_delta_pnl": median(deltas),
        "best_market_delta": max(deltas), "worst_market_delta": min(deltas),
        "total_delta_excluding_best": round(total - max(deltas), 6),
        "total_delta_excluding_worst": round(total - min(deltas), 6),
        "top_one_positive_share_of_aggregate_delta": positive[0] / total if total > 0 and positive else None,
        "top_two_positive_share_of_aggregate_delta": sum(positive[:2]) / total if total > 0 and positive else None,
        "fill_delta_decomposition": {"minus_A_only": round(-sum(r["A_only_contribution"] for r in decomposition), 6),
            "plus_B_only": round(sum(r["B_only_contribution"] for r in decomposition), 6),
            "common_trade_price_difference": round(sum(r["common_trade_price_difference"] for r in decomposition), 6)}}
    (OUT / "attribution_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
