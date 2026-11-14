"""Inventory reconstruction ambiguities without replacing the frozen cohort."""

import argparse
import json
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path

AMBIGUOUS = ("UNRECONSTRUCTABLE_SOURCE_ORDER", "BBO_DEPTH_INCONSISTENT")


def inspect(member):
    database = Path(member["input"]).with_name("simulation.sqlite3")
    with closing(sqlite3.connect(database.resolve().as_uri()+"?mode=ro", uri=True)) as db:
        raw = db.execute("SELECT payload FROM metadata WHERE name='diagnostic_archive'").fetchone()
        if raw is None:
            return {"market": member["market"], "status": "UNAVAILABLE", "reason": "NO_DIAGNOSTIC_WATERMARK"}
        metadata = json.loads(raw[0])
    quality = Counter()
    for row in metadata.get("research_quality", metadata.get("quality", [])):
        if row["reason"] in AMBIGUOUS:
            quality[row["reason"]] += row["count"]
    return {"market": member["market"], "status": "HAS_AMBIGUOUS_INTERVALS" if sum(quality.values()) else "NO_RECORDED_SOURCE_OR_BBO_AMBIGUITY",
        "recorded_ambiguities": quality, "diagnostic_input_seq": metadata["input_seq"],
        "source_database": str(database)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    root = args.directory.resolve()
    cohort = json.loads((root / "cohort25.json").read_text())["markets"]
    available = json.loads((root / "available_markets.json").read_text())
    rows = [inspect(m) for m in available if m.get("complete")]
    selected = {m["market"] for m in cohort}
    selected_rows = [m for m in rows if m["market"] in selected]
    result = {"status": "LIMITATION", "frozen_cohort_changed": False,
        "definition": "Counts of explicit source-time-order or BBO/depth ambiguity events over the recorded market. Zero is only no recorded ambiguity, not proof of fully reconstructable inputs.",
        "cohort25": selected_rows, "cohort25_status_counts": Counter(m["status"] for m in selected_rows),
        "available_complete_markets": len(rows), "available_status_counts": Counter(m["status"] for m in rows),
        "consequence": "Complete replayable recordings are not fully reconstructable at every instant. Retain the preselected cohort and unavailable interval guards; never count ambiguous evidence as a signal or relabel this cohort as 25 entirely clean windows.",
        "inventory": rows}
    (root / "cohort-quality.json").write_text(json.dumps(result, indent=2)+"\n")
    print(json.dumps({k:v for k,v in result.items() if k not in {"inventory", "cohort25"}}, indent=2))


if __name__ == "__main__":
    main()
