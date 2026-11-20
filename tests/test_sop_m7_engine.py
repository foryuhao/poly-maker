"""Selected M7 raw sequence into the native independent simulation ledgers."""
from copy import deepcopy
from decimal import Decimal as D
import hashlib
import pytest
from m1223_sim import SopEngine,definition
from m1223_sim_forward import execution_configuration
from m1234_fixtures import fixture_events
from sop_signals.m7 import Parameters
from test_sop_m7_pause_release import Wire


class RecordedWire(Wire):
    def __init__(self):
        self.events=[]
        super().__init__()

    def send(self,t,raw):
        self.events.append((t,'FRAME',deepcopy(raw)))
        return super().send(t,raw)

    def timer(self,t):
        self.events.append((t,'POLL',None))
        return super().timer(t)


@pytest.mark.asyncio
@pytest.mark.parametrize('settled,parent_guard',[(True,False),(False,False),(True,True)])
async def test_native_selected_m7_parallel_private_pressure_partial_fill(tmp_path,monkeypatch,settled,parent_guard):
    monkeypatch.setattr('m511_sample_quality.own_market_gate',lambda *_:{
        'status':'ELIGIBLE','reason':'ARTIFICIAL_NATIVE_TEST_NOT_MARKET_SAMPLE'})
    tape=RecordedWire();tape.prepare(finish=not parent_guard)
    if parent_guard:
        tape.send(2.051,[{'event_type':'best_bid_ask','asset_id':'yes','best_bid':'.50','best_ask':'.52'},
            {'event_type':'price_change','price_changes':[
             {'asset_id':'yes','side':'BUY','price':'.50','size':'0'},
             {'asset_id':'yes','side':'BUY','price':'.50','size':'95'}]}])
    p=Parameters();initial=deepcopy(fixture_events()[0]);cfg=initial['definition']['settings']
    cfg.update(profiles={},sop=definition(p),sop_execution=execution_configuration(),
        scenarios={'trade-through':{'post_ms':100,'ack_ms':200,'cancel_ms':200,'ahead_cancel_fraction':'0'}})
    e=SopEngine(tmp_path,parameters=p);await e.ingest(initial);seq=1;wall=initial['available_wall']

    async def event(t,kind='FRAME',raw=None,**data):
        nonlocal seq
        seq+=1
        if kind=='FRAME':
            raw=deepcopy(raw)
            for row in raw if isinstance(raw,list) else [raw]:
                row.update(market=e.meta.condition_id,timestamp=str(round((wall+t)*1000)))
                if 'asset_id' in row:row['asset_id']={'yes':'1','no':'2'}.get(row['asset_id'],row['asset_id'])
                for change in row.get('price_changes',[]):
                    change['asset_id']={'yes':'1','no':'2'}.get(change['asset_id'],change['asset_id'])
            data.update(raw=raw,received_mono=t,received_wall=wall+t)
        elif raw is not None:
            data['raw']=raw
        await e.ingest({'schema':initial['schema'],'seq':seq,'kind':kind,
            'available_mono':t,'available_wall':wall+t,'generation':1,**data})

    await event(0,'CONNECT')
    for t,kind,raw in tape.events:await event(t,kind,raw)
    await event(2.052,'POLL');e.store.commit()
    broker=e.brokers['sop/trade-through']
    if parent_guard:
        assert not e.signal_history and not broker.orders and not e.assumed_orders
    else:
        assert len(e.signal_history)==len(broker.orders)==len(e.assumed_orders)==1
        fact=next(iter(e.signal_history.values()));order=next(iter(broker.orders.values()))
        assert fact['variant_id']=='M7_PAUSE_RELEASE'
        assert fact['feature_values']['ap1_occupancy_exception_used']
        assert fact['deadline']==pytest.approx(2.801)
        def sell(t,q,identity):
            return {'event_type':'last_trade_price','asset_id':'1','side':'SELL','price':'.49','size':q,
                    'transaction_hash':'0x'+hashlib.sha256(identity.encode()).hexdigest()}
        await event(2.2,raw=sell(2.2,'3','within'))
        await event(2.25,raw={'event_type':'price_change','price_changes':[
            {'asset_id':'1','side':'BUY','price':'.50','size':'0'},
            {'asset_id':'1','side':'BUY','price':'.50','size':'95'}]})
        await event(2.3,raw=sell(2.3,'4','in-flight'));await event(2.46,'POLL')
        assert order.filled==7 and order.state=='CANCELLED'
    if settled:
        await event(2.5,'SETTLEMENT',raw={'slug':e.meta.slug,'conditionId':e.meta.condition_id,
            'clobTokenIds':['1','2'],'outcomePrices':['1','0'],'closed':True,'resolutionStatus':'resolved'})
    await event(300,'END',reason='T300_OBSERVED');await e.close()
    from m1223_report import market_result
    m,rows=market_result(e.store.path,tmp_path)
    assert m['signal_count']==(0 if parent_guard else 1)
    if not parent_guard:
        assert D(rows[0]['known_filled_quantity'])==7
        assert D(rows[0]['within_signal_validity_quantity'])==3
        assert D(rows[0]['after_signal_end_quantity'])==D(rows[0]['cancel_in_flight_quantity'])==4
    e.retire_runtime();assert e.sop_runtime is None
