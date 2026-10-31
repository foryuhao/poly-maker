"""Public score receipt timestamps for the fixed cross-venue market universe."""

import argparse
import asyncio
import collections
import gzip
import hashlib
import json
import time
from pathlib import Path

import websockets

SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("root", type=Path)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--seconds", type=int, default=43200)
    a = p.parse_args()
    a.output.mkdir(exist_ok=False)
    start = time.time()
    stop = start + a.seconds
    ids = {
        str(v["event"]["gameId"])
        for v in json.loads((a.root / "mappings.json").read_text())
        if v["event"].get("gameId") is not None
    }
    (a.output / "registration.json").write_text(
        json.dumps(
            dict(
                created=start,
                stop=stop,
                game_ids=sorted(ids),
                source="wss://sports-api.polymarket.com/ws",
                code_sha256=SOURCE_SHA256,
                qualification="Provider receipt time is not original on-field event time; scores need primary-source confirmation for positive claims",
            ),
            indent=2,
        )
    )
    counts = collections.Counter()
    generation = 0
    with gzip.open(a.output / "raw.jsonl.gz", "wt") as f:

        def write(row):
            f.write(json.dumps(row, separators=(",", ":")) + "\n")
            f.flush()

        while time.time() < stop:
            try:
                async with websockets.connect(
                    "wss://sports-api.polymarket.com/ws",
                    ping_interval=15,
                    ping_timeout=15,
                    max_size=2**22,
                ) as ws:
                    generation += 1
                    while time.time() < stop:
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=1)
                        except TimeoutError:
                            continue
                        now = time.time()
                        if msg in ("ping", "PING"):
                            await ws.send("pong" if msg == "ping" else "PONG")
                            continue
                        if msg in ("pong", "PONG"):
                            continue
                        payload = json.loads(msg)
                        for event in payload if isinstance(payload, list) else [payload]:
                            counts["all"] += 1
                            if str(event.get("gameId")) in ids:
                                counts["selected"] += 1
                                write(dict(received=now, generation=generation, event=event))
            except Exception as exc:
                counts["errors"] += 1
                write(dict(received=time.time(), generation=generation, error=repr(exc)))
                await asyncio.sleep(min(2, max(0, stop - time.time())))
    (a.output / "complete.json").write_text(
        json.dumps(dict(completed=time.time(), counts=counts, generations=generation), indent=2)
    )


if __name__ == "__main__":
    asyncio.run(main())
