"""Bounded public-only final-minute evidence capture; never places orders."""

import argparse
import asyncio
import gzip
import hashlib
import json
import time
from pathlib import Path

import httpx
import websockets
from study import Book, WS, array


def dump(path, value):
    path.write_text(json.dumps(value, indent=2))


async def sleep_until(target):
    while time.time() < target:
        await asyncio.sleep(min(30, target - time.time()))


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--seconds", type=int, default=86400)
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=False)
    start = time.time()
    stop = start + a.seconds
    cfg = dict(
        start=start,
        stop=stop,
        asset="eth",
        quantity=5,
        window=30,
        ask=0.99,
        delays=[0.5, 1, 2],
        simulated_orders_only=True,
    )
    dump(a.output / "config.json", cfg)
    dump(
        a.output / "frozen_sources.json",
        {
            str(f): hashlib.sha256(f.read_bytes()).hexdigest()
            for f in [
                Path(__file__),
                Path(__file__).with_name("PROTOCOL_V5_ENDGAME.md"),
                Path(__file__).with_name("study.py"),
            ]
        },
    )
    slot = int(start) // 300 * 300
    async with httpx.AsyncClient(timeout=15) as client:
        while slot + 300 < stop:
            end = slot + 300
            if time.time() >= end - 5:
                slot += 300
                continue
            await sleep_until(end - 75)
            folder = a.output / str(slot)
            folder.mkdir(exist_ok=False)
            try:
                r = await client.get(
                    "https://gamma-api.polymarket.com/markets",
                    params={"slug": f"eth-updown-5m-{slot}"},
                )
                r.raise_for_status()
                markets = r.json()
                dump(folder / "market.json", markets)
                assert len(markets) == 1
                m = markets[0]
                assert (
                    "time-weighted average" in m["description"] and "Chainlink" in m["description"]
                )
                r = await client.get(f"https://clob.polymarket.com/clob-markets/{m['conditionId']}")
                dump(
                    folder / "clob_fee.json",
                    dict(status=r.status_code, received=time.time(), data=r.json()),
                )
                assets = array(m["clobTokenIds"])
                books = {asset: Book() for asset in assets}
                await sleep_until(end - 60)
                generation, decision, samples, event_line = 0, None, {}, 0
                with (
                    gzip.open(folder / "raw.jsonl.gz", "wt") as raw,
                    (folder / "snapshots.jsonl").open("w") as snapshots,
                ):

                    def write(file, obj):
                        file.write(json.dumps(obj, separators=(",", ":")) + "\n")
                        file.flush()

                    def view():
                        return {
                            asset: dict(
                                valid=b.valid,
                                bids=sorted(b.bids.items(), reverse=True)[:8],
                                asks=sorted(b.asks.items())[:8],
                            )
                            for asset, b in books.items()
                        }

                    while time.time() < end + 5:
                        try:
                            async with websockets.connect(
                                WS, ping_interval=15, ping_timeout=10, max_size=2**23
                            ) as ws:
                                generation += 1
                                for b in books.values():
                                    b.valid = False
                                await ws.send(
                                    json.dumps(
                                        dict(
                                            assets_ids=assets,
                                            type="market",
                                            custom_feature_enabled=True,
                                        )
                                    )
                                )
                                last_ping = time.time()
                                while time.time() < end + 5:
                                    if time.time() - last_ping >= 10:
                                        await ws.send("PING")
                                        last_ping = time.time()
                                    try:
                                        msg = await asyncio.wait_for(ws.recv(), timeout=0.05)
                                    except TimeoutError:
                                        msg = None
                                    now = time.time()
                                    if msg and msg not in ("PING", "PONG"):
                                        payload = json.loads(msg)
                                        for ev in (
                                            payload if isinstance(payload, list) else [payload]
                                        ):
                                            event_line += 1
                                            write(
                                                raw,
                                                dict(received=now, generation=generation, event=ev),
                                            )
                                            kind = ev.get("event_type")
                                            if kind == "book" and ev.get("asset_id") in books:
                                                books[ev["asset_id"]].update(ev)
                                            elif kind == "price_change":
                                                for change in ev.get("price_changes", []):
                                                    if change.get("asset_id") in books:
                                                        b = books[change["asset_id"]]
                                                        b.update(dict(change, event_type=kind))
                                                        if (
                                                            b.bids
                                                            and b.asks
                                                            and (
                                                                abs(
                                                                    max(b.bids)
                                                                    - float(
                                                                        change.get(
                                                                            "best_bid", max(b.bids)
                                                                        )
                                                                    )
                                                                )
                                                                > 1e-8
                                                                or abs(
                                                                    min(b.asks)
                                                                    - float(
                                                                        change.get(
                                                                            "best_ask", min(b.asks)
                                                                        )
                                                                    )
                                                                )
                                                                > 1e-8
                                                            )
                                                        ):
                                                            b.valid = False
                                            elif kind in ("tick_size_change", "market_resolved"):
                                                for b in books.values():
                                                    b.valid = False
                                    if decision is None and end - 30 <= now < end:
                                        eligible = [
                                            asset
                                            for asset, b in books.items()
                                            if b.quote()
                                            and abs(min(b.asks) - 0.99) < 1e-8
                                            and sum(
                                                q
                                                for price, q in b.asks.items()
                                                if price <= 0.99 + 1e-9
                                            )
                                            >= 5
                                        ]
                                        if eligible:
                                            decision = dict(
                                                received=now,
                                                asset=min(eligible),
                                                generation=generation,
                                                line=event_line,
                                                books=view(),
                                            )
                                            dump(folder / "decision.json", decision)
                                    if decision:
                                        for delay in cfg["delays"]:
                                            if (
                                                str(delay) not in samples
                                                and now >= decision["received"] + delay
                                            ):
                                                row = dict(
                                                    received=now,
                                                    target=decision["received"] + delay,
                                                    delay=delay,
                                                    generation=generation,
                                                    line=event_line,
                                                    books=view(),
                                                )
                                                samples[str(delay)] = row
                                                write(snapshots, row)
                        except Exception as exc:
                            event_line += 1
                            write(
                                raw,
                                dict(received=time.time(), generation=generation, error=repr(exc)),
                            )
                            for b in books.values():
                                b.valid = False
                            await asyncio.sleep(0.5)
                dump(
                    folder / "capture_complete.json",
                    dict(
                        completed=time.time(),
                        lines=event_line,
                        generations=generation,
                        decision=bool(decision),
                    ),
                )
                # Finality is queried independently after capture; never used to choose an outcome.
                r = await client.get(
                    "https://data-api.polymarket.com/v2/resolutions",
                    params={"condition": m["conditionId"]},
                )
                dump(
                    folder / "resolution_initial.json",
                    dict(status=r.status_code, received=time.time(), data=r.json()),
                )
            except Exception as exc:
                dump(folder / "error.json", dict(received=time.time(), error=repr(exc)))
            slot += 300
    dump(a.output / "complete.json", dict(completed=time.time()))


if __name__ == "__main__":
    asyncio.run(main())
