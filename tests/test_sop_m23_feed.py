"""Original frames preserve before/after quantity and SELL presence evidence."""
from test_sop_feed import Wire as BaseWire


class Wire(BaseWire):
    def __init__(self):super().__init__('M23')

    def depth(self,t,q):
        self.send(t,{'event_type':'price_change','price_changes':[
            {'asset_id':'yes','side':'BUY','price':'.40','size':str(q)}]})

    def refresh(self,t):
        self.send(t,[{'event_type':'best_bid_ask','asset_id':token,
                     'best_bid':'.40','best_ask':'.42'} for token in ('yes','no')])

    def timer(self,t):
        self.seq+=1;self.f.timer(now=t,wall=1000+t,seq=self.seq)

    def seed(self):
        self.book(.99);self.book(.99,'no');self.depth(1.,150);self.refresh(1.1);self.timer(1.101)

    def first_sell(self,t=2.301,quantity='30',identity='challenge'):
        self.send(t,{'event_type':'last_trade_price','asset_id':'yes','side':'SELL',
                     'price':'.40','size':quantity,'trade_id':identity})

    def ready(self,terminal=True):
        self.seed()
        for t in (1.25,1.4,1.55,1.7,1.85,2.,2.15,2.30):self.refresh(t)
        self.first_sell()
        if terminal:self.depth(2.35,145)
        for t in (2.5,2.65,2.8):self.refresh(t)
        self.timer(2.802)


def test_original_feed_accepts_late_first_sell_without_rf_loss_gate():
    w=Wire();w.ready();s=next(iter(w.f.core.signals.values()))
    assert s['test']['hits']==30 and s['test']['loss']==5
    assert s['deadline']==4.101 and not s['diagnostics']['first_sell_within_750ms']
    assert s['test']['terminal']['kind']=='BID_PRICE_CHANGE'


def test_bbo_refresh_cannot_replace_post_sell_explicit_p_quantity():
    w=Wire();w.ready(terminal=False)
    assert not w.f.core.signals and w.f.core.counts['ROOT_UNKNOWN']>=1


def test_sell_in_earlier_parent_row_same_receipt_cannot_be_filtered_out():
    w=Wire();w.book(.99);w.book(.99,'no')
    w.send(1.,[{'event_type':'last_trade_price','asset_id':'yes','side':'SELL',
               'price':'.40','size':'.01','trade_id':'early'},
              {'event_type':'price_change','price_changes':[
               {'asset_id':'yes','side':'BUY','price':'.40','size':'150'}]}])
    w.refresh(1.1);w.timer(1.101)
    assert not w.f.core.roots


def test_same_frame_intermediate_fpre_break_cannot_be_hidden():
    w=Wire();w.book(.99);w.book(.99,'no');w.depth(1.,150)
    w.send(1.101,[{'event_type':'best_bid_ask','asset_id':'yes','best_bid':'.40','best_ask':'.42'},
                 {'event_type':'price_change','price_changes':[
                  {'asset_id':'yes','side':'BUY','price':'.40','size':'139'},
                  {'asset_id':'yes','side':'BUY','price':'.40','size':'150'}]}])
    assert not w.f.core.roots and not w.f.core.signals


def test_duplicate_direct_print_does_not_supply_extra_test_volume():
    w=Wire();w.seed();w.first_sell(1.2,'20','same');w.first_sell(1.2,'20','same')
    w.depth(1.3,145);w.refresh(1.45);w.refresh(1.6);w.timer(1.701)
    assert not w.f.core.signals and w.f.counts['DUPLICATE_DIRECT_TRADE']==1


def test_healthy_snapshot_provides_atomic_delta_and_original_bid_array_index():
    w=Wire();w.book(.99);w.book(.99,'no')
    w.send(1.,{'event_type':'book','asset_id':'yes',
        'bids':[{'price':'.38','size':'200'},{'price':'.40','size':'150'}],
        'asks':[{'price':'.42','size':'100'}]})
    r=w.f.core.roots['yes'];assert r.delta==50 and r.seed_input_ref[2]==1


def test_tick_change_drops_preload_and_does_not_reuse_old_generation():
    w=Wire();w.seed();w.send(1.15,{'event_type':'tick_size_change','asset_id':'yes',
        'old_tick_size':'.01','new_tick_size':'.02'})
    assert not w.f.core.roots and not w.f.core.signals
    w.f.release_runtime();assert not w.f.core.last_sell


def test_unidentified_original_sell_cannot_become_zero_sell_after_same_parent_rebuild():
    w=Wire();w.book(.99);w.book(.99,'no')
    w.send(1.,[
        {'event_type':'last_trade_price','asset_id':'yes','side':'SELL','price':'.40','size':'.01'},
        {'event_type':'book','asset_id':'yes','bids':[{'price':'.40','size':'100'}],
         'asks':[{'price':'.42','size':'100'}]},
        {'event_type':'price_change','price_changes':[
         {'asset_id':'yes','side':'BUY','price':'.40','size':'150'}]}])
    w.refresh(1.1);w.timer(1.101)
    assert not w.f.core.roots and not w.f.core.signals
    assert w.f.core.counts['SEED_UNKNOWN']>=1


def test_unknown_direction_before_rebuild_does_not_prove_seed_sell_absence():
    w=Wire();w.book(.99);w.book(.99,'no')
    w.send(1.,[
        {'event_type':'last_trade_price','asset_id':'yes','price':'.40','size':'.01','trade_id':'unknown-side'},
        {'event_type':'book','asset_id':'yes','bids':[{'price':'.40','size':'100'}],
         'asks':[{'price':'.42','size':'100'}]}])
    # A new parent with the same original received time still overlaps t0.
    w.send(1.,{'event_type':'book','asset_id':'yes','bids':[{'price':'.40','size':'100'}],
              'asks':[{'price':'.42','size':'100'}]})
    w.depth(1.,150);w.refresh(1.1);w.timer(1.101)
    assert not w.f.core.roots


def test_known_retransmission_keeps_original_receipt_and_does_not_invent_new_sell():
    w=Wire();w.book(.98);w.book(.98,'no')
    old={'event_type':'last_trade_price','asset_id':'yes','side':'SELL',
         'price':'.40','size':'.01','trade_id':'original','timestamp':'1000990'}
    w.send(.99,old)
    w.send(1.,[old,{'event_type':'price_change','price_changes':[
        {'asset_id':'yes','side':'BUY','price':'.40','size':'150'}]}])
    w.refresh(1.1);w.timer(1.101)
    assert w.f.core.roots['yes'].phase=='WAIT_FIRST_SELL'
    assert w.f.counts['DUPLICATE_DIRECT_TRADE']==1


def test_unproved_marker_does_not_forbid_fresh_seed_at_a_later_receipt():
    w=Wire();w.book(.99);w.book(.99,'no')
    w.send(1.,[{'event_type':'last_trade_price','asset_id':'yes','side':'SELL','price':'.40','size':'.01'},
              {'event_type':'book','asset_id':'yes','bids':[{'price':'.40','size':'100'}],
               'asks':[{'price':'.42','size':'100'}]}])
    w.depth(1.,150);assert not w.f.core.roots
    w.depth(1.01,100);w.depth(1.02,150);w.refresh(1.12);w.timer(1.121)
    assert w.f.core.roots['yes'].phase=='WAIT_FIRST_SELL'
