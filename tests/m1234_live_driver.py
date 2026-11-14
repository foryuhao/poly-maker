"""Offline adapters around the actual run CLI, with faults held through product return."""
import argparse
import asyncio
import gzip
import json
import runpy
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from m1234_rr1_fixtures import rr1_events
from polymaker.m1234_sim import report, runner
from polymaker.m1234_sim.engine import Engine, market_meta
from polymaker.m1234_sim.events import Clock, encode, write_json
from polymaker.m1234_sim.report import read_state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--mode', choices=['normal', 'filled-conflict', 'cancelled-partial-conflict'], default='filled-conflict')
    parser.add_argument('--prior-healthy', action='store_true')
    parser.add_argument('--markets', type=int, choices=[1, 12], default=1)
    parser.add_argument('--fault', choices=['snapshot', 'none', 'report-task', 'finish-reports', 'backup',
        'recorder-close', 'completion', 'terminal-files', 'all-failure-outputs', 'api-close',
        'policy-close', 'database-close', 'cancel', 'stop'], default='snapshot')
    args = parser.parse_args()
    if args.markets == 12 and (args.mode != 'normal' or args.fault != 'none' or args.prior_healthy):
        parser.error('Twelve-market offline control requires normal mode without faults or a prior fixture')
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    tape = rr1_events(args.mode)
    definition = tape[0]['definition']
    tapes = {definition['market']['slug']: tape}
    base_epoch = int(definition['market']['slug'].rsplit('-', 1)[1])
    if args.markets == 12:
        for index in range(1, 12):
            offset = index * 300
            slug = 'btc-updown-5m-' + str(base_epoch + offset)
            condition = '0x' + format(index, '064x')
            shifted = json.loads(json.dumps(tape).replace(definition['market']['slug'], slug)
                .replace(definition['market']['condition_id'], condition))

            def shift_times(value, seconds=offset):
                if isinstance(value, dict):
                    for key, item in value.items():
                        if key in {'available_wall', 'received_wall', 'sent_wall',
                                   'available_mono', 'received_mono', 'sent_mono',
                                   'missing_since_received_mono'} and item is not None:
                            value[key] += seconds
                        elif key == 'pending' and isinstance(item, list):
                            value[key] = [part + seconds for part in item]
                        elif key == 'timestamp':
                            value[key] = str(float(item) + seconds * 1000)
                        elif key == 'official_clock':
                            value[key] = [part + seconds for part in item]
                        else:
                            shift_times(item, seconds)
                elif isinstance(value, list):
                    for item in value:
                        shift_times(item, seconds)
            shift_times(shifted)
            tapes[slug] = shifted
    clock = Clock(0, tape[0]['available_wall'])
    feeds, instances = [], []
    state = {'injected': False, 'snapshot_attempts': 0, 'api_closed': False, 'seen_markets': []}
    cfg = deepcopy(definition['settings'])
    cfg.update(max_markets=args.markets if args.mode == 'normal' else 12, max_seconds=4500,
        max_output_mib=4096, min_free_disk_mib=2048, max_rss_mib=512,
        input_queue_frames=2048, writer_queue_records=8192, markout_seconds=[1, 5, 30])
    write_json(output/'injection.json', {'artificial_fixture': True, 'mode': args.mode,
        'prior_healthy': args.prior_healthy, 'fault': args.fault, 'settings': cfg, 'markets': args.markets,
        'source': 'Existing unchanged rr1_events native policy fixture with current implementation identity',
        'scope': 'Offline API/feed adapters and controlled product failure channels only. No historical INIT rewritten.'})
    with gzip.open(output/'fixture-source.jsonl.gz', 'wt') as stream:
        for row in tape:
            stream.write(encode(row)+'\n')

    class API:
        async def clock(self):
            if args.markets == 12:
                return [base_epoch + clock.mono, clock.mono, clock.mono]
            return definition['official_clock']

        async def market(self, slug):
            assert slug in tapes
            return market_meta(tapes[slug][0]['definition']['market'])

        async def ticks(self, tokens):
            return definition['ticks']

        async def raw_market(self, *values, **keywords):
            assert args.mode == 'normal', 'Invalid fixture reached public settlement'
            return next(row['raw'] for row in tapes[values[0]] if row['kind'] == 'SETTLEMENT')

        async def close(self):
            state['api_closed'] = True
            if args.fault == 'api-close':
                raise OSError('ARTIFICIAL_API_CLOSE_FAILURE')

    class Feed:
        def __init__(self, meta, queue):
            self.stop_event = asyncio.Event()
            self.m1_pending_received, self.failure = [], None
            self.queue, self.task, self.closed = queue, None, False
            feeds.append(self)

        async def run(self):
            self.task = asyncio.current_task()
            try:
                await asyncio.Event().wait()
            finally:
                self.closed = True

    def snapshot_failure(*values):
        state['snapshot_attempts'] += 1
        raise OSError('ARTIFICIAL_CONTINUOUS_SNAPSHOT_FAILURE')

    def failing(*values, **keywords):
        raise OSError('ARTIFICIAL_'+args.fault.upper().replace('-', '_')+'_FAILURE')

    original_write = runner.write_json

    def failed_output(path, value):
        path = Path(path)
        if args.fault == 'all-failure-outputs':
            failing()
        if path.name == 'completion.json' or args.fault == 'terminal-files' and path.name.endswith('.run-failure.json'):
            failing()
        return original_write(path, value)

    original_record = runner.LiveRun.record

    async def record(self, engine, kind, **payload):
        if kind == 'INIT':
            payload['definition'] = {**payload['definition'], 'artificial_fixture':
                {'kind': 'OFFLINE_COORDINATOR_FIXTURE', 'source': 'm1234_live_driver'}}
        return await original_record(self, engine, kind, **payload)

    async def add_previous(run):
        prior = rr1_events('normal')
        old = prior[0]['definition']['market']['slug']
        new = 'btc-updown-5m-'+str(int(old.rsplit('-', 1)[1])-300)
        prior = json.loads(json.dumps(prior).replace(old, new))

        def shift(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in {'available_wall', 'received_wall', 'sent_wall'}:
                        value[key] -= 300
                    elif key == 'timestamp':
                        value[key] = str(float(item)-300000)
                    elif key == 'official_clock':
                        item[0] -= 300
                    else:
                        shift(item)
            elif isinstance(value, list):
                for item in value:
                    shift(item)
        shift(prior)
        previous = Engine(run.directory/new)
        with gzip.open(previous.directory/'input.jsonl.gz', 'wt') as stream:
            for row in prior:
                stream.write(encode(row)+'\n')
                await previous.ingest(row)
        previous.persist()
        run.engines.append(previous)
        write_json(output/'previous-before.json', previous.snapshot())

    async def refresh(self, engine=None):
        assert engine is not None and engine.meta.slug not in state['seen_markets']
        state['seen_markets'].append(engine.meta.slug)
        state['injected'] = True
        current = tapes[engine.meta.slug]
        offset = engine.epoch - base_epoch
        if args.prior_healthy:
            await add_previous(self)
        boundary = next(index for index, row in enumerate(current)
            if row['kind'] == ('END' if args.mode == 'normal' else 'FRAME')
            and (args.mode == 'normal' or isinstance(row.get('raw'), dict)
                 and row['raw'].get('event_type') == 'tick_size_change'))
        for row in current[1:boundary]:
            clock.set(row['available_mono'], row['available_wall'])
            payload = {key: value for key, value in row.items()
                if key not in {'schema', 'seq', 'kind', 'available_mono', 'available_wall', 'generation'}}
            if args.markets == 12 and row['kind'] == 'CLOCK':
                payload['official_clock'] = await self.api.clock()
            await self.record(engine, row['kind'], **payload)
        if args.mode != 'normal':
            row = current[boundary]
            payload = {key: value for key, value in row.items()
                if key not in {'schema', 'seq', 'kind', 'available_mono', 'available_wall', 'generation'}}
            feeds[-1].queue.put_nowait(('FRAME', payload))
        end = offset + (300 if args.markets == 12 else 306)
        clock.set(end, tape[0]['available_wall'] + end)
        await self.record(engine, 'RESOURCE')
        engine.persist()
        write_json(output/'committed-before-fault.json', read_state(engine.store.path))
        if args.fault in {'snapshot', 'all-failure-outputs'}:
            engine.store.snapshot = snapshot_failure
        elif args.fault == 'report-task':
            runner.summary = failing
        elif args.fault == 'finish-reports':
            self.finish_reports = failing
        elif args.fault == 'backup':
            engine.store.backup = failing
        elif args.fault == 'recorder-close':
            recorder = self.recorders[engine.directory.name]
            close = recorder.close

            def close_with_failure():
                close()
                failing()
            recorder.close = close_with_failure
        elif args.fault == 'policy-close':
            engine.m1_policies[0].closed = False
            engine.m1_policies[0].close = failing
            engine.m1_policies[0].abort = failing
        elif args.fault == 'database-close':
            close = engine.store.close

            def close_with_failure(**keywords):
                close(**keywords)
                failing()
            engine.store.close = close_with_failure
        if args.fault in {'completion', 'terminal-files', 'all-failure-outputs'}:
            runner.write_json = failed_output
        if args.fault == 'all-failure-outputs':
            import polymaker.m1234_sim.store as store
            store.write_json = failing
            report.write_json = failing
            engine.store.backup = failing
            original_logs = self.logs

            class UnwritableLog:
                @property
                def closed(self):
                    return original_logs.closed

                def write(self, value):
                    failing()

                def flush(self):
                    failing()

                def close(self):
                    original_logs.close()
            self.logs = UnwritableLog()
        await asyncio.sleep(0)
        if args.fault == 'cancel':
            raise asyncio.CancelledError('ARTIFICIAL_RUN_CANCELLATION')
        if args.fault == 'stop':
            self.stop.set()

    original_run = runner.LiveRun.run

    async def observed_run(self):
        instances.append(self)
        error, result = None, None
        try:
            result = await original_run(self)
            return result
        except BaseException as exc:
            error = {'type': type(exc).__name__, 'message': str(exc)}
            raise
        finally:
            members = []
            for engine in self.engines:
                try:
                    engine.store.db.execute('SELECT 1')
                    db_closed = False
                except Exception:
                    db_closed = True
                members.append({'directory': str(engine.directory), 'engine_closed': engine.closed,
                    'ended': engine.ended,
                    'database_closed': db_closed, 'policies_closed': [p.closed for p in engine.m1_policies],
                    'snapshot_fault_still_installed': engine.store.snapshot is snapshot_failure,
                    'memory_state': engine.snapshot()})
            write_json(output/'product-return.json', {'exception': error, 'result': result,
                'run_identity': self.run_identity, 'terminal_saved': getattr(self, 'terminal_saved', False),
                'terminal': getattr(self, 'terminal', None), 'cleanup_errors': self.cleanup_errors,
                'members': members, 'feeds_closed': [feed.closed and feed.task.done() for feed in feeds],
                'recorders_closed': {key: not rec.thread.is_alive() for key, rec in self.recorders.items()},
                'logs_closed': self.logs.closed, 'adapter_state': state,
                'capture_point': 'Actual product run returned or threw. Test fallback cleanup has not started.'})

    runner.VirtualPublicAPI, runner.CaptureFeed = API, Feed
    runner.time = SimpleNamespace(monotonic=clock.monotonic, time=clock.time)
    runner.settings = lambda *values: deepcopy(cfg)
    runner.LiveRun.record, runner.LiveRun.refresh, runner.LiveRun.run = record, refresh, observed_run
    original_signal = asyncio.unix_events._UnixSelectorEventLoop.add_signal_handler
    asyncio.unix_events._UnixSelectorEventLoop.add_signal_handler = lambda *values: None
    sys.argv = ['polymaker.m1234_sim', 'run', '--config', str(output/'injection.json'),
        '--contract', str(output/'injection.json'), '--output', str(output/'run')]
    write_json(output/'entrypoint.json', {'argv': sys.argv, 'module': 'polymaker.m1234_sim',
        'entry': 'runpy __main__ calls actual main/run path', 'fault_held_through_product_return': True})
    try:
        runpy.run_module('polymaker.m1234_sim', run_name='__main__')
    finally:
        asyncio.unix_events._UnixSelectorEventLoop.add_signal_handler = original_signal
        # Last-resort test cleanup is recorded separately and cannot satisfy product assertions.
        leftovers = []
        for instance in instances:
            for engine in instance.engines:
                for policy in engine.m1_policies:
                    if not policy.closed:
                        policy.e.ledger.close()
                        leftovers.append('policy:'+str(engine.directory)+':'+policy.e.profile)
                if not engine.store.closed:
                    engine.store.db.rollback()
                    engine.store.db.close()
                    leftovers.append('database:'+str(engine.directory))
            for key, recorder in instance.recorders.items():
                if recorder.thread.is_alive():
                    from polymaker.m1234_sim.events import Recorder
                    Recorder.close(recorder)
                    leftovers.append('recorder:'+key)
        write_json(output/'test-fallback-cleanup.json', {'leftovers': leftovers,
            'scope': 'Excluded from product success. Product-return assertions use the earlier observation.'})


if __name__ == '__main__':
    main()
