"""M4A.4 durability and failure evidence at the Research boundary."""

import asyncio
import json
import os
import time
from pathlib import Path
from threading import Event, Thread, Timer

from polymaker.research.capture import CausalCapture
from polymaker.research.observer import BoundedResearchQueue, ResearchConfig, ResearchObserver
from polymaker.research.schema import ResearchEvent
from polymaker.research.writer import ResearchWriter


def event(seq: int) -> ResearchEvent:
    return ResearchEvent(
        schema_version=1, session_id="session", event_seq=seq,
        condition_id="condition", episode_id=None, kind="BOOK",
        exchange_at_us=None, received_at_us=None, available_at_us=seq,
        monotonic_ns=seq, source="OBSERVER", connection_generation=None,
        source_ref=None, payload={"level": str(seq)})


def rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_durable_order_manifest_quality_and_repeated_close(tmp_path: Path):
    queue = BoundedResearchQueue(8)
    writer = ResearchWriter(tmp_path, "dataset", "session", queue,
                            flush_interval_s=0.01, fsync_interval_s=0.01)
    writer.start()
    assert writer.ready.wait(2)
    assert json.loads((writer.directory / "manifest.json").read_text())["close_status"] == "OPEN"
    for seq in range(1, 6):
        assert queue.put_nowait(event(seq))
    assert writer.close()
    assert writer.close()
    assert [r["event_seq"] for r in rows(writer.directory / "events.jsonl")] == list(range(1, 6))
    manifest = json.loads((writer.directory / "manifest.json").read_text())
    quality = json.loads((writer.directory / "quality.json").read_text())
    assert manifest["close_status"] == "COMPLETE"
    assert manifest["accepted_count"] == manifest["persisted_count"] == 5
    assert manifest["event_watermark"] == 5
    assert set(manifest["file_sha256"]) == {"events.jsonl", "episodes.jsonl", "states.jsonl"}
    assert quality["status"] == "FINALIZING" and quality["clean_close"] is None
    assert quality["gap_count"] == 0
    assert "secret" not in (writer.directory / "manifest.json").read_text()


def test_overflow_emits_causal_gap_and_incomplete_manifest(tmp_path: Path):
    queue = BoundedResearchQueue(2)
    observer = ResearchObserver(ResearchConfig(enabled=True), queue)
    capture = CausalCapture(observer, "session")
    writer = ResearchWriter(tmp_path, "dataset", "session", queue)
    entered = Event()
    release = Event()
    original_write = writer._write

    def paused_write(item, files):
        entered.set()
        assert release.wait(2)
        original_write(item, files)

    writer._write = paused_write
    writer.start()
    capture.emit("BOOK", "condition", {"n": 1})
    assert entered.wait(2)
    capture.emit("BOOK", "condition", {"n": 2})
    capture.emit("BOOK", "condition", {"n": 3})
    capture.emit("BOOK", "condition", {"n": 4})
    release.set()
    # Wait until the writer has drained the blocked queue before retrying.
    queue._queue.join()
    capture.emit("BOOK", "condition", {"n": 5})
    assert not writer.close()
    persisted = rows(writer.directory / "events.jsonl")
    gaps = [row for row in persisted if row["kind"] == "GAP"]
    assert len(gaps) == 1 and gaps[0]["payload"]["lost_start_seq"] == 4
    assert gaps[0]["payload"]["reason"] == "QUEUE_CAPACITY"
    assert gaps[0]["available_at_us"] > 0
    manifest = json.loads((writer.directory / "manifest.json").read_text())
    assert manifest["close_status"] == "INCOMPLETE"
    assert manifest["gap_count"] >= 1 and manifest["lost_event_ranges"]


def test_serialization_failure_does_not_escape_producer_and_marks_loss(tmp_path: Path):
    queue = BoundedResearchQueue(2)
    writer = ResearchWriter(tmp_path, "dataset", "session", queue)
    writer.start()
    # model_construct deliberately bypasses schema validation to exercise a
    # writer-side serialization fault; normal producer validation forbids this.
    malformed = event(1).model_copy(update={"payload": {"bad": object()}})
    assert queue.put_nowait(malformed)
    assert not writer.close()
    manifest = json.loads((writer.directory / "manifest.json").read_text())
    quality = json.loads((writer.directory / "quality.json").read_text())
    assert manifest["close_status"] == "INCOMPLETE"
    assert manifest["persisted_count"] < manifest["accepted_count"]
    assert quality["status"] == "DEGRADED" and "WRITER_FAILURE" in quality["reasons"]


def test_write_failure_and_concurrent_close_account_for_accepted(tmp_path: Path):
    queue = BoundedResearchQueue(4)
    writer = ResearchWriter(tmp_path, "dataset", "session", queue)
    entered = Event()
    release = Event()

    def fail_write(_item, _files):
        entered.set()
        assert release.wait(2)
        raise OSError("disk unavailable")

    writer._write = fail_write
    writer.start()
    assert queue.put_nowait(event(1))
    assert entered.wait(2)
    result = []
    admission_closed = Event()
    original_close = queue.close_producer

    def signaled_close():
        original_close()
        admission_closed.set()

    queue.close_producer = signaled_close
    closer = Thread(target=lambda: result.append(writer.close()))
    closer.start()
    assert admission_closed.wait(2)
    assert queue.put_nowait(event(2)) is False
    release.set()
    closer.join(2)
    assert result == [False]
    manifest = json.loads((writer.directory / "manifest.json").read_text())
    assert manifest["close_status"] == "INCOMPLETE"
    assert manifest["accepted_count"] >= 1 and manifest["persisted_count"] == 0
    assert manifest["writer_error"] == "OSError"


def test_observer_rejects_malformed_record_without_live_exception():
    observer = ResearchObserver(ResearchConfig(enabled=True))
    assert not observer.observe({"payload": object()})
    assert observer.failures == 1


def test_quality_file_failure_keeps_manifest_incomplete(tmp_path: Path):
    queue = BoundedResearchQueue(2)
    writer = ResearchWriter(tmp_path, "dataset", "session", queue)
    writer.start()
    assert queue.put_nowait(event(1))
    original_atomic = writer._atomic_json

    def fail_quality(path, value):
        if path.name == "quality.json":
            raise OSError("quality unavailable")
        original_atomic(path, value)

    writer._atomic_json = fail_quality
    assert not writer.close()
    manifest = json.loads((writer.directory / "manifest.json").read_text())
    assert manifest["close_status"] == "INCOMPLETE"
    assert manifest["writer_error"] == "OSError"
    assert manifest["file_sha256"] == {}


def test_schema_rejection_keeps_sequence_gap_visible(tmp_path: Path):
    observer = ResearchObserver(ResearchConfig(enabled=True, queue_capacity=4))
    capture = CausalCapture(observer, "session")
    capture.emit("BOOK", "condition", {"bad": object()})
    capture.emit("BOOK", "condition", {"good": True})
    assert capture.failures == 1 and capture.lost_events == 1
    queue = observer.queue
    assert queue is not None
    writer = ResearchWriter(tmp_path, "dataset", "session", queue)
    writer.start()
    assert not writer.close()
    persisted = rows(writer.directory / "events.jsonl")
    assert [row["kind"] for row in persisted] == ["GAP", "BOOK"]
    assert persisted[0]["payload"]["lost_start_seq"] == 1
    assert persisted[0]["payload"]["reason"] == "SCHEMA_REJECTION"
    manifest = json.loads((writer.directory / "manifest.json").read_text())
    assert manifest["close_status"] == "INCOMPLETE"
    assert manifest["lost_event_ranges"] == [[1, 1]]


def test_concurrent_schema_rejection_precedes_later_persisted_event(tmp_path: Path):
    queue = BoundedResearchQueue(8)
    capture = CausalCapture(ResearchObserver(ResearchConfig(enabled=True), queue), "session")
    entered = Event()
    release = Event()
    later_done = Event()
    original_note = queue.note_lost_event

    def delayed_note(seq, reason="OBSERVER_REJECTION"):
        entered.set()
        assert release.wait(2)
        original_note(seq, reason)

    queue.note_lost_event = delayed_note
    first = Thread(target=lambda: capture.emit("BOOK", "condition", {"bad": object()}))
    later = Thread(target=lambda: (capture.emit("BOOK", "condition", {"good": 2}),
                                   later_done.set()))
    first.start()
    try:
        assert entered.wait(2)
        later.start()
        assert not later_done.wait(0.05)
    finally:
        release.set()
        first.join(2)
        if later.ident is not None:
            later.join(2)
    assert later_done.is_set()
    capture.emit("BOOK", "condition", {"good": 3})
    writer = ResearchWriter(tmp_path, "dataset", "session", queue)
    writer.start()
    assert not writer.close()
    persisted = rows(writer.directory / "events.jsonl")
    assert [(row["event_seq"], row["kind"]) for row in persisted] == [
        (1, "GAP"), (2, "BOOK"), (3, "BOOK")]
    assert persisted[0]["payload"]["reason"] == "SCHEMA_REJECTION"


def test_final_manifest_write_failure_cannot_certify_complete(tmp_path: Path):
    writer = ResearchWriter(tmp_path, "dataset", "session", BoundedResearchQueue(2))
    writer.start()
    assert writer.ready.wait(2)
    writer._commit_manifest = lambda _path, _value: (_ for _ in ()).throw(OSError("manifest"))
    assert not writer.close()
    manifest = json.loads((writer.directory / "manifest.json").read_text())
    quality = json.loads((writer.directory / "quality.json").read_text())
    assert manifest["close_status"] == "OPEN"
    assert quality["status"] == "FINALIZING" and quality["clean_close"] is None


def test_final_manifest_replace_failure_cannot_certify_complete(tmp_path: Path, monkeypatch):
    writer = ResearchWriter(tmp_path, "dataset", "session", BoundedResearchQueue(2))
    writer.start()
    assert writer.ready.wait(2)
    original_replace = os.replace

    def fail_commit_replace(source, target):
        if str(source).endswith(".commit.tmp"):
            raise OSError("rename unavailable")
        original_replace(source, target)

    monkeypatch.setattr(os, "replace", fail_commit_replace)
    assert not writer.close()
    assert json.loads((writer.directory / "manifest.json").read_text())["close_status"] == "OPEN"
    quality = json.loads((writer.directory / "quality.json").read_text())
    assert quality["status"] == "FINALIZING" and quality["clean_close"] is None


def test_final_manifest_directory_fsync_failure_precedes_commit(tmp_path: Path, monkeypatch):
    writer = ResearchWriter(tmp_path, "dataset", "session", BoundedResearchQueue(2))
    writer.start()
    assert writer.ready.wait(2)
    original_fsync = os.fsync
    in_commit = Event()
    original_commit = writer._commit_manifest

    def marked_commit(path, value):
        in_commit.set()
        try:
            original_commit(path, value)
        finally:
            in_commit.clear()

    def fail_commit_directory(fd):
        if in_commit.is_set() and os.path.isdir(os.readlink(f"/proc/self/fd/{fd}")):
            raise OSError("directory fsync unavailable")
        original_fsync(fd)

    writer._commit_manifest = marked_commit
    monkeypatch.setattr(os, "fsync", fail_commit_directory)
    assert not writer.close()
    assert json.loads((writer.directory / "manifest.json").read_text())["close_status"] == "OPEN"
    quality = json.loads((writer.directory / "quality.json").read_text())
    assert quality["status"] == "FINALIZING" and quality["clean_close"] is None


def test_jsonl_fsync_failure_marks_incomplete(tmp_path: Path, monkeypatch):
    queue = BoundedResearchQueue(2)
    writer = ResearchWriter(tmp_path, "dataset", "session", queue)
    writer.start()
    assert writer.ready.wait(2)
    original_fsync = os.fsync

    def fail_events_fsync(fd):
        if os.readlink(f"/proc/self/fd/{fd}").endswith("events.jsonl"):
            raise OSError("events fsync unavailable")
        original_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_events_fsync)
    assert queue.put_nowait(event(1))
    assert not writer.close()
    manifest = json.loads((writer.directory / "manifest.json").read_text())
    quality = json.loads((writer.directory / "quality.json").read_text())
    assert manifest["close_status"] == "INCOMPLETE"
    assert quality["status"] == "DEGRADED" and quality["clean_close"] is False


def test_startup_disk_work_runs_on_writer_thread(tmp_path: Path):
    writer = ResearchWriter(tmp_path, "dataset", "session", BoundedResearchQueue(2))
    entered = Event()
    release = Event()
    original_atomic = writer._atomic_json

    def blocked_open_manifest(path, value):
        if path.name == "manifest.json":
            entered.set()
            assert release.wait(2)
        original_atomic(path, value)

    writer._atomic_json = blocked_open_manifest
    timer = Timer(0.5, release.set)
    timer.start()

    async def live_progress():
        began = time.monotonic()
        writer.start()
        assert time.monotonic() - began < 0.2
        assert await asyncio.to_thread(entered.wait, 2)
        assert not writer.ready.is_set()
        await asyncio.wait_for(asyncio.sleep(0), 0.1)

    try:
        asyncio.run(live_progress())
    finally:
        release.set()
        timer.join(2)
    assert writer.close()


def test_startup_failure_closes_admission_without_live_exception(tmp_path: Path):
    queue = BoundedResearchQueue(2)
    writer = ResearchWriter(tmp_path, "dataset", "session", queue)
    writer._atomic_json = lambda _path, _value: (_ for _ in ()).throw(OSError("startup"))
    writer.start()
    assert writer.ready.wait(2)
    assert isinstance(writer.error, OSError)
    assert queue.put_nowait(event(1)) is False
    assert not writer.close()
    assert not (writer.directory / "quality.json").exists()


def test_loss_range_memory_is_bounded_with_exact_summary():
    queue = BoundedResearchQueue(1)
    for n in range(BoundedResearchQueue.MAX_LOST_RANGES + 12):
        assert queue.put_nowait(event(n * 2 + 1))
        assert not queue.put_nowait(event(n * 2 + 2))
        queue.get()
        queue.task_done()
    counters = queue.counters
    assert len(queue.lost_event_ranges) == BoundedResearchQueue.MAX_LOST_RANGES
    assert counters.lost_range_count == BoundedResearchQueue.MAX_LOST_RANGES + 12
    assert counters.lost_ranges_truncated == 12
    assert counters.lost_event_count == counters.lost_range_count
    assert queue.lost_reason_counts == {"QUEUE_CAPACITY": counters.lost_event_count}


def test_closed_admission_loss_has_distinct_reason(tmp_path: Path):
    queue = BoundedResearchQueue(2)
    capture = CausalCapture(ResearchObserver(ResearchConfig(enabled=True), queue), "session")
    queue.close_producer()
    capture.emit("BOOK", "condition", {"event": 1})
    writer = ResearchWriter(tmp_path, "dataset", "session", queue)
    writer.start()
    assert not writer.close()
    manifest = json.loads((writer.directory / "manifest.json").read_text())
    assert manifest["close_status"] == "INCOMPLETE"
    assert manifest["lost_reason_counts"] == {"ADMISSION_CLOSED": 1}
    assert manifest["lost_event_count"] == 1


def test_loss_during_close_drain_prevents_complete_for_multiple_producers(tmp_path: Path):
    queue = BoundedResearchQueue(8)
    capture = CausalCapture(ResearchObserver(ResearchConfig(enabled=True), queue), "session")
    writer = ResearchWriter(tmp_path, "dataset", "session", queue)
    writer.start()
    assert writer.ready.wait(2)
    entered = Event()
    release = Event()
    original_sync = writer._sync

    def paused_sync(files):
        entered.set()
        assert release.wait(2)
        original_sync(files)

    writer._sync = paused_sync
    closed = []
    closer = Thread(target=lambda: closed.append(writer.close()))
    closer.start()
    try:
        assert entered.wait(2)
        assert not queue.session_sealed
        producers = [Thread(target=lambda n=n: capture.emit("BOOK", "condition", {"n": n}))
                     for n in range(3)]
        for producer in producers:
            producer.start()
        for producer in producers:
            producer.join(2)
            assert not producer.is_alive()
        assert capture.lost_events == 3
        assert queue.counters.rejected_closed == 3
    finally:
        release.set()
        closer.join(2)
    assert closed == [False] and queue.session_sealed
    manifest = json.loads((writer.directory / "manifest.json").read_text())
    quality = json.loads((writer.directory / "quality.json").read_text())
    assert manifest["close_status"] == "INCOMPLETE"
    assert manifest["rejected_closed"] == manifest["lost_event_count"] == 3
    assert manifest["lost_reason_counts"] == {"ADMISSION_CLOSED": 3}
    assert quality["status"] == "DEGRADED" and quality["lost_event_count"] == 3


def test_reviewer_finalization_race_uses_sealed_session_boundary(tmp_path: Path):
    queue = BoundedResearchQueue(2)
    capture = CausalCapture(ResearchObserver(ResearchConfig(enabled=True), queue), "session")
    writer = ResearchWriter(tmp_path, "dataset", "session", queue)
    writer.start()
    assert writer.ready.wait(2)
    entered = Event()
    release = Event()
    original_atomic = writer._atomic_json

    def paused_quality(path, value):
        if path.name == "quality.json":
            entered.set()
            assert release.wait(2)
        original_atomic(path, value)

    writer._atomic_json = paused_quality
    closed = []
    closer = Thread(target=lambda: closed.append(writer.close()))
    closer.start()
    try:
        assert entered.wait(2)
        assert queue.session_sealed
        capture.emit("BOOK", "condition", {"after_boundary": True})
        assert capture._seq == capture.lost_events == 0
        assert not queue.put_nowait(event(1))
        assert queue.counters.rejected_closed == queue.counters.lost_event_count == 0
    finally:
        release.set()
        closer.join(2)
    assert closed == [True]
    capture.emit("BOOK", "condition", {"after_return": True})
    assert writer.close()
    assert capture._seq == capture.lost_events == 0
    manifest = json.loads((writer.directory / "manifest.json").read_text())
    quality = json.loads((writer.directory / "quality.json").read_text())
    assert manifest["close_status"] == "COMPLETE"
    assert manifest["rejected_closed"] == manifest["lost_event_count"] == 0
    assert quality["status"] == "FINALIZING" and quality["lost_event_count"] == 0


def test_capture_already_in_admission_finishes_before_session_seal(tmp_path: Path):
    queue = BoundedResearchQueue(2)
    capture = CausalCapture(ResearchObserver(ResearchConfig(enabled=True), queue), "session")
    writer = ResearchWriter(tmp_path, "dataset", "session", queue)
    writer.start()
    assert writer.ready.wait(2)
    entered = Event()
    release = Event()
    underlying_put = queue._queue.put_nowait

    def paused_put(item):
        entered.set()
        assert release.wait(2)
        underlying_put(item)

    queue._queue.put_nowait = paused_put
    producer = Thread(target=lambda: capture.emit("BOOK", "condition", {"in_flight": True}))
    producer.start()
    assert entered.wait(2)
    closed = []
    close_started = Event()

    def close_writer():
        close_started.set()
        closed.append(writer.close())

    closer = Thread(target=close_writer)
    closer.start()
    try:
        assert close_started.wait(2)
        assert closer.is_alive()
    finally:
        release.set()
        producer.join(2)
        closer.join(2)
    assert not producer.is_alive() and not closer.is_alive()
    assert closed == [True] and queue.session_sealed
    assert capture._seq == 1 and capture.lost_events == 0
    assert [(row["event_seq"], row["kind"]) for row in rows(writer.directory / "events.jsonl")] == [
        (1, "BOOK")]
    assert json.loads((writer.directory / "manifest.json").read_text())["close_status"] == "COMPLETE"


def test_writer_failure_during_close_window_keeps_session_incomplete(tmp_path: Path):
    queue = BoundedResearchQueue(2)
    capture = CausalCapture(ResearchObserver(ResearchConfig(enabled=True), queue), "session")
    writer = ResearchWriter(tmp_path, "dataset", "session", queue)
    entered = Event()
    release = Event()
    admission_closed = Event()
    original_close = queue.close_producer

    def fail_write(_item, _files):
        entered.set()
        assert release.wait(2)
        raise OSError("disk write failure")

    def signaled_close():
        original_close()
        admission_closed.set()

    writer._write = fail_write
    queue.close_producer = signaled_close
    writer.start()
    capture.emit("BOOK", "condition", {"n": 1})
    assert entered.wait(2)
    closed = []
    closer = Thread(target=lambda: closed.append(writer.close()))
    closer.start()
    try:
        assert admission_closed.wait(2)
        capture.emit("BOOK", "condition", {"n": 2})
        assert capture.lost_events == 1 and not queue.session_sealed
    finally:
        release.set()
        closer.join(2)
    assert closed == [False] and queue.session_sealed
    manifest = json.loads((writer.directory / "manifest.json").read_text())
    quality = json.loads((writer.directory / "quality.json").read_text())
    assert manifest["close_status"] == "INCOMPLETE"
    assert manifest["lost_event_count"] == manifest["rejected_closed"] == 1
    assert quality["status"] == "DEGRADED" and "WRITER_FAILURE" in quality["reasons"]
