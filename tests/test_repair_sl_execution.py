"""SL concurrency through real signing/gateway/tracker, fake venue I/O only."""
import asyncio
import threading
import time
from dataclasses import replace
from decimal import Decimal
from itertools import product
from types import SimpleNamespace

import httpx
import pytest

from polymaker.config import Config
from polymaker.domain import Side, TradeState
from polymaker.rdp_sm import session_capital_limit
from polymaker.rdp_sm_execution import Placement, WindowQuote
from polymaker.rdp_sm_network import NetworkHealth
from polymaker.rdp_sm_sl_ledger import Exposure, exposure, fifo_residual, install_reverse_episode
from polymaker.state.tracker import TradeEvent
from test_rdp_sm_cached_reads import attach, finish
from test_rdp_sm_entry import boundary
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_obi_execution import cleanup
from test_rdp_sm_path_repair import env as env
from test_rdp_sm_path_repair import setup_policy
from test_rdp_sm_repair import reflect_positions

D = Decimal


async def until(predicate, seconds=4):
    async with asyncio.timeout(seconds):
        while not predicate():  # noqa: ASYNC110 - observe independent SDK/cache tasks
            await asyncio.sleep(.005)


def force_sl(controller):
    controller._policy().force_sl(controller.elapsed(), "TEST_SL")
    controller._persist()


def test_budget20_is_scoped_to_the_path_strategy():
    cfg = Config()
    assert session_capital_limit(cfg) == 15
    cfg.rdp_sm.normal_entry_mode = "obi_v1"
    assert session_capital_limit(cfg) == 15
    cfg.rdp_sm.repair.policy = "hd_tp_sl_v1"
    assert session_capital_limit(cfg) == 20


def test_only_own_sl_batch_continues_and_real_network_fault_still_pauses_it():
    cfg=Config().rdp_sm.obi
    network=NetworkHealth(cfg)
    for _ in range(10):
        network.observe('GET',.001)
    assert network.begin_batch('sl-chain')
    assert not network.snapshot().allowed
    assert network.continuation('sl-chain').allowed
    assert not network.continuation('other-chain').allowed
    network.observe('POST',.01,False,batch_id='sl-chain')
    assert not network.continuation('sl-chain').allowed
    assert network.continuation('sl-chain').state=='PAUSED'


@pytest.mark.parametrize("actual,uy,un,side,q,allowed", [
    ("5", "0", "5", "NO", "5", True),
    ("5", "0", "10", "NO", "5", False),
    ("3", "0", "5", "NO", "5", False),
    ("-5", "0", "0", "YES", "5", True),
    ("5", "0", "6.67", "NO", "6.67", False),
])
def test_every_partial_fill_combination_is_inside_the_reserved_interval(actual, uy, un, side, q, allowed):
    actual, uy, un, q = map(D, (actual, uy, un, q))
    state = Exposure(actual, actual, uy, un, D('1.5'), un*D('.5'))
    assert state.allows(side, q, D('.5'), D(20)) is allowed
    if allowed:
        for old_yes, old_no, new in product((D(0), D('.5'), D(1)), repeat=3):
            end = actual+old_yes*uy-old_no*un+new*q*(1 if side == "YES" else -1)
            assert abs(end) <= 5


def test_budget_counts_paid_and_every_possible_old_order_once():
    s = Exposure(D(5), D(5), D(0), D(5), D('13'), D('4.5'))
    assert not s.allows('NO', D(5), D('.6'), D(20))
    assert s.allows('NO', D(5), D('.5'), D(20))


def fill(token, qty, price, tid, oid):
    return TradeEvent(token, Side.BUY, price, qty, tid, TradeState.CONFIRMED, 0, oid, "market")


def test_fifo_residual_retains_actual_lot_cost_and_conserves_total_pnl():
    fills = [fill('Y', 5, .30, 'open', 'a'), fill('N', 5, .65, 'tp', 'b'),
             fill('N', 5, .71, 'sl', 'c')]
    stamps = {(f.trade_id, f.maker_order_id): {'elapsed': i} for i, f in enumerate(fills)}
    residual = fifo_residual(list(reversed(fills)), stamps, 'Y')
    assert [(f.token_id, f.size, f.price) for f in residual] == [('N', 5., .71)]
    assert D('5')*(1-D('.30')-D('.65'))+D('5')*(D('.70')-D('.71')) == D('.20')


def test_matched_offsets_execution_but_never_frees_cash_or_delta():
    opening = fill('Y', 5, .30, 'a', 'opening')
    pending = replace(fill('N', 3, .60, 'b', 'repair'), status=TradeState.MATCHED)
    a = Placement('x', WindowQuote('market', 'N', 'NO', Side.BUY, .60, 5., 'w'),
                  outcome='ACK', order_id='repair', post_started=True)
    e = SimpleNamespace(economic_fills=lambda: [opening], economic_inventory=lambda: (D(5),D(0)),
        processor=SimpleNamespace(economic_fills_for_orders=lambda _: [pending]), known={'repair'},
        meta=SimpleNamespace(yes=SimpleNamespace(token_id='Y')), attempts=[a], proofs={}, terminal={})
    s = exposure(e)
    assert s.actual == 5 and s.execution == 2 and s.interval == (D(0), D(5))
    assert s.spent == D('1.5') and s.reserved == D(3)
    e.sl_terminal_quantities = {'repair': D(3)}
    s = exposure(e)
    assert s.reserved == D('1.8') and s.interval == (D(2),D(5))
    e.economic_fills = lambda: [opening, replace(pending, status=TradeState.CONFIRMED)]
    e.economic_inventory = lambda: (D(5),D(3))
    s = exposure(e)
    assert s.actual == s.execution == 2 and s.reserved == 0
    assert s.spent == D('3.3')


@pytest.mark.parametrize('reverse_qty,created', [('2.5',False),('2.500001',True),('5',True)])
def test_reverse_threshold_and_fifo_inputs_do_not_reset_session(reverse_qty, created):
    q = D(reverse_qty)
    fills = [fill('Y',5,.30,'a','open'), fill('N',5,.65,'b','tp'),
             fill('N',float(q),.71,'c','sl')]
    records=[]
    ep = {'opening_window':'open-w', 'side':'YES', 'windows':['open-w','tp-w','sl-w']}
    attempts=[SimpleNamespace(order_id=f.maker_order_id,quote=SimpleNamespace(window_id=w))
              for f,w in zip(fills, ep['windows'], strict=True)]
    e = SimpleNamespace(repair_episode=ep, economic_inventory=lambda: (D(5),D(5)+q),
        economic_fills=lambda: fills, attempts=attempts, meta=SimpleNamespace(yes=SimpleNamespace(token_id='Y')),
        fill_observed_at={(f.trade_id,f.maker_order_id): {'elapsed':100+i,'source':'VENUE_TRADE_TIME'}
                          for i,f in enumerate(fills)}, order_post_elapsed={}, time_anchor=0,
        slug='market-1000', official_time=1105, record=lambda *args:records.append(args))
    assert install_reverse_episode(e) is created
    if created:
        assert e.repair_episode['side'] == 'NO'
        assert D(e.repair_episode['original_qty']) == q
        assert D(e.repair_episode['original_cost']) == q*D('.71')
        assert e.repair_episode['first_fill_elapsed'] == 102
        assert 'policy_state' not in e.repair_episode  # ordinary policy defaults HD
        assert e.repair_episode['chain_windows'] == ep['windows']


def test_reverse_fifo_includes_proven_dust_from_previous_normal_loop():
    dust=fill('Y',.09,.25,'dust','old')
    opening=fill('N',5,.70,'open','open')
    repair=fill('Y',7.5,.14,'repair','repair')
    fills=[dust,opening,repair]
    stamps={(f.trade_id,f.maker_order_id):{'elapsed':10+i*10,'source':'VENUE_TRADE_TIME'}
            for i,f in enumerate(fills)}
    e=SimpleNamespace(repair_episode={'opening_window':'open-w','side':'NO','windows':['open-w','repair-w']},
        economic_inventory=lambda:(D('7.59'),D(5)),economic_fills=lambda:fills,
        fill_observed_at=stamps,meta=SimpleNamespace(yes=SimpleNamespace(token_id='Y')),
        order_post_elapsed={},time_anchor=0,slug='market-1000',official_time=1035,record=lambda *args:None)
    assert install_reverse_episode(e)
    assert D(e.repair_episode['original_qty'])==D('2.59')
    assert D(e.repair_episode['original_cost'])==D('2.59')*D('.14')


async def test_sl_replacement_posts_while_old_cancel_is_still_waiting(env, monkeypatch):
    setup, controller = await setup_policy(env, monkeypatch)
    v,e,_,_,_,wire,_,pump,_ = setup
    force_sl(controller)
    new_post = threading.Event()
    canceled_waited = []
    post_times=[]

    def writes(request):
        if request.method == 'POST':
            post_times.append(time.monotonic())
            raw=wire(request)
            if len(v.post_requests)==3:
                new_post.set()
            return raw
        if request.method == 'DELETE' and len(v.post_requests)>=2:
            canceled_waited.append(new_post.wait(.4))
        return wire(request)

    client=boundary(monkeypatch,v,writes)
    def filled(venue,oid):
        if len(v.post_requests)>=3 and oid==e.attempts[-1].expected_order_id and not v.rows[oid]['associate_trades']:
            v.fill(oid,'replacement-fill',5)
            reflect_positions(v)
    v.on_order_read=filled

    async def change():
        await until(lambda:len(v.post_requests)==2)
        pump.update(bid='.40',yes_ask='.42',no_bid='.58',no_ask='.60')

    task=asyncio.create_task(change())
    try:
        result,_=await asyncio.wait_for(controller.repair(*e.economic_inventory(),'concurrent'),5)
        await task
        assert result.close_barrier=='SAFE_TO_CONTINUE',result.block_reason
        assert new_post.is_set() and canceled_waited and all(canceled_waited)
        assert e.economic_inventory()==(D(5),D(5)) and len(v.post_requests)==3
        assert e.attempts[-1].quote.price==.599
    finally:
        await asyncio.gather(task,return_exceptions=True)
        await cleanup(setup)
        client.close()


async def test_sl_double_fill_returns_fifo_reverse_inventory_to_ordinary_hold(env, monkeypatch):
    setup,controller=await setup_policy(env,monkeypatch)
    v,e,feed,_,_,wire,_,pump,_=setup
    force_sl(controller)
    old=[]

    def writes(request):
        raw=wire(request)
        if request.method=='POST' and len(v.post_requests)==2:
            old.append(e.attempts[-1].expected_order_id)
        elif request.method=='POST' and len(v.post_requests)==3:
            # Both were already matched when cancellation was being processed.
            v.fill(old[0],'old-sl-late',5)
            v.fill(e.attempts[-1].expected_order_id,'new-sl-fill',5)
            reflect_positions(v)
        return raw

    client=boundary(monkeypatch,v,writes)
    async def change():
        await until(lambda:len(v.post_requests)==2)
        pump.update(bid='.40',yes_ask='.42',no_bid='.58',no_ask='.60')
    task=asyncio.create_task(change())
    try:
        result,_=await asyncio.wait_for(controller.repair(*e.economic_inventory(),'double-fill'),5)
        await task
        assert result.close_barrier=='SAFE_TO_CONTINUE',result.block_reason
        assert e.economic_inventory()==(D(5),D(10))
        assert e.repair_episode['side']=='NO' and D(e.repair_episode['original_qty'])==5
        assert D(e.repair_episode['original_cost'])==D('2.995')
        assert controller._policy().mode=='HD'
        decision,point=controller.observe(feed.snapshot(repair=True))
        assert point.pnl_per_share==pytest.approx(-.009)
        assert decision['mode']=='HD'
        assert len(v.post_requests)==3  # no special immediate reverse SL
    finally:
        await asyncio.gather(task,return_exceptions=True)
        await cleanup(setup)
        client.close()


async def test_cached_sl_uses_background_details_and_can_finish_without_foreground_gets(env, monkeypatch,tmp_path):
    setup,controller=await setup_policy(env,monkeypatch)
    v,e=setup[:2]
    official,anchor=e.official_time,e.time_anchor
    def clock(request):
        if request.url.path=='/time':
            return httpx.Response(200,json=official+time.monotonic()-anchor)
    companion,calls=await attach(setup,tmp_path,hook=clock)
    force_sl(controller)
    def filled(venue,oid):
        if venue.rows[oid]['asset_id']=='2' and not venue.rows[oid]['associate_trades']:
            t=venue.fill(oid,'cached-sl',5)
            t['trader_side']='MAKER'
            t['maker_orders'][0]['maker_address']=v.funder
            reflect_positions(v)
    v.on_order_read=filled
    try:
        result,_=await asyncio.wait_for(controller.repair(*e.economic_inventory(),'cached-sl'),5)
        assert result.close_barrier=='SAFE_TO_CONTINUE',result.block_reason
        assert e.economic_inventory()==(D(5),D(5))
        assert all(method=='GET' for method,_,_ in calls)
        records=journal_records(e.journal.path)
        assert any(r['kind']=='sm_sl_order_release' for r in records)
        assert not e.shared_reads.inflight
    finally:
        await finish(setup,companion)


@pytest.mark.parametrize('from_tp',[False,True])
async def test_new_sl_does_not_wait_for_old_post_ack_and_recancels_late_acceptance(env,monkeypatch,from_tp):
    setup,controller=await setup_policy(env,monkeypatch)
    v,e,feed,_,_,wire,_,pump,_=setup
    if from_tp:
        start=int(e.slug.rsplit('-',1)[1])
        official,anchor=start+179.8,time.monotonic()
        e.official_time,e.time_anchor=official,anchor
        v.time=official
        feed.connect()
        async def clock():
            e.reads.network.observe('GET',.001,endpoint='/time')
            return official+time.monotonic()-anchor
        monkeypatch.setattr(e.reads,'server_time',clock)
        await asyncio.sleep(.025)
    else:
        force_sl(controller)
    replacement=threading.Event()
    held=[]
    old=[]
    def delay():
        a=e.gateway._client.batch_attempts[0]
        if not old:
            old.append(a.expected_order_id)
            held.append(replacement.wait(.45))
        else:
            replacement.set()
    wire.delay=delay
    def filled(venue,oid):
        if old and oid!=old[0] and venue.rows[oid]['asset_id']=='2' and not venue.rows[oid]['associate_trades']:
            venue.fill(oid,'fill-before-old-ack',5)
            reflect_positions(venue)
    v.on_order_read=filled
    async def change():
        await until(lambda:len(v.post_requests)==2)
        if not from_tp:
            pump.update(bid='.40',yes_ask='.42',no_bid='.58',no_ask='.60')
    task=asyncio.create_task(change())
    try:
        result,_=await asyncio.wait_for(controller.repair(*e.economic_inventory(),'unacked-old'),5)
        await task
        assert held==[True] and replacement.is_set()
        assert result.close_barrier=='SAFE_TO_CONTINUE',result.block_reason
        assert len(v.post_requests)==3
        assert v.rows[old[0]]['status']=='CANCELED'
        assert e.economic_inventory()==(D(5),D(5))
        assert any(old[0] in ids for ids in v.cancels)
    finally:
        replacement.set()
        await asyncio.gather(task,return_exceptions=True)
        await cleanup(setup)


async def test_matched_three_cancels_remainder_without_buying_again(env,monkeypatch):
    setup,controller=await setup_policy(env,monkeypatch)
    v,e=setup[:2]
    force_sl(controller)
    def partial(venue,oid):
        row=venue.rows[oid]
        if row['asset_id']=='2' and not row['associate_trades']:
            venue.fill(oid,'matched-three',3,status='MATCHED')
            reflect_positions(venue)
    v.on_order_read=partial
    try:
        result,_=await asyncio.wait_for(controller.repair(*e.economic_inventory(),'matched-three'),5)
        assert result.close_barrier=='SAFE_TO_CONTINUE',result.block_reason
        assert len(v.post_requests)==2
        assert e.economic_inventory()==(D(5),D(3))
        assert e.position_reconciled  # proof by actual positions; no timeout promotion
        assert all(f.status is not TradeState.CONFIRMED for f in e.economic_fills() if f.token_id=='2')
        assert exposure(e).execution==2
    finally:
        await cleanup(setup)


async def test_t240_cancels_a_post_accepted_after_the_cutoff(env,monkeypatch):
    setup,controller=await setup_policy(env,monkeypatch)
    v,e,feed,_,_,wire,*_=setup
    start=int(e.slug.rsplit('-',1)[1])
    official,anchor=start+239.8,time.monotonic()
    e.official_time,e.time_anchor=official,anchor
    v.time=official
    feed.connect()
    async def clock():
        e.reads.network.observe('GET',.001,endpoint='/time')
        return official+time.monotonic()-anchor
    monkeypatch.setattr(e.reads,'server_time',clock)
    await asyncio.sleep(.025)
    force_sl(controller)
    wire.delay=lambda:time.sleep(.3)
    try:
        result,_=await asyncio.wait_for(controller.repair(*e.economic_inventory(),'cutoff-post'),5)
        assert result.close_barrier=='SAFE_TO_CONTINUE',result.block_reason
        assert len(v.post_requests)==2
        a=e.attempts[-1]
        assert e.order_post_elapsed[a.order_id]<240<=controller.elapsed()
        assert v.rows[a.order_id]['status']=='CANCELED'
        assert len([ids for ids in v.cancels if a.order_id in ids])>=2
    finally:
        await cleanup(setup)


async def test_sl_requotes_after_minimal_identity_write_without_posting_stale_signature(env,monkeypatch):
    from test_repair_preparation_quote_race import setup_no_sl

    setup,controller=await setup_no_sl(env,monkeypatch)
    v,e,_,_,_,_,_,pump,_=setup
    durable=e.journal.durable
    moved=[]
    async def move_on_identity_write(*args,**kwargs):
        result=await durable(*args,**kwargs)
        if args[0]=='sm_prepared' and not moved:
            moved.append(True)
            pump.update(bid='.72',yes_ask='.73',no_bid='.27',no_ask='.28')
            await asyncio.sleep(.025)
        return result
    monkeypatch.setattr(e.journal,'durable',move_on_identity_write)
    def filled(venue,oid):
        row=venue.rows[oid]
        if row['asset_id']=='1' and not row['associate_trades']:
            venue.fill(oid,'fresh-sl-after-write',5)
            reflect_positions(venue)
    v.on_order_read=filled
    try:
        result,_=await asyncio.wait_for(controller.repair(*e.economic_inventory(),'sl-signature-race'),5)
        assert moved and result.close_barrier=='SAFE_TO_CONTINUE',result.block_reason
        assert len(v.post_requests)==2 and len(e.attempts)==3
        assert not e.attempts[-2].post_started and e.attempts[-2].outcome=='ABORTED'
        assert e.attempts[-1].quote.price==.72
        assert e.economic_inventory()==(D(5),D(5))
    finally:
        await cleanup(setup)


async def test_third_reprice_waits_for_per_order_release_instead_of_exceeding_delta(env,monkeypatch):
    setup,controller=await setup_policy(env,monkeypatch)
    v,e,_,_,_,wire,_,pump,_=setup
    force_sl(controller)
    release=threading.Event()
    def delay():
        if len(v.post_requests)<=3:
            release.wait(.4)
    wire.delay=delay
    def filled(venue,oid):
        row=venue.rows[oid]
        if row['asset_id']=='2' and D(row['price'])==D('.699') and not row['associate_trades']:
            venue.fill(oid,'third-target-fill',5)
            reflect_positions(venue)
    v.on_order_read=filled
    async def changes():
        await until(lambda:len(v.post_requests)==2)
        pump.update(bid='.40',yes_ask='.42',no_bid='.58',no_ask='.60')
        await until(lambda:len(v.post_requests)==3)
        pump.update(bid='.30',yes_ask='.32',no_bid='.68',no_ask='.70')
        await asyncio.sleep(.06)
        assert len(v.post_requests)==3  # two possible NO5 already reserve all Delta space
        assert exposure(e).interval==(D(-5),D(5))
        release.set()
    task=asyncio.create_task(changes())
    try:
        result,_=await asyncio.wait_for(controller.repair(*e.economic_inventory(),'third-target'),5)
        await task
        assert result.close_barrier=='SAFE_TO_CONTINUE',result.block_reason
        assert len(v.post_requests)==4 and e.economic_inventory()==(D(5),D(5))
        records=journal_records(e.journal.path)
        assert any(r['kind']=='sm_sl_wait' and r['data']['payload']['reason']=='WAIT_DELTA_CAPACITY' for r in records)
        for r in records:
            if r['kind']=='sm_sl_signal':
                lo,hi=map(D,r['data']['payload']['delta_interval'])
                assert -5<=lo<=hi<=5
    finally:
        release.set()
        await asyncio.gather(task,return_exceptions=True)
        await cleanup(setup)
