"""Endpoint-only joint proof, original BUY absence and fixed followup guards."""
from decimal import Decimal as D
import pytest
from test_sop_feed import Wire as BaseWire


class Wire(BaseWire):
    def __init__(self):super().__init__('M21')

    def book(self,t=.99,token='yes',bid='100',ask='100'):
        self.send(t,{'event_type':'book','asset_id':token,
            'bids':[{'price':'.40','size':str(bid)}],
            'asks':[{'price':'.42','size':str(ask)}]})

    def delta(self,t,*,bid=None,ask=None,token='yes'):
        changes=[]
        if bid is not None:changes.append({'asset_id':token,'side':'BUY','price':'.40','size':str(bid)})
        if ask is not None:changes.append({'asset_id':token,'side':'SELL','price':'.42','size':str(ask)})
        self.send(t,{'event_type':'price_change','price_changes':changes})

    def refresh(self,t):
        self.send(t,[{'event_type':'best_bid_ask','asset_id':token,'best_bid':'.40','best_ask':'.42'} for token in ('yes','no')])

    def print(self,t,side='SELL',quantity='5',identity='first',**extra):
        self.send(t,{'event_type':'last_trade_price','asset_id':'yes','side':side,
            'price':'.40' if side=='SELL' else '.42','size':str(quantity),'trade_id':identity,**extra})

    def timer(self,t):
        self.seq+=1;self.f.timer(now=t,wall=1000+t,seq=self.seq)

    def prepare(self,q0='100',low='90',end='96',hit='5'):
        self.book(.99,bid=q0);self.book(.99,'no');self.print(1.,quantity=hit)
        self.delta(1.1,bid=low);self.refresh(1.2);self.refresh(1.35)
        self.delta(1.48,bid=end,ask='80')

    def form(self,**kwargs):
        self.prepare(**kwargs);self.timer(1.501)


def test_both_100ms_holds_are_diagnostics_not_endpoint_vetoes():
    w=Wire();w.form();s=next(iter(w.f.core.signals.values()))
    assert s['guard_floor']==94 and s['ask_ceiling']==90
    assert s['deadline']==pytest.approx(4.501)
    assert not s['diagnostics']['bid_final_100ms_recovered']
    assert not s['diagnostics']['ask_final_100ms_net_loss']
    assert s['diagnostics']['ask_prequiet_250ms'] is None


def test_tiny_relative_initial_hit_and_loss_do_not_reintroduce_common_fraction():
    w=Wire();w.form(q0='100000',low='99999',end='99999.6',hit='5')
    s=next(iter(w.f.core.signals.values()));assert s['bid']['data_proven']
    assert not s['diagnostics']['bid_hit_fraction_at_least_10pct']
    assert not s['diagnostics']['bid_loss_fraction_at_least_5pct']


@pytest.mark.parametrize('kwargs',[{'hit':'4.99'},{'low':'99.51','end':'99.8'},{'end':'94.9'}])
def test_mandatory_initial_hit_loss_and_endpoint_recovery_still_apply(kwargs):
    w=Wire();w.form(**kwargs);assert not w.f.core.signals


def test_ask_net_loss_must_be_new_in_this_window():
    w=Wire();w.prepare();w.delta(1.49,ask='95');w.timer(1.501)
    assert not w.f.core.signals


def test_bbo_cannot_replace_explicit_ask_terminal_after_counted_sell():
    w=Wire();w.prepare();w.print(1.49,quantity='1',identity='last');w.delta(1.495,bid='96')
    w.timer(1.501);assert not w.f.core.signals and w.f.core.counts['ROOT_UNKNOWN']


def test_same_parent_buy_after_candidate_close_prevents_publication():
    w=Wire();w.prepare()
    w.send(1.501,[{'event_type':'best_bid_ask','asset_id':'yes','best_bid':'.40','best_ask':'.42'},
        {'event_type':'last_trade_price','asset_id':'yes','side':'BUY','price':'.42','size':'1','trade_id':'counter'}])
    assert not w.f.core.signals and not w.f.core.roots


@pytest.mark.parametrize('side,broken,recovered',[('BUY','93','96'),('SELL','91','80')])
def test_post_window_guard_break_cannot_be_hidden_by_same_parent_recovery(side,broken,recovered):
    w=Wire();w.prepare();price='.40' if side=='BUY' else '.42'
    w.send(1.501,{'event_type':'price_change','price_changes':[
        {'asset_id':'yes','side':side,'price':price,'size':broken},
        {'asset_id':'yes','side':side,'price':price,'size':recovered}]})
    assert not w.f.core.signals


def test_equal_guard_values_stay_valid_and_new_buy_after_confirmation_is_not_exit():
    w=Wire();w.form();sid=next(iter(w.f.core.signals));deadline=w.f.core.signals[sid]['deadline']
    w.delta(1.6,bid='94',ask='90');w.print(1.61,side='BUY',quantity='1',identity='new-buy')
    assert sid in w.f.core.signals and w.f.core.signals[sid]['deadline']==deadline


def test_late_discovered_buy_received_at_confirmation_ends_on_discovery():
    w=Wire();w.form();sid=next(iter(w.f.core.signals));at=w.f.core.signals[sid]['confirmed']
    w.send(at,{'event_type':'last_trade_price','asset_id':'yes','side':'BUY','price':'.42','size':'1','trade_id':'late'},available=at+.01)
    assert sid not in w.f.core.signals


def test_unidentified_buy_survives_same_receipt_rebuild_before_first_sell():
    w=Wire();w.book();w.book(token='no')
    w.send(1.,[{'event_type':'last_trade_price','asset_id':'yes','side':'BUY','price':'.42','size':'1'},
        {'event_type':'book','asset_id':'yes','bids':[{'price':'.40','size':'100'}],
         'asks':[{'price':'.42','size':'100'}]},
        {'event_type':'last_trade_price','asset_id':'yes','side':'SELL','price':'.40','size':'5','trade_id':'seed'}])
    assert not w.f.core.roots and not w.f.core.signals


def test_known_retransmitted_old_buy_does_not_become_new_counterevidence():
    w=Wire();w.book(.98);w.book(.98,'no')
    old={'event_type':'last_trade_price','asset_id':'yes','side':'BUY','price':'.42','size':'1','trade_id':'old','timestamp':'1000990'}
    w.send(.99,old);w.send(1.,old);w.print(1.,quantity='5');assert 'yes' in w.f.core.roots


def test_proved_old_buy_retransmit_deduplicates_before_source_regression():
    w=Wire();w.book(.98);w.book(.98,'no')
    old={'event_type':'last_trade_price','asset_id':'yes','side':'BUY','price':'.42','size':'1','trade_id':'old','timestamp':'1000990'}
    w.send(.99,old);w.delta(.995,bid='101')
    w.send(1.,[old,
        {'event_type':'last_trade_price','asset_id':'yes','side':'SELL','price':'.40','size':'5','trade_id':'new'}])
    assert 'yes' in w.f.core.roots and w.f.counts['DUPLICATE_DIRECT_TRADE']==1


@pytest.mark.parametrize('endpoint,alive',[('96',True),('94.9',False)])
def test_followup_endpoint_uses_common_materiality_but_not_100ms_hold(endpoint,alive):
    w=Wire();w.form();w.print(1.6,quantity='1',identity='follow')
    w.delta(1.65,bid='95');w.refresh(1.8);w.refresh(1.95);w.delta(2.08,bid=endpoint)
    w.timer(2.101)
    assert bool(w.f.core.signals) is alive


def test_exact_original_expiry_and_release():
    w=Wire();w.form();deadline=next(iter(w.f.core.signals.values()))['deadline']
    w.timer(deadline);assert not w.f.core.signals
    w.f.release_runtime();assert not w.f.core.roots and not w.f.core.buy_presence and not w.f.quantities
