"""Read-only hourly M2/M3/M4 health audit in the authorized existing chat."""

import argparse
import fcntl
import json
import sqlite3
import subprocess
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

from polymaker.m2_report import summarize

if __package__:
    from .m234_watch_metrics import operational_metrics
else:
    from m234_watch_metrics import operational_metrics

INTERVAL = 3600


def atomic_json(path, value):
    temp = path.with_suffix(path.suffix+".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n")
    temp.replace(path)


def services(runner=subprocess.run):
    names = ("polymaker-m2-study.service", "polymaker-m1-live.service")
    result = {}
    for name in names:
        reply = runner(["systemctl", "--user", "show", name, "-p", "ActiveState", "-p", "MainPID",
                        "-p", "NRestarts", "-p", "ExecMainStartTimestamp"],
                       capture_output=True, text=True, timeout=5, check=False)
        result[name] = dict(line.split("=", 1) for line in reply.stdout.splitlines() if "=" in line)
    return result


def assess(directory, *, now=None):
    now = time.time() if now is None else now
    directory = Path(directory)
    latest = json.loads((directory/"latest.json").read_text())
    version = latest["active_version"]
    db = sqlite3.connect((directory/"state.sqlite3").resolve().as_uri()+"?mode=ro", uri=True, timeout=2)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        markets = [dict(r) for r in db.execute("""SELECT m.*,r.id AS run_id,r.counts FROM runs r
            JOIN markets m ON m.slug=r.slug WHERE r.version=? ORDER BY m.epoch""", (version,))]
        complete = [m for m in markets if m["state"] == "CLOSED" and m["full_window"]]
        eligible = [m for m in complete if m["winner"] is not None]
        rows = [dict(r) for r in db.execute("""SELECT s.*,m.winner FROM samples s
            JOIN markets m ON m.slug=s.slug WHERE s.version=? AND m.state='CLOSED'
            AND m.full_window=1 AND m.winner IS NOT NULL ORDER BY s.wall,s.id""", (version,))]
        observation = db.execute("""SELECT count(*) AS total,coalesce(sum(o.valid),0) AS valid
            FROM observations o JOIN runs r ON r.id=o.run_id WHERE r.version=?""", (version,)).fetchone()
        candidates = db.execute("""SELECT count(*) FROM candidates c JOIN runs r ON r.id=c.run_id
            WHERE r.version=?""", (version,)).fetchone()[0]
        orphan = db.execute("""SELECT count(*) FROM observations o JOIN runs r ON r.id=o.run_id
            LEFT JOIN candidates c ON c.id=o.candidate_id
            WHERE r.version=? AND (c.id IS NULL OR c.run_id!=o.run_id)""", (version,)).fetchone()[0]
        duplicates = db.execute("""SELECT count(*) FROM (SELECT slug,rule,ap_id FROM samples
            WHERE version=? GROUP BY slug,rule,ap_id HAVING count(*)>1)""", (version,)).fetchone()[0]
        extra_rows = []
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='strategy_samples'").fetchone():
            extra_rows = [dict(r) for r in db.execute('''SELECT s.*,m.winner FROM strategy_samples s
                JOIN markets m ON m.slug=s.slug WHERE s.version=? ORDER BY s.wall,s.id''', (version,))]
        reused_m4_ids = db.execute('''SELECT count(*) FROM (SELECT e.run_id,
            json_extract(e.payload,'$.payload.id') AS identity FROM events e JOIN runs r ON r.id=e.run_id
            WHERE r.version=? AND e.kind='M4_ROUND_CLOSED' GROUP BY e.run_id,identity
            HAVING count(DISTINCT json_extract(e.payload,'$.payload.start'))>1)''', (version,)).fetchone()[0]
        operational = operational_metrics(db, directory, latest, markets, now)
    finally:
        db.close()

    invalid_quotes = payout_mismatch = 0
    for row in rows+extra_rows:
        try:
            price, quantity, cost = (Decimal(row[k]) for k in ("price", "quantity", "cost"))
            valid = (all(v.is_finite() for v in (price, quantity, cost))
                     and 0 < price < 1 and quantity >= 0 and 0 <= cost <= 5
                     and abs(price*quantity-cost) < Decimal(".00000001"))
            invalid_quotes += not valid
            if valid and quantity > 0 and row["sample_pnl"] is not None:
                expected = (quantity if row["outcome"] == row["winner"] else Decimal(0))-cost
                payout_mismatch += Decimal(row["sample_pnl"]) != expected
                if row["status"] == "ASSUMED_FILL":
                    payout_mismatch += row['trade_pnl'] is None or Decimal(row['trade_pnl']) != expected
                else:
                    payout_mismatch += row['trade_pnl'] is not None
        except (ValueError, TypeError, InvalidOperation):
            invalid_quotes += 1
    gaps, observer_failures, directions = {}, {}, {}
    for row in extra_rows:
        if row['status'] == 'ASSUMED_FILL':
            key = (row['strategy'], row['slug'], row['outcome'])
            directions[key] = directions.get(key, 0)+1
    direction_violations = sum(n > 1 for n in directions.values())
    for market in markets:
        counts = json.loads(market["counts"])
        observer_failures.update(counts.get('observer_failures', {}))
        for key, value in counts.get("invalid_reasons", {}).items():
            gaps[key] = gaps.get(key, 0)+value
    observer_failures.update(latest.get('runtime', {}).get('observer_failures', {}))
    quality = {"report_age_sec": max(0, now-latest["observed_at"]),
               "observations": observation["total"], "valid_observations": observation["valid"],
               "valid_observation_fraction": observation["valid"]/observation["total"] if observation["total"] else None,
               "orphan_observations": orphan, "duplicate_rule_episodes": duplicates,
               "invalid_quotes_or_risk": invalid_quotes, "settlement_pnl_mismatch": payout_mismatch,
               "input_reset_counts": gaps,
               "observer_failures": observer_failures,
               "strategy_direction_limit_violations": direction_violations,
               "reused_m4_round_ids": reused_m4_ids,
               "scope": "Valid observed intervals only; full_window is not a promise of gap-free data."}
    problems = []
    if quality["report_age_sec"] > 180:
        problems.append("STALE_REPORT")
    if orphan or duplicates or invalid_quotes or payout_mismatch or direction_violations:
        problems.append("LEDGER_INVARIANT_FAILED")
    if observer_failures:
        problems.append('STRATEGY_OBSERVER_FAILED')
    if reused_m4_ids:
        problems.append('STRATEGY_IDENTITY_REUSED')
    if latest.get("real_posts") != 0:
        problems.append("UNEXPECTED_REAL_POST_VALUE")
    problems.extend(operational['problems'])

    grouped = {}
    for row in rows:
        grouped.setdefault(row["rule"], []).append(row)
    split_epoch = eligible[len(eligible)//2]["epoch"] if len(eligible) >= 2 else None
    rule_reports = {}
    possible = False
    comparable = False
    for rule, samples in sorted(grouped.items()):
        overall = summarize(samples)
        early, late = [], []
        for sample in samples:
            epoch = int(sample["slug"].rsplit("-", 1)[1])
            (early if split_epoch is not None and epoch < split_epoch else late).append(sample)
        temporal = {"early": summarize(early), "late": summarize(late)}
        for label, half in (("early", early), ("late", late)):
            settled_half = [r for r in half if r["trade_pnl"] is not None and r["status"] == "ASSUMED_FILL"]
            wins = sum(r["outcome"] == r["winner"] for r in settled_half)
            temporal[label]["observed_wins"] = wins
            temporal[label]["observed_losses"] = len(settled_half)-wins
        comparable |= overall["settled_markets"] >= 30
        # This flags a human exploratory review, never trading readiness. Thirty
        # markets is only a minimum for the interval approximation, not proof.
        intervals = [temporal[k]["market_cluster_normal_95_interval"] for k in ("early", "late")]
        represented = all(temporal[k]["observed_wins"] >= 5 and temporal[k]["observed_losses"] >= 5
                          for k in ("early", "late"))
        stable_positive = represented and all(i is not None and i[0] > 0 for i in intervals)
        possible |= stable_positive and rule != "all_candidates"
        rule_reports[rule] = {"overall": overall, "temporal_halves": temporal,
                              "stable_positive_exploratory_pattern": stable_positive}
    if problems:
        stage = "DATA_QUALITY_REVIEW_REQUIRED"
    elif possible:
        stage = "EXPLORATORY_THRESHOLD_REVIEW_POSSIBLE"
    elif comparable:
        stage = "EXPLORATORY_COMPARISON_ONLY"
    else:
        stage = "INSUFFICIENT_INDEPENDENT_DATA"
    eligible_slugs = {m['slug'] for m in eligible}
    strategies = {'M2': summarize([r for r in rows if r['rule'] == 'all_candidates'])}
    for strategy in ('M3', 'M4'):
        strategies[strategy] = summarize([r for r in extra_rows
            if r['strategy'] == strategy and r['slug'] in eligible_slugs])
    return {"observed_at": now, "bucket": int(now//INTERVAL)*INTERVAL,
            "version": version, "stage": stage, "automatic_threshold_change": False,
            "trading_ready": False, "runtime": latest.get("runtime"), "problems": problems,
            "markets": {"observed": len(markets), "complete": len(complete),
                        "complete_officially_settled": len(eligible),
                        "incomplete_or_active": len(markets)-len(complete)},
            "candidates": candidates,
            "unique_settled_signal_episodes": len({(r["slug"], r["ap_id"]) for r in rows
                                                   if r["trade_pnl"] is not None and r["status"] == "ASSUMED_FILL"}),
            "independent_settled_signal_markets": len({r["slug"] for r in rows
                                                       if r["trade_pnl"] is not None and r["status"] == "ASSUMED_FILL"}),
            "quality": quality, "rules": rule_reports,
            "strategies": strategies,
            "operational": operational,
            "strategy_summary_scope": "Current version; complete officially settled markets. Extra strategy risk checks include active/pending markets.",
            "interpretation": [
                "Repeated observations and overlapping threshold groups are not independent samples.",
                "No fixed sample count establishes sufficiency; assess interval precision and temporal stability.",
                "Repeated peeking and comparing several thresholds can exaggerate evidence; intervals are exploratory.",
                "All wins or all losses can create misleading zero-variance intervals; both outcomes must be represented.",
                "Freeze a candidate rule, then verify on later unseen markets before any trading decision.",
                "Assumed full Maker fills are not actual execution evidence; no threshold may be auto-deployed.",
            ]}


def wake(thread, project, output, report, *, runner=subprocess.run, now=None):
    now = time.time() if now is None else now
    state_path = Path(output)/"agent-state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    if state.get('monitoring_paused'):
        return {'status': 'MONITORING_PAUSED'}
    bucket = report["bucket"]
    reviewed, queued = state.get("reviewed_through_bucket", 0), state.get("queued_bucket", 0)
    if bucket <= reviewed:
        return {"status": "ALREADY_REVIEWED"}
    if queued > reviewed and now-state.get("last_queued_at", 0) < 3*INTERVAL:
        return {"status": "COALESCED_WITH_PENDING_REVIEW", "queued_bucket": queued}
    summaries = report.get('operational', {}).get('strategies', {})
    initial = '\n'.join(f"{s}：历史累计有效信号 {v['cumulative_all_versions']['valid_signals']}；当前版本有效信号 {v['current_version']['valid_signals']}；当前版本假设成交 {v['current_version']['assumed_fills']}；待官方结算 {v['current_version']['pending_fills']}。"
                        for s, v in summaries.items())
    if not initial:
        initial = 'M2/M3/M4 计数暂不可用，需报告巡检读取故障；不能将未知数量记为零。'
    prompt = f'''M2 M3 M4 每小时巡检：用户明确授权恢复当前对话 session 的巡检，每小时一次。
只在当前对话继续，不创建新对话，不调用其他聊天，不创建新的定时任务。
在 {project} 读取 {output}/latest.json、reports 内未复核记录，必要时只读 research_outputs/m2_virtual/state.sqlite3、definition 文件及 M2 服务日志。
分别检查 M2/M3/M4：系统是否正常（共享进程/时效/时钟/积压/断流/资源等待）、策略是否正常（当前定义/组件事件/observer故障）、信号是否正常产出（候选→有效观察→首次合格根事件）、模拟交易是否正常（假设成交/方向与风险额度/待结算/官方结果）。
看板展示、历史归档界面不在本巡检范围，已证明仅影响看板的修改不告警、不作为重启理由；保留三策略实际依赖的公共数据与结算校验检查。
每次都在当前对话简短汇报，分别输出 M2、M3、M4 的历史累计有效信号数量、当前版本有效信号数量和模拟开仓/待结算数量，即使没有新增信号也照常报告。此要求取代此前的状态不变静默规则。
有效信号按独立根机会第一次合格入样且数量为正计数，含因同方向限笔而跳过的研究信号；不把重复观察、M2嵌套阈值组、同场信号当成独立市场。M2仅取all_candidates主组；M3按CA根，M4按DF根。
历史版本仅作库存累计计数，按版本/定义单列；当前策略健康和收益只能按当前源码/设置版本解释。M4旧v1与当前m4-frequency-v2不能混合收益或声称同一定义验证。
全合法价格、单笔风险上限5 USDC、每策略/规则每场20 USDC、每方向最多1笔假设成交；后续同方向信号保留研究记录但不计策略PnL。未知结果保持待结算，不能当零收益；full_window不等于输入无缺口；没有新信号本身不算故障。
保护 M1 实盘：本巡检只读，不改动或重启 M1，不读取钱包、余额、成交或仓位，不制造真实试单。M2 无信号/资源等待不是修改信号门槛的理由。发现实现问题先记录证据并报告，不擅自改变用户已确认规则。
本轮初步健康状态：{report['stage']}。计数如下（以实际复核快照为准）：
{initial}
复核完成后，原子更新 {state_path} 的 reviewed_through_bucket 为实际已复核 latest.json 的 bucket，保留其他字段。可附 reviewed_at 与简短 assessment。不要把未完成的审查标为已复核。
'''
    try:
        result = runner(["/home/ubuntu/.local/bin/codex", "queue", "--thread", thread,
                         "--message", prompt, "--resume"], cwd=project,
                        capture_output=True, text=True, timeout=45, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "WAKE_FAILED", "reason": type(exc).__name__}
    if result.returncode != 0:
        return {"status": "WAKE_FAILED", "returncode": result.returncode}
    warning = "warning" in (result.stdout+result.stderr).lower()
    state.update(queued_bucket=bucket, last_queued_at=now, resume_unconfirmed=warning, thread_id=thread)
    atomic_json(state_path, state)
    return {"status": "QUEUED_RESUME_UNCONFIRMED" if warning else "QUEUED_AND_RESUME_REQUESTED",
            "queued_bucket": bucket, "thread_id": thread}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--thread", required=True)
    parser.add_argument("--no-wake", action="store_true")
    args = parser.parse_args()
    project = args.project_dir.resolve()
    output = project/"research_outputs"/"m2_watch"
    output.mkdir(parents=True, exist_ok=True)
    (output/"reports").mkdir(exist_ok=True)
    with (output/"watch.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            report = assess(project/"research_outputs"/"m2_virtual")
        except (OSError, ValueError, KeyError, TypeError, InvalidOperation, sqlite3.Error) as exc:
            now = time.time()
            report = {"observed_at": now, "bucket": int(now//INTERVAL)*INTERVAL,
                      "stage": "DATA_QUALITY_REVIEW_REQUIRED",
                      "problems": ["ASSESSMENT_FAILED:"+type(exc).__name__],
                      "markets": {"complete_officially_settled": None},
                      "independent_settled_signal_markets": None,
                      "unique_settled_signal_episodes": None, "trading_ready": False}
        report["services"] = services()
        report['watch_contract'] = 'M234_HOURLY_HEALTH_V1'
        report['interval_sec'] = INTERVAL
        previous_path = output/'latest.json'
        previous = json.loads(previous_path.read_text()) if previous_path.exists() else {}
        if previous.get('watch_contract') == report['watch_contract'] and previous.get('bucket', report['bucket']) < report['bucket']:
            report['previous_report_bucket'] = previous['bucket']
            for strategy, metrics in report.get('operational', {}).get('strategies', {}).items():
                old = previous.get('operational', {}).get('strategies', {}).get(strategy)
                if old:
                    metrics['valid_signal_delta_since_previous_report'] = metrics['cumulative_all_versions']['valid_signals']-old['cumulative_all_versions']['valid_signals']
        m2 = report["services"].get("polymaker-m2-study.service", {})
        if m2.get("ActiveState") != "active":
            report["problems"].append("M2_SERVICE_NOT_ACTIVE")
            report["stage"] = "DATA_QUALITY_REVIEW_REQUIRED"
        atomic_json(output/"latest.json", report)
        atomic_json(output/"reports"/f"{report['bucket']}.json", report)
        if not args.no_wake:
            report["wakeup"] = wake(args.thread, project, output, report)
            atomic_json(output/"latest.json", report)
            atomic_json(output/"reports"/f"{report['bucket']}.json", report)
        print(json.dumps({"stage": report["stage"], "markets": report["markets"],
                          "independent_settled_signal_markets": report["independent_settled_signal_markets"],
                          "wakeup": report.get("wakeup")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
