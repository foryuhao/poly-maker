"""Read-only reconstruction of 12 closed BTC markets; run from repository root."""
import csv
import hashlib
import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

OUT = Path(__file__).resolve().parent
TZ = timezone(timedelta(hours=8))
START, END = 1792248300, 1792251900
D = lambda x: Decimal(str(x))


def stamp(t):
    return datetime.fromtimestamp(float(t), TZ).strftime("%H:%M:%S.%f")[:-3] if t is not None else "—"


def percentile(values, p):
    return sorted(values)[max(0, math.ceil(len(values)*p)-1)] if values else None


def write_csv(name, rows):
    if rows:
        with (OUT / name).open("w") as file:
            writer = csv.DictWriter(file, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)


def analyze():
    markets, windows, fills_out, events, provenance = [], [], [], [], []
    errors, paused, requests, final_outcomes, waits_all = [], [], [], Counter(), Counter()
    signal_histories, early_signals = [], []
    for path in Path("journal").glob("rdp_sm_*.jsonl"):
        with path.open() as file:
            first_line = next(file, "")
        if not first_line.strip():
            continue
        try:
            head = json.loads(first_line)
        except ValueError:
            continue
        if head["kind"] != "sm_session":
            continue
        session = head["data"]["payload"]
        epoch = int(session["slug"].rsplit("-", 1)[-1])
        if not START <= epoch < END:
            continue
        raw = path.read_bytes()
        rows = []
        for line, value in enumerate(raw.decode().splitlines(), 1):
            try:
                x = json.loads(value)
                rows.append((line, x, x["data"]["payload"]))
            except (ValueError, KeyError) as exc:
                errors.append([str(path), line, type(exc).__name__])
        by = defaultdict(list)
        for line, x, q in rows:
            by[x["kind"]].append((line, x["ts"], q))
        assert len(by["sm_run_result"]) == 1, path
        result_line, stopped, result = by["sm_run_result"][0]
        sid = session["session_id"]
        label = stamp(epoch)[:5]
        net = by["sm_network_request"]
        offset = statistics.median(q["completed_wall"]-q["completed_monotonic"] for _, _, q in net)
        source = str(path.resolve())
        provenance.append(dict(market=label, session_id=sid, slug=session["slug"],
                               path=source, sha256=hashlib.sha256(raw).hexdigest(),
                               bytes=len(raw), lines=len(rows), result_line=result_line))
        intents = {q["intent"]["window_id"]: (line, q["intent"], q.get("sizing", {}))
                   for line, _, q in by["sm_obi_final_decision"]}
        closes = {q["window_id"]: (line, q) for line, _, q in by["sm_obi_close_requested"]}
        results = {q["window_id"]: q for _, _, q in by["sm_window_result"]}
        placements = {q["quote"]["window_id"]: q for _, _, q in by["sm_placement_result"]}
        quote_by_oid, token_side = {}, {}
        for _, intent, _ in intents.values():
            for side in ("yes", "no"):
                token_side[intent["snapshot"][side]["token_id"]] = side.upper()
            w = intent["window_id"]
            q = placements[w]
            quote_by_oid[q.get("order_id") or q["expected_order_id"]] = intent["quotes"][0]
        official_times = {}
        for _, _, q in by["sm_official_reconciliation"]:
            for trade in q.get("trades", []):
                for leg in trade.get("maker_orders", []):
                    if leg.get("order_id") in quote_by_oid and trade.get("match_time"):
                        official_times[(trade["id"], leg["order_id"])] = float(trade["match_time"])
        confirmed = {}
        observations = defaultdict(dict)
        for line, t, fill in by["sm_trade"]:
            key = (fill["trade_id"], fill["maker_order_id"])
            observations[key].setdefault(fill["status"], t)
            if fill["status"] == "CONFIRMED":
                if key in confirmed:
                    assert confirmed[key][2] == fill, (path, key, "conflicting fill")
                confirmed[key] = (line, t, fill)
        costs, quantities = defaultdict(Decimal), defaultdict(Decimal)
        order_fills = defaultdict(list)
        for key, (line, t, fill) in confirmed.items():
            side = token_side[fill["token_id"]]
            quantities[side] += D(fill["size"])
            costs[side] += D(fill["size"]) * D(fill["price"])
            actual = official_times.get(key)
            record = dict(market=label, session_id=sid, order_id=key[1], trade_id=key[0],
                          side=side, quantity=fill["size"], price=fill["price"],
                          match_time=stamp(actual), match_epoch=actual,
                          confirmed_observed_time=stamp(t), confirmed_epoch=t,
                          matched_observed_time=stamp(observations[key].get("MATCHED")),
                          matched_observed_epoch=observations[key].get("MATCHED"),
                          confirmation_lag_s=t-actual if actual else None, source_line=line)
            fills_out.append(record)
            order_fills[key[1]].append(record)
        merges = {q["merge_id"]: (line, t, q) for line, t, q in by["sm_merge_result"]
                  if q.get("status") == "CONFIRMED"}
        merged = sum((D(q["amount_raw"])/1000000 for _, _, q in merges.values()), Decimal(0))
        released = sum((D(q["released_cost_usdc"]) for _, _, q in merges.values()), Decimal(0))
        pair_pnl = merged-released
        residual_cost = sum(costs.values())-released
        cash_flow = merged-sum(costs.values())
        assert len(confirmed) == result["confirmed_fills"], (label, "fill count")
        assert abs(pair_pnl-D(result["realized_merge_pnl"])) < D(".000001"), (label, "PnL")
        for side in ("YES", "NO"):
            assert abs(quantities[side]-merged-D(result[f"confirmed_{side.lower()}"])) < D(".000001"), (label, side)
        local_windows = []
        for wid, (line, intent, sizing) in sorted(intents.items(), key=lambda a:a[1][1]["post_started"] or a[1][1]["frozen_at"]):
            placement, outcome = placements[wid], results[wid]
            order = placement.get("order_id") or placement["expected_order_id"]
            final_placement = outcome["placement_results"][0]
            final_outcomes[final_placement["outcome"]] += 1
            close_line, close = closes[wid]
            signal = intent.get("final_signal") or intent.get("signal") or {}
            quote = intent["quotes"][0]
            fills = order_fills[order]
            submitted = intent["post_started"] is not None
            began = intent["post_started"] or intent["frozen_at"]
            row = dict(market=label, session_id=sid, window=wid.rsplit(":", 1)[-1], phase=intent["phase"],
                       signal=signal.get("label"), obi=signal.get("imbalance"),
                       submitted=submitted, aborted_reason=intent.get("aborted_reason"),
                       post_time=stamp(began+offset) if submitted else "未发送", post_epoch=began+offset,
                       post_ms=(intent["post_returned"]-began)*1000 if submitted else None,
                       side=quote["outcome"], price=quote["price"], size=quote["size"],
                       outcome_initial=placement["outcome"], outcome_final=final_placement["outcome"],
                       close_time=stamp(close["requested_monotonic"]+offset), close_reason=close["reason"],
                       close_signal=(close.get("signal") or {}).get("imbalance"),
                       post_to_close_s=close["requested_monotonic"]-began if submitted else None,
                       original_deadline=stamp(intent["deadline"]+offset if intent["deadline"] else None), close_barrier=outcome["close_barrier"],
                       block_reason=outcome.get("block_reason"),
                       final_close_time=stamp(outcome["end_time"]),
                       post_to_safe_s=outcome["end_time"]-(began+offset) if submitted else None,
                       close_to_safe_s=outcome["end_time"]-(close["requested_monotonic"]+offset),
                       fill_quantity=sum(f["quantity"] for f in fills),
                       match_times=";".join(f["match_time"] if f["match_epoch"] else "本地MATCHED " + f["matched_observed_time"] for f in fills),
                       fill_count=len(fills), initial_p95_ms=(intent.get("health") or {}).get("p95_ms"),
                       target_delta=sizing.get("configured_target_delta"), ideal_repair=sizing.get("ideal_repair_size"),
                       repair_fallback=";".join(sizing.get("repair_sizing_fallback", [])),
                       source_line=line, close_source_line=close_line, order_id=order,
                       raw_error=";".join(z.get("errorMsg", "") for z in (placement.get("raw_response") or []) if isinstance(z, dict)))
            windows.append(row)
            local_windows.append(row)
        for line, t, q in by["sm_network_request"]:
            requests.append(dict(market=label, **q))
        for line, t, q in by["sm_obi_wait"]:
            waits_all[q["reason"]] += 1
            early_signals.append(dict(market=label, time=stamp(t), epoch=t, reason=q["reason"],
                                      signal=(q.get("signal") or {}).get("label"),
                                      obi=(q.get("signal") or {}).get("imbalance"), line=line))
        for line, t, q in by["sm_obi_signal_changes"]:
            active = closes[q["window_id"]][1]["requested_monotonic"]
            signal_histories.append(dict(market=label, window=q["window_id"].rsplit(":",1)[-1],
                                        count=len(q["changes"]), active_count=sum(z[1]<=active for z in q["changes"]),
                                        overwritten=q["history_overwritten"]))
        for line, t, q in by["sm_network_transition"]:
            event_time = q.get("event_monotonic", t-offset)+offset
            paused.append(dict(market=label, time=stamp(event_time), epoch=event_time, line=line, **q))
        relevant = ("sm_network_transition", "sm_obi_wait", "sm_trade", "sm_merge_result", "sm_run_result")
        for line, x, q in rows:
            if x["kind"] in relevant and (x["kind"]!="sm_trade" or q["status"]=="CONFIRMED"):
                event_epoch = q.get("event_monotonic", x["ts"]-offset)+offset if x["kind"]=="sm_network_transition" else x["ts"]
                events.append(dict(market=label, epoch=event_epoch, time=stamp(event_epoch), kind=x["kind"], line=line,
                                   side=token_side.get(q.get("token_id")), data=q))
        first = next((w for w in local_windows if w['submitted']), {})
        waits = Counter(q["reason"] for _, _, q in by["sm_obi_wait"])
        close_count = Counter(w["close_reason"] for w in local_windows)
        market = dict(market=label, epoch=epoch, session_id=sid, slug=session["slug"],
                      execution_status=result["execution_status"], final_barrier=result["final_close_barrier"],
                      windows=len(local_windows), submitted=sum(w['submitted'] for w in local_windows),
                      normal=sum(w["phase"]=="NORMAL" and w['submitted'] for w in local_windows),
                      rebalance=sum(w["phase"]=="REBALANCE" and w['submitted'] for w in local_windows),
                      first_side=first.get("side"), first_obi=first.get("obi"), first_post=first.get("post_time"),
                      first_observed=stamp(head["ts"]), start_delay_s=head["ts"]-epoch,
                      filled_orders=sum(w["fill_quantity"]>0 for w in local_windows), fill_records=len(confirmed),
                      bought_yes=float(quantities["YES"]), bought_no=float(quantities["NO"]),
                      merged_pairs=float(merged), pair_pnl=float(pair_pnl), residual_cost=float(residual_cost),
                      residual_yes=result["confirmed_yes"], residual_no=result["confirmed_no"],
                      scoped_cash_flow=float(cash_flow), starting_balance=session["starting_official_balance_usdc"],
                      end_time=stamp(stopped), end_epoch=stopped,
                      reverse_closes=close_count["SIGNAL_REVERSED"], deadline_closes=close_count["ORDER_DEADLINE"],
                      stale_closes=close_count["WS_BOOK_STALE"],
                      no_legal_waits=waits["NO_LEGAL_SIGNAL_CANDIDATE"],
                      network_pauses=sum(q["state"]=="PAUSED" for _, _, q in by["sm_network_transition"]),
                      max_writer_queue=max(x["data"].get("writer_queue_depth",0) for _,x,_ in rows),
                      dropped_diagnostics=max(x["data"].get("diagnostics_dropped",0) for _,x,_ in rows),
                      source=source, result_line=result_line)
        markets.append(market)
    markets.sort(key=lambda r:r["epoch"])
    windows.sort(key=lambda r:r["post_epoch"])
    assert len(markets)==12 and [r["epoch"] for r in markets]==list(range(START,END,300))
    sent = [w for w in windows if w['submitted']]
    totals = dict(markets=len(markets), windows=len(windows), submitted=len(sent),
                  executions=dict(Counter(m["execution_status"] for m in markets)),
                  phases=dict(Counter(w["phase"] for w in sent)), outcomes=dict(final_outcomes),
                  close_reasons=dict(Counter(w["close_reason"] for w in windows)),
                  phase_close_reasons={p:dict(Counter(w["close_reason"] for w in windows if w["phase"]==p)) for p in ("NORMAL","REBALANCE")},
                  fill_records=len(fills_out), filled_orders=sum(w["fill_quantity"]>0 for w in windows),
                  traded_markets=sum(m["fill_records"]>0 for m in markets),
                  residual_markets=sum(m["residual_yes"]+m["residual_no"]>0 for m in markets),
                  pair_pnl=sum(m["pair_pnl"] for m in markets), residual_cost=sum(m["residual_cost"] for m in markets),
                  scoped_cash_flow=sum(m["scoped_cash_flow"] for m in markets),
                  merged_pairs=sum(m["merged_pairs"] for m in markets),
                  waits=dict(waits_all), pause_reasons=dict(Counter(p["reason"] for p in paused if p["state"]=="PAUSED")),
                  request_outcomes=dict(Counter(r["outcome"] for r in requests)),
                  request_methods=dict(Counter(r["method"] for r in requests)),
                  post_ms={"median":statistics.median(w["post_ms"] for w in sent), "p95":percentile([w["post_ms"] for w in sent],.95), "max":max(w["post_ms"] for w in sent)},
                  phase_fill_rates={p:dict(submitted=sum(w["phase"]==p for w in sent),filled=sum(w["phase"]==p and w["fill_quantity"]>0 for w in sent)) for p in ("NORMAL","REBALANCE")},
                  malformed_lines=errors, history_overwritten=sum(r["overwritten"] for r in signal_histories))
    write_csv("markets.csv", markets)
    write_csv("windows.csv", windows)
    write_csv("fills.csv", fills_out)
    write_csv("signal_waits.csv", sorted(early_signals,key=lambda r:r["epoch"]))
    (OUT/"analysis.json").write_text(json.dumps(dict(totals=totals,markets=markets,windows=windows,
        fills=fills_out,network_transitions=paused,signal_histories=signal_histories,
        signal_waits=early_signals,provenance=provenance),ensure_ascii=False,indent=2))
    # Full per-market timeline, including all orders and material lifecycle events.
    lines=["# 12 场逐单时间线（北京时间，2026-10-17 22:45–23:45）", "",
           "范围冻结为 12 个连续已结束市场；未过滤异常结果。POST 时间按 monotonic/墙钟对齐。",
           "本批全部 33 条成交缺少可用官方撮合时间（缓存 timestamp=0）；成交列使用本地首次 MATCHED 观察时间，相关事件使用 CONFIRMED 观察时间，均不能称为官方撮合时刻。NONE/INVALID/等待日志不构成完整逐帧信号流。", ""]
    for m in markets:
        lines += [f"## {m['market']} / {m['session_id']}", "",
                  f"[{m['slug']}]({m['source']}:1)；结束 {m['end_time']}，{m['execution_status']} / {m['final_barrier']}。",
                  f"合并毛收益 {m['pair_pnl']:+.6f} USDC；期末 YES {m['residual_yes']} / NO {m['residual_no']}；残仓成本 {m['residual_cost']:.6f}。", "",
                  "|轮次|阶段|POST|信号/OBI|BUY 价格×数量|POST ms|关闭请求/原因/反向OBI|终态/核验结束|确认成交|",
                  "|---|---|---|---|---|---:|---|---|---|"]
        for w in windows:
            if w['session_id']!=m['session_id']:continue
            signal=f"{w['signal'] or '库存补仓'} / {float(w['obi']):+.3f}" if w['obi'] is not None else '库存补仓'
            post_ms=f"{w['post_ms']:.0f}" if w['submitted'] else '—'
            reversal=f" / {float(w['close_signal']):+.3f}" if w['close_signal'] is not None else ''
            lines.append(f"|[{w['window']}]({m['source']}:{w['source_line']})|{w['phase']}|{w['post_time']}|{signal}|{w['side']} {w['price']}×{w['size']}|{post_ms}|{w['close_time']} {w['close_reason']}{reversal}|{w['outcome_final']} / {w['close_barrier']} {w['block_reason'] or ''} / {w['final_close_time']}|{w['fill_quantity']}；{w['match_times']}|")
        lines += ["", "相关事件：", ""]
        for event in sorted((e for e in events if e['market']==m['market']),key=lambda e:e['epoch']):
            q=event['data'];k=event['kind']
            if k=='sm_network_transition':desc=f"网络 {q['previous']} → {q['state']}：{q['reason']}"
            elif k=='sm_obi_wait':desc=f"等待 {q['reason']}；{(q.get('signal') or {}).get('label')} / {(q.get('signal') or {}).get('imbalance')}"
            elif k=='sm_trade':desc=f"本地观察 CONFIRMED：{event['side']} {q['size']} @ {q['price']}；trade={q['trade_id']}"
            elif k=='sm_merge_result':desc=f"合并 {D(q['amount_raw'])/1000000} 对，状态 {q['status']}，释放成本 {q.get('released_cost_usdc')}"
            else:continue
            lines.append(f"- [{event['time']}]({m['source']}:{event['line']}) {desc}")
        lines.append("")
    (OUT/"timeline.md").write_text("\n".join(lines))
    print(json.dumps(totals,ensure_ascii=False,indent=2))
    for m in markets:
        print(m['market'],m['normal'],m['rebalance'],m['first_side'],m['first_obi'],m['filled_orders'],m['pair_pnl'],m['residual_yes'],m['residual_no'])
    return totals


if __name__=="__main__":
    analyze()
