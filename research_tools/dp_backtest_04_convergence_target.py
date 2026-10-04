"""Research-only convergence surface and fixed B/E inventory-target replay.

Run: python3 research_tools/dp_backtest_04_convergence_target.py
Surface-only: python3 research_tools/dp_backtest_04_convergence_target.py --surface-only
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median

from dp_backtest_01_inventory_skew import Book, Policy, QTY, RATE, latency_audit, persisted_winner
from dp_backtest_02_quote_filter import write_csv

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "research_data/m4a"
SOURCE = ROOT / "research_outputs/dp_backtest_01"
OUT = ROOT / "research_outputs/dp_backtest_04"
TIME_EDGES = (0, 60, 120, 180, 210, 240, 270, 300)
PRICE_EDGES = (0.50, 0.60, 0.70, 0.80, 0.90, 0.95, 1.000001)


def fixed_universe():
    audit = list(csv.DictReader((SOURCE / "dataset_audit.csv").open()))
    selected = [r for r in audit if r["execution_replay_usable"] == "True"]
    if len(selected) != 20 or len({r["market"] for r in selected}) != 20:
        raise RuntimeError("Backtest 01 market universe changed")
    return [(DATA / r["session"], r["market"]) for r in selected]


def valid(books):
    return all(b.seen and b.bid is not None and b.ask is not None and b.bid < b.ask for b in books.values())


def mid(book):
    return (book.bid + book.ask) / 20000


def leader(books):
    if not valid(books): return None
    ym, nm = mid(books["YES"]), mid(books["NO"])
    if ym == nm: return None
    return ("YES", ym, ym, nm) if ym > nm else ("NO", nm, ym, nm)


def bucket(value, edges):
    for lo, hi in zip(edges, edges[1:]):
        if lo <= value < hi:
            shown_hi = 1 if edges == PRICE_EDGES and hi > 1 else hi
            return f"{lo:g}–{shown_hi:g}"
    return None


def sample_surface(path):
    manifest = json.loads((path / "manifest.json").read_text())
    market = manifest["market_slug"]
    start = int(market.rsplit("-", 1)[1]) * 1_000_000
    end = start + 300_000_000
    winner = persisted_winner(path)
    if winner not in ("YES", "NO"): raise RuntimeError(f"Missing validated winner: {market}")
    sides = {manifest["yes_token_id"]: "YES", manifest["no_token_id"]: "NO"}
    books = {"YES": Book(), "NO": Book()}
    observations = []
    next_second = 0
    last_t = 0
    def sample(second):
        l = leader(books)
        if l is None: return
        side, price, yes_mid, no_mid = l
        pb = bucket(price, PRICE_EDGES)
        tb = bucket(second, TIME_EDGES)
        if pb is None or tb is None: return
        observations.append({"market": market, "elapsed_s": second, "remaining_s": 300-second,
            "yes_mid": yes_mid, "no_mid": no_mid, "leader": side, "leader_price": price,
            "price_bucket": pb, "time_bucket": tb, "winner": winner,
            "leader_won": int(side == winner)})
    for line in (path / "events.jsonl").open():
        event = json.loads(line)
        t = event.get("available_at_us")
        if t is None or t < start or t >= end: continue
        if t < last_t: raise RuntimeError(f"Nonmonotone available time: {market}")
        last_t = t
        while next_second < 300 and start + next_second * 1_000_000 < t:
            sample(next_second)
            next_second += 1
        payload = event.get("payload") or {}
        side = sides.get(payload.get("token_id"))
        if side is None: continue
        if event["kind"] in ("BOOK", "REST_BOOK", "DELTA"):
            books[side].update(event["kind"], payload)
        elif event["kind"] == "TICK":
            books[side].tick = float(payload["tick_size"])
    while next_second < 300:
        sample(next_second)
        next_second += 1
    return observations


def surface_stats(rows, *, first_per_market=False):
    if first_per_market:
        rows = list({r["market"]: r for r in reversed(rows)}.values())
    if not rows:
        return {"observations": 0, "unique_markets": 0, "leader_win_rate": None,
                "mean_price": None, "median_price": None, "edge": None, "reversal_rate": None,
                "reversal_markets": 0}
    wins = mean(r["leader_won"] for r in rows)
    avg_price = mean(r["leader_price"] for r in rows)
    return {"observations": len(rows), "unique_markets": len({r["market"] for r in rows}),
            "leader_win_rate": wins, "mean_price": avg_price,
            "median_price": median(r["leader_price"] for r in rows),
            "edge": wins-avg_price, "reversal_rate": 1-wins,
            "reversal_markets": len({r["market"] for r in rows if not r["leader_won"]})}


def make_surface(universe):
    obs = []
    for path, _ in universe: obs.extend(sample_surface(path))
    surface = []
    for ti in range(len(TIME_EDGES)-1):
        tb = bucket(TIME_EDGES[ti], TIME_EDGES)
        for pi in range(len(PRICE_EDGES)-1):
            pb = bucket(PRICE_EDGES[pi], PRICE_EDGES)
            rows = [r for r in obs if r["time_bucket"] == tb and r["price_bucket"] == pb]
            raw = surface_stats(rows)
            one = surface_stats(rows, first_per_market=True)
            surface.append({"time_bucket": tb, "price_bucket": pb, **raw,
                "market_first_win_rate": one["leader_win_rate"],
                "market_first_mean_price": one["mean_price"],
                "market_first_edge": one["edge"],
                "market_first_reversals": one["reversal_markets"]})
    focus = {}
    for price_cut in (0.90, 0.95):
        for time_cut in (210, 240):
            rows = [r for r in obs if r["elapsed_s"] >= time_cut and r["leader_price"] >= price_cut]
            raw = surface_stats(rows)
            one = surface_stats(rows, first_per_market=True)
            focus[f"price_ge_{price_cut:.2f}_elapsed_ge_{time_cut}"] = {
                **raw, "market_first_win_rate": one["leader_win_rate"],
                "market_first_mean_price": one["mean_price"],
                "market_first_edge": one["edge"],
                "market_first_reversals": one["reversal_markets"]}
    write_csv(OUT / "convergence_observations.csv", obs)
    write_csv(OUT / "convergence_surface.csv", surface)
    return obs, surface, focus


def target_rule(t, start, books):
    """Frozen after surface-only review: one 5-share late high-price tier."""
    observed = leader(books)
    elapsed = (t - start) / 1_000_000
    if observed is None or elapsed < 210 or observed[1] < 0.90:
        return 0, observed
    return (QTY if observed[0] == "YES" else -QTY), observed


class TargetPolicy(Policy):
    def __init__(self, slug, start, activation_us, cancel_us, orders, fills):
        super().__init__("E", slug, start, activation_us, cancel_us, orders, fills)
        self.target = 0
        self.target_since_us = start
        self.target_time_us = 0
        self.nonzero_decisions = 0
        self.max_target_abs = 0
        self.order_context = {}
        self.fill_context = {}
        self.target_intervals = []
        self.interval_open = None
        self.last_leader = None
        self.last_leader_price = None

    def decide(self, t, books):
        # Preserve B's decision-time integration and validity behavior.
        if self.last_decision_us is not None:
            elapsed = max(0, t - self.last_decision_us)
            if self.last_valid: self.valid_quote_time_us += elapsed
            if self.last_diff: self.diff_quote_time_us += elapsed
        self.last_decision_us = t
        if self.target:
            self.target_time_us += max(0, t - self.target_since_us)
        self.target_since_us = t
        new_target, observed = target_rule(t, self.start_us, books)
        if new_target != self.target:
            if self.interval_open is not None:
                self.target_intervals.append({**self.interval_open, "end_us": t})
                self.interval_open = None
            if new_target:
                self.interval_open = {"market": self.slug, "start_us": t,
                    "leader": observed[0], "leader_price": observed[1],
                    "target_inventory": new_target, "inventory_at_start": self.inv["YES"] - self.inv["NO"]}
        self.target = new_target
        self.last_leader = observed[0] if observed else None
        self.last_leader_price = observed[1] if observed else None
        if not valid(books):
            self.last_valid = False
            self.last_diff = False
            self.skip["invalid_or_incomplete_book"] += 1
            return
        self.last_valid = True
        self.decisions += 1
        if self.target:
            self.nonzero_decisions += 1
            self.max_target_abs = max(self.max_target_abs, abs(self.target))
        net = self.inv["YES"] - self.inv["NO"]
        error = net - self.target
        ticks = round(abs(error) * RATE)
        self.skew[str(ticks)] += 1
        if ticks: self.differing += 1
        self.last_diff = ticks > 0
        heavy = "YES" if error > 0 else "NO" if error < 0 else None
        for side, book in books.items():
            desired = book.bid - (int(round(ticks * book.tick * 10000)) if side == heavy else 0)
            step = int(round(book.tick * 10000))
            if not (0 < desired < 10000 and desired < book.ask and step > 0 and desired % step == 0):
                self.skip["invalid_tick_bound_or_marketable"] += 1
                desired = None
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
                self.order_context[o["order_id"]] = {
                    "target_at_submit": self.target, "leader_at_submit": observed[0] if observed else "",
                    "leader_price_at_submit": observed[1] if observed else "",
                    "elapsed_s_at_submit": (t-self.start_us)/1_000_000,
                    "yes_inventory_at_submit": self.inv["YES"],
                    "no_inventory_at_submit": self.inv["NO"], "inventory_error_at_submit": error}

    def trade(self, event, side):
        order = self.orders[side]
        order_id = order["order_id"] if order else None
        before_yes, before_no = self.inv["YES"], self.inv["NO"]
        target_at_fill = self.target
        if not super().trade(event, side): return False
        self.fill_context[order_id] = {**self.order_context[order_id],
            "target_at_fill": target_at_fill,
            "leader_at_fill": self.last_leader or "",
            "leader_price_at_fill": self.last_leader_price if self.last_leader_price is not None else "",
            "yes_inventory_before_fill": before_yes, "no_inventory_before_fill": before_no,
            "yes_inventory_after_fill": self.inv["YES"],
            "no_inventory_after_fill": self.inv["NO"],
            "elapsed_s_at_fill": (event["available_at_us"]-self.start_us)/1_000_000}
        return True

    def finish(self, end):
        if self.target:
            self.target_time_us += max(0, end-self.target_since_us)
        if self.interval_open is not None:
            self.target_intervals.append({**self.interval_open, "end_us": end})
            self.interval_open = None
        row = super().finish(end)
        row.update(target_time_us=self.target_time_us, nonzero_target_decisions=self.nonzero_decisions,
                   max_target_abs=self.max_target_abs)
        return row


def replay_market(path, activation_us, cancel_us, orders, fills, fill_context, intervals):
    manifest = json.loads((path / "manifest.json").read_text())
    slug = manifest["market_slug"]
    start = int(slug.rsplit("-", 1)[1]) * 1_000_000
    end = start + 300_000_000
    winner = persisted_winner(path)
    if winner not in ("YES", "NO"): raise RuntimeError(f"Missing official winner: {slug}")
    sides = {manifest["yes_token_id"]: "YES", manifest["no_token_id"]: "NO"}
    books = {"YES": Book(), "NO": Book()}
    b = Policy("B", slug, start, activation_us, cancel_us, orders, fills)
    e = TargetPolicy(slug, start, activation_us, cancel_us, orders, fills)
    last_t = 0
    for line in (path / "events.jsonl").open():
        event = json.loads(line)
        t = event.get("available_at_us")
        if t is None or t < start or t >= end: continue
        if t < last_t: raise RuntimeError(f"Nonmonotone available time: {slug}")
        last_t = t
        for policy in (b, e):
            if policy.advance(t): policy.decide(t, books)
        payload = event.get("payload") or {}
        side = sides.get(payload.get("token_id"))
        if side is None: continue
        kind = event["kind"]
        if kind in ("BOOK", "REST_BOOK", "DELTA"):
            if books[side].update(kind, payload):
                for policy in (b, e): policy.decide(t, books)
        elif kind == "TICK":
            tick = float(payload["tick_size"])
            if tick != books[side].tick:
                books[side].tick = tick
                for policy in (b, e): policy.decide(t, books)
        elif kind == "TRADE":
            for policy in (b, e):
                if policy.trade(event, side): policy.decide(t, books)
    rows = [p.finish(end) for p in (b, e)]
    for row in rows:
        row["settlement_winner"] = winner
        row["pnl"] = round(row["yes_shares" if winner == "YES" else "no_shares"] - row["gross_purchase_cost"], 6)
    fill_context.update(e.fill_context)
    intervals.extend(e.target_intervals)
    return rows


def assert_b_parity(markets, orders, fills):
    for name, rows in (("orders.csv", orders), ("fills.csv", fills)):
        prior = [r for r in csv.DictReader((SOURCE / name).open()) if r["strategy"] == "B"]
        now = [{k: "" if row.get(k) is None else str(row.get(k)) for k in prior[0]}
               for row in rows if row["strategy"] == "B"]
        if prior != now: raise RuntimeError(f"Frozen B path mismatch: {name}")
    prior = {r["market"]: r for r in csv.DictReader((SOURCE / "market_results.csv").open())
             if r["strategy"] == "B"}
    for row in markets:
        if row["strategy"] != "B": continue
        for key, value in prior[row["market"]].items():
            if str(row[key]) != value: raise RuntimeError(f"Frozen B metric mismatch: {row['market']} {key}")


def residual_economics(row, fills):
    market, strategy, winner = row["market"], row["strategy"], row["settlement_winner"]
    fs = [f for f in fills if f["market"] == market and f["strategy"] == strategy]
    y, n = row["yes_shares"], row["no_shares"]
    matched = min(y, n)
    cy = sum(QTY * f["price"] for f in fs if f["side"] == "YES")
    cn = sum(QTY * f["price"] for f in fs if f["side"] == "NO")
    avg_y = cy/y if y else 0
    avg_n = cn/n if n else 0
    winning_residual = max(y-n, 0) if winner == "YES" else max(n-y, 0)
    losing_residual = max(n-y, 0) if winner == "YES" else max(y-n, 0)
    paired_contribution = matched * (1-avg_y-avg_n)
    residual_contribution = winning_residual * (1-(avg_y if winner == "YES" else avg_n))
    residual_contribution -= losing_residual * (avg_n if winner == "YES" else avg_y)
    signed = winning_residual-losing_residual
    start = int(market.rsplit("-", 1)[1]) * 1_000_000
    end = start + 300_000_000
    previous, net, signed_share_us = start, 0, 0
    for f in sorted(fs, key=lambda f: (f["fill_available_us"], f["trade_event_seq"])):
        t = f["fill_available_us"]
        signed_share_us += net * (1 if winner == "YES" else -1) * (t-previous)
        net += QTY if f["side"] == "YES" else -QTY
        previous = t
    signed_share_us += net * (1 if winner == "YES" else -1) * (end-previous)
    if abs(paired_contribution + residual_contribution - row["pnl"]) > 1e-5:
        raise RuntimeError(f"Residual accounting mismatch: {market} {strategy}")
    return {"paired_contribution": round(paired_contribution, 6),
        "residual_contribution": round(residual_contribution, 6),
        "final_winning_residual": winning_residual, "final_losing_residual": losing_residual,
        "signed_residual_toward_winner": signed,
        "time_avg_signed_residual_toward_winner": signed_share_us / (end-start),
        "unmatched_shares": abs(y-n)}


def write_empty_capable(path, rows, fields):
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def evaluate(universe, surface, focus):
    paths = [p for p, _ in universe]
    submit, cancel = latency_audit(paths)
    activation_us = int(round(submit["p75_ms"] * 1000))
    cancel_us = int(round(cancel["p75_ms"] * 1000))
    if (submit["n"], cancel["n"], activation_us, cancel_us) != (74, 176, 412289, 231100):
        raise RuntimeError("Frozen replay latency changed")
    orders, fills, markets, intervals = [], [], [], []
    fill_context = {}
    for path, _ in universe:
        markets.extend(replay_market(path, activation_us, cancel_us, orders, fills,
                                     fill_context, intervals))
    assert_b_parity(markets, orders, fills)
    for row in markets:
        row.update(residual_economics(row, fills))
    b_keys = {(f["market"], f["side"], f["trade_event_seq"]) for f in fills if f["strategy"] == "B"}
    e_keys = {(f["market"], f["side"], f["trade_event_seq"]) for f in fills if f["strategy"] == "E"}
    winners = {r["market"]: r["settlement_winner"] for r in markets if r["strategy"] == "B"}
    convergence_fills = []
    for f in fills:
        if f["strategy"] != "E": continue
        context = fill_context[f["order_id"]]
        key = (f["market"], f["side"], f["trade_event_seq"])
        target_associated = context["target_at_submit"] != 0 or context["target_at_fill"] != 0
        target_specific = key not in b_keys and target_associated
        leader_side = context["leader_at_submit"]
        convergence_fills.append({**f, **context, "event_also_fills_B": key in b_keys,
            "target_associated": target_associated, "target_specific_path_fill": target_specific,
            "toward_leader_at_submit": f["side"] == leader_side if leader_side else "",
            "eventual_winner": winners[f["market"]],
            "convergence_correct": f["side"] == winners[f["market"]] if target_associated else "",
            "expost_settlement_contribution": round(QTY*((f["side"] == winners[f["market"]])-f["price"]), 6)})
    e_fills_by_market = defaultdict(list)
    for f in fills:
        if f["strategy"] == "E": e_fills_by_market[f["market"]].append(f)
    reversal_cases = []
    for interval in intervals:
        net = interval["inventory_at_start"]
        leader_sign = 1 if interval["leader"] == "YES" else -1
        peak_leader_residual = max(0, net * leader_sign)
        carried = peak_leader_residual > 0
        last_residual = net
        for f in sorted(e_fills_by_market[interval["market"]], key=lambda x: x["fill_available_us"]):
            if not interval["start_us"] < f["fill_available_us"] <= interval["end_us"]: continue
            net += QTY if f["side"] == "YES" else -QTY
            peak_leader_residual = max(peak_leader_residual, net * leader_sign)
            carried |= peak_leader_residual > 0
            last_residual = net
        interval["residual_at_end"] = last_residual
        interval["carried_leader_residual"] = carried
        interval["peak_leader_residual"] = peak_leader_residual
        interval["winner"] = winners[interval["market"]]
        interval["leader_reversed"] = interval["leader"] != interval["winner"]
        if carried and interval["leader_reversed"]:
            econ = next(r for r in markets if r["market"] == interval["market"] and r["strategy"] == "E")
            reversal_cases.append({**interval, "reversal_timing_us": "after_last_target_observation_or_before_settlement",
                "market_residual_settlement_contribution": econ["residual_contribution"],
                "market_pnl": econ["pnl"]})
    paired = []
    for market in sorted({r["market"] for r in markets}):
        b, e = (next(r for r in markets if r["market"] == market and r["strategy"] == x)
                for x in ("B", "E"))
        paired.append({"market": market, "winner": b["settlement_winner"],
            "pnl_B": b["pnl"], "pnl_E": e["pnl"], "delta_pnl": round(e["pnl"]-b["pnl"], 6),
            "paired_contribution_B": b["paired_contribution"],
            "paired_contribution_E": e["paired_contribution"],
            "residual_contribution_B": b["residual_contribution"],
            "residual_contribution_E": e["residual_contribution"],
            "final_winning_residual_B": b["final_winning_residual"],
            "final_winning_residual_E": e["final_winning_residual"],
            "final_losing_residual_B": b["final_losing_residual"],
            "final_losing_residual_E": e["final_losing_residual"],
            "target_time_us_E": e["target_time_us"]})
    def stats(strategy):
        rs = [r for r in markets if r["strategy"] == strategy]
        pnls = [r["pnl"] for r in rs]
        return {"total_pnl": round(sum(pnls), 6), "mean_pnl": mean(pnls),
            "median_pnl": median(pnls), "profitable_markets": sum(v>0 for v in pnls),
            "losing_markets": sum(v<0 for v in pnls), "best_market": max(pnls),
            "worst_market": min(pnls),
            "mean_time_avg_residual": mean(r["time_avg_residual"] for r in rs),
            "mean_final_residual": mean(r["final_residual"] for r in rs),
            "maximum_residual": max(r["max_residual"] for r in rs),
            "mean_signed_final_residual_to_winner": mean(r["signed_residual_toward_winner"] for r in rs),
            "mean_time_avg_signed_residual_to_winner": mean(r["time_avg_signed_residual_toward_winner"] for r in rs),
            "matched_shares": sum(r["matched_qty"] for r in rs),
            "unmatched_shares": sum(r["unmatched_shares"] for r in rs),
            "winning_residual_shares": sum(r["final_winning_residual"] for r in rs),
            "losing_residual_shares": sum(r["final_losing_residual"] for r in rs),
            "paired_contribution": round(sum(r["paired_contribution"] for r in rs), 6),
            "residual_contribution": round(sum(r["residual_contribution"] for r in rs), 6),
            "orders": sum(r["submitted_orders"] for r in rs),
            "fills": sum(r["conservative_fills"] for r in rs)}
    s = {x: stats(x) for x in ("B", "E")}
    ds = [r["delta_pnl"] for r in paired]
    positive = sorted((v for v in ds if v > 0), reverse=True)
    focus_key = "price_ge_0.90_elapsed_ge_210"
    focus_markets = focus[focus_key]["unique_markets"]
    target_markets = {r["market"] for r in intervals}
    target_fs = [f for f in convergence_fills if f["target_at_fill"] != 0]
    associated = [f for f in convergence_fills if f["target_associated"]]
    specific = [f for f in convergence_fills if f["target_specific_path_fill"]]
    # Zero observed reversals in the sampled region are not proof of zero risk.
    lower_win_one_sided_95 = 0.05 ** (1/focus_markets) if focus_markets else None
    summary = {"markets": len(universe), "classification": "RESIDUAL QUALITY IMPROVES BUT PNL NOT YET POSITIVE",
        "rule": "elapsed >= 210 s and leading token mid >= 0.90: target +5 YES or -5 NO; else zero",
        "surface": {"sample_clock": "one-second causal as-of BBO, both tokens valid, ties excluded",
            "market_first": "earliest qualifying one-second observation per market per bucket/region",
            "focus_regions": focus, "focus_ge_0.90_after_210_one_sided_95_win_rate_lower_bound": lower_win_one_sided_95},
        "latency": {"activation_us": activation_us, "cancel_us": cancel_us},
        "strategies": s,
        "exposure": {"markets_target_nonzero": len(target_markets),
            "target_time_us": sum(r["target_time_us"] for r in markets if r["strategy"] == "E"),
            "target_intervals": len(intervals), "fills_while_target_nonzero": len(target_fs),
            "target_intervals_with_carried_leader_residual": sum(i["carried_leader_residual"] for i in intervals),
            "max_leader_aligned_residual_during_target": max((i["peak_leader_residual"] for i in intervals), default=0),
            "shares_accumulated_toward_leader": QTY*sum(f["side"] == f["leader_at_fill"] for f in target_fs),
            "shares_accumulated_away_from_leader": QTY*sum(f["side"] != f["leader_at_fill"] for f in target_fs),
            "fills_while_target_on_winner_side": sum(f["side"] == f["eventual_winner"] for f in target_fs),
            "fills_while_target_on_loser_side": sum(f["side"] != f["eventual_winner"] for f in target_fs),
            "target_associated_fills": len(associated),
            "target_specific_path_fills": len(specific),
            "target_specific_correct": sum(f["convergence_correct"] for f in specific),
            "target_specific_wrong": sum(not f["convergence_correct"] for f in specific),
            "target_specific_expost_contribution": round(sum(f["expost_settlement_contribution"] for f in specific), 6)},
        "reversal": {"reversal_intervals": sum(i["leader_reversed"] for i in intervals),
            "reversal_markets": len({i["market"] for i in intervals if i["leader_reversed"]}),
            "carried_reversal_cases": len(reversal_cases),
            "carried_reversal_market_residual_contribution": round(sum(r["market_residual_settlement_contribution"] for r in reversal_cases),6)},
        "fill_overlap": {"shared": len(b_keys & e_keys), "B_only": len(b_keys-e_keys), "E_only": len(e_keys-b_keys)},
        "paired": {"delta_pnl": round(sum(ds), 6), "E_better": sum(v>0 for v in ds),
            "E_worse": sum(v<0 for v in ds), "ties": sum(v==0 for v in ds),
            "excluding_best": round(sum(ds)-max(ds), 6),
            "excluding_worst": round(sum(ds)-min(ds), 6),
            "best_delta": max(ds), "worst_delta": min(ds),
            "top_one_positive": positive[0] if positive else 0,
            "top_two_positive": round(sum(positive[:2]), 6)}}
    write_csv(OUT / "market_results.csv", markets)
    write_csv(OUT / "paired_markets.csv", paired)
    write_csv(OUT / "orders.csv", orders)
    write_csv(OUT / "fills.csv", fills)
    write_csv(OUT / "convergence_fills.csv", convergence_fills)
    write_csv(OUT / "target_intervals.csv", intervals)
    write_empty_capable(OUT / "reversal_cases.csv", reversal_cases,
        ["market", "start_us", "end_us", "leader", "leader_price", "target_inventory",
         "inventory_at_start", "residual_at_end", "carried_leader_residual", "peak_leader_residual", "winner",
         "leader_reversed", "reversal_timing_us", "market_residual_settlement_contribution", "market_pnl"])
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: summary[k] for k in ("strategies", "exposure", "reversal", "fill_overlap", "paired")},indent=2))
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--surface-only", action="store_true")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    universe = fixed_universe()
    obs, surface, focus = make_surface(universe)
    if args.surface_only:
        result = {"markets": len(universe), "observations": len(obs), "focus_regions": focus,
                  "late_surface": [r for r in surface if r["time_bucket"] in ("210–240", "240–270", "270–300")
                                   and r["price_bucket"] in ("0.8–0.9", "0.9–0.95", "0.95–1")]}
        (OUT / "surface_preview.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2))
        return
    evaluate(universe, surface, focus)


if __name__ == "__main__": main()
