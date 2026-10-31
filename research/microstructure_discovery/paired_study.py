"""Paired maker liquidity replay; PROTOCOL_V4_PAIRED_MAKERS.md governs claims."""

import argparse
import collections
import hashlib
import heapq
import json
import math
from pathlib import Path

from execution_study import aggregate, vwap
from study import Book


def fallback_gross_quantity(rate, exponent):
    # Before rounding fee-share fraction <= rate; nearest-unit rounding can at most double it.
    if not 0 <= rate < 0.2 or exponent < 1:
        return None
    return math.ceil((5 / (1 - 2 * rate)) * 100 - 1e-9) / 100


def worst_buy_price(book, quantity):
    remaining = quantity
    for price, size in sorted(book.asks.items()):
        remaining -= min(remaining, size)
        if remaining <= 1e-8:
            return price
    return None


class PairedReplay:
    def __init__(self, metadata, fees, end, decision_end):
        self.metadata = {m["asset"]: m for m in metadata}
        primary = {}
        for m in metadata:
            primary.setdefault(m["market"], m)
        self.primary = {m["asset"]: m for m in primary.values()}
        self.complement_to_primary = {}
        for m in metadata:
            first = primary[m["market"]]
            if m["asset"] != first["asset"]:
                self.complement_to_primary[m["asset"]] = first["asset"]
        self.fees = fees
        self.end = end
        self.decision_end = decision_end
        self.books = collections.defaultdict(Book)
        self.dirty = set()
        self.regressed = set()
        self.source = {}
        self.generation = None
        self.history = collections.defaultdict(collections.deque)
        self.states = {}
        for a in self.primary:
            for policy in ("neutral_inside", "pressure_filtered"):
                for horizon in (30, 120):
                    for delay in (0.5, 1.0, 2.0):
                        key = (a, policy, horizon, delay)
                        self.states[key] = dict(key=key, stage="IDLE", last=-math.inf, version=0)
        self.timers = []
        self.timer_id = 0
        self.rows = []
        self.seen = set()
        self.checks = collections.Counter()
        self.raw_line = 0
        self.book_line = {}
        self.resolved = set()
        self.no_new_quotes = set()

    def timer(self, when, state, action):
        self.timer_id += 1
        heapq.heappush(self.timers, (when, self.timer_id, state["key"], state["version"], action))

    def trusted(self, a):
        return (
            a not in self.resolved
            and a not in self.dirty
            and a not in self.regressed
            and self.books[a].quote() is not None
        )

    def advance(self, at, inclusive=False):
        while self.timers and (self.timers[0][0] <= at if inclusive else self.timers[0][0] < at):
            when, _, key, version, action = heapq.heappop(self.timers)
            s = self.states[key]
            if s["version"] != version:
                continue
            a, policy, horizon, delay = key
            if action == "POST":
                q = self.books[a].quote()
                if (
                    a in self.no_new_quotes
                    or not self.trusted(a)
                    or self.generation != s["generation"]
                    or q is None
                    or not q["bid"] + 1e-9 < s["price"] < q["ask"] - 1e-9
                ):
                    s["stage"] = "IDLE"
                    s["version"] += 1
                    self.checks["post_not_admitted"] += 1
                    continue
                s.update(
                    stage="LIVE",
                    posted=when,
                    cancel_at=when + 60 + delay,
                    quantity=0.0,
                    first_fill=None,
                    ambiguous=False,
                    post_state_line=self.book_line.get(a),
                    post_bid=q["bid"],
                    post_ask=q["ask"],
                    hedge_stage="NONE",
                    hedge_strict=0.0,
                    hedge_possible=0.0,
                    hedge_price=None,
                    hedge_seen=set(),
                    hedge_evidence=[],
                    hedge_last=-math.inf,
                    hedge_admission_uncertain=False,
                    force_started=False,
                    pair_release=False,
                )
                self.timer(s["cancel_at"] + 2.25, s, "DRAIN")
            elif action == "DRAIN":
                if s["stage"] == "LIVE" and s["quantity"] == 0:
                    s["stage"] = "IDLE"
                    s["version"] += 1
                    self.checks["unfilled_quote"] += 1
            elif action == "EXIT":
                self.force_cancel(s, when)
            elif action == "HEDGE_POST":
                self.post_hedge(s, when)
            elif action == "HEDGE_FINISH":
                self.close(s, when)
            elif action == "PAIR_RELEASE":
                if s.get("hedge_strict", 0) >= 5 - 1e-8 and not s.get("hedge_admission_uncertain"):
                    self.close(s, when)

    def entry_position(self, s, quantity, at, ambiguous=False, evidence=None):
        quantity = min(quantity, 5 - s["quantity"])
        if quantity <= 0:
            return
        s["quantity"] += quantity
        s.setdefault("fill_evidence", []).append(
            dict(quantity=quantity, received=at, ambiguous=ambiguous, **(evidence or {}))
        )
        s["ambiguous"] = s["ambiguous"] or ambiguous
        if s["first_fill"] is None:
            s["first_fill"] = at
            s["cancel_at"] = min(s["cancel_at"], at + s["key"][3])
            self.timer(s["cancel_at"] + 2.25, s, "DRAIN")
            self.timer(at + s["key"][2] + s["key"][3], s, "EXIT")
        if s["quantity"] >= 5 - 1e-8:
            s["stage"] = "INVENTORY"

    def entry_trade(self, event, at):
        original_asset = event.get("asset_id")
        complement = original_asset in self.complement_to_primary
        if complement and event.get("side") == "BUY":
            a = self.complement_to_primary[original_asset]
            price = round(1 - float(event["price"]), 12)
        elif original_asset in self.primary and event.get("side") == "SELL":
            a = original_asset
            price = float(event["price"])
        else:
            return
        # Mirror legs of one economic execution must not multiply available volume.
        key = (
            a,
            event.get("timestamp"),
            round(price, 9),
            round(float(event.get("size", 0)), 9),
            event.get("transaction_hash"),
        )
        if key in self.seen:
            return
        self.seen.add(key)
        source = float(event.get("timestamp", 0)) / 1000
        if not -0.25 <= at - source <= 2:
            return
        for policy in ("neutral_inside", "pressure_filtered"):
            for horizon in (30, 120):
                for delay in (0.5, 1.0, 2.0):
                    s = self.states[(a, policy, horizon, delay)]
                    if s["stage"] != "LIVE" or self.generation != s["generation"]:
                        continue
                    if (
                        source < s["posted"] - 0.25
                        or source > s["cancel_at"] + 0.25
                        or price > s["price"] + 1e-9
                    ):
                        continue
                    self.position(
                        s,
                        float(event.get("size", 0)),
                        at,
                        ambiguous=(source < s["posted"] + 0.25 or source > s["cancel_at"] - 0.25),
                        evidence=dict(
                            raw_line=self.raw_line,
                            kind="SELL_TRADE_THROUGH_WITH_CLOCK_BOUND",
                            source_timestamp=source,
                            reported_price=float(event["price"]),
                            canonical_primary_price=price,
                            original_asset=original_asset,
                            complementary_buy=complement,
                        ),
                    )

    def entry_event(self, r):
        self.raw_line += 1
        at = r["received"]
        self.advance(at)
        if (self.generation is not None and r.get("generation") != self.generation) or r.get(
            "error"
        ):
            for s in self.states.values():
                if s["stage"] == "LIVE":
                    self.position(
                        s,
                        5 - s["quantity"],
                        at,
                        True,
                        evidence=dict(
                            raw_line=self.raw_line, kind="POTENTIAL_FILL_DURING_STREAM_BREAK"
                        ),
                    )
            self.dirty.update(self.books)
        if r.get("generation") != self.generation:
            self.books.clear()
            self.dirty.clear()
            self.regressed.clear()
            self.source.clear()
            self.history.clear()
            self.generation = r.get("generation")
            self.checks["generations"] += 1
            # Existing positions are retained; exits across the break cannot get favorable marks.
        e = r.get("event", {})
        kind = e.get("event_type")
        changed = set()
        if kind == "book":
            a = e["asset_id"]
            self.books[a].update(e)
            self.dirty.discard(a)
            self.regressed.discard(a)
            self.source[a] = float(e.get("timestamp", 0))
            changed.add(a)
        elif kind == "price_change":
            latest = {}
            for c in e.get("price_changes", []):
                a = c["asset_id"]
                ts = float(e.get("timestamp", 0))
                if ts < self.source.get(a, 0):
                    self.regressed.add(a)
                    self.checks["source_regression"] += 1
                self.source[a] = max(ts, self.source.get(a, 0))
                self.books[a].update(dict(c, event_type=kind))
                latest[a] = c
                changed.add(a)
            for a, c in latest.items():
                q = self.books[a].quote()
                if (
                    q is not None
                    and c.get("best_bid") is not None
                    and c.get("best_ask") is not None
                ):
                    if (
                        abs(q["bid"] - float(c["best_bid"])) > 1e-8
                        or abs(q["ask"] - float(c["best_ask"])) > 1e-8
                    ):
                        self.dirty.add(a)
                    else:
                        self.dirty.discard(a)
        elif kind == "last_trade_price":
            self.trade(e, at)
        elif kind == "tick_size_change":
            a = e.get("asset_id")
            if a in self.primary:
                self.no_new_quotes.add(a)
                self.checks["tick_change_quote_pause"] += 1
                for s in self.states.values():
                    if s["key"][0] == a and s["stage"] == "LIVE":
                        self.position(
                            s,
                            5 - s["quantity"],
                            at,
                            True,
                            evidence=dict(
                                raw_line=self.raw_line, kind="POTENTIAL_FILL_AT_TICK_CHANGE"
                            ),
                        )
        elif kind == "market_resolved":
            affected = set(e.get("assets_ids", []))
            affected.update(a for a, m in self.metadata.items() if m["market"] == e.get("market"))
            self.resolved.update(affected)
        for a in changed:
            self.book_line[a] = self.raw_line
        # Every active quote whose hidden counterfactual fill becomes ambiguous gets a loss-only bound.
        for a in changed & self.primary.keys():
            q = self.books[a].quote()
            if q is not None:
                h = self.history[a]
                h.append((at, q["bid"]))
                while len(h) > 2 and h[1][0] <= at - 3:
                    h.popleft()
                for policy in ("neutral_inside", "pressure_filtered"):
                    for horizon in (30, 120):
                        for delay in (0.5, 1.0, 2.0):
                            s = self.states[(a, policy, horizon, delay)]
                            if (
                                s["stage"] == "INVENTORY"
                                and at <= s["posted"] + 2.25
                                and self.source.get(a, 0) / 1000 <= s["posted"] + 0.25
                                and q["ask"] <= s["price"] + 1e-9
                            ):
                                s["ambiguous"] = True
                                self.checks["late_post_admission_ambiguity"] += 1
                            if (
                                s["stage"] == "LIVE"
                                and at <= s["cancel_at"]
                                and q["ask"] <= s["price"] + 1e-9
                            ):
                                self.position(
                                    s,
                                    5 - s["quantity"],
                                    at,
                                    True,
                                    evidence=dict(
                                        raw_line=self.raw_line, kind="AMBIGUOUS_ASK_CROSS"
                                    ),
                                )
                                self.checks["ambiguous_shadow_cross"] += 1
        self.advance(at, True)
        if at > self.decision_end:
            return
        for a in changed & self.primary.keys():
            if a in self.no_new_quotes or not self.trusted(a):
                continue
            q = self.books[a].quote()
            m = self.primary[a]
            tick = float(m["tick"])
            price = round(q["bid"] + tick, 6)
            if (
                q["spread"] < 2 * tick - 1e-9
                or not 0.02 <= price <= 0.98
                or price >= q["ask"] - 1e-9
            ):
                continue
            total = q["bid_depth"] + q["ask_depth"]
            imbalance = (q["bid_depth"] - q["ask_depth"]) / total if total else 0
            hist = list(self.history[a])
            past_known = bool(hist and hist[0][0] <= at - 3)
            no_decline = past_known and all(
                hist[i][1] >= hist[i - 1][1] - 1e-9 for i in range(1, len(hist))
            )
            for policy in ("neutral_inside", "pressure_filtered"):
                if policy == "pressure_filtered" and not (imbalance >= 0.5 and no_decline):
                    continue
                for horizon in (30, 120):
                    for delay in (0.5, 1.0, 2.0):
                        s = self.states[(a, policy, horizon, delay)]
                        if (
                            s["stage"] != "IDLE"
                            or at - s["last"] < 15
                            or at + 5 + 2 * delay + horizon + 2 > self.end
                        ):
                            continue
                        s["version"] += 1
                        s.update(
                            stage="PENDING",
                            last=at,
                            decision=at,
                            price=price,
                            generation=self.generation,
                            quantity=0.0,
                            first_fill=None,
                            ambiguous=False,
                            fill_evidence=[],
                            decision_line=self.raw_line,
                            hedge_stage="NONE",
                            hedge_strict=0.0,
                            hedge_possible=0.0,
                            hedge_price=None,
                            hedge_evidence=[],
                            hedge_seen=set(),
                            hedge_last=-math.inf,
                            hedge_admission_uncertain=False,
                            force_started=False,
                            pair_release=False,
                        )
                        self.timer(at + delay, s, "POST")
                        self.checks["quote_decisions"] += 1

    def finish(self):
        self.advance(self.end, True)
        for s in self.states.values():
            if s.get("quantity", 0) > 0 and s["stage"] != "IDLE":
                # Any entered position still open at end has zero recovery.
                a = s["key"][0]
                self.regressed.add(a)
                self.close(s, self.end)
        return self.rows, dict(self.checks)

    def complement(self, a):
        return next(b for b, p in self.complement_to_primary.items() if p == a)

    def position(self, s, quantity, at, ambiguous=False, evidence=None):
        self.entry_position(s, quantity, at, ambiguous, evidence)
        if s["quantity"] >= 5 - 1e-8:
            self.plan_hedge(s, at)

    def plan_hedge(self, s, at):
        if (
            s.get("hedge_stage") != "NONE"
            or s.get("force_started")
            or at - s.get("hedge_last", -math.inf) < 5
        ):
            return
        if at + s["key"][3] >= s["first_fill"] + s["key"][2]:
            return
        a = self.complement(s["key"][0])
        q = self.books[a].quote()
        tick = float(self.metadata[a]["tick"])
        if (
            a in self.no_new_quotes
            or not self.trusted(a)
            or self.generation != s["generation"]
            or q is None
            or q["spread"] < 2 * tick - 1e-9
        ):
            return
        price = round(q["bid"] + tick, 6)
        if not 0 < price < 1 or price >= q["ask"] - 1e-9:
            return
        s.update(
            hedge_stage="PENDING",
            hedge_price=price,
            hedge_last=at,
            hedge_decision_line=self.raw_line,
        )
        self.timer(at + s["key"][3], s, "HEDGE_POST")

    def post_hedge(self, s, at):
        if s.get("force_started") or s.get("hedge_stage") != "PENDING":
            return
        a = self.complement(s["key"][0])
        q = self.books[a].quote()
        price = s["hedge_price"]
        if (
            a in self.no_new_quotes
            or not self.trusted(a)
            or self.generation != s["generation"]
            or q is None
            or not q["bid"] + 1e-9 < price < q["ask"] - 1e-9
        ):
            s["hedge_stage"] = "NONE"
            self.checks["hedge_post_not_admitted"] += 1
            return
        s.update(
            hedge_stage="LIVE",
            hedge_posted=at,
            hedge_cancel_at=s["first_fill"] + s["key"][2] + s["key"][3],
            hedge_post_state_line=self.book_line.get(a),
        )
        self.checks["hedge_posted"] += 1

    def trade(self, event, at):
        self.entry_trade(event, at)
        a = event.get("asset_id")
        side = event.get("side")
        if a in self.complement_to_primary and side == "SELL":
            primary = self.complement_to_primary[a]
            price = float(event["price"])
        elif a in self.primary and side == "BUY":
            primary = a
            price = round(1 - float(event["price"]), 12)
        else:
            return
        source = float(event.get("timestamp", 0)) / 1000
        if not -0.25 <= at - source <= 2:
            return
        signature = (
            primary,
            event.get("timestamp"),
            round(price, 9),
            round(float(event.get("size", 0)), 9),
            event.get("transaction_hash"),
        )
        for policy in ("neutral_inside", "pressure_filtered"):
            for horizon in (30, 120):
                for delay in (0.5, 1.0, 2.0):
                    s = self.states[(primary, policy, horizon, delay)]
                    if (
                        s.get("hedge_stage") != "LIVE"
                        or self.generation != s["generation"]
                        or signature in s.get("hedge_seen", set())
                    ):
                        continue
                    if (
                        source < s["hedge_posted"] - 0.25
                        or source > s["hedge_cancel_at"] + 0.25
                        or price > s["hedge_price"] + 1e-9
                    ):
                        continue
                    s["hedge_seen"].add(signature)
                    qty = min(5, float(event.get("size", 0)))
                    strict = (
                        source >= s["hedge_posted"] + 0.25 and source <= s["hedge_cancel_at"] - 0.25
                    )
                    if strict:
                        s["hedge_strict"] = min(5, s["hedge_strict"] + qty)
                    else:
                        s["hedge_possible"] = min(5, s["hedge_possible"] + qty)
                    s["hedge_evidence"].append(
                        dict(
                            raw_line=self.raw_line,
                            received=at,
                            source=source,
                            quantity=qty,
                            canonical_complement_price=price,
                            original_asset=a,
                            original_side=side,
                            strict=strict,
                        )
                    )
                    if (
                        s["hedge_strict"] >= 5 - 1e-8
                        and not s["hedge_admission_uncertain"]
                        and not s["pair_release"]
                    ):
                        s["pair_release"] = True
                        self.timer(at + 5, s, "PAIR_RELEASE")

    def force_cancel(self, s, at):
        if s.get("force_started"):
            return
        s["force_started"] = True
        # EXIT timer is already first-fill+horizon+cancel latency.
        s["hedge_cancel_at"] = at
        self.timer(at + 2.25 + s["key"][3], s, "HEDGE_FINISH")

    def event(self, r):
        at = r["received"]
        self.advance(at)
        generation_break = self.generation is not None and r.get("generation") != self.generation
        if generation_break or r.get("error"):
            for s in self.states.values():
                if s.get("hedge_stage") == "LIVE":
                    s["hedge_possible"] = 5
                    self.force_cancel(s, at + s["key"][3])
        self.entry_event(r)
        e = r.get("event", {})
        kind = e.get("event_type")
        changed = (
            {e.get("asset_id")}
            if kind in ("book", "tick_size_change")
            else {c["asset_id"] for c in e.get("price_changes", [])}
            if kind == "price_change"
            else set()
        )
        for a in changed:
            if a not in self.complement_to_primary:
                continue
            p = self.complement_to_primary[a]
            q = self.books[a].quote()
            for policy in ("neutral_inside", "pressure_filtered"):
                for horizon in (30, 120):
                    for delay in (0.5, 1.0, 2.0):
                        s = self.states[(p, policy, horizon, delay)]
                        if kind == "tick_size_change":
                            self.no_new_quotes.add(a)
                            if s.get("hedge_stage") == "LIVE":
                                s["hedge_possible"] = 5
                                s["hedge_admission_uncertain"] = True
                                self.force_cancel(s, at + delay)
                        if (
                            s.get("hedge_stage") == "LIVE"
                            and q is not None
                            and q["ask"] <= s["hedge_price"] + 1e-9
                        ):
                            s["hedge_possible"] = 5
                            if (
                                at <= s["hedge_posted"] + 2.25
                                and self.source.get(a, 0) / 1000 <= s["hedge_posted"] + 0.25
                            ):
                                s["hedge_admission_uncertain"] = True
                        if s.get("quantity", 0) >= 5 - 1e-8 and s["stage"] == "INVENTORY":
                            self.plan_hedge(s, at)

    def close(self, s, at):
        if s["stage"] == "IDLE" or s.get("quantity", 0) <= 0:
            return
        a, policy, horizon, delay = s["key"]
        b = self.complement(a)
        m = self.primary[a]
        qty = s["quantity"]
        entry = qty * s["price"]
        fd = self.fees.get(m["market"])
        rate = fd["r"] if fd is not None else 0.10
        exponent = fd["e"] if fd is not None else 1
        strict = s.get("hedge_strict", 0)
        possible = s.get("hedge_possible", 0)
        hp = s.get("hedge_price") or 0.0
        held = max(0.0, at - s["decision"])
        capital_factor = 0.08 * held / (365.25 * 86400)
        reserve = 0.004 * qty

        def value(branch):
            branch["hurdle"] = branch["cash"] * capital_factor
            branch["net_usdc"] = (
                branch["payout"] - branch["cash"] - branch["fee"] - reserve - branch["hurdle"]
            )
            return branch

        if qty < 5 - 1e-8:
            branches = [
                value(
                    dict(
                        hedge_quantity=0.0,
                        fallback_quantity=0.0,
                        payout=0.0,
                        cash=entry,
                        fee=0.0,
                        status="SUBMINIMUM_ENTRY",
                    )
                )
            ]
        else:
            full_strict = strict >= 5 - 1e-8
            if full_strict:
                amounts = [5.0] if not s.get("hedge_admission_uncertain") else [0.0, 5.0]
            else:
                minimum = 0.0 if s.get("hedge_admission_uncertain") else strict
                maximum = min(5.0, strict + possible)
                amounts = [minimum, maximum]
            gross = fallback_gross_quantity(rate, exponent)
            fp = (
                vwap(self.books[b], "BUY", gross)
                if gross is not None and self.trusted(b) and self.generation == s["generation"]
                else None
            )
            forced_fee = 0.0  # Share fee is already funded by the larger gross buy.
            wp = worst_buy_price(self.books[b], gross) if fp is not None else None
            capped_cost = gross * wp * (1 + capital_factor) if wp is not None else math.inf
            # A causal FOK limit guarantees total worst-level cost <= remaining payoff.
            if not full_strict and wp is not None:
                kink = 5 - capped_cost
                for point in (kink, kink + 1e-6):
                    if minimum < point < maximum:
                        amounts.append(point)
            branches = []
            for maker_qty in set(amounts):
                remaining = max(0.0, 5 - maker_qty)
                residual = value(
                    dict(
                        hedge_quantity=maker_qty,
                        fallback_quantity=0.0,
                        payout=maker_qty,
                        cash=entry + maker_qty * hp,
                        fee=0.0,
                        status="PAIRED_MAKERS" if remaining <= 1e-8 else "RESIDUAL_WRITTEN_OFF",
                    )
                )
                if remaining > 1e-8 and fp is not None and capped_cost <= remaining + 1e-10:
                    residual = value(
                        dict(
                            hedge_quantity=maker_qty,
                            fallback_quantity=gross,
                            payout=5.0,
                            cash=entry + maker_qty * hp + gross * fp,
                            fee=forced_fee,
                            status="MINIMUM_SIZE_FORCED_HEDGE",
                        )
                    )
                branches.append(residual)
        worst = min(branches, key=lambda branch: branch["net_usdc"])
        net = worst["net_usdc"]
        if s.get("ambiguous"):
            net = min(net, -qty * s["price"] - reserve)
        max_cash = max(branch["cash"] + branch["fee"] for branch in branches)
        if max_cash > 16 + 1e-8:
            net = min(net, -entry - reserve)
            self.checks["capital_limit_exceeded"] += 1
        self.rows.append(
            dict(
                signal=dict(
                    time=s["first_fill"],
                    decision=s["decision"],
                    market=m["market"],
                    asset=a,
                    question=m["question"],
                    outcome=m["outcome"],
                    rule=policy,
                ),
                horizon=horizon,
                delay=delay,
                quantity=qty,
                entry_price=s["price"],
                exit_time=at,
                exit_status=worst["status"],
                ambiguous_execution=s.get("ambiguous", False),
                hedge_admission_uncertain=s.get("hedge_admission_uncertain", False),
                hedge_strict=strict,
                hedge_possible=possible,
                hedge_price=hp,
                hedge_evidence=list(s.get("hedge_evidence", [])),
                entry_evidence=list(s.get("fill_evidence", [])),
                quote_decision_raw_line=s.get("decision_line"),
                posted=s.get("posted"),
                hedge_posted=s.get("hedge_posted"),
                possible_cash_branches=branches,
                max_cash_required=max_cash,
                forced_gross_size=fallback_gross_quantity(rate, exponent),
                buy_fee_collection="OUTCOME_SHARES",
                fee_parameters_verified=fd is not None,
                net_per_share=net / qty,
                net_usdc=net,
                double_reserve_net_per_share=net / qty - 0.004,
                stress_net_per_share=net / qty,
            )
        )
        s["stage"] = "IDLE"
        s["hedge_stage"] = "CLOSED"
        s["last"] = at
        s["version"] += 1


def summarize(rows):
    summaries = aggregate(rows)
    for r in summaries:
        rs = [
            x
            for x in rows
            if (x["signal"]["market"], x["signal"]["rule"], x["horizon"], x["delay"])
            == (r["market"], r["rule"], r["horizon"], r["delay"])
        ]
        r.pop("total_net_5shares", None)
        r["total_net_usdc"] = sum(x["net_usdc"] for x in rs)
        r["mean_double_reserve_net"] = sum(x["double_reserve_net_per_share"] for x in rs) / len(rs)
        r["strict_entry_positions"] = sum(not x["ambiguous_execution"] for x in rs)
        r["paired_maker_positions"] = sum(x["exit_status"] == "PAIRED_MAKERS" for x in rs)
        r["max_cash_required"] = max(x["max_cash_required"] for x in rs)
        cumulative = 0.0
        minimum_prefix = 0.0
        peak = 0.0
        drawdown = 0.0
        for x in sorted(rs, key=lambda row: row["exit_time"]):
            minimum_prefix = min(minimum_prefix, cumulative)
            cumulative += x["net_usdc"]
            peak = max(peak, cumulative)
            drawdown = max(drawdown, peak - cumulative)
        r["required_initial_cash_usdc"] = 16 - minimum_prefix
        r["max_drawdown_usdc"] = drawdown
        elapsed = max(x["exit_time"] for x in rs) - min(x["signal"]["decision"] for x in rs)
        r["net_after_allocated_100usd_hurdle"] = sum(
            x["net_usdc"] for x in rs
        ) - 100 * 0.08 * elapsed / (365.25 * 86400)

        blocks = collections.defaultdict(float)
        for x in rs:
            blocks[int(x["signal"]["time"] // 300)] += max(0, x["net_usdc"])
        r["largest_positive_block_fraction"] = (
            max(blocks.values()) / sum(blocks.values()) if sum(blocks.values()) else 1.0
        )
    return summaries


def run(root, train_only=False):
    root = Path(root)
    meta = json.loads((root / "markets.json").read_text())
    fees_raw = json.loads((root / "fees_provenance.json").read_text())["markets"]
    fees = {
        r["market"]: r["clob_info"]["fd"] for r in fees_raw if r.get("effective_parameters_agree")
    }
    with (root / "raw.jsonl").open() as f:
        start = json.loads(next(f))["received"]
        end = start
        for line in f:
            end = json.loads(line)["received"]
    split = start + 3600
    end = min(end, split) if train_only else end
    decision_end = (split if train_only else end) - 205
    selected = root / "paired_frozen_selection.json"
    if not train_only and not selected.exists():
        raise RuntimeError("No prospective paired selection")
    engine = PairedReplay(meta, fees, end, decision_end)
    for line in (root / "raw.jsonl").open():
        r = json.loads(line)
        if r["received"] > end:
            break
        engine.event(r)
    rows, checks = engine.finish()
    if train_only:
        summary = summarize(rows)
        eligible = []
        for s in summary:
            if s["delay"] != 0.5 or s["n"] < 10 or not s["fee_parameters_verified"]:
                continue
            peers = [
                x
                for x in summary
                if (x["rule"], x["horizon"], x["market"]) == (s["rule"], s["horizon"], s["market"])
            ]
            if len(peers) == 3 and all(
                x["n"] >= 10
                and x["mean_net"] > 0
                and x["required_initial_cash_usdc"] <= 100
                and x["net_after_allocated_100usd_hurdle"] > 0
                for x in peers
            ):
                eligible.append(s)
        candidate = max(eligible, key=lambda s: s["mean_net"]) if eligible else None
        stage = "paired_training"
        frozen = dict(
            split=split,
            candidate=candidate,
            protocol_sha256=hashlib.sha256(
                Path(__file__).with_name("PROTOCOL_V4_PAIRED_MAKERS.md").read_bytes()
            ).hexdigest(),
            evaluator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        )
        with selected.open("x") as f:
            json.dump(frozen, f, indent=2)
    else:
        frozen = json.loads(selected.read_text())
        candidate = frozen["candidate"]
        stage = "paired_holdout"
        if frozen["evaluator_sha256"] != hashlib.sha256(Path(__file__).read_bytes()).hexdigest():
            raise RuntimeError("Paired code changed after freeze")
        if (
            frozen["protocol_sha256"]
            != hashlib.sha256(
                Path(__file__).with_name("PROTOCOL_V4_PAIRED_MAKERS.md").read_bytes()
            ).hexdigest()
        ):
            raise RuntimeError("Paired protocol changed after freeze")
        rows = [
            r
            for r in rows
            if candidate is not None
            and r["signal"]["time"] >= max(split + 135, frozen.get("frozen_at", split) + 135)
            and (r["signal"]["market"], r["signal"]["rule"], r["horizon"])
            == (candidate["market"], candidate["rule"], candidate["horizon"])
        ]
        summary = summarize(rows)
    (root / f"{stage}.json").write_text(
        json.dumps(
            dict(
                bounds=dict(start=start, end=end, split=split),
                checks=checks,
                candidate=candidate,
                summary=summary,
                rows=rows,
                qualification="Counterfactual paired maker execution with conservative ambiguity bounds; no actual fills or proven edge asserted.",
            ),
            indent=2,
        )
    )
    print(
        json.dumps(
            dict(stage=stage, candidate=candidate, checks=checks, summary=summary[:8]), indent=2
        )
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("root", type=Path)
    p.add_argument("--train-only", action="store_true")
    a = p.parse_args()
    run(a.root, a.train_only)
