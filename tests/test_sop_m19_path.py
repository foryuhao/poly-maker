"""M19 direct-side and endpoint evidence, independent of strategy predicates."""
from decimal import Decimal as D

import pytest

from sop_signals.m19_path import AnchorRoundPath, Observation


def point(side,t,q,frame,*,source=None):
    return Observation(side,'UP',D('.52'),1,t,t,frame,0,0,
                       100+t if source is None else source,D(str(q)))


def ask():
    path = AnchorRoundPath(token='UP',side='ASK',price=D('.52'),generation=1,start=1.,q0=100)
    path.observe(point('DIRECT_BUY',1.,40,1))
    path.observe(point('ASK_PRICE_CHANGE',1.02,90,2))
    path.observe(point('ASK_PRICE_CHANGE',1.3,94,3))
    return path


def finish(path,**changes):
    return path.finish(**{'available':1.501,'watermark':1.501,'coverage_proven':True,**changes})


def test_ask_direct_buy_and_explicit_terminal_yield_a_nonstable_but_proven_path():
    result = finish(ask())
    assert result['data_proven'] and result['qualified'] and not result['stable']
    assert result['side']=='ASK' and result['hits']==40 and result['loss']==10
    assert result['recovery']==D('.4') and result['terminal_quantity_proven']


def test_foreign_trade_direction_or_depth_side_cannot_supply_ask_evidence():
    path = ask()
    assert path.observe(point('DIRECT_SELL',1.4,100,4)) is False
    assert path.observe(point('BID_BOOK',1.41,100,5)) is False
    assert finish(path)['hits']==40


def test_post_window_high_is_kept_even_if_quantity_recovers_before_publication():
    path = ask()
    path.observe(point('ASK_PRICE_CHANGE',1.5,100,4))
    path.observe(point('ASK_PRICE_CHANGE',1.5001,91,5))
    result = finish(path)
    assert result['qend']==94 and result['post_window_max']==100
    assert result['post_window_min']==91


@pytest.mark.parametrize('source,proven',[(101.49,False),(101.4901,True)])
def test_terminal_quantity_source_must_be_after_the_last_direct_buy(source,proven):
    path = ask()
    path.observe(point('DIRECT_BUY',1.49,1,4))
    path.observe(point('ASK_BOOK',1.499,94,5,source=source))
    assert finish(path)['terminal_quantity_proven'] is proven


@pytest.mark.parametrize('changes',[{'available':1.550001,'watermark':1.550001},
                                   {'coverage_proven':False},{'watermark':1.5}])
def test_missing_coverage_late_close_and_endpoint_watermark_remain_unknown(changes):
    assert finish(ask(),**changes)['status']=='UNKNOWN'


def test_original_500ms_cannot_silently_become_a_shorter_ask_window():
    with pytest.raises(ValueError,match='ORIGINAL_500MS'):
        AnchorRoundPath(token='UP',side='ASK',price=D('.52'),generation=1,start=1.,q0=100,round_seconds=.25)
