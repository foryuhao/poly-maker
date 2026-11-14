"""Live-only Maker/FAK adapter: actual SDK signing, mock HTTP, temporary risk."""
import asyncio
import json
from dataclasses import asdict, replace
from decimal import Decimal as D
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from eth_account import Account
from m4_helpers import advance, close, fixture_feed, form, op
from py_clob_client_v2.clob_types import ApiCreds
from py_clob_client_v2.http_helpers import helpers

from polymaker.config import RDPSMConfig
from polymaker.m4_config import M4Config
from polymaker.m4_control import StopControl
from polymaker.m4_execution import M4Market
from polymaker.m4_journal import M4RiskJournal
from polymaker.m4_live_config import (
    M4LiveSettings,
    all_in_cost,
    gross_risk_cost,
    planned_fee,
    taker_quantity,
    validated_fees,
)
from polymaker.m4_live_http import M4LiveClobClient, M4LiveGateway
from polymaker.m4_live_taker import M4LiveLedger, M4TakerMarket
from polymaker.m4_run import Executor
from polymaker.rdp_sm_writer import AsyncRiskWriter

FEES = {'rate': '0.07', 'exponent': 1}


def settings(mode='taker'):
    return M4LiveSettings(execution_revision=('m4-taker-single-fak-v1' if mode == 'taker' else 'm4-maker-v1'),
                          execution={'mode': mode})


async def market(tmp_path, monkeypatch, *, mode='taker', response='ACK', price='.70', recover=False):
    feed, clock = fixture_feed(price)
    form(feed, clock)
    cfg = settings(mode)
    ident = dict(strategy='m4_frequency_v2_native_v1', mode='LIVE', funder='synthetic',
                 market_id='synthetic', settings_hash=cfg.fingerprint)
    book = M4LiveLedger(tmp_path/'risk.sqlite3', ident, settings=cfg, fees=FEES if mode == 'taker' else None)
    sid = book.get('session_id') or 'm4_live_'+uuid4().hex
    if not recover:
        book.initialize_session(sid, cfg.model_dump(mode='json'), asdict(feed.meta))
    gw = M4LiveGateway(SimpleNamespace(wallet=SimpleNamespace(data_api_host='https://data-api.polymarket.com'),
                                     execution=SimpleNamespace(rate_budget_fraction=1)))
    client = M4LiveClobClient('https://clob.polymarket.com', 137, key=Account.create().key.hex(),
                              creds=ApiCreds('offline', 'dmlydHVhbA==', 'offline'), retry_on_error=False)
    client.known_ids, client.observed_open_rows = set(), {}
    client._pagination_failures, client.m4_taker_ids = {}, {}
    client.sm_config = RDPSMConfig(normal_entry_mode='m4_v2')
    client.m4_execution, client.session_market_id = cfg.execution, feed.meta.condition_id
    client.get_version = lambda: 2
    client.get_tick_size = lambda token: str(feed.books[token].tick)
    client.m4_ledger = gw.m4_ledger = book
    gw._client = client
    reads = SimpleNamespace(gateway=gw, feed=feed, close=gw.close, network=SimpleNamespace(
        snapshot=lambda: SimpleNamespace(allowed=True, reason=None),
        recent_writes=lambda: SimpleNamespace(post=SimpleNamespace(max_ms=0))))
    writer = AsyncRiskWriter(M4RiskJournal(tmp_path/'journal', '0x'+'1'*40, session_id=sid, resume=recover))
    # Match the production runner: repeated recovery must continue journal numbering.
    writer.accepted = writer.written = writer.journal.last_writer_sequence
    e = Executor(None, feed.meta.slug, sid, reads, writer, ledger=book,
                 control=StopControl(tmp_path, live=True), live=True, monotonic=feed.monotonic)
    e.meta, e.official_time, e.time_anchor = feed.meta, int(feed.meta.slug.rsplit('-', 1)[1])+20, clock.now
    e.stop_event = asyncio.Event()
    async def cash():
        return {'official_balance_usd': '40', 'applicable_allowance_usd': '40', 'source': 'MOCK'}
    e.account_snapshot = cash
    m = (M4TakerMarket if mode == 'taker' else M4Market)(e, cfg, e.stop_event, display=lambda _: None)
    calls = []
    def wire(request):
        assert request.method == 'POST' and request.url.path == '/orders'
        rows = json.loads(request.content)
        assert len(rows) == 1
        calls.extend(rows)
        if response == 'TIMEOUT':
            raise httpx.ReadTimeout('synthetic lost response', request=request)
        if response == 'REJECTED':
            return httpx.Response(200, json=[{'success': False, 'errorMsg': 'not enough balance / allowance'}])
        oid = client._operation_value('attempt').expected_order_id
        if response == 'KILLED':
            return httpx.Response(200, json=[{'success': True, 'orderID': oid,
                'errorMsg': 'no orders found to match with FAK order. FAK orders are partially filled or killed if no match is found.',
                'status': '', 'makingAmount': '', 'takingAmount': '', 'tradeIDs': [], 'transactionsHashes': []}])
        return httpx.Response(200, json=[{'success': True, 'orderID': oid,
            **({'status': 'matched', 'makingAmount': '1', 'takingAmount': '1.4', 'tradeIDs': ['synthetic']}
               if response == 'PARTIAL' else {})}])
    transport = httpx.Client(transport=httpx.MockTransport(wire))
    monkeypatch.setattr(helpers, '_http_client', transport)
    await m.refresh_account()
    if recover:
        m.restore()
    else:
        m.collect()
    return m, e, feed, clock, calls, transport


def test_live_configuration_is_separate_and_legacy_fingerprint_unchanged(tmp_path):
    (tmp_path/'config/m4-live').mkdir(parents=True)
    (tmp_path/'config/m4.toml').write_text('minimum_price="0.20"\n')
    p = tmp_path/'config/m4-live/execution.toml'
    p.write_text('mode="taker"\n[taker]\norder_type="FAK"\nmax_anchor_slippage_ticks=1\n')
    assert M4LiveSettings.load(tmp_path).execution.mode == 'taker'
    assert M4Config.load(tmp_path).fingerprint == M4Config().fingerprint
    assert settings('maker').fingerprint == M4Config().fingerprint
    assert M4LiveSettings.model_validate(M4Config().model_dump()).fingerprint == M4Config().fingerprint
    for text in ('mode="TAKER"', 'mode="taker"\n[taker]\norder_type="FOK"',
                 'mode="taker"\n[taker]\nmax_anchor_slippage_ticks=2', 'mode="maker"\nunknown=1',
                 'mode="taker"\n[taker]\nmax_anchor_slippage_ticks=true'):
        p.write_text(text)
        with pytest.raises(ValueError):
            M4LiveSettings.load(tmp_path)


@pytest.mark.parametrize('price', ['.20', '.21', '.51', '.71', '.80', '.87', '.88', '.95', '.98', '.99', '.9999'])
def test_limit_price_planned_cost_step_minimum_and_separate_gross_bound(price):
    price = D(price)
    quantity = taker_quantity(price, 5, FEES)
    assert quantity >= 5 and quantity % D('.1') == 0
    assert all_in_cost(price, quantity, FEES) <= 5
    assert all_in_cost(price, quantity+D('.1'), FEES) > 5
    # The signed collateral can buy more shares at a better execution price.
    principal = price*quantity
    for actual in (D('.01'), D('.20'), price):
        if actual > price:
            continue
        actual_fee = principal*D('.07')*(1-actual)
        assert principal+actual_fee <= gross_risk_cost(price, quantity, FEES)


@pytest.mark.parametrize('price,quantity,fee,total', [
    ('.80', '1', '.01120', '.81120'),
    ('.88', '5', '.03696', '4.43696'),
    ('.95', '5', '.01663', '4.76663'),
    ('.99', '5', '.00347', '4.95347'),
])
def test_published_crypto_fee_formula_not_flat_seven_percent(price, quantity, fee, total):
    assert planned_fee(price, quantity, FEES) == D(fee)
    assert all_in_cost(price, quantity, FEES) == D(total)


def test_limit_price_estimate_is_not_an_actual_buy_debit_cap():
    price, quantity = D('.99'), taker_quantity('.99', 5, FEES)
    assert quantity == 5 and all_in_cost(price, quantity, FEES) <= 5
    principal = price*quantity
    actual_price = D('.50')
    actual_quantity = principal/actual_price
    actual_cost = principal+actual_quantity*D('.07')*actual_price*(1-actual_price)
    assert actual_cost == D('5.12325') > 5
    assert gross_risk_cost(price, quantity, FEES) >= actual_cost


@pytest.mark.parametrize('rate', ['0', '.04', '.07', '.9'])
@pytest.mark.parametrize('exponent', [1, 2, 5])
def test_quantity_grid_uses_market_fee_parameters_and_fee_precision(rate, exponent):
    fees = {'rate': rate, 'exponent': exponent}
    for i in range(20, 100):
        price = D(i)/100
        quantity = taker_quantity(price, 5, fees)
        cost = price*quantity+(quantity*D(rate)*(price*(1-price))**exponent).quantize(
            D('.00001'), rounding='ROUND_CEILING')
        assert cost <= 5 and quantity >= 5 and quantity % D('.1') == 0
        bigger = quantity+D('.1')
        assert price*bigger+(bigger*D(rate)*(price*(1-price))**exponent).quantize(
            D('.00001'), rounding='ROUND_CEILING') > 5


@pytest.mark.parametrize('result', ['ACK', 'PARTIAL', 'REJECTED', 'TIMEOUT', 'KILLED'])
async def test_fak_wire_single_post_and_restart_preserve_direction_and_risk(tmp_path, monkeypatch, result):
    m, e, f, c, calls, transport = await market(tmp_path, monkeypatch, response=result)
    key = next(iter(m.pending))
    try:
        await m.submit(m.pending[key])
        assert len(calls) == 1 and calls[0]['orderType'] == 'FAK' and calls[0]['postOnly'] is False
        row, plan = m.ledger.row(key), m.ledger.plan(key)
        assert D(calls[0]['order']['makerAmount']) == D(plan['principal_usdc'])*1_000_000
        assert D(calls[0]['order']['takerAmount']) == D(plan['quantity'])*1_000_000
        assert D(plan['limit_price']) == D('.71')
        assert row['posts'] == 1 and row['uncertain_retries'] == 0
        assert row['state'] == ('ACK' if result in {'ACK', 'PARTIAL'} else 'REJECTED' if result == 'REJECTED' else 'UNKNOWN')
        before = m.ledger.committed()
        assert before == (0 if result == 'REJECTED' else D(plan['commitment_usdc']))
        assert not m.ledger.facts('virtual_fill') and not m.ledger.facts('virtual_cancel')
        assert m.ledger.metric()['fill_tracking'] == 'NOT_TRACKED'
        for _ in range(3):
            await m.submit(m.pending[key])
        assert len(calls) == 1
    finally:
        await close(m, e)
        transport.close()
    m, e, f, c, calls, transport = await market(tmp_path, monkeypatch, recover=True)
    try:
        assert m.ledger.committed() == before and not m.pending
        m.ledger.observe(op(98), minimum=5, elapsed=20)
        m.ledger.plan_signal('df98', '.01', 5)
        assert not m.ledger.reserve('df98', spendable=100)
        assert m.ledger.row('df98')['reason'] == 'M4_DIRECTION_OPPORTUNITY_LIMIT'
        assert not calls
    finally:
        await close(m, e)
        transport.close()


async def test_signal_expiry_does_not_abort_accepted_intent_but_t240_does(tmp_path, monkeypatch):
    m, e, f, c, calls, transport = await market(tmp_path, monkeypatch)
    opportunity = next(iter(m.pending.values()))
    monkeypatch.setattr(f.m1, 'active_reason', lambda *a: (_ for _ in ()).throw(AssertionError('do not recheck expired signal')))
    advance(f, c, opportunity.order_deadline+1)
    await m.refresh_account()
    assert c.now > opportunity.order_deadline
    try:
        await m.submit(opportunity)
        assert len(calls) == 1 and m.ledger.row(opportunity.episode_id)['posts'] == 1
    finally:
        await close(m, e)
        transport.close()


async def test_live_maker_still_rejects_expired_initial_signal(tmp_path, monkeypatch):
    m, e, f, c, calls, transport = await market(tmp_path, monkeypatch, mode='maker')
    opportunity = next(iter(m.pending.values()))
    advance(f, c, opportunity.order_deadline+1)
    await m.refresh_account()
    try:
        await m.submit(opportunity)
        assert not calls and m.ledger.row(opportunity.episode_id)['posts'] == 0
    finally:
        await close(m, e)
        transport.close()


@pytest.mark.parametrize('boundary', ['t240', 'manual', 'balance', 'ask', 'tick'])
async def test_final_guard_blocks_unsent_order(tmp_path, monkeypatch, boundary):
    m, e, f, c, calls, transport = await market(tmp_path, monkeypatch)
    opportunity = next(iter(m.pending.values()))
    original = e.gateway.prepare
    async def prepare(*args, **kwargs):
        result = await original(*args, **kwargs)
        if boundary == 't240':
            e.official_time += 220
        elif boundary in {'manual', 'balance'}:
            e.control.root.mkdir(parents=True, exist_ok=True)
            (e.control.manual_path if boundary == 'manual' else e.control.balance_path).write_text('{}')
        elif boundary == 'ask':
            f.books['1'] = replace(f.books['1'], asks=((D('.72'), D(100)),))
        else:
            f.books['1'] = replace(f.books['1'], tick=D('.001'))
        return result
    monkeypatch.setattr(e.gateway, 'prepare', prepare)
    try:
        await m.submit(opportunity)
        assert not calls and m.ledger.row(opportunity.episode_id)['posts'] == 0
        if boundary in {'manual', 'balance'}:
            assert e.inhibited()
    finally:
        await close(m, e)
        transport.close()


async def test_unsubmitted_failure_can_reprepare_without_consuming_post(tmp_path, monkeypatch):
    m, e, f, c, calls, transport = await market(tmp_path, monkeypatch)
    opportunity = next(iter(m.pending.values()))
    original = e.gateway.prepare
    async def fail(*args, **kwargs):
        raise ValueError('synthetic signing failure before POST')
    monkeypatch.setattr(e.gateway, 'prepare', fail)
    try:
        await m.submit(opportunity)
        assert not calls and m.ledger.row(opportunity.episode_id)['posts'] == 0
        monkeypatch.setattr(e.gateway, 'prepare', original)
        await m.submit(opportunity)
        assert len(calls) == 1 and m.ledger.row(opportunity.episode_id)['posts'] == 1
    finally:
        await close(m, e)
        transport.close()


async def test_second_signal_cannot_exceed_market_gross_risk_even_if_both_plans_fit_ten(tmp_path, monkeypatch):
    m, e, f, c, calls, transport = await market(tmp_path, monkeypatch)
    opportunity = next(iter(m.pending.values()))
    second = op(102, token='2')
    try:
        m.ledger.observe(second, minimum=5, elapsed=20)
        # The fixture's other token uses a different tick; quote it legally.
        f.books['2'] = replace(f.books['2'], asks=((D('.701'), D(100)),))
        m.ledger.plan_signal(second.episode_id, f.books['2'].tick, 5)
        for candidate in (opportunity, second):
            await m.submit(candidate)
        plans = [m.ledger.plan(r['id']) for r in m.ledger.rows()]
        assert sum(D(p['planned_cost_usdc']) for p in plans) <= 10
        assert sum(D(p['commitment_usdc']) for p in plans) > 10
        assert len(calls) == 1 and 5 < m.ledger.committed() <= 10
        assert m.ledger.row(second.episode_id)['posts'] == 0
        assert m.ledger.row(second.episode_id)['reason'] == 'M4_SESSION_BUDGET'
        assert all(not m.ledger.retry(r['id'], proof={'kind': 'UNCERTAIN_TIMEOUT'}, spendable=100)
                   for r in m.ledger.rows())
    finally:
        await close(m, e)
        transport.close()


async def test_live_maker_wire_still_postonly_gtc(tmp_path, monkeypatch):
    m, e, f, c, calls, transport = await market(tmp_path, monkeypatch, mode='maker')
    try:
        await m.submit(next(iter(m.pending.values())))
        assert len(calls) == 1 and calls[0]['postOnly'] is True and calls[0]['orderType'] == 'GTC'
        assert m.ledger.committed() == D('4.97')
    finally:
        await close(m, e)
        transport.close()


async def test_no_private_order_trade_reads_or_cancel_even_if_known(tmp_path, monkeypatch):
    m, e, f, c, calls, transport = await market(tmp_path, monkeypatch)
    client = e.gateway._client
    try:
        for path in ('/data/orders', '/data/trades', '/data/order/known'):
            with pytest.raises(ValueError, match='FILL_READ_FORBIDDEN'):
                client._get('https://clob.polymarket.com'+path)
        with pytest.raises(RuntimeError, match='CANCEL_FORBIDDEN'):
            client._delete('https://clob.polymarket.com/orders', data='["known"]')
        assert not calls
    finally:
        await close(m, e)
        transport.close()


def test_public_fee_identity_is_required():
    assert validated_fees({'t': [{'t': '1'}, {'t': '2'}], 'fd': {'r': .07, 'e': 1, 'to': True}}, ('1', '2')) == FEES
    with pytest.raises(ValueError):
        validated_fees({'t': [{'t': 'wrong'}], 'fd': {'r': .07, 'e': 1, 'to': True}}, ('1', '2'))


async def test_restart_of_prepared_but_unsent_order_preserves_plan(tmp_path, monkeypatch):
    from polymaker.domain import Quote, Side
    from polymaker.rdp_sm_execution import WindowQuote
    m, e, f, c, calls, transport = await market(tmp_path, monkeypatch)
    key = next(iter(m.pending))
    plan = m.ledger.plan(key)
    try:
        assert m.ledger.reserve(key, spendable=100)
        assert m.ledger.reserve_quote(key, price=plan['limit_price'], quantity=plan['quantity'], spendable=100)
        quote = WindowQuote(f.meta.condition_id, '1', 'YES', Side.BUY,
                            float(plan['limit_price']), float(plan['quantity']), key)
        prepared = await e.gateway.prepare([Quote('1', Side.BUY, quote.price, quote.size)],
                                           f.meta, {t: float(b.tick) for t, b in f.books.items()})
        m.ledger.prepare(key, prepared.order_ids[0], asdict(quote))
        assert m.ledger.row(key)['posts'] == 0 and not calls
    finally:
        await close(m, e)
        transport.close()
    m, e, f, c, calls, transport = await market(tmp_path, monkeypatch, recover=True)
    try:
        assert m.ledger.plan(key) == plan and key in m.pending
        await m.submit(m.pending[key])
        assert len(calls) == 1 and m.ledger.row(key)['posts'] == 1
        assert len(m.ledger.facts('signed_attempt')) == 2
    finally:
        await close(m, e)
        transport.close()


@pytest.mark.parametrize('fault', ['unregistered', 'wrong_mode', 'wrong_id'])
async def test_native_fak_permission_cannot_be_bypassed(tmp_path, monkeypatch, fault):
    m, e, f, c, calls, transport = await market(tmp_path, monkeypatch)
    original = e.gateway.send_prepared
    if fault == 'unregistered':
        monkeypatch.setattr(e.gateway._client, 'm4_taker_permitted', lambda *args: False)
    elif fault == 'wrong_mode':
        e.gateway._client.sm_config = RDPSMConfig()
    else:
        async def send(prepared, **kwargs):
            kwargs['operation_context']['batch_attempts'][0].expected_order_id = '0x'+'ab'*32
            return await original(prepared, **kwargs)
        monkeypatch.setattr(e.gateway, 'send_prepared', send)
    try:
        await m.submit(next(iter(m.pending.values())))
        assert not calls and not any(r['posts'] for r in m.ledger.rows())
    finally:
        await close(m, e)
        transport.close()


async def test_market_fee_get_is_exactly_scoped_and_does_not_allow_trades(tmp_path, monkeypatch):
    m, e, f, c, calls, transport = await market(tmp_path, monkeypatch)
    client = e.gateway._client
    seen = []
    def wire(request):
        seen.append(str(request.url))
        assert request.method == 'GET' and request.url.path == '/clob-markets/'+f.meta.condition_id
        return httpx.Response(200, json={'t': [{'t': '1'}, {'t': '2'}], 'mts': '.01', 'nr': False,
                                        'fd': {'r': .07, 'e': 1, 'to': True}})
    http = httpx.Client(transport=httpx.MockTransport(wire))
    monkeypatch.setattr(helpers, '_http_client', http)
    try:
        raw = await e.gateway._io(client.get_clob_market_info, f.meta.condition_id)
        assert validated_fees(raw, ('1', '2')) == FEES and len(seen) == 1
        for url, params in [('https://wrong.example/clob-markets/'+f.meta.condition_id, None),
                            ('https://clob.polymarket.com/clob-markets/foreign', None),
                            ('https://clob.polymarket.com/clob-markets/'+f.meta.condition_id+'?extra=1', None),
                            ('https://clob.polymarket.com/clob-markets/'+f.meta.condition_id, {'extra': 1})]:
            with pytest.raises(ValueError, match='MARKET_INFO_SCOPE_INVALID'):
                client._get(url, params=params)
        assert len(seen) == 1 and not calls
    finally:
        await close(m, e)
        http.close()  # noqa: ASYNC212 -- Synchronous in-memory mock transport only.
        transport.close()


async def test_high_price_signal_can_post_with_limit_fee_budget(tmp_path, monkeypatch):
    m, e, f, c, calls, transport = await market(tmp_path, monkeypatch, price='.98')
    try:
        opportunity = next(iter(m.pending.values()))
        plan = m.ledger.plan(opportunity.episode_id)
        assert D(plan['quantity']) == 5 and D(plan['limit_price']) == D('.99')
        assert D(plan['planned_cost_usdc']) == D('4.95347')
        assert D(plan['commitment_usdc']) > 5
        await m.submit(opportunity)
        assert len(calls) == 1 and m.ledger.row(opportunity.episode_id)['posts'] == 1
        assert m.ledger.committed() == D(plan['commitment_usdc'])
        metric = m.ledger.metric()
        assert metric['actual_order_debit_may_exceed_plan'] is True
        assert metric['by_signal'][0]['planned_cost_usdc'] == '4.95347'
        assert metric['by_signal'][0]['gross_reserve_usdc'] == plan['commitment_usdc']
    finally:
        await close(m, e)
        transport.close()


def test_larger_venue_minimum_is_still_enforced():
    with pytest.raises(ValueError, match='MINIMUM_EXCEEDS_ALL_IN_BUDGET'):
        taker_quantity('.99', 6, FEES)


async def test_live_maker_keeps_its_original_one_uncertain_retry(tmp_path, monkeypatch):
    m, e, f, c, calls, transport = await market(tmp_path, monkeypatch, mode='maker', response='TIMEOUT')
    opportunity = next(iter(m.pending.values()))
    try:
        for _ in range(4):
            await m.submit(opportunity)
        assert len(calls) == 2
        assert all(r['postOnly'] is True and r['orderType'] == 'GTC' for r in calls)
        assert m.ledger.committed() == D('9.94') and len(m.ledger.uncertain_posts()) == 1
    finally:
        await close(m, e)
        transport.close()


@pytest.mark.parametrize('mode', ['maker', 'taker'])
async def test_live_stop_prevents_any_connection_even_after_mode_change(tmp_path, monkeypatch, mode):
    from polymaker.m4_live_http import M4LiveReads
    from polymaker.m4_run import run
    (tmp_path/'config/m4-live').mkdir(parents=True)
    (tmp_path/'config/m4.toml').write_text('minimum_price="0.20"\n')
    (tmp_path/'config/m4-live/execution.toml').write_text(f'mode="{mode}"\n')
    marker = tmp_path/'runtime/trading-control/balance-stop.json'
    marker.parent.mkdir(parents=True)
    marker.write_text('{"requires_confirmation":true}')
    async def forbidden(*args, **kwargs):
        raise AssertionError('stop marker must prevent connection')
    monkeypatch.setattr(M4LiveReads, 'connect_account', forbidden)
    assert await run(tmp_path, live=True) == {'status': 'STOP_CONFIRMATION_REQUIRED', 'live': True}
    assert marker.read_text() == '{"requires_confirmation":true}'
    assert not (tmp_path/'research_outputs').exists()
