"""Offline raw-tape parity with frozen v7; never instantiates a gateway."""

import argparse
import gzip
import hashlib
import json
import time
from collections import deque
from pathlib import Path
from types import SimpleNamespace

from polymaker.config import RDPObiConfig
from polymaker.domain import MarketMeta, TokenMeta
from polymaker.m6_baseline._signal_v7.feed import ResearchFeed
from polymaker.m6_baseline._signal_v7.strategies import M5_M10_LOCAL_AP, Strategies
from polymaker.m6_baseline.control import atomic_json
from polymaker.m6_baseline.signals import M6Feed


def replay(directory):
    definition = json.loads((directory / "definition.json").read_text())
    raw_meta = definition["market"]
    meta = MarketMeta(**{**raw_meta, "tokens": tuple(TokenMeta(**t) for t in raw_meta["tokens"])})
    clock = SimpleNamespace(now=0.0, wall=0.0)
    baseline = M6Feed(meta, RDPObiConfig(), monotonic=lambda: clock.now, wall=lambda: clock.wall)
    baseline.verify_ticks(definition["ticks"])
    actual, expected = [], []

    def canonical(candidate, context):
        dependencies = [context.registry.facts[k] for k in candidate["primitive_ids"]]
        return {
            "token": candidate["token"],
            "price": candidate["anchor_price"],
            "confirmed": candidate["confirmed"],
            "deadline": candidate["deadline"],
            "dependencies": [(f.kind, f.start, f.end, str(f.anchor)) for f in dependencies],
        }

    def reference_emit(kind, payload):
        if kind == "SIGNAL" and payload["strategy"] == "M6" and payload["event"] == "CANDIDATE":
            expected.append(canonical(payload["payload"], reference))

    reference = ResearchFeed(
        meta.condition_id,
        tuple(t.token_id for t in meta.tokens),
        definition["ticks"],
        reference_emit,
    )
    Strategies(
        reference,
        dict(zip(reference.tokens, ("YES", "NO"), strict=True)),
        signal_variant=M5_M10_LOCAL_AP,
        parent_filters=False,
    )

    def baseline_emit(payload):
        if payload["event"] == "CANDIDATE":
            evidence = payload["evidence"]["payload"]
            actual.append(canonical(evidence, baseline.research))
        return True

    baseline.m1.record = baseline_emit
    began = time.monotonic()
    counts = {}
    path = directory / "input.jsonl.gz"
    with gzip.open(path, "rt") as stream:
        for line in stream:
            event = json.loads(line)
            clock.now, clock.wall = event["available_mono"], event["available_wall"]
            kind = event["kind"]
            counts[kind] = counts.get(kind, 0) + 1
            pending = event.get("pending", [])
            baseline.m1_pending_received = deque(pending)
            if kind == "CONNECT":
                baseline.connect()
                reference.connect(now=clock.now, wall=clock.wall, seq=event["seq"])
            elif kind == "FRAME":
                baseline.apply(
                    event["raw"],
                    received_mono=event["received_mono"],
                    received_wall=event["received_wall"],
                )
                reference.apply(
                    event["raw"],
                    now=clock.now,
                    wall=clock.wall,
                    received=event["received_mono"],
                    seq=event["seq"],
                    pending=pending,
                )
            elif kind == "TIMER":
                if baseline.connected:
                    baseline.m1.tick()
                reference.timer(now=clock.now, wall=clock.wall, seq=event["seq"], pending=pending)
            elif kind == "GAP":
                baseline.invalidate(event["reason"])
                reference.gap(event["reason"])
            elif kind == "TICKS":
                baseline.verify_ticks(event["ticks"])
                for token in event["ticks"]:
                    reference.books[token].tick = baseline.research.books[token].tick
                    reference.books[token].tick_verified = True
    assert actual == expected, (
        f"M6_SIGNAL_PARITY_FAILED actual={len(actual)} expected={len(expected)}"
    )
    return {
        "market": meta.slug,
        "status": "PASS",
        "events": counts,
        "reference_candidates": len(expected),
        "baseline_candidates": len(actual),
        "candidates": actual,
        "input_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "elapsed_sec": time.monotonic() - began,
        "network_requests": 0,
        "scope": "Exact semantic candidate parity excluding run-local fact IDs; submission/fill policy intentionally separate",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market-directory", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for directory in args.market_directory:
        result = replay(directory)
        atomic_json(args.output / (directory.name + ".json"), result)
        print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
