"""Explain bounded-cohort signal attrition from committed campaign audit only."""

import argparse
import json
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path

TARGETS = ("M7", "M9", "M10")


def diagnose(root, name):
    members = json.loads((root / "cohort25.json").read_text())["markets"]
    results = {s: {"starts": 0, "published_candidates": 0, "confirmed_candidate_facts": 0,
                   "ended": 0, "end_reason_classes": Counter(), "end_reasons": Counter(),
                   "ended_before_candidate_fact": 0, "ended_after_candidate_fact": 0,
                   "before_candidate_end_reasons": Counter(), "after_candidate_end_reasons": Counter(),
                   "local_rounds": Counter()} for s in TARGETS}
    counted = []
    for member in members:
        market = root / name / member["market"]
        if not (market / "replay-state.json").exists():
            continue
        counted.append(member["market"])
        confirmed_roots = set()
        with closing(sqlite3.connect((market / "simulation.sqlite3").resolve().as_uri()+"?mode=ro",
                                     uri=True)) as db:
            for raw, in db.execute("SELECT payload FROM audit WHERE kind='RESEARCH_EVIDENCE' ORDER BY seq"):
                row = json.loads(raw)
                event, p = row["event"], row["payload"]
                strategy = p.get("strategy")
                if event in {"CAMPAIGN_STARTED", "CAMPAIGN_ENDED"} and strategy in results:
                    a = results[strategy]
                    if event == "CAMPAIGN_STARTED":
                        a["starts"] += 1
                    else:
                        a["ended"] += 1
                        a["end_reason_classes"][p["reason_class"]] += 1
                        a["end_reasons"][p["reason"]] += 1
                        prefix = ("after" if (strategy, p["token"], p["root_id"]) in confirmed_roots
                                  else "before")
                        a["ended_"+prefix+"_candidate_fact"] += 1
                        a[prefix+"_candidate_end_reasons"][p["reason"]] += 1
                elif event == "FACT_CONFIRMED":
                    if p["kind"].startswith("CANDIDATE_") and p["values"].get("strategy") in results:
                        confirmed_roots.add((p["values"]["strategy"], p["token"], p["values"]["root_id"]))
                    if p["kind"] == "ROUND":
                        v = p["values"]
                        strategy = v["scope"].split(":", 1)[0]
                        if strategy in results:
                            bucket = ("stable" if v["stable"] else "qualified_unstable"
                                      if v["qualified"] else "unqualified")
                            results[strategy]["local_rounds"][bucket] += 1
            for strategy, n in db.execute("SELECT json_extract(payload,'$.strategy'),count(*) "
                    "FROM audit WHERE kind='SIGNAL' AND json_extract(payload,'$.event')='CANDIDATE' GROUP BY 1"):
                if strategy in results:
                    results[strategy]["published_candidates"] += n
            # Candidate fact strategy lives inside values, unlike campaign rows.
            for strategy in TARGETS:
                results[strategy]["confirmed_candidate_facts"] += db.execute(
                    "SELECT count(*) FROM audit WHERE kind='RESEARCH_EVIDENCE' "
                    "AND json_extract(payload,'$.event')='FACT_CONFIRMED' "
                    "AND json_extract(payload,'$.payload.kind')=?", ("CANDIDATE_"+strategy,)).fetchone()[0]
    for a in results.values():
        assert a["published_candidates"] <= a["confirmed_candidate_facts"] <= a["starts"]
        assert a["ended"] <= a["starts"]
        assert a["ended_before_candidate_fact"]+a["ended_after_candidate_fact"] == a["ended"]
        assert a["ended_after_candidate_fact"] <= a["confirmed_candidate_facts"]
    return {"status": "COMPLETE" if len(counted) == 25 else "PARTIAL", "run": name,
            "counted_markets": len(counted), "strategies": results,
            "interpretation": "Campaign attempts and end reasons, not independent signals or PnL attribution. Ends before and after candidate fact confirmation are separate. Candidate fact is not necessarily published/admitted. UNKNOWN is an audit class, not a numerical-threshold rejection or a count of missing signals.",
            "selection_uses_pnl": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--run", default="next25-v3b")
    args = parser.parse_args()
    directory = args.directory.resolve()
    result = diagnose(directory, args.run)
    (directory / ("frequency-diagnostics-"+args.run+".json")).write_text(
        json.dumps(result, indent=2)+"\n")
    print(json.dumps(result, indent=2))
