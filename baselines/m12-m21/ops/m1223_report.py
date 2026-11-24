"""Read committed SOP audits; keep assumed, known fills and uncertainty distinct."""

import argparse
import csv
import json
import sqlite3
from collections import defaultdict
from contextlib import closing
from decimal import Decimal as D
from pathlib import Path

from m1223_execution import ANCHOR_FILL_MODEL
from m511_sample_quality import own_market_gate


def install_report_compatibility():
    """Future runs only: teach the shared reporter the independent SOP account."""
    from polymaker.m1234_sim import report
    from polymaker.m1234_sim.events import digest

    original = report.execution_version
    if getattr(original, "sop_compatible", False):
        return

    def execution_version(definition, scenario):
        settings = definition["settings"]
        if scenario.startswith("sop/") and settings.get("sop"):
            return digest(
                {
                    "implementation": settings["implementation"],
                    "scenario": settings["scenarios"][scenario.split("/", 1)[1]],
                    "seed": settings["seed"],
                    "execution_policy": settings["sop_execution"],
                    "signal_definition": settings["sop"],
                }
            )
        return original(definition, scenario)

    execution_version.sop_compatible = True
    report.execution_version = execution_version


def market_result(path, project, *, eligibility=None):
    from polymaker.m1234_sim.validity import database_validity

    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        watermark = db.execute("SELECT MAX(input_seq) FROM audit").fetchone()[0]
        row = db.execute("SELECT payload FROM snapshots WHERE name='state'").fetchone()
        state = json.loads(row[0]) if row else None
        gate_row = db.execute(
            "SELECT payload FROM metadata WHERE name='research_sample_eligibility'"
        ).fetchone()
        recorded_gate = json.loads(gate_row[0]) if gate_row else None
        query = """SELECT input_seq,kind,payload FROM audit WHERE kind IN
            ('DEFINITION','SUBMIT','ANCHOR_ENTRY','ASSUMED_FILL','MODELLED_FILL','SOP_FILL_ATTRIBUTION','ORDER_UNCERTAIN',
             'UNKNOWN_TAIL','OFFICIAL_RESULT','OBSERVATION_END','SIMULATION_INVALID')
            OR (kind='SOP_EVIDENCE' AND json_extract(payload,'$.event') IN
                ('SIGNAL_CONFIRMED','SIGNAL_ENDED','SIGNAL_REFERENCE_READY')) ORDER BY seq"""
        audit = [(seq, kind, json.loads(p)) for seq, kind, p in db.execute(query)]
        quality = dict(
            db.execute("""SELECT json_extract(payload,'$.payload.reason'),COUNT(*)
            FROM audit WHERE kind='SOP_EVIDENCE'
            AND json_extract(payload,'$.event') IN
                ('QUALITY_UNKNOWN','ROUND_UNKNOWN','LW_UNKNOWN','ROOT_UNKNOWN','SEED_UNKNOWN') GROUP BY 1""")
        )
        verified = database_validity(path, state, connection=db) if state is not None else None
    definition = next((p["definition"] for _, k, p in audit if k == "DEFINITION"), None)
    if definition is None:
        return {
            "market": path.parent.name,
            "status": "UNAVAILABLE",
            "reason": "INIT_NOT_COMMITTED",
        }, []
    market = definition["market"]["slug"]
    signals, orders, assumed, uncertain, fills = {}, {}, {}, set(), defaultdict(lambda: D(0))
    attribution, cancellation_races = (
        defaultdict(lambda: defaultdict(lambda: D(0))),
        defaultdict(lambda: D(0)),
    )
    winner, ended, invalid = None, False, False
    m17 = definition.get("settings", {}).get("sop", {}).get("strategy") == "M17"
    anchor_only = definition.get("settings", {}).get("sop_execution", {}).get("fill_model") == ANCHOR_FILL_MODEL
    ready_signals, ended_signals = set(), set()
    for _seq, kind, p in audit:
        if anchor_only and kind in {"SUBMIT", "MODELLED_FILL", "SOP_FILL_ATTRIBUTION",
                                    "ORDER_UNCERTAIN", "UNKNOWN_TAIL"}:
            raise ValueError("SOP_ANCHOR_ONLY_AUDIT_HAS_ORDER_LIFECYCLE:" + market)
        if kind == ("ANCHOR_ENTRY" if anchor_only else "SUBMIT"):
            orders[p["order"]] = p["intent"]
        elif kind == "ASSUMED_FILL":
            assumed[p["order"]] = p
        elif kind == "MODELLED_FILL":
            fills[p["order"]] += D(p["quantity"])
            if p.get("cancel_in_flight"):
                cancellation_races[p["order"]] += D(p["quantity"])
        elif kind == "SOP_FILL_ATTRIBUTION":
            oid = p["order"]
            category = p["classification"]
            # The original request deadline was known at submission. Earlier
            # r1 audits omitted this separate execution-window classification.
            if (
                category == "WITHIN_SIGNAL_VALIDITY"
                and oid in orders
                and p.get("source_mono") is not None
                and p["source_mono"] >= orders[oid]["deadline"]
            ):
                category = "AFTER_EXECUTION_WINDOW"
            attribution[oid][category] += D(p["fill"]["quantity"])
        elif kind in {"ORDER_UNCERTAIN", "UNKNOWN_TAIL"}:
            uncertain.add(p["order"])
        elif kind == "SOP_EVIDENCE" and p["event"] == "SIGNAL_CONFIRMED":
            signals[p["payload"]["id"]] = p["payload"]
        elif m17 and kind == "SOP_EVIDENCE" and p["event"] == "SIGNAL_ENDED":
            ended_signals.add(p["payload"]["id"])
        elif m17 and kind == "SOP_EVIDENCE" and p["event"] == "SIGNAL_REFERENCE_READY":
            fact = p["payload"]
            sid = fact["id"]
            original = signals.get(sid)
            if (original is None or sid in ended_signals or fact.get("pending_rechecks") != 0
                    or fact.get("token") != original.get("token")
                    or not original["confirmed"] <= fact["available"] < original["deadline"]):
                raise ValueError("M17_REFERENCE_READY_WITHOUT_LIVE_CONFIRMED_SIGNAL:" + market)
            ready_signals.add(sid)
        elif kind == "OFFICIAL_RESULT" and p.get("result"):
            winner = p["result"]["winner"]
        elif kind == "OBSERVATION_END":
            ended = True
        elif kind == "SIMULATION_INVALID":
            invalid = True
    if m17 and any(s.get("can_reference_for_new_action") is True and sid not in ready_signals
                   for sid, s in signals.items()):
        raise ValueError("M17_INITIAL_REFERENCE_READY_NOT_AUDITED:" + market)
    if set(orders) != set(assumed):
        raise ValueError("SOP_ASSUMED_SUBMIT_POPULATION_MISMATCH:" + market)
    gate = (
        eligibility
        if eligibility is not None
        else recorded_gate
        if ended and recorded_gate is not None
        else own_market_gate(project, market)
    )
    invalid |= verified is not None and verified["status"] != "VALID"
    eligible = gate["status"] == "ELIGIBLE" and not invalid
    results = []
    for oid, intent in orders.items():
        p, q, f = D(intent["price"]), D(intent["quantity"]), fills[oid]
        if f > q or D(assumed[oid]["quantity"]) != q or D(assumed[oid]["price"]) != p:
            raise ValueError("SOP_QUANTITY_ACCOUNTING_MISMATCH:" + oid)
        uncertainty = oid in uncertain and f < q
        classified = sum(attribution[oid].values(), D(0))
        if classified > f:
            raise ValueError("DUPLICATED_FILL_ATTRIBUTION:" + oid)
        payout = D(1 if intent["outcome"] == winner else 0) if winner else None
        results.append(
            {
                "market": market,
                "order_id": oid,
                "token": intent["token"],
                "candidate_id": intent["opportunity"],
                "price": str(p),
                "quantity": str(q),
                "official_winner": winner,
                "primary_eligible": eligible,
                "isolation_reason": gate["reason"],
                "assumed_gross_pnl": str(q * (payout - p)) if winner else None,
                "market_audit_watermark": watermark,
                **({ "fill_model": ANCHOR_FILL_MODEL, "assumed_filled_quantity": str(q)}
                   if anchor_only else {
                       "known_filled_quantity": str(f),
                       "has_positive_modelled_fill": f > 0,
                       "unresolved": uncertainty,
                       "known_simulated_gross_pnl": str(f * (payout - p)) if winner else None,
                       "within_signal_validity_quantity": str(attribution[oid]["WITHIN_SIGNAL_VALIDITY"]),
                       "after_signal_end_quantity": str(attribution[oid]["AFTER_SIGNAL_END"]),
                       "after_execution_window_quantity": str(attribution[oid]["AFTER_EXECUTION_WINDOW"]),
                       "ordering_unknown_quantity": str(attribution[oid]["ORDERING_UNKNOWN"]),
                       **({"signal_reference_unproven_quantity": str(attribution[oid]["SIGNAL_REFERENCE_UNPROVEN"])}
                          if intent.get("strategy") == "M24" else {}),
                       "attribution_missing_quantity": str(f - classified),
                       "cancel_in_flight_quantity": str(cancellation_races[oid]),
                   }),
            }
        )
    coverage = state.get("coverage", {}) if state else {}
    status = "INVALID" if invalid else "SEALED" if ended else "ACTIVE_COMMITTED_PREFIX"
    return {
        "market": market,
        "status": status,
        "signal_ids": list(signals),
        "signal_count": len(signals),
        **({"admission_signal_ids": sorted(ready_signals),
            "admission_signal_count": len(ready_signals),
            "admission_signal_basis": "EVER_REFERENCE_READY_BEFORE_ORIGINAL_DEADLINE"} if m17 else {}),
        **({"admitted_orders": len(orders), "fill_model": ANCHOR_FILL_MODEL} if anchor_only else {
            "submitted_orders": len(orders),
            "positive_modelled_fill_orders": sum(fills[o] > 0 for o in orders),
            "unresolved_orders": sum(r["unresolved"] for r in results),
        }),
        "assumed_orders": len(assumed),
        "official_winner": winner,
        "pending_settlement_orders": len(orders) if not winner else 0,
        "primary_eligible": eligible,
        "gate": gate,
        "gate_source": "EXPLICIT_FROZEN_OVERRIDE"
        if eligibility is not None
        else "COMMITTED_END_OF_MARKET"
        if ended and recorded_gate is not None
        else "CURRENT_READ_ONLY_OWN_TRADE_EVIDENCE",
        "unknown": bool(quality) or invalid,
        "quality_counts": quality,
        "complete": coverage.get("complete_window") is True,
        "input_seq": watermark,
        "source_db": str(path),
        "run_owner": definition["settings"].get("sop_cohort_sha256"),
        "audit_validity": verified["status"] if verified else "ACTIVE_COMMITTED_PREFIX_NOT_SEALED",
    }, results


def summary(orders, signals, mode):
    assumed = mode in {"assumed", "anchor"}
    field = "assumed_gross_pnl" if assumed else "known_simulated_gross_pnl"
    active = orders if assumed else [r for r in orders if r["has_positive_modelled_fill"]]
    settled = [r for r in active if r[field] is not None]
    pnl = sum((D(r[field]) for r in settled), D(0)) if settled else None
    by_market = defaultdict(lambda: D(0))
    for r in settled:
        by_market[r["market"]] += D(r[field])
    top = sorted(((m, v) for m, v in by_market.items() if v > 0), key=lambda p: (-p[1], p[0]))
    trim = {
        str(n): {
            "removed_markets": [m for m, _ in top[:n]],
            "settled_gross_pnl": str(pnl - sum((v for _, v in top[:n]), D(0)))
            if pnl is not None
            else None,
        }
        for n in (1, 3)
    }
    bins = [
        (".01", ".03"),
        (".03", ".10"),
        (".10", ".20"),
        (".20", ".40"),
        (".40", ".60"),
        (".60", ".80"),
        (".80", "1"),
    ]
    buckets = []
    for lo, hi in bins:
        selected = [r for r in active if D(lo) <= D(r["price"]) < D(hi)]
        values = [D(r[field]) for r in selected if r[field] is not None]
        buckets.append(
            {
                "price_interval": f"[{lo},{hi})",
                "trading_markets": len({r["market"] for r in selected}),
                "orders": len(selected),
                "pending_settlement_orders": sum(r[field] is None for r in selected),
                "settled_gross_pnl": str(sum(values, D(0))) if values else None,
            }
        )
    tail = [r for r in settled if D(".01") <= D(r["price"]) <= D(".02")]
    fill_classification = {}
    if mode == "simulated":
        for field in (
            "within_signal_validity_quantity",
            "after_signal_end_quantity",
            "after_execution_window_quantity",
            "ordering_unknown_quantity",
            "attribution_missing_quantity",
            "cancel_in_flight_quantity",
        ):
            values = [D(r.get(field, "0")) for r in active]
            fill_classification[field] = {
                "orders": sum(v > 0 for v in values),
                "shares": str(sum(values, D(0))),
            }
    return {
        "signal_count": signals,
        ("admitted_orders" if mode == "anchor" else "submitted_orders"): len(orders),
        "trading_markets": len({r["market"] for r in active}),
        "orders": len(active),
        "settled_orders": len(settled),
        "pending_settlement_orders": len(active) - len(settled),
        "settled_gross_pnl": str(pnl) if pnl is not None else None,
        **({"fill_model": ANCHOR_FILL_MODEL} if mode == "anchor" else {
            "unresolved_orders": sum(r["unresolved"] for r in orders),
            "full_execution_pnl_known": bool(active)
            and all((assumed or not r["unresolved"]) and r["official_winner"] for r in orders),
        }),
        "drop_best_profitable_markets": trim,
        "price_buckets": buckets,
        "one_to_two_cent_contribution": {
            "orders": len(tail),
            "settled_gross_pnl": str(sum((D(r[field]) for r in tail), D(0))) if tail else None,
        },
        **(
            {
                "fill_classification": fill_classification,
                "classification_note": "One order may appear in multiple classes; cancel-in-flight overlaps other classes; all known fills remain in gross PnL",
            }
            if mode == "simulated"
            else {}
        ),
        "limitations": ["Immediate full fill at admitted anchor, gross fees excluded"] if mode == "anchor" else [
            "Committed audit only; active transaction not counted",
            "Known simulated fills are not complete execution PnL while orders remain unresolved",
            "No fill probability or real queue execution guarantee; gross fees excluded",
        ],
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--project", type=Path, default=Path("/home/ubuntu/polymaker"))
    args = p.parse_args()
    markets, orders, errors = [], [], []
    for path in sorted(args.run.glob("btc-*/simulation.sqlite3")):
        try:
            m, o = market_result(path, args.project)
            markets.append(m)
            orders.extend(o)
        except (ValueError, sqlite3.Error, KeyError) as exc:
            errors.append({"market": path.parent.name, "error": str(exc)})
    primary = [r for r in orders if r["primary_eligible"]]
    result = {
        "run": str(args.run),
        "status": "PARTIAL" if errors or any(m["status"] != "SEALED" for m in markets) else "OK",
        "markets": markets,
        "errors": errors,
        "metrics_scope": "CURRENT_RUN_COMMITTED_AUDIT",
        "all_recorded": {
            mode: summary(orders, sum(m.get("signal_count", 0) for m in markets), mode)
            for mode in ("assumed", "simulated")
        },
        "primary": {
            mode: summary(
                primary,
                sum(m.get("signal_count", 0) for m in markets if m.get("primary_eligible")),
                mode,
            )
            for mode in ("assumed", "simulated")
        },
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    with (args.output / "orders.csv").open("w", newline="") as f:
        fields = list(orders[0]) if orders else ["market", "order_id", "price", "quantity"]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(orders)
    print(
        json.dumps(
            {
                "status": result["status"],
                "markets": len(markets),
                "orders": len(orders),
                "errors": errors,
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
