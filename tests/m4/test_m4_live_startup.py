"""Startup transport failures with no sockets, credentials or real orders."""

import asyncio
import json
import time
from dataclasses import asdict, replace
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from m4_helpers import feed_fixture
from py_clob_client_v2.exceptions import PolyApiException

from polymaker import m4_live_startup
from polymaker.m4_config import STRATEGY
from polymaker.m4_control import StopControl
from polymaker.m4_live_config import M4LiveSettings
from polymaker.m4_live_startup import LivePublicAPI, LiveStartup, transient_read_error
from polymaker.m4_state import M4Ledger

EPOCH = 1800000000
FUNDER = '0x'+'1'*40
SETTINGS = M4LiveSettings(execution_revision='m4-taker-budget-v1', execution={'mode': 'taker'})
PROJECT = Path(__file__).resolve().parents[2]


def harness(tmp_path, monkeypatch, *, phase=None, fault=None):
    meta = feed_fixture()[0].meta
    calls, readers, events, pauses = [], [], [], []
    clock_values = []
    failed = False

    def call(name):
        nonlocal failed
        calls.append(name)
        if name == phase and not failed:
            failed = True
            raise fault or httpx.ConnectTimeout('synthetic TLS handshake timeout')

    class Public:
        async def clock(self):
            call('clock')
            now = time.monotonic()
            return (clock_values.pop(0) if clock_values else EPOCH+1), now, now

        async def market(self, slug):
            call('market')
            return replace(meta, slug=slug)

        async def ticks(self, tokens):
            call('ticks')
            return {t: D('.01') for t in tokens}

        async def close(self):
            call('close_public')

    class Reads:
        def __init__(self, config):
            self.gateway = SimpleNamespace(funder=FUNDER)
            self.taker_fees = {'rate': '0.07', 'exponent': 1}
            self.closed = False
            readers.append(self)

        async def connect_account(self):
            call('account_identity')

        async def bind_market(self, settings, meta):
            self.settings = settings
            call('market_fees')

        def bind_ledger(self, ledger):
            self.ledger = ledger

        def close(self):
            assert not self.closed
            self.closed = True

    root = tmp_path/'research_outputs/m4_live'
    root.mkdir(parents=True)
    stop = asyncio.Event()
    startup = LiveStartup(root, Public(), StopControl(tmp_path, live=True), stop)
    report = startup.report

    def record(status, **fields):
        report(status, **fields)
        events.append({'status': status, **fields})

    async def pause(seconds):
        pauses.append(seconds)
        startup.check_stop()

    monkeypatch.setattr(m4_live_startup, '_execution_identity', lambda _: None)
    monkeypatch.setattr(startup, 'report', record)
    monkeypatch.setattr(startup, 'pause', pause)
    return SimpleNamespace(startup=startup, reads_type=Reads, calls=calls, readers=readers,
                           events=events, pauses=pauses, clock_values=clock_values, meta=meta)


@pytest.mark.parametrize('phase', ['clock', 'market', 'account_identity', 'market_fees', 'ticks'])
@pytest.mark.parametrize('fault', [httpx.ConnectTimeout('tls'), httpx.ReadTimeout('read'),
                                  PolyApiException(error_msg='Request exception!')])
async def test_startup_get_failures_recover_without_creating_ledger(tmp_path, monkeypatch, phase, fault):
    h = harness(tmp_path, monkeypatch, phase=phase, fault=fault)
    ready = await h.startup.prepare(SETTINGS, h.reads_type, None)
    assert h.pauses == [.5]
    assert h.events[-1]['recovered_network'] is True
    assert ready.clock[0] == EPOCH+1 and ready.clock[2]-ready.clock[1] < .01
    assert not list(tmp_path.rglob('*.sqlite3'))
    assert all(r.closed for r in h.readers[:-1]) and not ready.reads.closed
    ready.reads.close()


@pytest.mark.parametrize('status', [408, 429, 500, 502, 503, 504])
def test_only_transient_http_responses_are_retryable(status):
    response = httpx.Response(status, request=httpx.Request('GET', 'https://clob.polymarket.com/time'))
    assert transient_read_error(httpx.HTTPStatusError('test', request=response.request, response=response))
    assert transient_read_error(PolyApiException(response))


@pytest.mark.parametrize('fault', [ValueError('M4_FEE_INVALID'), RuntimeError('identity'),
    PolyApiException(httpx.Response(401)), PolyApiException(httpx.Response(403)),
    PolyApiException(error_msg='unknown non-network failure')])
async def test_invalid_data_auth_and_unknown_errors_fail_closed(tmp_path, monkeypatch, fault):
    h = harness(tmp_path, monkeypatch, phase='market_fees', fault=fault)
    with pytest.raises(type(fault)):
        await h.startup.prepare(SETTINGS, h.reads_type, None)
    assert h.pauses == [] and all(r.closed for r in h.readers)
    assert h.events[-1]['status'] == 'FAILED'
    assert not list(tmp_path.rglob('*.sqlite3'))


@pytest.mark.parametrize('late', [EPOCH+239, EPOCH+301])
async def test_slow_preparation_never_enters_cutoff_or_rolled_market(tmp_path, monkeypatch, late):
    h = harness(tmp_path, monkeypatch)
    h.clock_values[:] = [EPOCH+1, late, EPOCH+301, EPOCH+302]
    ready = await h.startup.prepare(SETTINGS, h.reads_type, None)
    assert ready.slug == f'btc-updown-5m-{EPOCH+300}'
    assert any(e['status'] == 'SKIPPED_AFTER_ENTRY_CUTOFF_OR_ROLLOVER' for e in h.events)
    assert h.readers[0].closed and not ready.reads.closed
    assert not list(tmp_path.rglob('*.sqlite3'))
    ready.reads.close()


@pytest.mark.parametrize('elapsed', [14, 20, 100, 238])
async def test_preparation_after_t15_keeps_current_market_until_t240(tmp_path, monkeypatch, elapsed):
    h = harness(tmp_path, monkeypatch)
    h.clock_values[:] = [EPOCH+1, EPOCH+elapsed]
    ready = await h.startup.prepare(SETTINGS, h.reads_type, None)
    assert ready.slug == h.meta.slug and h.pauses == []
    assert ready.startup['late_new_market_start'] is True
    assert ready.startup['startup_entry_policy'] == 'NEW_MARKET_BEFORE_T240_V1'
    assert ready.settings.start_sec == 15 and ready.settings.cutoff_sec == 240
    assert ready.startup['signal_history'] == 'FRESH_FEED_FROM_PREPARATION'
    assert not list(tmp_path.rglob('*.sqlite3'))
    ready.reads.close()


async def test_tls_and_clock_timeouts_crossing_t15_recover_same_market(tmp_path, monkeypatch):
    h = harness(tmp_path, monkeypatch, phase='account_identity', fault=PolyApiException(error_msg='Request exception!'))
    count=0
    async def clock():
        nonlocal count
        count+=1
        if count==2:
            raise httpx.ReadTimeout('synthetic second failure at clock')
        now=time.monotonic()
        return EPOCH+(1 if count==1 else 21),now,now
    monkeypatch.setattr(h.startup.api,'clock',clock)
    ready=await h.startup.prepare(SETTINGS,h.reads_type,None)
    assert ready.slug == h.meta.slug and h.pauses == [.5,1]
    assert ready.startup['late_new_market_start'] and ready.startup['transient_read_failures']==2
    assert all(r.closed for r in h.readers[:-1]) and not ready.reads.closed
    saved=json.loads((h.startup.root/'startup-admissions'/f'{ready.slug}.json').read_text())
    assert saved['first_preparation']['prepared_elapsed_upper_sec']==22
    assert saved['first_preparation']['signal_history']=='FRESH_FEED_FROM_PREPARATION'
    assert not list(tmp_path.rglob('*.sqlite3'))
    ready.reads.close()


async def test_discovery_in_middle_of_market_can_prepare_without_waiting(tmp_path, monkeypatch):
    h=harness(tmp_path,monkeypatch)
    h.clock_values[:]=[EPOCH+100,EPOCH+101]
    ready=await h.startup.prepare(SETTINGS,h.reads_type,None)
    assert ready.slug==h.meta.slug and h.pauses==[]
    assert ready.startup['prepared_elapsed_upper_sec']==102
    ready.reads.close()


@pytest.mark.parametrize('initial,final', [(239,239),(240,240),(238,239)])
async def test_new_market_at_conservative_t240_waits_next_market(tmp_path,monkeypatch,initial,final):
    h=harness(tmp_path,monkeypatch)
    h.clock_values[:]=([EPOCH+initial]+([EPOCH+final] if initial<239 else [])
                      +[EPOCH+301,EPOCH+302])
    ready=await h.startup.prepare(SETTINGS,h.reads_type,None)
    assert ready.slug==f'btc-updown-5m-{EPOCH+300}'
    assert not list(tmp_path.rglob('*.sqlite3'))
    assert not (h.startup.root/'startup-admissions'/f'{h.meta.slug}.json').exists()
    ready.reads.close()


@pytest.mark.parametrize('server,rtt,upper', [(238,.999999,239999999),(238,1,240000000)])
def test_startup_clock_uses_same_microsecond_t240_boundary(server,rtt,upper):
    assert m4_live_startup.elapsed_upper_us((EPOCH+server,100.,100.+rtt),EPOCH)==upper


@pytest.mark.parametrize('shared_pool', [False, True])
async def test_late_preparation_hands_runner_a_fresh_feed_without_old_signals(tmp_path, monkeypatch, shared_pool):
    from polymaker import m4_live_http, m4_live_taker, m4_run, taker_runner
    from polymaker.config import Config
    from polymaker.rdp_sm import OFFICIAL_HOSTS

    h=harness(tmp_path,monkeypatch)
    h.clock_values[:]=[EPOCH+100,EPOCH+101]
    (tmp_path/'config/m4-live').mkdir(parents=True)
    (tmp_path/'config/m4.toml').write_text('')
    (tmp_path/'config/m4-live/execution.toml').write_text('mode="taker"\n')
    if shared_pool:
        for name in ('m4.toml', 'm4-live/execution.toml'):
            (tmp_path/'config'/name).write_text((PROJECT/'config'/name).read_text())
        (tmp_path/'config/m4-live/strategies.toml').write_text('enabled = ["M4"]\n')
        h.clock_values.append(EPOCH+105)
        reads = []

        async def capital(*_, raise_on_error):
            assert raise_on_error and not (h.startup.root/'ledgers').exists()
            reads.append(True)
            if len(reads) == 1:
                raise httpx.ReadTimeout('balance')
            return {'official_balance_usd':'40', 'applicable_allowance_usd':'40'}

        monkeypatch.setattr(taker_runner, 'account_snapshot', capital)
    config=SimpleNamespace(wallet=SimpleNamespace(**OFFICIAL_HOSTS,chain_id=137),paths=SimpleNamespace(),proxy=None)
    config.model_copy=lambda **_:config
    monkeypatch.setattr(Config,'load',lambda *_:config)
    monkeypatch.setattr(m4_live_startup,'LivePublicAPI',lambda:h.startup.api)
    monkeypatch.setattr(m4_live_startup,'LiveStartup',lambda *args:h.startup)
    monkeypatch.setattr(m4_live_http,'M4LiveReads',h.reads_type)
    runs=[]
    async def no_socket(self):
        await asyncio.Event().wait()
    class Market:
        def __init__(self,e,settings,stop,**kwargs):
            self.e,self.settings=e,settings
            if shared_pool:
                assert kwargs['opening'].payload['status']=='OPEN'
                assert kwargs['opening'].payload['opening_read_retries']==1
        async def run(self):
            runs.append(self.e.slug)
            assert self.e.slug==h.meta.slug and self.e.official_time==EPOCH+(105 if shared_pool else 101)
            assert self.settings.start_sec==15 and self.settings.cutoff_sec==240
            assert self.e.ledger.rows()==[]
            assert self.e.reads.feed.m1.opportunities=={}
            assert self.e.reads.feed.m1.frequency.candidates=={}
            self.e.ledger.set('complete',True)
            return True
    monkeypatch.setattr(m4_run.M4Feed,'run',no_socket)
    monkeypatch.setattr(m4_live_taker,'M4TakerMarket',Market)
    assert (await m4_run.run(tmp_path,live=True,once=True))['status']=='COMPLETE'
    assert runs==[h.meta.slug] and h.pauses==([.5] if shared_pool else [])


async def test_final_clock_failure_restarts_discovery_after_rollover(tmp_path, monkeypatch):
    h = harness(tmp_path, monkeypatch)
    calls = 0

    async def clock():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise httpx.ReadTimeout('final clock lost')
        now = time.monotonic()
        return EPOCH+(1 if calls == 1 else 301), now, now

    monkeypatch.setattr(h.startup.api, 'clock', clock)
    ready = await h.startup.prepare(SETTINGS, h.reads_type, None)
    assert ready.slug.endswith(str(EPOCH+300)) and calls == 4
    assert h.readers[0].closed and h.pauses == [.5]
    ready.reads.close()


@pytest.mark.parametrize('identity_changes', [False, True])
async def test_t270_warms_next_reader_and_t0_revalidates_identity(tmp_path, monkeypatch, identity_changes):
    h = harness(tmp_path, monkeypatch)
    monkeypatch.setattr(m4_live_startup, 'LivePublicAPI', type(h.startup.api))
    elapsed = 269
    old = SimpleNamespace(e=SimpleNamespace(slug=h.meta.slug, official_time_uncertainty_s=1),
                          settings=SETTINGS, elapsed=lambda: elapsed)
    pause = h.startup.pause

    async def advance_to_t270(seconds):
        nonlocal elapsed
        assert seconds == 1 and not h.calls
        elapsed += seconds

    monkeypatch.setattr(h.startup, 'pause', advance_to_t270)
    h.startup.schedule_next_preparation(old, SETTINGS, h.reads_type, None)
    warmed = await h.startup.next_preparation
    next_slug = f'btc-updown-5m-{EPOCH+300}'
    receipt = json.loads((h.startup.root/'next-market-preparations'/f'{next_slug}.json').read_text())
    assert warmed.meta.slug == next_slug and not warmed.reads.closed
    assert receipt['previous_elapsed_at_start_sec'] == 270 and receipt['status'] == 'READY'
    assert h.calls == ['market', 'account_identity', 'market_fees', 'ticks', 'close_public']
    assert not list(tmp_path.rglob('*.sqlite3'))

    if identity_changes:
        async def different_market(slug):
            return replace(h.meta, slug=slug, condition_id='0x'+'ac'*32)

        monkeypatch.setattr(h.startup.api, 'market', different_market)
    monkeypatch.setattr(h.startup, 'pause', pause)
    h.clock_values[:] = [EPOCH+299, EPOCH+300, EPOCH+301]
    prepared = await h.startup.prepare(SETTINGS, h.reads_type, None)
    assert any(e['status'] == 'WAITING_NEXT_MARKET_T0' for e in h.events)
    assert prepared.slug == next_slug and prepared.clock[0] == EPOCH+301
    assert prepared.startup.get('next_market_reader_reused', False) is not identity_changes
    assert (prepared.reads is warmed.reads) is not identity_changes
    assert warmed.reads.closed is identity_changes
    assert h.calls.count('market_fees') == h.calls.count('ticks') == 2
    assert h.calls.count('account_identity') == (2 if identity_changes else 1)
    assert not list(tmp_path.rglob('*.sqlite3'))
    prepared.reads.close()


@pytest.mark.parametrize('cancel', [False, True])
async def test_prefetch_timeout_or_stop_closes_reader_and_uses_normal_startup(tmp_path, monkeypatch, cancel):
    h = harness(tmp_path, monkeypatch)
    tick_started = asyncio.Event()

    class SlowPublic(type(h.startup.api)):
        async def ticks(self, tokens):
            tick_started.set()
            await asyncio.Event().wait()

    monkeypatch.setattr(m4_live_startup, 'LivePublicAPI', SlowPublic)
    old = SimpleNamespace(e=SimpleNamespace(slug=h.meta.slug, official_time_uncertainty_s=1),
                          settings=SETTINGS, elapsed=lambda: 270 if cancel else 298.98)
    h.startup.schedule_next_preparation(old, SETTINGS, h.reads_type, None)
    await asyncio.wait_for(tick_started.wait(), 1)
    if cancel:
        h.startup.stop.set()
        assert await h.startup.take_next_preparation() is None
    else:
        assert await asyncio.wait_for(h.startup.next_preparation, 1) is None
    next_slug = f'btc-updown-5m-{EPOCH+300}'
    receipt = json.loads((h.startup.root/'next-market-preparations'/f'{next_slug}.json').read_text())
    assert receipt['status'] == ('CANCELLED' if cancel else 'FALLBACK')
    assert h.readers[0].closed and h.calls.count('close_public') == 1
    assert not list(tmp_path.rglob('*.sqlite3'))
    if not cancel:
        h.clock_values[:] = [EPOCH+300, EPOCH+301]
        ready = await h.startup.prepare(SETTINGS, h.reads_type, None)
        assert ready.slug == next_slug and ready.reads is h.readers[1]
        assert not ready.startup.get('next_market_reader_reused')
        ready.reads.close()


async def test_live_runner_prefetches_while_old_market_is_still_in_charge(tmp_path, monkeypatch):
    from polymaker import m4_live_http, m4_live_taker, m4_run, taker_runner
    from polymaker.config import Config
    from polymaker.rdp_sm import OFFICIAL_HOSTS

    h = harness(tmp_path, monkeypatch)
    h.clock_values[:] = [EPOCH+10, EPOCH+11, EPOCH+300, EPOCH+301]
    (tmp_path/'config/m4-live').mkdir(parents=True)
    for name in ('m4.toml', 'm6.toml', 'm4-live/execution.toml', 'm4-live/strategies.toml'):
        (tmp_path/'config'/name).write_text((PROJECT/'config'/name).read_text())
    (tmp_path/'config/m4-live/strategies.toml').write_text('enabled = ["M4", "M6"]\n')
    config = SimpleNamespace(wallet=SimpleNamespace(**OFFICIAL_HOSTS, chain_id=137),
                             paths=SimpleNamespace(), proxy=None)
    config.model_copy = lambda **_: config
    monkeypatch.setattr(Config, 'load', lambda *_: config)
    public_clients = []

    def make_public():
        client = type(h.startup.api)() if public_clients else h.startup.api
        public_clients.append(client)
        return client

    monkeypatch.setattr(m4_live_startup, 'LivePublicAPI', make_public)
    monkeypatch.setattr(m4_live_startup, 'LiveStartup', lambda *args: h.startup)
    monkeypatch.setattr(m4_live_http, 'M4LiveReads', h.reads_type)

    async def no_socket(self):
        await asyncio.Event().wait()

    monkeypatch.setattr(taker_runner.SharedTakerFeed, 'run', no_socket)
    old_elapsed = 269
    next_slug = f'btc-updown-5m-{EPOCH+300}'
    capital_reads = []
    markets = []

    async def delayed_wait(seconds):
        nonlocal old_elapsed
        assert seconds == 1
        old_elapsed = 270

    async def capital(reads, meta, *, raise_on_error):
        assert raise_on_error
        capital_reads.append(meta.slug)
        if meta.slug == next_slug:
            assert markets[0].e.ledger.path.is_file() and old_elapsed == 300
            assert reads is h.readers[1]
        return {'official_balance_usd': '60', 'applicable_allowance_usd': '60'}

    class Market:
        def __init__(self, e, settings, stop, **kwargs):
            self.e, self.settings, self.stop = e, settings, stop

        def elapsed(self):
            return old_elapsed if self.e.slug == h.meta.slug else 2

    async def run_markets(primary, opening, feed, root, *_):
        nonlocal old_elapsed
        markets.append(primary)
        assert primary.e.ledger.rows() == [] and feed.m6.m1.opportunities == {}
        if primary.e.slug == h.meta.slug:
            warmed = await h.startup.next_preparation
            assert warmed.meta.slug == next_slug and old_elapsed == 270
            assert not primary.e.ledger.get('complete')
            assert not (root/'ledgers'/f'{next_slug}.sqlite3').exists()
            assert opening.pool.existing(opening.funder, EPOCH+300) is None
            assert capital_reads == [h.meta.slug]
            old_elapsed = 300
        else:
            assert primary.e.slug == next_slug and primary.e.official_time >= EPOCH+300
            assert opening.payload['status'] == 'OPEN'
            primary.stop.set()
        primary.e.ledger.set('complete', True)
        return True

    monkeypatch.setattr(h.startup, 'pause', delayed_wait)
    monkeypatch.setattr(taker_runner, 'account_snapshot', capital)
    monkeypatch.setattr(taker_runner, 'run_markets', run_markets)
    monkeypatch.setattr(m4_live_taker, 'M4TakerMarket', Market)

    assert (await m4_run.run(tmp_path, live=True, stop=h.startup.stop))['status'] == 'STOPPED'
    assert capital_reads == [h.meta.slug, next_slug]
    assert len(public_clients) == 2 and h.calls.count('account_identity') == 2
    assert h.events[-1]['next_market_reader_reused']
    assert all(reader.closed for reader in h.readers)


async def test_recovery_remains_pinned_and_keeps_saved_limits_across_t240_and_t300(tmp_path, monkeypatch):
    h = harness(tmp_path, monkeypatch, phase='market_fees')
    saved = SETTINGS.model_copy(update={'heartbeat_sec': 20})
    ident = {'strategy': STRATEGY, 'mode': 'LIVE', 'funder': FUNDER,
             'market_id': h.meta.condition_id, 'settings_hash': saved.fingerprint}
    path = h.startup.root/'ledgers'/f'{h.meta.slug}.sqlite3'
    ledger = M4Ledger(path, ident)
    ledger.initialize_session('offline_recovery', saved.model_dump(mode='json'), asdict(h.meta))
    ledger.close()
    h.clock_values[:] = [EPOCH+239, EPOCH+301, EPOCH+302]
    ready = await h.startup.prepare(SETTINGS, h.reads_type, None)
    assert ready.path == path and ready.slug == h.meta.slug
    assert ready.settings.heartbeat_sec == 20
    assert ready.clock[0] == EPOCH+302  # Existing market engine drains; no new T240 entry.
    assert ready.identity == ident and h.events[-1]['recovery'] is True
    assert len(list(tmp_path.rglob('*.sqlite3'))) == 1
    ready.reads.close()


@pytest.mark.parametrize('marker', [False, True])
async def test_stop_interrupts_retry_without_another_request(tmp_path, monkeypatch, marker):
    h = harness(tmp_path, monkeypatch, phase='clock')
    original = h.startup.report

    def report(status, **fields):
        original(status, **fields)
        if status == 'RETRYING_READ':
            if marker:
                h.startup.control.manual_path.parent.mkdir(parents=True)
                h.startup.control.manual_path.write_text('{}')
            else:
                h.startup.stop.set()

    monkeypatch.setattr(h.startup, 'report', report)
    # Exercise the real interruptible backoff.
    monkeypatch.setattr(h.startup, 'pause', LiveStartup.pause.__get__(h.startup))
    assert await h.startup.prepare(SETTINGS, h.reads_type, None) is None
    assert h.calls == ['clock'] and h.events[-1]['status'] == 'STOPPED'


async def test_backoff_is_capped_and_status_does_not_leak_exception_text(tmp_path, monkeypatch):
    h = harness(tmp_path, monkeypatch)

    async def broken_clock():
        if len(h.pauses) == 8:
            h.startup.stop.set()
        raise httpx.ConnectTimeout('secret headers must never be logged')

    monkeypatch.setattr(h.startup.api, 'clock', broken_clock)
    assert await h.startup.prepare(SETTINGS, h.reads_type, None) is None
    assert h.pauses == [.5, 1, 2, 4, 5, 5, 5, 5, 5]
    assert 'secret' not in json.dumps(h.events)


async def test_live_public_api_keeps_get_only_boundary_and_no_implicit_retry():
    calls = []

    def wire(request):
        calls.append(request)
        raise httpx.ConnectTimeout('tls')

    api = LivePublicAPI(transport=httpx.MockTransport(wire))
    try:
        with pytest.raises(httpx.ConnectTimeout):
            await api.clock()
        assert len(calls) == 1
        with pytest.raises(RuntimeError, match='WRITE_OR_HOST_DENIED'):
            await api.client.post('https://clob.polymarket.com/order', json={})
        assert len(calls) == 1
    finally:
        await api.close()


@pytest.mark.parametrize('result', ['COMPLETE', 'RETAINED', 'market_error'])
async def test_live_runner_once_never_retries_a_started_market(tmp_path, monkeypatch, result):
    from polymaker import m4_live_http, m4_live_taker, m4_run
    from polymaker.config import Config
    from polymaker.rdp_sm import OFFICIAL_HOSTS

    h = harness(tmp_path, monkeypatch, phase='clock')
    (tmp_path/'config/m4-live').mkdir(parents=True)
    (tmp_path/'config/m4.toml').write_text('')
    (tmp_path/'config/m4-live/execution.toml').write_text('mode="taker"\n')
    config = SimpleNamespace(wallet=SimpleNamespace(**OFFICIAL_HOSTS, chain_id=137),
                             paths=SimpleNamespace(), proxy=None)
    config.model_copy = lambda **_: config
    monkeypatch.setattr(Config, 'load', lambda *_: config)
    monkeypatch.setattr(m4_live_startup, 'LivePublicAPI', lambda: h.startup.api)
    monkeypatch.setattr(m4_live_startup, 'LiveStartup', lambda *args: h.startup)
    monkeypatch.setattr(m4_live_http, 'M4LiveReads', h.reads_type)
    runs = []

    async def no_socket(self):
        await asyncio.Event().wait()

    class Market:
        def __init__(self, e, settings, stop, **kwargs):
            self.e = e

        async def run(self):
            runs.append(self.e.slug)
            if result == 'market_error':
                raise httpx.ConnectTimeout('after market startup; must not restart')
            self.e.ledger.set('complete', result == 'COMPLETE')
            return result == 'COMPLETE'

    monkeypatch.setattr(m4_run.M4Feed, 'run', no_socket)
    monkeypatch.setattr(m4_live_taker, 'M4TakerMarket', Market)
    if result == 'market_error':
        with pytest.raises(httpx.ConnectTimeout):
            await m4_run.run(tmp_path, live=True, once=True)
    else:
        assert (await m4_run.run(tmp_path, live=True, once=True))['status'] == result
    assert len(runs) == 1 and all(r.closed for r in h.readers)
    assert len(list(tmp_path.rglob('*.sqlite3'))) == 1
