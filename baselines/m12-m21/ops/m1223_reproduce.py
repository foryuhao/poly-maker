"""Reproduce one sealed SOP market with its original frozen engine, offline.

No public data API is created. A report-only failed parent run is retained as a
failed run; this command verifies its individually valid market prefix and does
not promote it into a completed 24-market development or validation cohort.
"""

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path


def file_hash(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


async def reproduce(source, runtime, output):
    import sqlite3

    import m511_sample_quality
    from m1223_sim import SopEngine

    from polymaker.m1234_sim.events import digest, read_events, write_json
    from polymaker.m1234_sim.report import read_state
    from polymaker.m1234_sim.store import read_audit
    from polymaker.m1234_sim.validity import database_validity

    allowed = Path("/home/ubuntu/polymaker/research_outputs/m1223_sim")
    if allowed not in output.parents or output.exists():
        raise ValueError("NEW_OWN_REPRODUCTION_DIRECTORY_REQUIRED")
    database = source / "simulation.sqlite3"
    original = read_state(database)
    valid = database_validity(database, original)
    if valid["status"] != "VALID" or not original["coverage"].get("observed_through_t300"):
        raise ValueError("SEALED_INDIVIDUALLY_VALID_MARKET_REQUIRED")
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        row = db.execute(
            "SELECT payload FROM metadata WHERE name='research_sample_eligibility'"
        ).fetchone()
        gate = json.loads(row[0]) if row else None
        owner_row = db.execute("SELECT payload FROM metadata WHERE name='run_owner'").fetchone()
        owner = json.loads(owner_row[0]) if owner_row else None
    # Evaluation metadata is not a model input. Preserve the original read
    # watermark instead of silently joining a newer live-account ledger.
    if gate is not None:
        m511_sample_quality.own_market_gate = lambda _project, _market: gate
    raw = source / "input.jsonl.gz"
    input_hash = file_hash(raw)
    output.mkdir(parents=True)
    binding = {
        "mode": "ORIGINAL_FROZEN_MARKET_REPRODUCTION",
        "source": str(source),
        "source_input_sha256": input_hash,
        "source_input_bytes": raw.stat().st_size,
        "source_database_sha256": file_hash(database),
        "source_watermark": original["input_seq"],
        "runtime": str(runtime),
        "source_run_promoted_to_complete": False,
        "source_input_copied": False,
        "original_raw_must_be_retained_for_replay": True,
    }
    write_json(output / "source.json", binding)
    parameter_file = runtime / "config/m1223-parameters.json"
    parameters = None
    if parameter_file.exists():
        from sop_signals.parameters import load_revision

        parameters, _ = load_revision(json.loads(parameter_file.read_text()))
    engine = SopEngine(output, parameters=parameters)
    engine.store.run_owner = owner
    try:
        for event in read_events(raw):
            await engine.ingest(event)
        if not engine.ended or engine.seq != original["input_seq"]:
            raise ValueError("SOP_REPLAY_NOT_AT_SEALED_SOURCE_WATERMARK")
        state = engine.snapshot()
        await engine.close()
        original_hash = digest(original)
        replayed_hash = digest(state)
        old_audit = digest(list(read_audit(database)))
        new_audit = digest(list(read_audit(output / "simulation.sqlite3")))
        result = {
            **binding,
            "status": "PASS"
            if original_hash == replayed_hash and old_audit == new_audit
            else "FAIL",
            "source_state_hash": original_hash,
            "replayed_state_hash": replayed_hash,
            "source_audit_hash": old_audit,
            "replayed_audit_hash": new_audit,
            "input_seq": engine.seq,
            "state_identical": original_hash == replayed_hash,
            "audit_identical": old_audit == new_audit,
        }
        write_json(output / "reproduction.json", result)
        return result
    except BaseException as exc:
        if not engine.store.closed:
            await engine.close()
        write_json(
            output / "reproduction.json",
            {
                **binding,
                "status": "FAIL",
                "input_seq": engine.seq,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    runtime = args.runtime.resolve()
    inventory = json.loads((runtime / "frozen-manifest.json").read_text())
    for name, expected in inventory.items():
        if file_hash(runtime / name) != expected:
            raise ValueError("ORIGINAL_FROZEN_SOURCE_CHANGED:" + name)
    sys.path.insert(0, str(runtime / "src"))
    sys.path.insert(0, str(runtime / "extension"))
    result = asyncio.run(reproduce(args.source.resolve(), runtime, args.output.resolve()))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
