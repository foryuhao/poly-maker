"""Single public stream and shared real Maker account for M2/M3/M4.

Explicit --live is mandatory for account access. Observation mode is a smoke
test only and cannot satisfy real execution acceptance.
"""

import argparse
import asyncio
import fcntl
import hashlib
import json
import signal
import subprocess
import time
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

from polymaker.config import Config, RDPM1Config, RDPObiConfig, RDPSMConfig
from polymaker.domain import MarketMeta, TokenMeta
from polymaker.m1_virtual_api import VirtualPublicAPI
from polymaker.m2_config import M2Config
from polymaker.m2_store import encode
from polymaker.m234_execution import LiveExecution
from polymaker.m234_ledger import LiveLedger
from polymaker.m234_signal import M234Feed
from polymaker.rdp_sm import OFFICIAL_HOSTS
from polymaker.rdp_sm_execution import _execution_identity, _ExecutionReads
from polymaker.rdp_sm_fence import AccountLease

ROOT = Path(__file__).resolve().parents[2]
FILES = ('m234_live.py', 'm234_signal.py', 'm234_ledger.py', 'm234_execution.py',
    'm2_signal.py', 'm2_config.py', 'rdp_sm_m1_signal.py', 'rdp_sm_ca.py',
    'rdp_sm_ba.py', 'rdp_sm_marketdata.py', 'rdp_sm_execution.py',
    'execution/gateway.py', 'userstream/parse.py', 'config.py')


def fingerprint():
    return {name: hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest() for name in FILES}


def foreign_writer():
    for name in ('polymaker-m1-live.service', 'poly-maker.service', 'polymaker-ca.service'):
        result = subprocess.run(['systemctl', '--user', 'show', name, '-p', 'MainPID', '--value'],
                                capture_output=True, text=True, timeout=5, check=False)
        if result.returncode == 0 and result.stdout.strip().isdigit() and int(result.stdout.strip()) > 0:
            return name
    return None


def atomic_json(path, data):
    temp = path.with_suffix('.tmp')
    temp.write_text(encode(data)+'\n')
    temp.replace(path)


class Runner:
    def __init__(self, directory, *, live=False, config_dir='config', max_seconds=None,
                 partial_smoke=False):
        self.directory, self.live = Path(directory), live
        self.config_dir, self.max_seconds = config_dir, max_seconds
        self.partial_smoke = partial_smoke
        if live and partial_smoke:
            raise ValueError('PARTIAL_SMOKE_CANNOT_TRADE')
        self.stop, self.api = asyncio.Event(), VirtualPublicAPI()
        self.clock = None
        self.reads = self.lease = self.ledger = self.feed = self.execution = None
        self.sources = fingerprint()
        self.version = hashlib.sha256(encode({'sources': self.sources, 'live': live,
            'M2_rule': 'all_candidates', 'risk': 5, 'slots': 1}).encode()).hexdigest()
        self.started = time.time()
        self.runtime = {'state': 'STARTING', 'mode': 'LIVE' if live else 'OBSERVATION_ONLY'}
        self.last_heartbeat = self.last_report = self.last_resolution = 0.
        self.resolution_task = None

    def bounds(self, epoch, now=None):
        now = time.monotonic() if now is None else now
        server, sent, received = self.clock
        return server+now-received-epoch, server+1+now-sent-epoch

    def log(self, event, **payload):
        print(encode({'wall': time.time(), 'event': event, 'version': self.version,
                      'mode': 'LIVE' if self.live else 'OBSERVATION_ONLY', **payload}), flush=True)

    async def open(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = (self.directory/'runner.lock').open('a+')
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if self.live:
            conflict = foreign_writer()
            if conflict:
                raise RuntimeError('EXISTING_ACCOUNT_WRITER:'+conflict)
            original = Config.load(self.config_dir)
            if any(getattr(original.wallet, k).rstrip('/') != v for k, v in OFFICIAL_HOSTS.items()):
                raise ValueError('M234_OFFICIAL_ENDPOINTS_REQUIRED')
            cfg = original.model_copy(deep=True)
            cfg.rdp_sm = RDPSMConfig(normal_entry_mode='m1_v1', m1=RDPM1Config(
                observation_only=False, record_events=False, signal=M2Config().signal))
            self.reads = _ExecutionReads(cfg)
            await self.reads.connect_account()
            _execution_identity(self.reads)
            self.lease = await asyncio.to_thread(AccountLease, cfg, self.reads.gateway.funder)
            if (self.lease.state.get('status') == 'UNRESOLVED'
                    and self.lease.state.get('strategy') != 'm234_live_v1'):
                raise RuntimeError('PREVIOUS_ACCOUNT_OWNER_UNRESOLVED')
            self.ledger = LiveLedger(self.directory/'state.sqlite3', self.reads.gateway.funder)
            self.lease.write({'strategy': 'm234_live_v1', 'status': 'UNRESOLVED',
                              'ledger': str(self.ledger.path.resolve())})
            for row in self.ledger.rows():
                if not row['started'] and row['state'] in {'RESERVED', 'PREPARED'}:
                    self.ledger.outcome(row['id'], 'ABORTED', 'RESTART_BEFORE_POST')
        else:
            self.ledger = LiveLedger(self.directory/'state.sqlite3', 'observation-only')
        atomic_json(self.directory/f'definition-{self.version[:16]}.json',
            {'version': self.version, 'sources': self.sources, 'live': self.live,
             'M2_rule': 'all_candidates', 'risk_usdc': 5, 'max_tasks_per_strategy_side_market': 1})

    def drain(self):
        if self.feed:
            with self.feed.lock:
                rows = []
                for queue in self.feed.hub.events.values():
                    rows.extend(queue)
                    queue.clear()
            if rows:
                self.ledger.event_batch(rows)

    def report(self):
        self.drain()
        entries = self.ledger.rows()
        summary = {}
        for strategy in ('M2', 'M3', 'M4'):
            rows = [r for r in entries if r['strategy'] == strategy]
            summary[strategy] = {'entry_tasks': len(rows),
                'posts': sum(r['started'] for r in rows),
                'confirmed_filled_orders': sum(float(r['filled']) > 0 for r in rows),
                'confirmed_cost': str(sum((Decimal(r['spent']) for r in rows), Decimal(0))),
                'unresolved_orders': sum(r['started'] and not r['terminal'] for r in rows)}
            settled_cost, settled_pnl, settled = Decimal(0), Decimal(0), 0
            for row in rows:
                winner = self.ledger.db.execute('SELECT winner FROM markets WHERE slug=?', (row['slug'],)).fetchone()[0]
                if winner and Decimal(row['filled']) > 0:
                    settled += 1
                    settled_cost += Decimal(row['spent'])
                    settled_pnl += ((Decimal(row['filled']) if row['outcome'] == winner else Decimal(0))
                                    - Decimal(row['spent']))
            summary[strategy].update(settled_filled_orders=settled, settled_cost=str(settled_cost),
                pnl_before_fees=str(settled_pnl), fees_status='NOT_YET_RECONCILED')
        result = {'version': self.version, 'mode': 'LIVE' if self.live else 'OBSERVATION_ONLY',
            'started_wall': self.started, 'observed_wall': time.time(),
            'uptime_sec': time.time()-self.started, 'runtime': self.runtime,
            'strategies': summary, 'reserved_usdc': str(self.ledger.reserved()),
            'order_uncertainty': self.ledger.uncertain(),
            'source_hashes': self.sources}
        atomic_json(self.directory/'latest.json', result)

    async def recover(self):
        pending = [r for r in self.ledger.rows() if r['started'] and not r['terminal']]
        if not self.live or not pending:
            return True
        raw = json.loads(self.ledger.db.execute('SELECT metadata FROM markets WHERE slug=?',
                                               (pending[0]['slug'],)).fetchone()[0])
        raw['tokens'] = tuple(TokenMeta(**t) for t in raw['tokens'])
        meta = MarketMeta(**raw)
        feed = M234Feed(meta, RDPObiConfig(), normal_entry_mode='m1_v1')
        cleanup = LiveExecution(self.reads, feed, self.ledger, lambda now: (-1, 1000), self.stop)
        for row in pending:
            await cleanup.cancel(row)
        await cleanup.reconcile()
        return not any(r['started'] and not r['terminal'] for r in self.ledger.rows())

    async def resolutions(self):
        now = time.monotonic()
        if self.resolution_task and self.resolution_task.done():
            self.resolution_task.result()
            self.resolution_task = None
        if self.resolution_task is None and now-self.last_resolution >= 15:
            self.last_resolution = now
            self.resolution_task = asyncio.create_task(self.resolve_one())

    async def resolve_one(self):
        rows = self.ledger.db.execute('SELECT * FROM markets WHERE ended IS NOT NULL AND winner IS NULL '
                                      'ORDER BY started LIMIT 16').fetchall()
        for row in rows:
            meta = json.loads(row['metadata'])
            expected = {'slug': row['slug'], 'condition_id': meta['condition_id'],
                'token_ids': {'YES': meta['tokens'][0]['token_id'], 'NO': meta['tokens'][1]['token_id']}}
            try:
                result = await self.api.resolution(expected)
                if result:
                    with self.ledger.lock, self.ledger.db:
                        self.ledger.db.execute('UPDATE markets SET winner=?,resolution=? WHERE slug=?',
                            (result['winner'], encode(result), row['slug']))
                    self.ledger.event('OFFICIAL_RESOLUTION', result)
            except Exception as exc:
                self.log('RESOLUTION_PENDING', slug=row['slug'], error_type=type(exc).__name__)

    async def market(self, meta, end_at):
        epoch = int(meta.slug.rsplit('-', 1)[1])
        self.feed = M234Feed(meta, RDPObiConfig(), normal_entry_mode='m1_v1',
                            m1_config=RDPM1Config(record_events=False))
        self.feed.verify_ticks(await self.api.ticks(self.feed.books))
        if self.reads:
            self.reads.feed = self.feed
            self.execution = LiveExecution(self.reads, self.feed, self.ledger,
                lambda now: self.bounds(epoch, now) if now-self.clock[2] <= 60 else (-1, 1000), self.stop)
        self.feed.task = asyncio.create_task(self.feed.run(), name='m234-single-public-feed')
        self.runtime = {'state': 'OBSERVING', 'slug': meta.slug, 'observer_failures': {}}
        clock_due, clock_task = time.monotonic()+30, None
        previous, loop_max = time.monotonic(), 0.
        try:
            while not self.stop.is_set() and time.monotonic() < end_at:
                now = time.monotonic()
                loop_max = max(loop_max, max(0., now-previous-.025)*1000)
                previous = now
                lower, upper = self.bounds(epoch, now)
                if lower >= 300:
                    break
                fresh = now-self.clock[2] <= 60
                self.runtime.update(elapsed=lower, pending_frames=len(self.feed.m1_pending_received),
                    max_loop_delay_ms=loop_max, observer_failures=dict(self.feed.hub.failures),
                    signal_counts=dict(self.feed.hub.counts))
                if self.feed.hub.failures:
                    self.ledger.event('OBSERVER_FAILURES', self.feed.hub.failures)
                    raise RuntimeError('M234_OBSERVER_FAILURE')
                if not fresh:
                    self.feed.invalidate('PUBLIC_CLOCK_STALE')
                if upper >= 240 and not self.feed.stop_event.is_set():
                    await self.feed.close()
                if self.execution:
                    await self.execution.advance()
                    self.runtime.update(execution_reason=self.execution.reason,
                                        execution_fatal=self.execution.fatal)
                    if self.execution.fatal:
                        raise RuntimeError('M234_EXECUTION_INTEGRITY_FAILURE')
                if clock_task and clock_task.done():
                    try:
                        self.clock = clock_task.result()
                    except Exception as exc:
                        self.log('CLOCK_UNAVAILABLE', error_type=type(exc).__name__)
                    clock_task = None
                if now >= clock_due and clock_task is None:
                    clock_task = asyncio.create_task(self.api.clock())
                    clock_due = now+30
                if now-self.last_heartbeat >= 10:
                    self.last_heartbeat = now
                    self.log('HEARTBEAT', **self.runtime)
                    self.report()
                if now-self.last_report >= 60:
                    self.last_report = now
                    if fingerprint() != self.sources:
                        raise RuntimeError('M234_RUNNING_SOURCE_CHANGED')
                    if self.live and foreign_writer():
                        raise RuntimeError('M234_EXTERNAL_WRITER_STARTED')
                self.drain()
                await self.resolutions()
                await asyncio.sleep(.025)
        finally:
            await self.feed.close()
            if self.execution:
                await self.execution.drain()
                self.execution = None
            self.drain()
            if clock_task:
                clock_task.cancel()
                await asyncio.gather(clock_task, return_exceptions=True)
            with self.ledger.lock, self.ledger.db:
                self.ledger.db.execute('UPDATE markets SET ended=? WHERE slug=?', (time.time(), meta.slug))

    async def run(self):
        end_at = time.monotonic()+(self.max_seconds if self.max_seconds is not None else 10**12)
        try:
            await self.open()
            self.clock = await self.api.clock()
            while not self.stop.is_set() and time.monotonic() < end_at:
                if not await self.recover():
                    self.runtime = {'state': 'WAIT_OWN_ORDER_RECOVERY'}
                    self.report()
                    await asyncio.sleep(3)
                    continue
                server, _, received = self.clock
                epoch = int((server+time.monotonic()-received)//300)*300
                slug = f'btc-updown-5m-{epoch}'
                existing = self.ledger.db.execute('SELECT 1 FROM markets WHERE slug=?', (slug,)).fetchone()
                if existing or (not self.partial_smoke and self.bounds(epoch)[1] >= 15):
                    self.runtime = {'state': 'WAIT_FULL_MARKET', 'slug': slug}
                    self.report()
                    await self.resolutions()
                    await asyncio.sleep(min(5, max(.05, epoch+300-(server+time.monotonic()-received))))
                    self.clock = await self.api.clock()
                    continue
                meta = await self.api.market(slug)
                if self.ledger.start_market(asdict(meta), self.version):
                    self.log('MARKET_START', slug=slug)
                    await self.market(meta, end_at)
                self.clock = await self.api.clock()
            self.runtime['state'] = 'STOPPED'
        except Exception as exc:
            self.runtime = {'state': 'FAILED', 'error_type': type(exc).__name__,
                            'reason': str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__}
            self.log('RUN_FAILED', **self.runtime)
            raise
        finally:
            if self.ledger:
                self.report()
            if self.resolution_task:
                await asyncio.gather(self.resolution_task, return_exceptions=True)
            await self.api.close()
            if self.reads:
                self.reads.close()
            if self.lease:
                if self.ledger and not any(r['started'] and not r['terminal'] for r in self.ledger.rows()):
                    self.lease.verified_closed()
                self.lease.close()
            if self.ledger:
                self.ledger.close()
            if hasattr(self, 'lock'):
                self.lock.close()


async def main_async(args):
    runner = Runner(args.output_dir, live=args.live, config_dir=args.config_dir,
                    max_seconds=args.max_seconds, partial_smoke=args.partial_smoke)
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, runner.stop.set)
    await runner.run()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--config-dir', default='config')
    parser.add_argument('--output-dir', type=Path, default=ROOT/'research_outputs/m234_live')
    parser.add_argument('--max-seconds', type=float)
    parser.add_argument('--partial-smoke', action='store_true', help='Observation-only current-market smoke')
    args = parser.parse_args()
    args.output_dir = args.output_dir.resolve()
    if ROOT/'research_outputs' not in args.output_dir.parents:
        parser.error('Output must be under project research_outputs')
    asyncio.run(main_async(args))


if __name__ == '__main__':
    main()
