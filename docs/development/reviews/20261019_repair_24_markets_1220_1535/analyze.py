"""Reproducible, read-only audit of the 24 sessions locked at request time."""

import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
SESSIONS = (
    "d44a1224727047c8999aa1a585a6d93c", "4c87022b5b1d4252b02ab31272f6ef82",
    "2f15361d56554ca082fa787e82ea3b9d", "bfc87b1bbff041e3b78c0c6ebd4392cf",
    "579f1d40cf5e48ffb49ebeca4b3a58a6", "71123a66c78447fe962e2deefde545b5",
    "1e3e434c46a94be9b41d20a21b8d4c3b", "784dcfa6b2d94ef18a6d5c27a4548d45",
    "5c7ceb7eb5a147fab276e7717c0048fd", "4e772fcf9ec64522a49f61c83d449c2a",
    "a22554bc79bc4a3982fe13d2699b7715", "33e45756eb9642f49e31160c1f224f32",
)
SESSIONS += ('2693008f1f6044c4848880c61a122347', 'a94a6a2279f24e7495971b3c5321301a', 'e866262881f24758b52b81c983ee1878', 'e5fa972920f64912a6f91fd263885321', '46b1f02594a34f3ca3b25f9ea17c485a', 'f35091eced5341f2b96b34810eb7472c', '0a4621fb630741c99e334a7fed1bf436', 'cd8e93e921f24b8b9f31629fa55b46e7', '908e3945b31640cdb4d5cd809b9653ae', '5d985467ded645459f437550f22bcb6d', '48661340d6dc458b843b769d1b412195', '917e65f87a5348e496a9e69e26355f47')
D = Decimal
MICRO = D(".000001")


def payload(row):
    return row["data"]["payload"]


def cost(fill):
    # The raw maker price can contain a ~1e-16 representation remainder.
    # USDC amounts have six decimal places; preserve raw fields separately.
    return (D(str(fill["price"]))*D(str(fill["size"]))).quantize(MICRO)


def write_csv(name, rows):
    if not rows:
        return
    with (OUT/name).open("w") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def drawdown(pnls):
    value = peak = maximum = D(0)
    peak_index = -1
    result = {"amount": "0", "peak_index": -1, "trough_index": -1}
    curve = []
    for i, pnl in enumerate(pnls):
        value += D(str(pnl))
        if value > peak:
            peak, peak_index = value, i
        loss = peak-value
        if loss > maximum:
            maximum = loss
            result = {"amount": str(loss), "peak_index": peak_index, "trough_index": i}
        curve.append({"cumulative": str(value), "drawdown": str(loss), "peak": str(peak)})
    return result, curve


def main():
    markets, episodes, orders, timeline, evidence = [], [], [], [], []
    resolution_path = OUT / "clob_resolution_queries.json"
    resolutions = {q["slug"]: q for q in json.loads(resolution_path.read_text())}
    for sid in SESSIONS:
        source = next((ROOT/"journal").glob(f"*_{sid}.jsonl"))
        raw = source.read_bytes()
        rows = [json.loads(line) for line in raw.splitlines()]
        by_kind = defaultdict(list)
        for line, row in enumerate(rows, 1):
            by_kind[row["kind"]].append((line, row))
        session = payload(by_kind["sm_session"][0][1])
        result = payload(by_kind["sm_run_result"][0][1])
        start = int(session["slug"].rsplit("-", 1)[1])
        label = datetime.fromtimestamp(start, ZoneInfo("Asia/Shanghai")).strftime("%H:%M")
        cache_root = ROOT/"research_outputs/periodic_sync"/sid
        meta = json.loads((cache_root/"manifest.json").read_text())["market"]
        tokens = meta["tokens"]
        sides = dict(zip(tokens, ("YES", "NO"), strict=True))
        final_windows = {w["result"]["window_id"]: w for w in result["windows"]}
        placements = {payload(r)["expected_order_id"]: payload(r)
                      for _, r in by_kind["sm_placement_result"]}
        placement_times = {payload(r)["expected_order_id"]: r["ts"]
                           for _, r in by_kind["sm_placement_result"]}
        fills = {}
        groups = defaultdict(lambda: {"windows": set(), "states": [], "last": {}, "completed_at": None})
        active = None
        for line, row in enumerate(rows, 1):
            d, kind = payload(row), row["kind"]
            if kind == "sm_repair_episode":
                active = d["opening_window"]
                group = groups[active]
                group["last"] = d
                group["windows"].update(d.get("windows", [active]))
                if d["event"] == "COMPLETE_WITH_DUST_TOLERANCE":
                    group["completed_at"] = row["ts"]-start
            elif kind == "sm_strategy_window" and d["state"] == "REPAIR":
                assert active is not None
                groups[active]["windows"].add(d["window_id"])
            elif kind == "sm_repair_policy_state":
                groups[d["opening_window"]]["states"].append({"ts": row["ts"], "mode": d["mode"],
                                                             "reason": d.get("reason")})
            elif kind == "sm_trade":
                key = d["trade_id"], d["maker_order_id"]
                f = fills.setdefault(key, {**d, "side": sides[d["token_id"]],
                                          "first_observed_elapsed": row["ts"]-start,
                                          "journal_line": line, "statuses": []})
                f["status"] = d["status"]
                f["statuses"].append([row["ts"]-start, d["status"]])
            keep = kind in {"sm_placement_result", "sm_trade", "sm_repair_quote_invalidated",
                            "sm_obi_close_requested", "sm_merge_intent", "sm_merge_result"}
            keep |= kind == "sm_repair_decision" and d.get("action") not in {"HOLD", "KEEP"}
            if keep:
                timeline.append({"market": label, "session_id": sid, "journal_line": line,
                                 "elapsed_wall": row["ts"]-start, "kind": kind, "payload": d})
        assert all(f["status"] == "CONFIRMED" for f in fills.values())
        qty = {side: sum((D(str(f["size"])) for f in fills.values() if f["side"] == side), D(0))
               for side in ("YES", "NO")}
        costs = {side: sum((cost(f) for f in fills.values() if f["side"] == side), D(0))
                 for side in ("YES", "NO")}
        merged = D(str(result["merged_pairs"]))
        released = sum((costs[s]*merged/qty[s] for s in qty if qty[s]), D(0))
        realized = merged-released
        total_cost = sum(costs.values(), D(0))
        residual = {s: qty[s]-merged for s in qty}
        residual_cost = total_cost-released
        winner = None
        settlement_payout = D(0)
        if any(residual.values()):
            query = resolutions[session["slug"]]
            resolved = query["body"]
            assert query["status"] == 200 and resolved["closed"] is True
            assert resolved["condition_id"] == meta["condition_id"]
            assert resolved["market_slug"] == session["slug"]
            assert {t["token_id"] for t in resolved["tokens"]} == set(tokens)
            winners = [t for t in resolved["tokens"] if t["winner"] is True]
            assert len(winners) == 1
            winner = sides[winners[0]["token_id"]]
            settlement_payout = residual[winner]
        full_pnl = merged + settlement_payout - total_cost
        assert abs(realized-D(str(result["realized_merge_pnl"]))) < D("1e-9")
        assert abs(residual["YES"]-D(str(result["confirmed_yes"]))) < D("1e-9")
        assert abs(residual["NO"]-D(str(result["confirmed_no"]))) < D("1e-9")
        merge_cost = sum((D(payload(r)["released_cost_usdc"]) for _, r in by_kind["sm_merge_result"]
                          if payload(r)["status"] == "CONFIRMED"), D(0))
        assert abs(released-merge_cost) < D("1e-9")
        m_episodes = []
        for opening, group in groups.items():
            original = [f for f in fills.values()
                        if placements[f["maker_order_id"]]["quote"]["window_id"] == opening]
            if not original:
                continue
            own = [f for f in fills.values()
                   if placements[f["maker_order_id"]]["quote"]["window_id"] in group["windows"]]
            repair_fills = [f for f in own if final_windows[
                placements[f["maker_order_id"]]["quote"]["window_id"]]["state"] == "REPAIR"]
            fill_mode = None
            if repair_fills:
                mode_set = {final_windows[placements[f["maker_order_id"]]["quote"]["window_id"]]
                            ["sizing"]["repair_plan"]["mode"] for f in repair_fills}
                assert len(mode_set) == 1
                fill_mode = next(iter(mode_set))
                first_repair = min(f["first_observed_elapsed"] for f in repair_fills)
                earlier_modes = [s["mode"] for s in group["states"] if s["ts"] <= start+first_repair]
                path = "HD→TP" if fill_mode == "TP" else ("HD→TP→SL" if "TP" in earlier_modes else "HD→SL")
            else:
                first_repair = None
                path = "DUST_NO_REPAIR" if sum((D(str(f["size"])) for f in original), D(0)) < D(".1") else "UNFILLED_REPAIR"
            q = {s: sum((D(str(f["size"])) for f in own if f["side"] == s), D(0)) for s in qty}
            c = {s: sum((cost(f) for f in own if f["side"] == s), D(0)) for s in qty}
            pairs = min(q.values())
            pair_cost = sum((c[s]*pairs/q[s] for s in q if q[s]), D(0))
            ep_residual_cost = sum(c.values(), D(0))-pair_cost
            ep_settlement_payout = q[winner]-pairs if winner else D(0)
            ep = {"market": label, "session_id": sid, "opening_window": opening,
                  "original_side": original[0]["side"],
                  "original_quantity": str(sum((D(str(f["size"])) for f in original), D(0))),
                  "first_open_fill_elapsed": min(f["first_observed_elapsed"] for f in original),
                  "repair_fill_mode": fill_mode, "path_to_repair_fill": path,
                  "first_repair_fill_elapsed": first_repair, "completed_elapsed": group["completed_at"],
                  "paired_quantity": str(pairs), "paired_pnl": str(pairs-pair_cost),
                  "residual_cost": str(ep_residual_cost), "settlement_payout": str(ep_settlement_payout),
                  "full_pnl": str(pairs-pair_cost-ep_residual_cost+ep_settlement_payout),
                  "residual_yes": str(q["YES"]-pairs), "residual_no": str(q["NO"]-pairs),
                  "state_at_session_end": group["states"][-1]["mode"] if group["states"] else None,
                  "first_exit_mode": next((s["mode"] for s in group["states"] if s["mode"] != "HD"), None),
                  "states": group["states"],
                  "actual_fills": own, "windows": sorted(group["windows"], key=lambda w: int(w.rsplit("w", 1)[1]))}
            episodes.append(ep)
            m_episodes.append(ep)
        pool_adjustment = realized-sum((D(e["paired_pnl"]) for e in m_episodes), D(0))
        assert abs(sum((D(e["full_pnl"]) for e in m_episodes), D(0))-full_pnl) < D("1e-9")
        closes = {payload(r)["window_id"]: (line, r) for line, r in by_kind["sm_obi_close_requested"]}
        for _, row in by_kind["sm_window_result"]:
            w = payload(row)
            for a in w["placement_results"]:
                q, wid, oid = a["quote"], w["window_id"], a["expected_order_id"]
                wrapper = final_windows[wid]
                own = [f for f in fills.values() if f["maker_order_id"] == oid]
                orders.append({"market": label, "session_id": sid, "window": wid.rsplit(":", 1)[1],
                               "phase": wrapper["state"],
                               "mode": wrapper["sizing"].get("repair_plan", {}).get("mode"),
                               "side": q["outcome"], "price": q["price"], "quantity": q["size"],
                               "post_started": a["post_started"],
                               "initial_outcome": placements[oid]["outcome"], "final_outcome": a["outcome"],
                               "placement_elapsed": placement_times[oid]-start,
                               "close_reason": payload(closes[wid][1])["reason"],
                               "end_elapsed": w["end_time"]-start,
                               "actual_fill_quantity": str(sum((D(str(f["size"])) for f in own), D(0))),
                               "close_barrier": w["close_barrier"], "block_reason": w["block_reason"]})
        blockers = Counter(payload(r)["reason"] for _, r in by_kind["sm_repair_execution_blocked"])
        first_block = {}
        for line, row in by_kind["sm_repair_execution_blocked"]:
            d = payload(row)
            first_block.setdefault(d["reason"], {"journal_line": line, "elapsed_wall": row["ts"]-start})
        lower = merged-total_cost+min(residual.values())
        upper = merged-total_cost+max(residual.values())
        health = json.loads((cache_root/"summary.json").read_text())
        tolerance = sorted({payload(r)["phase_inventory"]["tolerance_shares"]
                            for _, r in by_kind["sm_obi_wait"] if payload(r).get("phase_inventory")})
        market = {"market": label, "slug": session["slug"], "session_id": sid,
                  "start_epoch": start, "end_epoch": start+300,
                  "normal_windows": result["normal_windows"], "repair_windows": result["repair_windows"],
                  "actual_fill_records": len(fills), "buy_cost": str(total_cost),
                  "merged_pairs": str(merged), "released_cost": str(released),
                  "merge_pnl": str(realized), "session_pool_cost_adjustment": str(pool_adjustment), "reported_merge_pnl": result["realized_merge_pnl"],
                  "residual_yes": str(residual["YES"]), "residual_no": str(residual["NO"]),
                  "residual_cost": str(residual_cost), "full_pnl_lower": str(lower), "full_pnl_upper": str(upper),
                  "full_pnl": str(full_pnl), "settlement_winner": winner,
                  "settlement_payout": str(settlement_payout),
                  "execution_status": result["execution_status"], "close_barrier": result["final_close_barrier"],
                  "stop_reason": result["stop_reason"], "stop_elapsed_wall": result["stop_time"]-start,
                  "blocked_windows": result["blocked_windows"], "block_reason_counts": dict(blockers),
                  "first_blocks": first_block, "observed_inventory_tolerance": tolerance,
                  "actual_fills": list(fills.values()), "episodes": m_episodes,
                  "merge_events": [{"journal_line": line, "elapsed_wall": row["ts"]-start,
                                    "payload": payload(row)} for line, row in by_kind["sm_merge_result"]],
                  "cache_health": {k: health.get(k) for k in ("failure", "observer_fatal_type", "dropped_events", "io_drained")},
                  "cache_source_failures": health["metrics"]["source_failures"],
                  "small_exposure_events": [{"elapsed_wall": r["ts"]-start, "payload": payload(r)} for _,r in by_kind["sm_repair_small_exposure"]],
                  "kind_counts": dict(Counter(r["kind"] for r in rows))}
        markets.append(market)
        evidence.append({"market": label, "session_id": sid, "source": str(source.resolve()),
                         "sha256": hashlib.sha256(raw).hexdigest(), "journal_rows": len(rows),
                         "cache_summary": str((cache_root/"summary.json").resolve()),
                         "supporting_sources": [{"source": str((cache_root/name).resolve()),
                              "sha256": hashlib.sha256((cache_root/name).read_bytes()).hexdigest()}
                             for name in ("manifest.json", "summary.json")]})
        assert source.read_bytes() == raw
    pnls = [D(m["merge_pnl"]) for m in markets]
    mdd, curve = drawdown(pnls)
    for market, point in zip(markets, curve, strict=True):
        market.update(point)
    full_mdd, full_curve = drawdown([m["full_pnl"] for m in markets])
    for market, point in zip(markets, full_curve, strict=True):
        market.update({"full_"+k: v for k, v in point.items()})
    repair_eps = [e for e in episodes if e["repair_fill_mode"]]
    groups = []
    for path in ("HD→TP", "HD→TP→SL", "HD→SL"):
        subset = [e for e in repair_eps if e["path_to_repair_fill"] == path]
        pnl = sum((D(e["paired_pnl"]) for e in subset), D(0))
        groups.append({"path": path, "episodes": len(subset), "share": len(subset)/len(repair_eps),
                       "paired_pnl": str(pnl), "mean_paired_pnl": str(pnl/len(subset)) if subset else None,
                       "full_pnl": str(sum((D(e["full_pnl"]) for e in subset), D(0))),
                       "mean_full_pnl": str(sum((D(e["full_pnl"]) for e in subset), D(0))/len(subset)) if subset else None,
                       "fully_paired": sum(abs(D(e["residual_yes"])-D(e["residual_no"])) < D(".1") for e in subset)})
    total_pnl = sum(pnls, D(0))
    stats = {"markets": len(markets), "period": "2026-10-19 12:20–13:20 + 14:35–15:35 Asia/Shanghai",
             "normal_windows": sum(m["normal_windows"] for m in markets),
             "repair_windows": sum(m["repair_windows"] for m in markets),
             "actual_fill_records": sum(m["actual_fill_records"] for m in markets),
             "repair_episodes": len(repair_eps), "dust_only_episodes": sum(e["path_to_repair_fill"] == "DUST_NO_REPAIR" for e in episodes),
             "unfilled_repair_episodes": sum(e["path_to_repair_fill"] == "UNFILLED_REPAIR" for e in episodes),
             "repair_fill_modes": dict(Counter(e["repair_fill_mode"] for e in repair_eps)),
             "paths": groups, "merge_pnl": str(total_pnl), "mean_market_merge_pnl": str(total_pnl/len(markets)),
             "mean_repair_paired_pnl": str(sum((D(e["paired_pnl"]) for e in repair_eps),D(0))/len(repair_eps)),
             "session_pool_cost_adjustment": str(sum((D(m["session_pool_cost_adjustment"]) for m in markets),D(0))),
             "winning_merge_markets": sum(p>0 for p in pnls), "losing_merge_markets": sum(p<0 for p in pnls),
             "zero_merge_markets": sum(p==0 for p in pnls),
             "merged_pairs": str(sum((D(m["merged_pairs"]) for m in markets), D(0))),
             "buy_cost": str(sum((D(m["buy_cost"]) for m in markets), D(0))),
             "residual_cost": str(sum((D(m["residual_cost"]) for m in markets), D(0))),
             "residual_markets": [m["market"] for m in markets if D(m["residual_yes"]) or D(m["residual_no"])],
             "large_residual_markets": [m["market"] for m in markets if abs(D(m["residual_yes"])-D(m["residual_no"])) >= D(".1")],
             "full_pnl_lower": str(sum((D(m["full_pnl_lower"]) for m in markets), D(0))),
             "full_pnl_upper": str(sum((D(m["full_pnl_upper"]) for m in markets), D(0))),
             "realized_merge_max_drawdown": mdd,
             "full_pnl": str(sum((D(m["full_pnl"]) for m in markets), D(0))),
             "mean_market_full_pnl": str(sum((D(m["full_pnl"]) for m in markets), D(0))/len(markets)),
             "full_pnl_max_drawdown": full_mdd,
             "settlement_payout": str(sum((D(m["settlement_payout"]) for m in markets), D(0))),
             "winning_full_markets": sum(D(m["full_pnl"]) > 0 for m in markets),
             "losing_full_markets": sum(D(m["full_pnl"]) < 0 for m in markets),
             "zero_full_markets": sum(D(m["full_pnl"]) == 0 for m in markets),
             "dust_only_full_pnl": str(sum((D(e["full_pnl"]) for e in episodes if not e["repair_fill_mode"]), D(0))),
             "settlement_evidence": {"source": str(resolution_path.resolve()),
                                     "sha256": hashlib.sha256(resolution_path.read_bytes()).hexdigest()},
             "actual_post_count": sum(o["post_started"] for o in orders),
             "initial_placement_outcomes": dict(Counter(o["initial_outcome"] for o in orders)),
             "all_orders_safely_closed": all(m["close_barrier"] == "SAFE_TO_CONTINUE" for m in markets),
             "all_inventory_flat": all(D(m["residual_yes"]) == D(m["residual_no"]) == 0 for m in markets)}
    assert len(markets) == 24
    assert len(orders) == stats["normal_windows"]+stats["repair_windows"]
    assert abs(sum((D(e["paired_pnl"]) for e in repair_eps), D(0))+D(stats["session_pool_cost_adjustment"])-total_pnl) < D("1e-9")
    (OUT/"summary.json").write_text(json.dumps({"stats": stats, "markets": markets}, indent=2)+"\n")
    (OUT/"source_manifest.json").write_text(json.dumps(evidence, indent=2)+"\n")
    (OUT/"timeline.json").write_text(json.dumps(timeline, indent=2)+"\n")
    write_csv("markets.csv", [{k: v for k, v in m.items() if k not in {"actual_fills", "episodes", "merge_events", "cache_health", "cache_source_failures", "block_reason_counts", "first_blocks", "observed_inventory_tolerance"}} for m in markets])
    write_csv("episodes.csv", [{k: v for k, v in e.items() if k not in {"actual_fills", "windows", "states"}} for e in episodes])
    write_csv("orders.csv", orders)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
