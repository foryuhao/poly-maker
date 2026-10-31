"""Exploratory actual-taker endgame calibration; block time is NOT order time."""

import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path

import httpx


async def collect(output, slots, asset, cutoff):
    sem = asyncio.Semaphore(3)
    async with httpx.AsyncClient(timeout=30) as client:

        async def get(url, params):
            for attempt in range(5):
                async with sem:
                    r = await client.get(url, params=params)
                if r.status_code in (429, 500, 502, 503, 504):
                    await asyncio.sleep(2**attempt)
                    continue
                r.raise_for_status()
                return r.json()
            raise RuntimeError(f"request retries exhausted: {url}")

        async def one(start):
            path = output / f"{start}.json"
            if path.exists():
                return json.loads(path.read_text())
            slug = f"{asset}-updown-5m-{start}"
            try:
                ms = await get(
                    "https://gamma-api.polymarket.com/markets", {"slug": slug, "closed": "true"}
                )
                if len(ms) != 1:
                    return {"slot": start, "error": "missing_or_ambiguous_closed_market"}
                m = ms[0]
                cid = m["conditionId"]
                resolution = await get(
                    "https://data-api.polymarket.com/v2/resolutions", {"condition": cid}
                )
                trades, cursor, pages = [], None, 0
                while True:
                    params = {"condition": cid, "limit": 1000, "side": "BUY", "taker_only": "true"}
                    if cursor:
                        params["cursor"] = cursor
                    data = await get("https://data-api.polymarket.com/v2/trades", params)
                    trades.extend(
                        {
                            k: t[k]
                            for k in [
                                "condition_id",
                                "token_id",
                                "price",
                                "size",
                                "timestamp",
                                "outcome_index",
                                "side",
                                "transaction_hash",
                            ]
                        }
                        for t in data["data"]
                    )
                    pages += 1
                    cursor = data["pagination"].get("next_cursor")
                    if not cursor:
                        break
                    if pages >= 100:
                        raise RuntimeError("pagination cap reached")
                row = dict(
                    slot=start,
                    market=m,
                    resolution=resolution,
                    trades=trades,
                    pages=pages,
                    retrieved=time.time(),
                )
                path.write_text(json.dumps(row))
                return row
            except Exception as e:
                return {"slot": start, "error": str(e)}

        return await asyncio.gather(*(one(cutoff - 300 * (i + 1)) for i in range(slots)))


def analyze(rows, confirmation=False):
    groups, excluded = [], []
    valid = []
    for r in rows:
        if "error" in r:
            excluded.append(r)
            continue
        rs = r["resolution"]["data"]
        if (
            len(rs) != 1
            or rs[0].get("status") != "resolved"
            or rs[0].get("payouts") not in ([1000000, 0], [0, 1000000])
        ):
            excluded.append(dict(slot=r["slot"], error="unverified_binary_resolution"))
            continue
        fee = r["market"].get("feeSchedule")
        if (
            not fee
            or not fee.get("takerOnly")
            or fee.get("rate") is None
            or fee.get("exponent") is None
        ):
            excluded.append(dict(slot=r["slot"], error="unverified_fee"))
            continue
        valid.append((r, rs[0]["payouts"], fee))
    for seconds in [30] if confirmation else [60, 30, 15, 5]:
        for floor in [0.99] if confirmation else [0.90, 0.95, 0.97, 0.98, 0.99]:
            positions = []
            for r, payout, fee in valid:
                end = r["slot"] + 300
                ts = [
                    t
                    for t in r["trades"]
                    if end - seconds <= t["timestamp"] < end
                    and floor <= t["price"] <= 0.995
                    and t["size"] >= 5
                    and t["outcome_index"] in (0, 1)
                ]
                if not ts:
                    continue
                # Price-independent tx ordering breaks block-time ties; never pick winner/best price.
                t = min(ts, key=lambda t: (t["timestamp"], t["transaction_hash"], t["token_id"]))
                p = t["price"]
                delivered = 1 - fee["rate"] * (p * (1 - p)) ** fee["exponent"] / p
                net = delivered * payout[t["outcome_index"]] / 1000000 - p - 0.002
                positions.append(
                    dict(
                        slot=r["slot"],
                        price=p,
                        outcome=t["outcome_index"],
                        won=bool(payout[t["outcome_index"]]),
                        seconds_before_end=end - t["timestamp"],
                        net_per_share=net,
                        transaction_hash=t["transaction_hash"],
                    )
                )
            groups.append(
                dict(
                    seconds=seconds,
                    floor=floor,
                    n=len(positions),
                    wins=sum(p["won"] for p in positions),
                    mean_net_per_share=sum(p["net_per_share"] for p in positions) / len(positions)
                    if positions
                    else None,
                    positions=positions,
                )
            )
    return dict(
        exploratory_only=True,
        limitations=[
            "block timestamp is settlement time, not execution time",
            "historical actual fills do not prove repeatable available depth",
            "20 policy variants inspected; fresh independent prospective validation required",
            "Gamma fee snapshot needs historical activation verification",
            "one slot is independent sampling unit, adjacent slots can be dependent",
        ],
        requested=len(rows),
        valid=len(valid),
        excluded=excluded,
        groups=groups,
    )


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--slots", type=int, default=144)
    p.add_argument("--asset", default="eth")
    p.add_argument("--cutoff", type=int)
    p.add_argument("--confirmation", action="store_true")
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=True)
    config = a.output / "config.json"
    if config.exists():
        cfg = json.loads(config.read_text())
        assert cfg["slots"] == a.slots and cfg["asset"] == a.asset
    else:
        cfg = dict(
            slots=a.slots,
            asset=a.asset,
            cutoff=a.cutoff or int(time.time()) // 300 * 300 - 900,
            created=time.time(),
            confirmation=a.confirmation,
        )
        config.write_text(json.dumps(cfg, indent=2))
    rows = await collect(a.output, a.slots, a.asset, cfg["cutoff"])
    result = analyze(rows, a.confirmation)
    (a.output / "screen.json").write_text(json.dumps(result, indent=2))
    (a.output / "manifest.json").write_text(
        json.dumps(
            {
                str(p.name): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in a.output.glob("*.json")
                if p.name != "manifest.json"
            },
            indent=2,
        )
    )
    print(
        json.dumps(
            {
                "requested": result["requested"],
                "valid": result["valid"],
                "excluded": len(result["excluded"]),
                "groups": [
                    {k: v for k, v in g.items() if k != "positions"} for g in result["groups"]
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
