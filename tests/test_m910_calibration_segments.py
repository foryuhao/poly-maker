"""A performance continuation cannot omit, duplicate or replace fixed inputs."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def segment_evidence(tmp_path, monkeypatch):
    source=Path(__file__).resolve().parents[1]/'research_outputs/analysis/m910_density_v8_20261113/calibration_evidence.py'
    spec=importlib.util.spec_from_file_location('calibration_evidence_test',source)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module,'HERE',tmp_path)
    def write(name,value):
        path=tmp_path/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(value))
        return hashlib.sha256(path.read_bytes()).hexdigest()
    members=[{'market':str(i),'input_seq':100+i,'sha256':str(i),'complete_window':True} for i in range(100)]
    development_sha=write('development100.json',{'markets':members})
    remainder_sha=write('remainder.json',{'markets':members[2:]})
    write('grid-development-stage2/progress.json',{'markets':[{'market':'0'},{'market':'1'}]})
    write('grid-development-stage2b/progress.json',{'markets':[{'market':'2'}]})
    binding={'development_sha256':development_sha,'stages':{'stage2':[
        {'directory':'grid-development-stage2','input_manifest':'development100.json',
         'input_manifest_sha256':development_sha,'stopped_after_markets':2},
        {'directory':'grid-development-stage2b','input_manifest':'remainder.json',
         'input_manifest_sha256':remainder_sha}]}}
    write('calibration-continuations.json',binding)
    return module,write,binding,members


def test_exact_fixed_prefix_can_continue_without_counting_a_partial_market(segment_evidence):
    module,_,_,_=segment_evidence
    assert module.stage_rows('stage2')==[{'market':'0'},{'market':'1'},{'market':'2'}]


@pytest.mark.parametrize('corruption',['duplicate','skip','changed_watermark','changed_stopped_prefix'])
def test_segment_admission_rejects_incomplete_or_changed_input_proof(segment_evidence,corruption):
    module,write,binding,members=segment_evidence
    if corruption in ('duplicate','skip'):
        write('grid-development-stage2b/progress.json',{'markets':[{'market':'1' if corruption=='duplicate' else '3'}]})
    elif corruption=='changed_watermark':
        members[2]['input_seq']+=1
        binding['stages']['stage2'][1]['input_manifest_sha256']=write('remainder.json',{'markets':members[2:]})
        write('calibration-continuations.json',binding)
    else:
        write('grid-development-stage2/progress.json',{'markets':[{'market':'0'},{'market':'1'},{'market':'2'}]})
    with pytest.raises(AssertionError):
        module.stage_rows('stage2')
