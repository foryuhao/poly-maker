"""Official METAR lower-bound screen; observation certainty is conditional on corrections."""

import argparse
import asyncio
import datetime as dt
import json
import re
import time
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from execution_study import vwap
from study import Book, array

ZONES = {
    "RKSI": "Asia/Seoul",
    "RKPK": "Asia/Seoul",
    "EGLC": "Europe/London",
    "NZWN": "Pacific/Auckland",
    "ZSPD": "Asia/Shanghai",
    "ZBAA": "Asia/Shanghai",
    "ZGSZ": "Asia/Shanghai",
    "ZUUU": "Asia/Shanghai",
    "ZGGG": "Asia/Shanghai",
    "ZUCK": "Asia/Shanghai",
    "ZHHH": "Asia/Shanghai",
    "ZSQD": "Asia/Shanghai",
    "WSSS": "Asia/Singapore",
    "VILK": "Asia/Kolkata",
    "EFHK": "Europe/Helsinki",
    "WMKK": "Asia/Kuala_Lumpur",
    "RJTT": "Asia/Tokyo",
    "RCSS": "Asia/Taipei",
    "LLBG": "Asia/Jerusalem",
    "EDDM": "Europe/Berlin",
    "LFPB": "Europe/Paris",
    "EHAM": "Europe/Amsterdam",
    "LIMC": "Europe/Rome",
    "SAEZ": "America/Argentina/Buenos_Aires",
    "LEMD": "Europe/Madrid",
    "RPLL": "Asia/Manila",
    "KATL": "America/New_York",
    "KHOU": "America/Chicago",
    "KMIA": "America/New_York",
    "KDAL": "America/Chicago",
    "EPWA": "Europe/Warsaw",
    "OEJN": "Asia/Riyadh",
    "FACT": "Africa/Johannesburg",
    "LTAC": "Europe/Istanbul",
    "LTFM": "Europe/Istanbul",
    "KSFO": "America/Los_Angeles",
    "SBGR": "America/Sao_Paulo",
    "KLAX": "America/Los_Angeles",
    "UUWW": "Europe/Moscow",
    "KAUS": "America/Chicago",
    "OPKC": "Asia/Karachi",
    "KDEN": "America/Denver",
    "CYYZ": "America/Toronto",
    "KLGA": "America/New_York",
    "KSEA": "America/Los_Angeles",
    "KORD": "America/Chicago",
    "MPMG": "America/Panama",
    "MMMX": "America/Mexico_City",
}


def temperature(raw):
    if raw.lstrip().startswith("SPECI"):
        return None
    match = re.search(r"\bT([01])(\d{3})[01]\d{3}\b", raw)
    if match:
        return Decimal(match[2]) / 10 * (-1 if match[1] == "1" else 1)
    match = re.search(r"(?:^|\s)(M?\d{2})/(?:M?\d{2}|/)?(?=\s|$)", raw)
    if match:
        return Decimal(match[1].replace("M", "-"))
    return None


def rounded(value, unit):
    if unit == "F":
        value = value * Decimal(9) / 5 + 32
    return int((value + Decimal(".5")).to_integral_value(rounding=ROUND_FLOOR))


def upper_bin(label):
    if "higher" in label or "above" in label or "+" in label:
        return None
    values = re.findall(r"(?<!\d)-?\d+", label)
    # Hyphenated positive ranges need separation rather than a negative second bound.
    range_match = re.match(r"^(-?\d+)-(-?\d+)", label)
    if range_match:
        return int(range_match[2])
    return int(values[0]) if values else None


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--events", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=False)
    events = json.loads(a.events.read_text())
    (a.output / "events_raw.json").write_bytes(a.events.read_bytes())
    selected = []
    for e in events:
        if not e["title"].startswith("Highest temperature") or "October 31" not in e["title"]:
            continue
        desc = e["markets"][0]["description"]
        station = re.search(r"ids=([A-Z]{4})", desc)
        if (
            station is None
            or station[1] not in ZONES
            or "routine" not in desc
            or "hourly temperature remark" not in desc
        ):
            continue
        selected.append((e, station[1]))
    stations = sorted({s for _, s in selected})
    observations = []
    screens = []
    rejected = []
    async with httpx.AsyncClient(timeout=30) as c:
        with (a.output / "source_requests.jsonl").open("x", buffering=1) as raw:
            for start in range(0, len(stations), 10):
                params = dict(ids=",".join(stations[start : start + 10]), format="json", hours=36)
                before = time.time()
                r = await c.get("https://aviationweather.gov/api/data/metar", params=params)
                r.raise_for_status()
                rows = r.json()
                raw.write(
                    json.dumps(
                        dict(requested=before, received=time.time(), url=str(r.url), data=rows)
                    )
                    + "\n"
                )
                observations.extend(rows)
        (a.output / "metar_observations.json").write_text(json.dumps(observations, indent=2))
        for e, station in selected:
            obs = {}
            for o in observations:
                if o.get("icaoId") != station:
                    continue
                local = dt.datetime.fromtimestamp(o["obsTime"], ZoneInfo(ZONES[station]))
                if local.date() != dt.date(2026, 10, 31):
                    continue
                raw = o.get("rawOb", "")
                if raw.lstrip().startswith("SPECI") or o.get("metarType") == "SPECI":
                    continue
                value = temperature(raw)
                if value is None:
                    continue
                existing = obs.get(o["obsTime"])
                if existing is None or o.get("receiptTime", "") > existing["observation"].get(
                    "receiptTime", ""
                ):
                    obs[o["obsTime"]] = dict(tempC=str(value), observation=o)
            if not obs:
                rejected.append(dict(event_id=e["id"], reason="NO_QUALIFYING_OBSERVATIONS"))
                continue
            max_c = max(Decimal(o["tempC"]) for o in obs.values())
            for m in e["markets"]:
                label = m.get("groupItemTitle", "")
                unit = "F" if "°F" in label else "C"
                bound = upper_bin(label)
                if bound is None or rounded(max_c, unit) <= bound:
                    continue
                supports = [o for o in obs.values() if rounded(Decimal(o["tempC"]), unit) > bound]
                case = dict(
                    event_id=e["id"],
                    title=e["title"],
                    market=m["conditionId"],
                    question=m["question"],
                    station=station,
                    timezone=ZONES[station],
                    bracket=label,
                    upper=bound,
                    unit=unit,
                    max_observed_C=str(max_c),
                    max_rounded=rounded(max_c, unit),
                    supporting_routine_observations=len(supports),
                    support_evidence=supports,
                    closed=m.get("closed"),
                    accepting_orders=m.get("acceptingOrders"),
                )
                if m.get("closed") or not m.get("acceptingOrders"):
                    case["status"] = "CLOSED_OR_NOT_ACCEPTING"
                    screens.append(case)
                    continue
                outs = array(m["outcomes"])
                ids = array(m["clobTokenIds"])
                token = ids[outs.index("No")]
                before = time.time()
                r = await c.get("https://clob.polymarket.com/book", params={"token_id": token})
                case["book_requested"] = before
                case["book_received"] = time.time()
                if r.status_code != 200:
                    case["status"] = "BOOK_ERROR"
                    screens.append(case)
                    continue
                book = r.json()
                case["book"] = book
                b = Book()
                b.update(dict(book, event_type="book"))
                price = vwap(b, "BUY", 5)
                case["no_ask_vwap5"] = price
                fd = m.get("feeSchedule") or {}
                known = fd.get("rate") is not None and fd.get("exponent") is not None
                fee = (
                    fd["rate"] * (price * (1 - price)) ** fd["exponent"]
                    if known and price is not None
                    else None
                )
                case["gamma_feeSchedule"] = fd
                case["fee_per_share"] = fee
                case["conditional_net_per_share"] = (
                    1 - price - fee - 0.002 if fee is not None else None
                )
                case["status"] = (
                    "MEANINGFUL_PRICE_CANDIDATE"
                    if price is not None
                    and price <= 0.98
                    and fee is not None
                    and case["conditional_net_per_share"] > 0
                    and len(supports) >= 2
                    else "NO_MEANINGFUL_EXECUTABLE_MARGIN"
                )
                screens.append(case)
    (a.output / "barrier_screen.json").write_text(
        json.dumps(
            dict(
                created=time.time(),
                events_checked=len(selected),
                stations=len(stations),
                rejected=rejected,
                cases=screens,
                qualification="Published routine-METAR maximum excludes lower brackets only conditional on no material source correction and correct settlement interpretation. Not a guaranteed payoff or proven edge. Any positive candidate needs repeated executable depth, current CLOB fee verification, independent observations and correction-risk/settlement audit.",
            ),
            indent=2,
        )
    )
    candidates = [v for v in screens if v["status"] == "MEANINGFUL_PRICE_CANDIDATE"]
    (a.output / "candidates.json").write_text(json.dumps(candidates, indent=2))
    print(
        json.dumps(
            dict(
                events=len(selected),
                cases=len(screens),
                candidates=len(candidates),
                statuses={
                    s: sum(v["status"] == s for v in screens)
                    for s in {v["status"] for v in screens}
                },
                path=str(a.output),
            )
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
