"""Artificial native-ledger proof: endpoint acceptance and quantity-capped fills."""
from copy import deepcopy
from decimal import Decimal as D
import hashlib
import pytest

from m1223_sim import SopEngine, definition
from m1223_sim_forward import execution_configuration
from m1234_fixtures import fixture_events
from sop_signals.m21 import Parameters


@pytest.mark.asyncio
@pytest.mark.parametrize('settled,parent_buy',[(True,False),(False,False),(True,True)])
async def test_native_endpoint_joint_signal_to_independent_ledgers(tmp_path,monkeypatch,settled,parent_buy):
    monkeypatch.setattr('m511_sample_quality.own_market_gate',lambda *_:{
        'status':'ELIGIBLE','reason':'ARTIFICIAL_NATIVE_TEST_NOT_REAL_MARKET_SAMPLE'})
    p=Parameters();initial=deepcopy(fixture_events()[0]);cfg=initial['definition']['settings']
    cfg.update(profiles={},sop=definition(p),sop_execution=execution_configuration(),
        scenarios={'trade-through':{'post_ms':100,'ack_ms':200,'cancel_ms':200,'ahead_cancel_fraction':'0'}})
    e=SopEngine(tmp_path,parameters=p);await e.ingest(initial);seq=1;wall=initial['available_wall']

    async def event(t,kind='FRAME',**data):
        nonlocal seq
        seq+=1
        if kind=='FRAME':
            raw=data['raw']
            for row in raw if isinstance(raw,list) else [raw]:
                row.update(market=e.meta.condition_id,timestamp=str(round((wall+t)*1000)))
            data.update(received_mono=t,received_wall=wall+t)
        await e.ingest({'schema':initial['schema'],'seq':seq,'kind':kind,
            'available_mono':t,'available_wall':wall+t,'generation':1,**data})

    def bbo():return [{'event_type':'best_bid_ask','asset_id':token,'best_bid':'.50','best_ask':'.52'} for token in ('1','2')]
    def delta(bid=None,ask=None):
        changes=[]
        if bid is not None:changes.append({'asset_id':'1','side':'BUY','price':'.50','size':str(bid)})
        if ask is not None:changes.append({'asset_id':'1','side':'SELL','price':'.52','size':str(ask)})
        return {'event_type':'price_change','price_changes':changes}
    def trade(price,q,identity,side='SELL'):return {'event_type':'last_trade_price','asset_id':'1','side':side,
        'price':price,'size':q,'trade_id':identity,'transaction_hash':'0x'+hashlib.sha256(identity.encode()).hexdigest()}

    await event(0,'CONNECT')
    await event(0,raw=[{'event_type':'book','asset_id':token,
        'bids':[{'price':'.50','size':'100'},{'price':'.49','size':'100'}],
        'asks':[{'price':'.52','size':'100'}]} for token in ('1','2')])
    await event(.01,raw=trade('.50','5','formation'));await event(.10,raw=delta(bid='90'))
    for t in (.15,.30,.45):await event(t,raw=bbo())
    await event(.49,raw=delta(bid='96',ask='80'))
    # The window[.01,.51) crosses the.50s coverage bucket. Supply actual
    # in-window evidence there; a later close-frame cannot fill that bucket.
    await event(.505,raw=bbo())
    if parent_buy:await event(.512,raw=[*bbo(),trade('.52','1','same-parent-buy','BUY')])
    else:await event(.512,raw=bbo())
    await event(.513,'POLL');e.store.commit()
    broker=e.brokers['sop/trade-through']
    if parent_buy:
        assert not e.signal_history and not broker.orders and not e.assumed_orders
    else:
        assert len(e.signal_history)==len(broker.orders)==len(e.assumed_orders)==1
        fact=next(iter(e.signal_history.values()));order=next(iter(broker.orders.values()))
        assert fact['guard_floor']==94 and fact['ask_ceiling']==90
        assert not fact['diagnostics']['bid_final_100ms_recovered']
        assert not fact['diagnostics']['ask_final_100ms_net_loss']
        assert D(order.intent.quantity)==10 and D(order.intent.price)==D('.50')
        await event(.65,raw=trade('.49','3','within-validity'))
        await event(.70,raw=delta(bid='93'));await event(.80,raw=trade('.49','4','cancel-race'))
        assert order.filled==7
        await event(.91,'POLL');assert order.state=='CANCELLED'
    if settled:
        await event(.95,'SETTLEMENT',raw={'slug':e.meta.slug,'conditionId':e.meta.condition_id,
            'clobTokenIds':['1','2'],'outcomePrices':['1','0'],'closed':True,'resolutionStatus':'resolved'})
    await event(300,'END',reason='T300_OBSERVED');await e.close()
    from m1223_report import market_result
    m,rows=market_result(e.store.path,tmp_path)
    assert m['signal_count']==(0 if parent_buy else 1)
    if not parent_buy:
        assert len(rows)==1 and D(rows[0]['known_filled_quantity'])==7 and rows[0]['unresolved']
        assert D(rows[0]['within_signal_validity_quantity'])==3
        assert D(rows[0]['after_signal_end_quantity'])==D(rows[0]['cancel_in_flight_quantity'])==4
        assert (D(rows[0]['assumed_gross_pnl'])==5 and D(rows[0]['known_simulated_gross_pnl'])==D('3.5')) if settled else rows[0]['assumed_gross_pnl'] is None
    e.retire_runtime();assert e.sop_runtime is None
