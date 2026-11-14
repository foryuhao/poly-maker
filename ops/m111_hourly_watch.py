"""Read-only hourly M1–M11 inspection; queue reports to one authorized existing chat."""

from __future__ import annotations

import argparse
import concurrent.futures
import fcntl
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from m511_sim_follow import Tail
from m1234_sim_watch import atomic_json, tail_events

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_STATE = PROJECT / "research_outputs/m111_hourly_watch"
SCENARIOS = ("trades-only", "cancel-half")
RESEARCH = ("M5", "M6", "M7", "M8", "M9", "M10", "M4+M11", "M9+M11", "M10+M11")
OFFSET = 170  # HH:02:50; live watch runs at */5:20, 150 seconds away.


def read(path):
    return json.loads(Path(path).read_text())


def owned(path, project=PROJECT):
    path = Path(path).resolve()
    if (project / "research_outputs").resolve() not in path.parents:
        raise ValueError("MONITOR_SOURCE_OUTSIDE_RESEARCH_OUTPUTS")
    return path


def service(unit, runner=subprocess.run):
    if not re.fullmatch(r"polymaker-[a-z0-9.-]+\.service", unit):
        raise ValueError("INVALID_MONITOR_UNIT")
    reply = runner(
        [
            "systemctl",
            "--user",
            "show",
            unit,
            "--property=LoadState,ActiveState,SubState,MainPID,ExecMainStatus,Result",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    return dict(line.split("=", 1) for line in reply.stdout.splitlines() if "=" in line)


def execution_counts(run, script, cache, source_root):
    env = dict(os.environ, PYTHONPATH=str(source_root / "src"), PYTHONDONTWRITEBYTECODE="1")
    reply = subprocess.run(
        [
            str(PROJECT / ".venv/bin/python"),
            "-B",
            str(script),
            "--run",
            str(run),
            "--cache",
            str(cache),
        ],
        cwd=PROJECT,
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
        check=True,
    )
    value = json.loads(reply.stdout)
    if value.get("market_databases", 0) == 0:
        value["status"] = "UNAVAILABLE"
    return value


def assumed_report(directory, source, scenario, now):
    report = read(directory / "latest.json")
    plan = read(source / "plan.json")
    if (
        Path(report["source_run"]).resolve() != source
        or report["source_identity"] != plan["run_identity"]
        or report["source_scenario"] != scenario
        or report.get("evidence") != "ASSUMED_FILL"
    ):
        raise ValueError("ASSUMED_FILL_SOURCE_IDENTITY_MISMATCH")
    age = now - report["observed_at"]
    if 0 <= age <= 180:
        return report
    completion = source / "completion.json"
    if age < 0 or not completion.is_file():
        raise ValueError("ASSUMED_FILL_REPORT_STALE")
    terminal = read(completion)
    final = {key: terminal.get(key) for key in ("status", "reason")}
    planned_complete = (
        final == {"status": "PASS", "reason": "PLANNED_COMPLETE"}
        if scenario == "main/trades-only"
        else final["status"] == "SOURCE_COMPLETED" and terminal.get("unfinished_markets") == []
    )
    if (
        not planned_complete
        or terminal.get("run_identity") != plan["run_identity"]
        or report.get("source_terminal") != final
    ):
        raise ValueError("ASSUMED_FILL_REPORT_STALE")
    return {**report, "final_snapshot": True}


def verify_final_watermarks(report, metrics):
    def watermarks(data):
        return sorted(
            (row["market"], row["database_id"], row["audit_seq"], row["input_seq"])
            for row in data["sources"]
        )

    if metrics.get("status") in {"OK", "PARTIAL"}:
        final = watermarks(report)
        if final and final == watermarks(metrics):
            return
    raise ValueError("FINAL_ASSUMED_FILL_WATERMARK_MISMATCH")


def sum_profiles(metrics, strategy, scenario, profile=None):
    counts = {k: 0 for k in ("submitted_orders", "filled_orders", "unresolved_orders")}
    if metrics.get("status") == "UNAVAILABLE" or metrics.get("counted_markets", 0) == 0:
        return dict.fromkeys(counts)
    mode = "research" if strategy in RESEARCH else "main"
    profiles = metrics["modes"][mode]["by_profile_scenario"]
    for key, values in profiles.items():
        s, p, q = key.split("/", 2)
        expected = scenario if strategy == "M1" else mode + "/" + scenario
        if s == strategy and q == expected and (profile is None or p == profile):
            for field in counts:
                counts[field] += values[field]
    return counts


def make_row(strategy, metrics, assumed, profile=None):
    """Use one ledger watermark for all four columns when an assumed ledger exists."""
    mode = "research" if strategy in RESEARCH else "main"
    usable = metrics.get("status") != "UNAVAILABLE" and metrics.get("counted_markets", 0) > 0
    signals = (
        metrics.get("modes", {})
        .get(mode, {})
        .get("strategies", {})
        .get(strategy, {})
        .get("signals")
        if usable
        else None
    )
    execution = {q: sum_profiles(metrics, strategy, q, profile) for q in SCENARIOS}
    row = {
        "strategy": strategy,
        "profile": profile,
        "signals": signals,
        "execution": execution,
        "assumed_filled_orders": None,
        "data_status": metrics.get("status", "UNAVAILABLE"),
        "counted_markets": metrics.get("counted_markets", 0),
        "observed_at": metrics.get("observed_at"),
        "assumed_basis": "UNAVAILABLE",
    }
    if strategy == "M1":
        row.update(
            assumed_filled_orders=execution["trades-only"]["submitted_orders"],
            assumed_basis="DERIVED_ALL_COMMITTED_SUBMITS_FULL_FILL_NOT_SEPARATE_LEDGER",
        )
        return row
    if assumed.get("status") not in {"OK", "PARTIAL"} or not assumed.get("counted_markets"):
        if assumed.get("counted_markets") == 0 and assumed.get("excluded_research_samples"):
            row.update(
                data_status="NO_ELIGIBLE_SAMPLES",
                signals=None,
                execution={q: dict.fromkeys(execution[q]) for q in SCENARIOS},
            )
        return row
    values = assumed["strategies"][strategy]
    comparisons = assumed["execution_comparison"][strategy]
    row.update(
        signals=values["signals"],
        assumed_filled_orders=values["assumed_filled_orders"],
        observed_at=assumed["observed_at"],
        data_status=assumed["status"],
        counted_markets=assumed["counted_markets"],
        assumed_basis="ASSUMED_FILL_LEDGER",
        execution={
            q: {
                k: comparisons[mode + "/" + q][k]
                for k in ("submitted_orders", "filled_orders", "unresolved_orders")
            }
            for q in SCENARIOS
        },
    )
    if row["assumed_filled_orders"] != row["execution"]["trades-only"]["submitted_orders"]:
        raise ValueError("ASSUMED_FILL_SUBMISSION_COUNT_MISMATCH")
    return row


def unavailable(error):
    return {"status": "UNAVAILABLE", "error": type(error).__name__ + ":" + str(error)[:500]}


def expected_stop(family, terminal, follow_terminal):
    if family in {"M5_M11", "M511_ASSUMED"}:
        return (follow_terminal or {}).get("status") in {
            "STOPPED_BY_OPERATOR",
            "SOURCE_COMPLETED",
            "CAUGHT_UP",
        }
    return (terminal or {}).get("reason") in {"PLANNED_COMPLETE", "MANUAL_STOP"}


def published_input_seq(path):
    """Read only decodable tape records, including an as-yet unsealed gzip stream."""
    tail = Tail(path)
    last = 0
    try:
        while rows := tail.batch(2048):
            last = rows[-1]["seq"]
    finally:
        tail.close()
    return last


def following_progress(follow, source_metrics, now, source=None):
    watermarks = {row["market"]: row["input_seq"] for row in source_metrics.get("sources", [])}
    progress, ages = {}, []
    for market, row in follow.get("active", {}).items():
        high = watermarks.get(market)
        processed = row.get("processed_input_seq", 0)
        unread = max(0, row.get("source_bytes_available", 0) - row.get("source_bytes_read", 0))
        age = max(0, now - row["source_wall"]) if row.get("source_wall") else None
        ended = now >= int(market.rsplit("-", 1)[1]) + 300
        behind = unread > 0 or (high is not None and processed < high)
        published = None
        probe_error = None
        if ended and unread == 0 and high is not None and processed < high and source is not None:
            try:
                published = published_input_seq(source / market / "input.jsonl.gz")
            except Exception as error:
                probe_error = type(error).__name__
        if published is not None and published <= processed:
            stage = "WAITING_RECORDER_FLUSH_OR_SOURCE_TAIL"
        elif behind or not ended:
            stage = "PROCESSING_RECORDED_INPUT"
            if age is not None:
                ages.append(age)
        elif high is not None:
            stage = "AT_COMMITTED_WATERMARK_WAITING_SOURCE_TAIL"
        else:
            stage = "SOURCE_WATERMARK_UNAVAILABLE"
        progress[market] = {
            "stage": stage,
            "last_event_age_seconds": age,
            "unread_source_bytes": unread,
            "processed_input_seq": processed,
            "source_committed_input_seq": high,
            "source_published_input_seq": published,
            "publication_probe_error": probe_error,
        }
    lag = (
        max(ages)
        if ages
        else (
            0
            if progress
            and all(
                r["stage"]
                in {
                    "AT_COMMITTED_WATERMARK_WAITING_SOURCE_TAIL",
                    "WAITING_RECORDER_FLUSH_OR_SOURCE_TAIL",
                }
                for r in progress.values()
            )
            else None
        )
    )
    return progress, lag


def inspect(state_dir, project=PROJECT):
    now = time.time()
    main = read(project / "research_outputs/m1234_sim/monitor/control.json")
    research = read(project / "research_outputs/m511_sim/current.json")
    source, follower = owned(main["run_directory"], project), owned(research["directory"], project)
    source_matches = Path(research["source_run"]).resolve() == source
    for run in (source, follower):
        plan = read(run / "plan.json")
        if plan.get("mode") != "SIMULATION_ONLY" or plan.get("real_order_transport") != "ABSENT":
            raise ValueError("HOURLY_WATCH_REQUIRES_SIMULATION_ONLY_SOURCE")
    main_root = next(p for p in source.parents if (p / "src/polymaker").is_dir())
    research_root = owned(research["source_root"], project)
    units = {
        "M1_M4": main["unit"],
        "M234_ASSUMED": main["signal_validation"]["unit"],
        "M5_M11": research["unit"],
        "M511_ASSUMED": research["assumed_fill_unit"],
    }
    jobs = {
        "main_metrics": lambda: execution_counts(
            source, Path(main["metrics_script"]), state_dir / "main-cache.json", main_root
        ),
        "research_metrics": lambda: execution_counts(
            follower,
            follower / "launch/extension/m511_sim_metrics.py",
            state_dir / "research-cache.json",
            research_root,
        ),
        "main_assumed": lambda: assumed_report(
            owned(main["signal_validation"]["directory"], project),
            source,
            "main/trades-only",
            time.time(),
        ),
        "research_assumed": lambda: assumed_report(
            owned(research["assumed_fill_directory"], project),
            follower,
            "research/trades-only",
            time.time(),
        ),
    }
    values = {}
    if not source_matches:
        for name in ("research_metrics", "research_assumed"):
            jobs.pop(name)
            values[name] = unavailable(ValueError("RESEARCH_FOLLOWS_DIFFERENT_SOURCE_RUN"))
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        pending = {pool.submit(fn): name for name, fn in jobs.items()}
        for future in concurrent.futures.as_completed(pending):
            try:
                values[pending[future]] = future.result()
            except Exception as error:
                values[pending[future]] = unavailable(error)
    for family in ("main", "research"):
        name = family + "_assumed"
        if values[name].get("final_snapshot"):
            try:
                verify_final_watermarks(values[name], values[family + "_metrics"])
            except (KeyError, TypeError, ValueError) as error:
                values[name] = unavailable(error)
    now = time.time()
    states = {}
    problems = [
        name + ":METRICS_UNAVAILABLE"
        for name, value in values.items()
        if name in jobs and value.get("status") == "UNAVAILABLE"
    ]
    terminal = read(source / "completion.json") if (source / "completion.json").exists() else None
    follow = read(follower / "status.json") if (follower / "status.json").exists() else {}
    follow_terminal = (
        read(follower / "completion.json") if (follower / "completion.json").exists() else None
    )
    for name, unit in units.items():
        try:
            states[name] = {"unit": unit, **service(unit)}
            if states[name].get("ActiveState") != "active":
                states[name]["expected_stop"] = expected_stop(name, terminal, follow_terminal)
                if not states[name]["expected_stop"]:
                    problems.append(name + ":SERVICE_NOT_ACTIVE")
        except Exception as error:
            states[name] = {"unit": unit, **unavailable(error)}
            problems.append(name + ":SERVICE_STATE_UNAVAILABLE")
    if not source_matches:
        problems.append("RESEARCH_FOLLOWS_DIFFERENT_SOURCE_RUN")
    events = tail_events(source / "runtime.jsonl")
    heartbeat = next((r for r in reversed(events) if r.get("event") == "HEARTBEAT"), None)
    if states["M1_M4"].get("ActiveState") == "active" and (
        not heartbeat or now - heartbeat["at"] > 120
    ):
        problems.append("M1_M4_HEARTBEAT_STALE_OR_MISSING")
    progress, lag = {}, None
    if source_matches and states["M5_M11"].get("ActiveState") == "active":
        if now - follow.get("observed_at", 0) > 120:
            problems.append("RESEARCH_STATUS_STALE")
        progress, lag = following_progress(follow, values["main_metrics"], time.time(), source)
    if lag is not None and lag > 300:
        problems.append("RESEARCH_FOLLOW_LAG_OVER_300S")
    free = shutil.disk_usage(source).free
    if free < 5 * 1024**3:
        problems.append("FREE_DISK_BELOW_5_GIB")
    rows = []
    for strategy in ("M1", "M2", "M3", "M4", *RESEARCH):
        family = "research" if strategy in RESEARCH else "main"
        rows.append(make_row(strategy, values[family + "_metrics"], values[family + "_assumed"]))
    m1_profiles = [
        make_row("M1", values["main_metrics"], {}, profile)
        for profile in ("M1-virtual", "M1-live-mirror")
    ]
    report = {
        "observed_at": now,
        "beijing_time": datetime.fromtimestamp(now, ZoneInfo("Asia/Shanghai")).isoformat(),
        "bucket": int((now - OFFSET) // 3600),
        "interval_seconds": 3600,
        "services": states,
        "problems": problems,
        "heartbeat": heartbeat,
        "follow_status": follow,
        "follow_progress": progress,
        "source_terminal": terminal,
        "follow_terminal": follow_terminal,
        "following_lag_seconds": lag,
        "free_disk_bytes": free,
        "runs": {"M1_M4": str(source), "M5_M11": str(follower)},
        "rows": rows,
        "m1_profiles": m1_profiles,
        "evidence": values,
        "definitions": {
            "signals": "unique candidate per market and strategy, not multiplied by scenario",
            "submissions": "committed SUBMIT orders, reported separately for trades-only/cancel-half",
            "assumed": "full fill of admitted trades-only SUBMITs; M1 derived and labelled, others separate ASSUMED_FILL ledger",
            "modelled": "orders with positive MODELLED_FILL, partial fills count once; unresolved may overlap",
            "m11": "filter groups M4+M11, M9+M11, M10+M11; no standalone direction or pooled count",
            "scope": "current run cumulative, active transactions not yet counted; each row carries its watermark",
            "research": "recorded-data following, not a real-time latency validation; own-post samples excluded",
            "m1": "two independent execution profiles aggregated for counts only; split also supplied",
        },
    }
    report["status"] = (
        "ATTENTION"
        if problems
        else "STOPPED_AS_EXPECTED"
        if all(s.get("expected_stop") for s in states.values())
        else "RUNNING"
    )
    return report


def display(value):
    return "—" if value is None else str(value)


def render(report):
    lines = [
        f"北京时间 {report['beijing_time']}；巡检状态 {report['status']}。",
        "",
        "发单、模拟成交及未决列按 trades-only / cancel-half 排列；假设成交采用 trades-only 发单群体。",
        "",
        "| 策略 | 信号 | 发单 T/C | 假设成交 | 模拟成交 T/C | 未决 T/C | 数据 |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in report["rows"]:

        def pair(k, current=row):
            return " / ".join(display(current["execution"][q][k]) for q in SCENARIOS)

        lines.append(
            f"| {row['strategy']} | {display(row['signals'])} | {pair('submitted_orders')} | "
            f"{display(row['assumed_filled_orders'])} | {pair('filled_orders')} | "
            f"{pair('unresolved_orders')} | {row['data_status']} |"
        )
    lines.extend(
        [
            "",
            "M1 订单计数含 virtual 与 live-mirror 两个独立执行版本；假设成交数由已提交订单推导，没有独立假设成交账本。",
            "M11 分母策略显示，不能相加作独立策略；PARTIAL 的数字仅覆盖已纳入样本，排除/缺失部分不能按零处理。",
            "仅本轮已提交审计；未决订单可能已有部分成交，不能解释为确定未成交。",
        ]
    )
    for row in report.get("m1_profiles", []):

        def split(k, current=row):
            return " / ".join(display(current["execution"][q][k]) for q in SCENARIOS)

        lines.append(
            f"{row['profile']}: 发单 {split('submitted_orders')}，"
            f"假设成交 {display(row['assumed_filled_orders'])}，模拟成交 {split('filled_orders')}，"
            f"未决 {split('unresolved_orders')}（T/C）。"
        )
    for name, state in report["services"].items():
        lines.append(
            f"{name}: {state.get('ActiveState', 'UNAVAILABLE')}/{state.get('SubState', '—')} PID {state.get('MainPID', '—')}"
        )
    for family in ("main", "research"):
        data = report["evidence"][family + "_assumed"]
        excluded = data.get("excluded_research_samples", data.get("excluded_invalid_markets", []))
        lines.append(
            f"{family}: 纳入 {data.get('counted_markets', '—')} 场，排除 {len(excluded)} 场；运行 {report['runs']['M1_M4' if family == 'main' else 'M5_M11']}"
        )
    lag = report["following_lag_seconds"]
    lag_text = "—" if lag is None else f"{lag:.2f} 秒"
    lines.append(f"跟随延迟 {lag_text}，磁盘可用 {report['free_disk_bytes'] / 1024**3:.2f} GiB。")
    waiting = [
        m
        for m, row in report.get("follow_progress", {}).items()
        if row["stage"].startswith(("WAITING_", "AT_COMMITTED_"))
    ]
    if waiting:
        lines.append(
            f"另有 {len(waiting)} 个结束市场已追到可读水位，等待录制尾部落盘或封口，不计作处理积压。"
        )
    if report["problems"]:
        lines.append("需关注：" + "、".join(report["problems"]))
    return "\n".join(lines) + "\n"


def wake(control, report, state_dir, runner=subprocess.run):
    path = state_dir / "agent-state.json"
    state = read(path) if path.exists() else {}
    token = f"{control['thread']}:{report['bucket']}"
    if token in state.get("notifications", []):
        return {"status": "ALREADY_QUEUED", "thread": control["thread"]}
    prompt = (state_dir / "prompt.txt").read_text()
    prompt += f"\n巡检证据：{state_dir}/reports/{report['bucket']}.json\n" + render(report)
    reply = runner(
        [
            str(Path.home() / ".local/bin/codex"),
            "queue",
            "--thread",
            control["thread"],
            "--message",
            prompt,
            "--resume",
        ],
        cwd=PROJECT,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    if reply.returncode:
        return {"status": "WAKE_FAILED", "returncode": reply.returncode}
    warning = "warning" in (reply.stdout + reply.stderr).lower()
    state.update(thread=control["thread"], last_queued_at=time.time(), resume_unconfirmed=warning)
    # The CLI queues before attempting resume; do not duplicate a successfully queued message.
    state["notifications"] = (state.get("notifications", []) + [token])[-48:]
    atomic_json(path, state)
    return {
        "status": "QUEUED_RESUME_UNCONFIRMED" if warning else "QUEUED_AND_RESUME_REQUESTED",
        "thread": control["thread"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--no-wake", action="store_true")
    args = parser.parse_args()
    args.state_dir.mkdir(parents=True, exist_ok=True)
    with (args.state_dir / "watch.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('{"status":"ALREADY_RUNNING"}')
            return
        control = read(args.state_dir / "control.json")
        if not control.get("enabled"):
            print('{"status":"DISABLED"}')
            return
        try:
            report = inspect(args.state_dir)
        except Exception as error:
            now = time.time()
            report = {
                "observed_at": now,
                "beijing_time": datetime.fromtimestamp(now, ZoneInfo("Asia/Shanghai")).isoformat(),
                "bucket": int((now - OFFSET) // 3600),
                "interval_seconds": 3600,
                "status": "ATTENTION",
                "problems": ["MONITOR_READ_FAILED"],
                "error": unavailable(error),
                "services": {},
                "rows": [
                    make_row(s, {"status": "UNAVAILABLE"}, {})
                    for s in ("M1", "M2", "M3", "M4", *RESEARCH)
                ],
                "evidence": {"main_assumed": {}, "research_assumed": {}},
                "runs": {"M1_M4": "UNAVAILABLE", "M5_M11": "UNAVAILABLE"},
                "following_lag_seconds": None,
                "free_disk_bytes": shutil.disk_usage(PROJECT).free,
            }
        atomic_json(args.state_dir / "latest.json", report)
        (args.state_dir / "latest.md").write_text(render(report))
        atomic_json(args.state_dir / "reports" / f"{report['bucket']}.json", report)
        if not args.no_wake:
            try:
                report["wakeup"] = wake(control, report, args.state_dir)
            except Exception as error:
                report["wakeup"] = {**unavailable(error), "status": "WAKE_FAILED"}
            atomic_json(args.state_dir / "latest.json", report)
            atomic_json(args.state_dir / "reports" / f"{report['bucket']}.json", report)
        print(
            json.dumps(
                {k: report[k] for k in ("observed_at", "status", "problems")}
                | {"wakeup": report.get("wakeup")},
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
