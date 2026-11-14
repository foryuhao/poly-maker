"""Exercise the production12-market pending bound on the frozen native runner."""

import argparse
import asyncio
import json
import resource
import sys
from pathlib import Path


async def probe(output, monkeypatch):
    from m510_trade_through import SCENARIO_SETTINGS
    from m511_sim import ResearchOnlyEngine, definition

    from polymaker.m1234_sim import runner
    from polymaker.m1234_sim.report import read_state
    from test_m1234_sim_continuous import coordinator

    original = runner.LiveRun.__init__

    def init(self, directory, cfg):
        cfg.update(profiles={}, scenarios=SCENARIO_SETTINGS, max_pending_markets=12,
                   m5_m11=definition(only_m5_m10=True))
        original(self, directory, cfg)

    monkeypatch.setattr(runner.LiveRun, "__init__", init)
    monkeypatch.setattr(runner, "Engine", ResearchOnlyEngine)
    run = coordinator(output, monkeypatch, markets=16, unresolved=True)
    original_record = run.record

    async def record(engine, kind, **payload):
        # Match the native lifecycle fixture: legacy tick request target names
        # refer to M1/M234; no successful tick read or signal/fill is injected.
        if kind not in {"TICK_REQUEST", "TICK_RESULT"}:
            return await original_record(engine, kind, **payload)

    run.record = record
    terminal = await run.run()
    assert terminal["status"] == "PASS"
    assert run.cfg["max_pending_markets"] == 12 and len(run.pending) == 12
    assert len(terminal["deferred_settlements"]) == 4
    assert all(engine.store.closed for engine in run.engines)
    retired = [engine for engine in run.engines if getattr(engine, "retired", False)]
    assert len(retired) == 4 and all(engine.research_extension is None for engine in retired)
    assert all(read_state(engine.store.path)["resolution"] is None for engine in run.engines)
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024
    assert peak <= 512
    samples = run.resources
    assert samples and max(r["open_databases"] for r in samples) <= 13
    assert max(r["active_recorders"] for r in samples) <= 13
    return {"status": "PASS", "scope": "ARTIFICIAL_NATIVE_PRODUCTION_PENDING_BOUND",
            "markets": 16, "pending_bound": 12, "pending_states_preserved": 12,
            "deferred_states_preserved": 4, "official_settlements_still_pending": 16,
            "all_market_stores_closed_at_exit": True, "retired_research_graphs": 0,
            "peak_rusage_mib": peak,
            "maximum_sampled_open_market_stores": max(r["open_databases"] for r in samples),
            "maximum_sampled_active_recorders": max(r["active_recorders"] for r in samples),
            "counter_scope": "Market Store instances and active recorder threads at capacity sampling; transient backup/read-only SQLite handles are not included.",
            "limits": "Artificial input and intentionally delayed resolutions, not a24h or complete-settlement result. Partial unknown order preservation is covered separately by raw M10 fixture."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.source_root.resolve()
    sys.path[:0] = [str(root / "extension"), str(root / "src"), str(root / "tests")]
    import m511_sim
    import pytest

    from polymaker.m1234_sim import runner

    if root != runner.ROOT or Path(m511_sim.__file__).resolve() != root / "extension/m511_sim.py":
        raise RuntimeError("PENDING_PROBE_SOURCE_IMPORT_MISMATCH")
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    monkeypatch = pytest.MonkeyPatch()
    try:
        result = asyncio.run(probe(out, monkeypatch))
    except BaseException as error:
        result = {"status": "FAIL", "reason": type(error).__name__+":"+str(error)}
        (out / "result.json").write_text(json.dumps(result, indent=2)+"\n")
        raise
    finally:
        monkeypatch.undo()
    result["source_root"] = str(root)
    (out / "result.json").write_text(json.dumps(result, indent=2)+"\n")
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
