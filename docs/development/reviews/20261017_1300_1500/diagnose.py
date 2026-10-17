"""Additional read-only timing evidence. Run after analyze.py from repo root."""
import hashlib
import json
import statistics
from collections import Counter
from pathlib import Path

from analyze import OUT, stamp, write_csv


def diagnose():
    data = json.loads((OUT / "analysis.json").read_text())
    cycles, changes, initial, account_flows, merges = [], [], [], [], []
    previous = None
    for m in data["markets"]:
        rows = [(i, json.loads(s)) for i, s in enumerate(Path(m["source"]).read_text().splitlines(), 1)]
        net = sorted([(i, r["data"]["payload"]) for i, r in rows if r["kind"] == "sm_network_request"],
                     key=lambda p: p[1]["started_monotonic"])
        offset = statistics.median(q["completed_wall"] - q["completed_monotonic"] for _, q in net)
        barriers = [(i, r["data"]["occurred_monotonic"], r["data"]["payload"])
                    for i, r in rows if r["kind"] == "sm_close_barrier"]
        barrier_times = sorted(b for _, b, _ in barriers)
        accepted = [w for w in data["windows"] if w["market"] == m["market"]]
        canceled = [w for w in accepted if "Already canceled" in w["raw_error"]]
        last_post = max((w["post_epoch"] for w in accepted if w["submitted"]), default=m["epoch"])
        if canceled:
            first_cancel_epoch = min(w["post_epoch"] for w in canceled)
            for line, barrier_mono, q in barriers:
                if q["result"] != "SAFE_TO_CONTINUE" or barrier_mono + offset < first_cancel_epoch:
                    continue
                # The cancellation rejection proof reads open orders before all per-ID reads.
                before = [(i, p) for i, p in net if p["endpoint"] == "/data/orders"
                          and p["completed_monotonic"] <= barrier_mono]
                after_balance = [(i, p) for i, p in net if p["endpoint"] == "/balance-allowance"
                                 and 0 <= p["started_monotonic"] - barrier_mono < 3]
                if not before or not after_balance:
                    continue
                first_line, first = before[-1]
                balance_line, balance = after_balance[0]
                next_barrier = next((b for b in barrier_times if b > barrier_mono), float("inf"))
                if balance["started_monotonic"] >= next_barrier:
                    continue
                final = next(((i, p) for i, p in net if p["endpoint"] == "/data/orders"
                              and balance["completed_monotonic"] <= p["started_monotonic"] < next_barrier
                              and p["started_monotonic"] - balance["completed_monotonic"] < 3), None)
                if final is None:
                    continue
                final_line, final = final
                duration = final["completed_monotonic"] - first["started_monotonic"]
                cycles.append(dict(market=m["market"], proof_start=stamp(first["started_wall"]),
                                   proof_safe=stamp(barrier_mono+offset),
                                   account_orders_end=stamp(final["completed_wall"]),
                                   snapshot_lower_bound_s=duration, exceeds_10s=duration > 10,
                                   after_last_post=first["started_wall"] > last_post,
                                   known_orders=len(q.get("proofs", {})), source=m["source"],
                                   first_line=first_line, safe_line=line, final_line=final_line))
        signal_candidates = []
        close_monos = {r["data"]["payload"]["window_id"]: r["data"]["payload"]["requested_monotonic"]
                       for _, r in rows if r["kind"] == "sm_obi_close_requested"}
        for line, r in rows:
            q = r["data"]["payload"]
            if r["kind"] == "sm_obi_signal_changes":
                for change in q["changes"]:
                    changes.append(dict(market=m["market"], window=q["window_id"].rsplit(":", 1)[-1],
                                        time=stamp(change[1]+offset), epoch=change[1]+offset,
                                        label=change[3], before_close_request=change[1] <= close_monos[q["window_id"]],
                                        snapshot_id=change[2], source=m["source"], source_line=line))
            if r["kind"] in {"sm_obi_wait", "sm_obi_final_decision"}:
                intent = q.get("intent", q)
                signal = intent.get("signal") or {}
                if signal.get("label") in {"VALID_YES", "VALID_NO"}:
                    event_epoch = signal["event_mono"] + offset
                    signal_candidates.append(dict(time=stamp(event_epoch), epoch=event_epoch,
                                                  recorded_time=stamp(r["ts"]),
                                                  signal=signal["label"], obi=signal.get("imbalance"),
                                                  kind=r["kind"], source_line=line))
            if r["kind"] == "sm_merge_result":
                merges.append(dict(market=m["market"], time=stamp(r["ts"]), line=line,
                                   source=m["source"], status=q["status"],
                                   pairs=int(q["amount_raw"])/1e6,
                                   official_balance_after=int(q["collateral_after_raw"])/1e6,
                                   fields=list(q)))
        initial.append(dict(market=m["market"], first=min(signal_candidates, key=lambda s:s["epoch"])
                            if signal_candidates else None))
        if previous is not None:
            other = m["starting_balance"] - (previous["starting_balance"]+previous["scoped_cash_flow"])
            account_flows.append(dict(previous=previous["market"], next=m["market"],
                                      unexplained_balance_delta=round(other, 6)))
        previous = m
    write_csv("snapshot_cycles.csv", cycles)
    write_csv("signal_changes.csv", sorted(changes, key=lambda c:c["epoch"]))
    write_csv("balance_gaps.csv", account_flows)
    sent = [w for w in data["windows"] if w["submitted"]]
    repair = [w for w in sent if w["phase"] == "REBALANCE"]
    unfilled_repair = [w for w in repair if w["fill_quantity"] == 0]
    ack_unfilled_repair = [w for w in unfilled_repair if w["outcome_final"] == "ACK"]
    evidence = dict(
        snapshot_cycle_count=len(cycles),
        snapshot_over_10s_by_market=dict(Counter(c["market"] for c in cycles if c["exceeds_10s"] and c["after_last_post"])),
        snapshot_examples=[next((c for c in cycles if c["market"] == market and c["after_last_post"] and c["exceeds_10s"]), None)
                           for market in ("13:10", "13:55", "14:55")],
        canceled_submission_cases=[dict(market=w["market"], window=w["window"], time=w["post_time"],
                                        outcome=w["outcome_final"], error=w["raw_error"])
                                    for w in sent if "Already canceled" in w["raw_error"]],
        first_recorded_valid_signals=initial,
        residual_markets=[dict(market=m["market"], side="YES" if m["residual_yes"] else "NO",
                              shares=m["residual_yes"]+m["residual_no"], cost=m["residual_cost"])
                          for m in data["markets"] if m["residual_yes"]+m["residual_no"]],
        unfilled_repair_close_reasons=dict(Counter(w["close_reason"] for w in unfilled_repair)),
        unfilled_repair_median_post_to_close_s=statistics.median(w["post_to_close_s"] for w in unfilled_repair),
        unfilled_repair_median_close_to_safe_s=statistics.median(w["close_to_safe_s"] for w in unfilled_repair),
        repair_close_by_outcome={outcome:dict(Counter(w["close_reason"] for w in repair if w["outcome_final"] == outcome))
                                 for outcome in ("ACK", "REJECTED")},
        ack_unfilled_repair_count=len(ack_unfilled_repair),
        ack_unfilled_repair_median_post_to_close_s=statistics.median(w["post_to_close_s"] for w in ack_unfilled_repair),
        ack_unfilled_repair_median_close_to_safe_s=statistics.median(w["close_to_safe_s"] for w in ack_unfilled_repair),
        filled_normal_close_reasons=dict(Counter(w["close_reason"] for w in sent if w["phase"]=="NORMAL" and w["fill_quantity"])),
        max_writer_queue=max(m["max_writer_queue"] for m in data["markets"]),
        max_dropped_diagnostics=max(m["dropped_diagnostics"] for m in data["markets"]),
        unexplained_balance_deltas=[f for f in account_flows if abs(f["unexplained_balance_delta"])>1e-5],
        merges=merges,
    )
    assert all(m["execution_status"] == "COMPLETED" and m["final_barrier"] == "SAFE_TO_CONTINUE" for m in data["markets"])
    assert all(hashlib.sha256(Path(p["path"]).read_bytes()).hexdigest()==p["sha256"] for p in data["provenance"])
    (OUT/"evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
    print(json.dumps({k:v for k,v in evidence.items() if k not in {"merges", "first_recorded_valid_signals"}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    diagnose()
