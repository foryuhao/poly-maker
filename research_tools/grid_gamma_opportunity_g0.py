"""G0: read-only, fixed 5-cent YES-mid grid opportunity measurement.

Run: python3 research_tools/grid_gamma_opportunity_g0.py
No orders, fills, positions, external data, or parameter sweep.
"""
from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median

from r0_rolling_pair_replay import Book

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "research_outputs/execution_replay_audit_20261003/dataset_audit.csv"
LABELS = ROOT / "research_outputs/r0_rolling_pair_replay_20261003/settlement_labels.csv"
DATA = ROOT / "research_data/m4a"
OUT = ROOT / "research_outputs/grid_gamma_g0_20261004"
LEVELS = range(500, 10000, 500)  # price in 1e-4 units; fixed 0.05 grid
PAIR_FIELDS = ["market", "first_direction", "first_grid", "first_time", "first_elapsed", "second_direction", "second_grid", "second_time", "second_elapsed", "holding_seconds", "yes_price", "no_price", "pair_cost", "pair_edge"]
CROSS_FIELDS = ["market", "timestamp", "elapsed", "direction", "grid_level", "yes_mid_before", "yes_mid_after", "open_crossing_state", "pair_completed", "pair_edge"]
MARKET_FIELDS = ["market", "session", "valid_mid_observations", "invalid_yes_book_events", "active_seconds", "total_crossings", "up_crossings", "down_crossings", "alternating_pairs", "mean_pair_edge", "median_pair_edge", "total_theoretical_edge_per_share", "mean_holding_seconds", "median_holding_seconds", "grid_turnover", "grid_crossing_rate", "theoretical_grid_edge_per_minute", "theoretical_grid_edge_per_pair", "unpaired_final_crossing", "unpaired_direction", "unpaired_grid_level", "unpaired_first_elapsed", "unpaired_time_open_seconds"]
BUCKET_FIELDS = ["bucket", "valid_book_seconds", "markets_with_valid_book", "crossings", "crossings_per_active_minute", "alternating_pairs_initiated", "alternating_pairs_completed", "completed_pairs_per_active_minute", "positive_edge_pairs_completed", "mean_pair_edge", "median_pair_edge"]
COHORT_FIELDS = ["bucket", "candidate_first_crossings", "completed", "superseded_same_direction", "censored_invalid_book", "unpaired_at_end", "completion_rate"]


def q(values, fraction):
    if not values:
        return None
    v = sorted(values)
    x = (len(v) - 1) * fraction
    i = int(x)
    return v[i] + (v[min(i + 1, len(v) - 1)] - v[i]) * (x - i)


def bucket(t):
    if t < 0 or t >= 300:
        return None
    lo = min(270, int(t // 30) * 30)
    return f"{lo}-{lo + 30}s"


def entry_bucket(t):
    if t < 0 or t >= 300:
        return None
    lo = 0 if t < 150 else min(270, int(t // 30) * 30)
    hi = 150 if lo == 0 else lo + 30
    return f"{lo}-{hi}s"


def crossing_levels(before, after):
    """Use strict source side and inclusive destination; traverse jump in path order."""
    if after > before:
        return [("UP", x) for x in LEVELS if before < x <= after]
    if after < before:
        return [("DOWN", x) for x in reversed(LEVELS) if after <= x < before]
    return []


def iso(t):
    return datetime.fromtimestamp(t / 1_000_000, timezone.utc).isoformat()


def csv_write(name, rows, fields):
    with (OUT / name).open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def analyze(row, label):
    slug, session = row["market"], row["session"]
    path = DATA / session
    manifest = json.loads((path / "manifest.json").read_text())
    if (manifest["market_slug"] != slug or str(manifest["condition_id"]).lower() != label["condition_id"].lower()
            or manifest["yes_token_id"] != label["yes_token_id"] or manifest["no_token_id"] != label["no_token_id"]
            or label["resolution_status"] != "resolved" or label["winner"] not in ("YES", "NO")):
        raise RuntimeError(f"Session/official-label identity mismatch: {slug}")
    start = int(slug.rsplit("-", 1)[1]) * 1_000_000
    end = start + 300_000_000
    book = Book()
    previous_mid = None
    previous_t = None
    last_t = None
    last_valid_t = None
    active_us = 0
    active_by_bucket = [0] * 10
    valid_count = invalid_count = 0
    crossings, pairs = [], []
    candidates = []
    open_crossing = None
    for line in (path / "events.jsonl").open():
        e = json.loads(line)
        t = e.get("available_at_us")
        if t is None or not start <= t < end:
            continue
        if last_t is not None and t < last_t:
            raise RuntimeError(f"Nonmonotone availability in {session}")
        last_t = t
        p = e.get("payload") or {}
        if p.get("token_id") != manifest["yes_token_id"] or e["kind"] not in ("BOOK", "REST_BOOK", "DELTA"):
            continue
        # Duration is observed valid-book time between successive YES book events.
        if previous_mid is not None and previous_t is not None:
            active_us += t - previous_t
            for i in range(10):
                lo, hi = start + i*30_000_000, start + (i+1)*30_000_000
                active_by_bucket[i] += max(0, min(t, hi) - max(previous_t, lo))
        book.update(e["kind"], p)
        if not book.valid():
            invalid_count += 1
            if open_crossing is not None:
                candidates[-1]["outcome"] = "CENSORED_INVALID_BOOK"
                open_crossing = None
            previous_mid = None
            previous_t = None
            continue
        mid = (book.bid + book.ask) / 2  # exact in 1e-4 units; half-ticks allowed
        valid_count += 1
        last_valid_t = t
        if previous_mid is not None:
            for direction, level in crossing_levels(previous_mid, mid):
                elapsed = (t - start) / 1_000_000
                crossing = {"market": slug, "timestamp": iso(t), "elapsed": elapsed,
                            "direction": direction, "grid_level": level / 10000,
                            "yes_mid_before": previous_mid / 10000, "yes_mid_after": mid / 10000,
                            "open_crossing_state": "", "pair_completed": False, "pair_edge": ""}
                current = {"direction": direction, "level": level, "time_us": t,
                           "elapsed": elapsed, "timestamp": iso(t)}
                if open_crossing is None:
                    open_crossing = current
                    candidates.append({**current, "market": slug, "outcome": None})
                elif direction == open_crossing["direction"]:
                    if (direction == "DOWN" and level < open_crossing["level"] or
                            direction == "UP" and level > open_crossing["level"]):
                        candidates[-1]["outcome"] = "SUPERSEDED"
                        open_crossing = current
                        candidates.append({**current, "market": slug, "outcome": None})
                else:
                    first = open_crossing
                    yes = first["level"] if first["direction"] == "DOWN" else level
                    no = 10000 - (first["level"] if first["direction"] == "UP" else level)
                    edge = (10000 - yes - no) / 10000
                    pair = {"market": slug, "first_direction": first["direction"],
                            "first_grid": first["level"] / 10000, "first_time": first["timestamp"],
                            "first_elapsed": first["elapsed"], "second_direction": direction,
                            "second_grid": level / 10000, "second_time": iso(t),
                            "second_elapsed": elapsed, "holding_seconds": (t - first["time_us"]) / 1_000_000,
                            "yes_price": yes / 10000, "no_price": no / 10000,
                            "pair_cost": (yes + no) / 10000, "pair_edge": edge}
                    if edge < 0 or round(pair["pair_cost"] + edge, 10) != 1:
                        raise RuntimeError(f"Invalid pair accounting: {pair}")
                    pairs.append(pair)
                    candidates[-1]["outcome"] = "COMPLETED"
                    crossing["pair_completed"] = True
                    crossing["pair_edge"] = edge
                    open_crossing = current
                    candidates.append({**current, "market": slug, "outcome": None})
                crossing["open_crossing_state"] = f"{open_crossing['direction']} {open_crossing['level'] / 10000:.2f}"
                crossings.append(crossing)
        previous_mid = mid
        previous_t = t
    if open_crossing:
        candidates[-1]["outcome"] = "OPEN_AT_END"
    edges = [p["pair_edge"] for p in pairs]
    holdings = [p["holding_seconds"] for p in pairs]
    active_s = active_us / 1_000_000
    summary = {"market": slug, "session": session, "valid_mid_observations": valid_count,
               "invalid_yes_book_events": invalid_count, "active_seconds": active_s,
               "total_crossings": len(crossings), "up_crossings": sum(c["direction"] == "UP" for c in crossings),
               "down_crossings": sum(c["direction"] == "DOWN" for c in crossings),
               "alternating_pairs": len(pairs), "mean_pair_edge": mean(edges) if edges else None,
               "median_pair_edge": median(edges) if edges else None,
               "total_theoretical_edge_per_share": sum(edges),
               "mean_holding_seconds": mean(holdings) if holdings else None,
               "median_holding_seconds": median(holdings) if holdings else None,
               "grid_turnover": len(pairs), "grid_crossing_rate": len(crossings) / active_s if active_s else None,
               "theoretical_grid_edge_per_minute": sum(edges) / (active_s / 60) if active_s else None,
               "theoretical_grid_edge_per_pair": mean(edges) if edges else None,
               "unpaired_final_crossing": bool(open_crossing),
               "unpaired_direction": open_crossing["direction"] if open_crossing else "",
               "unpaired_grid_level": open_crossing["level"] / 10000 if open_crossing else "",
               "unpaired_first_elapsed": open_crossing["elapsed"] if open_crossing else "",
               "unpaired_time_open_seconds": (end - open_crossing["time_us"]) / 1_000_000 if open_crossing else ""}
    return crossings, pairs, candidates, summary, active_by_bucket


def main():
    with AUDIT.open() as f:
        selected = [r for r in csv.DictReader(f) if r["execution_replay_usable"].lower() == "true"]
    if len(selected) != 32 or len({r["market"] for r in selected}) != 32:
        raise RuntimeError("Expected exact audited 32-market universe")
    with LABELS.open() as f:
        labels = list(csv.DictReader(f))
    if len(labels) != 32 or {r["market"] for r in labels} != {r["market"] for r in selected}:
        raise RuntimeError("Official-label universe differs from selected markets")
    by_label = {r["market"]: r for r in labels}
    all_cross, all_pairs, all_candidates, markets = [], [], [], []
    active_by_market = {}
    for row in selected:
        cross, pairs, candidates, summary, active = analyze(row, by_label[row["market"]])
        all_cross.extend(cross); all_pairs.extend(pairs); all_candidates.extend(candidates); markets.append(summary)
        active_by_market[row["market"]] = active
    OUT.mkdir(parents=True, exist_ok=True)
    csv_write("crossings.csv", all_cross, CROSS_FIELDS)
    csv_write("theoretical_pairs.csv", all_pairs, PAIR_FIELDS)
    csv_write("market_summary.csv", markets, MARKET_FIELDS)
    buckets = []
    for i in range(0, 300, 30):
        name = f"{i}-{i+30}s"
        c = [x for x in all_cross if bucket(x["elapsed"]) == name]
        initiated = [x for x in all_pairs if bucket(x["first_elapsed"]) == name]
        completed = [x for x in all_pairs if bucket(x["second_elapsed"]) == name]
        edges = [x["pair_edge"] for x in completed]
        seconds = sum(x[i//30] for x in active_by_market.values()) / 1_000_000
        buckets.append({"bucket": name, "valid_book_seconds": seconds,
                        "markets_with_valid_book": sum(x[i//30] > 0 for x in active_by_market.values()),
                        "crossings": len(c), "crossings_per_active_minute": len(c)/(seconds/60) if seconds else None,
                        "alternating_pairs_initiated": len(initiated),
                        "alternating_pairs_completed": len(completed),
                        "completed_pairs_per_active_minute": len(completed)/(seconds/60) if seconds else None,
                        "positive_edge_pairs_completed": sum(x > 0 for x in edges),
                        "mean_pair_edge": mean(edges) if edges else None, "median_pair_edge": median(edges) if edges else None})
    csv_write("time_buckets.csv", buckets, BUCKET_FIELDS)
    cohorts = []
    for name in ["0-150s"] + [f"{i}-{i+30}s" for i in range(150, 300, 30)]:
        xs = [x for x in all_candidates if entry_bucket(x["elapsed"]) == name]
        completed = sum(x["outcome"] == "COMPLETED" for x in xs)
        cohorts.append({"bucket": name, "candidate_first_crossings": len(xs), "completed": completed,
                        "superseded_same_direction": sum(x["outcome"] == "SUPERSEDED" for x in xs),
                        "censored_invalid_book": sum(x["outcome"] == "CENSORED_INVALID_BOOK" for x in xs),
                        "unpaired_at_end": sum(x["outcome"] == "OPEN_AT_END" for x in xs),
                        "completion_rate": completed / len(xs) if xs else None})
    csv_write("completion_by_entry_time.csv", cohorts, COHORT_FIELDS)
    edges = [p["pair_edge"] for p in all_pairs]
    holds = [p["holding_seconds"] for p in all_pairs]
    pairs_per_market = [m["alternating_pairs"] for m in markets]
    crossings_per_market = [m["total_crossings"] for m in markets]
    edge_per_market = [m["total_theoretical_edge_per_share"] for m in markets]
    high = max(markets, key=lambda m: (m["total_crossings"], m["alternating_pairs"]))["market"]
    low = min(markets, key=lambda m: (m["total_crossings"], m["alternating_pairs"]))["market"]
    one_direction = max(markets, key=lambda m: (m["total_crossings"]/(m["alternating_pairs"]+1), m["total_crossings"]))["market"]
    positive_markets = len({p["market"] for p in all_pairs if p["pair_edge"] > 0})
    classification = ("NO MATERIAL GRID EDGE" if q(edges, .5) == 0 or positive_markets <= len(markets)/2
                      else "GRID GAMMA OPPORTUNITY OBSERVED")
    summary = {"classification": classification, "grid_step": 0.05, "sample_markets": len(markets),
               "source_audit": str(AUDIT.relative_to(ROOT)), "source_labels": str(LABELS.relative_to(ROOT)),
               "crossings_total": len(all_cross), "pairs_total": len(all_pairs),
               "crossings_per_market_mean": mean(crossings_per_market), "crossings_per_market_median": median(crossings_per_market),
               "pairs_per_market_mean": mean(pairs_per_market), "pairs_per_market_median": median(pairs_per_market),
               "markets_with_ge_1_pair": sum(x >= 1 for x in pairs_per_market),
               "markets_with_ge_3_pairs": sum(x >= 3 for x in pairs_per_market),
               "markets_with_ge_5_pairs": sum(x >= 5 for x in pairs_per_market),
               "markets_with_positive_edge_pair": positive_markets,
               "pair_edge": {"mean": mean(edges) if edges else None, "median": q(edges, .5),
                             "p25": q(edges, .25), "p75": q(edges, .75), "p90": q(edges, .90),
                             "positive": sum(x > 0 for x in edges), "zero": sum(x == 0 for x in edges),
                             "negative": sum(x < 0 for x in edges)},
               "theoretical_edge_per_market_mean": mean(edge_per_market),
               "theoretical_edge_per_market_median": median(edge_per_market),
               "theoretical_grid_edge_per_minute_pooled": sum(edges) / (sum(m["active_seconds"] for m in markets) / 60),
               "grid_crossing_rate_pooled": len(all_cross) / sum(m["active_seconds"] for m in markets),
               "holding_seconds": {"mean": mean(holds) if holds else None, "median": q(holds, .5),
                                   "p75": q(holds, .75), "p90": q(holds, .9), "max": max(holds, default=None)},
               "unpaired_market_count": sum(m["unpaired_final_crossing"] for m in markets),
               "censored_invalid_book_candidates": sum(x["outcome"] == "CENSORED_INVALID_BOOK" for x in all_candidates),
               "unpaired_direction": dict(Counter(m["unpaired_direction"] for m in markets if m["unpaired_final_crossing"])),
               "representative_markets": {"high": high, "low": low, "one_direction": one_direction},
               "time_buckets": buckets, "completion_by_entry_time": cohorts,
               "notes": ["Pair edge is grid-level arithmetic, not executable profit.",
                         "Same-direction more-extreme crossings supersede the candidate; other same-direction crossings are ignored.",
                         "Each completed opposite crossing also starts the next rolling candidate; counts are not independent trades.",
                         "Active seconds accumulate valid YES-book intervals between successive YES book events; invalid intervals and time after final event are excluded."]}
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: summary[k] for k in ("sample_markets", "crossings_total", "pairs_total", "pairs_per_market_mean", "pair_edge", "unpaired_market_count", "representative_markets")}, indent=2))


if __name__ == "__main__":
    main()
