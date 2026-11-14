"""M4 migration acceptance. Synthetic books, temporary ledgers, no network."""
# ruff: noqa: ASYNC240 -- All filesystem operations use isolated pytest paths.
import asyncio
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal as D

import httpx
import pytest
from m4_helpers import close, feed_fixture, fixture_feed, form, identity, ledger, op, send, setup

from polymaker.m4_config import M4Config, quantity_for_budget
from polymaker.m4_control import StopControl, atomic_json
from polymaker.m4_execution import M4Market
from polymaker.m4_frequency_signal import PARAMETERS
from polymaker.m4_run import paths, run
from polymaker.m4_signals import M4Feed
from polymaker.m4_state import M4Ledger


@pytest.mark.parametrize('price,quantity', [('.20','25.0'),('.21','23.8'),('.59','8.4'),
    ('.60','8.3'),('.63','7.9'),('.70','7.1'),('.99','5.0')])
def test_five_dollar_floor(price, quantity):
    q = quantity_for_budget(price, 5)
    assert q == D(quantity) and q*D(price) <= 5
    assert (q+D('.1'))*D(price) > 5
    with pytest.raises(ValueError):
        quantity_for_budget(price, q+D('.1'))


def test_frozen_contract():
    for override in ({'order_budget_usdc': 6}, {'session_budget_usdc': 11},
                     {'minimum_price': '.19'}, {'minimum_price': '.59'}, {'cutoff_sec': 241}, {'quantity_step': '.01'},
                     {'max_opportunities_per_direction': 2}, {'heartbeat_sec': float('nan')}):
        with pytest.raises(ValueError):
            M4Config(**override)


@pytest.mark.parametrize('state', ['ACK','UNKNOWN','REJECTED'])
def test_direction_survives_sent_rejection_and_restart(tmp_path, state):
    b = ledger(tmp_path)
    send(b, 1, state=state)
    before = b.committed()
    b.close()
    b = ledger(tmp_path)
    try:
        b.observe(op(2), minimum=5, elapsed=20)
        assert not b.reserve('df2', spendable=100)
        assert b.row('df2')['reason'] == 'M4_DIRECTION_OPPORTUNITY_LIMIT'
        b.observe(op(3, token='2'), minimum=5, elapsed=20)
        assert b.reserve('df3', spendable=100)
        assert before == (0 if state == 'REJECTED' else D('4.97'))
    finally:
        b.close()


def test_unsent_skip_abort_do_not_consume_direction_and_support_can_be_reused(tmp_path):
    b = ledger(tmp_path)
    try:
        for i in (1,2,3):
            b.observe(op(i), minimum=5, elapsed=20)
        assert not b.reserve('df1', spendable=0)
        assert b.reserve('df2', spendable=100)
        b.outcome('df2','ABORTED')
        assert b.reserve('df3', spendable=100)
        assert b.observe(op(3), minimum=5, elapsed=20) is None
    finally:
        b.close()


def test_atomic_direction_and_gross_cap(tmp_path):
    b = ledger(tmp_path)
    try:
        for i in range(30):
            b.observe(op(i, token='1' if i%2 else '2'), minimum=5, elapsed=20)
        with ThreadPoolExecutor(8) as pool:
            result = list(pool.map(lambda i: b.reserve(f'df{i}', spendable=100), range(30)))
        assert sum(result) == 2 and b.committed() == D('9.94')
    finally:
        b.close()


def test_unknown_retry_preserves_two_risks_and_only_one_extra_post(tmp_path):
    b = ledger(tmp_path)
    send(b, 1)
    assert b.retry('df1', proof={'kind':'UNCERTAIN_TIMEOUT'}, spendable=100)
    assert b.committed() == D('9.94')
    row = b.row('df1')
    b.prepare('df1', '0x'+'f'*64, {'side':'BUY','token_id':'1','price':'.70','size':row['quantity']})
    b.post_start('df1')
    b.close()
    b = ledger(tmp_path)
    try:
        assert not b.retry('df1', proof={'kind':'UNCERTAIN_RESPONSE'}, spendable=100)
        assert len(b.uncertain_posts()) == 1 and b.committed() == D('9.94')
        b.outcome('df1','REJECTED')
        assert b.committed() == D('4.97')  # Only the explicitly rejected attempt released.
        assert b.retry('df1', proof={'kind':'REJECTED_RESPONSE','reprice':True})
        assert b.committed() == D('9.94')
    finally:
        b.close()


def test_unknown_retry_denied_if_other_side_owns_remaining_budget(tmp_path):
    b = ledger(tmp_path)
    try:
        send(b, 1)
        send(b, 2, token='2', state='ACK')
        assert not b.retry('df1', proof={'kind':'UNCERTAIN_TIMEOUT'}, spendable=100)
        assert b.committed() == D('9.94') and not b.uncertain_posts()
    finally:
        b.close()


def test_repricing_requires_lower_price_at_both_reservation_and_signing(tmp_path):
    b = ledger(tmp_path)
    try:
        send(b, 1, state='REJECTED')
        assert b.retry('df1', proof={'kind':'REJECTED_RESPONSE','reprice':True})
        for price in ('.70','.71','.19'):
            with pytest.raises(ValueError):
                b.reserve_quote('df1',price=price,quantity=quantity_for_budget(price,5),spendable=100)
        assert b.reserve_quote('df1',price='.65',quantity=D('7.6'),spendable=100)
        with pytest.raises(ValueError):
            b.prepare('df1','0x'+'f'*64,{'side':'BUY','token_id':'1','price':'.70','size':'7.1'})
        b.prepare('df1','0x'+'f'*64,{'side':'BUY','token_id':'1','price':'.65','size':'7.6'})
    finally:
        b.close()












@pytest.mark.parametrize('live',[False,True])
async def test_real_signing_shared_pipeline_and_receipts_are_not_actual_fills(tmp_path,live):
    m,e,f,c = await setup(tmp_path,live=live)
    try:
        assert len(m.pending) == 1
        await m.submit(next(iter(m.pending.values())))
        row = m.ledger.rows()[0]
        assert row['state'] == 'ACK', row['reason']
        assert row['posts'] == 1 and m.ledger.committed() == D('4.97')
        assert m.ledger.metric()['fill_tracking'] == 'NOT_TRACKED'
        receipt = m.ledger.facts('virtual_fill')[0]
        assert receipt['model'] == 'FULL_FILL_AT_INSTRUCTION_PRICE'
        assert receipt['quote']['price'] == .70 and receipt['quote']['size'] == 7.1
        m.complete_window()
        assert m.ledger.committed() == D('4.97')
    finally:
        await close(m,e)


@pytest.mark.parametrize('fault',['timeout','abnormal'])
async def test_native_uncertain_response_has_one_original_price_retry(tmp_path,fault):
    m,e,f,c = await setup(tmp_path)
    calls=[]
    def native(*args,**kwargs):
        calls.append(e.gateway._client._operation_value('batch_attempts')[0].quote)
        if fault == 'timeout':
            raise httpx.ReadTimeout('synthetic timeout')
        return {'unexpected':'response'}
    e.gateway._client._native_order_post = native
    try:
        opportunity = next(iter(m.pending.values()))
        await m.submit(opportunity)
        await m.submit(opportunity)
        await m.submit(opportunity)
        assert len(calls) == 2
        assert all(q.price == .70 and q.size == 7.1 for q in calls)
        assert m.ledger.committed() == D('9.94')
        assert m.ledger.rows()[0]['uncertain_retries'] == 1
    finally:
        await close(m,e)


async def test_native_postonly_rejection_no_lower_bid_no_repost(tmp_path):
    m,e,f,c = await setup(tmp_path)
    calls=[]
    def native(*args,**kwargs):
        calls.append(e.gateway._client._operation_value('batch_attempts')[0].quote)
        return [{'success':False,'errorMsg':'post-only order crosses book'}]
    e.gateway._client._native_order_post = native
    try:
        opportunity = next(iter(m.pending.values()))
        await m.submit(opportunity)
        assert m.ledger.row(opportunity.episode_id)['posts'] == 1
        await m.submit(opportunity)
        assert len(calls) == 1 and m.quote_price(opportunity) is None
        f.books['1'] = replace(f.books['1'],bids=((D('.65'),D(100)),))
        await m.submit(opportunity)
        assert len(calls) == 2 and calls[1].price == .65 and calls[1].size == 7.6
        await m.submit(opportunity)
        assert len(calls) == 2  # Never fall back upward to the signal price.
        f.books['1'] = replace(f.books['1'],bids=((D('.19'),D(100)),))
        await m.submit(opportunity)
        assert len(calls) == 2
    finally:
        await close(m,e)


async def test_live_http_boundary_is_signed_postonly_and_same_economics(tmp_path,monkeypatch):
    import json

    from py_clob_client_v2.http_helpers import helpers

    from polymaker.rdp_sm_execution import ExecutionClobClient
    m,e,f,c = await setup(tmp_path,live=True)
    calls=[]
    def wire(request):
        assert request.method=='POST' and request.url.path=='/orders'
        rows=json.loads(request.content)
        calls.extend(rows)
        row=rows[0]
        assert row['postOnly'] is True and row['orderType']=='GTC'
        assert D(row['order']['makerAmount'])==4970000
        assert D(row['order']['takerAmount'])==7100000
        return httpx.Response(200,json=[{'success':True,'orderID':e.attempts[-1].expected_order_id}])
    client=httpx.Client(transport=httpx.MockTransport(wire))
    monkeypatch.setattr(helpers,'_http_client',client)
    e.gateway._client._native_order_post=ExecutionClobClient._native_order_post.__get__(e.gateway._client)
    try:
        await m.submit(next(iter(m.pending.values())))
        assert len(calls)==1 and m.ledger.rows()[0]['state']=='ACK'
        assert not m.ledger.facts('virtual_fill')  # ACK is never treated as a live fill.
    finally:
        await close(m,e)
        await asyncio.to_thread(client.close)


@pytest.mark.parametrize('error',['not enough balance','invalid signature'])
async def test_definite_non_postonly_reject_does_not_repost(tmp_path,error):
    m,e,f,c=await setup(tmp_path)
    def native(*args,**kwargs):
        return [{'success':False,'errorMsg':error}]
    e.gateway._client._native_order_post=native
    try:
        opportunity=next(iter(m.pending.values()))
        await m.submit(opportunity)
        await m.submit(opportunity)
        assert m.ledger.rows()[0]['posts']==1
        assert m.ledger.rows()[0]['state']=='REJECTED' and m.ledger.committed()==0
    finally:
        await close(m,e)


async def test_restart_preserves_unknown_and_rejected_retry_pricing(tmp_path):
    m,e,f,c=await setup(tmp_path)
    def reject(*args,**kwargs):
        return [{'success':False,'errorMsg':'post only'}]
    e.gateway._client._native_order_post=reject
    try:
        opportunity=next(iter(m.pending.values()))
        await m.submit(opportunity)
        m.stop.set()
        m.finish_pending()
        m.ledger.close()
        m.ledger=e.ledger=ledger(tmp_path)
        resumed=M4Market(e,m.settings,asyncio.Event(),display=lambda _:None)
        resumed.restore()
        assert opportunity.episode_id in resumed.pending
        assert resumed.quote_price(opportunity) is None
        assert resumed.ledger.row(opportunity.episode_id)['posts']==1
        f.books['1']=replace(f.books['1'],bids=((D('.65'),D(100)),))
        await resumed.refresh_account()
        await resumed.submit(resumed.pending[opportunity.episode_id])
        assert resumed.ledger.row(opportunity.episode_id)['posts']==2
        assert resumed.quote_price(opportunity) is None
    finally:
        await close(m,e)


@pytest.mark.parametrize('block',['T240','STOP','PRICE','STALE'])
async def test_final_native_guard_blocks_changes_during_prepare(tmp_path,block):
    m,e,f,c = await setup(tmp_path)
    prepare=e.gateway.prepare
    async def changed(*args,**kwargs):
        prepared=await prepare(*args,**kwargs)
        if block=='T240':
            e.official_time += 220
        elif block=='STOP':
            atomic_json(e.control.manual_path,{'reason':'USER_STOP'})
        elif block=='PRICE':
            f.books['1']=replace(f.books['1'],asks=((D('.69'),D(100)),))
        else:
            c.now += .3
        return prepared
    e.gateway.prepare=changed
    try:
        await m.submit(next(iter(m.pending.values())))
        assert m.ledger.rows()[0]['posts']==0
        assert not m.ledger.facts('virtual_fill')
    finally:
        await close(m,e)


def test_balance_latch_is_sticky_and_baseline_independent(tmp_path):
    ctl=StopControl(tmp_path,live=True)
    ctl.config_path.parent.mkdir(parents=True)
    ctl.config_path.write_text('enabled = true\nthreshold_usdc = "20"\n')
    assert not ctl.observe_balance('20')['requires_confirmation']
    assert ctl.observe_balance('19.999999')['requires_confirmation']
    before=ctl.balance_path.read_bytes()
    ctl.config_path.write_text('enabled = false\nthreshold_usdc = "1"\n')
    assert ctl.observe_balance('100')['requires_confirmation']
    assert ctl.inhibited() and ctl.balance_path.read_bytes()==before
    baseline=StopControl(tmp_path,live=False)
    assert not baseline.inhibited() and baseline.observe_balance('0') is None


def test_invalid_config_and_unavailable_balance(tmp_path):
    ctl=StopControl(tmp_path,live=True)
    with pytest.raises(ValueError):
        ctl.observe_balance('NaN')
    assert not ctl.balance_path.exists()
    r=ctl.observe_balance('21')
    assert r['configuration_status']=='INVALID_USING_DEFAULTS' and r['threshold_usdc']=='20'
    atomic_json(ctl.legacy_path,{'requires_confirmation':True})
    assert ctl.inhibited()


async def test_stopped_runner_is_offline_and_does_not_create_trading_state(tmp_path):
    (tmp_path/'config').mkdir()
    (tmp_path/'config/m4.toml').write_text('')
    ctl=StopControl(tmp_path,live=True)
    atomic_json(ctl.balance_path,{'requires_confirmation':True})
    assert (await run(tmp_path,live=True))['status']=='STOP_CONFIRMATION_REQUIRED'
    assert not (tmp_path/'research_outputs/m4_live').exists()


def test_retired_entrypoints_fail_before_legacy_runner(tmp_path,monkeypatch):
    from typer.testing import CliRunner

    from polymaker import m4_control
    from polymaker.cli import app
    guard=m4_control.legacy_entry_guard
    atomic_json(tmp_path/'runtime/trading-control/m1-retired.json',{'reason':'RETIRED'})
    monkeypatch.setattr(m4_control,'legacy_entry_guard',lambda:guard(tmp_path))
    for args in (['m1-run','--preflight-only'],['m1-live-run','--live']):
        result=CliRunner().invoke(app,args)
        assert result.exit_code!=0 and 'M1_RUNTIME_RETIRED' in str(result.exception)


def test_foreign_or_symlink_state_cannot_be_restored(tmp_path):
    import sqlite3
    p=tmp_path/'old.sqlite3'
    db=sqlite3.connect(p)
    db.execute('CREATE TABLE old_m1 (id TEXT)')
    db.close()
    before=p.read_bytes()
    with pytest.raises(ValueError,match='FOREIGN_DATABASE'):
        M4Ledger(p,identity())
    assert p.read_bytes()==before
    (tmp_path/'new.sqlite3').symlink_to(p)
    with pytest.raises(ValueError,match='SYMLINK'):
        M4Ledger(tmp_path/'new.sqlite3',identity())
    (tmp_path/'research_outputs').symlink_to(tmp_path)
    with pytest.raises(ValueError,match='SYMLINK'):
        paths(tmp_path,True)


def test_settings_and_mode_changes_do_not_reset_market(tmp_path):
    b=ledger(tmp_path)
    send(b,1,state='ACK')
    b.close()
    for ident in (identity('LIVE'),{**identity(),'settings_hash':'changed'}):
        with pytest.raises(ValueError,match='RECOVERY_IDENTITY'):
            M4Ledger(tmp_path/'risk.sqlite3',ident)
    b=ledger(tmp_path)
    assert b.rows()[0]['posts']==1 and b.committed()==D('4.97')
    b.close()


@pytest.mark.parametrize('old_minimum_loss', [None, '.20'])
def test_new_weak_ask_gate_does_not_relabel_or_reset_legacy_risk(tmp_path,old_minimum_loss):
    old_parameters={k:v for k,v in PARAMETERS.items() if k!='weak_min_loss_fraction'}
    if old_minimum_loss is not None:
        old_parameters['weak_min_loss_fraction']=old_minimum_loss
    old_hash=hashlib.sha256(json.dumps({
        'settings':M4Config().model_dump(mode='json'),'signal':old_parameters},
        sort_keys=True).encode()).hexdigest()
    assert old_hash!=M4Config().fingerprint
    old_identity={**identity(),'settings_hash':old_hash}
    path=tmp_path/'legacy.sqlite3'
    b=M4Ledger(path,old_identity)
    send(b,1,state='UNKNOWN')
    b.close()
    before=path.read_bytes()
    with pytest.raises(ValueError,match='RECOVERY_IDENTITY_OR_SETTINGS_CHANGED'):
        M4Ledger(path,identity())
    assert path.read_bytes()==before
    b=M4Ledger(path,old_identity)
    try:
        assert b.rows()[0]['posts']==1 and b.committed()==D('4.97')
        b.observe(op(2),minimum=5,elapsed=20)
        assert not b.reserve('df2',spendable=100)
        assert b.row('df2')['reason']=='M4_DIRECTION_OPPORTUNITY_LIMIT'
    finally:
        b.close()


async def test_baseline_settlement_uses_only_modeled_receipts_and_keeps_risk(tmp_path):
    from dataclasses import asdict

    from polymaker.m4_baseline import settle
    m,e,f,c=await setup(tmp_path)
    try:
        m.ledger.set('market',asdict(f.meta))
        await m.submit(next(iter(m.pending.values())))
        before=m.ledger.committed()
        result=settle(m.ledger.path,m.ledger.get('identity'),{'slug':f.meta.slug,
            'condition_id':f.meta.condition_id,'token_ids':{'YES':'1','NO':'2'},'winner':'YES'})
        assert result['modeled_pnl_usdc']=='2.13' and result['real_orders']==0
        assert m.ledger.committed()==before
        assert m.ledger.metric()['fill_tracking']=='NOT_TRACKED'
    finally:
        await close(m,e)


async def test_baseline_runner_bootstraps_isolated_root_without_credentials(tmp_path,monkeypatch):
    import time

    from polymaker import m4_run
    from polymaker.config import Config
    old,_,_=feed_fixture()
    (tmp_path/'config').mkdir()
    (tmp_path/'config/m4.toml').write_text('')
    class Public:
        async def clock(self):
            now=time.monotonic()
            return int(old.meta.slug.rsplit('-',1)[1])+1,now,now
        async def market(self,slug):
            assert slug==old.meta.slug
            return old.meta
        async def ticks(self,tokens):
            return {t:D('.01') for t in tokens}
        async def close(self):
            pass
    def private_forbidden(*args,**kwargs):
        raise AssertionError('Baseline loaded wallet credentials')
    async def no_socket(self):
        await asyncio.Event().wait()
    async def one_window(self):
        assert self.feed.m1.m4_only
        from polymaker.m4_fill_model import POLICY
        assert self.ledger.get('baseline_fill_model') == POLICY
        assert self.e.reads.fill_model is not None
        await self.refresh_account()
        assert self.account['source']=='MODELED_GROSS_BUDGET_10_USDC_PER_MARKET'
        self.complete_window()
        return True
    monkeypatch.setattr(m4_run,'VirtualPublicAPI',Public)
    monkeypatch.setattr(Config,'load',private_forbidden)
    monkeypatch.setattr(M4Feed,'run',no_socket)
    monkeypatch.setattr(M4Market,'run',one_window)
    assert (await run(tmp_path,once=True))['status']=='COMPLETE'
    assert len(list((tmp_path/'research_outputs/m4_virtual/ledgers').glob('*.sqlite3')))==1
    assert not (tmp_path/'research_outputs/m4_live').exists()


def test_frequency_reference_and_m4_only_detector():
    feed,clock=fixture_feed('.70')
    form(feed,clock)
    candidate=feed.m1.frequency.candidates['1']
    assert candidate['price']==D('.70') and candidate['confirmed']==pytest.approx(1.7)
    assert candidate['deadline']==pytest.approx(6.7)
    assert candidate['definition']=='m4-frequency-v2'
    assert feed.m1.m4_only and feed.ba is None and not hasattr(feed,'hub')
    feed.invalidate('TEST')
    assert not feed.m1.frequency.candidates
