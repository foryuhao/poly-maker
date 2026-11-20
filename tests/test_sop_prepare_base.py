"""A removed archive must never cause mutable production-source fallback."""

import hashlib
import json
from pathlib import Path

import pytest
import m1223_prepare as prepare


def base(path):
    for rel,text in [('src/sample.py','constant=1\n'),('extension/helper.py','value=2\n'),
                     ('config/m1234-sim-contract.json','{}\n'),('config/m1234-sim-24h.toml','[test]\n')]:
        p=path/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
    return path


def retained(tmp_path,monkeypatch):
    old=tmp_path/'deleted-legacy';kept=base(tmp_path/'kept')
    manifest={str(p.relative_to(kept)):hashlib.sha256(p.read_bytes()).hexdigest()
              for p in kept.rglob('*') if p.is_file()}
    (kept/'frozen-manifest.json').write_text(json.dumps(manifest))
    fingerprint=hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()
    monkeypatch.setattr(prepare,'CLEAN_BASE',old)
    monkeypatch.setattr(prepare,'RETAINED_BASE',kept)
    monkeypatch.setattr(prepare,'RETAINED_BASE_SHA256',fingerprint)
    return old,kept


def test_existing_complete_original_base_keeps_its_source(tmp_path,monkeypatch):
    old,kept=retained(tmp_path,monkeypatch);base(old)
    selected,proof=prepare.clean_base_source()
    assert selected==old and proof['mode']=='ORIGINAL_CLEAN_BASE'


def test_deleted_base_can_use_only_verified_pinned_retained_runtime(tmp_path,monkeypatch):
    old,kept=retained(tmp_path,monkeypatch)
    selected,proof=prepare.clean_base_source()
    assert selected==kept and proof['mode']=='RECOVERED_VERIFIED_RETAINED_RUNTIME'
    assert proof['verified_manifest_files']==4 and proof['original_status']=='MISSING'
    assert not old.exists() and proof['workspace_base_dependencies_copied'] is False


def test_incomplete_old_base_is_explicit_and_not_recreated(tmp_path,monkeypatch):
    old,kept=retained(tmp_path,monkeypatch);old.mkdir()
    selected,proof=prepare.clean_base_source()
    assert selected==kept and proof['original_status']=='INCOMPLETE' and not list(old.iterdir())


def test_retained_file_mutation_is_rejected(tmp_path,monkeypatch):
    _,kept=retained(tmp_path,monkeypatch);(kept/'src/sample.py').write_text('constant=99\n')
    with pytest.raises(ValueError,match='RETAINED_BASE_FILE_CHANGED'):prepare.clean_base_source()


def test_unmanifested_source_cannot_be_included_by_recursive_copy(tmp_path,monkeypatch):
    _,kept=retained(tmp_path,monkeypatch);(kept/'src/unreviewed.py').write_text('unknown=1\n')
    with pytest.raises(ValueError,match='EXTRA_OR_MISSING_SOURCE'):prepare.clean_base_source()


def test_missing_retained_base_cannot_fall_back_to_workspace(tmp_path,monkeypatch):
    old,kept=retained(tmp_path,monkeypatch);(kept/'frozen-manifest.json').unlink()
    with pytest.raises(ValueError,match='UNAVAILABLE_NO_LIVE_WORKTREE'):prepare.clean_base_source()
    assert not old.exists()


def test_rehashed_changed_inventory_still_cannot_replace_pinned_identity(tmp_path,monkeypatch):
    _,kept=retained(tmp_path,monkeypatch);mp=kept/'frozen-manifest.json'
    m=json.loads(mp.read_text());m.pop('src/sample.py');mp.write_text(json.dumps(m))
    with pytest.raises(ValueError,match='MANIFEST_IDENTITY_CHANGED'):prepare.clean_base_source()
