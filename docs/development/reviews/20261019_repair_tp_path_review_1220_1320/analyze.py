"""Audit TP opportunity paths; never treat a mark or a resting quote as a fill."""

import csv
import hashlib
import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[3]
BASE = OUT.parent / "20261019_repair_last12_1220_1320"
D = Decimal


def pl(row):
    return row["data"]["payload"]


def money(value):
    return str(value)


def mark(point, qty, cost, side):
    held = D(str(point["p"])) if side == "YES" else 1-D(str(point["p"]))
    return qty*held-cost


def current(row):
    d = pl(row)
    if row["kind"] == "sm_repair_policy_state" and d.get("points"):
        return d["points"][-1]
    if row["kind"] == "sm_repair_decision":
        return d.get("observations")
    if row["kind"] == "sm_repair_execution_blocked":
        return (d.get("decision") or {}).get("observations")
    return None


def main():
    base = json.loads((BASE / "summary.json").read_text())
    expected_hashes = {s["session_id"]: s["sha256"] for s in json.loads((BASE / "source_manifest.json").read_text())}
    results, evidence, paths_export = [], [], []
    for m in base["markets"]:
        sid, start = m["session_id"], m["start_epoch"]
        source = next((ROOT / "journal").glob(f"*_{sid}.jsonl"))
        raw = source.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == expected_hashes[sid]
        rows = [{**json.loads(line), "line": i} for i, line in enumerate(raw.splitlines(), 1)]
        kinds = defaultdict(list)
        for row in rows:
            kinds[row["kind"]].append(row)
        window_results = {pl(r)["window_id"]: pl(r) for r in kinds["sm_window_result"]}
        final_intents = {pl(r)["intent"]["window_id"]: pl(r)["intent"]
                         for r in kinds["sm_obi_final_decision"]}
        clock_windows = []
        for r in kinds["sm_strategy_window"]:
            d = pl(r)
            if d["state"] == "REPAIR":
                intent = final_intents[d["window_id"]]
                clock_windows.append({"start": r["ts"],
                                      "end": window_results[d["window_id"]]["end_time"],
                                      "mono_offset": intent["post_started"]-d["elapsed_s"],
                                      "wall_offset": r["ts"]-start-d["elapsed_s"]})
        episode_points, anchor_gaps = defaultdict(list), defaultdict(int)
        # Every raw path point is already in a strategy-clock coordinate. Link
        # it to the current observation from the same synchronous observe()
        # call, then project to local observation time. Sub-ms diagnostic work
        # can perturb this projection; it is not a native venue timestamp.
        for i, row in enumerate(rows):
            if row["kind"] != "sm_repair_market_path":
                continue
            d = pl(row)
            reference = None
            for nxt in rows[i+1:i+15]:
                if nxt["ts"]-row["ts"] > .2:
                    break
                reference_point = current(nxt)
                if reference_point:
                    reference = (nxt, reference_point)
                    break
            active_clock = next((w for w in clock_windows if w["start"] <= row["ts"] <= w["end"]), None)
            if active_clock:
                # official_time/time_anchor change only in _snapshot(). Active
                # REPAIR reviews/close proof do not call _snapshot(), so the
                # POST's strategy-clock offset remains valid through that batch.
                offset, wall_offset = active_clock["mono_offset"], active_clock["wall_offset"]
                anchor_method = "ACTIVE_POST_CLOCK"
            elif reference is not None:
                ref, ref_point = reference
                offset = ref["data"]["occurred_monotonic"]-ref_point["elapsed"]
                wall_offset = ref["ts"]-start-ref_point["elapsed"]
                anchor_method = "SAME_OBSERVE_CURRENT_POINT"
            else:
                anchor_gaps[d["opening_window"]] += 1
                continue
            for point in d["points"]:
                episode_points[d["opening_window"]].append({
                    **point, "projected_monotonic": offset+point["elapsed"],
                    "wall_elapsed_projection": wall_offset+point["elapsed"],
                    "journal_line": row["line"], "recorded_wall_elapsed": row["ts"]-start,
                    "recorded_monotonic": row["data"]["occurred_monotonic"],
                    "source_kind": "RAW_PRICE_PATH",
                    "anchor_method": anchor_method,
                })
        for ep in m["episodes"]:
            if not ep["repair_fill_mode"]:
                continue
            opening, side = ep["opening_window"], ep["original_side"]
            qty = D(ep["original_quantity"])
            originals = [f for f in ep["actual_fills"] if f["side"] == side]
            cost = sum(((D(str(f["price"]))*D(str(f["size"]))).quantize(D(".000001"))
                        for f in originals), D(0))
            repair_fills = [f for f in ep["actual_fills"] if f["side"] != side]
            assert sum((D(str(f["size"])) for f in originals), D(0)) == qty
            first_fill = min(repair_fills, key=lambda f: f["first_observed_elapsed"])
            fill_row = rows[first_fill["journal_line"]-1]
            stop_mono = fill_row["data"]["occurred_monotonic"]
            # Use the literal TP/SL transition marker, not final tail inventory's
            # later forced SL. A TP completion with overshoot is still TP-filled.
            states = [r for r in kinds["sm_repair_policy_state"] if pl(r)["opening_window"] == opening]
            first_tp = next((r for r in states if pl(r)["mode"] == "TP"), None)
            first_sl = next((r for r in states if pl(r)["mode"] == "SL"
                             and r["data"]["occurred_monotonic"] < stop_mono), None)
            transitions = {}
            for name, state in (("TP", first_tp), ("SL", first_sl)):
                if state is None:
                    continue
                d, ref = pl(state), current(state)
                point = d["last_transition"]["observations"]
                if "p" not in point:
                    continue
                elapsed_gap = ref["elapsed"]-point["elapsed"]
                transitions[name] = {"point": point, "journal_line": state["line"],
                                     "detected_wall_elapsed": state["ts"]-start,
                                     "wall_elapsed_projection": state["ts"]-start-elapsed_gap,
                                     "projected_monotonic": state["data"]["occurred_monotonic"]-elapsed_gap,
                                     "mark_pnl": money(mark(point, qty, cost, side)),
                                     "reason": d["reason"]}
            points = list(episode_points[opening])
            for r in states:
                point = current(r)
                if point:
                    points.append({**point, "projected_monotonic": r["data"]["occurred_monotonic"],
                                   "wall_elapsed_projection": r["ts"]-start, "journal_line": r["line"],
                                   "source_kind": "CURRENT_POLICY_OBSERVATION"})
            window_rows = [r for r in kinds["sm_strategy_window"] if pl(r)["state"] == "REPAIR"
                           and pl(r)["window_id"] in ep["windows"]]
            order_results = {pl(r)["window_id"]: r for r in kinds["sm_window_result"]}
            intent_rows = {pl(r)["intent"]["window_id"]: r for r in kinds["sm_obi_final_decision"]}
            posts = []
            for row in window_rows:
                d, plan = pl(row), pl(row)["sizing"]["repair_plan"]
                if plan["mode"] == "TP" and row["data"]["occurred_monotonic"] > stop_mono:
                    continue
                quote = d["quotes"][0]
                assert quote["outcome"] != side
                target = min(qty, D(str(quote["size"])))*(1-cost/qty-D(str(quote["price"])))
                intent = pl(intent_rows[d["window_id"]])["intent"]
                # strategy_window.books is the final SDK guard's evidence;
                # intent.snapshot is an earlier preparation snapshot.
                held_book = d["books"][side]
                opposite_book = d["books"][quote["outcome"]]
                best_bid = D(str(opposite_book["best_bid"]))
                best_ask = D(str(opposite_book["best_ask"]))
                tick = D(str(opposite_book["tick"]))
                current_mark = qty*(D(str(held_book["best_bid"]))+D(str(held_book["best_ask"])))/2-cost
                result = pl(order_results[d["window_id"]])
                close = next(r for r in kinds["sm_obi_close_requested"] if pl(r)["window_id"] == d["window_id"])
                oid = result["placement_results"][0]["expected_order_id"]
                filled_qty = sum((D(str(f["size"])) for f in repair_fills if f["maker_order_id"] == oid), D(0))
                # Two legal maker quote alternatives are pricing benchmarks,
                # not historical orders/fills. Minimum notional and limits
                # must be reevaluated before any real submission.
                posts.append({"window": d["window_id"], "mode": plan["mode"],
                              "post_wall_elapsed": row["ts"]-start, "side": quote["outcome"],
                              "quantity": quote["size"], "price": quote["price"],
                              "plan_mark_pnl": money(qty*D(str(plan["observations"]["pnl_per_share"]))),
                              "post_guard_mark_pnl": money(current_mark),
                              "paired_pnl_at_quote": money(target),
                              "opposite_bid": money(best_bid), "opposite_ask": money(best_ask),
                              "opposite_tick": money(tick),
                              "paired_pnl_at_bid_benchmark": money(qty*(1-cost/qty-best_bid)),
                              "paired_pnl_at_ask_minus_tick_benchmark": money(qty*(1-cost/qty-(best_ask-tick))),
                              "first_tp_quote_offset_ticks_at_post": money((best_bid-D(str(quote["price"])))/tick),
                              "close_wall_elapsed": close["ts"]-start,
                              "close_reason": pl(close)["reason"], "observed_fill_qty": money(filled_qty),
                              "journal_line": row["line"], "intent_line": intent_rows[d["window_id"]]["line"]})
            tp_posts = [p for p in posts if p["mode"] == "TP"]
            start_mono = transitions.get("TP", {}).get("projected_monotonic", -float("inf"))
            points = [p for p in points if start_mono-.001 <= p["projected_monotonic"] < stop_mono]
            assert points, (m["market"], opening)
            for p in points:
                p["original_leg_mark_pnl"] = money(mark(p, qty, cost, side))
            peak = max(points, key=lambda p: D(p["original_leg_mark_pnl"]))
            sl_at = transitions.get("SL", {}).get("projected_monotonic", float("inf"))
            before_sl = [p for p in points if p["projected_monotonic"] <= sl_at+.001]
            before_sl_peak = max(before_sl, key=lambda p: D(p["original_leg_mark_pnl"])) if before_sl else None
            blocked = [r for r in kinds["sm_repair_execution_blocked"]
                       if first_tp and first_tp["ts"] <= r["ts"] <= (tp_posts[0]["post_wall_elapsed"]+start if tp_posts else first_tp["ts"])
                       and pl(r)["reason"] == "TP_VOLATILITY_UNKNOWN"]
            record = {"market": m["market"], "opening_window": opening,
                      "original_side": side, "original_quantity": money(qty), "original_cost": money(cost),
                      "original_avg_price": money(cost/qty), "path": ep["path_to_repair_fill"],
                      "first_tp": transitions.get("TP"), "first_sl": transitions.get("SL"),
                      "first_tp_post": tp_posts[0] if tp_posts else None, "tp_posts": tp_posts,
                      "all_repair_posts": posts, "first_observed_repair_fill": first_fill,
                      "peak_original_leg_mark": peak,
                      "peak_original_leg_mark_before_sl": before_sl_peak,
                      "observed_mark_points": len(points), "unmapped_path_batches": anchor_gaps[opening],
                      "path_break_reasons": sorted({b["reason"] for r in states for b in pl(r).get("path_breaks", [])}),
                      "tp_warmup_blocks_before_post": len(blocked),
                      "actual_paired_pnl": ep["paired_pnl"],
                      "residual_yes": ep["residual_yes"], "residual_no": ep["residual_no"]}
            assert peak["projected_monotonic"] < stop_mono
            results.append(record)
            for p in points:
                paths_export.append({"market": m["market"], "opening_window": opening,
                                     "first_exit": "TP" if first_tp else "SL", **p})
        assert hashlib.sha256(source.read_bytes()).hexdigest() == expected_hashes[sid]
        evidence.append({"market": m["market"], "session_id": sid,
                         "source": str(source.resolve()), "sha256": expected_hashes[sid]})
    tp = [r for r in results if r["first_tp"]]
    sl = [r for r in results if not r["first_tp"]]
    assert len(results) == 14 and len(tp) == 9 and len(sl) == 5
    actual_tp = sum((D(r["actual_paired_pnl"]) for r in tp), D(0))
    direct_sl = sum((D(r["actual_paired_pnl"]) for r in sl), D(0))
    first_targets = sum((D(r["first_tp_post"]["paired_pnl_at_quote"]) for r in tp), D(0))
    tp_to_sl = [r for r in tp if r["path"] == "HD→TP→SL"]
    peaks = sum((D(r["peak_original_leg_mark_before_sl"]["original_leg_mark_pnl"]) for r in tp_to_sl), D(0))
    sl_marks = sum((D(r["first_sl"]["mark_pnl"]) for r in tp_to_sl), D(0))
    sl_actuals = sum((D(r["actual_paired_pnl"]) for r in tp_to_sl), D(0))
    assert abs(actual_tp+direct_sl-D(base["stats"]["merge_pnl"])) < D("1e-9")
    stats = {"tp_first_episodes": len(tp), "sl_first_episodes": len(sl),
             "tp_first_actual_paired_pnl": money(actual_tp), "direct_sl_actual_paired_pnl": money(direct_sl),
             "first_tp_quote_fixed_basket_paired_pnl": money(first_targets),
             "first_tp_quote_plus_actual_direct_sl_fixed_basket": money(first_targets+direct_sl),
             "first_tp_quote_nonpositive_episodes": sum(D(r["first_tp_post"]["paired_pnl_at_quote"]) <= 0 for r in tp),
             "mean_first_tp_trigger_mark": money(sum((D(r["first_tp"]["mark_pnl"]) for r in tp), D(0))/len(tp)),
             "tp_peaks_below_configured_drawdown_allowance": sum(
                 D(r["peak_original_leg_mark_before_sl"]["original_leg_mark_pnl"]) < D(".06")*D(r["original_quantity"]) for r in tp),
             "tp_to_sl_peak_mark_sum": money(peaks), "tp_to_sl_sl_trigger_mark_sum": money(sl_marks),
             "tp_to_sl_actual_paired_pnl": money(sl_actuals),
             "tp_to_sl_peak_to_sl_trigger_mark_difference": money(peaks-sl_marks),
             "tp_to_sl_sl_trigger_mark_to_fill_pnl_difference": money(sl_marks-sl_actuals),
             "tp_first_pre_sl_peak_mark_sum": money(sum((D(r["peak_original_leg_mark_before_sl"]["original_leg_mark_pnl"]) for r in tp), D(0))),
             "tp_first_pre_fill_peak_mark_sum": money(sum((D(r["peak_original_leg_mark"]["original_leg_mark_pnl"]) for r in tp), D(0))),
             "max_unmapped_path_batches": max(r["unmapped_path_batches"] for r in results)}
    (OUT / "summary.json").write_text(json.dumps({"stats": stats, "episodes": results}, ensure_ascii=False, indent=2))
    (OUT / "source_manifest.json").write_text(json.dumps(evidence, indent=2))
    (OUT / "price_paths.json").write_text(json.dumps(paths_export, ensure_ascii=False))
    with (OUT / "episodes.csv").open("w") as stream:
        flat = []
        for r in results:
            flat.append({"market": r["market"], "opening_window": r["opening_window"], "path": r["path"],
                         "original_quantity": r["original_quantity"], "original_avg_price": r["original_avg_price"],
                         "first_tp_mark": (r["first_tp"] or {}).get("mark_pnl"),
                         "peak_pre_sl_mark": r["peak_original_leg_mark_before_sl"]["original_leg_mark_pnl"] if r["peak_original_leg_mark_before_sl"] else None,
                         "peak_pre_fill_mark": r["peak_original_leg_mark"]["original_leg_mark_pnl"],
                         "first_tp_quote_paired_pnl": (r["first_tp_post"] or {}).get("paired_pnl_at_quote"),
                         "actual_paired_pnl": r["actual_paired_pnl"],
                         "residual_yes": r["residual_yes"], "residual_no": r["residual_no"]})
        writer = csv.DictWriter(stream, fieldnames=list(flat[0]))
        writer.writeheader()
        writer.writerows(flat)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    for r in tp:
        print(r["market"], r["opening_window"].rsplit(":", 1)[1],
              "TP", r["first_tp"]["mark_pnl"],
              "peak", r["peak_original_leg_mark"]["original_leg_mark_pnl"],
              "preSLpeak", r["peak_original_leg_mark_before_sl"]["original_leg_mark_pnl"],
              "target", r["first_tp_post"]["paired_pnl_at_quote"], "actual", r["actual_paired_pnl"])


if __name__ == "__main__":
    main()
