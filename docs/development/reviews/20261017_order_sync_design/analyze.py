"""Read-only latency/resource evidence for the periodic sync service design."""

import csv
import hashlib
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path

OUT = Path(__file__).resolve().parent


def summarize(values):
    values = sorted(values)
    return {
        "n": len(values),
        "p50_ms": statistics.median(values),
        **{f"p{p}_ms": values[math.floor((len(values) - 1) * p / 100)]
           for p in (90, 95, 99)},
        "max_ms": max(values),
    }


def main():
    samples = defaultdict(list)
    counts = Counter()
    provenance = []
    sessions = set()
    for folder, cohort in (("20261017_1300_1500", "first24"),
                           ("20261017_1540_1640", "latest12")):
        data = json.loads((OUT.parent / folder / "analysis.json").read_text())
        for p in data["provenance"]:
            assert hashlib.sha256(Path(p["path"]).read_bytes()).hexdigest() == p["sha256"]
        provenance.extend(data["provenance"])
        for market in data["markets"]:
            assert market["session_id"] not in sessions
            sessions.add(market["session_id"])
            for line in Path(market["source"]).read_text().splitlines():
                row = json.loads(line)
                if row["kind"] != "sm_network_request":
                    continue
                q = row["data"]["payload"]
                endpoint = q["endpoint"]
                if endpoint.startswith("/data/order/"):
                    endpoint = "/data/order/:id"
                for group in (cohort, "all36"):
                    counts[group, q["method"], endpoint, q["outcome"]] += 1
                    if q["method"] == "GET" and q["outcome"] == "SUCCESS":
                        samples[group, endpoint].append(q["duration_ms"])
    assert len(sessions) == 36
    rows = [dict(cohort=c, endpoint=e, **summarize(v))
            for (c, e), v in sorted(samples.items())]
    with (OUT / "latencies.csv").open("w") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    result = {
        "scope": "Historical HTTP requests, not a benchmark of the proposed service",
        "quantiles": "sorted samples at floor((n-1)*p); p50 uses median",
        "markets": len(sessions),
        "nominal_market_seconds": 36 * 300,
        "limitations": [
            "Data API /v2/positions is not covered by sm_network_request instrumentation",
            "Individual HTTP timing excludes queueing and complete multi-page read cost",
            "Per-endpoint quantiles do not establish a whole-round percentile",
            "No API calls, trading process changes, or live performance measurements",
        ],
        "latencies": rows,
        "request_counts": [dict(cohort=c, method=m, endpoint=e, outcome=o, count=n)
                           for (c, m, e, o), n in sorted(counts.items())],
        "provenance": provenance,
    }
    (OUT / "evidence.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    for row in rows:
        if row["cohort"] == "all36":
            print(row)
    total = sum(n for (c, m, e, o), n in counts.items() if c == "all36" and m == "GET")
    print("Recorded GETs:", total, "nominal per second:", total / (36 * 300))


if __name__ == "__main__":
    main()
