"""Read-only reconstruction; never invokes the gateway or a trading command."""
import csv
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
SESSION = "52b1dd0a83be482e8844f82829ed228a"
JOURNAL = next((ROOT / "journal").glob(f"rdp_sm_*_{SESSION}.jsonl"))
SYNC = ROOT / "research_outputs" / "periodic_sync" / SESSION
rows = [(n, json.loads(line)) for n, line in enumerate(JOURNAL.open(), 1)]
assert all(r["data"]["session_id"] == SESSION for _, r in rows)
session = next(r["data"]["payload"] for _, r in rows if r["kind"] == "sm_session")
result = next(r["data"]["payload"] for _, r in rows if r["kind"] == "sm_run_result")
start = int(session["slug"].rsplit("-", 1)[1])
zone = ZoneInfo("Asia/Shanghai")
decisions, timeline = [], []

for line, r in rows:
    kind, d, ts = r["kind"], r["data"]["payload"], r["ts"]
    base = {"source_line": line, "wall_time_shanghai": datetime.fromtimestamp(ts, zone).isoformat(),
            "wall_elapsed_sec": ts-start}
    if kind in {"sm_strategy_window", "sm_obi_close_requested", "sm_trade", "sm_repair_episode",
                "sm_window_result", "sm_obi_wait", "sm_prepared"}:
        selected = {k: d[k] for k in ("event", "reason", "state", "window_id", "phase", "status",
                    "price", "size", "original_qty", "actual_delta", "delta", "close_barrier") if k in d}
        if kind == "sm_strategy_window":
            selected["quotes"] = [{k: q[k] for k in ("outcome", "price", "size")} for q in d["quotes"]]
        timeline.append({**base, "kind": kind, "evidence": json.dumps(selected, ensure_ascii=False)})
    if kind != "sm_repair_decision":
        continue
    s = d["state"]
    hold = next(c for c in d["candidates"] if c["action"] == "HOLD")
    buy = max((c for c in d["candidates"] if c["action"] == "BUY"), key=lambda c: c["expected_pnl"])
    assert abs(s["p"]*s["yes"]+(1-s["p"])*s["no"]-s["cost"]-d["current_mark_pnl"]) < 1e-10
    assert d["action"] == ("BUY" if buy["expected_pnl"] > hold["expected_pnl"]+1e-10 else "HOLD")
    decisions.append({**base, "model_input_T": s["elapsed"], "p_yes": s["p"], "sigma_per_sqrt_second": s["sigma"],
        "no_best_bid": s["bid"], "no_best_ask": s["ask"], "action": d["action"],
        "yes": s["yes"], "no": s["no"], "cost": s["cost"], "session_cash": s["cash"],
        "floating_pnl_at_model_input": d["current_mark_pnl"], "Q_hold_total_pnl": hold["expected_pnl"],
        "hold_continuation_gain": hold.get("continuation_gain", 0), "Q_best_buy_total_pnl": buy["expected_pnl"],
        "best_buy_increment_vs_current_mark": buy["incremental_ev"],
        "best_buy_advantage_vs_hold": buy["expected_pnl"]-hold["expected_pnl"],
        "buy_quantity": buy["quantity"], "buy_cancel_at_T": buy["cancel_at"],
        "best_buy_settlement_sd": buy["settlement_sd"], "hold_settlement_sd": hold["settlement_sd"],
        "parameter_status": d["parameter_status"]})

for name, data in (("decisions.csv", decisions), ("timeline.csv", timeline)):
    with (OUT / name).open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(data[0]))
        writer.writeheader()
        writer.writerows(data)

actions = Counter(d["action"] for d in decisions)
posts = [r for _, r in rows if r["kind"] == "sm_network_request" and r["data"]["payload"].get("method") == "POST"]
prepared = [n for n, r in rows if r["kind"] == "sm_prepared" and r["data"]["payload"]["phase"] == "REPAIR"]
assert actions == {"HOLD": 79, "BUY": 2} and len(posts) == 3 and len(prepared) == 2
assert (result["normal_windows"], result["repair_windows"], result["confirmed_yes"], result["confirmed_no"]) == (3, 0, 5, 0)
assert result["delta"] == 5 and result["final_close_barrier"] == "SAFE_TO_CONTINUE"
sources = [JOURNAL, SYNC / "snapshots.jsonl", SYNC / "summary.json", SYNC / "manifest.json"]
audit = {"session_id": SESSION, "slug": session["slug"], "start_epoch": start,
    "journal_rows": len(rows), "actions": dict(actions), "actual_post_requests": len(posts),
    "repair_prepared_lines": prepared, "repair_actual_posts": 0,
    "held_despite_positive_buy_incremental_EV": sum(d["action"] == "HOLD" and
        d["best_buy_increment_vs_current_mark"] > 1e-10 for d in decisions),
    "model_versions": sorted({session["repair"]["model_version"]}),
    "result": {k: result[k] for k in ("normal_windows", "repair_windows", "delta", "confirmed_yes", "confirmed_no",
        "merged_pairs", "final_close_barrier", "execution_status", "stop_reason")},
    "sources": [{"path": str(p.resolve()), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in sources],
    "limitations": ["event wall time is observation/logging time, not native matching time",
        "no official winner/payout evidence in this reconstruction",
        "second activation failure deduplicated: counter evidence, no independent reason snapshot",
        "81 model results are not 81 accepted orders or evidence of positive realized EV"]}
(OUT / "audit.json").write_text(json.dumps(audit, indent=2, ensure_ascii=False)+"\n")
print(json.dumps({k: audit[k] for k in ("session_id", "actions", "actual_post_requests", "repair_actual_posts")}, ensure_ascii=False))
