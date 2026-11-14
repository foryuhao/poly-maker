"""Compare optimized legacy-rule replay with the original committed audit/state."""

import argparse
import json
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path

from m510_audit_equivalence import compare as compare_audit

ECONOMIC_KINDS = (
    "INTENT", "SUBMIT", "LIVE", "REJECT", "MODELLED_FILL", "CANCEL_REQUEST",
    "CANCEL_EFFECTIVE", "RELEASE", "ORDER_UNCERTAIN", "UNKNOWN_TAIL", "SETTLE",
    "RESEARCH_FILL_ATTRIBUTION",
)


def read(path):
    return json.loads(path.read_text())


def query(path, sql, params=()):
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        return db.execute(sql, params).fetchall()


def state(path):
    return json.loads(query(path, "SELECT payload FROM snapshots WHERE name='state'")[0][0])


def economic_audit(path):
    sql = "SELECT input_seq,kind,payload FROM audit WHERE kind IN (" + ",".join("?" for _ in ECONOMIC_KINDS) + ") ORDER BY seq"
    return [(i, k, json.loads(p)) for i, k, p in query(path, sql, ECONOMIC_KINDS)]


def candidates(path):
    result = []
    for seq, raw in query(path, "SELECT input_seq,payload FROM audit WHERE kind='SIGNAL' AND json_extract(payload,'$.event')='CANDIDATE' ORDER BY seq"):
        p = json.loads(raw)
        v = p["payload"]
        result.append((seq, p["strategy"], *(v.get(k) for k in (
            "id", "generation", "token", "outcome", "anchor_price", "confirmed", "deadline"))))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--run", default="memory288-v3b")
    args = parser.parse_args()
    root = args.directory.resolve()
    cohort = read(root / "cohort288.json")
    run = root / args.run
    checks = []
    for member in cohort["markets"]:
        database = run / member["market"] / "simulation.sqlite3"
        if not database.with_name("replay-state.json").exists():
            continue
        source = Path(member["input"]).with_name("simulation.sqlite3")
        old, new = state(source), state(database)
        a, b = economic_audit(source), economic_audit(database)
        audit_comparison = compare_audit(source, database)
        checks.append({
            "market": member["market"], "source_run": member["source_run"],
            "input_seq": new["input_seq"], "broker_states_identical": old["brokers"] == new["brokers"],
            "marks_identical": old["marks"] == new["marks"],
            "coverage_identical": old["coverage"] == new["coverage"],
            "settlement_identical": old["resolution"] == new["resolution"],
            "economic_audit_identical": a == b,
            "economic_audit_rows": len(b), "first_economic_difference": next(
                (i for i, pair in enumerate(zip(a, b, strict=False)) if pair[0] != pair[1]),
                min(len(a), len(b)) if len(a) != len(b) else None),
            "candidate_audit_identical": candidates(source) == candidates(database),
            "full_audit_semantics_equivalent": audit_comparison[
                "equivalent_after_explicit_annotation_normalization"],
            "full_audit_comparison": audit_comparison,
        })
    progress = read(run / "progress.json") if (run / "progress.json").exists() else {"markets": []}
    resources = progress["markets"]
    matched = all(all(c[k] for k in (
        "broker_states_identical", "marks_identical", "coverage_identical", "settlement_identical",
        "economic_audit_identical", "candidate_audit_identical",
        "full_audit_semantics_equivalent")) for c in checks)
    rusage_peak = max((r["peak_rss_mib"] for r in resources), default=None)
    sampled_peak = max((r["rss_mib"] for r in resources), default=None)
    max_peak = max(rusage_peak, sampled_peak) if resources else None
    max_graphs = max((r["retained_research_graphs"] for r in resources), default=None)
    terminal = read(run / "completion.json") if (run / "completion.json").exists() else {}
    plan = read(run / "plan.json") if (run / "plan.json").exists() else {}
    proof = read(run / "import-proof.json") if (run / "import-proof.json").exists() else {}
    terminal_consistent = (not terminal or terminal.get("run_identity") == plan.get("run_identity"))
    expected_order = [m["market"] for m in cohort["markets"]]
    observed_order = [r["market"] for r in resources]
    ordered = observed_order == expected_order[:len(observed_order)]
    gc_free = bool(resources) and all(r.get("retirement_gc_seconds") == 0
                                     for r in resources)
    complete = (len(checks) == len(cohort["markets"]) == len(resources) == 288
                and terminal.get("status") == "PASS" and terminal.get("markets") == 288)
    result = {
        "status": ("FAIL" if not terminal_consistent or not ordered
                   or terminal and terminal.get("status") != "PASS" else
                   "PASS" if complete and matched and max_peak <= 512 and max_graphs == 0
                   else "FAIL" if not matched or (max_peak is not None and max_peak > 512)
                   or (max_graphs is not None and max_graphs > 0)
                   else "PARTIAL"),
        "run": args.run, "terminal_identity_consistent": terminal_consistent,
        "frozen_cohort_order_preserved": ordered,
        "loaded_source_root": proof.get("expected_source_root"),
        "retirement_mode": "NOT_YET_OBSERVED" if not resources else
                           "GC_FREE_DURABLE_RETIREMENT" if gc_free else
                           "EARLIER_EXPLICIT_GC_COMPARISON",
        "checked_markets": len(checks), "expected_markets": 288,
        "unique_input_markets": len({m["market"] for m in cohort["markets"]}),
        "source_runs": dict(Counter(m["source_run"] for m in cohort["markets"])),
        "source_and_replay_rules": "same legacy signal and queue rules; isolated memory optimization check",
        "peak_rss_mib": max_peak, "last_rss_mib": resources[-1]["rss_mib"] if resources else None,
        "maximum_reported_rusage_mib": rusage_peak,
        "maximum_sampled_statm_rss_mib": sampled_peak,
        "peak_definition": "Conservative maximum of reported getrusage high-water and sampled /proc/statm RSS; raw counters retained separately.",
        "maximum_retained_research_graphs_after_retirement": max_graphs,
        "last_retained_engine_identity_count": resources[-1]["retained_engines"] if resources else None,
        "continuous_24h_live_test": False,
        "limits": "Accelerated original-clock replay across retained sources; native writer/retirement/pending lifecycle covered separately by directed tests. Live WS/HTTP latency and 24h scheduling remain untested.",
        "checks": checks,
    }
    filename = "memory-review.json" if args.run == "memory288-v3b" else args.run+"-review.json"
    (root / filename).write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps({k: v for k, v in result.items() if k != "checks"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
