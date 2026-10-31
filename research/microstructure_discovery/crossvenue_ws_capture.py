"""Bounded raw market-feed evidence for every fixed cross-venue token."""

import argparse
import asyncio
import gzip
import hashlib
import json
import time
from pathlib import Path

import websockets
from study import WS, array

SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("root", type=Path)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--seconds", type=int, default=43200)
    a = p.parse_args()
    a.output.mkdir(exist_ok=False)
    markets = {
        p["market"]["conditionId"]: p["market"]
        for p in json.loads((a.root / "mappings.json").read_text())
    }
    meta = []
    for cid, m in markets.items():
        for token, label in zip(array(m["clobTokenIds"]), array(m["outcomes"]), strict=True):
            meta.append(
                dict(
                    asset=token,
                    market=cid,
                    outcome=label,
                    question=m["question"],
                    tick=m.get("orderPriceMinTickSize", 0.01),
                )
            )
    (a.output / "markets.json").write_text(json.dumps(meta, indent=2))
    stop = time.time() + a.seconds
    (a.output / "registration.json").write_text(
        json.dumps(
            dict(
                created=time.time(),
                stop=stop,
                source=WS,
                code_sha256=SOURCE_SHA256,
                assets=len(meta),
                raw_only=True,
                orders=False,
            ),
            indent=2,
        )
    )
    generation = 0
    lines = 0
    with gzip.open(a.output / "raw.jsonl.gz", "wt", compresslevel=1) as raw:

        def write(row):
            nonlocal lines
            raw.write(json.dumps(row, separators=(",", ":")) + "\n")
            lines += 1

        while time.time() < stop:
            try:
                async with websockets.connect(
                    WS, ping_interval=15, ping_timeout=15, max_size=2**24
                ) as ws:
                    generation += 1
                    await ws.send(
                        json.dumps(
                            dict(
                                assets_ids=[m["asset"] for m in meta],
                                type="market",
                                custom_feature_enabled=True,
                            )
                        )
                    )
                    last_ping = time.monotonic()
                    last_flush = time.monotonic()
                    while time.time() < stop:
                        if time.monotonic() - last_ping >= 10:
                            await ws.send("PING")
                            last_ping = time.monotonic()
                        try:
                            msg = await asyncio.wait_for(ws.recv(), 1)
                        except TimeoutError:
                            msg = None
                        now = time.time()
                        if msg and msg not in ("PING", "PONG"):
                            data = json.loads(msg)
                            for event in data if isinstance(data, list) else [data]:
                                write(dict(received=now, generation=generation, event=event))
                        if time.monotonic() - last_flush >= 1:
                            raw.flush()
                            last_flush = time.monotonic()
            except Exception as exc:
                write(dict(received=time.time(), generation=generation, error=repr(exc)))
                raw.flush()
                await asyncio.sleep(min(2, max(0, stop - time.time())))
    (a.output / "complete.json").write_text(
        json.dumps(dict(completed=time.time(), generations=generation, lines=lines))
    )


if __name__ == "__main__":
    asyncio.run(main())
