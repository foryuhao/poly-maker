"""M4-only five-minute audit and wakeup of the user's existing chat.

Reads local submission state and GET-only cash. May stop live; never starts it,
submits/cancels orders, reads fills/positions, or mutates a trading ledger.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import re
import sqlite3
import subprocess
import time
import tomllib
from collections import Counter
from contextlib import closing
from decimal import Decimal
from pathlib import Path

import httpx

from polymaker.config import Config
from polymaker.m4_control import BalanceSettings, StopControl, atomic_json

LIVE = 'polymaker-m4-live.service'
ACTIVE = {'RESERVED', 'PREPARED', 'UNKNOWN', 'ACK'}


class CashTransport(httpx.BaseTransport):
    def __init__(self, inner=None):
        self.inner = inner or httpx.HTTPTransport(retries=0)

    def handle_request(self, request):
        u = request.url
        query_ok = (not u.query if u.path == '/auth/derive-api-key' else
                    dict(u.params) in ({'asset_type': 'COLLATERAL', 'signature_type': str(i)} for i in (0, 2)))
        if (request.method != 'GET' or u.scheme != 'https' or u.host != 'clob.polymarket.com'
                or u.port not in (None, 443) or u.username or u.password or not query_ok
                or u.path not in ('/auth/derive-api-key', '/balance-allowance')):
            raise ValueError('M4_CASH_READ_ONLY_ROUTE_REQUIRED')
        return self.inner.handle_request(request)

    def close(self):
        self.inner.close()


def fetch_cash(project, *, transport=None):
    from py_clob_client_v2.clob_types import ApiCreds, RequestArgs
    from py_clob_client_v2.headers.headers import create_level_1_headers, create_level_2_headers
    from py_clob_client_v2.signer import Signer

    cfg = Config.load(project/'config/m4-live')
    if cfg.wallet.chain_id != 137 or cfg.wallet.clob_host.rstrip('/') != 'https://clob.polymarket.com':
        raise ValueError('M4_CASH_CONFIG_INVALID')
    signer = Signer(cfg.secrets.pk, 137)
    kind, wallet = cfg.wallet.signature_type, cfg.secrets.browser_address.lower()
    if kind not in (0, 2) or not re.fullmatch(r'0x[0-9a-f]{40}', wallet):
        raise ValueError('M4_CASH_IDENTITY_INVALID')
    expected = signer.address()
    if kind == 2:
        from py_builder_relayer_client.builder.derive import derive
        from py_builder_relayer_client.config import get_contract_config

        expected = derive(expected, get_contract_config(137).safe_factory)
    if expected.lower() != wallet:
        raise ValueError('M4_CASH_IDENTITY_MISMATCH')
    host, route = 'https://clob.polymarket.com', '/balance-allowance'
    with httpx.Client(timeout=8, transport=CashTransport(transport), follow_redirects=False) as client:
        response = client.get(host+'/auth/derive-api-key', headers=create_level_1_headers(signer, nonce=0))
        response.raise_for_status()
        raw = response.json()
        creds = ApiCreds(api_key=raw['apiKey'], api_secret=raw['secret'], api_passphrase=raw['passphrase'])
        headers = create_level_2_headers(signer, creds, RequestArgs(method='GET', request_path=route))
        response = client.get(host+route, headers=headers,
                              params={'asset_type': 'COLLATERAL', 'signature_type': kind})
        response.raise_for_status()
        value = response.json()['balance']
        if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,78}', value):
            raise ValueError('M4_CASH_MICRO_UNITS_INVALID')
        return {'state': 'AVAILABLE', 'wallet': wallet, 'balance_usdc': str(Decimal(value)/1_000_000),
                'observed_at': time.time(), 'source': host+route}


def cash_guard(project, *, fetch=fetch_cash, runner=subprocess.run):
    control = StopControl(project, live=True)
    settings, config_status = BalanceSettings(), 'VALID'
    try:
        settings = BalanceSettings.model_validate(tomllib.loads(control.config_path.read_text(), parse_float=Decimal))
    except (ValueError, OSError):
        config_status = 'INVALID_USING_DEFAULTS'
    result = {'status': 'BALANCE_UNAVAILABLE', 'enabled': settings.enabled,
              'threshold_usdc': str(settings.threshold_usdc), 'configuration_status': config_status}
    try:
        observation = fetch(project)
        amount = Decimal(observation['balance_usdc'])
        if (observation['state'] != 'AVAILABLE' or not amount.is_finite() or amount < 0
                or not -5 <= time.time()-observation['observed_at'] <= 30):
            raise ValueError('M4_CASH_UNAVAILABLE_OR_STALE')
        result = {**control.observe_balance(amount), 'observation': observation, 'status': 'AVAILABLE'}
    except Exception as exc:
        result['read_error_type'] = type(exc).__name__  # Never expose credentials or HTTP bodies.
    result['requires_confirmation'] = control.inhibited()
    result['stop_markers'] = [p.name for p in (control.manual_path, control.balance_path, control.legacy_path) if p.exists()]
    result['stop_fingerprint'] = '|'.join(
        p.name+':'+hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (control.manual_path, control.balance_path, control.legacy_path) if p.exists())
    if control.inhibited():
        result['status'] = 'AWAITING_CONFIRMATION'
        try:
            stopped = runner(['systemctl', '--user', 'stop', LIVE], capture_output=True, text=True, timeout=65)
            result['stop_returncode'] = stopped.returncode
        except (OSError, subprocess.SubprocessError) as exc:
            result['stop_error_type'] = type(exc).__name__
    return result


def service_status(unit=LIVE):
    r = subprocess.run(['systemctl', '--user', 'show', unit, '-p', 'ActiveState', '-p', 'SubState',
                        '-p', 'NRestarts', '-p', 'MainPID'], capture_output=True, text=True, timeout=10)
    return dict(line.split('=', 1) for line in r.stdout.splitlines() if '=' in line)


def inspect(project, epoch):
    root = project/'research_outputs/m4_live'
    path = root/'ledgers'/f'btc-updown-5m-{epoch}.sqlite3'
    if not path.exists():
        return {'epoch': epoch, 'record_status': 'NO_SESSION', 'alerts': ['CHECK_SERVICE_START_OR_STOP_TIME']}
    if path.resolve() != path or path.is_symlink():
        raise ValueError('M4_LEDGER_PATH_INVALID')
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=2)) as db:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN')
        meta = {r['key']: json.loads(r['value']) for r in db.execute('SELECT key,value FROM metadata')}
        ident = meta['identity']
        if ident['strategy'] != 'm4_frequency_v2_native_v1' or ident['mode'] != 'LIVE':
            raise ValueError('M4_LIVE_IDENTITY_REQUIRED')
        orders = [dict(r) for r in db.execute('SELECT id,outcome,price,quantity,target,reserved,state,reason,posts,uncertain_retries FROM opportunities')]
        retained = [dict(r) for r in db.execute('SELECT * FROM uncertain_posts')]
        retries = [json.loads(r[0]) for r in db.execute("SELECT payload FROM events WHERE kind='m4_retry_authorized' ORDER BY seq")]
    journals = list((root/'journal').glob('*_'+meta['session_id']+'.jsonl'))
    if len(journals) != 1 or journals[0].resolve() != journals[0]:
        raise ValueError('M4_JOURNAL_IDENTITY_UNAVAILABLE')
    posts, responses, transitions, errors = [], [], [], []
    heartbeat = {}
    for line in journals[0].open():
        if not line.endswith('\n'):
            continue
        row = json.loads(line)
        kind, payload = row['kind'], row['data']['payload']
        if kind == 'sm_m4_native_post':
            posts.append({'wall': row['ts'], **payload})
        elif kind == 'sm_m4_live_post':
            responses.append(payload)
        elif kind == 'sm_m4_state':
            transitions.append({'wall': row['ts'], **payload})
        elif kind == 'sm_m4_heartbeat':
            heartbeat = payload
        elif kind == 'sm_m4_live_post_error':
            errors.append(payload)
    commitment = sum((Decimal(r['reserved']) for r in orders if r['state'] in ACTIVE), Decimal(0))
    commitment += sum((Decimal(r['cost']) for r in retained), Decimal(0))
    alerts = []
    if commitment > Decimal(meta['settings']['session_budget_usdc']):
        alerts.append('M4_MARKET_RISK_OVER_LIMIT')
    if any(n > 1 for n in Counter(r['outcome'] for r in orders if r['posts']).values()):
        alerts.append('M4_DIRECTION_POSTED_OPPORTUNITIES_OVER_LIMIT')
    if any(r['uncertain_retries'] not in (0, 1) for r in orders):
        alerts.append('M4_UNCERTAIN_RETRY_OVER_LIMIT')
    if sum(r['posts'] for r in orders) != len(posts):
        alerts.append('M4_NATIVE_POST_LOG_COUNT_MISMATCH')
    if len({r['order_id'] for r in posts}) != len(posts):
        alerts.append('M4_REPEATED_SIGNED_ORDER_ID')
    for post in posts:
        q = post['quote']
        p, size = Decimal(str(q['price'])), Decimal(str(q['size']))
        if (q['side'] != 'BUY' or not Decimal('.60') <= p < 1 or size < 5
                or size % Decimal('.1') or p*size > 5):
            alerts.append('M4_NATIVE_QUOTE_RULE_VIOLATION')
        if post['wall']-epoch >= 241:
            alerts.append('M4_POST_TIME_REQUIRES_OFFICIAL_CLOCK_REVIEW')
    return {'epoch': epoch, 'record_status': 'AVAILABLE', 'complete': bool(meta.get('complete')),
            'session_id': meta['session_id'], 'settings': meta['settings'], 'ledger_path': str(path),
            'journal_path': str(journals[0]), 'signals': len(orders), 'posts': len(posts),
            'responses': dict(Counter(r['outcome'] for r in responses)), 'post_errors': errors,
            'uncertain_retries': sum(r['uncertain_retries'] for r in orders), 'commitment_usdc': str(commitment),
            'orders': orders, 'uncertain_posts': retained, 'retry_authorizations': retries, 'native_posts': posts,
            'transitions': transitions, 'last_heartbeat': heartbeat, 'alerts': sorted(set(alerts)),
            'fill_tracking': 'NOT_TRACKED'}


def wake(project, output, thread, epoch, report, *, runner=subprocess.run):
    # Only the monitor/agent review cursor is locked; never an account mutex.
    with (output/'agent-state.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = output/'agent-state.json'
        state = json.loads(path.read_text()) if path.exists() else {}
        pending = state.get('requested_through_epoch', 0) > state.get('reviewed_through_epoch', 0)
        state['requested_through_epoch'] = max(state.get('requested_through_epoch', 0), epoch)
        fingerprint = report['balance_guard'].get('stop_fingerprint')
        urgent = report['balance_guard']['requires_confirmation'] and fingerprint != state.get('stop_notified')
        if pending and time.time()-state.get('last_queued_at', 0) < 900 and not state.get('resume_unconfirmed') and not urgent:
            atomic_json(path, state)
            return {'status': 'COALESCED_WITH_PENDING_REVIEW'}
        if epoch <= state.get('reviewed_through_epoch', 0) and not urgent:
            return {'status': 'ALREADY_REVIEWED'}
        prompt = (project/'ops/m4_watch_prompt.txt').read_text().replace('{project}', str(project)).replace('{output}', str(output))
        prompt += '\n本轮摘要：'+json.dumps({'previous_market_epoch': epoch, 'balance_guard': report['balance_guard'],
                                           'live_service': report['live_service']}, ensure_ascii=False)
        try:
            r = runner(['/home/ubuntu/.local/bin/codex', 'queue', '--thread', thread, '--message', prompt, '--resume'],
                       cwd=project, capture_output=True, text=True, timeout=45)
        except (OSError, subprocess.SubprocessError) as exc:
            return {'status': 'WAKE_FAILED', 'error_type': type(exc).__name__}
        warning = 'warning' in (r.stdout+r.stderr).lower()
        if r.returncode:
            return {'status': 'WAKE_FAILED', 'returncode': r.returncode}
        state.update(last_queued_at=time.time(), resume_unconfirmed=warning, thread_id=thread)
        if urgent and not warning:
            state['stop_notified'] = fingerprint
        if not report['balance_guard']['requires_confirmation']:
            state['stop_notified'] = None
        atomic_json(path, state)
        return {'status': 'QUEUED_RESUME_UNCONFIRMED' if warning else 'QUEUED_AND_RESUME_REQUESTED', 'thread': thread}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-dir', type=Path, required=True)
    parser.add_argument('--thread', required=True)
    parser.add_argument('--no-wake', action='store_true')
    args = parser.parse_args()
    project = args.project_dir.resolve()
    output = project/'research_outputs/m4_live_watch'
    (output/'reports').mkdir(parents=True, exist_ok=True)
    with (output/'observer.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        epoch = int(time.time()//300)*300-300
        # Cash protection must run even if journal parsing fails.
        guard = cash_guard(project)
        state_path = output/'agent-state.json'
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        epochs = {epoch}
        for path in (project/'research_outputs/m4_live/ledgers').glob('btc-updown-5m-*.sqlite3'):
            e = int(path.stem.rsplit('-', 1)[1])
            if state.get('reviewed_through_epoch', 0) < e <= epoch:
                epochs.add(e)
        reports = []
        for e in sorted(epochs):
            try:
                market = inspect(project, e)
            except (ValueError, KeyError, TypeError, ArithmeticError, OSError, sqlite3.Error) as exc:
                market = {'epoch': e, 'record_status': 'READ_ERROR', 'error_type': type(exc).__name__}
            atomic_json(output/'reports'/f'{e}.json', market)
            reports.append(market)
        report = {'checked_at': time.time(), 'previous_market_epoch': epoch, 'timezone': 'Asia/Shanghai',
                  'markets': reports, 'balance_guard': guard, 'live_service': service_status(),
                  'baseline_service': service_status('polymaker-m4-baseline.service')}
        atomic_json(output/'latest.json', report)
        delivery = {'status': 'NO_WAKE'} if args.no_wake else wake(project, output, args.thread, epoch, report)
        atomic_json(output/'delivery.json', delivery)
        print(json.dumps({'previous_market_epoch': epoch, 'balance_guard': guard, 'delivery': delivery}, ensure_ascii=False))


if __name__ == '__main__':
    main()
