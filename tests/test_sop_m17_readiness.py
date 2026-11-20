"""Pending historical confirmation is not proof of an actionable M17 signal."""

from copy import deepcopy
import json
import sqlite3

import pytest
from m1223_gates import validate_independent_report
from m1223_report import market_result
from sop_signals.metrics import coverage


def cohort_rows():
    names=[f'btc-updown-5m-{e}' for e in range(7500,14700,300)]
    return names,[{'market':m,'signal_ids':[f'c-{i}'],'admission_signal_ids':[],
                   'primary_eligible':True,'audit_validity':'VALID','complete':True,'unknown':False}
                  for i,m in enumerate(names)]


def test_confirmation_only_pending_roots_cannot_pass_frequency_gate():
    names,rows=cohort_rows()
    result=coverage(names,rows)
    assert result['all_recorded_signal_markets']==24
    assert result['primary_confirmed_signal_markets']==24
    assert result['primary_signal_markets']==0 and not result['frequency_passed']
    for row in rows[:10]:row['admission_signal_ids']=row['signal_ids']
    result=coverage(names,rows)
    assert result['primary_signal_markets']==10 and result['frequency_passed']


def test_multi_roots_one_market_and_isolated_ready_roots_do_not_inflate_fixed24_coverage():
    names,rows=cohort_rows()
    rows[0]['signal_ids']=[f'root-{i}' for i in range(30)]
    rows[0]['admission_signal_ids']=rows[0]['signal_ids']
    rows[1]['admission_signal_ids']=rows[1]['signal_ids'];rows[1]['primary_eligible']=False
    result=coverage(names,rows)
    assert result['denominator']==24 and result['primary_signal_markets']==1
    assert result['all_recorded_reference_ready_signal_markets']==2


def test_independent_m17_admission_needs_explicit_readiness_and_ten_distinct_usable_markets():
    names,rows=cohort_rows();sop={'strategy':'M17','groups':['M17']}
    r={'schema':'m17-independent-validation-report-v1','sample_role':'INDEPENDENT_VALIDATION',
       'mode':'REALTIME_PUBLIC_DATA_SIMULATION','phase_complete':True,'run_status':'PASS',
       'sop_definition':sop,'batch_number':1,'market_epochs':[int(n.rsplit('-',1)[1]) for n in names],
       'markets':rows}
    development=list(range(300,7500,300))
    with pytest.raises(ValueError,match='BELOW_10'):validate_independent_report(r,sop,development)
    missing=deepcopy(r)
    for row in missing['markets']:row.pop('admission_signal_ids')
    with pytest.raises(ValueError,match='READINESS_EVIDENCE_REQUIRED'):
        validate_independent_report(missing,sop,development)
    for row in rows[:10]:row['admission_signal_ids']=row['signal_ids']
    assert validate_independent_report(r,sop,development)['primary_signal_markets']==10


def audit_fixture(tmp_path,events):
    """Minimal artificial committed-prefix ledger for report queries,not admission."""
    path=tmp_path/'simulation.sqlite3'
    with sqlite3.connect(path) as db:
        db.executescript('CREATE TABLE audit(seq INTEGER PRIMARY KEY,input_seq INTEGER,kind TEXT,payload TEXT);'
                         'CREATE TABLE snapshots(name TEXT PRIMARY KEY,payload TEXT);'
                         'CREATE TABLE metadata(name TEXT PRIMARY KEY,payload TEXT);')
        definition={'definition':{'artificial_fixture':True,'market':{'slug':'synthetic-m17-report'},
                                 'settings':{'sop':{'strategy':'M17'}}}}
        db.execute('INSERT INTO audit VALUES(1,1,?,?)',('DEFINITION',json.dumps(definition)))
        for i,(kind,payload) in enumerate(events,2):
            db.execute('INSERT INTO audit VALUES(?,?,?,?)',(i,i,'SOP_EVIDENCE',json.dumps({'event':kind,'payload':payload})))
    return path


def confirmed(ready=False):
    return {'id':'s','token':'yes','confirmed':1.,'deadline':4.,'can_reference_for_new_action':ready}


def ready(**overrides):
    return {'id':'s','token':'yes','available':1.1,'pending_rechecks':0,**overrides}


def result(path,tmp_path):
    return market_result(path,tmp_path,eligibility={'status':'ELIGIBLE','reason':'ARTIFICIAL_UNIT_FIXTURE'})[0]


def test_report_query_reads_later_ready_event_and_deduplicates_repeated_ready(tmp_path):
    p=audit_fixture(tmp_path,[('SIGNAL_CONFIRMED',confirmed()),('SIGNAL_REFERENCE_READY',ready()),
                              ('SIGNAL_REFERENCE_READY',ready(available=1.2))])
    m=result(p,tmp_path)
    assert m['signal_count']==m['admission_signal_count']==1 and m['admission_signal_ids']==['s']


def test_pending_never_ready_stays_confirmed_but_not_admitted(tmp_path):
    p=audit_fixture(tmp_path,[('SIGNAL_CONFIRMED',confirmed()),('SIGNAL_ENDED',{'id':'s'})])
    m=result(p,tmp_path)
    assert m['signal_count']==1 and m['admission_signal_count']==0


def test_initially_ready_confirmation_without_audited_readiness_is_not_inferred(tmp_path):
    p=audit_fixture(tmp_path,[('SIGNAL_CONFIRMED',confirmed(True))])
    with pytest.raises(ValueError,match='INITIAL_REFERENCE_READY_NOT_AUDITED'):result(p,tmp_path)


@pytest.mark.parametrize('bad',[{'available':4.},{'token':'no'},{'pending_rechecks':1}])
def test_expired_foreign_token_or_still_pending_ready_facts_rejected(tmp_path,bad):
    p=audit_fixture(tmp_path,[('SIGNAL_CONFIRMED',confirmed()),('SIGNAL_REFERENCE_READY',ready(**bad))])
    with pytest.raises(ValueError,match='WITHOUT_LIVE_CONFIRMED_SIGNAL'):result(p,tmp_path)


def test_ready_after_signal_end_is_not_usable(tmp_path):
    p=audit_fixture(tmp_path,[('SIGNAL_CONFIRMED',confirmed()),('SIGNAL_ENDED',{'id':'s'}),
                              ('SIGNAL_REFERENCE_READY',ready())])
    with pytest.raises(ValueError,match='WITHOUT_LIVE_CONFIRMED_SIGNAL'):result(p,tmp_path)
