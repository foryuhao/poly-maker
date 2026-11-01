"""Timed Buy-only CA exit through native FAK and positive cancellation proof."""
import asyncio
from decimal import Decimal as D

import httpx
import pytest
from pydantic import ValidationError

from polymaker.config import RDPCAMatchedTPConfig, RDPSMConfig
from polymaker.dashboard.model import summarize
from test_dashboard import event, header, source
from test_dashboard import fill as dashboard_fill
from test_dashboard import placement as dashboard_placement
from test_rdp_sm_ca_entry import confirm_ca
from test_rdp_sm_ca_entry import env as env
from test_rdp_sm_ca_matched_tp import fill, prices
from test_rdp_sm_ca_taker import reflect, setup_taker, taker_fill
from test_rdp_sm_cached_reads import attach, finish
from test_rdp_sm_m1 import CID
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_obi_execution import cleanup


def test_expiry_exit_opt_in_and_configuration_constraints():
    assert not RDPCAMatchedTPConfig().taker_exit_on_expiry
    with pytest.raises(ValidationError, match='requires ca_matched_tp.enabled'):
        RDPCAMatchedTPConfig(taker_exit_on_expiry=True)
    with pytest.raises(ValidationError, match='ba_enabled=false'):
        RDPSMConfig(normal_entry_mode='ca_v1', ca_observe={'enabled': True},
                    repair={'policy': 'opposite_ba_v1'},
                    ca_matched_tp={'enabled': True, 'taker_exit_on_expiry': True})


@pytest.mark.parametrize('cached', [False, True])
@pytest.mark.parametrize('token', ['1', '2'])
@pytest.mark.parametrize('tp_fill', ['none', 'partial', 'cancel_race', 'full'])
async def test_expired_tp_only_takes_proved_remaining_shares(env, monkeypatch, tmp_path, cached, token, tp_fill):
    setup = await setup_taker(env, monkeypatch, tp=True)
    v, e, feed, lifecycle, normal = setup[:5]
    e.cfg.rdp_sm.ca_matched_tp.taker_exit_on_expiry = True
    e.cfg.rdp_sm.ca_matched_tp.profit_per_share = D('.05')
    e.cfg.rdp_sm.ca_matched_tp.ttl_sec = .08
    e.cfg.rdp_sm.repair.ba_enabled = False
    companion = (await attach(setup, tmp_path))[0] if cached else None
    native = e.gateway.send_prepared
    tp_id = []

    async def send(signed, **kwargs):
        first = not v.post_requests
        if not first and not signed.post_only:
            assert tp_id[0] in e.proofs and tp_id[0] in e.terminal
            assert v.rows[tp_id[0]]['status'] == 'CANCELED'
            assert signed.orders[0].orderType == 'FAK'
        raw = await native(signed, **kwargs)
        oid = signed.order_ids[0]
        if first:
            taker_fill(setup, oid)
        elif signed.post_only:
            tp_id.append(oid)
            if tp_fill in {'partial', 'full'}:
                fill(v, oid, 'tp-fill', qty=2 if tp_fill == 'partial' else 5)
                v.rows[oid]['status'] = 'LIVE' if tp_fill == 'partial' else 'MATCHED'
                reflect(setup)
        else:
            snapshot = lifecycle.feed.snapshot()
            price = snapshot.yes.best_ask if token == '2' else snapshot.no.best_ask
            taker_fill(setup, oid, price=price)
        return raw

    def cancel_race(venue):
        if (tp_fill == 'cancel_race' and tp_id and venue.rows[tp_id[0]]['status'] == 'CANCELED'
                and not venue.rows[tp_id[0]]['associate_trades']):
            fill(venue, tp_id[0], 'tp-race', qty=2)
            reflect(setup)

    v.on_cancel = cancel_race
    monkeypatch.setattr(e.gateway, 'send_prepared', send)
    try:
        await confirm_ca(feed, token)
        entered = await asyncio.wait_for(normal.enter(D(0), D(0), 'session:w1'), 8)
        assert entered is not None, {'budget': lifecycle.account.get('basic_spendable_budget_usd'),
                                    'limit': str(e.capital_limit), 'quote': normal.quote_block_reason,
                                    'signal': normal.signal_block_reason}
        result, sizing = entered
        assert result.close_barrier == 'SAFE_TO_CONTINUE', result.block_reason
        assert e.economic_inventory() == (D(5), D(5))
        assert len(v.post_requests) == (2 if tp_fill == 'full' else 3)
        if tp_fill != 'full':
            last = e.attempts[-1]
            assert last.quote.window_id.endswith(':ca-expiry-exit')
            assert last.quote.size == (3 if tp_fill in {'partial', 'cancel_race'} else 5)
            assert last.quote.token_id != token
            assert last.entry_execution == 'taker'
            assert v.post_requests[-1][0]['postOnly'] is False
            assert sizing['ca_matched_tp']['expiry_exit_reason'] == 'PAIRED'
            await e.journal.flush()
            events = journal_records(e.journal.path)
            expiry = next(r['data']['payload'] for r in events if r['kind'] == 'sm_ca_matched_tp_lease_started')
            sent = next(r['data']['payload'] for r in events if r['kind'] == 'sm_ca_expiry_exit_command')
            assert sent['post_started_monotonic'] >= expiry['expires_at_monotonic']
            assert e.confirmed()[-1].fee_usdc is not None or any(
                f.maker_order_id == last.order_id and f.fee_usdc is not None for f in e.confirmed())
    finally:
        await finish(setup, companion) if companion else await cleanup(setup)


@pytest.mark.parametrize('fault', ['reject', 'unknown', 'not_filled', 'insufficient_cash'])
async def test_definite_failure_retries_but_unknown_or_insufficient_cash_blocks(env, monkeypatch, fault):
    setup = await setup_taker(env, monkeypatch, tp=True)
    v, e, feed, _, normal, wire = setup[:6]
    e.cfg.rdp_sm.ca_matched_tp.taker_exit_on_expiry = True
    e.cfg.rdp_sm.ca_matched_tp.ttl_sec = .06
    e.cfg.rdp_sm.repair.ba_enabled = False
    native = e.gateway.send_prepared
    exits = []

    async def send(signed, **kwargs):
        first = not v.post_requests
        exit_order = not first and not signed.post_only
        if exit_order:
            exits.append(signed.order_ids[0])
            if fault == 'reject':
                wire.reject_index = 0 if len(exits) == 1 else None
            elif fault == 'unknown':
                wire.fail_ack = True
        try:
            raw = await native(signed, **kwargs)
        finally:
            if exit_order and fault == 'unknown':
                v.rows.pop(signed.order_ids[0], None)
        if first or exit_order and len(exits) == 2:
            taker_fill(setup, signed.order_ids[0])
        if signed.post_only and fault == 'insufficient_cash':
            monkeypatch.setattr(type(e), 'capital_limit', property(lambda _: D(5)))
        return raw

    monkeypatch.setattr(e.gateway, 'send_prepared', send)
    try:
        await confirm_ca(feed)
        result, _ = await asyncio.wait_for(normal.enter(D(0), D(0), 'session:w1'), 8)
        blocked = fault in {'unknown', 'insufficient_cash'}
        assert result.close_barrier == ('BLOCKED' if blocked else 'SAFE_TO_CONTINUE'), result.block_reason
        assert bool(e.fatal) == blocked
        assert len(v.post_requests) == (2 if fault == 'insufficient_cash' else 3 if blocked else 4)
        assert e.economic_inventory() == (D(5), D(0) if blocked else D(5))
        assert e.safe_to_continue != blocked
    finally:
        await cleanup(setup)


@pytest.mark.parametrize('opening_type', ['maker', 'taker'])
async def test_exit_lost_ack_reconciles_by_signed_id_without_another_post(env, monkeypatch, opening_type):
    setup = await setup_taker(env, monkeypatch, tp=True)
    v, e, feed, _, normal = setup[:5]
    e.cfg.rdp_sm.ca_entry_execution = opening_type
    e.cfg.rdp_sm.ca_matched_tp.taker_exit_on_expiry = True
    e.cfg.rdp_sm.ca_matched_tp.ttl_sec = .06
    e.cfg.rdp_sm.repair.ba_enabled = False
    native = e.gateway.send_prepared

    async def send(signed, **kwargs):
        first = not v.post_requests
        raw = await native(signed, **kwargs)
        if first:
            if signed.post_only:
                fill(v, signed.order_ids[0], 'opening-maker')
                reflect(setup)
            else:
                taker_fill(setup, signed.order_ids[0])
        elif not signed.post_only:
            taker_fill(setup, signed.order_ids[0], price='.51')
            raise httpx.ReadTimeout('accepted expiry exit, lost acknowledgement')
        return raw

    monkeypatch.setattr(e.gateway, 'send_prepared', send)
    try:
        await confirm_ca(feed)
        result, sizing = await asyncio.wait_for(normal.enter(D(0), D(0), 'session:w1'), 8)
        assert result.close_barrier == 'SAFE_TO_CONTINUE', result.block_reason
        assert e.economic_inventory() == (D(5), D(5))
        assert len(v.post_requests) == 3
        assert e.attempts[-1].outcome == 'VERIFIED'
        assert sizing['ca_matched_tp']['expiry_exit_reason'] == 'PAIRED'
    finally:
        await cleanup(setup)


async def test_exit_cannot_overlap_a_tp_with_unfinished_cancel(env, monkeypatch):
    setup = await setup_taker(env, monkeypatch, tp=True)
    v, e, feed, _, normal = setup[:5]
    e.cfg.rdp_sm.ca_matched_tp.taker_exit_on_expiry = True
    e.cfg.rdp_sm.ca_matched_tp.ttl_sec = .06
    e.cfg.rdp_sm.repair.ba_enabled = False
    native, cancel = e.gateway.send_prepared, e.gateway.cancel_detailed
    blocked, release = asyncio.Event(), asyncio.Event()
    tp = []

    async def send(signed, **kwargs):
        first = not v.post_requests
        raw = await native(signed, **kwargs)
        if first:
            taker_fill(setup, signed.order_ids[0])
        elif signed.post_only:
            tp.extend(signed.order_ids)
        else:
            taker_fill(setup, signed.order_ids[0], price='.51')
        return raw

    async def delayed_cancel(ids):
        if set(ids) & set(tp):
            blocked.set()
            await release.wait()
        return await cancel(ids)

    monkeypatch.setattr(e.gateway, 'send_prepared', send)
    monkeypatch.setattr(e.gateway, 'cancel_detailed', delayed_cancel)
    task = None
    try:
        await confirm_ca(feed)
        task = asyncio.create_task(normal.enter(D(0), D(0), 'session:w1'))
        await asyncio.wait_for(blocked.wait(), 3)
        await asyncio.sleep(.1)
        assert len(v.post_requests) == 2 and not task.done()
        assert v.rows[tp[0]]['status'] == 'LIVE'
        release.set()
        result, _ = await asyncio.wait_for(task, 8)
        assert result.close_barrier == 'SAFE_TO_CONTINUE', result.block_reason
        assert len(v.post_requests) == 3 and e.economic_inventory() == (D(5), D(5))
    finally:
        release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await cleanup(setup)


async def test_unavailable_tp_still_hedges_at_ttl_by_topping_up_minimum(env, monkeypatch):
    setup = await setup_taker(env, monkeypatch, tp=True)
    v, e, feed, _, normal = setup[:5]
    e.cfg.rdp_sm.ca_matched_tp.taker_exit_on_expiry = True
    e.cfg.rdp_sm.ca_taker.entry_price_ceiling = D('.99')  # Explicit override to exercise the no-TP fallback.
    e.cfg.rdp_sm.ca_matched_tp.profit_per_share = D('.05')
    e.cfg.rdp_sm.ca_matched_tp.ttl_sec = .08
    e.cfg.rdp_sm.repair.ba_enabled = False
    # This intentionally bypasses the deployed .90 entry filter. Fully funded
    # worst-case mill-tick retries need more than the normal $20 cycle here.
    monkeypatch.setattr(type(e), 'capital_limit', property(lambda _: D(25)))
    setup[3].account = (await e._snapshot(include_books=False))[3]
    prices(feed, '.96')
    native = e.gateway.send_prepared

    async def send(signed, **kwargs):
        first = not v.post_requests
        raw = await native(signed, **kwargs)
        taker_fill(setup, signed.order_ids[0], price='.97' if first else '.04')
        return raw

    monkeypatch.setattr(e.gateway, 'send_prepared', send)
    try:
        await confirm_ca(feed)
        result, sizing = await asyncio.wait_for(normal.enter(D(0), D(0), 'session:w1'), 8)
        assert result.close_barrier == 'SAFE_TO_CONTINUE', result.block_reason
        assert len(v.post_requests) == 2 and not v.post_requests[0][0]['postOnly']
        assert e.attempts[-1].quote.size == 25
        assert e.economic_inventory() == (D(5), D(25))
        assert sizing['ca_matched_tp']['reason'] == 'ENTRY_PRICE_TOO_HIGH'
        assert sizing['ca_matched_tp']['expiry_exit_reason'] == 'HEDGED_WITH_EXCESS'
        await e.journal.flush()
        command = next(row for row in journal_records(e.journal.path)
                       if row['kind'] == 'sm_ca_expiry_exit_result')
        opened = next(stamp['observed_monotonic'] for (tid, oid), stamp in e.fill_observed_at.items()
                      if oid == e.attempts[0].order_id)
        assert command['data']['occurred_monotonic'] >= opened+.08
        assert D(command['data']['payload']['original_unhedged_shares']) == 0
        assert D(command['data']['payload']['extra_reverse_shares']) == 20
    finally:
        await cleanup(setup)


def test_dashboard_counts_expiry_as_exit_and_includes_both_taker_fees():
    events = [event('sm_session', header()),
              event('sm_placement_result', dashboard_placement('opening', 'YES'), 1),
              event('sm_trade', dashboard_fill('opening', 'YES', '.4', liquidity_role='TAKER',
                                              principal_usdc='2', fee_usdc='.084'), 2),
              event('sm_placement_result', dashboard_placement('expired', 'NO', 'w1:ca-expiry-exit'), 3),
              event('sm_trade', dashboard_fill('expired', 'NO', '.65', liquidity_role='TAKER',
                                              principal_usdc='3.25', fee_usdc='.07'), 4),
              event('sm_ca_expiry_exit_result', {'orders': [{'order_id': 'expired', 'complete': True}]}, 5)]
    result = summarize('session-test', events, [source()])
    assert D(result['pair_pnl']) == D('-.404')
    assert D(result['fees']) == D('.154')
    assert result['post_counts']['CA']['YES'] == 1
    assert result['post_counts']['EXIT']['NO'] == 1
    assert result['post_counts']['TP']['NO'] == 0
    assert result['pairs'][0]['exit_role'] == 'EXIT'
    assert result['pairs'][0]['category'] == 'SL'
    assert D(result['pnl_by_role']['SL']) == D('-.404')
    assert D(result['pnl_by_role']['OTHER']) == 0
    assert not result['issues']


async def test_entry_requires_enough_budget_for_its_future_expiry_exit(env, monkeypatch):
    setup = await setup_taker(env, monkeypatch, tp=True)
    v, e, feed, _, normal = setup[:5]
    e.cfg.rdp_sm.ca_matched_tp.taker_exit_on_expiry = True
    e.cfg.rdp_sm.repair.ba_enabled = False
    monkeypatch.setattr(type(e), 'capital_limit', property(lambda _: D(5)))
    try:
        await confirm_ca(feed)
        assert await normal.enter(D(0), D(0), 'session:w1') is None
        assert not v.post_requests and not feed.ca_entry.consumed
    finally:
        await cleanup(setup)


@pytest.mark.parametrize('filled', [4.99, 4.994263])
async def test_verified_tp_dust_is_accepted_without_minimum_topup(env, monkeypatch, filled):
    setup = await setup_taker(env, monkeypatch, tp=True)
    v, e, feed, _, normal = setup[:5]
    e.cfg.rdp_sm.ca_matched_tp.taker_exit_on_expiry = True
    e.cfg.rdp_sm.ca_matched_tp.ttl_sec = .06
    e.cfg.rdp_sm.repair.ba_enabled = False
    native = e.gateway.send_prepared

    async def send(signed, **kwargs):
        first = not v.post_requests
        raw = await native(signed, **kwargs)
        if first:
            taker_fill(setup, signed.order_ids[0])
        elif signed.post_only:
            fill(v, signed.order_ids[0], 'tp-almost-filled', qty=filled)
            v.rows[signed.order_ids[0]]['status'] = 'LIVE'
            reflect(setup)
        else:
            taker_fill(setup, signed.order_ids[0])
        return raw

    monkeypatch.setattr(e.gateway, 'send_prepared', send)
    try:
        await confirm_ca(feed)
        result, _ = await asyncio.wait_for(normal.enter(D(0), D(0), 'session:w1'), 8)
        assert result.close_barrier == 'SAFE_TO_CONTINUE', result.block_reason
        assert len(v.post_requests) == 2
        assert e.economic_inventory() == (D(5), D(str(filled)))
        await e.journal.flush()
        event = next(row['data']['payload'] for row in journal_records(e.journal.path)
                     if row['kind'] == 'sm_ca_expiry_exit_result')
        assert event['reason'] == 'WITHIN_RESIDUAL_TOLERANCE'
        assert D(event['original_unhedged_shares']) == D(5)-D(str(filled))
    finally:
        await cleanup(setup)


async def test_partial_fak_exit_retries_proven_remainder_without_sell(env, monkeypatch):
    setup = await setup_taker(env, monkeypatch, tp=True)
    v, e, feed, _, normal = setup[:5]
    e.cfg.rdp_sm.ca_matched_tp.taker_exit_on_expiry = True
    e.cfg.rdp_sm.ca_matched_tp.ttl_sec = .06
    e.cfg.rdp_sm.repair.ba_enabled = False
    native = e.gateway.send_prepared
    exits = []

    async def send(signed, **kwargs):
        first = not v.post_requests
        raw = await native(signed, **kwargs)
        if first:
            taker_fill(setup, signed.order_ids[0])
        elif signed.post_only:
            fill(v, signed.order_ids[0], 'partial-tp', qty=2)
            v.rows[signed.order_ids[0]]['status'] = 'LIVE'
            reflect(setup)
        else:
            if exits:
                assert exits[-1] in e.proofs and exits[-1] in e.terminal
            exits.append(signed.order_ids[0])
            assert signed.quotes[0].size == (3 if len(v.post_requests) == 3 else 2)
            taker_fill(setup, signed.order_ids[0], quantity=2, price='.51')
        return raw

    monkeypatch.setattr(e.gateway, 'send_prepared', send)
    try:
        await confirm_ca(feed)
        result, sizing = await asyncio.wait_for(normal.enter(D(0), D(0), 'session:w1'), 8)
        assert result.close_barrier == 'SAFE_TO_CONTINUE', result.block_reason
        assert e.economic_inventory() == (D(5), D(6))
        assert len(v.post_requests) == 4 and e.fatal is None
        assert all(item['order']['side'] == 'BUY' for request in v.post_requests for item in request)
        assert sizing['ca_matched_tp']['expiry_exit_reason'] == 'HEDGED_WITH_EXCESS'
        assert await normal.enter(*e.economic_inventory(), 'session:w2') is None
        assert len(v.post_requests) == 4
    finally:
        await cleanup(setup)


def test_fak_quantity_rounds_up_to_cover_remaining_and_marketable_minimum():
    from polymaker.rdp_sm_ca_expiry_exit import executable_quantity

    assert executable_quantity(D('3.99'), D('.51')) == D(4)
    assert executable_quantity(D('3.99'), D('.52')) == D(4)
    assert executable_quantity(D('.01'), D('.51')) == D(2)
    assert executable_quantity(D(5), D('.19')) == D(6)
    assert executable_quantity(D(5), D('.01')) == D(100)
    assert executable_quantity(D(5), D('.001')) == D(1000)


def test_all_legal_mill_prices_cover_quantity_minimum_cent_precision_and_cash_budget():
    from polymaker.rdp_sm_ca_expiry_exit import executable_quantity, exit_budget, exit_fee_reserve

    fees = {'rate': '.07', 'exponent': 1}
    for mill in range(1, 1000):
        price = D(mill)/1000
        for remaining in [D('.01'), D('3.99'), D(5)]:
            qty = executable_quantity(remaining, price)
            assert qty >= remaining and price*qty >= 1
            assert (price*qty) % D('.01') == 0
            reserve = exit_fee_reserve(qty, price, fees)
            assert reserve >= (qty*D('.07')*price*(1-price)).quantize(D('.00001'))
            assert price*qty+reserve <= exit_budget(D(5), D('.001'), fees)
            prior = D(5)-remaining
            prior_fee = prior*D('.035')+D('.000001')
            assert prior*D('.999')+prior_fee+price*qty+reserve <= exit_budget(D(5), D('.001'), fees)


def test_exit_budget_keeps_89_entry_fundable_and_bounds_price_improvement_cost():
    from polymaker.rdp_sm_ca_expiry_exit import exit_budget, exit_fee_reserve

    fees = {'rate': '.07', 'exponent': 1}
    # Dollar-budget BUY may get more shares at a better price. Reserve future
    # dollar execution including this possibility, on the ordinary cent tick.
    from polymaker.rdp_sm_ca_taker import buy_fee_reserve
    assert D(5)*D('.89')+buy_fee_reserve(D(5), D('.89'), fees)+exit_budget(D(5), D('.01'), fees) < 20
    for exponent in range(1, 6):
        for rate in ['.07', '.6']:
            params = {'rate': rate, 'exponent': exponent}
            for cap in [D('.01'), D('.19'), D('.51'), D('.99')]:
                maximum = D(5)*cap+exit_fee_reserve(D(5), cap, params)
                for mill in range(1, int(cap*1000)+1):
                    price = D(mill)/1000
                    fee = (D(5)*D(rate)*(price*(1-price))**exponent).quantize(D('.00001'))
                    assert D(5)*price+fee <= maximum


@pytest.mark.parametrize('fault', ['empty_ask', 'crossed', 'unverified_tick', 'stale', 'disconnected'])
async def test_ask_only_exit_does_not_bypass_market_evidence(env, monkeypatch, fault):
    setup = await setup_taker(env, monkeypatch)
    _, _, feed = setup[:3]
    try:
        feed.apply({'event_type': 'book', 'market': CID, 'asset_id': '2',
                    'timestamp': str(feed.wall()*1000), 'bids': [],
                    'asks': [{'price': '.01', 'size': '200'}]})
        assert feed.snapshot(taker_buy_token='2').valid
        assert not feed.snapshot().valid  # Normal/Maker still requires both sides.
        if fault in {'empty_ask', 'crossed'}:
            feed.apply({'event_type': 'book', 'market': CID, 'asset_id': '2',
                        'timestamp': str(feed.wall()*1000),
                        'bids': [{'price': '.02', 'size': '80'}],
                        'asks': [] if fault == 'empty_ask' else [{'price': '.01', 'size': '200'}]})
        elif fault == 'unverified_tick':
            feed.tick_verified.pop('2')
        elif fault == 'stale':
            clock = feed.monotonic
            monkeypatch.setattr(feed, 'monotonic', lambda: clock()+1)
        else:
            feed.connected = False
            feed.reason = 'WS_DISCONNECTED'
        assert not feed.snapshot(taker_buy_token='2').valid
    finally:
        # The shared socket fixture requires a Bid to build subsequent frames.
        feed.connected = True
        feed.reason = ''
        feed.apply({'event_type': 'book', 'market': CID, 'asset_id': '2',
                    'timestamp': str(feed.wall()*1000),
                    'bids': [{'price': '.49', 'size': '80'}],
                    'asks': [{'price': '.51', 'size': '200'}]})
        await cleanup(setup)


@pytest.mark.parametrize('cached', [False, True])
@pytest.mark.parametrize('token', ['1', '2'])
@pytest.mark.parametrize('price,quantity', [('.19', 6), ('.01', 100), ('.001', 1000), ('.99', 5)])
async def test_exit_tops_up_minimum_even_above_size_limit_and_keeps_excess_cost(
    env, monkeypatch, tmp_path, cached, token, price, quantity
):
    setup = await setup_taker(env, monkeypatch, tp=True)
    v, e, feed, _, normal = setup[:5]
    # Match the real CA lifetime/entry-age settings: this case tests exit
    # sizing, while short formation/admission deadlines have dedicated tests.
    e.cfg.rdp_sm.ba.signal_lifetime_sec = 3
    e.cfg.rdp_sm.ba.max_entry_age_ms = 1000
    state = setup[-2]
    state['running'] = False
    await setup[-1]
    state['running'] = True

    async def pump():
        while state['running']:
            feed.apply([{'event_type': 'book', 'market': CID, 'asset_id': t,
                         'timestamp': str(feed.wall()*1000),
                         'bids': [{'price': str(p), 'size': str(q)} for p, q in feed.books[t].bids],
                         'asks': [{'price': str(p), 'size': str(q)} for p, q in feed.books[t].asks]}
                        for t in ('1', '2')])
            await asyncio.sleep(.01)

    setup[-1] = asyncio.create_task(pump())
    # One official tick for both outcomes, including cached preparation reads.
    v.ticks['1'] = '.001'
    feed.apply({'event_type': 'tick_size_change', 'market': CID, 'asset_id': '1',
                'old_tick_size': '.01', 'new_tick_size': '.001',
                'timestamp': str(feed.wall()*1000)})
    feed.verify_ticks(v.ticks)
    e.cfg.rdp_sm.ca_matched_tp.taker_exit_on_expiry = True
    e.cfg.rdp_sm.ca_matched_tp.ttl_sec = .06
    e.cfg.rdp_sm.repair.ba_enabled = False
    companion = (await attach(setup, tmp_path))[0] if cached else None
    native = e.gateway.send_prepared
    opposite = '2' if token == '1' else '1'

    async def send(signed, **kwargs):
        first = not v.post_requests
        raw = await native(signed, **kwargs)
        if first:
            taker_fill(setup, signed.order_ids[0])
        elif signed.post_only:
            feed.apply({'event_type': 'book', 'market': CID, 'asset_id': opposite,
                        'timestamp': str(feed.wall()*1000),
                        'bids': [] if price in {'.01', '.001'} else [{'price': str(D(price)-D('.001')), 'size': '80'}],
                        'asks': [{'price': price, 'size': '200'}]})
        else:
            assert signed.quotes[0].size == quantity
            assert signed.quotes[0].size*float(price) >= 1
            assert signed.orders[0].orderType == 'FAK'
            assert e.attempts[-1].maximum <= D(quantity)*D(price)*D('1.14')+D('.000001')
            taker_fill(setup, signed.order_ids[0])
        return raw

    monkeypatch.setattr(e.gateway, 'send_prepared', send)
    try:
        await confirm_ca(feed, token)
        result, sizing = await asyncio.wait_for(normal.enter(D(0), D(0), 'session:w1'), 8)
        assert result.close_barrier == 'SAFE_TO_CONTINUE', result.block_reason
        assert e.economic_inventory() == ((D(5), D(quantity)) if token == '1' else (D(quantity), D(5)))
        assert len(v.post_requests) == 3
        expected = 'HEDGED_WITH_EXCESS' if quantity > 5 else 'PAIRED'
        assert sizing['ca_matched_tp']['expiry_exit_reason'] == expected
        await e.journal.flush()
        event = next(r['data']['payload'] for r in journal_records(e.journal.path)
                     if r['kind'] == 'sm_ca_expiry_exit_result')
        assert D(event['original_unhedged_shares']) == 0
        assert D(event['extra_reverse_shares']) == quantity-5
        assert D(event['extra_reverse_cost_usdc']) >= (quantity-5)*D(price)
        assert all(item['order']['side'] == 'BUY' for req in v.post_requests for item in req)
    finally:
        await finish(setup, companion) if companion else await cleanup(setup)


@pytest.mark.parametrize('fault', ['price_changed', 'cutoff_unfilled'])
async def test_exit_replans_changed_quote_but_blocks_original_unhedged_at_cutoff(env, monkeypatch, fault):
    setup = await setup_taker(env, monkeypatch, tp=True)
    v, e, feed, _, normal = setup[:5]
    e.cfg.rdp_sm.ca_matched_tp.taker_exit_on_expiry = True
    e.cfg.rdp_sm.ca_matched_tp.ttl_sec = .06
    e.cfg.rdp_sm.repair.ba_enabled = False
    native = e.gateway.send_prepared
    exits = []

    async def send(signed, **kwargs):
        first = not v.post_requests
        is_exit = not first and not signed.post_only
        if is_exit:
            exits.append(signed.order_ids[0])
            if fault == 'price_changed' and len(exits) == 1:
                feed.apply({'event_type': 'book', 'market': CID, 'asset_id': '2',
                            'timestamp': str(feed.wall()*1000),
                            'bids': [{'price': '.50', 'size': '80'}],
                            'asks': [{'price': '.52', 'size': '20'}]})
        raw = await native(signed, **kwargs)
        if first or is_exit and len(exits) == 2:
            taker_fill(setup, signed.order_ids[0])
        if is_exit and fault == 'cutoff_unfilled':
            e.cfg.rdp_sm.repair.cutoff_sec = (e.official_time+e.monotonic()-e.time_anchor
                                            - int(e.slug.rsplit('-', 1)[1])-.01)
        return raw

    monkeypatch.setattr(e.gateway, 'send_prepared', send)
    try:
        await confirm_ca(feed)
        result, sizing = await asyncio.wait_for(normal.enter(D(0), D(0), 'session:w1'), 8)
        if fault == 'price_changed':
            assert result.close_barrier == 'SAFE_TO_CONTINUE', result.block_reason
            assert len(exits) == 2 and len(v.post_requests) == 3
            assert e.economic_inventory() == (D(5), D(5))
            assert e.attempts[-2].outcome == 'ABORTED'
        else:
            assert result.close_barrier == 'BLOCKED'
            assert 'CA_EXPIRY_EXIT_OUTSIDE_WINDOW' in e.fatal
            assert len(exits) == 1 and e.economic_inventory() == (D(5), D(0))
            assert sizing['ca_matched_tp']['expiry_exit_reason'] != 'PAIRED'
    finally:
        await cleanup(setup)
