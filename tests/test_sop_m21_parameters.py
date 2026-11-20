import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
import pytest

from sop_signals import m21
from sop_signals.m12 import parameter_identity
from sop_signals.parameters import load_revision, strategy_from_parameters


def document():
    return {'strategy':'M21','variant_id':m21.VARIANT,'revision_round':0,
            'pnl_used_for_selection':False,'sop_sha256':m21.SOP_SHA256,
            'document_parameters_sha256':m21.DOCUMENT_PARAMETERS_SHA256,
            'parameters':parameter_identity(m21.Parameters())[0]}


def test_explicit_selected_m21_identity_and_document_hashes():
    p,r=load_revision(document());assert strategy_from_parameters(p)=='M21'
    assert r['variant_id']==m21.VARIANT and r['revision_round']==0
    root=Path(m21.__file__).resolve().parents[2]
    docs=root/'research/signals'
    if not docs.exists():docs=root/'documents/research/signals'
    raw=(docs/m21.SOP_FILE).read_bytes();assert hashlib.sha256(raw).hexdigest()==m21.SOP_SHA256
    body=json.loads(re.search(r'```json\s*(.*?)\s*```',raw.decode(),re.S).group(1))
    assert hashlib.sha256(json.dumps(body,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()==m21.DOCUMENT_PARAMETERS_SHA256


@pytest.mark.parametrize('change',['variant','sop','params','revision','pnl'])
def test_no_silent_old_variant_or_undeclared_threshold_change(change):
    d=deepcopy(document())
    if change=='variant':d['variant_id']='M21_SINGLE_ROUND'
    elif change=='sop':d['sop_sha256']='foreign'
    elif change=='params':d['parameters']['recovery_min']='.49'
    elif change=='revision':d['revision_round']=1
    else:d['pnl_used_for_selection']=True
    with pytest.raises(ValueError):load_revision(d)
