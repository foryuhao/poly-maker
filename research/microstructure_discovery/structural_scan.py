"""Read-only screen of fixed exhaustive negative-risk event baskets; no execution."""

import argparse
import asyncio
import datetime as dt
import json
import time
from pathlib import Path

import httpx
from execution_study import vwap
from study import Book, array


def eligible(event):
    ms = event.get("markets", [])
    if not event.get("negRisk") or event.get("negRiskAugmented") or not 2 <= len(ms) <= 32:
        return False
    # Do not silently remove closed bins or placeholders from the payoff partition.
    return all(
        not m.get("closed")
        and m.get("acceptingOrders")
        and m.get("enableOrderBook")
        and not m.get("negRiskOther")
        and len(array(m.get("clobTokenIds", []))) == 2
        and set(array(m.get("outcomes", []))) == {"Yes", "No"}
        for m in ms
    )


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--rounds", type=int, default=12)
    p.add_argument("--interval", type=float, default=10)
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=False)
    async with httpx.AsyncClient(timeout=25) as c:
        r = await c.get(
            "https://gamma-api.polymarket.com/events",
            params=dict(
                active="true", closed="false", limit=100, order="volume24hr", ascending="false"
            ),
        )
        r.raise_for_status()
        events = r.json()
        (a.output / "events_raw.json").write_text(json.dumps(events, indent=2))
        selected = [e for e in events if eligible(e)]
        (a.output / "selection.json").write_text(
            json.dumps(
                dict(
                    discovered=len(events),
                    eligible=len(selected),
                    event_ids=[e["id"] for e in selected],
                    selection="Standard nonaugmented negative-risk events,2..32 markets,all bins open/accepting orderbook,explicit binary Yes/No, no placeholders removed",
                    created=time.time(),
                ),
                indent=2,
            )
        )
        summaries = []
        with (a.output / "raw_snapshots.jsonl").open("x", buffering=1) as raw:
            for round_no in range(a.rounds):
                for e in selected:
                    tokens = []
                    mapping = []
                    for m in e["markets"]:
                        ids = array(m["clobTokenIds"])
                        outs = array(m["outcomes"])
                        tok = {o: t for o, t in zip(outs, ids, strict=True)}
                        tokens.extend(ids)
                        mapping.append((m, tok))
                    requested = time.time()
                    try:
                        r = await c.post(
                            "https://clob.polymarket.com/books",
                            json=[dict(token_id=t) for t in tokens],
                        )
                        r.raise_for_status()
                        books = r.json()
                        received = time.time()
                        raw.write(
                            json.dumps(
                                dict(
                                    round=round_no,
                                    event_id=e["id"],
                                    requested=requested,
                                    received=received,
                                    url=str(r.url),
                                    books=books,
                                )
                            )
                            + "\n"
                        )
                        by_asset = {b["asset_id"]: b for b in books}
                        legs = []
                        for m, tok in mapping:
                            leg = dict(market=m["conditionId"], question=m["question"])
                            for side in ("Yes", "No"):
                                b = Book()
                                b.update(dict(by_asset[tok[side]], event_type="book"))
                                price = vwap(b, "BUY", 5)
                                if price is None:
                                    raise ValueError("insufficient five-share depth")
                                leg[side] = price
                            fd = m.get("feeSchedule") or {}
                            leg["fee_known"] = (
                                fd.get("rate") is not None and fd.get("exponent") is not None
                            )
                            for side in ("Yes", "No"):
                                price = leg[side]
                                leg["fee_" + side] = (
                                    fd["rate"] * (price * (1 - price)) ** fd["exponent"]
                                    if leg["fee_known"]
                                    else None
                                )
                            legs.append(leg)
                        n = len(legs)
                        yes = sum(leg["Yes"] for leg in legs)
                        no = sum(leg["No"] for leg in legs)
                        known = all(leg["fee_known"] for leg in legs)
                        end = (
                            dt.datetime.fromisoformat(
                                e["endDate"].replace("Z", "+00:00")
                            ).timestamp()
                            if e.get("endDate")
                            else None
                        )
                        years = (
                            max(0, (end - received) / (365.25 * 86400)) if end is not None else None
                        )
                        gross_yes = 1 - yes
                        gross_no = n - 1 - no
                        net_yes = (
                            gross_yes - sum(leg["fee_Yes"] for leg in legs) - 0.001 * n
                            if known
                            else None
                        )
                        net_no = (
                            gross_no - sum(leg["fee_No"] for leg in legs) - 0.001 * n
                            if known
                            else None
                        )
                        row = dict(
                            round=round_no,
                            event_id=e["id"],
                            title=e["title"],
                            received=received,
                            request_seconds=received - requested,
                            legs=n,
                            sum_yes_asks=yes,
                            sum_no_asks=no,
                            gross_buy_yes_surplus=gross_yes,
                            gross_buy_no_surplus=gross_no,
                            effective_fees_known=known,
                            net_yes_after_execution_reserve=net_yes,
                            net_no_after_execution_reserve=net_no,
                            years_until_nominal_end=years,
                            net_yes_after_8pct_hurdle=net_yes - yes * 0.08 * years
                            if known and years is not None
                            else None,
                            net_no_after_8pct_hurdle=net_no - no * 0.08 * years
                            if known and years is not None
                            else None,
                            leg_quotes=legs,
                        )
                        summaries.append(row)
                    except Exception as exc:
                        raw.write(
                            json.dumps(
                                dict(
                                    round=round_no,
                                    event_id=e["id"],
                                    requested=requested,
                                    received=time.time(),
                                    error=repr(exc),
                                )
                            )
                            + "\n"
                        )
                if round_no < a.rounds - 1:
                    await asyncio.sleep(a.interval)
        (a.output / "screen.json").write_text(
            json.dumps(
                dict(
                    observations=summaries,
                    qualification="Only price-screen candidates. Must verify CLOB effective fees, complete-set contracts/payoff partition, minimum order sizes, stability under sequential execution, funding horizon, and independent availability. No orders sent or profits claimed.",
                ),
                indent=2,
            )
        )
        positives = [
            r
            for r in summaries
            if (r["net_yes_after_8pct_hurdle"] is not None and r["net_yes_after_8pct_hurdle"] > 0)
            or (r["net_no_after_8pct_hurdle"] is not None and r["net_no_after_8pct_hurdle"] > 0)
        ]
        (a.output / "positive_screen.json").write_text(json.dumps(positives, indent=2))
        print(
            json.dumps(
                dict(
                    events=len(selected),
                    observations=len(summaries),
                    positive_screen_observations=len(positives),
                    path=str(a.output),
                )
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
