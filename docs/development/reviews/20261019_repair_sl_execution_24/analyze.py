"""Read-only SL implementation-shortfall audit of the previously locked 24 markets.

Times use native monotonic clocks. Historical signal times are projected from
the current observation in the SAME policy call, never subtracted from wall
time without an anchor. Quotes/marks are benchmarks, not hypothetical fills.
"""

import csv
import hashlib
import json
import statistics
from collections import Counter, defaultdict
from decimal import Decimal as D
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[3]
BASE = OUT.parent / "20261019_repair_24_markets_1220_1535"


def pl(row):
    return row["data"]["payload"]


def mono(row):
    return row["data"]["occurred_monotonic"]


def current(row):
    d = pl(row)
    if row["kind"] == "sm_repair_policy_state" and d.get("points"):
        return d["points"][-1]
    if row["kind"] == "sm_repair_decision":
        return d.get("observations")
    if row["kind"] == "sm_repair_execution_blocked":
        return (d.get("decision") or {}).get("observations")
    return None


def write_csv(name, rows):
    if rows:
        with (OUT / name).open("w") as f:
            w = csv.DictWriter(f, fieldnames=list(dict.fromkeys(k for row in rows for k in row)))
            w.writeheader()
            w.writerows(rows)


def stats(values):
    values = sorted(values)
    return {"n": len(values), "sum": sum(values), "mean": statistics.mean(values),
            "median": statistics.median(values), "max": max(values), "min": min(values)} if values else {}


def intervals_duration(intervals, lower, upper):
    clipped = sorted((max(a, lower), min(b, upper)) for a, b in intervals
                     if min(b, upper) > max(a, lower))
    total, end = 0., lower
    for a, b in clipped:
        total += max(0., b-max(a, end))
        end = max(end, b)
    return total


def main():
    base = json.loads((BASE / "summary.json").read_text())
    expected = {r["session_id"]: r for r in json.loads((BASE / "source_manifest.json").read_text())}
    episodes, windows, gaps, evidence, paths_export = [], [], [], [], []
    for market in base["markets"]:
        selected = [e for e in market["episodes"] if e["repair_fill_mode"] == "SL"]
        if not selected:
            continue
        sid, start = market["session_id"], market["start_epoch"]
        source = Path(expected[sid]["source"])
        raw = source.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == expected[sid]["sha256"]
        rows = [{**json.loads(line), "line": i} for i, line in enumerate(raw.splitlines(), 1)]
        kinds = defaultdict(list)
        for r in rows:
            kinds[r["kind"]].append(r)
        results = {pl(r)["window_id"]: pl(r) for r in kinds["sm_window_result"]}
        strategies = {pl(r)["window_id"]: r for r in kinds["sm_strategy_window"]}
        intents = {pl(r)["intent"]["window_id"]: pl(r)["intent"] for r in kinds["sm_obi_final_decision"]}
        closes = {pl(r)["window_id"]: r for r in kinds["sm_obi_close_requested"]}
        posts = {pl(r)["batch_id"]: pl(r) for r in kinds["sm_network_request"]
                 if pl(r)["method"] == "POST"}
        # Map recorded price history to local monotonic times. During an active
        # batch the official clock anchor is frozen, so its native POST is the
        # stronger reference than a later replay/detection timestamp.
        clocks = [{"start": intents[wid]["post_started"], "end": res["close_monotonic"],
                   "offset": intents[wid]["post_started"]-pl(strategies[wid])["elapsed_s"]}
                  for wid, res in results.items() if pl(strategies[wid])["state"] == "REPAIR"
                  and intents[wid]["post_started"] is not None]
        points, unmapped = defaultdict(list), Counter()
        for i, row in enumerate(rows):
            if row["kind"] != "sm_repair_market_path":
                continue
            d = pl(row)
            clock = next((w for w in clocks if w["start"] <= mono(row) <= w["end"]), None)
            if clock:
                offset = clock["offset"]
            else:
                reference = next(((nxt, current(nxt)) for nxt in rows[i+1:i+16]
                                  if mono(nxt)-mono(row) <= .2 and current(nxt)), None)
                if reference is None:
                    unmapped[d["opening_window"]] += 1
                    continue
                offset = mono(reference[0])-reference[1]["elapsed"]
            for point in d["points"]:
                points[d["opening_window"]].append({**point, "monotonic": offset+point["elapsed"],
                                                   "journal_line": row["line"]})

        for ep in selected:
            opening, side = ep["opening_window"], ep["original_side"]
            state = next(r for r in kinds["sm_repair_policy_state"]
                         if pl(r)["opening_window"] == opening and pl(r)["mode"] == "SL")
            trigger, now = pl(state)["last_transition"]["observations"], current(state)
            assert "p" in trigger and now is not None
            backlog = now["elapsed"]-trigger["elapsed"]
            assert backlog >= -1e-6
            detection, signal = mono(state), mono(state)-backlog
            first_buy = next(r for r in kinds["sm_repair_decision"] if mono(r) >= detection
                             and pl(r).get("mode") == "SL" and pl(r).get("action") == "BUY")
            sl_ids = [wid for wid in ep["windows"] if wid in strategies
                      and pl(strategies[wid])["state"] == "REPAIR"
                      and pl(strategies[wid])["sizing"]["repair_plan"]["mode"] == "SL"]
            sl_ids.sort(key=lambda wid: posts[wid]["started_monotonic"])
            assert sl_ids
            repair = [f for f in ep["actual_fills"] if f["side"] != side]
            original = [f for f in ep["actual_fills"] if f["side"] == side]
            q0 = D(ep["original_quantity"])
            original_cost = sum(((D(str(f["price"]))*D(str(f["size"]))).quantize(D(".000001"))
                                 for f in original), D(0))
            repair_qty = sum((D(str(f["size"])) for f in repair), D(0))
            repair_cost = sum(((D(str(f["price"]))*D(str(f["size"]))).quantize(D(".000001"))
                               for f in repair), D(0))
            quantity = min(q0, repair_qty)
            average_entry, repair_vwap = original_cost/q0, repair_cost/repair_qty
            first_fill = min(repair, key=lambda f: f["journal_line"])
            last_fill = max(repair, key=lambda f: f["journal_line"])
            fill_mono = mono(rows[first_fill["journal_line"]-1])
            last_fill_mono = mono(rows[last_fill["journal_line"]-1])
            confirmations = [mono(r) for r in kinds["sm_trade"]
                             if pl(r)["status"] == "CONFIRMED"
                             and pl(r)["maker_order_id"] in {f["maker_order_id"] for f in repair}]
            original_confirmations = [mono(r) for r in kinds["sm_trade"]
                                      if pl(r)["status"] == "CONFIRMED"
                                      and pl(r)["maker_order_id"] in {f["maker_order_id"] for f in original}]
            first_post = posts[sl_ids[0]]
            local_offset = first_post["started_wall"]-first_post["started_monotonic"]-start

            def elapsed(at, local_offset=local_offset):
                return at+local_offset

            def held(p, side=side):
                return D(str(p)) if side == "YES" else 1-D(str(p))

            def marked(p, quantity=quantity, average_entry=average_entry, held=held):
                return quantity*(held(p)-average_entry)

            first_book = pl(strategies[sl_ids[0]])["books"]
            first_p = (D(str(first_book["YES"]["best_bid"]))+D(str(first_book["YES"]["best_ask"])))/2
            opposite = "NO" if side == "YES" else "YES"
            quote_price = D(str(results[sl_ids[0]]["requested_quotes"][0]["price"]))
            benchmarks = {"trigger_mark": marked(trigger["p"]), "detection_mark": marked(now["p"]),
                          "first_buy_mark": marked(pl(first_buy)["observations"]["p"]),
                          "first_post_mark": marked(first_p),
                          "first_post_quote_pair_target": quantity*(1-average_entry-quote_price),
                          "actual_pair_pnl": quantity*(1-average_entry-repair_vwap)}
            assert abs(benchmarks["actual_pair_pnl"]-D(ep["paired_pnl"])) < D("1e-9")
            money_phases = {"signal_to_detection": benchmarks["trigger_mark"]-benchmarks["detection_mark"],
                            "detection_to_first_post": benchmarks["detection_mark"]-benchmarks["first_post_mark"],
                            "post_mark_to_quote": benchmarks["first_post_mark"]-benchmarks["first_post_quote_pair_target"],
                            "first_quote_to_fill": benchmarks["first_post_quote_pair_target"]-benchmarks["actual_pair_pnl"]}
            assert abs(sum(money_phases.values(), D(0))-(benchmarks["trigger_mark"]-benchmarks["actual_pair_pnl"])) < D("1e-12")
            own_points = sorted(points[opening], key=lambda p: p["monotonic"])

            def asof(at, own_points=own_points):
                before = [p for p in own_points if p["monotonic"] <= at+1e-5]
                return before[-1] if before else None

            own_windows = []
            for i, wid in enumerate(sl_ids):
                post, result, strategy = posts[wid], results[wid], pl(strategies[wid])
                quote = result["requested_quotes"][0]
                oid = result["placement_results"][0]["expected_order_id"]
                close = closes[wid]
                close_at = pl(close)["requested_monotonic"]
                deletes = [pl(r) for r in kinds["sm_network_request"] if pl(r)["method"] == "DELETE"
                           and pl(r).get("batch_id") == wid]
                safe = next(r for r in kinds["sm_close_barrier"] if mono(close) <= mono(r) <= result["close_monotonic"]
                            and pl(r)["result"] == "SAFE_TO_CONTINUE")
                caches = [r for r in kinds["sm_sync_cache_read"] if close_at <= mono(r) <= mono(safe)
                          and pl(r)["purpose"] == "close"]
                cache = pl(caches[-1]) if caches else None
                cancel_acks = [r for r in kinds["sm_cancel_ack"] if oid in pl(r).get("ids", [])
                               and close_at <= mono(r) <= result["close_monotonic"]]
                positive = [r for r in cancel_acks
                            if oid in (pl(r).get("result") or pl(r).get("ack") or {}).get("canceled", [])]
                canceled_at = min(((pl(r).get("result") or pl(r).get("ack"))["ack_monotonic"]
                                   for r in positive), default=None)
                fills = [f for f in repair if f["maker_order_id"] == oid]
                plan = strategy["sizing"]["repair_plan"]
                books = strategy["books"]
                final = intents[wid]["final_snapshot"]
                guard_age = max(post["started_monotonic"]-final[k]["received_mono"] for k in ("yes", "no"))
                pp = (D(str(books["YES"]["best_bid"]))+D(str(books["YES"]["best_ask"])))/2
                ack_point = asof(post["completed_monotonic"])
                post_point = asof(post["started_monotonic"])
                match = min((mono(rows[f["journal_line"]-1]) for f in fills), default=None)
                record = {"market": market["market"], "opening_window": opening, "window": wid,
                          "price": quote["price"], "quantity": quote["size"], "outcome": result["placement_results"][0]["outcome"],
                          "post": elapsed(post["started_monotonic"]), "ack": elapsed(post["completed_monotonic"]),
                          "post_monotonic": post["started_monotonic"], "ack_monotonic": post["completed_monotonic"],
                          "http_sec": post["duration_ms"]/1000, "guard_book_age_ms": guard_age*1000,
                          "plan_age_at_post_sec": strategy["elapsed_s"]-plan["observations"]["elapsed"],
                          "guard_price_equals_ask_minus_tick": D(str(quote["price"])) == D(str(books[opposite]["best_ask"]))-D(str(books[opposite]["tick"])),
                          "post_yes_mid": str(pp), "raw_asof_post_yes_mid": post_point["p"] if post_point else None,
                          "raw_asof_ack_yes_mid": ack_point["p"] if ack_point else None,
                          "raw_asof_ack_point_age_sec": post["completed_monotonic"]-ack_point["monotonic"] if ack_point else None,
                          "close": elapsed(close_at), "close_monotonic": close_at, "close_reason": pl(close)["reason"],
                          "cancel_positive_ack": elapsed(canceled_at) if canceled_at else None,
                          "cancel_positive_ack_monotonic": canceled_at,
                          "delete_requests": len(deletes),
                          "delete_starts_after_positive_ack": sum(p["started_monotonic"] > canceled_at for p in deletes) if canceled_at else 0,
                          "first_delete_ack": elapsed(min(p["completed_monotonic"] for p in deletes)) if deletes else None,
                          "last_delete_ack": elapsed(max(p["completed_monotonic"] for p in deletes)) if deletes else None,
                          "safe": elapsed(mono(safe)), "safe_monotonic": mono(safe),
                          "close_cache_sources": cache["sources"] if cache else None,
                          "close_write_boundary": cache["operation_after_monotonic"] if cache else None,
                          "matched": elapsed(match) if match else None,
                          "actual_fill_quantity": str(sum((D(str(f["size"])) for f in fills), D(0))),
                          "close_proof_order": pl(safe).get("proofs", {}).get(oid, {}).get("order"),
                          "lines": {"strategy": strategies[wid]["line"], "close": close["line"], "safe": safe["line"]}}
                own_windows.append(record)
                windows.append(record)
                if i+1 < len(sl_ids):
                    next_wid = sl_ids[i+1]
                    next_at = posts[next_wid]["started_monotonic"]
                    next_price = D(str(results[next_wid]["requested_quotes"][0]["price"]))
                    last_source = max(cache["sources"], key=lambda k: cache["sources"][k]["completed_monotonic"]) if cache else None
                    gap = {"market": market["market"], "opening_window": opening, "from_window": wid,
                           "to_window": next_wid, "close_reason": pl(close)["reason"],
                           "from_price": quote["price"], "to_price": str(next_price),
                           "close_to_next_post_sec": next_at-close_at,
                           "close_to_first_delete_ack_sec": min(p["completed_monotonic"] for p in deletes)-close_at if deletes else None,
                           "positive_cancel_to_next_post_sec": next_at-canceled_at if canceled_at else None,
                           "last_delete_to_safe_sec": mono(safe)-max(p["completed_monotonic"] for p in deletes) if deletes else None,
                           "safe_to_next_post_sec": next_at-mono(safe),
                           "last_page_source_in_accepted_close_frame": last_source,
                           "latest_page_finished_before_safe_sec": mono(safe)-cache["sources"][last_source]["completed_monotonic"] if cache else None,
                           "quote_price_change": str(next_price-D(str(quote["price"]))),
                           "fixed_matched_basket_price_change_usdc": str(quantity*(next_price-D(str(quote["price"])))),
                           "delete_requests": len(deletes), "delete_starts_after_positive_ack": record["delete_starts_after_positive_ack"]}
                    gaps.append(gap)
            post_start = first_post["started_monotonic"]
            post_intervals = [(w["post_monotonic"], w["ack_monotonic"]) for w in own_windows]
            gap_intervals = [(w["close_monotonic"], nxt["post_monotonic"])
                             for w, nxt in zip(own_windows, own_windows[1:], strict=False)]
            http = intervals_duration(post_intervals, post_start, fill_mono)
            close_gap = intervals_duration(gap_intervals, post_start, fill_mono)
            overlaps = [(max(a, c), min(b, d)) for a, b in post_intervals for c, d in gap_intervals
                        if max(a, c) < min(b, d)]
            close_gap -= intervals_duration(overlaps, post_start, fill_mono)
            # The remaining elapsed time includes resting/monitoring, and may
            # contain delayed observation of an already matched venue trade.
            resting = fill_mono-post_start-http-close_gap
            assert resting >= -1e-6
            time_phases = {"signal_to_detection": backlog, "detection_to_first_post": post_start-detection,
                           "post_http": http, "close_to_repost": close_gap, "resting_or_observation": resting}
            assert abs(sum(time_phases.values())-(fill_mono-signal)) < 1e-6
            original_end = results[opening]["close_monotonic"]
            previous_tp = [wid for wid in ep["windows"] if wid in strategies
                           and pl(strategies[wid])["state"] == "REPAIR"
                           and pl(strategies[wid])["sizing"]["repair_plan"]["mode"] == "TP"
                           and posts[wid]["started_monotonic"] < post_start]
            rec = {"market": market["market"], "opening_window": opening,
                   "path": ep["path_to_repair_fill"], "side": side, "original_quantity": str(q0),
                   "matched_quantity_used_for_benchmarks": str(quantity), "original_avg_price": str(average_entry),
                   "repair_quantity": str(repair_qty), "repair_vwap": str(repair_vwap),
                   "reason": pl(state)["reason"], "trigger_inventory_path_valid": trigger["inventory_path_valid"],
                   "signal": elapsed(signal), "detected": elapsed(detection), "first_buy": elapsed(mono(first_buy)),
                   "first_post": elapsed(post_start), "first_ack": elapsed(first_post["completed_monotonic"]),
                   "first_match": elapsed(fill_mono), "last_match": elapsed(last_fill_mono),
                   "last_confirmation": elapsed(max(confirmations)),
                   "opening_last_confirmation": elapsed(max(original_confirmations)), "opening_close": elapsed(original_end),
                   "signal_before_opening_close_sec": max(0., original_end-signal),
                   "opening_close_to_detection_sec": max(0., detection-max(signal, original_end)),
                   "detection_to_first_buy_sec": mono(first_buy)-detection,
                   "first_buy_to_first_post_sec": post_start-mono(first_buy),
                   "signal_to_first_match_sec": fill_mono-signal,
                   "first_post_to_first_match_sec": fill_mono-post_start,
                   "last_match_to_confirmation_sec": max(confirmations)-last_fill_mono,
                   "previous_tp_windows": previous_tp, "sl_windows": sl_ids,
                   "price_sequence": [w["price"] for w in own_windows], "first_quote_price": str(quote_price),
                   "signal_yes_p": trigger["p"], "detection_yes_p": now["p"], "post_yes_p": str(first_p),
                   "time_phases": time_phases, "benchmarks": {k: str(v) for k, v in benchmarks.items()},
                   "money_phases": {k: str(v) for k, v in money_phases.items()},
                   "actual_full_pnl": ep["full_pnl"], "unmapped_path_batches": unmapped[opening],
                   "source_lines": {"sl_state": state["line"], "first_buy": first_buy["line"], "first_match": first_fill["journal_line"]}}
            episodes.append(rec)
        for point in own_points:
                if signal-3 <= point["monotonic"] <= last_fill_mono+.1:
                    paths_export.append({"market": market["market"], "opening_window": opening,
                                         "wall_elapsed_projection": elapsed(point["monotonic"]), **point})
        # Investigate when a narrower zero-fill cancellation proof was already
        # OBSERVED in the existing cache. This is evidence availability, not a
        # simulated replacement order or permission to skip the current gate.
        market_gaps = [g for g in gaps if g["market"] == market["market"]]
        candidates = {}
        for gap in market_gaps:
            w = next(w for w in windows if w["window"] == gap["from_window"])
            if w["cancel_positive_ack_monotonic"] is not None:
                candidates[w["window"]] = (w, gap)
        snapshot_path = ROOT / "research_outputs/periodic_sync" / sid / "snapshots.jsonl"
        with snapshot_path.open() as stream:
            for line in stream:
                snap = json.loads(line)
                at = snap["published_monotonic"]
                if snap.get("fatal_type"):
                    continue
                good, attempted = snap.get("last_good", {}), snap.get("attempts", {})
                for wid, (w, gap) in candidates.items():
                    if "narrow_zero_fill_proof_available_sec_before_actual_post" in gap:
                        continue
                    boundary = w["cancel_positive_ack_monotonic"]
                    if at < boundary or at > posts[gap["to_window"]]["started_monotonic"]:
                        continue
                    oid = results[wid]["placement_results"][0]["expected_order_id"]
                    names = ["open_orders", "trades", "order:"+oid]
                    if any(name not in good or not good[name]["complete"]
                           or good[name]["started_monotonic"] < boundary
                           or at-good[name]["started_monotonic"] > 5
                           or attempted.get(name, {}).get("read_id") != good[name]["read_id"] for name in names):
                        continue
                    order = good["order:"+oid]["data"]
                    if (order["order_id"] != oid or order["status"] not in {"CANCELED", "CANCELLED"}
                            or D(str(order["size_matched"])) != 0
                            or not order.get("associate_trades_present") or order["associate_trades"]):
                        continue
                    if any(o["order_id"] == oid for o in good["open_orders"]["data"]):
                        continue
                    if any(leg["order_id"] == oid for t in good["trades"]["data"] for leg in t["maker_legs"]):
                        continue
                    gap.update({"narrow_zero_fill_proof_available_sec_before_actual_post":
                                posts[gap["to_window"]]["started_monotonic"]-at,
                                "narrow_proof_snapshot_version": snap["version"],
                                "narrow_proof_monotonic": at,
                                "narrow_proof_read_ids": {name: good[name]["read_id"] for name in names}})
                    episode = next(e for e in episodes if e["opening_window"] == gap["opening_window"])
                    next_window = next(w for w in windows if w["window"] == gap["to_window"])
                    before = [p for p in points[gap["opening_window"]] if p["monotonic"] <= at+1e-5]
                    if before:
                        previous_p = max(before, key=lambda p: p["monotonic"])["p"]
                        sign = 1 if episode["side"] == "YES" else -1
                        drift = D(episode["matched_quantity_used_for_benchmarks"])*sign*(D(str(previous_p))-D(next_window["post_yes_mid"]))
                        gap.update({"narrow_proof_yes_p": previous_p, "next_post_yes_p": next_window["post_yes_mid"],
                                    "fair_mark_drift_during_remaining_wait_usdc": str(drift)})
        assert hashlib.sha256(source.read_bytes()).hexdigest() == expected[sid]["sha256"]
        evidence.append({"market": market["market"], "session_id": sid,
                         "source": str(source), "sha256": expected[sid]["sha256"],
                         "cache_snapshots": str(snapshot_path.resolve()),
                         "cache_snapshots_sha256": hashlib.sha256(snapshot_path.read_bytes()).hexdigest()})
    assert len(episodes) == 22 and len(windows) == 47 and len(gaps) == 25
    tail_only = [{"market": m["market"], "opening_window": e["opening_window"],
                  "tp_fill_elapsed": e["first_repair_fill_elapsed"],
                  "first_tail_sl_elapsed": next(st["ts"]-m["start_epoch"] for st in e["states"] if st["mode"] == "SL"),
                  "residual_yes": e["residual_yes"], "residual_no": e["residual_no"],
                  "market_execution_blocks": m["block_reason_counts"]}
                 for m in base["markets"] for e in m["episodes"]
                 if e["repair_fill_mode"] == "TP" and any(st["mode"] == "SL" for st in e["states"])]
    totals = {"episodes": 22, "sl_order_windows": 47, "replacement_gaps": 25,
              "close_reason_counts": dict(Counter(w["close_reason"] for w in windows)),
              "placement_outcomes": dict(Counter(w["outcome"] for w in windows)),
              "time_phases": {k: stats([e["time_phases"][k] for e in episodes]) for k in episodes[0]["time_phases"]},
              "benchmarks": {k: str(sum((D(e["benchmarks"][k]) for e in episodes), D(0))) for k in episodes[0]["benchmarks"]},
              "money_phases": {k: str(sum((D(e["money_phases"][k]) for e in episodes), D(0))) for k in episodes[0]["money_phases"]},
              "signal_to_first_match_sec": stats([e["signal_to_first_match_sec"] for e in episodes]),
              "post_to_first_match_sec": stats([e["first_post_to_first_match_sec"] for e in episodes]),
              "detection_to_first_match_sec": stats([e["first_match"]-e["detected"] for e in episodes]),
              "plan_age_at_post_sec": stats([w["plan_age_at_post_sec"] for w in windows]),
              "final_guard_age_ms": stats([w["guard_book_age_ms"] for w in windows]),
              "guard_price_equals_ask_minus_tick_count": sum(w["guard_price_equals_ask_minus_tick"] for w in windows),
              "http_post_sec": stats([w["http_sec"] for w in windows]),
              "replacement_gap_sec": stats([g["close_to_next_post_sec"] for g in gaps]),
              "positive_cancel_to_next_post_sec": stats([g["positive_cancel_to_next_post_sec"] for g in gaps if g["positive_cancel_to_next_post_sec"] is not None]),
              "last_page_source_in_close_frames": dict(Counter(g["last_page_source_in_accepted_close_frame"] for g in gaps)),
              "last_match_to_confirmation_sec": stats([e["last_match_to_confirmation_sec"] for e in episodes]),
              "duplicate_deletes_after_positive_cancel_ack": sum(g["delete_starts_after_positive_ack"] for g in gaps),
              "narrow_zero_fill_proof_lead_sec": stats([g["narrow_zero_fill_proof_available_sec_before_actual_post"]
                  for g in gaps if "narrow_zero_fill_proof_available_sec_before_actual_post" in g]),
              "fair_mark_drift_during_narrow_proof_to_post_wait": str(sum((D(g.get(
                  "fair_mark_drift_during_remaining_wait_usdc", "0")) for g in gaps), D(0))),
              "raw_post_mid_reconciliations": sum(w["raw_asof_post_yes_mid"] is not None
                  and abs(float(w["post_yes_mid"])-w["raw_asof_post_yes_mid"]) < 1e-8 for w in windows),
              "late_detection_over_one_sec_episodes": sum(e["time_phases"]["signal_to_detection"] > 1 for e in episodes),
              "post_first_quote_worse_episodes": sum(D(e["money_phases"]["first_quote_to_fill"]) > D(".000001") for e in episodes),
              "post_first_quote_better_episodes": sum(D(e["money_phases"]["first_quote_to_fill"]) < -D(".000001") for e in episodes),
              "unmapped_path_batches": sum(e["unmapped_path_batches"] for e in episodes)}
    (OUT / "summary.json").write_text(json.dumps({"stats": totals, "episodes": episodes, "windows": windows,
                                                 "gaps": gaps, "tail_only_sl_cases": tail_only}, indent=2))
    (OUT / "source_manifest.json").write_text(json.dumps(evidence, indent=2))
    (OUT / "price_paths.json").write_text(json.dumps(paths_export, indent=2))
    write_csv("episodes.csv", [{**{k: v for k, v in e.items() if not isinstance(v, (dict, list))},
                                **{"time_"+k: v for k, v in e["time_phases"].items()},
                                **{"pnl_"+k: v for k, v in e["benchmarks"].items()},
                                **{"difference_"+k: v for k, v in e["money_phases"].items()}} for e in episodes])
    write_csv("sl_windows.csv", [{k: v for k, v in w.items() if not isinstance(v, (dict, list))} for w in windows])
    write_csv("replacement_gaps.csv", gaps)
    print(json.dumps(totals, indent=2))


if __name__ == "__main__":
    main()
