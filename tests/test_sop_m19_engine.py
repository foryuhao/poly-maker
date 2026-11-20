"""M19 actual raw adapter through orders, partial fills and official ledgers."""
from copy import deepcopy
from decimal import Decimal as D
import hashlib
import json

import pytest

from m1223_sim import SopEngine, definition
from m1223_sim_forward import execution_configuration
from sop_signals.parameters import load_revision
from m1234_fixtures import fixture_events


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['settled','unsettled','parent_guard_break','tick_change'])
async def test_m19_raw_two_sides_to_native_orders_cancel_race_and_retirement(tmp_path,mode):
    p,revision = load_revision({'strategy':'M19','revision_round':0,'parameters':{},
                               'pnl_used_for_selection':False})
    initial = deepcopy(fixture_events()[0])
    initial['definition']['settings'].update(profiles={},sop=definition(p),
        sop_parameter_revision=revision,sop_execution=execution_configuration(),
        scenarios={'trade-through':{'post_ms':100,'ack_ms':200,'cancel_ms':200,
                                    'ahead_cancel_fraction':'0'}})
    e = SopEngine(tmp_path,parameters=p)
    await e.ingest(initial)
    start,seq = initial['available_wall'],1

    async def event(t,kind,raw=None,**payload):
        nonlocal seq
        seq += 1
        if raw is not None:
            if kind=='FRAME':
                for row in raw if isinstance(raw,list) else [raw]:
                    row.update(market=e.meta.condition_id,timestamp=str(round((start+t)*1000)))
            payload.update(raw=raw,received_mono=t,received_wall=start+t)
        await e.ingest({'schema':initial['schema'],'seq':seq,'kind':kind,'available_mono':t,
                        'available_wall':start+t,'generation':1,**payload})

    def depth(side,q):
        return {'event_type':'price_change','price_changes':[{'asset_id':'1',
            'side':'BUY' if side=='BID' else 'SELL','price':'.50' if side=='BID' else '.52','size':q}]}

    def trade(side,price,q,identity):
        return {'event_type':'last_trade_price','asset_id':'1','side':side,'price':price,'size':q,
                'trade_id':identity,'transaction_hash':'0x'+hashlib.sha256(identity.encode()).hexdigest()}

    async def refresh(t):
        await event(t,'FRAME',[{'event_type':'best_bid_ask','asset_id':token,
                               'best_bid':'.50','best_ask':'.52'} for token in ('1','2')])

    await event(0,'CONNECT')
    await event(.99,'FRAME',[{'event_type':'book','asset_id':token,
        'bids':[{'price':'.50','size':'100'},{'price':'.49','size':'100'}],
        'asks':[{'price':'.52','size':'100'}]} for token in ('1','2')])
    await event(1.,'FRAME',trade('SELL','.50','10','bid-support'))
    await event(1.001,'FRAME',trade('BUY','.52','40','ask-reference'))
    await event(1.02,'FRAME',depth('BID','80'))
    await event(1.021,'FRAME',depth('ASK','90'))
    await refresh(1.15)
    await event(1.30,'FRAME',depth('BID','95'))
    await refresh(1.30)
    await event(1.301,'FRAME',depth('ASK','94'))
    await refresh(1.45)
    await refresh(1.50)
    await event(1.502,'POLL')
    core = e.sop_runtime.core
    assert core.roots['1'].reference['hits']==40 and core.components
    await event(1.55,'FRAME',depth('ASK','100'))
    await event(1.6,'FRAME',trade('BUY','.52','20','ask-second'))
    await event(1.62,'FRAME',depth('ASK','85'))
    await refresh(1.75)
    await event(1.90,'FRAME',depth('ASK','88'))
    await refresh(2.05)
    parent = [{'event_type':'best_bid_ask','asset_id':token,
               'best_bid':'.50','best_ask':'.52'} for token in ('1','2')]
    if mode=='parent_guard_break':
        parent.extend([depth('ASK','100'),depth('ASK','88')])
    elif mode=='tick_change':
        parent.append({'event_type':'tick_size_change','asset_id':'1',
                       'old_tick_size':'.01','new_tick_size':'.02'})
    await event(2.101,'FRAME',parent)
    await event(2.102,'POLL')
    broker = e.brokers['sop/trade-through']
    if mode in {'parent_guard_break','tick_change'}:
        assert not core.signals and not e.signal_history and not broker.orders and not e.assumed_orders
        await event(300,'END',reason='T300_OBSERVED')
        await e.close()
        e.retire_runtime()
        assert e.sop_runtime is None and not core.roots and not core.closing_rounds
        return
    assert len(e.signal_history)==len(broker.orders)==len(e.assumed_orders)==1
    signal = next(iter(e.signal_history.values()))
    assert signal['confirmed']==2.101 and D(str(signal['deadline']))==D('4.502')
    order = next(iter(broker.orders.values()))
    assert order.intent.strategy==order.intent.profile=='M19'
    assert D(order.intent.price)==D('.50') and D(order.intent.quantity)==10
    await refresh(2.20)
    await event(2.31,'POLL')
    await event(2.35,'FRAME',trade('SELL','.49','3','valid-partial'))
    assert order.filled==3
    await event(2.35,'FRAME',trade('SELL','.49','3','valid-partial'))
    assert order.filled==3
    await event(2.40,'FRAME',depth('ASK','93'))
    assert not core.signals and order.cancel_requested==pytest.approx(2.40)
    await event(2.50,'FRAME',trade('SELL','.49','4','cancel-race'))
    await event(2.61,'POLL')
    assert order.filled==7 and order.state=='CANCELLED'
    if mode=='settled':
        await event(2.62,'SETTLEMENT',{'slug':e.meta.slug,'conditionId':e.meta.condition_id,
            'clobTokenIds':['1','2'],'outcomePrices':['1','0'],'closed':True,'resolutionStatus':'resolved'})
        assert broker.account('M19').payout==7
    e.store.commit()
    attributes = [json.loads(raw) for (raw,) in e.store.db.execute(
        "SELECT payload FROM audit WHERE kind='SOP_FILL_ATTRIBUTION' ORDER BY seq")]
    assert [p['classification'] for p in attributes]==['WITHIN_SIGNAL_VALIDITY','AFTER_SIGNAL_END']
    await event(300,'END',reason='T300_OBSERVED')
    await e.close()
    from m1223_report import install_report_compatibility, market_result
    from polymaker.m1234_sim.report import read_state
    install_report_compatibility()
    market,orders = market_result(e.store.path,tmp_path)
    assert market['signal_count']==market['positive_modelled_fill_orders']==1
    assert market['signal_ids']==[signal['id']]
    assert D(orders[0]['within_signal_validity_quantity'])==3
    assert D(orders[0]['after_signal_end_quantity'])==D(orders[0]['cancel_in_flight_quantity'])==4
    if mode=='unsettled':
        assert market['pending_settlement_orders']==1
        assert orders[0]['assumed_gross_pnl'] is orders[0]['known_simulated_gross_pnl'] is None
    else:
        assert D(orders[0]['assumed_gross_pnl'])==5
        assert D(orders[0]['known_simulated_gross_pnl'])==D('3.50')
    durable = read_state(e.store.path)
    e.retire_runtime()
    assert e.sop_runtime is None and not core.roots and not core.rounds and not core.closing_rounds
    assert not core.components and not core.pending_bids
    assert read_state(e.store.path)==durable
