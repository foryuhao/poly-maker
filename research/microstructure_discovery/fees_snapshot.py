"""Capture effective V2 fee parameters separately from legacy base-fee fields."""

import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("root", type=Path)
    a = p.parse_args()
    root = a.root
    meta = json.loads((root / "markets.json").read_text())
    gamma = {m["conditionId"]: m for m in json.loads((root / "discovery_raw.json").read_text())}
    unique = {m["market"]: m for m in meta}
    sem = asyncio.Semaphore(4)
    async with httpx.AsyncClient(timeout=25) as c:

        async def one(mid, m):
            async with sem:
                row = dict(
                    market=mid,
                    asset=m["asset"],
                    requested=time.time(),
                    gamma_feeSchedule=gamma.get(mid, {}).get("feeSchedule"),
                    gamma_feeType=gamma.get(mid, {}).get("feeType"),
                )
                for key, path, params in [
                    ("clob_info", "/clob-markets/" + mid, None),
                    ("legacy_fee", "/fee-rate", {"token_id": m["asset"]}),
                ]:
                    try:
                        r = await c.get("https://clob.polymarket.com" + path, params=params)
                        r.raise_for_status()
                        row[key] = r.json()
                    except Exception as exc:
                        row[key] = dict(error=repr(exc))
                row["received"] = time.time()
                fd = row["clob_info"].get("fd") or {}
                gs = row["gamma_feeSchedule"] or {}
                row["effective_parameters_agree"] = (
                    fd.get("r") is not None
                    and gs.get("rate") is not None
                    and abs(fd["r"] - gs["rate"]) < 1e-12
                    and fd.get("e") == gs.get("exponent")
                )
                return row

        result = await asyncio.gather(*(one(mid, m) for mid, m in unique.items()))
    out = root / "fees_provenance.json"
    with out.open("x") as f:
        json.dump(
            dict(
                captured=time.time(),
                source_interpretation="Installed py-clob-client-v2 client.get_clob_market_info caches fd.r/fd.e for effective fee calculation; legacy base_fee is separately preserved, never conflated.",
                markets=result,
            ),
            f,
            indent=2,
        )
    print(
        json.dumps(
            dict(
                markets=len(result),
                effective_matches=sum(r["effective_parameters_agree"] for r in result),
                path=str(out),
            )
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
