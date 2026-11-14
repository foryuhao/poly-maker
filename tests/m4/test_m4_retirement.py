"""Destructive cutover is tested against a synthetic project and user home."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

spec=importlib.util.spec_from_file_location('m4_retirement',Path(__file__).parents[2]/'ops/m4_retire_m1.py')
retirement=importlib.util.module_from_spec(spec)
spec.loader.exec_module(retirement)


def test_archive_preserves_opaque_risk_bytes_and_latch_without_start(tmp_path,monkeypatch):
    project=tmp_path/'project'
    project.mkdir()
    home=tmp_path/'home'
    units=home/'.config/systemd/user'
    units.mkdir(parents=True)
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:home))
    calls=[]
    def system(args,**kwargs):
        calls.append(args)
        return SimpleNamespace(stdout='inactive\n')
    monkeypatch.setattr(retirement.subprocess,'run',system)
    for unit in retirement.OLD_UNITS:
        (units/unit).write_text('[Service]\n')
    for name,data in {
        'journal/m1_live_synthetic.sqlite3':b'opaque-risk-original',
        'journal/m1_live_synthetic.sqlite3-wal':b'opaque-risk-wal',
        'research_outputs/m1_virtual/state.sqlite3':b'opaque-model',
        'research_outputs/m1_live_watch/balance-stop.json':b'{"id":"old-stop","requires_confirmation":true}',
        'research_outputs/analysis/m4_cutover_review_20261108/cleanup_manifest.json':b'[]',
        'config/m1-live/m1.toml':b'maximum_price=".09"',
        'ops/polymaker-m4-live.service':b'[Service]\n',
        'ops/polymaker-m4-baseline.service':b'[Service]\n',
    }.items():
        p=project/name
        p.parent.mkdir(parents=True,exist_ok=True)
        p.write_bytes(data)
    (project/'research_outputs/analysis/m1_retention_preview/preview/empty/nested').mkdir(parents=True)
    archive=tmp_path/'retired'
    plan=retirement.retire(project,archive)
    assert not plan['applied'] and not archive.exists()
    result=retirement.retire(project,archive,apply=True)
    assert result['applied'] and result['balance_stop_id']=='old-stop'
    assert not (project/'research_outputs/m1_virtual').exists()
    assert (archive/'runtime/journal/m1_live_synthetic.sqlite3').read_bytes()==b'opaque-risk-original'
    assert (archive/'runtime/journal/m1_live_synthetic.sqlite3-wal').read_bytes()==b'opaque-risk-wal'
    assert json.loads((project/'runtime/trading-control/balance-stop.json').read_text())['id']=='old-stop'
    assert (project/'runtime/trading-control/m4-live-stop.json').exists()
    assert (project/'runtime/trading-control/m4-baseline-stop.json').exists()
    assert all((units/unit).is_symlink() for unit in retirement.OLD_UNITS)
    assert not any('start' in cmd or 'restart' in cmd or 'enable' in cmd for cmd in calls)


def test_active_runtime_blocks_even_plan(tmp_path,monkeypatch):
    monkeypatch.setattr(retirement.subprocess,'run',lambda *a,**k:SimpleNamespace(stdout='active\n'))
    with pytest.raises(ValueError,match='STILL_ACTIVE'):
        retirement.retire(tmp_path,tmp_path.parent/'archive-for-test')


def test_unreadable_backup_cannot_be_silently_omitted(tmp_path,monkeypatch):
    monkeypatch.setattr(retirement.subprocess,'run',lambda *a,**k:SimpleNamespace(stdout='inactive\n'))
    manifest=tmp_path/'research_outputs/analysis/m4_cutover_review_20261108/cleanup_manifest.json'
    manifest.parent.mkdir(parents=True)
    manifest.write_text('[]')
    hidden=tmp_path/'research_outputs/analysis/m1_private/preview'
    hidden.mkdir(parents=True)
    (hidden/'old.log').write_bytes(b'old evidence')
    hidden.chmod(0)
    try:
        with pytest.raises(PermissionError):
            retirement.inventory(tmp_path)
    finally:
        hidden.chmod(0o700)
