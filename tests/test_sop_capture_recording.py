"""Bounded writer batching preserves exact events and visible failures."""
import gzip
import json
import threading

import pytest

import m1223_capture_recording as recording
from polymaker.m1234_sim.events import Clock, Recorder as LegacyRecorder, read_events


class TrackedStream:
    def __init__(self, path, entered=None, release=None, fail=None):
        self.path, self.entered, self.release, self.fail = path, entered, release, fail
        self.writes, self.flush_at, self.records = [], [], 0

    def __enter__(self):
        self.stream = gzip.open(self.path, "at", encoding="utf-8", compresslevel=3)
        return self

    def __exit__(self, *exc):
        return self.stream.__exit__(*exc)

    def write(self, value):
        if self.entered:
            self.entered.set()
            assert self.release.wait(5)
        if self.fail == "write":
            raise OSError("controlled writer failure")
        self.writes.append(value)
        self.records += len(value.splitlines())
        return self.stream.write(value)

    def flush(self):
        self.flush_at.append(self.records)
        if self.fail == "flush":
            raise OSError("controlled flush failure")
        return self.stream.flush()


def queued_writer(path, *, count=300, capacity=8192, fail=None, write_block=False, raw="x"):
    open_ready, release_open = threading.Event(), threading.Event()
    entered, release_write = threading.Event(), threading.Event()
    tracked = TrackedStream(path, entered if write_block else None, release_write, fail)

    def opener(*args, **kwargs):
        assert args == (path, "at")
        assert kwargs == {"encoding": "utf-8", "compresslevel": 3}
        open_ready.set()
        assert release_open.wait(5)
        return tracked

    recorder = recording.Recorder(path, capacity, opener)
    assert open_ready.wait(5)
    for i in range(count):
        recorder.append("FRAME", Clock(1.25 + i / 1000, 1001.25 + i / 1000),
                        generation=3, raw=raw, source_index=i)
    return recorder, tracked, release_open, entered, release_write


def test_exact_legacy_decompressed_bytes_clocks_order_and_generations(tmp_path):
    legacy = LegacyRecorder(tmp_path / "legacy.gz")
    new = recording.Recorder(tmp_path / "new.gz")
    for i in range(300):
        c = Clock(1.234567890123 + i, 1794000000.123456 + i)
        payload = {"raw": json.dumps({"event_type": "last_trade_price", "id": "same",
                                      "price": "0.01", "size": "0.10"}),
                   "received_mono": c.mono - .01, "pending": [c.mono], "token": "α"}
        assert legacy.append("FRAME", c, 7, **payload) == new.append("FRAME", c, 7, **payload)
    legacy.close()
    new.close()
    assert gzip.decompress((tmp_path / "legacy.gz").read_bytes()) == gzip.decompress(
        (tmp_path / "new.gz").read_bytes())
    rows = list(read_events(tmp_path / "new.gz"))
    assert len(rows) == 300 and rows[-1]["seq"] == 300
    assert new.diagnostic_snapshot()["writer_thread_alive"] is False


def test_queued_batching_preserves_original_flush_boundaries_and_final_tail(tmp_path):
    r, stream, release, _, _ = queued_writer(tmp_path / "batch.gz")
    release.set()
    r.close()
    assert r.written == 300
    assert stream.flush_at == [128, 256]
    assert [len(text.splitlines()) for text in stream.writes] == [64, 64, 64, 64, 44]
    assert [row["seq"] for row in read_events(r.path)] == list(range(1, 301))
    assert r.maximum_batch_records == 64 and r.batch_count == 5
    assert r.flush_count == 2 and r.failure is None


def test_batch_character_target_is_bounded_by_one_existing_record(tmp_path):
    r, stream, release, _, _ = queued_writer(tmp_path / "large.gz", count=100, raw="x" * 20000)
    release.set()
    r.close()
    largest_line = max(len(line) + 1 for text in stream.writes for line in text.splitlines())
    assert r.maximum_batch_characters < recording.MAX_BATCH_CHARACTERS + largest_line
    assert r.maximum_batch_records <= 64
    assert len(list(read_events(r.path))) == 100


def test_one_large_record_remains_exact_without_accumulating_large_batches(tmp_path):
    r, stream, release, _, _ = queued_writer(tmp_path / "one.gz", count=3, raw="x" * 70000)
    release.set()
    r.close()
    assert r.maximum_batch_records == 1 and r.written == 3
    assert all(len(text.splitlines()) == 1 for text in stream.writes)


def test_queue_full_failure_sidecar_and_close_keep_missing_request(tmp_path):
    r, _, release, _, _ = queued_writer(tmp_path / "overflow.gz", count=1, capacity=1)
    try:
        with pytest.raises(RuntimeError, match="EVENT_BUFFER_OVERFLOW"):
            r.append("FRAME", Clock(2, 1002), raw="missing")
        sidecar = json.loads(r.path.with_suffix(".failure.json").read_text())
        assert sidecar["first_missing_seq"] == 2
        assert sidecar["event"]["raw"] == "missing"
        assert sidecar["written_at_failure"] == 0
    finally:
        release.set()
        with pytest.raises(RuntimeError, match="SIM_RECORDING_FAILED:EVENT_BUFFER_OVERFLOW"):
            r.close()
    assert len(list(read_events(r.path))) == 1


def test_batch_does_not_expand_queued_plus_inflight_allowance(tmp_path):
    r, stream, release_open, entered, release_write = queued_writer(
        tmp_path / "inflight.gz", count=4, capacity=4, write_block=True)
    release_open.set()
    assert entered.wait(5)
    try:
        assert r.written == 0 and r.queue.qsize() == 0
        r.append("FRAME", Clock(2, 1002), raw="fifth accepted")
        assert r.seq - r.written == 5 and r.queue.qsize() == 1
        with pytest.raises(RuntimeError, match="EVENT_BUFFER_OVERFLOW"):
            r.append("FRAME", Clock(3, 1003), raw="sixth rejected despite queue space")
        assert r.outstanding_high_water == 5
        assert r.failure["first_missing_seq"] == 6
        assert r.failure["event"]["raw"] == "sixth rejected despite queue space"
    finally:
        release_write.set()
        with pytest.raises(RuntimeError, match="EVENT_BUFFER_OVERFLOW"):
            r.close()
    assert r.written == 5 and len(list(read_events(r.path))) == 5


@pytest.mark.parametrize("operation", ["write", "flush"])
def test_write_or_flush_failure_remains_visible_and_does_not_publish_success(tmp_path, operation):
    r, _, release, _, _ = queued_writer(tmp_path / "failed.gz", count=128, fail=operation)
    release.set()
    r.thread.join(5)
    assert not r.thread.is_alive()
    assert r.failure["reason"] == "WRITE_FAILED:OSError"
    assert r.written == (0 if operation == "write" else 128)
    with pytest.raises(RuntimeError, match="WRITE_FAILED"):
        r.append("TIMER", Clock())
    with pytest.raises(RuntimeError, match="WRITE_FAILED"):
        r.close()
    assert json.loads(r.path.with_suffix(".failure.json").read_text())["reason"] == "WRITE_FAILED:OSError"


def test_open_failure_is_visible_before_any_record(tmp_path):
    def failed(*a, **kw):
        raise OSError("controlled open failure")
    r = recording.Recorder(tmp_path / "open.gz", opener=failed)
    r.thread.join(5)
    with pytest.raises(RuntimeError, match="WRITE_FAILED"):
        r.append("FRAME", Clock())
    with pytest.raises(RuntimeError, match="WRITE_FAILED"):
        r.close()


@pytest.mark.parametrize("capacity", [0, -1, 1.5, True])
def test_unbounded_or_invalid_capacity_is_rejected(tmp_path, capacity):
    with pytest.raises(ValueError, match="POSITIVE_FIXED_CAPACITY"):
        recording.Recorder(tmp_path / "invalid.gz", capacity)


def test_contract_keeps_original_budget_compression_and_no_producer_wait():
    contract = recording.recording_contract(8192)
    assert contract["queue_capacity"] == 8192
    assert contract["maximum_queued_plus_inflight_records"] == 8193
    assert contract["gzip_compresslevel"] == 3 and contract["flush_every_records"] == 128
    assert contract["producer_wait_or_backpressure"] is False


def test_changed_native_recorder_requires_explicit_review(monkeypatch):
    monkeypatch.setattr(recording, "BASE_SOURCE_SHA256", "0" * 64)
    with pytest.raises(ValueError, match="NATIVE_BASE_CHANGED"):
        recording.recording_contract(8192)
