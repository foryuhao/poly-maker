"""Actual raw parser, both-side quantity references and parent publication."""
from decimal import Decimal as D

from test_sop_feed import Wire as BaseWire


class Wire(BaseWire):
    def __init__(self):
        super().__init__('M19')

    def hit(self,t,side='BUY',size='40',identity=None):
        self.send(t,{'event_type':'last_trade_price','asset_id':'yes','side':side,
                     'price':'.40' if side=='SELL' else '.42','size':size,
                     'trade_id':identity or str(t)+side})

    def delta(self,side,quantity,token='yes'):
        return {'asset_id':token,'side':'BUY' if side=='BID' else 'SELL',
                'price':'.40' if side=='BID' else '.42','size':str(quantity)}

    def depth(self,t,side,quantity):
        self.send(t,{'event_type':'price_change','price_changes':[self.delta(side,quantity)]})

    def refresh(self,t):
        self.send(t,[{'event_type':'best_bid_ask','asset_id':token,
                     'best_bid':'.40','best_ask':'.42'} for token in ('yes','no')])

    def timer(self,t):
        self.seq += 1
        self.f.timer(now=t,wall=1000+t,seq=self.seq)

    def reference(self):
        self.book(.99)
        self.book(.99,'no')
        self.hit(1.,'SELL','10')
        self.hit(1.001)
        self.depth(1.02,'BID',80)
        self.depth(1.021,'ASK',90)
        self.refresh(1.15)
        self.depth(1.30,'BID',95)
        self.refresh(1.30)
        self.depth(1.301,'ASK',94)
        self.refresh(1.45)
        self.refresh(1.50)
        self.timer(1.502)

    def second(self,*,late_buy=False,parent_break=False):
        self.depth(1.55,'ASK',100)
        self.hit(1.6,size='20')
        self.depth(1.62,'ASK',85)
        self.refresh(1.75)
        self.depth(1.90,'ASK',88)
        self.refresh(2.05)
        if late_buy:self.hit(2.09,size='1')
        if parent_break:
            self.send(2.101,[{'event_type':'best_bid_ask','asset_id':'yes',
                             'best_bid':'.40','best_ask':'.42'},
                            {'event_type':'price_change','price_changes':[
                                self.delta('ASK',100),self.delta('ASK',88)]}])
        else:self.timer(2.101)


def test_raw_direct_two_ask_rounds_and_strict_bid_publish_one_original_signal():
    w = Wire()
    w.reference()
    assert 'yes' in w.f.core.roots and w.f.core.components
    w.second()
    signal = next(iter(w.f.core.signals.values()))
    assert signal['anchor_price']==D('.40') and signal['deadline']==4.502
    assert signal['ask']['hits']==20 and signal['ask']['terminal_depth']['kind']=='ASK_PRICE_CHANGE'
    assert signal['prior_reference']['r']==D('.4') and signal['prior_reference']['data_proven']
    assert not hasattr(w.f,'receiver_clock_check')


def test_later_subupdate_in_the_same_parent_cannot_be_hidden_by_restored_ask():
    w = Wire()
    w.reference()
    w.second(parent_break=True)
    assert not w.f.core.signals and not w.f.core.roots


def test_last_buy_needs_an_explicit_later_ask_quantity_not_just_bbo_refresh():
    w = Wire()
    w.reference()
    w.second(late_buy=True)
    assert not w.f.core.signals
    assert any(p['payload'].get('reason')=='TERMINAL_ANCHOR_QUANTITY_UNAVAILABLE' for _,p in w.rows)


def test_complete_book_retains_original_ask_array_index():
    w = Wire()
    w.book(.99)
    w.book(.99,'no')
    w.hit(1.)
    w.depth(1.02,'ASK',90)
    w.refresh(1.15)
    w.send(1.30,{'event_type':'book','asset_id':'yes',
        'bids':[{'price':'.40','size':'100'}],
        'asks':[{'price':'.46','size':'200'},{'price':'.42','size':'94'},
                {'price':'.44','size':'150'}]})
    w.refresh(1.45)
    w.timer(1.501)
    terminal = w.f.core.roots['yes'].reference['terminal_depth']
    assert terminal['kind']=='ASK_BOOK' and terminal['change']==1


def test_multi_token_increment_keeps_global_ask_change_index():
    w = Wire()
    w.book(.99)
    w.book(.99,'no')
    w.hit(1.)
    w.depth(1.02,'ASK',90)
    w.send(1.15,{'event_type':'price_change','price_changes':[
        w.delta('ASK',100,'no'),w.delta('BID',100),w.delta('ASK',92),
        w.delta('BID',100,'no'),w.delta('ASK',94)]})
    terminal = w.f.core.rounds['yes','ASK',D('.42')].path.last_depth
    assert terminal.change==4 and terminal.kind=='ASK_PRICE_CHANGE'


def test_duplicate_direct_buy_does_not_create_a_second_hit_or_mirror_signal():
    w = Wire()
    w.book(.99)
    w.book(.99,'no')
    w.hit(1.,identity='same')
    w.hit(1.,identity='same')
    assert w.f.core.rounds['yes','ASK',D('.42')].path.hits==40
    assert not any(r.token=='no' for r in w.f.core.rounds.values())
    assert w.f.counts['DUPLICATE_DIRECT_TRADE']==1


def test_same_source_quantity_does_not_prove_pre_hit_ask():
    w = Wire()
    w.book(1.)
    w.hit(1.)
    assert not w.f.core.rounds and w.f.reasons['yes']=='PRE_HIT_LEVEL_ORDER_UNPROVEN'


def test_tick_and_reconnect_invalidate_both_ask_and_bid_and_release_paths():
    w = Wire()
    w.reference()
    w.send(1.55,{'event_type':'tick_size_change','asset_id':'yes',
                 'old_tick_size':'.01','new_tick_size':'.02'})
    assert not w.f.core.roots and not w.f.core.components and not w.f.core.signals
    w.f.gap('DISCONNECTED')
    w.f.release_runtime()
    assert not w.f.core.rounds and not w.f.core.closing_rounds and not w.f.core.roots
