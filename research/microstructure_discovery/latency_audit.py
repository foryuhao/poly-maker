"""Read-only execution-latency evidence; never interpret RTT/2 as measured arrival."""

import argparse
import collections
import glob
import hashlib
import json
import os
import statistics
import time
from pathlib import Path


def quantile(xs, p):
    ys = sorted(xs)
    return ys[min(len(ys) - 1, int((len(ys) - 1) * p))] if ys else None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--sessions", type=int, default=24)
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=False)
    paths = sorted(glob.glob("journal/*.jsonl"), key=os.path.getmtime, reverse=True)[: a.sessions]
    groups = collections.defaultdict(list)
    rows = []
    counts = collections.Counter()
    for path in paths:
        with open(path) as stream:
            for lineno, line in enumerate(stream, 1):
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    counts["partial_journal_line"] += 1
                    continue
                if r.get("kind") != "sm_network_request":
                    continue
                e = r["data"].get("payload", {})
                method = e.get("method")
                endpoint = e.get("endpoint")
                ms = e.get("duration_ms")
                if method not in ("POST", "DELETE") or ms is None:
                    continue
                groups[(method, endpoint, e.get("outcome"))].append(float(ms))
                rows.append(
                    dict(
                        source=path,
                        line=lineno,
                        record_sha256=hashlib.sha256(line.encode()).hexdigest(),
                        method=method,
                        endpoint=endpoint,
                        duration_ms=ms,
                        outcome=e.get("outcome"),
                        status=e.get("http_status"),
                        started_wall=e.get("started_wall"),
                        completed_wall=e.get("completed_wall"),
                    )
                )
    summaries = []
    for (method, endpoint, outcome), xs in groups.items():
        summaries.append(
            dict(
                method=method,
                endpoint=endpoint,
                outcome=outcome,
                n=len(xs),
                mean=statistics.mean(xs),
                p50=quantile(xs, 0.5),
                p90=quantile(xs, 0.9),
                p99=quantile(xs, 0.99),
                maximum=max(xs),
            )
        )
    (a.output / "request_rows.json").write_text(json.dumps(rows, indent=2))
    (a.output / "summary.json").write_text(
        json.dumps(
            dict(
                captured=time.time(),
                sessions=paths,
                requests=len(rows),
                summary=summaries,
                checks=counts,
                qualification="Measured request-to-response RTT on existing infrastructure, not one-way order-arrival or cancellation effect. Submission processing, venue delay, feed lag and failures must be modeled separately. Previous strategy supplies execution infrastructure evidence only, not alpha selection.",
            ),
            indent=2,
        )
    )
    print(json.dumps(summaries, indent=2))


if __name__ == "__main__":
    main()
