"""Actual-unit admission prevents full regression competing with capture."""

from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest
import m1223_validate as tool


def status(monkeypatch, rows="", *, own="0::/user.slice/session.scope", returncode=0):
    original = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda p, *a, **k: own if str(p) == "/proc/self/cgroup" else original(p, *a, **k))
    calls = []
    def query(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=returncode, stdout=rows)
    monkeypatch.setattr(tool.subprocess, "run", query)
    return calls


@pytest.mark.parametrize("name", ["m16-development24-abc", "m16-profile-failure-abc", "m16-original-baseline-full24-abc"])
def test_capture_and_other_cpu_jobs_defer_full_validation_without_service_mutation(monkeypatch, name):
    calls = status(monkeypatch, f"polymaker-m1223-{name}.service loaded active running Description\n")
    with pytest.raises(RuntimeError, match="FULL_VALIDATION_DEFERRED_WHILE_TASK_SERVICE_ACTIVE"):
        tool.exclusive_validation_window()
    assert len(calls) == 1 and calls[0][2] == "list-units"
    assert all(x not in calls[0] for x in ["stop", "restart", "start"])


def test_finite_validation_unit_does_not_block_itself(monkeypatch):
    name = "polymaker-m1223-m16-validation-abc.service"
    status(monkeypatch, f"{name} loaded active running Description\n", own=f"0::/user.slice/app.slice/{name}")
    result = tool.exclusive_validation_window()
    assert result["status"] == "NO_OTHER_ACTIVE_M1223_SERVICE" and result["own_units_excluded"] == [name]


def test_no_active_task_service_allows_validation(monkeypatch):
    status(monkeypatch)
    assert tool.exclusive_validation_window()["status"] == "NO_OTHER_ACTIVE_M1223_SERVICE"


def test_missing_service_observation_is_unavailable_not_idle(monkeypatch):
    status(monkeypatch, returncode=1)
    with pytest.raises(RuntimeError, match="SERVICE_STATUS_UNAVAILABLE"):
        tool.exclusive_validation_window()


def test_unexpected_unit_output_is_not_silently_ignored(monkeypatch):
    status(monkeypatch, rows="garbled\n")
    with pytest.raises(RuntimeError, match="STATUS_UNPARSEABLE"):
        tool.exclusive_validation_window()


def test_status_timeout_is_not_an_idle_capture(monkeypatch):
    status(monkeypatch)
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("systemctl", 5)
    monkeypatch.setattr(tool.subprocess, "run", timeout)
    with pytest.raises(RuntimeError, match="SERVICE_STATUS_UNAVAILABLE"):
        tool.exclusive_validation_window()


@pytest.mark.parametrize('verified',[True,False])
def test_independent_baseline_requires_verified_separate_root_and_no_mutation(monkeypatch,verified):
    calls=[]
    original=Path.read_text
    monkeypatch.setattr(Path,'read_text',lambda p,*a,**k:'0::/session.scope' if str(p)=='/proc/self/cgroup' else original(p,*a,**k))
    def query(args,**kwargs):
        calls.append(args)
        if 'list-units' in args:
            return SimpleNamespace(returncode=0,stdout='polymaker-m1223-baseline.service loaded active running Baseline\n')
        command=('/home/ubuntu/polymaker/research_outputs/m1223_baseline/runtime/pinned/extension/m1223_baseline.py '
                 '--root /home/ubuntu/polymaker/research_outputs/m1223_baseline run')
        return SimpleNamespace(returncode=0,stdout=command if verified else '/unknown/entry.py')
    monkeypatch.setattr(tool.subprocess,'run',query)
    if verified:
        result=tool.exclusive_validation_window()
        assert result['separate_baseline_units_observed_not_changed']==['polymaker-m1223-baseline.service']
    else:
        with pytest.raises(RuntimeError,match='FULL_VALIDATION_DEFERRED'):
            tool.exclusive_validation_window()
    assert len(calls)==2 and all(not ({'stop','restart','start'} & set(c)) for c in calls)
