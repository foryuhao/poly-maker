"""Frozen R0 rolling BUY/BUY pair replay over the audited 32-market universe.

Run from repository root with Python 3.12:
    python3 research_tools/r0_rolling_pair_replay.py

Replay inputs are read-only M4A event captures and the saved execution-replay
audit. Settlement labels are read from the official Polymarket Gamma API only.
No production modules are imported or modified.
"""
from __future__ import annotations

import csv
import json
import math
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "research_data/m4a"
AUDIT_DIR = ROOT / "research_outputs/execution_replay_audit_20261003"
M4A_AUDIT = ROOT / "research_audits/research_data/m4a/audit.json"
OUT = ROOT / "research_outputs/r0_rolling_pair_replay_20261003"

# Frozen R0 experiment settings.
CAPITAL = 25.0
CLIP = 5.0
PAIR_CAP = 0.98
TARGET_PAIRS = 5
# Source-repository quote-construction defaults; not tuned for this replay.
SOURCE_MIN_CHEAP_BID = 0.30
SOURCE_MAX_CHEAP_BID = 0.69
SOURCE_CUT_LOSS_S = 60


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    vals = sorted(values)
    z = (len(vals) - 1) * q
    lo = int(z)
    return vals[lo] + (vals[min(lo + 1, len(vals) - 1)] - vals[lo]) * (z - lo)


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    if fields is None:
        fields = list(rows[0]) if rows else []
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def gamma_winner(slug: str, manifest: dict) -> tuple[str, dict]:
    # Once fetched, preserve the official identity-checked labels with the
    # research outputs so a replay rerun does not depend on network availability.
    labels_path = OUT / "settlement_labels.csv"
    if labels_path.exists():
        with labels_path.open() as f:
            cached = [row for row in csv.DictReader(f) if row["market"] == slug]
        if len(cached) == 1:
            row = cached[0]
            if (row["condition_id"].lower() != str(manifest["condition_id"]).lower()
                    or row["yes_token_id"] != manifest["yes_token_id"]
                    or row["no_token_id"] != manifest["no_token_id"]):
                raise RuntimeError(f"Cached settlement identity mismatch for {slug}")
            return row["winner"], dict(row)
        if cached:
            raise RuntimeError(f"Duplicate cached settlement labels for {slug}")
    query = urllib.parse.urlencode({"slug": slug})
    url = f"https://gamma-api.polymarket.com/events?{query}"
    request = urllib.request.Request(url, headers={"User-Agent": "R0-research-replay/1.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        events = json.loads(response.read())
    if len(events) != 1:
        raise RuntimeError(f"Gamma event lookup returned {len(events)} rows for {slug}")
    markets = events[0].get("markets") or []
    matched = [m for m in markets if m.get("slug") == slug]
    if len(matched) != 1:
        raise RuntimeError(f"Gamma market lookup returned {len(matched)} rows for {slug}")
    market = matched[0]
    if str(market.get("conditionId", "")).lower() != str(manifest["condition_id"]).lower():
        raise RuntimeError(f"Gamma condition identity mismatch for {slug}")
    tokens = json.loads(market.get("clobTokenIds") or "[]")
    if tokens != [manifest["yes_token_id"], manifest["no_token_id"]]:
        raise RuntimeError(f"Gamma token identity mismatch for {slug}")
    outcomes = json.loads(market.get("outcomes") or "[]")
    prices = json.loads(market.get("outcomePrices") or "[]")
    if not market.get("closed") or market.get("umaResolutionStatus") != "resolved":
        raise RuntimeError(f"Gamma market is not officially resolved for {slug}")
    if outcomes != ["Up", "Down"] or prices not in (["1", "0"], ["0", "1"]):
        raise RuntimeError(f"Unclear Gamma settlement identity/outcomes for {slug}: {outcomes}/{prices}")
    winner = "YES" if prices == ["1", "0"] else "NO"
    return winner, {"url": url, "condition_id": market["conditionId"],
                    "yes_token_id": tokens[0], "no_token_id": tokens[1],
                    "outcomes": outcomes, "outcome_prices": prices,
                    "resolution_status": market.get("umaResolutionStatus"),
                    "closed_time": market.get("closedTime"), "winner": winner}


class Book:
    def __init__(self) -> None:
        self.bids: dict[int, float] = {}
        self.asks: dict[int, float] = {}
        self.bid: int | None = None
        self.ask: int | None = None
        self.tick_int = 100  # $0.01 in 1e-4 units, replaced by capture tick data.
        self.seen = False

    def update(self, kind: str, payload: dict) -> bool:
        before = (self.bid, self.ask, self.tick_int)
        if kind in ("BOOK", "REST_BOOK"):
            self.bids = {int(round(float(p) * 10000)): float(s)
                         for p, s in payload.get("bids", []) if float(s) > 0}
            self.asks = {int(round(float(p) * 10000)): float(s)
                         for p, s in payload.get("asks", []) if float(s) > 0}
            self.bid, self.ask = max(self.bids, default=None), min(self.asks, default=None)
            tick = payload.get("tick_size")
            if tick:
                self.tick_int = int(round(float(tick) * 10000))
            self.seen = True
        else:
            side = payload.get("side")
            if side not in ("BUY", "SELL"):
                return False
            levels = self.bids if side == "BUY" else self.asks
            px, size = int(round(float(payload["price"]) * 10000)), float(payload["size"])
            if size > 0:
                levels[px] = size
            else:
                levels.pop(px, None)
            if side == "BUY" and (self.bid is None or px >= self.bid or self.bid not in levels):
                self.bid = max(levels, default=None)
            if side == "SELL" and (self.ask is None or px <= self.ask or self.ask not in levels):
                self.ask = min(levels, default=None)
        return before != (self.bid, self.ask, self.tick_int)

    def valid(self) -> bool:
        return (self.seen and self.bid is not None and self.ask is not None
                and self.bid < self.ask and self.tick_int > 0)


def js_round_tick(price_int: int, tick_int: int) -> int:
    # Source helper uses JavaScript Math.round(price / tick) * tick.
    return int(math.floor(price_int / tick_int + 0.5)) * tick_int


def source_pair_quotes(books: dict[str, Book]) -> tuple[tuple[int, int] | None, str]:
    if not all(book.valid() for book in books.values()):
        return None, "invalid_book"
    tick = books["YES"].tick_int
    if tick != books["NO"].tick_int:
        return None, "tick_mismatch"
    cheap = "YES" if books["YES"].bid <= books["NO"].bid else "NO"
    expensive = "NO" if cheap == "YES" else "YES"
    cheap_book, expensive_book = books[cheap], books[expensive]
    cheap_quote = js_round_tick(cheap_book.bid + tick, tick)
    if cheap_book.ask is not None and cheap_quote >= cheap_book.ask - tick:
        cheap_quote = js_round_tick(cheap_book.ask - 2 * tick, tick)
    cheap_px = cheap_quote / 10000
    if not (SOURCE_MIN_CHEAP_BID <= cheap_px <= SOURCE_MAX_CHEAP_BID):
        return None, "source_cheap_side_price_band"
    expensive_quote = js_round_tick(int(round((PAIR_CAP - cheap_px) * 10000)), tick)
    if expensive_book.ask is not None and expensive_quote >= expensive_book.ask - tick:
        expensive_quote = js_round_tick(expensive_book.ask - 2 * tick, tick)
    if expensive_quote <= 0 or expensive_quote >= 10000:
        return None, "source_expensive_side_bounds"
    yes_quote = cheap_quote if cheap == "YES" else expensive_quote
    no_quote = expensive_quote if cheap == "YES" else cheap_quote
    combined = (yes_quote + no_quote) / 10000
    if combined > PAIR_CAP + 1e-12:
        return None, "combined_cap"
    # Current source executor declines quotes more than one tick below cap.
    if combined < PAIR_CAP - tick / 10000 - 1e-12:
        return None, "source_minimum_combined"
    if yes_quote >= books["YES"].ask or no_quote >= books["NO"].ask:
        return None, "post_only"
    return (yes_quote, no_quote), "valid"


class RollingReplay:
    def __init__(self, slug: str, start: int, activation_us: int, cancel_us: int,
                 winner: str, order_rows: list[dict], fill_rows: list[dict],
                 pair_rows: list[dict]) -> None:
        self.slug, self.start, self.end = slug, start, start + 300_000_000
        self.activation_us, self.cancel_us, self.winner = activation_us, cancel_us, winner
        self.cancel_cutoff_us = self.end - SOURCE_CUT_LOSS_S * 1_000_000
        self.cancel_cutoff_started = False
        self.order_rows, self.fill_rows, self.pair_rows = order_rows, fill_rows, pair_rows
        self.books = {"YES": Book(), "NO": Book()}
        self.orders: dict[str, dict | None] = {"YES": None, "NO": None}
        self.inventory = {"YES": 0.0, "NO": 0.0}
        self.cost = {"YES": 0.0, "NO": 0.0}
        self.spent = 0.0
        self.peak_capital = 0.0
        self.completed_pairs = 0
        self.cycle_num = 0
        self.cycle: dict | None = None
        self.state = "READY"
        self.first_leg_count = 0
        self.completed_second_leg_count = 0
        self.quote_submissions = 0
        self.cancel_replaces = 0
        self.raw_partial_fill_records = 0
        self.quote_reasons = Counter()
        self.event_audit = Counter()
        self.first_valid_us: int | None = None
        self.last_valid_us: int | None = None
        self.first_trade_us: int | None = None
        self.error: str | None = None

    def capital_used(self) -> float:
        reserve = sum(float(o["remaining_qty"]) * float(o["price"])
                      for o in self.orders.values() if o is not None)
        return self.spent + reserve

    def refresh_peak(self) -> None:
        used = self.capital_used()
        self.peak_capital = max(self.peak_capital, used)
        if used > CAPITAL + 1e-8:
            self.error = f"capital invariant exceeded: {used:.8f}"

    def _quote_decision(self, t: int) -> None:
        quotes, reason = source_pair_quotes(self.books)
        self.quote_reasons[reason] += 1
        if reason == "valid":
            if self.first_valid_us is None:
                self.first_valid_us = t
            self.last_valid_us = t
        if (self.state != "READY" or self.completed_pairs >= TARGET_PAIRS
                or t >= self.cancel_cutoff_us):
            return
        if quotes is None:
            return
        yes_px, no_px = quotes[0] / 10000, quotes[1] / 10000
        reserve = CLIP * (yes_px + no_px)
        if self.capital_used() + reserve > CAPITAL + 1e-8:
            self.quote_reasons["capital_blocked"] += 1
            return
        self.cycle_num += 1
        self.cycle = {"market": self.slug, "pair_number": self.cycle_num,
                      "yes_quote": yes_px, "no_quote": no_px,
                      "first_side": None, "first_price": None, "first_us": None,
                      "second_leg_max_price": None,
                      "yes_fill_qty": 0.0, "no_fill_qty": 0.0,
                      "yes_cost": 0.0, "no_cost": 0.0}
        self.state = "TWO_LEGS_WORKING"
        for side, price in (("YES", yes_px), ("NO", no_px)):
            order = {"market": self.slug, "cycle": self.cycle_num,
                     "order_id": f"{self.slug}:R0:{self.cycle_num}:{side}",
                     "side": side, "price": price, "size": CLIP,
                     "remaining_qty": CLIP, "submitted_us": t,
                     "activation_us": t + self.activation_us,
                     "status": "submitted", "closed_us": None}
            self.orders[side] = order
            self.order_rows.append(order)
            self.quote_submissions += 1
        self.refresh_peak()

    def _trade(self, event: dict, side: str) -> None:
        payload = event.get("payload") or {}
        if payload.get("side") != "SELL":
            return
        order = self.orders[side]
        if order is None or order["status"] not in ("active", "cancel_pending"):
            return
        t, exch, received = (event.get(k) for k in
                             ("available_at_us", "exchange_at_us", "received_at_us"))
        if None in (t, exch, received) or min(t, exch, received) <= order["activation_us"]:
            return
        if order.get("cancel_effective_us") is not None and t >= order["cancel_effective_us"]:
            return
        trade_price = int(round(float(payload["price"]) * 10000))
        if trade_price >= int(round(float(order["price"]) * 10000)):
            return
        if self.first_trade_us is None:
            self.first_trade_us = t
        # Exact inherited Backtest 01 conservative rule: a same-token SELL print
        # strictly below our active bid fills the entire 5-share clip. The
        # historical replay does not volume-match trades or model queue position.
        order["status"] = "filled"
        order["closed_us"] = t
        qty = float(order["remaining_qty"])
        order["remaining_qty"] = 0.0
        self.orders[side] = None
        fill_cost = qty * float(order["price"])
        self.inventory[side] += qty
        self.cost[side] += fill_cost
        self.spent += fill_cost
        self.raw_partial_fill_records += 1
        fill = {"market": self.slug, "cycle": self.cycle_num,
                "order_id": order["order_id"], "side": side,
                "price": order["price"], "size": qty,
                "raw_trade_size": payload.get("size"),
                "fill_available_us": t, "fill_exchange_us": exch,
                "fill_received_us": received,
                "activation_us": order["activation_us"],
                "trade_price": trade_price / 10000,
                "trade_event_seq": event.get("event_seq"),
                "evidence": "same-token public SELL strictly below active bid; all timestamps after activation"}
        self.fill_rows.append(fill)
        assert self.cycle is not None
        self.cycle[f"{side.lower()}_fill_qty"] += qty
        self.cycle[f"{side.lower()}_cost"] += fill_cost
        if self.cycle["first_side"] is None:
            self.cycle["first_side"] = side
            self.cycle["first_price"] = float(order["price"])
            self.cycle["first_us"] = t
            self.cycle["second_leg_max_price"] = PAIR_CAP - float(order["price"])
            self.first_leg_count += 1
            self.state = "ONE_LEG_OPEN"
        elif side == self.cycle["first_side"]:
            # The inherited simulator fills each 5-share order once; a second
            # fill on the first side would violate that order-lifecycle model.
            self.error = "unexpected repeated same-side fill in one cycle"
            return
        if (self.cycle["yes_fill_qty"] >= CLIP and self.cycle["no_fill_qty"] >= CLIP):
            first_side = self.cycle["first_side"]
            first_us = self.cycle["first_us"]
            second_us = t
            yes_px = self.cycle["yes_cost"] / CLIP
            no_px = self.cycle["no_cost"] / CLIP
            pair_cost = yes_px + no_px
            if float(order["price"]) > self.cycle["second_leg_max_price"] + 1e-8:
                self.error = "second leg exceeded first-leg-derived maximum price"
                return
            if pair_cost > PAIR_CAP + 1e-8:
                self.error = f"completed pair exceeds cap: {pair_cost:.8f}"
                return
            self.completed_pairs += 1
            self.completed_second_leg_count += 1
            row = {"market": self.slug, "pair_number": self.completed_pairs,
                   "first_side": first_side, "first_leg_price": self.cycle["first_price"],
                   "first_leg_timestamp_us": first_us,
                   "second_leg_side": side, "second_leg_price": float(order["price"]),
                   "second_leg_max_price": self.cycle["second_leg_max_price"],
                   "second_leg_timestamp_us": second_us,
                   "second_leg_lag_s": (second_us - first_us) / 1_000_000,
                   "yes_price": yes_px, "no_price": no_px,
                   "combined_pair_cost": pair_cost,
                   "pair_gross_edge": CLIP * (1 - pair_cost),
                   "cumulative_capital_used": self.spent}
            self.pair_rows.append(row)
            self.cycle = None
            self.state = "DONE" if self.completed_pairs >= TARGET_PAIRS else "PAIR_COMPLETE"
            self.refresh_peak()
            if self.state != "DONE":
                self.state = "READY"
                self._quote_decision(t)
        else:
            self.state = "ONE_LEG_OPEN"
        self.refresh_peak()

    def replay(self, path: Path, token_map: dict[str, str]) -> dict:
        last_t = 0
        with (path / "events.jsonl").open() as f:
            for line in f:
                event = json.loads(line)
                t = event.get("available_at_us")
                if t is None or t < self.start or t >= self.end:
                    continue
                if t < last_t:
                    self.event_audit["nonmonotone_available_time"] += 1
                    continue
                last_t = t
                self.advance(t)
                payload = event.get("payload") or {}
                side = token_map.get(payload.get("token_id"))
                if side is None:
                    continue
                kind = event.get("kind")
                if kind in ("BOOK", "REST_BOOK", "DELTA"):
                    self.event_audit[f"{side}_{kind}"] += 1
                    if self.books[side].update(kind, payload):
                        self.event_audit["bbo_decisions"] += 1
                        self._quote_decision(t)
                elif kind == "TICK":
                    tick = int(round(float(payload["tick_size"]) * 10000))
                    if tick != self.books[side].tick_int:
                        self.books[side].tick_int = tick
                        self.event_audit[f"{side}_TICK"] += 1
                        self._quote_decision(t)
                elif kind == "TRADE":
                    self.event_audit["public_trades"] += 1
                    self._trade(event, side)
                if self.error:
                    break
        self.advance(self.end)
        if self.event_audit["nonmonotone_available_time"]:
            self.error = self.error or "nonmonotone event availability timestamps"
        if self.orders["YES"] or self.orders["NO"]:
            self.event_audit["open_orders_at_market_end"] = sum(o is not None for o in self.orders.values())
            for order in self.orders.values():
                if order is not None:
                    order["status"] = "expired_at_market_end"
                    order["closed_us"] = self.end
                    order["remaining_qty"] = 0.0
            self.orders = {"YES": None, "NO": None}
        self.refresh_peak()
        return self.result()

    def advance(self, t: int) -> None:
        if t >= self.cancel_cutoff_us and not self.cancel_cutoff_started:
            self.cancel_cutoff_started = True
            for order in self.orders.values():
                if order is None:
                    continue
                order["status"] = "cancel_pending"
                order["cancel_request_us"] = self.cancel_cutoff_us
                order["cancel_effective_us"] = max(
                    self.cancel_cutoff_us + self.cancel_us, order["activation_us"])
                self.cancel_replaces += 1
                self.event_audit["cancel_requests"] += 1
        for side, order in list(self.orders.items()):
            if order is None:
                continue
            if order["status"] == "submitted" and t >= order["activation_us"]:
                order["status"] = "active"
                self.event_audit["activated_orders"] += 1
            if order["status"] == "cancel_pending" and t >= order["cancel_effective_us"]:
                order["status"] = "canceled"
                order["closed_us"] = order["cancel_effective_us"]
                order["remaining_qty"] = 0.0
                self.orders[side] = None
                self.event_audit["cancel_effective"] += 1
        self.refresh_peak()

    def result(self) -> dict:
        yes_qty, no_qty = self.inventory["YES"], self.inventory["NO"]
        residual_side = "YES" if yes_qty > no_qty else "NO" if no_qty > yes_qty else "NONE"
        residual_qty = abs(yes_qty - no_qty)
        # Only the open rolling cycle can be unmatched. Use that exact cycle's
        # acquisition cost rather than an average across earlier completed pairs.
        residual_cost = 0.0
        if residual_side != "NONE" and self.cycle is not None:
            residual_cost = float(self.cycle["yes_cost"] + self.cycle["no_cost"])
        payout = 1.0 if self.winner == residual_side else 0.0
        residual_pnl = residual_qty * payout - residual_cost
        pair_rows = [p for p in self.pair_rows if p["market"] == self.slug]
        pair_pnl = sum(p["pair_gross_edge"] for p in pair_rows)
        costs = [p["combined_pair_cost"] for p in pair_rows]
        lags = [p["second_leg_lag_s"] for p in pair_rows]
        pending_first = max(0, self.first_leg_count - self.completed_second_leg_count)
        blocking = "NONE"
        if self.error:
            blocking = "EXECUTION_LOGIC_FAILURE"
        elif self.completed_pairs < TARGET_PAIRS:
            if pending_first:
                blocking = "FIRST_LEG_STUCK"
            elif self.quote_reasons["capital_blocked"]:
                blocking = "CAPITAL_BLOCKED"
            elif not self.quote_reasons["valid"]:
                blocking = "NO_PAIR_OPPORTUNITY"
            elif self.quote_submissions and self.event_audit["open_orders_at_market_end"]:
                blocking = "LOW_FILL_RATE"
            elif self.quote_submissions:
                blocking = "LOW_FILL_RATE"
            else:
                blocking = "NO_PAIR_OPPORTUNITY"
        timeline = []
        for row in pair_rows:
            lag = row["second_leg_lag_s"]
            edge = row["pair_gross_edge"]
            timeline.append(f"Pair {row['pair_number']}: {row['first_side']} @ {row['first_leg_price']:.3f} → {lag:.3f}s → {row['second_leg_side']} @ {row['second_leg_price']:.3f} → +${edge:.3f}")
        if pending_first and self.cycle:
            first = self.cycle["first_side"]
            opp = "NO" if first == "YES" else "YES"
            timeline.append(f"Pair {self.cycle_num}: {first} @ {self.cycle['first_price']:.3f} → {opp} leg never completed (max ${self.cycle['second_leg_max_price']:.3f})")
        elif self.completed_pairs < TARGET_PAIRS:
            timeline.append(f"Stopped after Pair {self.completed_pairs}: {blocking}")
        return {
            "market": self.slug, "winner": self.winner,
            "completed_pairs": self.completed_pairs, "matched_shares": min(yes_qty, no_qty),
            "pair_pnl": round(pair_pnl, 6), "residual_pnl": round(residual_pnl, 6),
            "total_pnl": round(pair_pnl + residual_pnl, 6),
            "average_pair_cost": mean(costs) if costs else None,
            "best_pair_cost": min(costs) if costs else None,
            "worst_pair_cost": max(costs) if costs else None,
            "first_leg_count": self.first_leg_count,
            "completed_second_leg_count": self.completed_second_leg_count,
            "second_leg_lag_median_s": median(lags) if lags else None,
            "second_leg_lag_max_s": max(lags) if lags else None,
            "second_leg_lag_mean_s": mean(lags) if lags else None,
            "yes_inventory": yes_qty, "no_inventory": no_qty,
            "end_residual_side": residual_side, "end_residual_shares": residual_qty,
            "end_residual_cost": round(residual_cost, 6),
            "peak_capital_used": round(self.peak_capital, 6),
            "cash_spent_at_end": round(self.spent, 6),
            "cash_reserved_at_end": round(self.capital_used() - self.spent, 6),
            "quote_submissions": self.quote_submissions,
            "cancels_replaces": self.cancel_replaces,
            "raw_fill_records": self.raw_partial_fill_records,
            "partial_fill_records": 0,
            "primary_failure": blocking,
            "secondary_blockers": dict(self.quote_reasons),
            "quote_reason_counts": dict(self.quote_reasons),
            "timeline": "\n".join(timeline),
            "data_error": self.error or "",
        }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with (AUDIT_DIR / "dataset_audit.csv").open() as f:
        audit = list(csv.DictReader(f))
    chosen_rows = [r for r in audit if r["execution_replay_usable"].lower() == "true"]
    if len(chosen_rows) != 32 or len({r["market"] for r in chosen_rows}) != 32:
        raise RuntimeError(f"Expected exact 32-market audited universe; got {len(chosen_rows)}")
    replay_summary = json.loads((AUDIT_DIR / "summary.json").read_text())
    activation_us = int(replay_summary["activation_latency_us"])
    cancel_us = int(replay_summary["cancel_latency_us"])
    audit_by_session = {row["session_id"]: row for row in json.loads(M4A_AUDIT.read_text())["sessions"]}
    order_rows: list[dict] = []
    fill_rows: list[dict] = []
    pair_rows: list[dict] = []
    market_rows: list[dict] = []
    labels: list[dict] = []
    timelines: list[str] = []
    selected_sessions: list[dict] = []
    for audited in chosen_rows:
        path = DATA / audited["session"]
        manifest = json.loads((path / "manifest.json").read_text())
        if manifest["market_slug"] != audited["market"]:
            raise RuntimeError(f"Saved audit/session identity mismatch: {path}")
        winner, label = gamma_winner(audited["market"], manifest)
        labels.append({"market": audited["market"], "winner": winner, **label})
        session_audit = audit_by_session.get(path.name)
        if session_audit is None:
            raise RuntimeError(f"Selected execution session missing from M4A audit: {path.name}")
        selected_sessions.append({"market": audited["market"], "session": path.name,
                                  "m4a_integrity": session_audit["integrity"]})
        token_map = {manifest["yes_token_id"]: "YES", manifest["no_token_id"]: "NO"}
        start = int(audited["market"].rsplit("-", 1)[1]) * 1_000_000
        sim = RollingReplay(audited["market"], start, activation_us, cancel_us,
                            winner, order_rows, fill_rows, pair_rows)
        result = sim.replay(path, token_map)
        result["m4a_integrity"] = session_audit["integrity"]
        result["session"] = path.name
        market_rows.append(result)
        timelines.append(f"## {audited['market']} — {result['completed_pairs']}/5\n\n{result['timeline']}\n")
    if any(row["primary_failure"] == "EXECUTION_LOGIC_FAILURE" for row in market_rows):
        raise RuntimeError("One or more markets failed an execution invariant; inspect outputs")
    write_csv(OUT / "markets.csv", market_rows)
    write_csv(OUT / "pairs.csv", pair_rows)
    write_csv(OUT / "orders.csv", order_rows)
    write_csv(OUT / "fills.csv", fill_rows)
    write_csv(OUT / "settlement_labels.csv", labels)
    write_csv(OUT / "sample_audit.csv", selected_sessions)
    (OUT / "cycle_timelines.md").write_text("# R0 Cycle Timelines\n\n" + "\n".join(timelines))

    passing = [r for r in market_rows if r["m4a_integrity"] == "PASS"]
    def summarize(rows: list[dict]) -> dict:
        n = len(rows)
        pairs = [int(r["completed_pairs"]) for r in rows]
        pair_rows_subset = [p for p in pair_rows if p["market"] in {r["market"] for r in rows}]
        pair_costs = [float(p["combined_pair_cost"]) for p in pair_rows_subset]
        lags = [float(p["second_leg_lag_s"]) for p in pair_rows_subset]
        total_pair = sum(float(r["pair_pnl"]) for r in rows)
        total_residual = sum(float(r["residual_pnl"]) for r in rows)
        totals = [float(r["total_pnl"]) for r in rows]
        classes = Counter(r["primary_failure"] for r in rows if r["completed_pairs"] < TARGET_PAIRS)
        secondary = Counter()
        for row in rows:
            secondary.update(row["quote_reason_counts"])
        failure_names = ("NO_PAIR_OPPORTUNITY", "LOW_FILL_RATE", "FIRST_LEG_STUCK",
                         "CAPITAL_BLOCKED", "TIME_EXPIRED", "DATA_FAILURE",
                         "EXECUTION_LOGIC_FAILURE")
        cap_blocks = sum(int(r["quote_reason_counts"].get("capital_blocked", 0) > 0) for r in rows)
        first_legs = sum(int(r["first_leg_count"]) for r in rows)
        paired_legs = sum(int(r["completed_second_leg_count"]) for r in rows)
        return {
            "markets": n,
            "complete_5_of_5": sum(v == 5 for v in pairs),
            "completion_rate": sum(v == 5 for v in pairs) / n if n else None,
            "mean_pairs_per_market": mean(pairs) if pairs else None,
            "median_pairs_per_market": median(pairs) if pairs else None,
            "p25_pairs_per_market": percentile(pairs, .25),
            "p75_pairs_per_market": percentile(pairs, .75),
            "mean_matched_shares_per_market": mean(float(r["matched_shares"]) for r in rows) if rows else None,
            "aggregate_pair_pnl": total_pair,
            "mean_pair_pnl_per_market": total_pair / n if n else None,
            "median_pair_pnl_per_market": median([float(r["pair_pnl"]) for r in rows]) if rows else None,
            "aggregate_residual_pnl": total_residual,
            "aggregate_total_pnl": sum(totals),
            "mean_total_pnl_per_market": mean(totals) if totals else None,
            "profitable_markets": sum(x > 1e-9 for x in totals),
            "losing_markets": sum(x < -1e-9 for x in totals),
            "flat_markets": sum(abs(x) <= 1e-9 for x in totals),
            "weighted_mean_pair_cost": (sum(float(p["combined_pair_cost"]) * CLIP for p in pair_rows_subset) /
                                        (len(pair_rows_subset) * CLIP) if pair_rows_subset else None),
            "median_pair_cost": median(pair_costs) if pair_costs else None,
            "pair_cost_pct_le_095": sum(c <= .95 + 1e-9 for c in pair_costs) / len(pair_costs) if pair_costs else None,
            "pair_cost_pct_0951_097": sum(.951 - 1e-9 <= c <= .97 + 1e-9 for c in pair_costs) / len(pair_costs) if pair_costs else None,
            "pair_cost_pct_0971_098": sum(.971 - 1e-9 <= c <= .98 + 1e-9 for c in pair_costs) / len(pair_costs) if pair_costs else None,
            "mean_peak_capital_used": mean(float(r["peak_capital_used"]) for r in rows) if rows else None,
            "median_peak_capital_used": median(float(r["peak_capital_used"]) for r in rows) if rows else None,
            "max_peak_capital_used": max((float(r["peak_capital_used"]) for r in rows), default=0),
            "markets_capital_blocked": cap_blocks,
            "second_leg_lag_mean_s": mean(lags) if lags else None,
            "second_leg_lag_median_s": median(lags) if lags else None,
            "second_leg_lag_p75_s": percentile(lags, .75),
            "second_leg_lag_p90_s": percentile(lags, .90),
            "second_leg_lag_max_s": max(lags) if lags else None,
            "first_legs_never_completed": first_legs - paired_legs,
            "first_legs_never_completed_pct": (first_legs - paired_legs) / first_legs if first_legs else None,
            "failure_classes": {name: classes.get(name, 0) for name in failure_names},
            "secondary_blocker_counts": dict(secondary),
            "raw_fill_records": sum(int(r["raw_fill_records"]) for r in rows),
            "partial_fill_records": sum(int(r["partial_fill_records"]) for r in rows),
        }

    full = summarize(market_rows)
    diagnostic = summarize(passing)
    if full["aggregate_total_pnl"] > 0 and full["aggregate_pair_pnl"] > 0 and full["complete_5_of_5"] > 0:
        classification = "ROLLING EDGE OBSERVED"
    elif full["aggregate_pair_pnl"] > 0 and full["aggregate_residual_pnl"] <= -full["aggregate_pair_pnl"]:
        classification = "PAIR EDGE EXISTS BUT RESIDUAL DOMINATES"
    elif full["markets_capital_blocked"] > 0:
        classification = "CAPITAL LIMIT DOMINATES"
    elif sum(float(r["quote_reason_counts"].get("valid", 0)) for r in market_rows) == 0:
        classification = "PAIR CAP TOO TIGHT"
    elif sum(int(r["first_leg_count"]) for r in market_rows) < len(market_rows):
        classification = "EDGE EXISTS BUT FILL RATE TOO LOW"
    elif full["aggregate_pair_pnl"] <= 0:
        classification = "NO ROLLING EDGE OBSERVED"
    else:
        classification = "EDGE EXISTS BUT FILL RATE TOO LOW"

    summary = {
        "classification": classification,
        "parameters": {"capital_usd": CAPITAL, "clip_shares": CLIP,
                       "pair_cap": PAIR_CAP, "target_pairs": TARGET_PAIRS,
                       "source_default_min_cheap_bid": SOURCE_MIN_CHEAP_BID,
                       "source_default_max_cheap_bid": SOURCE_MAX_CHEAP_BID,
                       "source_default_cut_loss_s": SOURCE_CUT_LOSS_S,
                       "source_minimum_combined_cost": "pair_cap minus one market tick"},
        "sample": {"execution_replay_markets": len(market_rows),
                   "unique_markets": len({r["market"] for r in market_rows}),
                   "excluded_short_sessions": 10,
                   "m4a_integrity_passing_overlap": len(passing),
                   "official_settlement_labels": len(labels),
                   "all_market_books_reconstructed": all(r["data_error"] == "" for r in market_rows)},
        "latencies": {"activation_p75_us": activation_us, "cancel_p75_us": cancel_us,
                      "source": "existing execution replay audit summary.json"},
        "all_32": full,
        "m4a_pass_overlap_diagnostic": diagnostic,
        "fill_rule": "same-token public SELL strictly below active resting BUY; exchange, received, available timestamps all strictly after activation; one eligible print fills entire 5-share order, independent of print size; no queue-position or equal-price fill; inherited from existing Backtest 01 replay",
        "quote_rule": "direkturcrypto/polymarket-terminal makerRebateExecutor.js source construction: cheap side best bid plus one tick, safety backoff to ask minus two ticks; expensive side fills 0.98 combined budget then same ask safety; source default cheap-side range 0.30–0.69; current source declines combined below cap minus one tick; post-only asks checked; fixed orders held without repricing; immediate new cycle after pair completion; source default cancels outstanding orders at T-60s with existing replay p75 cancel latency",
        "settlement": "Official Gamma event API outcomes only, checked closed/resolved status, condition ID and ordered YES/NO token identities; residual settlement only; matched pairs use gross pair edge 5*(1-pair cost)",
        "source_url": "https://github.com/direkturcrypto/polymarket-terminal/blob/main/src/services/makerRebateExecutor.js",
        "source_readme_url": "https://github.com/direkturcrypto/polymarket-terminal",
        "markets": market_rows,
        "event_audit": {r["market"]: r.get("quote_reason_counts", {}) for r in market_rows},
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({"classification": classification, "all_32": full,
                      "m4a_pass_overlap_diagnostic": diagnostic}, indent=2))


if __name__ == "__main__":
    main()
