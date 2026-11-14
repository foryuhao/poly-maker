"""Duplicate scheduling, retry sequences and hard-crash durability. Offline only."""
# ruff: noqa: ASYNC240 -- Synthetic state lives exclusively under tmp_path.
import asyncio
import multiprocessing
import os
from dataclasses import replace
from decimal import Decimal as D

import pytest
from m4_helpers import close, identity, ledger, op, send, setup

from polymaker.m4_execution import M4Market
from polymaker.m4_state import M4Ledger


async def reopen(m,e):
    await m.drain()
    await e.journal.flush()
    path,ident=m.ledger.path,m.ledger.get('identity')
    m.ledger.close()
    e.ledger=M4Ledger(path,{k:v for k,v in ident.items() if k not in {'limit','order_budget','prepost_policy'}})
    # Discard process-local placement knowledge: durable state must suffice.
    e.known.clear()
    e.attempts.clear()
    e.gateway._client.known_ids.clear()
    resumed=M4Market(e,m.settings,asyncio.Event(),display=lambda _:None)
    resumed.restore()
    await resumed.refresh_account()
    return resumed


@pytest.mark.parametrize('live',[False,True])
@pytest.mark.parametrize('outcome',['ACK','UNKNOWN'])
async def test_duplicate_scheduling_and_three_restarts_do_not_add_posts(tmp_path,live,outcome):
    m,e,f,c=await setup(tmp_path,live=live)
    calls=[]
    def native(*args,**kwargs):
        attempt=e.gateway._client._operation_value('batch_attempts')[0]
        calls.append(attempt.expected_order_id)
        return ([{'success':True,'orderID':attempt.expected_order_id}] if outcome=='ACK' else {'unexpected':'response'})
    e.gateway._client._native_order_post=native
    try:
        ep=next(iter(m.pending.values()))
        await asyncio.gather(*(m.submit(ep) for _ in range(20)))
        for _ in range(3):
            m=await reopen(m,e)
            await asyncio.gather(*(m.submit(ep) for _ in range(10)))
        assert len(calls)==(1 if outcome=='ACK' else 2)
        assert len(set(calls))==len(calls)
        assert m.ledger.committed()==(D('4.97') if outcome=='ACK' else D('9.94'))
        assert m.ledger.rows()[0]['uncertain_retries']==(0 if outcome=='ACK' else 1)
        assert not m.pending
    finally:
        await close(m,e)


@pytest.mark.parametrize('live',[False,True])
@pytest.mark.parametrize('boundary',['STOP','T240'])
async def test_recovered_uncertain_retry_never_posts_after_stop_or_cutoff(tmp_path,live,boundary):
    m,e,f,c=await setup(tmp_path,live=live)
    calls=[]
    def native(*args,**kwargs):
        calls.append(1)
        return {'unexpected':'response'}
    e.gateway._client._native_order_post=native
    try:
        ep=next(iter(m.pending.values()))
        await m.submit(ep)
        assert m.ledger.committed()==D('9.94')
        if boundary=='T240':
            e.official_time+=220
        else:
            e.control.manual_path.parent.mkdir(parents=True,exist_ok=True)
            e.control.manual_path.write_text('{}')
        m=await reopen(m,e)
        await m.submit(ep)
        assert calls==[1] and not m.pending
        assert len(m.ledger.uncertain_posts())==1
        assert m.ledger.committed()==D('4.97')
        assert m.ledger.row(ep.episode_id)['posts']==1
    finally:
        await close(m,e)


@pytest.mark.parametrize('live',[False,True])
async def test_opposite_direction_blocks_extra_post_across_restarts(tmp_path,live):
    m,e,f,c=await setup(tmp_path,live=live)
    calls=[]
    def native(*args,**kwargs):
        calls.append(1)
        return {'unexpected':'response'}
    e.gateway._client._native_order_post=native
    try:
        ep=next(iter(m.pending.values()))
        send(m.ledger,99,token='2',state='ACK')
        await m.submit(ep)
        for _ in range(3):
            m=await reopen(m,e)
            await m.submit(ep)
            assert m.ledger.committed()==D('9.94')
        assert len(calls)==1 and m.ledger.row(ep.episode_id)['uncertain_retries']==0
        assert sum(r['posts'] for r in m.ledger.rows())==2
    finally:
        await close(m,e)


@pytest.mark.parametrize('live',[False,True])
async def test_unknown_then_reject_then_lower_bid_never_releases_first_risk(tmp_path,live):
    m,e,f,c=await setup(tmp_path,live=live)
    calls=[]
    def native(*args,**kwargs):
        attempt=e.gateway._client._operation_value('batch_attempts')[0]
        calls.append(attempt.quote.price)
        if len(calls)==2:
            return [{'success':False,'errorMsg':'post only'}]
        return {'unexpected':'response'}
    e.gateway._client._native_order_post=native
    try:
        ep=next(iter(m.pending.values()))
        await m.submit(ep)
        m=await reopen(m,e)
        await m.submit(ep)
        m=await reopen(m,e)
        f.books['1']=replace(f.books['1'],bids=((D('.65'),D(100)),))
        await m.submit(ep)
        m=await reopen(m,e)
        await m.submit(ep)
        assert calls==[.70,.70,.65]
        assert m.ledger.committed()==D('9.91')
        assert len(m.ledger.uncertain_posts())==1
        assert m.ledger.row(ep.episode_id)['uncertain_retries']==1
        m.ledger.observe(op(2),minimum=5,elapsed=20)
        assert not m.ledger.reserve('df2',spendable=100)
    finally:
        await close(m,e)


def crash_transaction(path,mode,operation,point):
    book=M4Ledger(path,identity(mode))
    def trace(sql):
        match = (
            point=='before_commit' and sql=='COMMIT'
            or point=='after_archive' and sql.startswith('UPDATE opportunities')
            or point=='after_update' and sql.startswith('INSERT INTO events')
        )
        if match:
            os._exit(73)  # No close(), Python exception rollback or WAL checkpoint.
    book.db.set_trace_callback(trace)
    if operation=='retry':
        assert book.retry('df1',proof={'kind':'UNCERTAIN_TIMEOUT'},spendable=100)
    else:
        book.post_start('df1')
    os._exit(73 if point=='after_commit' else 74)


@pytest.mark.parametrize('mode',['LIVE','VIRTUAL'])
@pytest.mark.parametrize('point',['after_archive','after_update','before_commit','after_commit'])
def test_hard_crash_retry_is_atomic_and_original_risk_survives(tmp_path,mode,point):
    book=ledger(tmp_path,mode)
    send(book,1)
    path=book.path
    book.close()
    process=multiprocessing.get_context('fork').Process(target=crash_transaction,args=(path,mode,'retry',point))
    process.start()
    process.join(10)
    if process.is_alive():
        process.kill()
        process.join()
        pytest.fail('synthetic child hung')
    assert process.exitcode==73
    book=ledger(tmp_path,mode)
    try:
        committed=point=='after_commit'
        assert book.committed()==(D('9.94') if committed else D('4.97'))
        assert len(book.uncertain_posts())==int(committed)
        assert book.row('df1')['uncertain_retries']==int(committed)
        assert book.row('df1')['posts']==1
        if not committed:
            assert book.retry('df1',proof={'kind':'UNCERTAIN_RESTART'},spendable=100)
        assert book.committed()==D('9.94')
        book.prepare('df1','0x'+'2'*64,{'token_id':'1','side':'BUY','price':'.70','size':'7.1'})
        book.post_start('df1')
        assert not book.retry('df1',proof={'kind':'UNCERTAIN_RESTART'},spendable=100)
        assert book.committed()==D('9.94') and book.row('df1')['posts']==2
    finally:
        book.close()


@pytest.mark.parametrize('mode',['LIVE','VIRTUAL'])
@pytest.mark.parametrize('point',['before_commit','after_commit'])
def test_hard_crash_post_intent_retains_possible_post_and_reservation(tmp_path,mode,point):
    book=ledger(tmp_path,mode)
    book.observe(op(1),minimum=5,elapsed=20)
    assert book.reserve('df1',spendable=100)
    book.prepare('df1','0x'+'1'*64,{'token_id':'1','side':'BUY','price':'.70','size':'7.1'})
    path=book.path
    book.close()
    process=multiprocessing.get_context('fork').Process(target=crash_transaction,args=(path,mode,'post',point))
    process.start()
    process.join(10)
    if process.is_alive():
        process.kill()
        process.join()
        pytest.fail('synthetic child hung')
    assert process.exitcode==73
    book=ledger(tmp_path,mode)
    try:
        assert book.committed()==D('4.97')
        assert book.row('df1')['posts']==int(point=='after_commit')
        if point=='after_commit':
            with pytest.raises(ValueError,match='DUPLICATE_POST'):
                book.post_start('df1')
    finally:
        book.close()
