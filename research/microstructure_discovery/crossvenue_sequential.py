"""Causal sequential-leg policy on independently collected audit snapshots."""

import argparse
import hashlib
import json
import math
import time
from pathlib import Path

from crossvenue_scan import walk


def kfee(levels):
    return sum(math.ceil(0.07 * p * (1 - p) * q * 100 - 1e-9) / 100 for p, q in levels)


def delivered(levels, rate):
    return sum(q - math.ceil(rate * q * p * (1 - p) * 1e5 - 1e-8) / 1e5 / p for p, q in levels)


def hedge_cap(quantity, kcost, rate, tick, reserve=0.02):
    lo, hi = 0.0, 0.999
    for _ in range(60):
        p = (lo + hi) / 2
        floor = min(10, quantity * (1 - rate * (1 - p))) - quantity * p - kcost - reserve - 0.0001
        if floor >= 0:
            lo = p
        else:
            hi = p
    return math.floor((lo + 1e-10) / tick) * tick


def evaluate(trigger, direction, snapshots, fd, tick, delay):
    spec = trigger["calculations"][direction]
    quantity = spec["poly_gross_shares"]
    side = "no_dollars" if direction == "poly_no_kalshi_yes" else "yes_dollars"
    exit_side = "yes_dollars" if direction == "poly_no_kalshi_yes" else "no_dollars"
    first = snapshots[0.5]["books"]["kalshi"]
    cap = max(p for p, q in spec["kalshi_levels"])
    ka = [
        (1 - float(p), float(q))
        for p, q in first["data"]["orderbook_fp"][side]
        if 1 - float(p) <= cap + 1e-8
    ]
    leg = walk(ka, 10)
    if leg is None:
        return dict(status="NO_FIRST_FILL", net=0.0)
    cost = leg[0] + kfee(leg[1])
    pcap = hedge_cap(quantity, cost, fd["r"], tick)
    pb = snapshots[2.5]["books"]["poly"]
    hedge = None
    causal = pb["requested"] >= first["received"] + delay + 0.25
    if causal and quantity >= 5 and quantity * pcap >= 1:
        pa = [
            (float(v["price"]), float(v["size"]))
            for v in pb["data"].get("asks", [])
            if 0 < float(v["price"]) <= pcap + 1e-8
        ]
        hedge = walk(pa, quantity)
    if hedge:
        payout = min(10, delivered(hedge[1], fd["r"]))
        return dict(
            status="PAIRED_COUNTERFACTUAL",
            net=payout - hedge[0] - cost - 0.02,
            net_before_reserve=payout - hedge[0] - cost,
            cap=pcap,
            kcost=cost,
            poly_cash=hedge[0],
            poly_delivered=payout,
            causal_latency=causal,
        )
    kb = snapshots[5.0]["books"]["kalshi"]["data"]["orderbook_fp"]
    sale = walk([(-float(p), float(q)) for p, q in kb[exit_side]], 10)
    recover = -sale[0] if sale else 0
    fee = kfee([(-p, q) for p, q in sale[1]]) if sale else 0
    return dict(
        status="HEDGE_REJECTED_UNWIND" if causal else "LATENCY_ABORT_UNWIND",
        net=recover - fee - cost - 0.02,
        cap=pcap,
        kcost=cost,
        recovered=recover,
        exit_fee=fee,
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("root", type=Path)
    p.add_argument("--registration", type=Path, required=True)
    a = p.parse_args()
    maps = {x["kalshi"]["ticker"]: x for x in json.loads((a.root / "mappings.json").read_text())}
    fees = json.loads((a.root / "fees.json").read_text())
    rows, pending = [], []
    reg = json.loads(a.registration.read_text())
    assert reg["code_sha256"] == hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    registration = reg["created"]
    terminal_path = a.root / "automatic_confirmation/results.jsonl"
    terminal = {}
    if terminal_path.exists():
        for line in terminal_path.read_text().splitlines():
            item = json.loads(line)
            terminal[(item["trigger_at"], item["direction"])] = item
    for folder in sorted((a.root / "automatic_confirmation").glob("*")):
        if not folder.is_dir() or not (folder / "trigger.json").exists():
            continue
        trigger = json.loads((folder / "trigger.json").read_text())
        ts = max(trigger[k]["received"] for k in ["yes", "no", "kalshi"])
        if ts < registration:
            continue
        for direction, spec in trigger["calculations"].items():
            if not spec or spec["net_per_contract"] <= 0.002:
                continue
            files = {d: folder / f"{direction}_{d}.json" for d in [0.5, 2.5, 5.0]}
            row = dict(trigger_at=ts, ticker=trigger["ticker"], direction=direction)
            if not all(f.exists() for f in files.values()):
                old = terminal.get((ts, direction))
                if old and old.get("status") == "UNRESOLVED_EXPOSURE":
                    row.update(
                        status="UNRESOLVED_EXPOSURE",
                        net=-sum(spec[k] for k in ["poly_cash", "kalshi_cash", "kalshi_fee"])
                        - 0.02,
                    )
                    rows.append(row)
                else:
                    pending.append(row)
                continue
            try:
                snaps = {d: json.loads(f.read_text()) for d, f in files.items()}
                pair = maps[trigger["ticker"]]
                fd = fees[trigger["market"]]["clob"]["fd"]
                row.update(
                    evaluate(
                        trigger,
                        direction,
                        snaps,
                        fd,
                        float(pair["market"].get("orderPriceMinTickSize", 0.01)),
                        float(pair["market"].get("secondsDelay", 0)),
                    )
                )
            except Exception as exc:
                row.update(
                    status="UNRESOLVED_EXPOSURE",
                    error=repr(exc),
                    net=-sum(spec[k] for k in ["poly_cash", "kalshi_cash", "kalshi_fee"]) - 0.02,
                )
            rows.append(row)
    result = dict(
        created=time.time(),
        protocol_sha256=hashlib.sha256(
            Path(__file__).with_name("PROTOCOL_CROSSVENUE_SEQUENTIAL.md").read_bytes()
        ).hexdigest(),
        rows=rows,
        pending=pending,
        net=sum(r["net"] for r in rows),
        qualification="counterfactual FOK/cross-venue-basis assumptions, not actual fills; incomplete audit folders are pending, not profitable zero losses",
    )
    (a.root / "sequential_evaluation.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
