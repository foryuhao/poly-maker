"""Frozen BTC5m continuous inventory pool replay; read-only official captures."""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from statistics import mean, median

from r0_rolling_pair_replay import Book, write_csv

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "research_outputs/execution_replay_audit_20261003"
LABELS = ROOT / "research_outputs/r0_rolling_pair_replay_20261003/settlement_labels.csv"
OUT = ROOT / "research_outputs/cip_b0_20261003"
CAPITAL, CLIP, BID_SUM_LIMIT, TARGET = 25.0, 5.0, 0.98, 25.0
CUTOFF_S = 60


class PoolReplay:
    def __init__(self, slug, start, winner, activation_us, cancel_us, fills, decisions, paths):
        self.slug, self.start, self.end, self.winner = slug, start, start + 300_000_000, winner
        self.cutoff = self.end - CUTOFF_S * 1_000_000
        self.activation_us, self.cancel_us = activation_us, cancel_us
        self.books = {s: Book() for s in ("YES", "NO")}
        self.orders = {s: None for s in ("YES", "NO")}
        self.qty = {s: 0.0 for s in ("YES", "NO")}
        self.cost = {s: 0.0 for s in ("YES", "NO")}
        self.fills, self.decisions, self.paths = fills, decisions, paths
        self.order_count = 0
        self.blocked = 0
        self.peak_capital = 0.0
        self.integral_capital = self.integral_abs = self.integral_pair_cost = 0.0
        self.pair_cost_duration = 0
        self.min_pair_cost = self.max_pair_cost = None
        self.max_abs = 0.0
        self.abs_samples = []
        self.last_t = start
        self.event_counts = Counter()
        self.error = None

    def values(self):
        qy, qn = self.qty["YES"], self.qty["NO"]
        ay = self.cost["YES"] / qy if qy else None
        an = self.cost["NO"] / qn if qn else None
        matched = min(qy, qn)
        pair_cost = ay + an if ay is not None and an is not None else None
        return qy - qn, matched, ay, an, pair_cost

    def capital(self):
        return sum(self.cost.values()) + sum(o["remaining"] * o["price"] for o in self.orders.values() if o)

    def integrate(self, t):
        dt = t - self.last_t
        if dt < 0:
            self.error = "nonmonotone available time"
            return
        net, _, _, _, pair_cost = self.values()
        self.integral_abs += abs(net) * dt
        self.integral_capital += self.capital() * dt
        if pair_cost is not None:
            self.integral_pair_cost += pair_cost * dt
            self.pair_cost_duration += dt
        self.last_t = t

    def check(self):
        used = self.capital()
        self.peak_capital = max(self.peak_capital, used)
        self.max_abs = max(self.max_abs, abs(self.values()[0]))
        if used > CAPITAL + 1e-8:
            self.error = f"capital invariant exceeded: {used}"

    def cancel(self, side, t, actions):
        o = self.orders[side]
        if o and o["status"] != "cancel_pending":
            o["status"] = "cancel_pending"
            o["cancel_request_us"] = t
            o["cancel_effective_us"] = max(t + self.cancel_us, o["activation_us"])
            actions.append(f"CANCEL_{side}")
            self.event_counts["cancel_requests"] += 1

    def advance(self, t):
        changed = False
        for side in ("YES", "NO"):
            o = self.orders[side]
            if not o:
                continue
            if o["status"] == "submitted" and t >= o["activation_us"]:
                o["status"] = "active"
                self.event_counts["activated"] += 1
                changed = True
            if o["status"] == "cancel_pending" and t >= o["cancel_effective_us"]:
                self.orders[side] = None
                self.event_counts["canceled"] += 1
                changed = True
        self.check()
        return changed

    def snapshot(self, t, reason, action, event_reason):
        net, matched, ay, an, pc = self.values()
        row = {"market": self.slug, "timestamp_us": t,
               "yes_best_bid": self.books["YES"].bid / 10000 if self.books["YES"].bid is not None else None,
               "no_best_bid": self.books["NO"].bid / 10000 if self.books["NO"].bid is not None else None,
               "qy": self.qty["YES"], "qn": self.qty["NO"], "avg_y": ay, "avg_n": an,
               "matched_qty": matched, "net_inventory": net, "pool_pair_cost": pc,
               "pair_pnl": matched * (1 - pc) if pc is not None else 0.0,
               "capital_used": self.capital(), "inventory_condition": "BALANCED" if net == 0 else "YES_HEAVY" if net > 0 else "NO_HEAVY",
               "reason": reason, "event_reason": event_reason, "action": action}
        self.decisions.append(row)
        if action or event_reason == "FILL":
            self.paths.append(row.copy())
        self.abs_samples.append(abs(net))
        if pc is not None:
            self.min_pair_cost = pc if self.min_pair_cost is None else min(self.min_pair_cost, pc)
            self.max_pair_cost = pc if self.max_pair_cost is None else max(self.max_pair_cost, pc)

    def reconcile(self, t, reason):
        net, matched, _, _, _ = self.values()
        actions = []
        valid = all(b.valid() for b in self.books.values())
        desired = {s: None for s in ("YES", "NO")}
        state_reason = reason
        if t >= self.cutoff:
            state_reason = "CUTOFF"
        elif matched >= TARGET and net == 0:
            state_reason = "TARGET_STOP"
        elif not valid:
            state_reason = "INVALID_BOOK"
        else:
            bids = {s: self.books[s].bid / 10000 for s in ("YES", "NO")}
            if net == 0:
                if sum(bids.values()) <= BID_SUM_LIMIT + 1e-12:
                    # A balanced expansion needs capacity for both full clips.
                    if sum(self.cost.values()) + CLIP * sum(bids.values()) <= CAPITAL + 1e-8:
                        desired = bids
                    else:
                        self.blocked += 1
                        state_reason = "CAPITAL_BLOCKED"
                else:
                    state_reason = "BID_SUM"
            else:
                lighter = "NO" if net > 0 else "YES"
                # Existing same-side reserve can fund a KEEP; a replacement needs
                # room for the new clip once the old cancellation becomes effective.
                other = "YES" if lighter == "NO" else "NO"
                other_reserve = self.orders[other]["remaining"] * self.orders[other]["price"] if self.orders[other] else 0.0
                if sum(self.cost.values()) + other_reserve + CLIP * bids[lighter] <= CAPITAL + 1e-8:
                    desired[lighter] = bids[lighter]
                else:
                    self.blocked += 1
                    state_reason = "CAPITAL_BLOCKED"
        for side in ("YES", "NO"):
            o, price = self.orders[side], desired[side]
            if o and (price is None or abs(o["price"] - price) > 1e-9):
                self.cancel(side, t, actions)
            elif o and price is not None and o["status"] != "cancel_pending":
                actions.append(f"KEEP_{side}")
        for side in ("YES", "NO"):
            price = desired[side]
            if price is None or self.orders[side] is not None:
                continue
            # Never exceed the cap while a stale opposite-side cancellation is pending.
            if self.capital() + CLIP * price > CAPITAL + 1e-8:
                self.blocked += 1
                state_reason = "CAPITAL_BLOCKED"
                continue
            self.order_count += 1
            self.orders[side] = {"id": f"{self.slug}:B0:{self.order_count}", "side": side,
                                 "price": price, "remaining": CLIP, "status": "submitted",
                                 "activation_us": t + self.activation_us}
            actions.append(f"PLACE_{side}@{price:.4f}")
            self.event_counts["placed"] += 1
        self.check()
        self.snapshot(t, state_reason, "|".join(actions), reason)

    def trade(self, event, side):
        p = event.get("payload") or {}
        o = self.orders[side]
        if p.get("side") != "SELL" or not o or o["status"] not in ("active", "cancel_pending"):
            return False
        t, exch, recv = (event.get(k) for k in ("available_at_us", "exchange_at_us", "received_at_us"))
        if None in (t, exch, recv) or min(t, exch, recv) <= o["activation_us"]:
            return False
        if o.get("cancel_effective_us") is not None and t >= o["cancel_effective_us"]:
            return False
        trade_px = int(round(float(p["price"]) * 10000))
        if trade_px >= int(round(o["price"] * 10000)):
            return False
        # Frozen R0 maker semantics: one qualifying print fills all remaining
        # quantity, independently of public print size.
        self.apply_fill(o, o["remaining"], event, trade_px)
        self.check()
        return True

    def apply_fill(self, o, qty, event, trade_px):
        """Apply any positive executed quantity immediately to pool inventory."""
        if qty <= 0 or qty > o["remaining"] + 1e-12:
            raise ValueError("invalid fill quantity")
        side = o["side"]
        p = event.get("payload") or {}
        t, exch, recv = (event.get(k) for k in ("available_at_us", "exchange_at_us", "received_at_us"))
        o["remaining"] -= qty
        self.qty[side] += qty
        self.cost[side] += qty * o["price"]
        self.fills.append({"market": self.slug, "order_id": o["id"], "side": side,
                           "fill_qty": qty, "fill_price": o["price"],
                           "raw_trade_size": p.get("size"), "fill_available_us": t,
                           "fill_exchange_us": exch, "fill_received_us": recv,
                           "activation_us": o["activation_us"], "trade_price": trade_px / 10000,
                           "trade_event_seq": event.get("event_seq")})
        if o["remaining"] <= 1e-12:
            self.orders[side] = None
        self.event_counts["fills"] += 1

    def replay(self, path, token_map):
        with (path / "events.jsonl").open() as stream:
            for line in stream:
                event = json.loads(line)
                t = event.get("available_at_us")
                if t is None or t < self.start or t >= self.end:
                    continue
                if t < self.last_t:
                    self.event_counts["nonmonotone"] += 1
                    continue
                self.integrate(t)
                transitioned = self.advance(t)
                p = event.get("payload") or {}
                side = token_map.get(p.get("token_id"))
                decision = False
                reason = "LIFECYCLE"
                if side is not None:
                    kind = event.get("kind")
                    if kind in ("BOOK", "REST_BOOK", "DELTA"):
                        decision = self.books[side].update(kind, p)
                        reason = "BBO"
                    elif kind == "TICK":
                        tick = int(round(float(p["tick_size"]) * 10000))
                        if tick != self.books[side].tick_int:
                            self.books[side].tick_int = tick
                            decision, reason = True, "TICK"
                    elif kind == "TRADE":
                        decision = self.trade(event, side)
                        reason = "FILL"
                if transitioned or decision or (t >= self.cutoff and self.last_t == t and not self.event_counts["cutoff_seen"]):
                    if t >= self.cutoff:
                        self.event_counts["cutoff_seen"] = 1
                    self.reconcile(t, reason if decision else "LIFECYCLE")
                if self.error:
                    break
        self.integrate(self.end)
        self.advance(self.end)
        if self.event_counts["nonmonotone"]:
            self.error = self.error or "nonmonotone event availability timestamps"
        if any(self.orders.values()):
            self.error = self.error or "open order at market end"
        self.check()
        net, matched, ay, an, pc = self.values()
        residual_side = "YES" if net > 0 else "NO" if net < 0 else None
        residual_cost = abs(net) * ({"YES": ay, "NO": an}[residual_side] if residual_side else 0.0)
        residual_pnl = abs(net) * (1 if residual_side == self.winner else 0) - residual_cost
        pair_pnl = matched * (1 - pc) if pc is not None else 0.0
        return {"market": self.slug, "winner": self.winner,
                "raw_fill_records": self.event_counts["fills"],
                "yes_shares": self.qty["YES"], "no_shares": self.qty["NO"],
                "purchased_shares": sum(self.qty.values()), "matched_qty": matched,
                "pairing_efficiency": 2 * matched / sum(self.qty.values()) if sum(self.qty.values()) else None,
                "avg_y": ay, "avg_n": an, "final_pool_pair_cost": pc,
                "min_pool_pair_cost": self.min_pair_cost, "max_pool_pair_cost": self.max_pair_cost,
                "time_weighted_pool_pair_cost": self.integral_pair_cost / self.pair_cost_duration if self.pair_cost_duration else None,
                "pair_pnl": pair_pnl, "pair_pnl_per_matched_share": pair_pnl / matched if matched else None,
                "time_weighted_abs_net_inventory": self.integral_abs / (self.end - self.start),
                "mean_abs_net_inventory": mean(self.abs_samples) if self.abs_samples else 0.0,
                "max_abs_net_inventory": self.max_abs, "final_net_inventory": net,
                "final_residual_qty": abs(net), "residual_pnl": residual_pnl,
                "mean_capital_used": self.integral_capital / (self.end - self.start),
                "peak_capital_used": self.peak_capital, "capital_blocked_events": self.blocked,
                "total_pnl": pair_pnl + residual_pnl, "orders_placed": self.event_counts["placed"],
                "orders_canceled": self.event_counts["canceled"], "data_error": self.error or ""}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with (AUDIT / "dataset_audit.csv").open() as f:
        selected = [r for r in csv.DictReader(f) if r["execution_replay_usable"].lower() == "true"]
    if len(selected) != 32 or len({r["market"] for r in selected}) != 32:
        raise RuntimeError("Audited universe is not 32 unique markets")
    with LABELS.open() as f:
        labels = {r["market"]: r for r in csv.DictReader(f)}
    if set(labels) != {r["market"] for r in selected}:
        raise RuntimeError("R0 settlement-label universe mismatch")
    lat = json.loads((AUDIT / "summary.json").read_text())
    fills, decisions, paths, results = [], [], [], []
    for row in selected:
        path = ROOT / "research_data/m4a" / row["session"]
        manifest = json.loads((path / "manifest.json").read_text())
        label = labels[row["market"]]
        if (manifest["market_slug"] != row["market"] or
            str(manifest["condition_id"]).lower() != label["condition_id"].lower() or
            manifest["yes_token_id"] != label["yes_token_id"] or
            manifest["no_token_id"] != label["no_token_id"] or
            label["resolution_status"] != "resolved" or label["winner"] not in ("YES", "NO")):
            raise RuntimeError(f"Settlement/session identity mismatch: {row['market']}")
        start = int(row["market"].rsplit("-", 1)[1]) * 1_000_000
        sim = PoolReplay(row["market"], start, label["winner"],
                         int(lat["activation_latency_us"]), int(lat["cancel_latency_us"]),
                         fills, decisions, paths)
        result = sim.replay(path, {manifest["yes_token_id"]: "YES", manifest["no_token_id"]: "NO"})
        result["session"] = row["session"]
        results.append(result)
    if any(r["data_error"] for r in results):
        raise RuntimeError({r["market"]: r["data_error"] for r in results if r["data_error"]})
    write_csv(OUT / "market_results.csv", results)
    write_csv(OUT / "fills.csv", fills)
    write_csv(OUT / "decisions.csv", decisions)
    write_csv(OUT / "inventory_path.csv", paths)
    total_matched = sum(r["matched_qty"] for r in results)
    pair_pnl = sum(r["pair_pnl"] for r in results)
    residual_pnl = sum(r["residual_pnl"] for r in results)
    totals = [r["total_pnl"] for r in results]
    summary = {"classification": None, "sample": {"markets": len(results), "official_labels": len(labels),
               "data_errors": 0}, "parameters": {"capital": CAPITAL, "clip": CLIP, "bid_sum_limit": BID_SUM_LIMIT,
               "target_matched": TARGET, "cutoff_seconds": CUTOFF_S},
               "latencies_us": {"activation": lat["activation_latency_us"], "cancel": lat["cancel_latency_us"]},
               "fill_rule": "R0: same-token public SELL strictly below active resting BUY; all three timestamps after activation; fills all remaining order quantity regardless of print size",
               "metrics": {"fills": len(fills), "mean_fills": mean(r["raw_fill_records"] for r in results),
               "median_fills": median(r["raw_fill_records"] for r in results),
               "matched_shares": total_matched, "mean_matched": mean(r["matched_qty"] for r in results),
               "median_matched": median(r["matched_qty"] for r in results),
               "pairing_efficiency": (2 * total_matched / sum(r["purchased_shares"] for r in results)
                                      if sum(r["purchased_shares"] for r in results) else None),
               "pair_pnl": pair_pnl, "mean_pair_pnl": pair_pnl / len(results),
               "weighted_pool_pair_cost": 1 - pair_pnl / total_matched if total_matched else None,
               "pair_pnl_per_matched_share": pair_pnl / total_matched if total_matched else None,
               "time_weighted_residual": mean(r["time_weighted_abs_net_inventory"] for r in results),
               "max_residual": max(r["max_abs_net_inventory"] for r in results),
               "residual_pnl": residual_pnl, "total_pnl": sum(totals),
               "mean_peak_capital": mean(r["peak_capital_used"] for r in results),
               "median_peak_capital": median(r["peak_capital_used"] for r in results),
               "max_peak_capital": max(r["peak_capital_used"] for r in results),
               "capital_blocked_events": sum(r["capital_blocked_events"] for r in results),
               "profitable": sum(x > 1e-9 for x in totals), "losing": sum(x < -1e-9 for x in totals),
               "flat": sum(abs(x) <= 1e-9 for x in totals)}}
    m = summary["metrics"]
    if m["capital_blocked_events"] > 0 and m["mean_matched"] <= 6.25:
        cls = "CAPITAL LIMIT DOMINATES"
    elif pair_pnl > 0 and sum(totals) > 0 and m["mean_matched"] > 6.25 and sum(sorted(totals, reverse=True)[1:]) > 0:
        cls = "CONTINUOUS POOL EDGE OBSERVED"
    elif pair_pnl > 0 and sum(totals) <= 0 and residual_pnl < 0:
        cls = "PAIR EDGE EXISTS BUT RESIDUAL DOMINATES"
    elif m["mean_matched"] > 6.25 and pair_pnl <= 0:
        cls = "THROUGHPUT IMPROVES BUT PAIR ECONOMICS FAIL"
    elif m["capital_blocked_events"] > 0:
        cls = "CAPITAL LIMIT DOMINATES"
    else:
        cls = "NO IMPROVEMENT OVER CYCLE MODEL"
    summary["classification"] = cls
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({"classification": cls, "metrics": m}, indent=2))


if __name__ == "__main__":
    main()
