"""Secondary public-maker exposure study; never assume our place in the queue."""

import argparse
import asyncio
import gzip
import hashlib
import json
import time
from decimal import Decimal
from pathlib import Path

import httpx
from weather_calibration import array, no_tokens

SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def choose(rows, event, lo, hi):
    mapping = no_tokens(event)
    yes = {m["conditionId"]: array(m["clobTokenIds"])[0] for m in event["markets"]}
    candidates = []
    for row in rows:
        cid = row["condition_id"]
        if cid not in mapping or row["side"] != "BUY":
            raise ValueError("foreign trade or side")
        p = Decimal(str(row["price"]))
        if (
            lo <= row["timestamp"] < hi
            and row["token_id"] == yes[cid]
            and Decimal(".75") <= p <= Decimal(".95")
            and Decimal(str(row["size"])) >= 2 / (1 - p)
        ):
            candidates.append(row)
    return min(
        candidates,
        key=lambda r: (r["timestamp"], r["transaction_hash"], r["token_id"], r["price"], r["size"]),
        default=None,
    )


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("source", type=Path)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    assert (a.source / "collection_complete.json").exists()
    a.output.mkdir(exist_ok=False)
    protocol = Path(__file__).with_name("PROTOCOL_V10_WEATHER_COUNTERPARTY.md")
    (a.output / "registration.json").write_text(
        json.dumps(
            dict(
                created=time.time(),
                code_sha256=SOURCE_SHA256,
                protocol_sha256=hashlib.sha256(protocol.read_bytes()).hexdigest(),
                source=str(a.source),
                expected_city_dates=910,
                own_orders=False,
                note="Secondary same-parent study, after coverage-only V9 inspection and before inspecting any financial results.",
            ),
            indent=2,
        )
    )
    sem = asyncio.Semaphore(3)
    counts = {}
    async with httpx.AsyncClient(timeout=20) as client:

        async def one(path):
            origin = json.loads(path.read_text())
            row = {k: origin[k] for k in ["city", "date", "status"]}
            folder = a.output / path.parent.name
            folder.mkdir()
            try:
                if origin["status"] not in ("SELECTED", "NO_QUALIFYING_FILL"):
                    raise ValueError("source city-date data quality uncertain")
                event = json.loads((path.parent / "event.json").read_text())
                trades = []
                records = []
                for line in (path.parent / "requests.jsonl").open():
                    request = json.loads(line)
                    if "/v2/trades?" not in request["url"] or request["status"] != 200:
                        continue
                    raw = gzip.decompress(Path(request["source"]).read_bytes())
                    assert hashlib.sha256(raw).hexdigest() == request["response_sha256"]
                    trades.extend(json.loads(raw)["data"])
                    records.append(request["response_sha256"])
                chosen = choose(trades, event, origin["window_start"], origin["window_end"])
                row.update(
                    status="SELECTED" if chosen else "NO_QUALIFYING_FILL",
                    source_observation_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    source_page_sha256=records,
                    event_id=event["id"],
                )
                if chosen:
                    cid = chosen["condition_id"]
                    row.update(trade=chosen, market=no_tokens(event)[cid]["market"])
                    async with sem:
                        t = time.time()
                        response = await client.get(
                            "https://data-api.polymarket.com/v2/resolutions",
                            params=dict(condition=cid),
                        )
                        (folder / "resolution_source.json").write_text(
                            json.dumps(
                                dict(
                                    requested=t,
                                    received=time.time(),
                                    url=str(response.url),
                                    status=response.status_code,
                                    body=response.text,
                                )
                            )
                        )
                        response.raise_for_status()
                        row["resolution"] = response.json()
            except Exception as exc:
                row.update(status="DATA_QUALITY_ERROR", error=repr(exc))
            (folder / "observation.json").write_text(json.dumps(row, indent=2))
            counts[row["status"]] = counts.get(row["status"], 0) + 1
            (a.output / "quality.json").write_text(
                json.dumps(dict(received=time.time(), counts=counts))
            )

        await asyncio.gather(*(one(p) for p in sorted(a.source.glob("*/observation.json"))))
    (a.output / "collection_complete.json").write_text(
        json.dumps(dict(completed=time.time(), counts=counts))
    )


if __name__ == "__main__":
    asyncio.run(main())
