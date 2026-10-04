"""Checks the conditional Maker queue rule and post-only gate."""
import importlib.util
from pathlib import Path

path=Path(__file__).resolve().parents[1]/'research_tools'
spec=importlib.util.spec_from_file_location('p0_maker_only',path/'p0_maker_only.py')
import sys
sys.path.insert(0,str(path))
maker=importlib.util.module_from_spec(spec)
spec.loader.exec_module(maker)


def test_print_consumes_ahead_before_order_and_supports_partial():
    ahead,own=maker.consume_queue(10,5,7)
    assert ahead==3 and own==0
    ahead,own=maker.consume_queue(ahead,5,5)
    assert ahead==0 and own==2
    ahead,own=maker.consume_queue(ahead,3,20)
    assert ahead==0 and own==3


def test_post_only_rejects_crossing_price():
    assert maker.post_only_accept(4900,5000)
    assert not maker.post_only_accept(5000,5000)
    assert not maker.post_only_accept(5100,5000)
    assert not maker.post_only_accept(4900,None)


def test_cancel_race_can_fill_after_arrival_before_confirmation(tmp_path, monkeypatch):
    import json
    slug='btc-updown-5m-1000000000'; start=1_000_000_000_000_000
    session=tmp_path/'session';session.mkdir()
    (session/'manifest.json').write_text(json.dumps({'condition_id':'0xabc','yes_token_id':'yes','no_token_id':'no',
        'started_at_us':start-1,'ended_at_us':start+301_000_000}))
    def event(seq,kind,t,payload):
        return {'event_seq':seq,'kind':kind,'available_at_us':t,'exchange_at_us':t,'received_at_us':t,'payload':payload}
    events=[
        event(1,'BOOK',start,{'token_id':'yes','bids':[[.60,1]],'asks':[[.62,10]],'tick_size':.01}),
        event(2,'BOOK',start+1000,{'token_id':'no','bids':[[.38,10]],'asks':[[.40,10]],'tick_size':.01}),
        event(3,'DELTA',start+601000,{'token_id':'yes','side':'BUY','price':.61,'size':2}),
        event(4,'TRADE',start+801000,{'token_id':'yes','side':'SELL','price':.60,'size':10}),
    ]
    (session/'events.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in events))
    monkeypatch.setattr(maker,'DATA',tmp_path)
    replay=maker.MakerReplay()
    sample={'market':slug,'session':'session','start_us':str(start),'end_us':str(start+300_000_000),'grade':'A_conditional'}
    label={'condition_id':'0xabc','yes_token_id':'yes','no_token_id':'no','winner':'YES'}
    replay.market(sample,label,{})
    row=replay.markets[0]
    assert row['accepted']==1 and row['cancel_race_events']==1
    assert abs(row['buy_qty']-5.5)<1e-8 and row['canceled']==0 and row['filled_orders']==1
    assert replay.cash>=0 and abs(row['pnl']-2.2)<1e-8


def test_stale_book_requests_cancel_before_next_public_event(tmp_path, monkeypatch):
    import json
    slug='btc-updown-5m-1000000000';start=1_000_000_000_000_000
    session=tmp_path/'session';session.mkdir()
    (session/'manifest.json').write_text(json.dumps({'condition_id':'0xabc','yes_token_id':'yes','no_token_id':'no',
        'started_at_us':start-1,'ended_at_us':start+301_000_000}))
    def event(seq,kind,t,payload):
        return {'event_seq':seq,'kind':kind,'available_at_us':t,'exchange_at_us':t,'received_at_us':t,'payload':payload}
    events=[
        event(1,'BOOK',start,{'token_id':'yes','bids':[[.60,1]],'asks':[[.62,10]],'tick_size':.01}),
        event(2,'BOOK',start+1000,{'token_id':'no','bids':[[.38,10]],'asks':[[.40,10]],'tick_size':.01}),
        event(3,'TRADE',start+2_200_000,{'token_id':'yes','side':'SELL','price':.60,'size':10}),
    ]
    (session/'events.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in events))
    monkeypatch.setattr(maker,'DATA',tmp_path)
    replay=maker.MakerReplay()
    replay.market({'market':slug,'session':'session','start_us':str(start),'end_us':str(start+300_000_000),'grade':'A_conditional'},
                  {'condition_id':'0xabc','yes_token_id':'yes','no_token_id':'no','winner':'YES'}, {})
    cancels=[x for x in replay.orders if x['event']=='CANCEL_REQUEST']
    assert cancels and int(cancels[0]['time_us'])==start+2_000_001
    assert replay.markets[0]['cancel_race_events']==1
