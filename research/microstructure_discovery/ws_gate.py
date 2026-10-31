"""Public in-memory book gate; no order placement and no queue-fill assumptions."""

import asyncio
import gzip
import json
import time

import websockets
from information_lead import cash_fill
from study import WS, Book


class Gate:
    def __init__(self, assets):
        self.assets = set(assets)
        self.books = {}
        self.state = {}
        self.generation = 0
        self.connected = False
        self.last_frame = 0.0

    def reset(self):
        self.generation += 1
        self.books.clear()
        self.state.clear()
        self.connected = False

    def update(self, event, received):
        self.last_frame = received
        kind = event.get("event_type")
        if kind == "book" and event.get("asset_id") in self.assets:
            asset = event["asset_id"]
            b = Book()
            b.update(event)
            self.books[asset] = b
            stamp = float(event.get("timestamp", 0))
            previous = self.state.get(asset, {})
            self.state[asset] = dict(
                dirty=False,
                regressed=previous.get("regressed", False) or stamp < previous.get("source", 0),
                source=stamp,
                received=received,
            )
        elif kind == "price_change":
            for c in event.get("price_changes", []):
                asset = c.get("asset_id")
                if asset not in self.books:
                    continue
                b, state = self.books[asset], self.state[asset]
                b.update(dict(c, event_type=kind))
                stamp = float(event.get("timestamp", 0))
                state["regressed"] |= stamp < state["source"]
                state.update(source=max(stamp, state["source"]), received=received)
            for c in event.get("price_changes", []):
                asset = c.get("asset_id")
                if asset not in self.books:
                    continue
                q = self.books[asset].quote()
                self.state[asset]["dirty"] |= bool(
                    q
                    and (
                        abs(q["bid"] - float(c.get("best_bid", q["bid"]))) > 1e-8
                        or abs(q["ask"] - float(c.get("best_ask", q["ask"]))) > 1e-8
                    )
                )

    def check(self, asset, spec, now):
        b = self.books.get(asset)
        quote = b.quote() if b else None
        state = self.state.get(asset, {})
        eligible = bool(
            self.connected
            and 0 <= now - self.last_frame <= 0.5
            and b
            and b.valid
            and quote is not None
            and not state.get("dirty")
            and not state.get("regressed")
        )
        fill = None
        if eligible:
            fill = cash_fill(
                dict(asks=[dict(price=p, size=q) for p, q in b.asks.items()]),
                spec["poly_budget"],
                spec["poly_cap"],
                spec["rate"],
            )
        return dict(
            eligible=bool(fill and fill["token_floor"] >= 10 - 1e-8),
            received=now,
            generation=self.generation,
            connected=self.connected,
            last_frame_received=self.last_frame,
            state=state.copy(),
            quote=quote,
            cash_fill=fill,
        )

    async def collect(self, output, stop, counts):
        with gzip.open(output / "gate_raw.jsonl.gz", "wt", compresslevel=1) as raw:
            while time.time() < stop:
                self.reset()
                try:
                    async with websockets.connect(
                        WS, ping_interval=15, ping_timeout=15, max_size=2**24
                    ) as ws:
                        await ws.send(
                            json.dumps(
                                dict(
                                    assets_ids=sorted(self.assets),
                                    type="market",
                                    custom_feature_enabled=True,
                                )
                            )
                        )
                        self.connected = True
                        last_ping = last_flush = time.monotonic()
                        while time.time() < stop:
                            if time.monotonic() - last_ping >= 10:
                                await ws.send("PING")
                                last_ping = time.monotonic()
                            try:
                                msg = await asyncio.wait_for(ws.recv(), 1)
                            except TimeoutError:
                                msg = None
                            now = time.time()
                            if msg:
                                self.last_frame = now
                            if msg and msg not in ("PING", "PONG"):
                                data = json.loads(msg)
                                for event in data if isinstance(data, list) else [data]:
                                    raw.write(
                                        json.dumps(
                                            dict(
                                                received=now,
                                                generation=self.generation,
                                                event=event,
                                            ),
                                            separators=(",", ":"),
                                        )
                                        + "\n"
                                    )
                                    self.update(event, now)
                                    counts["ws_messages"] += 1
                            if time.monotonic() - last_flush >= 1:
                                raw.flush()
                                last_flush = time.monotonic()
                except Exception as exc:
                    counts["ws_disconnects"] += 1
                    self.connected = False
                    raw.write(
                        json.dumps(
                            dict(received=time.time(), generation=self.generation, error=repr(exc))
                        )
                        + "\n"
                    )
                    raw.flush()
                    await asyncio.sleep(min(1, max(0, stop - time.time())))
                finally:
                    self.connected = False
