"""Recorded M9 pressure exits can use a fresh-AP rule below rolling threshold."""
import importlib.util
from decimal import Decimal as D
from pathlib import Path

import pytest


PATH=Path(__file__).resolve().parents[1]/'research_outputs/analysis/m910_density_v8_20261113/resumption_guard_evidence.py'
SPEC=importlib.util.spec_from_file_location('resumption_guard_evidence_test',PATH)
MODULE=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def example():
    end={'reason':'SEED_EX_PRESSURE_RESUMED','resume_fraction':'.75',
        'token':'token','ap_id':'original','seq':10,'input_seq':20,'at':3.,
        'reference_volume':D('13.52'),'extended_seed_guard_applicable':True}
    ap={'kind':'AP_SELL','token':'token','id':'fresh','audit_seq':8,
        'input_seq':20,'confirmed':3.,'volume':'9.50'}
    return end,ap


def test_actual_boundary_shift_case_has_new_ap_evidence_despite_low_rolling_volume():
    end,ap=example()
    result=MODULE.pressure_guard_evidence(end,D('2.32')/D('13.52'),[ap])
    assert not result['rolling_rule_sufficient']
    assert result['same_input_new_ap_sell_rule_sufficient']


def test_rolling_threshold_exact_boundary_is_sufficient_without_ap():
    end,_=example()
    result=MODULE.pressure_guard_evidence(end,D('.75'),[])
    assert result['rolling_rule_sufficient']
    assert not result['same_input_new_ap_sell_rule_sufficient']


def test_new_ap_exact_half_reference_is_a_separate_sufficient_rule():
    end,ap=example();ap['volume']='6.76'
    assert MODULE.pressure_guard_evidence(end,D('.1'),[ap])['same_input_new_ap_sell_rule_sufficient']


@pytest.mark.parametrize('change',[{'kind':'AP_BUY'},{'token':'other'},
    {'id':'original'},{'audit_seq':11},{'input_seq':19},{'confirmed':2.9},
    {'volume':'6.75'}])
def test_wrong_or_stale_ap_cannot_explain_an_exit_below_rolling_threshold(change):
    end,ap=example();ap.update(change)
    with pytest.raises(AssertionError,match='UNEXPLAINED_PRESSURE_END'):
        MODULE.pressure_guard_evidence(end,D('.1'),[ap])


def test_fresh_ap_cannot_substitute_for_residual95percent_guard():
    end,ap=example();end['reason']='RETEST_RESIDUAL_PRESSURE_RESUMED'
    with pytest.raises(AssertionError,match='UNEXPLAINED_PRESSURE_END'):
        MODULE.pressure_guard_evidence(end,D('.74'),[ap])


def test_both_sufficient_rules_are_recorded_without_unique_cause_claim():
    end,ap=example()
    result=MODULE.pressure_guard_evidence(end,D('.8'),[ap])
    assert result['rolling_rule_sufficient'] and result['same_input_new_ap_sell_rule_sufficient']


def test_actual_new_ap_case_is_not_mislabeled_as_50_to75percent_rolling_pressure():
    assert MODULE.ratio_bucket(D('2.32')/D('13.52'))=='[0.00,0.50)'


def test_exact50percent_belongs_to_the_next_histogram_bin():
    assert MODULE.ratio_bucket(D('.50'))=='[0.50,0.75)'


@pytest.mark.parametrize('applicable',[False,None])
def test_fresh_ap_rule_requires_a_current_extended_seed_wrapper(applicable):
    end,ap=example()
    if applicable is None:
        end.pop('extended_seed_guard_applicable')
    else:
        end['extended_seed_guard_applicable']=False
    with pytest.raises(AssertionError,match='UNEXPLAINED_PRESSURE_END'):
        MODULE.pressure_guard_evidence(end,D('.1'),[ap])
