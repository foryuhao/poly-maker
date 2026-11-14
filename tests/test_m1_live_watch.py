"""M1 monitoring must preserve intentional retries and not mistake protection for bugs."""
import json
import time
from types import SimpleNamespace

import pytest
from ops.m1_live_watch import atomic_json, inspect_market, wake


def row(**changes):
    return {'id': 'op', 'outcome': 'YES', 'price': '.1', 'state': 'ACK', 'posts': 1,
            'uncertain_retries': 0, 'reserved': '1', 'spent': '0', 'closed': 0,
            'reason': None, **changes}


def event(kind, payload, stamp=120):
    return {'kind': kind, 'ts': stamp, 'data': {'payload': payload}}


def post(price='.1', size='10', number=1):
    return event('sm_m1_native_post', {'opportunity_id': 'op', 'post_number': number,
        'quote': {'side': 'BUY', 'outcome': 'YES', 'price': price, 'size': size},
        'signal_to_post_ms': 30})


def inspect(events, rows, retained=None, auth=None):
    return inspect_market(0, events, rows, retained or [], auth or [], complete=True)


def bugs(report):
    return {a['code'] for a in report['alerts'] if a['severity'] == 'bug'}


def test_accepted_order_is_not_claimed_as_fill():
    report = inspect([post(), event('sm_m1_live_post', {'outcome': 'ACK'})], [row()])
    assert report['acks'] == 1 and report['native_posts'] == 1
    assert report['fill_verification'] == 'NOT_TRACKED'
    assert report['commitment_usdc'] == '1'
    assert not report['alerts']


def test_intentional_uncertain_retry_keeps_two_risks_but_one_signal():
    report = inspect([post(), post(number=2)], [row(posts=2, uncertain_retries=1)],
                     [{'opportunity_id': 'op', 'cost': '1'}])
    assert report['signals'] == 1 and report['native_posts'] == 2
    assert report['uncertain_retries'] == 1 and report['commitment_usdc'] == '2'
    assert not bugs(report)


def test_direction_limit_checks_roots_not_post_attempts_and_preserves_old_markets():
    rows = [row(posts=2, uncertain_retries=1)]
    retained = [{'opportunity_id': 'op', 'cost': '1'}]
    report = inspect_market(0, [post(), post(number=2)], rows, retained, [],
                            complete=True, direction_limit=1)
    assert not bugs(report)
    two = [row(), row(id='other')]
    assert 'DIRECTION_OPPORTUNITY_LIMIT_EXCEEDED' in bugs(
        inspect_market(0, [], two, [], [], complete=True, direction_limit=1))
    assert 'DIRECTION_OPPORTUNITY_LIMIT_EXCEEDED' not in bugs(inspect([], two))
    opposite = [row(), row(id='other', outcome='NO')]
    assert 'DIRECTION_OPPORTUNITY_LIMIT_EXCEEDED' not in bugs(
        inspect_market(0, [], opposite, [], [], complete=True, direction_limit=1))


def test_network_pause_and_expired_signal_are_not_automatic_bug_authorization():
    report = inspect([event('sm_network_request', {'outcome': 'TRANSPORT_ERROR'})],
                     [row(state='SKIPPED', reason='M1_OPPORTUNITY_EXPIRED', posts=0, reserved='0')])
    assert not bugs(report)
    assert {a['severity'] for a in report['alerts']} == {'protection'}


def test_latched_signal_failure_is_reported_as_protection_without_orders():
    report = inspect([event('sm_m1_heartbeat', {
        'account_reason': None, 'signal_failure': 'M1_EVENT_RECORDING_FAILED',
        'signal_counts': {},
    })], [])
    assert report['signal_failure'] == 'M1_EVENT_RECORDING_FAILED'
    assert not bugs(report)
    assert report['signals'] == report['native_posts'] == 0
    assert report['commitment_usdc'] == '0'
    assert report['alerts'] == [{'code': 'M1_SIGNAL_ENGINE_FAILED', 'severity': 'protection',
                                  'detail': 'M1_EVENT_RECORDING_FAILED'}]


@pytest.mark.parametrize('unpublished', ['', '{"kind":"sm_session"',
                                        '{"kind":"sm_session"}'])
def test_unpublished_next_market_header_does_not_block_prior_audit_or_balance(tmp_path, unpublished):
    from ops.m1_live_watch import inspect as inspect_project

    from polymaker.m1_balance_guard import check_balance
    from polymaker.m1_live_state import M1Ledger

    directory = tmp_path/'journal'
    directory.mkdir()
    wallet, sid = '0x'+'2'*40, 'm1_live_'+'a'*32
    ledger = M1Ledger(directory/'risk.sqlite3', {'market': 'test'})
    ledger.set('complete', True)
    ledger.close()
    header = {'kind': 'sm_session', 'data': {'session_id': sid, 'payload': {
        'execution_mode': 'LIVE', 'slug': 'btc-updown-5m-0',
        'state_path': str(directory/'risk.sqlite3')}}}
    (directory/f'rdp_sm_{wallet}_{sid}.jsonl').write_text(json.dumps(header)+'\n')
    next_path = directory/f'rdp_sm_{wallet}_m1_live_{"b"*32}.jsonl'
    next_path.write_text(unpublished)
    markets = inspect_project(tmp_path, 0)
    assert len(markets) == 1 and markets[0].get('complete') is True
    output = tmp_path/'watch'
    output.mkdir()
    calls = []

    def fetch(project, identity):
        calls.append(identity)
        return {'state': 'AVAILABLE', 'wallet': identity, 'balance_usdc': '53',
                'observed_at': 1000, 'source': 'offline_balance'}

    result = check_balance(tmp_path, output, markets, fetch=fetch, clock=lambda: 1000)
    assert result['status'] == 'HEALTHY' and calls == [wallet]
    assert not result['requires_confirmation'] and not (output/'balance-stop.json').exists()
    assert next_path.read_text() == unpublished  # Reader never repairs or truncates live files.


def test_complete_corrupt_header_still_reports_read_failure(tmp_path):
    from ops.m1_live_watch import inspect as inspect_project

    (tmp_path/'journal').mkdir()
    path = tmp_path/'journal'/f'rdp_sm_0x{"2"*40}_m1_live_{"b"*32}.jsonl'
    path.write_text('{bad complete record}\n')
    markets = inspect_project(tmp_path, 0)
    assert markets[0]['alerts'][0]['code'] == 'MONITOR_READ_FAILED'
    assert markets[0]['alerts'][0]['detail'] == 'JSONDecodeError'
    assert path.read_text() == '{bad complete record}\n'


def test_only_explicit_best_bid_authorization_allows_reprice():
    events = [post('.09', '11.2', 2)]
    orders = [row(price='.1', posts=1, reserved='1.008')]
    assert bugs(inspect(events, orders)) == {'UNAUTHORIZED_REPRICE'}
    auth = [{'opportunity_id': 'op', 'post_number': 1, 'retry_pricing': 'BEST_BID_ON_REJECTION'}]
    assert not bugs(inspect(events, orders, auth=auth))


def test_missing_uncertain_risk_and_cleanup_are_flagged():
    report = inspect([post(), event('sm_network_request', {'method': 'DELETE'})],
                     [row(uncertain_retries=1)])
    assert bugs(report) == {'UNCERTAIN_RETRY_IDENTITY_MISMATCH', 'UNEXPECTED_ORDER_CLEANUP'}


def test_exposure_limit_and_invalid_order_are_bugs():
    report = inspect([post(size='4')], [row(reserved='21')])
    assert bugs(report) == {'EXPOSURE_OVER_20', 'INVALID_NATIVE_BUY'}


def test_wakeup_targets_same_chat_and_coalesces_until_agent_acknowledges(tmp_path):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout='Queued message for thread test.', stderr='')

    assert wake('test', tmp_path, tmp_path, 300, runner=run)['status'] == 'QUEUED_AND_RESUME_REQUESTED'
    assert calls[0][1:4] == ['queue', '--thread', 'test']
    assert calls[0][-1] == '--resume'
    assert 'ACK当成交' in calls[0][-2]
    assert wake('test', tmp_path, tmp_path, 600, runner=run)['status'] == 'COALESCED_WITH_PENDING_REVIEW'
    assert len(calls) == 1
    state = json.loads((tmp_path/'agent-state.json').read_text())
    atomic_json(tmp_path/'agent-state.json', {**state, 'reviewed_through_epoch': 300})
    assert wake('test', tmp_path, tmp_path, 300, runner=run)['status'] == 'ALREADY_REVIEWED'
    assert len(calls) == 1
    assert wake('test', tmp_path, tmp_path, 600, runner=run)['status'] == 'QUEUED_AND_RESUME_REQUESTED'
    assert len(calls) == 2


def test_failed_wakeup_is_not_marked_as_delivered(tmp_path):
    result = wake('test', tmp_path, tmp_path, 300,
                  runner=lambda *a, **k: SimpleNamespace(returncode=2, stdout='', stderr='unavailable'))
    assert result['status'] == 'WAKE_FAILED'
    assert not (tmp_path/'agent-state.json').exists()


def test_resume_warning_is_retried_instead_of_silently_blocking_future_checks(tmp_path):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout='Queued.', stderr='warning: resume unavailable')

    assert wake('test', tmp_path, tmp_path, 300, runner=run)['status'] == 'QUEUED_RESUME_UNCONFIRMED'
    assert wake('test', tmp_path, tmp_path, 600, runner=run)['status'] == 'QUEUED_RESUME_UNCONFIRMED'
    assert len(calls) == 2


def test_long_stale_review_does_not_suppress_wakeups_forever(tmp_path):
    atomic_json(tmp_path/'agent-state.json', {'requested_through_epoch': 300,
        'reviewed_through_epoch': 0, 'last_queued_at': 0})
    result = wake('test', tmp_path, tmp_path, 600,
        runner=lambda *a, **k: SimpleNamespace(returncode=0, stdout='Queued.', stderr=''))
    assert result['status'] == 'QUEUED_AND_RESUME_REQUESTED'


def test_balance_stop_bypasses_pending_review_and_already_reviewed_but_never_resumes_live(tmp_path):
    atomic_json(tmp_path/'agent-state.json', {'requested_through_epoch': 600,
        'reviewed_through_epoch': 600, 'last_queued_at': time.time(), 'preserve': 123})
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout='Queued.', stderr='')

    guard = {'status': 'AWAITING_CONFIRMATION', 'stop_event_id': 'stop1',
             'requires_confirmation': True, 'triggered_balance_usdc': '39'}
    result = wake('test', tmp_path, tmp_path, 300, runner=run, balance_guard=guard)
    assert result['status'] == 'QUEUED_AND_RESUME_REQUESTED'
    assert '低于20 USDC' in calls[0][-2] and '禁止自动重启' in calls[0][-2]
    state = json.loads((tmp_path/'agent-state.json').read_text())
    assert state['requested_through_epoch'] == state['reviewed_through_epoch'] == 600
    assert state['preserve'] == 123 and state['balance_notification_id'] == 'stop1'
    assert wake('test', tmp_path, tmp_path, 600, runner=run, balance_guard=guard)['status'] == 'ALREADY_REVIEWED'
    assert len(calls) == 1


def test_balance_notification_failure_remains_pending_for_retry(tmp_path):
    guard = {'stop_event_id': 'stop1', 'requires_confirmation': True}
    def fail(*args, **kwargs):
        return SimpleNamespace(returncode=1, stdout='', stderr='failed')

    assert wake('test', tmp_path, tmp_path, 300, runner=fail, balance_guard=guard)['status'] == 'WAKE_FAILED'
    assert not (tmp_path/'agent-state.json').exists()
    def warning(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout='Queued.', stderr='warning')

    wake('test', tmp_path, tmp_path, 300, runner=warning, balance_guard=guard)
    assert 'balance_notification_id' not in json.loads((tmp_path/'agent-state.json').read_text())


@pytest.mark.parametrize('enabled,threshold', [(True, '12.5'), (False, '30')])
def test_wakeup_reports_effective_guard_configuration_without_unlocking(tmp_path, enabled, threshold):
    calls = []

    def runner(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout='Queued.', stderr='')

    guard = {'enabled': enabled, 'threshold_usdc': threshold, 'requires_confirmation': True,
             'stop_event_id': 'existing-stop', 'status': 'AWAITING_CONFIRMATION'}
    wake('test', tmp_path, tmp_path, 300, runner=runner, balance_guard=guard)
    prompt = calls[0][-2]
    if enabled:
        assert '严格低于12.5 USDC' in prompt
    else:
        assert '自动停机开关已关闭' in prompt and '不因低余额新建停机标记' in prompt
        assert '严格低于20 USDC时' not in prompt
    assert '关闭保护、调整阈值' in prompt and '只有用户明确确认恢复后' in prompt
    assert '基线模拟盘不受余额保护配置影响' in prompt


def test_main_reports_invalid_guard_configuration_without_external_calls(tmp_path, monkeypatch):
    from ops import m1_live_watch

    from polymaker import m1_balance_guard

    (tmp_path/'research_outputs/m1_live_watch').mkdir(parents=True)
    monkeypatch.setattr('sys.argv', ['watch', '--project-dir', str(tmp_path), '--thread', 'test', '--no-wake'])
    monkeypatch.setattr(m1_live_watch, 'inspect', lambda *a: [])
    monkeypatch.setattr(m1_live_watch, 'service_status', lambda: {'ActiveState': 'active'})
    monkeypatch.setattr(m1_balance_guard, 'check_balance', lambda *a: {
        'enabled': True, 'threshold_usdc': '20', 'configuration_status': 'INVALID_USING_DEFAULTS',
        'requires_confirmation': False, 'status': 'BALANCE_UNAVAILABLE'})
    m1_live_watch.main()
    result = json.loads((tmp_path/'research_outputs/m1_live_watch/delivery.json').read_text())
    assert {a['code'] for a in result['alerts']} == {
        'M1_BALANCE_GUARD_CONFIG_INVALID', 'M1_BALANCE_READ_UNAVAILABLE'}
