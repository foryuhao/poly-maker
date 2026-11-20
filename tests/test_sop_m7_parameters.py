from copy import deepcopy
import pytest
from sop_signals import m7
from sop_signals.m12 import parameter_identity
from sop_signals.parameters import load_revision,strategy_from_parameters


def document():
    return {'strategy':'M7','variant_id':m7.VARIANT,'sop_sha256':m7.SOP_SHA256,
        'document_parameters_sha256':m7.DOCUMENT_PARAMETERS_SHA256,
        'revision_round':0,'pnl_used_for_selection':False,
        'parameters':parameter_identity(m7.Parameters())[0]}


def test_m7_explicit_private750_and_legacy_hysteresis_have_distinct_identity():
    p,r=load_revision(document());assert strategy_from_parameters(p)=='M7'
    assert p.round_seconds==.1 and p.hold_seconds==.01 and p.ap_window_seconds==.75
    assert r['variant_id']=='M7_PAUSE_RELEASE'


@pytest.mark.parametrize('change',['old','round','pause','revision','pnl'])
def test_unreviewed_changes_cannot_silently_enter_selected_m7(change):
    d=deepcopy(document())
    if change=='old':d['variant_id']='M7_PARALLEL_CONFIRM'
    elif change=='round':d['parameters']['round_seconds']=.05
    elif change=='pause':d['parameters']['pause_total_fraction']='.60'
    elif change=='revision':d['revision_round']=1
    else:d['pnl_used_for_selection']=True
    with pytest.raises(ValueError):load_revision(d)
