"""B1 persistent maker quotes with a continuous YES/NO inventory pool.

Run from the repository root: python3 research_tools/cip_b1_backtest.py
Reads only the saved official market captures and R0's identity-checked labels.
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from statistics import mean, median

from dp_backtest_01_inventory_skew import QTY, RATE
from r0_rolling_pair_replay import Book, write_csv

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "research_data/m4a"
AUDIT = ROOT / "research_outputs/execution_replay_audit_20261003"
LABELS = ROOT / "research_outputs/r0_rolling_pair_replay_20261003/settlement_labels.csv"
M4A_AUDIT = ROOT / "research_audits/research_data/m4a/audit.json"
OUT = ROOT / "research_outputs/cip_b1_20261003"
CAPITAL = 25.0
CLIP = 5.0
TARGET = 25.0
CUTOFF_S = 60
assert QTY == CLIP and RATE == min(0.08, 2.8 / CLIP)


def skew_ticks(net: float) -> int:
    """Backtest 01's frozen Python-round skew, including ties-to-even."""
    return round(abs(net) * RATE)


class PoolReplay:
    def __init__(self, slug, start, winner, activation_us, cancel_us, order_rows,
                 fill_rows, path_rows, action_rows):
        self.slug, self.start, self.end, self.winner = slug, start, start + 300_000_000, winner
        self.cutoff = self.end - CUTOFF_S * 1_000_000
        self.activation_us, self.cancel_us = activation_us, cancel_us
        self.books = {s: Book() for s in ("YES", "NO")}
        self.orders = {s: None for s in ("YES", "NO")}
        self.pending_replacement = {s: None for s in ("YES", "NO")}
        self.qty = {s: 0.0 for s in ("YES", "NO")}
        self.cost = {s: 0.0 for s in ("YES", "NO")}
        self.order_rows, self.fill_rows = order_rows, fill_rows
        self.path_rows, self.action_rows = path_rows, action_rows
        self.serial = 0
        self.last_t = start
        self.last_inventory_change_t = start
        self.integral_abs = 0.0
        self.integral_capital = 0.0
        self.integral_pair_cost = 0.0
        self.pair_cost_duration = 0
        self.inventory_duration = 0
        self.positive_pair_duration = 0
        self.at_098_duration = 0
        self.peak_capital = 0.0
        self.max_abs = 0.0
        self.min_pair_cost = None
        self.max_pair_cost = None
        self.first_pair_cost = None
        self.first_avg_y = None
        self.first_avg_n = None
        self.abs_samples = []
        self.resting_lifetimes = []
        self.counts = Counter()
        self.error = None

    def values(self):
        qy, qn = self.qty["YES"], self.qty["NO"]
        ay = self.cost["YES"] / qy if qy else None
        an = self.cost["NO"] / qn if qn else None
        matched = min(qy, qn)
        pc = ay + an if ay is not None and an is not None else None
        return qy - qn, matched, ay, an, pc

    def capital(self):
        return sum(self.cost.values()) + sum(
            o["remaining_qty"] * o["price"] for o in self.orders.values() if o)

    def check(self):
        used = self.capital()
        self.peak_capital = max(self.peak_capital, used)
        self.max_abs = max(self.max_abs, abs(self.values()[0]))
        if used > CAPITAL + 1e-8:
            self.error = f"capital invariant exceeded: {used:.8f}"

    def integrate(self, t):
        dt = t - self.last_t
        if dt < 0:
            self.error = "nonmonotone available time"
            return
        net, _, _, _, pc = self.values()
        self.integral_abs += abs(net) * dt
        self.integral_capital += self.capital() * dt
        if self.qty["YES"] + self.qty["NO"] > 0:
            self.inventory_duration += dt
        if pc is not None:
            self.integral_pair_cost += pc * dt
            self.pair_cost_duration += dt
            if pc < 1 - 1e-12:
                self.positive_pair_duration += dt
            if pc <= .98 + 1e-12:
                self.at_098_duration += dt
        self.last_t = t

    def audit(self, t, action, reason, side=None, order=None, event_kind=None):
        row = {"market": self.slug, "timestamp_us": t, "action": action,
               "reason": reason, "side": side, "order_id": order["order_id"] if order else None,
               "order_price": order["price"] if order else None,
               "remaining_qty": order["remaining_qty"] if order else None,
               "event_kind": event_kind, "book_valid": self.valid_books(),
               "yes_best_bid": self.books["YES"].bid / 10000 if self.books["YES"].bid is not None else None,
               "yes_best_ask": self.books["YES"].ask / 10000 if self.books["YES"].ask is not None else None,
               "no_best_bid": self.books["NO"].bid / 10000 if self.books["NO"].bid is not None else None,
               "no_best_ask": self.books["NO"].ask / 10000 if self.books["NO"].ask is not None else None,
               "net_inventory": self.values()[0],
               "matched_qty": self.values()[1],
               "capital_used": self.capital()}
        self.action_rows.append(row)
        if action in ("CANCEL", "REPLACE") and reason not in (
            "HEAVY_SIDE_RISK_RETREAT", "ORDER_REJECTED", "BOOK_INVALID",
            "CAPITAL_CONSTRAINT", "TARGET_REACHED", "CUTOFF"):
            self.counts["BBO_ONLY_REPRICE"] += 1
        self.counts[action] += 1

    def snapshot(self, t, fill_side, fill_price, fill_qty):
        net, matched, ay, an, pc = self.values()
        row = {"market": self.slug, "timestamp_us": t, "fill_side": fill_side,
               "fill_price": fill_price, "fill_qty": fill_qty,
               "qy": self.qty["YES"], "qn": self.qty["NO"], "avg_y": ay,
               "avg_n": an, "net_inventory": net, "matched_qty": matched,
               "pool_pair_cost": pc, "pair_pnl": matched * (1 - pc) if pc is not None else 0.0,
               "working_yes_price": self.orders["YES"]["price"] if self.orders["YES"] else None,
               "working_no_price": self.orders["NO"]["price"] if self.orders["NO"] else None,
               "capital_used": self.capital(), "skew_ticks": skew_ticks(net),
               "inventory_condition": "BALANCED" if net == 0 else "YES_HEAVY" if net > 0 else "NO_HEAVY"}
        self.path_rows.append(row)
        if pc is not None:
            if self.first_pair_cost is None:
                self.first_pair_cost = pc
                self.first_avg_y, self.first_avg_n = ay, an
            self.min_pair_cost = pc if self.min_pair_cost is None else min(self.min_pair_cost, pc)
            self.max_pair_cost = pc if self.max_pair_cost is None else max(self.max_pair_cost, pc)

    def close_order(self, side, t, status):
        o = self.orders[side]
        assert o is not None
        o["status"] = status
        o["closed_us"] = t
        if status == "canceled":
            self.pending_replacement[side] = o["cancel_reason"]
        if o["accepted_us"] is not None:
            self.resting_lifetimes.append((t - o["accepted_us"]) / 1_000_000)
        self.orders[side] = None

    def cancel(self, side, t, reason, event_kind):
        o = self.orders[side]
        if o is None or o["cancel_request_us"] is not None:
            return
        o["cancel_request_us"] = t
        o["cancel_effective_us"] = max(t + self.cancel_us, o["activation_us"])
        o["cancel_reason"] = reason
        o["status"] = "cancel_pending"
        self.audit(t, "CANCEL", reason, side, o, event_kind)

    def advance(self, t):
        changed = False
        for side in ("YES", "NO"):
            o = self.orders[side]
            if o is None:
                continue
            if o["accepted_us"] is None and t >= o["activation_us"]:
                o["accepted_us"] = o["activation_us"]
                if o["cancel_request_us"] is None:
                    o["status"] = "active"
                self.audit(t, "ACCEPT", o["placement_reason"], side, o, "LIFECYCLE")
                changed = True
            if o["cancel_effective_us"] is not None and t >= o["cancel_effective_us"]:
                self.close_order(side, o["cancel_effective_us"], "canceled")
                changed = True
        self.check()
        return changed

    def valid_books(self):
        return all(b.valid() for b in self.books.values())

    def desired_price_int(self, side, net):
        b = self.books[side]
        heavy = "YES" if net > 0 else "NO" if net < 0 else None
        ticks = skew_ticks(net)
        # R0 book stores ticks in 1e-4 price units. This is Backtest 01's
        # book.bid - round(ticks * book.tick * 10000), without float drift.
        desired = b.bid - (int(round(ticks * b.tick_int)) if side == heavy else 0)
        step = b.tick_int
        if not (0 < desired < 10000 and desired < b.ask and step > 0 and desired % step == 0):
            return None
        return desired

    def place(self, side, t, price_int, reason, event_kind):
        assert self.orders[side] is None
        price = price_int / 10000
        if self.capital() + CLIP * price > CAPITAL + 1e-8:
            self.counts["CAPITAL_BLOCKED"] += 1
            self.audit(t, "SKIP", "CAPITAL_CONSTRAINT", side, None, event_kind)
            return False
        self.serial += 1
        o = {"market": self.slug, "order_id": f"{self.slug}:B1:{self.serial}",
             "side": side, "price": price, "price_int": price_int,
             "size": CLIP, "remaining_qty": CLIP, "submitted_us": t,
             "activation_us": t + self.activation_us, "accepted_us": None,
             "cancel_request_us": None, "cancel_effective_us": None,
             "cancel_reason": None, "closed_us": None, "status": "submitted",
             "placement_reason": reason}
        self.orders[side] = o
        self.order_rows.append(o)
        action = "REPLACE" if self.pending_replacement[side] else "PLACE"
        self.audit(t, action, reason, side, o, event_kind)
        self.pending_replacement[side] = None
        self.check()
        return True

    def reconcile(self, t, event_kind, inventory_changed=False):
        net, matched, _, _, _ = self.values()
        self.abs_samples.append(abs(net))
        if t >= self.cutoff:
            for side in ("YES", "NO"):
                self.cancel(side, t, "CUTOFF", event_kind)
            return
        if matched >= TARGET and net == 0:
            for side in ("YES", "NO"):
                self.cancel(side, t, "TARGET_REACHED", event_kind)
            return
        if not self.valid_books():
            for side in ("YES", "NO"):
                self.cancel(side, t, "BOOK_INVALID", event_kind)
            return
        heavy = "YES" if net > 0 else "NO" if net < 0 else None
        if matched >= TARGET and heavy is not None:
            self.cancel(heavy, t, "TARGET_REACHED", event_kind)
        elif inventory_changed and heavy is not None:
            o = self.orders[heavy]
            desired = self.desired_price_int(heavy, net)
            # A fill/risk change alone permits a retreat. An ordinary BBO
            # change never enters this branch and never reprices a resting order.
            if o is not None and o["cancel_request_us"] is None and (
                desired is None or o["price_int"] > desired
            ):
                self.cancel(heavy, t, "HEAVY_SIDE_RISK_RETREAT", event_kind)

        missing = [s for s in ("YES", "NO") if self.orders[s] is None]
        if not missing:
            return
        desired = {s: self.desired_price_int(s, net) for s in missing}
        if net == 0 and len(missing) == 2:
            if any(desired[s] is None for s in missing):
                self.counts["INVALID_PRICE"] += 1
                return
            required = CLIP * sum(desired[s] for s in missing) / 10000
            if self.capital() + required > CAPITAL + 1e-8:
                self.counts["CAPITAL_BLOCKED"] += 1
                self.audit(t, "SKIP", "CAPITAL_CONSTRAINT", None, None, event_kind)
                return
        if net != 0:
            lighter = "NO" if net > 0 else "YES"
            missing.sort(key=lambda s: s != lighter)
        for side in missing:
            if side == heavy and matched >= TARGET:
                continue
            price_int = desired[side]
            if price_int is None:
                self.counts["INVALID_PRICE"] += 1
                continue
            reason = (self.pending_replacement[side] or
                      ("POST_FILL_NEW_ORDER" if self.counts["FILL"] else "INITIAL_PLACE"))
            self.place(side, t, price_int, reason, event_kind)

    def apply_fill(self, o, qty, event, trade_px):
        if qty <= 0 or qty > o["remaining_qty"] + 1e-12:
            raise ValueError("invalid fill quantity")
        side = o["side"]
        p = event.get("payload") or {}
        t = event["available_at_us"]
        before_net = self.values()[0]
        o["remaining_qty"] -= qty
        self.qty[side] += qty
        self.cost[side] += qty * o["price"]
        self.last_inventory_change_t = t
        self.counts["FILL"] += 1
        if o["remaining_qty"] <= 1e-12:
            self.audit(t, "FILLED", "ORDER_FILLED", side, o, "TRADE")
            self.close_order(side, t, "filled")
        self.fill_rows.append({"market": self.slug, "order_id": o["order_id"],
                              "side": side, "price": o["price"], "size": qty,
                              "remaining_after": o["remaining_qty"],
                              "activation_us": o["activation_us"],
                              "fill_available_us": t,
                              "fill_exchange_us": event.get("exchange_at_us"),
                              "fill_received_us": event.get("received_at_us"),
                              "trade_price": trade_px / 10000,
                              "trade_size": p.get("size"),
                              "trade_event_seq": event.get("event_seq")})
        self.check()
        return before_net != self.values()[0]

    def trade(self, event, side):
        p = event.get("payload") or {}
        o = self.orders[side]
        if p.get("side") != "SELL" or o is None or o["accepted_us"] is None:
            return False
        t, exch, recv = (event.get(k) for k in
                         ("available_at_us", "exchange_at_us", "received_at_us"))
        if None in (t, exch, recv) or min(t, exch, recv) <= o["activation_us"]:
            return False
        if o["cancel_effective_us"] is not None and t >= o["cancel_effective_us"]:
            return False
        trade_px = int(round(float(p["price"]) * 10000))
        if trade_px >= o["price_int"]:
            return False
        # Exact R0 fill semantics: an eligible print fills every remaining share,
        # regardless of public print size. apply_fill handles arbitrary quantity.
        self.apply_fill(o, o["remaining_qty"], event, trade_px)
        return True

    def replay(self, path, token_map):
        with (path / "events.jsonl").open() as stream:
            for line in stream:
                event = json.loads(line)
                t = event.get("available_at_us")
                if t is None or t < self.start or t >= self.end:
                    continue
                if t < self.last_t:
                    self.counts["NONMONOTONE"] += 1
                    continue
                if t >= self.cutoff and not self.counts["CUTOFF_SEEN"]:
                    self.integrate(self.cutoff)
                    self.reconcile(self.cutoff, "CUTOFF")
                    self.counts["CUTOFF_SEEN"] = 1
                self.integrate(t)
                transitioned = self.advance(t)
                p = event.get("payload") or {}
                side = token_map.get(p.get("token_id"))
                decision = False
                filled = False
                kind = event.get("kind")
                if side is not None:
                    if kind in ("BOOK", "REST_BOOK", "DELTA"):
                        decision = self.books[side].update(kind, p)
                    elif kind == "TICK":
                        tick = int(round(float(p["tick_size"]) * 10000))
                        if tick != self.books[side].tick_int:
                            self.books[side].tick_int = tick
                            decision = True
                    elif kind == "TRADE":
                        filled = self.trade(event, side)
                        decision = filled
                if transitioned or decision:
                    self.reconcile(t, kind if decision else "LIFECYCLE", filled)
                    if filled:
                        last = self.fill_rows[-1]
                        self.snapshot(t, last["side"], last["price"], last["size"])
                if self.error:
                    break
        if not self.counts["CUTOFF_SEEN"]:
            self.integrate(self.cutoff)
            self.reconcile(self.cutoff, "CUTOFF")
            self.counts["CUTOFF_SEEN"] = 1
        self.integrate(self.end)
        self.advance(self.end)
        if self.counts["NONMONOTONE"]:
            self.error = self.error or "nonmonotone event availability timestamps"
        if any(self.orders.values()):
            self.error = self.error or "open order at market end"
        self.check()
        return self.result()

    def result(self):
        net, matched, ay, an, pc = self.values()
        residual_side = "YES" if net > 0 else "NO" if net < 0 else None
        residual_cost = abs(net) * ({"YES": ay, "NO": an}[residual_side] if residual_side else 0.0)
        residual_pnl = abs(net) * (1 if residual_side == self.winner else 0) - residual_cost
        pair_pnl = matched * (1 - pc) if pc is not None else 0.0
        lower_y = bool(pair_pnl > 0 and ay is not None and self.first_avg_y is not None
                       and ay < self.first_avg_y - 1e-12)
        lower_n = bool(pair_pnl > 0 and an is not None and self.first_avg_n is not None
                       and an < self.first_avg_n - 1e-12)
        attribution = ("BOTH" if lower_y and lower_n else
                       "LOW-COST YES ACCUMULATION" if lower_y else
                       "LOW-COST NO ACCUMULATION" if lower_n else "NONE")
        duration = self.end - self.start
        market_orders = [o for o in self.order_rows if o["market"] == self.slug]
        market_fills = [f for f in self.fill_rows if f["market"] == self.slug]
        return {"market": self.slug, "winner": self.winner, "qy": self.qty["YES"],
                "qn": self.qty["NO"], "purchased_shares": sum(self.qty.values()),
                "matched_qty": matched, "residual_qty": abs(net),
                "pairing_efficiency": 2 * matched / sum(self.qty.values()) if sum(self.qty.values()) else None,
                "avg_y": ay, "avg_n": an, "final_pool_pair_cost": pc,
                "first_pool_pair_cost": self.first_pair_cost,
                "first_avg_y": self.first_avg_y, "first_avg_n": self.first_avg_n,
                "positive_pair_edge_attribution": attribution,
                "min_pool_pair_cost": self.min_pair_cost, "max_pool_pair_cost": self.max_pair_cost,
                "time_weighted_pool_pair_cost": self.integral_pair_cost / self.pair_cost_duration if self.pair_cost_duration else None,
                "pair_cost_lt_1_time_fraction": self.positive_pair_duration / self.pair_cost_duration if self.pair_cost_duration else None,
                "pair_cost_le_098_time_fraction": self.at_098_duration / self.pair_cost_duration if self.pair_cost_duration else None,
                "pair_cost_observed_seconds": self.pair_cost_duration / 1_000_000,
                "inventory_active_seconds": self.inventory_duration / 1_000_000,
                "pair_pnl": pair_pnl, "residual_pnl": residual_pnl,
                "total_pnl": pair_pnl + residual_pnl,
                "mean_abs_net_inventory": mean(self.abs_samples) if self.abs_samples else 0.0,
                "time_weighted_abs_net_inventory": self.integral_abs / duration,
                "max_abs_net_inventory": self.max_abs, "final_net_inventory": net,
                "mean_capital_used": self.integral_capital / duration,
                "peak_capital_used": self.peak_capital,
                "capital_blocked_events": self.counts["CAPITAL_BLOCKED"],
                "order_submissions": len(market_orders),
                "order_cancels": sum(o["status"] == "canceled" for o in market_orders),
                "order_replaces": self.counts["REPLACE"],
                "accepted_orders": sum(o["accepted_us"] is not None for o in market_orders),
                "raw_fill_records": len(market_fills),
                "filled_shares": sum(f["size"] for f in market_fills),
                "fills_per_100_submissions": 100 * len(market_fills) / len(market_orders) if market_orders else None,
                "median_resting_lifetime_s": median(self.resting_lifetimes) if self.resting_lifetimes else None,
                "mean_resting_lifetime_s": mean(self.resting_lifetimes) if self.resting_lifetimes else None,
                "bbo_only_reprice_count": self.counts["BBO_ONLY_REPRICE"],
                "data_error": self.error or ""}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with (AUDIT / "dataset_audit.csv").open() as stream:
        selected = [r for r in csv.DictReader(stream) if r["execution_replay_usable"].lower() == "true"]
    if len(selected) != 32 or len({r["market"] for r in selected}) != 32:
        raise RuntimeError("audited universe is not exactly 32 unique markets")
    with LABELS.open() as stream:
        label_rows = list(csv.DictReader(stream))
    if len(label_rows) != 32 or {r["market"] for r in label_rows} != {r["market"] for r in selected}:
        raise RuntimeError("R0 official settlement-label universe mismatch")
    labels = {r["market"]: r for r in label_rows}
    lat = json.loads((AUDIT / "summary.json").read_text())
    audit_by_session = {r["session_id"]: r for r in json.loads(M4A_AUDIT.read_text())["sessions"]}
    orders, fills, paths, actions, markets = [], [], [], [], []
    for row in selected:
        path = DATA / row["session"]
        manifest = json.loads((path / "manifest.json").read_text())
        label = labels[row["market"]]
        if (manifest["market_slug"] != row["market"] or
            str(manifest["condition_id"]).lower() != label["condition_id"].lower() or
            manifest["yes_token_id"] != label["yes_token_id"] or
            manifest["no_token_id"] != label["no_token_id"] or
            label["resolution_status"] != "resolved" or label["winner"] not in ("YES", "NO")):
            raise RuntimeError(f"settlement/session identity mismatch: {row['market']}")
        start = int(row["market"].rsplit("-", 1)[1]) * 1_000_000
        sim = PoolReplay(row["market"], start, label["winner"],
                         int(lat["activation_latency_us"]), int(lat["cancel_latency_us"]),
                         orders, fills, paths, actions)
        result = sim.replay(path, {manifest["yes_token_id"]: "YES", manifest["no_token_id"]: "NO"})
        result["session"] = row["session"]
        result["m4a_integrity"] = audit_by_session[row["session"]]["integrity"]
        markets.append(result)
    write_csv(OUT / "market_results.csv", markets)
    write_csv(OUT / "orders.csv", orders)
    write_csv(OUT / "fills.csv", fills)
    write_csv(OUT / "inventory_paths.csv", paths)
    write_csv(OUT / "action_audit.csv", actions)
    errors = {r["market"]: r["data_error"] for r in markets if r["data_error"]}
    bbo_reprices = sum(r["bbo_only_reprice_count"] for r in markets)
    cutoff_by_market = {r["market"]: int(r["market"].rsplit("-", 1)[1]) * 1_000_000
                        + (300 - CUTOFF_S) * 1_000_000 for r in markets}
    for a in actions:
        if a["action"] != "CANCEL":
            continue
        if a["reason"] == "BOOK_INVALID" and a["book_valid"]:
            errors[a["market"]] = "BOOK_INVALID cancel with valid book"
        elif a["reason"] == "HEAVY_SIDE_RISK_RETREAT" and a["event_kind"] != "TRADE":
            bbo_reprices += 1
        elif a["reason"] == "CUTOFF" and a["timestamp_us"] < cutoff_by_market[a["market"]]:
            errors[a["market"]] = "early cutoff cancel"
        elif a["reason"] == "TARGET_REACHED" and a["matched_qty"] < TARGET:
            errors[a["market"]] = "early target cancel"
    matched = sum(r["matched_qty"] for r in markets)
    purchased = sum(r["purchased_shares"] for r in markets)
    pair_pnl = sum(r["pair_pnl"] for r in markets)
    residual_pnl = sum(r["residual_pnl"] for r in markets)
    total_pnl = pair_pnl + residual_pnl
    active_time = sum(r["pair_cost_observed_seconds"] for r in markets)
    positive_time = sum(r["pair_cost_lt_1_time_fraction"] * r["pair_cost_observed_seconds"] for r in markets if r["pair_cost_lt_1_time_fraction"] is not None)
    at_098_time = sum(r["pair_cost_le_098_time_fraction"] * r["pair_cost_observed_seconds"] for r in markets if r["pair_cost_le_098_time_fraction"] is not None)
    resting = [(o["closed_us"] - o["accepted_us"]) / 1_000_000 for o in orders if o["accepted_us"] is not None and o["closed_us"] is not None]
    totals = [r["total_pnl"] for r in markets]
    cap_blocks = sum(r["capital_blocked_events"] for r in markets)
    material_throughput = mean(r["matched_qty"] for r in markets) >= 1.25 * 6.25
    if bbo_reprices or errors:
        cls = "IMPLEMENTATION INVALID"
    elif pair_pnl > 0 and total_pnl > 0 and material_throughput and total_pnl - max(totals) > 0:
        cls = "CONTINUOUS POOL EDGE OBSERVED"
    elif pair_pnl > 0 and total_pnl <= 0 and residual_pnl < 0:
        cls = "PAIR EDGE EXISTS BUT RESIDUAL DOMINATES"
    elif material_throughput:
        cls = "CONTINUOUS POOL IMPROVES THROUGHPUT ONLY"
    elif cap_blocks and sum(r["capital_blocked_events"] > 0 for r in markets) >= 16:
        cls = "CAPITAL LIMIT DOMINATES"
    else:
        cls = "NO IMPROVEMENT OVER R0"
    m = {"order_submissions": len(orders), "order_cancels": sum(o["status"] == "canceled" for o in orders),
         "order_replaces": sum(r["order_replaces"] for r in markets),
         "accepted_orders": sum(o["accepted_us"] is not None for o in orders),
         "raw_fills": len(fills), "filled_shares": sum(f["size"] for f in fills),
         "fills_per_100_submissions": 100 * len(fills) / len(orders) if orders else None,
         "median_resting_lifetime_s": median(resting) if resting else None,
         "mean_resting_lifetime_s": mean(resting) if resting else None,
         "bbo_only_reprice_count": bbo_reprices,
         "mean_fills_per_market": mean(r["raw_fill_records"] for r in markets),
         "median_fills_per_market": median(r["raw_fill_records"] for r in markets),
         "matched_shares": matched, "mean_matched_per_market": mean(r["matched_qty"] for r in markets),
         "median_matched_per_market": median(r["matched_qty"] for r in markets),
         "pairing_efficiency": 2 * matched / purchased if purchased else None,
         "pair_pnl": pair_pnl, "weighted_pool_pair_cost": 1 - pair_pnl / matched if matched else None,
         "pair_pnl_per_matched_share": pair_pnl / matched if matched else None,
         "pair_cost_lt_1_time_fraction": positive_time / active_time if active_time else None,
         "pair_cost_le_098_time_fraction": at_098_time / active_time if active_time else None,
         "pair_cost_observed_seconds": active_time,
         "time_weighted_abs_net_inventory": mean(r["time_weighted_abs_net_inventory"] for r in markets),
         "mean_abs_net_inventory": mean(r["mean_abs_net_inventory"] for r in markets),
         "max_abs_net_inventory": max(r["max_abs_net_inventory"] for r in markets),
         "residual_pnl": residual_pnl,
         "mean_peak_capital": mean(r["peak_capital_used"] for r in markets),
         "median_peak_capital": median(r["peak_capital_used"] for r in markets),
         "max_peak_capital": max(r["peak_capital_used"] for r in markets),
         "capital_blocked_events": cap_blocks,
         "total_pnl": total_pnl,
         "profitable_markets": sum(x > 1e-9 for x in totals),
         "losing_markets": sum(x < -1e-9 for x in totals),
         "flat_markets": sum(abs(x) <= 1e-9 for x in totals)}
    m["positive_pair_edge_attribution"] = {
        name: {"markets": sum(r["pair_pnl"] > 0 and r["positive_pair_edge_attribution"] == name for r in markets),
               "pair_pnl": sum(r["pair_pnl"] for r in markets if r["pair_pnl"] > 0 and r["positive_pair_edge_attribution"] == name)}
        for name in ("LOW-COST YES ACCUMULATION", "LOW-COST NO ACCUMULATION", "BOTH", "NONE")}
    summary = {"classification": cls, "sample": {"markets": len(markets),
               "unique_markets": len({r["market"] for r in markets}),
               "official_settlement_labels": len(labels),
               "m4a_integrity_pass_overlap": sum(r["m4a_integrity"] == "PASS" for r in markets),
               "data_errors": errors},
               "frozen_parameters": {"capital_usd": CAPITAL, "clip_shares": CLIP,
               "skew_rate": RATE, "target_matched_shares": TARGET,
               "pre_close_cutoff_s": CUTOFF_S},
               "latencies_us": {"submit": lat["activation_latency_us"],
               "cancel": lat["cancel_latency_us"]},
               "maker_fill_rule": "R0 full-remaining-order trade-through on same-token SELL print strictly below active BUY; all three timestamps after activation; no print-size limit",
               "metrics": m}
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({"classification": cls, "sample": summary["sample"], "metrics": m}, indent=2))


if __name__ == "__main__":
    main()
