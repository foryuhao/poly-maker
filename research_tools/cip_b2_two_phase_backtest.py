"""Frozen B1 gamma harvest through T150, then maker BUY reduce-only to T300."""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from statistics import mean, median

from cip_b1_backtest import (AUDIT, CAPITAL, CLIP, DATA, LABELS, M4A_AUDIT,
                             ROOT, PoolReplay, write_csv)
from r0_rolling_pair_replay import percentile

OUT = ROOT / "research_outputs/cip_b2_20261003"
B1_OUT = ROOT / "research_outputs/cip_b1_20261003"
SWITCH_S = 150
MARKET_S = 300


class TwoPhaseReplay(PoolReplay):
    def __init__(self, slug, start, winner, activation_us, cancel_us,
                 order_rows, fill_rows, path_rows, action_rows, phase_rows):
        super().__init__(slug, start, winner, activation_us, cancel_us,
                         order_rows, fill_rows, path_rows, action_rows)
        self.phase = "GAMMA_HARVEST"
        self.switch_us = start + SWITCH_S * 1_000_000
        self.cutoff = self.end  # B2 reduce-only continues to the specified T300 end.
        self.phase_rows = phase_rows
        self.t150 = None
        self.reduce_stopped = False
        self.first_flat_us = None
        self.reduce_blocked = False
        self.reduce_blocked_us = 0
        self.reduce_blocked_net_us = 0.0
        self.reduce_blocked_max_net = 0.0
        self.reduce_blocked_nets = []
        self.gamma_fills = 0
        self.gamma_shares = {"YES": 0.0, "NO": 0.0}
        self.reduce_fills = 0
        self.reduce_shares = 0.0
        self.race_events = 0
        self.race_added_shares = 0.0
        self.race_max_single_increase = 0.0
        self.race_max_abs_after = 0.0
        self.sign_flip_events = 0
        self.sign_flip_excess_shares = 0.0
        self.sign_flip_max_excess = 0.0
        self.last_sign_flip = False
        self.action_start = 0

    def integrate(self, t):
        dt = t - self.last_t
        if self.phase == "REDUCE_ONLY" and self.reduce_blocked and dt > 0:
            self.reduce_blocked_us += dt
            self.reduce_blocked_net_us += abs(self.values()[0]) * dt
        super().integrate(t)

    def audit(self, t, action, reason, side=None, order=None, event_kind=None):
        net, matched, _, _, _ = self.values()
        row = {"market": self.slug, "timestamp_us": t, "phase": self.phase,
               "action": action, "reason_code": reason, "side": side,
               "order_id": order["order_id"] if order else None,
               "order_price": order["price"] if order else None,
               "remaining_qty": order["remaining_qty"] if order else None,
               "event_kind": event_kind, "book_valid": self.valid_books(),
               "yes_best_bid": self.books["YES"].bid / 10000 if self.books["YES"].bid is not None else None,
               "yes_best_ask": self.books["YES"].ask / 10000 if self.books["YES"].ask is not None else None,
               "no_best_bid": self.books["NO"].bid / 10000 if self.books["NO"].bid is not None else None,
               "no_best_ask": self.books["NO"].ask / 10000 if self.books["NO"].ask is not None else None,
               "net_inventory": net, "matched_qty": matched,
               "capital_used": self.capital()}
        self.action_rows.append(row)
        allowed = {"HEAVY_SIDE_RISK_RETREAT", "ORDER_REJECTED", "BOOK_INVALID",
                   "CAPITAL_CONSTRAINT", "TARGET_REACHED", "CUTOFF",
                   "PHASE_SWITCH_HEAVY", "REDUCE_BALANCED", "REDUCE_SIGN_CHANGE",
                   "REDUCE_HEAVY_SIDE", "REDUCE_STOPPED", "REDUCE_RISK",
                   "MARKET_END"}
        if action in ("CANCEL", "REPLACE") and reason not in allowed:
            self.counts["BBO_ONLY_REPRICE"] += 1
        self.counts[action] += 1

    def snapshot(self, t, fill_side, fill_price, fill_qty):
        super().snapshot(t, fill_side, fill_price, fill_qty)
        row = self.path_rows[-1]
        recent = [a for a in self.action_rows[self.action_start:]
                  if a["market"] == self.slug and a["timestamp_us"] == t]
        row["phase"] = self.phase
        row["elapsed_s"] = (t - self.start) / 1_000_000
        row["action"] = "|".join(a["action"] for a in recent)
        row["reason_code"] = "|".join(a["reason_code"] for a in recent)
        row["record_kind"] = "FILL" if fill_side else "PHASE_SWITCH" if t == self.switch_us else "FINAL"

    def switch_phase(self):
        if self.phase != "GAMMA_HARVEST" or self.t150 is not None:
            self.error = "duplicate or reversed phase transition"
            return
        t = self.switch_us
        net, matched, ay, an, pc = self.values()
        def open_order(side):
            o = self.orders[side]
            return (None, None, None, None) if o is None else (
                o["order_id"], o["price"], o["remaining_qty"], o["status"])
        yes_id, yes_price, yes_remaining, yes_status = open_order("YES")
        no_id, no_price, no_remaining, no_status = open_order("NO")
        self.t150 = {"qy_t150": self.qty["YES"], "qn_t150": self.qty["NO"],
                     "cy_t150": self.cost["YES"], "cn_t150": self.cost["NO"],
                     "net_t150": net, "residual_t150": abs(net),
                     "matched_t150": matched, "avg_y_t150": ay, "avg_n_t150": an,
                     "pool_pair_cost_t150": pc,
                     "gamma_pnl_t150": matched * (1 - pc) if pc is not None else 0.0,
                     "capital_used_t150": self.capital(),
                     "open_yes_order_id_t150": yes_id,
                     "open_yes_price_t150": yes_price,
                     "open_yes_remaining_t150": yes_remaining,
                     "open_yes_status_t150": yes_status,
                     "open_no_order_id_t150": no_id,
                     "open_no_price_t150": no_price,
                     "open_no_remaining_t150": no_remaining,
                     "open_no_status_t150": no_status}
        self.phase = "REDUCE_ONLY"
        self.action_start = len(self.action_rows)
        self.audit(t, "PHASE_SWITCH", "T150", None, None, "PHASE_SWITCH")
        self.reconcile(t, "PHASE_SWITCH")
        self.snapshot(t, None, None, None)
        self.phase_rows.append({"market": self.slug, "timestamp_us": t,
                                "from_phase": "GAMMA_HARVEST", "to_phase": "REDUCE_ONLY",
                                **self.t150,
                                "post_switch_yes_status": self.orders["YES"]["status"] if self.orders["YES"] else None,
                                "post_switch_no_status": self.orders["NO"]["status"] if self.orders["NO"] else None,
                                "post_switch_action": self.path_rows[-1]["action"],
                                "post_switch_reason_code": self.path_rows[-1]["reason_code"]})

    def reconcile(self, t, event_kind, inventory_changed=False):
        if self.phase == "GAMMA_HARVEST":
            return super().reconcile(t, event_kind, inventory_changed)
        net, _, _, _, _ = self.values()
        self.abs_samples.append(abs(net))
        self.reduce_blocked = False
        if t >= self.end:
            for side in ("YES", "NO"):
                self.cancel(side, t, "MARKET_END", event_kind)
            return
        if not self.valid_books():
            for side in ("YES", "NO"):
                self.cancel(side, t, "BOOK_INVALID", event_kind)
            return
        if net == 0:
            if self.t150 is not None and self.t150["net_t150"] != 0 and self.first_flat_us is None:
                self.first_flat_us = t
            self.reduce_stopped = True
            for side in ("YES", "NO"):
                self.cancel(side, t, "REDUCE_BALANCED", event_kind)
            return
        if self.reduce_stopped:
            for side in ("YES", "NO"):
                self.cancel(side, t, "REDUCE_STOPPED", event_kind)
            return
        heavy, lighter = ("YES", "NO") if net > 0 else ("NO", "YES")
        reason = ("PHASE_SWITCH_HEAVY" if event_kind == "PHASE_SWITCH" else
                  "REDUCE_SIGN_CHANGE" if self.last_sign_flip else "REDUCE_HEAVY_SIDE")
        self.cancel(heavy, t, reason, event_kind)
        self.last_sign_flip = False
        if self.orders[lighter] is not None:
            return  # Persistent lighter-side quote keeps its original price.
        price_int = self.desired_price_int(lighter, net)
        if price_int is None:
            self.counts["INVALID_PRICE"] += 1
            return
        price = price_int / 10000
        if self.capital() + CLIP * price > CAPITAL + 1e-8:
            self.counts["REDUCE_CAPITAL_BLOCKED"] += 1
            self.reduce_blocked = True
            self.reduce_blocked_nets.append(abs(net))
            self.reduce_blocked_max_net = max(self.reduce_blocked_max_net, abs(net))
            self.audit(t, "SKIP", "REDUCE_CAPITAL_BLOCKED", lighter, None, event_kind)
            return
        if (net > 0 and lighter != "NO") or (net < 0 and lighter != "YES"):
            self.error = "reduce-only new order increases risk"
            return
        self.place(lighter, t, price_int,
                   self.pending_replacement[lighter] or "REDUCE_RISK", event_kind)

    def apply_fill(self, o, qty, event, trade_px):
        before = self.values()[0]
        was_cancel_pending = o["cancel_request_us"] is not None
        super().apply_fill(o, qty, event, trade_px)
        after = self.values()[0]
        f = self.fill_rows[-1]
        f["phase"] = self.phase
        f["net_before"] = before
        f["net_after"] = after
        f["cancel_pending_at_fill"] = was_cancel_pending
        if self.phase == "GAMMA_HARVEST":
            self.gamma_fills += 1
            self.gamma_shares[o["side"]] += qty
            return before != after
        self.reduce_fills += 1
        self.reduce_shares += qty
        increase = abs(after) - abs(before)
        f["risk_increase_shares"] = max(0.0, increase)
        if increase > 1e-9:
            if not was_cancel_pending:
                self.error = "risk-increasing reduce fill without pending cancellation"
            else:
                self.race_events += 1
                self.race_added_shares += increase
                self.race_max_single_increase = max(self.race_max_single_increase, increase)
                self.race_max_abs_after = max(self.race_max_abs_after, abs(after))
                f["reason_code"] = "REDUCE_CANCEL_RACE_OVERSHOOT"
        if before * after < 0:
            self.sign_flip_events += 1
            self.sign_flip_excess_shares += abs(after)
            self.sign_flip_max_excess = max(self.sign_flip_max_excess, abs(after))
            self.last_sign_flip = True
            f["reason_code"] = "REDUCE_OVERSHOOT_SIGN_FLIP"
        return before != after

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
                if t >= self.switch_us and self.phase == "GAMMA_HARVEST":
                    self.integrate(self.switch_us)
                    self.advance(self.switch_us)
                    self.switch_phase()
                self.integrate(t)
                self.action_start = len(self.action_rows)
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
        if self.phase == "GAMMA_HARVEST":
            self.integrate(self.switch_us)
            self.advance(self.switch_us)
            self.switch_phase()
        self.integrate(self.end)
        self.action_start = len(self.action_rows)
        self.advance(self.end)
        self.reconcile(self.end, "MARKET_END")
        for side in ("YES", "NO"):
            if self.orders[side] is not None:
                self.close_order(side, self.end, "canceled")
        self.snapshot(self.end, None, None, None)
        if self.counts["NONMONOTONE"]:
            self.error = self.error or "nonmonotone event availability timestamps"
        self.check()
        return self.result()

    def result(self):
        row = super().result()
        assert self.t150 is not None
        net, matched, ay, an, pc = self.values()
        first_net = self.t150["net_t150"]
        reduction = abs(first_net) - abs(net)
        flat_status = ("ALREADY_FLAT_AT_SWITCH" if first_net == 0 else
                       "FLAT_AFTER_SWITCH" if self.first_flat_us is not None else "NOT_FLAT_BY_END")
        row.update(self.t150)
        row.update({"phase_at_end": self.phase,
                    "qy_final": self.qty["YES"], "qn_final": self.qty["NO"],
                    "cy_final": self.cost["YES"], "cn_final": self.cost["NO"],
                    "net_final": net, "residual_final": abs(net),
                    "matched_final": matched, "avg_y_final": ay, "avg_n_final": an,
                    "pool_pair_cost_final": pc, "gamma_pnl_final": row["pair_pnl"],
                    "capital_used_final": self.capital(),
                    "residual_reduction": reduction,
                    "residual_reduction_rate": 1 - abs(net) / abs(first_net) if first_net else None,
                    "flat_status": flat_status,
                    "time_to_flat_seconds": (self.first_flat_us - self.switch_us) / 1_000_000
                    if self.first_flat_us is not None else None,
                    "post_switch_matched_change": matched - self.t150["matched_t150"],
                    "matched_generated_during_reduce": matched - self.t150["matched_t150"],
                    "gamma_retention": row["total_pnl"] / self.t150["gamma_pnl_t150"]
                    if self.t150["gamma_pnl_t150"] > 0 else None,
                    "fills_0_150": self.gamma_fills,
                    "shares_0_150": sum(self.gamma_shares.values()),
                    "yes_shares_0_150": self.gamma_shares["YES"],
                    "no_shares_0_150": self.gamma_shares["NO"],
                    "reduce_fills_150_end": self.reduce_fills,
                    "reduce_shares_150_end": self.reduce_shares,
                    "reduce_capital_blocked_events": self.counts["REDUCE_CAPITAL_BLOCKED"],
                    "reduce_capital_blocked_seconds": self.reduce_blocked_us / 1_000_000,
                    "reduce_capital_blocked_fraction": self.reduce_blocked_us /
                    (self.end - self.switch_us),
                    "mean_abs_net_while_capital_blocked": self.reduce_blocked_net_us /
                    self.reduce_blocked_us if self.reduce_blocked_us else None,
                    "max_abs_net_while_capital_blocked": self.reduce_blocked_max_net,
                    "reduce_cancel_race_overshoot_events": self.race_events,
                    "reduce_cancel_race_added_shares": self.race_added_shares,
                    "reduce_cancel_race_max_single_increase": self.race_max_single_increase,
                    "reduce_cancel_race_max_abs_after": self.race_max_abs_after,
                    "reduce_sign_flip_events": self.sign_flip_events,
                    "reduce_sign_flip_excess_shares": self.sign_flip_excess_shares,
                    "reduce_sign_flip_max_excess": self.sign_flip_max_excess})
        return row


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
    orders, fills, paths, actions, transitions, markets = [], [], [], [], [], []
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
        sim = TwoPhaseReplay(row["market"], start, label["winner"],
                             int(lat["activation_latency_us"]), int(lat["cancel_latency_us"]),
                             orders, fills, paths, actions, transitions)
        result = sim.replay(path, {manifest["yes_token_id"]: "YES", manifest["no_token_id"]: "NO"})
        result["session"] = row["session"]
        result["m4a_integrity"] = audit_by_session[row["session"]]["integrity"]
        markets.append(result)
    write_csv(OUT / "market_results.csv", markets)
    write_csv(OUT / "orders.csv", orders)
    write_csv(OUT / "fills.csv", fills)
    write_csv(OUT / "inventory_paths.csv", paths)
    write_csv(OUT / "phase_transitions.csv", transitions)
    write_csv(OUT / "action_audit.csv", actions)
    errors = {r["market"]: r["data_error"] for r in markets if r["data_error"]}
    invalid_actions = []
    for a in actions:
        if a["action"] in ("PLACE", "REPLACE") and a["phase"] == "REDUCE_ONLY":
            net = a["net_inventory"]
            if net == 0 or (net > 0 and a["side"] != "NO") or (net < 0 and a["side"] != "YES"):
                invalid_actions.append((a["market"], a["order_id"], "risk-increasing reduce placement"))
        if a["action"] == "CANCEL" and a["reason_code"] == "BOOK_INVALID" and a["book_valid"]:
            invalid_actions.append((a["market"], a["order_id"], "BOOK_INVALID with valid book"))
    if len(transitions) != 32 or any(r["phase_at_end"] != "REDUCE_ONLY" for r in markets):
        invalid_actions.append(("*", "*", "phase transition count/end state"))
    b1_fills = list(csv.DictReader((B1_OUT / "fills.csv").open()))
    b1_prefix = [(f["market"], f["trade_event_seq"], f["side"], float(f["price"]), float(f["size"]))
                 for f in b1_fills if int(f["fill_available_us"]) <
                 (int(f["market"].rsplit("-", 1)[1]) + SWITCH_S) * 1_000_000]
    b2_prefix = [(f["market"], str(f["trade_event_seq"]), f["side"], float(f["price"]), float(f["size"]))
                 for f in fills if f["phase"] == "GAMMA_HARVEST"]
    if b1_prefix != b2_prefix:
        invalid_actions.append(("*", "*", "B1 gamma fill prefix mismatch"))
    b1_orders = list(csv.DictReader((B1_OUT / "orders.csv").open()))
    b1_order_prefix = [(o["market"], o["side"], float(o["price"]), int(o["submitted_us"]))
                       for o in b1_orders if int(o["submitted_us"]) <
                       (int(o["market"].rsplit("-", 1)[1]) + SWITCH_S) * 1_000_000]
    b2_order_prefix = [(o["market"], o["side"], float(o["price"]), int(o["submitted_us"]))
                       for o in orders if int(o["submitted_us"]) <
                       (int(o["market"].rsplit("-", 1)[1]) + SWITCH_S) * 1_000_000]
    if b1_order_prefix != b2_order_prefix:
        invalid_actions.append(("*", "*", "B1 gamma order prefix mismatch"))
    totals = [r["total_pnl"] for r in markets]
    b1_rows = {r["market"]: r for r in csv.DictReader((B1_OUT / "market_results.csv").open())}
    if set(b1_rows) != {r["market"] for r in markets}:
        invalid_actions.append(("*", "*", "B1 market-set mismatch"))
    paired_deltas = [r["total_pnl"] - float(b1_rows[r["market"]]["total_pnl"])
                     for r in markets]
    final_residual = sum(r["residual_final"] for r in markets)
    residual_pnl = sum(r["residual_pnl"] for r in markets)
    total_pnl = sum(totals)
    gamma_pnl = sum(r["gamma_pnl_final"] for r in markets)
    net150_markets = [r for r in markets if r["net_t150"] != 0]
    flat_times = [r["time_to_flat_seconds"] for r in net150_markets if r["time_to_flat_seconds"] is not None]
    blocked_dominant = sum(r["reduce_capital_blocked_fraction"] >= .5 for r in net150_markets)
    reduce_accepted = {a["market"] for a in actions if a["phase"] == "REDUCE_ONLY" and
                       a["action"] == "ACCEPT" and a["reason_code"] == "REDUCE_RISK"}
    execution_blocked = sum(r["market"] in reduce_accepted and r["reduce_fills_150_end"] == 0
                            and r["reduce_capital_blocked_fraction"] < .5 for r in net150_markets)
    material_residual = final_residual <= .8 * 405
    material_residual_pnl = residual_pnl >= -.8 * 115.98045634920635
    material_total = total_pnl >= -99.77 + 10
    if errors or invalid_actions or any(r["bbo_only_reprice_count"] for r in markets):
        cls = "IMPLEMENTATION INVALID"
    elif (material_residual and material_residual_pnl and material_total and
          sum(paired_deltas) - max(paired_deltas) > 0):
        cls = "TWO_PHASE MODEL SUPPORTED"
    elif blocked_dominant >= max(1, len(net150_markets) / 2) and not material_residual:
        cls = "CAPITAL BLOCKS REDUCE PHASE"
    elif execution_blocked >= max(1, len(net150_markets) / 2) and not material_residual:
        cls = "EXECUTION BLOCKS REDUCE PHASE"
    elif gamma_pnl >= .8 * 16.210456349206343 and not material_residual:
        cls = "GAMMA PRESERVED, REDUCE INEFFECTIVE"
    elif material_residual and total_pnl <= -99.77:
        cls = "REDUCE WORKS BUT TOO MUCH GAMMA LOST"
    else:
        cls = "NO BENEFIT VS B1"
    resting = [(o["closed_us"] - o["accepted_us"]) / 1_000_000 for o in orders
               if o["accepted_us"] is not None and o["closed_us"] is not None]
    defined_retention = [r["gamma_retention"] for r in markets if r["gamma_retention"] is not None]
    m = {"gamma_phase_fills": sum(r["fills_0_150"] for r in markets),
         "gamma_phase_shares": sum(r["shares_0_150"] for r in markets),
         "gamma_phase_yes_shares": sum(r["yes_shares_0_150"] for r in markets),
         "gamma_phase_no_shares": sum(r["no_shares_0_150"] for r in markets),
         "matched_t150": sum(r["matched_t150"] for r in markets),
         "residual_t150": sum(r["residual_t150"] for r in markets),
         "gamma_pnl_t150": sum(r["gamma_pnl_t150"] for r in markets),
         "gamma_retention_defined_markets": len(defined_retention),
         "median_gamma_retention_when_defined": median(defined_retention) if defined_retention else None,
         "weighted_pool_pair_cost_t150": 1 - sum(r["gamma_pnl_t150"] for r in markets) /
         sum(r["matched_t150"] for r in markets) if sum(r["matched_t150"] for r in markets) else None,
         "reduce_fills": sum(r["reduce_fills_150_end"] for r in markets),
         "reduce_shares": sum(r["reduce_shares_150_end"] for r in markets),
         "post_switch_matched_change": sum(r["post_switch_matched_change"] for r in markets),
         "residual_reduced_shares_from_t150": sum(r["residual_reduction"] for r in markets),
         "residual_reduction_rate_from_t150": 1 - final_residual /
         sum(r["residual_t150"] for r in markets) if sum(r["residual_t150"] for r in markets) else None,
         "already_flat_at_switch": sum(r["flat_status"] == "ALREADY_FLAT_AT_SWITCH" for r in markets),
         "t150_nonflat_markets": len(net150_markets),
         "flat_after_switch": len(flat_times),
         "flat_rate_of_t150_nonflat": len(flat_times) / len(net150_markets) if net150_markets else None,
         "median_time_to_flat_s": median(flat_times) if flat_times else None,
         "p75_time_to_flat_s": percentile(flat_times, .75),
         "p90_time_to_flat_s": percentile(flat_times, .90),
         "not_flat_by_end": sum(r["flat_status"] == "NOT_FLAT_BY_END" for r in markets),
         "reduce_capital_blocked_events": sum(r["reduce_capital_blocked_events"] for r in markets),
         "reduce_capital_blocked_markets": sum(r["reduce_capital_blocked_events"] > 0 for r in markets),
         "reduce_capital_blocked_seconds": sum(r["reduce_capital_blocked_seconds"] for r in markets),
         "mean_abs_net_while_reduce_capital_blocked": sum(r["reduce_capital_blocked_seconds"] *
         (r["mean_abs_net_while_capital_blocked"] or 0) for r in markets) /
         sum(r["reduce_capital_blocked_seconds"] for r in markets)
         if sum(r["reduce_capital_blocked_seconds"] for r in markets) else None,
         "reduce_cancel_race_overshoot_events": sum(r["reduce_cancel_race_overshoot_events"] for r in markets),
         "reduce_cancel_race_added_shares": sum(r["reduce_cancel_race_added_shares"] for r in markets),
         "reduce_cancel_race_max_single_increase": max(r["reduce_cancel_race_max_single_increase"] for r in markets),
         "reduce_cancel_race_max_abs_after": max(r["reduce_cancel_race_max_abs_after"] for r in markets),
         "reduce_sign_flip_events": sum(r["reduce_sign_flip_events"] for r in markets),
         "reduce_sign_flip_excess_shares": sum(r["reduce_sign_flip_excess_shares"] for r in markets),
         "matched_final": sum(r["matched_final"] for r in markets),
         "residual_final": final_residual,
         "gamma_pnl_final": gamma_pnl,
         "residual_pnl": residual_pnl,
         "total_pnl": total_pnl,
         "profitable_markets": sum(x > 1e-9 for x in totals),
         "losing_markets": sum(x < -1e-9 for x in totals),
         "flat_markets": sum(abs(x) <= 1e-9 for x in totals),
         "mean_peak_capital": mean(r["peak_capital_used"] for r in markets),
         "median_peak_capital": median(r["peak_capital_used"] for r in markets),
         "max_peak_capital": max(r["peak_capital_used"] for r in markets),
         "order_submissions": len(orders),
         "order_cancel_requests": sum(a["action"] == "CANCEL" for a in actions),
         "order_cancels": sum(o["status"] == "canceled" for o in orders),
         "accepted_orders": sum(o["accepted_us"] is not None for o in orders),
         "raw_fills": len(fills),
         "filled_shares": sum(float(f["size"]) for f in fills),
         "fills_per_100_submissions": 100 * len(fills) / len(orders) if orders else None,
         "median_resting_lifetime_s": median(resting) if resting else None,
         "mean_resting_lifetime_s": mean(resting) if resting else None,
         "bbo_only_reprice_count": sum(r["bbo_only_reprice_count"] for r in markets)}
    summary = {"classification": cls,
               "sample": {"markets": len(markets), "unique_markets": len({r["market"] for r in markets}),
                          "official_settlement_labels": len(labels),
                          "m4a_integrity_pass_overlap": sum(r["m4a_integrity"] == "PASS" for r in markets),
                          "data_errors": errors, "invalid_actions": invalid_actions,
                          "b1_gamma_fill_prefix_match": b1_prefix == b2_prefix,
                          "b1_gamma_order_prefix_match": b1_order_prefix == b2_order_prefix},
               "frozen_parameters": {"capital_usd": CAPITAL, "clip_shares": CLIP,
                                     "gamma_phase_end_s": SWITCH_S, "market_end_s": MARKET_S},
               "latencies_us": {"submit": lat["activation_latency_us"],
                                "cancel": lat["cancel_latency_us"]},
               "metrics": m}
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({"classification": cls, "sample": summary["sample"], "metrics": m}, indent=2))


if __name__ == "__main__":
    main()
