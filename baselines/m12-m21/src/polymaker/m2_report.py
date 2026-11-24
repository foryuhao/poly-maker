"""Read-only M2 price/time/anchor breakdowns; never fits or selects a threshold."""

import argparse
import json
import math
import sqlite3
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from polymaker.m2_store import encode


def summarize(rows):
    settled = [r for r in rows if r["sample_pnl"] is not None]
    fills = [r for r in settled if r["status"] == "ASSUMED_FILL"]
    cost = sum((Decimal(r["cost"]) for r in fills), Decimal(0))
    pnl = sum((Decimal(r["trade_pnl"]) for r in fills), Decimal(0))
    blocks = defaultdict(list)
    for row in fills:
        residual = (1 if row["outcome"] == row["winner"] else 0)-float(row["price"])
        blocks[row["slug"]].append(residual)
    n, k = len(settled), len(blocks)
    fill_n = len(fills)
    mean = sum(sum(v) for v in blocks.values())/fill_n if fill_n else None
    interval = None
    if k >= 30:
        # Cluster by five-minute market; repeated signals are not independent trials.
        se = math.sqrt(k/(k-1)*sum((sum(v)-mean*len(v))**2 for v in blocks.values()))/fill_n
        interval = [mean-1.96*se, mean+1.96*se]
    return {"samples": len(rows), "settled_samples": n, "settled_markets": k,
            "settled_research_markets": len({r["slug"] for r in settled}),
            "direction_limit_skipped_samples": sum(r.get("reason") == "DIRECTION_ENTRY_LIMIT_REACHED" for r in rows),
            "pending_samples": sum(r["sample_pnl"] is None and Decimal(r["quantity"]) > 0
                                   and r["status"] == "ASSUMED_FILL" for r in rows),
            "settled_assumed_fills": len(fills), "settled_cost": str(cost), "settled_pnl": str(pnl),
            "settled_roi": str(pnl/cost) if cost else None,
            "all_sample_pnl": str(sum((Decimal(r["sample_pnl"]) for r in settled), Decimal(0))),
            "mean_outcome_minus_price": mean,
            "market_cluster_normal_95_interval": interval,
            "interval_note": "Assumed fills only; exploratory normal approximation; requires >=30 settled markets. No holdout claim."}


def price_bucket(row):
    p = Decimal(row["price"])
    lower = int(p*10)/10
    return f"[{lower:.1f},{lower+.1:.1f})"


def time_bucket(row):
    t = row["elapsed"]
    for lower, upper in ((15, 60), (60, 120), (120, 180), (180, 240)):
        if lower <= t < upper:
            return f"T{lower}-{upper}"
    return "outside_default_window"


def anchor_bucket(row):
    if not row["candidate_payload"]:
        return "unknown"
    value = json.loads(row["candidate_payload"]).get("anchor_displacement_ticks")
    if value is None:
        return "unknown"
    delta = Decimal(str(value))
    return "above_attack_ask" if delta > 0 else "below_attack_ask" if delta < 0 else "same_as_attack_ask"


def analyze(directory, *, version=None, include_incomplete=False):
    path = (Path(directory)/"state.sqlite3").resolve()
    db = sqlite3.connect(path.as_uri()+"?mode=ro", uri=True, timeout=1)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        if version is None:
            row = db.execute("SELECT version FROM runs ORDER BY started DESC LIMIT 1").fetchone()
            version = row[0] if row else None
        rows = [dict(r) for r in db.execute("""SELECT s.*,m.winner,m.full_window,
            c.payload AS candidate_payload FROM samples s JOIN markets m ON m.slug=s.slug
            LEFT JOIN candidates c ON c.id=s.candidate_id
            WHERE s.version=? AND (? OR m.full_window=1) ORDER BY s.wall,s.id""",
            (version, int(include_incomplete)))]
        grouped = defaultdict(list)
        for row in rows:
            grouped[row["rule"]].append(row)
        result = {"phase": "EXPLORATION_NOT_HOLDOUT_VALIDATION", "version": version,
                  "include_incomplete": include_incomplete, "real_posts": 0,
                  "fill_assumption": "FULL_FILL_AT_OBSERVATION_PRICE", "rules": {}}
        for rule, samples in sorted(grouped.items()):
            details = {"overall": summarize(samples)}
            for key, classify in (("price", price_bucket), ("time", time_bucket),
                                  ("anchor_relation", anchor_bucket), ("direction", lambda r: r["outcome"])):
                groups = defaultdict(list)
                for row in samples:
                    groups[classify(row)].append(row)
                details[key] = {name: summarize(group) for name, group in sorted(groups.items())}
            result["rules"][rule] = details
        result['strategies'] = {'M2': result['rules'].get('all_candidates', {'overall': summarize([])})}
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='strategy_samples'").fetchone():
            for strategy in ('M3', 'M4'):
                samples = [dict(r) for r in db.execute('''SELECT s.*,m.winner,m.full_window
                    FROM strategy_samples s JOIN markets m ON m.slug=s.slug
                    WHERE s.version=? AND s.strategy=? AND (? OR m.full_window=1) ORDER BY s.wall,s.id''',
                    (version, strategy, int(include_incomplete)))]
                details = {'overall': summarize(samples)}
                for key, classify in (('price', price_bucket), ('time', time_bucket),
                                      ('direction', lambda r: r['outcome'])):
                    groups = defaultdict(list)
                    for row in samples:
                        groups[classify(row)].append(row)
                    details[key] = {name: summarize(group) for name, group in sorted(groups.items())}
                result['strategies'][strategy] = details
        return result
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description="Read-only M2 exploratory breakdowns")
    parser.add_argument("--directory", default="research_outputs/m2_virtual")
    parser.add_argument("--version")
    parser.add_argument("--include-incomplete", action="store_true")
    args = parser.parse_args()
    print(encode(analyze(args.directory, version=args.version,
                         include_incomplete=args.include_incomplete)))


if __name__ == "__main__":
    main()
