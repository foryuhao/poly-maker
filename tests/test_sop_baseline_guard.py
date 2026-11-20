"""M23-only protection never turns reserve exhaustion into live interruption."""
import json

import pytest
from m1223_baseline_contract import atomic_json
from m1223_baseline_guard import MINIMUM_FREE_BYTES, disk_inhibited, process_identity, record_state


@pytest.mark.parametrize("offset,stopped", [(-1, True), (0, True), (1, False), (1024**3, False)])
def test_disk_guard_boundary_includes_equal_reserve(offset, stopped):
    assert disk_inhibited(MINIMUM_FREE_BYTES + offset, {}) == (stopped, MINIMUM_FREE_BYTES)


def test_protection_cannot_silently_lower_the_ten_gib_reserve():
    assert disk_inhibited(MINIMUM_FREE_BYTES - 1, {"minimum_free_bytes": 1})[0]
    assert disk_inhibited(MINIMUM_FREE_BYTES + 1, {"minimum_free_bytes": 12 * 1024**3})[0]


def test_existing_market_identity_and_ledger_pointers_are_preserved(tmp_path):
    state = {"stage": "RUNNING", "strategies": {
        "M23": {"stage": "RUNNING", "pid": 123, "output": "unchanged", "cohort": "fixed"},
        "M12": {"stage": "PENDING"}}, "retained": {"unresolved": ["old"]}}
    atomic_json(tmp_path / "state.json", state)
    record_state(tmp_path, free_bytes=20 * 1024**3, floor=MINIMUM_FREE_BYTES)
    actual = json.loads((tmp_path / "state.json").read_text())
    assert actual["strategies"]["M23"] == state["strategies"]["M23"]
    assert actual["strategies"]["M12"]["stage"] == "HELD_BY_USER"
    assert actual["retained"] == state["retained"]
    assert actual["auto_start_next_strategy"] is False
    record_state(tmp_path, free_bytes=MINIMUM_FREE_BYTES, floor=MINIMUM_FREE_BYTES,
                 reason="DISK_RESERVE_REACHED", terminal={"complete": False, "result": {"status": "FAIL"}})
    actual = json.loads((tmp_path / "state.json").read_text())
    assert actual["stage"] == "STOPPED_DISK_PROTECTION"
    assert actual["strategies"]["M12"]["stage"] == "HELD_BY_USER"


def test_pid_start_identity_comes_from_the_process_not_pid_number_alone():
    import os
    assert isinstance(process_identity(os.getpid()), int)
    assert process_identity(2147483647) is None


def test_low_disk_stops_only_the_owned_fixture_worker_and_holds_m12(tmp_path, monkeypatch):
    import subprocess
    import sys
    import time
    from types import SimpleNamespace

    import m1223_baseline_guard as guard

    root = tmp_path / "m1223_baseline"
    output = root / "runs/m23-fixture"
    atomic_json(root / "control.json", {"enabled": True})
    atomic_json(root / "cohort.json", {})
    ready = root / "fixture-ready"
    child = subprocess.Popen([sys.executable, "-c",
        "import pathlib,sys,time;pathlib.Path(sys.argv[1]).write_text('ready');time.sleep(90)", str(ready),
        "--strategy", "M23", "--output", str(output),
        "--fixture-path", "/fixture/extension/m1223_baseline_forward.py"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 3
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(.01)
    assert ready.exists()
    atomic_json(root / "state.json", {"stage": "RUNNING", "strategies": {
        "M23": {"stage": "RUNNING", "pid": child.pid, "output": str(output),
                "cohort": str(root / "cohort.json")}, "M12": {"stage": "PENDING"}}})
    monkeypatch.setattr(guard.shutil, "disk_usage", lambda _: SimpleNamespace(free=MINIMUM_FREE_BYTES))
    monkeypatch.setattr(guard.signal, "signal", lambda *_: None)
    real_sleep = time.sleep
    monkeypatch.setattr(guard.time, "sleep", lambda _: real_sleep(.01))
    try:
        guard.run(root, child.pid, process_identity(child.pid))
        child.wait(timeout=3)
        state = json.loads((root / "state.json").read_text())
        assert state["stage"] == "STOPPED_DISK_PROTECTION"
        assert state["strategies"]["M12"]["stage"] == "HELD_BY_USER"
        assert state["disk_guard"]["stop_reason"] == "DISK_RESERVE_REACHED"
    finally:
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=3)
