"""Research-only B4 replay: frozen B2 plus a five-share Phase 1 net budget.

Run: python3 research_tools/cip_b4_residual_budget_backtest.py
No network or production trading module is used.
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean, median

import cip_b2_two_phase_backtest as b2
from cip_b1_backtest import CLIP, ROOT, write_csv

OUT = ROOT / "research_outputs/cip_b4_20261004"
B2_OUT = ROOT / "research_outputs/cip_b2_20261003"
BUDGET = 5.0
GUARDRAIL_EVENTS = []


class BudgetReplay(b2.TwoPhaseReplay):
    """Keep B2's replay and Phase 2 methods; intervene only during Phase 1."""

    def __init__(self, *args):
        super().__init__(*args)
        self.guardrail_active = False
        self.guardrail_activations = 0
        self.guardrail_active_us = 0
        self.phase1_abs_integral_us = 0.0
        self.phase1_abs_samples = []
        self.phase1_max_abs = 0.0
        self.overshoot_events = 0
        self.overshoot_shares = 0.0
        self.overshoot_max_abs = 0.0
        self.overshoot_order_ids = []
        self.guardrail_cancel_ids = set()
        self.guardrail_violations = []

    def integrate(self, t):
        if self.phase == "GAMMA_HARVEST" and t > self.last_t:
            dt = t - self.last_t
            net = abs(self.values()[0])
            self.phase1_abs_integral_us += net * dt
            self.phase1_max_abs = max(self.phase1_max_abs, net)
            if net >= BUDGET:
                self.guardrail_active_us += dt
        return super().integrate(t)

    def audit(self, t, action, reason, side=None, order=None, event_kind=None):
        super().audit(t, action, reason, side, order, event_kind)
        if action == "CANCEL" and reason == "PHASE1_GUARDRAIL":
            # B2 counts every unknown cancel reason as BBO-only reprice.
            # This is an inventory change cancellation, never a BBO reprice.
            self.counts["BBO_ONLY_REPRICE"] -= 1

    def snapshot(self, t, fill_side, fill_price, fill_qty):
        super().snapshot(t, fill_side, fill_price, fill_qty)
        row = self.path_rows[-1]
        row["guardrail_active"] = (self.phase == "GAMMA_HARVEST" and
                                   abs(row["net_inventory"]) >= BUDGET)
        row["working_yes_status"] = self.orders["YES"]["status"] if self.orders["YES"] else None
        row["working_no_status"] = self.orders["NO"]["status"] if self.orders["NO"] else None

    def place(self, side, t, price_int, reason, event_kind):
        if self.phase == "GAMMA_HARVEST":
            net = self.values()[0]
            projected = net + (CLIP if side == "YES" else -CLIP)
            if abs(projected) > BUDGET + 1e-9 and abs(projected) > abs(net):
                self.audit(t, "SKIP", "PHASE1_GUARDRAIL", side, None, event_kind)
                GUARDRAIL_EVENTS.append({"market": self.slug, "timestamp_us": t,
                                         "event": "BLOCK_NEW_HEAVY", "net_inventory": net,
                                         "side": side, "order_id": None,
                                         "overshoot_shares": 0, "event_kind": event_kind,
                                         "cancel_pending_at_fill": None})
                return False
        return super().place(side, t, price_int, reason, event_kind)

    def reconcile(self, t, event_kind, inventory_changed=False):
        if self.phase != "GAMMA_HARVEST":
            return super().reconcile(t, event_kind, inventory_changed)
        net = self.values()[0]
        self.phase1_abs_samples.append(abs(net))
        self.phase1_max_abs = max(self.phase1_max_abs, abs(net))
        active = abs(net) >= BUDGET
        if active and not self.guardrail_active:
            self.guardrail_activations += 1
            GUARDRAIL_EVENTS.append({"market": self.slug, "timestamp_us": t,
                                     "event": "ACTIVATE", "net_inventory": net,
                                     "side": None, "order_id": None,
                                     "overshoot_shares": max(0, abs(net) - BUDGET),
                                     "event_kind": event_kind, "cancel_pending_at_fill": None})
        elif not active and self.guardrail_active:
            GUARDRAIL_EVENTS.append({"market": self.slug, "timestamp_us": t,
                                     "event": "RECOVER", "net_inventory": net,
                                     "side": None, "order_id": None,
                                     "overshoot_shares": 0, "event_kind": event_kind,
                                     "cancel_pending_at_fill": None})
        self.guardrail_active = active
        # B2 still handles invalid books first and cancels both sides.
        # A valid book at the budget boundary allows a narrow, inventory-driven
        # heavy-side cancellation; B2 handles the lighter side and all pricing.
        if active and t < self.switch_us and self.valid_books():
            heavy = "YES" if net > 0 else "NO"
            order = self.orders[heavy]
            if order is not None and order["cancel_request_us"] is None:
                self.guardrail_cancel_ids.add(order["order_id"])
                self.cancel(heavy, t, "PHASE1_GUARDRAIL", event_kind)
                GUARDRAIL_EVENTS.append({"market": self.slug, "timestamp_us": t,
                                         "event": "CANCEL_HEAVY", "net_inventory": net,
                                         "side": heavy, "order_id": order["order_id"],
                                         "overshoot_shares": max(0, abs(net) - BUDGET),
                                         "event_kind": event_kind,
                                         "cancel_pending_at_fill": None})
        return super().reconcile(t, event_kind, inventory_changed)

    def apply_fill(self, order, qty, event, trade_px):
        before = self.values()[0]
        cancel_pending = order["cancel_request_us"] is not None
        changed = super().apply_fill(order, qty, event, trade_px)
        after = self.values()[0]
        if self.phase == "GAMMA_HARVEST" and abs(after) > BUDGET + 1e-9 and abs(after) > abs(before):
            excess = abs(after) - BUDGET
            self.overshoot_events += 1
            self.overshoot_shares += excess
            self.overshoot_max_abs = max(self.overshoot_max_abs, abs(after))
            self.overshoot_order_ids.append(order["order_id"])
            if order["order_id"] not in self.guardrail_cancel_ids and not cancel_pending:
                self.guardrail_violations.append(order["order_id"])
            GUARDRAIL_EVENTS.append({"market": self.slug,
                                     "timestamp_us": event["available_at_us"],
                                     "event": "OVERSHOOT", "net_inventory": after,
                                     "side": order["side"], "order_id": order["order_id"],
                                     "overshoot_shares": excess, "event_kind": "TRADE",
                                     "cancel_pending_at_fill": cancel_pending})
        return changed

    def result(self):
        row = super().result()
        row.update({"phase1_mean_abs_net": mean(self.phase1_abs_samples)
                    if self.phase1_abs_samples else 0.0,
                    "phase1_time_weighted_abs_net": self.phase1_abs_integral_us / 150_000_000,
                    "phase1_max_abs_net": self.phase1_max_abs,
                    "phase1_guardrail_activations": self.guardrail_activations,
                    "phase1_guardrail_active_seconds": self.guardrail_active_us / 1_000_000,
                    "phase1_guardrail_overshoot_events": self.overshoot_events,
                    "phase1_guardrail_overshoot_shares": self.overshoot_shares,
                    "phase1_guardrail_max_overshoot_abs_net": self.overshoot_max_abs,
                    "phase1_guardrail_intent_violations": len(self.guardrail_violations)})
        return row


def read_csv(path):
    with path.open() as stream:
        return list(csv.DictReader(stream))


def write_all_columns(path, rows):
    """Keep late optional reduce-fill audit fields in B4's output CSVs."""
    fields = list(dict.fromkeys(key for row in rows for key in row))
    write_csv(path, rows, fields)


def main():
    # Reuse B2's exact 32-market loader, identity checks, replay loop, Phase 2,
    # latency, fill and capital accounting. Only its class/output binding changes.
    b2.TwoPhaseReplay = BudgetReplay
    b2.OUT = OUT
    b2.write_csv = write_all_columns
    b2.main()
    summary = json.loads((OUT / "summary.json").read_text())
    markets = read_csv(OUT / "market_results.csv")
    orders = read_csv(OUT / "orders.csv")
    fills = read_csv(OUT / "fills.csv")
    actions = read_csv(OUT / "action_audit.csv")
    b2_markets = {r["market"]: r for r in read_csv(B2_OUT / "market_results.csv")}
    b2_summary = json.loads((B2_OUT / "summary.json").read_text())
    if len(markets) != 32 or set(b2_markets) != {r["market"] for r in markets}:
        raise RuntimeError("B2/B4 market-set mismatch")
    write_csv(OUT / "guardrail_events.csv", GUARDRAIL_EVENTS)

    m = summary["metrics"]
    b = b2_summary["metrics"]
    m.update({
        "phase1_mean_abs_net_market_mean": mean(float(r["phase1_mean_abs_net"]) for r in markets),
        "phase1_time_weighted_abs_net_market_mean": mean(float(r["phase1_time_weighted_abs_net"]) for r in markets),
        "phase1_max_abs_net": max(float(r["phase1_max_abs_net"]) for r in markets),
        "phase1_guardrail_activations": sum(int(r["phase1_guardrail_activations"]) for r in markets),
        "phase1_guardrail_active_seconds": sum(float(r["phase1_guardrail_active_seconds"]) for r in markets),
        "phase1_guardrail_overshoot_events": sum(int(r["phase1_guardrail_overshoot_events"]) for r in markets),
        "phase1_guardrail_overshoot_shares": sum(float(r["phase1_guardrail_overshoot_shares"]) for r in markets),
        "phase1_guardrail_overshoot_markets": sum(int(r["phase1_guardrail_overshoot_events"]) > 0 for r in markets),
        "phase1_guardrail_intent_violations": sum(int(r["phase1_guardrail_intent_violations"]) for r in markets),
        "fills_per_market": len(fills) / 32,
        "matched_shares_per_market": m["matched_final"] / 32,
        "matched_t150_per_market": m["matched_t150"] / 32,
        "total_purchased_shares": m["filled_shares"],
        "capital_used_t150_total": sum(float(r["capital_used_t150"]) for r in markets),
        "final_inventory_flat_markets": sum(float(r["residual_final"]) == 0 for r in markets),
        "pairing_efficiency": 2 * m["matched_final"] / m["filled_shares"] if m["filled_shares"] else None,
        "t150_residual_change_vs_b2": m["residual_t150"] - b["residual_t150"],
        "t150_residual_reduction_pct_vs_b2": 1 - m["residual_t150"] / b["residual_t150"],
        "final_residual_change_vs_b2": m["residual_final"] - b["residual_final"],
        "gamma_pnl_t150_change_vs_b2": m["gamma_pnl_t150"] - b["gamma_pnl_t150"],
        "gamma_pnl_final_change_vs_b2": m["gamma_pnl_final"] - b["gamma_pnl_final"],
        "residual_pnl_change_vs_b2": m["residual_pnl"] - b["residual_pnl"],
        "total_pnl_change_vs_b2": m["total_pnl"] - b["total_pnl"],
    })
    paired = sorted((float(r["total_pnl"]) - float(b2_markets[r["market"]]["total_pnl"]),
                     r["market"]) for r in markets)
    m["paired_total_pnl_delta_excluding_best_market"] = sum(x for x, _ in paired[:-1])
    m["markets_with_positive_paired_delta"] = sum(x > 1e-9 for x, _ in paired)
    m["markets_with_negative_paired_delta"] = sum(x < -1e-9 for x, _ in paired)
    m["markets_with_zero_paired_delta"] = sum(abs(x) <= 1e-9 for x, _ in paired)
    m["phase1_fills_per_market"] = m["gamma_phase_fills"] / 32
    m["phase1_purchased_shares_per_market"] = m["gamma_phase_shares"] / 32
    m["phase1_pairing_efficiency"] = 2 * m["matched_t150"] / m["gamma_phase_shares"] if m["gamma_phase_shares"] else None
    m["phase1_guardrail_cancel_requests"] = sum(a["reason_code"] == "PHASE1_GUARDRAIL" and
                                                 a["action"] == "CANCEL" for a in actions)
    m["phase1_guardrail_blocked_new_decisions"] = sum(a["reason_code"] == "PHASE1_GUARDRAIL" and
                                                     a["action"] == "SKIP" for a in actions)

    # B2's generic main classifies the intentionally different B1/B2 gamma
    # prefix as invalid. Discard only those expected comparator warnings.
    invalid = [x for x in summary["sample"]["invalid_actions"]
               if x[2] not in ("B1 gamma fill prefix mismatch", "B1 gamma order prefix mismatch")]
    if m["phase1_guardrail_intent_violations"]:
        invalid.append(["*", "*", "phase1 risk-increasing fill without cancel race"])
    for a in actions:
        if a["phase"] == "GAMMA_HARVEST" and a["action"] in ("PLACE", "REPLACE"):
            net = float(a["net_inventory"])
            projected = net + (CLIP if a["side"] == "YES" else -CLIP)
            if abs(projected) > BUDGET + 1e-9 and abs(projected) > abs(net):
                invalid.append([a["market"], a["order_id"], "phase1 active intent above budget"])
    if m["bbo_only_reprice_count"]:
        invalid.append(["*", "*", "BBO_ONLY_REPRICE > 0"])
    if summary["sample"]["data_errors"]:
        invalid.append(["*", "*", "replay data error"])
    if (summary["latencies_us"] != b2_summary["latencies_us"] or
        summary["frozen_parameters"] != b2_summary["frozen_parameters"]):
        invalid.append(["*", "*", "frozen replay parameter mismatch"])
    if any(float(r["peak_capital_used"]) > 25 + 1e-8 for r in markets):
        invalid.append(["*", "*", "capital invariant violation"])
    if any(f["phase"] == "REDUCE_ONLY" and float(f["net_after"]) * float(f["net_before"]) > 0 and
           abs(float(f["net_after"])) > abs(float(f["net_before"])) and
           f["cancel_pending_at_fill"] != "True" for f in fills):
        invalid.append(["*", "*", "risk-increasing reduce fill outside cancel race"])

    # Classification thresholds were frozen in the research design before run.
    if invalid:
        cls = "IMPLEMENTATION INVALID"
    elif (m["gamma_phase_fills"] < .5 * b["gamma_phase_fills"] or
          m["matched_t150"] < .5 * b["matched_t150"]):
        cls = "GUARDRAIL TOO RESTRICTIVE"
    elif m["residual_t150"] > 160:
        cls = "GUARDRAIL TOO LOOSE"
    elif m["total_pnl_change_vs_b2"] >= 10 and m["total_pnl"] < -10:
        cls = "RESIDUAL CONTROL WORKS, STRATEGY STILL NEGATIVE"
    elif (m["residual_t150"] <= .5 * b["residual_t150"] and
          m["residual_final"] <= .5 * b["residual_final"] and
          m["residual_pnl_change_vs_b2"] >= 20 and
          m["total_pnl_change_vs_b2"] >= 10 and
          m["paired_total_pnl_delta_excluding_best_market"] > 0):
        cls = "RESIDUAL BUDGET SUPPORTED"
    elif m["residual_t150"] <= .5 * b["residual_t150"] and m["total_pnl_change_vs_b2"] <= 0:
        cls = "RESIDUAL CONTROL WORKS BUT GAMMA COST TOO HIGH"
    else:
        cls = "NO MATERIAL BENEFIT VS B2"
    summary["classification"] = cls
    summary["sample"]["invalid_actions"] = invalid
    summary["sample"]["b2_market_set_match"] = True
    summary["sample"]["b1_gamma_prefix_expected_to_differ"] = True
    summary["frozen_parameters"]["phase1_residual_budget_shares"] = BUDGET
    summary["b2_comparator"] = {"classification": b2_summary["classification"],
                                 "metrics": b, "market_set": list(b2_markets)}
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({"B4_classification": cls, "B4_metrics": m,
                      "invalid_actions": invalid}, indent=2))


if __name__ == "__main__":
    main()
