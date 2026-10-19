"""Read-only reconstruction; original execution/cache journals are unchanged."""

import csv
import hashlib
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
SESSION = "2f3dc094e2ba4a2ca6f92a88e407761a"
SOURCE = ROOT / f"journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_{SESSION}.jsonl"


def main():
    rows = [json.loads(line) for line in SOURCE.open()]
    def payload(r):
        return r["data"]["payload"]
    session = next(payload(r) for r in rows if r["kind"] == "sm_session")
    result = next(payload(r) for r in rows if r["kind"] == "sm_run_result")
    start = int(session["slug"].rsplit("-", 1)[1])
    def by_kind(kind):
        return [r for r in rows if r["kind"] == kind]
    intents = {payload(r)["intent"]["window_id"]: payload(r)["intent"]
               for r in by_kind("sm_obi_final_decision")}
    posts = {payload(r)["batch_id"]: payload(r) for r in by_kind("sm_network_request")
             if payload(r)["method"] == "POST"}
    closes = {payload(r)["window_id"]: r for r in by_kind("sm_obi_close_requested")}
    phases = {payload(r)["window_id"]: payload(r)["state"] for r in by_kind("sm_strategy_window")}
    orders = []
    for r in by_kind("sm_window_result"):
        d = payload(r)
        window = d["window_id"]
        a = d["placement_results"][0]
        q, intent, post = a["quote"], intents[window], posts.get(window)
        post_return_wall = (post["completed_wall"]+intent["post_returned"]-post["completed_monotonic"]) if post else None
        oid = a["expected_order_id"]
        canceled = [x for x in by_kind("sm_cancel_ack") if oid in payload(x)["ids"]]
        orders.append({
            "window": window.rsplit(":", 1)[1], "phase": phases[window],
            "side": q["outcome"], "size": q["size"], "price": q["price"],
            "post_started": a["post_started"],
            "post_start_wall_elapsed": post["started_wall"]-start if post else None,
            "post_return_wall_elapsed": post_return_wall-start if post_return_wall is not None else None,
            "post_pipeline_sec": intent["post_returned"]-intent["post_started"] if post else None,
            "initial_outcome": a["outcome"],
            "close_requested_elapsed": closes[window]["ts"]-start,
            "close_reason": payload(closes[window])["reason"],
            "window_end_elapsed": d["end_time"]-start,
            "post_response_to_window_end_sec": d["close_monotonic"]-intent["post_returned"] if post else None,
            "close_barrier": d["close_barrier"], "block_reason": d["block_reason"],
            "delete_count_including_later_recovery": len(canceled),
            "expected_order_id": oid,
        })
    with (OUT / "orders.csv").open("w") as f:
        writer = csv.DictWriter(f, fieldnames=list(orders[0]))
        writer.writeheader()
        writer.writerows(orders)

    fills = {}
    for r in by_kind("sm_trade"):
        d = payload(r)
        key = (d["trade_id"], d["maker_order_id"])
        f = fills.setdefault(key, {"first_observed_elapsed": r["ts"]-start, **d, "statuses": []})
        f["statuses"].append({"elapsed": r["ts"]-start, "status": d["status"]})
        f["status"] = d["status"]
    assert len(fills) == 4 and all(f["status"] == "CONFIRMED" for f in fills.values())
    cost = sum(Decimal(str(f["price"]))*Decimal(str(f["size"])) for f in fills.values())
    assert cost == Decimal("11.05")
    assert Decimal(str(result["realized_merge_pnl"])) == Decimal(str(result["merged_pairs"]))-cost
    assert result["final_close_barrier"] == "SAFE_TO_CONTINUE" and result["residual_shares"] == 0
    assert not any("Already canceled." in json.dumps(payload(r).get("raw_response"))
                   for r in by_kind("sm_placement_result"))

    episodes = []
    for r in by_kind("sm_repair_episode"):
        d = payload(r)
        if d["event"] != "COMPLETE_WITH_DUST_TOLERANCE" or Decimal(str(d["original_qty"])) <= 0:
            continue
        windows = set(d["windows"])
        ids = {payload(x)["expected_order_id"] for x in by_kind("sm_placement_result")
               if payload(x)["quote"]["window_id"] in windows}
        own = [f for f in fills.values() if f["maker_order_id"] in ids]
        own_cost = sum(Decimal(str(f["price"]))*Decimal(str(f["size"])) for f in own)
        assert len(own) == 2 and all(Decimal(str(f["size"])) == 5 for f in own)
        episodes.append({"opening_window": d["opening_window"], "windows": d["windows"],
                         "actual_fills": own, "cost": str(own_cost),
                         "paired_pnl": str(Decimal(5)-own_cost),
                         "completed_elapsed": r["ts"]-start,
                         "last_transition": d.get("policy_state", {}).get("last_transition")})
    assert [Decimal(ep["paired_pnl"]) for ep in episodes] == [Decimal("-.30"), Decimal("-.75")]
    assert sum(Decimal(ep["paired_pnl"]) for ep in episodes) == Decimal(str(result["realized_merge_pnl"]))

    keep = {"sm_placement_result", "sm_obi_close_requested", "sm_trade", "sm_repair_decision",
            "sm_repair_policy_state", "sm_position_reconciled", "sm_network_transition",
            "sm_merge_intent", "sm_merge_submitted", "sm_merge_result", "sm_trade_confirmation_advanced"}
    timeline = []
    for line, r in enumerate(rows, 1):
        if r["kind"] not in keep:
            continue
        d = dict(payload(r))
        if r["kind"] == "sm_repair_policy_state":
            d.pop("points", None)
        if r["kind"] == "sm_obi_close_requested":
            d = {k: d[k] for k in ("window_id", "reason", "requested_monotonic", "deadline")}
        if r["kind"] == "sm_position_reconciled":
            d = {k: d[k] for k in ("source", "actual", "collateral", "cost", "included")}
        timeline.append({"journal_line": line, "elapsed_wall": r["ts"]-start,
                         "kind": r["kind"], "payload": d})
    (OUT / "timeline.json").write_text(json.dumps(timeline, indent=2)+"\n")
    cache_path = ROOT / f"research_outputs/periodic_sync/{SESSION}/summary.json"
    cache = json.loads(cache_path.read_text())
    summary = {
        "market": session["slug"], "session_id": SESSION, "source": str(SOURCE.resolve()),
        "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        "cache_summary": str(cache_path.resolve()),
        "cache_summary_sha256": hashlib.sha256(cache_path.read_bytes()).hexdigest(),
        "time_basis": "Journal wall time minus market start; strategy elapsed uses official clock. Fills are local observations, not native exchange timestamps.",
        "actual_economic_fills": list(fills.values()), "actual_buy_cost": str(cost),
        "episodes": episodes,
        "actual_http_posts": len(posts), "window_attempts": len(orders),
        "final_result": {k: v for k, v in result.items() if k != "windows"},
        "initial_placement_outcomes": dict(Counter(o["initial_outcome"] for o in orders)),
        "close_request_reasons": dict(Counter(o["close_reason"] for o in orders)),
        "blocked_close_reasons": dict(Counter(payload(r).get("reason") for r in by_kind("sm_close_barrier")
                                               if payload(r)["result"] == "BLOCKED")),
        "cache_health": {k: cache[k] for k in ("failure", "observer_fatal_type", "dropped_events", "dropped_references", "io_drained")},
        "cache_source_failures": cache["metrics"]["source_failures"],
        "explicit_rejection": [o for o in orders if o["initial_outcome"] == "REJECTED"],
        "confirmation_advanced_events": [{"elapsed": r["ts"]-start, **payload(r)}
                                          for r in by_kind("sm_trade_confirmation_advanced")],
        "identity_bound_canceled_post_scenario_occurred": False,
        "changes_to_strategy_or_code": False,
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2)+"\n")
    print(json.dumps({"market": summary["market"], "fill_cost": str(cost),
                      "realized_pnl": result["realized_merge_pnl"],
                      "normal_attempts": result["normal_windows"], "repair_attempts": result["repair_windows"],
                      "close_reasons": summary["close_request_reasons"],
                      "last_window": orders[-1]}, indent=2))


if __name__ == "__main__":
    main()
