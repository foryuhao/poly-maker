"""Public historical calibration collection; no historical own-fill simulation."""

import argparse
import asyncio
import datetime as dt
import gzip
import hashlib
import json
import time
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

ZONES = {
    "atlanta": "America/New_York",
    "chicago": "America/Chicago",
    "london": "Europe/London",
    "miami": "America/New_York",
    "nyc": "America/New_York",
    "seattle": "America/Los_Angeles",
    "seoul": "Asia/Seoul",
    "shanghai": "Asia/Shanghai",
    "tokyo": "Asia/Tokyo",
    "toronto": "America/Toronto",
}
SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def array(value):
    return json.loads(value) if isinstance(value, str) else value


def interval(city, day):
    start = dt.datetime.combine(day, dt.time(9), ZoneInfo(ZONES[city])).timestamp()
    return int(start), int(start + 300)


def no_tokens(event):
    result = {}
    for market in event["markets"]:
        labels, tokens = array(market["outcomes"]), array(market["clobTokenIds"])
        if labels != ["Yes", "No"] or len(tokens) != 2:
            raise ValueError("noncanonical binary labels or token mapping")
        cid = market["conditionId"]
        if cid in result:
            raise ValueError("duplicate condition within event")
        result[cid] = dict(token=tokens[1], market=market)
    return result


def first_candidate(trades, mapping, start, end):
    eligible = []
    for row in trades:
        cid = row["condition_id"]
        if cid not in mapping or row["side"] != "BUY":
            raise ValueError("event/side filter returned foreign fill")
        price, size = Decimal(str(row["price"])), Decimal(str(row["size"]))
        if price * size < Decimal("9.99998"):
            raise ValueError("server CASH minimum filter not honored")
        if (
            start <= row["timestamp"] < end
            and row["token_id"] == mapping[cid]["token"]
            and Decimal(".05") <= price <= Decimal(".25")
        ):
            eligible.append(row)
    return min(
        eligible,
        key=lambda r: (
            r["timestamp"],
            r["transaction_hash"],
            r["token_id"],
            Decimal(str(r["price"])),
            Decimal(str(r["size"])),
        ),
        default=None,
    )


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    a.output.mkdir(exist_ok=False, parents=True)
    protocol = Path(__file__).with_name("PROTOCOL_V9_WEATHER_FAVORITES.md")
    startday, endday = dt.date(2026, 8, 1), dt.date(2026, 10, 30)
    grid = [(city, startday + dt.timedelta(days=i)) for city in ZONES for i in range(91)]
    (a.output / "registration.json").write_text(
        json.dumps(
            dict(
                created=time.time(),
                code_sha256=SOURCE_SHA256,
                protocol_sha256=hashlib.sha256(protocol.read_bytes()).hexdigest(),
                first_date=str(startday),
                last_date=str(endday),
                city_zones=ZONES,
                expected_city_dates=len(grid),
                clock="09:00:00 <= local time < 09:05:00",
                order_placement=False,
                own_fill_backtest=False,
            ),
            indent=2,
        )
    )
    semaphore = asyncio.Semaphore(3)
    counts = {}
    async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:

        async def collect(city, day):
            name = f"{city}_{day.isoformat()}"
            folder = a.output / name
            folder.mkdir()
            seq = 0

            async def get(url, params=None):
                nonlocal seq
                for attempt in range(4):
                    before = time.time()
                    reply = await client.get(url, params=params)
                    seq += 1
                    target = folder / f"source_{seq:04d}.json.gz"
                    target.write_bytes(gzip.compress(reply.content, compresslevel=1))
                    with (folder / "requests.jsonl").open("a") as out:
                        out.write(
                            json.dumps(
                                dict(
                                    requested=before,
                                    received=time.time(),
                                    url=str(reply.url),
                                    status=reply.status_code,
                                    source=str(target),
                                    response_sha256=hashlib.sha256(reply.content).hexdigest(),
                                )
                            )
                            + "\n"
                        )
                    if reply.status_code in (429, 500, 502, 503, 504):
                        await asyncio.sleep(2**attempt)
                        continue
                    reply.raise_for_status()
                    return reply.json()
                raise RuntimeError("request retries exhausted")

            row = dict(city=city, date=str(day), status="PENDING", requests=0)
            try:
                async with semaphore:
                    slug = f"highest-temperature-in-{city}-on-{day.strftime('%B').lower()}-{day.day}-{day.year}"
                    events = await get(
                        "https://gamma-api.polymarket.com/events", dict(slug=slug, closed="true")
                    )
                    if not events:
                        row.update(status="EVENT_NOT_FOUND", requested_slug=slug)
                    elif len(events) != 1 or events[0]["slug"] != slug:
                        raise ValueError("ambiguous event or slug filter ignored")
                    else:
                        event = events[0]
                        mapping = no_tokens(event)
                        if not mapping:
                            raise ValueError("empty partition")
                        row.update(event_id=event["id"], slug=slug, contracts=len(mapping))
                        (folder / "event.json").write_text(json.dumps(event))
                        lo, hi = interval(city, day)
                        previous = float("inf")
                        cursor = None
                        cursors = set()
                        candidates = []
                        pages = 0
                        while True:
                            params = dict(
                                event_id=event["id"],
                                side="BUY",
                                taker_only="true",
                                filter_type="CASH",
                                filter_amount=10,
                                limit=1000,
                            )
                            if cursor:
                                params["cursor"] = cursor
                            data = await get("https://data-api.polymarket.com/v2/trades", params)
                            rows = data["data"]
                            for trade in rows:
                                if trade["timestamp"] > previous:
                                    raise ValueError("descending timestamp order violated")
                                previous = trade["timestamp"]
                            candidate = first_candidate(rows, mapping, lo, hi)
                            if candidate:
                                candidates.append(candidate)
                            pages += 1
                            cursor = data["pagination"].get("next_cursor")
                            if not cursor or (rows and rows[-1]["timestamp"] < lo):
                                break
                            if cursor in cursors or pages >= 200:
                                raise ValueError(
                                    "pagination cycle or safety cap; do not count as zero"
                                )
                            cursors.add(cursor)
                        chosen = first_candidate(candidates, mapping, lo, hi)
                        row.update(
                            pages=pages,
                            window_start=lo,
                            window_end=hi,
                            status="SELECTED" if chosen else "NO_QUALIFYING_FILL",
                        )
                        if chosen:
                            cid = chosen["condition_id"]
                            resolution = await get(
                                "https://data-api.polymarket.com/v2/resolutions",
                                dict(condition=cid),
                            )
                            row.update(
                                trade=chosen, market=mapping[cid]["market"], resolution=resolution
                            )
            except Exception as exc:
                row.update(status="DATA_QUALITY_ERROR", error=repr(exc))
            row.update(completed=time.time(), requests=seq)
            (folder / "observation.json").write_text(json.dumps(row, indent=2))
            counts[row["status"]] = counts.get(row["status"], 0) + 1
            (a.output / "quality.json").write_text(
                json.dumps(dict(received=time.time(), counts=counts))
            )
            return row["status"]

        await asyncio.gather(*(collect(city, day) for city, day in grid))
    (a.output / "collection_complete.json").write_text(
        json.dumps(dict(completed=time.time(), counts=counts), indent=2)
    )


if __name__ == "__main__":
    asyncio.run(main())
