"""Research-only B versus side-specific book-health and BBO-pressure gate.

Run from repository root: python3 research_tools/dp_backtest_03_three_layer.py
Uses only frozen local M4A events and identity-validated Backtest 01 labels.
"""
from __future__ import annotations

import bisect
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median

from dp_backtest_01_inventory_skew import Book, Policy, QTY, RATE, latency_audit, persisted_winner
from dp_backtest_02_quote_filter import active_quote_time, write_csv

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "research_data/m4a"
SOURCE = ROOT / "research_outputs/dp_backtest_01"
OUT = ROOT / "research_outputs/dp_backtest_03"
HORIZONS_US = (1_000_000, 3_000_000, 5_000_000)
# Declared research bucket boundary, never fitted to markout or settlement.
TOXIC_BOUNDARY = -0.5


def bucket(v):
    if v < -0.5: return "strong_negative"
    if v < -0.2: return "moderate_negative"
    if v <= 0.2: return "neutral"
    if v <= 0.5: return "moderate_positive"
    return "strong_positive"


def depth_bucket(v):
    if v < 5: return "lt_5"
    if v < 10: return "5_to_lt_10"
    if v < 25: return "10_to_lt_25"
    return "ge_25"


def features(book, generation):
    """Only contemporaneous local L2 and the existing snapshot-generation semantics."""
    if not book.seen: return {"quality": "incomplete_book"}
    if getattr(book, "generation", None) != generation: return {"quality": "stale_snapshot_generation"}
    if book.bid is None or book.ask is None: return {"quality": "incomplete_book"}
    if book.bid >= book.ask: return {"quality": "crossed_or_invalid_book"}
    step = int(round(book.tick * 10000))
    if step <= 0 or book.bid % step or book.ask % step or not (0 < book.bid < book.ask < 10000):
        return {"quality": "invalid_tick_or_price"}
    qb = float(book.bids.get(book.bid, 0))
    qa = float(book.asks.get(book.ask, 0))
    if qb <= 0 or qa <= 0: return {"quality": "incomplete_book"}
    imbalance = (qb - qa) / (qb + qa)
    mid = (book.bid + book.ask) / 20000
    micro = (book.ask * qb + book.bid * qa) / (qb + qa) / 10000
    shift_ticks = (micro - mid) / book.tick
    # Local L2 is the BBO and next two displayed levels per side, including BBO.
    bid_depth = sum(v for p, v in book.bids.items() if book.bid - 2 * step <= p <= book.bid)
    ask_depth = sum(v for p, v in book.asks.items() if book.ask <= p <= book.ask + 2 * step)
    return {"quality": "pass", "bid": book.bid / 10000, "ask": book.ask / 10000,
            "tick": book.tick, "spread_ticks": (book.ask - book.bid) / step,
            "q_bid": qb, "q_ask": qa, "near_bid_depth": bid_depth,
            "near_ask_depth": ask_depth, "bid_depth_bucket": depth_bucket(bid_depth),
            "ask_depth_bucket": depth_bucket(ask_depth), "bbo_min_depth_bucket": depth_bucket(min(qb, qa)),
            "mid": mid, "microprice": micro, "microprice_shift_ticks": shift_ticks,
            "imbalance": imbalance, "imbalance_bucket": bucket(imbalance),
            "toxic": imbalance <= TOXIC_BOUNDARY}


class GatedPolicy(Policy):
    def __init__(self, slug, start, activation_us, cancel_us, orders, fills, decisions):
        super().__init__("D", slug, start, activation_us, cancel_us, orders, fills)
        self.decisions_rows = decisions
        self.generation = {"YES": None, "NO": None}
        self.trigger = ""
        self.rejections = Counter()
        self.last_eligible = False

    def decide(self, t, books):
        if self.last_decision_us is not None:
            elapsed = max(0, t - self.last_decision_us)
            if self.last_valid: self.valid_quote_time_us += elapsed
            if self.last_diff: self.diff_quote_time_us += elapsed
        self.last_decision_us = t
        net = self.inv["YES"] - self.inv["NO"]
        ticks = round(abs(net) * RATE)
        heavy = "YES" if net > 0 else "NO" if net < 0 else None
        self.last_valid = False
        self.last_diff = False
        for side, book in books.items():
            f = features(book, self.generation[side])
            quality = f["quality"]
            eligible = quality == "pass" and not f["toxic"]
            if quality == "pass":
                self.last_valid = True
                self.rejections["eligible_quality"] += 1
                if f["toxic"]: self.rejections["layer2"] += 1
                else: self.rejections["eligible_entry"] += 1
            else:
                self.rejections[quality] += 1
            desired = None
            if eligible:
                desired_int = book.bid - (int(round(ticks * book.tick * 10000)) if side == heavy else 0)
                step = int(round(book.tick * 10000))
                if 0 < desired_int < 10000 and desired_int < book.ask and step > 0 and desired_int % step == 0:
                    desired = desired_int
                else:
                    self.skip["invalid_tick_bound_or_marketable"] += 1
            self.decisions_rows.append({"market": self.slug, "strategy": "D", "available_at_us": t,
                "trigger": self.trigger, "side": side, "generation": self.generation[side],
                "quality": quality, "layer1_pass": quality == "pass", "layer2_reject": quality == "pass" and f["toxic"],
                "quote_off": desired is None, "yes_inventory": self.inv["YES"],
                "no_inventory": self.inv["NO"], "net_inventory": net, "skew_ticks": ticks,
                "desired_quote": desired / 10000 if desired is not None else "", **f})
            order = self.orders[side]
            if order is not None and order["status"] == "cancel_pending": continue
            if order is not None and order["price_int"] == desired: continue
            if order is not None:
                order["status"] = "cancel_pending"
                order["cancel_request_us"] = t
                order["cancel_effective_us"] = max(t + self.cancel_us, order["activation_us"])
                self.quote_changes += 1
            elif desired is not None:
                self.serial += 1
                o = {"market": self.slug, "strategy": self.name,
                     "order_id": f"{self.slug}:{self.name}:{self.serial}", "side": side,
                     "price": desired / 10000, "price_int": desired, "size": QTY,
                     "submitted_us": t, "activation_us": t + self.activation_us,
                     "status": "submitted", "cancel_request_us": None,
                     "cancel_effective_us": None, "closed_us": None,
                     "fill_us": None, "fill_exchange_us": None}
                self.orders[side] = o
                self.order_rows.append(o)
        if self.last_valid:
            self.decisions += 1
            self.skew[str(ticks)] += 1
            if ticks:
                self.differing += 1
                self.last_diff = True


def replay_market(path, activation_us, cancel_us, orders, fills, decisions, submit_features, timelines):
    m = json.loads((path / "manifest.json").read_text())
    slug = m["market_slug"]
    start = int(slug.rsplit("-", 1)[1]) * 1_000_000
    end = start + 300_000_000
    winner = persisted_winner(path)
    if winner not in ("YES", "NO"): raise RuntimeError(f"Missing official winner: {slug}")
    sides = {m["yes_token_id"]: "YES", m["no_token_id"]: "NO"}
    books = {"YES": Book(), "NO": Book()}
    b = Policy("B", slug, start, activation_us, cancel_us, orders, fills)
    d = GatedPolicy(slug, start, activation_us, cancel_us, orders, fills, decisions)
    generation = {"YES": None, "NO": None}
    last_t = 0
    last_seq = 0
    def decide(policy, t, trigger):
        n = len(orders)
        if policy is d:
            d.trigger = trigger
            d.decide(t, books)
        else:
            b.decide(t, books)
        for o in orders[n:]:
            side = o["side"]
            submit_features[o["order_id"]] = {**features(books[side], generation[side]),
                "yes_inventory_at_decision": policy.inv["YES"], "no_inventory_at_decision": policy.inv["NO"],
                "submitted_us": t}
    for line in (path / "events.jsonl").open():
        event = json.loads(line)
        t = event.get("available_at_us")
        if t is None or t < start or t >= end: continue
        if t < last_t or int(event["event_seq"]) <= last_seq:
            raise RuntimeError(f"Causal event ordering failure: {slug}")
        last_t, last_seq = t, int(event["event_seq"])
        for policy in (b, d):
            if policy.advance(t): decide(policy, t, "lifecycle")
        kind = event["kind"]
        payload = event.get("payload") or {}
        side = sides.get(payload.get("token_id"))
        if side is None: continue
        if kind in ("BOOK", "REST_BOOK", "DELTA"):
            if kind in ("BOOK", "REST_BOOK"):
                generation[side] = int(payload.get("snapshot_generation") or event.get("connection_generation") or 0)
                d.generation[side] = generation[side]
            elif event.get("connection_generation") and generation[side] != int(event["connection_generation"]):
                # Keep B's exact reconstruction; D withholds until a new snapshot.
                d.generation[side] = int(event["connection_generation"])
            changed = books[side].update(kind, payload)
            books[side].generation = generation[side]
            if changed:
                f = features(books[side], generation[side])
                timelines[(slug, side)].append((t, f.get("mid"), f["quality"]))
                for policy in (b, d): decide(policy, t, "book")
        elif kind == "TICK":
            tick = float(payload["tick_size"])
            if tick != books[side].tick:
                books[side].tick = tick
                for policy in (b, d): decide(policy, t, "tick")
        elif kind == "TRADE":
            for policy in (b, d):
                if policy.trade(event, side): decide(policy, t, "fill")
    result = [p.finish(end) for p in (b, d)]
    for row in result:
        row["settlement_winner"] = winner
        row["pnl"] = round(row["yes_shares" if winner == "YES" else "no_shares"] - row["gross_purchase_cost"], 6)
        row["active_quote_time_us"] = active_quote_time(orders, slug, row["strategy"], start, end)
    return result, dict(d.rejections)


def enrich_fills(fills, markets, submit_features, timelines):
    winner = {r["market"]: r["settlement_winner"] for r in markets if r["strategy"] == "B"}
    grouped = defaultdict(list)
    for f in fills: grouped[(f["market"], f["strategy"])].append(f)
    enriched, markouts = [], []
    for (market, strategy), fs in grouped.items():
        y = n = 0
        for f in sorted(fs, key=lambda x: (int(x["fill_available_us"]), int(x["trade_event_seq"]))):
            before = abs(y - n)
            iy, ino = y, n
            if f["side"] == "YES": y += QTY
            else: n += QTY
            after = abs(y - n)
            category = "BALANCING" if after < before else "IMBALANCE-INCREASING"
            attr = submit_features[f["order_id"]]
            contribution = QTY * ((1 if f["side"] == winner[market] else 0) - float(f["price"]))
            row = {**f, "inventory_before_fill_yes": iy, "inventory_before_fill_no": ino,
                   "fill_class": category, "winner": winner[market],
                   "expost_settlement_contribution": round(contribution, 6),
                   "decision_quality": attr["quality"],
                   "decision_gate_allows": attr["quality"] == "pass" and not attr.get("toxic", False),
                   "decision_yes_inventory": attr["yes_inventory_at_decision"],
                   "decision_no_inventory": attr["no_inventory_at_decision"],
                   "decision_imbalance": attr.get("imbalance", ""),
                   "decision_imbalance_bucket": attr.get("imbalance_bucket", ""),
                   "decision_microprice_shift_ticks": attr.get("microprice_shift_ticks", ""),
                   "decision_q_bid": attr.get("q_bid", ""), "decision_q_ask": attr.get("q_ask", ""),
                   "decision_near_bid_depth": attr.get("near_bid_depth", ""),
                   "decision_near_ask_depth": attr.get("near_ask_depth", "")}
            enriched.append(row)
            series = timelines[(market, f["side"])]
            ts = [v[0] for v in series]
            end = int(market.rsplit("-", 1)[1]) * 1_000_000 + 300_000_000
            for horizon in HORIZONS_US:
                target = int(f["fill_available_us"]) + horizon
                idx = bisect.bisect_right(ts, target) - 1
                observed = idx >= 0 and target < end and series[idx][2] == "pass"
                mid = series[idx][1] if observed else None
                markouts.append({"market": market, "strategy": strategy, "order_id": f["order_id"],
                    "side": f["side"], "trade_event_seq": f["trade_event_seq"],
                    "fill_available_us": f["fill_available_us"], "horizon_s": horizon // 1_000_000,
                    "target_us": target, "asof_book_us": series[idx][0] if observed else "",
                    "asof_age_us": target - series[idx][0] if observed else "",
                    "mid": mid if observed else "", "fill_price": f["price"],
                    "markout_per_share": round(mid - float(f["price"]), 6) if observed else "",
                    "markout_5share": round(QTY * (mid - float(f["price"])), 6) if observed else "",
                    "fill_class": category, "decision_gate_allows": row["decision_gate_allows"]})
    return enriched, markouts


def same_b(markets, orders, fills):
    for name, rows in (("orders.csv", orders), ("fills.csv", fills)):
        prior = [r for r in csv.DictReader((SOURCE / name).open()) if r["strategy"] == "B"]
        now = [{k: "" if row.get(k) is None else str(row.get(k)) for k in prior[0]}
               for row in rows if row["strategy"] == "B"]
        if prior != now:
            mismatch = next(((i, a, b) for i, (a, b) in enumerate(zip(prior, now)) if a != b), None)
            raise RuntimeError(f"B path differs from frozen Backtest 01: {name}; counts {len(prior)}/{len(now)}; first {mismatch}")
    prior = {r["market"]: r for r in csv.DictReader((SOURCE / "market_results.csv").open()) if r["strategy"] == "B"}
    for row in markets:
        if row["strategy"] == "B":
            for k, v in prior[row["market"]].items():
                if str(row[k]) != v: raise RuntimeError(f"B market differs: {row['market']} {k}")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    audit = list(csv.DictReader((SOURCE / "dataset_audit.csv").open()))
    fixed = [r for r in audit if r["execution_replay_usable"] == "True"]
    if len(fixed) != 20 or len({r["market"] for r in fixed}) != 20:
        raise RuntimeError("Frozen 20-market universe changed")
    paths = [DATA / r["session"] for r in fixed]
    submit, cancel = latency_audit(paths)
    activation_us, cancel_us = int(round(submit["p75_ms"] * 1000)), int(round(cancel["p75_ms"] * 1000))
    if (submit["n"], cancel["n"], activation_us, cancel_us) != (74, 176, 412289, 231100):
        raise RuntimeError("Frozen latency changed")
    orders, fills, decisions, markets = [], [], [], []
    submit_features, timelines = {}, defaultdict(list)
    quality_by_market = {}
    for row in fixed:
        rs, q = replay_market(DATA / row["session"], activation_us, cancel_us, orders, fills,
                              decisions, submit_features, timelines)
        markets.extend(rs)
        quality_by_market[row["market"]] = q
    same_b(markets, orders, fills)
    enriched, markouts = enrich_fills(fills, markets, submit_features, timelines)
    bkeys = {(f["market"], f["side"], int(f["trade_event_seq"])) for f in enriched if f["strategy"] == "B"}
    dkeys = {(f["market"], f["side"], int(f["trade_event_seq"])) for f in enriched if f["strategy"] == "D"}
    blocked = [{**f, "block_source": "layer1" if f["decision_quality"] != "pass" else "layer2" if not f["decision_gate_allows"] else "path_dependency"}
               for f in enriched if f["strategy"] == "B" and (f["market"], f["side"], int(f["trade_event_seq"])) not in dkeys]
    paired = []
    for market in sorted({r["market"] for r in markets}):
        b, d = (next(r for r in markets if r["market"] == market and r["strategy"] == x) for x in ("B", "D"))
        paired.append({"market": market, "winner": b["settlement_winner"],
            "pnl_B": b["pnl"], "pnl_D": d["pnl"], "delta_pnl": round(d["pnl"] - b["pnl"], 6),
            "fills_B": b["conservative_fills"], "fills_D": d["conservative_fills"],
            "final_residual_B": b["final_residual"], "final_residual_D": d["final_residual"],
            "time_avg_residual_B": b["time_avg_residual"], "time_avg_residual_D": d["time_avg_residual"],
            "active_quote_time_us_B": b["active_quote_time_us"],
            "active_quote_time_us_D": d["active_quote_time_us"]})
    def stats(name):
        rs = [r for r in markets if r["strategy"] == name]
        fs = [f for f in enriched if f["strategy"] == name]
        pnl = [r["pnl"] for r in rs]
        return {"total_pnl": round(sum(pnl), 6), "mean_pnl": mean(pnl), "median_pnl": median(pnl),
            "profitable_markets": sum(v > 0 for v in pnl), "losing_markets": sum(v < 0 for v in pnl),
            "worst_pnl": min(pnl), "best_pnl": max(pnl),
            "mean_time_weighted_residual": mean(r["time_avg_residual"] for r in rs),
            "mean_final_residual": mean(r["final_residual"] for r in rs),
            "maximum_residual": max(r["max_residual"] for r in rs),
            "matched_quantity": sum(r["matched_qty"] for r in rs),
            "imbalance_increasing_fills": sum(f["fill_class"] == "IMBALANCE-INCREASING" for f in fs),
            "balancing_fills": sum(f["fill_class"] == "BALANCING" for f in fs),
            "active_quote_time_us": sum(r["active_quote_time_us"] for r in rs),
            "orders": sum(r["submitted_orders"] for r in rs), "fills": len(fs),
            "zero_fill_markets": sum(r["conservative_fills"] == 0 for r in rs)}
    st = {name: stats(name) for name in ("B", "D")}
    q = Counter(r["quality"] for r in decisions)
    n = len(decisions)
    layer2 = sum(r["layer2_reject"] for r in decisions)
    valid = sum(r["layer1_pass"] for r in decisions)
    bucket_rows = {}
    for label in ("strong_negative", "moderate_negative", "neutral", "moderate_positive", "strong_positive"):
        ds = [r for r in decisions if r.get("imbalance_bucket") == label]
        fs = [f for f in enriched if f["strategy"] == "B" and f["decision_imbalance_bucket"] == label]
        mo = [r["markout_per_share"] for r in markouts if r["strategy"] == "B" and r["horizon_s"] == 3
              and r["markout_per_share"] != "" and next((f["decision_imbalance_bucket"] for f in fs
              if f["order_id"] == r["order_id"]), None) == label]
        bucket_rows[label] = {"decision_side_events": len(ds), "B_fills": len(fs),
            "mean_microprice_shift_ticks": mean(r["microprice_shift_ticks"] for r in ds) if ds else None,
            "mean_B_fill_3s_markout": mean(mo) if mo else None}
    ds = [r["delta_pnl"] for r in paired]
    positives = sorted((v for v in ds if v > 0), reverse=True)
    markout_summary = {}
    blocked_ids = {f["order_id"] for f in blocked}
    b_fill_by_order = {f["order_id"]: f for f in enriched if f["strategy"] == "B"}
    for horizon in (1, 3, 5):
        group = {}
        for label, predicate in (("B_blocked", lambda r: r["strategy"] == "B" and r["order_id"] in blocked_ids),
                                 ("B_retained", lambda r: r["strategy"] == "B" and r["order_id"] not in blocked_ids),
                                 ("D", lambda r: r["strategy"] == "D")):
            rows = [r for r in markouts if r["horizon_s"] == horizon and predicate(r)]
            values = [r["markout_per_share"] for r in rows if r["markout_per_share"] != ""]
            group[label] = {"observed": len(values), "possible": len(rows),
                            "mean_per_share": mean(values) if values else None,
                            "median_per_share": median(values) if values else None,
                            "asof_age_gt_1s": sum(r["asof_age_us"] > 1_000_000 for r in rows if r["asof_age_us"] != "")}
        markout_summary[str(horizon)] = group
    decision_markouts = {}
    for horizon in (1, 3, 5):
        decision_markouts[str(horizon)] = {}
        for label, allowed in (("decision_blocked", False), ("decision_allowed", True)):
            rows = [r for r in markouts if r["strategy"] == "B" and r["horizon_s"] == horizon
                    and b_fill_by_order[r["order_id"]]["decision_gate_allows"] == allowed]
            values = [r["markout_per_share"] for r in rows if r["markout_per_share"] != ""]
            fresh = [r["markout_per_share"] for r in rows if r["markout_per_share"] != ""
                     and r["asof_age_us"] <= 1_000_000]
            decision_markouts[str(horizon)][label] = {
                "observed": len(values), "possible": len(rows),
                "mean_per_share": mean(values) if values else None,
                "fresh_asof_at_most_1s_n": len(fresh),
                "fresh_asof_at_most_1s_mean": mean(fresh) if fresh else None}
    depth_buckets = dict(Counter(r["bbo_min_depth_bucket"] for r in decisions if r["quality"] == "pass"))
    summary = {"markets": 20, "classification": "RISK IMPROVEMENT ONLY",
        "rule": {"toxic_buy": "imbalance <= -0.5 (same sign as negative microprice shift)",
            "imbalance": "(Q_bid-Q_ask)/(Q_bid+Q_ask)",
            "microprice": "(Ask*Q_bid+Bid*Q_ask)/(Q_bid+Q_ask)",
            "microprice_shift_ticks": "(Microprice-(Bid+Ask)/2)/tick",
            "depth": "BBO and BBO-plus-two-ticks depth recorded in fixed 5, 10, 25 share buckets; no economic depth reject",
            "staleness": "same-generation post-snapshot book required; no age cutoff on a quiet live book"},
        "latency": {"activation_us": activation_us, "cancel_us": cancel_us},
        "strategies": st, "layer": {"decision_side_events": n, "eligible_quality": valid,
            "layer1_rejects": n-valid, "layer1_reject_rate": (n-valid)/n,
            "quality_reasons": dict(q), "layer2_rejects": layer2,
            "layer2_reject_rate_of_quality_pass": layer2/valid if valid else None,
            "side_quote_off_events": sum(r["quote_off"] for r in decisions),
            "eligible_entry_events": sum(r["layer1_pass"] and not r["layer2_reject"] for r in decisions),
            "active_quote_time_ratio": st["D"]["active_quote_time_us"]/st["B"]["active_quote_time_us"],
            "fill_ratio": st["D"]["fills"]/st["B"]["fills"]},
        "bucket_analysis": bucket_rows,
        "depth_buckets_quality_pass": depth_buckets,
        "fills": {"shared": len(bkeys & dkeys), "B_only": len(bkeys-dkeys), "D_only": len(dkeys-bkeys),
            "B_decision_blocked": sum(not f["decision_gate_allows"] for f in enriched if f["strategy"] == "B"),
            "B_decision_blocked_class": dict(Counter(f["fill_class"] for f in enriched
                if f["strategy"] == "B" and not f["decision_gate_allows"])),
            "B_decision_blocked_settlement_contribution": round(sum(f["expost_settlement_contribution"] for f in enriched
                if f["strategy"] == "B" and not f["decision_gate_allows"]), 6),
            "blocked_by_source": dict(Counter(f["block_source"] for f in blocked)),
            "blocked_balancing": sum(f["fill_class"] == "BALANCING" for f in blocked),
            "blocked_imbalance_increasing": sum(f["fill_class"] == "IMBALANCE-INCREASING" for f in blocked),
            "blocked_settlement_contribution": round(sum(f["expost_settlement_contribution"] for f in blocked), 6)},
        "markouts": markout_summary, "decision_markouts": decision_markouts,
        "paired": {"total_delta_pnl": round(sum(ds), 6), "D_better": sum(v > 0 for v in ds),
            "D_worse": sum(v < 0 for v in ds), "ties": sum(v == 0 for v in ds),
            "excluding_best_delta": round(sum(ds)-max(ds), 6),
            "excluding_worst_delta": round(sum(ds)-min(ds), 6),
            "top_two_positive_contribution": round(sum(positives[:2]), 6)}}
    for name, rows in (("market_results.csv", markets), ("decision_events.csv", decisions),
                       ("orders.csv", orders), ("fills.csv", enriched),
                       ("blocked_fills.csv", blocked), ("markouts.csv", markouts),
                       ("paired_markets.csv", paired)):
        write_csv(OUT / name, rows)
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: summary[k] for k in ("strategies", "layer", "fills", "markouts", "paired")}, indent=2))


if __name__ == "__main__": main()
