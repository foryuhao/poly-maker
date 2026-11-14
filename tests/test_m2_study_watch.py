"""M2 audits must not turn repeated observations or assumed fills into evidence."""

import hashlib
import json
from types import SimpleNamespace

import pytest
from ops.m2_study_watch import INTERVAL, assess, wake
from ops.m234_watch_metrics import strategy_dependency_drift

from polymaker.m2_config import M2Config
from polymaker.m2_store import M2Store
from test_m2_study import META, row
from test_m234_study import event


@pytest.fixture
def store(tmp_path):
    s = M2Store(tmp_path, M2Config(), "v1")
    yield s
    s.close()


def latest(store, now=1000, version="v1"):
    (store.directory/"latest.json").write_text(json.dumps(
        {"active_version": version, "observed_at": now, "real_posts": 0, "runtime": {"state": "OBSERVING"}}))


def market(store, *, number=0, winner="YES", complete=True):
    epoch = 1793710500+number*300
    meta = {**META, "slug": f"btc-updown-5m-{epoch}"}
    run = f"run{number}"
    store.start(run, meta)
    observation = row(f"ap{number}")
    candidate = {**observation, "event": "M2_CANDIDATE"}
    store.ingest(run, meta["slug"], [candidate]+[observation]*10,
                 bounds=lambda t: (t, t+1), minimum=5)
    store.finish(run, meta["slug"], full_window=complete, complete=complete, counts={})
    if winner:
        store.settle(meta["slug"], {"slug": meta["slug"], "winner": winner})
    latest(store)
    return run


def test_repeated_observations_and_nested_rules_are_not_independent_markets(store):
    market(store)
    report = assess(store.directory, now=1000)
    assert report["quality"]["observations"] == 10
    assert len(report["rules"]) == 4
    assert report["unique_settled_signal_episodes"] == 1
    assert report["independent_settled_signal_markets"] == 1
    assert report["stage"] == "INSUFFICIENT_INDEPENDENT_DATA"
    assert not report["trading_ready"] and not report["automatic_threshold_change"]


def test_unsettled_is_pending_not_a_zero_return(store):
    market(store, winner=None)
    report = assess(store.directory, now=1000)
    assert report["markets"]["complete"] == 1
    assert report["markets"]["complete_officially_settled"] == 0
    assert report["unique_settled_signal_episodes"] == 0
    assert report["rules"] == {}


def test_incomplete_markets_cannot_justify_a_threshold(store):
    market(store, complete=False)
    report = assess(store.directory, now=1000)
    assert report["markets"]["complete"] == 0
    assert report["independent_settled_signal_markets"] == 0


def test_stale_export_and_orphan_features_trigger_quality_review(store):
    market(store)
    with store.db:
        store.db.execute("DELETE FROM candidates")
    report = assess(store.directory, now=2000)
    assert report["quality"]["orphan_observations"] == 10
    assert "STALE_REPORT" in report["problems"]
    assert report["stage"] == "DATA_QUALITY_REVIEW_REQUIRED"


def test_settlement_pnl_is_recomputed_instead_of_trusting_report(store):
    market(store)
    with store.db:
        store.db.execute("UPDATE samples SET sample_pnl='999'")
    report = assess(store.directory, now=1000)
    assert report["quality"]["settlement_pnl_mismatch"] == 4
    assert report["stage"] == "DATA_QUALITY_REVIEW_REQUIRED"


def test_current_version_is_not_mixed_with_previous_runs(store):
    market(store)
    latest(store, version="v2")
    report = assess(store.directory, now=1000)
    assert report["markets"]["observed"] == 0
    assert report["unique_settled_signal_episodes"] == 0


@pytest.mark.parametrize('fault', ['risk', 'direction_limit'])
def test_extra_strategy_risk_checks_include_pending_markets(store, fault):
    market(store, winner=None)
    store.ingest_extra('run0', META['slug'], [event(root='r1'), event(root='r2')], minimum=5)
    with store.db:
        if fault == 'risk':
            store.db.execute("UPDATE strategy_samples SET cost='6' WHERE root='r1'")
        else:
            store.db.execute("UPDATE strategy_samples SET status='ASSUMED_FILL',reason=NULL WHERE root='r2'")
    result = assess(store.directory, now=1000)
    assert 'LEDGER_INVARIANT_FAILED' in result['problems']
    assert not result['trading_ready']


def test_completed_market_observer_failure_is_not_hidden_by_new_runtime(store):
    market(store)
    with store.db:
        store.db.execute('UPDATE runs SET counts=?',
                         (json.dumps({'observer_failures': {'M4': 'ZeroDivisionError'}}),))
    result = assess(store.directory, now=1000)
    assert result['quality']['observer_failures'] == {'M4': 'ZeroDivisionError'}
    assert 'STRATEGY_OBSERVER_FAILED' in result['problems']


def test_reused_m4_round_id_with_different_start_is_reported(store):
    market(store)
    with store.db:
        for start in (1., 2.):
            store.db.execute('INSERT INTO events(run_id,wall,kind,payload) VALUES(?,?,?,?)',
                ('run0', 1000, 'M4_ROUND_CLOSED', json.dumps({'payload': {'id': 'round1', 'start': start}})))
    result = assess(store.directory, now=1000)
    assert result['quality']['reused_m4_round_ids'] == 1
    assert 'STRATEGY_IDENTITY_REUSED' in result['problems']


def test_three_strategy_summaries_use_primary_m2_and_official_results(store):
    market(store)
    store.ingest_extra('run0', META['slug'], [event()], minimum=5)
    store.settle(META['slug'], {'slug': META['slug'], 'winner': 'YES'})
    result = assess(store.directory, now=1000)
    assert set(result['strategies']) == {'M2', 'M3', 'M4'}
    assert result['strategies']['M2']['settled_assumed_fills'] == 1
    assert result['strategies']['M3']['settled_assumed_fills'] == 1
    assert result['strategies']['M4']['samples'] == 0


def test_all_wins_are_not_declared_enough_for_trading_even_after_sixty_markets(store):
    for i in range(60):
        market(store, number=i)
    report = assess(store.directory, now=1000)
    assert report["independent_settled_signal_markets"] == 60
    assert report["stage"] == "EXPLORATORY_COMPARISON_ONLY"
    assert not report["trading_ready"]
    assert not any(r["stable_positive_exploratory_pattern"] for r in report["rules"].values())


def report(bucket=900):
    return {"bucket": bucket, "stage": "INSUFFICIENT_INDEPENDENT_DATA",
            "markets": {"complete_officially_settled": 1},
            "independent_settled_signal_markets": 0, "unique_settled_signal_episodes": 0}


def test_wakeup_stays_in_authorized_session_and_coalesces(tmp_path):
    calls = []

    def runner(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="Queued", stderr="")

    result = wake("current-session", tmp_path, tmp_path, report(), runner=runner, now=1000)
    assert result["status"] == "QUEUED_AND_RESUME_REQUESTED"
    assert calls[0][1:4] == ["queue", "--thread", "current-session"]
    assert calls[0][-1] == "--resume"
    prompt = calls[0][-2]
    assert "不创建新对话" in prompt and "每小时" in prompt
    assert '即使没有新增信号也照常报告' in prompt
    assert 'M2、M3、M4' in prompt and '每隔15分钟' not in prompt
    assert wake("current-session", tmp_path, tmp_path, report(1800), runner=runner,
                now=1900)["status"] == "COALESCED_WITH_PENDING_REVIEW"
    state = json.loads((tmp_path/"agent-state.json").read_text())
    state["reviewed_through_bucket"] = 900
    (tmp_path/"agent-state.json").write_text(json.dumps(state))
    assert wake("current-session", tmp_path, tmp_path, report(1800), runner=runner,
                now=1900)["status"] == "QUEUED_AND_RESUME_REQUESTED"


def test_failed_wakeup_does_not_record_delivery(tmp_path):
    result = wake("current-session", tmp_path, tmp_path, report(), now=1000,
                  runner=lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr=""))
    assert result["status"] == "WAKE_FAILED"
    assert not (tmp_path/"agent-state.json").exists()


def test_paused_monitor_never_queues_a_wakeup(tmp_path):
    path = tmp_path/'agent-state.json'
    path.write_text(json.dumps({'monitoring_paused': True, 'reviewed_through_bucket': 0}))
    def forbidden(*args, **kwargs):
        raise AssertionError('paused watcher must not queue')
    assert wake('current', tmp_path, tmp_path, report(), runner=forbidden)['status'] == 'MONITORING_PAUSED'


def test_hourly_bucket_and_three_strategies_count_roots_not_observations(store):
    market(store, winner=None)
    events = [event('M3', root) for root in ('a', 'a', 'b')]
    events += [event('M4', 'c')]
    store.ingest_extra('run0', META['slug'], events, minimum=5)
    result = assess(store.directory, now=7300)
    assert INTERVAL == 3600 and result['bucket'] == 7200
    metrics = result['operational']['strategies']
    assert metrics['M2']['cumulative_all_versions']['valid_signals'] == 1
    assert metrics['M2']['current_version']['observations'] == 10
    assert metrics['M3']['current_version']['valid_signals'] == 2
    assert metrics['M3']['current_version']['assumed_fills'] == 1
    assert metrics['M3']['current_version']['pending_fills'] == 1
    assert metrics['M3']['current_version']['direction_skipped'] == 1
    assert metrics['M4']['current_version']['valid_signals'] == 1
    assert metrics['M3']['last_hour']['valid_signals'] == 0
    assert 'settled_pnl' not in metrics['M3']['cumulative_all_versions']


def test_cumulative_inventory_and_current_version_are_separate(store):
    market(store)
    store.ingest_extra('run0', META['slug'], [event()], minimum=5)
    latest(store, version='v2')
    metrics = assess(store.directory, now=1000)['operational']['strategies']
    assert metrics['M2']['cumulative_all_versions']['valid_signals'] == 1
    assert metrics['M2']['current_version']['valid_signals'] == 0
    assert metrics['M3']['cumulative_all_versions']['valid_signals'] == 1
    assert metrics['M3']['current_version']['valid_signals'] == 0
    assert metrics['M4']['cumulative_all_versions']['valid_signals'] == 0


def test_pending_m2_simulation_errors_are_detected(store):
    market(store, winner=None)
    with store.db:
        store.db.execute("UPDATE samples SET trade_pnl='0' WHERE rule='all_candidates'")
    result = assess(store.directory, now=1000)
    assert result['operational']['checks']['unsettled_pnl'] == 1
    assert 'SIMULATION_OR_DEFINITION_INVARIANT_FAILED' in result['problems']


def test_m4_definitions_are_not_silently_merged(store):
    market(store)
    store.ingest_extra('run0', META['slug'], [event('M4', 'v1root')], minimum=5)
    store.version = 'v2'
    store.settings = M2Config(include_m3_m4=True, m4_frequency_study=True)
    market(store, number=1)
    slug = 'btc-updown-5m-1793710800'
    store.ingest_extra('run1', slug, [event('M4', 'v2root')], minimum=5)
    metrics = assess(store.directory, now=1000)['operational']['strategies']['M4']
    assert metrics['cumulative_all_versions']['valid_signals'] == 2
    assert metrics['current_version']['valid_signals'] == 1
    assert metrics['by_definition']['m4-v1']['valid_signals'] == 1
    assert metrics['by_definition']['m4-frequency-v2']['valid_signals'] == 1


@pytest.mark.parametrize('tampered', [False, True])
def test_official_identity_and_payout_evidence(store, tampered):
    market(store)
    result = {'slug': META['slug'], 'winner': 'YES', 'condition_id': META['condition_id'],
              'token_ids': {'YES': '1', 'NO': '2'}, 'clob_token_ids': ['1', '2'],
              'closed': True, 'resolution_status': 'resolved',
              'outcome_prices': ['0.0', '1.0'] if tampered else ['1.0', '0.0'],
              'source': 'https://gamma-api.polymarket.com/markets/slug/'+META['slug']}
    with store.db:
        store.db.execute('UPDATE markets SET resolution=?', (json.dumps(result),))
    checks = assess(store.directory, now=1000)['operational']['checks']
    assert bool(checks.get('official_resolution_evidence')) == tampered


@pytest.mark.parametrize('change', ['dashboard', 'validator', 'constant', 'untrusted_baseline'])
def test_source_scope_ignores_only_proven_dashboard_changes(tmp_path, change):
    root, baseline_dir = tmp_path/'source', tmp_path/'baselines'
    (root/'dashboard').mkdir(parents=True)
    baseline_dir.mkdir()
    original = b"BASE = 1\ndef validate_resolution(x):\n    return x\nclass SettlementObserver:\n    def tick(self):\n        return 1\n"
    expected = hashlib.sha256(original).hexdigest()
    frozen = original if change != 'untrusted_baseline' else original+b'\n# untrusted\n'
    (baseline_dir/(expected+'.py')).write_bytes(frozen)
    if change == 'validator':
        changed = original.replace(b'return x', b'return None')
    elif change == 'constant':
        changed = original.replace(b'BASE = 1', b'BASE = 2')
    else:
        changed = original.replace(b'return 1', b'return 2')
    (root/'dashboard/settlement.py').write_bytes(changed)
    relevant, excluded = strategy_dependency_drift(root, {'dashboard/settlement.py': expected}, baseline_dir)
    expected_result = ([], ['dashboard/settlement.py']) if change == 'dashboard' else (['dashboard/settlement.py'], [])
    assert (relevant, excluded) == expected_result
