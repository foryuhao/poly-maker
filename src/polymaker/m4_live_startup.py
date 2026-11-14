"""Live-only, pre-ledger GET recovery. Never retry a trading market or a POST."""

import asyncio
import json
import time
from contextlib import suppress
from dataclasses import asdict, dataclass

import httpx
from py_clob_client_v2.exceptions import PolyApiException

from polymaker.m4_config import STRATEGY
from polymaker.m4_control import atomic_json
from polymaker.m4_live_config import M4LiveSettings
from polymaker.m4_live_halt import MarketHalt
from polymaker.m4_live_numeric import age_us
from polymaker.m4_state import M4Ledger
from polymaker.maker_public import VirtualPublicAPI
from polymaker.rdp_sm_execution import _execution_identity

STARTUP_ENTRY_POLICY = 'NEW_MARKET_BEFORE_T240_V1'


def elapsed_upper_us(clock, epoch):
    """Same integer-microsecond clock bounds as live execution; time is coarse."""
    server, sent, received = clock
    rtt = age_us(received, sent)
    if rtt < 0:
        raise ValueError('M4_STARTUP_CLOCK_BOUNDS_INVALID')
    return age_us(server, epoch) + 1_000_000 + rtt


class LivePublicAPI(VirtualPublicAPI):
    def __init__(self, *, transport=None):
        # Waiting for the next market polls every five seconds. Keep a healthy
        # TLS connection longer than that; retain strict TLS and 5s I/O limits.
        super().__init__(transport=transport or httpx.AsyncHTTPTransport(
            http2=False, retries=0, limits=httpx.Limits(keepalive_expiry=30)))


def transient_read_error(exc):
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
    elif isinstance(exc, PolyApiException):
        # The pinned SDK wraps transport failures with this exact message.
        if exc.status_code is None:
            return exc.error_msg == 'Request exception!'
        status = exc.status_code
    else:
        return False
    return status in {408, 429, 500, 502, 503, 504}


@dataclass
class PreparedMarket:
    settings: object
    clock: tuple
    slug: str
    path: object
    meta: object
    reads: object
    identity: dict
    ticks: dict
    startup: dict | None = None


class PreparationStopped(Exception):
    pass


class LiveStartup:
    def __init__(self, root, api, control, stop):
        self.root, self.api, self.control, self.stop = root, api, control, stop
        self.phase, self.slug, self.failures = 'clock', None, 0
        self.recovery_slug = None
        self.continue_market_faults = True

    def check_stop(self):
        if self.stop.is_set() or self.control.inhibited():
            raise PreparationStopped

    def report(self, status, **fields):
        # Never include exception text, headers, credentials or account data.
        row = {'status': status, 'phase': self.phase, 'slug': self.slug,
               'failures': self.failures, 'observed_at': time.time(), **fields}
        atomic_json(self.root/'startup-status.json', row)
        print(json.dumps({'m4_live_startup': row}), flush=True)

    async def pause(self, seconds):
        # Also notice a persistent stop marker, not just SIGINT/SIGTERM.
        until = time.monotonic()+seconds
        while time.monotonic() < until:
            self.check_stop()
            with suppress(TimeoutError):
                await asyncio.wait_for(self.stop.wait(), min(.1, until-time.monotonic()))
        self.check_stop()

    async def read(self, phase, operation, *args):
        self.check_stop()
        self.phase = phase
        result = await operation(*args)
        self.check_stop()
        return result

    async def prepare(self, configured, reads_type, config):
        """Retry only preparation GETs; hand ownership to the runner on success.

        No ledger is created and no signal task exists until this returns.
        A known recovery session stays pinned through a network outage, so an
        outage crossing T300 cannot silently redirect its recovery to a new one.
        """
        try:
            while True:
                reads = None
                try:
                    clock = await self.read('clock', self.api.clock)
                    server, sent, received = clock
                    epoch = int(server//300)*300
                    if self.recovery_slug:
                        old = self.root/'ledgers'/f'{self.recovery_slug}.sqlite3'
                        ended = int(self.recovery_slug.rsplit('-', 1)[-1]) + 300 <= server
                        if (self.continue_market_faults and ended
                                and MarketHalt(old, self.recovery_slug).reason):
                            # A halted old session is recovered by the archive
                            # reader; its startup GET failures cannot pin new epochs.
                            self.report('HALTED_MARKET_ARCHIVED', market=self.recovery_slug)
                            self.recovery_slug = None
                    self.slug = self.recovery_slug or f'btc-updown-5m-{epoch}'
                    epoch = int(self.slug.rsplit('-', 1)[1])
                    path = self.root/'ledgers'/f'{self.slug}.sqlite3'
                    self.phase = 'ledger_metadata'
                    if path.is_symlink() or path.resolve() != path:
                        raise ValueError('M4_LEDGER_SYMLINK_FORBIDDEN')
                    previous = M4Ledger.metadata(path)
                    recovering = previous.get('session_id') is not None
                    if recovering:
                        self.recovery_slug = self.slug
                    # T15 is the earliest order time, not a preparation deadline.
                    # A new feed can accumulate fresh evidence after a transient
                    # outage within this market; never invent its missed history.
                    if not recovering and elapsed_upper_us(clock, epoch) >= configured.cutoff_sec*1_000_000:
                        self.report('WAITING_NEXT_MARKET_CUTOFF',
                                    startup_entry_policy=STARTUP_ENTRY_POLICY)
                        await self.pause(min(5, max(.01, epoch+300-server)))
                        continue
                    meta = await self.read('market', self.api.market, self.slug)
                    reads = reads_type(config)
                    # connect_account uses derive_api_key GET only, never key creation.
                    await self.read('account_identity', reads.connect_account)
                    _execution_identity(reads)
                    settings = configured
                    identity = {'strategy': STRATEGY, 'mode': 'LIVE',
                                'funder': reads.gateway.funder.lower(),
                                'market_id': meta.condition_id, 'settings_hash': settings.fingerprint}
                    if recovering or previous.get('settings') is not None:
                        self.phase = 'recovery_identity'
                        saved = previous.get('identity', {})
                        market = json.loads(json.dumps(asdict(meta), default=str))
                        stored = previous.get('market') or {}
                        if (any(saved.get(k) != identity[k] for k in ('strategy', 'mode', 'funder', 'market_id'))
                                or not recovering or any(stored.get(k) != market[k]
                                                        for k in ('slug', 'condition_id', 'tokens'))):
                            raise ValueError('M4_RECOVERY_SESSION_IDENTITY_CHANGED')
                        settings = M4LiveSettings.model_validate(previous['settings'])
                        if settings.fingerprint != saved.get('settings_hash'):
                            raise ValueError('M4_RECOVERY_SETTINGS_INVALID')
                        identity['settings_hash'] = settings.fingerprint
                    await self.read('market_fees', reads.bind_market, settings, meta)
                    ticks = await self.read('ticks', self.api.ticks, [t.token_id for t in meta.tokens])
                    # Re-anchor after all potentially slow GETs. Retry delays do
                    # not belong to the successful clock sample's RTT bound.
                    clock = await self.read('final_clock', self.api.clock)
                    server, sent, received = clock
                    upper = elapsed_upper_us(clock, epoch)
                    if not recovering and (int(server//300)*300 != epoch
                            or upper >= settings.cutoff_sec*1_000_000):
                        self.report('SKIPPED_AFTER_ENTRY_CUTOFF_OR_ROLLOVER',
                                    startup_entry_policy=STARTUP_ENTRY_POLICY,
                                    prepared_elapsed_upper_sec=upper/1_000_000)
                        continue
                    observation = {
                        'startup_entry_policy': STARTUP_ENTRY_POLICY,
                        'slug': self.slug, 'recovery': recovering,
                        'prepared_at': time.time(),
                        'prepared_elapsed_upper_sec': upper/1_000_000,
                        'late_new_market_start': not recovering and upper >= settings.start_sec*1_000_000,
                        'recovered_network': self.failures > 0,
                        'transient_read_failures': self.failures,
                        'signal_history': 'EXISTING_LEDGER_RECOVERY' if recovering else 'FRESH_FEED_FROM_PREPARATION',
                        'preparation_observation_gap': not recovering and upper > 0,
                    }
                    # Audit files are distinct from trading ledgers. Retain the
                    # first admission and record the latest recovery separately.
                    admission_path = self.root/'startup-admissions'/f'{self.slug}.json'
                    if admission_path.is_symlink():
                        raise ValueError('M4_STARTUP_ADMISSION_SYMLINK_FORBIDDEN')
                    admission = json.loads(admission_path.read_text()) if admission_path.exists() else {}
                    admission.setdefault('first_preparation', observation)
                    admission['latest_preparation'] = observation
                    atomic_json(admission_path, admission)
                    self.report('READY', **{k:v for k,v in observation.items() if k != 'slug'})
                    result = PreparedMarket(settings, clock, self.slug, path, meta, reads, identity, ticks, observation)
                    reads = None  # Runner now owns and closes these readers.
                    self.failures, self.recovery_slug = 0, None
                    return result
                except Exception as exc:
                    if isinstance(exc, PreparationStopped):
                        raise
                    if not transient_read_error(exc):
                        if self.phase in {'ledger_metadata', 'recovery_identity'}:
                            MarketHalt(path, self.slug).latch('M4_MARKET_RECOVERY_INVALID')
                            if not self.continue_market_faults:
                                raise
                            self.report('HALTED_RECOVERY_UNAVAILABLE', error_type=type(exc).__name__)
                            self.recovery_slug = None
                            await self.pause(5)
                            continue
                        self.report('FAILED', error_type=type(exc).__name__)
                        raise
                    self.failures += 1
                    delay = min(5., .5*2**min(self.failures-1, 4))
                    self.report('RETRYING_READ', error_type=type(exc).__name__, retry_in_sec=delay)
                    await self.pause(delay)
                finally:
                    if reads is not None:
                        reads.close()
        except PreparationStopped:
            self.report('STOPPED')
            return None
