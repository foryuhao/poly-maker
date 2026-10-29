"""Best-effort CA sink, isolated from the mandatory execution/risk journal."""

import json
import time
from queue import Empty, Full, Queue
from threading import Event, Thread


class CAWriter:
    def __init__(self, path, *, capacity=2048):
        self.path = path
        self.queue = Queue(capacity)
        self.failure = None
        self.dropped = 0
        self.accepted = self.written = 0
        self.stopping = Event()
        self.thread = Thread(target=self._run, name="ca-observe-writer", daemon=True)
        self.thread.start()

    def submit(self, record):
        if self.failure or self.stopping.is_set():
            self.dropped += 1
            return False
        try:
            self.queue.put_nowait(record)
            self.accepted += 1
            return True
        except Full:
            self.dropped += 1
            self.failure = "CA_LOG_QUEUE_OVERFLOW"
            return False

    def status(self):
        return {"path": str(self.path), "failure": self.failure, "dropped": self.dropped,
                "accepted": self.accepted, "written": self.written}

    def _run(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # A session file is never silently truncated or mixed with an old run.
            with self.path.open("x", encoding="utf-8", buffering=1) as file:
                while not self.stopping.is_set() or not self.queue.empty():
                    try:
                        row = self.queue.get(timeout=.05)
                    except Empty:
                        continue
                    try:
                        file.write(json.dumps({**row, "written_wall": time.time()},
                                              ensure_ascii=False, allow_nan=False)+"\n")
                        self.written += 1
                    finally:
                        self.queue.task_done()
        except Exception as exc:
            # No exception or callback reaches the trade executor.
            self.failure = "CA_LOG_FAILED:"+type(exc).__name__

    def close(self):
        self.stopping.set()
        self.thread.join(.5)
        if self.thread.is_alive():
            self.failure = "CA_LOG_DRAIN_TIMEOUT"
