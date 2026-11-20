"""Artificial causal contracts; never count these as market frequency."""
from decimal import Decimal as D
import pytest
from test_sop_m7_pause_release import Wire
from sop_signals.legacy_variant_feed import LegacyVariantFeed
from sop_signals.m9 import Parameters

class M9Wire(Wire):
    def __init__(self):
        self.rows=[];self.seq=0;self.events=[]
        self.f=LegacyVariantFeed('condition',('yes','no'),{'yes':'.01','no':'.01'},lambda k,v:self.rows.append((k,v)),Parameters(),strategy='M9')
        self.f.connect(now=0,wall=1000,seq=0)
    def send(self,t,raw):
        from copy import deepcopy
        self.events.append((t,'FRAME',deepcopy(raw)));return super().send(t,raw)
    def timer(self,t):
        self.events.append((t,'POLL',None));return super().timer(t)
    def prepare(self,*,late_support=False,prepair_failure=False,q_trade=10,post_q=70,loss=True,split_qualification=False,finish=True,ex_first=35):
        self.book()
        a={.01:lambda:self.trade(.01,'SELL',40,'ap-a'),.30:lambda:self.trade(.30,'SELL',30,'ap-b'),
           .511:lambda:self.timer(.511),.60:lambda:self.trade(.60,'SELL',30,'ap-c'),.761:lambda:self.timer(.761),
           .80:lambda:self.trade(.80,'SELL',ex_first,'ex-a'),1.05:lambda:self.trade(1.05,'SELL',5,'ex-b'),
           1.30:lambda:self.trade(1.30,'SELL',5,'ex-c'),1.512:lambda:self.timer(1.512)}
        if not late_support:a.update({.03:lambda:self.depth(.03,90),.07:lambda:self.depth(.07,95)})
        if late_support:
            a.update({2.40:lambda:self.trade(2.40,'SELL',10,'seed-late'),2.42:lambda:self.depth(2.42,90),
                      2.46:lambda:self.depth(2.46,95),2.901:lambda:self.timer(2.901),
                      3.:lambda:self.trade(3.,'SELL',10,'retest-late'),3.02:lambda:self.depth(3.02,85),
                      3.06:lambda:self.depth(3.06,90),3.501:lambda:self.timer(3.501)})
            if prepair_failure:a[1.70]=lambda:self.trade(1.70,'SELL',50,'irreversible')
            last=3.501
        else:
            a.update({1.60:lambda:self.trade(1.60,'SELL',q_trade,'retest'),2.101:lambda:self.timer(2.101)})
            if loss:a.update({1.62:lambda:self.depth(1.62,85),1.66:lambda:self.depth(1.66,90)})
            if split_qualification:a[1.63]=lambda:self.trade(1.63,'SELL',D('39.9'),'qualified-at50')
            if post_q:a[1.70]=lambda:self.trade(1.70,'SELL',post_q,'post-q')
            if not finish:a.pop(2.101)
            last=2.101 if finish else 2.075
        for i in range(1,int(last/.025)+1):
            t=round(i*.025,3);a.setdefault(t,lambda t=t:self.bbo(t))
        for t,fn in sorted(a.items()):fn()
        return self

def reasons(w):
    return [v['payload'].get('reason') for k,v in w.rows if k=='SOP_EVIDENCE']

def test_total_ex_bin_diagnostic_and_post_handoff50_does_not_veto95():
    w=M9Wire().prepare();assert len(w.f.core.signals)==1
    f=next(iter(w.f.core.signals.values()));v=f['feature_values']
    assert not v['diagnostic_ex_bin_cap_satisfied'] and v['ex_role']=='HISTORY'
    assert v['ex_proxy_created_at']==pytest.approx(1.512)
    assert v['ex_proxy_deadline']==pytest.approx(4.512)
    assert v['first_qualified_key'][0]==pytest.approx(1.62)
    assert v['handoff_prechecks']['rolling_sell']==20
    assert f['confirmed']==pytest.approx(2.1) and f['deadline']==pytest.approx(10.1)
    assert 'EX_PRESSURE_RESUMED' in reasons(w)

def test_pre_handoff50_equality_wins_before_joint_hl_on_same_trade():
    w=M9Wire().prepare(q_trade=D('.1'),split_qualification=True,post_q=0)
    assert not w.f.core.signals
    assert not any(v['event']=='LEGACY_M9_EX_HANDOFF' for k,v in w.rows if k=='SOP_EVIDENCE')
    assert 'SEED_EX_PRESSURE_RESUMED' in reasons(w)

def test_pre_handoff50_before_loss_is_not_rescued_by_later_depth():
    w=M9Wire().prepare(q_trade=40,post_q=0)
    assert not w.f.core.signals and 'SEED_EX_PRESSURE_RESUMED' in reasons(w)

def test_post_handoff95_equality_ends_even_with_good_recovery():
    w=M9Wire().prepare(post_q=75)
    assert not w.f.core.signals and 'RETEST_RESIDUAL_PRESSURE_RESUMED' in reasons(w)

@pytest.mark.parametrize('q_trade,loss',[(10,False),(D('.1'),True)])
def test_only_hit_or_only_loss_does_not_handoff_and_first_unqualified_is_terminal(q_trade,loss):
    w=M9Wire().prepare(q_trade=q_trade,loss=loss,post_q=0)
    assert not w.f.core.signals and 'FIRST_RETEST_ROUND_UNQUALIFIED' in reasons(w)
    assert not any(v['event']=='LEGACY_M9_EX_HANDOFF' for k,v in w.rows if k=='SOP_EVIDENCE')

def test_ex_proxy_born_before_late_support_and_native750_ttl_is_not_pairing_limit():
    w=M9Wire().prepare(late_support=True)
    assert len(w.f.core.signals)==1
    f=next(iter(w.f.core.signals.values()));v=f['feature_values']
    assert v['paired_after_native_ex_ttl']
    assert v['ex_proxy_created_at']==pytest.approx(1.512) and v['ex_proxy_deadline']==pytest.approx(4.512)
    assert v['seed_key'][0]==pytest.approx(2.9)
    assert v['seed_original_deadline']==pytest.approx(4.512)

def test_pre_pair_failure_is_terminal_even_when_pressure_later_falls():
    w=M9Wire().prepare(late_support=True,prepair_failure=True)
    first=next(iter(w.f.manager.ex_proxies.values()))
    assert first['ended'] and first['end_reason']=='EX_PRESSURE_RESUMED'
    assert first['end_key'][0]==pytest.approx(1.70)
    assert first['proxy'].id not in w.f.manager.current_proxies
    assert not w.f.core.signals
    assert len({v['source'].id for v in w.f.manager.ex_proxies.values()})==len(w.f.manager.ex_proxies)

def test_history_quality_loss_still_ends_confirmed_signal_and_release_clears_proxy():
    w=M9Wire().prepare();assert w.f.core.signals
    w.f.unavailable('yes','ARTIFICIAL_HISTORY_SOURCE_UNPROVEN')
    assert not w.f.core.signals
    w.f.release_runtime();assert not w.f.manager.ex_proxies and not w.f.legacy.registry.facts
    assert not w.f.manager.current_proxies

def test_full_parent_anchor_loss_suppresses_first_publication():
    w=M9Wire().prepare(finish=False)
    w.send(2.1,[{'event_type':'price_change','price_changes':[
        {'asset_id':'yes','side':'BUY','price':'.50','size':'0'},
        {'asset_id':'yes','side':'BUY','price':'.50','size':'90'}]}])
    assert not w.f.core.signals and w.f.core.counts['SIGNAL_CONFIRMED']==0

def test_new_large_ap_after_handoff_is_historical_regime_not_hidden_veto():
    w=M9Wire().prepare();original=next(iter(w.f.core.signals.values()))
    for i in range(85,97):
        t=round(i*.025,3)
        if t==2.2:w.trade(t,'SELL',5,'new-ap-tail')
        else:w.bbo(t)
    assert len(w.f.core.signals)==1
    assert next(iter(w.f.core.signals.values()))['deadline']==original['deadline']
    aps=[v for k,v in w.rows if k=='SOP_EVIDENCE' and v['event']=='LEGACY_FACT_CONFIRMED'
         and v['payload']['kind']=='AP_SELL' and v['payload']['token']=='yes']
    assert len(aps)>=2

def test_subsequent_qualified_failed_round_still_ends_history_handoff_signal():
    w=M9Wire().prepare();assert w.f.core.signals
    for i in range(85,109):
        t=round(i*.025,3)
        if t==2.2:w.trade(t,'SELL',1,'bad-followup')
        elif t==2.225:w.depth(t,85)
        else:w.bbo(t)
    assert not w.f.core.signals and 'QUALIFIED_REFILL_FAILED' in reasons(w)


def test_unavailable_ex_formation_is_explicit_unknown_in_frequency_audit():
    w=M9Wire()
    w.f.event('RESEARCH_EVIDENCE',{'event':'M9_LOCAL_EX_UNAVAILABLE',
        'payload':{'token':'yes','reason':'EX_INPUT_BACKLOG','ap_id':'fixture-only'}})
    assert w.f.core.counts['SEED_UNKNOWN']==1
    assert not w.f.core.signals


@pytest.mark.parametrize('first,allowed',[(39,True),(40,False)])
def test_ex_total49_equality_allowed_but_above49_still_rejected(first,allowed):
    w=M9Wire().prepare(ex_first=first)
    assert bool(w.f.core.signals) is allowed
    assert bool(w.f.manager.ex_proxies) is allowed


def test_joint_hl_at_original_proxy_deadline_cannot_handoff_or_renew():
    template=M9Wire().prepare(late_support=True)
    w=M9Wire()
    for t,kind,raw in template.events:
        if t>2.901:break
        if kind=='FRAME':w.send(t,raw)
        else:w.timer(t)
    campaign=w.f.manager.active('M9','yes')
    assert campaign
    deadline=campaign.data['seed_deadline']
    for i in range(117,181):w.bbo(round(i*.025,3))
    w.trade(4.50,'SELL',10,'qualification-deadline')
    # Test the frozen clock value itself, rather than a separately rounded
    # display literal differing from it by a binary floating-point ulp.
    w.depth(deadline,85)
    assert not w.f.core.signals and 'SEED_TTL_EXPIRED' in reasons(w)
    assert not any(v['event']=='LEGACY_M9_EX_HANDOFF' for k,v in w.rows if k=='SOP_EVIDENCE')
