"""M4 scheduled inspection: synthetic cash, local ledgers and mocked chat delivery."""
import importlib.util
import json
import time
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from m4_helpers import identity, send

from polymaker.m4_config import M4Config
from polymaker.m4_control import atomic_json
from polymaker.m4_state import M4Ledger

spec = importlib.util.spec_from_file_location('m4_watch', Path(__file__).parents[2]/'ops/m4_live_watch.py')
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)


def configure(root, text='enabled=true\nthreshold_usdc="10"\n'):
    path = root/'config/m4-live/balance-guard.toml'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def cash(amount):
    return lambda _: {'state': 'AVAILABLE', 'balance_usdc': amount, 'observed_at': time.time()}


def runner(calls):
    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    return run


@pytest.mark.parametrize('amount,stopped', [('9.99', True), ('10', False), ('19.76', False)])
def test_watch_cash_threshold_and_baseline_isolation(tmp_path, amount, stopped):
    configure(tmp_path)
    baseline = tmp_path/'runtime/trading-control/m4-baseline-stop.json'
    atomic_json(baseline, {'reason': 'PRESERVE'})
    original = baseline.read_bytes()
    calls = []
    result = watch.cash_guard(tmp_path, fetch=cash(amount), runner=runner(calls))
    assert result['requires_confirmation'] == stopped
    assert bool(calls) == stopped
    assert all(c == ['systemctl', '--user', 'stop', 'polymaker-m4-live.service'] for c in calls)
    assert baseline.read_bytes() == original
    assert (baseline.parent/'balance-stop.json').exists() == stopped


def test_watch_disabled_protection_never_releases_old_stop(tmp_path):
    configure(tmp_path, 'enabled=false\nthreshold_usdc="1"\n')
    stop = tmp_path/'runtime/trading-control/balance-stop.json'
    atomic_json(stop, {'reason': 'OLD', 'requires_confirmation': True})
    original = stop.read_bytes()
    calls = []
    result = watch.cash_guard(tmp_path, fetch=cash('100'), runner=runner(calls))
    assert result['requires_confirmation'] and len(calls) == 1
    assert stop.read_bytes() == original


@pytest.mark.parametrize('latched', [False, True])
def test_watch_cash_failure_is_not_zero_and_retains_latch(tmp_path, latched):
    configure(tmp_path)
    stop = tmp_path/'runtime/trading-control/balance-stop.json'
    if latched:
        atomic_json(stop, {'reason': 'OLD'})
    def fail(_):
        raise TimeoutError('not a balance')
    calls = []
    result = watch.cash_guard(tmp_path, fetch=fail, runner=runner(calls))
    assert 'observation' not in result and result['read_error_type'] == 'TimeoutError'
    assert result['requires_confirmation'] == latched
    assert bool(calls) == latched and stop.exists() == latched


def test_watch_invalid_config_reports_twenty_default(tmp_path):
    configure(tmp_path, 'enabled="false"\n')
    calls = []
    result = watch.cash_guard(tmp_path, fetch=cash('19'), runner=runner(calls))
    assert result['configuration_status'] == 'INVALID_USING_DEFAULTS'
    assert result['enabled'] and result['threshold_usdc'] == '20'
    assert result['requires_confirmation'] and len(calls) == 1


@pytest.mark.parametrize('method,url', [
    ('POST', 'https://clob.polymarket.com/order'),
    ('GET', 'https://clob.polymarket.com/data/trades'),
    ('GET', 'https://data-api.polymarket.com/positions'),
    ('POST', 'https://clob.polymarket.com/auth/api-key'),
    ('GET', 'https://clob.polymarket.com/balance-allowance?asset_type=CONDITIONAL&signature_type=2'),
])
def test_watch_cash_transport_rejects_non_cash_routes(method, url):
    touched = []
    inner = httpx.MockTransport(lambda request: touched.append(request) or httpx.Response(200))
    with httpx.Client(transport=watch.CashTransport(inner)) as client, pytest.raises(ValueError, match='READ_ONLY_ROUTE'):
        client.request(method, url)
    assert not touched


def test_watch_retains_both_unknown_risks_and_never_writes_ledger(tmp_path):
    epoch = 1800000000
    root = tmp_path/'research_outputs/m4_live'
    b = M4Ledger(root/'ledgers'/f'btc-updown-5m-{epoch}.sqlite3', identity('LIVE'))
    sid = 'm4_live_synthetic'
    b.set('session_id', sid)
    b.set('settings', M4Config().model_dump(mode='json'))
    b.set('complete', True)
    send(b, 1)
    first = b.row('df1')
    assert b.retry('df1', proof={'kind': 'UNCERTAIN_TIMEOUT'}, spendable=100)
    b.prepare('df1', '0x'+'f'*64, json.loads(first['quote']))
    b.post_start('df1')
    b.outcome('df1', 'UNKNOWN')
    b.close()
    journal = root/'journal'/f'rdp_sm_synthetic_{sid}.jsonl'
    journal.parent.mkdir()
    rows = [{'kind': 'sm_m4_native_post', 'ts': epoch+20+i, 'data': {'payload': {
        'order_id': oid, 'quote': json.loads(first['quote'])}}} for i, oid in enumerate([first['order_id'], '0x'+'f'*64])]
    journal.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    path = root/'ledgers'/f'btc-updown-5m-{epoch}.sqlite3'
    original = path.read_bytes()
    result = watch.inspect(tmp_path, epoch)
    assert result['posts'] == 2 and result['uncertain_retries'] == 1
    assert Decimal(result['commitment_usdc']) == Decimal('9.94') and not result['alerts']
    assert result['fill_tracking'] == 'NOT_TRACKED'
    assert path.read_bytes() == original


def test_watch_same_thread_delivery_coalesces_and_preserves_review_cursor(tmp_path):
    output = tmp_path/'watch'
    output.mkdir()
    (tmp_path/'ops').mkdir()
    (tmp_path/'ops/m4_watch_prompt.txt').write_text('M4 {project} {output}')
    state = output/'agent-state.json'
    atomic_json(state, {'reviewed_through_epoch': 300, 'custom': 'keep'})
    report = {'balance_guard': {'requires_confirmation': False}, 'live_service': {}}
    calls = []
    first = watch.wake(tmp_path, output, 'current-thread', 600, report, runner=runner(calls))
    assert first['status'] == 'QUEUED_AND_RESUME_REQUESTED'
    assert calls[0][2:4] == ['--thread', 'current-thread'] and calls[0][-1] == '--resume'
    second = watch.wake(tmp_path, output, 'current-thread', 900, report, runner=runner(calls))
    assert second['status'] == 'COALESCED_WITH_PENDING_REVIEW' and len(calls) == 1
    saved = json.loads(state.read_text())
    assert saved['reviewed_through_epoch'] == 300 and saved['requested_through_epoch'] == 900
    assert saved['custom'] == 'keep'
    report['balance_guard'] = {'requires_confirmation': True, 'stop_fingerprint': 'new-cash-stop'}
    watch.wake(tmp_path, output, 'current-thread', 900, report, runner=runner(calls))
    assert len(calls) == 2
