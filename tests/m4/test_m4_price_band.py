"""Shared 0.20 price floor, with offline native submission and durable risk."""
from dataclasses import replace
from decimal import Decimal as D

import pytest
from m4_helpers import close, identity, ledger, op, send, setup

from polymaker.m4_config import M4Config, quantity_for_budget
from polymaker.m4_state import M4Ledger


@pytest.mark.parametrize('live',[False,True])
async def test_price_floor_admission_in_both_modes(tmp_path,live):
    m,e,f,c=await setup(tmp_path,live=live,price='.19')
    try:
        assert not m.pending and not m.ledger.rows()
    finally:
        await close(m,e)


@pytest.mark.parametrize('live',[False,True])
async def test_twenty_cent_unknown_retry_and_restart_keep_ten_dollars(tmp_path,live):
    m,e,f,c=await setup(tmp_path,live=live,price='.20')
    calls=[]
    def unknown(*args,**kwargs):
        calls.append(e.gateway._client._operation_value('batch_attempts')[0].quote)
        return {'unexpected':'response'}
    e.gateway._client._native_order_post=unknown
    try:
        opportunity=next(iter(m.pending.values()))
        for _ in range(3):
            await m.submit(opportunity)
        assert len(calls)==2
        assert all(q.price==.20 and q.size==25 for q in calls)
        assert m.ledger.committed()==10
        assert m.ledger.rows()[0]['uncertain_retries']==1
    finally:
        await close(m,e)
    b=ledger(tmp_path,'LIVE' if live else 'VIRTUAL')
    try:
        assert b.rows()[0]['posts']==2 and b.committed()==10
        assert len(b.uncertain_posts())==1
        assert not b.retry(opportunity.episode_id,proof={'kind':'UNCERTAIN_RESTART'},spendable=100)
        b.observe(op(2,price='.20',token='2'),minimum=5,elapsed=20)
        assert not b.reserve('df2',spendable=100)
    finally:
        b.close()


@pytest.mark.parametrize('live',[False,True])
async def test_rejected_order_can_reprice_to_twenty_cents_only(tmp_path,live):
    m,e,f,c=await setup(tmp_path,live=live,price='.21')
    calls=[]
    def rejected(*args,**kwargs):
        calls.append(e.gateway._client._operation_value('batch_attempts')[0].quote)
        return [{'success':False,'errorMsg':'post-only order crosses book'}]
    e.gateway._client._native_order_post=rejected
    try:
        opportunity=next(iter(m.pending.values()))
        await m.submit(opportunity)
        f.books['1']=replace(f.books['1'],bids=((D('.20'),D(100)),))
        await m.submit(opportunity)
        assert len(calls)==2 and calls[-1].price==.20 and calls[-1].size==25
        f.books['1']=replace(f.books['1'],bids=((D('.19'),D(100)),))
        await m.submit(opportunity)
        assert len(calls)==2
    finally:
        await close(m,e)


def test_old_market_keeps_sixty_cent_floor_on_restart(tmp_path):
    old=M4Config(minimum_price='.60')
    assert M4Config().minimum_price==D('.20')
    assert old.fingerprint!=M4Config().fingerprint
    ident={**identity(),'settings_hash':old.fingerprint}
    path=tmp_path/'old.sqlite3'
    b=M4Ledger(path,ident)
    b.set('settings',old.model_dump(mode='json'))
    send(b,1,state='REJECTED')
    assert b.retry('df1',proof={'kind':'REJECTED_RESPONSE','reprice':True})
    b.close()
    b=M4Ledger(path,ident)
    try:
        assert b.minimum_price==D('.60') and b.rows()[0]['posts']==1
        with pytest.raises(ValueError,match='PRICE_OUTSIDE_BAND'):
            b.observe(op(2,price='.20'),minimum=5,elapsed=20)
        with pytest.raises(ValueError,match='UNAUTHORIZED_REPRICE'):
            b.reserve_quote('df1',price='.59',quantity=quantity_for_budget('.59',5),spendable=100)
        with pytest.raises(ValueError,match='PREPARE_ECONOMICS_CHANGED'):
            b.prepare('df1','0x'+'f'*64,{'side':'BUY','token_id':'1','price':'.59','size':'8.4'})
        assert b.committed()==D('4.97')
    finally:
        b.close()
