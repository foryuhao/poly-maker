"""Read-only quote-competitiveness attribution for frozen B2 Reduce orders."""
from __future__ import annotations

import bisect
import csv
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median

from r0_rolling_pair_replay import Book, percentile, write_csv

ROOT = Path(__file__).resolve().parents[1]
B2 = ROOT / "research_outputs/cip_b2_20261003"
DATA = ROOT / "research_data/m4a"
AUDIT = ROOT / "research_outputs/execution_replay_audit_20261003/summary.json"
OUT = ROOT / "research_outputs/b2_reduce_quote_attribution_20261004"
BUCKETS = ("<=0 ticks", "1 tick", "2 ticks", "3-5 ticks", ">5 ticks")
CAPITAL = 25.0
CLIP = 5.0


def read_csv(path):
    with path.open(newline="") as stream:
        return list(csv.DictReader(stream))


def bucket(distance):
    if distance <= 0:
        return BUCKETS[0]
    if distance == 1:
        return BUCKETS[1]
    if distance == 2:
        return BUCKETS[2]
    if distance <= 5:
        return BUCKETS[3]
    return BUCKETS[4]


def weighted_quantile(segments, q):
    weighted = sorted((distance, seconds) for distance, seconds in segments if seconds > 0)
    total = sum(seconds for _, seconds in weighted)
    if not total:
        return None
    target = q * total
    elapsed = 0.0
    for distance, seconds in weighted:
        elapsed += seconds
        if elapsed >= target:
            return distance
    return weighted[-1][0]


def distance_of(books, side, price_int):
    if not all(book.valid() for book in books.values()):
        return None
    book = books[side]
    return round((book.bid - price_int) / book.tick_int)


def best_bid_of(books, side):
    book = books[side]
    return book.bid / 10000 if book.bid is not None else None


def stats(values):
    values = [x for x in values if x is not None]
    return {"mean": mean(values) if values else None,
            "median": median(values) if values else None,
            "p75": percentile(values, .75), "p90": percentile(values, .90),
            "n_defined": len(values)}


def market_cluster_bootstrap_gap(rows, markets, repetitions=5000):
    """95% interval for no-fill minus filled order-median distance, by market."""
    by_market = defaultdict(list)
    for row in rows:
        by_market[row["market"]].append(row)
    rng = random.Random(20261004)
    gaps = []
    for _ in range(repetitions):
        sampled = [row for _ in markets for row in by_market[rng.choice(markets)]]
        filled = [r["time_weighted_mean_distance_ticks"] for r in sampled
                  if r["outcome"] == "FILLED_REDUCE_ORDER" and r["time_weighted_mean_distance_ticks"] is not None]
        nofill = [r["time_weighted_mean_distance_ticks"] for r in sampled
                  if r["outcome"] == "NO_FILL_REDUCE_ORDER" and r["time_weighted_mean_distance_ticks"] is not None]
        if filled and nofill:
            gaps.append(median(nofill) - median(filled))
    return {"observed_no_fill_minus_filled_median_ticks":
            stats(r["time_weighted_mean_distance_ticks"] for r in rows
                  if r["outcome"] == "NO_FILL_REDUCE_ORDER")["median"] -
            stats(r["time_weighted_mean_distance_ticks"] for r in rows
                  if r["outcome"] == "FILLED_REDUCE_ORDER")["median"],
            "market_cluster_bootstrap_95pct_interval_ticks":
            [percentile(gaps, .025), percentile(gaps, .975)],
            "bootstrap_repetitions": repetitions, "valid_repetitions": len(gaps),
            "seed": 20261004}


class OrderMeasure:
    def __init__(self, raw, accept_action, fill):
        self.raw = raw
        self.market = raw["market"]
        self.order_id = raw["order_id"]
        self.side = raw["side"]
        self.price_int = int(raw["price_int"])
        self.price = float(raw["price"])
        self.accept = int(raw["accepted_us"])
        self.close = int(raw["closed_us"])
        self.activation = int(raw["activation_us"])
        self.fill = fill
        self.fill_time = int(fill["fill_available_us"]) if fill else None
        self.accept_action = accept_action
        self.event_distances = []
        self.segments = []
        self.seconds = Counter()
        self.valid_seconds = 0.0
        self.invalid_seconds = 0.0
        self.accept_bid = None
        self.accept_distance = None
        self.accept_observed = False
        self.fill_bid = None
        self.fill_distance = None
        self.fill_book_valid = None
        self.max_bid = None
        self.max_distance = None
        self.last_competitive_us = None
        self.candidate_by_price = {}

    def observe_accept(self, books):
        if self.accept_observed:
            return
        self.accept_bid = best_bid_of(books, self.side)
        self.accept_distance = distance_of(books, self.side, self.price_int)
        self.accept_observed = True

    def observe_fill(self, books):
        self.fill_bid = best_bid_of(books, self.side)
        self.fill_distance = distance_of(books, self.side, self.price_int)
        self.fill_book_valid = all(book.valid() for book in books.values())

    def integrate(self, lo, hi, books):
        start, end = max(lo, self.accept), min(hi, self.close)
        if end <= start:
            return
        if start == self.accept:
            self.observe_accept(books)
        seconds = (end - start) / 1_000_000
        distance = distance_of(books, self.side, self.price_int)
        if distance is None:
            self.invalid_seconds += seconds
            return
        self.valid_seconds += seconds
        self.segments.append((distance, seconds))
        self.seconds[bucket(distance)] += seconds
        if distance == 0:
            self.seconds["exactly_0"] += seconds
        if distance < 0:
            self.seconds["negative"] += seconds
        if distance <= 1:
            self.seconds["within_1"] += seconds
            self.last_competitive_us = end
        if distance <= 2:
            self.seconds["within_2"] += seconds
        bid = books[self.side].bid
        self.max_bid = bid if self.max_bid is None else max(self.max_bid, bid)
        self.max_distance = distance if self.max_distance is None else max(self.max_distance, distance)

    def observe_book_event(self, t, books):
        if not (self.accept <= t < self.close):
            return
        distance = distance_of(books, self.side, self.price_int)
        if distance is None:
            return
        self.event_distances.append(distance)
        bid = books[self.side].bid
        self.max_bid = bid if self.max_bid is None else max(self.max_bid, bid)
        self.max_distance = distance if self.max_distance is None else max(self.max_distance, distance)
        cancel_requested = int(self.raw["cancel_request_us"]) if self.raw["cancel_request_us"] else None
        if self.fill is None and bid > self.price_int and (cancel_requested is None or t < cancel_requested):
            # Earliest observation of a given quote dominates later observations
            # at that same price under the unchanged persistent maker fill rule.
            self.candidate_by_price.setdefault(bid, t)

    def row(self, end_us):
        event = self.event_distances
        valid = self.valid_seconds
        seconds = self.seconds
        return {
            "market": self.market, "order_id": self.order_id, "side": self.side,
            "outcome": "FILLED_REDUCE_ORDER" if self.fill else "NO_FILL_REDUCE_ORDER",
            "order_price": self.price, "submitted_us": int(self.raw["submitted_us"]),
            "accept_time_us": self.accept, "fill_time_us": self.fill_time,
            "close_time_us": self.close, "close_reason": self.raw["cancel_reason"],
            "resting_duration_s": (self.close - self.accept) / 1e6,
            "time_to_fill_s": (self.fill_time - self.accept) / 1e6 if self.fill else None,
            "best_bid_at_accept": self.accept_bid, "best_bid_at_fill": self.fill_bid,
            "distance_at_accept_ticks": self.accept_distance,
            "distance_immediately_before_fill_ticks": self.fill_distance,
            "book_valid_at_fill": self.fill_book_valid,
            "median_distance_before_fill_ticks": median(event) if self.fill and event else None,
            "median_distance_ticks": median(event) if event else None,
            "mean_distance_ticks": mean(event) if event else None,
            "p75_distance_ticks": percentile(event, .75),
            "p90_distance_ticks": percentile(event, .90),
            "max_distance_ticks": self.max_distance,
            "book_event_observations": len(event),
            "valid_book_seconds": valid, "invalid_book_seconds": self.invalid_seconds,
            "time_weighted_mean_distance_ticks": sum(d * s for d, s in self.segments) / valid if valid else None,
            "time_weighted_median_distance_ticks": weighted_quantile(self.segments, .5),
            "pct_time_at_best_bid": seconds["exactly_0"] / valid if valid else None,
            "pct_time_within_1_tick": seconds["within_1"] / valid if valid else None,
            "pct_time_within_2_ticks": seconds["within_2"] / valid if valid else None,
            "pct_time_more_than_2_ticks": (seconds[BUCKETS[3]] + seconds[BUCKETS[4]]) / valid if valid else None,
            "pct_time_more_than_5_ticks": seconds[BUCKETS[4]] / valid if valid else None,
            "seconds_above_best_bid": seconds["negative"],
            "seconds_at_0_ticks": seconds["exactly_0"],
            "seconds_at_1_tick": seconds[BUCKETS[1]],
            "seconds_at_2_ticks": seconds[BUCKETS[2]],
            "seconds_at_3_to_5_ticks": seconds[BUCKETS[3]],
            "seconds_above_5_ticks": seconds[BUCKETS[4]],
            "best_bid_drift_usd": self.max_bid / 10000 - self.price if self.max_bid is not None else None,
            "max_best_bid_improvement_over_order_usd": self.max_bid / 10000 - self.price if self.max_bid is not None else None,
            "did_best_bid_move_away": self.max_bid is not None and self.max_bid > self.price_int,
            "last_competitive_time_us": self.last_competitive_us,
            "time_from_last_competitive_to_close_s": (self.close - self.last_competitive_us) / 1e6 if self.last_competitive_us else None,
            "time_remaining_at_accept_s": (end_us - self.accept) / 1e6,
            "time_remaining_at_close_s": (end_us - self.close) / 1e6,
            "net_inventory_at_accept": float(self.accept_action["net_inventory"]),
            "abs_net_inventory_at_accept": abs(float(self.accept_action["net_inventory"])),
            "residual_at_accept": abs(float(self.accept_action["net_inventory"])),
            "capital_used_at_accept": float(self.accept_action["capital_used"]),
        }


def capital_at(t, market_orders, market_fills):
    filled_cost = sum(float(f["size"]) * float(f["price"])
                      for f in market_fills if int(f["fill_available_us"]) < t)
    reservation = sum(float(o["size"]) * float(o["price"]) for o in market_orders
                      if int(o["submitted_us"]) < t < int(o["closed_us"]))
    return filled_cost + reservation


def counterfactual(measure, market_orders, market_fills, sell_trades, submit_us, cancel_us):
    candidates = []
    for bid, observed_us in measure.candidate_by_price.items():
        # Cancel then submit at the observed best bid. This is an opportunity
        # screen, not a modified strategy or inventory replay.
        activation = max(observed_us + cancel_us, measure.activation) + submit_us
        if activation >= measure.close:
            continue
        baseline_capital = capital_at(observed_us, market_orders, market_fills)
        incremental_notional = CLIP * (bid - measure.price_int) / 10000
        feasible = baseline_capital + incremental_notional <= CAPITAL + 1e-8
        candidates.append((activation, bid, observed_us, feasible))
    candidates.sort()
    activations = [x[0] for x in candidates]
    prefix_raw, prefix_feasible = [], []
    best_raw = best_feasible = None
    for candidate in candidates:
        if best_raw is None or candidate[1] > best_raw[1]:
            best_raw = candidate
        if candidate[3] and (best_feasible is None or candidate[1] > best_feasible[1]):
            best_feasible = candidate
        prefix_raw.append(best_raw)
        prefix_feasible.append(best_feasible)
    raw = feasible_hit = None
    for trade in sell_trades:
        t, exch, recv, side, price_int, seq = trade
        if t >= measure.close:
            break
        if side != measure.side or min(t, exch, recv) <= measure.accept:
            continue
        idx = bisect.bisect_left(activations, min(t, exch, recv)) - 1
        if idx < 0:
            continue
        if raw is None and prefix_raw[idx] and price_int < prefix_raw[idx][1]:
            raw = (trade, prefix_raw[idx])
        if feasible_hit is None and prefix_feasible[idx] and price_int < prefix_feasible[idx][1]:
            feasible_hit = (trade, prefix_feasible[idx])
        if raw and feasible_hit:
            break
    chosen = feasible_hit
    return {
        "market": measure.market, "order_id": measure.order_id,
        "side": measure.side, "original_order_price": measure.price,
        "resting_duration_s": (measure.close - measure.accept) / 1e6,
        "candidate_best_bid_prices": len(measure.candidate_by_price),
        "eligible_candidates_after_latency": len(candidates),
        "capital_feasible_candidates": sum(x[3] for x in candidates),
        "raw_best_bid_fill_opportunity": raw is not None,
        "best_bid_requote_would_have_seen_fill_opportunity": chosen is not None,
        "first_hypothetical_fill_time_us": chosen[0][0] if chosen else None,
        "hypothetical_price": chosen[1][1] / 10000 if chosen else None,
        "hypothetical_observation_time_us": chosen[1][2] if chosen else None,
        "hypothetical_activation_us": chosen[1][0] if chosen else None,
        "hypothetical_trade_event_seq": chosen[0][5] if chosen else None,
        "hypothetical_time_to_fill_from_accept_s": (chosen[0][0] - measure.accept) / 1e6 if chosen else None,
        "hypothetical_time_from_quote_observation_to_fill_s": (chosen[0][0] - chosen[1][2]) / 1e6 if chosen else None,
        "capital_used_at_hypothetical_observation": capital_at(chosen[1][2], market_orders, market_fills) if chosen else None,
        "incremental_notional_at_hypothetical_observation": CLIP * (chosen[1][1] - measure.price_int) / 10000 if chosen else None,
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    summary_b2 = json.loads((B2 / "summary.json").read_text())
    market_rows = read_csv(B2 / "market_results.csv")
    if len(market_rows) != 32 or len({r["market"] for r in market_rows}) != 32:
        raise RuntimeError("B2 market coverage mismatch")
    if (summary_b2["metrics"]["residual_t150"] != 380 or
        summary_b2["metrics"]["reduce_fills"] != 27 or
        summary_b2["metrics"]["residual_final"] != 245):
        raise RuntimeError("B2 baseline changed")
    lat = json.loads(AUDIT.read_text())
    submit_us, cancel_us = int(lat["activation_latency_us"]), int(lat["cancel_latency_us"])
    all_orders, all_fills, actions = (read_csv(B2 / file) for file in
                                      ("orders.csv", "fills.csv", "action_audit.csv"))
    by_market_orders, by_market_fills = defaultdict(list), defaultdict(list)
    for order in all_orders:
        by_market_orders[order["market"]].append(order)
    for fill in all_fills:
        by_market_fills[fill["market"]].append(fill)
    accept_actions = {a["order_id"]: a for a in actions if a["action"] == "ACCEPT"}
    fill_by_order = {f["order_id"]: f for f in all_fills}
    measures = []
    for raw in all_orders:
        market = raw["market"]
        switch = (int(market.rsplit("-", 1)[1]) + 150) * 1_000_000
        if not raw["accepted_us"] or int(raw["submitted_us"]) < switch:
            continue
        action = accept_actions.get(raw["order_id"])
        if action is None or action["phase"] != "REDUCE_ONLY":
            raise RuntimeError(f"missing Reduce acceptance: {raw['order_id']}")
        net = float(action["net_inventory"])
        if net == 0 or (net > 0 and raw["side"] != "NO") or (net < 0 and raw["side"] != "YES"):
            raise RuntimeError(f"non-reducing order: {raw['order_id']}")
        measures.append(OrderMeasure(raw, action, fill_by_order.get(raw["order_id"])))
    if len(measures) != 113 or sum(m.fill is not None for m in measures) != 24:
        raise RuntimeError("B2 Reduce accepted/fill count mismatch")
    by_market_measures = defaultdict(list)
    for measure in measures:
        by_market_measures[measure.market].append(measure)
    counterfactual_rows = []
    errors = []
    event_counts = Counter()
    for market_row in market_rows:
        market = market_row["market"]
        end = (int(market.rsplit("-", 1)[1]) + 300) * 1_000_000
        start = end - 300_000_000
        manifest = json.loads((DATA / market_row["session"] / "manifest.json").read_text())
        if manifest["market_slug"] != market:
            raise RuntimeError(f"capture identity mismatch: {market}")
        token_map = {manifest["yes_token_id"]: "YES", manifest["no_token_id"]: "NO"}
        books = {side: Book() for side in ("YES", "NO")}
        active = by_market_measures[market]
        fills_at_seq = {int(m.fill["trade_event_seq"]): m for m in active if m.fill}
        sell_trades = []
        last_t = start
        with (DATA / market_row["session"] / "events.jsonl").open() as stream:
            for line in stream:
                event = json.loads(line)
                t = event.get("available_at_us")
                if t is None or t < start or t >= end:
                    continue
                if t < last_t:
                    event_counts["nonmonotone_skipped"] += 1
                    continue
                for measure in active:
                    measure.integrate(last_t, t, books)
                    if last_t <= measure.accept <= t:
                        measure.observe_accept(books)
                kind = event.get("kind")
                payload = event.get("payload") or {}
                side = token_map.get(payload.get("token_id"))
                seq = event.get("event_seq")
                if seq in fills_at_seq:
                    measure = fills_at_seq[seq]
                    if measure.fill_time != t or side != measure.side or kind != "TRADE":
                        errors.append(f"fill event mismatch {measure.order_id}")
                    measure.observe_fill(books)
                if kind == "TRADE" and side is not None and payload.get("side") == "SELL":
                    exch, recv = event.get("exchange_at_us"), event.get("received_at_us")
                    if exch is not None and recv is not None:
                        sell_trades.append((t, exch, recv, side,
                                            int(round(float(payload["price"]) * 10000)), seq))
                if side is not None and kind in ("BOOK", "REST_BOOK", "DELTA"):
                    books[side].update(kind, payload)
                elif side is not None and kind == "TICK":
                    books[side].tick_int = int(round(float(payload["tick_size"]) * 10000))
                if side is not None and kind in ("BOOK", "REST_BOOK", "DELTA", "TICK"):
                    event_counts["book_events"] += 1
                    for measure in active:
                        measure.observe_book_event(t, books)
                last_t = t
        for measure in active:
            measure.integrate(last_t, end, books)
            if not measure.accept_observed:
                errors.append(f"missing accept book observation {measure.order_id}")
            if measure.fill and measure.fill_book_valid is None:
                errors.append(f"missing fill book observation {measure.order_id}")
            if measure.fill is None:
                counterfactual_rows.append(counterfactual(
                    measure, by_market_orders[market], by_market_fills[market], sell_trades,
                    submit_us, cancel_us))
    rows = [m.row((int(m.market.rsplit("-", 1)[1]) + 300) * 1_000_000) for m in measures]
    filled = [r for r in rows if r["outcome"] == "FILLED_REDUCE_ORDER"]
    nofill = [r for r in rows if r["outcome"] == "NO_FILL_REDUCE_ORDER"]
    groups = {"FILLED_REDUCE_ORDER": filled, "NO_FILL_REDUCE_ORDER": nofill}
    compare = []
    for group, items in groups.items():
        valid_s = sum(r["valid_book_seconds"] for r in items)
        compare.append({
            "group": group, "order_count": len(items),
            "median_resting_time_s": median(r["resting_duration_s"] for r in items),
            "median_distance_at_accept_ticks": stats(r["distance_at_accept_ticks"] for r in items)["median"],
            "median_event_distance_ticks": stats(r["median_distance_ticks"] for r in items)["median"],
            "median_time_weighted_distance_ticks": stats(r["time_weighted_mean_distance_ticks"] for r in items)["median"],
            "pct_time_at_best_bid": sum(r["seconds_at_0_ticks"] for r in items) / valid_s if valid_s else None,
            "pct_time_within_1_tick": sum(r["valid_book_seconds"] * r["pct_time_within_1_tick"] for r in items if r["pct_time_within_1_tick"] is not None) / valid_s if valid_s else None,
            "pct_time_more_than_2_ticks": sum(r["seconds_at_3_to_5_ticks"] + r["seconds_above_5_ticks"] for r in items) / valid_s if valid_s else None,
            "pct_time_more_than_5_ticks": sum(r["seconds_above_5_ticks"] for r in items) / valid_s if valid_s else None,
            "valid_book_seconds": valid_s,
            "orders_with_valid_book_seconds": sum(r["valid_book_seconds"] > 0 for r in items),
        })
    bucket_rows = []
    for name in BUCKETS:
        seconds = sum(m.seconds[name] for m in measures)
        entered = sum(m.seconds[name] > 0 for m in measures)
        fills_in = sum(m.fill is not None and m.fill_distance is not None and
                       bucket(m.fill_distance) == name for m in measures)
        bucket_rows.append({"distance_bucket": name, "total_resting_seconds": seconds,
                            "number_of_orders_entering_bucket": entered,
                            "fills_occurring_while_in_bucket": fills_in,
                            "fills_per_100_order_minutes": 100 * fills_in / (seconds / 60) if seconds else None,
                            "fills_per_100_orders_entering_bucket": 100 * fills_in / entered if entered else None})
    market_summary = []
    for r in market_rows:
        group = [x for x in rows if x["market"] == r["market"]]
        distances = [x["time_weighted_mean_distance_ticks"] for x in group
                     if x["time_weighted_mean_distance_ticks"] is not None]
        total_valid = sum(x["valid_book_seconds"] for x in group)
        market_summary.append({"market": r["market"], "t150_residual": float(r["residual_t150"]),
                               "reduce_orders": len(group),
                               "filled_reduce_orders": sum(x["outcome"] == "FILLED_REDUCE_ORDER" for x in group),
                               "no_fill_reduce_orders": sum(x["outcome"] == "NO_FILL_REDUCE_ORDER" for x in group),
                               "mean_quote_distance_ticks_order_level": mean(distances) if distances else None,
                               "median_quote_distance_ticks_order_level": median(distances) if distances else None,
                               "seconds_at_best_bid": sum(x["seconds_at_0_ticks"] for x in group),
                               "seconds_more_than_2_ticks": sum(x["seconds_at_3_to_5_ticks"] + x["seconds_above_5_ticks"] for x in group),
                               "pct_valid_time_more_than_2_ticks": sum(x["seconds_at_3_to_5_ticks"] + x["seconds_above_5_ticks"] for x in group) / total_valid if total_valid else None,
                               "reduce_shares": float(r["reduce_shares_150_end"]),
                               "final_residual": float(r["residual_final"])})
    cf_hits = [r for r in counterfactual_rows if r["best_bid_requote_would_have_seen_fill_opportunity"]]
    raw_hits = [r for r in counterfactual_rows if r["raw_best_bid_fill_opportunity"]]
    inventory_context = []
    for label, cohort in (("abs_net_at_accept_le_10", [r for r in rows if r["abs_net_inventory_at_accept"] <= 10]),
                          ("abs_net_at_accept_gt_10", [r for r in rows if r["abs_net_inventory_at_accept"] > 10])):
        valid = sum(r["valid_book_seconds"] for r in cohort)
        inventory_context.append({
            "cohort": label, "orders": len(cohort),
            "no_fill_orders": sum(r["outcome"] == "NO_FILL_REDUCE_ORDER" for r in cohort),
            "no_fill_rate": sum(r["outcome"] == "NO_FILL_REDUCE_ORDER" for r in cohort) / len(cohort) if cohort else None,
            "pct_valid_time_more_than_2_ticks": sum(r["seconds_at_3_to_5_ticks"] + r["seconds_above_5_ticks"] for r in cohort) / valid if valid else None,
            "median_order_time_weighted_mean_distance_ticks": stats(r["time_weighted_mean_distance_ticks"] for r in cohort)["median"],
        })
    market_residual_context = []
    for label, cohort in (("final_residual_le_10", [r for r in market_summary if r["final_residual"] <= 10 and r["reduce_orders"]]),
                          ("final_residual_gt_10", [r for r in market_summary if r["final_residual"] > 10 and r["reduce_orders"]])):
        market_residual_context.append({"cohort": label, "markets_with_reduce_orders": len(cohort),
                                       "median_market_pct_valid_time_more_than_2_ticks": stats(r["pct_valid_time_more_than_2_ticks"] for r in cohort)["median"]})
    summary = {
        "classification": None, "sample": {"markets": len(market_rows), "accepted_new_reduce_orders": len(rows),
                                           "filled": len(filled), "no_fill": len(nofill),
                                           "b2_all_reduce_phase_fills": summary_b2["metrics"]["reduce_fills"]},
        "sources": {"b2_dir": str(B2.relative_to(ROOT)), "capture_dir": str(DATA.relative_to(ROOT))},
        "latencies_us": {"submit": submit_us, "cancel": cancel_us},
        "book_events_processed": event_counts["book_events"],
        "nonmonotone_events_skipped": event_counts["nonmonotone_skipped"],
        "errors": errors,
        "valid_book_seconds": sum(r["valid_book_seconds"] for r in rows),
        "invalid_book_seconds": sum(r["invalid_book_seconds"] for r in rows),
        "filled_vs_no_fill": compare,
        "distance_gap_uncertainty": market_cluster_bootstrap_gap(
            rows, [r["market"] for r in market_rows]),
        "distance_buckets": bucket_rows,
        "best_bid_drift_usd": {name: stats(r["best_bid_drift_usd"] for r in items)
                               for name, items in groups.items()},
        "fill_distance_ticks": stats(r["distance_immediately_before_fill_ticks"] for r in filled),
        "fills_with_invalid_book": sum(r["book_valid_at_fill"] is False for r in filled),
        "no_fill_orders_without_valid_book_seconds": sum(r["valid_book_seconds"] == 0 for r in nofill),
        "no_fill_more_than_2_seconds": sum(r["seconds_at_3_to_5_ticks"] + r["seconds_above_5_ticks"] for r in nofill),
        "no_fill_more_than_5_seconds": sum(r["seconds_above_5_ticks"] for r in nofill),
        "inventory_context": inventory_context,
        "market_residual_context": market_residual_context,
        "counterfactual": {"tested": len(counterfactual_rows), "raw_opportunities": len(raw_hits),
                           "capital_feasible_opportunities": len(cf_hits),
                           "capital_feasible_pct": len(cf_hits) / len(counterfactual_rows) if counterfactual_rows else None,
                           "median_time_from_observation_to_fill_s": stats(r["hypothetical_time_from_quote_observation_to_fill_s"] for r in cf_hits)["median"],
                           "median_time_from_accept_to_fill_s": stats(r["hypothetical_time_to_fill_from_accept_s"] for r in cf_hits)["median"]},
    }
    if errors or len(rows) != 113 or len(counterfactual_rows) != 89:
        classification = "ANALYSIS INVALID"
    elif sum(r["valid_book_seconds"] > 0 for r in rows) < .8 * len(rows):
        classification = "DATA INSUFFICIENT"
    else:
        filled_dist = compare[0]["median_time_weighted_distance_ticks"]
        nofill_dist = compare[1]["median_time_weighted_distance_ticks"]
        stale_gap = compare[1]["pct_time_more_than_2_ticks"] - compare[0]["pct_time_more_than_2_ticks"]
        opportunity = len(cf_hits) / len(counterfactual_rows)
        if (nofill_dist is not None and filled_dist is not None and nofill_dist >= filled_dist + 2
                and stale_gap >= .20 and opportunity >= .30):
            classification = "REDUCE QUOTES ARE TOO PASSIVE"
        elif (nofill_dist is not None and filled_dist is not None and
              abs(nofill_dist - filled_dist) < 1 and abs(stale_gap) < .10):
            classification = "QUOTE DISTANCE IS NOT PRIMARY BOTTLENECK"
        else:
            classification = "MIXED EXECUTION BOTTLENECK"
    summary["classification"] = classification
    write_csv(OUT / "reduce_orders.csv", rows)
    write_csv(OUT / "filled_vs_no_fill.csv", compare)
    write_csv(OUT / "distance_buckets.csv", bucket_rows)
    write_csv(OUT / "market_summary.csv", market_summary)
    write_csv(OUT / "best_bid_counterfactual.csv", counterfactual_rows)
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({"classification": classification, "sample": summary["sample"],
                      "filled_vs_no_fill": compare, "counterfactual": summary["counterfactual"],
                      "errors": errors}, indent=2))


if __name__ == "__main__":
    main()
