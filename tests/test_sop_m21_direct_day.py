"""An explicit user24h stage must not manufacture independent validation."""
import hashlib
import json
from copy import deepcopy

import pytest

from m1223_gates import frequency_reporting_contract, phase_admission
from m1223_start import phase_configuration


def bound(tmp_path, name, value):
    path=tmp_path/name
    path.write_text(json.dumps(value))
    return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def fixture(tmp_path):
    old=list(range(1800000000,1800000000+7200,300))
    epochs=list(range(old[-1]+300,old[-1]+300+86400,300))
    sop={'strategy':'M21','groups':['M21'],'variant_id':'M21_ENDPOINT_COFLOW',
         'parameter_sha256':'parameters-unchanged','parameters':{'ttl':3},
         'implementation':{k:'unchanged-'+k for k in ('m21.py','feed.py','m19_path.py','m12.py','metrics.py')}}
    freq={'status':'PASS','denominator':24,'pnl_used_for_selection':False,
          'independent_or_24h_admission_granted':False,
          'revision':{'parameter_sha256':sop['parameter_sha256']},
          'primary_distinct_signal_markets':13,'owner':{'runtime':'/runtime/old-m21-code'},
          'markets':[{'market':f'btc-updown-5m-{e}','primary_eligible':True,
                      'signal_ids':['signal'] if i<13 else [],'has_unknown':True}
                     for i,e in enumerate(old)]}
    frequency=bound(tmp_path,'frequency.json',freq)
    document={'schema':'m21-user-direct-24h-admission-v1',
              'authorization_quote':'清理刚才的日志，空出磁盘空间，然后M21跑24小时',
              'independent_realtime_validation_completed':False,'selection_uses_pnl':False,
              'selected_sop_definition':sop,'runtime_sha256':'new-frozen-runtime',
              'frequency_report':frequency,
              'replay_plan':bound(tmp_path,'replay.json',{'selected_sop':deepcopy(sop)}),
              'frequency_review':bound(tmp_path,'review.json',{
                  'status':'PASS','actual_child_exit':0,'frequency_sha256':frequency['sha256'],
                  'runtime_sha256':'old-m21-code','distinct_signal_markets':13})}
    cohort={'strategy':'M21','stage':'USER_DIRECT_24H_TEST','market_epochs':epochs,
            'markets':[f'btc-updown-5m-{e}' for e in epochs],
            'window_start_epoch':epochs[0],'window_end_epoch':epochs[-1]+300,
            'runtime_sha256':'new-frozen-runtime'}
    cfg={'max_markets':288,'sop':sop,'sop_parameter_revision':{'revision_round':0}}
    return document,cohort,cfg


def test_user_day_reports_development_proof_without_inventing_independent_pass(tmp_path):
    doc,cohort,cfg=fixture(tmp_path)
    result=phase_admission(cohort,cfg,user_direct_document=doc)
    assert result['admission_granted_for_24h'] is True
    assert result['independent_realtime_validation_completed'] is False
    assert result['development_primary_signal_markets']==13
    assert result['development_denominator']==24
    assert 'primary_signal_markets' not in result
    assert frequency_reporting_contract('USER_DIRECT_24H_TEST',288)['minimum_distinct_primary_signal_markets']==116


@pytest.mark.parametrize('change',['no_authorization','wrong_strategy','wrong_variant','params',
                                  'code','runtime','revision','false_independent','old_markets','fewer_markets'])
def test_direct_day_cannot_be_reused_for_untested_identity_or_different_scope(tmp_path,change):
    doc,cohort,cfg=fixture(tmp_path)
    if change=='no_authorization':doc['authorization_quote']='permission inferred'
    elif change=='wrong_strategy':cfg['sop']['strategy']='M9'
    elif change=='wrong_variant':cfg['sop']['variant_id']='OTHER'
    elif change=='params':cfg['sop']['parameters']['ttl']=30
    elif change=='code':cfg['sop']['implementation']['m21.py']='changed'
    elif change=='runtime':doc['runtime_sha256']='different'
    elif change=='revision':cfg['sop_parameter_revision']['revision_round']=1
    elif change=='false_independent':doc['independent_realtime_validation_completed']=True
    elif change=='old_markets':
        cohort['market_epochs']=[e-86400 for e in cohort['market_epochs']]
        cohort['markets']=[f'btc-updown-5m-{e}' for e in cohort['market_epochs']]
        cohort['window_start_epoch']-=86400;cohort['window_end_epoch']-=86400
    else:cohort['market_epochs']=cohort['market_epochs'][:24]
    with pytest.raises(ValueError):phase_admission(cohort,cfg,user_direct_document=doc)


def test_frequency_report_hash_and_distinct_market_counts_are_checked(tmp_path):
    doc,cohort,cfg=fixture(tmp_path)
    path=doc['frequency_report']['path']
    with open(path,'a') as stream:stream.write(' ')
    with pytest.raises(ValueError,match='EVIDENCE_CHANGED'):
        phase_admission(cohort,cfg,user_direct_document=doc)
    doc['frequency_report']['sha256']=hashlib.sha256(open(path,'rb').read()).hexdigest()
    with pytest.raises(ValueError,match='REVIEW_REQUIRED'):
        phase_admission(cohort,cfg,user_direct_document=doc)


def test_normal_day_still_requires_independent_validation(tmp_path):
    doc,cohort,cfg=fixture(tmp_path)
    cohort['stage']='CONTINUOUS_24H_TEST'
    with pytest.raises(ValueError,match='EXPLICIT_PHASE'):
        phase_admission(cohort,cfg,user_direct_document=doc)
    with pytest.raises(ValueError,match='FROZEN_SELECTION'):
        phase_admission(cohort,cfg)
    cohort['stage']='USER_DIRECT_24H_TEST'
    with pytest.raises(ValueError,match='IMPERSONATE'):
        phase_admission(cohort,cfg,user_direct_document=doc,independent_report={})


def test_launcher_keeps_direct_authorization_separate_and_full_resource_budget():
    spec=phase_configuration('USER_DIRECT_24H_TEST',0,has_selection=False,
                             has_independent_report=False,has_user_direct_admission=True)
    assert spec['markets']==288 and spec['config']=='m1223-24h.toml'
    with pytest.raises(ValueError):
        phase_configuration('USER_DIRECT_24H_TEST',0,has_selection=False,has_independent_report=False)
    with pytest.raises(ValueError):
        phase_configuration('CONTINUOUS_24H_TEST',0,has_selection=False,
                            has_independent_report=False,has_user_direct_admission=True)
