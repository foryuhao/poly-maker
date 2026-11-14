"""The hourly chat delivery must not multiply notifications on retries."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

MODULE_PATH = Path(__file__).resolve().parents[2] / "ops/m4_pnl_hourly_watch.py"
SPEC = importlib.util.spec_from_file_location("m4_pnl_hourly_watch", MODULE_PATH)
watch = importlib.util.module_from_spec(SPEC)
with pytest.MonkeyPatch.context() as import_paths:
    import_paths.syspath_prepend(str(MODULE_PATH.parent))
    SPEC.loader.exec_module(watch)


@pytest.mark.parametrize(
    "stderr, expected",
    [
        ("", "QUEUED_AND_RESUME_REQUESTED"),
        ("warning: queued but resume could not be confirmed", "QUEUED_RESUME_UNCONFIRMED"),
    ],
)
def test_hourly_delivery_preserves_review_state_and_deduplicates(tmp_path, stderr, expected):
    control = {"thread": "authorized-existing-thread"}
    report = {"bucket": 100, "directory": str(tmp_path / "report")}
    state_path = tmp_path / "agent-state.json"
    state_path.write_text(json.dumps({"reviewed_report_directory": "previous-report"}))
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr=stderr)

    assert watch.wake(control, report, tmp_path, runner)["status"] == expected
    assert watch.wake(control, report, tmp_path, runner)["status"] == "ALREADY_QUEUED"
    assert len(calls) == 1
    assert calls[0][calls[0].index("--thread") + 1] == control["thread"]
    assert "--resume" in calls[0]
    assert json.loads(state_path.read_text())["reviewed_report_directory"] == "previous-report"
    report["bucket"] += 1
    assert watch.wake(control, report, tmp_path, runner)["status"] == expected
    assert len(calls) == 2


def test_failed_queue_remains_retryable(tmp_path):
    control = {"thread": "authorized-existing-thread"}
    report = {"bucket": 100, "directory": str(tmp_path / "report")}
    replies = iter(
        [
            SimpleNamespace(returncode=1, stdout="", stderr="unavailable"),
            SimpleNamespace(returncode=0, stdout="queued", stderr=""),
        ]
    )

    def runner(command, **kwargs):
        return next(replies)

    assert watch.wake(control, report, tmp_path, runner)["status"] == "WAKE_FAILED"
    assert not (tmp_path / "agent-state.json").exists()
    assert watch.wake(control, report, tmp_path, runner)["status"] == "QUEUED_AND_RESUME_REQUESTED"
