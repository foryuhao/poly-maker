"""Fresh fixed-universe paired books using public batch endpoints."""

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
    p.add_argument("--seconds", type=int, default=43200)
    a = p.parse_args()
    a.output.mkdir(exist_ok=False)
    pairs = json.loads((a.root / "mappings.json").read_text())
    fees = json.loads((a.root / "fees.json").read_text())
    async with httpx.AsyncClient(timeout=10) as c:
        for cid, fee in fees.items():
            r = await c.get("https://clob.polymarket.com/clob-markets/" + cid)
            r.raise_for_status()
            info = r.json()
            fd = info.get("fd", {})
            gamma = fee["gamma"]
            fee.update(
                received=time.time(),
                clob=info,
                parameters_verified=info.get("ao", False)
                and fd.get("e") == 1
                and fd.get("r") == gamma.get("rate")
                and fd.get("r") is not None,
            )
        series = []
        for ticker in ["KXEPLGAME", "KXNBAGAME", "KXNHLGAME"]:
            r = await c.get(KBASE + "/series/" + ticker)
            r.raise_for_status()
            s = r.json()
            assert (
                s["series"]["fee_multiplier"] == 1
                and s["series"]["fee_type"] == "quadratic_with_maker_fees"
            )
            series.append(s)
        pairs = [v for v in pairs if fees[v["market"]["conditionId"]]["parameters_verified"]]
        (a.output / "mappings.json").write_text(json.dumps(pairs, indent=2))
        (a.output / "fees.json").write_text(json.dumps(fees, indent=2))
        (a.output / "series_fees.json").write_text(json.dumps(series, indent=2))
        assets = sorted({v[k] for v in pairs for k in ["poly_yes", "poly_no"]})
        tickers = sorted({v["kalshi"]["ticker"] for v in pairs})
        stop = time.time() + a.seconds
        (a.output / "registration.json").write_text(
            json.dumps(
                dict(
                    created=time.time(),
                    stop=stop,
                    source_sha256=SOURCE_SHA256,
                    pairs=len(pairs),
                    assets=len(assets),
                    pause_seconds=0.2,
                    signal_policy="unchanged fixed paired-discount screen",
                    max_receipt_skew=0.5,
                    orders=False,
                    qualification="New observation period, not independent duplicate samples of concurrent fast/broad feeds",
                ),
                indent=2,
            )
        )
        print("bulk ready", len(pairs), len(assets), flush=True)
        with (
            gzip.open(a.output / "books.jsonl.gz", "wt") as raw,
            (a.output / "positive.jsonl").open("w", buffering=1) as pos,
        ):
            n = 0
            while time.time() < stop:
                try:

                    async def poly():
                        before = time.time()
                        r = await c.post(
                            "https://clob.polymarket.com/books",
                            json=[{"token_id": x} for x in assets],
                        )
                        r.raise_for_status()
                        return dict(
                            requested=before,
                            received=time.time(),
                            headers={
                                k: v
                                for k, v in r.headers.items()
                                if k.lower() in ["date", "age", "cf-cache-status", "cache-control"]
                            },
                            data=r.json(),
                        )

                    async def kalshi():
                        before = time.time()
                        r = await c.get(
                            KBASE + "/markets/orderbooks", params=[("tickers", t) for t in tickers]
                        )
                        r.raise_for_status()
                        return dict(
                            requested=before,
                            received=time.time(),
                            headers={
                                k: v
                                for k, v in r.headers.items()
                                if k.lower() in ["date", "age", "cf-cache-status", "cache-control"]
                            },
                            data=r.json(),
                        )

                    pr, kr = await asyncio.gather(poly(), kalshi())
                    pm = {v["asset_id"]: v for v in pr["data"]}
                    km = {v["ticker"]: v["orderbook_fp"] for v in kr["data"]["orderbooks"]}
                    raw.write(
                        json.dumps(dict(round=n, poly=pr, kalshi=kr), separators=(",", ":")) + "\n"
                    )
                    raw.flush()
                    skew = abs(pr["received"] - kr["received"])
                    if skew <= 0.5:
                        for pair in pairs:
                            cid = pair["market"]["conditionId"]
                            ticker = pair["kalshi"]["ticker"]
                            y = pm.get(pair["poly_yes"])
                            no = pm.get(pair["poly_no"])
                            b = km.get(ticker)
                            if not y or not no or not b:
                                continue
                            fd = fees[cid]["clob"]["fd"]
                            ky = [(1 - float(p), float(q)) for p, q in b.get("no_dollars", [])]
                            kn = [(1 - float(p), float(q)) for p, q in b.get("yes_dollars", [])]
                            calc = dict(
                                poly_yes_kalshi_no=calculate(y, kn, fd["r"], fd["e"]),
                                poly_no_kalshi_yes=calculate(no, ky, fd["r"], fd["e"]),
                            )
                            for direction, spec in list(calc.items()):
                                book = y if direction == "poly_yes_kalshi_no" else no
                                if spec and spec["poly_gross_shares"] < float(
                                    book.get("min_order_size", 5)
                                ):
                                    calc[direction] = None
                            if any(v and v["net_per_contract"] > 0.002 for v in calc.values()):
                                row = dict(
                                    round=n,
                                    market=cid,
                                    ticker=ticker,
                                    yes=dict(pr, data=y),
                                    no=dict(pr, data=no),
                                    kalshi=dict(kr, data={"orderbook_fp": b}),
                                    receipt_skew=skew,
                                    calculations=calc,
                                )
                                pos.write(json.dumps(row, separators=(",", ":")) + "\n")
                                print(
                                    "potential",
                                    n,
                                    ticker,
                                    {
                                        k: v["net_per_contract"] if v else None
                                        for k, v in calc.items()
                                    },
                                    flush=True,
                                )
                except Exception as exc:
                    raw.write(
                        json.dumps(dict(round=n, received=time.time(), error=repr(exc))) + "\n"
                    )
                    raw.flush()
                    await asyncio.sleep(1)
                n += 1
                await asyncio.sleep(min(0.2, max(0, stop - time.time())))
        (a.output / "complete.json").write_text(json.dumps(dict(completed=time.time(), rounds=n)))


if __name__ == "__main__":
    asyncio.run(main())
