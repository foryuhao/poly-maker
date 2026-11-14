"""Crash-boundary and rule acceptance for the second M4 review; offline only."""
# ruff: noqa: ASYNC240 -- All files are isolated temporary test state.
import asyncio
import json
import sqlite3
from contextlib import suppress
from dataclasses import asdict, replace
from decimal import Decimal as D
from uuid import uuid4

import pytest
from m4_helpers import close, feed_fixture, identity, ledger, send, setup

from polymaker.m4_baseline import pending, resolutions, settle
from polymaker.m4_config import STRATEGY, M4Config
from polymaker.m4_execution import M4Market
from polymaker.m4_frequency_signal import PARAMETERS
from polymaker.m4_run import VIRTUAL_IDENTITY, run
from polymaker.m4_signals import M4Feed
from polymaker.m4_state import M4Ledger


@pytest.mark.parametrize('field',['official_balance_usd','applicable_allowance_usd'])
async def test_reprice_must_recheck_fresh_capital(tmp_path,field):
    m,e,f,c=await setup(tmp_path)
    def native(*a,**k):
        return [{'success':False,'errorMsg':'post only'}]
    e.gateway._client._native_order_post=native
    try:
        ep=next(iter(m.pending.values()))
        await m.submit(ep)
        f.books['1']=replace(f.books['1'],bids=((D('.65'),D(100)),))
        m.account={**m.account,field:'4'}
        await m.submit(ep)
        assert m.ledger.row(ep.episode_id)['posts']==1
    finally:
        await close(m,e)


async def test_postonly_rejection_recovers_if_crash_precedes_retry_authorization(tmp_path):
    class Crash(BaseException):
        pass
    m,e,f,c=await setup(tmp_path)
    e.gateway._client._native_order_post=lambda *a,**k:[{'success':False,'errorMsg':'post only'}]
    def crash(*a,**k):
        raise Crash()
    m.queue_retry=crash
    try:
        ep=next(iter(m.pending.values()))
        with pytest.raises(Crash):
            await m.submit(ep)
        assert m.ledger.row(ep.episode_id)['state']=='REJECTED'
        m.ledger.close()
        m.ledger=e.ledger=ledger(tmp_path)
        resumed=M4Market(e,m.settings,asyncio.Event(),display=lambda _:None)
        resumed.restore()
        assert ep.episode_id in resumed.pending
        assert resumed.quote_price(ep) is None
        assert resumed.ledger.row(ep.episode_id)['posts']==1
    finally:
        await close(m,e)


async def test_authorized_retry_aborted_before_native_post_survives_restart(tmp_path):
    m,e,f,c=await setup(tmp_path)
    e.gateway._client._native_order_post=lambda *a,**k: {'unexpected':'response'}
    try:
        ep=next(iter(m.pending.values()))
        await m.submit(ep)
        assert m.ledger.row(ep.episode_id)['uncertain_retries']==1
        m.ledger.outcome(ep.episode_id,'ABORTED','RESTART_UNSENT_RETRY_PREPARATION')
        m.ledger.close()
        m.ledger=e.ledger=ledger(tmp_path)
        resumed=M4Market(e,m.settings,asyncio.Event(),display=lambda _:None)
        resumed.restore()
        assert ep.episode_id in resumed.pending
        assert resumed.ledger.committed()==D('9.94')
        assert resumed.ledger.row(ep.episode_id)['uncertain_retries']==1
        await resumed.refresh_account()
        await resumed.submit(resumed.pending[ep.episode_id])
        assert resumed.ledger.row(ep.episode_id)['posts']==2
        m.ledger.close()
        m.ledger=e.ledger=ledger(tmp_path)
        again=M4Market(e,m.settings,asyncio.Event(),display=lambda _:None)
        again.restore()
        assert not again.pending and m.ledger.committed()==D('9.94')
    finally:
        await close(m,e)


async def test_modeled_settlement_fact_and_marker_are_atomic(tmp_path):
    m,e,f,c=await setup(tmp_path)
    try:
        m.ledger.set('market',asdict(f.meta))
        await m.submit(next(iter(m.pending.values())))
        proof={'slug':f.meta.slug,'condition_id':f.meta.condition_id,
               'token_ids':{'YES':'1','NO':'2'},'winner':'YES','observed_at':1}
        m.ledger.db.executescript("""CREATE TRIGGER crash_settlement BEFORE INSERT ON metadata
            WHEN NEW.key='modeled_settlement' BEGIN SELECT RAISE(ABORT,'synthetic crash'); END;""")
        with pytest.raises(sqlite3.IntegrityError):
            settle(m.ledger.path,m.ledger.get('identity'),proof)
        assert not m.ledger.facts('modeled_settlement')
        m.ledger.db.execute('DROP TRIGGER crash_settlement')
        r=settle(m.ledger.path,m.ledger.get('identity'),{**proof,'observed_at':2})
        assert r['modeled_pnl_usdc']=='2.13'
        assert m.ledger.committed()==D('4.97')
    finally:
        await close(m,e)


async def test_completed_twenty_percent_baseline_can_settle_under_five_percent_code(tmp_path,monkeypatch):
    old,_,_=feed_fixture()
    path=tmp_path/'ledgers'/f'{old.meta.slug}.sqlite3'
    with monkeypatch.context() as context:
        context.setitem(PARAMETERS,'weak_min_loss_fraction','.20')
        ident={**identity(),'market_id':old.meta.condition_id}
    assert ident['settings_hash']!=M4Config().fingerprint
    b=M4Ledger(path,ident)
    b.initialize_session('old-profile',M4Config().model_dump(mode='json'),asdict(old.meta))
    send(b,1,state='ACK')
    b.fact('virtual_fill','synthetic',{'quote':{'price':'.70','size':'7.1','outcome':'YES'},
                                     'model':'FULL_FILL_AT_INSTRUCTION_PRICE'})
    b.set('complete',True)
    b.close()
    epoch=int(old.meta.slug.rsplit('-',1)[1])
    [(saved_path,saved_identity,market)]=pending(tmp_path,epoch+301)
    assert saved_identity['settings_hash']==ident['settings_hash']
    result=settle(saved_path,saved_identity,{'slug':market['slug'],
        'condition_id':market['condition_id'],'token_ids':{'YES':'1','NO':'2'},'winner':'YES'})
    assert D(result['modeled_pnl_usdc'])==D('2.13')
    b=M4Ledger(path,ident)
    try:
        assert b.get('identity')['settings_hash']==ident['settings_hash']
        assert b.committed()==D('4.97') and b.row('df1')['posts']==1
    finally:
        b.close()


async def test_expired_incomplete_baseline_market_is_recovered(tmp_path):
    old,_,_=feed_fixture()
    root=tmp_path/'m4_virtual'
    path=root/'ledgers'/f'{old.meta.slug}.sqlite3'
    ident={**identity(), 'market_id':old.meta.condition_id}
    b=M4Ledger(path,ident)
    b.set('market',asdict(old.meta))
    b.set('settings',M4Config().model_dump(mode='json'))
    send(b,1,state='ACK')
    b.fact('virtual_fill','synthetic',{'quote':{'price':'.70','size':'7.1','outcome':'YES'},
                                     'model':'FULL_FILL_AT_INSTRUCTION_PRICE'})
    b.close()
    done,stop=asyncio.Event(),asyncio.Event()
    class Public:
        async def clock(self):
            return int(old.meta.slug.rsplit('-',1)[1])+310,0,0
        async def resolution(self,expected):
            done.set()
            return {**expected,'winner':'YES'}
    task=asyncio.create_task(resolutions(root,Public(),stop))
    try:
        await asyncio.wait_for(done.wait(),timeout=1)
    finally:
        stop.set()
        await task
    b=M4Ledger(path,ident)
    try:
        assert b.get('complete') and b.get('modeled_settlement')
        assert b.committed()==D('4.97')
    finally:
        b.close()


async def test_restart_uses_stored_market_settings(tmp_path,monkeypatch):
    import time

    from polymaker import m4_run
    old,_,_=feed_fixture()
    (tmp_path/'config').mkdir()
    (tmp_path/'config/m4.toml').write_text('heartbeat_sec=20\n')
    original=M4Config(minimum_price='.60')
    ident=dict(strategy=STRATEGY,mode='VIRTUAL',funder=VIRTUAL_IDENTITY,
               market_id=old.meta.condition_id,settings_hash=original.fingerprint)
    b=M4Ledger(tmp_path/'research_outputs/m4_virtual/ledgers'/f'{old.meta.slug}.sqlite3',ident)
    b.set('settings',original.model_dump(mode='json'))
    b.set('market',asdict(old.meta))
    b.set('session_id','m4_virtual_'+uuid4().hex)
    send(b,1,state='ACK')
    b.close()
    class Public:
        async def clock(self):
            return int(old.meta.slug.rsplit('-',1)[1])+20,time.monotonic(),time.monotonic()
        async def market(self,slug):
            return old.meta
        async def ticks(self,tokens):
            return {t:D('.01') for t in tokens}
        async def close(self):
            pass
    async def no_socket(self):
        await asyncio.Event().wait()
    async def check(self):
        assert self.settings.heartbeat_sec==10
        assert self.settings.minimum_price==D('.60')
        assert self.ledger.minimum_price==D('.60')
        assert self.ledger.rows()[0]['posts']==1 and self.ledger.committed()==D('4.97')
        return False
    monkeypatch.setattr(m4_run,'VirtualPublicAPI',Public)
    monkeypatch.setattr(M4Feed,'run',no_socket)
    monkeypatch.setattr(M4Market,'run',check)
    assert (await run(tmp_path,once=True))['status']=='RETAINED'


def test_wrong_mode_does_not_change_foreign_database_journal(tmp_path):
    b=ledger(tmp_path)
    b.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    b.db.execute('PRAGMA journal_mode=DELETE')
    b.close()
    with pytest.raises(ValueError,match='RECOVERY_IDENTITY'):
        M4Ledger(tmp_path/'risk.sqlite3',identity('LIVE'))
    with sqlite3.connect(tmp_path/'risk.sqlite3') as db:
        assert db.execute('PRAGMA journal_mode').fetchone()[0]=='delete'


async def test_signal_journal_failure_during_selection_is_fail_closed(tmp_path):
    m,e,f,c=await setup(tmp_path)
    try:
        f.m1.used_roots.clear()
        f.m1.opportunities.clear()
        f.m1.record=lambda row: False
        f.m1.evaluate(f.snapshot())
        assert f.m1.failure=='M4_EVENT_RECORDING_FAILED'
        assert f.m1.selected is None
    finally:
        await close(m,e)


async def test_tick_change_requires_fresh_public_confirmation_and_can_recover(tmp_path):
    m,e,f,c=await setup(tmp_path)
    m.pending.clear()
    calls=[]
    class Public:
        async def ticks(self,tokens):
            calls.append(tuple(tokens))
            return {t:D('.001') for t in tokens}
    m.public_api=Public()
    try:
        f.books['1']=replace(f.books['1'],tick=D('.001'))
        assert not f.m1.legal(f.books['1'],c.now)
        await m.advance()
        await m.drain()
        assert calls==[('1',)]
        assert f.m1.legal(f.books['1'],c.now)
        assert not m.ledger.metric()['native_posts']
    finally:
        await close(m,e)


async def test_capital_drop_after_signing_blocks_native_post(tmp_path):
    m,e,f,c=await setup(tmp_path)
    prepare=e.gateway.prepare
    async def changed(*args,**kwargs):
        prepared=await prepare(*args,**kwargs)
        m.account={**m.account,'applicable_allowance_usd':'4'}
        return prepared
    e.gateway.prepare=changed
    try:
        await m.submit(next(iter(m.pending.values())))
        assert m.ledger.rows()[0]['posts']==0
        assert not m.ledger.facts('virtual_fill')
    finally:
        await close(m,e)


async def test_lower_quote_exact_capital_is_sufficient(tmp_path):
    m,e,f,c=await setup(tmp_path)
    e.gateway._client._native_order_post=lambda *a,**k:[{'success':False,'errorMsg':'post only'}]
    try:
        ep=next(iter(m.pending.values()))
        await m.submit(ep)
        f.books['1']=replace(f.books['1'],bids=((D('.65'),D(100)),))
        m.account={**m.account,'applicable_allowance_usd':'4.94'}
        await m.submit(ep)
        assert m.ledger.row(ep.episode_id)['posts']==2
    finally:
        await close(m,e)


def test_partial_settlement_recovers_original_fact_and_rejects_conflict(tmp_path):
    b=ledger(tmp_path)
    result={'resolution':{'winner':'YES','observed_at':1},'mode':'VIRTUAL','modeled_pnl_usdc':'0'}
    try:
        b.fact('modeled_settlement','synthetic',result)
        repeat={**result,'resolution':{'winner':'YES','observed_at':2}}
        assert b.modeled_settlement('synthetic',repeat)==result
        assert b.get('modeled_settlement')==result
        with pytest.raises(ValueError,match='FACT_CONFLICT'):
            b.modeled_settlement('synthetic',{**result,'resolution':{'winner':'NO'}})
    finally:
        b.close()


def test_session_metadata_initialization_is_atomic(tmp_path):
    b=ledger(tmp_path)
    try:
        b.db.executescript("""CREATE TRIGGER stop_init BEFORE INSERT ON metadata
            WHEN NEW.key='settings' BEGIN SELECT RAISE(ABORT,'synthetic crash'); END;""")
        with pytest.raises(sqlite3.IntegrityError):
            b.initialize_session('sid',{}, {})
        assert b.get('session_id') is None and b.get('market') is None
    finally:
        b.close()


def test_recovery_skips_active_and_unexpired_baseline_and_preserves_unknown(tmp_path):
    old,_,_=feed_fixture()
    path=tmp_path/'ledgers'/f'{old.meta.slug}.sqlite3'
    ident={**identity(), 'market_id':old.meta.condition_id}
    b=M4Ledger(path,ident)
    b.initialize_session('synthetic',M4Config().model_dump(mode='json'),asdict(old.meta))
    send(b,1,state='UNKNOWN')
    b.close()
    epoch=int(old.meta.slug.rsplit('-',1)[1])
    assert not pending(tmp_path,epoch+299)
    assert not pending(tmp_path,epoch+301,(path,))
    assert len(pending(tmp_path,epoch+301))==1
    b=M4Ledger(path,ident)
    try:
        assert b.get('complete') and b.committed()==D('4.97')
        assert b.row('df1')['state']=='UNKNOWN' and b.row('df1')['posts']==1
    finally:
        b.close()


async def test_durable_ack_is_not_downgraded_by_later_logging_failure(tmp_path):
    m,e,f,c=await setup(tmp_path)
    record=e.record
    def fail(kind,*args,**kwargs):
        if kind=='sm_m4_live_post':
            e.fatal='M4_JOURNAL_WRITE_FAILED'
            raise OSError('synthetic logging failure after durable ACK')
        return record(kind,*args,**kwargs)
    e.record=fail
    try:
        ep=next(iter(m.pending.values()))
        with suppress(OSError):
            await m.submit(ep)
        assert m.ledger.row(ep.episode_id)['state']=='ACK'
        assert m.ledger.row(ep.episode_id)['uncertain_retries']==0
        assert m.ledger.committed()==D('4.97')
    finally:
        await close(m,e)


@pytest.mark.parametrize('block',['T240','STOP'])
async def test_rejected_recovery_obeys_stop_and_cutoff(tmp_path,block):
    m,e,f,c=await setup(tmp_path)
    try:
        ep=next(iter(m.pending.values()))
        await m.submit(ep)
        # Synthetic durable rejection and proof; no account state is queried.
        m.ledger.db.execute("UPDATE opportunities SET state='REJECTED',failure_proof=? WHERE id=?",
            (json.dumps({'kind':'REJECTED_RESPONSE','reprice':True}),ep.episode_id))
        m.ledger.db.commit()
        if block=='T240':
            e.official_time+=220
        else:
            e.control.manual_path.parent.mkdir(parents=True,exist_ok=True)
            e.control.manual_path.write_text('{}')
        restored=M4Market(e,m.settings,asyncio.Event(),display=lambda _:None)
        restored.restore()
        assert not restored.pending and m.ledger.rows()[0]['posts']==1
    finally:
        await close(m,e)


async def test_stale_public_tick_cannot_override_ws_tick(tmp_path):
    m,e,f,c=await setup(tmp_path)
    m.pending.clear()
    class Public:
        async def ticks(self,tokens):
            return {t:D('.01') for t in tokens}
    m.public_api=Public()
    try:
        f.books['1']=replace(f.books['1'],tick=D('.001'))
        await m.advance()
        await m.drain()
        assert f.books['1'].tick==D('.001')
        assert not f.m1.legal(f.books['1'],c.now)
        assert not m.ledger.metric()['native_posts']
    finally:
        await close(m,e)


async def test_committed_model_result_reexports_after_crash_without_public_requery(tmp_path,monkeypatch):
    from polymaker import m4_baseline
    old,_,_=feed_fixture()
    path=tmp_path/'ledgers'/f'{old.meta.slug}.sqlite3'
    ident={**identity(), 'market_id':old.meta.condition_id}
    b=M4Ledger(path,ident)
    result={'resolution':{'winner':'YES','observed_at':1},'mode':'VIRTUAL','modeled_pnl_usdc':'0'}
    b.initialize_session('synthetic',M4Config().model_dump(mode='json'),asdict(old.meta))
    b.set('complete',True)
    b.modeled_settlement(old.meta.slug,result)
    b.close()
    stopped=asyncio.Event()
    write=m4_baseline.atomic_json
    def exported(target,value):
        write(target,value)
        stopped.set()
    monkeypatch.setattr(m4_baseline,'atomic_json',exported)
    class Public:
        async def clock(self):
            return int(old.meta.slug.rsplit('-',1)[1])+301,0,0
        async def resolution(self,*args):
            raise AssertionError('already settled: export durable result only')
    await asyncio.wait_for(resolutions(tmp_path,Public(),stopped),1)
    target=tmp_path/'modeled-settlements'/f'{old.meta.slug}.json'
    assert json.loads(target.read_text())==result


@pytest.mark.parametrize('price',['.20','.21','.59','.60','.67','.71','.98'])
async def test_sdk_wire_rounding_preserves_five_dollar_ceiling(tmp_path,monkeypatch,price):
    import httpx
    from py_clob_client_v2.http_helpers import helpers

    from polymaker.rdp_sm_execution import ExecutionClobClient
    m,e,f,c=await setup(tmp_path,live=True,price=price)
    calls=[]
    def wire(request):
        assert request.method=='POST' and request.url.path=='/orders'
        submitted=json.loads(request.content)[0]
        calls.append(submitted)
        amount=D(submitted['order']['makerAmount'])/1000000
        quantity=D(submitted['order']['takerAmount'])/1000000
        assert amount==D(price)*quantity<=5
        assert quantity%D('.1')==0 and (quantity+D('.1'))*D(price)>5
        assert submitted['postOnly'] and submitted['orderType']=='GTC'
        return httpx.Response(200,json=[{'success':True,'orderID':e.attempts[-1].expected_order_id}])
    client=httpx.Client(transport=httpx.MockTransport(wire))
    monkeypatch.setattr(helpers,'_http_client',client)
    e.gateway._client._native_order_post=ExecutionClobClient._native_order_post.__get__(e.gateway._client)
    try:
        await m.submit(next(iter(m.pending.values())))
        assert len(calls)==1 and m.ledger.rows()[0]['state']=='ACK'
        assert not m.ledger.facts('virtual_fill')
    finally:
        await close(m,e)
        await asyncio.to_thread(client.close)
