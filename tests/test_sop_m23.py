"""New M23 mechanics and causal boundaries; artificial paths,not market samples."""
from decimal import Decimal as D

import pytest
from sop_signals.m23 import M23, Parameters


class Tape:
    def __init__(self):
        self.events=[];self.seq=0;self.q=D(150)
        self.s=M23(lambda k,p:self.events.append((k,p)))

    def at(self,t):
        self.seq+=1;self.s.clock(t,self.seq)

    def seed(self,t=1.):
        self.at(t);self.s.depth('yes','BID',D('.4'),D(150),t,generation=1,
            input_ref=(self.seq,0,0),source=t,kind='BID_PRICE_CHANGE',
            previous_quantity=D(100),previous_quantity_ref=(t-.01,t-.01,(0,0,0)),
            pre_event_best_bid=D('.4'),pre_event_valid=True)

    def close(self,t,publish=True,covered=True):
        self.at(t);self.s.close(t,lambda *_:covered)
        if publish:self.s.publish()

    def prepared(self):
        self.seed();self.close(1.101)

    def sell(self,t,q='30',received=None,token='yes',p='.4',source=None):
        self.at(t);received=t if received is None else received
        self.s.observe_direct_trade(token,'SELL',D(p),received)
        self.s.trade(token=token,side='SELL',price=D(p),quantity=D(q),received=received,
            q0=self.q,generation=1,input_ref=(self.seq,0,0),source=t if source is None else source,
            initial_quantity_ref=(1.,1.,(1,0,0)))

    def depth(self,t,q,received=None,source=None,token='yes'):
        self.at(t);self.q=D(q);self.s.depth(token,'BID',D('.4'),D(q),t if received is None else received,
            generation=1,input_ref=(self.seq,0,0),source=t if source is None else source,kind='BID_PRICE_CHANGE')


@pytest.mark.parametrize('q,loss',[('145','5'),('110','40'),('160','0')])
def test_buffer_not_rf_accepts_low_zero_and_more_than_sell_loss(q,loss):
    t=Tape();t.prepared();t.sell(2.301);t.depth(2.35,q);t.close(2.802)
    x=next(iter(t.s.signals.values()));assert x['test']['loss']==D(loss)
    assert x['deadline']==pytest.approx(4.101) and x['guard_floor']==110
    assert not x['diagnostics']['first_sell_within_750ms']


@pytest.mark.parametrize('wait',[.2,.75,1.2,2.4])
def test_first_sell_has_no_750ms_gate_and_short_signal_is_still_a_signal(wait):
    t=Tape();t.prepared();c=t.s.roots['yes'].seed_confirmed;r=c+wait
    t.sell(r);t.depth(r+.1,'145');t.close(r+.52)
    x=next(iter(t.s.signals.values()))
    assert x['deadline']==pytest.approx(c+3)
    assert x['diagnostics']['first_sell_within_750ms'] is (wait<.75)
    if wait==2.4:assert x['deadline']-x['confirmed']==pytest.approx(.08)


def test_750ms_wait_does_not_clear_or_release_seed():
    t=Tape();t.prepared();r=t.s.roots['yes'];t.at(r.seed_confirmed+.75)
    assert t.s.roots['yes'] is r and r.phase=='WAIT_FIRST_SELL'


@pytest.mark.parametrize('wait',[2.5,2.6,3.])
def test_original_seed_expiry_and_full500ms_cannot_be_extended(wait):
    t=Tape();t.prepared();r=1.101+wait;t.sell(r);t.depth(r+.1,'145');t.close(r+.501)
    assert not t.s.signals


def test_original_first_tiny_print_starts_window_and_cannot_be_skipped():
    t=Tape();t.prepared();t.sell(1.2,'.01');t.depth(1.3,'145');t.close(1.701)
    assert not t.s.roots and not t.s.signals
    t.sell(1.8,'30');t.depth(1.9,'145');t.close(2.301)
    assert not t.s.signals


def test_moving_materiality_threshold_does_not_publish_before_full_window():
    t=Tape();t.prepared();t.sell(1.2,'30');t.depth(1.3,'145');t.close(1.69)
    assert not t.s.signals;t.close(1.701);assert t.s.signals


@pytest.mark.parametrize('stage,q,valid', [('pre','140',True),('pre','139.99',False),
                                         ('test','110',True),('test','109.99',False)])
def test_fpre_f_equality_and_no_restoration_rescue(stage,q,valid):
    t=Tape();t.prepared()
    if stage=='pre':t.depth(1.15,q);t.depth(1.16,'150')
    t.sell(1.2)
    t.depth(1.3,q if stage=='test' else '145');t.depth(1.31,'145');t.close(1.701)
    assert bool(t.s.signals) is valid


@pytest.mark.parametrize('when',[1.,1.05,1.101])
def test_sell_at_seed_or_publication_boundary_is_not_new_challenge(when):
    t=Tape()
    if when==1.:t.sell(1.,'.01');t.seed(1.);t.close(1.101)
    elif when==1.05:t.seed();t.sell(1.05,'.01');t.close(1.101)
    else:t.prepared();t.sell(1.101)
    assert not t.s.roots


@pytest.mark.parametrize('terminal', ['absent','same_source','outside_window'])
def test_window_needs_explicit_post_all_sell_p_quantity_inside_window(terminal):
    t=Tape();t.prepared();t.sell(1.2)
    if terminal=='same_source':t.depth(1.3,'145',source=1.2)
    if terminal=='outside_window':t.depth(1.71,'145')
    t.close(1.72)
    assert not t.s.signals and t.s.counts['ROOT_UNKNOWN']==1


def test_later_counted_sell_invalidates_earlier_terminal_proof():
    t=Tape();t.prepared();t.sell(1.2,'10');t.depth(1.3,'145');t.sell(1.4,'20');t.close(1.701)
    assert not t.s.signals and t.s.counts['ROOT_UNKNOWN']==1


def test_late_earlier_sell_does_not_reselect_first_window():
    t=Tape();t.prepared();t.sell(1.3);t.sell(1.4,'1',received=1.2)
    assert not t.s.roots and t.s.counts['ROOT_UNKNOWN']==1


def test_complete_parent_guard_break_precedes_signal_publication():
    t=Tape();t.prepared();t.sell(1.2);t.depth(1.3,'145');t.close(1.701,publish=False)
    t.depth(1.701,'109');t.depth(1.701,'145');t.s.publish()
    assert not t.s.signals


def test_first_sell_of_other_token_cannot_test_this_preload():
    t=Tape();t.prepared();t.sell(1.2,token='no');t.close(1.701)
    assert t.s.roots['yes'].phase=='WAIT_FIRST_SELL' and not t.s.signals


@pytest.mark.parametrize('cause',['TICK_CHANGED','WS_DISCONNECTED','UNRECONSTRUCTABLE_SOURCE_ORDER'])
def test_source_failures_end_root_and_release_is_bounded(cause):
    t=Tape();t.prepared();t.s.invalidate('yes',cause)
    assert not t.s.roots and not t.s.signals;t.s.release()
    assert not any([t.s.roots,t.s.last_sell,t.s.last_end,t.s.quality])


def test_seed_and_test_lateness_or_missing_coverage_are_unknown():
    t=Tape();t.seed();t.close(1.151);assert t.s.counts['ROOT_UNKNOWN']==1
    t=Tape();t.prepared();t.sell(1.2);t.depth(1.3,'145');t.close(1.751)
    assert t.s.counts['ROOT_UNKNOWN']==1
    t=Tape();t.seed();t.close(1.101,covered=False);assert t.s.counts['ROOT_UNKNOWN']==1


def test_explicit_variant_loader_definition_and_public_reuse_identity():
    import json,hashlib
    from copy import deepcopy
    from sop_signals.m23 import VARIANT,SOP_SHA256,DOCUMENT_PARAMETERS_SHA256
    from sop_signals.m12 import parameter_identity
    from sop_signals.m19 import Parameters as OldParameters
    from sop_signals.parameters import load_revision
    from m1223_sim import definition
    from m1223_replay import replay_initial_event
    from m1234_fixtures import fixture_events
    doc={'strategy':'M23','variant_id':VARIANT,'sop_sha256':SOP_SHA256,
         'document_parameters_sha256':DOCUMENT_PARAMETERS_SHA256,'revision_round':0,
         'pnl_used_for_selection':False,'parameters':parameter_identity(Parameters())[0]}
    p,r=load_revision(doc);selected=definition(p);assert selected['strategy']=='M23'
    assert selected['variant_id']==VARIANT and selected['parameters']['first_sell_limit'] is None
    initial=deepcopy(fixture_events()[0]);old=definition(OldParameters())
    initial['definition']['settings'].update(profiles={},sop=old)
    proof={'mode':'PUBLIC_INPUT_ONLY_NEW_STRATEGY_LEDGER','source_strategy':'M19',
           'source_sop_sha256':hashlib.sha256(json.dumps(old,sort_keys=True).encode()).hexdigest(),
           'target_strategy':'M23','target_variant':VARIANT}
    rebound=replay_initial_event(initial,p,{'public_input_reuse':proof},r)
    assert rebound['definition']['settings']['sop']['groups']==['M23']
    assert rebound['definition']['settings']['sop_replay']['source_sop_definition']==old
    doc['parameters']['first_sell_limit']=.75
    with pytest.raises(ValueError):load_revision(doc)
