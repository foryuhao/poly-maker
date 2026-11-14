"""Expand the fixed-cohort price-cap sensitivity table into time curves.

Missing markets remain gaps. This is cumulative observed full-fill hypothetical
PnL by price cap, not a surface of individual transaction prices or actual fills.
"""

import hashlib
import importlib.util
import json
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path


def main():
    directory = Path("research_outputs/m1_isolated/studies/m1-paper-20261101T125004Z")
    table_path = directory/"signal-hold-010-50-upper-bands-latest.json"
    table = json.loads(table_path.read_text())
    state = json.loads((directory/"study.json").read_text())
    cohort = set(table["cohort_epochs"])
    state["sessions"] = [r for r in state["sessions"] if r["phase"] == "EVALUATION" and r["epoch"] in cohort]
    assert len(state["sessions"]) == len(cohort) == 192
    spec = importlib.util.spec_from_file_location("m1_hold_analysis", Path(__file__).with_name("m1_signal_hold_analysis.py"))
    analysis = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(analysis)
    plan = json.loads((directory/"paper-ooo10-applied-plan.json").read_text())
    times = list(range(min(cohort), max(cohort)+300, 300))
    times_text = [datetime.fromtimestamp(t, timezone(timedelta(hours=8))).strftime("%m/%d %H:%M") for t in times]
    series, summaries = [], []
    minimum = None
    for i in range(50, 100):
        cap = D(i)/100
        result = analysis.analyze(state, plan, min_price=D(".10"), max_price=cap)
        reference = next(r for r in table["rows"] if D(r["upper"]) == cap)
        assert D(result["summary"]["pnl"]) == D(reference["pnl_usdc"])
        assert result["summary"]["signals"] == reference["trades"]
        by_time = {r["epoch"]: r for r in result["markets"]}
        values = [float(D(by_time[t]["end"])) if t in by_time else None for t in times]
        negative = [(j, D(by_time[t]["end"])) for j, t in enumerate(times) if t in by_time and D(by_time[t]["end"]) < 0]
        low_index, low_value = min(((j, D(by_time[t]["end"])) for j, t in enumerate(times) if t in by_time), key=lambda x: x[1])
        intervals = []
        for j, _ in negative:
            if intervals and j == intervals[-1][1]+1:
                intervals[-1][1] = j
            else:
                intervals.append([j, j])
        info = {"cap": str(cap), "final_pnl": reference["pnl_usdc"], "trades": reference["trades"],
                "minimum_pnl": str(low_value), "minimum_time": times_text[low_index], "minimum_index": low_index,
                "negative_market_count": len(negative),
                "first_negative_time": times_text[negative[0][0]] if negative else None,
                "last_negative_time": times_text[negative[-1][0]] if negative else None,
                "last_negative_index": negative[-1][0] if negative else None,
                "negative_intervals": [{"from": times_text[a], "through": times_text[b], "from_index": a, "through_index": b} for a,b in intervals]}
        if minimum is None or low_value < D(minimum["pnl"]):
            minimum = {"cap": str(cap), "pnl": str(low_value), "time": times_text[low_index], "time_index": low_index}
        series.append(values)
        summaries.append(info)
    last_negative_index = max(r["last_negative_index"] for r in summaries if r["last_negative_index"] is not None)
    output = {"as_of_utc": datetime.now(UTC).isoformat(), "table_as_of_utc": table["as_of_utc"],
              "cohort_markets": len(cohort), "minimum_price": "0.10", "caps": [i/100 for i in range(50,100)],
              "times": times, "time_labels": times_text, "values": series, "summaries": summaries,
              "global_minimum": minimum, "last_observed_negative_time": times_text[last_negative_index],
              "all_final_positive": all(D(r["final_pnl"]) > 0 for r in summaries),
              "ever_negative_caps": sum(r["negative_market_count"]>0 for r in summaries),
              "gap_slots": sum(t not in cohort for t in times),
              "source_table_sha256": hashlib.sha256(table_path.read_bytes()).hexdigest(),
              "scope": "CUMULATIVE_OBSERVED_SETTLED_HYPOTHETICAL_PNL_BY_PRICE_CAP; MISSING_MARKETS_ARE_GAPS"}
    (directory/"price-time-pnl-surface.json").write_text(json.dumps(output, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps({k: output[k] for k in ("cohort_markets", "global_minimum", "last_observed_negative_time", "all_final_positive", "ever_negative_caps", "gap_slots")}, ensure_ascii=False, indent=2))
    for cap in ("0.5", "0.6", "0.66", "0.7", "0.8", "0.9", "0.99"):
        print(json.dumps(next(r for r in summaries if r["cap"]==cap), ensure_ascii=False))


if __name__ == "__main__":
    main()
