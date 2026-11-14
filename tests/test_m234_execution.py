"""Money, identity, restart and the real SDK behind an offline transport."""

import asyncio
import time
from dataclasses import asdict
from decimal import Decimal as D
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from polymaker.m234_execution import LiveExecution
from polymaker.m234_ledger import LiveLedger
from test_rdp_sm_ba import env as env
from test_rdp_sm_entry import boundary
from test_rdp_sm_m1_strategy import m1_execution
from test_rdp_sm_obi_execution import cleanup

META = {'slug': 'btc-updown-5m-1793807400', 'condition_id': '0x'+'a'*64}


def candidate(strategy='M2', outcome='YES', price='.50'):
    return {'id': strategy+outcome, 'root': strategy+outcome, 'strategy': strategy,
        'outcome': outcome, 'token': '1' if outcome == 'YES' else '2', 'price': D(price),
        'confirmed': 10., 'deadline': 20., 'generation': 1}


@pytest.fixture
def ledger(tmp_path):
    book = LiveLedger(tmp_path/'live.sqlite3', 'test-owner')
    book.start_market(META, 'test')
    yield book
    book.close()


def prepared(book, strategy='M2', outcome='YES'):
    row = book.reserve(META['slug'], candidate(strategy, outcome), 5, 30)
    oid = '0x'+f'{len(book.rows()):064x}'
    book.prepare(row['id'], oid, {'token_id': row['token'], 'side': 'BUY',
        'price': row['price'], 'size': row['quantity']})
    book.post_start(row['id'])
    return book.row(row['id'])


def order(row, status='LIVE', matched='0'):
    return {'id': row['order_id'], 'asset_id': row['token'], 'market': META['condition_id'],
        'side': 'BUY', 'price': row['price'], 'original_size': row['quantity'],
        'size_matched': matched, 'status': status, 'associate_trades': []}


def trade(row, qty='2', status='CONFIRMED', identity='trade1'):
    return {'id': identity, 'status': status, 'market': META['condition_id'],
        'timestamp': 1793807400, 'maker_orders': [{'order_id': row['order_id'],
        'asset_id': row['token'], 'side': 'BUY', 'price': row['price'], 'matched_amount': qty}]}


def test_six_independent_slots_share_thirty_dollars_and_restart_limit(ledger):
    for strategy in ('M2', 'M3', 'M4'):
        for outcome in ('YES', 'NO'):
            row = ledger.reserve(META['slug'], candidate(strategy, outcome), 5, 30)
            assert row and D(row['cost']) == 5
    assert len(ledger.rows()) == 6 and ledger.reserved() == 30
    assert ledger.reserve(META['slug'], candidate(), 5, 1000) is None
    reopened = LiveLedger(ledger.path, 'test-owner')
    try:
        assert reopened.reserve(META['slug'], candidate(), 5, 1000) is None
    finally:
        reopened.close()


@pytest.mark.parametrize('price', ['.0001', '.01', '.19', '.5', '.99', '.9999'])
def test_quantity_floors_cost_and_minimum_does_not_expand_budget(ledger, price):
    row = ledger.reserve(META['slug'], candidate(price=price), 5, 5)
    assert 0 < D(row['cost']) <= 5
    assert D(row['quantity'])*D(price) == D(row['cost'])
    assert (D(row['quantity'])+D('.01'))*D(price) > 5


def test_unknown_retains_money_blocks_resend_and_other_entries(ledger):
    row = prepared(ledger)
    ledger.outcome(row['id'], 'UNKNOWN', 'lost_ack')
    assert ledger.reserved() == 5 and ledger.uncertain()
    assert ledger.reserve(META['slug'], candidate('M3'), 5, 100) is None
    with pytest.raises(ValueError, match='DUPLICATE_POST'):
        ledger.post_start(row['id'])
    with pytest.raises(ValueError, match='CANNOT_ABORT'):
        ledger.outcome(row['id'], 'ABORTED')


def test_partial_cancel_needs_confirmed_trade_proof_and_accepts_late_fill(ledger):
    row = prepared(ledger)
    assert not ledger.reconcile(row['id'], order(row, 'CANCELED', '2'), [trade(row, status='MATCHED')])
    assert ledger.reserved() == 5
    assert ledger.reconcile(row['id'], order(row, 'CANCELED', '2'), [trade(row)])
    assert ledger.reserved() == 0
    assert D(ledger.row(row['id'])['spent']) == 1
    assert ledger.reconcile(row['id'], order(row, 'CANCELED', '3'),
        [trade(row), trade(row, qty='1', identity='late')])
    assert D(ledger.row(row['id'])['filled']) == 3
    assert D(ledger.row(row['id'])['spent']) == D('1.5')


def test_cancel_ack_is_not_terminal_and_unknown_get_does_not_release(ledger):
    row = prepared(ledger)
    ledger.event('CANCEL_RESULT', {'order_id': row['order_id'], 'canceled': True})
    assert not ledger.row(row['id'])['terminal']
    assert ledger.reserved() == 5
    ledger.outcome(row['id'], 'RECONCILE_UNKNOWN', 'pagination')
    assert ledger.reserved() == 5 and ledger.uncertain()


async def test_canceled_prior_market_order_still_accepts_late_fill_and_invalidates_cash(ledger):
    row = prepared(ledger)
    assert ledger.reconcile(row['id'], order(row, 'CANCELED', '2'), [trade(row)])
    gateway = SimpleNamespace(_client=SimpleNamespace(known_ids=set(), pagination_failure=None),
        get_trades=AsyncMock(return_value=[trade(row), trade(row, qty='1', identity='late')]),
        get_order=AsyncMock(return_value=order(row, 'CANCELED', '3')))
    runtime = LiveExecution(SimpleNamespace(gateway=gateway),
        SimpleNamespace(meta=SimpleNamespace(slug='next-market')), ledger, None, asyncio.Event())
    runtime.account_at = time.monotonic()
    await runtime.reconcile()
    assert ledger.row(row['id'])['filled'] == '3.0'
    assert D(ledger.row(row['id'])['spent']) == D('1.5')
    assert runtime.account_barrier > runtime.account_at
    gateway.get_order.assert_awaited_once_with(row['order_id'])


async def test_balance_read_started_before_reconciliation_cannot_publish_cash(ledger, monkeypatch):
    gateway = SimpleNamespace(_client=SimpleNamespace(known_ids=set(), pagination_failure=None),
        open_orders=AsyncMock(return_value=[]))
    runtime = LiveExecution(SimpleNamespace(gateway=gateway),
        SimpleNamespace(meta=object()), ledger, None, asyncio.Event())
    async def stale(*args):
        runtime.account_barrier = time.monotonic()
        return {'official_balance_usd': '10', 'applicable_allowance_usd': '10'}
    monkeypatch.setattr('polymaker.m234_execution.account_snapshot', stale)
    await runtime.refresh_account()
    assert runtime.reason == 'ACCOUNT_READ_BEFORE_RECONCILIATION' and runtime.account is None
    fresh = AsyncMock(return_value={'official_balance_usd': '9', 'applicable_allowance_usd': '10'})
    monkeypatch.setattr('polymaker.m234_execution.account_snapshot', fresh)
    await runtime.refresh_account()
    assert runtime.reason is None and runtime.account_at >= runtime.account_barrier


@pytest.mark.parametrize('fault', ['token', 'market', 'side', 'size', 'overfill'])
def test_identity_and_economics_conflicts_cannot_close_risk(ledger, fault):
    row = prepared(ledger)
    raw = order(row, 'CANCELED')
    if fault == 'token':
        raw['asset_id'] = '2'
    elif fault == 'market':
        raw['market'] = 'other'
    elif fault == 'side':
        raw['side'] = 'SELL'
    elif fault == 'size':
        raw['original_size'] = '100'
    else:
        raw['size_matched'] = '11'
    with pytest.raises(ValueError):
        ledger.reconcile(row['id'], raw, [])
    assert ledger.reserved() == 5


@pytest.mark.parametrize('lose_ack', [False, True])
async def test_real_sdk_postonly_five_dollar_signed_order_and_no_unknown_resend(
        env, monkeypatch, tmp_path, lose_ack):
    setup = await m1_execution(env, monkeypatch)
    venue, executor, feed = setup[:3]
    book = LiveLedger(tmp_path/'sdk.sqlite3', 'sdk-test')
    book.start_market(asdict(feed.meta), 'test')
    runtime = LiveExecution(executor.reads, feed, book, lambda now: (20, 21), asyncio.Event())
    runtime.account = {'official_balance_usd': '100', 'applicable_allowance_usd': '100'}
    runtime.guard = lambda *a: True
    posts = []

    def wire(request):
        if request.method != 'POST':
            return None
        import json
        body = json.loads(request.content)
        row = book.rows()[0]
        assert row['started'] and row['state'] == 'SUBMITTING'
        assert body[0]['postOnly'] is True and body[0]['orderType'] == 'GTC'
        signed = body[0]['order']
        assert signed['side'] == 'BUY'
        assert D(str(signed['makerAmount'])) <= 5_000_000
        assert D(str(signed['takerAmount'])) == D(row['quantity'])*1_000_000
        posts.append(body)
        if lose_ack:
            raise httpx.ReadTimeout('lost ACK')
        return httpx.Response(200, json=[{'success': True, 'orderID': row['order_id']}], request=request)

    boundary(monkeypatch, venue, wire)
    c = candidate()
    try:
        await runtime.submit(c)
        assert len(posts) == 1
        assert book.rows()[0]['state'] == ('UNKNOWN' if lose_ack else 'ACK')
        await runtime.submit(c)
        assert len(posts) == 1
    finally:
        book.close()
        await cleanup(setup)
