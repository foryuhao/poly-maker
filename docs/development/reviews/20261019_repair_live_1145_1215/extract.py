"""Read-only reconstruction of six consecutive completed live sessions."""

import csv
import hashlib
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
SESSIONS = (
    "a68f09b67b6442b38fc234123d14380e",
    "bb36be62dddf461ca63b572b4313b66d",
    "5f0e9e5f6b794d15931e4a7813e46a36",
    "bb77dc4494074b1193318c908ea067c0",
    "fec0e69719df4c70bde14da84fb4743b",
    "cef5c84a092441369a72975556b24417",
)


def get(row):
    return row["data"]["payload"]


def main():
    summaries, orders, timelines = [], [], []
    for sid in SESSIONS:
        source = next((ROOT / "journal").glob(f"*_{sid}.jsonl"))
        raw = source.read_bytes()
        rows = [json.loads(line) for line in raw.splitlines()]
        session = next(get(r) for r in rows if r["kind"] == "sm_session")
        result = next(get(r) for r in rows if r["kind"] == "sm_run_result")
        start = int(session["slug"].rsplit("-", 1)[1])
        def by_kind(kind, records=rows):
            return [r for r in records if r["kind"] == kind]
        cache_root = ROOT / "research_outputs/periodic_sync" / sid
        cache_source = cache_root / "snapshots.jsonl"
        cache_raw = cache_source.read_bytes()
        snapshots = {r["version"]: r for r in map(json.loads, cache_raw.splitlines())}
        tokens = json.loads((cache_root / "manifest.json").read_text())["market"]["tokens"]
        token_side = dict(zip(tokens, ("YES", "NO"), strict=True))
        placements = {get(r)["expected_order_id"]: get(r)
                      for r in by_kind("sm_placement_result")}
        phases = {w["result"]["window_id"]: w["state"] for w in result["windows"]}
        closes = {get(r)["window_id"]: r for r in by_kind("sm_obi_close_requested")}
        fills = {}
        for line, row in enumerate(rows, 1):
            d, kind, elapsed = get(row), row["kind"], row["ts"]-start
            if kind == "sm_trade":
                key = (d["trade_id"], d["maker_order_id"])
                entry = fills.setdefault(key, {**d, "side": token_side[d["token_id"]],
                                               "first_observed_elapsed": elapsed,
                                               "status_history": []})
                entry["status"] = d["status"]
                entry["status_history"].append([elapsed, d["status"]])
            keep = kind in {"sm_placement_result", "sm_trade", "sm_repair_quote_invalidated",
                            "sm_obi_close_requested", "sm_position_reconciled",
                            "sm_merge_intent", "sm_merge_submitted", "sm_merge_result"}
            keep |= kind == "sm_repair_decision" and d.get("action") not in {"HOLD", "KEEP"}
            if keep:
                timelines.append({"session_id": sid, "journal_line": line,
                                  "elapsed_wall": elapsed, "kind": kind, "payload": d})
        assert all(f["status"] == "CONFIRMED" for f in fills.values())
        cost = sum((Decimal(str(f["price"]))*Decimal(str(f["size"]))
                    for f in fills.values()), Decimal(0))
        assert Decimal(str(result["merged_pairs"]))-cost == Decimal(str(result["realized_merge_pnl"]))
        assert result["confirmed_yes"] == result["confirmed_no"] == result["residual_shares"] == 0
        assert result["execution_status"] == "COMPLETED"
        assert result["final_close_barrier"] == "SAFE_TO_CONTINUE"
        episodes = []
        for row in by_kind("sm_repair_episode"):
            d = get(row)
            if d["event"] != "COMPLETE_WITH_DUST_TOLERANCE" or Decimal(d["original_qty"]) <= 0:
                continue
            windows = set(d["windows"])
            own = [f for f in fills.values()
                   if placements[f["maker_order_id"]]["quote"]["window_id"] in windows]
            qty = {side: sum((Decimal(str(f["size"])) for f in own if f["side"] == side), Decimal(0))
                   for side in ("YES", "NO")}
            ep_cost = sum((Decimal(str(f["price"]))*Decimal(str(f["size"])) for f in own), Decimal(0))
            assert qty["YES"] == qty["NO"] == 5
            episodes.append({"opening_window": d["opening_window"], "windows": d["windows"],
                             "actual_fills": own, "paired_pnl": str(qty["YES"]-ep_cost),
                             "completed_elapsed": row["ts"]-start,
                             "last_transition": d.get("policy_state", {}).get("last_transition")})
        assert sum((Decimal(e["paired_pnl"]) for e in episodes), Decimal(0)) == Decimal(str(result["realized_merge_pnl"]))
        for row in by_kind("sm_window_result"):
            w = get(row)
            for a in w["placement_results"]:
                q, wid = a["quote"], w["window_id"]
                initial = placements[a["expected_order_id"]]
                own = [f for f in fills.values() if f["maker_order_id"] == a["expected_order_id"]]
                orders.append({"session_id": sid, "window": wid.rsplit(":", 1)[1],
                               "phase": phases[wid], "side": q["outcome"], "price": q["price"],
                               "quantity": q["size"], "post_started": a["post_started"],
                               "initial_outcome": initial["outcome"], "final_outcome": a["outcome"],
                               "close_reason": get(closes[wid])["reason"],
                               "close_requested_elapsed": closes[wid]["ts"]-start,
                               "end_elapsed": w["end_time"]-start,
                               "diagnostic_response_to_end_sec": w["end_time"]-next(
                                   r["ts"] for r in by_kind("sm_placement_result")
                                   if get(r)["expected_order_id"] == a["expected_order_id"]),
                               "actual_fill_qty": str(sum((Decimal(str(f["size"])) for f in own), Decimal(0))),
                               "close_barrier": w["close_barrier"], "block_reason": w["block_reason"]})
        # Reconstruct the same per-token quantity comparison on each pinned account snapshot.
        seen, eligible, merged, mismatches, compared = {}, set(), Decimal(0), [], 0
        for line, row in enumerate(rows, 1):
            d, kind = get(row), row["kind"]
            if kind == "sm_trade":
                key = (d["trade_id"], d["maker_order_id"])
                seen[key] = d
                if d["status"] == "CONFIRMED":
                    eligible.add(key)
            elif kind == "sm_position_reconciled":
                for f in d["included"]:
                    key = (f["trade_id"], f["maker_order_id"])
                    seen.setdefault(key, f)
                    eligible.add(key)
            elif kind == "sm_merge_result" and d["status"] == "CONFIRMED":
                merged += Decimal(d["amount_raw"])/1000000
            elif kind == "sm_sync_cache_read" and d["purpose"] == "account":
                compared += 1
                positions = snapshots[d["snapshot_version"]]["last_good"]["positions"]
                actual = {p["token_id"]: Decimal(str(p["size"])) for p in positions["data"]}
                diffs = {}
                for token in tokens:
                    expected = sum((Decimal(str(seen[k]["size"])) for k in eligible
                                    if seen[k]["token_id"] == token), Decimal(0))-merged
                    difference = actual.get(token, Decimal(0))-expected
                    if abs(difference) >= Decimal(".01"):
                        diffs[token_side[token]] = str(difference)
                if diffs:
                    mismatches.append({"journal_line": line, "elapsed_wall": row["ts"]-start,
                                       "cache_version": d["snapshot_version"], "differences": diffs})
        health = json.loads((cache_root / "summary.json").read_text())
        tolerance = sorted({get(r)["phase_inventory"]["tolerance_shares"] for r in by_kind("sm_obi_wait")
                            if get(r).get("phase_inventory")})
        assert tolerance == [.01]
        final_cash = Decimal(snapshots[max(snapshots)]["last_good"]["capital"]["data"]["balance"])/1000000
        unexplained_cash = final_cash-(Decimal(str(session["starting_official_balance_usdc"]))
                                       +Decimal(str(result["realized_merge_pnl"])))
        summaries.append({"market": session["slug"], "session_id": sid,
                          "source": str(source.resolve()), "source_sha256": hashlib.sha256(raw).hexdigest(),
                          "cache_source": str(cache_source.resolve()), "cache_sha256": hashlib.sha256(cache_raw).hexdigest(),
                          "result": {k: v for k, v in result.items() if k != "windows"},
                          "actual_fills": list(fills.values()), "actual_buy_cost": str(cost),
                          "episodes": episodes, "observed_inventory_tolerance": tolerance,
                          "account_references_compared": compared, "reconstructed_mismatch_references": mismatches,
                          "initial_outcomes": dict(Counter(get(r)["outcome"] for r in by_kind("sm_placement_result"))),
                          "actual_posts": sum(r["kind"] == "sm_network_request" and get(r)["method"] == "POST" for r in rows),
                          "starting_cash": str(session["starting_official_balance_usdc"]),
                          "final_cache_cash": str(final_cash), "cash_change_outside_session_trades_and_merge": str(unexplained_cash),
                          "cache_health": {k: health.get(k) for k in ("failure", "observer_fatal_type", "dropped_events", "io_drained")},
                          "cache_source_failures": health["metrics"]["source_failures"]})
        assert source.read_bytes() == raw and cache_source.read_bytes() == cache_raw
    totals = {"markets": len(summaries), "windows": len(orders),
              "normal_windows": sum(s["result"]["normal_windows"] for s in summaries),
              "repair_windows": sum(s["result"]["repair_windows"] for s in summaries),
              "actual_fills": sum(len(s["actual_fills"]) for s in summaries),
              "episodes": sum(len(s["episodes"]) for s in summaries),
              "actual_buy_cost": str(sum((Decimal(s["actual_buy_cost"]) for s in summaries), Decimal(0))),
              "merged_pairs": sum(s["result"]["merged_pairs"] for s in summaries),
              "realized_merge_pnl": str(sum((Decimal(str(s["result"]["realized_merge_pnl"])) for s in summaries), Decimal(0)))}
    assert totals["windows"] == 124 and totals["episodes"] == 6
    assert totals["actual_fills"] == 13 and totals["realized_merge_pnl"] == "1.55"
    (OUT / "summary.json").write_text(json.dumps({"totals": totals, "markets": summaries}, indent=2)+"\n")
    (OUT / "timeline.json").write_text(json.dumps(timelines, indent=2)+"\n")
    with (OUT / "orders.csv").open("w") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(orders[0]))
        writer.writeheader()
        writer.writerows(orders)
    print(json.dumps(totals, indent=2))
    print(json.dumps([{k: s[k] for k in ("market", "actual_posts", "initial_outcomes",
                                         "account_references_compared", "cash_change_outside_session_trades_and_merge")}
                      | {"mismatch_references": s["reconstructed_mismatch_references"]}
                      for s in summaries], indent=2))


if __name__ == "__main__":
    main()
