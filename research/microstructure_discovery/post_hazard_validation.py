"""Fixed independent-month survival replication with coverage checks."""

import argparse
import asyncio
import bisect
import calendar
import datetime as dt
import hashlib
import json
import math
from pathlib import Path

import httpx


def upper_bound(k, n, alpha=0.025):
    if n == 0 or k == n:
        return 1.0
    lo, hi = k / n, 1.0
    for _ in range(80):
        p = (lo + hi) / 2
        cdf = sum(
            math.exp(
                math.lgamma(n + 1)
                - math.lgamma(j + 1)
                - math.lgamma(n - j + 1)
                + j * math.log(p)
                + (n - j) * math.log1p(-p)
            )
            for j in range(k + 1)
        )
        if cdf > alpha:
            lo = p
        else:
            hi = p
    return hi


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("root", type=Path)
    a = p.parse_args()
    coverage = []
    async with httpx.AsyncClient(timeout=30) as c:
        for month in range(1, 9):
            source = json.loads((a.root / f"month_{month:02}.json").read_text())
            sets = []
            for start, end in [
                (f"2026-{month:02}-01T00:00:00Z", f"2026-{month:02}-16T00:00:00Z"),
                (f"2026-{month:02}-16T00:00:00Z", f"2026-{month + 1:02}-01T00:00:00Z"),
            ]:
                r = await c.get(
                    "https://xtracker.polymarket.com/api/users/elonmusk/posts",
                    params={"startDate": start, "endDate": end},
                )
                r.raise_for_status()
                rows = r.json()["data"]
                sets.append(set(t["id"] for t in rows))
                (a.root / f"chunk_{month:02}_{len(sets)}.json").write_text(
                    json.dumps(
                        dict(
                            start=start,
                            end=end,
                            sha256=hashlib.sha256(r.content).hexdigest(),
                            ids=sorted(sets[-1]),
                        )
                    )
                )
            whole = {t["id"] for t in source["rows"]}
            coverage.append(
                dict(
                    month=month,
                    whole=len(whole),
                    chunked=len(sets[0] | sets[1]),
                    exact_match=whole == sets[0] | sets[1],
                )
            )
    groups = []
    for hour, horizon in [(13, 3), (10, 6)]:
        anchors = []
        for month in range(1, 9):
            if not coverage[month - 1]["exact_match"]:
                continue
            rs = json.loads((a.root / f"month_{month:02}.json").read_text())["rows"]
            ts = sorted(
                dt.datetime.fromisoformat(r["createdAt"].replace("Z", "+00:00")).timestamp()
                for r in {r["id"]: r for r in rs}.values()
            )
            for day in range(2, calendar.monthrange(2026, month)[1] + 1):
                anchor = dt.datetime(2026, month, day, hour, tzinfo=dt.UTC)
                t = anchor.timestamp()
                index = bisect.bisect_right(ts, t)
                age = t - ts[index - 1] if index else None
                if age is None or age < 3 * 3600:
                    continue
                count = bisect.bisect_left(ts, t + horizon * 3600) - index
                anchors.append(
                    dict(
                        utc=anchor.isoformat(),
                        month=month,
                        weekday=anchor.weekday(),
                        age_hours=age / 3600,
                        count=count,
                    )
                )
        n = len(anchors)
        k = sum(r["count"] == 0 for r in anchors)
        monthly = []
        for month in range(1, 9):
            xs = [r for r in anchors if r["month"] == month]
            monthly.append(dict(month=month, n=len(xs), zero=sum(r["count"] == 0 for r in xs)))
        groups.append(
            dict(
                hour=hour,
                horizon=horizon,
                n=n,
                zero=k,
                zero_probability=k / n if n else None,
                upper_97_5_iid=upper_bound(k, n),
                months=monthly,
                anchors=anchors,
            )
        )
    result = dict(
        coverage=coverage,
        groups=groups,
        qualifications=[
            "IID bounds are sensitivity only; daily dependence/regime changes remain",
            "chunk consistency detects truncation, not upstream historical omissions",
            "no current event edge established solely from historical calibrated rates",
        ],
    )
    (a.root / "validation.json").write_text(json.dumps(result, indent=2))
    print(
        json.dumps(
            {
                "coverage": coverage,
                "groups": [{k: v for k, v in g.items() if k != "anchors"} for g in groups],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
