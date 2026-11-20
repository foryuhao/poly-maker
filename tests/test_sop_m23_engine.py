"""Artificial raw-to-ledger M23: partial volume,late challenge and cancel race."""
from copy import deepcopy
from decimal import Decimal as D
import hashlib

import pytest
from m1223_sim import SopEngine,definition
from m1223_sim_forward import execution_configuration
from m1234_fixtures import fixture_events
from sop_signals.m23 import Parameters


@pytest.mark.asyncio
@pytest.mark.parametrize('settled,parent_guard',[(True,False),(False,False),(True,True)])
async def test_m23_native_original_first_sell_to_two_ledgers(tmp_path,monkeypatch,settled,parent_guard):
    monkeypatch.setattr('m511_sample_quality.own_market_gate',lambda *_:{
        'status':'ELIGIBLE','reason':'ARTIFICIAL_NATIVE_TEST_NOT_MARKET_SAMPLE'})
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
    def delta(q):return {'event_type':'price_change','price_changes':[
        {'asset_id':'1','side':'BUY','price':'.50','size':str(q)}]}
    def sell(price,q,identity):return {'event_type':'last_trade_price','asset_id':'1','side':'SELL',
        'price':price,'size':q,'trade_id':identity,'transaction_hash':'0x'+hashlib.sha256(identity.encode()).hexdigest()}
    await event(0,'CONNECT')
    await event(0,raw=[{'event_type':'book','asset_id':token,
        'bids':[{'price':'.50','size':'100'},{'price':'.49','size':'100'}],
        'asks':[{'price':'.52','size':'100'}]} for token in ('1','2')])
    await event(.01,raw=delta(150));await event(.10,raw=bbo());await event(.111,'POLL')
    assert e.sop_runtime.core.roots['1'].phase=='WAIT_FIRST_SELL'
    for t in (.2,.35,.5,.65,.8,.95,1.1,1.25):await event(t,raw=bbo())
    assert e.sop_runtime.core.roots['1'].phase=='WAIT_FIRST_SELL'  # No .75s timeout.
    await event(1.311,raw=sell('.50','30','challenge'));await event(1.35,raw=delta(145))
    for t in (1.45,1.6,1.75):await event(t,raw=bbo())
    if parent_guard:await event(1.812,raw=[*bbo(),delta(109),delta(145)])
    else:await event(1.812,raw=bbo())
    # The native public parser retains a pending-input watermark until POLL.
    # Advance that real publication boundary, rather than bypassing it.
    await event(1.813,'POLL')
    e.store.commit()  # Preserve the actual native trace if an assertion fails.
    broker=e.brokers['sop/trade-through']
    if parent_guard:
        assert not e.signal_history and not broker.orders and not e.assumed_orders
    else:
        assert len(e.signal_history)==len(broker.orders)==len(e.assumed_orders)==1
        fact=next(iter(e.signal_history.values()));assert not fact['diagnostics']['first_sell_within_750ms']
        assert fact['deadline']==pytest.approx(3.111)
        order=next(iter(broker.orders.values()));assert D(order.intent.quantity)==10 and D(order.intent.price)==D('.5')
        await event(1.95,raw=sell('.49','3','fill-valid'))
        await event(2.,raw=delta(109));await event(2.1,raw=sell('.49','4','fill-cancel-race'))
        assert order.filled==7 and order.cancel_requested==pytest.approx(2.)
        await event(2.21,'POLL');assert order.state=='CANCELLED'
    if settled:
        await event(2.3,'SETTLEMENT',raw={'slug':e.meta.slug,'conditionId':e.meta.condition_id,
            'clobTokenIds':['1','2'],'outcomePrices':['1','0'],'closed':True,'resolutionStatus':'resolved'})
    await event(300,'END',reason='T300_OBSERVED');await e.close()
    from m1223_report import market_result
    m,rows=market_result(e.store.path,tmp_path)
    assert m['signal_count']==(0 if parent_guard else 1)
    assert m['positive_modelled_fill_orders']==(0 if parent_guard else 1)
    if not parent_guard:
        assert len(rows)==1 and D(rows[0]['known_filled_quantity'])==7
        assert D(rows[0]['within_signal_validity_quantity'])==3
        assert D(rows[0]['after_signal_end_quantity'])==4
        assert D(rows[0]['cancel_in_flight_quantity'])==4
        if settled:
            assert D(rows[0]['assumed_gross_pnl'])==5
            assert D(rows[0]['known_simulated_gross_pnl'])==D('3.5')
        else:
            assert rows[0]['assumed_gross_pnl'] is None and rows[0]['known_simulated_gross_pnl'] is None
    e.retire_runtime();assert e.sop_runtime is None
