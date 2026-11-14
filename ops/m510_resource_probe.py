"""Observe real-market memory watermarks without changing its input clock/rules."""

import argparse
import asyncio
import gc
import json
import resource
import sys
import time
from pathlib import Path
from types import SimpleNamespace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.source_root.resolve()
    sys.path[:0] = [str(root / "extension"), str(root / "src")]
    import m510_sim_replay
    import m511_sim

    samples = args.output.with_name(args.output.name+"-samples.jsonl")
    with samples.open("x") as stream:
        def sample(engine, phase):
            status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines()
                          if ":" in line)
            ext = engine.research_extension if engine is not None else None
            feed = ext.research if ext is not None else None
            row = {"phase": phase, "at": time.time(),
                   "input_seq": engine.seq if engine is not None else None,
                   "rss_mib": int(status["VmRSS"].split()[0])/1024,
                   "vmhwm_mib": int(status["VmHWM"].split()[0])/1024,
                   "rusage_peak_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,
                   "gc_counts": gc.get_count(),
                   "registry_facts": len(feed.registry.facts) if feed else 0,
                   "tracked_observers": len(feed.runtime_observers) if feed else 0,
                   "active_observers": len(feed.observers) if feed else 0,
                   "trade_identities": sum(len(b.seen_trades) for b in engine.brokers.values())
                       if engine is not None else 0}
            stream.write(json.dumps(row)+"\n")
            stream.flush()

        class ObservedEngine(m511_sim.ResearchOnlyEngine):
            async def _ingest(self, event):
                await super()._ingest(event)
                if self.seq % 5000 == 0 or event["kind"] in {"INIT", "END", "SETTLEMENT"}:
                    sample(self, event["kind"])

            def snapshot(self):
                sample(self, "snapshot-before")
                value = super().snapshot()
                sample(self, "snapshot-after")
                return value

            async def close(self, **kwargs):
                sample(self, "close-before")
                await super().close(**kwargs)
                sample(self, "close-after")

            def retire_runtime(self):
                sample(self, "release-before")
                super().retire_runtime()
                sample(self, "release-after")

        sample(None, "imports-complete")
        m511_sim.ResearchOnlyEngine = ObservedEngine
        asyncio.run(m510_sim_replay.replay(SimpleNamespace(
            manifest=args.manifest.resolve(), output=args.output.resolve(), source_root=root,
            extension_dir=root / "extension", execution="legacy",
            signal_variant="m5-m8-frequency-v1", release=True, limit=1)))


if __name__ == "__main__":
    main()
