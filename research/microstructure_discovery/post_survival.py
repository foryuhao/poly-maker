"""Exploratory public-count survival benchmark. Never a validated betting signal."""

import argparse
import bisect
import datetime as dt
import hashlib
import json
import statistics
import time
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("source", type=Path)
    p.add_argument("output", type=Path)
    a = p.parse_args()
    x = json.loads(a.source.read_text())
    rows = x["data"]
    timestamps = sorted(
        {dt.datetime.fromisoformat(r["createdAt"].replace("Z", "+00:00")).timestamp() for r in rows}
    )
    # Exclude Oct31: current window. Each historical day at UTC10 is a separate anchor.
    anchors = []
    for day in range(2, 31):
        t = dt.datetime(2026, 10, day, 10, tzinfo=dt.UTC).timestamp()
        before = bisect.bisect_right(timestamps, t) - 1
        age = t - timestamps[before] if before >= 0 else None
        future = bisect.bisect_left(timestamps, t + 6 * 3600) - bisect.bisect_right(timestamps, t)
        anchors.append(
            dict(
                utc=dt.datetime.fromtimestamp(t, dt.UTC).isoformat(),
                last_post_age_hours=age / 3600 if age is not None else None,
                future_6h_posts=future,
            )
        )
    quiet = [
        r for r in anchors if r["last_post_age_hours"] is not None and r["last_post_age_hours"] >= 3
    ]
    grouped = {}
    for key, arr in [("all_daily_utc10", anchors), ("already_quiet_3h", quiet)]:
        grouped[key] = dict(
            n=len(arr),
            zero_next6h=sum(r["future_6h_posts"] == 0 for r in arr),
            mean_next6h=statistics.mean(r["future_6h_posts"] for r in arr) if arr else None,
            counts=[r["future_6h_posts"] for r in arr],
        )
    a.output.mkdir(parents=True, exist_ok=True)
    # Store timestamp/import provenance only; original post contents are unnecessary.
    stripped = [
        {k: r.get(k) for k in ("id", "platformId", "createdAt", "importedAt")} for r in rows
    ]
    (a.output / "post_timestamps.json").write_text(json.dumps(stripped, indent=2))
    (a.output / "survival_exploration.json").write_text(
        json.dumps(
            dict(
                created=time.time(),
                source="https://xtracker.polymarket.com/api/users/elonmusk/posts",
                source_request=dict(
                    startDate="2026-10-01T00:00:00Z", endDate="2026-10-31T10:00:00Z"
                ),
                raw_sha256=hashlib.sha256(a.source.read_bytes()).hexdigest(),
                posts=len(rows),
                unique_times=len(timestamps),
                start=timestamps[0],
                end=timestamps[-1],
                benchmarks=grouped,
                anchors=anchors,
                qualification="Exploratory only: few daily anchors, hour/silence chosen using current context, not OOS calibrated. Posting counts depend on tracker filtering and historical revisions. No edge established.",
            ),
            indent=2,
        )
    )
    print(json.dumps(grouped, indent=2))


if __name__ == "__main__":
    main()
