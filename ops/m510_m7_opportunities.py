"""A deliberately loose necessary-opportunity bound, never a signal generator."""

import argparse
import gzip
import hashlib
import json
import sqlite3
from bisect import bisect_left, bisect_right
from collections import defaultdict
from contextlib import closing
from pathlib import Path


def key(fact):
    return fact["confirmed"], fact["input_seq"], fact["values"].get("available_subindex", -1)


def possible_pairs(attacks, qualities):
    """Ignore RF/pause/deadline/anchor constraints, so this only overestimates."""
    q = {token: sorted(sequences) for token, sequences in qualities.items()}
    possible = {}
    for second in attacks:
        prior = []
        for first in attacks:
            if (first["token"] != second["token"] or first["generation"] != second["generation"]
                    or key(first) >= tuple(second["values"]["start_key"])):
                continue
            sequences = q.get(second["token"], [])
            # Equality boundaries deliberately ignored: conservatively allow
            # an edge rather than incorrectly prove there is no opportunity.
            if bisect_right(sequences, first["audit_seq"]) == bisect_left(sequences, second["audit_seq"]):
                prior.append(first["id"])
        if prior:
            possible[second["id"]] = prior
    return possible


def covered_candidate(candidate, attacks, pairs):
    references = [r for r in candidate["current_required_refs"] if r in attacks]
    return (len(references) == 1
            and candidate["root_id"] in pairs.get(references[0], ()))


def load_member(database):
    with closing(sqlite3.connect(database.resolve().as_uri()+"?mode=ro", uri=True)) as db:
        state = json.loads(db.execute("SELECT payload FROM snapshots WHERE name='state'").fetchone()[0])
        archive = json.loads(db.execute(
            "SELECT payload FROM metadata WHERE name='diagnostic_archive'").fetchone()[0])
        attacks = []
        for seq, payload in db.execute(
                "SELECT seq,payload FROM audit WHERE kind='RESEARCH_EVIDENCE' "
                "AND json_extract(payload,'$.event')='FACT_CONFIRMED' "
                "AND json_extract(payload,'$.payload.kind')='AP_BUY'"):
            attacks.append({**json.loads(payload)["payload"], "audit_seq": seq})
        candidates = [json.loads(p)["payload"] for p, in db.execute(
            "SELECT payload FROM audit WHERE kind='SIGNAL' "
            "AND json_extract(payload,'$.event')='CANDIDATE' "
            "AND json_extract(payload,'$.strategy')='M7'")]
    path = database.parent / archive["file"]
    if (not archive["closed"] or archive["input_seq"] != state["input_seq"]
            or hashlib.sha256(path.read_bytes()).hexdigest() != archive["sha256"]):
        raise ValueError("CLOSED_DIAGNOSTIC_HASH_REQUIRED")
    qualities, public_events = defaultdict(list), []
    for f in attacks:
        physical = (f["token"], f["generation"], *key(f), tuple(f["values"]["start_key"]),
                    f["values"]["volume"], f["values"]["depth"])
        public_events.append(((f["audit_seq"], 0, 0), ("AP_BUY", physical)))
    with gzip.open(path, "rt") as stream:
        for line in stream:
            row = json.loads(line)
            p = row.get("payload", {})
            if row["kind"] == "RESEARCH_EVIDENCE" and p.get("event") == "QUALITY_UNAVAILABLE":
                v = p["payload"]
                qualities[v["token"]].append(row["after_audit_seq"])
                physical = (row["input_seq"], p["subindex"], p["generation"], p["available"],
                            v["token"], v["reason"])
                public_events.append(((row["after_audit_seq"], 1, row["diagnostic_seq"]),
                                      ("QUALITY_UNAVAILABLE", physical)))
    # Compare interleaved public evidence order, ignoring only inserted private
    # fact counters. Sorting AP and quality streams separately would lose it.
    public_order = [value for _, value in sorted(public_events)]
    return state, attacks, candidates, qualities, public_order


def review(root):
    members = json.loads((root / "cohort25.json").read_text())["markets"]
    grid = root / "frequency-grid-phase3-v2"
    completed = json.loads((grid / "progress.json").read_text())["markets"]
    by_market = {row["market"]: row for row in completed}
    checks, errors = [], []
    for member in members:
        market = member["market"]
        if market not in by_market:
            continue
        state, attacks, candidates, qualities, public = load_member(grid / market / "simulation.sqlite3")
        if (state["input_seq"] != member["input_seq"] or not state["coverage"]["complete_window"]
                or state["validity"]["status"] != "VALID" or not state["resolution"]
                or state["resolution"]["winner"] != member["winner"]):
            errors.append("INCOMPLETE_OR_UNSETTLED_MEMBER:"+market)
        pairs = possible_pairs(attacks, qualities)
        index = {f["id"]: f for f in attacks}
        uncovered = [c["id"] for c in candidates if not covered_candidate(c, index, pairs)]
        maximum_actual = max(p["counts"].get("M7", 0) for p in by_market[market]["profiles"].values())
        if uncovered or maximum_actual > len(pairs):
            errors.append("PROPOSED_BOUND_CONTRADICTED_BY_ACTUAL_CANDIDATE:"+market)
        control = root / "control25-config-v5" / market
        same_public = None
        if (control / "replay-state.json").exists():
            same_public = public == load_member(control / "simulation.sqlite3")[-1]
            if not same_public:
                errors.append("PUBLIC_AP_OR_QUALITY_DIFFERS_WITH_SHADOW_PROFILES:"+market)
        checks.append({"market": market, "public_ap_buy_facts": len(attacks),
                       "necessary_ap2_opportunities": len(pairs),
                       "maximum_actual_profile_signals": maximum_actual,
                       "all_published_candidates_covered": not uncovered,
                       "published_rows_across_profiles_not_a_signal_total": len(candidates),
                       "same_public_evidence_as_independent_control": same_public})
    controls = json.loads((root / "control25-config-v5-review.json").read_text())
    source_review = json.loads((root / "phase3-grid-review.json").read_text())
    complete = (len(checks) == 25 and controls["status"] == "PASS"
                and source_review["status"] == "COMPLETE"
                and all(c["same_public_evidence_as_independent_control"] is True for c in checks))
    upper = sum(c["necessary_ap2_opportunities"] for c in checks)
    result = {"status": "FAIL" if errors else "COMPLETE" if complete else "PARTIAL",
              "counted_markets": len(checks), "expected_markets": 25,
              "observed_necessary_opportunity_upper_bound": upper if checks else None,
              "full_cohort_cannot_reach10_with_fixed_AP_and_quality":
                  upper < 10 if complete and not errors else None,
              "method": "Unique AP2 facts with a causally earlier same-token/generation AP1 and no strictly interior logged public quality invalidation. RF, pause, hold, anchor, deadlines and concurrency ignored to overestimate.",
              "scope": "Only confirmed public evidence in the fixed recording; missing/ambiguous intervals are not zero missing signals. This bound never creates a signal and cannot establish profitability.",
              "partial_bound_is_not_full_cohort_bound": not complete,
              "selection_uses_pnl": False, "errors": errors, "checks": checks}
    (root / "m7-causal-opportunity-review.json").write_text(json.dumps(result, indent=2)+"\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    result = review(args.directory.resolve())
    print(json.dumps({k: v for k, v in result.items() if k != "checks"}, indent=2))
