"""Economic counterparties to YES takers; not an executable NO-ask backtest."""

import argparse
import collections
import hashlib
import json
import time
from decimal import Decimal
from pathlib import Path

from weather_calibration import array
from weather_calibration_analysis import clustered_interval, no_payout


def account(row):
    result = {k: row[k] for k in ["city", "date", "status"]}
    result.update(selected=row["status"] == "SELECTED", uncertain=False)
    if row["status"] == "NO_QUALIFYING_FILL":
        return dict(result, pnl=0.0, notional=0.0)
    if row["status"] != "SELECTED":
        return dict(result, pnl=-2.02, notional=2.0, uncertain=True)
    try:
        trade, market = row["trade"], row["market"]
        assert trade["condition_id"] == market["conditionId"]
        assert trade["token_id"] == array(market["clobTokenIds"])[0]
        assert array(market["outcomes"]) == ["Yes", "No"]
        fee = market["feeSchedule"]
        assert fee.get("takerOnly") is True or fee.get("rate") == 0
        pyes = Decimal(str(trade["price"]))
        assert Decimal(".75") <= pyes <= Decimal(".95")
        price = 1 - pyes
        quantity = 2 / price
        assert Decimal(str(trade["size"])) >= quantity
        payout = no_payout(row["resolution"], trade["condition_id"], row.get("ctf"))
        return dict(
            result,
            notional=2.0,
            pnl=float(quantity * payout - 2 - Decimal(".02")),
            no_payout=float(payout),
            economic_no_cost=float(price),
            quantity=float(quantity),
        )
    except (AssertionError, KeyError, ValueError, TypeError) as exc:
        return dict(result, pnl=-2.02, notional=2.0, uncertain=True, reason=repr(exc))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("root", type=Path)
    a = p.parse_args()
    assert (a.root / "collection_complete.json").exists()
    reg = json.loads((a.root / "registration.json").read_text())
    code = Path(__file__).with_name("weather_counterparty.py")
    protocol = Path(__file__).with_name("PROTOCOL_V10_WEATHER_COUNTERPARTY.md")
    assert hashlib.sha256(code.read_bytes()).hexdigest() == reg["code_sha256"]
    assert hashlib.sha256(protocol.read_bytes()).hexdigest() == reg["protocol_sha256"]
    paths = sorted(a.root.glob("*/observation.json"))
    raw = []
    for file in paths:
        r = json.loads(file.read_text())
        chain = file.with_name("ctf_finality.json")
        if chain.exists():
            r["ctf"] = json.loads(chain.read_text())
        raw.append(r)
    assert len(raw) == 910 and len({(r["city"], r["date"]) for r in raw}) == 910
    rows = [account(r) for r in raw]
    selected = [r for r in rows if r["selected"]]
    notional = sum(r["notional"] for r in selected)
    total = sum(r["pnl"] for r in selected)
    cities = {}
    for city in sorted({r["city"] for r in rows}):
        own = [r for r in selected if r["city"] == city]
        other = [r for r in selected if r["city"] != city]
        denom = sum(r["notional"] for r in other)
        cities[city] = dict(
            selected=len(own),
            pnl=sum(r["pnl"] for r in own),
            leave_out_roi=sum(r["pnl"] for r in other) / denom if denom else None,
        )
    result = dict(
        analyzed_at=time.time(),
        coverage=dict(collections.Counter(r["status"] for r in raw)),
        selected=len(selected),
        selected_days=len({r["date"] for r in selected}),
        selected_confirmed=sum(not r["uncertain"] for r in selected),
        economic_counterparty_pnl_lower_bound=total,
        notional=notional,
        roi_lower_bound=total / notional if notional else None,
        day_cluster_99pct_ci=clustered_interval(
            selected, sorted({r["date"] for r in raw}), alpha=0.01
        ),
        all_grid_worst_missing_pnl=sum(r["pnl"] for r in rows),
        city_breakdown=cities,
        rows=rows,
        verified_tradable_edge=False,
        qualification="Aggregate opposing economic exposures to real YES takers. Neither our queue fills nor NO taker ask prices. Same source parents as V9; no pooling as independent evidence. Sparse clusters cannot support an inferential confidence interval. Future order-book/queue feasibility required for any edge claim.",
    )
    (a.root / "analysis.json").write_text(json.dumps(result, indent=2))
    files = (
        [
            code,
            protocol,
            Path(__file__),
            Path(__file__).with_name("weather_calibration_analysis.py"),
            a.root / "registration.json",
        ]
        + paths
        + list(a.root.glob("*/ctf_finality.json"))
    )
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
