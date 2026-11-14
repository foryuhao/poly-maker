"""Independent actual-coordinator RR2 probe, with network fully replaced by fixture adapters.
The original coordinator fixture is reused, adding only a persistent snapshot failure
once the valid economic prefix is committed. No END or validity fields are manufactured.
"""
import asyncio
import json
import os
import subprocess
import sys
from copy import deepcopy
from types import SimpleNamespace

import pytest

from m1234_rr1_fixtures import rr1_events
from polymaker.m1234_sim.engine import Engine, market_meta
from polymaker.m1234_sim.events import Clock
from polymaker.m1234_sim.report import read_state


@pytest.mark.parametrize('prior_healthy', [False, True])
@pytest.mark.parametrize('mode', ['filled-conflict', 'cancelled-partial-conflict'])
async def test_coordinator_persistent_snapshot_failure_never_publishes_success(tmp_path, monkeypatch, mode, prior_healthy):
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
    run = runner.LiveRun(tmp_path / 'coordinator', cfg)
    if prior_healthy:
        previous = Engine(tmp_path / 'previous-control')
        prior_tape = rr1_events('normal')
        old_slug = prior_tape[0]['definition']['market']['slug']
        new_slug = 'btc-updown-5m-' + str(int(old_slug.rsplit('-', 1)[1]) - 300)
        prior_tape = json.loads(json.dumps(prior_tape).replace(old_slug, new_slug))
        def shift_wall(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in {'available_wall', 'received_wall', 'sent_wall'}:
                        value[key] = item - 300
                    elif key == 'timestamp':
                        value[key] = str(float(item) - 300000)
                    elif key == 'official_clock':
                        item[0] -= 300
                    else:
                        shift_wall(item)
            elif isinstance(value, list):
                for item in value:
                    shift_wall(item)
        shift_wall(prior_tape)
        for event in prior_tape:
            await previous.ingest(event)
        previous.persist()
        assert previous.ended and previous.validity['status'] == 'VALID'
        run.engines.append(previous)
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
        engine.persist()
        def fail_snapshot(*args):
            raise OSError('INDEPENDENT_CONTINUOUS_SNAPSHOT_FAILURE')
        engine.store.snapshot = fail_snapshot
        assert engine.bounds()[0] >= 300 and engine.coverage['warmed_before_t15']
        await asyncio.sleep(0)

    run.refresh = refresh
    with pytest.raises(OSError, match='INDEPENDENT_CONTINUOUS_SNAPSHOT_FAILURE'):
        await run.run()
    engine = run.engines[-1]
    latest = json.loads((run.directory / 'latest.json').read_text())
    prefix = read_state(engine.store.path)
    observation = {
        'prior_healthy': prior_healthy,
        'fault': 'Snapshot failure remains injected through actual run finally',
        'engine_ended': engine.ended,
        'memory_validity': engine.validity,
        'committed_snapshot_validity': prefix['validity'],
        'published_report': latest,
        'completion_exists': (run.directory / 'completion.json').exists(),
        'feed_task_done': owned_feeds[0].task.done(),
    }
    for instance in run.engines:
        if not instance.closed:
            instance.store.db.rollback()
            instance.store.db.close()
            instance.closed = True
    for recorder in run.recorders.values():
        recorder.close()
    run.logs.close()
    for operation in ['audit', 'report']:
        command = [sys.executable, '-m', 'polymaker.m1234_sim', operation,
                   '--database', str(engine.store.path)]
        if operation == 'report':
            command += ['--output', str(tmp_path / 'reopened-report')]
        result = await asyncio.to_thread(subprocess.run, command, capture_output=True, text=True,
                                        env={**os.environ, 'PYTHONPATH': 'src:tests', 'PYTHONDONTWRITEBYTECODE':'1'})
        (tmp_path / (operation + '.stdout')).write_text(result.stdout)
        (tmp_path / (operation + '.stderr')).write_text(result.stderr)
        observation[operation + '_exit_code'] = result.returncode
    (tmp_path / 'observation.json').write_text(json.dumps(observation, indent=2, default=str))
    assert engine.validity['status'] == 'INVALID'
    assert observation['audit_exit_code'] == observation['report_exit_code'] == 1
    assert latest['status'] == 'FAIL', observation
    assert any(m['market']['slug'] == engine.meta.slug and m['validity']['status']=='INVALID'
               for m in latest['markets']), observation
