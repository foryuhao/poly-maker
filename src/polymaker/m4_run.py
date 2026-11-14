"""Independent M4 live/baseline runner. Import and --check are offline and inert."""
import argparse
import asyncio
import fcntl
import hashlib
import json
import signal
from contextlib import suppress
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from polymaker.config import Config, RDPObiConfig, RDPSMConfig
from polymaker.m4_baseline import resolutions
from polymaker.m4_config import STRATEGY, M4Config
from polymaker.m4_control import StopControl, atomic_json
from polymaker.m4_execution import M4Executor, M4Market
from polymaker.m4_fill_model import POLICY as BASELINE_FILL_POLICY
from polymaker.m4_frequency_signal import PARAMETERS
from polymaker.m4_journal import M4RiskJournal
from polymaker.m4_signals import M4Feed
from polymaker.m4_state import M4Ledger
from polymaker.m4_virtual import VirtualReads
from polymaker.maker_http import sdk_http1_transport
from polymaker.maker_public import VirtualPublicAPI
from polymaker.rdp_sm import OFFICIAL_HOSTS
from polymaker.rdp_sm_execution import _ExecutionReads
from polymaker.rdp_sm_writer import AsyncRiskWriter

VIRTUAL_IDENTITY = '0x'+'0'*39+'1'


def paths(project, live):
    project = Path(project).resolve()
    root = project/'research_outputs'/('m4_live' if live else 'm4_virtual')
    # Reject symlinked roots: M4 may never silently point back to M1 state.
    if root.resolve() != root or root.is_symlink():
        raise ValueError('M4_DATA_ROOT_SYMLINK_FORBIDDEN')
    return root


def sources():
    root = Path(__file__).parent
    names = ['m4_run.py', 'm4_config.py', 'm4_execution.py', 'm4_state.py', 'm4_types.py',
             'm4_signals.py', 'm4_rounds.py', 'm4_frequency_signal.py', 'm4_virtual.py',
             'm4_journal.py', 'm4_baseline.py', 'm4_fill_model.py', 'm4_control.py', 'maker_queue.py', 'maker_http.py', 'maker_retry.py',
             'maker_account.py', 'maker_public.py', 'm2_memory.py', 'rdp_sm_marketdata.py',
             'rdp_sm_execution.py', 'execution/gateway.py']
    return {name: hashlib.sha256((root/name).read_bytes()).hexdigest() for name in names}


class Executor(M4Executor):
    def __init__(self, *args, control, live, **kwargs):
        self.live, self.market_halt, self._fatal = live, None, None
        super().__init__(*args, **kwargs)
        self.control, self.live = control, live
        if live:
            from polymaker.m4_live_halt import MarketHalt
            self.market_halt = MarketHalt(self.ledger.path, self.slug, self.session_id)
            self._fatal = self.market_halt.reason

    @property
    def fatal(self):
        if (self.live and self.market_halt is not None and self._fatal is None
                and getattr(self.journal, 'failure', None) is not None):
            # The writer thread reports failures asynchronously; write() itself
            # does not raise. Persist them when the execution loop observes them.
            self.fatal = 'M4_JOURNAL_WRITE_FAILED'
        return self._fatal

    @fatal.setter
    def fatal(self, reason):
        if not self.live or self.market_halt is None:
            self._fatal = reason
            return
        if reason is not None:
            self._fatal = self._fatal or reason  # Inhibit immediately, before disk I/O.
            self.market_halt.latch(self._fatal)
        # A live market halt cannot be cleared by resetting an in-memory flag.

    def inhibited(self):
        return self.control.inhibited()

    async def balance_observed(self, account):
        if self.live:
            result = self.control.observe_balance(account['official_balance_usd'])
            self.record('sm_m4_balance_guard', result)
            if self.inhibited():
                self.stop_event.set()
                raise ValueError('M4_STOP_CONFIRMATION_REQUIRED')

    async def account_snapshot(self):
        if self.live:
            return await super().account_snapshot()
        # The ledger retains gross commitments for ACK and UNKNOWN. Subtracting
        # modeled receipts here too would double-count and block the second side.
        return {'official_balance_usd': '10', 'applicable_allowance_usd': '10',
                'source': 'MODELED_GROSS_BUDGET_10_USDC_PER_MARKET'}

    def drain_network_events(self):
        if self.live:
            super().drain_network_events()


async def wait(stop, seconds):
    with suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=seconds)


async def run(project, *, live=False, once=False, stop=None, until_first_fill=None):
    if until_first_fill is not None and (not live or once):
        raise ValueError('M4_FIRST_FILL_REQUIRES_LIVE_WITHOUT_ONCE')
    project = Path(project).resolve()  # noqa: ASYNC240 -- Local configuration, before network starts.
    settings_type, ledger_type, reads_type = M4Config, M4Ledger, _ExecutionReads
    if live:
        # Live-only policy modules are never imported by the baseline process.
        from polymaker.m4_live_config import M4LiveSettings
        from polymaker.m4_live_http import M4LiveReads
        from polymaker.m4_live_startup import LivePublicAPI, LiveStartup
        from polymaker.m4_live_taker import M4LiveLedger
        settings_type, ledger_type, reads_type = M4LiveSettings, M4LiveLedger, M4LiveReads
    configured_settings, control = settings_type.load(project), StopControl(project, live=live)
    if until_first_fill is not None and (configured_settings.execution.mode != 'taker'
            or configured_settings.execution_revision != 'm4-taker-budget-v1'):
        raise ValueError('M4_FIRST_FILL_REQUIRES_CASH_TARGET_TAKER')
    if control.inhibited():
        return {'status': 'STOP_CONFIRMATION_REQUIRED', 'live': live}
    root = paths(project, live)
    root.mkdir(parents=True, exist_ok=True)
    stop = stop or asyncio.Event()
    loop, handlers = asyncio.get_running_loop(), []
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop.set)
            handlers.append(signum)
        except (NotImplementedError, ValueError):
            pass
    api, reads, feed, ledger, executor = None, None, None, None, None
    config = None
    resolver = None
    resolver_stop = asyncio.Event()
    source_hashes = sources()
    if live:
        source_hashes.update({name: hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest()
                             for name in ('m4_live_config.py', 'm4_live_http.py', 'm4_live_taker.py',
                                          'm4_live_startup.py', 'm4_live_halt.py', 'execution/taker_fees.py')})
    try:
        if live:
            config = Config.load(str(project/'config/m4-live'))
            if any(getattr(config.wallet, k).rstrip('/') != v for k, v in OFFICIAL_HOSTS.items()) or config.wallet.chain_id != 137:
                raise ValueError('M4_OFFICIAL_ENDPOINTS_REQUIRED')
            config = config.model_copy(deep=True)
            config.rdp_sm = RDPSMConfig(normal_entry_mode='m4_v2')
            config.paths.journal_dir = str(root/'journal')
        with (root/'runner.lock').open('a+') as lock, sdk_http1_transport():
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            trial, recovery_slug = None, None
            if until_first_fill is not None:
                from polymaker.m4_live_first_fill import FirstFillMarket, FirstFillTrial
                trial = FirstFillTrial(until_first_fill, root)
                finished, recovery_slug = trial.resume()
                if finished:
                    return finished
                name = 'm4_live_first_fill.py'
                source_hashes[name] = hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest()
            api = LivePublicAPI() if live else VirtualPublicAPI()
            startup = LiveStartup(root, api, control, stop) if live else None
            if startup is not None:
                startup.continue_market_faults = not (once or trial)
            if recovery_slug:
                startup.recovery_slug = recovery_slug
            if not live:
                resolver = asyncio.create_task(resolutions(root, api, resolver_stop,
                    active_path=lambda: ledger.path if ledger else None), name='m4-modeled-resolution')
            while not stop.is_set() and not control.inhibited():
                settings = configured_settings
                if live:
                    prepared = await startup.prepare(configured_settings, reads_type, config)
                    if prepared is None:
                        return {'status': 'STOPPED', 'live': live}
                    settings, meta, reads = prepared.settings, prepared.meta, prepared.reads
                    slug, path, identity = prepared.slug, prepared.path, prepared.identity
                    server, sent, received = prepared.clock
                    epoch = int(slug.rsplit('-', 1)[1])
                    funder = reads.gateway.funder.lower()
                else:
                    server, sent, received = await api.clock()
                    epoch = int(server//300)*300
                    slug = f'btc-updown-5m-{epoch}'
                    path = root/'ledgers'/f'{slug}.sqlite3'
                    if path.is_symlink() or path.resolve() != path:
                        raise ValueError('M4_LEDGER_SYMLINK_FORBIDDEN')
                    if not path.exists() and server+1+received-sent-epoch >= settings.start_sec:
                        await wait(stop, min(5, max(.01, epoch+300-server)))
                        continue  # New deployments start with a fresh full market.
                    meta = await api.market(slug)
                    funder = VIRTUAL_IDENTITY
                    identity = {'strategy': STRATEGY, 'mode': 'VIRTUAL',
                        'funder': funder, 'market_id': meta.condition_id,
                        'settings_hash': settings.fingerprint}
                    previous = M4Ledger.metadata(path)
                    if previous.get('session_id') is not None or previous.get('settings') is not None:
                        saved_identity = previous.get('identity', {})
                        current_market = json.loads(json.dumps(asdict(meta), default=str))
                        stored_market = previous.get('market') or {}
                        if (any(saved_identity.get(k) != identity[k] for k in ('strategy','mode','funder','market_id'))
                                or not previous.get('session_id') or any(stored_market.get(k) != current_market[k]
                                    for k in ('slug', 'condition_id', 'tokens'))):
                            raise ValueError('M4_RECOVERY_SESSION_IDENTITY_CHANGED')
                        settings = settings_type.model_validate(previous['settings'])
                        if settings.fingerprint != saved_identity.get('settings_hash'):
                            raise ValueError('M4_RECOVERY_SETTINGS_INVALID')
                        identity['settings_hash'] = settings.fingerprint
                if live:
                    try:
                        ledger = ledger_type(path, identity, settings=settings, fees=reads.taker_fees)
                        reads.bind_ledger(ledger)
                    except Exception:
                        from polymaker.m4_live_halt import MarketHalt
                        MarketHalt(path, slug).latch('M4_MARKET_LEDGER_RECOVERY_FAILED')
                        if ledger is not None:
                            ledger.close()
                            ledger = None
                        reads.close()
                        reads = None
                        if once or trial:
                            raise
                        await wait(stop, 1)
                        continue
                else:
                    ledger = ledger_type(path, identity)
                if ledger.get('complete'):
                    ledger.close()
                    ledger = None
                    if reads:
                        reads.close()
                        reads = None
                    if once:
                        return {'status': 'MARKET_ALREADY_COMPLETE', 'live': live}
                    await wait(stop, min(5, max(.01, epoch+300-server)))
                    continue
                if trial and not getattr(ledger, 'cash_target', False):
                    raise ValueError('M4_FIRST_FILL_RECOVERY_POLICY_CONFLICT')
                market_failed, trial_fatal, sid = False, None, None
                try:
                    try:
                        sid = ledger.get('session_id')
                        recovering = sid is not None
                        if sid is None:
                            sid = ('m4_live_' if live else 'm4_virtual_')+uuid4().hex
                            ledger.initialize_session(sid, settings.model_dump(mode='json'), asdict(meta),
                                baseline_fill_model=None if live else BASELINE_FILL_POLICY)
                        if trial:
                            trial.bind(ledger, slug)
                        feed = M4Feed(meta, RDPObiConfig(), proxy=config.proxy if live else None)
                        if live:
                            reads.feed = feed
                        else:
                            reads = VirtualReads(feed, ledger, api)
                        backing = M4RiskJournal(root/'journal', funder, session_id=sid, resume=recovering)
                        journal = AsyncRiskWriter(backing, batch_size=128)
                        journal.accepted = journal.written = backing.last_writer_sequence
                        executor = Executor(config, slug, sid, reads, journal, ledger=ledger,
                                            control=control, live=live)
                        executor.stop_event = stop
                        executor.meta = meta
                        executor.official_time, executor.time_anchor = server, received
                        executor.official_time_uncertainty_s = 1+received-sent
                        executor.record('sm_session', {'strategy': STRATEGY, 'mode': 'LIVE' if live else 'VIRTUAL',
                            'slug': slug, 'market_id': meta.condition_id, 'settings': settings.model_dump(mode='json'),
                            'settings_hash': settings.fingerprint, 'signal_parameters': dict(PARAMETERS),
                            'baseline_fill_model': ledger.get('baseline_fill_model'),
                            'source_sha256': source_hashes,
                            'state_path': str(path), 'recovery': recovering, 'fill_tracking': 'NOT_TRACKED',
                            'market_end_actions': 'PLATFORM_MANAGED'})
                        def record_signal(row, executor=executor):
                            executor.record('sm_m4_signal', row)
                            return executor.journal.ready
                        feed.m1.record = record_signal
                        feed.verify_ticks(prepared.ticks if live else await api.ticks(feed.books))
                        feed.task = asyncio.create_task(feed.run(), name='m4-public-feed')
                        market_type = M4Market
                        if live and settings.execution.mode == 'taker':
                            from polymaker.m4_live_taker import M4TakerMarket
                            market_type = FirstFillMarket if trial else M4TakerMarket
                        market = market_type(executor, settings, stop, public_api=api,
                            display=lambda value: atomic_json(root/'latest.json', value))
                        clean = await market.run()
                    finally:
                        try:
                            if feed:
                                await feed.close()
                        finally:
                            if executor:
                                trial_fatal = executor.fatal if trial else None
                                executor.close()
                            elif reads:
                                reads.close()
                except Exception:
                    if not live:
                        raise
                    from polymaker.m4_live_halt import MarketHalt
                    # Also covers initialization/flush/cleanup failures that did
                    # not reach an executor's explicit fatal assignment.
                    halt = MarketHalt(path, slug, sid)
                    halt.latch((executor.fatal if executor else None) or 'M4_MARKET_RUNTIME_FAILED')
                    if once or trial:
                        raise
                    market_failed = True
                finally:
                    market, executor, reads, feed = None, None, None, None
                if market_failed:
                    ledger.close()
                    ledger = None
                    # Restart this market in read-only recovery, or discover the
                    # next epoch. Never erase the previous market's halt/evidence.
                    await wait(stop, 1)
                    continue
                trial_result = (trial.result('FIRST_FILL_FATAL', market=slug, reason=trial_fatal)
                                if trial_fatal else trial.finish_market(ledger, clean) if trial else None)
                ledger.close()
                ledger = None
                if trial_result:
                    return trial_result
                if not clean or once:
                    return {'status': 'COMPLETE' if clean else 'RETAINED', 'live': live}
            return {'status': 'STOPPED', 'live': live}
    finally:
        resolver_stop.set()
        if resolver:
            await resolver
        if feed:
            await feed.close()
        if executor:
            executor.close()
        elif reads:
            reads.close()
        if ledger:
            ledger.close()
        if api:
            await api.close()
        for signum in handlers:
            loop.remove_signal_handler(signum)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-dir', type=Path, default=Path.cwd())
    parser.add_argument('--mode', choices=('live', 'baseline'), default='baseline')
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--until-first-fill', type=Path, metavar='STATE_JSON',
                        help='Live cash-target Taker trial: consecutive markets until first filled signal finishes')
    parser.add_argument('--check', action='store_true', help='Offline configuration and stop-control check only')
    args = parser.parse_args()
    live = args.mode == 'live'
    if args.until_first_fill is not None and (not live or args.once):
        parser.error('--until-first-fill requires --mode live and cannot use --once')
    if live != args.live and not args.check:
        parser.error('Live mode requires --mode live --live; baseline cannot use --live')
    if args.check:
        settings_type = M4Config
        if live:
            from polymaker.m4_live_config import M4LiveSettings
            settings_type = M4LiveSettings
        settings = settings_type.load(args.project_dir)
        result = {'strategy': STRATEGY, 'settings': settings.model_dump(mode='json'),
            'settings_hash': settings.fingerprint, 'signal_parameters': dict(PARAMETERS),
            'new_market_baseline_fill_model': None if live else BASELINE_FILL_POLICY,
            'data_root': str(paths(args.project_dir, live)),
            'inhibited': StopControl(args.project_dir, live=live).inhibited(), 'network_requests': 0}
    else:
        result = asyncio.run(run(args.project_dir, live=live, once=args.once,
                                 until_first_fill=args.until_first_fill))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
