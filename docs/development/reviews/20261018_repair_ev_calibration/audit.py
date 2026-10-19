"""Read-only source audit for REPAIR execution-model identifiability (stdlib).

Run from any directory. Never imports production code or connects to an API.
Times describe local observations unless an explicit exchange field is present.
"""
import bisect
import csv
import hashlib
import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[3]
TZ = timezone(timedelta(hours=8))


def number(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def stats(values):
    values = sorted(values)
    return ({"n": len(values), "min": values[0], "median": statistics.median(values),
             "p90": values[math.ceil(.9 * len(values)) - 1], "max": values[-1]}
            if values else {"n": 0})


def stamp(t):
    return datetime.fromtimestamp(t, TZ).isoformat()


def main():
    sources, problems = [], []

    def read(path, expected=None):
        path = Path(path).resolve()
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if expected is not None:
            assert digest == expected, f"Frozen source changed: {path.name}"
        sources.append({"path": str(path), "sha256": digest, "bytes": len(raw),
                        "prior_hash_checked": expected is not None})
        return raw.decode()

    baseline_path = OUT.parent / "20261017_2245_2345/analysis.json"
    baseline = json.loads(read(baseline_path))
    latest = {m["session_id"]: m for m in baseline["markets"]}
    expected = {s["path"]: s["sha256"] for s in baseline["provenance"]}
    # Discover only the local journal family. Other dates/locations are not implied absent.
    catalog = defaultdict(list)
    catalog_errors = []
    for path in sorted((ROOT / "journal").glob("rdp_sm_*.jsonl")):
        try:
            with path.open() as f:
                head = json.loads(next(f))
            if head["kind"] == "sm_session":
                sid = head["data"]["payload"]["session_id"]
                catalog[sid].append(path.resolve())
        except (ValueError, KeyError, StopIteration) as exc:
            catalog_errors.append({"file": path.name, "error": type(exc).__name__})
    assert all(len(v) == 1 for v in catalog.values()), "Ambiguous journal sessions"

    shadows, snapshot_cache, snapshot_rows = {}, {}, []
    for path in sorted((ROOT / "research_outputs/rdp_risk_shadow").glob("*/*/market_summary.json")):
        summary = json.loads(read(path))
        sid = summary["session_id"]
        assert sid not in shadows, "Repeated shadow session"
        shadows[sid] = summary
        records = [json.loads(line) for line in read(path.parent / "market_snapshots.jsonl").splitlines()]
        assert all(r["session_id"] == sid and r["market_slug"] == summary["market_slug"] for r in records)
        keys = [r["slot_index"] for r in records]
        if len(set(keys)) != len(keys):
            problems.append({"session_id": sid, "issue": "DUPLICATE_SNAPSHOT_SLOT"})
        valid = [r for r in records if r.get("probability_validity") == "VALID"
                 and number(r.get("p_yes")) and 0 <= r["p_yes"] <= 1]
        snapshot_rows.append({"session_id": sid, "slug": summary["market_slug"],
                              "has_local_journal": sid in catalog,
                              "expected_slots": summary["expected_snapshot_slots"],
                              "rows": len(records), "valid_p_rows": len(valid),
                              "missed_rows": sum(r.get("slot_validity") == "MISSED" for r in records),
                              "writer_errors": summary.get("writer_error_count"),
                              "dropped_events": summary.get("shadow_dropped_event_count"),
                              "writer_drained": summary.get("writer_drained")})
        # Available time, never scheduled time: delayed snapshots must not enter earlier decisions.
        records.sort(key=lambda r: r["actual_snapshot_time"])
        snapshot_cache[sid] = ([r["actual_snapshot_time"] for r in records], records)

    paths = {sid: Path(m["source"]) for sid, m in latest.items()}
    paths.update({sid: catalog[sid][0] for sid in shadows if sid in catalog})
    assert not (set(latest) & set(shadows)), "Revisit cohort labels if overlap changes"
    order_rows, fill_rows, session_rows, request_rows, attempt_rows, accounting_rows = [], [], [], [], [], []
    trade_events, official_trade_rows, trade_states = Counter(), Counter(), Counter()
    cancel_request_rows = []
    duplicate_status_observations = 0
    for sid, path in sorted(paths.items()):
        cohort = "fixed_normal_20261017" if sid in latest else "older_shadow_joined"
        raw = read(path, expected.get(str(path)))
        records = [(i, json.loads(line)) for i, line in enumerate(raw.splitlines(), 1)]
        by = defaultdict(list)
        for line, row in records:
            payload = row["data"].get("payload", row["data"])
            by[row["kind"]].append((line, row["ts"], payload))
        session = by["sm_session"][0][2]
        assert session["session_id"] == sid
        slug = session["slug"]
        strategies = {q["window_id"]: q for _, _, q in by["sm_strategy_window"]}
        intents = {q["intent"]["window_id"]: (line, q["intent"])
                   for line, _, q in by["sm_obi_final_decision"]}
        net = by["sm_network_request"]
        offset = (statistics.median(q["completed_wall"] - q["completed_monotonic"]
                                    for _, _, q in net) if net else None)
        for line, _, q in net:
            if q["method"] in ("POST", "DELETE"):
                request_rows.append({"session_id": sid, "cohort": cohort, "method": q["method"],
                                     "endpoint_family": "/orders", "outcome": q["outcome"],
                                     "duration_s": q["duration_ms"] / 1000, "source_line": line})

        observations = defaultdict(dict)
        for line, t, q in by["sm_trade"]:
            key = (q["trade_id"], q["maker_order_id"])
            trade_events["events"] += 1
            trade_events["positive_native_ts"] += int(number(q.get("ts")) and q["ts"] > 0)
            if q["status"] in observations[key]:
                old = observations[key][q["status"]][2]
                assert (old["size"], old["price"]) == (q["size"], q["price"]), "Conflicting fill economics"
                duplicate_status_observations += 1
            else:
                observations[key][q["status"]] = (line, t, q)
        official_by_key = defaultdict(list)
        terminal_observations = defaultdict(list)
        for line, t, q in by["sm_official_reconciliation"]:
            order = q.get("order")
            if isinstance(order, dict) and order.get("status") in ("CANCELED", "CANCELLED", "MATCHED", "FILLED"):
                terminal_observations[q["order_id"]].append((line, t, order["status"]))
            for trade in q.get("trades", []):
                official_trade_rows["observations"] += 1
                official_trade_rows["positive_match_time"] += int(number(trade.get("match_time")) and trade["match_time"] > 0)
                official_trade_rows["positive_timestamp"] += int(number(trade.get("timestamp")) and trade["timestamp"] > 0)
                for leg in trade.get("maker_orders", []):
                    official_by_key[(trade["id"], leg["order_id"])].append((line, trade))
        confirmed = {k: v["CONFIRMED"] for k, v in observations.items() if "CONFIRMED" in v}
        trade_states.update("|".join(sorted(v)) for v in observations.values())
        for line, _, q in by["sm_cancel_ack"]:
            if number(q.get("duration_s")):
                cancel_request_rows.append({"session_id": sid, "cohort": cohort,
                                            "duration_s": q["duration_s"], "source_line": line})
        result = by["sm_run_result"]
        if result:
            assert len(confirmed) == result[-1][2]["confirmed_fills"]

        placed = {}
        for line, t, q in by["sm_placement_result"]:
            oid = q.get("order_id") or q.get("expected_order_id")
            wid = q["quote"]["window_id"]
            phase = intents[wid][1]["phase"] if wid in intents else strategies.get(wid, {}).get("state", "UNKNOWN")
            raw_responses = q.get("raw_response") or []
            if isinstance(raw_responses, dict):
                raw_responses = [raw_responses]
            attempt_rows.append({"session_id": sid, "cohort": cohort, "window_id": wid,
                                 "phase": phase, "outcome": q["outcome"],
                                 "post_started": bool(q.get("post_started")), "has_order_id": oid is not None,
                                 "raw_batch_live_entries": sum(isinstance(r, dict) and bool(r.get("orderID"))
                                                               for r in raw_responses),
                                 "raw_batch_error_entries": sum(isinstance(r, dict) and bool(r.get("errorMsg"))
                                                                for r in raw_responses), "source_line": line})
            if oid is None:
                if q["outcome"] != "REJECTED":
                    problems.append({"session_id": sid, "issue": "UNRESOLVED_PLACEMENT_WITHOUT_ORDER_ID", "line": line})
                continue
            assert oid not in placed, "Repeated placement result needs explicit resolution"
            placed[oid] = (line, t, q)
        intent_times = {q["attempt_id"]: (line, t) for line, t, q in by["sm_placement_intent"]}
        local_confirmed_rows = []
        for oid, (line, ack_t, q) in placed.items():
            quote = q["quote"]
            wid = quote["window_id"]
            phase = strategies.get(wid, {}).get("state", "UNKNOWN")
            post_start, start_line, time_basis = None, None, "UNKNOWN"
            final_book = False
            if wid in intents:
                iline, intent = intents[wid]
                phase = intent["phase"]
                if number(intent.get("post_started")) and offset is not None:
                    post_start, start_line = intent["post_started"] + offset, iline
                    time_basis = "LOCAL_POST_STARTED_MONOTONIC_ANCHORED"
                snapshot = intent.get("final_snapshot") or intent.get("snapshot") or {}
                final_book = all(isinstance(snapshot.get(side), dict) and
                                 snapshot[side].get("best_bid") is not None and
                                 snapshot[side].get("best_ask") is not None for side in ("yes", "no"))
            elif q.get("attempt_id") in intent_times:
                start_line, post_start = intent_times[q["attempt_id"]]
                time_basis = "LOCAL_PLACEMENT_INTENT_LOWER_BOUND_ONLY"
            cancels = [(cl, cq["request_ts"], ct) for cl, ct, cq in by["sm_cancel_ack"]
                       if oid in cq.get("ids", []) and number(cq.get("request_ts"))]
            first_cancel = min((ct for _, ct, _ in cancels), default=None)
            fs = [(key, v) for key, v in confirmed.items() if key[1] == oid]
            matched = [(observations[k]["MATCHED"][1], observations[k]["MATCHED"][0])
                       for k, _ in fs if "MATCHED" in observations[k]]
            first_match = min((t for t, _ in matched), default=None)
            quantity = sum(v[2]["size"] for _, v in fs)
            assert quantity <= quote["size"] + 1e-7, "Confirmed quantity exceeds original order"
            row = {"session_id": sid, "cohort": cohort, "slug": slug, "window_id": wid,
                   "order_id": oid, "phase": phase, "outcome": q["outcome"],
                   "post_started": bool(q.get("post_started")),
                   "side": quote["outcome"], "price": quote["price"], "size": quote["size"],
                   "confirmed_qty": quantity, "post_start_or_lower_bound": post_start,
                   "start_time_basis": time_basis, "placement_observed": ack_t,
                   "first_cancel_request": first_cancel, "first_match_observed": first_match,
                   "cancel_request_age_s": first_cancel - post_start if first_cancel and post_start else None,
                   "first_match_observed_age_s": first_match - post_start if first_match and post_start else None,
                   "has_final_decision_book": final_book, "has_shadow": sid in shadows,
                   "terminal_observed": min((t for _, t, _ in terminal_observations[oid]), default=None),
                   "source": str(path.resolve()), "placement_line": line, "start_line": start_line}
            if row["cancel_request_age_s"] is not None:
                assert row["cancel_request_age_s"] >= -.001
            order_rows.append(row)
            for key, (fl, confirm_t, f) in fs:
                observed = observations[key].get("MATCHED")
                mt = observed[1] if observed else None
                asof = None
                if sid in snapshot_cache and mt is not None:
                    times, snapshots = snapshot_cache[sid]
                    ix = bisect.bisect_right(times, mt) - 1
                    if ix >= 0:
                        asof = snapshots[ix]
                        assert asof["actual_snapshot_time"] <= mt
                native = [trade.get("match_time") for _, trade in official_by_key[key]
                          if number(trade.get("match_time")) and trade["match_time"] > 0]
                fr = {"session_id": sid, "cohort": cohort, "order_id": oid, "trade_id": key[0],
                      "phase": phase, "side": quote["outcome"], "price": f["price"], "quantity": f["size"],
                      "native_match_time": min(native) if native else None,
                      "matched_observed": mt, "confirmed_observed": confirm_t,
                      "conservative_match_lower_bound": post_start,
                      "conservative_match_upper_bound": mt if mt is not None else confirm_t,
                      "observed_after_cancel_request": mt > first_cancel if mt is not None and first_cancel else None,
                      "asof_snapshot_age_s": mt - asof["actual_snapshot_time"] if asof else None,
                      "asof_probability_validity": asof.get("probability_validity") if asof else None,
                      "asof_p_yes": asof.get("p_yes") if asof else None,
                      "source_line": fl, "matched_line": observed[0] if observed else None}
                assert fr["conservative_match_lower_bound"] is None or fr["conservative_match_upper_bound"] >= fr["conservative_match_lower_bound"]
                fill_rows.append(fr)
                local_confirmed_rows.append(fr)
        assert {k for k in confirmed if k[1] in placed} == set(confirmed), "Orphan confirmed trade"
        amounts = defaultdict(float)
        for fill in local_confirmed_rows:
            assert fill["phase"] in ("NORMAL", "REBALANCE")
            amounts[fill["phase"] + "_" + fill["side"]] += fill["quantity"]
            amounts[fill["phase"] + "_COST"] += fill["quantity"] * fill["price"]
        ny, nn, nc = (amounts["NORMAL_" + k] for k in ("YES", "NO", "COST"))
        ry, rn, rc = (amounts["REBALANCE_" + k] for k in ("YES", "NO", "COST"))
        terminal = result[-1][2].get("sizing_report", {}).get("final_winning_outcome") if result else None
        accounting_rows.append({"session_id": sid, "cohort": cohort, "slug": slug,
                                "normal_yes": ny, "normal_no": nn, "normal_cost": nc,
                                "repair_yes": ry, "repair_no": rn, "repair_cost": rc,
                                "total_pnl_if_yes": ny + ry - nc - rc,
                                "total_pnl_if_no": nn + rn - nc - rc,
                                "normal_only_pnl_if_yes": ny - nc, "normal_only_pnl_if_no": nn - nc,
                                "repair_increment_if_yes": ry - rc, "repair_increment_if_no": rn - rc,
                                "terminal_pnl_floor": min(ny + ry, nn + rn) - nc - rc,
                                "residual_qty": abs(ny + ry - nn - rn),
                                "recorded_final_winner": terminal or "MISSING"})
        epoch = int(slug.rsplit("-", 1)[1])
        session_rows.append({"session_id": sid, "cohort": cohort, "slug": slug,
                             "market_start_shanghai": stamp(epoch), "orders": len(placed),
                             "confirmed_fills": len(confirmed), "has_shadow": sid in shadows,
                             "has_run_result": bool(result),
                             "has_capital_limit_field": "test_capital_limit_usdc" in session})

    def cohort_summary(label):
        orders = [r for r in order_rows if r["cohort"] == label]
        repairs = [r for r in orders if r["phase"] == "REBALANCE"]
        fills = [r for r in fill_rows if r["cohort"] == label]
        sessions = [r for r in session_rows if r["cohort"] == label]
        attempts = [r for r in attempt_rows if r["cohort"] == label]
        accounting = [r for r in accounting_rows if r["cohort"] == label]
        return {"sessions": len(sessions), "market_start_min": min(r["market_start_shanghai"] for r in sessions),
                "market_start_max": max(r["market_start_shanghai"] for r in sessions),
                "orders": len(orders), "orders_by_phase": dict(Counter(r["phase"] for r in orders)),
                "placement_outcomes": dict(Counter(r["outcome"] for r in orders)),
                "placement_results_including_no_id": len(attempts),
                "placement_results_by_outcome": dict(Counter(r["outcome"] for r in attempts)),
                "submitted_placement_results": sum(r["post_started"] for r in attempts),
                "submitted_repair_placement_results": sum(r["post_started"] and r["phase"] == "REBALANCE" for r in attempts),
                "repair_submitted_with_cancel_request": sum(r["post_started"] and r["first_cancel_request"] is not None for r in repairs),
                "repair_submitted_without_cancel_observation": sum(r["post_started"] and r["first_cancel_request"] is None for r in repairs),
                "rejected_placements_without_id": sum(not r["has_order_id"] for r in attempts),
                "no_id_rejections_with_mixed_batch_response": sum(not r["has_order_id"] and r["raw_batch_live_entries"] > 0 and r["raw_batch_error_entries"] > 0 for r in attempts),
                "confirmed_fills": len(fills), "confirmed_repair_fills": sum(r["phase"] == "REBALANCE" for r in fills),
                "repair_order_sizes": sorted(set(r["size"] for r in repairs)),
                "repair_orders_with_confirmed_fill": sum(r["confirmed_qty"] > 0 for r in repairs),
                "repair_cancel_request_age_s": stats([r["cancel_request_age_s"] for r in repairs if r["cancel_request_age_s"] is not None]),
                "repair_cancel_after_20s_count": sum((r["cancel_request_age_s"] or 0) >= 20 for r in repairs),
                "repair_orders_without_cancel_observation": sum(r["first_cancel_request"] is None for r in repairs),
                "repair_orders_with_decision_book": sum(r["has_final_decision_book"] for r in repairs),
                "repair_match_observed_age_s": stats([r["first_match_observed_age_s"] for r in repairs if r["first_match_observed_age_s"] is not None]),
                "positive_exchange_match_time_fills": sum(r["native_match_time"] is not None for r in fills),
                "fills_observed_after_cancel_request": sum(r["observed_after_cancel_request"] is True for r in fills),
                "fills_with_valid_asof_snapshot_within_1_5s": sum(r["asof_probability_validity"] == "VALID" and
                      r["asof_snapshot_age_s"] is not None and r["asof_snapshot_age_s"] <= 1.5 for r in fills),
                "client_request_duration_s": {method: stats([r["duration_s"] for r in request_rows
                      if r["cohort"] == label and r["method"] == method and r["outcome"] == "SUCCESS"])
                      for method in ("POST", "DELETE")},
                "cancel_ack_duration_s": stats([r["duration_s"] for r in cancel_request_rows if r["cohort"] == label]),
                "terminal_accounting": {
                    "markets_with_recorded_winner": sum(r["recorded_final_winner"] in ("YES", "NO") for r in accounting),
                    "all_buy_cost_pnl_floor_usdc": sum(r["terminal_pnl_floor"] for r in accounting),
                    "residual_shares": sum(r["residual_qty"] for r in accounting),
                    "actual_pnl_outcome_bounds_usdc": [sum(min(r["total_pnl_if_yes"], r["total_pnl_if_no"]) for r in accounting),
                                                       sum(max(r["total_pnl_if_yes"], r["total_pnl_if_no"]) for r in accounting)],
                    "repair_increment_outcome_bounds_usdc": [sum(min(r["repair_increment_if_yes"], r["repair_increment_if_no"]) for r in accounting),
                                                             sum(max(r["repair_increment_if_yes"], r["repair_increment_if_no"]) for r in accounting)],
                    "max_normal_cost_per_market_usdc": max(r["normal_cost"] for r in accounting)}}

    evidence = {"status": "SOURCE_AUDIT_NOT_PARAMETER_CALIBRATION", "timezone": "Asia/Shanghai",
                "scope": "Frozen 12-market 2026-10-17 cohort plus local journals joined to all 47 existing shadow sessions",
                "local_catalog_sessions": len(catalog), "catalog_errors": catalog_errors,
                "shadow": {"sessions": len(shadows), "local_journal_matches": sum(sid in catalog for sid in shadows),
                           "fixed_normal_cohort_matches": len(set(latest) & set(shadows)),
                           "snapshot_rows": sum(r["rows"] for r in snapshot_rows),
                           "expected_slots": sum(r["expected_slots"] for r in snapshot_rows),
                           "valid_probability_rows": sum(r["valid_p_rows"] for r in snapshot_rows),
                           "missed_rows": sum(r["missed_rows"] for r in snapshot_rows),
                           "reported_dropped_events": sum(r["dropped_events"] or 0 for r in snapshot_rows),
                           "reported_writer_errors": sum(r["writer_errors"] or 0 for r in snapshot_rows),
                           "undrained_writers": sum(r["writer_drained"] is not True for r in snapshot_rows)},
                "cohorts": {label: cohort_summary(label) for label in ("fixed_normal_20261017", "older_shadow_joined")},
                "trade_event_timestamps": dict(trade_events), "official_trade_timestamps": dict(official_trade_rows),
                "trade_identity_status_sets": dict(trade_states),
                "duplicate_status_observations": duplicate_status_observations,
                "issues": problems, "sources": sources,
                "limitations": ["No market_events depth/queue reconstruction in this audit.",
                                "As-of snapshot is a local observation proxy, never exchange fill-time fair value.",
                                "Cancel request age is neither accepted queue age nor physical cancel effectiveness.",
                                "No long-rest counterfactual fill labels, causal policy comparison, fitted model or profitability claim.",
                                "Older strategy/size/schema differs; do not pool into fixed-NORMAL EV."]}
    assert not problems, problems
    for name, rows in [("orders", order_rows), ("fills", fill_rows), ("sessions", session_rows),
                       ("snapshots", snapshot_rows), ("requests", request_rows), ("attempts", attempt_rows),
                       ("cancel_requests", cancel_request_rows), ("terminal_accounting", accounting_rows)]:
        if rows:
            with (OUT / f"{name}.csv").open("w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
    (OUT / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in evidence.items() if k not in ("sources", "limitations")}, ensure_ascii=False, indent=2))
    return evidence


if __name__ == "__main__":
    main()
