"""Conservative counterfactual maker replay. PROTOCOL_V3_PASSIVE.md governs claims."""

import argparse
import collections
import hashlib
import heapq
import json
import math
from pathlib import Path

from execution_study import aggregate, vwap
from study import Book


class Replay:
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
                    cancel_at=when + 5 + delay,
                    quantity=0.0,
                    first_fill=None,
                    ambiguous=False,
                    post_state_line=self.book_line.get(a),
                    post_bid=q["bid"],
                    post_ask=q["ask"],
                )
                self.timer(s["cancel_at"] + 2.25, s, "DRAIN")
            elif action == "DRAIN":
                if s["stage"] == "LIVE" and s["quantity"] == 0:
                    s["stage"] = "IDLE"
                    s["version"] += 1
                    self.checks["unfilled_quote"] += 1
            elif action == "EXIT":
                self.close(s, when)

    def position(self, s, quantity, at, ambiguous=False, evidence=None):
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

    def close(self, s, at):
        a, policy, horizon, delay = s["key"]
        qty = s["quantity"]
        if qty <= 0:
            return
        m = self.primary[a]
        price = s["price"]
        fd = self.fees.get(m["market"])
        exit_price = None
        reason = "OK"
        if qty < 5 - 1e-8:
            reason = "SUBMINIMUM_PARTIAL"
        elif self.generation != s["generation"] or not self.trusted(a):
            reason = "UNTRUSTED_EXIT"
        else:
            exit_price = vwap(self.books[a], "SELL", qty)
            if exit_price is None:
                reason = "INSUFFICIENT_DEPTH"
        exit_price = exit_price if exit_price is not None else 0.0
        fee = (
            fd["r"] * (exit_price * (1 - exit_price)) ** fd["e"]
            if fd is not None
            else 0.10 * exit_price * (1 - exit_price)
        )
        net = exit_price - price - fee - 0.002
        # Ambiguous shadow-crossing execution contributes losses but never positive returns.
        bounded = min(0.0, net) if s["ambiguous"] else net
        double = min(0.0, net - 0.002) if s["ambiguous"] else net - 0.002
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
                entry_price=price,
                exit_price=exit_price,
                exit_time=at,
                exit_status=reason,
                ambiguous_execution=s["ambiguous"],
                quote_decision_raw_line=s.get("decision_line"),
                post_state_raw_line=s.get("post_state_line"),
                post_bid=s.get("post_bid"),
                post_ask=s.get("post_ask"),
                posted=s.get("posted"),
                cancel_arrival=s.get("cancel_at"),
                fill_evidence=s.get("fill_evidence", []),
                fee_parameters_verified=fd is not None,
                effective_exit_fee_per_share=fee,
                net_per_share=bounded,
                net_usdc=qty * bounded,
                double_reserve_net_per_share=double,
                stress_net_per_share=exit_price
                - price
                - 0.10 * exit_price * (1 - exit_price)
                - 0.002,
            )
        )
        s["stage"] = "IDLE"
        s["version"] += 1

    def trade(self, event, at):
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

    def event(self, r):
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


def summarize(rows):
    summaries = aggregate(rows)
    for s in summaries:
        rs = [
            r
            for r in rows
            if (r["signal"]["market"], r["signal"]["rule"], r["horizon"], r["delay"])
            == (s["market"], s["rule"], s["horizon"], s["delay"])
        ]
        s.pop("total_net_5shares", None)
        s["total_net_usdc"] = sum(r["net_usdc"] for r in rs)
        s["strict_execution_positions"] = sum(not r["ambiguous_execution"] for r in rs)
        s["ambiguous_execution_positions"] = sum(r["ambiguous_execution"] for r in rs)
        s["mean_double_reserve_net"] = sum(r["double_reserve_net_per_share"] for r in rs) / len(rs)
        positives = collections.defaultdict(float)
        for r in rs:
            positives[int(r["signal"]["time"] // 300)] += max(0, r["net_usdc"])
        total_positive = sum(positives.values())
        s["largest_positive_block_fraction"] = (
            max(positives.values()) / total_positive if total_positive else 1.0
        )
    return summaries


def run(root, train_only=False):
    root = Path(root)
    metadata = json.loads((root / "markets.json").read_text())
    fees_raw = json.loads((root / "fees_provenance.json").read_text())["markets"]
    fees = {
        r["market"]: r["clob_info"]["fd"] for r in fees_raw if r.get("effective_parameters_agree")
    }
    with (root / "raw.jsonl").open() as f:
        start = json.loads(next(f))["received"]
        end = start
        for line in f:
            end = json.loads(line)["received"]
    split = start + 1800
    decision_end = split - 135 if train_only else end - 135
    selected = root / "passive_frozen_selection.json"
    if not train_only and not selected.exists():
        raise RuntimeError("No prospective frozen selection")
    engine = Replay(metadata, fees, min(end, split) if train_only else end, decision_end)
    for line in (root / "raw.jsonl").open():
        r = json.loads(line)
        if r["received"] > engine.end:
            break
        engine.event(r)
    rows, checks = engine.finish()
    if train_only:
        summaries = summarize(rows)
        candidates = []
        for s in summaries:
            if s["delay"] != 0.5 or s["n"] < 10 or not s["fee_parameters_verified"]:
                continue
            peers = [
                r
                for r in summaries
                if (r["rule"], r["horizon"], r["market"]) == (s["rule"], s["horizon"], s["market"])
            ]
            if len(peers) == 3 and all(r["n"] >= 10 and r["mean_net"] > 0 for r in peers):
                candidates.append(s)
        candidate = max(candidates, key=lambda r: r["mean_net"]) if candidates else None
        frozen = dict(
            split=split,
            candidate=candidate,
            protocol_sha256=hashlib.sha256(
                Path(__file__).with_name("PROTOCOL_V3_PASSIVE.md").read_bytes()
            ).hexdigest(),
            evaluator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        )
        with selected.open("x") as f:
            json.dump(frozen, f, indent=2)
        stage = "passive_training"
    else:
        frozen = json.loads(selected.read_text())
        candidate = frozen["candidate"]
        stage = "passive_holdout"
        if frozen["evaluator_sha256"] != hashlib.sha256(Path(__file__).read_bytes()).hexdigest():
            raise RuntimeError("Passive evaluator changed after freeze")
        if (
            frozen["protocol_sha256"]
            != hashlib.sha256(
                Path(__file__).with_name("PROTOCOL_V3_PASSIVE.md").read_bytes()
            ).hexdigest()
        ):
            raise RuntimeError("Passive protocol changed after freeze")
        rows = [
            r
            for r in rows
            if candidate is not None
            and r["signal"]["time"] >= max(split + 122, frozen.get("frozen_at", split) + 122)
            and (r["signal"]["market"], r["signal"]["rule"], r["horizon"])
            == (candidate["market"], candidate["rule"], candidate["horizon"])
        ]
        summaries = summarize(rows)
    result = dict(
        bounds=dict(start=start, end=end, split=split),
        checks=checks,
        candidate=candidate,
        summary=summaries,
        rows=rows,
        qualification="Counterfactual paper fills and loss-only bounds for ambiguous shadow crosses; not real fills or a proven edge.",
    )
    (root / f"{stage}.json").write_text(json.dumps(result, indent=2))
    print(
        json.dumps(
            dict(stage=stage, checks=checks, candidate=candidate, summary=summaries[:8]), indent=2
        )
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("root", type=Path)
    p.add_argument("--train-only", action="store_true")
    a = p.parse_args()
    run(a.root, a.train_only)
