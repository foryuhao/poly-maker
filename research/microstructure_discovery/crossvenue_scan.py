"""Public paired books: potential cross-venue discount, NEVER guaranteed arb."""

import argparse
import asyncio
import datetime as dt
import gzip
import hashlib
import json
import math
import time
from pathlib import Path

import httpx

KBASE = "https://api.elections.kalshi.com/trade-api/v2"
SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
SERIES = {"nba": "KXNBAGAME", "nhl": "KXNHLGAME", "epl": "KXEPLGAME"}
ALIASES = {
    "epl": {
        "che": "CFC",
        "ast": "AVL",
        "mac": "MCI",
        "not": "NFO",
        "wol": "WOL",
        "mun": "MUN",
        "bri": "BRI",
    },
    "nba": {"no": "NOP", "sa": "SAS", "ny": "NYK", "gs": "GSW", "pho": "PHX"},
    "nhl": {},
}


def array(x):
    return json.loads(x) if isinstance(x, str) else x


def mappings(events, markets):
    pairs = []
    for e in events:
        sport = (e.get("sport") or {}).get("sport")
        if sport not in SERIES or not e.get("eventDate") or len(e.get("teams", [])) != 2:
            continue
        date = dt.date.fromisoformat(e["eventDate"]).strftime("%y%b%d").upper()
        teams = e["teams"]
        codes = {
            t["id"]: ALIASES[sport].get(t["abbreviation"].lower(), t["abbreviation"].upper())
            for t in teams
        }
        group = [
            k
            for k in markets
            if k["ticker"].startswith(SERIES[sport] + "-" + date)
            and k["ticker"].split("-")[1][7:]
            in (
                codes[teams[0]["id"]] + codes[teams[1]["id"]],
                codes[teams[1]["id"]] + codes[teams[0]["id"]],
            )
        ]
        for m in e.get("markets", []):
            if m.get("sportsMarketType") != "moneyline" or not m.get("acceptingOrders"):
                continue
            labels = array(m["outcomes"])
            tokens = array(m["clobTokenIds"])
            for k in group:
                code = k["ticker"].split("-")[-1]
                if sport == "epl":
                    selected = m.get("groupItemTitle", "")
                    match = (code == "TIE" and selected.lower() in ("draw", "tie")) or any(
                        codes[t["id"]] == code and selected == t["name"] for t in teams
                    )
                    if not match or labels != ["Yes", "No"]:
                        continue
                    idx = 0
                else:
                    names = {t["name"] for t in teams if codes[t["id"]] == code} | {
                        t.get("alias", "") for t in teams if codes[t["id"]] == code
                    }
                    matches = [i for i, label in enumerate(labels) if label in names]
                    if len(matches) != 1:
                        continue
                    idx = matches[0]
                pairs.append(
                    dict(
                        event={key: v for key, v in e.items() if key != "markets"},
                        market=m,
                        kalshi=k,
                        poly_yes=tokens[idx],
                        poly_no=tokens[1 - idx],
                        mapping_basis="exact date, explicit team abbreviation aliases, exact Gamma outcome/team label",
                    )
                )
    return pairs


def walk(levels, quantity):
    remain = quantity
    cash = 0.0
    consumed = []
    for price, size in sorted(levels):
        take = min(size, remain)
        if take <= 0:
            continue
        cash += price * take
        remain -= take
        consumed.append((price, take))
        if remain < 1e-8:
            return cash, consumed
    return None


def calculate(poly, kalshi, rate, exponent, quantity=10):
    asks = [
        (float(x["price"]), float(x["size"]))
        for x in poly.get("asks", [])
        if 0 < float(x["price"]) < 1 and float(x["size"]) > 0
    ]
    if not asks or exponent != 1:
        return None
    best = min(p for p, q in asks)
    # BUY fees remove shares; upper rounding envelope, positive surplus tokens marked0.
    gross = (
        quantity
        if rate == 0
        else math.ceil((quantity + 0.00002 / best) / (1 - rate * (1 - best)) * 100 - 1e-9) / 100
    )
    pp = walk(asks, gross)
    kp = walk(kalshi, quantity)
    if pp is None or kp is None:
        return None
    fees = sum(math.ceil(0.07 * q * p * (1 - p) * 100 - 1e-10) / 100 for p, q in kp[1])
    cash = pp[0] + kp[0] + fees
    return dict(
        poly_gross_shares=gross,
        paired_payout_if_equivalent=quantity,
        poly_cash=pp[0],
        kalshi_cash=kp[0],
        kalshi_fee=fees,
        net_before_basis_execution_and_capital=quantity - cash,
        net_per_contract=(quantity - cash) / quantity,
        poly_levels=pp[1],
        kalshi_levels=kp[1],
        qualification="contract cancellation/fair-price rules and asynchronous fill risk not covered by paired payout assumption",
    )


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--events", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--seconds", type=int, default=3600)
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=False)
    stop = time.time() + a.seconds
    async with httpx.AsyncClient(timeout=15) as c:
        km = []
        series = []
        for s in SERIES.values():
            r = await c.get(KBASE + "/series/" + s)
            r.raise_for_status()
            series.append(r.json())
            r = await c.get(
                KBASE + "/markets", params={"status": "open", "series_ticker": s, "limit": 1000}
            )
            r.raise_for_status()
            x = r.json()
            assert not x.get("cursor")
            km.extend(x["markets"])
        assert all(
            s["series"].get("fee_multiplier") == 1
            and s["series"].get("fee_type") == "quadratic_with_maker_fees"
            for s in series
        )
        pairs = mappings(json.loads(a.events.read_text()), km)
        (a.output / "mappings.json").write_text(json.dumps(pairs, indent=2))
        (a.output / "series_fees.json").write_text(json.dumps(series, indent=2))
        fees = {}
        for pair in pairs:
            m = pair["market"]
            cid = m["conditionId"]
            if cid in fees:
                continue
            r = await c.get("https://clob.polymarket.com/clob-markets/" + cid)
            r.raise_for_status()
            info = r.json()
            gamma = m.get("feeSchedule", {})
            fd = info.get("fd", {})
            agree = (
                fd.get("r") is not None
                and fd.get("e") == 1
                and gamma.get("rate") == fd.get("r")
                and gamma.get("exponent") == fd.get("e")
            )
            fees[cid] = dict(
                received=time.time(), clob=info, gamma=gamma, parameters_verified=agree
            )
        (a.output / "fees.json").write_text(json.dumps(fees, indent=2))
        (a.output / "registration.json").write_text(
            json.dumps(
                dict(
                    created=time.time(),
                    stop=stop,
                    code_sha256=SOURCE_SHA256,
                    pairs=len(pairs),
                    quantity=10,
                    order_placement=False,
                    positive_threshold_per_contract=0.002,
                    qualification="exploratory paired-price screen; rules and funding/execution risks need independent audit",
                ),
                indent=2,
            )
        )
        print("matched", len(pairs), flush=True)
        sem = asyncio.Semaphore(2)
        with (
            gzip.open(a.output / "books.jsonl.gz", "wt") as raw,
            (a.output / "positive.jsonl").open("w", buffering=1) as positives,
        ):

            async def one(pair, round_id):
                cid = pair["market"]["conditionId"]
                if not fees[cid]["parameters_verified"]:
                    return
                async with sem:

                    async def get(url, params=None):
                        before = time.time()
                        r = await c.get(url, params=params)
                        r.raise_for_status()
                        return dict(requested=before, received=time.time(), data=r.json())

                    try:
                        yes, no, k = await asyncio.gather(
                            get("https://clob.polymarket.com/book", {"token_id": pair["poly_yes"]}),
                            get("https://clob.polymarket.com/book", {"token_id": pair["poly_no"]}),
                            get(
                                KBASE + "/markets/" + pair["kalshi"]["ticker"] + "/orderbook",
                                {"depth": 20},
                            ),
                        )
                        b = k["data"]["orderbook_fp"]
                        fd = fees[cid]["clob"]["fd"]
                        # Kalshi exposes bids; opposite-side bid creates the implied ask.
                        ky = [(1 - float(p), float(q)) for p, q in b.get("no_dollars", [])]
                        kn = [(1 - float(p), float(q)) for p, q in b.get("yes_dollars", [])]
                        calc = {
                            "poly_yes_kalshi_no": calculate(yes["data"], kn, fd["r"], fd["e"]),
                            "poly_no_kalshi_yes": calculate(no["data"], ky, fd["r"], fd["e"]),
                        }
                        skew = max(v["received"] for v in [yes, no, k]) - min(
                            v["received"] for v in [yes, no, k]
                        )
                        row = dict(
                            round=round_id,
                            market=cid,
                            ticker=pair["kalshi"]["ticker"],
                            yes=yes,
                            no=no,
                            kalshi=k,
                            receipt_skew=skew,
                            calculations=calc,
                        )
                        raw.write(json.dumps(row, separators=(",", ":")) + "\n")
                        if skew <= 2 and any(
                            v is not None and v["net_per_contract"] > 0.002 for v in calc.values()
                        ):
                            positives.write(json.dumps(row, separators=(",", ":")) + "\n")
                            print("potential", cid, pair["kalshi"]["ticker"], calc, flush=True)
                    except Exception as exc:
                        raw.write(
                            json.dumps(dict(round=round_id, market=cid, error=repr(exc))) + "\n"
                        )

            rounds = 0
            while time.time() < stop:
                await asyncio.gather(*(one(pair, rounds) for pair in pairs))
                raw.flush()
                rounds += 1
                await asyncio.sleep(min(10, max(0, stop - time.time())))
        (a.output / "complete.json").write_text(
            json.dumps(dict(completed=time.time(), rounds=rounds))
        )


if __name__ == "__main__":
    asyncio.run(main())
