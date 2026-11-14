"""Offline, original-clock M5–M10 replay; never starts a service or network read."""

import argparse
import asyncio
import gc
import gzip
import hashlib
import inspect
import json
import resource
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path


def write(path, value):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")
    temp.replace(path)


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


async def supplement_settlement(engine, member):
    """Apply a separately frozen public result only after the recorded END."""
    if engine.resolution is not None:
        if member.get("winner") and engine.resolution["winner"] != member["winner"]:
            raise ValueError("FROZEN_SETTLEMENT_CONFLICT")
        return
    proof = member.get("official_result")
    if not engine.ended or not proof or not isinstance(proof.get("raw"), dict):
        raise ValueError("ENDED_REPLAY_REQUIRES_FROZEN_OFFICIAL_RESULT")
    observed = proof.get("observed_at")
    observed_wall = datetime.fromisoformat(observed).timestamp() if isinstance(observed, str) else engine.clock.wall+1
    wall = max(engine.clock.wall+1, observed_wall)
    await engine.ingest({"schema": engine.definition['input_schema'],
                         "seq": engine.seq+1, "kind": "SETTLEMENT",
                         "available_mono": engine.clock.mono+wall-engine.clock.wall,
                         "available_wall": wall, "raw": proof["raw"]})
    if engine.resolution is None or engine.resolution["winner"] != member.get("winner"):
        raise ValueError("FROZEN_OFFICIAL_RESULT_NOT_VERIFIED")


async def replay(args):
    import m511_sim
    from m511_sim import ResearchOnlyEngine, definition

    from polymaker.m1234_sim import runner
    from polymaker.m1234_sim.events import encode

    loaded = {"m511_sim": str(Path(m511_sim.__file__).resolve()),  # noqa: ASYNC240 -- Offline source identity.
              "runner": str(Path(runner.__file__).resolve())}  # noqa: ASYNC240 -- Offline source identity.
    if (Path(loaded["m511_sim"]) != args.extension_dir.resolve() / "m511_sim.py"
            or args.source_root.resolve() != runner.ROOT):
        raise RuntimeError("REPLAY_FROZEN_SOURCE_IMPORT_MISMATCH")
    frequency_file = getattr(args, "frequency_parameters", None)
    frequency_bytes = frequency_file.read_bytes() if frequency_file else None
    frequency_values = json.loads(frequency_bytes) if frequency_file else None
    frequency_sha = hashlib.sha256(frequency_bytes).hexdigest() if frequency_file else None
    frequency_kwargs = {"frequency_parameters": frequency_values} if frequency_file else {}
    groups = getattr(args, "strategies", None)
    if groups is not None:
        frequency_kwargs["enabled_strategies"] = groups
    if frequency_file:
        if "frequency_parameters" not in inspect.signature(ResearchOnlyEngine).parameters:
            raise RuntimeError("REPLAY_SOURCE_DOES_NOT_SUPPORT_FREQUENCY_PARAMETERS")
        definition(args.signal_variant, only_m5_m10=True,
                   execution_model=args.execution, **frequency_kwargs)
    manifest_bytes = args.manifest.read_bytes()
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    manifest = json.loads(manifest_bytes)
    rows = manifest["markets"] if isinstance(manifest, dict) else manifest
    rows = rows[:args.limit] if args.limit else rows
    args.output.mkdir(parents=True, exist_ok=False)
    write(args.output / "import-proof.json", {"status": "MATCH", "loaded_modules": loaded,
          "expected_source_root": str(args.source_root.resolve()),
          "expected_extension": str(args.extension_dir.resolve())})
    implementation = {str(p.relative_to(args.source_root)): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in (args.source_root / "src/polymaker").rglob("*.py")}
    run_id = hashlib.sha256((str(time.time_ns()) + str(args.output)).encode()).hexdigest()
    settings = None
    result, retained = [], []
    started = time.monotonic()
    for index, member in enumerate(rows):
        tape = Path(member["input"])
        if member.get("sha256") and sha(tape) != member["sha256"]:
            raise ValueError("FROZEN_INPUT_CHANGED")
        kwargs = ({"execution_model": args.execution, "signal_variant": args.signal_variant}
                  if "execution_model" in inspect.signature(ResearchOnlyEngine).parameters else {})
        kwargs.update(frequency_kwargs)
        engine = ResearchOnlyEngine(args.output / member["market"], **kwargs)
        engine.store.run_owner = run_id
        begin = time.monotonic()
        with gzip.open(tape, "rt") as stream:
            for line in stream:
                event = json.loads(line)
                if event["kind"] == "INIT":
                    cfg = event["definition"]["settings"]
                    cfg["implementation"] = implementation
                    cfg["m5_m11"] = definition(only_m5_m10=True, **(
                        {"signal_variant": args.signal_variant, "execution_model": args.execution}
                        if kwargs else {}), **frequency_kwargs)
                    if args.execution == "trade-through":
                        from m510_trade_through import SCENARIO_SETTINGS
                        cfg["scenarios"] = SCENARIO_SETTINGS
                    settings = cfg
                await engine.ingest(event)
        if getattr(args, "allow_incomplete", False):
            if engine.coverage["complete_window"] != member["complete_window"]:
                raise ValueError("FROZEN_WINDOW_COVERAGE_CHANGED:"+member["market"])
        if engine.ended and not engine.resolution and member.get("official_result"):
            await supplement_settlement(engine, member)
        if (not engine.ended or not engine.resolution or
                not engine.coverage["complete_window"] and not getattr(args, "allow_incomplete", False)):
            raise ValueError("COMPLETE_SETTLED_TAPE_REQUIRED:" + member["market"])
        state = json.loads(encode(engine.snapshot()))
        counts = Counter()
        for strategy, _payload in engine.store.db.execute(
            "SELECT json_extract(payload,'$.strategy'),json_extract(payload,'$.payload.id') "
            "FROM audit WHERE kind='SIGNAL' AND json_extract(payload,'$.event')='CANDIDATE'"
        ):
            counts[strategy] += 1
        await engine.close()
        write(engine.directory / "replay-state.json", state)
        if args.release and hasattr(engine, "retire_runtime"):
            engine.brokers.clear()
            engine.last_books = None
            engine.marks.clear()
            engine.mark_targets.clear()
            engine.tick_verification = None
            engine.retire_runtime()
        retained.append(engine)
        del state
        # Current retirement owns its collection, just like the coordinator.
        # Older frozen comparison sources need the explicit replay fallback.
        if not hasattr(engine, "retirement_gc_seconds"):
            gc.collect()
        rss = int(Path("/proc/self/statm").read_text().split()[1]) * 4096 / 1024**2  # noqa: ASYNC240 -- Offline resource sample.
        row = {"index": index + 1, "market": member["market"], "input_seq": engine.seq,
               "complete_window": engine.coverage["complete_window"],
               "signals": dict(counts), "seconds": time.monotonic()-begin,
               "rss_mib": rss, "peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,
               "retained_engines": len(retained), "retained_research_graphs": sum(
                   getattr(e, "research_extension", None) is not None for e in retained),
               "retirement_gc_seconds": getattr(engine, "retirement_gc_seconds", None),
               "retirement_collected_objects": getattr(engine, "retirement_collected_objects", None),
               "retirement_release_seconds": getattr(engine, "retirement_release_seconds", None),
               "retirement_release_cpu_seconds": getattr(engine, "retirement_release_cpu_seconds", None)}
        result.append(row)
        write(args.output / "progress.json", {"markets": result, "seconds": time.monotonic()-started})
        print(json.dumps(row), flush=True)
        if rss > 512:
            raise RuntimeError("REPLAY_MEMORY_LIMIT_EXCEEDED")
    write(args.output / "plan.json", {"mode": "SIMULATION_ONLY", "real_order_transport": "ABSENT",
          "run_identity": run_id, "settings": settings, "source_manifest": str(args.manifest),
          "source_manifest_sha256": manifest_sha,
          "frequency_parameters_source": str(frequency_file.resolve()) if frequency_file else None,
          "frequency_parameters_input_sha256": frequency_sha,
          "execution": args.execution, "realtime_latency_validation": False})
    write(args.output / "completion.json", {"status": "PASS", "markets": len(result),
          "run_identity": run_id,
          "complete_windows": sum(r['complete_window'] for r in result),
          "incomplete_markets": [r['market'] for r in result if not r['complete_window']],
          "data_scope": "Original complete and explicitly labelled incomplete windows; unknown intervals retained",
          "seconds": time.monotonic()-started, "memory_release": args.release,
          "replay_clock": "ORIGINAL_RECORDED_AVAILABILITY", "real_orders": 0})


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--source-root", type=Path, required=True)
    p.add_argument("--extension-dir", type=Path)
    p.add_argument("--execution", choices=("legacy", "trade-through"), default="legacy")
    p.add_argument("--limit", type=int)
    p.add_argument("--signal-variant")
    p.add_argument("--frequency-parameters", type=Path,
                   help="JSON overrides for M7/M9/M10; embedded in the immutable definition")
    p.add_argument("--release", action="store_true")
    p.add_argument("--strategies", nargs="+", choices=("M5", "M6", "M7", "M8", "M9", "M10"))
    p.add_argument("--allow-incomplete", action="store_true",
                   help="Retain manifest-labelled incomplete windows without upgrading coverage")
    a = p.parse_args()
    a.source_root = a.source_root.resolve()
    ext = a.extension_dir.resolve() if a.extension_dir else a.source_root / "ops"
    sys.path[:0] = [str(ext), str(ext / "maker_signals" / ".."), str(a.source_root / "research"),
                   str(a.source_root / "src")]
    asyncio.run(replay(a))


if __name__ == "__main__":
    main()
