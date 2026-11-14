"""Retrospective M7 sequence ceiling; never used to generate trading candidates."""

import argparse
import gzip
import json
import sqlite3
from bisect import bisect_left
from contextlib import closing
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    root = args.directory.resolve()
    members = json.loads((root / "cohort25.json").read_text())["markets"]
    results = []
    for member in members:
        directory = Path(member["input"]).parent
        gaps = {}
        with gzip.open(directory / "unavailable-diagnostics.jsonl.gz", "rt") as stream:
            for line in stream:
                p = json.loads(line)["payload"]
                if p.get("event") == "QUALITY_UNAVAILABLE":
                    gaps.setdefault(p["payload"]["token"], []).append(p["available"])
        with closing(sqlite3.connect((directory / "simulation.sqlite3").as_uri()+"?mode=ro", uri=True)) as db:
            rows = db.execute("SELECT payload FROM audit WHERE kind='RESEARCH_EVIDENCE' AND json_extract(payload,'$.event')='FACT_CONFIRMED' AND json_extract(payload,'$.payload.kind')='AP_BUY'").fetchall()
        attacks = [json.loads(p)["payload"] for p, in rows]
        per_window = {}
        for window in (0, .25, .5):
            possible = []
            for first in attacks:
                times = sorted(gaps.get(first["token"], []))
                index = bisect_left(times, first["confirmed"])
                end = min(first["confirmed"]+20, times[index] if index < len(times) else float("inf"))
                seconds = [second for second in attacks if second["token"] == first["token"]
                           and second["confirmed"] > first["confirmed"]
                           and second["values"]["start_key"][0] >= first["confirmed"]+window
                           and second["confirmed"] < end]
                if seconds:
                    possible.append({"root": first["id"], "confirmed": first["confirmed"],
                                     "next_attack": seconds[0]["id"],
                                     "next_attack_start": seconds[0]["values"]["start_key"][0],
                                     "next_attack_confirmed": seconds[0]["confirmed"],
                                     "first_quality_gap_or_campaign_end": end})
            per_window[str(window)] = possible
        results.append({"market": member["market"], "strong_buy_facts": len(attacks),
                        "possible_root_pairs": per_window})
    result = {
        "scope": "Upper bound for M7 with unchanged shared AP detector and recorded quality invalidations. Ignores RF existence, recovery, pause-volume gates, consumed rounds, and campaign ownership; not achievable signal counts.",
        "markets": results,
        "ceilings": {w: sum(len(r["possible_root_pairs"][w]) for r in results) for w in ("0", "0.25", "0.5")},
        "lookahead": "Retrospective diagnostic only; never admitted to the signal pipeline",
    }
    (root / "m7-sequence-ceiling.json").write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps(result["ceilings"]))


if __name__ == "__main__":
    main()
