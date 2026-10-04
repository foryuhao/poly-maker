"""Backtest 02: fixed Backtest 01 B versus B plus the source normal-regime gate.

Only persisted official Polymarket events and Backtest 01 settlement labels are read.
No network, credentials, production imports, parameter search, or order submission.
"""
from __future__ import annotations

import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median

from dp_backtest_01_inventory_skew import Book, Policy, QTY, RATE, latency_audit, persisted_winner

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "research_outputs/dp_backtest_01"
OUT = ROOT / "research_outputs/dp_backtest_02"
DATA = ROOT / "research_data/m4a"
STEPS = 2000  # source README: 2,000 simulator steps per experiment
ALPHA = 0.10


def write_csv(path, rows):
    if not rows:
        raise RuntimeError(f"No rows: {path}")
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def valid(books):
    return all(b.seen and b.bid is not None and b.ask is not None and b.bid < b.ask for b in books.values())


def phi_factor(prob):
    d = abs(prob - 0.5)
    if d > 0.45: return 0.20
    if d > 0.40: return 0.35
    if d > 0.35: return 0.50
    if d > 0.30: return 0.65
    if d > 0.20: return 0.82
    if d > 0.10: return 0.93
    return 1.0


class QuoteFilter:
    """Source formula; only step-to-clock mapping is a documented proxy."""

    def __init__(self, start, end):
        self.start = start
        self.end = end
        self.next_step = start + (end - start) / STEPS
        self.step_us = (end - start) / STEPS
        self.prev_mid = None
        self.vol_ema = 1.0
        self.step_count = 0
        self.step_with_valid_mid = 0

    def advance(self, t, yes_book):
        # Sample only the book already locally available before each boundary.
        while self.next_step <= t and self.next_step < self.end:
            if yes_book.seen and yes_book.bid is not None and yes_book.ask is not None and yes_book.bid < yes_book.ask:
                mid = (yes_book.bid + yes_book.ask) / 200.0  # integer price -> cents
                if self.prev_mid is not None:
                    self.vol_ema = ALPHA * abs(mid - self.prev_mid) + (1.0 - ALPHA) * self.vol_ema
                self.prev_mid = mid
                self.step_with_valid_mid += 1
            self.next_step += self.step_us
            self.step_count += 1

    def evaluate(self, t, yes_book):
        if not yes_book.seen or yes_book.bid is None or yes_book.ask is None or yes_book.bid >= yes_book.ask:
            return dict(result="UNAVAILABLE", reason="YES_book_invalid", pass_gate=False,
                        comp_bid_cents=None, comp_ask_cents=None, mid_cents=None,
                        spread_cents=None, spread_value=None, prob_est=None, phi=None,
                        steps_remaining=None, model_vol=None, vol_ema=self.vol_ema,
                        sigma_est=None, z=None, threshold=None)
        bid = yes_book.bid / 100.0
        ask = yes_book.ask / 100.0
        mid = (bid + ask) / 2.0
        spread = ask - bid
        spread_value = (spread - 2.0) / 2.0
        base = dict(comp_bid_cents=bid, comp_ask_cents=ask, mid_cents=mid,
                    spread_cents=spread, spread_value=spread_value,
                    vol_ema=self.vol_ema)
        if spread_value < 0.5:
            return {**base, "result": "REJECT", "reason": "spread_value_below_0.5",
                    "pass_gate": False, "prob_est": None, "phi": None,
                    "steps_remaining": None, "model_vol": None,
                    "sigma_est": None, "z": None, "threshold": None}
        prob = max(0.02, min(0.98, mid / 100.0))
        phi = phi_factor(prob)
        horizon = max(1.0, STEPS * (self.end - t) / (self.end - self.start))
        model_vol = phi * 39.9 / math.sqrt(horizon)
        sigma = max(model_vol, self.vol_ema)
        z = spread_value / sigma
        threshold = 0.4 if spread_value >= 3.0 else 0.8
        passed = z >= threshold
        return {**base, "result": "PASS" if passed else "REJECT",
                "reason": "" if passed else "z_below_threshold", "pass_gate": passed,
                "prob_est": prob, "phi": phi, "steps_remaining": horizon,
                "model_vol": model_vol, "sigma_est": sigma, "z": z,
                "threshold": threshold}


class SpreadClock:
    def __init__(self, start):
        self.last = start
        self.categories = {"YES": None, "NO": None}
        self.durations = defaultdict(int)

    def observe(self, t, books):
        elapsed = max(0, t - self.last)
        for side, category in self.categories.items():
            if category is not None:
                self.durations[(side, category)] += elapsed
        self.last = t
        for side, book in books.items():
            if book.seen and book.bid is not None and book.ask is not None and book.bid < book.ask and book.tick > 0:
                ticks = (book.ask - book.bid) / (book.tick * 10000)
                self.categories[side] = "1" if abs(ticks - 1) < 1e-6 else "2" if abs(ticks - 2) < 1e-6 else "3+" if ticks >= 3 - 1e-6 else "other"
            else:
                self.categories[side] = None

    def finish(self, end):
        elapsed = max(0, end - self.last)
        for side, category in self.categories.items():
            if category is not None:
                self.durations[(side, category)] += elapsed
        return {side: {category: self.durations[(side, category)] for category in ("1", "2", "3+", "other")}
                for side in ("YES", "NO")}


def desired_quotes(inventory, books):
    net = inventory["YES"] - inventory["NO"]
    ticks = round(abs(net) * RATE)
    heavy = "YES" if net > 0 else "NO" if net < 0 else None
    out = {}
    for side, book in books.items():
        if book.bid is None or book.ask is None:
            out[side] = None
            continue
        price = book.bid - (int(round(ticks * book.tick * 10000)) if side == heavy else 0)
        step = int(round(book.tick * 10000))
        out[side] = price / 10000 if 0 < price < 10000 and price < book.ask and step > 0 and price % step == 0 else None
    return out, ticks


def diagnostic(market, policy, trigger, t, books, gate):
    quotes, skew_ticks = desired_quotes(policy.inv, books)
    yes, no = books["YES"], books["NO"]
    return {"market": market, "strategy": policy.name, "trigger": trigger,
            "available_at_us": t, "yes_bid": yes.bid / 10000 if yes.bid is not None else "",
            "yes_ask": yes.ask / 10000 if yes.ask is not None else "",
            "no_bid": no.bid / 10000 if no.bid is not None else "",
            "no_ask": no.ask / 10000 if no.ask is not None else "",
            "yes_spread_ticks": (yes.ask - yes.bid) / (yes.tick * 10000) if yes.bid is not None and yes.ask is not None else "",
            "no_spread_ticks": (no.ask - no.bid) / (no.tick * 10000) if no.bid is not None and no.ask is not None else "",
            **gate, "yes_desired_quote_before_filter": quotes["YES"] if quotes["YES"] is not None else "",
            "no_desired_quote_before_filter": quotes["NO"] if quotes["NO"] is not None else "",
            "yes_inventory": policy.inv["YES"], "no_inventory": policy.inv["NO"],
            "net_inventory": policy.inv["YES"] - policy.inv["NO"],
            "skew_ticks": skew_ticks}


class FilteredPolicy(Policy):
    def __init__(self, slug, start, activation_us, cancel_us, order_rows, fill_rows, gate, diagnostics):
        super().__init__("C", slug, start, activation_us, cancel_us, order_rows, fill_rows)
        self.gate = gate
        self.diagnostics = diagnostics
        self.trigger = ""
        self.pass_decisions = 0
        self.reject_decisions = 0
        self.reason_counts = Counter()
        self.last_gate_t = None
        self.last_gate_result = None
        self.pass_time_us = 0
        self.reject_time_us = 0

    def decide(self, t, books):
        if self.last_gate_t is not None:
            elapsed = max(0, t - self.last_gate_t)
            if self.last_gate_result == "PASS": self.pass_time_us += elapsed
            if self.last_gate_result == "REJECT": self.reject_time_us += elapsed
        self.last_gate_t = t
        if self.last_decision_us is not None:
            elapsed = max(0, t - self.last_decision_us)
            if self.last_valid: self.valid_quote_time_us += elapsed
            if self.last_diff: self.diff_quote_time_us += elapsed
        self.last_decision_us = t
        if not valid(books):
            self.last_valid = False
            self.last_diff = False
            self.last_gate_result = "UNAVAILABLE"
            self.skip["invalid_or_incomplete_book"] += 1
            return
        self.last_valid = True
        self.decisions += 1
        result = self.gate.evaluate(t, books["YES"])
        self.last_gate_result = result["result"]
        if result["pass_gate"]: self.pass_decisions += 1
        else: self.reject_decisions += 1
        self.reason_counts[result["reason"]] += 1
        self.diagnostics.append(diagnostic(self.slug, self, self.trigger, t, books, result))
        net = self.inv["YES"] - self.inv["NO"]
        ticks = round(abs(net) * RATE)
        self.skew[str(ticks)] += 1
        if ticks: self.differing += 1
        self.last_diff = ticks > 0
        heavy = "YES" if net > 0 else "NO" if net < 0 else None
        for side, book in books.items():
            desired = book.bid - (int(round(ticks * book.tick * 10000)) if side == heavy else 0)
            step = int(round(book.tick * 10000))
            if not (0 < desired < 10000 and desired < book.ask and step > 0 and desired % step == 0):
                self.skip["invalid_tick_bound_or_marketable"] += 1
                desired = None
            if not result["pass_gate"]:
                desired = None
            order = self.orders[side]
            if order is not None and order["status"] == "cancel_pending":
                continue
            if order is not None and order["price_int"] == desired:
                continue
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

    def finish(self, end):
        if self.last_gate_t is not None:
            elapsed = max(0, end - self.last_gate_t)
            if self.last_gate_result == "PASS": self.pass_time_us += elapsed
            if self.last_gate_result == "REJECT": self.reject_time_us += elapsed
        row = super().finish(end)
        row.update(filter_pass_decisions=self.pass_decisions,
                   filter_reject_decisions=self.reject_decisions,
                   filter_pass_time_us=self.pass_time_us,
                   filter_reject_time_us=self.reject_time_us,
                   filter_spread_rejects=self.reason_counts["spread_value_below_0.5"],
                   filter_z_rejects=self.reason_counts["z_below_threshold"])
        return row


def active_quote_time(rows, market, strategy, start, end):
    intervals = sorted((max(start, int(o["activation_us"])), min(end, int(o["closed_us"])))
                       for o in rows if o["market"] == market and o["strategy"] == strategy
                       and o["closed_us"] and int(o["activation_us"]) < int(o["closed_us"]))
    total = 0
    left = right = None
    for a, b in intervals:
        if a >= b: continue
        if left is None: left, right = a, b
        elif a > right:
            total += right - left
            left, right = a, b
        else: right = max(right, b)
    if left is not None: total += right - left
    return total


def replay_market(path, activation_us, cancel_us, orders, fills, filter_rows, b_gate_rows):
    m = json.loads((path / "manifest.json").read_text())
    slug = m["market_slug"]
    start = int(slug.rsplit("-", 1)[1]) * 1_000_000
    end = start + 300_000_000
    winner = persisted_winner(path)
    if winner not in ("YES", "NO"):
        raise RuntimeError(f"Unresolved fixed-universe market: {slug}")
    sides = {m["yes_token_id"]: "YES", m["no_token_id"]: "NO"}
    books = {"YES": Book(), "NO": Book()}
    vol = QuoteFilter(start, end)
    spreads = SpreadClock(start)
    b = Policy("B", slug, start, activation_us, cancel_us, orders, fills)
    c = FilteredPolicy(slug, start, activation_us, cancel_us, orders, fills, vol, filter_rows)
    trade_gates = {}
    last_t = 0
    def decide(policy, t, trigger):
        if policy is b:
            policy.decide(t, books)  # exact Backtest 01 Policy method
            if valid(books):
                b_gate_rows.append(diagnostic(slug, b, trigger, t, books,
                                              vol.evaluate(t, books["YES"])))
        else:
            c.trigger = trigger
            c.decide(t, books)
    for line in (path / "events.jsonl").open():
        event = json.loads(line)
        t = event.get("available_at_us")
        if t is None or t < start or t >= end:
            continue
        if t < last_t:
            raise RuntimeError(f"Nonmonotone available timestamp: {slug}")
        last_t = t
        vol.advance(t, books["YES"])
        for policy in (b, c):
            if policy.advance(t):
                decide(policy, t, "lifecycle")
        kind = event["kind"]
        payload = event.get("payload") or {}
        side = sides.get(payload.get("token_id"))
        if side is None:
            continue
        if kind in ("BOOK", "REST_BOOK", "DELTA"):
            if books[side].update(kind, payload):
                spreads.observe(t, books)
                for policy in (b, c): decide(policy, t, "book")
        elif kind == "TICK":
            tick = float(payload["tick_size"])
            if tick != books[side].tick:
                books[side].tick = tick
                spreads.observe(t, books)
                for policy in (b, c): decide(policy, t, "tick")
        elif kind == "TRADE":
            for policy in (b, c):
                if policy.trade(event, side):
                    if policy is b:
                        trade_gates[(slug, side, event["event_seq"])] = vol.evaluate(t, books["YES"])
                    decide(policy, t, "fill")
    result = [p.finish(end) for p in (b, c)]
    for row in result:
        row["settlement_winner"] = winner
        row["pnl"] = round(row["yes_shares" if winner == "YES" else "no_shares"] - row["gross_purchase_cost"], 6)
        row["active_quote_time_us"] = active_quote_time(orders, slug, row["strategy"], start, end)
    return result, spreads.finish(end), vol.step_count, vol.step_with_valid_mid, trade_gates


def fill_attribution(fills, markets, trade_gates, b_gate_rows, orders):
    winner = {r["market"]: r["settlement_winner"] for r in markets if r["strategy"] == "B"}
    group = defaultdict(list)
    for f in fills: group[(f["market"], f["strategy"])].append(f)
    enriched = []
    for key, fs in group.items():
        y = n = 0
        for f in sorted(fs, key=lambda x: (int(x["fill_available_us"]), int(x["trade_event_seq"]))):
            by, bn = y, n
            before = abs(y - n)
            if f["side"] == "YES": y += QTY
            else: n += QTY
            after = abs(y - n)
            cat = "BALANCING" if after < before else "IMBALANCE-INCREASING" if after > before else "NEUTRAL"
            contribution = QTY * ((1 if f["side"] == winner[f["market"]] else 0) - float(f["price"]))
            enriched.append({**f, "winner": winner[f["market"]], "inventory_before_yes": by,
                "inventory_before_no": bn, "fill_class": cat,
                "expost_settlement_contribution": round(contribution, 6)})
    lookup = {(f["market"], f["side"], int(f["trade_event_seq"]), f["strategy"]): f for f in enriched}
    keys_b = {k[:3] for k in lookup if k[3] == "B"}
    keys_c = {k[:3] for k in lookup if k[3] == "C"}
    b_at_submit = {}
    for row in b_gate_rows:
        b_at_submit[(row["market"], int(row["available_at_us"]))] = row
    order_by_id = {o["order_id"]: o for o in orders}
    removed = []
    for market, side, seq in sorted(keys_b - keys_c):
        f = lookup[(market, side, seq, "B")]
        order = order_by_id[f["order_id"]]
        submit_gate = b_at_submit.get((market, int(order["submitted_us"])))
        trade_gate = trade_gates.get((market, side, seq), {})
        removed.append({"market": market, "side": side, "price": f["price"],
            "B_order_id": f["order_id"], "trade_event_seq": seq,
            "inventory_before_yes": f["inventory_before_yes"],
            "inventory_before_no": f["inventory_before_no"],
            "spread_cents_at_submit": submit_gate["spread_cents"] if submit_gate else "",
            "spread_value_at_submit": submit_gate["spread_value"] if submit_gate else "",
            "vol_ema_at_submit": submit_gate["vol_ema"] if submit_gate else "",
            "sigma_est_at_submit": submit_gate["sigma_est"] if submit_gate else "",
            "z_at_submit": submit_gate["z"] if submit_gate else "",
            "filter_result_at_submit": submit_gate["result"] if submit_gate else "",
            "filter_result_at_trade": trade_gate.get("result", ""),
            "winner": f["winner"], "fill_class": f["fill_class"],
            "expost_settlement_contribution": f["expost_settlement_contribution"]})
    return enriched, removed, len(keys_b & keys_c), len(keys_c - keys_b)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    audit = list(csv.DictReader((SOURCE / "dataset_audit.csv").open()))
    fixed = [r for r in audit if r["execution_replay_usable"] == "True"]
    if len(fixed) != 20 or len({r["market"] for r in fixed}) != 20:
        raise RuntimeError("Backtest 01 fixed market universe changed")
    paths = [DATA / r["session"] for r in fixed]
    submit, cancel = latency_audit(paths)
    activation_us = int(round(submit["p75_ms"] * 1000))
    cancel_us = int(round(cancel["p75_ms"] * 1000))
    if (submit["n"], cancel["n"], activation_us, cancel_us) != (74, 176, 412289, 231100):
        raise RuntimeError("Backtest 01 latency assumption changed")
    orders, fills, filter_rows, b_gate_rows, markets = [], [], [], [], []
    spread_time = {}
    steps = Counter()
    trade_gates = {}
    for row in fixed:
        rs, spread, all_steps, valid_steps, gates = replay_market(DATA / row["session"], activation_us, cancel_us,
                                                                  orders, fills, filter_rows, b_gate_rows)
        markets.extend(rs)
        spread_time[row["market"]] = spread
        steps["all"] += all_steps
        steps["with_valid_mid"] += valid_steps
        trade_gates.update(gates)
    # The B policy and its entire execution path must equal the frozen 01B output.
    def same_rows(name, current):
        prior = [r for r in csv.DictReader((SOURCE / name).open()) if r["strategy"] == "B"]
        now = [{k: "" if row.get(k) is None else str(row.get(k)) for k in prior[0]} for row in current if row["strategy"] == "B"]
        if prior != now:
            raise RuntimeError(f"B execution changed relative to Backtest 01: {name}")
    same_rows("orders.csv", orders)
    same_rows("fills.csv", fills)
    prior_markets = {r["market"]: r for r in csv.DictReader((SOURCE / "market_results.csv").open()) if r["strategy"] == "B"}
    for row in markets:
        if row["strategy"] != "B": continue
        prior = prior_markets[row["market"]]
        for key in prior:
            if str(row[key]) != prior[key]:
                raise RuntimeError(f"B market metric changed: {row['market']} {key}")
    enriched, removed, shared, c_only = fill_attribution(fills, markets, trade_gates, b_gate_rows, orders)
    write_csv(OUT / "market_results.csv", markets)
    write_csv(OUT / "orders.csv", orders)
    write_csv(OUT / "fills.csv", fills)
    write_csv(OUT / "filter_events.csv", filter_rows + b_gate_rows)
    if removed: write_csv(OUT / "filtered_fills.csv", removed)
    write_csv(OUT / "fill_attribution.csv", enriched)
    paired = []
    for market in sorted({r["market"] for r in markets}):
        b = next(r for r in markets if r["market"] == market and r["strategy"] == "B")
        c = next(r for r in markets if r["market"] == market and r["strategy"] == "C")
        paired.append({"market": market, "winner": b["settlement_winner"],
            "pnl_B": b["pnl"], "pnl_C": c["pnl"], "delta_pnl": round(c["pnl"] - b["pnl"], 6),
            "fills_B": b["conservative_fills"], "fills_C": c["conservative_fills"],
            "final_residual_B": b["final_residual"], "final_residual_C": c["final_residual"],
            "delta_final_residual": c["final_residual"] - b["final_residual"],
            "max_residual_B": b["max_residual"], "max_residual_C": c["max_residual"],
            "time_avg_residual_B": b["time_avg_residual"], "time_avg_residual_C": c["time_avg_residual"],
            "active_quote_time_us_B": b["active_quote_time_us"],
            "active_quote_time_us_C": c["active_quote_time_us"]})
    write_csv(OUT / "paired_markets.csv", paired)
    by_strategy = {}
    for name in ("B", "C"):
        rs = [r for r in markets if r["strategy"] == name]
        fs = [f for f in enriched if f["strategy"] == name]
        pnls = [r["pnl"] for r in rs]
        by_strategy[name] = {"total_pnl": round(sum(pnls), 6),
            "mean_pnl_per_market": mean(pnls), "median_pnl_per_market": median(pnls),
            "profitable_markets": sum(v > 0 for v in pnls), "losing_markets": sum(v < 0 for v in pnls),
            "worst_market_pnl": min(pnls), "best_market_pnl": max(pnls),
            "mean_time_avg_residual": mean(r["time_avg_residual"] for r in rs),
            "mean_final_residual": mean(r["final_residual"] for r in rs),
            "max_residual_any_market": max(r["max_residual"] for r in rs),
            "orders": sum(r["submitted_orders"] for r in rs), "fills": len(fs),
            "fills_per_market": len(fs) / len(rs), "zero_fill_markets": sum(r["conservative_fills"] == 0 for r in rs),
            "active_quote_time_us": sum(r["active_quote_time_us"] for r in rs),
            "balancing_fills": sum(f["fill_class"] == "BALANCING" for f in fs),
            "imbalance_increasing_fills": sum(f["fill_class"] == "IMBALANCE-INCREASING" for f in fs)}
    ds = [r["delta_pnl"] for r in paired]
    total = sum(ds)
    positive = sorted((d for d in ds if d > 0), reverse=True)
    vals = [r["z"] for r in filter_rows if r["z"] is not None]
    def distribution(values):
        values = sorted(values)
        return {"n": len(values), "p25": values[int(.25 * (len(values) - 1))] if values else None,
                "median": median(values) if values else None,
                "p75": values[int(.75 * (len(values) - 1))] if values else None}
    spread_totals = {side: {category: sum(market[side][category] for market in spread_time.values())
                             for category in ("1", "2", "3+", "other")}
                     for side in ("YES", "NO")}
    summary = {"markets": 20, "source_quote_filter": "octavi42/prediction-market-maker/strategies/strategy_documented.py",
        "source_fixed_parameters": {"vol_ema_initial": 1.0, "vol_alpha": ALPHA,
            "model_vol_numerator": 39.9, "min_spread_value": 0.5,
            "z_threshold_small_spread": 0.8, "z_threshold_spread_value_ge_3": 0.4,
            "simulation_steps": STEPS},
        "mapping": {"competitor_book": "public YES L2 book; one-security gate applied to both BUY tokens",
            "source_price_unit": "cents (0..100); actual Polymarket tick count recorded separately",
            "steps_remaining": "2000 * remaining_market_microseconds / 300000000, clamped at 1",
            "vol_ema": "source alpha 0.10 on YES midpoint absolute cent changes sampled on a causal 150ms clock",
            "prob_est": "clipped YES book midpoint, not privileged probability",
            "filter_semantics": "source normal-regime return CancelAll: reject both quotes"},
        "latency": {"submit_to_ack": submit, "cancel_to_ack": cancel,
                     "activation_us": activation_us, "cancel_effective_us": cancel_us},
        "steps": dict(steps), "spread_valid_time_us": spread_time,
        "spread_valid_time_totals_us": spread_totals,
        "B_baseline_parity": "orders, fills, and market metrics equal Backtest 01B B rows",
        "strategy": by_strategy,
        "filter": {"C_valid_decisions": len(filter_rows),
            "C_pass_decisions": sum(r["result"] == "PASS" for r in filter_rows),
            "C_reject_decisions": sum(r["result"] == "REJECT" for r in filter_rows),
            "C_reject_spread": sum(r["reason"] == "spread_value_below_0.5" for r in filter_rows),
            "C_reject_z": sum(r["reason"] == "z_below_threshold" for r in filter_rows),
            "B_reference_decisions": len(b_gate_rows),
            "B_reference_rejected": sum(r["result"] == "REJECT" for r in b_gate_rows),
            "z_count": len(vals), "z_p25": sorted(vals)[int(.25*(len(vals)-1))] if vals else None,
            "z_median": median(vals) if vals else None,
            "z_p75": sorted(vals)[int(.75*(len(vals)-1))] if vals else None,
            "z_pass": distribution([r["z"] for r in filter_rows if r["result"] == "PASS" and r["z"] is not None]),
            "z_reject": distribution([r["z"] for r in filter_rows if r["result"] == "REJECT" and r["z"] is not None]),
            "markets_without_eligible_decision": sum(not any(r["market"] == market and r["result"] == "PASS"
                                                        for r in filter_rows) for market in spread_time),
            "markets_without_C_orders": sum(r["strategy"] == "C" and r["submitted_orders"] == 0 for r in markets)},
        "removed_fills": {"B_only": len(removed), "C_only": c_only, "shared": shared,
            "balancing": sum(r["fill_class"] == "BALANCING" for r in removed),
            "imbalance_increasing": sum(r["fill_class"] == "IMBALANCE-INCREASING" for r in removed),
            "winner_side": sum(r["side"] == r["winner"] for r in removed),
            "loser_side": sum(r["side"] != r["winner"] for r in removed),
            "B_removed_expost_contribution": round(sum(float(r["expost_settlement_contribution"]) for r in removed), 6)},
        "paired": {"total_delta_pnl": round(total, 6), "median_market_delta": median(ds),
            "C_better": sum(v > 0 for v in ds), "C_worse": sum(v < 0 for v in ds), "ties": sum(v == 0 for v in ds),
            "exclude_best": round(total - max(ds), 6), "exclude_worst": round(total - min(ds), 6),
            "top_two_positive_contribution": sum(positive[:2]),
            "top_two_positive_share_of_net": sum(positive[:2]) / total if total > 0 and positive else None}}
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: summary[k] for k in ("B_baseline_parity", "strategy", "filter", "removed_fills", "paired")}, indent=2))


if __name__ == "__main__":
    main()
