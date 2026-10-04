"""B3 research replay: retain known resting risk-reducing BUY orders on invalid books."""
from __future__ import annotations

import csv
import json
from collections import Counter
from statistics import mean, median

from cip_b1_backtest import AUDIT, CAPITAL, CLIP, DATA, LABELS, M4A_AUDIT, ROOT, TARGET, write_csv
from cip_b2_two_phase_backtest import MARKET_S, SWITCH_S, TwoPhaseReplay
from r0_rolling_pair_replay import percentile

OUT = ROOT / "research_outputs/cip_b3_20261003"
B2_OUT = ROOT / "research_outputs/cip_b2_20261003"


def read_csv(path):
    with path.open(newline="") as stream:
        return list(csv.DictReader(stream))


def num(value):
    return float(value) if value not in (None, "") else None


class RetentionReplay(TwoPhaseReplay):
    def __init__(self, *args, episode_rows, **kwargs):
        super().__init__(*args, **kwargs)
        self.episode_rows = episode_rows
        self.open_episode = None
        self.kept = {}
        self.counters = Counter()
        self.invalid_risk_fills = []
        self.lifecycle_ambiguities = []

    def invalid_reasons(self):
        reasons = []
        for side in ("YES", "NO"):
            book = self.books[side]
            if book.bid is None:
                reasons.append(f"MISSING_{side}_BID")
            if book.ask is None:
                reasons.append(f"MISSING_{side}_ASK")
            if book.bid is not None and book.ask is not None and book.bid >= book.ask:
                reasons.append("CROSSED_BOOK")
            if book.tick_int <= 0:
                reasons.append("OTHER")
        return sorted(set(reasons)) or ["OTHER"]

    def _close_episode(self, t):
        episode = self.open_episode
        if episode is None:
            return
        episode["end_time_us"] = t
        episode["duration_s"] = (t - episode["start_time_us"]) / 1_000_000
        episode["reason"] = "|".join(sorted(episode.pop("reasons")))
        episode["b3_action"] = "|".join(sorted(episode.pop("b3_actions")))
        self.episode_rows.append(episode)
        self.open_episode = None

    def _observe_invalid(self, t):
        if self.valid_books() or t >= self.end:
            self._close_episode(t)
            return
        if self.open_episode is None:
            net = self.values()[0]
            yes, no = self.orders["YES"], self.orders["NO"]
            reducing = "NO" if net > 0 else "YES" if net < 0 else None
            increasing = "YES" if net > 0 else "NO" if net < 0 else None
            self.open_episode = {
                "market": self.slug, "start_time_us": t, "phase": self.phase,
                "net_inventory_at_start": net,
                "existing_yes_order": yes["order_id"] if yes else None,
                "existing_no_order": no["order_id"] if no else None,
                "risk_reducing_order_present": bool(reducing and self.orders[reducing]),
                "risk_increasing_order_present": bool(increasing and self.orders[increasing]),
                "b2_action": "CANCEL_ALL_EXISTING_ORDERS",
                "reasons": set(), "b3_actions": set(),
            }
            self.counters["BOOK_INVALID_EPISODES"] += 1
        self.open_episode["reasons"].update(self.invalid_reasons())

    def reconcile(self, t, event_kind, inventory_changed=False):
        self._observe_invalid(t)
        if self.valid_books() or t >= self.end:
            return super().reconcile(t, event_kind, inventory_changed)
        net = self.values()[0]
        self.abs_samples.append(abs(net))
        self.reduce_blocked = False
        if self.phase == "REDUCE_ONLY" and net == 0:
            if self.t150 is not None and self.t150["net_t150"] != 0 and self.first_flat_us is None:
                self.first_flat_us = t
            self.reduce_stopped = True
        reducing = "NO" if net > 0 else "YES" if net < 0 else None
        for side in ("YES", "NO"):
            order = self.orders[side]
            if order is None:
                continue
            if order["status"] not in ("submitted", "active", "cancel_pending"):
                self.lifecycle_ambiguities.append({"market": self.slug, "timestamp_us": t,
                                                   "order_id": order["order_id"],
                                                   "status": order["status"]})
            known_resting = order["accepted_us"] is not None and order["status"] == "active" \
                and order["cancel_request_us"] is None
            if side == reducing and known_resting and not self.reduce_stopped:
                key = order["order_id"]
                if key not in self.kept:
                    self.kept[key] = {"market": self.slug, "order_id": key,
                                      "side": side, "price": order["price"],
                                      "original_accept_time_us": order["accepted_us"],
                                      "first_book_invalid_time_us": t,
                                      "phase_at_first_keep": self.phase,
                                      "net_at_first_keep": net}
                    self.counters["RISK_REDUCING_ORDERS_KEPT"] += 1
                self.open_episode["b3_actions"].add("KEEP_REDUCING")
                continue
            # Existing cancel-pending lifecycle remains authoritative. A submitted
            # order is not assumed resting or accepted merely because it was sent.
            if order["cancel_request_us"] is not None:
                continue
            if net == 0:
                self.counters["FLAT_STATE_ORDERS_CANCELLED"] += 1
                action = "CANCEL_FLAT"
            elif side != reducing:
                self.counters["RISK_INCREASING_ORDERS_CANCELLED"] += 1
                action = "CANCEL_INCREASING"
            else:
                action = "CANCEL_UNACCEPTED_REDUCING"
            self.open_episode["b3_actions"].add(action)
            self.cancel(side, t, "BOOK_INVALID", event_kind)
        missing_opportunity = (
            (reducing is not None and self.orders[reducing] is None and not self.reduce_stopped)
            or (self.phase == "GAMMA_HARVEST" and net == 0 and self.values()[1] < TARGET
                and any(self.orders[side] is None for side in ("YES", "NO")))
        )
        if missing_opportunity:
            self.counters["NEW_ORDER_BLOCKED_DURING_INVALID"] += 1
            self.open_episode["b3_actions"].add("FREEZE_NEW_ORDER")

    def cancel(self, side, t, reason, event_kind):
        order = self.orders[side]
        if reason == "BOOK_INVALID" and order is not None and order["cancel_request_us"] is None:
            net = self.values()[0]
            if ((net > 0 and side == "NO") or (net < 0 and side == "YES")) \
                    and order["accepted_us"] is not None and order["status"] == "active":
                self.counters["BOOK_INVALID_KEEP_VIOLATIONS"] += 1
        super().cancel(side, t, reason, event_kind)

    def apply_fill(self, order, qty, event, trade_px):
        before = self.values()[0]
        invalid = not self.valid_books()
        changed = super().apply_fill(order, qty, event, trade_px)
        after = self.values()[0]
        if invalid and abs(after) > abs(before) + 1e-9:
            self.invalid_risk_fills.append({
                "market": self.slug, "order_id": order["order_id"],
                "timestamp_us": event["available_at_us"], "side": order["side"],
                "net_before": before, "net_after": after,
                "risk_increasing_shares": abs(after) - abs(before),
                "cancel_pending_at_fill": self.fill_rows[-1]["cancel_pending_at_fill"],
                "accepted_us": order["accepted_us"], "status": order["status"],
            })
        return changed

    def result(self):
        self._close_episode(self.end)
        row = super().result()
        row.update({key.lower(): self.counters[key] for key in (
            "BOOK_INVALID_EPISODES", "RISK_REDUCING_ORDERS_KEPT",
            "RISK_INCREASING_ORDERS_CANCELLED", "FLAT_STATE_ORDERS_CANCELLED",
            "NEW_ORDER_BLOCKED_DURING_INVALID", "BOOK_INVALID_KEEP_VIOLATIONS")})
        row["book_invalid_total_seconds"] = sum(e["duration_s"] for e in self.episode_rows
                                                   if e["market"] == self.slug)
        row["risk_increasing_fills_during_invalid"] = len(self.invalid_risk_fills)
        row["risk_increasing_shares_during_invalid"] = sum(
            x["risk_increasing_shares"] for x in self.invalid_risk_fills)
        return row


class Phase2OnlyDiagnostic(RetentionReplay):
    """Attribution control: exact B2 gamma path, B3 rule only after T150."""

    def cancel(self, side, t, reason, event_kind):
        if self.phase == "GAMMA_HARVEST":
            return TwoPhaseReplay.cancel(self, side, t, reason, event_kind)
        return super().cancel(side, t, reason, event_kind)

    def reconcile(self, t, event_kind, inventory_changed=False):
        if self.phase == "GAMMA_HARVEST":
            return TwoPhaseReplay.reconcile(self, t, event_kind, inventory_changed)
        return super().reconcile(t, event_kind, inventory_changed)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    selected = [r for r in read_csv(AUDIT / "dataset_audit.csv")
                if r["execution_replay_usable"].lower() == "true"]
    labels = read_csv(LABELS)
    b2_markets = {r["market"]: r for r in read_csv(B2_OUT / "market_results.csv")}
    if len(selected) != 32 or len({r["market"] for r in selected}) != 32 \
            or {r["market"] for r in selected} != {r["market"] for r in labels} \
            or {r["market"] for r in selected} != set(b2_markets):
        raise RuntimeError("32-market B2/label/capture universe mismatch")
    labels = {r["market"]: r for r in labels}
    lat = json.loads((AUDIT / "summary.json").read_text())
    integrity = {r["session_id"]: r for r in json.loads(M4A_AUDIT.read_text())["sessions"]}
    orders, fills, paths, actions, transitions, episodes, markets, sims = [], [], [], [], [], [], [], []
    control_orders, control_fills, control_paths, control_actions = [], [], [], []
    control_transitions, control_episodes, control_markets = [], [], []
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
        sim = RetentionReplay(row["market"], start, label["winner"],
                              int(lat["activation_latency_us"]), int(lat["cancel_latency_us"]),
                              orders, fills, paths, actions, transitions, episode_rows=episodes)
        result = sim.replay(path, {manifest["yes_token_id"]: "YES", manifest["no_token_id"]: "NO"})
        result["session"] = row["session"]
        result["m4a_integrity"] = integrity[row["session"]]["integrity"]
        markets.append(result)
        sims.append(sim)
        control = Phase2OnlyDiagnostic(row["market"], start, label["winner"],
                                       int(lat["activation_latency_us"]), int(lat["cancel_latency_us"]),
                                       control_orders, control_fills, control_paths, control_actions,
                                       control_transitions, episode_rows=control_episodes)
        control_markets.append(control.replay(path, {
            manifest["yes_token_id"]: "YES", manifest["no_token_id"]: "NO"}))
    b2_orders = {o["order_id"]: o for o in read_csv(B2_OUT / "orders.csv")}
    b2_fills = {f["order_id"] for f in read_csv(B2_OUT / "fills.csv")}
    b3_fills = {f["order_id"]: f for f in fills}
    kept_rows = []
    for sim in sims:
        for order_id, info in sim.kept.items():
            order = next(o for o in orders if o["order_id"] == order_id)
            b2_order = b2_orders.get(order_id)
            exact_b2_order = bool(b2_order and b2_order["side"] == order["side"]
                                  and num(b2_order["price"]) == order["price"]
                                  and int(b2_order["accepted_us"]) == order["accepted_us"])
            b2_cancel = (int(b2_order["cancel_request_us"]) if exact_b2_order and
                         b2_order["cancel_reason"] == "BOOK_INVALID" and
                         b2_order["cancel_request_us"] else None)
            b2_effective = (int(b2_order["cancel_effective_us"]) if b2_cancel else None)
            fill = b3_fills.get(order_id)
            direct_extra = bool(fill and b2_cancel is not None and b2_effective is not None
                                and int(fill["fill_available_us"]) >= b2_effective
                                and order_id not in b2_fills)
            policy_cancel = info["first_book_invalid_time_us"]
            policy_effective = max(policy_cancel + sim.cancel_us, order["activation_us"])
            same_state_extra = bool(fill and int(fill["fill_available_us"]) >= policy_effective)
            before = num(fill["net_before"]) if fill else None
            after = num(fill["net_after"]) if fill else None
            b2_closed = int(b2_order["closed_us"]) if exact_b2_order else None
            invalid_survived = sum(max(0, min(order["closed_us"], e["end_time_us"]) -
                                       max(info["first_book_invalid_time_us"], e["start_time_us"]))
                                   for e in episodes if e["market"] == sim.slug)
            kept_rows.append({**info, "order_status_final": order["status"],
                              "order_closed_us": order["closed_us"],
                              "total_resting_lifetime_s": (order["closed_us"] - order["accepted_us"]) / 1e6,
                              "book_invalid_time_survived_s": invalid_survived / 1e6,
                              "time_remaining_at_first_invalid_s": (sim.end - info["first_book_invalid_time_us"]) / 1e6,
                              "b2_exact_order_match": exact_b2_order,
                              "b2_cancel_time_us": b2_cancel,
                              "b2_cancel_effective_us": b2_effective,
                              "b2_policy_cancel_time_us_on_b3_state": policy_cancel,
                              "b2_policy_cancel_effective_us_on_b3_state": policy_effective,
                              "b3_fill_time_us": int(fill["fill_available_us"]) if fill else None,
                              "extra_resting_seconds": (order["closed_us"] - b2_closed) / 1e6 if b2_closed else None,
                              "extra_resting_seconds_same_state": (order["closed_us"] - policy_effective) / 1e6,
                              "inventory_before_fill": before,
                              "inventory_after_fill": after,
                              "residual_reduced_shares": (abs(before) - abs(after)) if fill else None,
                              "direct_extra_fill_vs_exact_b2_order": direct_extra,
                              "same_state_b2_policy_would_cancel_before_fill": same_state_extra,
                              "incremental_pnl_effect_direct": (((1 if order["side"] == sim.winner else 0) - order["price"]) * num(fill["size"])) if fill and same_state_extra else None,
                              "later_qualifying_sell_print": None})
    # Evaluate the frozen trade-through predicate for kept orders that remained unfilled.
    sessions = {r["market"]: r["session"] for r in selected}
    token_by_market = {}
    for market, session in sessions.items():
        manifest = json.loads((DATA / session / "manifest.json").read_text())
        token_by_market[market] = {"YES": manifest["yes_token_id"], "NO": manifest["no_token_id"]}
    orders_by_id = {o["order_id"]: o for o in orders}
    unfilled_by_market = {}
    for row in kept_rows:
        if row["b3_fill_time_us"] is None:
            row["later_qualifying_sell_print"] = False
            unfilled_by_market.setdefault(row["market"], []).append(row)
    for market, candidates in unfilled_by_market.items():
        with (DATA / sessions[market] / "events.jsonl").open() as stream:
            for line in stream:
                event = json.loads(line)
                if event.get("kind") != "TRADE":
                    continue
                payload = event.get("payload") or {}
                if payload.get("side") != "SELL":
                    continue
                t, exch, recv = (event.get(k) for k in
                                 ("available_at_us", "exchange_at_us", "received_at_us"))
                if None in (t, exch, recv):
                    continue
                trade_px = int(round(float(payload["price"]) * 10000))
                for row in candidates:
                    order = orders_by_id[row["order_id"]]
                    if (payload.get("token_id") == token_by_market[market][row["side"]]
                            and order["activation_us"] < min(t, exch, recv)
                            and row["first_book_invalid_time_us"] <= t < order["closed_us"]
                            and trade_px < order["price_int"]):
                        row["later_qualifying_sell_print"] = True
    write_csv(OUT / "market_results.csv", markets)
    write_csv(OUT / "orders.csv", orders)
    fill_fields = list(dict.fromkeys(key for row in fills for key in row))
    write_csv(OUT / "fills.csv", fills, fields=fill_fields)
    write_csv(OUT / "inventory_paths.csv", paths)
    write_csv(OUT / "book_invalid_episodes.csv", episodes)
    write_csv(OUT / "kept_reduce_orders.csv", kept_rows)
    write_csv(OUT / "action_audit.csv", actions)
    reduce_orders = [o for o in orders if o["submitted_us"] >=
                     (int(o["market"].rsplit("-", 1)[1]) + SWITCH_S) * 1_000_000]
    accepted = [o for o in reduce_orders if o["accepted_us"] is not None]
    reduce_filled = [o for o in accepted if o["status"] == "filled"]
    reduce_unfilled = [o for o in accepted if o["status"] != "filled"]
    resting = [(o["closed_us"] - o["accepted_us"]) / 1e6 for o in accepted]
    nofill_resting = [(o["closed_us"] - o["accepted_us"]) / 1e6 for o in reduce_unfilled]
    flat_times = [r["time_to_flat_seconds"] for r in markets if r["time_to_flat_seconds"] is not None]
    risk_fills = [x for sim in sims for x in sim.invalid_risk_fills]
    errors = {r["market"]: r["data_error"] for r in markets if r["data_error"]}
    invariant_issues = []
    if len(transitions) != 32 or any(r["phase_at_end"] != "REDUCE_ONLY" for r in markets):
        invariant_issues.append("T150 phase transition")
    if sum(s.counters["BOOK_INVALID_KEEP_VIOLATIONS"] for s in sims):
        invariant_issues.append("BOOK_INVALID_KEEP_VIOLATIONS")
    if any(s.lifecycle_ambiguities for s in sims):
        invariant_issues.append("unknown order lifecycle state")
    if any(r["bbo_only_reprice_count"] for r in markets):
        invariant_issues.append("BBO_ONLY_REPRICE")
    if any(a["action"] in ("PLACE", "REPLACE") and not a["book_valid"] for a in actions):
        invariant_issues.append("new order while book invalid")
    if any(a["action"] in ("PLACE", "REPLACE") and a["phase"] == "REDUCE_ONLY" and
           (a["net_inventory"] == 0 or (a["net_inventory"] > 0 and a["side"] != "NO") or
            (a["net_inventory"] < 0 and a["side"] != "YES")) for a in actions):
        invariant_issues.append("reduce-only side violation")
    b2_prefix = {(o["market"], o["submitted_us"], o["side"], o["price"])
                 for o in b2_orders.values() if int(o["submitted_us"]) <
                 (int(o["market"].rsplit("-", 1)[1]) + SWITCH_S) * 1_000_000}
    b3_prefix = {(o["market"], str(o["submitted_us"]), o["side"], str(o["price"]))
                 for o in orders if o["submitted_us"] <
                 (int(o["market"].rsplit("-", 1)[1]) + SWITCH_S) * 1_000_000}
    if not b3_prefix.issubset(b2_prefix):
        # Pre-T150 divergence is permitted only as a downstream consequence of
        # the invalid-book retention rule; report it explicitly for review.
        pass
    b2_summary = json.loads((B2_OUT / "summary.json").read_text())
    b2_metrics = b2_summary["metrics"]
    control_prefix = {(o["market"], str(o["submitted_us"]), o["side"], str(o["price"]))
                      for o in control_orders if o["submitted_us"] <
                      (int(o["market"].rsplit("-", 1)[1]) + SWITCH_S) * 1_000_000}
    control_t150 = sum(r["residual_t150"] for r in control_markets)
    if control_prefix != b2_prefix or control_t150 != b2_metrics["residual_t150"]:
        invariant_issues.append("phase2 diagnostic B2 prefix/T150 mismatch")
    control_reduce_orders = [o for o in control_orders if o["submitted_us"] >=
                             (int(o["market"].rsplit("-", 1)[1]) + SWITCH_S) * 1_000_000]
    control_accepted = [o for o in control_reduce_orders if o["accepted_us"] is not None]
    control_metrics = {
        "t150_residual": control_t150,
        "reduce_fills": sum(r["reduce_fills_150_end"] for r in control_markets),
        "reduce_shares": sum(r["reduce_shares_150_end"] for r in control_markets),
        "reduce_orders_accepted": len(control_accepted),
        "reduce_orders_filled": sum(o["status"] == "filled" for o in control_accepted),
        "risk_reducing_orders_kept": sum(r["risk_reducing_orders_kept"] for r in control_markets),
        "reduce_cancel_race_added_shares": sum(r["reduce_cancel_race_added_shares"] for r in control_markets),
        "final_residual": sum(r["residual_final"] for r in control_markets),
        "residual_reduction": sum(r["residual_reduction"] for r in control_markets),
        "gamma_pnl_final": sum(r["gamma_pnl_final"] for r in control_markets),
        "residual_pnl": sum(r["residual_pnl"] for r in control_markets),
        "total_pnl": sum(r["total_pnl"] for r in control_markets),
        "risk_increasing_fills_during_invalid": sum(r["risk_increasing_fills_during_invalid"] for r in control_markets),
        "book_invalid_keep_violations": sum(r["book_invalid_keep_violations"] for r in control_markets),
    }
    metrics = {
        "book_invalid_episodes": len(episodes),
        "book_invalid_total_seconds": sum(e["duration_s"] for e in episodes),
        "book_invalid_reasons": dict(Counter(reason for e in episodes for reason in e["reason"].split("|"))),
        "risk_reducing_orders_kept": sum(s.counters["RISK_REDUCING_ORDERS_KEPT"] for s in sims),
        "risk_increasing_orders_cancelled": sum(s.counters["RISK_INCREASING_ORDERS_CANCELLED"] for s in sims),
        "flat_state_orders_cancelled": sum(s.counters["FLAT_STATE_ORDERS_CANCELLED"] for s in sims),
        "new_order_blocked_during_invalid": sum(s.counters["NEW_ORDER_BLOCKED_DURING_INVALID"] for s in sims),
        "book_invalid_keep_violations": sum(s.counters["BOOK_INVALID_KEEP_VIOLATIONS"] for s in sims),
        "reduce_orders_submitted": len(reduce_orders), "reduce_orders_accepted": len(accepted),
        "reduce_orders_filled": len(reduce_filled), "reduce_orders_no_fill": len(reduce_unfilled),
        "reduce_fill_rate": len(reduce_filled) / len(accepted) if accepted else None,
        "fills_per_100_reduce_submissions": 100 * len(reduce_filled) / len(reduce_orders) if reduce_orders else None,
        "reduce_resting_median_s": median(resting) if resting else None,
        "reduce_resting_mean_s": mean(resting) if resting else None,
        "reduce_resting_p75_s": percentile(resting, .75),
        "reduce_resting_p90_s": percentile(resting, .90),
        "reduce_no_fill_resting_median_s": median(nofill_resting) if nofill_resting else None,
        "reduce_no_fill_resting_mean_s": mean(nofill_resting) if nofill_resting else None,
        "reduce_no_fill_resting_p75_s": percentile(nofill_resting, .75),
        "reduce_no_fill_resting_p90_s": percentile(nofill_resting, .90),
        "direct_extra_fills_from_exact_b2_canceled_orders": sum(r["direct_extra_fill_vs_exact_b2_order"] for r in kept_rows),
        "direct_extra_reduce_fills_from_exact_b2_canceled_orders": sum(r["direct_extra_fill_vs_exact_b2_order"] and r["phase_at_first_keep"] == "REDUCE_ONLY" for r in kept_rows),
        "same_state_extra_fills_after_b2_policy_cancel": sum(r["same_state_b2_policy_would_cancel_before_fill"] for r in kept_rows),
        "same_state_extra_reduce_fills_after_b2_policy_cancel": sum(r["same_state_b2_policy_would_cancel_before_fill"] and r["phase_at_first_keep"] == "REDUCE_ONLY" for r in kept_rows),
        "risk_reducing_orders_kept_phase1": sum(r["phase_at_first_keep"] == "GAMMA_HARVEST" for r in kept_rows),
        "risk_reducing_orders_kept_phase2": sum(r["phase_at_first_keep"] == "REDUCE_ONLY" for r in kept_rows),
        "t150_residual": sum(r["residual_t150"] for r in markets),
        "gamma_phase_fills": sum(r["fills_0_150"] for r in markets),
        "reduce_fills": sum(r["reduce_fills_150_end"] for r in markets),
        "reduce_shares": sum(r["reduce_shares_150_end"] for r in markets),
        "final_residual": sum(r["residual_final"] for r in markets),
        "residual_reduction": sum(r["residual_reduction"] for r in markets),
        "residual_reduction_rate": 1 - sum(r["residual_final"] for r in markets) /
        sum(r["residual_t150"] for r in markets),
        "markets_flat_by_end_from_nonflat_t150": sum(r["flat_status"] == "FLAT_AFTER_SWITCH" for r in markets),
        "median_time_to_flat_s": median(flat_times) if flat_times else None,
        "gamma_pnl_t150": sum(r["gamma_pnl_t150"] for r in markets),
        "gamma_pnl_final": sum(r["gamma_pnl_final"] for r in markets),
        "residual_pnl": sum(r["residual_pnl"] for r in markets),
        "total_pnl": sum(r["total_pnl"] for r in markets),
        "reduce_capital_blocked": sum(r["reduce_capital_blocked_events"] for r in markets),
        "reduce_cancel_race_overshoot_events": sum(r["reduce_cancel_race_overshoot_events"] for r in markets),
        "reduce_cancel_race_added_shares": sum(r["reduce_cancel_race_added_shares"] for r in markets),
        "risk_increasing_fills_during_invalid": len(risk_fills),
        "risk_increasing_shares_during_invalid": sum(x["risk_increasing_shares"] for x in risk_fills),
        "risk_increasing_fill_explanations": risk_fills,
        "lifecycle_ambiguities": sum(len(s.lifecycle_ambiguities) for s in sims),
        "bbo_only_reprice_count": sum(r["bbo_only_reprice_count"] for r in markets),
    }
    if errors or invariant_issues:
        classification = "IMPLEMENTATION INVALID"
    elif risk_fills and metrics["risk_increasing_shares_during_invalid"] > b2_metrics.get(
            "risk_increasing_shares_during_invalid", 0):
        classification = "SAFETY TRADEOFF UNACCEPTABLE"
    elif metrics["reduce_fills"] <= b2_metrics["reduce_fills"]:
        classification = "RETENTION DOES NOT IMPROVE FILLS" if metrics["risk_reducing_orders_kept"] else "NO MATERIAL BENEFIT VS B2"
    elif metrics["total_pnl"] > b2_metrics["total_pnl"] + 10 and metrics["final_residual"] < b2_metrics["residual_final"]:
        classification = ("EXECUTION IMPROVES BUT RESIDUAL STILL DOMINATES" if
                          metrics["residual_pnl"] < 0 and abs(metrics["residual_pnl"]) >
                          abs(metrics["gamma_pnl_final"]) else "BOOK-INVALID RETENTION SUPPORTED")
    elif metrics["reduce_capital_blocked"] > b2_metrics["reduce_capital_blocked_events"]:
        classification = "CAPITAL BECOMES PRIMARY BOTTLENECK"
    else:
        classification = "NO MATERIAL BENEFIT VS B2"
    summary = {"classification": classification,
               "sample": {"markets": len(markets), "official_settlement_labels": len(labels),
                          "m4a_integrity_pass_overlap": sum(r["m4a_integrity"] == "PASS" for r in markets),
                          "data_errors": errors},
               "frozen_parameters": {"capital_usd": CAPITAL, "clip_shares": CLIP,
                                     "gamma_phase_end_s": SWITCH_S, "market_end_s": MARKET_S},
               "latencies_us": {"submit": lat["activation_latency_us"],
                                "cancel": lat["cancel_latency_us"]},
               "b2_baseline": b2_metrics, "metrics": metrics,
               "phase2_only_diagnostic": control_metrics,
               "invariant_issues": invariant_issues,
               "phase1_order_prefix_exact_match": b3_prefix == b2_prefix,
               "attribution_note": "Direct extra fills require exact B2 order identity, quote and acceptance time plus B2 BOOK_INVALID cancellation effective before B3 fill; unmatched divergent paths are not claimed direct.",
               "book_reason_limit": "Only missing bid/ask and crossed BBO are derivable from frozen R0 Book.valid; stale, sequence gap and market inactive are not inferable from this book representation."}
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({"classification": classification, "sample": summary["sample"],
                      "metrics": metrics, "invariant_issues": invariant_issues}, indent=2))


if __name__ == "__main__":
    main()
