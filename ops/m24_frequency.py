"""Replay frozen M24 signal candidates against a predeclared M21 capture slice."""

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
from pathlib import Path
import resource
import sqlite3

from m1223_replay import file_hash
from polymaker.m1234_sim.events import read_events, write_json
from sop_signals.feed import Feed
from sop_signals.m24 import Parameters


def tick_boundaries(database):
    """Reuse original public tick verification at its recorded availability."""
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        rows = db.execute(
            "SELECT input_seq, kind, payload FROM audit WHERE kind IN "
            "('TICK_CONSTRAINT_CHANGED', 'TICK_REVERIFY_RESULT') ORDER BY seq"
        )
        return {
            seq: json.loads(payload).get("result", {}).get("ticks")
            if kind == "TICK_REVERIFY_RESULT" else None
            for seq, kind, payload in rows
            if kind == "TICK_CONSTRAINT_CHANGED" or json.loads(payload).get("applied") is True
        }


def replay_market(source, archived, parameters):
    raw = source / "input.jsonl.gz"
    assert archived["market"] == source.name
    if archived["status"] != "SEALED" or archived["audit_validity"] != "VALID":
        raise ValueError("UNVERIFIED_CAPTURE:" + source.name)
    tick_schedule = tick_boundaries(source / "simulation.sqlite3")
    signal_events = []
    quality = Counter()
    market_start = int(source.name.rsplit("-", 1)[1])
    market_end = market_start + 300

    def emit(kind, row):
        event = row["event"]
        if event in {"SIGNAL_CONFIRMED", "SIGNAL_SUSPENDED", "SIGNAL_ENDED"}:
            payload = row["payload"]
            if event == "SIGNAL_CONFIRMED":
                start_wall = wall - (now - payload["t0"])
                in_window = market_start <= start_wall <= wall < market_end
                payload = {**payload, "in_scheduled_window": in_window, "wall": wall}
            signal_events.append({"event": event, "input_seq": row["input_seq"],
                                  "available": row["available"], **payload})

    feed, last_receipt, end_seq = None, None, None
    for event in read_events(raw):
        kind, seq = event["kind"], event["seq"]
        now, wall = event["available_mono"], event["available_wall"]
        clock = dict(now=now, wall=wall, seq=seq)
        pending = event.get("pending", [])
        if len(pending) != min(2, event.get("pending_count", len(pending))):
            raise ValueError("PENDING_WATERMARK_INVALID")
        if kind == "INIT":
            definition = event["definition"]
            if definition["market"]["slug"] != source.name or feed is not None:
                raise ValueError("FOREIGN_MARKET")
            if definition.get("artificial_fixture"):
                raise ValueError("ARTIFICIAL_INPUT")
            ticks = definition["ticks"]
            feed = Feed(definition["market"]["condition_id"], tuple(ticks), ticks,
                        emit, parameters, strategy="M24")
            continue
        if feed is None or now < feed.now:
            raise ValueError("AVAILABLE_CLOCK_INVALID")
        if seq in tick_schedule and tick_schedule[seq] is None:
            feed.clock(**clock)
            for token, book in feed.books.items():
                book.tick_verified = False
                feed.unavailable(token, "OFFICIAL_TICK_UNVERIFIED")
            quality["PUBLIC_TICK_CONSTRAINT_CHANGED"] += 1
        if kind == "CONNECT":
            feed.connect(**clock)
            last_receipt = None
        elif kind == "GAP":
            feed.clock(**clock)
            feed.gap("RECORDED_SOURCE_GAP")
            quality["RECORDED_SOURCE_GAP"] += 1
        elif kind == "FRAME" and feed.connected:
            received = event["received_mono"]
            if last_receipt is not None and received < last_receipt:
                raise ValueError("RECEIPT_CLOCK_REGRESSION")
            last_receipt = received
            feed.apply(event["raw"], received=received, pending=pending, **clock)
        elif seq in tick_schedule and tick_schedule[seq] is not None:
            feed.clock(**clock)
            for token, tick in tick_schedule[seq].items():
                book = feed.books[token]
                if book.tick != type(book.tick)(tick):
                    raise ValueError("REVERIFY_TICK_VALUE_MISMATCH")
                book.tick_verified = True
            quality["PUBLIC_TICK_REVERIFIED"] += 1
        elif kind == "END":
            feed.clock(**clock)
            feed.gap("OBSERVATION_ENDED")
            end_seq = seq
        elif end_seq is None:
            feed.timer(pending=pending, **clock)
    if end_seq is None or seq != archived["input_seq"]:
        raise ValueError(f"WATERMARK_MISMATCH:{seq}:{archived['input_seq']}")
    results = {}
    for model in feed.core.variants:
        name = model.parameters.name
        confirmations = [ev for ev in signal_events
                         if ev["candidate"] == name and ev["event"] == "SIGNAL_CONFIRMED"]
        ids = {event["id"] for event in confirmations}
        if len(ids) != len(confirmations):
            raise ValueError("DUPLICATE_M24_SIGNAL")
        results[name] = {
            "version": model.version,
            "signals": sum(ev["in_scheduled_window"] for ev in confirmations),
            "out_of_window_signals": sum(not ev["in_scheduled_window"] for ev in confirmations),
            "funnel": dict(model.counts),
        }
    feed.release_runtime()
    return {
        "market": source.name, "status": "REPLAYED", "recorded_input_seq": seq, "end_seq": end_seq,
        "complete_window": archived["complete"], "primary_eligible": archived["primary_eligible"],
        "recorded_gate": archived["gate"], "source_sha256": file_hash(raw),
        "source_bytes": raw.stat().st_size, "source": str(raw), "quality": dict(quality),
        "capture_quality_counts": archived["quality_counts"], "variants": results,
        "events": signal_events,
        "worker_max_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
    }


def aggregate(rows, parameters, denominator, target):
    results = {}
    for p in parameters:
        all_rows = [r for r in rows if r["status"] == "REPLAYED"]
        primary = [r for r in all_rows if r["primary_eligible"]]
        totals = {}
        for label, source in (("all_recorded", all_rows), ("primary", primary)):
            signal_rows = [r for r in source if r["variants"][p.name]["signals"]]
            funnel = Counter()
            for row in source:
                funnel.update(row["variants"][p.name]["funnel"])
            totals[label] = {
                "signal_count": sum(r["variants"][p.name]["signals"] for r in signal_rows),
                "signal_markets": [r["market"] for r in signal_rows],
                "signal_market_count": len(signal_rows),
                "coverage": len(signal_rows) / denominator,
                "funnel": dict(funnel),
                "has_unknown_root_markets": sum(
                    any(k.startswith("UNKNOWN:") for k in r["variants"][p.name]["funnel"])
                    for r in source
                ),
            }
        results[p.name] = {"parameter_sha256": p.digest, **totals,
                           "development_target_met": totals["primary"]["signal_market_count"] >= target}
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=1, choices=(1, 2))
    args = parser.parse_args()
    cohort_path = args.cohort.resolve()
    cohort = json.loads(cohort_path.read_text())
    output = cohort_path.parent
    source = Path(cohort["capture"])
    archive = Path(cohort["capture_archive"])
    capture = json.loads((archive / "report.json").read_text())
    completion = json.loads((archive / "completion.json").read_text())
    declared = json.loads((archive / "cohort.json").read_text())
    names = cohort["markets"]
    if (completion["status"] != "PASS" or completion["reason"] != "PLANNED_COMPLETE"
            or cohort["denominator"] != 24 or len(names) != 24
            or names != declared["markets"][:24] or capture["run"] != str(source)):
        raise ValueError("COHORT_OR_CAPTURE_BINDING_INVALID")
    archived = {m["market"]: m for m in capture["markets"]}
    parameters = [Parameters(**p) for p in cohort["revisions"]]
    if parameters[0] != Parameters() or len({p.name for p in parameters}) != len(parameters):
        raise ValueError("BASELINE_OR_VARIANT_DEFINITION_INVALID")
    frozen = {p.name: p.digest for p in parameters}
    code_hash = file_hash(output / "frozen-replay-source.tar.gz")
    run_identity = hashlib.sha256(cohort_path.read_bytes() + code_hash.encode()).hexdigest()
    write_json(output / "settings.json", {
        "role": "FREQUENCY_ONLY", "replay_identity": run_identity,
        "cohort_sha256": file_hash(cohort_path), "frozen_code_sha256": code_hash, "versions": frozen,
        "counting": "distinct eligible markets with at least one proved deduplicated publication",
    })
    (output / "markets").mkdir()
    rows = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(replay_market, source / name, archived[name], parameters)
                   for name in names]
        for name, pending in zip(names, futures):
            try:
                row = pending.result()
            except Exception as exc:
                row = dict(market=name, status="UNAVAILABLE", error=f"{type(exc).__name__}:{exc}",
                           primary_eligible=archived[name]["primary_eligible"],
                           complete_window=archived[name]["complete"])
            write_json(output / "markets" / (name + ".json"), row)
            rows.append(row)
            print(f"{len(rows):2}/24 {name} {row['status']} "
                  + (str({k: v["signals"] for k, v in row["variants"].items()})
                     if row["status"] == "REPLAYED" else row["error"]), flush=True)
            write_json(output / "latest.json", {
                "status": "RUNNING" if len(rows) < 24 else (
                    "COMPLETE" if all(r["status"] == "REPLAYED" for r in rows) else "PARTIAL"),
                "markets_finished": len(rows),
                "markets_replayed": sum(r["status"] == "REPLAYED" for r in rows),
                "markets_unavailable": [r["market"] for r in rows if r["status"] != "REPLAYED"],
                "primary_exclusions": [r["market"] for r in rows if not r["primary_eligible"]],
                "recorded_complete_windows": sum(r["complete_window"] for r in rows),
                "metrics_status": "PARTIAL" if any(
                    not r["complete_window"] or r["status"] != "REPLAYED"
                    or any(k.startswith("UNKNOWN:") for v in r["variants"].values()
                           for k in v["funnel"]) for r in rows
                ) else "OK",
                "denominator": cohort["denominator"], "minimum_signal_markets": cohort["target"],
                "variants": aggregate(rows, parameters, cohort["denominator"], cohort["target"]),
            })


if __name__ == "__main__":
    main()
