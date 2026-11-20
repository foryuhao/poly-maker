"""Original M19 causal paths; artificial events do not count as market samples."""
from decimal import Decimal as D

import pytest

from sop_signals.m19 import M19, Parameters


class Tape:
    def __init__(self):
        self.events = []
        self.core = M19(lambda kind,payload:self.events.append((kind,payload)))
        self.seq = 1
        self.depths = {side:(D(100),(0.,100.,(1,0,0))) for side in ('BID','ASK')}

    def clock(self, t):
        self.seq += 1
        self.core.clock(t,self.seq,0)

    def trade(self,t,side,quantity,*,received=None):
        self.clock(t)
        received = t if received is None else received
        leg = 'BID' if side=='SELL' else 'ASK'
        q,ref = self.depths[leg]
        self.core.trade(token='UP',side=side,price=D('.50' if leg=='BID' else '.52'),
            quantity=D(str(quantity)),received=received,q0=q,generation=1,
            input_ref=(self.seq,0,0),source=100+received,initial_quantity_ref=ref,
            pre_event_best_bid=D('.50'),pre_event_best_ask=D('.52'))

    def depth(self,t,leg,quantity):
        self.clock(t)
        q = D(str(quantity))
        ref = (t,100+t,(self.seq,0,0))
        self.depths[leg] = q,ref
        self.core.depth('UP',leg,D('.50' if leg=='BID' else '.52'),q,t,
            generation=1,input_ref=ref[2],source=ref[1],kind=leg+'_PRICE_CHANGE')

    def close(self,t,*,covered=True):
        self.clock(t)
        self.core.close(t,lambda *_:covered)
        self.core.publish()


def first_ask(tape):
    tape.trade(1.,'BUY',40)
    tape.depth(1.02,'ASK',90)
    tape.depth(1.30,'ASK',94)
    tape.close(1.501)
    assert 'UP' in tape.core.roots


def second_ask(tape,*,low=85,end=88,quantity=20,close=2.101):
    # A1 is historical: regaining100 exceeds its old weak ceiling95 and
    # must remain legal before the new A2 starts.
    tape.depth(1.55,'ASK',100)
    tape.trade(1.6,'BUY',quantity)
    tape.depth(1.62,'ASK',low)
    tape.depth(1.90,'ASK',end)
    tape.close(close)


def full_path(*,bid_start=.1,bid_close=.601):
    tape = Tape()
    tape.trade(bid_start,'SELL',10)
    tape.depth(bid_start+.02,'BID',80)
    tape.depth(bid_start+.30,'BID',95)
    tape.close(bid_close)
    first_ask(tape)
    second_ask(tape)
    return tape


def test_original_ask_comparison_without_bid_is_not_a_signal():
    tape = Tape()
    first_ask(tape)
    second_ask(tape)
    assert tape.core.roots['UP'].second is not None and not tape.core.signals
    review = next(p for kind,p in tape.events if kind=='ASK_RESPONSE_PAIR_REVIEW')
    assert all(review['checks'].values())
    assert review['ratios']['absolute_hit']==D('.5')
    assert review['ratios']['absolute_loss']==D('1.5')


@pytest.mark.parametrize('bid_close,signals',[(.601,0),(.601001,1)])
def test_bid_pairing_gap_is_strictly_below1500ms(bid_close,signals):
    tape = full_path(bid_close=bid_close)
    assert len(tape.core.signals)==signals
    if signals:
        s = next(iter(tape.core.signals.values()))
        assert s['anchor_price']==D('.50') and s['confirmed']==2.101
        assert D(str(s['deadline']))==D(str(bid_close))+3


def test_recent_bid_and_ask_have_original_component_expiry_without_clock_reset():
    tape = full_path(bid_start=.4,bid_close=.901)
    s = next(iter(tape.core.signals.values()))
    assert s['deadline']==3.901 and len(s['component_ids'])==3
    tape.close(3.901)
    assert not tape.core.signals


def test_reference_is_historical_and_does_not_require_a_weak_r1():
    tape = Tape()
    tape.trade(1.,'BUY',40)
    tape.depth(1.02,'ASK',90)
    tape.depth(1.30,'ASK',100)
    tape.close(1.501)
    assert tape.core.roots['UP'].reference['r']==1
    second_ask(tape)
    assert tape.core.roots['UP'].second is not None


@pytest.mark.parametrize('changes,reason',[
    ({'low':90,'end':91},'FIRST_ASK2_COMPARISON_FAILED'),
    ({'low':70,'end':75},'FIRST_ASK2_COMPARISON_FAILED'),
    ({'quantity':30},'FIRST_ASK2_COMPARISON_FAILED'),
    ({'end':90},'FIRST_ASK2_COMPARISON_FAILED')])
def test_first_a2_failure_is_not_replaced_with_a_better_third_round(changes,reason):
    tape = Tape()
    first_ask(tape)
    reference = tape.core.roots['UP'].reference['id']
    second_ask(tape,**changes)
    assert 'UP' not in tape.core.roots and not tape.core.signals
    assert any(kind=='ASK_ROOT_ENDED' and p['root_id']==reference and p['reason'] in
               {reason,'EMPTY_QUANTITY_INTERVAL'} for kind,p in tape.events)


def test_ask_ceiling_equal_ends_existing_signal_but_does_not_erase_history():
    tape = full_path(bid_start=.4,bid_close=.901)
    s = next(iter(tape.core.signals.values()))
    tape.depth(2.2,'ASK',D('92.5'))
    assert not tape.core.signals
    assert any(kind=='SIGNAL_ENDED' and p['id']==s['id'] for kind,p in tape.events)


def test_bid_floor_equal_is_valid_but_a_strict_break_ends_the_signal():
    tape = full_path(bid_start=.4,bid_close=.901)
    tape.depth(2.2,'BID',88)
    assert tape.core.signals
    tape.depth(2.21,'BID',D('87.999'))
    assert not tape.core.signals


def test_missing_terminal_is_unknown_even_when_later_trade_is_too_small_to_qualify():
    tape = full_path(bid_start=.4,bid_close=.901)
    tape.trade(2.2,'BUY','.1')
    tape.close(2.701)
    assert not tape.core.signals
    assert any(kind=='ROUND_UNKNOWN' and p['reason']=='TERMINAL_ANCHOR_QUANTITY_UNAVAILABLE'
               for kind,p in tape.events)


def test_generation_and_resource_release_clear_every_retained_path():
    tape = full_path(bid_start=.4,bid_close=.901)
    tape.trade(2.2,'BUY',1)
    tape.core.new_generation('UP',2)
    assert not tape.core.signals and not tape.core.roots and not tape.core.components
    assert not tape.core.rounds and not tape.core.closing_rounds
    tape.core.release()
    assert not tape.core.root_ends and not tape.core.pending_bids


def test_original_contract_does_not_accept_a_different_round_or_hold_time():
    with pytest.raises(ValueError,match='ORIGINAL_STRICT_ROUND'):
        M19(lambda *_:None,Parameters(round_seconds=.25))


def test_preconfirmation_endpoint_print_does_not_seed_or_delay_the_first_new_a2():
    tape = Tape()
    tape.trade(1.,'BUY',40)
    tape.depth(1.02,'ASK',90)
    tape.depth(1.30,'ASK',94)
    tape.trade(1.5,'BUY',1)
    assert len(tape.core.closing_rounds)==1
    tape.close(1.501)
    assert tape.core.roots['UP'].second_id is None
    tape.depth(1.55,'ASK',100)
    tape.trade(1.6,'BUY',20)
    current = tape.core.rounds['UP','ASK',D('.52')]
    assert current.role=='ASK_SECOND' and current.path.start==D('1.6')
    assert current.path.hits==20
