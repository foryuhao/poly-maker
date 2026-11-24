"""M6-only socket queue. Applying a frame and retiring its receipt are atomic."""
import asyncio
import json

import websockets

from polymaker.rdp_sm_marketdata import WS_CLOSE_TIMEOUT_SEC, WS_HEARTBEAT_SEC


class AtomicCapture:
    def consume_received(self, raw, receipt, wall):
        with self.lock:
            if not self.m1_pending_received or self.m1_pending_received[0] != receipt:
                self.invalidate("M6_QUEUE_RECEIPT_CONFLICT")
                raise ValueError("M6_QUEUE_RECEIPT_CONFLICT")
            try:
                self.apply(raw, received_mono=receipt, received_wall=wall)
            except BaseException:
                # Never expose a partially applied frame to an SDK guard.
                self.invalidate("M6_FRAME_APPLY_FAILED")
                raise
            finally:
                self.m1_pending_received.popleft()
                self.input_revision += 1
                self.changed.set()

    async def _connection(self):
        kwargs = {"ping_interval": 5, "ping_timeout": 10, "open_timeout": 10,
                  "close_timeout": WS_CLOSE_TIMEOUT_SEC}
        if self.proxy:
            kwargs["proxy"] = self.proxy
        async with websockets.connect(
            "wss://ws-subscriptions-clob.polymarket.com/ws/market", **kwargs
        ) as ws:
            self.ws = ws
            try:
                self.connect()
                await ws.send(json.dumps({"assets_ids": list(self.books), "type": "market"}))
            except BaseException:
                try:
                    self.invalidate('WS_DISCONNECTED')
                finally:
                    self.ws = None
                raise
            queue = asyncio.Queue(maxsize=2048)

            async def reader():
                async for raw in ws:
                    try:
                        with self.lock:
                            receipt = self.monotonic()
                            queue.put_nowait((raw, receipt, self.wall()))
                            self.m1_pending_received.append(receipt)
                    except asyncio.QueueFull as exc:
                        self.invalidate("WS_QUEUE_OVERFLOW")
                        raise ValueError("WS_QUEUE_OVERFLOW") from exc
                    # Fairness without dropping, merging or changing timestamps.
                    await asyncio.sleep(0)

            async def consumer():
                while True:
                    raw, receipt, wall = await queue.get()
                    self.consume_received(raw, receipt, wall)
                    await asyncio.sleep(0)

            async def heartbeat():
                while True:
                    await ws.send("PING")
                    await asyncio.sleep(WS_HEARTBEAT_SEC)

            tasks = [asyncio.create_task(reader()), asyncio.create_task(consumer()),
                     asyncio.create_task(heartbeat())]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
            finally:
                try:
                    self.invalidate("WS_DISCONNECTED")
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    with self.lock:
                        self.m1_pending_received.clear()
                        self.m1_processing_received = None
                        self.input_revision += 1
                        self.changed.set()
                    self.ws = None
