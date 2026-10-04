"""Focused controls for the independent $30 C-M order planner."""
import importlib.util
import sys
from pathlib import Path

TOOLS=Path(__file__).resolve().parents[1]/'research_tools'
sys.path.insert(0,str(TOOLS))
spec=importlib.util.spec_from_file_location('p0_cm_30',TOOLS/'p0_cm_30.py')
cm30=importlib.util.module_from_spec(spec)
spec.loader.exec_module(cm30)


def test_frozen_worst_market_can_reduce_without_funding_full_flip():
    # After 15 NO @ .92, cash is 16.20. A 30-share YES flip is impossible;
    # 15 YES @ .85 can instead remove the old net exposure.
    q,reason,flip,pure=cm30.choose_quantity(-15,30,'YES',.85,16.2,-13.8)
    assert (q,reason,flip,pure)==(15,'reduce_after_cash',True,True)
    assert cm30.path_checks(-15,'YES',q,.85,16.2,-13.8)==(True,'ok')
    assert cm30.worst_pending_metric(-15,'YES',q,.85,-13.8)>=-13.8


def test_new_risk_requires_full_exit_reserve_and_can_shrink():
    assert cm30.path_checks(0,'YES',15,.99,30,0)==(True,'ok')
    assert cm30.path_checks(0,'YES',15,.99,29.8,0)==(False,'exit_reserve')
    q,reason,flip,pure=cm30.choose_quantity(0,15,'YES',.99,29.8,0)
    assert q==14.97 and reason=='shrink_after_exit_reserve' and not flip and not pure


def test_stop_allows_only_lawful_pure_reduction():
    assert cm30.path_checks(-10,'YES',10,.8,10,-21,stopped=True)==(True,'ok')
    assert cm30.path_checks(-10,'YES',15,.8,20,-21,stopped=True)==(False,'stopped_new_risk')
    assert cm30.choose_quantity(-3,20,'YES',.5,20,-21,stopped=True)[0]==0


def test_partial_flip_checks_intermediate_reserve_and_cap():
    # A 20-share order from -10 crosses zero. The first ten are risk-reducing;
    # new YES exposure then needs its own $1/share exit reserve.
    assert cm30.path_checks(-10,'YES',20,.5,20,-5)==(True,'ok')
    assert cm30.path_checks(-10,'YES',20,.5,19.99,-5)==(False,'exit_reserve')
    assert cm30.path_checks(-10,'YES',26,.5,40,-5)==(False,'potential_net_cap')


def test_existing_partial_order_remaining_can_fall_below_new_order_minimum():
    # The 5-share minimum gates new submissions, not a live order's remainder.
    ahead,own=cm30.consume_queue(2,5,4)
    assert ahead==0 and own==2
    ahead,own=cm30.consume_queue(ahead,3,1)
    assert ahead==0 and own==1
