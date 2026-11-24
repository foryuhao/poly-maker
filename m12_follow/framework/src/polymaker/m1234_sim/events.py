"""Ordered availability-time events and bounded, loss-visible compressed recording."""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import queue
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from polymaker.m1234_sim import INPUT_SCHEMA


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False)


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str, allow_nan=False) + "\n"
    )
    temporary.replace(path)


@dataclass
class Clock:
    mono: float = 0.0
    wall: float = 0.0

    def set(self, mono, wall):
        if (
            not all(math.isfinite(x) for x in (mono, wall))
            or mono < self.mono
            or self.wall
            and abs((wall - self.wall) - (mono - self.mono)) > 2
        ):
            raise ValueError("SIM_CLOCK_INVALID")
        self.mono, self.wall = mono, wall

    def monotonic(self):
        return self.mono

    def time(self):
        return self.wall


class Recorder:
    """A single writer owns gzip; failure prohibits publishing complete results.

    Frames are committed before processing. The recorded TIMER order is replayed
    verbatim; replay never inserts missed observer polling opportunities.
    """

    def __init__(self, path, capacity=8192, opener=gzip.open):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.queue = queue.Queue(capacity)
        self.failure = None
        self.seq = self.high_water = self.written = 0
        self.opener = opener
        self.thread = threading.Thread(target=self._write, name="m1234-recorder", daemon=True)
        self.thread.start()

    def fail(self, reason, event=None):
        if self.failure is None:
            self.failure = {
                "reason": reason,
                "first_missing_seq": self.seq,
                "event": event,
                "written_at_failure": self.written,
            }
            # Independent small sidecar survives queue overflow; IO failures
            # also propagate to the caller even if this sidecar cannot be saved.
            with suppress(OSError):
                write_json(self.path.with_suffix(".failure.json"), self.failure)

    def append(self, kind, clock, generation=0, **payload):
        if self.failure:
            raise RuntimeError("SIM_RECORDING_FAILED:" + self.failure["reason"])
        self.seq += 1
        event = {
            "schema": INPUT_SCHEMA,
            "seq": self.seq,
            "kind": kind,
            "available_mono": clock.mono,
            "available_wall": clock.wall,
            "generation": generation,
            **payload,
        }
        try:
            self.queue.put_nowait(encode(event) + "\n")
            self.high_water = max(self.high_water, self.queue.qsize())
        except queue.Full as exc:
            self.fail("EVENT_BUFFER_OVERFLOW", event)
            raise RuntimeError("EVENT_BUFFER_OVERFLOW") from exc
        return event

    def _write(self):
        try:
            with self.opener(self.path, "at", encoding="utf-8", compresslevel=3) as stream:
                while True:
                    item = self.queue.get()
                    if item is None:
                        break
                    stream.write(item)
                    self.written += 1
                    if self.written % 128 == 0:
                        stream.flush()
        except Exception as exc:
            self.fail("WRITE_FAILED:" + type(exc).__name__)

    def close(self):
        deadline = time.monotonic() + 10
        while self.thread.is_alive() and time.monotonic() < deadline:
            try:
                self.queue.put(None, timeout=min(0.1, max(0, deadline - time.monotonic())))
                break
            except queue.Full:
                continue
        self.thread.join(timeout=max(0, deadline - time.monotonic()))
        if self.thread.is_alive():
            self.fail("WRITER_CLOSE_TIMEOUT")
        if self.failure:
            raise RuntimeError("SIM_RECORDING_FAILED:" + self.failure["reason"])


def read_events(path):
    previous = 0
    with gzip.open(path, "rt") as source:
        for line in source:
            row = json.loads(line)
            if row.get("schema") != INPUT_SCHEMA:
                raise ValueError("SIM_INPUT_SCHEMA_VERSION")
            if row["seq"] != previous + 1:
                raise ValueError("SIM_INPUT_SEQUENCE_GAP")
            previous = row["seq"]
            yield row
