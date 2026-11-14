#!/usr/bin/env python3
"""Freeze settled recordings and replay baseline + 3/10/30s M234 lifetimes offline."""

from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import hashlib
import json
import shutil
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from decimal import Decimal as D
from pathlib import Path

# A replay must execute the source version that produced its frozen tape.
# This runs before importing polymaker; import-based unit tests keep their fixture version.
if __name__ == "__main__":
    from m1234_sim_frozen_source import materialize

    bootstrap = argparse.ArgumentParser(add_help=False)
    bootstrap.add_argument("--source-run", required=True)
    source_args, _ = bootstrap.parse_known_args()
    project = Path(__file__).resolve().parents[1]
    frozen_root, _ = materialize(
        source_args.source_run, project / "research_outputs/m1234_sim/runtime"
    )
    sys.path.insert(0, str(frozen_root / "src"))

from m1234_sim_lifetime import LifetimeEngine, experiment_definition  # noqa: E402

from polymaker.m1234_sim.events import digest, encode, read_events, write_json  # noqa: E402
from polymaker.m1234_sim.report import read_state  # noqa: E402
from polymaker.m1234_sim.runner import source_failure  # noqa: E402
from polymaker.m1234_sim.validity import database_validity  # noqa: E402


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def select_sources(run, output, end_epoch=None):
    output.mkdir(parents=True, exist_ok=False)
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "source_run": str(run.resolve()),
        "experiment": experiment_definition(),
        "runner_sha256": file_hash(__file__),
        "source_bootstrap_sha256": file_hash(
            Path(__file__).with_name("m1234_sim_frozen_source.py")
        ),
        "sources": [],
        "excluded": [],
    }
    for market in sorted(run.glob("btc-*")):
        reason = None
        db = market / "simulation.sqlite3"
        if end_epoch is not None and int(market.name.rsplit("-", 1)[1]) > end_epoch:
            reason = "AFTER_FROZEN_COHORT_CUTOFF"
        if not db.exists():
            reason = reason or "NO_COMMITTED_DATABASE"
        if reason:
            manifest["excluded"].append({"market": market.name, "reason": reason})
            continue
        try:
            state = read_state(db)
            validity = database_validity(db, state)
            if validity["status"] != "VALID":
                reason = "SOURCE_NOT_VALID"
            elif not state["coverage"]["observed_through_t300"]:
                reason = "OBSERVATION_NOT_ENDED_THROUGH_T300"
            elif not state["resolution"]:
                reason = "OFFICIAL_SETTLEMENT_PENDING"
        except Exception as e:
            reason = "SOURCE_UNAVAILABLE:" + str(e)
        if reason:
            manifest["excluded"].append({"market": market.name, "reason": reason})
            continue
        tape = market / "input.jsonl.gz"
        if source_failure(tape) is not None:
            raise ValueError("SOURCE_PACKAGE_FAILED:" + market.name)
        dest = output / market.name
        dest.mkdir()
        write_json(dest / "source-state.json", state)
        manifest["sources"].append(
            {
                "market": market.name,
                "input": str(tape.resolve()),
                "input_sha256": file_hash(tape),
                "input_bytes": tape.stat().st_size,
                "input_seq": state["input_seq"],
                "definition_hash": state["definition_hash"],
                "complete_window": state["coverage"]["complete_window"],
                "source_state_hash": digest(state),
                "winner": state["resolution"]["winner"],
            }
        )
    if not manifest["sources"]:
        raise ValueError("NO_SETTLED_VALID_RECORDINGS")
    write_json(output / "manifest.json", manifest)
    return manifest


def summarize_market(state, directory):
    db = sqlite3.connect(f"file:{directory}/simulation.sqlite3?mode=ro", uri=True)
    signals = defaultdict(set)
    decisions = defaultdict(Counter)
    for kind, raw in db.execute(
        "SELECT kind,payload FROM audit WHERE kind IN ('SIGNAL','LIFETIME_CANCEL_DECISION')"
    ):
        p = json.loads(raw)
        if (
            kind == "SIGNAL"
            and p["strategy"] in ("M2", "M3", "M4")
            and p["event"] in ("M2_CANDIDATE", "CANDIDATE")
        ):
            fact = p["payload"]
            signals[p["strategy"]].add(fact.get("candidate_id") or fact.get("id"))
        elif kind == "LIFETIME_CANCEL_DECISION":
            decisions[(p["scenario"], p["strategy"])][p["reason"]] += 1
    db.close()
    rows, order_rows, cohorts = [], [], {}
    for name, broker in state["brokers"].items():
        variant, scenario = name.split("/", 1) if "/" in name else ("baseline", name)
        for strategy in ("M2", "M3", "M4"):
            orders = [o for o in broker["orders"].values() if o["intent"]["strategy"] == strategy]
            cohort = sorted(
                (
                    o["intent"]["opportunity"],
                    o["intent"]["outcome"],
                    o["intent"]["price"],
                    o["intent"]["quantity"],
                    o["intent"]["available"],
                )
                for o in orders
            )
            cohorts[(variant, scenario, strategy)] = cohort
            filled, uncertain = 0, 0
            shares = cost = known_pnl = lower = upper = D(0)
            winner = state["resolution"]["winner"]
            for o in orders:
                i = o["intent"]
                q, p = D(o["filled"]), D(i["price"])
                unit_pnl = (D(1) if i["outcome"] == winner else D(0)) - p
                pnl = q * unit_pnl
                remaining = D(i["quantity"]) - q
                possible = remaining * unit_pnl if o["uncertain"] else D(0)
                lo, hi = pnl + min(D(0), possible), pnl + max(D(0), possible)
                filled += q > 0
                uncertain += bool(o["uncertain"])
                shares += q
                cost += D(o["spent"])
                known_pnl += pnl
                lower += lo
                upper += hi
                order_rows.append(
                    {
                        "market": state["market"]["slug"],
                        "strategy": strategy,
                        "variant": variant,
                        "scenario": scenario,
                        "order": o["id"],
                        "candidate": i["opportunity"],
                        "outcome": i["outcome"],
                        "winner": winner,
                        "price": i["price"],
                        "quantity": i["quantity"],
                        "filled": str(q),
                        "uncertain": o["uncertain"],
                        "known_gross_pnl": str(pnl),
                        "gross_pnl_lower": str(lo),
                        "gross_pnl_upper": str(hi),
                        "submitted_at": i["available"],
                        "signal_or_order_deadline": i["deadline"],
                        "live_at": o["live_at"],
                        "cancel_requested": o["cancel_requested"],
                        "first_fill": o["first_fill"],
                        "state": o["state"],
                    }
                )
            account = broker["accounts"][strategy]
            assert known_pnl == D(account["payout"]) - D(account["spent"])
            assert cost == D(account["spent"])
            rows.append(
                {
                    "strategy": strategy,
                    "variant": variant,
                    "scenario": scenario,
                    "signals": len(signals[strategy]),
                    "submitted": len(orders),
                    "filled_orders": filled,
                    "unresolved_orders": uncertain,
                    "filled_shares": str(shares),
                    "cost": str(cost),
                    "known_gross_pnl": str(known_pnl),
                    "gross_pnl_lower": str(lower),
                    "gross_pnl_upper": str(upper),
                    "cancel_reasons": dict(decisions[(name, strategy)]),
                }
            )
    for (variant, scenario, strategy), cohort in cohorts.items():
        if variant != "baseline" and cohort != cohorts[("baseline", scenario, strategy)]:
            raise AssertionError(f"ADMISSION_COHORT_CHANGED:{variant}/{scenario}/{strategy}")
    return rows, order_rows


async def replay_one(source, output):
    directory = output / source["market"]
    result_path = directory / "result.json"
    if result_path.exists():
        return json.loads(result_path.read_text())
    if shutil.disk_usage(output).free < 5 * 1024**3:
        raise RuntimeError("REPLAY_FREE_DISK_BELOW_5_GIB")
    tape = Path(source["input"])
    if file_hash(tape) != source["input_sha256"]:
        raise ValueError("FROZEN_INPUT_CHANGED")
    original = json.loads((directory / "source-state.json").read_text())
    engine = LifetimeEngine(directory)
    started = time.monotonic()
    try:
        for event in read_events(tape):
            if event["seq"] > source["input_seq"]:
                break
            await engine.ingest(event)
        if engine.seq != source["input_seq"] or not engine.ended or not engine.resolution:
            raise ValueError("FROZEN_RECORDING_INCOMPLETE")
        state = engine.snapshot()
        if state["validity"]["status"] != "VALID":
            raise ValueError("EXPERIMENT_MARKET_INVALID")
        baseline_checks = {}
        for name, expected in original["brokers"].items():
            baseline_checks[name] = digest(state["brokers"][name]) == digest(expected)
        if not all(baseline_checks.values()):
            write_json(directory / "mismatched-state.json", state)
            raise AssertionError("BASELINE_REPLAY_MISMATCH")
        counts = {
            "observed": dict(engine.store.observed_counts),
            "omitted": dict(engine.store.omitted_counts),
        }
        await engine.close()
        rows, orders = summarize_market(state, directory)
        write_json(directory / "orders.json", orders)
        result = {
            "market": source["market"],
            "status": "PASS",
            "baseline_exact": baseline_checks,
            "identical_admission_cohorts": True,
            "runtime_seconds": time.monotonic() - started,
            "input_seq": engine.seq,
            "rows": rows,
            "audit_retention": counts,
        }
        write_json(result_path, result)
        return result
    except BaseException as error:
        write_json(directory / "failure.json", {"error": str(error), "input_seq": engine.seq})
        if not engine.closed:
            await engine.close(persist=False)
        raise


def aggregate(output, manifest):
    totals = {}
    completed = []
    for source in manifest["sources"]:
        path = output / source["market"] / "result.json"
        if not path.exists():
            continue
        result = json.loads(path.read_text())
        completed.append(source["market"])
        for row in result["rows"]:
            key = (row["variant"], row["scenario"], row["strategy"])
            if key not in totals:
                totals[key] = {k: row[k] for k in ("variant", "scenario", "strategy")}
                totals[key].update(
                    {k: 0 for k in ("signals", "submitted", "filled_orders", "unresolved_orders")}
                )
                totals[key].update(
                    {
                        k: D(0)
                        for k in (
                            "filled_shares",
                            "cost",
                            "known_gross_pnl",
                            "gross_pnl_lower",
                            "gross_pnl_upper",
                        )
                    }
                )
                totals[key]["cancel_reasons"] = Counter()
            t = totals[key]
            for k in ("signals", "submitted", "filled_orders", "unresolved_orders"):
                t[k] += row[k]
            for k in (
                "filled_shares",
                "cost",
                "known_gross_pnl",
                "gross_pnl_lower",
                "gross_pnl_upper",
            ):
                t[k] += D(row[k])
            t["cancel_reasons"].update(row["cancel_reasons"])
    for t in totals.values():
        t["confirmed_fill_rate"] = t["filled_orders"] / t["submitted"] if t["submitted"] else None
        # Orders with known fills may also have an unknown remainder; don't add twice.
    result = {
        "status": "COMPLETE" if len(completed) == len(manifest["sources"]) else "PARTIAL",
        "markets": len(completed),
        "planned_markets": len(manifest["sources"]),
        "first_market": completed[0] if completed else None,
        "last_market": completed[-1] if completed else None,
        "rows": list(totals.values()),
        "definitions": {
            "fill_rate": "orders with positive MODELLED_FILL / submitted orders; partial counts once",
            "pnl": "confirmed fills held to recorded official winner; gross USDC, fees/rebates excluded",
            "bounds": "known PnL plus possible remaining fills of uncertain orders; not confidence intervals",
            "signals": "candidate IDs deduplicated within market/strategy",
            "accounts": "independent market/strategy/variant/scenario; do not sum variants as portfolio",
            "cohort": "all selected valid, observed-through-T300, officially settled markets of one source run",
        },
    }
    write_json(output / "comparison.json", result)
    return result


async def main(args):
    output = Path(args.output)
    manifest = (
        json.loads((output / "manifest.json").read_text())
        if args.resume
        else select_sources(Path(args.source_run), output, args.end_epoch)
    )
    if manifest["experiment"] != experiment_definition() or manifest["runner_sha256"] != file_hash(
        __file__
    ):
        raise ValueError("EXPERIMENT_IMPLEMENTATION_CHANGED")
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(replay_worker, source, output): source for source in manifest["sources"]
        }
        for index, future in enumerate(concurrent.futures.as_completed(futures)):
            result = future.result()
            aggregate(output, manifest)
            print(
                json.dumps(
                    {
                        "completed": index + 1,
                        "total": len(manifest["sources"]),
                        "market": result["market"],
                        "seconds": round(result["runtime_seconds"], 2),
                        "baseline_exact": result["baseline_exact"],
                    }
                ),
                flush=True,
            )
    print(encode(aggregate(output, manifest)), flush=True)


def replay_worker(source, output):
    return asyncio.run(replay_one(source, output))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--end-epoch", type=int)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--workers", type=int, choices=(1, 2), default=1)
    asyncio.run(main(parser.parse_args()))
