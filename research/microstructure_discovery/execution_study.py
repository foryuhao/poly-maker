"""Prospective taker-only execution study; see PROTOCOL_V2.md."""

import argparse
import collections
import hashlib
import heapq
import json
import statistics
from pathlib import Path

from study import Book

RULES = ("follow_imbalance", "fade_imbalance", "follow_large_flow", "fade_large_flow")


def signals(root, cutoff=None):
    meta = json.loads((root / "markets.json").read_text())
    pairs = collections.defaultdict(list)
    for m in meta:
        pairs[m["market"]].append(m)
    primary = {v[0]["asset"]: v for v in pairs.values()}
    seen = set()
    last = {}
    accepted = []
    for lineno, line in enumerate((root / "trades.jsonl").open(), 1):
        t = json.loads(line)
        if cutoff is not None and t["received"] > cutoff:
            continue
        asset = t["asset_id"]
        key = tuple(
            t.get(k) for k in ("asset_id", "timestamp", "side", "price", "size", "transaction_hash")
        )
        if asset not in primary or key in seen:
            continue
        seen.add(key)
        pre = t.get("pre")
        if not pre or t.get("side") not in ("BUY", "SELL"):
            continue
        if not -0.25 <= t["received"] - float(t["timestamp"]) / 1000 <= 2:
            continue
        sign = 1 if t["side"] == "BUY" else -1
        notional = float(t["price"]) * float(t.get("size", 0))
        depth = pre["bid_depth"] + pre["ask_depth"]
        imbalance = (pre["bid_depth"] - pre["ask_depth"]) / depth if depth else 0
        rules = []
        if pre["spread"] <= 0.02 + 1e-9 and notional >= 5:
            if sign * imbalance >= 0.5:
                rules.append("follow_imbalance")
            if sign * imbalance <= -0.5:
                rules.append("fade_imbalance")
        if pre["spread"] <= 0.01 + 1e-9 and notional >= 50:
            rules.extend(("follow_large_flow", "fade_large_flow"))
        market = primary[asset][0]["market"]
        if not rules or t["received"] - last.get(market, -1e99) < 120:
            continue
        last[market] = t["received"]
        for rule in rules:
            follow = rule.startswith("follow")
            buy_primary = (sign == 1) == follow
            target = primary[asset][0 if buy_primary else 1]
            accepted.append(
                dict(
                    id=len(accepted),
                    time=t["received"],
                    generation=t["generation"],
                    asset=target["asset"],
                    signal_asset=asset,
                    market=market,
                    question=target["question"],
                    outcome=target["outcome"],
                    rule=rule,
                    raw_trade_line=lineno,
                    imbalance=imbalance,
                    notional=notional,
                    arrival_spread=pre["spread"],
                )
            )
    return accepted


def vwap(book, side, quantity=5):
    levels = book.asks if side == "BUY" else book.bids
    remaining = quantity
    cash = 0.0
    for price, size in sorted(levels.items(), reverse=side == "SELL"):
        qty = min(remaining, size)
        remaining -= qty
        cash += qty * price
        if remaining <= 1e-8:
            return cash / quantity
    return None


def replay(root, ss, end):
    requests = []
    results = {}
    for s in ss:
        for horizon in (30, 120):
            if s["time"] + horizon + 2 > end:
                continue
            for delay in (0.5, 1.0, 2.0):
                key = f"{s['id']}:{horizon}:{delay}"
                results[key] = dict(signal=s, horizon=horizon, delay=delay)
                for leg, when in [
                    ("decision", s["time"]),
                    ("entry", s["time"] + delay),
                    ("exit", s["time"] + horizon + delay),
                ]:
                    heapq.heappush(requests, (when, key, leg))
    books = collections.defaultdict(Book)
    dirty = set()
    regressed = set()
    source = {}
    generation = None
    checks = collections.Counter()

    def process_requests(at, inclusive):
        while requests and (requests[0][0] <= at if inclusive else requests[0][0] < at):
            when, key, leg = heapq.heappop(requests)
            row = results[key]
            s = row["signal"]
            a = s["asset"]
            b = books[a]
            status = "OK"
            if at - when > 2:
                status = "GAP"
            elif generation != s["generation"] or not b.valid:
                status = "CONNECTION_OR_NO_BOOK"
            elif a in dirty or a in regressed or b.quote() is None:
                status = "UNTRUSTED_BOOK"
            signal_asset = s.get("signal_asset", a)
            if leg == "decision" and (
                signal_asset in dirty
                or signal_asset in regressed
                or books[signal_asset].quote() is None
            ):
                status = "UNTRUSTED_SIGNAL_BOOK"
            price = vwap(b, "BUY" if leg == "entry" else "SELL") if status == "OK" else None
            if status == "OK" and price is None:
                status = "INSUFFICIENT_DEPTH"
            row[leg] = dict(at=at, requested=when, status=status, price=price)

    for line in (root / "raw.jsonl").open():
        r = json.loads(line)
        process_requests(r["received"], False)
        if r.get("generation") != generation:
            books.clear()
            dirty.clear()
            regressed.clear()
            source.clear()
            generation = r.get("generation")
        e = r.get("event", {})
        kind = e.get("event_type")
        if kind == "book":
            a = e["asset_id"]
            books[a].update(e)
            dirty.discard(a)
            regressed.discard(a)
            source[a] = float(e.get("timestamp", 0))
        elif kind == "market_resolved":
            for token in e.get("assets_ids", []):
                regressed.add(token)
        elif kind == "price_change":
            changed = {}
            for c in e.get("price_changes", []):
                a = c["asset_id"]
                ts = float(e.get("timestamp", 0))
                if ts < source.get(a, 0):
                    regressed.add(a)
                    checks["source_regression"] += 1
                source[a] = max(ts, source.get(a, 0))
                books[a].update(dict(c, event_type=kind))
                changed[a] = c
            for a, c in changed.items():
                q = books[a].quote()
                if q and c.get("best_bid") is not None and c.get("best_ask") is not None:
                    if (
                        abs(q["bid"] - float(c["best_bid"])) > 1e-8
                        or abs(q["ask"] - float(c["best_ask"])) > 1e-8
                    ):
                        dirty.add(a)
                    else:
                        dirty.discard(a)
        process_requests(r["received"], True)
    fee_path = root / "fees_provenance.json"
    fee_rows = json.loads(fee_path.read_text())["markets"] if fee_path.exists() else []
    fee_map = {
        r["market"]: r["clob_info"].get("fd", {})
        for r in fee_rows
        if r.get("effective_parameters_agree")
    }
    output = []
    for row in results.values():
        if (
            row.get("decision", {}).get("status") != "OK"
            or row.get("entry", {}).get("status") != "OK"
        ):
            checks["no_entry"] += 1
            continue
        entry = row["entry"]["price"]
        exit_price = row.get("exit", {}).get("price")
        # Missing/untrusted exit is a zero recovery stress, never drop an entered loss.
        if exit_price is None:
            exit_price = 0.0
            checks["exit_zero_recovery"] += 1
        fees = 0.10 * (entry * (1 - entry) + exit_price * (1 - exit_price))
        row["gross_per_share"] = exit_price - entry
        row["stress_fee_per_share"] = fees
        row["stress_net_per_share"] = exit_price - entry - fees - 0.002
        fd = fee_map.get(row["signal"]["market"])
        row["fee_parameters_verified"] = fd is not None
        if fd is not None:
            effective = fd["r"] * (
                (entry * (1 - entry)) ** fd["e"] + (exit_price * (1 - exit_price)) ** fd["e"]
            )
            row["effective_fee_per_share"] = effective
            row["net_per_share"] = exit_price - entry - effective - 0.002
        else:
            row["net_per_share"] = row["stress_net_per_share"]
        output.append(row)
    return output, dict(checks)


def aggregate(rows):
    groups = collections.defaultdict(list)
    for r in rows:
        s = r["signal"]
        groups[(s["rule"], r["horizon"], r["delay"], s["market"])].append(r)
    result = []
    for (rule, horizon, delay, market), rs in groups.items():
        vals = [r["net_per_share"] for r in rs]
        blocks = collections.defaultdict(list)
        for r in rs:
            blocks[int(r["signal"]["time"] // 300)].append(r["net_per_share"])
        result.append(
            dict(
                rule=rule,
                horizon=horizon,
                delay=delay,
                market=market,
                question=rs[0]["signal"]["question"],
                n=len(vals),
                blocks=len(blocks),
                mean_net=statistics.mean(vals),
                total_net_5shares=5 * sum(vals),
                positive=sum(v > 0 for v in vals),
                worst=min(vals),
                fee_parameters_verified=all(r["fee_parameters_verified"] for r in rs),
                mean_stress_net=statistics.mean(r["stress_net_per_share"] for r in rs),
            )
        )
    return sorted(result, key=lambda r: r["mean_net"], reverse=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("root", type=Path)
    p.add_argument("--train-only", action="store_true")
    a = p.parse_args()
    root = a.root
    # Timestamp bounds from raw capture, independent of which trades mature or win.
    with (root / "raw.jsonl").open() as f:
        start = json.loads(next(f))["received"]
        end = start
        for line in f:
            end = json.loads(line)["received"]
    split = start + 900
    ss = signals(root, cutoff=split - 122 if a.train_only else None)
    # Selection is training only. Validation reads require a frozen selection first.
    selection = root / "frozen_selection.json"
    if not a.train_only and not selection.exists():
        raise SystemExit("Freeze training selection using --train-only before evaluating holdout.")
    rows, checks = replay(root, ss, min(end, split) if a.train_only else end)
    if a.train_only:
        rows = [r for r in rows if r["signal"]["time"] <= split - 122]
        agg = aggregate(rows)
        eligible = [
            r
            for r in agg
            if r["delay"] == 0.5
            and r["n"] >= 5
            and r["mean_net"] > 0
            and r["fee_parameters_verified"]
        ]
        candidate = eligible[0] if eligible else None
        # Exclusive creation prevents silently overwriting the registered choice.
        with selection.open("x") as f:
            json.dump(
                dict(
                    split=split,
                    training_end=split - 122,
                    candidate=candidate,
                    evaluator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    protocol_sha256=hashlib.sha256(
                        Path(__file__).with_name("PROTOCOL_V2.md").read_bytes()
                    ).hexdigest(),
                ),
                f,
                indent=2,
            )
        name = "training"
    else:
        frozen = json.loads(selection.read_text())
        if (
            frozen.get("evaluator_sha256")
            != hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        ):
            raise SystemExit(
                "Evaluator changed after freeze; preserve selection and audit before evaluation."
            )
        if (
            frozen["protocol_sha256"]
            != hashlib.sha256(Path(__file__).with_name("PROTOCOL_V2.md").read_bytes()).hexdigest()
        ):
            raise SystemExit(
                "Protocol changed after freeze; do not evaluate under a substituted protocol."
            )
        candidate = frozen["candidate"]
        name = "holdout"
        rows = [
            r
            for r in rows
            if r["signal"]["time"] >= split + 120
            and (
                candidate is not None
                and r["signal"]["market"] == candidate["market"]
                and r["signal"]["rule"] == candidate["rule"]
                and r["horizon"] == candidate["horizon"]
            )
        ]
        agg = aggregate(rows)
    (root / f"{name}_execution.json").write_text(
        json.dumps(
            dict(
                bounds=dict(start=start, end=end, split=split),
                checks=checks,
                summary=agg,
                rows=rows,
            ),
            indent=2,
        )
    )
    print(
        json.dumps(dict(stage=name, candidate=candidate, checks=checks, summary=agg[:10]), indent=2)
    )


if __name__ == "__main__":
    main()
