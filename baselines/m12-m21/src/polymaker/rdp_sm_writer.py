"""Single bounded durable writer. No serialization or disk I/O on operations."""

import asyncio
import time
from concurrent.futures import Future
from queue import Empty, Full, Queue
from threading import Thread


class AsyncRiskWriter:
    def __init__(self, journal, capacity=4096, *, batch_size=1):
        if not isinstance(batch_size, int) or batch_size < 1:
            raise ValueError("ASYNC_JOURNAL_BATCH_SIZE_INVALID")
        if batch_size > 1 and not callable(getattr(journal, "write_batch", None)):
            raise ValueError("ASYNC_JOURNAL_BATCH_UNSUPPORTED")
        self.journal = journal
        self.batch_size = batch_size
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
            batch = [item]
            # Drain only records already queued: no timer and no waiting for
            # a fuller batch on a prepared-order durability barrier.
            while len(batch) < self.batch_size:
                try:
                    batch.append(self.queue.get_nowait())
                except Empty:
                    break
            try:
                if self.failure is not None:
                    raise RuntimeError(self.failure)
                records = []
                for seq, kind, payload, ts, mono, _future in batch:
                    if kind is None:
                        continue
                    data = {
                        **payload,
                        "writer_sequence": seq,
                        "occurred_monotonic": mono,
                        "written_at": time.time(),
                        "written_monotonic": time.monotonic(),
                        "writer_queue_depth": self.queue.qsize(),
                        "diagnostics_dropped": self.dropped,
                    }
                    records.append((kind, data, ts))
                if records:
                    if self.batch_size > 1:
                        self.journal.write_batch(records)
                    else:
                        self.journal.write(*records[0])
                # Every record in the batch, including records before a flush
                # marker, must be fsynced before any future acknowledges it.
                for seq, _, _, _, _, future in batch:
                    self.written = seq
                    if not future.done():
                        future.set_result(seq)
            except BaseException as exc:
                self.failure = f"ASYNC_JOURNAL_FAILED:{type(exc).__name__}"
                for _, _, _, _, _, future in batch:
                    if not future.done():
                        future.set_exception(exc)
            finally:
                for _ in batch:
                    self.queue.task_done()

    def close(self):
        self.closed = True
        self.thread.join()
        self.journal.close()
