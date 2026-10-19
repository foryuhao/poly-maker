"""Read-only extraction of the two live rejected-order cancellation loops."""

import hashlib
import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OUTPUT = Path(__file__).resolve().parent
SOURCE = ROOT / "journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_f42def203ba8435497ed8ae9c90331f6.jsonl"


def main():
    rows = [json.loads(line) for line in SOURCE.open()]
    decisions = {r["data"]["payload"]["intent"]["window_id"]: r["data"]["payload"]["intent"]
                 for r in rows if r["kind"] == "sm_obi_final_decision"}
    windows = {r["data"]["payload"]["window_id"]: r["data"]["payload"]
               for r in rows if r["kind"] == "sm_window_result"}
    findings = []
    for row in rows:
        p = row["data"]["payload"]
        if row["kind"] != "sm_placement_result" or p["outcome"] != "REJECTED":
            continue
        window_id, oid = p["quote"]["window_id"], p["expected_order_id"]
        intent, result = decisions[window_id], windows[window_id]
        cancels = []
        for record in rows:
            d = record["data"]["payload"]
            if record["kind"] != "sm_cancel_ack" or oid not in d["ids"]:
                continue
            ack = d.get("result", d.get("ack"))
            cancels.append({"sdk_started": ack["sdk_started_monotonic"],
                            "ack": ack["ack_monotonic"], "not_canceled": ack["not_canceled"]})
        next_post = min(d["post_started"] for d in decisions.values()
                        if d["post_started"] > intent["post_returned"])
        findings.append({
            "window_id": window_id, "expected_order_id": oid,
            "quote": p["quote"], "raw_response": p["raw_response"],
            "post_request_sec": intent["post_returned"]-intent["post_started"],
            "post_response_to_blocked_return_sec": result["close_monotonic"]-intent["post_returned"],
            "post_response_to_next_post_sec": next_post-intent["post_returned"],
            "blocked_return_to_next_post_sec": next_post-result["close_monotonic"],
            "close_barrier": result["close_barrier"], "block_reason": result["block_reason"],
            "delete_count": len(cancels),
            "delete_sdk_mean_sec": statistics.mean(c["ack"]-c["sdk_started"] for c in cancels),
            "all_cancels_not_found": all(c["not_canceled"] == [[oid,
                "order can't be found - already canceled or matched"]] for c in cancels),
            "cancels": cancels,
        })
    output = {"source": str(SOURCE.resolve()),
              "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
              "time_basis": "Monotonic POST/DELETE boundaries; placement diagnostics can follow an awaited DELETE.",
              "rejected_windows": findings}
    (OUTPUT / "live_latency.json").write_text(json.dumps(output, indent=2)+"\n")
    print(json.dumps([{k: v for k, v in f.items() if k not in {
        "quote", "expected_order_id", "raw_response", "cancels"}} for f in findings], indent=2))


if __name__ == "__main__":
    main()
