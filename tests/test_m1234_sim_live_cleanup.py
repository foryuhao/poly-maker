"""Actual run CLI, unfinished failures, owned-resource closure and bound acceptance."""
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from polymaker.m1234_sim.events import digest, write_json
from polymaker.m1234_sim.report import read_state
from polymaker.m1234_sim.runstate import failure_path, inspect_run
from test_m1234_sim_failure_paths import cli_directory as research_output_fixture

PROJECT = Path(__file__).resolve().parents[1]
cli_directory = research_output_fixture


def read(path):
    return json.loads(Path(path).read_text())


async def command(directory, name, arguments, expected):
    values = {**os.environ, 'PYTHONPATH': 'src:tests', 'PYTHONHASHSEED': '0',
              'PYTHONDONTWRITEBYTECODE': '1'}
    args = [sys.executable, *map(str, arguments)]
    result = await asyncio.to_thread(subprocess.run, args, cwd=PROJECT, env=values,
                                     capture_output=True, text=True)
    (directory/(name+'.stdout')).write_text(result.stdout)
    (directory/(name+'.stderr')).write_text(result.stderr)
    source = {str(p.relative_to(PROJECT)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted((PROJECT/'src').rglob('*.py'))}
    write_json(directory/(name+'.command.json'), {'command': args, 'cwd': str(PROJECT),
        'implementation_hash': digest(source), 'exit_code': result.returncode,
        'expected_exit_code': expected})
    assert result.returncode == expected, result.stderr[-3000:]
    return result


async def driver(directory, *, mode='filled-conflict', prior=False, fault='snapshot', markets=1):
    output = directory/'case'
    args = ['tests/m1234_live_driver.py', '--output', output, '--mode', mode, '--fault', fault,
            '--markets', str(markets)]
    if prior:
        args.append('--prior-healthy')
    await command(directory, 'actual-run', args, 0 if fault == 'none' and mode == 'normal' else 1)
    observation = read(output/'product-return.json')
    assert observation['capture_point'].endswith('Test fallback cleanup has not started.')
    assert observation['adapter_state']['api_closed']
    assert all(observation['feeds_closed'])
    assert all(observation['recorders_closed'].values())
    assert observation['logs_closed']
    if fault != 'policy-close':
        assert all(row['engine_closed'] and row['database_closed']
                   and all(row['policies_closed']) for row in observation['members'])
        assert read(output/'test-fallback-cleanup.json')['leftovers'] == []
    return output, observation


async def verify(directory, run, expected, name='formal'):
    output = directory/(name+'.json')
    await command(directory, name, ['ops/m1234_sim_verify.py', '--run-dir', run,
                                   '--output', output], expected)
    return read(output)


@pytest.mark.parametrize('prior', [False, True])
@pytest.mark.parametrize('mode', ['filled-conflict', 'cancelled-partial-conflict'])
async def test_actual_run_cli_preserves_complete_persistent_failure_chain(cli_directory, prior, mode):
    output, observation = await driver(cli_directory, prior=prior, mode=mode)
    current = next(row for row in observation['members'] if row['memory_state']['validity']['status'] == 'INVALID')
    assert current['ended'] is False and current['snapshot_fault_still_installed']
    prefix = read(output/'committed-before-fault.json')
    memory = current['memory_state']
    assert memory['input_seq'] > prefix['input_seq']
    assert prefix['validity']['status'] == 'VALID'
    assert observation['adapter_state']['snapshot_attempts'] >= 2
    for scenario, broker in memory['brokers'].items():
        assert broker['accounts'] == prefix['brokers'][scenario]['accounts']
        assert all(order['client'] == prefix['brokers'][scenario]['orders'][key]['client']
                   for key, order in broker['orders'].items())
    terminal = read(output/'run/completion.json')
    assert terminal['status'] == 'FAIL' and terminal['run_identity'] == observation['run_identity']
    assert terminal['invalid_markets'] == [memory['market']['slug']]
    assert terminal['primary_error']['error_type'] == 'OSError'
    assert any(row['step'] == 'FINAL_SNAPSHOT' for row in terminal['cleanup_errors'])
    report = read(output/'run/latest.json')
    assert report['status'] == 'FAIL' and memory['market']['slug'] in report['invalid_markets']
    market = next(row for row in report['markets'] if row['market']['slug'] == memory['market']['slug'])
    assert market['engine_ended'] is False
    assert not market['sample_eligible'] and not market['performance_eligible']
    assert all(not account['sample_eligible'] and not account['performance_eligible'] for account in market['accounts'])
    if prior:
        previous = next(row for row in observation['members'] if row is not current)
        assert previous['memory_state']['brokers'] == read(output/'previous-before.json')['brokers']
        healthy = next(row for row in report['markets'] if row['market']['slug'] != memory['market']['slug'])
        assert healthy['validity']['status'] == 'VALID'
        assert all(account['sample_eligible'] and account['performance_eligible'] for account in healthy['accounts'])
    database = Path(current['directory'])/'simulation.sqlite3'
    assert read_state(database) == prefix
    audited = await command(cli_directory, 'reopened-audit', ['-m', 'polymaker.m1234_sim', 'audit', '--database', database], 1)
    assert json.loads(audited.stdout)['arithmetic_status'] == 'PASS'
    await command(cli_directory, 'reopened-report', ['-m', 'polymaker.m1234_sim', 'report', '--database', database,
        '--output', cli_directory/'reopened'], 1)
    formal = await verify(cli_directory, output/'run', 1)
    assert formal['status'] == 'FAIL' and formal['run_evidence']['invalid_markets']
    all_databases = [Path(row['directory'])/'simulation.sqlite3' for row in observation['members']]
    await command(cli_directory, 'regenerated-root', ['-m', 'polymaker.m1234_sim', 'report',
        '--database', *all_databases, '--output', output/'run'], 1)
    assert read(output/'run/latest.json')['status'] == 'FAIL'
    exported = cli_directory/'exported-run'
    shutil.copytree(output/'run', exported)
    copied = await verify(cli_directory, exported, 1, 'exported-formal')
    assert copied['status'] == 'FAIL' and copied['run_evidence']['run_identity'] == terminal['run_identity']
    foreign_run = cli_directory/'foreign-run'
    shutil.copytree(output/'run', foreign_run)
    plan = read(foreign_run/'plan.json')
    plan['run_identity']['run_id'] = str(uuid4())
    write_json(foreign_run/'plan.json', plan)
    foreign = await verify(cli_directory, foreign_run, 2, 'foreign-run-formal')
    assert foreign['status'] == 'INCOMPLETE' and not foreign['run_evidence']['invalid_markets']
    assert any('association' in error or 'different run' in error for error in foreign['errors'])


@pytest.mark.parametrize('fault', ['report-task', 'finish-reports', 'backup', 'recorder-close',
                                  'completion', 'terminal-files', 'api-close', 'database-close'])
async def test_one_cleanup_error_does_not_stop_other_owned_resource_closure(cli_directory, fault):
    output, observation = await driver(cli_directory, mode='normal', fault=fault)
    assert observation['exception'] and observation['cleanup_errors']
    assert all(member['memory_state']['validity']['status'] == 'VALID' for member in observation['members'])
    assert read(output/'run/latest.json')['status'] == 'FAIL'
    formal = await verify(cli_directory, output/'run', 1)
    assert formal['status'] == 'FAIL'
    database = Path(observation['members'][0]['directory'])/'simulation.sqlite3'
    result = await command(cli_directory, 'independent-economics', ['-m', 'polymaker.m1234_sim', 'audit', '--database', database], 0)
    assert json.loads(result.stdout)['arithmetic_status'] == 'PASS'
    if fault == 'completion':
        assert not (output/'run/completion.json').exists()
        assert failure_path(output/'run', observation['run_identity']).is_file()
        assert observation['terminal_saved']
    if fault == 'terminal-files':
        assert not observation['terminal_saved']
        assert 'SIM_RUN_TERMINAL_EVIDENCE_UNAVAILABLE' in observation['exception']['message']
        assert formal['run_evidence']['records'][0]['file'] == 'latest.json'


async def test_uncloseable_policy_is_explicit_and_other_resources_still_close(cli_directory):
    output, observation = await driver(cli_directory, mode='normal', fault='policy-close')
    assert observation['exception'] and observation['cleanup_errors']
    assert sum(not closed for member in observation['members'] for closed in member['policies_closed']) == 1
    assert all(member['database_closed'] for member in observation['members'])
    assert read(output/'test-fallback-cleanup.json')['leftovers']
    assert any(row['step'] == 'ENGINE_CLOSE' for row in observation['cleanup_errors'])
    assert (await verify(cli_directory, output/'run', 1))['status'] == 'FAIL'


async def test_all_failure_output_channels_report_unsaved_terminal_and_nonzero(cli_directory):
    output, observation = await driver(cli_directory, fault='all-failure-outputs')
    assert observation['terminal_saved'] is False
    assert 'SIM_RUN_TERMINAL_EVIDENCE_UNAVAILABLE' in observation['exception']['message']
    assert not (output/'run/completion.json').exists()
    assert not failure_path(output/'run', observation['run_identity']).exists()
    result = await verify(cli_directory, output/'run', 2)
    assert result['status'] == 'INCOMPLETE'


async def test_cancelled_run_finishes_owned_cleanup_and_keeps_failure(cli_directory):
    output, observation = await driver(cli_directory, mode='normal', fault='cancel')
    assert observation['exception']['type'] == 'CancelledError'
    assert read(output/'run/completion.json')['status'] == 'FAIL'
    assert (await verify(cli_directory, output/'run', 1))['status'] == 'FAIL'


async def test_manual_stop_keeps_its_reason_in_root_report_and_terminal(cli_directory):
    output, observation = await driver(cli_directory, mode='normal', fault='stop')
    assert observation['exception'] is None
    terminal = read(output/'run/completion.json')
    assert terminal['status'] == 'FAIL' and terminal['reason'] == 'MANUAL_STOP'
    report = read(output/'run/latest.json')
    assert report['status'] == 'FAIL' and report['run_failure']['reason'] == 'MANUAL_STOP'
    inspected = inspect_run(output/'run')
    assert inspected['status'] == 'FAIL' and not inspected['errors']
    assert (await verify(cli_directory, output/'run', 1))['status'] == 'FAIL'


async def test_complete_healthy_coordinator_passes_accounting_gate_and_artificial_input_gate_rejects_public_claim(cli_directory):
    output, observation = await driver(cli_directory, mode='normal', fault='none', markets=12)
    assert observation['result']['status'] == 'PASS' and observation['result']['complete_windows'] == 12
    assert len(observation['members']) == 12 and len(observation['feeds_closed']) == 12
    assert any(float(order['filled']) > 0 for member in observation['members']
        for broker in member['memory_state']['brokers'].values() for order in broker['orders'].values())
    result = await verify(cli_directory, output/'run', 0)
    assert result['status'] == 'PASS' and result['full_inputs_checked'] is False
    assert len(result['markets']) == 12
    public = cli_directory/'artificial-public-rejection.json'
    await command(cli_directory, 'artificial-public-rejection', ['ops/m1234_sim_verify.py',
        '--run-dir', output/'run', '--full-inputs', '--output', public], 1)
    assert read(public)['status'] == 'FAIL'
    assert len(read(public)['errors']) == 12
    assert all('artificial fixture' in error for error in read(public)['errors'])


async def test_healthy_actual_coordinator_and_unverified_or_foreign_terminal_controls(cli_directory):
    output, observation = await driver(cli_directory, mode='normal', fault='none')
    run = output/'run'
    assert observation['exception'] is None and observation['result']['status'] == 'PASS'
    assert read(run/'latest.json')['status'] == 'PASS'
    assert inspect_run(run)['status'] == 'PASS'
    database = Path(observation['members'][0]['directory'])/'simulation.sqlite3'
    await command(cli_directory, 'healthy-regenerated-report', ['-m', 'polymaker.m1234_sim',
        'report', '--database', database, '--output', run], 0)
    assert read(run/'latest.json')['status'] == 'PASS'
    assert inspect_run(run)['status'] == 'PASS'
    for scenario in observation['members'][0]['memory_state']['brokers'].values():
        assert scenario['accounts']['M3']['cash'] == '22.525'
    foreign = read(run/'completion.json')
    foreign['run_identity']['run_id'] = str(uuid4())
    foreign.update(status='FAIL', primary_error={'error_type': 'OSError', 'error': 'FOREIGN_RUN_ONLY'})
    write_json(failure_path(run, foreign['run_identity']), foreign)
    assert inspect_run(run)['status'] == 'PASS'
    # This one-window artificial normal control remains outside public12 acceptance.
    assert (await verify(cli_directory, run, 1))['status'] == 'FAIL'
    for variant in ['missing', 'malformed', 'foreign', 'member-mismatch']:
        target = cli_directory/variant
        shutil.copytree(run, target)
        completion = target/'completion.json'
        if variant == 'missing':
            completion.unlink()
        elif variant == 'malformed':
            completion.write_text('{broken')
        elif variant == 'foreign':
            write_json(completion, foreign)
        else:
            value = read(completion)
            value['members'][0]['binding']['database_id'] = str(uuid4())
            write_json(completion, value)
        result = await verify(cli_directory, target, 2, 'unverified-'+variant)
        assert result['status'] == 'INCOMPLETE'


@pytest.mark.parametrize('cache_mode', ['cached', 'inflight', 'empty'])
async def test_unfinished_invalid_market_survives_cached_and_inflight_reports(cli_directory, monkeypatch, cache_mode):
    import threading
    from copy import deepcopy

    from m1234_rr1_fixtures import rr1_events
    from polymaker.m1234_sim import runner
    from polymaker.m1234_sim.report import summary
    from test_m1234_sim_validity import filled_engine, late_constraint

    engine, _ = await filled_engine(cli_directory/'current')
    engine.persist()
    prefix = summary(engine.store.path)
    cfg = deepcopy(rr1_events('normal')[0]['definition']['settings'])
    cfg.update(max_markets=12, max_seconds=4500, max_output_mib=4096, min_free_disk_mib=2048,
               max_rss_mib=512, input_queue_frames=2048, writer_queue_records=8192,
               markout_seconds=[1, 5, 30])
    run = runner.LiveRun(cli_directory/'coordinator', cfg)
    run.engines.append(engine)
    slug = engine.meta.slug
    healthy_stamp = (None, digest(engine.validity))
    release = threading.Event()

    def delayed(*args):
        release.wait(5)
        return prefix

    monkeypatch.setattr(runner, 'summary', delayed)
    if cache_mode == 'cached':
        run.report_cache[slug] = (healthy_stamp, prefix)
    elif cache_mode == 'inflight':
        run.report_tasks[slug] = (healthy_stamp, asyncio.create_task(asyncio.to_thread(delayed)))

    def failure(*args):
        raise OSError('CONTINUOUS_UNFINISHED_SNAPSHOT_FAILURE')

    engine.store.snapshot = failure
    with pytest.raises(OSError):
        await late_constraint(engine, at=.16)
    try:
        assert not engine.ended
        run.update_report()
        result = read(run.directory/'latest.json')
        assert result['status'] == 'FAIL' and result['invalid_markets'] == [slug]
        assert result['markets'][0]['engine_ended'] is False
        assert not result['markets'][0]['sample_eligible']
        release.set()
        await run.finish_reports()
        run.update_report()
        assert read(run.directory/'latest.json')['status'] == 'FAIL'
        assert all(not account['sample_eligible'] and not account['performance_eligible']
                   for account in read(run.directory/'latest.json')['markets'][0]['accounts'])
    finally:
        release.set()
        await run.finalize()
    assert engine.closed and not engine.ended and engine.store.snapshot is failure
    assert read(run.directory/'completion.json')['status'] == 'FAIL'


def test_recorder_queue_close_has_a_total_deadline(tmp_path, monkeypatch):
    import queue
    from types import SimpleNamespace

    from polymaker.m1234_sim import events

    recorder = events.Recorder.__new__(events.Recorder)
    recorder.failure, recorder.seq, recorder.written = None, 10, 9
    recorder.path = tmp_path/'input.jsonl.gz'
    attempts, joins = [], []
    ticks = iter([0., 0., .01, 10.01, 10.01])
    monkeypatch.setattr(events, 'time', SimpleNamespace(monotonic=lambda: next(ticks)))

    def full(item, timeout):
        attempts.append(timeout)
        raise queue.Full

    recorder.queue = SimpleNamespace(put=full)
    recorder.thread = SimpleNamespace(is_alive=lambda: True, join=lambda timeout: joins.append(timeout))
    with pytest.raises(RuntimeError, match='WRITER_CLOSE_TIMEOUT'):
        recorder.close()
    assert len(attempts) == 1 and joins == [0]
    assert recorder.failure['reason'] == 'WRITER_CLOSE_TIMEOUT'
