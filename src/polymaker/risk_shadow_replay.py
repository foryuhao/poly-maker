"""Offline reproduction of OBSERVATION_ONLY risk logs, without any network."""

from __future__ import annotations

import argparse
import heapq
import json
import math
from dataclasses import replace
from itertools import chain
from pathlib import Path

from polymaker.risk_shadow_model import Market, Quote, ShadowModel


def equivalent(a, b):
    if type(a) in (int, float) and type(b) in (int, float):
        return math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-12)
    return a == b


def records(path):
    with path.open() as handle:
        for line in handle:
            yield json.loads(line)


def replay(directory):
    directory = Path(directory)
    summary = json.loads((directory / "market_summary.json").read_text())
    if summary["mode"] != "OBSERVATION_ONLY":
        raise ValueError("this replay verifies observation-only files")
    if (
        summary["shadow_dropped_event_count"]
        or summary["shadow_error_count"]
        or summary["writer_error_count"]
    ):
        raise ValueError("recorded gaps/errors prevent complete deterministic replay")
    events = records(directory / "market_events.jsonl")
    snapshots = records(directory / "market_snapshots.jsonl")
    risks = records(directory / "risk_snapshots.jsonl")
    hypothetical = records(directory / "hypothetical_risk.jsonl")
    meta = next(events)
    if meta["event_type"] != "OFFICIAL_METADATA":
        raise ValueError("initial official metadata missing")
    model = ShadowModel(
        Market(
            meta["market_slug"],
            meta["condition_id"],
            meta["yes_token_id"],
            meta["no_token_id"],
            meta["market_start_utc"],
            meta["official_tick_size"],
            meta["min_order_size"],
        )
    )
    risks_by_slot = {r["risk_snapshot_id"]: r for r in risks}
    h_by_time = {r["timestamp"]: r for r in hypothetical}
    rows = heapq.merge(
        ((e["observer_sequence"], "event", e) for e in chain((meta,), events)),
        ((s["observer_sequence"], "snapshot", s) for s in snapshots),
        key=lambda row: row[0],
    )
    mismatches = []
    checked = 0
    for _, kind, row in rows:
        if kind == "event":
            event_type = row["event_type"]
            if event_type == "CONNECTION" and row["event_payload"].get("state") == "UP":
                model.quotes = {s: Quote(tick=q.tick) for s, q in model.quotes.items()}
            elif event_type in {"BOOK", "DELTA"}:
                side = "YES" if row["token_id"] == model.market.yes_token_id else "NO"
                model.quote_update(
                    side,
                    Quote(
                        row["best_bid"],
                        row["best_ask"],
                        row["local_receive_timestamp"],
                        row["local_receive_monotonic"],
                        row["source_timestamp"],
                        row["official_tick_size"],
                        row["connection_generation"],
                        True,
                        row["event_id"],
                    ),
                )
            elif event_type == "TICK":
                payload = row["event_payload"]
                side = "YES" if payload["token_id"] == model.market.yes_token_id else "NO"
                model.quotes[side] = replace(model.quotes[side], tick=payload["tick_size"])
            continue
        s, r, h = model.sample(
            row["slot_index"], row["actual_snapshot_time"], row["snapshot_monotonic"]
        )
        for name, computed, saved in [
            ("market", s, row),
            ("risk", r, risks_by_slot[row["snapshot_id"]]),
            ("hypothetical", h, h_by_time[row["timestamp"]]),
        ]:
            for key, value in computed.items():
                if not equivalent(value, saved.get(key)):
                    mismatches.append({"slot": row["slot_index"], "file": name, "field": key})
        checked += 1
    return {
        "status": "PASS" if not mismatches and checked == 300 else "FAIL",
        "snapshot_slots_checked": checked,
        "mismatches": mismatches,
        "network_used": False,
        "actual_inventory_reconstructed": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory")
    result = replay(parser.parse_args().directory)
    print(json.dumps(result))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
