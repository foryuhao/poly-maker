"""Original M17 parameter identity and explicit, bounded numeric revisions."""

from decimal import Decimal as D
import json
from pathlib import Path
import re

import pytest
from m1223_sim import definition
from sop_signals.m12 import parameter_identity
from sop_signals.m17 import Parameters
from sop_signals.parameters import load_revision,strategy_from_parameters


def baseline():
    return {"strategy":"M17","revision_round":0,"parameters":{},"pnl_used_for_selection":False}


def test_original_sop_values_and_frozen_execution_layers_are_explicitly_bound():
    p,r=load_revision(baseline())
    root=Path(__file__).resolve().parents[1]
    sop=Path.cwd()/'documents/research/signals/M17.md'
    if not sop.exists():sop=root/'research/signals/M17.md'
    d=json.loads(re.search(r'```json\s*(.*?)```',sop.read_text(),re.S).group(1))
    assert p.window_seconds*1000==d['window_ms'] and p.subwindow_seconds*1000==d['subwindow_ms']
    assert p.recheck_seconds*1000==d['same_level_post_trade_recheck_max_ms']
    assert p.stable_seconds*1000==d['terminal_continuous_hold_ms'] and p.ttl*1000==d['post_confirmation_ttl_ms']
    for name in ['minimum_sell_shares','minimum_sell_to_initial_depth','max_single_sell_fraction',
                 'minimum_visible_to_initial_depth','terminal_visible_to_initial_depth','wait_floor_to_initial_depth']:
        assert getattr(p,name)==D(d[name])
    assert r['parameters']==parameter_identity(p)[0] and r['parameter_sha256']==parameter_identity(p)[1]
    assert r['original_sop_numerical_result'] and strategy_from_parameters(p)=='M17'
    native=definition(p)
    assert native['strategy']=='M17' and native['groups']==['M17'] and native['real_orders']==0
    assert native['admission_signal_basis']=='EVER_REFERENCE_READY_BEFORE_ORIGINAL_DEADLINE'
    assert 'P_QR.md' in native['sop_references']


@pytest.mark.parametrize('key,value',[
    ('window_seconds',.5),('subwindow_seconds',.2),('stable_seconds',.05),('recheck_seconds',.2),
    ('publication_lateness_seconds',.1),('minimum_distinct_sell_records',2),('occupied_subwindows_required',2),('ttl',4)])
def test_fixed_formation_sequence_and_quality_deadlines_are_not_numeric_knobs(key,value):
    d=baseline();d.update(revision_round=1,hypothesis='explicit numerical test');d['parameters'][key]=value
    with pytest.raises(ValueError,match='FIXED_SOP_CAUSAL'):load_revision(d)


@pytest.mark.parametrize('revision',[True,-1,4,1.5,'1'])
def test_revision_budget_is_integer_and_at_most_three(revision):
    d=baseline();d['revision_round']=revision
    with pytest.raises(ValueError,match='AT_MOST_THREE'):load_revision(d)


@pytest.mark.parametrize('raw',[None,True,'NaN','Infinity'])
def test_no_bool_missing_or_nonfinite_parameters(raw):
    d=baseline();d['parameters']['minimum_sell_shares']=raw
    with pytest.raises(ValueError,match='INVALID_PARAMETER_NUMBER'):load_revision(d)


def test_original_baseline_cannot_be_relabelled_and_revisions_need_every_changed_parameter_note():
    d=baseline();d['parameters']['minimum_sell_shares']='2'
    with pytest.raises(ValueError,match='FIXED_SOP'):load_revision(d)
    d.update(revision_round=1,hypothesis='Lower material quantity threshold while preserving three real buckets.')
    with pytest.raises(ValueError,match='EXPLICIT_NUMERIC'):load_revision(d)
    d['numeric_candidate_review']={'profile':'M17_RETAINED_DEPTH_NUMERICAL_V1',
                                   'parameter_change_notes':{'minimum_sell_shares':'5 -> 2; actual SELL and3 buckets remain.'}}
    p,r=load_revision(d)
    assert p.minimum_sell_shares==2 and not r['original_sop_numerical_result']
    assert r['frequency_basis']=='EVER_REFERENCE_READY_BEFORE_ORIGINAL_DEADLINE'


def test_continuous_and_waiting_absolute_floor_must_remain_the_same_quantity_rule():
    d=baseline();d.update(revision_round=1,hypothesis='Test explicit retention numbers.')
    d['parameters']={'minimum_visible_to_initial_depth':'.60'}
    with pytest.raises(ValueError,match='SAME_ABSOLUTE_FLOOR'):load_revision(d)
