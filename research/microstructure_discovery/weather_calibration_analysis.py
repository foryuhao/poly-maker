"""Outcome audit for the frozen V9 clock; calibration is not an own-fill backtest."""

import argparse
import collections
import hashlib
import json
import time
from decimal import Decimal
from pathlib import Path

import numpy as np


def no_payout(resolution, condition, chain=None):
    rows = [x for x in resolution["data"] if x["condition_id"] == condition]
    if len(rows) != 1 or rows[0]["status"] != "resolved":
        raise ValueError("native final payout not confirmed")
    row = rows[0]
    chain_value = None
    if chain is not None:
        assert chain["chain_id"] == 137 and chain["condition"] == condition
        assert chain["contract"].lower() == "0x4d97dcd97ec945f40cf65f87097ace5ea0476045"
        denominator = int(chain["denominator"])
        nums = [int(v) for v in chain["numerators"]]
        assert denominator > 0 and len(nums) == 2 and sum(nums) == denominator and min(nums) >= 0
        chain_value = Decimal(nums[1]) / denominator
    if row.get("payouts") is not None:
        values = [Decimal(str(x)) for x in row["payouts"]]
        if len(values) != 2 or sum(values) != 1000000 or min(values) < 0:
            raise ValueError("malformed payout vector")
        value = values[1] / 1000000
        if chain_value is not None and value != chain_value:
            raise ValueError("native and onchain payouts disagree")
        return value
    if chain_value is not None:
        return chain_value
    price = Decimal(str(row["price"]))
    if price != 0:
        raise ValueError("UMA scalar units require CTF verification; do not assume a probability")
    return Decimal(1)


def account(row):
    out = {k: row[k] for k in ["city", "date", "status"]}
    out.update(selected=row["status"] == "SELECTED", uncertain=False)
    if row["status"] == "NO_QUALIFYING_FILL":
        return dict(out, pnl=0.0, notional=0.0)
    if row["status"] != "SELECTED":
        return dict(out, uncertain=True, pnl=-10.02, notional=10.0)
    try:
        trade, market = row["trade"], row["market"]
        assert trade["condition_id"] == market["conditionId"]
        price = Decimal(str(trade["price"]))
        assert Decimal(".05") <= price <= Decimal(".25")
        fee = market["feeSchedule"]
        assert fee["exponent"] == 1 and 0 <= fee["rate"] <= 0.2
        rate = max(Decimal(".05"), Decimal(str(fee["rate"])))
        payout = no_payout(row["resolution"], trade["condition_id"], row.get("ctf"))
        tokens = 10 / price * (1 - rate * (1 - price))
        pnl = payout * tokens - 10 - Decimal(".02")
        return dict(
            out,
            pnl=float(pnl),
            notional=10.0,
            no_payout=float(payout),
            price=float(price),
            stress_rate=float(rate),
            tokens=float(tokens),
        )
    except (AssertionError, KeyError, TypeError, ValueError) as exc:
        return dict(out, uncertain=True, reason=repr(exc), pnl=-10.02, notional=10.0)


def clustered_interval(rows, dates, seed=20261031, alpha=0.05):
    sums = []
    for day in dates:
        group = [r for r in rows if r["date"] == day]
        sums.append([sum(r["pnl"] for r in group), sum(r["notional"] for r in group)])
    matrix = np.array(sums)
    effective = int((matrix[:, 1] > 0).sum())
    if effective < 20:
        return dict(
            lower=None,
            upper=None,
            effective_calendar_clusters=effective,
            reason="Too few exposed calendar-day clusters for a bootstrap confidence claim",
        )
    rng = np.random.default_rng(seed)
    draws = matrix[rng.integers(0, len(matrix), size=(10000, len(matrix)))].sum(axis=1)
    nonzero = draws[:, 1] > 0
    if not nonzero.any():
        return dict(lower=None, upper=None, zero_notional_draws=10000)
    values = draws[nonzero, 0] / draws[nonzero, 1]
    return dict(
        lower=float(np.quantile(values, alpha / 2)),
        upper=float(np.quantile(values, 1 - alpha / 2)),
        zero_notional_draws=int((~nonzero).sum()),
        samples=10000,
        seed=seed,
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("root", type=Path)
    a = p.parse_args()
    assert (a.root / "collection_complete.json").exists(), "do not inspect partial returns"
    reg = json.loads((a.root / "registration.json").read_text())
    code = Path(__file__).with_name("weather_calibration.py")
    protocol = Path(__file__).with_name("PROTOCOL_V9_WEATHER_FAVORITES.md")
    assert hashlib.sha256(code.read_bytes()).hexdigest() == reg["code_sha256"]
    assert hashlib.sha256(protocol.read_bytes()).hexdigest() == reg["protocol_sha256"]
    paths = sorted(a.root.glob("*/observation.json"))
    raw = []
    for file in paths:
        row = json.loads(file.read_text())
        ctf_path = file.with_name("ctf_finality.json")
        if ctf_path.exists():
            row["ctf"] = json.loads(ctf_path.read_text())
        raw.append(row)
    assert len(raw) == reg["expected_city_dates"] == 910
    assert len({(r["city"], r["date"]) for r in raw}) == 910
    event_counts = collections.Counter(r["event_id"] for r in raw if "event_id" in r)
    duplicate_events = {e: n for e, n in event_counts.items() if n > 1}
    rows = [account(r) for r in raw]
    selected = [r for r in rows if r["selected"]]
    dates = sorted({r["date"] for r in raw})
    total = sum(r["pnl"] for r in selected)
    notional = sum(r["notional"] for r in selected)
    cities = {}
    for city in reg["city_zones"]:
        sub = [r for r in selected if r["city"] == city]
        other = [r for r in selected if r["city"] != city]
        denominator = sum(r["notional"] for r in other)
        cities[city] = dict(
            selected=len(sub),
            pnl=sum(r["pnl"] for r in sub),
            leave_out_roi=sum(r["pnl"] for r in other) / denominator if denominator else None,
        )
    result = dict(
        analyzed_at=time.time(),
        coverage=dict(collections.Counter(r["status"] for r in raw)),
        selected=len(selected),
        selected_calendar_days=len({r["date"] for r in selected}),
        native_final_selected=sum(not r["uncertain"] for r in selected),
        chain_checked_selected=sum(
            computed["selected"] and not computed["uncertain"] and bool(source.get("ctf"))
            for source, computed in zip(raw, rows, strict=True)
        ),
        selected_pnl_lower_bound=total,
        selected_notional=notional,
        selected_roi_lower_bound=total / notional if notional else None,
        selected_day_cluster_ci=clustered_interval(selected, dates),
        all_grid_worst_missing_pnl=sum(r["pnl"] for r in rows),
        uncertain_city_dates=sum(r["uncertain"] for r in rows),
        duplicate_event_ids=duplicate_events,
        city_breakdown=cities,
        rows=rows,
        verified_tradable_edge=False,
        qualification="Historical public transaction calibration at a preselected clock. No own-entry price/depth/latency proof or historical fee contemporaneity proof. Selected CTF getters are checked where recorded; native scalar units alone are not trusted. Missing cases are charged worst loss in the grid bound. A positive calibration statistic alone never completes the trading-edge goal.",
    )
    (a.root / "analysis.json").write_text(json.dumps(result, indent=2))
    files = [code, protocol, Path(__file__), a.root / "registration.json"]
    files.extend(a.root.glob("*/observation.json"))
    files.extend(a.root.glob("*/requests.jsonl"))
    files.extend(a.root.glob("*/ctf_finality.json"))
    manifest = {}
    for file in files:
        with file.open("rb") as stream:
            manifest[str(file)] = hashlib.file_digest(stream, "sha256").hexdigest()
    (a.root / "analysis_manifest.json").write_text(json.dumps(manifest, indent=2))
    print(
        json.dumps(
            {k: v for k, v in result.items() if k not in ["rows", "city_breakdown"]}, indent=2
        )
    )


if __name__ == "__main__":
    main()
