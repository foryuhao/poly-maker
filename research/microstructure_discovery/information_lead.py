"""Fresh public-data-only lead/lag validation. No order placement or credentials."""

import argparse
import asyncio
import collections
import hashlib
import json
import math
import time
import zlib
from pathlib import Path

import httpx
from crossvenue_scan import KBASE, walk

SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def ceiling(x, scale):
    return math.ceil(x * scale - 1e-9) / scale


def cash_fill(book, budget, cap, rate):
    remain = budget
    tokens = 0.0
    levels = []
    for p, q in sorted((float(v["price"]), float(v["size"])) for v in book.get("asks", [])):
        if not 0 < p <= cap + 1e-9:
            continue
        cash = min(remain, p * q)
        qty = cash / p
        remain -= cash
        tokens += qty * (1 - 2 * rate * (1 - p))
        levels.append((p, qty))
        if remain <= 1e-8:
            return dict(cash=budget, token_floor=tokens, levels=levels)
    return None


def kbbo(book, side):
    own = "yes_dollars" if side == "yes" else "no_dollars"
    other = "no_dollars" if side == "yes" else "yes_dollars"
    bid = max((float(p) for p, q in book.get(own, []) if float(q) > 0), default=0)
    ask = min((1 - float(p) for p, q in book.get(other, []) if float(q) > 0), default=1)
    return bid, ask


def klevels(book, side, cap):
    opposite = "no_dollars" if side == "yes" else "yes_dollars"
    return [
        (1 - float(p), float(q))
        for p, q in book.get(opposite, [])
        if 0 < 1 - float(p) <= cap + 1e-9
    ]


def fee_bound(levels, contracts=10):
    raw = sum(0.07 * q * p * (1 - p) for p, q in levels)
    return ceiling(raw + contracts / 0.01 * 0.0001, 100)


def plan(poly, kalshi, side, rate):
    asks = [
        float(v["price"])
        for v in poly.get("asks", [])
        if float(v["size"]) > 0 and 0 < float(v["price"]) < 1
    ]
    if not asks or not 0 <= rate < 0.2:
        return None
    pc = min(asks)
    bids = [float(v["price"]) for v in poly.get("bids", []) if float(v["size"]) > 0]
    if not bids or not 0 < max(bids) < pc:
        return None
    bid, ask = kbbo(kalshi, side)
    if not 0 < bid <= ask < 1:
        return None
    budget = ceiling(10 * pc / (1 - 2 * rate * (1 - pc)) + 0.00002, 100)
    if budget < 1 or budget / pc < float(poly.get("min_order_size", 5)):
        return None
    pf = cash_fill(poly, budget, pc, rate)
    kf = walk(klevels(kalshi, side, ask), 10)
    if pf is None or kf is None or pf["token_floor"] < 10 - 1e-8:
        return None
    product = ask * (1 - ask) if ask <= 0.5 else 0.25
    kfee = ceiling(0.07 * 10 * product + 0.1, 100)
    kcost = 10 * ask + kfee
    margin = 10 - budget - kcost - 0.02
    return dict(
        poly_cap=pc,
        poly_budget=budget,
        kalshi_cap=ask,
        kalshi_side=side,
        kalshi_cost_bound=kcost,
        kalshi_fee_bound=kfee,
        rate=rate,
        net_units=10,
        reserve=0.02,
        margin_floor=margin,
        poly_token_floor=pf["token_floor"],
    )


def feature_state(row):
    pm = {v["asset_id"]: v for v in row["poly"]["data"]}
    km = {v["ticker"]: v["orderbook_fp"] for v in row["kalshi"]["data"]["orderbooks"]}
    t = max(row["poly"]["received"], row["kalshi"]["received"])
    pa = {
        a: min((float(v["price"]) for v in b.get("asks", []) if float(v["size"]) > 0), default=1)
        for a, b in pm.items()
    }
    ks = {k: {s: kbbo(b, s) for s in ["yes", "no"]} for k, b in km.items()}
    return t, pm, km, dict(time=t, poly_ask=pa, kalshi=ks)


def lead_gate(previous, current, ticker, asset, side):
    old = previous["kalshi"].get(ticker, {}).get(side)
    new = current["kalshi"].get(ticker, {}).get(side)
    po = previous["poly_ask"].get(asset)
    pn = current["poly_ask"].get(asset)
    return bool(
        old
        and new
        and 0 < old[0] <= old[1] < 1
        and po is not None
        and pn is not None
        and old[1] - new[1] >= 0.05 - 1e-8
        and old[0] - new[0] >= 0.03 - 1e-8
        and abs(pn - po) <= 0.02 + 1e-8
    )


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("root", type=Path)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    a.output.mkdir(exist_ok=False)
    pairs = json.loads((a.root / "mappings.json").read_text())
    fees = json.loads((a.root / "fees.json").read_text())
    protocol = Path(__file__).with_name("PROTOCOL_V8_INFORMATION_LEAD.md")
    started = time.time()
    stop = json.loads((a.root / "registration.json").read_text())["stop"]
    history = collections.deque()
    last = {}
    jobs = set()
    counts = collections.Counter()
    committed = 0.0
    (a.output / "registration.json").write_text(
        json.dumps(
            dict(
                created=started,
                stop=stop,
                code_sha256=SOURCE_SHA256,
                protocol_sha256=hashlib.sha256(protocol.read_bytes()).hexdigest(),
                source=str(a.root),
                paper_capital=1000,
                orders=False,
            ),
            indent=2,
        )
    )
    (a.output / "mappings.json").write_text(json.dumps(pairs, indent=2))
    async with httpx.AsyncClient(timeout=10) as client:

        async def refresh():
            sem = asyncio.Semaphore(3)

            async def one(cid):
                async with sem:
                    try:
                        r = await client.get("https://clob.polymarket.com/clob-markets/" + cid)
                        r.raise_for_status()
                        info = r.json()
                        fd = info.get("fd", {})
                        old = fees[cid]
                        fees[cid] = dict(
                            received=time.time(),
                            clob=info,
                            gamma=old["gamma"],
                            parameters_verified=bool(
                                info.get("ao")
                                and fd.get("e") == 1
                                and fd.get("r") == old["gamma"].get("rate")
                            ),
                        )
                    except Exception as exc:
                        fees[cid] = dict(fees[cid], parameters_verified=False, error=repr(exc))

            await asyncio.gather(*(one(cid) for cid in fees))
            (a.output / f"fees_{int(time.time())}.json").write_text(json.dumps(fees))

        await refresh()
        last_refresh = time.time()
        series = []
        for name in ["KXEPLGAME", "KXNBAGAME", "KXNHLGAME"]:
            r = await client.get(KBASE + "/series/" + name)
            r.raise_for_status()
            s = r.json()
            assert (
                s["series"]["fee_multiplier"] == 1
                and s["series"]["fee_type"] == "quadratic_with_maker_fees"
            )
            series.append(s)
        (a.output / "series_fees.json").write_text(json.dumps(series))

        async def get(url, params=None):
            before = time.time()
            r = await client.get(url, params=params)
            r.raise_for_status()
            return dict(requested=before, received=time.time(), data=r.json())

        async def observe(decision):
            folder = a.output / decision["id"]
            folder.mkdir()
            (folder / "decision.json").write_text(json.dumps(decision, indent=2))
            row = dict(
                id=decision["id"],
                decision_at=decision["decision_at"],
                ticker=decision["ticker"],
                market=decision["market"],
                event=decision["event"],
                plan=decision["plan"],
                status="PENDING",
                net_lower_bound=None,
            )
            spec = decision["plan"]
            when = decision["decision_at"]
            snap = {}
            try:
                for delay in [0.5, 1.5, 2.5, 5.0]:
                    await asyncio.sleep(max(0, when + delay - time.time()))
                    tasks = [
                        get("https://clob.polymarket.com/book", {"token_id": decision["asset"]}),
                        get(KBASE + "/markets/" + decision["ticker"] + "/orderbook", {"depth": 50}),
                    ]
                    if delay == 0.5:
                        tasks.append(get(KBASE + "/markets/" + decision["ticker"]))
                    if delay == 1.5:
                        tasks.append(
                            get("https://clob.polymarket.com/clob-markets/" + decision["market"])
                        )
                    replies = await asyncio.gather(*tasks)
                    snap[delay] = dict(poly=replies[0], kalshi=replies[1])
                    if len(replies) > 2:
                        snap[delay]["metadata"] = replies[2]
                    (folder / f"snapshot_{delay}.json").write_text(json.dumps(snap[delay]))
                ki = snap[0.5]["metadata"]["data"]["market"]
                pi = snap[1.5]["metadata"]["data"]
                fd = pi.get("fd", {})
                if (
                    ki.get("status") != "active"
                    or not pi.get("ao")
                    or fd.get("r") != spec["rate"]
                    or fd.get("e") != 1
                ):
                    raise RuntimeError("market activity or fee metadata uncertain at arrival")
                kfill = walk(
                    klevels(
                        snap[0.5]["kalshi"]["data"]["orderbook_fp"],
                        spec["kalshi_side"],
                        spec["kalshi_cap"],
                    ),
                    10,
                )
                pfill = cash_fill(
                    snap[1.5]["poly"]["data"], spec["poly_budget"], spec["poly_cap"], spec["rate"]
                )
                row.update(
                    kalshi_fok_available=bool(kfill),
                    poly_fok_available=bool(pfill),
                    poly_arrival_delay=snap[1.5]["poly"]["received"] - when,
                    kalshi_arrival_delay=snap[0.5]["kalshi"]["received"] - when,
                )
                if kfill and pfill and pfill["token_floor"] >= 10 - 1e-8:
                    row.update(
                        status="PAIRED_PENDING_SETTLEMENT",
                        nominal_profit_floor=spec["margin_floor"],
                        token_floor=pfill["token_floor"],
                    )
                elif kfill and not pfill:
                    side = "yes_dollars" if spec["kalshi_side"] == "yes" else "no_dollars"
                    b = snap[5.0]["kalshi"]["data"]["orderbook_fp"]
                    sale = walk(
                        [(-float(p), float(q)) for p, q in b.get(side, []) if float(p) >= 0.01], 10
                    )
                    recovery = -sale[0] if sale else 0
                    exitfee = fee_bound([(-p, q) for p, q in sale[1]]) if sale else 0
                    row.update(
                        status="K_ONLY_UNWIND_BOUND",
                        net_lower_bound=recovery - exitfee - spec["kalshi_cost_bound"] - 0.02,
                        recovered=recovery,
                        exit_fee_bound=exitfee,
                    )
                elif pfill and not kfill:
                    row.update(
                        status="P_ONLY_ZERO_RECOVERY_BOUND",
                        net_lower_bound=-spec["poly_budget"] - 0.02,
                    )
                elif not kfill and not pfill:
                    row.update(status="NO_FILLS", net_lower_bound=0.0)
                else:
                    raise RuntimeError("fee-reduced received amount below target")
            except Exception as exc:
                row.update(
                    status="UNRESOLVED_EXPOSURE_BOUND",
                    error=repr(exc),
                    net_lower_bound=-spec["poly_budget"] - spec["kalshi_cost_bound"] - 0.02,
                )
            (folder / "result.json").write_text(json.dumps(row, indent=2))
            with (a.output / "results.jsonl").open("a") as out:
                out.write(json.dumps(row) + "\n")
            counts[row["status"]] += 1

        decoder = zlib.decompressobj(31)
        buffer = b""
        last_good = None
        last_quality = 0.0
        with (
            (a.root / "books.jsonl.gz").open("rb") as feed,
            (a.output / "decisions.jsonl").open("w", buffering=1) as decisions,
        ):
            while time.time() < stop:
                chunk = feed.read(65536)
                if not chunk:
                    if decoder.eof:
                        break
                    await asyncio.sleep(0.05)
                    continue
                buffer += decoder.decompress(chunk)
                parts = buffer.split(b"\n")
                buffer = parts.pop()
                for data in parts:
                    counts["source_rows"] += 1
                    try:
                        r = json.loads(data)
                        if "error" in r:
                            history.clear()
                            counts["source_errors"] += 1
                            continue
                        t, pm, km, features = feature_state(r)
                        if last_good is not None and t - last_good > 2:
                            history.clear()
                            counts["source_gaps"] += 1
                        last_good = t
                        previous = next((x for x in reversed(history) if x["time"] <= t - 3), None)
                        history.append(features)
                        while history and history[0]["time"] < t - 8:
                            history.popleft()
                        now = time.time()
                        if (
                            t < started
                            or not previous
                            or t - previous["time"] > 5
                            or now - t > 0.35
                        ):
                            continue
                        if abs(r["poly"]["received"] - r["kalshi"]["received"]) > 0.5:
                            counts["receipt_skew"] += 1
                            continue
                        eligible = []
                        for pair in pairs:
                            cid = pair["market"]["conditionId"]
                            ticker = pair["kalshi"]["ticker"]
                            fee = fees[cid]
                            if (
                                not fee.get("parameters_verified")
                                or now - fee["received"] > 660
                                or ticker not in km
                            ):
                                continue
                            event = pair["kalshi"]["event_ticker"]
                            if now - last.get(event, 0) < 60:
                                continue
                            for asset, side in [(pair["poly_yes"], "no"), (pair["poly_no"], "yes")]:
                                b = pm.get(asset)
                                if not b or not lead_gate(previous, features, ticker, asset, side):
                                    continue
                                age = now - float(b.get("timestamp", 0)) / 1000
                                if not -0.25 <= age <= 1:
                                    counts["source_clock_age"] += 1
                                    continue
                                spec = plan(b, km[ticker], side, fee["clob"]["fd"]["r"])
                                if not spec or spec["margin_floor"] < 0.50 - 1e-8:
                                    continue
                                eligible.append((spec["margin_floor"], pair, asset, spec, fee))
                        for _margin, pair, asset, spec, fee in sorted(
                            eligible, key=lambda x: x[0], reverse=True
                        ):
                            event = pair["kalshi"]["event_ticker"]
                            cost = spec["poly_budget"] + spec["kalshi_cost_bound"] + 0.02
                            if now - last.get(event, 0) < 60 or committed + cost > 1000:
                                continue
                            if len(jobs) >= 3:
                                counts["observer_capacity_rejections"] += 1
                                continue
                            decision = dict(
                                id=f"{int(now * 1000)}_{pair['kalshi']['ticker']}",
                                decision_at=now,
                                book_time=t,
                                source_round=r["round"],
                                source_row_sha256=hashlib.sha256(data).hexdigest(),
                                event=event,
                                ticker=pair["kalshi"]["ticker"],
                                market=pair["market"]["conditionId"],
                                asset=asset,
                                plan=spec,
                                pair=pair,
                                fee=fee,
                                reference=previous,
                                current=features,
                                paper_committed_before=committed,
                            )
                            decisions.write(json.dumps(decision) + "\n")
                            last[event] = now
                            committed += cost
                            job = asyncio.create_task(observe(decision))
                            jobs.add(job)
                            job.add_done_callback(jobs.discard)
                            counts["decisions"] += 1
                        if now - last_quality > 60:
                            (a.output / "quality.json").write_text(
                                json.dumps(
                                    dict(received=now, counts=counts, paper_committed=committed),
                                    indent=2,
                                )
                            )
                            last_quality = now
                        if now - last_refresh > 600:
                            await refresh()
                            last_refresh = time.time()
                    except Exception as exc:
                        counts["row_parse_error"] += 1
                        with (a.output / "errors.jsonl").open("a") as err:
                            err.write(
                                json.dumps(dict(received=time.time(), error=repr(exc))) + "\n"
                            )
                await asyncio.sleep(0)
        if jobs:
            await asyncio.gather(*jobs)
    (a.output / "complete.json").write_text(
        json.dumps(
            dict(
                completed=time.time(),
                counts=counts,
                paper_committed=committed,
                source_terminal=decoder.eof,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
