"""Automatically audit fresh cross-venue signals at fixed executable price caps."""

import argparse
import asyncio
import hashlib
import json
import math
import time
from pathlib import Path

import httpx
from crossvenue_scan import KBASE, walk

SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def execution(books, trigger, direction, fd):
    spec = trigger["calculations"][direction]
    pb = books["poly"]["data"]
    kb = books["kalshi"]["data"]["orderbook_fp"]
    pc = max(p for p, q in spec["poly_levels"])
    kc = max(p for p, q in spec["kalshi_levels"])
    pa = [
        (float(x["price"]), float(x["size"]))
        for x in pb.get("asks", [])
        if float(x["price"]) <= pc + 1e-8
    ]
    side = "no_dollars" if direction == "poly_no_kalshi_yes" else "yes_dollars"
    ka = [(1 - float(p), float(q)) for p, q in kb.get(side, []) if 1 - float(p) <= kc + 1e-8]
    pp = walk(pa, spec["poly_gross_shares"])
    kp = walk(ka, 10)
    kfee = sum(math.ceil(0.07 * p * (1 - p) * q * 100 - 1e-9) / 100 for p, q in kp[1]) if kp else 0
    delivered = 0
    if pp:
        delivered = sum(
            q - (math.ceil(fd["r"] * q * p * (1 - p) * 1e5 - 1e-8) / 1e5) / p for p, q in pp[1]
        )
    net = (min(delivered, 10) - pp[0] - kp[0] - kfee) if pp and kp else None
    return dict(
        poly_fok_available=bool(pp),
        kalshi_fok_available=bool(kp),
        poly_delivered=delivered,
        poly_execution=pp,
        kalshi_execution=kp,
        kalshi_fee=kfee,
        paired_floor_net=net,
        caps=dict(poly=pc, kalshi=kc),
    )


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("root", type=Path)
    p.add_argument("--seconds", type=int, default=3600)
    a = p.parse_args()
    out = a.root / "automatic_confirmation"
    out.mkdir(exist_ok=False)
    maps = json.loads((a.root / "mappings.json").read_text())
    pairs = {v["kalshi"]["ticker"]: v for v in maps}
    fees = json.loads((a.root / "fees.json").read_text())
    stop = time.time() + a.seconds
    (out / "registration.json").write_text(
        json.dumps(
            dict(
                created=time.time(),
                stop=stop,
                code_sha256=SOURCE_SHA256,
                arrival_delay=1.5,
                unwind_delay=0.5,
                quantity=10,
                dedup_event_seconds=60,
                maximum_trigger_age=1,
                live_orders=False,
                qualification="hypothetical venue-equivalence/counterfactual-FOK study; single-leg failures retained",
            ),
            indent=2,
        )
    )
    jobs = set()
    last = {}
    async with httpx.AsyncClient(timeout=10) as client:

        async def capture(pair, asset):
            async def get(url, params):
                before = time.time()
                r = await client.get(url, params=params)
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

            v = await asyncio.gather(
                get("https://clob.polymarket.com/book", {"token_id": asset}),
                get(KBASE + "/markets/" + pair["kalshi"]["ticker"] + "/orderbook", {"depth": 20}),
            )
            return dict(poly=v[0], kalshi=v[1])

        async def audit(trigger):
            pair = pairs[trigger["ticker"]]
            cid = trigger["market"]
            fd = fees[cid]["clob"]["fd"]
            ts = max(trigger[k]["received"] for k in ["yes", "no", "kalshi"])
            folder = out / f"{int(ts * 1000)}_{trigger['ticker']}"
            folder.mkdir()
            (folder / "trigger.json").write_text(json.dumps(trigger))
            for direction, spec in trigger["calculations"].items():
                if not spec or spec["net_per_contract"] <= 0.002:
                    continue
                asset = pair["poly_no"] if direction == "poly_no_kalshi_yes" else pair["poly_yes"]
                row = dict(
                    direction=direction,
                    trigger_at=ts,
                    trigger_net=spec["net_per_contract"],
                    status="PENDING",
                )
                try:
                    for delay in [0.5, 1.5, 2.5, 5.0]:
                        await asyncio.sleep(max(0, ts + delay - time.time()))
                        snap = await capture(pair, asset)
                        ev = execution(snap, trigger, direction, fd)
                        (folder / f"{direction}_{delay}.json").write_text(
                            json.dumps(dict(target=ts + delay, books=snap, execution=ev), indent=2)
                        )
                        if delay == 1.5:
                            row.update(
                                arrival=ev,
                                actual_delay=max(v["received"] for v in snap.values()) - ts,
                            )
                            if ev["poly_fok_available"] != ev["kalshi_fok_available"]:
                                await asyncio.sleep(0.5)
                                exitbooks = await capture(pair, asset)
                                if ev["poly_fok_available"]:
                                    sell = walk(
                                        [
                                            (-float(v["price"]), float(v["size"]))
                                            for v in exitbooks["poly"]["data"].get("bids", [])
                                        ],
                                        ev["poly_delivered"],
                                    )
                                    # walk sorts ascending: negative bid makes highest bid first.
                                    recovered = -sell[0] if sell else 0
                                    fee = (
                                        sum(fd["r"] * q * (-p) * (1 + p) for p, q in sell[1])
                                        if sell
                                        else 0
                                    )
                                    net = recovered - fee - ev["poly_execution"][0]
                                else:
                                    side = (
                                        "yes_dollars"
                                        if direction == "poly_no_kalshi_yes"
                                        else "no_dollars"
                                    )
                                    sell = walk(
                                        [
                                            (-float(p), float(q))
                                            for p, q in exitbooks["kalshi"]["data"][
                                                "orderbook_fp"
                                            ].get(side, [])
                                        ],
                                        10,
                                    )
                                    recovered = -sell[0] if sell else 0
                                    fee = (
                                        sum(
                                            math.ceil(0.07 * q * (-p) * (1 + p) * 100 - 1e-9) / 100
                                            for p, q in sell[1]
                                        )
                                        if sell
                                        else 0
                                    )
                                    net = (
                                        recovered
                                        - fee
                                        - ev["kalshi_execution"][0]
                                        - ev["kalshi_fee"]
                                    )
                                row.update(single_leg_unwind_net=net, unwind_books=exitbooks)
                    row["status"] = "AUDITED"
                except Exception as exc:
                    row.update(
                        status="UNRESOLVED_EXPOSURE",
                        error=repr(exc),
                        worst_loss_bound=-sum(
                            spec[k] for k in ["poly_cash", "kalshi_cash", "kalshi_fee"]
                        ),
                    )
                with (out / "results.jsonl").open("a") as f:
                    f.write(json.dumps(row) + "\n")

        with (a.root / "positive.jsonl").open() as feed:
            while time.time() < stop:
                line = feed.readline()
                if not line:
                    await asyncio.sleep(0.05)
                    continue
                trigger = json.loads(line)
                ts = max(trigger[k]["received"] for k in ["yes", "no", "kalshi"])
                event = trigger["ticker"].rsplit("-", 1)[0]
                if time.time() - ts > 1 or ts - last.get(event, 0) < 60:
                    continue
                last[event] = ts
                job = asyncio.create_task(audit(trigger))
                jobs.add(job)
                job.add_done_callback(jobs.discard)
        if jobs:
            await asyncio.gather(*jobs)
    (out / "complete.json").write_text(json.dumps(dict(completed=time.time())))


if __name__ == "__main__":
    asyncio.run(main())
