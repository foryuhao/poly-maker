"""M4A.1 contracts, queue behavior and isolation boundary."""

import json
import time
from pathlib import Path
from threading import Event, Thread

import pytest
from pydantic import ValidationError

from polymaker.research import (
    BoundedResearchQueue,
    ResearchConfig,
    ResearchObserver,
    ResearchWriter,
)
from polymaker.research.schema import BookState, FeatureVector, ResearchEvent, StateSnapshot


def event(seq=1, payload=None):
    return ResearchEvent(
        schema_version=1, session_id="session", event_seq=seq, condition_id="condition",
        episode_id=None, kind="BOOK", exchange_at_us=None, received_at_us=None,
        available_at_us=100, monotonic_ns=200, source="OBSERVER",
        connection_generation=None, source_ref=None,
        payload=payload if payload is not None else {"book": {"levels": ["0.1"]}},
    )


def invalid_book(token):
    return BookState(
        token_id=token, bids=(), asks=(), levels_recorded=0, best_bid=None,
        best_ask=None, best_bid_qty=None, best_ask_qty=None, mid=None, spread=None,
        exchange_at_us=None, received_at_us=None, applied_at_us=100, book_seq=0,
        snapshot_generation=0, source="WS", valid=False,
    )


def snapshot():
    features = FeatureVector(**{name: None for name in FeatureVector.model_fields})
    return StateSnapshot(
        schema_version=1, dataset_id="dataset", record_id="state-1", created_at_us=100,
        source_refs=(), snapshot_id="state-1", episode_id="episode", session_id="session",
        snapshot_seq=1, sample_kind="FILL", scheduled_at_us=None, available_at_us=100,
        monotonic_ns=200, knowledge_seq=3, episode_revision=1, market_elapsed_s=50.0,
        seconds_to_end=250.0, seconds_to_forced_pair=190.0, seconds_to_order_cutoff=220.0,
        since_first_known_fill_s=0.0, since_first_exchange_fill_s=None, first_side="YES",
        first_qty="12.5", first_cost_usd="7.50", first_vwap="0.60", pair_qty="2.5",
        pair_cost_usd="0.90", pair_vwap="0.36", yes_qty="12.5", no_qty="2.5",
        paired_qty="2.5", unpaired_first_qty="10", unpaired_opposite_qty="0",
        first_finalized=False, pair_terminal_authoritative=False, baseline_state="RUNNER",
        baseline_pair_trigger=None, baseline_peak_quoted_pnl_usd=None,
        baseline_quote=None, effective_quote=None, working_orders=(),
        yes_book=invalid_book("yes"), no_book=invalid_book("no"), market_connected=False,
        user_connected=False, market_generation=0, user_generation=0, fill_truth_ready=False,
        order_state_resolved=False, safety_reasons=(), tick_size="0.01",
        venue_min_order_qty="5", theoretical_mid_pair_pnl_usd=None,
        quoted_pair_pnl_usd=None, candidate_pair_quote=None,
        pair_submission_feasible=False, all_postfill_peak_quoted_pnl_usd=None,
        drawdown_quoted_pnl_usd=None, execution_estimate=None, features=features,
        coverage_valid=False, quality_reasons=("missing book",),
    )


def test_schema_round_trip_immutability_and_unknowns():
    source = {"book": {"levels": ["0.1"]}}
    record = event(payload=source)
    source["book"]["levels"].append("0.2")
    assert record.payload["book"]["levels"] == ("0.1",)
    with pytest.raises(TypeError):
        record.payload["new"] = "bad"
    assert ResearchEvent.model_validate_json(record.model_dump_json()) == record
    state = snapshot()
    assert state.first_qty == "12.5" and state.paired_qty == "2.5"
    assert state.since_first_exchange_fill_s is None
    assert StateSnapshot.model_validate_json(state.model_dump_json()) == state
    with pytest.raises(ValidationError):
        StateSnapshot.model_validate({**state.model_dump(), "first_qty": "Infinity"})
    with pytest.raises(ValidationError):
        FeatureVector(**{**state.features.model_dump(), "yes_mid": float("nan")})
    with pytest.raises(ValidationError):
        ResearchEvent.model_validate({**record.model_dump(), "unexpected": 1})
    with pytest.raises(ValidationError):
        event(payload={"value": float("inf")})


def test_config_default_off_and_validation():
    assert ResearchConfig().enabled is False
    assert ResearchConfig(enabled=True).enabled is True
    for override in ({"queue_capacity": 0}, {"snapshot_interval_s": 0},
                     {"flush_interval_s": float("inf")}, {"fsync_interval_s": -1},
                     {"prefill_ring_duration_s": 0}, {"private_key": "secret"}):
        with pytest.raises(ValidationError):
            ResearchConfig(**override)


def test_observer_isolated_and_nonblocking():
    disabled = ResearchObserver()
    assert disabled.observe({"unvalidated": "ignored"}) is False
    assert disabled.queue is None
    queue = BoundedResearchQueue(1)
    observer = ResearchObserver(ResearchConfig(enabled=True), queue)
    assert observer.observe(event()) is True
    started = time.monotonic()
    assert observer.observe(event(2)) is False
    assert time.monotonic() - started < 0.1
    assert observer.observe({"unvalidated": "rejected"}) is False
    assert observer.failures == 1
    assert queue.counters.accepted == 1 and queue.counters.rejected_full == 1
    assert queue.get().event_seq == 1
    queue.task_done()
    queue.close_producer()
    assert queue.put_nowait(event(3)) is False
    assert queue.counters.rejected_closed == 1


def test_writer_lifecycle_and_layout(tmp_path: Path):
    queue = BoundedResearchQueue(2)
    writer = ResearchWriter(tmp_path, "dataset", "session", queue)
    assert not writer.directory.exists()
    writer.start()
    assert queue.put_nowait(event())
    assert writer.close()
    rows = (tmp_path / "dataset" / "session" / "events.jsonl").read_text().splitlines()
    assert len(rows) == 1 and json.loads(rows[0])["event_seq"] == 1
    assert (tmp_path / "dataset" / "session" / "episodes.jsonl").exists()
    assert (tmp_path / "dataset" / "session" / "states.jsonl").exists()
    with pytest.raises(RuntimeError):
        writer.start()
    with pytest.raises(ValueError):
        ResearchWriter(tmp_path, "../escape", "session", queue)


def test_close_drains_enqueue_that_entered_before_admission_closes(tmp_path: Path):
    queue = BoundedResearchQueue(2)
    writer = ResearchWriter(tmp_path, "dataset", "session", queue)
    writer.start()
    entered_put = Event()
    release_put = Event()
    close_started = Event()
    close_finished = Event()
    accepted = []
    closed = []
    underlying_put = queue._queue.put_nowait

    def paused_put(item):
        entered_put.set()
        assert release_put.wait(2)
        underlying_put(item)

    queue._queue.put_nowait = paused_put

    def produce():
        accepted.append(queue.put_nowait(event()))

    def close():
        close_started.set()
        closed.append(writer.close())
        close_finished.set()

    producer = Thread(target=produce)
    closer = Thread(target=close)
    producer.start()
    try:
        assert entered_put.wait(2)
        closer.start()
        assert close_started.wait(2)
        # The producer owns admission while paused inside put_nowait.
        assert not close_finished.wait(0.2)
    finally:
        release_put.set()
        producer.join(2)
        if closer.ident is not None:
            closer.join(2)

    assert not producer.is_alive() and not closer.is_alive()
    assert accepted == [True] and closed == [True]
    assert queue.empty()
    rows = (writer.directory / "events.jsonl").read_text().splitlines()
    assert [json.loads(row)["event_seq"] for row in rows] == [1]


def test_enqueue_rejected_while_writer_close_is_draining(tmp_path: Path):
    queue = BoundedResearchQueue(2)
    writer = ResearchWriter(tmp_path, "dataset", "session", queue)
    entered_write = Event()
    release_write = Event()
    admission_closed = Event()
    original_write = writer._write
    original_close_producer = queue.close_producer

    def paused_write(item, files):
        entered_write.set()
        assert release_write.wait(2)
        original_write(item, files)

    def signaled_close_producer():
        original_close_producer()
        admission_closed.set()

    writer._write = paused_write
    queue.close_producer = signaled_close_producer
    writer.start()
    assert queue.put_nowait(event())
    assert entered_write.wait(2)
    closed = []
    closer = Thread(target=lambda: closed.append(writer.close()))
    closer.start()
    try:
        assert admission_closed.wait(2)
        rejected = []
        producer = Thread(target=lambda: rejected.append(queue.put_nowait(event(2))))
        producer.start()
        producer.join(2)
        assert not producer.is_alive()
        assert rejected == [False]
        assert queue.counters.rejected_closed == 1
    finally:
        release_write.set()
        closer.join(2)
    assert not closer.is_alive() and closed == [True]
    rows = (writer.directory / "events.jsonl").read_text().splitlines()
    assert [json.loads(row)["event_seq"] for row in rows] == [1]


def test_payload_rejects_credentials():
    with pytest.raises(ValidationError):
        event(payload={"nested": {"private_key": "secret"}})


def test_manifest_public_config_and_round_trip():
    from polymaker.research.schema import DatasetManifest

    values = dict(
        schema_version=1, dataset_id="dataset", session_id="session", code_commit="9c28bca",
        strategy_config_id="favorite-v03", effective_strategy_config={"first_leg_qty": 12.5},
        config_sha256="hash", feature_version=None, label_version=None,
        policy_version=None, execution_version=None, payload_versions={"BOOK": 1},
        condition_id="condition", market_slug="market", yes_token_id="yes",
        no_token_id="no", split_boundaries_us={}, started_at_us=100, ended_at_us=None,
        file_sha256={}, event_watermark=0, gap_count=0, close_status="OPEN",
    )
    manifest = DatasetManifest(**values)
    values["effective_strategy_config"]["first_leg_qty"] = 5
    assert manifest.effective_strategy_config["first_leg_qty"] == 12.5
    assert DatasetManifest.model_validate_json(manifest.model_dump_json()) == manifest
    with pytest.raises(ValidationError):
        DatasetManifest(**{**values, "effective_strategy_config": {"private_key": "secret"}})
