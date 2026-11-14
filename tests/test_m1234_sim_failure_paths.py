"""Actual CLI and live coordinator failure paths over native-policy raw input."""
import asyncio
import gzip
import json
import os
import sqlite3
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from m1234_rr1_fixtures import rr1_events
from polymaker.m1234_sim.broker import CONSTRAINT_CONFLICT
from polymaker.m1234_sim.engine import market_meta
from polymaker.m1234_sim.events import Clock, encode
from polymaker.m1234_sim.report import read_state

PROJECT = Path(__file__).resolve().parents[1]
OUTPUT = PROJECT / 'research_outputs/m1234_sim'


@pytest.fixture
def cli_directory(tmp_path):
    if OUTPUT in tmp_path.parents:
        return tmp_path
    directory = OUTPUT / 'unit-tests' / str(uuid4())
    directory.mkdir(parents=True)
    return directory


def input_file(directory, mode):
    path = directory / (mode + '.jsonl.gz')
    with gzip.open(path, 'wt') as output:
        for row in rr1_events(mode):
            output.write(encode(row) + '\n')
    return path


def command(directory, label, *arguments, expected):
    values = os.environ.copy()
    values.update(PYTHONPATH='src:tests', PYTHONHASHSEED='0', PYTHONDONTWRITEBYTECODE='1')
    args = [sys.executable, '-m', 'polymaker.m1234_sim', *map(str, arguments)]
    result = subprocess.run(args, cwd=PROJECT, env=values, capture_output=True, text=True)
    (directory / (label + '.stdout')).write_text(result.stdout)
    (directory / (label + '.stderr')).write_text(result.stderr)
    (directory / (label + '.command.json')).write_text(json.dumps({
        'command': args, 'exit_code': result.returncode,
        'expected_exit_code': expected, 'cwd': str(PROJECT)}, indent=2))
    assert result.returncode == expected, result.stderr[-4000:]
    return result


@pytest.mark.parametrize('mode', ['filled-conflict', 'cancelled-partial-conflict'])
def test_failed_cli_replay_report_recover_and_settle_remain_failed(cli_directory, mode):
    directory = cli_directory
    source = input_file(directory, mode)
    output = directory / 'replayed'
    command(directory, 'replay', 'replay', '--input', source, '--output', output, expected=1)
    state = read_state(output / 'simulation.sqlite3')
    assert state['validity']['status'] == 'INVALID'
    assert len(state['validity']['failures']) == 2
    assert json.loads((output / 'failure.json').read_text())['reason'] == CONSTRAINT_CONFLICT
    report = json.loads((output / 'latest.json').read_text())
    assert report['status'] == 'FAIL' and report['valid_complete_windows'] == 0
    assert all(not account['sample_eligible'] for market in report['markets']
               for account in market['accounts'])
    result = command(directory, 'audit', 'audit', '--database', output / 'simulation.sqlite3', expected=1)
    arithmetic = json.loads(result.stdout)
    assert arithmetic['arithmetic_status'] == 'PASS' and arithmetic['acceptance_status'] == 'FAIL'
    command(directory, 'report', 'report', '--database', output / 'simulation.sqlite3',
            '--output', directory / 'reopened-report', expected=1)
    for operation in ['recover', 'settle']:
        target = directory / operation
        command(directory, operation, operation, '--input', source, '--output', target, expected=1)
        recovered = read_state(target / 'simulation.sqlite3')
        assert recovered['validity'] == state['validity']
        assert recovered['brokers'] == state['brokers']
        assert json.loads((target / 'recovery.json').read_text())['status'] == 'FAIL'


def test_normal_cli_produces_native_fills_settlement_and_valid_report(cli_directory):
    directory = cli_directory
    source = input_file(directory, 'normal')
    target = directory / 'normal-replay'
    command(directory, 'normal-replay', 'replay', '--input', source, '--output', target, expected=0)
    state = read_state(target / 'simulation.sqlite3')
    assert state['validity']['status'] == 'VALID' and state['resolution']['winner'] == 'YES'
    assert state['coverage']['complete_window']
    for broker in state['brokers'].values():
        orders = [order for order in broker['orders'].values() if order['intent']['strategy'] == 'M3']
        assert orders and orders[0]['filled'] == '5' and orders[0]['state'] == 'CANCELLED'
        assert broker['accounts']['M3']['cash'] == '22.525'
    command(directory, 'normal-audit', 'audit', '--database', target / 'simulation.sqlite3', expected=0)
    command(directory, 'normal-report', 'report', '--database', target / 'simulation.sqlite3',
            '--output', directory / 'normal-report', expected=0)


def test_missing_contract_field_cannot_become_valid_report_or_acceptance(cli_directory):
    directory = cli_directory
    source = input_file(directory, 'normal')
    target = directory / 'legacy-projection'
    command(directory, 'legacy-source', 'replay', '--input', source, '--output', target, expected=0)
    database = target / 'simulation.sqlite3'
    state = read_state(database)
    state.pop('validity')
    state.pop('snapshot_schema')
    # This is a new artificial compatibility fixture, never a historical record.
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE snapshots SET payload=? WHERE name='state'", (encode(state),))
    result = command(directory, 'legacy-audit', 'audit', '--database', database, expected=2)
    value = json.loads(result.stdout)
    assert value['arithmetic_status'] == 'PASS'
    assert value['acceptance_status'] == 'UNVERIFIED_LEGACY'
    command(directory, 'legacy-report', 'report', '--database', database,
            '--output', directory / 'legacy-report', expected=1)


async def test_initial_ticks_retry_only_transport_failures_and_leave_no_unbound_market(tmp_path):
    from polymaker.m1234_sim import runner

    meta = market_meta(rr1_events('normal')[0]['definition']['market'])
    expected = {token.token_id: '.01' for token in meta.tokens}
    events = []
    ticks = AsyncMock(side_effect=[httpx.ConnectTimeout('TLS timeout'), expected])
    run = SimpleNamespace(cfg={'initial_tick_read_attempts': 3}, api=SimpleNamespace(ticks=ticks),
                          log=lambda event, **_: events.append(event), directory=tmp_path)
    run.initial_ticks = runner.LiveRun.initial_ticks.__get__(run)
    assert await run.initial_ticks(meta) == expected
    assert ticks.await_count == 2 and events == ['INITIAL_TICKS_RETRY']

    ticks.reset_mock(side_effect=True)
    ticks.side_effect = httpx.ConnectTimeout('TLS timeout')
    events.clear()
    with pytest.raises(httpx.ConnectTimeout):
        await runner.LiveRun.market(run, meta)
    assert ticks.await_count == 3
    assert events == ['INITIAL_TICKS_RETRY', 'INITIAL_TICKS_RETRY', 'INITIAL_TICKS_FAILED']
    assert not (tmp_path / meta.slug).exists()

    ticks.reset_mock(side_effect=True)
    ticks.side_effect = ValueError('M1_PUBLIC_TICK_INVALID')
    with pytest.raises(ValueError, match='M1_PUBLIC_TICK_INVALID'):
        await runner.LiveRun.market(run, meta)
    assert ticks.await_count == 1


@pytest.mark.parametrize('mode', ['filled-conflict', 'cancelled-partial-conflict'])
async def test_coordinator_end_drain_latches_rr1_and_whole_run_fails(cli_directory, monkeypatch, mode):
    from polymaker.m1234_sim import runner

    tape = rr1_events(mode)
    definition = tape[0]['definition']
    clock = Clock(0, tape[0]['available_wall'])
    target_fault = next(index for index, row in enumerate(tape)
                        if row['kind'] == 'FRAME' and isinstance(row.get('raw'), dict)
                        and row['raw'].get('event_type') == 'tick_size_change')
    owned_feeds = []

    class API:
        async def clock(self):
            return definition['official_clock']

        async def market(self, slug):
            assert slug == definition['market']['slug']
            return market_meta(definition['market'])

        async def ticks(self, tokens):
            return definition['ticks']

        async def raw_market(self, *args, **kwargs):
            pytest.fail('Invalid market reached settlement query')

        async def close(self):
            pass

    class Feed:
        def __init__(self, meta, queue):
            self.stop_event = asyncio.Event()
            self.m1_pending_received = []
            self.failure = None
            self.queue = queue
            self.task = None
            owned_feeds.append(self)

        async def run(self):
            self.task = asyncio.current_task()
            await asyncio.Event().wait()

    monkeypatch.setattr(runner, 'VirtualPublicAPI', API)
    monkeypatch.setattr(runner, 'CaptureFeed', Feed)
    monkeypatch.setattr(runner, 'time', SimpleNamespace(monotonic=clock.monotonic, time=clock.time))
    monkeypatch.setattr(asyncio.get_running_loop(), 'add_signal_handler', lambda *args: None)
    cfg = deepcopy(definition['settings'])
    cfg.update(max_markets=12, max_seconds=4500, max_output_mib=4096, min_free_disk_mib=2048,
               max_rss_mib=512, input_queue_frames=2048, writer_queue_records=8192,
               markout_seconds=[1, 5, 30])
    run = runner.LiveRun(cli_directory / 'coordinator', cfg)
    injected = False

    async def refresh(engine=None):
        nonlocal injected
        assert engine is not None and not injected
        injected = True
        for row in tape[1:target_fault]:
            clock.set(row['available_mono'], row['available_wall'])
            payload = {key: value for key, value in row.items()
                       if key not in {'schema', 'seq', 'kind', 'available_mono', 'available_wall', 'generation'}}
            await run.record(engine, row['kind'], **payload)
        row = tape[target_fault]
        payload = {key: value for key, value in row.items()
                   if key not in {'schema', 'seq', 'kind', 'available_mono', 'available_wall', 'generation'}}
        owned_feeds[0].queue.put_nowait(('FRAME', payload))
        clock.set(306., tape[0]['available_wall'] + 306.)
        await run.record(engine, 'RESOURCE')
        assert engine.bounds()[0] >= 300 and engine.coverage['warmed_before_t15']
        await asyncio.sleep(0)

    run.refresh = refresh
    with pytest.raises(ValueError, match=CONSTRAINT_CONFLICT):
        await run.run()
    completion = json.loads((run.directory / 'completion.json').read_text())
    assert completion['status'] == 'FAIL' and completion['complete_windows'] == 0
    assert completion['invalid_markets'] == [definition['market']['slug']]
    assert owned_feeds[0].task.done() and owned_feeds[0].task.cancelled()
    engine = run.engines[0]
    state = read_state(engine.directory / 'snapshot.sqlite3')
    assert state['validity']['status'] == 'INVALID'
    assert len(state['validity']['failures']) == 2
    assert all(row['reason'] == CONSTRAINT_CONFLICT for row in state['validity']['failures'])
    assert json.loads((run.directory / 'latest.json').read_text())['status'] == 'FAIL'
    values = os.environ.copy()
    values['PYTHONPATH'] = 'src'
    verified = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, 'ops/m1234_sim_verify.py', '--run-dir', str(run.directory),
         '--output', str(cli_directory / 'acceptance.json')],
        cwd=PROJECT, env=values, capture_output=True, text=True,
    )
    assert verified.returncode == 1
    acceptance = json.loads((cli_directory / 'acceptance.json').read_text())
    assert acceptance['status'] == 'FAIL'
    assert any('invalid' in error for error in acceptance['errors'])
