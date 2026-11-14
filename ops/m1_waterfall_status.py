"""Refresh read-only M1 status and existing full-fill waterfall data.

Never imports exchange/strategy modules or modifies the running study. Both
ledgers use one immutable study.json read; low-price orders are re-sized before
the per-market budget is replayed. Missing markets retain null PnL.
"""

import argparse
import copy
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path

from m1_signal_hold_analysis import analyze

D = Decimal


def resolved(row):
    resolution = row.get("settlement", {}).get("resolution", {})
    return bool(row.get("complete_window") and row.get("audit_valid")
                and row.get("paper") and resolution.get("closed") is True
                and resolution.get("resolution_status") == "resolved")


def verify(rows, result, budget, bounds=None):
    """Reconcile independently to raw candidates and official winners."""
    cost = payout = D(0)
    count = 0
    for row in rows:
        if not resolved(row):
            continue
        spent = D(0)
        for signal in row["paper"]["signals"]:
            price = D(signal["price"])
            if bounds and not bounds[0] <= price <= bounds[1]:
                continue
            quantity = (D(signal["size"]) if bounds is None else
                        (budget / price).quantize(D(".01"), rounding=ROUND_FLOOR))
            amount = price * quantity
            assert quantity >= 5 and 0 < amount <= budget
            if spent + amount > 20:
                continue
            spent += amount
            cost += amount
            if signal["outcome"] == row["settlement"]["resolution"]["winner"]:
                payout += quantity
            count += 1
    summary = result["summary"]
    assert count == summary["signals"]
    assert cost == D(summary["cost"])
    assert payout == D(summary["payout"])
    assert payout - cost == D(summary["pnl"])
    assert sum((D(r["pnl"]) for r in result["markets"]
                if r["pnl"] is not None), D(0)) == payout - cost
    assert len({(t["epoch"], t["episode_id"]) for t in result["trades"]}) == count


def summarize(result):
    valid = [r for r in result["markets"] if r["pnl"] is not None]
    peak = drawdown = D(0)
    for row in valid:
        peak = max(peak, D(row["end"]))
        drawdown = max(drawdown, peak - D(row["end"]))
    result["summary"].update(max_drawdown_usdc=str(drawdown),
                             signal_markets=sum(r["signals"] > 0 for r in valid))
    result["valid_market_blocks"] = []
    for start in range(0, len(valid), 100):
        block = valid[start:start + 100]
        result["valid_market_blocks"].append({
            "start_valid_market": start + 1,
            "end_valid_market": start + len(block),
            "markets": len(block), "complete_100": len(block) == 100,
            "signals": sum(r["signals"] for r in block),
            "pnl": str(sum((D(r["pnl"]) for r in block), D(0))),
        })


def bind_chart(path, data_id, result):
    text = path.read_text()
    data = {k: v for k, v in result.items() if k != "trades"}
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    pattern = (r'(<script type="application/json" id="' + re.escape(data_id)
               + r'">).*?(</script>)')
    text, count = re.subn(pattern, lambda m: m[1] + payload + m[2],
                          text, count=1, flags=re.S)
    assert count == 1 and len(text.encode()) < 1_000_000
    path.write_text(text)


def refresh(directory, visualization_dir):
    raw = (directory / "study.json").read_bytes()
    state = json.loads(raw)
    plan = json.loads((directory / "paper-ooo10-applied-plan.json").read_text())
    rows = sorted((r for r in state["sessions"] if r["phase"] == "EVALUATION"),
                  key=lambda r: r["epoch"])
    assert len({r["epoch"] for r in rows}) == len(rows)
    full = analyze(state, plan)
    verify(rows, full, D(5))

    resized = copy.deepcopy(state)
    for row in resized["sessions"]:
        for signal in row.get("paper", {}).get("signals", []):
            price = D(signal["price"])
            if D(".01") <= price <= D(".19"):
                signal["size"] = str((D(1) / price).quantize(
                    D(".01"), rounding=ROUND_FLOOR))
    low = analyze(resized, plan, min_price=D(".01"), max_price=D(".19"))
    low.update(per_order_budget_usdc="1", as_of_utc=full["as_of_utc"],
               scope="RECORDED_LOW_PRICE_M1_SIGNALS_ONE_USDC_FULL_FILL_HOLD_TO_OFFICIAL_SETTLEMENT")
    low["reclassified"].update(
        original_approved_orders=low["reclassified"]["orders"],
        orders=low["reclassified"]["included"],
        distinct_markets=len({t["epoch"] for t in low["trades"] if t["reclassified"]}))
    verify(rows, low, D(1), (D(".01"), D(".19")))
    digest = hashlib.sha256(raw).hexdigest()
    for result in (full, low):
        result["source_study_sha256"] = digest
        summarize(result)

    queue = [r for r in rows if resolved(r)]
    status = {
        "as_of_utc": full["as_of_utc"],
        "as_of_bjt": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M"),
        "source_study_sha256": digest,
        "analysis_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "status": state["status"], "recorded": len(rows),
        "target": state["evaluation_target"], "active_market": state.get("active_market"),
        "calibration": sum(r["phase"] == "CALIBRATION" for r in state["sessions"]),
        "post_attempts": sum(r.get("paper", {}).get("post_attempts", 0) for r in rows),
        "post_markets": sum(r.get("paper", {}).get("post_attempts", 0) > 0 for r in rows),
        "filled_orders": sum(r.get("paper", {}).get("filled_orders", 0) for r in rows),
        "uncertain_orders": sum(r.get("paper", {}).get("uncertain_orders", 0) for r in rows),
        "queue_complete_settled_markets": len(queue),
        "queue_lower": str(sum((D(r["settlement"]["pnl_lower_usdc"]) for r in queue), D(0))),
        "queue_upper": str(sum((D(r["settlement"]["pnl_upper_usdc"]) for r in queue), D(0))),
        "full": full["summary"], "low_price_one_usdc": low["summary"],
        "low_price_blocks": low["valid_market_blocks"],
    }
    (directory / "status-waterfall-source.json").write_bytes(raw)
    for filename, result in (("signal-hold-waterfall-latest.json", full),
                             ("low-price-one-usdc-waterfall-latest.json", low),
                             ("status-waterfall-latest.json", status)):
        (directory / filename).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    bind_chart(visualization_dir / "m1-full-fill-session-waterfall.html",
               "m1-session-waterfall-data", full)
    bind_chart(visualization_dir / "m1-low-price-one-usdc-waterfall.html",
               "m1-low-price-waterfall-data", low)
    return status


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study_dir", type=Path)
    parser.add_argument("visualization_dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(refresh(args.study_dir, args.visualization_dir), ensure_ascii=False, indent=2))
