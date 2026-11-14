#!/usr/bin/env python3
"""Follow the existing public recorder without stopping or altering its services.

This is a causal tape-following simulation, not a measurement of live submission
latency. Original availability times drive both retained and research accounts.
"""

import argparse
import asyncio
import hashlib
import json
import shutil
import signal
import sys
import time
import zlib
from pathlib import Path


def write(path, value):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")
    tmp.replace(path)


class Tail:
    def __init__(self, path):
        self.path = path
        self.file = path.open("rb")
        self.z = zlib.decompressobj(31)
        self.compressed = b""
        self.buffer = b""
        self.eof = False
        self.offset = 0
        self.sha = hashlib.sha256()

    def batch(self, limit=512):
        rows = []
        while len(rows) < limit:
            if b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                rows.append(json.loads(line))
                continue
            if not self.compressed:
                self.compressed = self.file.read(32768)
                self.offset += len(self.compressed)
                self.sha.update(self.compressed)
                if not self.compressed:
                    break
            if self.z.eof:
                self.z = zlib.decompressobj(31)
            data = self.z.decompress(self.compressed, 1048576)
            self.compressed = self.z.unused_data if self.z.eof else self.z.unconsumed_tail
            self.buffer += data
        self.eof = (
            self.z.eof
            and not self.buffer
            and not self.compressed
            and self.offset == self.path.stat().st_size
        )
        return rows

    def close(self):
        self.file.close()


async def follow(args):
    from m511_sample_quality import own_market_gate
    from m511_sim import ResearchEngine, definition
    from maker_signals.strategies import FOLLOW_SIGNAL_VARIANTS

    from polymaker.m1234_sim.events import digest, encode
    from polymaker.m1234_sim.report import publish, read_state, summary
    from polymaker.m1234_sim.validity import database_validity

    source = args.source_run.resolve()
    out = args.output.resolve()
    root = args.source_root.resolve()
    project = next(p for p in source.parents if (p / "ops/m511_sample_quality.py").exists())
    signal_variant = getattr(args, "signal_variant", None)
    if signal_variant is not None:
        if args.min_epoch is None or args.min_epoch < 0 or args.min_epoch % 300:
            raise ValueError("SIGNAL_VARIANT_REQUIRES_ALIGNED_START_MARKET")
        if args.once:
            if args.max_epoch is None or args.min_epoch > args.max_epoch:
                raise ValueError("SIGNAL_VARIANT_REQUIRES_BOUNDED_ONE_SHOT_REPLAY")
        elif signal_variant not in FOLLOW_SIGNAL_VARIANTS:
            raise ValueError("SIGNAL_VARIANT_REQUIRES_BOUNDED_ONE_SHOT_REPLAY")
    research_definition = definition(signal_variant)
    if out == source or source in out.parents or out in source.parents:
        raise ValueError("OUTPUT_MUST_BE_INDEPENDENT")
    source_plan = json.loads((source / "plan.json").read_text())
    if source_plan.get("real_order_transport") != "ABSENT":
        raise ValueError("PUBLIC_SIMULATION_ONLY_REQUIRED")
    identity = json.loads((source / "launch/implementation.json").read_text())
    actual = {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (root / "src/polymaker").rglob("*.py")
    }
    differences = [k for k in set(identity) | set(actual) if identity.get(k) != actual.get(k)]
    if any(k != "src/polymaker/m1234_sim/report.py" for k in differences):
        raise ValueError("BASELINE_SOURCE_CHANGED:" + str(differences))
    run_id = digest(
        {
            "source": source_plan["run_identity"],
            "definition": research_definition,
            "root": actual,
            "created": time.time(),
        }
    )
    if (out / "plan.json").exists():
        raise ValueError("NEW_RUN_DIRECTORY_REQUIRED")
    out.mkdir(parents=True, exist_ok=True)
    write(
        out / "plan.json",
        {
            "mode": "SIMULATION_ONLY",
            "real_order_transport": "ABSENT",
            "run_identity": run_id,
            "source_run": str(source),
            "source_run_identity": source_plan["run_identity"],
            "m5_m11": research_definition,
            "clock_mode": "SOURCE_RECORDED_AVAILABILITY",
            "realtime_latency_validation": False,
            "unchanged_source_implementation_except_report": differences,
            "created_at": time.time(),
            "started_utc_epoch": source_plan["started_utc_epoch"],
            "max_seconds": source_plan["max_seconds"],
            "minimum_market_epoch": args.min_epoch,
            "maximum_market_epoch": args.max_epoch,
        },
    )
    live = {}
    done = {}
    reports = {}
    published_market_count = 0
    stop = False
    last_report = 0
    last_scan = 0

    def stopping(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, stopping)
    signal.signal(signal.SIGTERM, stopping)
    try:
        while not stop:
            now = time.time()
            progress = False
            if now - last_scan >= 1:
                last_scan = now
                for tape in sorted(source.glob("btc-*/input.jsonl.gz")):
                    name = tape.parent.name
                    epoch = int(name.rsplit("-", 1)[1])
                    if args.min_epoch is not None and epoch < args.min_epoch:
                        continue
                    if args.max_epoch is not None and epoch > args.max_epoch:
                        continue
                    if name in live or name in done:
                        continue
                    if (tape.with_suffix(".failure.json")).exists():
                        done[name] = {"status": "SOURCE_RECORDING_FAILED"}
                        continue
                    live[name] = {
                        "tail": Tail(tape),
                        "engine": None,
                        "last_wall": None,
                        "read_seq": 0,
                    }
            for name, member in list(live.items()):
                tail = member["tail"]
                events = tail.batch()
                if events:
                    progress = True
                for event in events:
                    if event["seq"] != member["read_seq"] + 1:
                        raise ValueError("SOURCE_SEQUENCE_GAP:" + name)
                    member["read_seq"] = event["seq"]
                    member["last_wall"] = event["available_wall"]
                    if event["kind"] == "INIT":
                        original = json.loads(encode(event["definition"]))
                        if original["settings"]["implementation"] != identity:
                            raise ValueError("SOURCE_INIT_IDENTITY_CHANGED")
                        member["engine"] = ResearchEngine(
                            out / name, derive=True, signal_variant=signal_variant
                        )
                        member["engine"].store.run_owner = run_id
                        write(
                            out / name / "source-binding.json",
                            {
                                "input": str(tail.path),
                                "original_definition": original,
                                "derivation": "Add M5–M11, preserve original timing and all legacy execution; report source only patched",
                            },
                        )
                        event["definition"]["settings"]["implementation"] = actual
                    await member["engine"].ingest(event)
                engine = member["engine"]
                if engine and now - member.get("gate_checked", 0) >= 1:
                    gate = own_market_gate(project, name)
                    member["gate_checked"] = now
                    member["gate"] = gate
                    engine.store.db.execute(
                        "INSERT OR REPLACE INTO metadata VALUES('research_sample_eligibility',?)",
                        (encode(gate),),
                    )
                if engine and events:
                    engine.store.commit()
                if tail.eof and engine:
                    original_state = read_state(tail.path.parent / "simulation.sqlite3")
                    valid = database_validity(
                        tail.path.parent / "simulation.sqlite3", original_state
                    )
                    if original_state["input_seq"] < member["read_seq"]:
                        continue
                    if not engine.ended:
                        engine.invalidate(
                            RuntimeError("SEALED_SOURCE_WITHOUT_END"), "SOURCE_REVIEW"
                        )
                        await engine.end_observation({"reason": "SOURCE_INCOMPLETE"})
                    if valid["status"] != "VALID":
                        engine.invalidate(RuntimeError("SOURCE_MARKET_INVALID"), "SOURCE_REVIEW")
                    legacy_equal = all(
                        json.loads(encode(engine.brokers[k].snapshot())) == v
                        for k, v in original_state["brokers"].items()
                    )
                    if valid["status"] == "VALID" and not legacy_equal:
                        raise ValueError("LEGACY_BROKER_DIVERGED:" + name)
                    await engine.close()
                    reports[name] = summary(out / name / "simulation.sqlite3")
                    gate = member.get("gate", {"status": "UNAVAILABLE"})
                    reports[name]["research_sample_eligibility"] = gate
                    if gate["status"] != "ELIGIBLE":
                        reports[name]["excluded_research_accounts"] = [
                            a
                            for a in reports[name]["accounts"]
                            if a["scenario"].startswith("research/")
                        ]
                        reports[name]["accounts"] = [
                            a
                            for a in reports[name]["accounts"]
                            if not a["scenario"].startswith("research/")
                        ]
                    done[name] = {
                        "status": valid["status"],
                        "input_seq": member["read_seq"],
                        "input_sha256": tail.sha.hexdigest(),
                        "input_bytes": tail.offset,
                        "legacy_brokers_identical": legacy_equal,
                        "confirmed_signal_counts": dict(engine.research_extension.research.counts),
                        "completed_at": time.time(),
                    }
                    write(out / name / "replay-validation.json", done[name])
                    tail.close()
                    del live[name]
                    print(
                        json.dumps({"event": "MARKET_VERIFIED", "market": name, **done[name]}),
                        flush=True,
                    )
            if time.time() - last_report >= 10:
                if shutil.disk_usage(out).free < 4 * 1024**3:
                    raise RuntimeError("RESEARCH_FREE_SPACE_RESERVE_4_GIB")
                last_report = time.time()
                active = {
                    name: {
                        "processed_input_seq": m["read_seq"],
                        "source_wall": m["last_wall"],
                        "following_lag_seconds": None
                        if m["last_wall"] is None
                        else max(0, time.time() - m["last_wall"]),
                        "source_bytes_read": m["tail"].offset,
                        "source_bytes_available": m["tail"].path.stat().st_size,
                    }
                    for name, m in live.items()
                }
                write(
                    out / "status.json",
                    {
                        "status": "FOLLOWING",
                        "observed_at": time.time(),
                        "source_run": str(source),
                        "clock_mode": "SOURCE_RECORDED_AVAILABILITY",
                        "active": active,
                        "completed": done,
                        "real_orders": 0,
                        "legacy_services_changed": False,
                    },
                )
                # Publishing revalidates every market's audit. Keep the
                # heartbeat independent of that work when results are unchanged.
                if len(reports) != published_market_count:
                    publish(out, list(reports.values()))
                    published_market_count = len(reports)
            source_terminal = source / "completion.json"
            if source_terminal.exists() and live and not progress:
                raise RuntimeError("SOURCE_TERMINATED_WITH_UNSEALED_MEMBERS")
            if not live and (source_terminal.exists() or args.once):
                break
            if args.once and live and not progress:
                break
            if not progress:
                await asyncio.sleep(0.25)
        if len(reports) != published_market_count:
            publish(out, list(reports.values()))
        write(
            out / "completion.json",
            {
                "status": "STOPPED_BY_OPERATOR"
                if stop
                else "CAUGHT_UP"
                if args.once
                else "SOURCE_COMPLETED",
                "run_identity": run_id,
                "invalid_markets": [
                    name for name, value in done.items() if value["status"] != "VALID"
                ],
                "observed_at": time.time(),
                "completed_markets": len(done),
                "unfinished_markets": list(live),
                "continuous_24h_claim": False,
                "real_orders": 0,
            },
        )
    except BaseException as error:
        write(
            out / "failure.json",
            {
                "status": "FAIL",
                "error": type(error).__name__ + ":" + str(error),
                "observed_at": time.time(),
            },
        )
        raise
    finally:
        for member in live.values():
            member["tail"].close()
            if member["engine"]:
                await member["engine"].close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("source-run", "source-root", "output"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--once", action="store_true")
    p.add_argument("--min-epoch", type=int)
    p.add_argument("--max-epoch", type=int)
    research = Path(__file__).resolve().parents[1] / "research"
    if research.is_dir():
        sys.path.insert(0, str(research))
    from maker_signals.strategies import SIGNAL_VARIANTS

    p.add_argument("--signal-variant", choices=SIGNAL_VARIANTS)
    a = p.parse_args()
    sys.path.insert(0, str(a.source_root.resolve() / "src"))
    asyncio.run(follow(a))


if __name__ == "__main__":
    main()
