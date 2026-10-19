"""Decompose SL signal backlog and order execution using original timestamps."""

import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[3]
PREVIOUS = OUT.parent / "20261019_repair_last12_1220_1320"


def data(row):
    return row["data"]["payload"]


def main():
    summary = json.loads((PREVIOUS / "summary.json").read_text())
    episodes, windows, sources = [], [], []
    for market in summary["markets"]:
        sid, start = market["session_id"], market["start_epoch"]
        path = next((ROOT / "journal").glob(f"*_{sid}.jsonl"))
        raw = path.read_bytes()
        rows = [{**json.loads(line), "line": i} for i, line in enumerate(raw.splitlines(), 1)]
        kinds = defaultdict(list)
        for row in rows:
            kinds[row["kind"]].append(row)
        sources.append({"market": market["market"], "source": str(path.resolve()),
                        "sha256": hashlib.sha256(raw).hexdigest()})
        decisions = {data(r)["intent"]["window_id"]: data(r)["intent"]
                     for r in kinds["sm_obi_final_decision"]}
        results = {data(r)["window_id"]: r for r in kinds["sm_window_result"]}
        closes = {data(r)["window_id"]: r for r in kinds["sm_obi_close_requested"]}
        starts = {data(r)["window_id"]: r for r in kinds["sm_strategy_window"]}
        for ep in market["episodes"]:
            if ep["repair_fill_mode"] != "SL":
                continue
            state = next(r for r in kinds["sm_repair_policy_state"]
                         if data(r)["opening_window"] == ep["opening_window"]
                         and data(r)["mode"] == "SL")
            first_buy = next(r for r in kinds["sm_repair_decision"] if r["ts"] >= state["ts"]
                             and data(r).get("action") == "BUY" and data(r).get("mode") == "SL")
            decision = data(first_buy)
            # The transition and current observation share the strategy clock.
            # Do NOT subtract a strategy elapsed time directly from wall time.
            trigger = decision["last_transition"]["observations"]
            lag = decision["observations"]["elapsed"] - trigger["elapsed"]
            trigger_wall_proxy = first_buy["ts"] - start - lag
            original_result = data(results[ep["opening_window"]])
            entry = {"market": market["market"], "opening_window": ep["opening_window"],
                     "reason": decision["reason"], "trigger_strategy_elapsed": trigger["elapsed"],
                     "trigger_wall_elapsed_projection": trigger_wall_proxy,
                     "first_sl_buy_wall_elapsed": first_buy["ts"] - start,
                     "signal_backlog_sec": lag,
                     "first_sl_state_wall_elapsed": state["ts"]-start,
                     "historical_point_to_detection_strategy_sec":
                     data(state)["points"][-1]["elapsed"]-trigger["elapsed"],
                     "sl_detection_to_first_buy_wall_sec": first_buy["ts"]-state["ts"],
                     "trigger_yes_probability": trigger["p"],
                     "decision_yes_probability": decision["observations"]["p"],
                     "opening_close_wall_elapsed": original_result["end_time"] - start,
                     "first_repair_fill_wall_elapsed": ep["first_repair_fill_elapsed"],
                     "first_sl_buy_to_fill_sec": ep["first_repair_fill_elapsed"] - (first_buy["ts"] - start),
                     "state_line": state["line"], "buy_line": first_buy["line"]}
            episodes.append(entry)
            own_windows = [wid for wid in ep["windows"] if wid in starts
                           and data(starts[wid])["state"] == "REPAIR"
                           and data(starts[wid])["sizing"]["repair_plan"]["mode"] == "SL"]
            own_windows.sort(key=lambda wid: data(starts[wid])["elapsed_s"])
            for i, wid in enumerate(own_windows):
                result, intent, begin, close = data(results[wid]), decisions[wid], starts[wid], closes[wid]
                close_end = result["end_time"]
                post = [data(r) for r in kinds["sm_network_request"]
                        if data(r)["method"] == "POST" and data(r).get("batch_id") == wid]
                assert len(post) == 1
                post = post[0]
                deleted = [r for r in kinds["sm_network_request"]
                           if data(r)["method"] == "DELETE" and data(r).get("batch_id") == wid]
                assert len({data(r)["request_id"] for r in deleted}) == len(deleted)
                barrier = next(r for r in kinds["sm_close_barrier"] if close["ts"] <= r["ts"] <= close_end
                               and data(r)["result"] == "SAFE_TO_CONTINUE")
                cache = [r for r in kinds["sm_sync_cache_read"]
                         if close["ts"] <= r["ts"] <= barrier["ts"] and data(r)["purpose"] == "close"]
                last_cache = cache[-1] if cache else None
                next_intent = decisions[own_windows[i+1]] if i+1 < len(own_windows) else None
                since = post["started_wall"] - intent["post_started"]
                next_post_wall = next_intent["post_started"] + since if next_intent else None
                order = result["placement_results"][0]["expected_order_id"]
                matched = next((r for r in kinds["sm_trade"]
                                if data(r)["maker_order_id"] == order and data(r)["status"] == "MATCHED"), None)
                windows.append({
                    "market": market["market"], "opening_window": ep["opening_window"], "window": wid,
                    "price": result["requested_quotes"][0]["price"],
                    "post_wall_elapsed": post["started_wall"]-start,
                    "post_ack_wall_elapsed": post["completed_wall"]-start,
                    "post_http_sec": post["duration_ms"]/1000,
                    "close_requested_wall_elapsed": close["ts"]-start,
                    "close_reason": data(close)["reason"],
                    "ack_to_close_sec": close["ts"]-post["completed_wall"],
                    "delete_requests": len(deleted),
                    "first_delete_completed_wall_elapsed": min(data(r)["completed_wall"] for r in deleted)-start if deleted else None,
                    "last_delete_completed_wall_elapsed": max(data(r)["completed_wall"] for r in deleted)-start if deleted else None,
                    "barrier_safe_wall_elapsed": barrier["ts"]-start,
                    "close_to_safe_sec": barrier["ts"]-close["ts"],
                    "next_post_wall_elapsed": next_post_wall-start if next_post_wall else None,
                    "close_to_next_post_sec": next_post_wall-close["ts"] if next_post_wall else None,
                    "matched_wall_elapsed": matched["ts"]-start if matched else None,
                    "close_cache_sources": data(last_cache)["sources"] if last_cache else None,
                    "last_write_monotonic": data(last_cache)["operation_after_monotonic"] if last_cache else None,
                    "lines": {"post": begin["line"], "close": close["line"], "safe": barrier["line"]},
                })
        assert hashlib.sha256(path.read_bytes()).hexdigest() == sources[-1]["sha256"]
    assert len(episodes) == 10
    (OUT / "latency.json").write_text(json.dumps({"episodes": episodes, "windows": windows}, indent=2))
    for name, items in (("episodes.csv", episodes), ("sl_windows.csv", windows)):
        projected = [{k: v for k, v in r.items() if k not in {"close_cache_sources", "lines"}} for r in items]
        with (OUT / name).open("w") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(projected[0]))
            writer.writeheader()
            writer.writerows(projected)
    (OUT / "source_manifest.json").write_text(json.dumps(sources, indent=2))
    # Scope all account evidence to this market's tokens; other account positions
    # are irrelevant to this diagnosis and should not be copied into the report.
    target = next(m for m in summary["markets"] if m["market"] == "13:05")
    cache = ROOT / "research_outputs/periodic_sync" / target["session_id"]
    tokens = json.loads((cache / "manifest.json").read_text())["market"]["tokens"]
    account_points, seen = [], set()
    for line in (cache / "snapshots.jsonl").open():
        snap = json.loads(line)
        pos = snap.get("last_good", {}).get("positions")
        if (not pos or pos["read_id"] in seen
                or not 60 <= pos["completed_wall"]-target["start_epoch"] <= 74):
            continue
        seen.add(pos["read_id"])
        current = {r["token_id"]: r for r in pos["data"] if r["token_id"] in tokens}
        account_points.append({"read_id": pos["read_id"],
                               "started_elapsed": pos["started_wall"]-target["start_epoch"],
                               "completed_elapsed": pos["completed_wall"]-target["start_epoch"],
                               "yes": current.get(tokens[0], {}).get("size", 0),
                               "no": current.get(tokens[1], {}).get("size", 0)})
    (OUT / "1305_positions.json").write_text(json.dumps(account_points, indent=2))
    sample = [w for w in windows if w["market"] == "13:05"]
    episode = next(e for e in episodes if e["market"] == "13:05")
    breakdown = {
        "buy_to_first_post_sec": sample[0]["post_wall_elapsed"]-episode["first_sl_buy_wall_elapsed"],
        "four_post_wall_durations_sec": sum(w["post_ack_wall_elapsed"]-w["post_wall_elapsed"] for w in sample),
        "first_three_ack_to_close_sec": sum(w["ack_to_close_sec"] for w in sample[:3]),
        "first_three_close_to_next_post_sec": sum(w["close_to_next_post_sec"] for w in sample[:3]),
        "last_ack_to_observed_matched_sec": episode["first_repair_fill_wall_elapsed"]-sample[-1]["post_ack_wall_elapsed"],
    }
    assert abs(sum(breakdown.values())-episode["first_sl_buy_to_fill_sec"]) < .001
    (OUT / "1305_decomposition.json").write_text(json.dumps(breakdown, indent=2))
    print("SL episodes:", len(episodes), "SL order windows:", len(windows))
    for ep in episodes:
        print(ep["market"], ep["opening_window"].rsplit(":", 1)[1],
              "signal backlog", round(ep["signal_backlog_sec"], 3),
              "buy to fill", round(ep["first_sl_buy_to_fill_sec"], 3))
    for w in windows:
        if w["market"] == "13:05":
            print(json.dumps({k: v for k, v in w.items() if k not in {"close_cache_sources", "lines"}}))


if __name__ == "__main__":
    main()
