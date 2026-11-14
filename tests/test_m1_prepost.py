"""Paired real-SDK/offline-transport acceptance for the shared pre-POST path."""

# ruff: noqa: ASYNC240 -- Isolated temporary account/virtual ledgers.

import json
from dataclasses import asdict, replace
from decimal import Decimal as D

import pytest
from py_clob_client_v2.client import ClobClient

from polymaker.execution.gateway import ExecutionGateway
from polymaker.m1_live import M1Market
from polymaker.m1_live_config import M1LiveConfig
from polymaker.m1_live_signal import M1LiveSignals
from polymaker.m1_live_state import M1Ledger
from polymaker.m1_virtual import VirtualPolicy
from polymaker.m1_virtual_execution import VirtualExecutionClient, VirtualExecutionGateway
from polymaker.m1_virtual_state import VirtualStore
from polymaker.rdp_sm_execution import ExecutionClobClient
from test_m1_live import close_setup, live_setup, opportunity, prepared
from test_rdp_sm_m1_strategy import env as env
from test_rdp_sm_m1_strategy import m1_feed, seed_opportunity


def virtual_setup(tmp_path, price='.05', settings=None):
    feed, clock = m1_feed()
    feed.meta = replace(feed.meta, slug='btc-updown-5m-1793650500')
    feed.m1 = M1LiveSignals(feed)
    feed.books['1'] = replace(feed.books['1'], bids=((D(price), D(100)),),
                              asks=((D(price)+D('.01'), D(100)),), reported_bid=None, reported_ask=None)
    store = VirtualStore(tmp_path)
    store.add_market(asdict(feed.meta), 0)
    policy = VirtualPolicy(feed, settings or M1LiveConfig(), store, lambda *args: None)
    seed_opportunity(feed)
    return policy, store, clock


async def cycle(paper):
    """Let the shared background account/POST jobs finish under a static test clock."""
    await paper.poll(15, 15)
    await paper.market.drain()
    await paper.poll(15, 15)
    await paper.market.drain()
    paper.flush()


def test_both_modes_reference_identical_prepost_and_sdk_methods(tmp_path):
    paper, store, _ = virtual_setup(tmp_path)
    try:
        assert type(paper.market) is M1Market
        assert VirtualExecutionGateway.prepare is ExecutionGateway.prepare
        assert VirtualExecutionGateway.send_prepared is ExecutionGateway.send_prepared
        assert VirtualExecutionClient.create_order is ExecutionClobClient.create_order
        assert VirtualExecutionClient._post is ExecutionClobClient._post
        assert VirtualExecutionClient._native_order_post is not ExecutionClobClient._native_order_post
    finally:
        paper.executor.close()
        store.close()


@pytest.mark.parametrize('price', ['.01', '.03', '.15', '.19'])
async def test_identical_quotes_signed_amounts_and_guards_reach_only_modeled_post(
    env, monkeypatch, tmp_path, price,
):
    setup, live = await live_setup(env, monkeypatch, tmp_path/'live', price=price)
    paper, store, _ = virtual_setup(tmp_path/'virtual', price, live.settings)
    prepared, guards = {}, []
    original_prepare, original_guard = ExecutionGateway.prepare, M1Market.guard

    async def capture(gateway, *args, **kwargs):
        result = await original_prepare(gateway, *args, **kwargs)
        body = json.loads(gateway._prepared_body(result.orders, result.post_only))[0]
        key = 'virtual' if isinstance(gateway, VirtualExecutionGateway) else 'live'
        prepared[key] = (body['postOnly'], body['orderType'],
                         {k: body['order'][k] for k in ['tokenId', 'side', 'makerAmount', 'takerAmount']})
        return result

    def guard(market, *args, **kwargs):
        guards.append(market)
        return original_guard(market, *args, **kwargs)

    monkeypatch.setattr(ExecutionGateway, 'prepare', capture)
    monkeypatch.setattr(M1Market, 'guard', guard)
    try:
        await live.submit(next(iter(live.pending.values())))
        assert len(setup[0].post_requests) == 1

        def forbidden(*args, **kwargs):
            pytest.fail('Virtual signer attempted a real SDK HTTP POST')

        monkeypatch.setattr(ClobClient, '_post', forbidden)
        await cycle(paper)
        assert prepared['live'] == prepared['virtual']
        assert guards.count(live) >= 3 and guards.count(paper.market) >= 3
        row = store.instructions(paper.feed.meta.slug)[0]
        assert row['status'] == 'VIRTUAL_SUCCESS'
        assert D(row['cost']) >= 1
        assert paper.executor.ledger.rows()[0]['state'] == 'ACK'
    finally:
        await paper.close()
        store.close()
        await close_setup(setup, live)


async def test_changing_shared_guard_changes_both_modes(env, monkeypatch, tmp_path):
    setup, live = await live_setup(env, monkeypatch, tmp_path/'live')
    paper, store, _ = virtual_setup(tmp_path/'virtual')
    checked = []

    def guard(market, *args, **kwargs):
        checked.append(market)
        market.guard_reason = 'NEW_COMMON_PREPOST_RULE'
        return False

    monkeypatch.setattr(M1Market, 'guard', guard)
    try:
        await live.submit(next(iter(live.pending.values())))
        await cycle(paper)
        assert live in checked and paper.market in checked
        assert not setup[0].post_requests
        assert not store.instructions(paper.feed.meta.slug)
        assert not paper.executor.ledger.facts('virtual_fill')
    finally:
        await paper.close()
        store.close()
        await close_setup(setup, live)


async def test_final_guard_runs_after_signing_and_blocks_expired_virtual_signal(monkeypatch, tmp_path):
    paper, store, clock = virtual_setup(tmp_path)
    original = ExecutionGateway.prepare

    async def slow_sign(*args, **kwargs):
        prepared = await original(*args, **kwargs)
        clock.now += 1
        return prepared

    monkeypatch.setattr(ExecutionGateway, 'prepare', slow_sign)
    try:
        await cycle(paper)
        assert not paper.executor.ledger.facts('virtual_fill')
        assert paper.executor.ledger.rows()[0]['state'] == 'ABORTED'
        assert paper.executor.ledger.committed() == 0
        assert store.instructions(paper.feed.meta.slug)[0]['status'] == 'SKIPPED'
    finally:
        await paper.close()
        store.close()


@pytest.mark.parametrize('mode', ['live', 'virtual'])
@pytest.mark.parametrize('stage', ['prepare', 'flush', 'post_orders'])
@pytest.mark.parametrize('delay,budget,allowed', [(.10, 400, True), (.55, 400, False), (.10, 650, False)])
async def test_final_post_budget_survives_preparation_and_journal_delay(
    env, monkeypatch, tmp_path, mode, stage, delay, budget, allowed,
):
    setup = paper = store = None
    if mode == 'live':
        setup, market = await live_setup(env, monkeypatch, tmp_path/'live')
        market.settings.post_budget_floor_ms = 400
    else:
        paper, store, _ = virtual_setup(tmp_path/'virtual')
        market = paper.market
        paper.executor.official_time += 15
        await market.refresh_account()
        market.collect()
    executor, feed = market.e, market.feed
    original_now, original_feed_now = executor.monotonic, feed.monotonic
    offset = 0.
    monkeypatch.setattr(executor, 'monotonic', lambda: original_now()+offset)
    monkeypatch.setattr(feed, 'monotonic', lambda: original_feed_now()+offset)
    original_recent = executor.reads.network.recent_writes

    def recent():
        result = original_recent()
        if budget == 650:
            from types import SimpleNamespace
            return SimpleNamespace(post=SimpleNamespace(max_ms=650))
        return result

    monkeypatch.setattr(executor.reads.network, 'recent_writes', recent)
    owner = (executor.gateway if stage == 'prepare' else executor.journal if stage == 'flush'
             else executor.gateway._client)
    original = getattr(owner, stage)

    def advance_clock():
        nonlocal offset
        offset += delay
        # Fresh frames can arrive while durable preparation waits. Keep the
        # original RF/EX deadlines and isolate the remaining POST budget.
        with feed.lock:
            for token, book in feed.books.items():
                feed.books[token] = replace(book, received_mono=executor.monotonic(),
                                           source_time=feed.wall())

    async def delayed(*args, **kwargs):
        result = await original(*args, **kwargs)
        advance_clock()
        return result

    def delayed_sdk(*args, **kwargs):
        advance_clock()
        return original(*args, **kwargs)

    monkeypatch.setattr(owner, stage, delayed_sdk if stage == 'post_orders' else delayed)
    try:
        ep = market.pending.pop(next(iter(market.pending)))
        await market.submit(ep)
        sent = len(setup[0].post_requests) if setup else len(executor.ledger.facts('virtual_fill'))
        assert sent == int(allowed)
        row = market.ledger.row(ep.episode_id)
        if not allowed:
            assert row['state'] == 'ABORTED'
            assert row['reason'] == 'M1_POST_LATENCY_BUDGET'
            assert row['posts'] == 0 and row['uncertain_retries'] == 0
            assert market.ledger.committed() == 0
            assert market.ledger.instruction_slots() == 0
            assert ep.episode_id not in market.pending
            assert not executor.known
    finally:
        if setup:
            await close_setup(setup, market)
        else:
            await paper.close()
            store.close()


async def test_rounded_actual_cost_checked_against_same_account_admission(env, monkeypatch, tmp_path):
    setup, live = await live_setup(env, monkeypatch, tmp_path/'live', price='.19')
    paper, store, _ = virtual_setup(tmp_path/'virtual', '.19', live.settings)
    try:
        live.account['official_balance_usd'] = '1.003'
        original = paper.executor.account_snapshot

        async def account():
            row = await original()
            row['official_balance_usd'] = '1.003'
            return row

        monkeypatch.setattr(paper.executor, 'account_snapshot', account)
        await live.submit(next(iter(live.pending.values())))
        await cycle(paper)
        assert not setup[0].post_requests
        assert live.ledger.rows()[0]['reason'] == 'M1_ACCOUNT_BUDGET'
        assert paper.executor.ledger.rows()[0]['reason'] == 'M1_ACCOUNT_BUDGET'
        assert store.instructions(paper.feed.meta.slug)[0]['status'] == 'SKIPPED'
    finally:
        await paper.close()
        store.close()
        await close_setup(setup, live)


@pytest.mark.parametrize('direction_limit,accepted,reason', [
    (1, 1, 'M1_DIRECTION_OPPORTUNITY_LIMIT'), (5, 5, 'SESSION_INSTRUCTION_LIMIT')])
async def test_direction_and_outer_instruction_limits_are_shared(
    env, monkeypatch, tmp_path, direction_limit, accepted, reason,
):
    setup, live = await live_setup(env, monkeypatch, tmp_path/'live')
    live.settings = live.settings.model_copy(update={'max_opportunities_per_direction': direction_limit})
    paper, store, _ = virtual_setup(tmp_path/'virtual', settings=live.settings)
    try:
        for i in range(6):
            if i:
                seed_opportunity(live.feed)
                live.collect()
                seed_opportunity(paper.feed)
            key = next(reversed(live.pending))
            await live.submit(live.pending.pop(key))
            await cycle(paper)
        assert len(setup[0].post_requests) == accepted
        assert sum(o['status'] == 'VIRTUAL_SUCCESS' for o in store.instructions(paper.feed.meta.slug)) == accepted
        assert live.ledger.rows()[-1]['reason'] == reason
        assert paper.executor.ledger.rows()[-1]['reason'] == reason
    finally:
        await paper.close()
        store.close()
        await close_setup(setup, live)


async def test_default_direction_limit_allows_yes_and_no_in_both_modes(env, monkeypatch, tmp_path):
    setup, live = await live_setup(env, monkeypatch, tmp_path/'live')
    paper, store, _ = virtual_setup(tmp_path/'virtual', settings=live.settings)
    try:
        await live.submit(live.pending.pop(next(iter(live.pending))))
        await cycle(paper)
        for feed in (live.feed, paper.feed):
            feed.books['2'] = replace(feed.books['2'], bids=((D('.05'), D(100)),),
                asks=((D('.06'), D(100)),), reported_bid=None, reported_ask=None)
            now = feed.monotonic()
            state = feed.m1.state('2')
            state['anchor'] = D('.05')
            state['rf'] = {'id': feed.m1.new_id('RF'), 'confirmed': now, 'deadline': now+.7}
            state['ex'] = {'id': feed.m1.new_id('EX'), 'ap_id': 'reference-no',
                           'confirmed': now, 'deadline': now+.7, 'volume': D(60)}
        live.collect()
        await live.submit(live.pending.pop(next(iter(live.pending))))
        await cycle(paper)
        assert len(setup[0].post_requests) == 2
        assert {r['outcome'] for r in live.ledger.rows() if r['state'] == 'ACK'} == {'YES', 'NO'}
        assert {r['outcome'] for r in store.instructions(paper.feed.meta.slug)
                if r['status'] == 'VIRTUAL_SUCCESS'} == {'YES', 'NO'}
    finally:
        await paper.close()
        store.close()
        await close_setup(setup, live)


async def test_virtual_receipt_recovers_without_resubmitting_after_report_write_failure(monkeypatch, tmp_path):
    paper, store, _ = virtual_setup(tmp_path)
    original = store.issue

    def failed_write(*args, **kwargs):
        raise OSError('simulated report write failure after modeled POST')

    monkeypatch.setattr(store, 'issue', failed_write)
    with pytest.raises(OSError, match='report write failure'):
        await cycle(paper)
    assert len(paper.executor.ledger.facts('virtual_fill')) == 1
    paper.executor.close()  # A crash, not an orderly report flush.
    monkeypatch.setattr(store, 'issue', original)
    restored = VirtualPolicy(paper.feed, paper.settings, store, lambda *args: None)
    try:
        await cycle(restored)
        await cycle(restored)
        assert len(restored.executor.ledger.facts('virtual_fill')) == 1
        assert len(store.instructions(paper.feed.meta.slug)) == 1
        assert restored.executor.ledger.metric()['native_posts'] == 1
        assert restored.executor.ledger.committed() == 1
    finally:
        await restored.close()
        store.close()


def test_retry_rounding_increase_requires_additional_actual_reservation(tmp_path):
    ledger = M1Ledger(tmp_path/'risk.sqlite3', {'market': 'test'})
    try:
        prepared(ledger, 1, '.19')
        assert ledger.committed() == D('1.007')
        ledger.outcome('op1', 'REJECTED')
        assert ledger.retry('op1', proof={'kind': 'REJECTED_RESPONSE', 'reprice': True})
        assert not ledger.reserve_quote('op1', price='.18', quantity='5.6', spendable='.0005')
        assert ledger.committed() == D('1.007')
        assert ledger.reserve_quote('op1', price='.18', quantity='5.6', spendable='.001')
        assert ledger.committed() == D('1.008')
        quote = {'side': 'BUY', 'token_id': '1', 'price': '.18', 'size': '5.6'}
        ledger.prepare('op1', '0x'+'2'*64, quote)
        ledger.post_start('op1')
        ledger.outcome('op1', 'ACK')
        ledger.reconcile('op1', quantity='5.6', cost='1.008', closed=True)
        assert D(ledger.metric()['attainment']) == 1
    finally:
        ledger.close()
    recovered = M1Ledger(tmp_path/'risk.sqlite3', {'market': 'test'})
    assert recovered.committed() == D('1.008')
    recovered.close()


def test_rounded_cost_must_fit_gross_session_budget_before_signing(tmp_path):
    ledger = M1Ledger(tmp_path/'risk.sqlite3', {'market': 'test'}, limit=1)
    try:
        row = ledger.observe(opportunity(1, '.19'), minimum=5, elapsed=15)
        assert not ledger.reserve(row['id'], spendable=20)
        assert ledger.row(row['id'])['reason'] == 'M1_SESSION_BUDGET'
        assert ledger.committed() == 0
    finally:
        ledger.close()


def test_restart_does_not_accept_zeroed_unknown_reservation(tmp_path):
    ledger = M1Ledger(tmp_path/'risk.sqlite3', {'market': 'test'})
    prepared(ledger, 1)
    with ledger.db:
        ledger.db.execute("UPDATE opportunities SET reserved='0' WHERE id='op1'")
    ledger.close()
    with pytest.raises(ValueError, match='DURABLE_LEDGER_INVALID'):
        M1Ledger(tmp_path/'risk.sqlite3', {'market': 'test'})


async def test_preexisting_virtual_successes_keep_slots_after_switch_to_shared_engine(tmp_path):
    paper, store, _ = virtual_setup(tmp_path)
    # Historical receipts remain unchanged and consume their slots after restart.
    paper.executor.close()
    for i in range(5):
        store.issue({'slug': paper.feed.meta.slug, 'episode_id': f'old{i}',
                     'rf_id': f'old-rf{i}', 'ex_id': f'old-ex{i}', 'outcome': 'YES'},
                    price='.05', quantity='20', eligible=True, reason=None, elapsed=20)
    # The first constructor was before any historical receipts existed.
    # A fresh per-market shared ledger seeds the old receipts exactly once.
    path = store.directory/'prepost'/f'{paper.feed.meta.slug}.sqlite3'
    path.unlink()
    restored = VirtualPolicy(paper.feed, paper.settings, store, lambda *args: None)
    try:
        await cycle(restored)
        assert not restored.executor.ledger.facts('virtual_fill')
        assert restored.executor.ledger.rows()[0]['reason'] == 'SESSION_INSTRUCTION_LIMIT'
        assert restored.executor.ledger.instruction_slots() == 5
        assert restored.executor.ledger.committed() == 5
        assert sum(o['status'] == 'VIRTUAL_SUCCESS' for o in store.instructions(paper.feed.meta.slug)) == 5
    finally:
        await restored.close()
        store.close()


async def test_virtual_t300_records_local_completion_without_order_actions(tmp_path, monkeypatch):
    paper, store, _ = virtual_setup(tmp_path, '.05')
    try:
        await cycle(paper)
        assert len(store.instructions(paper.executor.slug)) == 1
        assert not hasattr(paper.executor, 'processor')
        await paper.market.drain()
        paper.market.finish_pending()
        async def forbidden(*args, **kwargs):
            pytest.fail('Virtual M1 performed a market-end order action')

        for name in ('cancel', 'cancel_detailed', 'cancel_all', 'get_order', 'send_prepared'):
            monkeypatch.setattr(paper.executor.gateway, name, forbidden)
        paper.market.complete_window()
        paper.flush()
        assert paper.executor.ledger.get('complete') is True
        assert paper.executor.ledger.get('remaining_positions') is None
        assert paper.executor.ledger.get('cancel_request_status') is None
        assert paper.executor.ledger.get('market_end_actions') == 'PLATFORM_MANAGED'
        assert paper.executor.ledger.get('settlement_policy') == 'PLATFORM_AUTO_SETTLEMENT'
        assert paper.executor.ledger.metric()['attainment'] is None
        assert paper.executor.ledger.facts('virtual_fill')
    finally:
        await paper.close()
        store.close()


async def test_virtual_lost_response_keeps_both_fills_and_one_signal_after_restart(monkeypatch, tmp_path):
    paper, store, clock = virtual_setup(tmp_path)
    native = paper.executor.gateway._client._native_order_post
    calls = []

    def lost_first(*args, **kwargs):
        result = native(*args, **kwargs)
        calls.append(result)
        return None if len(calls) == 1 else result

    monkeypatch.setattr(paper.executor.gateway._client, '_native_order_post', lost_first)
    try:
        await cycle(paper)
        assert paper.executor.ledger.committed() == 2
        clock.now += .11
        await cycle(paper)
        paper.flush()
        rows = store.instructions(paper.executor.slug)
        assert len(rows) == 2 and all(r['status'] == 'VIRTUAL_SUCCESS' for r in rows)
        assert paper.executor.ledger.metric()['signals'] == 1
        assert paper.executor.ledger.instruction_slots() == 1
        assert sum(D(r['cost']) for r in rows) == 2
        assert len(paper.executor.ledger.facts('virtual_fill')) == 2
        slug = paper.executor.slug
        await paper.close()
        restored = VirtualPolicy(paper.feed, paper.settings, store, lambda *args: None)
        try:
            await cycle(restored)
            assert restored.executor.ledger.committed() == 2
            assert restored.executor.ledger.get('historical_orders') == []
            assert len(store.instructions(slug)) == 2
            store.settle(slug, {'winner': 'YES'})
            assert sum(D(r['pnl']) for r in store.instructions(slug)) == 38
        finally:
            await restored.close()
    finally:
        store.close()


async def test_virtual_timeout_resend_then_crossing_reject_can_repost_best_bid(monkeypatch, tmp_path):
    from test_m1_live import retry_book

    paper, store, clock = virtual_setup(tmp_path)
    native = paper.executor.gateway._client._native_order_post
    sent = []

    def response(*args, **kwargs):
        attempt = paper.executor.gateway._client._operation_value('batch_attempts')[0]
        sent.append(D(str(attempt.quote.price)))
        if len(sent) == 2:
            return [{'success': False, 'errorMsg': 'invalid post-only order: order crosses book'}]
        result = native(*args, **kwargs)
        return None if len(sent) == 1 else result

    monkeypatch.setattr(paper.executor.gateway._client, '_native_order_post', response)
    try:
        await cycle(paper)
        retry_book(paper.market)
        clock.now += .11
        await cycle(paper)
        clock.now += .21
        # Refresh public book receipt age independently of the retry policy.
        from test_rdp_sm_m1_strategy import refresh
        refresh(paper.feed)
        await cycle(paper)
        assert sent == [D('.05'), D('.05'), D('.04')]
        rows = store.instructions(paper.executor.slug)
        assert len(rows) == 2 and sum(D(r['cost']) for r in rows) == 2
        assert paper.executor.ledger.metric()['signals'] == 1
        assert paper.executor.ledger.instruction_slots() == 1
        from polymaker.dashboard.m1 import virtual_session
        market = dict(store.db.execute('SELECT * FROM markets').fetchone())
        session = virtual_session(market, rows, [], {}, 'test', market['epoch']+15)
        assert session['signal_counts']['M1'] == 1
        assert session['virtual_submitted_orders'] == 2
        assert len(session['m1_signals']) == 1
    finally:
        await paper.close()
        store.close()
