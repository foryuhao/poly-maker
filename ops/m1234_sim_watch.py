"""Five-minute simulation health and count reports in the authorized chat."""
import argparse
import fcntl
import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path

if __package__:
    from .m1234_sim_metrics import collect
else:
    from m1234_sim_metrics import collect

INTERVAL = 300
PROJECT = Path(__file__).resolve().parents[1]


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)


def load(path):
    if path.stat().st_size > 16 * 1024**2:
        raise ValueError('MONITOR_JSON_TOO_LARGE')
    return json.loads(path.read_text())


def tail_events(path):
    if not path.exists():
        return []
    with path.open('rb') as handle:
        offset = max(0, path.stat().st_size - 512 * 1024)
        handle.seek(offset)
        if offset:
            handle.readline()
        lines = handle.read().splitlines()
    rows = []
    for line in lines:
        try:
            rows.append(json.loads(line))
        except (ValueError, UnicodeError):
            continue  # A final in-progress line is not a heartbeat.
    return rows


def assess(control, *, now=None, runner=subprocess.run, project=PROJECT):
    now = time.time() if now is None else now
    run = Path(control['run_directory']).resolve()
    if (project / 'research_outputs/m1234_sim/forward').resolve() not in run.parents:
        raise ValueError('MONITOR_RUN_OUTSIDE_SIMULATION_FORWARD')
    unit = control['unit']
    if not unit.startswith('polymaker-m1234-sim-24h-') or not unit.endswith('.service'):
        raise ValueError('MONITOR_UNIT_OUTSIDE_SIMULATION_SCOPE')
    reply = runner(['systemctl', '--user', 'show', unit,
                    '--property=ActiveState,SubState,MainPID,ExecMainStatus,Result'],
                   capture_output=True, text=True, timeout=10, check=True)
    service = dict(line.split('=', 1) for line in reply.stdout.splitlines() if '=' in line)
    plan = load(run / 'plan.json') if (run / 'plan.json').exists() else None
    if plan and (plan.get('mode') != 'SIMULATION_ONLY' or plan.get('real_order_transport') != 'ABSENT'):
        raise ValueError('MONITOR_NOT_SIMULATION_ONLY')
    events = tail_events(run / 'runtime.jsonl')
    heartbeats = [r for r in events if r.get('event') == 'HEARTBEAT']
    heartbeat = heartbeats[-1] if heartbeats else None
    starts = [r for r in events if r.get('event') == 'MARKET_START']
    started = plan['started_utc_epoch'] if plan else control['started_at']
    age = now - (heartbeat['at'] if heartbeat else started)
    terminal = load(run / 'completion.json') if (run / 'completion.json').exists() else None
    if terminal and plan and terminal.get('run_identity') != plan.get('run_identity'):
        raise ValueError('MONITOR_TERMINAL_IDENTITY_MISMATCH')
    problems = []
    stage = 'RUNNING' if heartbeat else 'STARTING'
    if service.get('ActiveState') != 'active':
        if (terminal and plan and terminal.get('status') == 'PASS'
                and terminal.get('reason') == 'PLANNED_COMPLETE'
                and terminal.get('complete_windows') == plan['target_complete_windows']
                and not terminal.get('cleanup_errors') and not terminal.get('recording_errors')
                and not terminal.get('invalid_markets')):
            stage = 'COMPLETED'
        elif terminal and terminal.get('reason') == 'MANUAL_STOP':
            stage = 'STOPPED_MANUALLY'
        else:
            problems.append('SERVICE_EXITED')
    elif terminal is None:
        grace = 420 if heartbeat is None else 120
        if age > grace and not (starts and now - starts[-1]['at'] < 120):
            problems.append('HEARTBEAT_STALE')
        if heartbeat and now - heartbeat['at'] <= 120:
            limit = plan['settings']['input_queue_frames'] if plan else 2048
            if heartbeat.get('backlog', 0) >= limit // 2:
                problems.append('INPUT_BACKLOG_HIGH')
            if heartbeat.get('rss_peak_mib', 0) >= 0.9 * plan['settings']['max_rss_mib']:
                problems.append('MEMORY_NEAR_LIMIT')
    free = shutil.disk_usage(run).free
    if (stage not in {'COMPLETED', 'STOPPED_MANUALLY'} and plan
            and free < (plan['settings']['min_free_disk_mib'] + 512) * 1024**2):
        problems.append('DISK_NEAR_LIMIT')
    if problems:
        stage = 'REPAIR_REQUIRED'
    reason = terminal.get('reason') if terminal else None
    fingerprint = hashlib.sha256(json.dumps([unit, problems, reason]).encode()).hexdigest() if problems else None
    return {'observed_at': now, 'bucket': int(now // INTERVAL), 'interval_seconds': INTERVAL,
            'stage': stage, 'problems': problems, 'incident': fingerprint,
            'run_directory': str(run), 'unit': unit, 'service': service,
            'heartbeat': heartbeat, 'heartbeat_age_seconds': age, 'free_disk_bytes': free,
            'terminal': {key: terminal.get(key) for key in
                ('status', 'reason', 'markets', 'complete_windows', 'invalid_markets', 'real_orders')}
                if terminal else None}


def wake(control, report, state_dir, *, runner=subprocess.run, now=None):
    now = time.time() if now is None else now
    path = state_dir / 'agent-state.json'
    state = load(path) if path.exists() else {}
    every_check = control.get('notify_every_check', False)
    notification = json.dumps([control['unit'], report.get('bucket'), report.get('stage'),
                               report['incident']])
    if every_check and notification in state.get('notifications', []):
        return {'status': 'CHECK_ALREADY_QUEUED', 'notification': notification}
    if (not every_check and state.get('incident') == report['incident']
            and now - state.get('queued_at', 0) < 1800):
        return {'status': 'REPAIR_ALREADY_QUEUED', 'incident': report['incident']}
    # Full per-database watermarks remain in the report file, not the queued prompt.
    brief = dict(report)
    if 'metrics' in brief:
        brief['metrics'] = {k: v for k, v in brief['metrics'].items() if k != 'sources'}
    prompt = f'''M1234 模拟器五分钟巡检汇报。用户明确要求在这个对话里每 5 分钟巡检，显示 M1、M2、M3、M4 的信号数量与模拟成交数量；服务退出时已授权及时修复并重启。
只在当前对话处理；不要创建其他对话或定时任务，不改动旧 M1 实盘、M2 study 或 dashboard 服务。
工程目录：{PROJECT}
巡检控制及证据目录：{state_dir}
本次巡检：{json.dumps(brief, ensure_ascii=False)}
先读取 control.json/latest.json 和当前服务状态。每次都向用户显示北京时间、服务状态，以及 M1–M4 累计信号数、模拟发单数、已确认模拟成交订单数；即使计数没有变化也要汇报。发单和成交列优先分 trades-only/cancel-half 两个情景；M1 包含两个执行版本。信号按候选去重，不乘情景数；成交订单有正数 MODELLED_FILL 才计数，分批成交的同一订单只计一次。unresolved_orders 非零时必须单独报告策略和各情景的未决单数，不能把这些订单描述为已确定没有成交。只统计本轮已提交审计，活动事务可能尚未落盘；指标 PARTIAL/UNAVAILABLE 时注明缺项，不能报成确定的零。完整逐库水位在 reports/{report.get('bucket')}.json。
若已换到新运行且健康，记录已恢复，避免重复重启。正常运行仅汇报并沿用现有定时器。本消息本身不是故障；统计或唤醒故障只修复巡检，不因此重启健康模拟服务。
仅在确认模拟服务仍异常时，保留失败目录与原始行情，定位并修复工程原因，完成必要定向测试后，用新目录和新运行身份重新启动 M1234 公共行情模拟。不得将多次中断拼成连续 24 小时，不扩大队列掩盖处理缺陷，不更改策略门槛或撮合经济规则，不访问真实下单接口。
维护五分钟巡检；更新 control.json 指向新运行。正常计划完成或用户手动停止不作为故障重启。
磁盘不足时先核对空间，不删除原始行情或最终交付证据；保留失败事实，不伪造运行通过。向用户简短报告原因、处理和当前状态。
修复后更新 agent-state.json 的 reviewed_incident/reviewed_at，保留其他字段。不要把未完成的修复标为完成。
'''
    result = runner(['/home/ubuntu/.local/bin/codex', 'queue', '--thread', control['thread'],
                     '--message', prompt, '--resume'], cwd=PROJECT, capture_output=True,
                    text=True, timeout=45, check=False)
    if result.returncode:
        return {'status': 'WAKE_FAILED', 'returncode': result.returncode}
    warning = 'warning' in (result.stdout + result.stderr).lower()
    state.update(incident=report['incident'], queued_at=now, resume_unconfirmed=warning,
                 thread=control['thread'])
    if every_check:
        state['notifications'] = (state.get('notifications', []) + [notification])[-16:]
    atomic_json(path, state)
    return {'status': 'QUEUED_RESUME_UNCONFIRMED' if warning else 'QUEUED_AND_RESUME_REQUESTED',
            'incident': report['incident']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path,
                        default=PROJECT/'research_outputs/m1234_sim/monitor')
    parser.add_argument('--no-wake', action='store_true')
    args = parser.parse_args()
    args.state_dir.mkdir(parents=True, exist_ok=True)
    with (args.state_dir/'watch.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({'stage': 'MONITOR_LAUNCH_OR_CHECK_IN_PROGRESS'}))
            return
        control = load(args.state_dir/'control.json')
        if not control.get('enabled', False):
            print(json.dumps({'stage': 'MONITOR_DISABLED'}))
            return
        try:
            report = assess(control)
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            now = time.time()
            report = {'stage': 'REPAIR_REQUIRED', 'problems': ['MONITOR_READ_FAILED'],
                      'error': type(exc).__name__ + ':' + str(exc), 'observed_at': now,
                      'bucket': int(now // INTERVAL), 'incident': 'monitor:' + type(exc).__name__}
        if 'run_directory' in report:
            try:
                report['metrics'] = collect(Path(report['run_directory']), args.state_dir/'metrics-cache.json')
            except (OSError, ValueError, KeyError, TypeError) as exc:
                report['metrics'] = {'status': 'UNAVAILABLE', 'error': type(exc).__name__ + ':' + str(exc)}
        # Publish evidence before resuming the chat that reads it.
        atomic_json(args.state_dir/'latest.json', report)
        atomic_json(args.state_dir/'reports'/f"{report['bucket']}.json", report)
        if (report['problems'] or control.get('notify_every_check')) and not args.no_wake:
            try:
                report['wakeup'] = wake(control, report, args.state_dir)
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                report['wakeup'] = {'status': 'WAKE_FAILED', 'error': type(exc).__name__}
        atomic_json(args.state_dir/'latest.json', report)
        atomic_json(args.state_dir/'reports'/f"{report['bucket']}.json", report)
        if (report['stage'] in {'COMPLETED', 'STOPPED_MANUALLY'} and not args.no_wake
                and report.get('wakeup', {}).get('status') != 'WAKE_FAILED'):
            control.update(enabled=False, finished_stage=report['stage'], finished_at=time.time())
            atomic_json(args.state_dir/'control.json', control)
            subprocess.run(['systemctl', '--user', 'disable', '--now', 'polymaker-m1234-sim-watch.timer'],
                           capture_output=True, text=True, timeout=10, check=True)
        print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
