"""Fresh focused replication after a transient EPL cross-venue discount."""

import argparse
import asyncio
import gzip
import hashlib
import json
import time
from pathlib import Path

import httpx
from crossvenue_scan import KBASE, calculate

SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("root", type=Path)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--seconds", type=int, default=3600)
    a = p.parse_args()
    a.output.mkdir(exist_ok=False)
    stop = time.time() + a.seconds
    pair = next(
        p
        for p in json.loads((a.root / "mappings.json").read_text())
        if p["kalshi"]["ticker"] == "KXEPLGAME-26OCT31CFCMUN-MUN"
    )
    fee = json.loads((a.root / "fees.json").read_text())[pair["market"]["conditionId"]]
    fd = fee["clob"]["fd"]
    (a.output / "mappings.json").write_text(json.dumps([pair], indent=2))
    (a.output / "fees.json").write_text(json.dumps({pair["market"]["conditionId"]: fee}, indent=2))
    (a.output / "registration.json").write_text(
        json.dumps(
            dict(
                created=time.time(),
                stop=stop,
                source_sha256=SOURCE_SHA256,
                selection_basis="Original broad screen had one +.04046/contract discount at13:13:20UTC; manual followup88s later negative. This fresh period tests persistence and execution, not the old opportunity.",
                cadence_pause=0.2,
                quantity=10,
                orders=False,
            ),
            indent=2,
        )
    )
    async with httpx.AsyncClient(timeout=5) as client:
        with (
            gzip.open(a.output / "books.jsonl.gz", "wt") as raw,
            (a.output / "positive.jsonl").open("w", buffering=1) as pos,
        ):
            n = 0
            while time.time() < stop:
                try:

                    async def get(url, params):
                        start = time.time()
                        r = await client.get(url, params=params)
                        r.raise_for_status()
                        return dict(
                            requested=start,
                            received=time.time(),
                            headers={
                                k: v
                                for k, v in r.headers.items()
                                if k.lower() in ["date", "age", "cf-cache-status", "cache-control"]
                            },
                            data=r.json(),
                        )

                    yes, no, k = await asyncio.gather(
                        get("https://clob.polymarket.com/book", {"token_id": pair["poly_yes"]}),
                        get("https://clob.polymarket.com/book", {"token_id": pair["poly_no"]}),
                        get(
                            KBASE + "/markets/" + pair["kalshi"]["ticker"] + "/orderbook",
                            {"depth": 20},
                        ),
                    )
                    b = k["data"]["orderbook_fp"]
                    ky = [(1 - float(p), float(q)) for p, q in b.get("no_dollars", [])]
                    kn = [(1 - float(p), float(q)) for p, q in b.get("yes_dollars", [])]
                    calc = dict(
                        poly_yes_kalshi_no=calculate(yes["data"], kn, fd["r"], fd["e"]),
                        poly_no_kalshi_yes=calculate(no["data"], ky, fd["r"], fd["e"]),
                    )
                    skew = max(v["received"] for v in [yes, no, k]) - min(
                        v["received"] for v in [yes, no, k]
                    )
                    row = dict(
                        round=n,
                        market=pair["market"]["conditionId"],
                        ticker=pair["kalshi"]["ticker"],
                        yes=yes,
                        no=no,
                        kalshi=k,
                        receipt_skew=skew,
                        calculations=calc,
                    )
                    raw.write(json.dumps(row, separators=(",", ":")) + "\n")
                    raw.flush()
                    if skew < 0.5 and any(
                        v and v["net_per_contract"] > 0.002 for v in calc.values()
                    ):
                        pos.write(json.dumps(row, separators=(",", ":")) + "\n")
                        print(
                            "potential",
                            n,
                            time.time(),
                            {k: v["net_per_contract"] if v else None for k, v in calc.items()},
                            flush=True,
                        )
                except Exception as exc:
                    raw.write(
                        json.dumps(dict(round=n, received=time.time(), error=repr(exc))) + "\n"
                    )
                    raw.flush()
                n += 1
                await asyncio.sleep(min(0.2, max(0, stop - time.time())))
    (a.output / "complete.json").write_text(json.dumps(dict(completed=time.time(), rounds=n)))


if __name__ == "__main__":
    asyncio.run(main())
