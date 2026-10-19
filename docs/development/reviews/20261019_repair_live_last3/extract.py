"""Reconstruct the three sessions from local, immutable journals; no exchange I/O."""

import csv
import hashlib
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
SESSIONS = (
    "053c1d23a60c49dca6fc93798c476592",
    "5b44b7731b3640519f7b66c81b62f416",
    "cdfc165b84be4cfcb9154efe427e8129",
)


def main():
    summaries, orders, timeline = [], [], []
    for sid in SESSIONS:
        source = next((ROOT / "journal").glob(f"*_{sid}.jsonl"))
        original = source.read_bytes()
        rows = [json.loads(line) for line in original.splitlines()]
        payload = lambda row: row["data"]["payload"]
        session = next(payload(r) for r in rows if r["kind"] == "sm_session")
        result = next(payload(r) for r in rows if r["kind"] == "sm_run_result")
        start = int(session["slug"].rsplit("-", 1)[1])
        fills = {}
        phases = {payload(r)["window_id"]: payload(r)["state"]
                  for r in rows if r["kind"] == "sm_strategy_window"}
        closes = {payload(r)["window_id"]: payload(r)
                  for r in rows if r["kind"] == "sm_obi_close_requested"}
        for line, row in enumerate(rows, 1):
            d, kind = payload(row), row["kind"]
            if kind == "sm_trade":
                key = (d["trade_id"], d["maker_order_id"])
                entry = fills.setdefault(key, {**d, "first_observed_elapsed": row["ts"]-start,
                                               "status_history": []})
                entry["status"] = d["status"]
                entry["status_history"].append([row["ts"]-start, d["status"]])
            if kind == "sm_window_result":
                wid = d["window_id"]
                for a in d["placement_results"]:
                    q = a["quote"]
                    orders.append({"session_id": sid, "window": wid.split(":")[-1],
                                   "phase": phases[wid], "side": q["outcome"],
                                   "price": q["price"], "quantity": q["size"],
                                   "post_started": a["post_started"],
                                   "final_placement_outcome": a["outcome"],
                                   "close_reason": closes[wid]["reason"],
                                   "end_elapsed": d["end_time"]-start,
                                   "close_barrier": d["close_barrier"],
                                   "block_reason": d["block_reason"]})
            keep = kind in {"sm_trade", "sm_placement_result", "sm_prepared",
                            "sm_obi_close_requested", "sm_trade_confirmation_advanced",
                            "sm_repair_execution_blocked", "sm_merge_result"}
            keep |= kind == "sm_repair_decision" and d.get("action") not in {"HOLD", "KEEP"}
            if keep:
                timeline.append({"session_id": sid, "journal_line": line,
                                 "elapsed_wall": row["ts"]-start, "kind": kind, "payload": d})
        cost = sum((Decimal(str(f["price"]))*Decimal(str(f["size"]))
                    for f in fills.values()), Decimal(0))
        cache_path = ROOT / "research_outputs/periodic_sync" / sid / "summary.json"
        cache = json.loads(cache_path.read_bytes())
        summary = {"session_id": sid, "market": session["slug"],
                   "source": str(source.resolve()),
                   "sha256": hashlib.sha256(original).hexdigest(),
                   "time_basis": "Journal wall time minus market start; trade times are local observations.",
                   "actual_fill_cost": str(cost), "unique_fills": list(fills.values()),
                   "initial_placement_outcomes": dict(Counter(payload(r)["outcome"] for r in rows
                                                               if r["kind"] == "sm_placement_result")),
                   "http_posts": sum(r["kind"] == "sm_network_request"
                                     and payload(r)["method"] == "POST" for r in rows),
                   "result": {k: v for k, v in result.items() if k != "windows"},
                   "cache_health": {k: cache.get(k) for k in
                                    ("failure", "observer_fatal_type", "dropped_events", "io_drained")},
                   "cache_source_failures": cache["metrics"]["source_failures"]}
        assert source.read_bytes() == original
        summaries.append(summary)
    assert [s["actual_fill_cost"] for s in summaries] == ["0", "10.000", "1.950"]
    assert [s["result"]["delta"] for s in summaries] == [0, 0, -5]
    assert summaries[-1]["result"]["stop_reason"] == "QUOTE_WOULD_TAKE"
    assert summaries[-1]["http_posts"] == 5
    (OUT / "summary.json").write_text(json.dumps(summaries, indent=2)+"\n")
    (OUT / "timeline.json").write_text(json.dumps(timeline, indent=2)+"\n")
    with (OUT / "orders.csv").open("w") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(orders[0]))
        writer.writeheader()
        writer.writerows(orders)
    print(json.dumps([{k: s[k] for k in ("market", "actual_fill_cost", "http_posts")}
                      for s in summaries], indent=2))


if __name__ == "__main__":
    main()
