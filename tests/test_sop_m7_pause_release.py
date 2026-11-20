"""Full raw M7 sequence, private occupancy release and original deadlines."""
import hashlib
import json
from decimal import Decimal as D
import pytest

from sop_signals.legacy_variant_feed import LegacyVariantFeed
from sop_signals.m7 import Parameters


class Wire:
    def __init__(self):
        self.rows=[];self.seq=0
        self.f=LegacyVariantFeed('condition',('yes','no'),{'yes':'.01','no':'.01'},
            lambda k,v:self.rows.append((k,v)),Parameters(),strategy='M7')
        self.f.connect(now=0,wall=1000,seq=0)

    def send(self,t,raw):
        self.seq+=1
        values=raw if isinstance(raw,list) else [raw]
        values=[{'market':'condition','timestamp':str(round((1000+t)*1000)),**r} for r in values]
        self.f.apply(values,now=t,wall=1000+t,received=t,seq=self.seq)

    def timer(self,t):
        self.seq+=1;self.f.timer(now=t,wall=1000+t,seq=self.seq)

    def book(self):
        self.send(0,[{'event_type':'book','asset_id':'yes',
            'bids':[{'price':'.50','size':'100'}],'asks':[{'price':'.52','size':'100'}]},
            {'event_type':'book','asset_id':'no',
            'bids':[{'price':'.48','size':'100'}],'asks':[{'price':'.50','size':'100'}]}])

    def bbo(self,t):
        self.send(t,[{'event_type':'best_bid_ask','asset_id':'yes','best_bid':'.50','best_ask':'.52'},
                     {'event_type':'best_bid_ask','asset_id':'no','best_bid':'.48','best_ask':'.50'}])

    def trade(self,t,side,quantity,identity):
        self.send(t,{'event_type':'last_trade_price','asset_id':'yes','side':side,
            'price':'.52' if side=='BUY' else '.50','size':str(quantity),
            'transaction_hash':'0x'+hashlib.sha256(identity.encode()).hexdigest()})

    def depth(self,t,q):
        self.send(t,{'event_type':'price_change','price_changes':[
            {'asset_id':'yes','side':'BUY','price':'.50','size':str(q)},
            {'asset_id':'no','side':'SELL','price':'.50','size':str(q)}]})

    def prepare(self,*,pause_first=40,support=True,late_low=False,finish=True):
        self.book()
        actions={.01:lambda:self.trade(.01,'BUY',40,'ap1-a'),
            .30:lambda:self.trade(.30,'BUY',30,'ap1-b'),
            .60:lambda:self.trade(.60,'BUY',30,'ap1-c'),
            .761:lambda:self.timer(.761),
            .80:lambda:self.trade(.80,'BUY',pause_first,'pause-a'),
            1.05:lambda:self.trade(1.05,'BUY',5,'pause-b'),
            1.262:lambda:self.timer(1.262),
            1.30:lambda:self.trade(1.30,'BUY',20,'ap2-a'),
            1.60:lambda:self.trade(1.60,'BUY',20,'ap2-b'),
            1.90:lambda:self.trade(1.90,'BUY',20,'ap2-c'),
            2.051:lambda:self.timer(2.051)}
        if support:
            actions.update({.82:lambda:self.trade(.82,'SELL',10,'support'),
                .84:lambda:self.depth(.84,90),.88:lambda:self.depth(.88,95),
                .922:lambda:self.timer(.922)})
        if late_low:actions[1.70]=lambda:self.depth(1.70,91)
        if not finish:actions.pop(2.051)
        # Shared source observers have their own frozen close deadlines too.
        # Keep actual covered inputs within25ms of every boundary; do not
        # confuse a sparse artificial clock with the selected M7 conditions.
        for i in range(1,82):
            t=round(i*.025,3)
            actions.setdefault(t,lambda t=t:self.bbo(t))
        for t,action in sorted(actions.items()):action()


def test_pause_bin_over35pct_is_diagnostic_and_ap1_occupancy_releases_early():
    w=Wire();w.prepare();signals=w.f.core.signals
    assert len(signals)==1
    fact=next(iter(signals.values()));features=fact['feature_values']
    assert fact['token']=='yes' and D(fact['anchor_price'])==D('.50')
    assert not features['diagnostic_pause_bin_cap_satisfied']
    assert features['ap1_occupancy_exception_used']
    assert fact['confirmed']==pytest.approx(2.051) and fact['deadline']==pytest.approx(2.801)
    campaign=w.f.manager.active('M7','yes')
    assert campaign.data['ap1'].deadline==pytest.approx(1.511)
    assert campaign.data['ap2_start_key'][0]==1.30<campaign.data['ap1'].deadline
    assert campaign.data['rf'].confirmed<campaign.data['ex'].confirmed
    assert w.f.counts['LEGACY_M7_PAUSE_RELEASE_WATCH_STARTED']==1


def test_original_positive_anchor_guard_does_not_add_a_common40pct_floor():
    w=Wire();w.prepare(late_low=True)
    assert len(w.f.core.signals)==1 and w.f.books['yes'].bids[D('.50')]==91


def test_total_pause_above50pct_still_fails():
    w=Wire();w.prepare(pause_first=46)
    assert not w.f.core.signals
    reasons=[v['payload'].get('reason') for k,v in w.rows if k=='SOP_EVIDENCE']
    assert 'PAUSE_TOTAL_NOT_CONFIRMED' in reasons


def test_total_pause_exactly50pct_remains_allowed():
    w=Wire();w.prepare(pause_first=45)
    assert len(w.f.core.signals)==1
    campaign=w.f.manager.active('M7','yes')
    assert campaign.data['ex'].values['total_buy']==50


def test_ap2_never_substitutes_for_missing_support():
    w=Wire();w.prepare(support=False)
    assert not w.f.core.signals
    w.timer(2.801);assert not w.f.core.signals


def test_original_joint_deadline_equality_and_runtime_release():
    w=Wire();w.prepare();deadline=next(iter(w.f.core.signals.values()))['deadline']
    w.timer(deadline);assert not w.f.core.signals
    w.f.release_runtime();assert not w.f.core.signals and not w.f.legacy.registry.facts


def test_complementary_retransmission_does_not_double_economic_view():
    w=Wire();w.book()
    tx='0x'+'a'*64
    rows=[{'event_type':'last_trade_price','asset_id':'yes','side':'BUY','price':'.52','size':'2','transaction_hash':tx},
          {'event_type':'last_trade_price','asset_id':'no','side':'SELL','price':'.48','size':'2','transaction_hash':tx}]
    w.send(.01,rows)
    assert len(w.f.flows['yes'].trades)==len(w.f.flows['no'].trades)==1
    assert w.f.legacy.counts['DUPLICATE_ECONOMIC_VIEW_IGNORED']==2


def test_support_can_finish_after_ap2_without_renewing_ap2_deadline():
    w=Wire();w.prepare(support=False)
    assert not w.f.core.signals
    w.bbo(2.075);w.trade(2.1,'SELL',10,'late-support')
    w.depth(2.12,90);w.bbo(2.14);w.depth(2.16,95);w.bbo(2.18);w.timer(2.201)
    assert len(w.f.core.signals)==1
    fact=next(iter(w.f.core.signals.values()))
    assert fact['confirmed']==pytest.approx(2.201) and fact['deadline']==pytest.approx(2.801)


def test_support_available_exactly_at_ap2_deadline_cannot_publish():
    w=Wire();w.prepare(support=False)
    for i in range(83,109):w.bbo(round(i*.025,3))
    w.trade(2.701,'SELL',10,'support-at-deadline')
    w.depth(2.72,90);w.bbo(2.74);w.depth(2.76,95)
    w.bbo(2.78);w.bbo(2.80);w.timer(2.801)
    assert not w.f.core.signals and w.f.core.counts['SIGNAL_CONFIRMED']==0


def test_complete_parent_anchor_loss_prevents_first_joint_publication():
    w=Wire();w.prepare(finish=False)
    w.send(2.051,[{'event_type':'best_bid_ask','asset_id':'yes','best_bid':'.50','best_ask':'.52'},
        {'event_type':'price_change','price_changes':[
            {'asset_id':'yes','side':'BUY','price':'.50','size':'0'},
            {'asset_id':'yes','side':'BUY','price':'.50','size':'95'}]}])
    assert not w.f.core.signals and w.f.core.counts['SIGNAL_CONFIRMED']==0
