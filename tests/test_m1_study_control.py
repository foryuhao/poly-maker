"""Changing sample size must preserve the frozen strategy and prior observations."""

import asyncio
import gzip
import importlib.util
import json
from copy import deepcopy
from pathlib import Path
from threading import Event
from unittest.mock import AsyncMock

import httpx
import pytest

from polymaker.config import RDPM1Config
from test_rdp_sm_m1_observe import proposal_fixture
from test_rdp_sm_m1_paper import arrive


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
    assert not updated["run_controls"]["changes_signal_parameters"]
    assert updated["run_controls"]["ignored_trade_tolerance_ms"] == 10


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


def test_clock_transport_retry_preserves_last_epoch():
    wait = AsyncMock(side_effect=[httpx.ConnectTimeout("clock"), httpx.ReadTimeout("clock"), 900])
    assert asyncio.run(control().retry_market_boundary(wait, 300, delay=0)) == 900
    assert wait.await_count == 3
    assert all(call.args == (300,) for call in wait.await_args_list)


@pytest.mark.parametrize("failure", [ValueError("invalid clock"), asyncio.CancelledError()])
def test_clock_retry_never_hides_invalid_time_or_cancellation(failure):
    wait = AsyncMock(side_effect=failure)
    with pytest.raises(type(failure)):
        asyncio.run(control().retry_market_boundary(wait, 300, delay=0))
    assert wait.await_count == 1


def tolerant_broker():
    feed, clock, proposal = proposal_fixture()
    marker = proposal.poll(20, 21)
    records = []
    broker = control().TolerantPaperBroker(feed, RDPM1Config(), cutoff_mono=100,
                                          emit=records.append, post_latency_ms=400, cancel_latency_ms=200)
    broker.post(marker)
    return broker, feed, clock, records


def ignored(rollback):
    return {"event": "EVENT_IGNORED", "kind": "last_trade_price", "token_id": "1",
            "reason": "M1_EVENT_OUT_OF_ORDER_IGNORED", "rollback_ms": rollback}


@pytest.mark.parametrize("rollback", ["1", "3", "10"])
def test_small_rollback_keeps_order_live_without_queue_credit(rollback):
    b, f, c, records = tolerant_broker()
    order = arrive(b, f, c)
    ahead = order.queue_ahead
    b.on_record(ignored(rollback))
    assert b.active is order and not order.uncertain and order.cancel_requested is None
    assert order.queue_ahead == ahead and order.filled == 0
    assert b.summary()["tolerated_trade_events"] == 1
    assert records[-1]["event"] == "PAPER_TRADE_ROLLBACK_TOLERATED"


@pytest.mark.parametrize("record", [ignored("10.001"), ignored("NaN"), ignored(None),
                                   {"event": "UNAVAILABLE", "token_id": "1", "reason": "M1_SOURCE_AGE"}])
def test_larger_or_invalid_rollback_and_real_data_faults_remain_unknown(record):
    b, f, c, _ = tolerant_broker()
    order = arrive(b, f, c)
    b.on_record(record)
    assert order.uncertain and order.cancel_requested is not None


def test_tolerated_trade_during_cancel_does_not_mark_order_unknown():
    b, f, c, _ = tolerant_broker()
    order = arrive(b, f, c)
    b.cancel("PAPER_SIGNAL_ENDED")
    due = order.cancel_due
    b.on_record(ignored("2"))
    assert not order.uncertain and order.cancel_due == due


def reclassification_fixture():
    module = control()
    order = {"id": "paper:1", "state": "CLOSED", "uncertain": True, "price": ".5",
             "size": "10", "filled": "0", "outcome": "YES"}
    state = {"sessions": [{"phase": "EVALUATION", "epoch": 300, "paper": {
        "orders": [order], "uncertain_orders": 1, "spent_usdc": "0", "committed_usdc": "5",
        "positions": {"YES": "0", "NO": "0"}, "filled_orders": 0},
        "settlement": {"resolution": {"winner": "YES"}}}]}
    plan = {"policy": module.PAPER_UNCERTAINTY_POLICY, "tolerance_ms": 10, "orders": [
        {"epoch": 300, "order_id": "paper:1", "original_order_sha256": module.order_digest(order),
         "evidence": [ignored("2")]}]}
    return module, state, plan


def test_historical_reclassification_preserves_zero_fills_and_original_state():
    module, state, plan = reclassification_fixture()
    before = deepcopy(state)
    updated = module.reclassify_orders(state, plan)
    assert state == before
    row = updated["sessions"][0]
    assert row["paper"]["uncertain_orders"] == 0 and row["paper"]["filled_orders"] == 0
    assert row["paper"]["committed_usdc"] == "0"
    assert row["settlement"]["pnl_lower_usdc"] == row["settlement"]["pnl_upper_usdc"] == "0"
    assert updated["paper_policy_history"][0]["reclassified_orders"] == 1


@pytest.mark.parametrize("damage", ["hash", "other_cause", "large_rollback"])
def test_historical_reclassification_rejects_nonmatching_or_additional_faults(damage):
    module, state, plan = reclassification_fixture()
    entry = plan["orders"][0]
    if damage == "hash":
        entry["original_order_sha256"] = "different"
    elif damage == "other_cause":
        entry["evidence"].append({"event": "UNAVAILABLE", "reason": "M1_SOURCE_AGE"})
    else:
        entry["evidence"][0]["rollback_ms"] = "11"
    before = deepcopy(state)
    with pytest.raises(ValueError):
        module.reclassify_orders(state, plan)
    assert state == before
