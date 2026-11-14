"""Five-minute M1 audit and wakeup of the user's existing Codex chat.

Reads local submission records and authenticated cash only. Can stop the M1
live service below its configured threshold; never queries fills/positions, sends an order, resumes
trading, changes strategy policy, or repairs a ledger.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import sqlite3
import subprocess
import time
from collections import Counter
from contextlib import closing
from decimal import Decimal
from pathlib import Path

from polymaker.m1_balance_guard import THRESHOLD

ACTIVE = {'RESERVED', 'PREPARED', 'UNKNOWN', 'ACK'}


def atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
    temporary.replace(path)


def records(path):
    result = []
    with path.open() as source:
        for line in source:
            try:
                result.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # A live writer may have an incomplete final line.
    return result


def inspect_market(epoch, events, orders, retained, authorizations, *, complete, direction_limit=None):
    alerts = []

    def alert(code, detail, severity='review'):
        alerts.append({'code': code, 'severity': severity, 'detail': detail})

    signals = {row['id']: row for row in orders}
    native = [r for r in events if r['kind'] == 'sm_m1_native_post']
    responses = [r for r in events if r['kind'] == 'sm_m1_live_post']
    unknowns = [r for r in events if r['kind'] == 'sm_m1_live_post_error']
    heartbeats = [r['data']['payload'] for r in events if r['kind'] == 'sm_m1_heartbeat']
    heartbeat = heartbeats[-1] if heartbeats else {}
    all_retained = {r['opportunity_id'] for r in retained}
    commitment = sum((Decimal(r['cost']) for r in retained), Decimal(0))
    commitment += sum((Decimal(r['spent'] if r['closed'] else r['reserved'])
                       for r in orders if r['state'] in ACTIVE), Decimal(0))
    if commitment > 20:
        alert('EXPOSURE_OVER_20', str(commitment), 'bug')
    slots = sum(r['state'] in ACTIVE or r['id'] in all_retained for r in orders)
    if slots > 5:
        alert('MORE_THAN_FIVE_OPPORTUNITIES', str(slots), 'bug')
    sent_directions = Counter(r['outcome'] for r in orders if r['posts'] > 0)
    if direction_limit is not None and any(n > direction_limit for n in sent_directions.values()):
        alert('DIRECTION_OPPORTUNITY_LIMIT_EXCEEDED', dict(sent_directions), 'bug')
    for row in orders:
        count = sum(r['opportunity_id'] == row['id'] for r in retained)
        if row['uncertain_retries'] not in (0, 1) or count != row['uncertain_retries']:
            alert('UNCERTAIN_RETRY_IDENTITY_MISMATCH', row['id'], 'bug')
    for record in native:
        p = record['data']['payload']
        quote = p['quote']
        row = signals.get(p['opportunity_id'])
        price, size = Decimal(str(quote['price'])), Decimal(str(quote['size']))
        if quote['side'] != 'BUY' or price*size < 1 or size < 5 or size % Decimal('.1'):
            alert('INVALID_NATIVE_BUY', p['opportunity_id'], 'bug')
        if row is None:
            alert('POST_WITHOUT_OPPORTUNITY', p['opportunity_id'], 'bug')
        elif price != Decimal(row['price']):
            proof = [a for a in authorizations if a['opportunity_id'] == row['id']
                     and a['post_number'] == p['post_number']-1]
            if (not proof or proof[-1].get('retry_pricing') != 'BEST_BID_ON_REJECTION'
                    or price >= Decimal(row['price'])):
                alert('UNAUTHORIZED_REPRICE', p['opportunity_id'], 'bug')
        # Wall time is diagnostic only; the engine's official-time guard is authoritative.
        if record['ts']-epoch >= 241:
            alert('POST_TIME_REQUIRES_CLOCK_REVIEW', str(record['ts']-epoch))
    if sum(r['posts'] for r in orders) != len(native):
        alert('NATIVE_POST_LOG_COUNT_MISMATCH', 'Compare durable starts with journal callbacks')
    if any(r['kind'].startswith('sm_merge_') or r['kind'] == 'sm_m1_virtual_cancel_request'
           or r['kind'] == 'sm_network_request' and r['data']['payload'].get('method') == 'DELETE'
           for r in events):
        alert('UNEXPECTED_ORDER_CLEANUP', 'M1 must leave market-end actions to the platform', 'bug')
    if not complete:
        alert('PREVIOUS_MARKET_NOT_COMPLETE', str(epoch))
    skipped = Counter(r.get('reason') for r in orders if r['state'] in {'SKIPPED', 'ABORTED'})
    if skipped:
        alert('OPPORTUNITIES_NOT_SUBMITTED', dict(skipped), 'protection')
    network = Counter(r['data']['payload'].get('outcome') for r in events
                      if r['kind'] == 'sm_network_request'
                      and r['data']['payload'].get('outcome') not in {'SUCCESS', 'HTTP_REJECTION'})
    if network:
        alert('NETWORK_READ_OR_WRITE_ERRORS', dict(network), 'protection')
    if heartbeat.get('signal_failure'):
        alert('M1_SIGNAL_ENGINE_FAILED', heartbeat['signal_failure'], 'protection')
    excluded = Counter(r['data']['payload'].get('reason') for r in events
                       if r['kind'] == 'sm_m1_candidate_skipped')
    return {'epoch': epoch, 'market_end_epoch': epoch+300, 'complete': complete,
            'max_opportunities_per_direction': direction_limit,
            'signals': len(orders), 'rf': heartbeat.get('signal_counts', {}).get('RF_CONFIRMED', 0),
            'ex': heartbeat.get('signal_counts', {}).get('EX_CONFIRMED', 0),
            'native_posts': len(native), 'acks': sum(r['data']['payload']['outcome'] == 'ACK' for r in responses),
            'post_errors': len(unknowns), 'uncertain_retries': sum(r['uncertain_retries'] for r in orders),
            'commitment_usdc': str(commitment), 'states': dict(Counter(r['state'] for r in orders)),
            'skipped': dict(skipped), 'excluded_candidates': dict(excluded),
            'orders': [{k: row[k] for k in ('id', 'outcome', 'price', 'state', 'posts', 'reason')}
                       for row in orders],
            'posts': [{'wall': r['ts'], 'quote': {k: r['data']['payload']['quote'][k]
                       for k in ('outcome', 'side', 'price', 'size')},
                       'signal_to_post_ms': r['data']['payload']['signal_to_post_ms']} for r in native],
            'account_reason': heartbeat.get('account_reason'),
            'signal_failure': heartbeat.get('signal_failure'), 'fill_verification': 'NOT_TRACKED',
            'alerts': alerts}


def inspect(project, epoch):
    summaries = []
    for journal in (project/'journal').glob('rdp_sm_*_m1_live_*.jsonl'):
        try:
            with journal.open() as source:
                line = source.readline()
            # Creation precedes the first durable session append. Until that
            # newline exists this file has no published market identity; it
            # must not become a read error attached to an unrelated prior
            # market, which also prevents the cash guard identifying a wallet.
            if not line.endswith('\n'):
                continue
            first = json.loads(line)
            header = first['data']['payload']
            if (first['kind'] != 'sm_session' or header.get('execution_mode') != 'LIVE'
                    or header['slug'] != f'btc-updown-5m-{epoch}'):
                continue
            ledger = Path(header['state_path']).resolve()
            if not ledger.is_relative_to((project/'journal').resolve()):
                raise ValueError('M1_LEDGER_OUTSIDE_PROJECT_JOURNAL')
            with closing(sqlite3.connect(ledger.as_uri()+'?mode=ro', uri=True, timeout=1)) as db:
                db.row_factory = sqlite3.Row
                db.execute('BEGIN')
                orders = [dict(r) for r in db.execute('SELECT * FROM opportunities')]
                retained = [dict(r) for r in db.execute('SELECT * FROM uncertain_posts')]
                auth = [json.loads(r[0]) for r in db.execute(
                    "SELECT payload FROM events WHERE kind='m1_retry_authorized' ORDER BY seq")]
                row = db.execute("SELECT value FROM metadata WHERE key='complete'").fetchone()
                complete = bool(row and json.loads(row[0]))
            value = inspect_market(epoch, records(journal), orders, retained, auth, complete=complete,
                direction_limit=header.get('settings', {}).get('max_opportunities_per_direction'))
            value['session_id'] = first['data']['session_id']
            value['journal_path'] = str(journal)
            summaries.append(value)
        except (KeyError, ValueError, OSError, sqlite3.Error) as exc:
            summaries.append({'epoch': epoch, 'alerts': [{'code': 'MONITOR_READ_FAILED',
                'severity': 'review', 'detail': type(exc).__name__}], 'journal_path': str(journal)})
    if not summaries:
        summaries.append({'epoch': epoch, 'alerts': [{'code': 'PREVIOUS_MARKET_RECORD_MISSING',
                          'severity': 'review', 'detail': 'No LIVE session header for this market'}]})
    return summaries


def service_status():
    result = subprocess.run(['systemctl', '--user', 'show', 'polymaker-m1-live.service',
        '--property=ActiveState', '--property=SubState', '--property=NRestarts', '--property=MainPID'],
        capture_output=True, text=True, timeout=10, check=False)
    return dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)


def wake(thread, project, output, epoch, *, runner=subprocess.run, balance_guard=None):
    state_path = output/'agent-state.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    requested = state.get('requested_through_epoch', 0)
    reviewed = state.get('reviewed_through_epoch', 0)
    guard = balance_guard or {}
    stop_id = guard.get('stop_event_id')
    urgent = bool(stop_id and state.get('balance_notification_id') != stop_id)
    if epoch <= reviewed and not urgent:
        return {'status': 'ALREADY_REVIEWED', 'reviewed_through_epoch': reviewed}
    pending_fresh = time.time()-state.get('last_queued_at', 0) < 900
    if requested > reviewed and pending_fresh and not state.get('resume_unconfirmed') and not urgent:
        return {'status': 'COALESCED_WITH_PENDING_REVIEW', 'requested_through_epoch': requested}
    threshold = guard.get('threshold_usdc', str(THRESHOLD))
    balance_policy = (
        f'余额保护开关已开启，严格低于{threshold} USDC时，监控会写入 {output}/balance-stop.json 并停止 polymaker-m1-live.service。'
        if guard.get('enabled', True) else
        '余额保护自动停机开关已关闭：继续读取并报告现金余额，不因低余额新建停机标记；已有停机标记仍持续生效。')
    prompt = f'''M1 五分钟巡检（用户已授权本聊天定期检查与修复明确 BUG）：
在 {project} 阅读 {output}/latest.json 和 reports 中尚未复核的市场记录，检查上一完整五分钟场以及遗漏的巡检。
核对实盘服务、信号、准入、POST/ACK/REJECTED/UNKNOWN、一次异常重试、占款和换场。必要时读原始 M1 日志与本地账本；不要读取或恢复成交/仓位核对。
每轮检查账户现金余额（latest.json 的 balance_guard）。{balance_policy}开关及阈值以 {project}/config/m1-live/balance-guard.toml 为准（默认开启、20 USDC；配置无效时回退此默认并报告）。若停机标记文件存在或 requires_confirmation=true，必须保持实盘停机：即使余额回升、关闭保护、调整阈值、修复BUG、服务不活跃或换场，也禁止自动重启；保留原挂单及全部账本，不撤单、不清空风险。及时在本聊天通知余额、停机结果并请用户确认后续安排。只有用户明确确认恢复后，才可解除停机标记并恢复实盘。余额读取失败不能当作0或低余额，应报告不可用；已有停机标记仍持续生效。基线模拟盘不受余额保护配置影响。
明确可复现的代码 BUG 应及时修复：先保存证据与备份，运行针对性离线测试，验证通过后更新必要的 M1 服务。会导致错误发单的 BUG 可先停止新增实盘；保留原挂单与账本，禁止清空风险记录。只有确实需要的服务才重启，尽量在场次边界更新。代码或配置没有变化时不要重启。
保持用户已确认的交易规则：每笔目标至少1美元并合法取整；默认同一场同一方向最多一个独立机会，未POST的跳过或中止不占方向名额，同一机会已有重试不新增方向名额，重启不重置；外层每场5个独立机会、20美元总占款上限保留。以场次会话settings为准，旧场不追溯应用新限制。异常返回/超时合计一次额外原信号价POST，两笔可能成交风险分别保留；只有明确的下跌Post-only拒单才允许best bid重挂；T240停新单；不做账户互斥、成交核对、Merge或T300撤单。网络保护、正常拒单或无信号不是BUG，不得为提高覆盖率擅改门槛。
虚拟与实盘仍共用提交前逻辑。不要制造真实试单，不要查询或推断实际成交/PnL，不要把ACK当成交。若用户已经要求停止实盘或停止监控，优先尊重，不得擅自重启。
每轮在本聊天简短报告上一场时间、信号与提交结果、异常原因；发现BUG时及时说明证据、修复及验证进展。不必要求用户重复授权已明确允许的工作，不发送邮件或外部消息。
完成本轮审查后，原子更新 {state_path} 的 reviewed_through_epoch 为实际已经复核的最新场次epoch（只追加/更新此字段，保留requested_through_epoch等字段）。若当前轮仍在修复，先保留待审状态，后续定时记录会合并等待，避免重叠修复。
'''
    if guard:
        prompt += '\n本轮余额保护结果：'+json.dumps(guard, ensure_ascii=False)+'\n'
    try:
        result = runner(['/home/ubuntu/.local/bin/codex', 'queue', '--thread', thread,
                         '--message', prompt, '--resume'], cwd=project,
                        capture_output=True, text=True, timeout=45, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return {'status': 'WAKE_FAILED', 'error_type': type(exc).__name__}
    if result.returncode != 0:
        return {'status': 'WAKE_FAILED', 'returncode': result.returncode}
    state['requested_through_epoch'] = max(requested, epoch)
    state['last_queued_at'] = time.time()
    warning = 'warning' in (result.stdout+result.stderr).lower()
    state['resume_unconfirmed'] = warning
    if urgent and not warning:
        state['balance_notification_id'] = stop_id
    atomic_json(state_path, state)
    return {'status': 'QUEUED_RESUME_UNCONFIRMED' if warning else 'QUEUED_AND_RESUME_REQUESTED',
            'requested_through_epoch': epoch}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-dir', type=Path, default=Path('.'))
    parser.add_argument('--thread', required=True)
    parser.add_argument('--no-wake', action='store_true')
    args = parser.parse_args()
    project = args.project_dir.resolve()
    from polymaker.m4_control import legacy_entry_guard
    legacy_entry_guard(project)
    output = project/'research_outputs/m1_live_watch'
    (output/'reports').mkdir(parents=True, exist_ok=True)
    with (output/'observer.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        now = time.time()
        epoch = int(now//300)*300-300
        report = {'checked_at': now, 'previous_market_epoch': epoch,
                  'timezone': 'Asia/Shanghai', 'live_service': service_status(),
                  'markets': inspect(project, epoch)}
        from polymaker.m1_balance_guard import check_balance

        report['balance_guard'] = check_balance(project, output, report['markets'])
        report['live_service'] = service_status()
        atomic_json(output/'reports'/f'{epoch}.json', report)
        atomic_json(output/'latest.json', report)
        result = {'epoch': epoch, 'sessions': len(report['markets']),
                  'alerts': [a for m in report['markets'] for a in m.get('alerts', [])]}
        if report['live_service'].get('ActiveState') != 'active':
            result['alerts'].append({'code': 'LIVE_SERVICE_INACTIVE', 'severity': 'review',
                                     'detail': report['live_service']})
        guard = report['balance_guard']
        result['balance_guard'] = guard
        if guard.get('configuration_status') == 'INVALID_USING_DEFAULTS':
            result['alerts'].append({'code': 'M1_BALANCE_GUARD_CONFIG_INVALID',
                                     'severity': 'review', 'detail': guard})
        if guard['requires_confirmation']:
            result['alerts'].append({'code': 'M1_BALANCE_STOP_CONFIRMATION_REQUIRED',
                                     'severity': 'confirmation', 'detail': guard})
        elif guard['status'] == 'BALANCE_UNAVAILABLE':
            result['alerts'].append({'code': 'M1_BALANCE_READ_UNAVAILABLE',
                                     'severity': 'review', 'detail': guard})
        if not args.no_wake:
            result['wakeup'] = wake(args.thread, project, output, epoch, balance_guard=guard)
        atomic_json(output/'delivery.json', result)
        print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
