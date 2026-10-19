"""Read-only reconstruction of the first live SL execution test; no API access."""
import csv
import hashlib
import json
from collections import Counter
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from statistics import mean, median

from polymaker.config import Config
from polymaker.rdp_sm_network import NetworkHealth

SESSION = "414c7743c8b24879a0f7d81aeec8a850"
START = 1792405500
OUT = Path(__file__).parent
JOURNAL = next(Path("journal").glob(f"rdp_sm_*_{SESSION}.jsonl"))
COMPANION = Path("research_outputs/periodic_sync") / SESSION


def read(path):
    return [(i, json.loads(line)) for i, line in enumerate(path.open(), 1)]


def save(name, data):
    (OUT / name).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


rows = read(JOURNAL)
events = [(i, r["kind"], r["data"]["payload"], r["ts"]) for i, r in rows]
session = events[0][2]
result = next(p for _, k, p, _ in events if k == "sm_run_result")
placements = {p.get("order_id") or p.get("expected_order_id"): p
              for _, k, p, _ in events if k == "sm_placement_result"}
posts = {p["operation_id"]: (i, p) for i, k, p, _ in events
         if k == "sm_network_request" and p["method"] == "POST"}
sl = []
for line, kind, p, stamp in events:
    if kind != "sm_sl_command":
        continue
    oid = p["order_id"]
    post_line, native = posts["post:" + placements[oid]["request_hash"]]
    cancels = [(i, c) for i, k, c, _ in events if k == "sm_cancel_ack" and oid in c.get("ids", [])]
    sl_cancels = [(i, c) for i, c in cancels if c.get("scope") == "SL_ORDER"]
    first = min(c["delete_sdk_started"] for _, c in sl_cancels)
    sl.append({"window_id": p["window_id"], "order_id": oid, "price": p["price"],
               "quantity": p["quantity"], "initial_response": p["outcome"],
               "raw_response": placements[oid]["raw_response"],
               "journal_line": line, "native_post_line": post_line,
               "post_wall_elapsed": native["started_wall"] - START,
               "response_wall_elapsed": stamp - START,
               "signal_to_native_http_ms": 1000 * (
                   native["started_monotonic"] - p["signal_observed_monotonic"]),
               "native_http_ms": native["duration_ms"],
               "post_to_first_cancel_ms": 1000 * (first - native["started_monotonic"]),
               "actor_cancel_count": len(sl_cancels), "all_cancel_count": len(cancels),
               "first_cancel_line": min(sl_cancels, key=lambda c: c[1]["delete_sdk_started"])[0]})
assert len(sl) == 5
oid = sl[-1]["order_id"]
save("sl_orders.json", sl)

bad, seen = [], set()
for line, snapshot in read(COMPANION / "snapshots.jsonl"):
    detail = snapshot.get("attempts", {}).get("order:" + oid)
    if detail and detail["read_id"] not in seen:
        seen.add(detail["read_id"])
        bad.append({"snapshot_line": line, **detail})
assert len(bad) == 25 and all(r["error_code"] == "SYNC_ORDER_INVALID" for r in bad)
save("detail_failures.json", bad)

probes = [p for _, k, p, _ in events if k == "sm_network_request"
          and p["endpoint"] == "/time"]
middle = [p["completed_monotonic"] for p in probes
          if 128 < p["completed_wall"] - START < 240]
probe_spacings = [b-a for a, b in zip(middle, middle[1:], strict=False)]
assert len(middle) >= 3 and all(b-a > 5 for a, b in zip(middle, middle[2:], strict=False))


def recovery_reproduction(interval):
    clock = [0.0]
    network = NetworkHealth(Config().rdp_sm.obi, monotonic=lambda: clock[0])
    for _ in range(10):
        network.observe("GET", .2)
    network._pause("OFFLINE_REPRODUCTION", 0)
    states = []
    for step in range(1, 11):
        clock[0] = step * interval
        network.observe("GET", .2)
        states.append({"elapsed": clock[0], **asdict(network.snapshot())})
    return states


slow, fast = recovery_reproduction(8.4), recovery_reproduction(.5)
assert all(s["state"] == "PAUSED" for s in slow)
assert any(s["state"] == "PROBE" for s in fast)
save("recovery_reproduction.json", {"scope": "Offline simulated clock; no I/O",
                                    "8_4_second_probe_interval": slow,
                                    "half_second_probe_interval": fast})

keep = {"sm_strategy_window", "sm_placement_result", "sm_trade", "sm_position_reconciled",
        "sm_repair_decision", "sm_sl_signal", "sm_sl_command", "sm_sl_wait",
        "sm_sl_order_release", "sm_network_transition", "sm_merge_intent", "sm_merge_result"}
with (OUT / "timeline.csv").open("w") as f:
    writer = csv.writer(f)
    writer.writerow(["journal_line", "local_wall_elapsed_s", "event", "payload"])
    for line, kind, payload, stamp in sorted(events, key=lambda r: (r[3], r[0])):
        if kind in keep:
            writer.writerow([line, f"{stamp-START:.6f}", kind, json.dumps(payload)])

fills = {p["trade_id"]: p for _, k, p, _ in events if k == "sm_trade" and p["status"] == "CONFIRMED"}
spent = sum((Decimal(str(p["size"])) * Decimal(str(p["price"])) for p in fills.values()), Decimal(0))
merge_cash = Decimal(str(result["recovered_collateral_usdc"]))
assert spent == Decimal("5.8") and merge_cash == 5 and result["delta"] == 5
summary = {"session_id": SESSION, "market": session["slug"],
           "market_time_shanghai": "2026-10-19 18:25–18:30",
           "time_basis": "Timeline uses journal wall timestamp minus market epoch. Model elapsed uses refreshed venue clock; do not subtract wall and model elapsed directly.",
           "journal_lines": len(rows), "journal_kind_counts": Counter(k for _, k, _, _ in events),
           "result": {k: v for k, v in result.items() if k not in {"windows", "repair_readiness"}},
           "budget": {"session_limit": session["test_capital_limit_usdc"],
                      "actual_initial_collateral": session["starting_official_balance_usdc"]},
           "sl_orders": sl, "sl_signal_to_native_http_mean_ms": mean(r["signal_to_native_http_ms"] for r in sl),
           "detail_error_count": len(bad), "probe_gap_median_s_128_to_240": median(probe_spacings),
           "new_posts_after_wall_t128": sum(1 for _, p in posts.values() if p["started_wall"] > START+128),
           "cash": {"actual_buy_cost": str(spent), "merge_cash": str(merge_cash),
                    "cash_difference_before_residual_redemption": str(merge_cash-spent),
                    "remaining_yes": result["confirmed_yes"],
                    "final_winner": "UNKNOWN in journal; no resolution API queried",
                    "session_pnl_if_no_wins": str(merge_cash-spent),
                    "session_pnl_if_yes_wins": str(merge_cash-spent+Decimal(5)),
                    "logged_pair_pnl": result["theoretical_pair_pnl"]},
           "findings": ["Per-order HTTP-200 invalid detail drives entire orders lane backoff; sparse time probes cannot satisfy network recovery.",
                        "SL coordinator does not consume the existing canceled-submission proof path until final close; fifth ID receives repeated DELETEs.",
                        "SL wait diagnostic maps non-capital admission failures to WAIT_DELTA_CAPACITY, including network recovery.",
                        "Historical path shock is evaluated only after original NORMAL economic proof; fast POST does not include this earlier wait."],
           "source_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                             for p in [JOURNAL, COMPANION/"manifest.json", COMPANION/"events.jsonl", COMPANION/"snapshots.jsonl"]},
           "production_code_changed_during_review": False, "real_orders_sent_during_review": 0}
save("summary.json", summary)
print(json.dumps({k: summary[k] for k in ["session_id", "market", "sl_signal_to_native_http_mean_ms", "detail_error_count", "probe_gap_median_s_128_to_240", "new_posts_after_wall_t128", "cash"]}, indent=2))
