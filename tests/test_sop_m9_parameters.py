from copy import deepcopy
import pytest
from sop_signals import m9
from sop_signals.m12 import parameter_identity
from sop_signals.parameters import load_revision,strategy_from_parameters


def document():
    return {'strategy':'M9','variant_id':m9.VARIANT,'sop_sha256':m9.SOP_SHA256,
        'document_parameters_sha256':m9.DOCUMENT_PARAMETERS_SHA256,
        'revision_round':0,'pnl_used_for_selection':False,
        'parameters':parameter_identity(m9.Parameters())[0]}


def test_m9_explicit_continuous_ex_and_qualified_handoff_have_distinct_identity():
    p,r=load_revision(document());assert strategy_from_parameters(p)=='M9'
    assert p.round_seconds==.5 and p.hold_seconds==.01 and p.seed_ex_ttl_seconds==3
    assert r['variant_id']=='M9_EX_VALIDITY_OVERLAP'


@pytest.mark.parametrize('change',['old','round','handoff','revision','pnl'])
def test_unreviewed_changes_cannot_silently_enter_selected_m9(change):
    d=deepcopy(document())
    if change=='old':d['variant_id']='M9_EX_HANDOFF'
    elif change=='round':d['parameters']['round_seconds']=.05
    elif change=='handoff':d['parameters']['ex_resume_fraction']='.75'
    elif change=='revision':d['revision_round']=1
    else:d['pnl_used_for_selection']=True
    with pytest.raises(ValueError):load_revision(d)
