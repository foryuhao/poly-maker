"""Public-data-only discovery and event study. No trading/client credentials."""

import argparse
import asyncio
import bisect
import collections
import csv
import datetime as dt
import hashlib
import json
import math
import statistics
import time
from pathlib import Path

import httpx
import websockets

GAMMA = "https://gamma-api.polymarket.com"
WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


def category(m):
    s = (m.get("question", "") + " " + m.get("slug", "")).lower()
    groups = {
        "politics": ["election", "senate", "republican", "democrat", "trump", "president"],
        "sports": [" vs ", " vs.", "nba", "nfl", "counter-strike", "premier", "winner", "champion"],
        "crypto": ["bitcoin", "ethereum", "btc", "solana", "crypto", "up or down"],
        "macro": ["fed ", "interest rate", "inflation", "gdp", "recession", "oil", "gold"],
    }
    return next((k for k, words in groups.items() if any(w in s for w in words)), "other")


def array(v):
    return json.loads(v) if isinstance(v, str) else v


async def discover(out, limit):
    async with httpx.AsyncClient(timeout=30) as c:
        pages = []
        for order in ["volume24hr", "id"]:
            for offset in [0, 100, 200]:
                r = await c.get(
                    GAMMA + "/markets",
                    params=dict(
                        active="true",
                        closed="false",
                        limit=100,
                        offset=offset,
                        order=order,
                        ascending="false",
                    ),
                )
                r.raise_for_status()
                pages.extend(r.json())
        (out / "discovery_raw.json").write_text(json.dumps(pages))
    unique = {m["id"]: m for m in pages}
    eligible = []
    for m in unique.values():
        tokens = array(m.get("clobTokenIds", []))
        prices = array(m.get("outcomePrices", []))
        if (
            len(tokens) != 2
            or not m.get("acceptingOrders")
            or not m.get("enableOrderBook")
            or not prices
            or not 0.03 <= float(prices[0]) <= 0.97
        ):
            continue
        eligible.append(m)
    eligible.sort(key=lambda m: float(m.get("volume24hr", 0)), reverse=True)
    selected, counts = [], collections.Counter()
    for m in eligible:
        cat = category(m)
        if counts[cat] >= max(4, math.ceil(limit / 4)):
            continue
        counts[cat] += 1
        selected.append(m)
        if len(selected) >= limit:
            break
    metadata = []
    for m in selected:
        for token, outcome in zip(array(m["clobTokenIds"]), array(m["outcomes"]), strict=True):
            metadata.append(
                dict(
                    asset=token,
                    outcome=outcome,
                    market=m["conditionId"],
                    question=m["question"],
                    slug=m["slug"],
                    category=category(m),
                    volume24hr=m.get("volume24hr"),
                    tick=m.get("orderPriceMinTickSize", 0.01),
                )
            )
    (out / "markets.json").write_text(json.dumps(metadata, indent=2))
    (out / "discovery_summary.json").write_text(
        json.dumps(
            dict(
                raw_rows=len(pages),
                unique_markets=len(unique),
                eligible=len(eligible),
                selected=len(selected),
                categories=dict(counts),
            ),
            indent=2,
        )
    )
    return metadata


class Book:
    def __init__(self):
        self.bids, self.asks = {}, {}
        self.valid = False

    def update(self, event):
        if event["event_type"] == "book":
            self.bids = {
                float(x["price"]): float(x["size"]) for x in event["bids"] if float(x["size"]) > 0
            }
            self.asks = {
                float(x["price"]): float(x["size"]) for x in event["asks"] if float(x["size"]) > 0
            }
            self.valid = True
        else:
            side = self.bids if event["side"] == "BUY" else self.asks
            price, size = float(event["price"]), float(event["size"])
            if size > 0:
                side[price] = size
            else:
                side.pop(price, None)

    def quote(self):
        if not self.valid or not self.bids or not self.asks:
            return None
        bid, ask = max(self.bids), min(self.asks)
        if not 0 < bid < ask < 1:
            return None
        return dict(
            bid=bid,
            ask=ask,
            mid=(bid + ask) / 2,
            spread=ask - bid,
            bid_depth=sum(v for p, v in self.bids.items() if p >= bid - 0.02),
            ask_depth=sum(v for p, v in self.asks.items() if p <= ask + 0.02),
        )


async def collect(out, metadata, seconds):
    books = {m["asset"]: Book() for m in metadata}
    end = time.monotonic() + seconds
    counts = collections.Counter()
    generation = 0
    with (
        (out / "raw.jsonl").open("w") as raw,
        (out / "quotes.jsonl").open("w") as quotes,
        (out / "trades.jsonl").open("w") as trades,
    ):

        def write(f, v):
            f.write(json.dumps(v, separators=(",", ":")) + "\n")

        while time.monotonic() < end:
            try:
                async with websockets.connect(
                    WS, ping_interval=15, ping_timeout=15, max_size=2**24
                ) as ws:
                    generation += 1
                    for book in books.values():
                        book.valid = False
                    await ws.send(
                        json.dumps(
                            dict(assets_ids=list(books), type="market", custom_feature_enabled=True)
                        )
                    )
                    last_sample, last_ping = 0.0, time.monotonic()
                    while time.monotonic() < end:
                        if time.monotonic() - last_ping >= 10:
                            await ws.send("PING")
                            last_ping = time.monotonic()
                        try:
                            message = await asyncio.wait_for(
                                ws.recv(), timeout=min(1, max(0.01, end - time.monotonic()))
                            )
                        except TimeoutError:
                            message = None
                        now = time.time()
                        if message and message not in ("PONG", "PING"):
                            parsed = json.loads(message)
                            events = parsed if isinstance(parsed, list) else [parsed]
                            for event in events:
                                write(raw, dict(received=now, generation=generation, event=event))
                                kind = event.get("event_type", event.get("type", "unknown"))
                                counts[kind] += 1
                                if kind == "book" and event.get("asset_id") in books:
                                    books[event["asset_id"]].update(event)
                                elif kind == "price_change":
                                    for change in event.get("price_changes", []):
                                        if change.get("asset_id") in books:
                                            books[change["asset_id"]].update(
                                                dict(change, event_type=kind)
                                            )
                                elif kind == "last_trade_price" and event.get("asset_id") in books:
                                    write(
                                        trades,
                                        dict(
                                            received=now,
                                            generation=generation,
                                            pre=books[event["asset_id"]].quote(),
                                            **event,
                                        ),
                                    )
                        if now - last_sample >= 1:
                            for asset, book in books.items():
                                q = book.quote()
                                if q:
                                    write(
                                        quotes,
                                        dict(received=now, generation=generation, asset=asset, **q),
                                    )
                            last_sample = now
            except Exception as exc:
                counts["disconnects"] += 1
                write(raw, dict(received=time.time(), error=repr(exc), generation=generation))
                await asyncio.sleep(min(2, max(0, end - time.monotonic())))
    (out / "collection.json").write_text(
        json.dumps(
            dict(counts=counts, generations=generation, duration_requested=seconds), indent=2
        )
    )


def analyze(out):
    meta = {m["asset"]: m for m in json.loads((out / "markets.json").read_text())}
    quotes = collections.defaultdict(list)
    for line in (out / "quotes.jsonl").open():
        q = json.loads(line)
        quotes[q["asset"]].append(q)
    times = {a: [q["received"] for q in qs] for a, qs in quotes.items()}
    audit_path = out / "replay_audit.json"
    audit = json.loads(audit_path.read_text()) if audit_path.exists() else {}
    bad = collections.defaultdict(list)
    for interval in audit.get("bad_intervals", []):
        bad[interval["asset"]].append((interval["start"] - 1, interval["end"] + 1))

    def reliable(asset, when):
        return not any(start <= when <= end for start, end in bad[asset])

    checks = collections.Counter()
    seen, events = set(), []
    for lineno, line in enumerate((out / "trades.jsonl").open(), 1):
        t = json.loads(line)
        # No unique execution ID in this stream. Conservative signature dedup may merge identical fills.
        key = tuple(
            t.get(k) for k in ["asset_id", "timestamp", "side", "price", "size", "transaction_hash"]
        )
        checks["raw_trade_messages"] += 1
        if key in seen:
            checks["duplicate_signature"] += 1
            continue
        seen.add(key)
        a, pre = t["asset_id"], t["pre"]
        if not pre or t.get("side") not in ("BUY", "SELL"):
            checks["invalid_prebook_or_side"] += 1
            continue
        stamp = float(t.get("timestamp", 0)) / 1000
        lag = t["received"] - stamp
        if not -0.25 <= lag <= 2:
            checks["trade_clock_or_latency_rejected"] += 1
            continue
        if not reliable(a, t["received"]):
            checks["bbo_audit_baseline_rejected"] += 1
            continue
        sign = 1 if t["side"] == "BUY" else -1
        e = dict(
            asset=a,
            question=meta[a]["question"],
            outcome=meta[a]["outcome"],
            category=meta[a]["category"],
            received=t["received"],
            source_timestamp=stamp,
            raw_trade_line=lineno,
            generation=t["generation"],
            side=t["side"],
            price=float(t["price"]),
            size=float(t.get("size", 0)),
            **{"pre_" + k: v for k, v in pre.items()},
        )
        e["trade_notional"] = e["price"] * e["size"]
        total_depth = pre["bid_depth"] + pre["ask_depth"]
        e["arrival_depth_imbalance"] = (
            (pre["bid_depth"] - pre["ask_depth"]) / total_depth if total_depth else None
        )
        past_idx = bisect.bisect_right(times.get(a, []), t["received"] - 3) - 1
        if past_idx >= 0:
            past = quotes[a][past_idx]
            if past["generation"] == t["generation"] and t["received"] - 3 - past["received"] <= 2:
                e["past_3s_signed_mid_move"] = sign * (pre["mid"] - past["mid"])
                side_key = "ask_depth" if sign == 1 else "bid_depth"
                e["past_3s_consumed_side_depth_change"] = pre[side_key] - past[side_key]
        for horizon in [5, 30, 120]:
            # First sample at/after target, maximum 2s lateness, same continuous connection.
            idx = bisect.bisect_left(times.get(a, []), t["received"] + horizon)
            qs = quotes[a]
            if (
                idx < len(qs)
                and qs[idx]["received"] <= t["received"] + horizon + 2
                and qs[idx]["generation"] == t["generation"]
                and reliable(a, qs[idx]["received"])
            ):
                q = qs[idx]
                e[f"impact_{horizon}"] = sign * (q["mid"] - pre["mid"])
                e[f"depth_ratio_{horizon}"] = q["ask_depth" if sign == 1 else "bid_depth"] / max(
                    pre["ask_depth" if sign == 1 else "bid_depth"], 1e-9
                )
                # Immediate aggressive fade, no fill simulation; gross executable price PnL.
                e[f"fade_gross_{horizon}"] = (
                    (pre["bid"] - q["ask"]) if sign == 1 else (q["bid"] - pre["ask"])
                )
            else:
                checks[f"unmatured_or_gap_{horizon}"] += 1
        events.append(e)
    # Nonoverlapping anchors per condition (both outcomes share one economic market).
    last = {}
    anchors = []
    for e in events:
        mid = meta[e["asset"]]["market"]
        if e["received"] - last.get(mid, -math.inf) >= 120:
            anchors.append(e)
            last[mid] = e["received"]
    summary = []
    for a, m in meta.items():
        es = [e for e in events if e["asset"] == a]
        mature = [e for e in es if "impact_120" in e]
        if not es:
            continue
        row = {
            **m,
            "trade_events": len(es),
            "mature_120": len(mature),
            "nonoverlap_anchors": sum(e["asset"] == a and "impact_120" in e for e in anchors),
        }
        for h in [5, 30, 120]:
            vals = [e[f"impact_{h}"] for e in es if f"impact_{h}" in e]
            row[f"mean_impact_{h}"] = statistics.mean(vals) if vals else None
            row[f"median_impact_{h}"] = statistics.median(vals) if vals else None
        # Ex-post descriptive paths, NOT a predictive classifier.
        tick = float(m["tick"])
        row["persistent_paths"] = sum(
            e.get("impact_5", 0) >= tick * 0.5 and e.get("impact_120", 0) >= tick for e in mature
        )
        row["reverting_paths"] = sum(
            e.get("impact_5", 0) >= tick * 0.5 and e.get("impact_120", math.inf) <= 0
            for e in mature
        )
        row["status"] = "DESCRIPTIVE_ONLY_NOT_VALIDATED"
        summary.append(row)
    with (out / "events.jsonl").open("w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
    if summary:
        with (out / "summary.csv").open("w") as f:
            w = csv.DictWriter(f, fieldnames=list(summary[0]))
            w.writeheader()
            w.writerows(summary)
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    checks.update(unique_accepted=len(events), nonoverlapping_anchors=len(anchors))
    (out / "quality.json").write_text(json.dumps(checks, indent=2))
    manifest = {}
    for name in ["discovery_raw.json", "markets.json", "raw.jsonl", "quotes.jsonl", "trades.jsonl"]:
        p = out / name
        manifest[name] = dict(
            bytes=p.stat().st_size, sha256=hashlib.sha256(p.read_bytes()).hexdigest()
        )
    (out / "manifest.json").write_text(
        json.dumps(dict(created_utc=dt.datetime.now(dt.UTC).isoformat(), files=manifest), indent=2)
    )
    print(json.dumps(dict(output=str(out), quality=checks, active_outcomes=len(summary)), indent=2))


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--seconds", type=float, default=600)
    p.add_argument("--markets", type=int, default=24)
    p.add_argument("--analyze-only", action="store_true")
    p.add_argument("--metadata", type=Path, help="Reuse a fixed previously discovered market list")
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=True)
    if not a.analyze_only:
        if a.metadata:
            meta = json.loads(a.metadata.read_text())
            (a.output / "markets.json").write_text(json.dumps(meta, indent=2))
            for name in ("discovery_raw.json", "discovery_summary.json", "fees_provenance.json"):
                source = a.metadata.parent / name
                if source.exists():
                    (a.output / name).write_bytes(source.read_bytes())
            (a.output / "fixed_universe.json").write_text(
                json.dumps(dict(source=str(a.metadata), registered_at=time.time()), indent=2)
            )
        else:
            meta = await discover(a.output, a.markets)
        await collect(a.output, meta, a.seconds)
    from audit import audit

    audit(a.output)
    analyze(a.output)


if __name__ == "__main__":
    asyncio.run(main())
