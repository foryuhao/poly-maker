"""M6-only hourly read-only audit, PnL report and authorized same-chat delivery."""
import argparse
import fcntl
import json
import sqlite3
import subprocess
import time
from collections import Counter
from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from polymaker.m6_baseline.control import atomic_json

PROJECT = Path(__file__).resolve().parents[1]
STATE = PROJECT / 'research_outputs/m6_hourly_watch'
UNITS = ('polymaker-m6-baseline.service', 'polymaker-m6-chain-validation.service')


def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def service(unit):
    result = subprocess.run(['systemctl', '--user', 'show', unit, '-p', 'ActiveState',
        '-p', 'MainPID', '-p', 'NRestarts', '-p', 'MemoryCurrent'],
        text=True, capture_output=True, timeout=10, check=True)
    return dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)


def market(path, root):
    if path.is_symlink() or path.resolve() != path:
        raise ValueError('M6_WATCH_LEDGER_PATH_INVALID')
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=2)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        meta = {r['key']: json.loads(r['value']) for r in db.execute('SELECT * FROM metadata')}
        if (meta.get('identity', {}).get('strategy') != 'm6_withdrawal_supported_v1_1'
                or meta['identity'].get('mode') != 'VIRTUAL'):
            raise ValueError('M6_WATCH_FOREIGN_LEDGER')
        opportunities = [dict(r) for r in db.execute('SELECT * FROM opportunities ORDER BY rowid')]
        facts = {kind: [json.loads(r['payload']) for r in db.execute(
            'SELECT payload FROM facts WHERE kind=?', (kind,))]
            for kind in ('virtual_post', 'virtual_fill', 'virtual_observation', 'virtual_cancel')}
    journals = list((root/'journal').glob('*_'+meta['session_id']+'.jsonl'))
    counts, diagnostics, skips = Counter(), Counter(), []
    malformed = 0
    for journal in journals:
        # A concurrent append may leave a partial last line; it is not corruption.
        with journal.open('rb') as stream:
            lines = stream.read(journal.stat().st_size).splitlines(keepends=True)
        for line in lines:
            if not line.endswith(b'\n'):
                continue
            try:
                record = json.loads(line)
                kind, payload = record['kind'], record['data']['payload']
                if kind == 'sm_m6_signal':
                    counts[payload['event']] += 1
                    evidence = payload.get('evidence', {})
                    if evidence.get('event') == 'QUALITY_UNAVAILABLE':
                        diagnostics[evidence.get('payload', {}).get('reason', 'UNKNOWN')] += 1
                elif kind == 'sm_m6_candidate_skipped':
                    skips.append(payload)
                elif 'error' in kind:
                    diagnostics[kind] += 1
            except (ValueError, KeyError, TypeError):
                malformed += 1
    return {'slug': path.stem, 'settings': meta.get('settings'),
        'complete': meta.get('complete', False), 'session_id': meta['session_id'],
        'signal_counts': dict(counts), 'skipped': skips, 'diagnostics': dict(diagnostics),
        'malformed_complete_lines': malformed, 'journal_count': len(journals),
        'opportunities': opportunities, 'facts': facts,
        'modeled_settlement': meta.get('modeled_settlement')}


def generate(project, state, *, plots=True):
    now = time.time()
    out = state/'reports'/datetime.fromtimestamp(now, ZoneInfo('Asia/Shanghai')).strftime('%Y%m%dT%H%M%S')
    out.mkdir(parents=True, exist_ok=False)
    root = project/'research_outputs/m6_virtual'
    completion = project/'research_outputs/analysis/m6_baseline_development/full-chain-20261114/completion.json'
    report = {'at_bj': datetime.fromtimestamp(now, ZoneInfo('Asia/Shanghai')).isoformat(),
        'observed_at': now, 'bucket': int((now-20)//3600), 'directory': str(out),
        'status': 'OK', 'problems': [], 'services': {u: service(u) for u in UNITS},
        'chain_completion': read(completion), 'latest': read(root/'latest.json', {}),
        'resolution_status': read(root/'resolution-status.json', {}), 'markets': []}
    if report['services'][UNITS[0]]['ActiveState'] != 'active' and not report['chain_completion']:
        report['problems'].append('M6_BASELINE_INACTIVE_BEFORE_CHAIN_COMPLETE')
    heartbeat = root/'latest.json'
    report['heartbeat_age_sec'] = now-heartbeat.stat().st_mtime if heartbeat.exists() else None
    if report['services'][UNITS[0]]['ActiveState'] == 'active' and (
            report['heartbeat_age_sec'] is None or report['heartbeat_age_sec'] > 90):
        report['problems'].append('M6_HEARTBEAT_STALE')
    if report['latest'].get('signal_failure'):
        report['problems'].append('M6_SIGNAL_FAILURE')
    for path in sorted((root/'ledgers').glob('*.sqlite3')):
        try:
            report['markets'].append(market(path, root))
        except (OSError, ValueError, sqlite3.Error) as exc:
            report['problems'].append({'market': path.stem, 'error_type': type(exc).__name__})
    report['totals'] = {k: sum(len(m['facts'][kind]) for m in report['markets']) for k, kind in
        [('posts', 'virtual_post'), ('fills', 'virtual_fill'), ('observations', 'virtual_observation'), ('cancels', 'virtual_cancel')]}
    report['totals'].update(candidates=sum(m['signal_counts'].get('CANDIDATE', 0) for m in report['markets']),
        opportunities=sum(len(m['opportunities']) for m in report['markets']),
        officially_settled_markets=sum(bool(m['modeled_settlement']) for m in report['markets']))
    report['storage'] = {name: sum(p.stat().st_size for p in (root/name).glob('*') if p.is_file())
                         for name in ('journal', 'journal-archive', 'ledgers', 'modeled-settlements')}
    import shutil
    report['disk_free_bytes'] = shutil.disk_usage(project).free
    if report['disk_free_bytes'] < 5*1024**3:
        report['problems'].append('DISK_FREE_BELOW_5_GIB')
    if plots:
        commands = [
            ('snapshot', [str(project/'.venv/bin/python'), '-B', str(project/'ops/m6_pnl_snapshot.py'), '--project-dir', str(project), '--output', str(out)]),
            ('render', [str(project/'.venv-m4-report/bin/python'), '-B', str(project/'ops/m6_pnl_render.py'), '--project-dir', str(project), '--directory', str(out)])]
        for name, cmd in commands:
            try:
                reply = subprocess.run(cmd, cwd=project, text=True, capture_output=True, timeout=180)
                (out/(name+'.log')).write_text(reply.stdout+reply.stderr)
                reply.check_returncode()
            except (OSError, subprocess.SubprocessError) as exc:
                report['problems'].append({'step': name, 'error_type': type(exc).__name__})
                break
        png = out/'m6-pnl-waterfall-dual-cumulative-drawdown.png'
        if png.exists():
            report['png'] = str(png)
        report['summary'] = read(out/'summary.json')
    if report['problems']:
        report['status'] = 'ATTENTION'
    return report


def queue(state, control, report, runner=subprocess.run):
    with (state/'agent-state.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        saved = read(state/'agent-state.json', {})
        token = f"{control['thread']}:{report['bucket']}"
        if token in saved.get('notifications', []):
            return {'status': 'ALREADY_QUEUED'}
        prompt = (PROJECT/'ops/m6_hourly_prompt.txt').read_text()
        prompt += f"\n本轮记录：{report['directory']}/report.json\n最新索引：{state}/latest.json\n"
        reply = runner([str(Path.home()/'.local/bin/codex'), 'queue', '--thread', control['thread'],
            '--message', prompt, '--resume'], cwd=PROJECT, capture_output=True, text=True, timeout=45)
        if reply.returncode:
            return {'status': 'WAKE_FAILED', 'returncode': reply.returncode}
        warning = 'warning' in (reply.stdout+reply.stderr).lower()
        saved.update(thread=control['thread'], last_queued_at=time.time(), resume_unconfirmed=warning)
        saved['notifications'] = (saved.get('notifications', [])+[token])[-48:]
        atomic_json(state/'agent-state.json', saved)
        return {'status': 'QUEUED_RESUME_UNCONFIRMED' if warning else 'QUEUED_AND_RESUME_REQUESTED'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, default=STATE)
    parser.add_argument('--no-wake', action='store_true')
    args = parser.parse_args()
    state = args.state_dir.resolve()
    state.mkdir(parents=True, exist_ok=True)
    with (state/'watch.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('ALREADY_RUNNING')
            return
        control = read(state/'control.json', {})
        if not control.get('enabled'):
            print('DISABLED')
            return
        report = generate(PROJECT, state)
        atomic_json(Path(report['directory'])/'report.json', report)
        atomic_json(state/'latest.json', report)
        control = read(state/'control.json', {})
        if not args.no_wake and control.get('enabled'):
            try:
                report['wakeup'] = queue(state, control, report)
            except (OSError, subprocess.SubprocessError) as exc:
                report['wakeup'] = {'status': 'WAKE_FAILED', 'error_type': type(exc).__name__}
            atomic_json(Path(report['directory'])/'report.json', report)
            atomic_json(state/'latest.json', report)
        print(json.dumps({'status': report['status'], 'directory': report['directory'],
                          'totals': report['totals'], 'wakeup': report.get('wakeup')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
