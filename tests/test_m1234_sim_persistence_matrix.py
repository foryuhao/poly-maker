"""RR2 persistent faults, identity association, exports and actual reopened consumers."""
import asyncio
import gzip
import json
import os
import shutil
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from polymaker.m1234_sim import report, runner, store
from polymaker.m1234_sim.broker import CONSTRAINT_CONFLICT
from polymaker.m1234_sim.engine import Engine
from polymaker.m1234_sim.events import write_json
from polymaker.m1234_sim.report import read_state, summary
from polymaker.m1234_sim.validity import database_validity, tracker_path
from test_m1234_sim_failure_paths import cli_directory as research_output_fixture
from test_m1234_sim_validity import filled_engine, late_constraint

PROJECT = Path(__file__).resolve().parents[1]
entry_directory = research_output_fixture


def snapshot_failure(*args):
    raise OSError('PERSISTENT_SNAPSHOT_WRITE_FAILURE')


def close_failed(engine):
    engine.store.db.rollback()
    engine.store.db.close()
    engine.closed = True


async def command(directory, label, arguments, expected):
    values = {**os.environ, 'PYTHONPATH': 'src:tests', 'PYTHONHASHSEED': '0',
              'PYTHONDONTWRITEBYTECODE': '1'}
    args = [sys.executable, *map(str, arguments)]
    result = await asyncio.to_thread(subprocess.run, args, cwd=PROJECT, env=values,
                                     capture_output=True, text=True)
    (directory / (label + '.stdout')).write_text(result.stdout)
    (directory / (label + '.stderr')).write_text(result.stderr)
    write_json(directory / (label + '.command.json'), {'command': args, 'exit_code': result.returncode,
               'expected_exit_code': expected, 'cwd': str(PROJECT)})
    assert result.returncode == expected, result.stderr[-4000:]
    return result


async def reopen(directory, database, expected):
    audit = await command(directory, 'reopened-audit', ['-m', 'polymaker.m1234_sim', 'audit',
                          '--database', database], expected)
    await command(directory, 'reopened-report', ['-m', 'polymaker.m1234_sim', 'report',
                  '--database', database, '--output', directory / 'reopened-report'], expected)
    return json.loads(audit.stdout)


async def fail_engine(directory, scenario=None):
    engine, _ = await filled_engine(directory, scenario=scenario)
    engine.persist()
    prefix = read_state(engine.store.path)
    engine.store.snapshot = snapshot_failure
    with pytest.raises(OSError) as caught:
        await late_constraint(engine, at=.16)
    result = await runner.failed_output(engine, directory, 'OFFLINE_REPLAY', caught.value,
                                        directory / 'artificial-input.jsonl.gz')
    return engine, prefix, result


@pytest.mark.parametrize('scenario', ['trades-only', 'cancel-half'])
async def test_persistent_failure_keeps_prefix_economics_but_never_eligibility(tmp_path, scenario):
    engine, prefix, result = await fail_engine(tmp_path, scenario)
    current = read_state(engine.store.path)
    assert current == prefix and current['validity']['status'] == 'VALID'
    assert engine.validity['status'] == result['validity']['status'] == 'INVALID'
    assert result['reason'] == CONSTRAINT_CONFLICT
    assert result['input_seq'] > prefix['input_seq']
    assert result['binding'] == engine.store.identity
    for broker in current['brokers'].values():
        assert broker['accounts'] == prefix['brokers'][broker['scenario']]['accounts']
    publication = json.loads((tmp_path / 'latest.json').read_text())
    assert publication['status'] == 'FAIL' and publication['valid_complete_windows'] == 0
    assert all(not row['sample_eligible'] and not row['performance_eligible']
               for market in publication['markets'] for row in market['accounts'])
    close_failed(engine)
    arithmetic = await reopen(tmp_path, engine.store.path, 1)
    assert arithmetic['arithmetic_status'] == 'PASS' and arithmetic['acceptance_status'] == 'FAIL'
    assert arithmetic['validity']['status'] == 'INVALID'


async def test_repeated_failure_and_secondary_cleanup_keep_original_root(tmp_path):
    engine, prefix, first = await fail_engine(tmp_path)
    roots = deepcopy(first['validity']['failures'])
    second = await runner.failed_output(engine, tmp_path, 'OFFLINE_REPLAY', OSError('SECONDARY_CLOSE_ERROR'),
                                         tmp_path / 'artificial-input.jsonl.gz')
    assert second['reason'] == first['reason'] == CONSTRAINT_CONFLICT
    assert second['trigger_error'] == 'SECONDARY_CLOSE_ERROR'
    assert second['validity']['failures'] == roots
    assert read_state(engine.store.path) == prefix
    close_failed(engine)
    await reopen(tmp_path, engine.store.path, 1)


@pytest.mark.parametrize('unwritable', ['tracker', 'failure.json', 'replay.json'])
async def test_one_failed_evidence_channel_preserves_other_bound_evidence(tmp_path, monkeypatch, unwritable):
    engine, _ = await filled_engine(tmp_path)
    engine.persist()
    engine.store.snapshot = snapshot_failure
    original_store = store.write_json
    original_runner = runner.write_json

    def store_write(path, value):
        if unwritable == 'tracker':
            raise OSError('TRACKER_UNWRITABLE')
        original_store(path, value)

    def result_write(path, value):
        if Path(path).name == unwritable:
            raise OSError('RESULT_UNWRITABLE:' + unwritable)
        original_runner(path, value)

    monkeypatch.setattr(store, 'write_json', store_write)
    monkeypatch.setattr(runner, 'write_json', result_write)
    with pytest.raises(OSError) as caught:
        await late_constraint(engine, at=.16)
    value = await runner.failed_output(engine, tmp_path, 'OFFLINE_REPLAY', caught.value, tmp_path / 'input.gz')
    assert value['status'] == 'FAIL' and value['reason'] == CONSTRAINT_CONFLICT
    assert any('UNWRITABLE' in error for error in value['cleanup_errors'])
    assert json.loads((tmp_path / 'latest.json').read_text())['status'] == 'FAIL'
    if unwritable != 'tracker':
        assert not (tmp_path / unwritable).exists()
    close_failed(engine)
    await reopen(tmp_path, engine.store.path, 1)


async def test_all_failure_storage_unwritable_reports_unsaved_failure(tmp_path, monkeypatch):
    engine, _ = await filled_engine(tmp_path)
    engine.persist()
    report.publish(tmp_path, [summary(engine.store.path)])
    prefix_report = (tmp_path / 'latest.json').read_bytes()
    engine.store.snapshot = snapshot_failure

    def unwritable(*args):
        raise OSError('ALL_FAILURE_STORAGE_UNWRITABLE')

    monkeypatch.setattr(store, 'write_json', unwritable)
    monkeypatch.setattr(runner, 'write_json', unwritable)
    monkeypatch.setattr(report, 'write_json', unwritable)
    with pytest.raises(OSError):
        await late_constraint(engine, at=.16)
    with pytest.raises(OSError, match='SIM_FAILURE_EVIDENCE_UNAVAILABLE'):
        await runner.failed_output(engine, tmp_path, 'OFFLINE_REPLAY', OSError('PERSISTENT_WRITE_FAILURE'),
                                   tmp_path / 'input.gz')
    assert engine.validity['status'] == 'INVALID'
    assert not (tmp_path / 'failure.json').exists() and not (tmp_path / 'replay.json').exists()
    assert (tmp_path / 'latest.json').read_bytes() == prefix_report
    # The existing report is the old committed prefix. The failed command saved no new result.
    close_failed(engine)


@pytest.mark.parametrize('state_available', [False, True])
async def test_failed_diagnostics_when_snapshot_missing_or_report_unreadable(tmp_path, monkeypatch, state_available):
    engine, _ = await filled_engine(tmp_path)
    if state_available:
        engine.persist()
    engine.store.snapshot = snapshot_failure
    if state_available:
        def unreadable(*args):
            raise OSError('SNAPSHOT_READ_UNAVAILABLE')
        monkeypatch.setattr(runner, 'summary', unreadable)
    with pytest.raises(OSError) as caught:
        await late_constraint(engine, at=.16)
    result = await runner.failed_output(engine, tmp_path, 'OFFLINE_REPLAY', caught.value, tmp_path / 'input.gz')
    publication = json.loads((tmp_path / 'latest.json').read_text())
    assert result['status'] == publication['status'] == 'FAIL'
    assert publication['markets'] == [] and publication['valid_complete_windows'] == 0
    close_failed(engine)
    if not state_available:
        audit = await command(tmp_path, 'no-snapshot-audit', ['-m', 'polymaker.m1234_sim', 'audit',
                             '--database', engine.store.path], 1)
        assert 'SIM_SNAPSHOT_UNAVAILABLE' in audit.stderr


@pytest.mark.parametrize('mutation', ['market', 'definition_hash', 'implementation_hash', 'missing_binding',
                                     'malformed', 'schema', 'watermark', 'future_fact', 'bad_uuid', 'empty_facts'])
async def test_unverifiable_record_never_grants_health_or_confirms_foreign_economics(tmp_path, mutation):
    engine, _ = await filled_engine(tmp_path)
    await engine.close()
    binding = engine.store.identity
    value = {'evidence_schema': 1, 'binding': deepcopy(binding), 'status': 'FAIL',
             'input_seq': 6, 'validity': {'status': 'INVALID', 'failures': []}}
    if mutation in {'market', 'definition_hash', 'implementation_hash'}:
        value['binding'][mutation] = 'inconsistent'
    elif mutation == 'missing_binding':
        value.pop('binding')
    elif mutation == 'schema':
        value['evidence_schema'] = 999
    elif mutation == 'watermark':
        value['input_seq'] = -1
    elif mutation == 'future_fact':
        value['validity']['failures'] = [{'id': 'future', 'input_seq': 7, 'reason': 'ARTIFICIAL'}]
    elif mutation == 'bad_uuid':
        value['binding']['database_id'] = None
    path = tmp_path / 'failure.json'
    if mutation == 'malformed':
        path.write_text('{incomplete')
    else:
        write_json(path, value)
    validity = database_validity(engine.store.path, read_state(engine.store.path))
    assert validity['status'] == 'UNVERIFIED_EVIDENCE'
    assert not validity['failures'] and validity['evidence']['errors']
    arithmetic = await reopen(tmp_path, engine.store.path, 1)
    assert arithmetic['arithmetic_status'] == 'PASS' and arithmetic['acceptance_status'] == 'FAIL'


@pytest.mark.parametrize('different_market', [False, True])
async def test_fully_identified_other_instance_is_not_current_conflict(tmp_path, different_market):
    healthy, _ = await filled_engine(tmp_path / 'healthy')
    await healthy.close()
    if different_market:
        from m1234_fixtures import fixture_events
        tape = fixture_events()
        tape[0]['definition']['market']['slug'] = 'btc-updown-5m-1800000300'
        other = Engine(tmp_path / 'foreign')
        await other.ingest(tape[0])
        with pytest.raises(ValueError):
            await other.ingest({**tape[1], 'schema': 999})
        await runner.failed_output(other, tmp_path / 'foreign', 'OFFLINE_REPLAY',
                                   ValueError('SIM_INPUT_SCHEMA_VERSION'), tmp_path / 'foreign.gz')
    else:
        other, _, _ = await fail_engine(tmp_path / 'foreign')
        close_failed(other)
    shutil.copy2(tmp_path / 'foreign/failure.json', tmp_path / 'healthy/failure.json')
    validity = database_validity(healthy.store.path, read_state(healthy.store.path))
    assert validity['status'] == 'VALID' and not validity['failures']
    assert validity['evidence']['foreign_records'] and not validity['evidence']['errors']
    await reopen(tmp_path / 'healthy', healthy.store.path, 0)


@pytest.mark.parametrize('failed', [False, True])
async def test_supported_backup_and_relocation_carry_required_evidence(tmp_path, failed):
    if failed:
        engine, prefix, _ = await fail_engine(tmp_path / 'source')
        engine.store.db.rollback()
    else:
        engine, _ = await filled_engine(tmp_path / 'source')
        engine.persist()
        prefix = read_state(engine.store.path)
    target = tmp_path / 'export/simulation.sqlite3'
    target.parent.mkdir()
    engine.store.backup(target)
    assert read_state(target) == prefix
    if failed:
        close_failed(engine)
    else:
        await engine.close()
    shutil.copytree(target.parent, tmp_path / 'relocated')
    expected = 1 if failed else 0
    await reopen(tmp_path / 'export', target, expected)
    await reopen(tmp_path / 'relocated', tmp_path / 'relocated/simulation.sqlite3', expected)
    assert tracker_path(tmp_path / 'relocated/simulation.sqlite3', engine.store.identity).is_file()
    only = tmp_path / 'database-only'
    only.mkdir()
    shutil.copy2(target, only / 'simulation.sqlite3')
    validity = database_validity(only / 'simulation.sqlite3', read_state(only / 'simulation.sqlite3'))
    assert validity['status'] == 'UNVERIFIED_EVIDENCE'
    await reopen(only, only / 'simulation.sqlite3', 1)


async def test_comparison_and_formal_acceptance_reject_same_stale_valid_prefix(tmp_path):
    engines = []
    for name in ['one', 'two']:
        engine, _, _ = await fail_engine(tmp_path / name)
        engines.append(engine)
    output = tmp_path / 'comparison.json'
    await command(tmp_path, 'compare', ['ops/m1234_sim_compare.py', '--online', engines[0].store.path,
                  '--offline', engines[1].store.path, '--run-id', 'ARTIFICIAL_RR2', '--output', output], 1)
    comparison = json.loads(output.read_text())
    assert comparison['equivalence'] == 'MATCH' and comparison['state_equal']
    assert comparison['different_audit_rows'] == 0
    assert comparison['binding_comparison']['common_definition']
    assert comparison['binding_comparison']['individually_verified']
    assert comparison['binding_comparison']['failure_facts_equal']
    run = tmp_path / 'artificial-acceptance'
    market = run / engines[0].meta.slug
    market.mkdir(parents=True)
    engines[0].store.db.rollback()
    engines[0].store.backup(market / 'snapshot.sqlite3')
    write_json(run / 'plan.json', {'predeclared_max_markets': 12, 'max_seconds': 4500,
                                  'artificial_consumer_control': True})
    write_json(run / 'completion.json', {'reason': 'PLANNED_COMPLETE', 'status': 'PASS',
        'complete_windows': 12, 'markets': 12, 'invalid_markets': [], 'recording_errors': [], 'real_orders': 0,
        'artificial_consumer_control': 'Intentionally inconsistent completion to test database-level rejection'})
    write_json(run / 'resources.json', {'errors': []})
    acceptance = tmp_path / 'acceptance.json'
    await command(tmp_path, 'verify', ['ops/m1234_sim_verify.py', '--run-dir', run, '--output', acceptance], 1)
    assert any('simulation invalid' in error for error in json.loads(acceptance.read_text())['errors'])
    for engine in engines:
        close_failed(engine)


@pytest.mark.parametrize('mode', ['filled-conflict', 'cancelled-partial-conflict'])
async def test_existing_cli_entry_persistent_fault_and_reopen(entry_directory, mode):
    tmp_path = entry_directory
    from test_m1234_sim_failure_paths import input_file

    # output_path enforces the research boundary in the actual CLI.
    output_root = PROJECT / 'research_outputs/m1234_sim'
    assert output_root in tmp_path.parents
    source = input_file(tmp_path, mode)
    from polymaker.m1234_sim.events import read_events
    tape = list(read_events(source))
    fault = next(row['seq'] for row in tape if row['kind'] == 'FRAME'
                 and isinstance(row.get('raw'), dict) and row['raw'].get('event_type') == 'tick_size_change')
    target = tmp_path / 'injected'
    await command(tmp_path, 'injected-cli', ['tests/m1234_rr2_driver.py', '--input', source,
                  '--output', target, '--fail-at', fault], 1)
    result = json.loads((target / 'injection-result.json').read_text())
    assert result['writer_still_injected'] and result['snapshot_failure_attempts'] >= 2
    assert result['committed_prefix']['validity']['status'] == 'VALID'
    assert result['memory_state']['validity']['status'] == 'INVALID'
    assert result['failure']['reason'] == CONSTRAINT_CONFLICT
    assert result['publication']['status'] == 'FAIL'
    assert result['committed_prefix']['input_seq'] == fault - 1
    assert result['memory_state']['input_seq'] == fault
    for scenario in result['committed_prefix']['brokers']:
        assert result['committed_prefix']['brokers'][scenario]['accounts'] == result['memory_state']['brokers'][scenario]['accounts']
    arithmetic = await reopen(target, target / 'simulation.sqlite3', 1)
    assert arithmetic['arithmetic_status'] == 'PASS'

    for operation in ['recover', 'settle']:
        continued = tmp_path / operation
        await command(tmp_path, 'continue-' + operation, ['-m', 'polymaker.m1234_sim', operation,
                      '--input', target / 'input.jsonl.gz', '--output', continued], 1)
        value = json.loads((continued / 'failure.json').read_text())
        assert value['source_run']['validity']['status'] == 'INVALID'
        assert value['source_run']['validity']['evidence']['binding'] == result['binding']
        assert value['status'] == 'FAIL'

    alternative = target / 'alternative-input.jsonl.gz'
    from polymaker.m1234_sim.events import encode
    with gzip.open(alternative, 'wt') as output:
        for row in tape[:fault - 1]:
            output.write(encode(row) + '\n')
    mismatched = tmp_path / 'mismatched-source'
    await command(tmp_path, 'mismatched-source', ['-m', 'polymaker.m1234_sim', 'recover',
                  '--input', alternative, '--output', mismatched], 1)
    value = json.loads((mismatched / 'failure.json').read_text())
    assert value['reason'] == 'SIM_RECOVERY_INPUT_HASH_MISMATCH'


@pytest.mark.parametrize('cache_mode', ['cached', 'inflight', 'empty'])
async def test_live_report_never_republishes_cached_health_after_persistent_failure(tmp_path, monkeypatch, cache_mode):
    import threading
    from dataclasses import asdict

    from polymaker.m1234_sim.events import digest, encode
    from test_m1234_sim_ticks import send

    engine, _ = await filled_engine(tmp_path / 'market')
    engine.persist()
    prefix = summary(engine.store.path)
    run = runner.LiveRun.__new__(runner.LiveRun)
    run.directory = tmp_path
    run.cfg = {'markout_seconds': [1, 5, 30]}
    run.engines = [engine]
    run.report_cache, run.report_tasks = {}, {}
    slug = engine.meta.slug
    healthy_stamp = (None, digest(engine.validity))
    release = threading.Event()

    def slow(*args):
        release.wait(5)
        return prefix

    monkeypatch.setattr(runner, 'summary', slow)
    if cache_mode == 'cached':
        run.report_cache[slug] = (healthy_stamp, prefix)
    elif cache_mode == 'inflight':
        run.report_tasks[slug] = (healthy_stamp, asyncio.create_task(asyncio.to_thread(slow)))
    engine.store.snapshot = snapshot_failure
    with pytest.raises(OSError):
        await late_constraint(engine, at=.16)
    with pytest.raises(OSError):
        await send(engine, 'END', .17, reason='ARTIFICIAL_FAILED_CLEANUP')
    assert engine.ended
    try:
        run.update_report()
        value = json.loads((tmp_path / 'latest.json').read_text())
        assert value['status'] == 'FAIL' and value['valid_complete_windows'] == 0
        assert value['markets'][0]['market'] == json.loads(encode(asdict(engine.meta)))
        assert all(not account['sample_eligible'] and not account['performance_eligible']
                   for market in value['markets'] for account in market['accounts'])
        assert prefix['status'] == 'PASS' and all(row['sample_eligible'] for row in prefix['accounts'])
    finally:
        release.set()
        await run.finish_reports()
        close_failed(engine)
    assert json.loads((tmp_path / 'latest.json').read_text())['status'] == 'FAIL'


@pytest.mark.parametrize('consumer', ['report', 'audit'])
async def test_readers_keep_snapshot_and_audit_in_one_committed_view(tmp_path, monkeypatch, consumer):
    """A real concurrent commit cannot mix two watermarks in one result."""
    from polymaker.m1234_sim import audit

    engine, _ = await filled_engine(tmp_path)
    engine.persist()
    prefix = read_state(engine.store.path)
    module = report if consumer == 'report' else audit
    original = module.read_audit

    def advance():
        state = deepcopy(prefix)
        state['input_seq'] += 1
        engine.store.input_seq = state['input_seq']
        engine.store.emit('OFFICIAL_RESULT', {'raw': {'artificial_concurrent_commit': True}})
        engine.store.snapshot('state', state)

    def concurrent_audit(*args, **kwargs):
        if consumer == 'report':
            advance()
        yield from original(*args, **kwargs)
        if consumer == 'audit':
            advance()

    monkeypatch.setattr(module, 'read_audit', concurrent_audit)
    try:
        value = module.summary(engine.store.path) if consumer == 'report' else module.reconcile(engine.store.path)
        assert value['status'] == 'PASS'
        validity = value['validity']
        assert validity['status'] == 'VALID' and not validity['evidence']['errors']
        assert validity['evidence']['snapshot_input_seq'] == prefix['input_seq']
        assert validity['evidence']['committed_audit_input_seq'] == prefix['input_seq']
        assert read_state(engine.store.path)['input_seq'] == prefix['input_seq'] + 1
    finally:
        close_failed(engine)
