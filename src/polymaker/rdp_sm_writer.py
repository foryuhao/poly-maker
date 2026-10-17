"""Single bounded durable writer. No serialization or disk I/O on operations."""

import asyncio
import time
from concurrent.futures import Future
from queue import Empty, Full, Queue
from threading import Thread


class AsyncRiskWriter:
    def __init__(self, journal, capacity=4096):
        self.journal = journal
        self.queue = Queue(capacity)
        self.failure = None
        self.dropped = 0
        self.accepted = self.written = 0
        self.closed = False
        self.thread = Thread(target=self._run, name="rdp-risk-writer", daemon=True)
        self.thread.start()

    @property
    def path(self):
        return self.journal.path

    @property
    def sequence(self):
        return self.journal.sequence

    @property
    def ready(self):
        return (
            not self.closed
            and self.failure is None
            and self.queue.qsize() < self.queue.maxsize - 128
        )

    def _put(self, kind, payload, ts):
        future = Future()
        if self.closed or self.failure is not None:
            future.set_exception(RuntimeError("ASYNC_JOURNAL_FAILED"))
            return future
        try:
            self.accepted += 1
            self.queue.put_nowait((self.accepted, kind, payload, ts, time.monotonic(), future))
        except Full:
            self.failure = "ASYNC_JOURNAL_CAPACITY"
            future.set_exception(RuntimeError(self.failure))
        return future

    def write(self, kind, payload, ts):
        # Duplicate diagnostics are expendable; order facts are never silently dropped.
        if not self.ready and kind in {"sm_quote_skipped", "sm_obi_wait"}:
            self.dropped += 1
            return
        self._put(kind, payload, ts)

    async def durable(self, kind, payload, ts):
        return await self._wait(self._put(kind, payload, ts))

    async def _wait(self, future):
        wrapped = asyncio.wrap_future(future)
        interrupted = False
        while True:
            try:
                result = await asyncio.shield(wrapped)
                break
            except asyncio.CancelledError:
                interrupted = True
        if interrupted:
            raise asyncio.CancelledError()
        return result

    async def flush(self):
        await self._wait(self._put(None, None, time.time()))
        if self.failure is not None:
            raise RuntimeError(self.failure)

    def _run(self):
        while True:
            try:
                item = self.queue.get(timeout=0.1)
            except Empty:
                if self.closed:
                    return
                continue
            seq, kind, payload, ts, mono, future = item
            try:
                if self.failure is not None:
                    raise RuntimeError(self.failure)
                if kind is not None:
                    data = {
                        **payload,
                        "writer_sequence": seq,
                        "occurred_monotonic": mono,
                        "written_at": time.time(),
                        "written_monotonic": time.monotonic(),
                        "writer_queue_depth": self.queue.qsize(),
                        "diagnostics_dropped": self.dropped,
                    }
                    self.journal.write(kind, data, ts)
                self.written = seq
                if not future.done():
                    future.set_result(seq)
            except BaseException as exc:
                self.failure = f"ASYNC_JOURNAL_FAILED:{type(exc).__name__}"
                if not future.done():
                    future.set_exception(exc)
            finally:
                self.queue.task_done()

    def close(self):
        self.closed = True
        self.thread.join()
        self.journal.close()
