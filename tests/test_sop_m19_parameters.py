"""M19 original numerical identity, schema and one-strategy entry guards."""
import pytest

from sop_signals.m19 import M19, Parameters
from sop_signals.m18 import Parameters as M18Parameters
from sop_signals.parameters import load_revision, strategy_from_parameters


def document(**values):
    return {'strategy':'M19','revision_round':0,'parameters':values,'pnl_used_for_selection':False}


def test_baseline_has_original_m19_ratios_and_no_m14_or_m18_substitution():
    p,r = load_revision(document())
    assert p==Parameters() and strategy_from_parameters(p)=='M19'
    assert r['original_sop_numerical_result'] is True and r['revision_round']==0
    assert p.absolute_hit_ratio_min==p.normalized_hit_ratio_min==.25
    assert p.absolute_hit_ratio_max==p.normalized_hit_ratio_max==.5
    assert str(p.absolute_loss_ratio_min)==str(p.normalized_loss_ratio_min)=='1.25'
    assert p.ask_gap==2 and p.pair_gap==1.5 and p.ttl==3
    with pytest.raises(ValueError,match='M19_PARAMETERS_REQUIRED'):
        M19(lambda *_:None,M18Parameters())


@pytest.mark.parametrize('name,value',[('round_seconds',.25),('stable_seconds',.05),
    ('reference_hit_shares_min','1'),('reference_loss_fraction_min','.01'),
    ('absolute_hit_ratio_min','.1'),('absolute_loss_ratio_min','1'),
    ('second_recovery_max','.49'),('ask_gap',4),('pair_gap',2),('ttl',5)])
def test_original_baseline_cannot_silently_be_tuned(name,value):
    with pytest.raises(ValueError,match='ORIGINAL_SOP_BASELINE_PARAMETER_REQUIRED'):
        load_revision(document(**{name:value}))


@pytest.mark.parametrize('value',[None,True,'NaN','Infinity','oops'])
def test_bad_numeric_values_are_not_replaced_with_defaults_or_zero(value):
    with pytest.raises(ValueError,match='ORIGINAL_SOP_BASELINE_PARAMETER_REQUIRED'):
        load_revision(document(reference_hit_shares_min=value))


@pytest.mark.parametrize('revision',[True,-1,4,1.0])
def test_revision_budget_and_integer_identity_are_explicit(revision):
    d = document()
    d['revision_round'] = revision
    with pytest.raises(ValueError,match='AT_MOST_THREE'):
        load_revision(d)


def test_no_profit_selection_or_unimplemented_numeric_candidate_fallback():
    d = document()
    d['pnl_used_for_selection'] = True
    with pytest.raises(ValueError,match='FREQUENCY_ONLY'):
        load_revision(d)
    d.update(pnl_used_for_selection=False,revision_round=1)
    with pytest.raises(ValueError,match='NO_SILENT_DEFAULT'):
        load_revision(d)


def test_definition_preserves_both_terminal_sides_and_main_historical_scope():
    from m1223_sim import definition
    from m1223_gates import strategy_schema
    p,r = load_revision(document())
    sop = definition(p)
    assert sop['strategy']=='M19' and sop['groups']==['M19'] and sop['real_orders']==0
    assert sop['parameter_sha256']==r['parameter_sha256']
    assert sop['sop_role']=='USER_AUTHORIZED_MAIN_HISTORICAL_SOP_NOT_DIAGNOSTIC_REPLACEMENT'
    contract = sop['terminal_quantity_input_contract']
    assert contract['sides']==['BID','ASK'] and contract['window_ms']==500
    assert contract['ask_stable_required_for_formation'] is False
    assert contract['terminal_quantity_source_strictly_after_max_trade_source'] is True
    assert strategy_schema(sop,'frequency-selection-v1')=='m19-frequency-selection-v1'
