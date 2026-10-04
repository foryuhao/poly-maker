"""Read-only, fixed-parameter asymmetric-skew ablation on persisted M4A events.

Run from repository root: python3 research_tools/dp_backtest_01_inventory_skew.py
No network, credentials, or production imports are used.
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "research_data/m4a"
OUT = ROOT / "research_outputs/dp_backtest_01"
QTY = 5
RATE = min(0.08, 2.8 / QTY)


def percentile(values, q):
    if not values:
        return None
    v = sorted(values)
    z = (len(v) - 1) * q
    i = int(z)
    return v[i] + (v[min(i + 1, len(v) - 1)] - v[i]) * (z - i)


def write_csv(path, rows, fields):
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def persisted_winner(path):
    """Use explicit official settlement labels; never infer from terminal prices."""
    manifest = json.loads((path / "manifest.json").read_text())
    winners = set()
    with (path / "episodes.jsonl").open() as f:
        for line in f:
            row = json.loads(line)
            yes, no = row.get("payout_yes"), row.get("payout_no")
            if yes == 1 and no == 0:
                winners.add("YES")
            elif yes == 0 and no == 1:
                winners.add("NO")
    episode_winner = next(iter(winners)) if len(winners) == 1 else None
    labels_path = OUT / "settlement_labels.csv"
    if not labels_path.exists():
        return episode_winner
    rows = [r for r in csv.DictReader(labels_path.open()) if r["market"] == manifest["market_slug"]]
    if not rows:
        return episode_winner
    if len(rows) != 1:
        raise RuntimeError(f"Expected one official settlement label for {manifest['market_slug']}")
    row = rows[0]
    if (row["replay_session_id"] != manifest["session_id"]
            or row["condition_id"].lower() != manifest["condition_id"].lower()
            or row["yes_token_id"] != manifest["yes_token_id"]
            or row["no_token_id"] != manifest["no_token_id"]):
        raise RuntimeError(f"Settlement label identity mismatch: {manifest['market_slug']}")
    label_winner = None
    if row["resolved"] == "True" and row["identity_validation"] == "MATCH":
        if row["winning_outcome"] not in ("YES", "NO"):
            raise RuntimeError(f"Invalid winning outcome: {manifest['market_slug']}")
        expected_token = manifest["yes_token_id"] if row["winning_outcome"] == "YES" else manifest["no_token_id"]
        if row["winning_token_id"] != expected_token:
            raise RuntimeError(f"Winning token mismatch: {manifest['market_slug']}")
        label_winner = row["winning_outcome"]
    if episode_winner and label_winner and episode_winner != label_winner:
        raise RuntimeError(f"Persisted official winner conflict: {manifest['market_slug']}")
    return label_winner or episode_winner


def latency_audit(paths):
    submit = []
    cancel = []
    for path in paths:
        submitted = {}
        canceled = {}
        for line in (path / "events.jsonl").open():
            event = json.loads(line)
            kind = event["kind"]
            if kind not in ("ORDER_SUBMIT", "ORDER_ACK", "CANCEL_REQUEST", "CANCEL_ACK"):
                continue
            payload = event.get("payload") or {}
            rid = payload.get("request_id")
            t = event.get("available_at_us")
            if kind == "ORDER_SUBMIT" and rid and t is not None:
                submitted[rid] = t
            elif kind == "ORDER_ACK" and rid in submitted and payload.get("outcome") == "ACK":
                ms = (t - submitted.pop(rid)) / 1000
                if ms >= 0:
                    submit.append(ms)
            elif kind == "CANCEL_REQUEST" and rid and t is not None:
                canceled[rid] = t
            elif kind == "CANCEL_ACK" and rid in canceled and payload.get("outcome") == "ACK":
                ms = (t - canceled.pop(rid)) / 1000
                if ms >= 0:
                    cancel.append(ms)
    def stats(v):
        return {"n": len(v), "median_ms": percentile(v, .5),
                "p75_ms": percentile(v, .75), "p90_ms": percentile(v, .9)}
    return stats(submit), stats(cancel)


class Book:
    def __init__(self):
        self.bids = {}
        self.asks = {}
        self.bid = None
        self.ask = None
        self.tick = .01
        self.seen = False

    def update(self, kind, payload):
        before = (self.bid, self.ask, self.tick)
        if kind in ("BOOK", "REST_BOOK"):
            self.bids = {int(round(p * 10000)): s for p, s in payload.get("bids", []) if s > 0}
            self.asks = {int(round(p * 10000)): s for p, s in payload.get("asks", []) if s > 0}
            self.bid = max(self.bids, default=None)
            self.ask = min(self.asks, default=None)
            self.tick = float(payload.get("tick_size") or self.tick)
            self.seen = True
        else:
            side = payload.get("side")
            if side not in ("BUY", "SELL"):
                return False
            levels = self.bids if side == "BUY" else self.asks
            px = int(round(float(payload["price"]) * 10000))
            sz = float(payload["size"])
            if sz > 0:
                levels[px] = sz
            else:
                levels.pop(px, None)
            if side == "BUY":
                if self.bid is None or px >= self.bid or (self.bid not in levels):
                    self.bid = max(levels, default=None)
            else:
                if self.ask is None or px <= self.ask or (self.ask not in levels):
                    self.ask = min(levels, default=None)
        return before != (self.bid, self.ask, self.tick)


class Policy:
    def __init__(self, name, slug, start_us, activation_us, cancel_us, order_rows, fill_rows):
        self.name = name
        self.slug = slug
        self.activation_us = activation_us
        self.cancel_us = cancel_us
        self.order_rows = order_rows
        self.fill_rows = fill_rows
        self.inv = {"YES": 0, "NO": 0}
        self.orders = {"YES": None, "NO": None}
        self.serial = 0
        self.spent = 0.0
        self.max_residual = 0
        self.start_us = start_us
        self.last_inventory_us = start_us
        self.residual_share_us = 0
        self.quote_changes = 0
        self.skip = Counter()
        self.skew = Counter()
        self.differing = 0
        self.decisions = 0
        self.last_decision_us = None
        self.last_valid = False
        self.last_diff = False
        self.valid_quote_time_us = 0
        self.diff_quote_time_us = 0

    def advance(self, t):
        changed = False
        for side, order in self.orders.items():
            if order is None:
                continue
            if order["status"] == "submitted" and t >= order["activation_us"]:
                order["status"] = "active"
                changed = True
            elif order["status"] == "cancel_pending" and t >= order["cancel_effective_us"]:
                order["status"] = "canceled"
                order["closed_us"] = order["cancel_effective_us"]
                self.orders[side] = None
                changed = True
        return changed

    def decide(self, t, books):
        if self.last_decision_us is not None:
            elapsed = max(0, t - self.last_decision_us)
            if self.last_valid:
                self.valid_quote_time_us += elapsed
            if self.last_diff:
                self.diff_quote_time_us += elapsed
        self.last_decision_us = t
        if any(not b.seen or b.bid is None or b.ask is None or b.bid >= b.ask for b in books.values()):
            self.last_valid = False
            self.last_diff = False
            self.skip["invalid_or_incomplete_book"] += 1
            return
        self.last_valid = True
        self.decisions += 1
        net = self.inv["YES"] - self.inv["NO"]
        ticks = round(abs(net) * RATE) if self.name == "B" else 0
        if self.name == "B":
            self.skew[str(ticks)] += 1
            if ticks:
                self.differing += 1
        self.last_diff = self.name == "B" and ticks > 0
        heavy = "YES" if net > 0 else "NO" if net < 0 else None
        for side, book in books.items():
            desired = book.bid - (int(round(ticks * book.tick * 10000)) if side == heavy else 0)
            step = int(round(book.tick * 10000))
            if not (0 < desired < 10000 and desired < book.ask and step > 0 and desired % step == 0):
                self.skip["invalid_tick_bound_or_marketable"] += 1
                desired = None
            order = self.orders[side]
            if order is not None and order["status"] == "cancel_pending":
                continue
            if order is not None and order["price_int"] == desired:
                continue
            if order is not None:
                # A submitted order can still ACK; cancel takes effect no earlier
                # than both its activation and the observed cancel latency.
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

    def trade(self, event, side):
        p = event["payload"]
        if p.get("side") != "SELL":
            return False
        order = self.orders[side]
        if order is None or order["status"] not in ("active", "cancel_pending"):
            return False
        t = event.get("available_at_us")
        exch = event.get("exchange_at_us")
        recv = event.get("received_at_us")
        if None in (t, exch, recv) or min(t, exch, recv) <= order["activation_us"]:
            return False
        if order["cancel_effective_us"] is not None and t >= order["cancel_effective_us"]:
            return False
        price = int(round(float(p["price"]) * 10000))
        if price >= order["price_int"] or float(p.get("size") or 0) <= 0:
            return False
        order["status"] = "filled"
        order["closed_us"] = t
        order["fill_us"] = t
        order["fill_exchange_us"] = exch
        self.orders[side] = None
        self.residual_share_us += abs(self.inv["YES"] - self.inv["NO"]) * max(0, t - self.last_inventory_us)
        self.last_inventory_us = t
        self.inv[side] += QTY
        self.spent += QTY * order["price"]
        self.max_residual = max(self.max_residual, abs(self.inv["YES"] - self.inv["NO"]))
        self.fill_rows.append({"market": self.slug, "strategy": self.name,
            "order_id": order["order_id"], "side": side, "price": order["price"],
            "size": QTY, "activation_us": order["activation_us"],
            "fill_available_us": t, "fill_exchange_us": exch,
            "trade_price": price / 10000, "trade_size": p["size"],
            "trade_event_seq": event.get("event_seq"),
            "evidence": "same-token SELL strictly below active buy bid"})
        return True

    def finish(self, end_us):
        self.residual_share_us += abs(self.inv["YES"] - self.inv["NO"]) * max(0, end_us - self.last_inventory_us)
        if self.last_decision_us is not None:
            elapsed = max(0, end_us - self.last_decision_us)
            if self.last_valid:
                self.valid_quote_time_us += elapsed
            if self.last_diff:
                self.diff_quote_time_us += elapsed
        for side, order in self.orders.items():
            if order:
                order["status"] = "expired" if order["status"] != "cancel_pending" else "cancel_pending_at_expiry"
                order["closed_us"] = end_us
        y, n = self.inv["YES"], self.inv["NO"]
        return {"market": self.slug, "strategy": self.name, "yes_shares": y,
                "no_shares": n, "matched_qty": min(y, n), "final_residual": abs(y - n),
                "max_residual": self.max_residual,
                "time_avg_residual": self.residual_share_us / max(1, end_us - self.start_us),
                "heavy_side": "YES" if y > n else "NO" if n > y else "BALANCED",
                "gross_purchase_cost": round(self.spent, 6), "settlement_winner": "",
                "pnl": "", "submitted_orders": self.serial,
                "activated_orders": sum(o["strategy"] == self.name and o["market"] == self.slug and o["activation_us"] < end_us for o in self.order_rows),
                "conservative_fills": sum(f["strategy"] == self.name and f["market"] == self.slug for f in self.fill_rows),
                "canceled_orders": sum(o["strategy"] == self.name and o["market"] == self.slug and o["status"] == "canceled" for o in self.order_rows),
                "quote_changes": self.quote_changes,
                "decision_events": self.decisions, "skew_0": self.skew["0"],
                "skew_1": self.skew["1"],
                "skew_ge2": sum(v for k, v in self.skew.items() if int(k) >= 2),
                "max_skew": max((int(k) for k in self.skew), default=0),
                "differing_decisions": self.differing,
                "valid_quote_time_us": self.valid_quote_time_us,
                "different_quote_time_us": self.diff_quote_time_us,
                "invalid_quote_skips": self.skip["invalid_tick_bound_or_marketable"]}


def replay(path, activation_us, cancel_us, order_rows, fill_rows):
    manifest = json.loads((path / "manifest.json").read_text())
    slug = manifest["market_slug"]
    start = int(slug.rsplit("-", 1)[1]) * 1_000_000
    end = start + 300_000_000
    token_map = {manifest["yes_token_id"]: "YES", manifest["no_token_id"]: "NO"}
    winner = persisted_winner(path)
    books = {"YES": Book(), "NO": Book()}
    policies = [Policy(x, slug, start, activation_us, cancel_us, order_rows, fill_rows) for x in ("A", "B")]
    last_t = 0
    audit = Counter()
    for line in (path / "events.jsonl").open():
        event = json.loads(line)
        t = event.get("available_at_us")
        if t is None or t < start or t >= end:
            continue
        if t < last_t:
            audit["nonmonotone_available_time"] += 1
            continue
        last_t = t
        for policy in policies:
            if policy.advance(t):
                audit["lifecycle_decision"] += 1
                policy.decide(t, books)
        kind = event["kind"]
        payload = event.get("payload") or {}
        side = token_map.get(payload.get("token_id"))
        if side is None:
            continue
        if kind in ("BOOK", "REST_BOOK", "DELTA"):
            audit[f"{side}_{kind}"] += 1
            if books[side].update(kind, payload):
                audit["bbo_decision"] += 1
                for policy in policies:
                    policy.decide(t, books)
        elif kind == "TICK":
            tick = float(payload["tick_size"])
            if tick != books[side].tick:
                books[side].tick = tick
                audit[f"{side}_TICK"] += 1
                for policy in policies:
                    policy.decide(t, books)
        elif kind == "TRADE":
            audit["public_trades"] += 1
            for policy in policies:
                if policy.trade(event, side):
                    audit["fill_decision"] += 1
                    policy.decide(t, books)
    rows = [p.finish(end) for p in policies]
    if winner is not None:
        for row in rows:
            row["settlement_winner"] = winner
            row["pnl"] = round(row["yes_shares" if winner == "YES" else "no_shares"] - row["gross_purchase_cost"], 6)
    return rows, dict(audit)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    manifests = [(p.parent, json.loads(p.read_text())) for p in sorted(DATA.glob("*/manifest.json"))]
    chosen = {}
    audit_rows = []
    for path, m in manifests:
        reason = ("capture_gap_or_loss" if m.get("gap_count") or m.get("lost_event_count") else
                  "too_short_for_market_replay" if m.get("persisted_count", 0) < 50000 else
                  "incomplete_manifest" if m.get("close_status") != "COMPLETE" else "")
        slug = m["market_slug"]
        if not reason and slug in chosen:
            reason = "duplicate_session"
        if not reason:
            chosen[slug] = path
        audit_rows.append({"session": path.name, "market": slug,
                           "records": m.get("persisted_count"), "execution_replay_usable": not reason,
                           "exclusion_reason": reason,
                           "resolution_persisted": persisted_winner(path) is not None})
    paths = list(chosen.values())
    submit_latency, cancel_latency = latency_audit(paths)
    if not submit_latency["n"] or not cancel_latency["n"]:
        raise RuntimeError("Measured submit or cancel latency unavailable; no invented latency permitted")
    activation_us = int(round(submit_latency["p75_ms"] * 1000))
    cancel_us = int(round(cancel_latency["p75_ms"] * 1000))
    orders, fills, markets = [], [], []
    event_audit = {}
    for slug, path in sorted(chosen.items()):
        rows, events = replay(path, activation_us, cancel_us, orders, fills)
        markets.extend(rows)
        event_audit[slug] = events
    row_fields = list(markets[0]) if markets else []
    order_fields = list(orders[0]) if orders else []
    fill_fields = list(fills[0]) if fills else []
    for row in audit_rows:
        events = event_audit.get(row["market"], {}) if row["execution_replay_usable"] else {}
        row["yes_book_records"] = events.get("YES_BOOK", 0) + events.get("YES_REST_BOOK", 0)
        row["no_book_records"] = events.get("NO_BOOK", 0) + events.get("NO_REST_BOOK", 0)
        row["yes_delta_records"] = events.get("YES_DELTA", 0)
        row["no_delta_records"] = events.get("NO_DELTA", 0)
        row["public_trade_records"] = events.get("public_trades", 0)
        row["bbo_decisions"] = events.get("bbo_decision", 0)
        if row["execution_replay_usable"] and (not row["yes_book_records"] or not row["no_book_records"] or not row["bbo_decisions"]):
            raise RuntimeError(f"Selected market lacks reconstructible YES/NO books: {row['market']}")
    write_csv(OUT / "market_results.csv", markets, row_fields)
    write_csv(OUT / "orders.csv", orders, order_fields)
    write_csv(OUT / "fills.csv", fills, fill_fields)
    write_csv(OUT / "dataset_audit.csv", audit_rows, list(audit_rows[0]))
    def aggregate(policy):
        rows = [r for r in markets if r["strategy"] == policy]
        out = {"markets": len(rows)}
        for key in ("matched_qty", "final_residual", "max_residual", "time_avg_residual", "gross_purchase_cost", "submitted_orders",
                    "activated_orders", "conservative_fills", "canceled_orders", "quote_changes"):
            v = [r[key] for r in rows]
            out[key + "_total"] = sum(v)
            out[key + "_mean"] = mean(v) if v else None
            out[key + "_median"] = median(v) if v else None
        out["max_gross_purchase_cost"] = max((r["gross_purchase_cost"] for r in rows), default=0)
        out["max_residual_any_market"] = max((r["max_residual"] for r in rows), default=0)
        out["fill_rate_activated"] = out["conservative_fills_total"] / out["activated_orders_total"] if out["activated_orders_total"] else None
        out["yes_heavy_markets"] = sum(r["heavy_side"] == "YES" for r in rows)
        out["no_heavy_markets"] = sum(r["heavy_side"] == "NO" for r in rows)
        pnl = [r["pnl"] for r in rows if r["pnl"] != ""]
        complete = len(pnl) == len(rows) and bool(rows)
        out["total_pnl"] = sum(pnl) if complete else None
        out["mean_pnl_per_market"] = mean(pnl) if complete else None
        out["median_pnl_per_market"] = median(pnl) if complete else None
        out["profitable_market_pct"] = 100 * sum(v > 0 for v in pnl) / len(pnl) if complete else None
        out["losing_market_pct"] = 100 * sum(v < 0 for v in pnl) / len(pnl) if complete else None
        out["worst_single_market_loss"] = min(pnl) if complete else None
        out["best_single_market_gain"] = max(pnl) if complete else None
        return out
    paired = []
    for slug in sorted(chosen):
        a, b = (next(r for r in markets if r["market"] == slug and r["strategy"] == x) for x in ("A", "B"))
        paired.append({"market": slug, "delta_pnl": round(b["pnl"] - a["pnl"], 6) if a["pnl"] != "" and b["pnl"] != "" else "",
                       "delta_final_residual": b["final_residual"] - a["final_residual"],
                       "delta_max_residual": b["max_residual"] - a["max_residual"]})
    write_csv(OUT / "paired_deltas.csv", paired, list(paired[0]) if paired else [])
    b_rows = [r for r in markets if r["strategy"] == "B"]
    decisions = sum(r["decision_events"] for r in b_rows)
    resolved = sum(r["settlement_winner"] != "" for r in markets if r["strategy"] == "A")
    pnl_deltas = [r["delta_pnl"] for r in paired if r["delta_pnl"] != ""]
    all_pnl = len(pnl_deltas) == len(paired) and bool(paired)
    summary = {"classification": "REQUIRES_RESULT_INTERPRETATION" if all_pnl else "INSUFFICIENT CONSERVATIVE FILLS", "markets_found": len(set(m["market_slug"] for _, m in manifests)),
               "sessions_found": len(manifests), "execution_replay_markets": len(chosen),
               "settlement_pnl_usable_markets": resolved,
               "winner_audit": f"{resolved}/{len(chosen)} markets have explicit episode payout_yes/payout_no labels. Terminal book price is never treated as resolution.",
               "submit_to_ack": submit_latency, "cancel_request_to_ack": cancel_latency,
               "activation_latency_us": activation_us, "cancel_latency_us": cancel_us,
               "strategies": {x: aggregate(x) for x in ("A", "B")},
               "skew_activity": {"decision_events": decisions,
                   "zero": sum(r["skew_0"] for r in b_rows), "one": sum(r["skew_1"] for r in b_rows),
                   "ge_two": sum(r["skew_ge2"] for r in b_rows),
                   "max_ticks": max((r["max_skew"] for r in b_rows), default=0),
                   "different_quote_decisions": sum(r["differing_decisions"] for r in b_rows),
                   "valid_quote_time_us": sum(r["valid_quote_time_us"] for r in b_rows),
                   "different_quote_time_us": sum(r["different_quote_time_us"] for r in b_rows)},
               "paired_inventory": {"reduced_final_residual": sum(r["delta_final_residual"] < 0 for r in paired),
                   "reduced_max_residual": sum(r["delta_max_residual"] < 0 for r in paired)},
               "paired_return": {"markets_b_better_pnl": sum(v > 0 for v in pnl_deltas) if all_pnl else None,
                   "markets_b_better_both_pnl_and_final_residual": sum(r["delta_pnl"] > 0 and r["delta_final_residual"] < 0 for r in paired) if all_pnl else None,
                   "markets_b_worse_both_pnl_and_final_residual": sum(r["delta_pnl"] < 0 and r["delta_final_residual"] > 0 for r in paired) if all_pnl else None,
                   "total_delta_pnl": sum(pnl_deltas) if all_pnl else None,
                   "best_delta_market_contribution": max(pnl_deltas) if all_pnl else None,
                   "worst_delta_market_contribution": min(pnl_deltas) if all_pnl else None,
                   "delta_excluding_best": sum(pnl_deltas) - max(pnl_deltas) if all_pnl else None,
                   "delta_excluding_worst": sum(pnl_deltas) - min(pnl_deltas) if all_pnl else None},
               "event_audit": event_audit,
               "assumptions": {"quantity": QTY, "skew_rate": RATE, "rounding": "Python round, nearest even; 5-share increments avoid exact half tick",
                   "fill": "same-token public SELL strictly below resting bid, exchange/receipt/available timestamps all after activation; no same-price or L2 fills",
                   "decision": "both complete books; every BBO/tick change, lifecycle transition, and simulated fill",
                   "capital": "no starting cash persisted, no fabricated cap; gross cost reported; no capital validity claim",
                   "settlement": "not computed; persisted official winner absent"}}
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: summary[k] for k in ("classification", "markets_found", "execution_replay_markets", "settlement_pnl_usable_markets", "strategies", "skew_activity")}, indent=2))


if __name__ == "__main__":
    main()
