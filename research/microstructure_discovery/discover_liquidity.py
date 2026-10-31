"""Discover a fresh passive-study universe from observable spread, flow and fee metadata."""

import argparse
import asyncio
import collections
import json
import math
import time
from pathlib import Path

import httpx
from study import array, category


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--limit", type=int, default=32)
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=False)
    async with httpx.AsyncClient(timeout=30) as c:
        rows = []
        for offset in range(0, 1000, 100):
            r = await c.get(
                "https://gamma-api.polymarket.com/markets",
                params=dict(
                    active="true",
                    closed="false",
                    order="volume24hr",
                    ascending="false",
                    limit=100,
                    offset=offset,
                ),
            )
            r.raise_for_status()
            rows.extend(r.json())
        (a.output / "discovery_raw.json").write_text(json.dumps(rows))
        unique = {m["conditionId"]: m for m in rows}
        candidates = []
        for m in unique.values():
            ts = array(m.get("clobTokenIds", []))
            ps = array(m.get("outcomePrices", []))
            vol = float(m.get("volume24hr", 0))
            tick = float(m.get("orderPriceMinTickSize", 0.01))
            spread = float(m.get("spread", 0))
            if (
                len(ts) != 2
                or not m.get("acceptingOrders")
                or not m.get("enableOrderBook")
                or len(ps) != 2
            ):
                continue
            price = float(ps[0])
            if not 0.02 <= price <= 0.98 or vol < 5000 or spread < 2 * tick - 1e-9 or spread > 0.05:
                continue
            score = math.log1p(vol) * spread / tick
            parent = m.get("events", [{}])[0].get("id", m["conditionId"])
            candidates.append((score, parent, m))
        candidates.sort(key=lambda r: r[0], reverse=True)
        selected = []
        parents = collections.Counter()
        cats = collections.Counter()
        for score, parent, m in candidates:
            cat = category(m)
            if parents[parent] >= 2 or cats[cat] >= max(8, a.limit // 2):
                continue
            ids = array(m["clobTokenIds"])
            # Confirm the spread is currently inside-quote eligible, rather than stale Gamma state.
            r = await c.get("https://clob.polymarket.com/book", params={"token_id": ids[0]})
            if r.status_code != 200:
                continue
            b = r.json()
            bid = max((float(v["price"]) for v in b.get("bids", [])), default=None)
            ask = min((float(v["price"]) for v in b.get("asks", [])), default=None)
            if (
                bid is None
                or ask is None
                or not 0 < bid < ask < 1
                or ask - bid < 2 * tick - 1e-9
                or ask - bid > 0.05
            ):
                continue
            if float(b.get("min_order_size", 5)) > 5:
                continue
            selected.append((score, parent, m, dict(received=time.time(), book=b)))
            parents[parent] += 1
            cats[cat] += 1
            if len(selected) >= a.limit:
                break
    meta = []
    checks = []
    for score, parent, m, b in selected:
        checks.append(dict(market=m["conditionId"], score=score, parent=parent, **b))
        for tok, outcome in zip(array(m["clobTokenIds"]), array(m["outcomes"]), strict=True):
            meta.append(
                dict(
                    asset=tok,
                    outcome=outcome,
                    market=m["conditionId"],
                    question=m["question"],
                    slug=m["slug"],
                    category=category(m),
                    volume24hr=m.get("volume24hr"),
                    tick=m.get("orderPriceMinTickSize", 0.01),
                    parent_event_id=parent,
                )
            )
    (a.output / "markets.json").write_text(json.dumps(meta, indent=2))
    (a.output / "initial_books.json").write_text(json.dumps(checks, indent=2))
    (a.output / "discovery_summary.json").write_text(
        json.dumps(
            dict(
                created=time.time(),
                raw_rows=len(rows),
                unique_markets=len(unique),
                eligible=len(candidates),
                selected=len(selected),
                categories=dict(cats),
                definition="Top1000 volume binary markets; price.02-.98,volume>=5000,spread2ticks..0.05; rank log(1+volume)*spread/tick;max2 per parent;confirm current book and5-share minimum.",
            ),
            indent=2,
        )
    )
    print(json.dumps(dict(selected=len(selected), categories=dict(cats), path=str(a.output))))


if __name__ == "__main__":
    asyncio.run(main())
