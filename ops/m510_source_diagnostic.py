"""Observe source-order rejections without changing the engine's decisions."""

import argparse
import asyncio
import gzip
import hashlib
import json
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path


async def run(args):
    import m511_sim
    from maker_signals.feed import ResearchFeed

    from polymaker.m1234_sim.events import encode

    member = json.loads(args.manifest.read_text())["markets"][0]
    args.output.mkdir(parents=True, exist_ok=False)
    counts, examples = Counter(), []
    engine = m511_sim.ResearchOnlyEngine(args.output / member["market"],
        execution_model="legacy", signal_variant="m5-m8-frequency-v1")

    class ObservedFeed(ResearchFeed):
        def source(self, token, row):
            before = self.sources.get(token)
            previous = getattr(self, "previous_source_metadata", {}).get(token)
            observation = None
            try:
                stamp = float(Decimal(str(row["timestamp"]))/1000)
                age_valid = -.002-1e-9 <= self.wall-stamp <= .500+1e-9
                if before is not None and age_valid and stamp < before-1e-9:
                    book = self.books[token]
                    kind = row.get("event_type")
                    same_bbo = (kind == "best_bid_ask"
                                and Decimal(row["best_bid"]) == book.bid
                                and Decimal(row["best_ask"]) == book.ask)
                    lower, upper = engine.bounds()
                    observation = {"input_seq": self.seq, "subindex": self.subindex,
                        "token": token, "origin_token": row.get("asset_id"),
                        "kind": kind, "source": stamp, "previous_source": before,
                        "regression_ms": (before-stamp)*1000,
                        "previous": previous, "book_complete_before": book.complete,
                        "good_before": self.good[token], "same_bbo_before": same_bbo,
                        "elapsed_bounds": [lower, upper], "within_T15_T240": lower >= 15 and upper < 240,
                        "source_hash": row.get("hash")}
            except (KeyError, ValueError, TypeError, ArithmeticError):
                pass  # Only the original implementation decides malformed input.
            accepted = super().source(token, row)
            if accepted:
                if not hasattr(self, "previous_source_metadata"):
                    self.previous_source_metadata = {}
                self.previous_source_metadata[token] = {
                    "kind": row.get("event_type"), "input_seq": self.seq,
                    "source": self.sources[token], "origin_token": row.get("asset_id")}
            elif observation:
                category = "/".join((str(observation["kind"]), str((previous or {}).get("kind")),
                    "same-bbo" if observation["same_bbo_before"] else "different-or-not-bbo",
                    "complete" if observation["book_complete_before"] else "incomplete"))
                counts[category] += 1
                if observation["within_T15_T240"]:
                    counts["within_T15_T240/"+category] += 1
                    if len(examples) < 200:
                        examples.append(observation)
            return accepted

    m511_sim.ResearchFeed = ObservedFeed
    implementation = {str(p.relative_to(args.source_root)): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in (args.source_root / "src/polymaker").rglob("*.py")}
    with gzip.open(member["input"], "rt") as stream:
        for line in stream:
            event = json.loads(line)
            if event["kind"] == "INIT":
                event["definition"]["settings"].update(implementation=implementation,
                    m5_m11=m511_sim.definition(only_m5_m10=True, execution_model="legacy",
                                             signal_variant="m5-m8-frequency-v1"))
            await engine.ingest(event)
    state = json.loads(encode(engine.snapshot()))
    original = json.loads((args.baseline / member["market"] / "replay-state.json").read_text())
    equivalence = {k: original[k] == state[k] for k in ("brokers", "coverage", "marks", "resolution")}
    await engine.close()
    result = {"status": "OBSERVATION_COMPLETED" if all(equivalence.values()) else "INSTRUMENTATION_DIVERGED",
        "scope": "one frozen complete recorded market; observational diagnostics, no new signal acceptance rule",
        "market": member["market"], "economic_and_coverage_equivalence": equivalence,
        "source_order_rejections": counts, "examples": examples,
        "limitations": "A matching BBO alone does not prove lower depth or book/trade source ordering; no quality guard has been relaxed."}
    (args.output / "diagnosis.json").write_text(json.dumps(result, indent=2)+"\n")
    print(json.dumps({k:v for k,v in result.items() if k != "examples"}, indent=2), flush=True)
    if not all(equivalence.values()):
        raise RuntimeError("SOURCE_DIAGNOSTIC_CHANGED_ECONOMIC_OR_COVERAGE_RESULTS")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("source-root", "extension-dir", "manifest", "baseline", "output"):
        p.add_argument("--"+name, type=Path, required=True)
    args = p.parse_args()
    args.source_root = args.source_root.resolve()
    sys.path[:0] = [str(args.extension_dir.resolve()), str(args.source_root / "src")]
    asyncio.run(run(args))
