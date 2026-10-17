"""Inspectable read-only follow-up to the rejection-refresh fix."""
import hashlib
import json
import statistics
from collections import Counter
from pathlib import Path

from analyze import OUT, stamp, write_csv


def study():
    data = json.loads((OUT / "analysis.json").read_text())
    initial, changes, merges, barriers, errors, cash_gaps = [], [], [], [], [], []
    previous = None
    for m in data["markets"]:
        raw = Path(m["source"]).read_bytes()
        rows = [(i, json.loads(s)) for i, s in enumerate(raw.decode().splitlines(), 1)]
        net = [r["data"]["payload"] for _, r in rows if r["kind"] == "sm_network_request"]
        offset = statistics.median(q["completed_wall"]-q["completed_monotonic"] for q in net)
        close = {r["data"]["payload"]["window_id"]: r["data"]["payload"]["requested_monotonic"]
                 for _, r in rows if r["kind"] == "sm_obi_close_requested"}
        candidates = []
        for line, r in rows:
            q = r["data"]["payload"]
            if r["kind"] == "sm_obi_signal_changes":
                for change in q["changes"]:
                    changes.append(dict(market=m["market"], window=q["window_id"].rsplit(":",1)[-1],
                                        time=stamp(change[1]+offset), epoch=change[1]+offset,
                                        signal=change[3], before_close=change[1]<=close[q["window_id"]],
                                        source=m["source"], source_line=line))
            if r["kind"] in {"sm_obi_wait", "sm_obi_final_decision"}:
                q1 = q.get("intent", q)
                signal = q1.get("signal") or {}
                if signal.get("label") in {"VALID_YES", "VALID_NO"}:
                    event_epoch = signal["event_mono"]+offset
                    candidates.append(dict(time=stamp(event_epoch), epoch=event_epoch,
                                           signal=signal["label"], obi=signal["imbalance"],
                                           source_line=line, recorded_time=stamp(r["ts"])))
            if r["kind"] == "sm_merge_result" and q["status"] == "CONFIRMED":
                merges.append(dict(market=m["market"], time=stamp(r["ts"]), epoch=r["ts"],
                                   pairs=int(q["amount_raw"])/1e6, released_cost=q["released_cost_usdc"],
                                   balance_before=int(q["collateral_before_raw"])/1e6,
                                   balance_after=int(q["collateral_after_raw"])/1e6,
                                   source=m["source"], source_line=line))
            if r["kind"] == "sm_close_barrier":
                barriers.append(dict(market=m["market"], time=stamp(r["ts"]), epoch=r["ts"],
                                     result=q["result"], reason=q.get("reason"),
                                     source=m["source"], source_line=line))
            if r["kind"] == "sm_network_request" and q["outcome"] != "SUCCESS":
                errors.append(dict(market=m["market"], time=stamp(q["completed_wall"]),
                                   source=m["source"], source_line=line, **q))
        initial.append(dict(market=m["market"], first=min(candidates,key=lambda z:z["epoch"]) if candidates else None))
        if previous:
            gap=m["starting_balance"]-previous["starting_balance"]-previous["scoped_cash_flow"]
            cash_gaps.append(dict(previous=previous["market"], next=m["market"], unassigned_cash_flow=round(gap,8)))
        previous = m
    sent = [w for w in data["windows"] if w["submitted"]]
    repairs = [w for w in sent if w["phase"]=="REBALANCE"]
    unfilled = [w for w in repairs if w["outcome_final"]=="ACK" and not w["fill_quantity"]]
    old = json.loads((OUT.parent / "20261017_1300_1500" / "analysis.json").read_text())
    latest_balance = data["markets"][-1]["starting_balance"]+data["markets"][-1]["scoped_cash_flow"]
    evidence = dict(
        scope=dict(start="2026-10-17T15:40:00+08:00", end="2026-10-17T16:40:00+08:00", frozen_markets=12),
        first_recorded_valid_signals=initial,
        already_canceled=[dict(market=w["market"], window=w["window"], error=w["raw_error"]) for w in sent if "Already canceled" in w["raw_error"]],
        rejected=[dict(market=w["market"], window=w["window"], time=w["post_time"], reason=w["raw_error"]) for w in sent if w["outcome_final"]=="REJECTED"],
        repair_close_outcome={outcome:dict(Counter(w["close_reason"] for w in repairs if w["outcome_final"]==outcome)) for outcome in ("ACK","REJECTED")},
        repair_median_post_to_close_s=statistics.median(w["post_to_close_s"] for w in repairs),
        repair_median_close_to_safe_s=statistics.median(w["close_to_safe_s"] for w in repairs),
        repair_max_close_to_safe_s=max(w["close_to_safe_s"] for w in repairs),
        unfilled_ack_repair_count=len(unfilled),
        unfilled_ack_repair_median_post_to_close_s=statistics.median(w["post_to_close_s"] for w in unfilled),
        filled_normal_close_reasons=dict(Counter(w["close_reason"] for w in sent if w["phase"]=="NORMAL" and w["fill_quantity"])),
        starting_balance=data["markets"][0]["starting_balance"], latest_observed_balance=latest_balance,
        observed_balance_change=round(latest_balance-data["markets"][0]["starting_balance"],8),
        unexplained_cash_flow=round(latest_balance-data["markets"][0]["starting_balance"]-data["totals"]["scoped_cash_flow"],8),
        cash_gaps=[g for g in cash_gaps if abs(g["unassigned_cash_flow"])>1e-6], merges=merges,
        non_success_requests=errors,
        comparison={k:dict(before=old["totals"][k], after=data["totals"][k]) for k in ("markets","submitted","traded_markets","residual_markets","pair_pnl","residual_cost","phase_fill_rates")},
        max_writer_queue=max(m["max_writer_queue"] for m in data["markets"]),
        diagnostics_dropped=max(m["dropped_diagnostics"] for m in data["markets"]),
        confirmation_lag_median_s=statistics.median(f["confirmation_lag_s"] for f in data["fills"]),
        confirmation_lag_max_s=max(f["confirmation_lag_s"] for f in data["fills"]),
    )
    assert all(hashlib.sha256(Path(p["path"]).read_bytes()).hexdigest()==p["sha256"] for p in data["provenance"])
    assert all(m["final_barrier"]=="SAFE_TO_CONTINUE" for m in data["markets"])
    write_csv("signal_changes.csv", sorted(changes,key=lambda z:z["epoch"]))
    write_csv("close_barriers.csv", sorted(barriers,key=lambda z:z["epoch"]))
    write_csv("merges.csv", sorted(merges,key=lambda z:z["epoch"]))
    write_csv("balance_gaps.csv", cash_gaps)
    (OUT/"evidence.json").write_text(json.dumps(evidence,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in evidence.items() if k not in {"first_recorded_valid_signals","merges"}},ensure_ascii=False,indent=2))


if __name__=="__main__":
    study()
