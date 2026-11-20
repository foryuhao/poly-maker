"""Selected SOP boundaries through real formation paths, parser and native identity."""
from copy import deepcopy
from decimal import Decimal as D

import pytest

from sop_signals.feed import Feed
from sop_signals.m12 import parameter_identity
from sop_signals.m12_overlap import (M12Overlap, Parameters, VARIANT, SOP_SHA256,
                                    DOCUMENT_PARAMETERS_SHA256)
from sop_signals.parameters import load_revision
from test_sop_m12 import Tape as BaselineTape


class Tape(BaselineTape):
    def __init__(self):
        self.events, self.seq = [], 0
        self.s = M12Overlap(lambda kind, payload: self.events.append((kind, payload)))

    def one_side(self, t, side, *, close=True, quantity="10"):
        self.hit(t, "SELL" if side == "BID" else "BUY", quantity=quantity)
        self.depth(t + .01, side, "99" if side == "BID" else "80")
        self.depth(t + .3, side, "99.8" if side == "BID" else "84")
        if close:
            self.close(t + .501)


@pytest.mark.parametrize("first", ["BID", "ASK"])
@pytest.mark.parametrize("gap", [0.8, 1.5, 2.2, 2.9])
def test_live_overlap_allows_both_orders_and_original_expiry(first, gap):
    t = Tape()
    t.one_side(1, first)
    t.one_side(1 + gap, "ASK" if first == "BID" else "BID")
    signal = next(iter(t.s.signals.values()))
    assert signal['deadline'] == pytest.approx(4.501)
    assert signal['diagnostics']['gap_ms'] == pytest.approx(gap * 1000)
    assert signal['diagnostics']['remaining_lifetime_seconds'] == pytest.approx(3 - gap)
    assert signal['diagnostics']['normalized_loss_ratio'] == 20  # Former d gate rejects.
    assert signal['variant_id'] == VARIANT


@pytest.mark.parametrize("gap", [3., 3.01])
def test_expiry_equality_or_later_never_confirms(gap):
    t = Tape(); t.one_side(1, "BID"); t.one_side(1 + gap, "ASK")
    assert not t.s.signals


@pytest.mark.parametrize("side,q,valid", [("BID", "99.4", True),
    ("BID", "99.39", False), ("ASK", "90", False), ("ASK", "89.99", True)])
def test_waiting_guard_equality_and_no_recovery_revival(side, q, valid):
    t = Tape(); t.one_side(1, side)
    t.depth(2., side, q)
    t.depth(2.1, side, "99.8" if side == "BID" else "84")
    t.one_side(3.2, "ASK" if side == "BID" else "BID")
    assert bool(t.s.signals) is valid


@pytest.mark.parametrize("hit,valid", [("1", True), ("100", True), (".99", False), ("101", False)])
def test_u_comparability_endpoints_remain_real_gates(hit, valid):
    t = Tape(); t.one_side(1, "BID"); t.one_side(1.8, "ASK", quantity=hit)
    assert bool(t.s.signals) is valid


def test_very_small_bid_loss_has_only_own_materiality_floor():
    t = Tape(); t.hit(1., "SELL"); t.depth(1.01, "BID", "99.5")
    t.depth(1.3, "BID", "99.8"); t.close(); t.one_side(3.2, "ASK")
    assert t.s.signals  # dB=.005; dA/dB=40 is deliberately not a gate.


def test_complete_parent_invalidation_precedes_final_publish():
    t = Tape(); t.one_side(1, "BID"); t.one_side(3.2, "ASK", close=False)
    t.at(3.701); t.s.close(3.701, lambda *_: True)
    t.s.depth("yes", "ASK", D('.42'), D('90'), 3.701)
    t.s.depth("yes", "ASK", D('.42'), D('84'), 3.701)
    t.s.publish(); assert not t.s.signals


def test_repeated_publish_does_not_reuse_ask_and_quality_ends_evidence():
    t = Tape(); t.one_side(1, "BID"); t.one_side(3.2, "ASK")
    t.s.publish(); assert t.s.counts['SIGNAL_CONFIRMED'] == 1
    t.s.invalidate('yes', 'SOURCE_GAP'); assert not t.s.signals and not t.s.components


def document():
    return {'strategy':'M12','variant_id':VARIANT,'sop_sha256':SOP_SHA256,
            'document_parameters_sha256':DOCUMENT_PARAMETERS_SHA256,
            'revision_round':0,'pnl_used_for_selection':False,
            'parameters':parameter_identity(Parameters())[0]}


@pytest.mark.parametrize('field', ['sop_sha256','variant_id','document_parameters_sha256',
                                  'pnl_used_for_selection','revision_round','parameters'])
def test_explicit_entry_cannot_silently_fall_back_or_restore_old_filters(field):
    d = document(); d[field] = {'pair_gap':1.5} if field == 'parameters' else 'wrong'
    with pytest.raises(ValueError): load_revision(d)


def test_parser_and_definition_select_explicit_identity():
    from m1223_sim import definition
    p, record = load_revision(document())
    f = Feed('c',('yes','no'),{'yes':'.01','no':'.01'},lambda *_:None,parameters=p)
    d = definition(p)
    assert isinstance(f.core, M12Overlap) and d['variant_id'] == VARIANT
    assert d['parameter_sha256'] == record['parameter_sha256']
    assert d['selected_sop_sha256'] == SOP_SHA256


def test_public_source_reuse_requires_explicit_target_and_unchanged_source_identity():
    import hashlib,json
    from m1223_replay import replay_initial_event
    from m1223_sim import definition
    from m1234_fixtures import fixture_events
    from sop_signals.m19 import Parameters as M19Parameters
    initial=deepcopy(fixture_events()[0]);old=definition(M19Parameters())
    initial['definition']['settings'].update(profiles={},sop=old)
    p, rev=load_revision(document())
    with pytest.raises(ValueError,match='MIX_STRATEGY'):
        replay_initial_event(initial,p,{},rev)
    proof={'mode':'PUBLIC_INPUT_ONLY_NEW_STRATEGY_LEDGER','source_strategy':'M19',
           'source_sop_sha256':hashlib.sha256(json.dumps(old,sort_keys=True).encode()).hexdigest(),
           'target_strategy':'M12','target_variant':VARIANT}
    new=replay_initial_event(initial,p,{'public_input_reuse':proof},rev)
    assert new['definition']['settings']['sop']['variant_id']==VARIANT
    assert new['definition']['settings']['sop_replay']['source_sop_definition']==old
    proof['source_sop_sha256']='bad'
    with pytest.raises(ValueError,match='MIX_STRATEGY'):
        replay_initial_event(initial,p,{'public_input_reuse':proof},rev)


def test_diagnostics_does_not_restore_gap_or_loss_ratio_filters(tmp_path):
    import sqlite3
    from m1223_sim import definition
    from m1223_pair_diagnostics import diagnose
    from polymaker.m1234_sim.events import encode
    t=Tape();t.one_side(1,'BID');t.one_side(3.2,'ASK')
    database=tmp_path/'simulation.sqlite3'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE audit(seq INTEGER PRIMARY KEY,input_seq INTEGER,kind TEXT,payload TEXT)')
        db.execute('INSERT INTO audit VALUES(1,1,?,?)',('DEFINITION',encode({'definition':{
            'market':{'slug':'artificial-diagnostic-case'},'settings':{'sop':definition(Parameters())}}})))
        for n,(kind,fact) in enumerate(t.events,2):
            if kind=='SIGNAL_CONFIRMED':continue  # Diagnose a missing publication, not a synthetic success.
            db.execute('INSERT INTO audit VALUES(?,?,?,?)',(n,fact['key'][1],'SOP_EVIDENCE',
                encode({'event':kind,'payload':fact})))
    r=diagnose(database)
    assert r['outcomes']=={'ELIGIBLE_PAIR_WITHOUT_RECORDED_SIGNAL_REQUIRES_REVIEW':1}
    assert not r['pair_gap_filter_enabled'] and not r['cross_side_loss_ratio_filter_enabled']
