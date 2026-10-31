"""Capture authoritative external post counts and contemporaneous CLOB snapshots."""

import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx

TRACKER = "https://xtracker.polymarket.com/api"


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--seconds", type=float, default=7200)
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=True)
    end = time.monotonic() + a.seconds
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(TRACKER + "/users/elonmusk/trackings", params={"activeOnly": "true"})
        r.raise_for_status()
        trackings = r.json()["data"]
        (a.output / "trackings.json").write_text(json.dumps(trackings, indent=2))
        selected = [t for t in trackings if "October 29 - October 31" in t["title"]]
        r = await c.get(
            "https://gamma-api.polymarket.com/events",
            params={"slug": "elon-musk-of-tweets-october-29-october-31-2026"},
        )
        r.raise_for_status()
        events = r.json()
        (a.output / "event_rules.json").write_text(json.dumps(events, indent=2))
        tokens = []
        for event in events:
            for market in event.get("markets", []):
                v = market.get("clobTokenIds", [])
                tokens.extend(json.loads(v) if isinstance(v, str) else v)
        with (a.output / "context.jsonl").open("x", buffering=1) as f:

            async def capture(url, params=None):
                before = time.time()
                try:
                    r = await c.get(url, params=params)
                    r.raise_for_status()
                    row = dict(
                        requested=before,
                        received=time.time(),
                        url=str(r.url),
                        status=r.status_code,
                        data=r.json(),
                    )
                except Exception as exc:
                    row = dict(requested=before, received=time.time(), url=url, error=repr(exc))
                f.write(json.dumps(row) + "\n")

            while time.monotonic() < end:
                await asyncio.gather(
                    *(
                        capture(TRACKER + "/trackings/" + t["id"], {"includeStats": "true"})
                        for t in selected
                    )
                )
                # At most one bounded batched snapshot every 30s; read-only POST /books.
                before = time.time()
                try:
                    r = await c.post(
                        "https://clob.polymarket.com/books", json=[dict(token_id=t) for t in tokens]
                    )
                    r.raise_for_status()
                    f.write(
                        json.dumps(
                            dict(
                                requested=before,
                                received=time.time(),
                                url=str(r.url),
                                data=r.json(),
                            )
                        )
                        + "\n"
                    )
                except Exception as exc:
                    f.write(
                        json.dumps(dict(requested=before, received=time.time(), error=repr(exc)))
                        + "\n"
                    )
                await asyncio.sleep(min(30, max(0, end - time.monotonic())))


if __name__ == "__main__":
    asyncio.run(main())
