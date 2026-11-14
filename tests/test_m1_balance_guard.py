"""Cash threshold and durable stop behavior; all HTTP/service calls are fake."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from polymaker.m1_balance_guard import CONFIG_PATH, BalanceGuardSettings, check_balance

WALLET = '0x'+'2'*40
SID = 'm1_live_'+'a'*32


@pytest.fixture
def setup_guard(tmp_path):
    (tmp_path/'journal').mkdir()
    journal = tmp_path/'journal'/f'rdp_sm_{WALLET}_{SID}.jsonl'
    journal.write_text(json.dumps({'kind': 'sm_session', 'data': {
        'session_id': SID, 'payload': {'execution_mode': 'LIVE'}}})+'\n')
    output = tmp_path/'watch'
    output.mkdir()
    return tmp_path, output, [{'journal_path': str(journal)}]


def observation(amount, **changes):
    return {'state': 'AVAILABLE', 'wallet': WALLET, 'balance_usdc': amount,
            'observed_at': 1000, 'source': 'https://clob.polymarket.com/balance-allowance', **changes}


def run_guard(setup_guard, payload=None, *, fetch=None, runner=None):
    return check_balance(*setup_guard, fetch=fetch or (lambda *a: payload),
                         runner=runner or (lambda *a, **k: SimpleNamespace(returncode=0)),
                         clock=lambda: 1000)


def configure(setup_guard, content):
    path = setup_guard[0]/CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def test_missing_file_preserves_enabled_twenty_default(setup_guard):
    result = run_guard(setup_guard, observation('20'))
    assert result['enabled'] is True and result['threshold_usdc'] == '20'
    assert result['status'] == 'HEALTHY'


@pytest.mark.parametrize('threshold,amount,stop', [
    ('12.5', '12.499999', True), ('12.5', '12.5', False),
    ('12.5', '12.500001', False), ('30', '21', True), ('0.01', '0.01', False)])
def test_configured_threshold_uses_strict_decimal_comparison(setup_guard, threshold, amount, stop):
    configure(setup_guard, f'enabled = true\nthreshold_usdc = "{threshold}"\n')
    result = run_guard(setup_guard, observation(amount))
    assert result['threshold_usdc'] == threshold
    assert result['requires_confirmation'] is stop
    if stop:
        marker = json.loads((setup_guard[1]/'balance-stop.json').read_text())
        assert marker['threshold_usdc'] == threshold


def test_disabled_guard_still_reads_cash_without_new_stop(setup_guard):
    configure(setup_guard, 'enabled = false\nthreshold_usdc = "30"\n')
    calls = []

    def fetch(project, wallet):
        calls.append(('balance', wallet))
        return observation('0')

    result = run_guard(setup_guard, fetch=fetch, runner=lambda *a, **k: calls.append(a))
    assert calls == [('balance', WALLET)]
    assert result['enabled'] is False and result['status'] == 'DISABLED'
    assert result['observation']['balance_usdc'] == '0'
    assert not result['requires_confirmation'] and not (setup_guard[1]/'balance-stop.json').exists()


@pytest.mark.parametrize('configuration', ['enabled = false\nthreshold_usdc = "1"\n',
    'enabled = true\nthreshold_usdc = "1"\n', 'enabled = false\n',
    'enabled = "invalid"\n', 'threshold_usdc = "NaN"\n'])
@pytest.mark.parametrize('read_fails', [False, True])
def test_config_edits_never_release_existing_stop_even_with_read_failure(
        setup_guard, configuration, read_fails):
    first = run_guard(setup_guard, observation('19'))
    marker = setup_guard[1]/'balance-stop.json'
    original = marker.read_bytes()
    configure(setup_guard, configuration)
    calls = []

    def fetch(*args):
        if read_fails:
            raise TimeoutError('private')
        return observation('100')

    def stop(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0)

    result = run_guard(setup_guard, fetch=fetch, runner=stop)
    assert result['status'] == 'AWAITING_CONFIRMATION' and result['requires_confirmation']
    assert result['stop_event_id'] == first['stop_event_id'] and result['stop_requested']
    assert calls == [['systemctl', '--user', 'stop', '--no-block', 'polymaker-m1-live.service']]
    assert marker.read_bytes() == original


def test_each_check_reloads_configuration_without_service_restart(setup_guard):
    configure(setup_guard, 'enabled = false\n')
    assert run_guard(setup_guard, observation('25'))['status'] == 'DISABLED'
    configure(setup_guard, 'enabled = true\nthreshold_usdc = "30"\n')
    result = run_guard(setup_guard, observation('25'))
    assert result['requires_confirmation'] and result['triggered_balance_usdc'] == '25'
    configure(setup_guard, 'enabled = false\nthreshold_usdc = "1"\n')
    assert run_guard(setup_guard, observation('100'))['requires_confirmation']


@pytest.mark.parametrize('content', ['enabled = "false"', 'enabled = 0',
    'enabled = false\nthreshold_usdc = "NaN"', 'threshold_usdc = inf',
    'threshold_usdc = 0', 'threshold_usdc = -1', 'threshold_usdc = true',
    'enabled = false\nunknown = "private"', 'broken [ TOML'])
def test_invalid_config_reports_and_uses_safe_defaults(setup_guard, content):
    configure(setup_guard, content)
    result = run_guard(setup_guard, observation('19'))
    assert result['configuration_status'] == 'INVALID_USING_DEFAULTS'
    assert result['enabled'] is True and result['threshold_usdc'] == '20'
    assert result['requires_confirmation'] and result['stop_requested']
    assert 'private' not in json.dumps(result)


def test_disabled_read_failure_does_not_invent_a_balance_or_stop(setup_guard):
    configure(setup_guard, 'enabled = false\n')

    def fail(*args):
        raise TimeoutError('private')

    result = run_guard(setup_guard, fetch=fail)
    assert result['status'] == 'BALANCE_UNAVAILABLE' and result['enabled'] is False
    assert 'observation' not in result and not result['requires_confirmation']


def test_unreadable_config_falls_back_and_preserves_existing_stop(setup_guard, monkeypatch):
    first = run_guard(setup_guard, observation('19'))
    marker = setup_guard[1]/'balance-stop.json'
    original = marker.read_bytes()

    def fail(path):
        raise PermissionError('private configuration detail')

    monkeypatch.setattr(BalanceGuardSettings, 'load', fail)
    result = run_guard(setup_guard, observation('100'))
    assert result['configuration_status'] == 'INVALID_USING_DEFAULTS'
    assert result['config_error_type'] == 'PermissionError' and result['enabled'] is True
    assert result['stop_event_id'] == first['stop_event_id'] and result['stop_requested']
    assert marker.read_bytes() == original and 'private' not in json.dumps(result)


def test_toml_numeric_threshold_keeps_decimal_precision(setup_guard):
    configure(setup_guard, 'threshold_usdc = 20.000000000000000001\n')
    result = run_guard(setup_guard, observation('20.000000000000000000'))
    assert result['threshold_usdc'] == '20.000000000000000001'
    assert result['enabled'] is True and result['requires_confirmation']


def test_corrupt_marker_is_still_authoritative_when_disabled(setup_guard):
    marker = setup_guard[1]/'balance-stop.json'
    marker.write_text('corrupt')
    configure(setup_guard, 'enabled = false\n')
    result = run_guard(setup_guard, observation('100'))
    assert result['requires_confirmation'] and result['stop_requested']
    assert marker.read_text() == 'corrupt'


def test_guard_config_is_separate_from_live_and_baseline_settings(setup_guard):
    from polymaker.m1_live_config import M1LiveConfig
    from polymaker.m1_virtual import baseline_settings

    project = setup_guard[0]
    before_live = M1LiveConfig.load(project/'config/m1-live').fingerprint
    before_baseline = baseline_settings(M1LiveConfig.load(project/'config'))
    configure(setup_guard, 'enabled = false\nthreshold_usdc = "123.45"\n')
    assert M1LiveConfig.load(project/'config/m1-live').fingerprint == before_live
    assert baseline_settings(M1LiveConfig.load(project/'config')) == before_baseline


@pytest.mark.parametrize('amount,stop', [('19.999999', True), ('0', True), ('20', False),
                                       ('20.000001', False), ('39.999999', False),
                                       ('40', False), ('68.686227', False)])
def test_strict_threshold_stops_only_live_and_latch_precedes_signal(setup_guard, amount, stop):
    calls = []
    project, output, _ = setup_guard
    ledger = project/'journal'/'risk.sqlite3'
    ledger.write_bytes(b'preserve risk and resting orders')

    def runner(args, **kwargs):
        assert (output/'balance-stop.json').exists()
        calls.append(args)
        return SimpleNamespace(returncode=0)

    result = run_guard(setup_guard, observation(amount), runner=runner)
    assert result['threshold_usdc'] == '20'
    assert result['requires_confirmation'] is stop
    assert (output/'balance-stop.json').exists() is stop
    assert calls == ([['systemctl', '--user', 'stop', '--no-block', 'polymaker-m1-live.service']]
                     if stop else [])
    assert result['status'] == ('AWAITING_CONFIRMATION' if stop else 'HEALTHY')
    if stop:
        marker = json.loads((output/'balance-stop.json').read_text())
        assert marker['threshold_usdc'] == '20'
        assert marker['reason'] == 'M1_BALANCE_BELOW_20'
    assert ledger.read_bytes() == b'preserve risk and resting orders'


def test_balance_recovery_does_not_clear_latch_and_missing_market_keeps_checking(setup_guard):
    first = run_guard(setup_guard, observation('19'))
    project, output, _ = setup_guard
    original = (output/'balance-stop.json').read_bytes()
    later = run_guard((project, output, []), observation('100'))
    assert later['status'] == 'AWAITING_CONFIRMATION' and later['stop_requested']
    assert later['stop_event_id'] == first['stop_event_id']
    assert (output/'balance-stop.json').read_bytes() == original


def test_transient_read_failure_does_not_invent_zero_or_stop(setup_guard):
    def fail(*args):
        raise RuntimeError('private response must never be logged')

    result = run_guard(setup_guard, fetch=fail)
    assert result['status'] == 'BALANCE_UNAVAILABLE' and not result['requires_confirmation']
    assert 'observation' not in result and 'private' not in json.dumps(result)
    assert not (setup_guard[1]/'balance-stop.json').exists()


def test_read_failure_keeps_existing_stop_and_stop_failure_is_retried(setup_guard):
    calls = []
    first = run_guard(setup_guard, observation('19'),
                      runner=lambda *a, **k: SimpleNamespace(returncode=1))
    assert not first['stop_requested'] and first['requires_confirmation']

    def fail(*args):
        raise TimeoutError('sensitive details')

    def stop(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0)

    retry = run_guard(setup_guard, fetch=fail, runner=stop)
    assert retry['stop_requested'] and retry['stop_event_id'] == first['stop_event_id']
    assert retry['status'] == 'AWAITING_CONFIRMATION' and len(calls) == 1


def test_corrupt_stop_marker_still_stops_and_never_unlocks(setup_guard):
    path = setup_guard[1]/'balance-stop.json'
    path.write_text('interrupted legacy marker')
    result = run_guard(setup_guard, observation('100'))
    assert result['requires_confirmation'] and result['stop_requested']
    assert result['triggered_balance_usdc'] is None
    assert path.read_text() == 'interrupted legacy marker'


def test_lower_threshold_does_not_release_a_legacy_40_stop(setup_guard):
    path = setup_guard[1]/'balance-stop.json'
    path.write_text(json.dumps({'id': 'legacy-40-stop', 'balance_usdc': '39',
        'threshold_usdc': '40', 'reason': 'M1_BALANCE_BELOW_40', 'requires_confirmation': True}))
    original = path.read_bytes()
    result = run_guard(setup_guard, observation('39'))
    assert result['threshold_usdc'] == '20'
    assert result['requires_confirmation'] and result['stop_requested']
    assert result['stop_event_id'] == 'legacy-40-stop'
    assert path.read_bytes() == original


@pytest.mark.parametrize('payload', [observation('NaN'), observation('Infinity'), observation('-1'),
    observation('10', observed_at=950), observation('10', observed_at=1100),
    observation('10', wallet='0x'+'3'*40), observation('10', state='UNAVAILABLE')])
def test_invalid_stale_or_wrong_wallet_balance_never_triggers_false_stop(setup_guard, payload):
    result = run_guard(setup_guard, payload)
    assert result['status'] == 'BALANCE_UNAVAILABLE' and not result['requires_confirmation']
    assert not (setup_guard[1]/'balance-stop.json').exists()


def test_non_live_identity_is_not_used_for_account_decision(setup_guard):
    project, _, markets = setup_guard
    p = Path(markets[0]['journal_path'])
    first = json.loads(p.read_text())
    first['data']['payload']['execution_mode'] = 'VIRTUAL'
    p.write_text(json.dumps(first)+'\n')
    called = []
    result = run_guard(setup_guard, fetch=lambda *a: called.append(a))
    assert result['status'] == 'BALANCE_UNAVAILABLE' and not called
    assert (project/'journal').exists()
