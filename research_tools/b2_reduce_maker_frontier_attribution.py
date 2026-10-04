"""Read-only maker-safe frontier opportunities on the frozen B2 Reduce path."""
from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median

from b2_reduce_quote_attribution import capital_at, counterfactual, read_csv, stats
from r0_rolling_pair_replay import Book, percentile, write_csv

ROOT = Path(__file__).resolve().parents[1]
B2 = ROOT / "research_outputs/cip_b2_20261003"
PRIOR = ROOT / "research_outputs/b2_reduce_quote_attribution_20261004"
DATA = ROOT / "research_data/m4a"
AUDIT = ROOT / "research_outputs/execution_replay_audit_20261003/summary.json"
OUT = ROOT / "research_outputs/b2_reduce_maker_frontier_attribution_20261004"
CLIP = 5.0
CAPITAL = 25.0


def book_state(books, side):
    if not all(book.valid() for book in books.values()):
        return (None, None, None, None)
    book = books[side]
    bid, ask, tick = book.bid, book.ask, book.tick_int
    frontier = ask - tick
    if not (tick > 0 and frontier > 0 and frontier >= bid and frontier < ask):
        return (bid, ask, tick, None)
    return (bid, ask, tick, frontier)


def opportunity_bucket(seconds_remaining):
    if seconds_remaining < 30:
        return "0-30s"
    if seconds_remaining < 60:
        return "30-60s"
    if seconds_remaining < 90:
        return "60-90s"
    return "90-150s"


class FrontierOrder:
    def __init__(self, raw, accept_action, fill):
        self.raw = raw
        self.market, self.order_id, self.side = raw["market"], raw["order_id"], raw["side"]
        self.price_int = int(raw["price_int"])
        self.price = float(raw["price"])
        self.accept = int(raw["accepted_us"])
        self.close = int(raw["closed_us"])
        self.activation = int(raw["activation_us"])
        self.cancel_request = int(raw["cancel_request_us"]) if raw["cancel_request_us"] else None
        self.fill = fill
        self.accept_action = accept_action
        self.timeline = []  # (timestamp, event_seq, BOOK/TRADE, state, optional trade)
        self.initialized = False
        self.bestbid_candidates = {}
        self.candidate_by_price = self.bestbid_candidates
        self.gap_segments = []
        self.accept_gap = None

    def initialize(self, state):
        if self.initialized:
            return
        self.timeline.append((self.accept, -1, "BOOK", state, None))
        self.initialized = True
        if state[3] is not None:
            self.accept_gap = round((state[3] - self.price_int) / state[2])

    def on_book(self, t, seq, state):
        if not (self.accept <= t < self.close):
            return
        if not self.initialized:
            self.initialize(state)
        if self.timeline[-1][3] != state:
            self.timeline.append((t, seq, "BOOK", state, None))
        if state[3] is not None and state[0] > self.price_int and \
                (self.cancel_request is None or t < self.cancel_request):
            self.bestbid_candidates.setdefault(state[0], t)

    def on_trade(self, event, state):
        t = event["available_at_us"]
        if not (self.accept <= t < self.close):
            return
        if not self.initialized:
            self.initialize(state)
        p = event["payload"]
        exch, recv = event.get("exchange_at_us"), event.get("received_at_us")
        if exch is None or recv is None:
            return
        trade = (t, exch, recv, self.side, int(round(float(p["price"]) * 10000)),
                 event.get("event_seq"))
        self.timeline.append((t, event.get("event_seq"), "TRADE", state, trade))

    def finish(self):
        if not self.initialized:
            return
        next_book = self.close
        next_times = [self.close] * len(self.timeline)
        for i in range(len(self.timeline) - 1, -1, -1):
            next_times[i] = next_book
            if self.timeline[i][2] == "BOOK":
                next_book = self.timeline[i][0]
        for i, item in enumerate(self.timeline):
            if item[2] != "BOOK":
                continue
            end = min(next_times[i], self.close)
            if end > item[0] and item[3][3] is not None:
                gap = round((item[3][3] - self.price_int) / item[3][2])
                self.gap_segments.append((gap, (end - item[0]) / 1e6))

    def gap_summary(self):
        seconds = sum(duration for _, duration in self.gap_segments)
        return {"frontier_gap_at_accept_ticks": self.accept_gap,
                "frontier_gap_time_weighted_mean_ticks":
                sum(gap * duration for gap, duration in self.gap_segments) / seconds if seconds else None,
                "frontier_gap_max_ticks": max((gap for gap, _ in self.gap_segments), default=None),
                "frontier_valid_seconds": seconds}


def feasible_price(t, quote_int, original_int, market_orders, market_fills):
    return capital_at(t, market_orders, market_fills) + \
        CLIP * (quote_int - original_int) / 10000 <= CAPITAL + 1e-8


def make_hit(order, trade, quote_int, observed_us, activation_us, state,
             market_orders, market_fills, placement_state=None):
    t, exch, recv, _, trade_int, seq = trade
    safe_state = placement_state or state
    assert safe_state[3] is not None and 0 < quote_int <= safe_state[3] < safe_state[1]
    assert trade_int < quote_int and min(t, exch, recv) > activation_us
    price = quote_int / 10000
    return {"market": order.market, "order_id": order.order_id, "side": order.side,
            "opportunity_time_us": t, "trade_event_seq": seq,
            "trade_price": trade_int / 10000,
            "quote_price": price, "quote_price_int": quote_int,
            "book_bid_at_opportunity": state[0] / 10000 if state[0] is not None else None,
            "book_ask_at_opportunity": state[1] / 10000 if state[1] is not None else None,
            "tick_at_opportunity": state[2] / 10000 if state[2] is not None else None,
            "book_valid_at_opportunity": state[3] is not None,
            "book_ask_at_maker_safe_placement": safe_state[1] / 10000,
            "observed_quote_time_us": observed_us, "hypothetical_activation_us": activation_us,
            "seconds_remaining_to_market_end":
            ((int(order.market.rsplit("-", 1)[1]) + 300) * 1_000_000 - t) / 1e6,
            "price_concession_usd_per_share": price - order.price,
            "price_concession_usd_per_clip": CLIP * (price - order.price),
            "original_path_capital_at_opportunity": capital_at(t, market_orders, market_fills),
            "capital_feasible_at_opportunity": feasible_price(
                t, quote_int, order.price_int, market_orders, market_fills)}


def instant_upper(order, market_orders, market_fills):
    raw = feasible = None
    working_quote = observed = placement_state = None
    for t_event, _, kind, state, trade in order.timeline:
        if kind == "BOOK" and state[3] is not None:
            working_quote = state[3]
            observed = t_event
            placement_state = state
        if kind != "TRADE" or working_quote is None:
            continue
        t, exch, recv, _, trade_px, _ = trade
        if min(t, exch, recv) <= order.accept or trade_px >= working_quote:
            continue
        hit = make_hit(order, trade, working_quote, observed, order.accept, state,
                       market_orders, market_fills, placement_state)
        if raw is None:
            raw = hit
        if feasible is None and hit["capital_feasible_at_opportunity"]:
            feasible = hit
            break
    return raw, feasible


def frontier_at_trade(order, trade_seq):
    latest_quote = None
    for _, _, kind, state, trade in order.timeline:
        if kind == "BOOK" and state[3] is not None:
            latest_quote = state[3]
        if kind == "TRADE" and trade[5] == trade_seq:
            return latest_quote, state, trade
    return None, None, None


def latency_observation(order, market_orders, market_fills, cancel_us, submit_us,
                        capital_filter, audit):
    if not order.timeline:
        return None
    status = "ACTIVE"
    quote = order.price_int
    activation = order.activation
    observed = order.accept
    due = None
    state = order.timeline[0][3]

    def quote_valid(candidate, current_state):
        return current_state[3] is not None and 0 < candidate <= current_state[3] < current_state[1]

    def cap_valid(at, candidate):
        return not capital_filter or feasible_price(
            at, candidate, order.price_int, market_orders, market_fills)

    def advance(t):
        nonlocal status, quote, activation, observed, due
        while due is not None and due <= t:
            at = due
            due = None
            if status == "CANCEL_PENDING":
                status = "NO_ORDER"
                if state[3] is not None and cap_valid(at, state[3]):
                    quote = state[3]
                    observed = at
                    activation = at + submit_us
                    status = "SUBMIT_PENDING"
                    due = activation
                    audit["submissions"] += 1
            elif status == "SUBMIT_PENDING":
                if quote_valid(quote, state) and cap_valid(at, quote):
                    status = "ACTIVE"
                    audit["activations"] += 1
                    if quote != state[3]:
                        status = "CANCEL_PENDING"
                        due = at + cancel_us
                        audit["cancel_requests"] += 1
                else:
                    status = "NO_ORDER"
                    audit["activation_rejections"] += 1

    for t, _, kind, new_state, trade in order.timeline:
        advance(t)
        if kind == "TRADE":
            audit["sell_prints_during_lifetime"] += 1
            if status not in ("ACTIVE", "CANCEL_PENDING") or state[3] is None:
                continue
            audit["sell_prints_with_active_order_and_valid_book"] += 1
            trade_t, exch, recv, _, trade_px, _ = trade
            if not (quote_valid(quote, state) and trade_px < quote and
                    min(trade_t, exch, recv) > activation):
                continue
            if not cap_valid(t, quote):
                continue
            audit["qualifying_active_maker_prints"] += 1
            return make_hit(order, trade, quote, observed, activation, state,
                            market_orders, market_fills)
        state = new_state
        if status == "ACTIVE" and state[3] != quote:
            status = "CANCEL_PENDING"
            due = t + cancel_us
            audit["cancel_requests"] += 1
        elif status == "NO_ORDER" and state[3] is not None and cap_valid(t, state[3]):
            quote = state[3]
            observed = t
            activation = t + submit_us
            status = "SUBMIT_PENDING"
            due = activation
            audit["submissions"] += 1
    return None


def inventory_projection(hit, market_fills):
    if hit is None:
        return None
    t = hit["opportunity_time_us"]
    qty = {"YES": 0.0, "NO": 0.0}
    cost = {"YES": 0.0, "NO": 0.0}
    for fill in market_fills:
        if int(fill["fill_available_us"]) >= t:
            continue
        side = fill["side"]
        size, price = float(fill["size"]), float(fill["price"])
        qty[side] += size
        cost[side] += size * price

    def economics():
        matched = min(qty.values())
        pair_cost = sum(cost[side] / qty[side] for side in ("YES", "NO")) \
            if all(qty.values()) else None
        pair_pnl = matched * (1 - pair_cost) if pair_cost is not None else 0.0
        return pair_cost, pair_pnl

    before_cost, before_pnl = economics()
    net_before = qty["YES"] - qty["NO"]
    side = hit["side"]
    qty[side] += CLIP
    cost[side] += CLIP * hit["quote_price"]
    after_cost, after_pnl = economics()
    return {"net_inventory_before_print": net_before,
            "risk_reducing_at_print": (net_before > 0 and side == "NO") or
            (net_before < 0 and side == "YES"),
            "projected_pool_pair_cost_before": before_cost,
            "projected_pool_pair_cost_after_fill": after_cost,
            "projected_pool_pair_cost_change": after_cost - before_cost
            if before_cost is not None and after_cost is not None else None,
            "projected_pair_pnl_before": before_pnl,
            "projected_pair_pnl_after_fill": after_pnl,
            "projected_pair_pnl_change": after_pnl - before_pnl}


def hit_columns(prefix, hit):
    if hit is None:
        return {f"{prefix}_{name}": None for name in (
            "time_us", "price", "concession_usd_per_share", "concession_usd_per_clip",
            "seconds_remaining_to_market_end", "projected_pool_pair_cost_after_fill",
            "projected_pool_pair_cost_change", "projected_pair_pnl_change",
            "risk_reducing_at_print")}
    return {
        f"{prefix}_time_us": hit["opportunity_time_us"],
        f"{prefix}_price": hit["quote_price"],
        f"{prefix}_concession_usd_per_share": hit["price_concession_usd_per_share"],
        f"{prefix}_concession_usd_per_clip": hit["price_concession_usd_per_clip"],
        f"{prefix}_seconds_remaining_to_market_end": hit["seconds_remaining_to_market_end"],
        f"{prefix}_projected_pool_pair_cost_after_fill": hit["projected_pool_pair_cost_after_fill"],
        f"{prefix}_projected_pool_pair_cost_change": hit["projected_pool_pair_cost_change"],
        f"{prefix}_projected_pair_pnl_change": hit["projected_pair_pnl_change"],
        f"{prefix}_risk_reducing_at_print": hit["risk_reducing_at_print"],
    }


def layer_summary(name, raw_hits, feasible_hits, denominator):
    n_raw, n_cap = sum(x is not None for x in raw_hits), sum(x is not None for x in feasible_hits)
    return {"execution_rule": name, "opportunity_orders": n_raw,
            "opportunity_pct_of_89": n_raw / denominator,
            "capital_valid_orders": n_cap, "capital_valid_pct_of_89": n_cap / denominator,
            "gross_capital_valid_shares_upper_bound": CLIP * n_cap}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    b2_summary = json.loads((B2 / "summary.json").read_text())
    prior_summary = json.loads((PRIOR / "summary.json").read_text())
    markets = read_csv(B2 / "market_results.csv")
    b2_orders, b2_fills, actions = (read_csv(B2 / name) for name in
                                    ("orders.csv", "fills.csv", "action_audit.csv"))
    previous_bestbid = {r["order_id"]: r for r in read_csv(PRIOR / "best_bid_counterfactual.csv")}
    if (len(markets) != 32 or len({r["market"] for r in markets}) != 32 or
        b2_summary["metrics"]["residual_t150"] != 380 or
        b2_summary["metrics"]["residual_final"] != 245 or
        b2_summary["metrics"]["reduce_fills"] != 27 or
        prior_summary["counterfactual"]["raw_opportunities"] != 16 or
        prior_summary["counterfactual"]["capital_feasible_opportunities"] != 14):
        raise RuntimeError("frozen B2 or best-bid comparator mismatch")
    lat = json.loads(AUDIT.read_text())
    submit_us, cancel_us = int(lat["activation_latency_us"]), int(lat["cancel_latency_us"])
    accepted = {a["order_id"]: a for a in actions if a["action"] == "ACCEPT"}
    fill_by_order = {f["order_id"]: f for f in b2_fills}
    by_market_orders, by_market_fills = defaultdict(list), defaultdict(list)
    for row in b2_orders:
        by_market_orders[row["market"]].append(row)
    for row in b2_fills:
        by_market_fills[row["market"]].append(row)
    all_measures = []
    for raw in b2_orders:
        switch = (int(raw["market"].rsplit("-", 1)[1]) + 150) * 1_000_000
        if not raw["accepted_us"] or int(raw["submitted_us"]) < switch:
            continue
        action = accepted[raw["order_id"]]
        net = float(action["net_inventory"])
        if action["phase"] != "REDUCE_ONLY" or net == 0 or \
                (net > 0 and raw["side"] != "NO") or (net < 0 and raw["side"] != "YES"):
            raise RuntimeError(f"non-reducing cohort member {raw['order_id']}")
        all_measures.append(FrontierOrder(raw, action, fill_by_order.get(raw["order_id"])))
    nofill = [m for m in all_measures if m.fill is None]
    filled = [m for m in all_measures if m.fill is not None]
    if len(all_measures) != 113 or len(nofill) != 89 or len(filled) != 24 or \
            {m.order_id for m in nofill} != set(previous_bestbid):
        raise RuntimeError("113/24/89 cohort or best-bid order identity mismatch")
    by_market = defaultdict(list)
    for m in all_measures:
        by_market[m.market].append(m)
    market_sells = defaultdict(list)
    book_events = Counter()
    for market_row in markets:
        market = market_row["market"]
        start = int(market.rsplit("-", 1)[1]) * 1_000_000
        end = start + 300_000_000
        path = DATA / market_row["session"]
        manifest = json.loads((path / "manifest.json").read_text())
        if manifest["market_slug"] != market:
            raise RuntimeError(f"capture identity mismatch: {market}")
        token_map = {manifest["yes_token_id"]: "YES", manifest["no_token_id"]: "NO"}
        books = {s: Book() for s in ("YES", "NO")}
        active = by_market[market]
        last_t = start
        with (path / "events.jsonl").open() as stream:
            for line in stream:
                event = json.loads(line)
                t = event.get("available_at_us")
                if t is None or t < start or t >= end:
                    continue
                if t < last_t:
                    book_events["nonmonotone_skipped"] += 1
                    continue
                for m in active:
                    if not m.initialized and last_t <= m.accept <= t and m.accept < m.close:
                        m.initialize(book_state(books, m.side))
                payload = event.get("payload") or {}
                side, kind = token_map.get(payload.get("token_id")), event.get("kind")
                if kind == "TRADE" and side is not None and payload.get("side") == "SELL":
                    exch, recv = event.get("exchange_at_us"), event.get("received_at_us")
                    if exch is not None and recv is not None:
                        market_sells[market].append((t, exch, recv, side,
                            int(round(float(payload["price"]) * 10000)), event.get("event_seq")))
                        for m in active:
                            if m.side == side:
                                m.on_trade(event, book_state(books, side))
                if side is not None and kind in ("BOOK", "REST_BOOK", "DELTA"):
                    books[side].update(kind, payload)
                elif side is not None and kind == "TICK":
                    books[side].tick_int = int(round(float(payload["tick_size"]) * 10000))
                if side is not None and kind in ("BOOK", "REST_BOOK", "DELTA", "TICK"):
                    book_events["book_events"] += 1
                    for m in active:
                        m.on_book(t, event.get("event_seq"), book_state(books, m.side))
                last_t = t
        for m in active:
            m.finish()

    rows, latency_rows, concessions = [], [], []
    hits_by_layer = defaultdict(dict)
    original_qualifying = []
    for m in all_measures:
        orders, fills = by_market_orders[m.market], by_market_fills[m.market]
        if m.fill:
            rows.append({"market": m.market, "order_id": m.order_id, "side": m.side,
                         "outcome": "FILLED_REDUCE_ORDER", "original_price": m.price,
                         "accept_time_us": m.accept, "close_time_us": m.close,
                         "net_at_accept": float(m.accept_action["net_inventory"]),
                         "abs_net_at_accept": abs(float(m.accept_action["net_inventory"])),
                         **m.gap_summary()})
            continue
        original = any(kind == "TRADE" and trade is not None and
                       min(trade[0], trade[1], trade[2]) > m.activation and
                       trade[4] < m.price_int for _, _, kind, _, trade in m.timeline)
        original_qualifying.append(original)
        best = counterfactual(m, orders, fills, market_sells[m.market], submit_us, cancel_us)
        prior = previous_bestbid[m.order_id]
        if ((str(best["raw_best_bid_fill_opportunity"]) != prior["raw_best_bid_fill_opportunity"]) or
            (str(best["best_bid_requote_would_have_seen_fill_opportunity"]) !=
             prior["best_bid_requote_would_have_seen_fill_opportunity"])):
            raise RuntimeError(f"best-bid opportunity mismatch {m.order_id}")
        best_raw = best_cap = None
        if best["raw_best_bid_fill_opportunity"]:
            # The prior helper records only capital-valid first-hit details. For
            # raw-only best-bid orders, the boolean is retained without invented
            # price/economics metadata.
            best_raw = True
        if best["best_bid_requote_would_have_seen_fill_opportunity"]:
            best_cap = {"market": m.market, "order_id": m.order_id,
                        "side": m.side, "opportunity_time_us": best["first_hypothetical_fill_time_us"],
                        "quote_price": best["hypothetical_price"],
                        "price_concession_usd_per_share": best["hypothetical_price"] - m.price,
                        "price_concession_usd_per_clip": CLIP * (best["hypothetical_price"] - m.price),
                        "seconds_remaining_to_market_end":
                        ((int(m.market.rsplit("-", 1)[1]) + 300) * 1_000_000 -
                         best["first_hypothetical_fill_time_us"]) / 1e6}
            best_cap.update(inventory_projection(best_cap, fills))
        best_frontier_quote = best_trade_price = None
        best_frontier_book_valid = None
        if best_cap:
            q, trade_state, trade = frontier_at_trade(m, best["hypothetical_trade_event_seq"])
            if trade is None:
                raise RuntimeError(f"missing prior best-bid print {m.order_id}")
            best_frontier_quote = q / 10000 if q is not None else None
            best_trade_price = trade[4] / 10000
            best_frontier_book_valid = trade_state[3] is not None
        upper_raw, upper_cap = instant_upper(m, orders, fills)
        raw_audit, cap_audit = Counter(), Counter()
        latency_raw = latency_observation(m, orders, fills, cancel_us, submit_us, False, raw_audit)
        latency_cap = latency_observation(m, orders, fills, cancel_us, submit_us, True, cap_audit)
        for hit in (upper_raw, upper_cap, latency_raw, latency_cap):
            if hit is not None:
                hit.update(inventory_projection(hit, fills))
        for layer, hit in (("BEST_BID", best_cap), ("FRONTIER_UPPER_BOUND", upper_cap),
                           ("FRONTIER_LATENCY", latency_cap)):
            if hit is not None:
                hits_by_layer[layer][m.order_id] = hit
                concessions.append({"layer": layer, "market": m.market,
                                    "order_id": m.order_id, "side": m.side,
                                    "original_price": m.price, **hit})
        row = {"market": m.market, "order_id": m.order_id, "side": m.side,
               "outcome": "NO_FILL_REDUCE_ORDER", "original_price": m.price,
               "accept_time_us": m.accept, "close_time_us": m.close,
               "net_at_accept": float(m.accept_action["net_inventory"]),
               "abs_net_at_accept": abs(float(m.accept_action["net_inventory"])),
               "original_b2_qualifying_print": original,
               "best_bid_opportunity": bool(best_raw),
               "best_bid_opportunity_with_capital": best_cap is not None,
               "frontier_upper_bound_opportunity": upper_raw is not None,
               "frontier_upper_bound_with_capital": upper_cap is not None,
               "frontier_latency_opportunity": latency_raw is not None,
               "frontier_latency_with_capital": latency_cap is not None,
               "first_best_bid_opportunity_time_us": best_cap["opportunity_time_us"] if best_cap else None,
               "first_frontier_upper_bound_time_us": upper_raw["opportunity_time_us"] if upper_raw else None,
               "first_frontier_latency_time_us": latency_raw["opportunity_time_us"] if latency_raw else None,
               "best_bid_hypothetical_price": best_cap["quote_price"] if best_cap else None,
               "frontier_quote_at_best_bid_print": best_frontier_quote,
               "trade_price_at_best_bid_print": best_trade_price,
               "book_valid_at_best_bid_print": best_frontier_book_valid,
               "frontier_hypothetical_price": upper_cap["quote_price"] if upper_cap else None,
               "time_saved_vs_original_s": None,
               **m.gap_summary(),
               **hit_columns("best_bid_capital", best_cap),
               **hit_columns("frontier_upper_capital", upper_cap),
               **hit_columns("frontier_latency_capital", latency_cap)}
        rows.append(row)
        latency_rows.append({"market": m.market, "order_id": m.order_id,
                             "frontier_upper_raw": upper_raw is not None,
                             "frontier_upper_capital": upper_cap is not None,
                             "frontier_latency_raw": latency_raw is not None,
                             "frontier_latency_capital": latency_cap is not None,
                             "upper_raw_time_us": upper_raw["opportunity_time_us"] if upper_raw else None,
                             "upper_cap_time_us": upper_cap["opportunity_time_us"] if upper_cap else None,
                             "latency_raw_time_us": latency_raw["opportunity_time_us"] if latency_raw else None,
                             "latency_cap_time_us": latency_cap["opportunity_time_us"] if latency_cap else None,
                             "latency_cap_quote_price": latency_cap["quote_price"] if latency_cap else None,
                             **{f"raw_{key}": raw_audit[key] for key in (
                                 "cancel_requests", "submissions", "activations",
                                 "activation_rejections", "sell_prints_during_lifetime",
                                 "sell_prints_with_active_order_and_valid_book",
                                 "qualifying_active_maker_prints")},
                             **{f"capital_{key}": cap_audit[key] for key in (
                                 "cancel_requests", "submissions", "activations",
                                 "activation_rejections", "sell_prints_during_lifetime",
                                 "sell_prints_with_active_order_and_valid_book",
                                 "qualifying_active_maker_prints")}})

    nofill_rows = [r for r in rows if r["outcome"] == "NO_FILL_REDUCE_ORDER"]
    ladder = [layer_summary("ORIGINAL_B2_PRICE", [None] * 89, [None] * 89, 89),
              layer_summary("CURRENT_BEST_BID", [r["best_bid_opportunity"] or None for r in nofill_rows],
                            [r["best_bid_opportunity_with_capital"] or None for r in nofill_rows], 89),
              layer_summary("MAKER_SAFE_FRONTIER_UPPER_BOUND",
                            [r["frontier_upper_bound_opportunity"] or None for r in nofill_rows],
                            [r["frontier_upper_bound_with_capital"] or None for r in nofill_rows], 89),
              layer_summary("MAKER_SAFE_FRONTIER_LATENCY",
                            [r["frontier_latency_opportunity"] or None for r in nofill_rows],
                            [r["frontier_latency_with_capital"] or None for r in nofill_rows], 89)]
    market_dedup = []
    for mr in markets:
        market = mr["market"]
        t150, final = float(mr["residual_t150"]), float(mr["residual_final"])
        result = {"market": market, "t150_residual": t150, "b2_final_residual": final}
        for layer in ("BEST_BID", "FRONTIER_UPPER_BOUND", "FRONTIER_LATENCY"):
            hits = sorted((h for h in hits_by_layer[layer].values() if h["market"] == market),
                          key=lambda h: (h["opportunity_time_us"], h["order_id"]))
            remaining = t150
            for hit in hits:
                remaining -= min(CLIP, remaining)
            dedup = t150 - remaining
            result[f"{layer.lower()}_orders"] = len(hits)
            result[f"{layer.lower()}_gross_shares"] = CLIP * len(hits)
            result[f"{layer.lower()}_market_dedup_upper_bound"] = dedup
            result[f"{layer.lower()}_final_residual_capped_upper_bound"] = min(dedup, final)
        market_dedup.append(result)
    price_stats = {}
    economics_stats = {}
    time_distribution = {}
    for layer in ("BEST_BID", "FRONTIER_UPPER_BOUND", "FRONTIER_LATENCY"):
        hits = list(hits_by_layer[layer].values())
        price_stats[layer] = {
            "per_share_usd": stats(h["price_concession_usd_per_share"] for h in hits),
            "per_5_share_clip_usd": stats(h["price_concession_usd_per_clip"] for h in hits)}
        economics_stats[layer] = {
            "projected_pair_pnl_change_usd": stats(h["projected_pair_pnl_change"] for h in hits),
            "projected_pool_pair_cost_change": stats(h["projected_pool_pair_cost_change"] for h in hits),
            "risk_reducing_at_print": sum(h["risk_reducing_at_print"] for h in hits),
            "orders": len(hits)}
        time_distribution[layer] = dict(Counter(opportunity_bucket(h["seconds_remaining_to_market_end"])
                                                for h in hits))
    best_set = set(hits_by_layer["BEST_BID"])
    upper_set = set(hits_by_layer["FRONTIER_UPPER_BOUND"])
    latency_set = set(hits_by_layer["FRONTIER_LATENCY"])
    incremental = {
        "upper_capital_count_minus_best_bid_capital_count": len(upper_set) - len(best_set),
        "upper_incremental_pct_of_89": (len(upper_set) - len(best_set)) / 89,
        "upper_new_order_ids_vs_best_bid": sorted(upper_set - best_set),
        "upper_best_bid_ids_not_recovered": sorted(best_set - upper_set),
        "latency_capital_count_minus_best_bid_capital_count": len(latency_set) - len(best_set),
        "latency_incremental_pct_of_89": (len(latency_set) - len(best_set)) / 89,
        "latency_new_order_ids_vs_best_bid": sorted(latency_set - best_set),
        "latency_best_bid_ids_not_recovered": sorted(best_set - latency_set),
        "best_bid_capital_hits_lost_after_frontier_down_move": sum(
            r["best_bid_opportunity_with_capital"] and not r["frontier_upper_bound_opportunity"]
            and r["frontier_quote_at_best_bid_print"] is not None
            and r["trade_price_at_best_bid_print"] >= r["frontier_quote_at_best_bid_print"]
            for r in nofill_rows),
    }
    errors = []
    if any(original_qualifying):
        errors.append("original B2 no-fill has qualifying active print")
    if sum(r["best_bid_opportunity"] for r in nofill_rows) != 16 or \
            sum(r["best_bid_opportunity_with_capital"] for r in nofill_rows) != 14:
        errors.append("best-bid 16/14 mismatch")
    if any(h["quote_price_int"] >= round(h["book_ask_at_maker_safe_placement"] * 10000)
           for layer in ("FRONTIER_UPPER_BOUND", "FRONTIER_LATENCY")
           for h in hits_by_layer[layer].values()):
        errors.append("taker quote")
    if any(r["frontier_latency_opportunity"] and not r["frontier_upper_bound_opportunity"]
           for r in nofill_rows):
        errors.append("latency opportunity not contained in instant upper bound")
    if len(rows) != 113 or len(nofill_rows) != 89 or len(market_dedup) != 32:
        errors.append("coverage mismatch")
    gross_upper = CLIP * len(upper_set)
    dedup_t150 = sum(r["frontier_upper_bound_market_dedup_upper_bound"] for r in market_dedup)
    capped_final = sum(r["frontier_upper_bound_final_residual_capped_upper_bound"] for r in market_dedup)
    if errors:
        classification = "ANALYSIS INVALID"
    elif sum(m.gap_summary()["frontier_valid_seconds"] > 0 for m in nofill) < .8 * 89:
        classification = "DATA INSUFFICIENT"
    elif len(upper_set) - len(best_set) <= 5:
        classification = "BEST BID IS NEAR MAKER EXECUTION FRONTIER"
    elif capped_final < 65:
        classification = "MAKER-ONLY EXECUTION IS STRUCTURALLY INSUFFICIENT"
    elif capped_final < 180:
        classification = "FRONTIER HELPS BUT CANNOT SOLVE RESIDUAL"
    elif (price_stats["FRONTIER_LATENCY"]["per_share_usd"]["median"] is not None and
          price_stats["FRONTIER_LATENCY"]["per_share_usd"]["median"] > .10 and
          economics_stats["FRONTIER_LATENCY"]["projected_pair_pnl_change_usd"]["median"] < 0):
        classification = "PRICE CONCESSION DESTROYS GAMMA EDGE"
    elif len(latency_set) - len(best_set) >= 20:
        classification = "AGGRESSIVE MAKER FRONTIER HAS MATERIAL HEADROOM"
    else:
        classification = "FRONTIER HELPS BUT CANNOT SOLVE RESIDUAL"
    gap_comparison = {
        "filled": stats(m.gap_summary()["frontier_gap_time_weighted_mean_ticks"] for m in filled),
        "no_fill": stats(m.gap_summary()["frontier_gap_time_weighted_mean_ticks"] for m in nofill)}
    latency_audit = {key: sum(row[key] for row in latency_rows)
                     for key in latency_rows[0] if key.startswith(("raw_", "capital_"))}
    summary = {"classification": classification,
               "sample": {"markets": len(markets), "accepted_reduce_orders": len(all_measures),
                          "filled_reference_orders": len(filled), "no_fill_orders": len(nofill),
                          "b2_final_residual_shares": 245,
                          "book_events": book_events["book_events"],
                          "nonmonotone_skipped": book_events["nonmonotone_skipped"]},
               "frozen_latency_us": {"cancel": cancel_us, "submit": submit_us},
               "opportunity_ladder": ladder,
               "incremental_frontier_vs_best_bid": incremental,
               "residual_upper_bound": {"frontier_upper_gross_shares": gross_upper,
                                        "frontier_upper_market_dedup_t150_shares": dedup_t150,
                                        "frontier_upper_final_residual_capped_shares": capped_final,
                                        "frontier_latency_gross_shares": CLIP * len(latency_set),
                                        "frontier_latency_market_dedup_t150_shares": sum(r["frontier_latency_market_dedup_upper_bound"] for r in market_dedup),
                                        "frontier_latency_final_residual_capped_shares": sum(r["frontier_latency_final_residual_capped_upper_bound"] for r in market_dedup)},
               "price_concession": price_stats,
               "local_pool_economics": economics_stats,
               "time_remaining_distribution": time_distribution,
               "frontier_gap_ticks": gap_comparison,
               "latency_sequence_audit": latency_audit,
               "errors": errors,
               "interpretation": "Historical maker-only print opportunities on unchanged B2 paths; not simulated fills or additive strategy PnL."}
    write_csv(OUT / "order_frontier_analysis.csv", rows,
              fields=list(dict.fromkeys(key for row in rows for key in row)))
    write_csv(OUT / "opportunity_ladder.csv", ladder)
    write_csv(OUT / "latency_frontier.csv", latency_rows)
    write_csv(OUT / "market_dedup_upper_bound.csv", market_dedup)
    write_csv(OUT / "price_concession.csv", concessions,
              fields=list(dict.fromkeys(key for row in concessions for key in row)))
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({"classification": classification, "sample": summary["sample"],
                      "opportunity_ladder": ladder, "residual_upper_bound": summary["residual_upper_bound"],
                      "errors": errors}, indent=2))


if __name__ == "__main__":
    main()
