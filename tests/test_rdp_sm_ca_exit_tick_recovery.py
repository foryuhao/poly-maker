"""00:15: an owned expiry loop must refresh ticks after the book runs to an edge."""
import asyncio
from decimal import Decimal as D

import httpx
import pytest

from test_rdp_sm_ca_entry import confirm_ca
from test_rdp_sm_ca_entry import env as env
from test_rdp_sm_ca_entry_price_filter import set_ask
from test_rdp_sm_ca_taker import setup_taker, taker_fill
from test_rdp_sm_cached_reads import attach, finish
from test_rdp_sm_m1 import CID
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_obi_execution import cleanup


@pytest.mark.parametrize('cached', [False, True])
@pytest.mark.parametrize('mode', ['normal', 'read_failure_once', 'conflict_once', 'empty_then_restore',
                                 'read_failure_persistent', 'empty_forever', 'unrelated_tick'])
async def test_expiry_owner_recovers_official_tick_without_losing_deadline_or_bypassing_books(
    env, monkeypatch, tmp_path, cached, mode
):
    setup = await setup_taker(env, monkeypatch, tp=True)
    v, e, feed, _, normal = setup[:5]
    # Preserve genuinely empty Asks in the test socket pump, as production WS does.
    state = setup[-2]
    state['running'] = False
    await setup[-1]
    state['running'] = True

    async def pump():
        while state['running']:
            feed.apply([{'event_type': 'book', 'market': CID, 'asset_id': token,
                'timestamp': str(feed.wall()*1000),
                'bids': [{'price': str(p), 'size': str(q)} for p, q in feed.books[token].bids],
                'asks': [{'price': str(p), 'size': str(q)} for p, q in feed.books[token].asks]}
                for token in ('1', '2')])
            await asyncio.sleep(.01)

    setup[-1] = asyncio.create_task(pump())
    v.ticks['2'] = '.01'
    feed.apply({'event_type': 'tick_size_change', 'market': CID, 'asset_id': '2',
                'old_tick_size': '.001', 'new_tick_size': '.01', 'timestamp': str(feed.wall()*1000)})
    feed.verify_ticks(v.ticks)
    e.cfg.rdp_sm.ba.signal_lifetime_sec = 3
    e.cfg.rdp_sm.ba.max_entry_age_ms = 1000
    e.cfg.rdp_sm.ca_matched_tp.taker_exit_on_expiry = True
    e.cfg.rdp_sm.ca_matched_tp.ttl_sec = .08
    e.cfg.rdp_sm.repair.ba_enabled = False
    set_ask(feed, '2', '.21')
    companion = (await attach(setup, tmp_path))[0] if cached else None
    native, refresh_native = e.gateway.send_prepared, e.reads.refresh_feed_ticks
    refreshes, exit_ids = [], []

    def target_book(empty=False):
        feed.apply({'event_type': 'book', 'market': CID, 'asset_id': '1',
                    'timestamp': str(feed.wall()*1000),
                    'bids': [{'price': '.998', 'size': '80'}],
                    'asks': [] if empty else [{'price': '.999', 'size': '20'}]})

    async def refresh(*, tokens=None):
        assert tokens == ('1',)
        refreshes.append(e.monotonic())
        assert len(v.post_requests) == 2 and not exit_ids
        assert feed.tick_verified['1'] == D('.01')
        if mode == 'read_failure_once' and len(refreshes) == 1:
            raise httpx.ReadTimeout('offline tick refresh timed out once')
        if mode == 'read_failure_persistent':
            if len(refreshes) == 1:
                e.cfg.rdp_sm.repair.cutoff_sec = (e.official_time+e.monotonic()-e.time_anchor
                                                - int(e.slug.rsplit('-', 1)[1])+.65)
            raise httpx.ReadTimeout('offline tick refresh remains unavailable')
        try:
            await refresh_native(tokens=tokens)
        finally:
            if mode == 'conflict_once':
                v.ticks['1'] = '.001'
        if mode == 'empty_then_restore':
            # Recovery is required even when EMPTY_BOOK masks TICK_UNVERIFIED.
            assert feed.snapshot(taker_buy_token='1').reason == 'WS_EMPTY_BOOK'
            await asyncio.sleep(.03)
            target_book()
        if mode == 'empty_forever':
            e.cfg.rdp_sm.repair.cutoff_sec = (e.official_time+e.monotonic()-e.time_anchor
                                            - int(e.slug.rsplit('-', 1)[1])+.3)

    async def send(signed, **kwargs):
        first = not v.post_requests
        if not first and not signed.post_only:
            assert feed.tick_verified['1'] == feed.books['1'].tick == D('.001')
            assert signed.quotes[0].price == .999
            assert D(signed.orders[0].order.makerAmount)/1_000_000 == 5
            assert D(signed.orders[0].order.takerAmount)/1_000_000 == D('5.0050')
            assert feed.snapshot(taker_buy_token='1').valid
            exit_ids.append(signed.order_ids[0])
        raw = await native(signed, **kwargs)
        oid = signed.order_ids[0]
        if first:
            taker_fill(setup, oid)
        elif signed.post_only:
            v.ticks['1'] = '.01' if mode == 'conflict_once' else '.001'
            feed.apply({'event_type': 'tick_size_change', 'market': CID, 'asset_id': '1',
                        'old_tick_size': '.01', 'new_tick_size': '.001',
                        'timestamp': str(feed.wall()*1000)})
            target_book(empty=mode in {'empty_then_restore', 'empty_forever'})
            if mode == 'unrelated_tick':
                feed.verify_ticks({'1': '.001'})
                # The opening side's new tick is deliberately not proved;
                # only the target BUY book may gate this exit.
                feed.apply({'event_type': 'tick_size_change', 'market': CID, 'asset_id': '2',
                            'old_tick_size': '.01', 'new_tick_size': '.001',
                            'timestamp': str(feed.wall()*1000)})
        else:
            taker_fill(setup, oid, quantity='5.005005', price='.999')
        return raw

    monkeypatch.setattr(e.reads, 'refresh_feed_ticks', refresh)
    monkeypatch.setattr(e.gateway, 'send_prepared', send)
    try:
        await confirm_ca(feed, '2')
        entered = await asyncio.wait_for(normal.enter(D(0), D(0), 'session:w1'), 8)
        assert entered is not None, (normal.quote_block_reason, normal.signal_block_reason)
        result, sizing = entered
        failed = mode in {'read_failure_persistent', 'empty_forever'}
        if failed:
            assert result.close_barrier == 'BLOCKED' and len(v.post_requests) == 2 and not exit_ids
            assert e.economic_inventory() == (D(0), D(5))
            assert sizing['ca_matched_tp']['expiry_exit_reason'] == 'CA_EXPIRY_EXIT_OUTSIDE_WINDOW'
        else:
            assert result.close_barrier == 'SAFE_TO_CONTINUE', result.block_reason
            assert len(v.post_requests) == 3 and len(exit_ids) == 1 and e.fatal is None
            assert e.economic_inventory() == (D('5.005005'), D(5))
        if mode == 'read_failure_persistent':
            assert refreshes
        else:
            assert len(refreshes) == (0 if mode == 'unrelated_tick' else
                                     2 if mode in {'read_failure_once', 'conflict_once'} else 1)
        await e.journal.flush()
        events = journal_records(e.journal.path)
        lease = next(r['data']['payload'] for r in events if r['kind'] == 'sm_ca_matched_tp_lease_started')
        if not failed:
            signal = next(r['data']['payload'] for r in events if r['kind'] == 'sm_ca_expiry_exit_signal')
            assert signal['deadline_monotonic'] == lease['expires_at_monotonic']
        if mode == 'empty_forever':
            waits = [r['data']['payload'] for r in events if r['kind'] == 'sm_ca_expiry_exit_wait']
            assert any(w['reason'] == 'WS_EMPTY_BOOK' and w['exit_book']['ask'] == 'None'
                       and w['exit_book']['tick_verified'] for w in waits)
    finally:
        await finish(setup, companion) if companion else await cleanup(setup)
