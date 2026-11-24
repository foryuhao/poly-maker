"""Compact M1 research logs; reuse the existing bounded writer lifecycle."""

import gzip
import json
import time
from queue import Empty

from polymaker.rdp_sm_ca_writer import CAWriter


class M1ObserverWriter(CAWriter):
    def __init__(self, path, *, capacity=2048, listener=None):
        self.filtered_decisions = 0
        self.listener = listener
        super().__init__(path, capacity=capacity)

    def submit(self, record):
        if self.listener is not None:
            try:
                self.listener(record)
            except Exception as exc:
                self.failure = "M1_PAPER_LISTENER_FAILED:"+type(exc).__name__
                self.dropped += 1
                return False
        # Accepted per-level decisions duplicate RAW_FRAME. Preserve every raw
        # frame, rejection and signal fact; counters remain in the detector.
        if record.get("event") == "EVENT_DECISION" and record.get("accepted"):
            self.filtered_decisions += 1
            return not self.failure and not self.stopping.is_set()
        return super().submit(record)

    def status(self):
        return {**super().status(), "filtered_accepted_decisions": self.filtered_decisions,
                "format": "gzip-jsonl", "raw_frames_preserved": True}

    def _run(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(self.path, "xt", encoding="utf-8", compresslevel=1) as file:
                flushed = time.monotonic()
                while not self.stopping.is_set() or not self.queue.empty():
                    try:
                        batch = [self.queue.get(timeout=.05)]
                    except Empty:
                        file.flush()
                        continue
                    for _ in range(127):
                        try:
                            batch.append(self.queue.get_nowait())
                        except Empty:
                            break
                    try:
                        wall = time.time()
                        file.write("".join(json.dumps({**row, "written_wall": wall},
                            ensure_ascii=False, allow_nan=False, separators=(",", ":"))+"\n"
                            for row in batch))
                        self.written += len(batch)
                        if time.monotonic()-flushed >= .25:
                            file.flush()
                            flushed = time.monotonic()
                    finally:
                        for _ in batch:
                            self.queue.task_done()
        except Exception as exc:
            self.failure = "M1_LOG_FAILED:"+type(exc).__name__
