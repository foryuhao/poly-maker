"""Task-local bounded recorder batching and diagnostic counters.

Only M12--M23 forward processes install this writer. Availability clocks,
serialized records, FIFO order, compression level and 128-record flush boundaries
are unchanged. A batch must NOT increase the original queue+in-flight budget.
These local performance clocks are diagnostics, never market/signal input.
"""
import gzip
import hashlib
import queue
import time
from pathlib import Path

from polymaker.m1234_sim import INPUT_SCHEMA
from polymaker.m1234_sim import events

VERSION = "m1223-bounded-recorder-batch-v1"
BASE_SOURCE_SHA256 = "ad98f44239c39fa5f6e73a7e75d42c23ef4d6605a8069f2ec3eb0e8983e716e4"
MAX_BATCH_RECORDS = 64
MAX_BATCH_CHARACTERS = 65536
FLUSH_RECORDS = 128


def recording_contract(capacity):
    source = hashlib.sha256(Path(events.__file__).read_bytes()).hexdigest()
    if source != BASE_SOURCE_SHA256:
        raise ValueError("SOP_RECORDER_NATIVE_BASE_CHANGED_REVIEW_REQUIRED")
    return {
        "version": VERSION,
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "native_source_sha256": source,
        "scope": "RECORDING_ONLY_NOT_SIGNAL_OR_ECONOMIC_INPUT",
        "queue_capacity": capacity,
        "maximum_queued_plus_inflight_records": capacity + 1,
        "batch_records": MAX_BATCH_RECORDS,
        "batch_character_target": MAX_BATCH_CHARACTERS,
        "maximum_batch_size_rule": "TARGET_PLUS_AT_MOST_ONE_EXISTING_RECORD",
        "flush_every_records": FLUSH_RECORDS,
        "gzip_compresslevel": 3,
        "producer_wait_or_backpressure": False,
    }


class Recorder(events.Recorder):
    def __init__(self, path, capacity=8192, opener=gzip.open):
        if type(capacity) is not int or capacity <= 0:
            raise ValueError("SOP_RECORDER_POSITIVE_FIXED_CAPACITY_REQUIRED")
        self.outstanding_high_water = 0
        self.batch_count = self.maximum_batch_records = self.maximum_batch_characters = 0
        self.write_wall_seconds = self.write_cpu_seconds = self.maximum_write_seconds = 0.
        self.flush_wall_seconds = self.flush_cpu_seconds = self.maximum_flush_seconds = 0.
        self.flush_count = 0
        self.last_writer_progress_monotonic = None
        self.write_started_monotonic = None
        super().__init__(path, capacity, opener)

    def append(self, kind, clock, generation=0, **payload):
        if self.failure:
            raise RuntimeError("SIM_RECORDING_FAILED:" + self.failure["reason"])
        self.seq += 1
        event = {"schema": INPUT_SCHEMA, "seq": self.seq, "kind": kind,
                 "available_mono": clock.mono, "available_wall": clock.wall,
                 "generation": generation, **payload}
        try:
            # Legacy holds at most capacity queued plus one writer-owned record.
            # A 64-record batch may only borrow from this same total allowance.
            if self.seq - self.written > self.queue.maxsize + 1:
                raise queue.Full
            self.queue.put_nowait(events.encode(event) + "\n")
            self.high_water = max(self.high_water, self.queue.qsize())
            self.outstanding_high_water = max(self.outstanding_high_water,
                                              self.seq - self.written)
        except queue.Full as exc:
            self.fail("EVENT_BUFFER_OVERFLOW", event)
            raise RuntimeError("EVENT_BUFFER_OVERFLOW") from exc
        return event

    def _measure(self, stream, operation, value=None):
        start, cpu = time.monotonic(), time.thread_time()
        self.write_started_monotonic = start
        if operation == "write":
            stream.write(value)
        else:
            stream.flush()
        elapsed, used = time.monotonic() - start, time.thread_time() - cpu
        setattr(self, operation + "_wall_seconds",
                getattr(self, operation + "_wall_seconds") + elapsed)
        setattr(self, operation + "_cpu_seconds",
                getattr(self, operation + "_cpu_seconds") + used)
        key = "maximum_" + operation + "_seconds"
        setattr(self, key, max(getattr(self, key), elapsed))
        self.last_writer_progress_monotonic = time.monotonic()
        self.write_started_monotonic = None

    def _write(self):
        try:
            with self.opener(self.path, "at", encoding="utf-8", compresslevel=3) as stream:
                stopping = False
                while not stopping:
                    item = self.queue.get()
                    if item is None:
                        break
                    batch = [item]
                    characters = len(item)
                    limit = min(MAX_BATCH_RECORDS, FLUSH_RECORDS - self.written % FLUSH_RECORDS)
                    while len(batch) < limit and characters < MAX_BATCH_CHARACTERS:
                        try:
                            item = self.queue.get_nowait()
                        except queue.Empty:
                            break
                        if item is None:
                            stopping = True
                            break
                        batch.append(item)
                        characters += len(item)
                    self._measure(stream, "write", "".join(batch))
                    self.written += len(batch)
                    self.batch_count += 1
                    self.maximum_batch_records = max(self.maximum_batch_records, len(batch))
                    self.maximum_batch_characters = max(self.maximum_batch_characters, characters)
                    if self.written % FLUSH_RECORDS == 0:
                        self._measure(stream, "flush")
                        self.flush_count += 1
        except Exception as exc:
            self.fail("WRITE_FAILED:" + type(exc).__name__)

    def diagnostic_snapshot(self):
        return {"version": VERSION, "seq": self.seq, "written": self.written,
                "queue_records": self.queue.qsize(), "queue_capacity": self.queue.maxsize,
                "queue_high_water": self.high_water,
                "outstanding_records": self.seq - self.written,
                "outstanding_high_water": self.outstanding_high_water,
                "maximum_outstanding_records": self.queue.maxsize + 1,
                "batch_count": self.batch_count,
                "maximum_batch_records": self.maximum_batch_records,
                "maximum_batch_characters": self.maximum_batch_characters,
                "write_wall_seconds": self.write_wall_seconds,
                "write_cpu_seconds": self.write_cpu_seconds,
                "maximum_write_seconds": self.maximum_write_seconds,
                "flush_wall_seconds": self.flush_wall_seconds,
                "flush_cpu_seconds": self.flush_cpu_seconds,
                "maximum_flush_seconds": self.maximum_flush_seconds,
                "flush_count": self.flush_count,
                "write_started_monotonic": self.write_started_monotonic,
                "last_writer_progress_monotonic": self.last_writer_progress_monotonic,
                "writer_thread_alive": self.thread.is_alive(), "failure": self.failure}
