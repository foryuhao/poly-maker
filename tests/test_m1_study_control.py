"""Changing sample size must preserve the frozen strategy and prior observations."""

import gzip
import importlib.util
import json
from copy import deepcopy
from pathlib import Path
from threading import Event

import pytest


def control():
    path = Path(__file__).resolve().parents[1]/"ops/m1_study_control.py"
    spec = importlib.util.spec_from_file_location("m1_control", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_extend_sampling_preserves_every_existing_market_and_frozen_identity():
    state = {"phase": "EVALUATION", "evaluation_target": 50, "frozen_config_sha256": "fixed",
             "source_sha256": {"model": "unchanged"}, "paper_model": {"post_latency_ms": 400},
             "sessions": [{"phase": "CALIBRATION", "signals": 1},
                          {"phase": "EVALUATION", "signals": 0, "complete_window": False}]}
    before = deepcopy(state)
    updated = control().change_target(state, 150, 6144, 8)
    assert updated["evaluation_target"] == 150
    for key in ("sessions", "frozen_config_sha256", "source_sha256", "paper_model"):
        assert updated[key] == before[key]
    assert updated["target_history"][0]["evaluation_markets_already_recorded"] == 1
    assert not updated["run_controls"]["changes_strategy_or_fill_model"]


@pytest.mark.parametrize("phase,target", [("CALIBRATION", 150), ("EVALUATION", 50), ("EVALUATION", 40)])
def test_extension_cannot_recalibrate_or_shrink_sample(phase, target):
    with pytest.raises(ValueError):
        control().change_target({"phase": phase, "evaluation_target": 50}, target, 6144, 8)


def test_recording_buffer_absorbs_a_burst_without_losing_frames(tmp_path):
    module = control()
    gate = Event()
    class PausedWriter(module.BufferedStudyWriter):
        def _run(self):
            gate.wait(2)
            super()._run()
    writer = PausedWriter(tmp_path/"burst.jsonl.gz")
    for index in range(5000):
        assert writer.submit({"event": "RAW_FRAME", "frame": index, "raw": "public"})
    gate.set()
    writer.close()
    assert not writer.failure and writer.dropped == 0 and writer.written == 5000
    with gzip.open(writer.path, "rt") as stream:
        assert [json.loads(line)["frame"] for line in stream] == list(range(5000))


def test_recorder_only_update_preserves_target_and_samples():
    state = {"phase": "EVALUATION", "evaluation_target": 150, "sessions": [], "source_sha256": {"model": "frozen"}}
    control().change_target(state, 150, 6144, 8, storage_only=True)
    assert state["evaluation_target"] == 150 and state["source_sha256"] == {"model": "frozen"}
    assert state["run_controls"]["log_queue_capacity"] == 8192
