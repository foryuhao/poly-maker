"""Independent replay: compare reconstructed best prices with explicit stream BBO."""

import collections
import json
import sys
from pathlib import Path

from study import Book


def audit(root):
    root = Path(root)
    books = collections.defaultdict(Book)
    counts = collections.Counter()
    last_source = {}
    generation = None
    examples = []
    dirty = {}
    bad_intervals = []
    for lineno, line in enumerate((root / "raw.jsonl").open(), 1):
        r = json.loads(line)
        if r.get("generation") != generation:
            books.clear()
            last_source.clear()
            generation = r.get("generation")
        e = r.get("event", {})
        kind = e.get("event_type")
        if kind == "book":
            a = e["asset_id"]
            books[a].update(e)
            if a in dirty:
                bad_intervals.append(dict(asset=a, start=dirty.pop(a), end=r["received"]))
        elif kind == "price_change":
            for c in e.get("price_changes", []):
                a = c["asset_id"]
                ts = float(e.get("timestamp", 0))
                if ts < last_source.get(a, 0):
                    counts["source_timestamp_regressions"] += 1
                last_source[a] = ts
                books[a].update(dict(c, event_type=kind))
            # Compare only after the complete atomic message has been applied.
            for a, c in {c["asset_id"]: c for c in e.get("price_changes", [])}.items():
                q = books[a].quote()
                if (
                    q is not None
                    and c.get("best_bid") is not None
                    and c.get("best_ask") is not None
                ):
                    counts["bbo_checks"] += 1
                    if (
                        abs(q["bid"] - float(c["best_bid"])) > 1e-8
                        or abs(q["ask"] - float(c["best_ask"])) > 1e-8
                    ):
                        counts["bbo_mismatches"] += 1
                        dirty.setdefault(a, r["received"])

                        if len(examples) < 5:
                            examples.append(
                                dict(raw_line=lineno, asset=a, reconstructed=q, reported=c)
                            )
                    else:
                        if a in dirty:
                            bad_intervals.append(
                                dict(asset=a, start=dirty.pop(a), end=r["received"])
                            )
    for a, start in dirty.items():
        bad_intervals.append(dict(asset=a, start=start, end=r["received"]))
    result = dict(
        bad_intervals=bad_intervals,
        counts=counts,
        examples=examples,
        note="Checks compare each price_change against its supplied BBO when available; not an exchange sequence/checksum guarantee.",
    )
    (root / "replay_audit.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(dict(counts=counts, bad_intervals=len(bad_intervals)), indent=2))


if __name__ == "__main__":
    audit(sys.argv[1])
