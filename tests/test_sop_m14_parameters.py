"""Freeze M14 baseline numbers and forbid structural or quality shortcuts."""

from copy import deepcopy
from decimal import Decimal as D

import pytest
from sop_signals.m13 import Parameters as M13Parameters
from sop_signals.m14 import M14, Parameters
from sop_signals.parameters import load_revision, strategy_from_parameters


def document():
    return {'strategy': 'M14', 'revision_round': 0,
            'hypothesis': 'ORIGINAL_M14_SOP_BASELINE', 'parameters': {}, 'pnl_used_for_selection': False}


def test_original_baseline_matches_preimplementation_freeze_and_is_a_new_strategy():
    p, revision = load_revision(document())
    assert p == Parameters() and strategy_from_parameters(p) == 'M14'
    assert revision['parameter_sha256'] == 'bbfbc47f4c06fa9b5daf9bafbbd7b4d12ec298bc613af59a8002abe8be88a32d'
    assert p.ask_gap == 2 and p.pair_gap == 1.5 and p.ttl == 3
    assert p.ask_recovery == D('.30') and p.second_recovery_increase == D('.10')
    assert p.ratio_min == D('.50') and p.ratio_max == D('2.00')


def test_explicit_frequency_only_revision_records_changed_numbers_and_fixed_causal_rules():
    d = document()
    d.update(revision_round=1, hypothesis='Increase numerical matching tolerance, keep two new Ask rounds',
             parameters={'ask_recovery': '.35', 'ask_loss': '.02', 'ask_gap': 3,
                         'pair_gap': 2, 'ratio_min': '.25', 'ratio_max': '4'})
    p, revision = load_revision(d)
    assert p.ask_recovery == D('.35') and p.ask_gap == 3
    assert revision['pnl_used_for_selection'] is False and revision['revision_round'] == 1
    assert revision['fixed_causal_rules'] == {'round_seconds': .5, 'stable_recovery': '0.50',
                                            'stable_seconds': .1, 'ttl': 3}


@pytest.mark.parametrize('name,value', [('round_seconds', .25), ('stable_seconds', .05),
                                       ('stable_recovery', '.40'), ('ttl', 8)])
def test_fixed_round_stability_and_deadlines_are_not_tuning_knobs(name, value):
    d = document()
    d.update(revision_round=1, parameters={name: value})
    with pytest.raises(ValueError, match='FIXED_SOP_CAUSAL_RULE'):
        load_revision(d)


@pytest.mark.parametrize('name,value', [('ask_recovery', '.50'), ('ask_loss', 0), ('ratio_min', 0),
                                       ('pair_gap', 3), ('ask_gap', .5), ('second_recovery_increase', -1)])
def test_semantically_invalid_numeric_candidate_is_rejected(name, value):
    d = document()
    d.update(revision_round=1, parameters={name: value})
    with pytest.raises(ValueError, match='SEMANTICS_OR_FEASIBILITY'):
        load_revision(d)


@pytest.mark.parametrize('name,value', [('ask_loss', True), ('ask_loss', None),
                                       ('ask_gap', 'NaN'), ('ask_loss', 'Infinity')])
def test_invalid_nonfinite_numbers_are_not_accepted(name, value):
    d = document()
    d.update(revision_round=1, parameters={name: value})
    with pytest.raises(ValueError, match='INVALID_PARAMETER_NUMBER'):
        load_revision(d)


@pytest.mark.parametrize('name', ['ask_rounds', 'first_failed_retry', 'quality_relaxed', 'root_reuse'])
def test_structural_or_quality_shortcuts_are_not_parameters(name):
    d = document()
    d.update(revision_round=1, parameters={name: 1})
    with pytest.raises(ValueError, match='UNAUTHORIZED_M14_PARAMETER'):
        load_revision(d)


def test_revision_limit_hypothesis_and_pnl_prohibition():
    d = document()
    d['revision_round'] = 4
    with pytest.raises(ValueError, match='AT_MOST_THREE'):
        load_revision(d)
    d.update(revision_round=1, hypothesis=' ')
    with pytest.raises(ValueError, match='HYPOTHESIS'):
        load_revision(d)
    d.update(hypothesis='Candidate', pnl_used_for_selection=True)
    with pytest.raises(ValueError, match='FREQUENCY_ONLY'):
        load_revision(d)


def test_original_baseline_cannot_be_silently_changed_and_old_parameter_type_is_rejected():
    d = deepcopy(document())
    d['parameters']['ask_loss'] = '.02'
    with pytest.raises(ValueError, match='FIXED_SOP_CAUSAL_RULE'):
        load_revision(d)
    with pytest.raises(ValueError, match='M14_PARAMETERS_REQUIRED'):
        M14(lambda *_: None, M13Parameters())
