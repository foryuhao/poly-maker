"""Independent M4 live/baseline runner. Import and --check are offline and inert."""
import argparse
import asyncio
import fcntl
import hashlib
import json
import signal
import time
from contextlib import contextmanager, suppress
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
from polymaker.market_protocol import market_protocol
from polymaker.rdp_sm import OFFICIAL_HOSTS
from polymaker.rdp_sm_execution import _ExecutionReads
from polymaker.rdp_sm_writer import AsyncRiskWriter

VIRTUAL_IDENTITY = '0x'+'0'*39+'1'


def paths(project, live, *, independent_m4=False):
    project = Path(project).resolve()
    root = project/'research_outputs'/('m4_independent_live' if independent_m4 else 'm4_live' if live else 'm4_virtual')
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


@contextmanager
def runner_ownership(root, strategy):
    # The legacy runner takes an exclusive lock. Dedicated runners share that
    # lock and independently exclude a duplicate owner of the same ledgers.
    with (root / 'runner.lock').open('a+') as lock:
        fcntl.flock(lock, (fcntl.LOCK_SH if strategy else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        if strategy:
            with (root / f'{strategy.lower()}-runner.lock').open('a+') as owner:
                fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
                yield
        else:
            yield


async def run(project, *, live=False, once=False, stop=None, until_first_fill=None, strategy=None, independent_m4=False, follow=None):
    if live and (follow is None or strategy is not None or independent_m4 or until_first_fill
                 or Path(project).resolve() != Path(__file__).resolve().parents[2]):
        raise ValueError("INDEPENDENT_M12_FOLLOW_ENTRY_REQUIRED")
    if independent_m4 and strategy != 'M4':
        raise ValueError('INDEPENDENT_M4_REQUIRES_M4_ROLE')
    if strategy is not None and (strategy not in {'M4', 'M21'} or not live or until_first_fill):
        raise ValueError('TAKER_DEDICATED_STRATEGY_REQUIRES_LIVE')
    if until_first_fill is not None and (not live or once):
        raise ValueError('M4_FIRST_FILL_REQUIRES_LIVE_WITHOUT_ONCE')
    project = Path(project).resolve()  # noqa: ASYNC240 -- Local configuration, before network starts.
    settings_type, ledger_type, reads_type = M4Config, M4Ledger, _ExecutionReads
    if live:
        # Live-only policy modules are never imported by the baseline process.
        from polymaker.m4_live_config import M4LiveSettings
        from polymaker.m4_live_http import M4LiveReads
        from polymaker.m4_live_startup import (
            NEXT_MARKET_PREPARATION_POLICY,
            LivePublicAPI,
            LiveStartup,
            PreparationStopped,
        )
        from polymaker.m4_live_taker import M4LiveLedger
        settings_type, ledger_type, reads_type = M4LiveSettings, M4LiveLedger, M4LiveReads
    state_dir = getattr(follow, "state_dir", None)
    follow_strategy = getattr(follow, "strategy", "M12") if follow is not None else None
    configured_settings = settings_type.load(project)
    control = StopControl(project, live=live, state_dir=state_dir)
    if follow is not None:
        control = follow.stop_control(control)
    strategy_id = STRATEGY
    if strategy:
        from polymaker.m4_types import TAKER_LEDGER_IDS
        from polymaker.taker_runner import dedicated_feed_type, strategy_settings

        strategy_id = TAKER_LEDGER_IDS[strategy]
        configured_settings = strategy_settings(project, strategy_id, configured_settings)
        if configured_settings.execution_revision != 'm4-taker-budget-v1':
            raise ValueError('TAKER_DEDICATED_REQUIRES_BUDGET_EXECUTION')
    if until_first_fill is not None and (configured_settings.execution.mode != 'taker'
            or configured_settings.execution_revision != 'm4-taker-budget-v1'):
        raise ValueError('M4_FIRST_FILL_REQUIRES_CASH_TARGET_TAKER')
    if control.inhibited():
        return {'status': 'STOP_CONFIRMATION_REQUIRED', 'live': live}
    root = paths(state_dir or project, live, independent_m4=independent_m4)
    root.mkdir(parents=True, exist_ok=True)
    journal_dir = root / ('m21-journal' if strategy == 'M21' else 'journal')
    latest_path = root / ('m21-latest.json' if strategy == 'M21' else 'latest.json')
    stop = stop or asyncio.Event()
    loop, handlers = asyncio.get_running_loop(), []
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop.set)
            handlers.append(signum)
        except (NotImplementedError, ValueError):
            pass
    api, reads, feed, ledger, executor = None, None, None, None, None
    config = startup = None
    resolver = None
    resolver_stop = asyncio.Event()
    source_hashes = sources()
    trial_window = follow.trial_window if follow else None
    if live:
        source_hashes.update({name: hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest()
                             for name in ('m4_live_config.py', 'm4_live_http.py', 'm4_live_taker.py',
                                          'm4_live_startup.py', 'm4_live_halt.py', 'execution/taker_fees.py',
                                          'taker_pool.py', 'taker_runner.py')})
    try:
        if live:
            config = Config.load(str(project/'config/m4-live'), load_env=False, secrets=follow.secrets())
            if any(getattr(config.wallet, k).rstrip('/') != v for k, v in OFFICIAL_HOSTS.items()) or config.wallet.chain_id != 137:
                raise ValueError('M4_OFFICIAL_ENDPOINTS_REQUIRED')
            config = config.model_copy(deep=True)
            config.rdp_sm = RDPSMConfig(normal_entry_mode='m4_v2')
            config.paths.journal_dir = str(journal_dir)
        with runner_ownership(root, strategy), sdk_http1_transport():
            follow_archive = None
            if follow is not None:
                if state_dir is not None:
                    follow.bind_instance(root)
                from m12_follow.archive import PendingArchive
                follow_archive = PendingArchive(root)
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
            startup = (LiveStartup(root, api, control, stop, strategy=strategy) if strategy
                       else LiveStartup(root, api, control, stop)) if live else None
            if startup is not None:
                startup.continue_market_faults = not (once or trial)
                if trial_window:
                    startup.trial_end = trial_window.end
            if recovery_slug:
                startup.recovery_slug = recovery_slug
            if not live:
                resolver = asyncio.create_task(resolutions(root, api, resolver_stop,
                    active_path=lambda: ledger.path if ledger else None), name='m4-modeled-resolution')
            while not stop.is_set() and not control.inhibited():
                settings = configured_settings
                opening = opening_account = account_began = m6_settings = m21_settings = m421_settings = None
                if live:
                    if trial_window:
                        if time.time() >= trial_window.end:
                            break
                        if (delay := trial_window.first_epoch - time.time()) > 0:
                            await wait(stop, min(5, delay))
                            continue
                    prepared = await startup.prepare(configured_settings, reads_type, config)
                    if prepared is None:
                        if trial_window and time.time() >= trial_window.end:
                            break
                        return {'status': 'STOPPED', 'live': live}
                    settings, meta, reads = prepared.settings, prepared.meta, prepared.reads
                    slug, path, identity = prepared.slug, prepared.path, prepared.identity
                    epoch = int(slug.rsplit('-', 1)[1])
                    if trial_window and (not trial_window.contains(slug) or time.time() >= trial_window.end):
                        reads.close()
                        reads = None
                        if epoch >= trial_window.end or time.time() >= trial_window.end:
                            break
                        await wait(stop, 1)
                        continue
                    funder = reads.gateway.funder.lower()
                    follow.verify_existing_wallet()
                    if funder != follow.funder:
                        raise ValueError("M12_FOLLOW_FUNDER_MISMATCH")
                    use_pool = not independent_m4 and (strategy or (root/'taker-pool.sqlite3').exists()
                        or any((project/'config/m4-live'/name).exists()
                               for name in ('strategies.toml', 'strategies-additions.toml')))
                    if market_protocol(meta) == 'v2' and (
                            not use_pool or strategy or trial or settings.execution_revision != 'm4-taker-budget-v1'):
                        reads.close()
                        reads = None
                        startup.report('V2_MARKET_REQUIRES_M42112')
                        if once:
                            return {'status': 'V2_MARKET_REQUIRES_M42112', 'live': live}
                        await wait(stop, max(.01, epoch+300-prepared.clock[0]))
                        continue
                    if use_pool:
                        from polymaker.m4_types import M6_LEDGER, M21_LEDGER, M421_LEDGER
                        from polymaker.taker_runner import prepare_opening
                        try:
                            opening, opening_account, account_began = await prepare_opening(
                                project, root, prepared, startup, isolated=bool(strategy),
                                follow_strategy=follow_strategy)
                        except PreparationStopped:
                            if trial_window and time.time() >= trial_window.end:
                                reads.close()
                                reads = None
                                break
                            startup.report('STOPPED')
                            return {'status': 'STOPPED', 'live': live}
                        if strategy and opening is None:
                            raise ValueError('TAKER_DEDICATED_REQUIRES_SAVED_POOL_OPENING')
                        if opening:
                            saved_settings = opening.settings(strategy_id)
                            if (prepared.startup or {}).get('recovery'):
                                if saved_settings is not None and settings != saved_settings:
                                    raise ValueError(f'TAKER_POOL_{strategy or "M4"}_RECOVERY_SETTINGS_CONFLICT')
                            elif saved_settings is not None:
                                if settings.execution != saved_settings.execution:
                                    reads.gateway._client.m4_execution = saved_settings.execution
                                settings = saved_settings
                                identity = {**identity, 'settings_hash': settings.fingerprint}
                            elif strategy:
                                reads.close()
                                reads = None
                                startup.report('STRATEGY_NOT_IN_OPENING', strategy=strategy,
                                               pool_status=opening.payload['status'])
                                if once:
                                    return {'status': 'STRATEGY_NOT_IN_OPENING', 'live': live}
                                await wait(stop, min(5, max(.01, epoch+300-prepared.clock[0])))
                                continue
                            if not strategy:
                                m6_settings = opening.settings(M6_LEDGER)
                                m21_settings = opening.settings(M21_LEDGER)
                                m421_settings = opening.settings(M421_LEDGER)
                            if settings.execution_revision != 'm4-taker-budget-v1':
                                raise ValueError('TAKER_POOL_REQUIRES_M4_BUDGET_TRANSPORT')
                            if control.inhibited():
                                return {'status': 'STOP_CONFIRMATION_REQUIRED', 'live': live}
                    if not (opening and m421_settings is not None and opening.settings(M421_LEDGER)
                            and m421_settings.signal_profile == "M42112"
                            and tuple(m421_settings.signal_output_sources or ()) == (follow_strategy,)
                            and set(opening.payload["settings"]) == {M421_LEDGER}):
                        reads.close()
                        reads = None
                        startup.report("M12_FOLLOW_POOL_CLOSED")
                        if once:
                            return {"status": "M12_FOLLOW_POOL_CLOSED", "live": True}
                        await wait(stop, max(.01, epoch+300-prepared.clock[0]))
                        continue
                    server, sent, received = prepared.clock
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
                        feed_type, options = M4Feed, {}
                        if strategy:
                            feed_type = dedicated_feed_type(strategy_id)
                            if strategy == 'M21':
                                options = {'taker_settings': settings}
                        elif any(cfg is not None for cfg in (m6_settings, m21_settings, m421_settings)):
                            from m12_follow.adapter import FollowSocket
                            feed_type, options = FollowSocket, {"follow": follow, "m421_settings": m421_settings}
                        if strategy != 'M21':
                            options.update(price_risk=settings.live_taker_price_risk if live else None,
                                           taker_strategy=settings.taker_strategy if live else None)
                        feed = feed_type(meta, RDPObiConfig(), proxy=config.proxy if live else None, **options)
                        if live:
                            reads.feed = feed
                        else:
                            reads = VirtualReads(feed, ledger, api)
                        backing = M4RiskJournal(journal_dir, funder, session_id=sid, resume=recovering)
                        journal = AsyncRiskWriter(backing, batch_size=128)
                        journal.accepted = journal.written = backing.last_writer_sequence
                        executor = Executor(config, slug, sid, reads, journal, ledger=ledger,
                                            control=control, live=live)
                        executor.isolated_strategy = strategy is not None
                        executor.stop_event = stop
                        executor.meta = meta
                        executor.official_time, executor.time_anchor = server, received
                        executor.official_time_uncertainty_s = 1+received-sent
                        prepare_next = (startup is not None and not once and trial is None
                                        and (not trial_window or epoch + 300 < trial_window.end)
                                        and settings.execution_revision == 'm4-taker-budget-v1')
                        executor.record('sm_session', {'strategy': strategy_id, 'mode': 'LIVE' if live else 'VIRTUAL',
                            'slug': slug, 'market_id': meta.condition_id, 'settings': settings.model_dump(mode='json'),
                            **({'protocol_version': 'v2'} if market_protocol(meta) == 'v2' else {}),
                            'settings_hash': settings.fingerprint,
                            **({'signal_parameters': dict(PARAMETERS)} if strategy != 'M21' else
                               {'signal_input_policy': feed.research.source_order_policy}),
                            **({'marketdata_process_policy': 'DEDICATED_STRATEGY_WS_V1'} if strategy else {}),
                            'baseline_fill_model': ledger.get('baseline_fill_model'),
                            'source_sha256': source_hashes,
                            'state_path': str(path), 'recovery': recovering, 'fill_tracking': 'NOT_TRACKED',
                            **({'next_market_preparation_policy': NEXT_MARKET_PREPARATION_POLICY}
                               if prepare_next else {}),
                            'market_end_actions': 'PLATFORM_MANAGED'})
                        if opening:
                            executor.record('sm_taker_pool_opening', opening.payload)
                        def record_signal(row, executor=executor):
                            executor.record('sm_m21_signal' if strategy == 'M21' else 'sm_m4_signal', row)
                            if (strategy == 'M21' and row['event'] == 'OPPORTUNITY_IDENTIFIED'
                                    and market.elapsed() >= settings.cutoff_sec):
                                executor.record('sm_m4_candidate_skipped',
                                                {'opportunity': row['opportunity'],
                                                 'reason': 'M21_FORMAL_AFTER_T240'})
                            return executor.journal.ready
                        if m421_settings is None:
                            feed.m1.record = record_signal
                        feed.verify_ticks(prepared.ticks if live else await api.ticks(feed.books))
                        market_type = M4Market
                        if live and settings.execution.mode == 'taker':
                            from polymaker.m4_live_taker import M4TakerMarket
                            market_type = FirstFillMarket if trial else M4TakerMarket
                        if follow_archive is not None:
                            follow_archive.register_market(slug)
                        market = market_type(executor, settings, stop, public_api=api,
                            **({'opening': opening} if opening and settings.execution_revision == 'm4-taker-budget-v1' else {}),
                            **({'follow_archive': follow_archive} if follow_archive is not None else {}),
                            display=lambda value: atomic_json(latest_path, value))
                        feed.task = asyncio.create_task(feed.run(), name='m4-public-feed')
                        if opening_account is not None and hasattr(market, 'opening'):
                            market.account, market.account_at = opening_account, account_began
                            market.account_reason = None
                        if prepare_next:
                            startup.schedule_next_preparation(market, configured_settings, reads_type, config)
                        if all(cfg is None for cfg in (m6_settings, m21_settings, m421_settings)):
                            clean = await market.run()
                        else:
                            from polymaker.taker_runner import run_markets
                            clean = await run_markets(market, opening, feed, root, Executor, control)
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
                    if startup is not None:
                        await startup.discard_next_preparation()
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
            if trial_window and not stop.is_set() and not control.inhibited():
                return await trial_window.finish(root, follow.funder, reads_type, config, stop, control)
            return {'status': 'STOPPED', 'live': live}
    finally:
        resolver_stop.set()
        if startup is not None:
            await startup.discard_next_preparation()
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
    parser.add_argument('--strategy', choices=('M4', 'M21'), help='Own only this live strategy and its WS')
    parser.add_argument('--independent-m4', action='store_true',
                        help='Use M4-only state and direct account checks without the shared pool')
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--until-first-fill', type=Path, metavar='STATE_JSON',
                        help='Live cash-target Taker trial: consecutive markets until first filled signal finishes')
    parser.add_argument('--check', action='store_true', help='Offline configuration and stop-control check only')
    args = parser.parse_args()
    live = args.mode == 'live'
    if args.independent_m4 and args.strategy != 'M4':
        parser.error('--independent-m4 requires --strategy M4')
    if args.strategy and (not live or args.until_first_fill is not None):
        parser.error('--strategy requires --mode live and cannot use --until-first-fill')
    if args.until_first_fill is not None and (not live or args.once):
        parser.error('--until-first-fill requires --mode live and cannot use --once')
    if live != args.live and not args.check:
        parser.error('Live mode requires --mode live --live; baseline cannot use --live')
    if args.check:
        settings_type = M4Config
        if live:
            from polymaker.m4_live_config import M4LiveSettings
            settings_type = M4LiveSettings
        base_settings = settings = settings_type.load(args.project_dir)
        if args.strategy:
            from polymaker.m4_types import TAKER_LEDGER_IDS
            from polymaker.taker_runner import strategy_settings
            settings = strategy_settings(args.project_dir, TAKER_LEDGER_IDS[args.strategy], settings)
        result = {'strategy': args.strategy or STRATEGY, 'settings': settings.model_dump(mode='json'),
            'settings_hash': settings.fingerprint,
            **({'signal_parameters': dict(PARAMETERS)} if args.strategy != 'M21' else {}),
            'new_market_baseline_fill_model': None if live else BASELINE_FILL_POLICY,
            'data_root': str(paths(args.project_dir, live, independent_m4=args.independent_m4)),
            'inhibited': StopControl(args.project_dir, live=live).inhibited(), 'network_requests': 0}
        if args.independent_m4:
            result['capital_policy'] = 'DIRECT_ACCOUNT_CHECKS'
        if live and not args.independent_m4 and (args.project_dir/'config/m4-live/strategies.toml').exists():
            from polymaker.m4_live_startup import (
                NEXT_MARKET_PREPARATION_POLICY,
                NEXT_MARKET_PREPARATION_SEC,
            )
            from polymaker.m4_types import M421_LEDGER, TAKER_LEDGER_IDS
            from polymaker.taker_pool import POLICY, configured_profiles
            from polymaker.taker_runner import strategy_settings
            profiles = configured_profiles(args.project_dir)
            result['taker_pool'] = {'policy': POLICY, 'enabled': tuple(profiles),
                'selected_signal_profiles': profiles,
                'available_strategy_settings': {
                    k: strategy_settings(args.project_dir, k, base_settings).model_dump(mode='json')
                    for name, k in TAKER_LEDGER_IDS.items()
                    if name not in {'M21', 'M421'} or (args.project_dir/'config/m4-live/m21.toml').is_file()}}
            if (args.project_dir/'config/m4-live/m21.toml').is_file():
                result['taker_pool']['available_signal_profiles'] = {
                    'M42112': strategy_settings(args.project_dir, M421_LEDGER, base_settings,
                                               signal_profile='M42112').model_dump(mode='json')}
            if settings.execution_revision == 'm4-taker-budget-v1':
                result['next_market_preparation'] = {'policy': NEXT_MARKET_PREPARATION_POLICY,
                                                     'previous_market_start_sec': NEXT_MARKET_PREPARATION_SEC}
    else:
        result = asyncio.run(run(args.project_dir, live=live, once=args.once,
                                 until_first_fill=args.until_first_fill, strategy=args.strategy,
                                 independent_m4=args.independent_m4))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
