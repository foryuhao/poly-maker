"""Artificial accelerated native lifecycle validation, never a real-time run."""

import argparse
import asyncio
import hashlib
import inspect
import json
import resource
import sys
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--extension-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tests-dir", type=Path, required=True)
    parser.add_argument("--signal-variant")
    parser.add_argument("--frequency-parameters", type=Path)
    args = parser.parse_args()
    root, ext, out = args.source_root.resolve(), args.extension_dir.resolve(), args.output.resolve()
    sys.path[:0] = [str(ext), str(root / "src"), str(args.tests_dir.resolve())]
    import m511_sim
    import pytest

    from polymaker.m1234_sim import runner
    from test_m510_memory import test_native_research_retirement_and_deferred_settlements

    loaded = {"m511_sim": str(Path(m511_sim.__file__).resolve()),
              "runner": str(Path(runner.__file__).resolve())}
    if Path(loaded["m511_sim"]) != ext / "m511_sim.py" or root != runner.ROOT:
        raise RuntimeError("NATIVE_FROZEN_SOURCE_IMPORT_MISMATCH")

    options = {}
    frequency_bytes = args.frequency_parameters.read_bytes() if args.frequency_parameters else None
    frequency_values = json.loads(frequency_bytes) if frequency_bytes is not None else None
    frequency_sha = hashlib.sha256(frequency_bytes).hexdigest() if frequency_bytes is not None else None
    if args.signal_variant is not None or args.frequency_parameters:
        if "frequency_parameters" not in inspect.signature(
                test_native_research_retirement_and_deferred_settlements).parameters:
            raise RuntimeError("NATIVE_FIXTURE_DOES_NOT_SUPPORT_FREQUENCY_CONFIGURATION")
        m511_sim.definition(args.signal_variant, only_m5_m10=True,
                            frequency_parameters=frequency_values)
        options = {"signal_variant": args.signal_variant, "frequency_parameters": frequency_values}

    out.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    monkeypatch = pytest.MonkeyPatch()
    try:
        asyncio.run(test_native_research_retirement_and_deferred_settlements(
            out, monkeypatch, False, 288, **options))
        terminal = json.loads((out / "run/completion.json").read_text())
        samples = json.loads((out / "run/resources.json").read_text())["samples"]
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        assert terminal["status"] == "PASS" and terminal["complete_windows"] == 288
        assert samples and peak <= 512
        assert max(r["retired_research_graphs"] for r in samples) == 0
        assert max(r["open_databases"] for r in samples) <= 3
        assert max(r["active_recorders"] for r in samples) <= 3
        result = {"status": "PASS", "scope": "ARTIFICIAL_ACCELERATED_NATIVE_COORDINATOR_LIFECYCLE",
            "markets": 288, "rich_artificial_windows": 2, "minimal_artificial_windows": 286,
            "continuous_live_24h": False, "actual_wall_seconds": time.monotonic()-started,
            "peak_rss_mib": peak, "resource_samples": len(samples),
            "maximum_retired_research_graphs": 0,
            "maximum_open_databases": max(r["open_databases"] for r in samples),
            "maximum_active_recorders": max(r["active_recorders"] for r in samples),
            "maximum_retirement_gc_seconds": max(r["retirement_gc_max_seconds"] for r in samples),
            "maximum_retirement_release_seconds": max(
                r.get("retirement_release_max_seconds", 0) for r in samples),
            "maximum_retirement_release_cpu_seconds": max(
                r.get("retirement_release_cpu_max_seconds", 0) for r in samples),
            "source_root": str(root), "extension_directory": str(ext),
            "frequency_configuration": options,
            "frequency_parameters_input_sha256": frequency_sha,
            "loaded_modules": loaded}
    except BaseException as exc:
        result = {"status": "FAIL", "reason": type(exc).__name__+":"+str(exc),
                  "scope": "ARTIFICIAL_ACCELERATED_NATIVE_COORDINATOR_LIFECYCLE"}
        (out / "result.json").write_text(json.dumps(result, indent=2)+"\n")
        raise
    finally:
        monkeypatch.undo()
    (out / "result.json").write_text(json.dumps(result, indent=2)+"\n")
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
