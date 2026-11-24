"""Bounded public forward run and fully offline event replay."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import resource
import shutil
import signal
import time
import tomllib
from collections import deque
from dataclasses import asdict
from functools import partial
from heapq import nsmallest
from itertools import chain
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx

from polymaker.config import RDPObiConfig
from polymaker.m1_virtual_api import VirtualPublicAPI
from polymaker.m2_config import M2Config
from polymaker.m1234_sim import INPUT_SCHEMA, MODEL
from polymaker.m1234_sim.broker import ConstraintConflict
from polymaker.m1234_sim.config import M1SimulationConfig
from polymaker.m1234_sim.engine import Engine
from polymaker.m1234_sim.events import Clock, Recorder, digest, read_events, write_json
from polymaker.m1234_sim.report import invalidated_report, publish, read_state, summary
from polymaker.m1234_sim.runstate import RUN_SCHEMA, failure_path, run_identity
from polymaker.m1234_sim.store import read_audit
from polymaker.m1234_sim.ticks import TICK_POLICY
from polymaker.m1234_sim.validity import EVIDENCE_SCHEMA, database_validity
from polymaker.rdp_sm_marketdata import SessionFeed

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = ROOT / "research_outputs/m1234_sim"


def validate_run_limits(cfg):
    continuous = cfg.get("continuous", False)
    if type(continuous) is not bool:
        raise ValueError("SIM_INVALID_CONTINUOUS_MODE")
    markets, seconds = cfg["max_markets"], cfg["max_seconds"]
    if (type(markets) is not int or type(seconds) is not int
            or not 0 < markets <= (288 if continuous else 12)
            or not 0 < seconds <= (87300 if continuous else 4500)):
        raise ValueError("SIM_RUN_BOUND_EXCEEDED")
    if continuous and seconds < markets * 300 + 300:
        raise ValueError("SIM_DURATION_MUST_ALLOW_WINDOWS_AND_ALIGNMENT")
    pending = cfg.get("max_pending_markets", 12)
    if type(pending) is not int or not 1 <= pending <= 12:
        raise ValueError("SIM_PENDING_MARKET_BOUND_EXCEEDED")


def directory_bytes(directory):
    return sum(p.stat().st_size for p in directory.rglob("*") if p.is_file())


def pending_watermark(values, *, current=None, compact=False):
    """Keep the same receipt frontier and post-frame head without copying a backlog.

    Policy guards use only emptiness and the oldest receipt, including after
    popping the current frame. The full count remains explicit for coverage.
    Old recordings with a complete pending vector remain valid inputs.
    """
    values = (v for v in values if current is None or v > current)
    if not compact:
        return {"pending": ([current] if current is not None else []) + sorted(values)}
    count = 0

    def counted():
        nonlocal count
        if current is not None:
            count += 1
            yield current
        for value in values:
            count += 1
            yield value

    heads = nsmallest(2, counted())
    return {"pending": heads, "pending_count": count}


class CaptureQueue(asyncio.Queue):
    """Track receipt heads with the bounded FIFO; never scan its full payloads."""

    def __init__(self, maxsize):
        super().__init__(maxsize)
        self.frame_receipts = deque()

    def _put(self, item):
        super()._put(item)
        kind, payload = item
        if kind == 'FRAME':
            self.frame_receipts.append(payload['received_mono'])

    def _get(self):
        item = super()._get()
        if item[0] == 'FRAME':
            self.frame_receipts.popleft()
        return item


def fifo_watermark(*queues, current=None):
    """Receipts in each native FIFO are monotonic, so only their heads matter."""
    heads = [] if current is None else [current]
    count = len(heads)
    for queue in queues:
        skipped, selected = 0, 0
        for value in queue:
            if current is not None and value <= current:
                skipped += 1
                continue
            heads.append(value)
            selected += 1
            if selected == 2:
                break
        count += len(queue) - skipped
    return {'pending': sorted(heads)[:2], 'pending_count': count}


async def cancel_owned(task):
    task.cancel()
    result, = await asyncio.gather(task, return_exceptions=True)
    if isinstance(result, Exception):
        raise result


def output_path(path):
    path = Path(path).resolve()
    if OUTPUT not in path.parents:
        raise ValueError("SIM_OUTPUT_MUST_BE_INSIDE_RESEARCH_OUTPUTS_M1234_SIM")
    return path


def settings(config, contract):
    cfg = tomllib.loads(Path(config).read_text())
    allowed = {
        "seed",
        "max_output_mib",
        "min_free_disk_mib",
        "max_rss_mib",
        "input_queue_frames",
        "writer_queue_records",
        "max_seconds",
        "max_markets",
        "markout_seconds",
        "scenarios",
        "continuous",
        "max_pending_markets",
    }
    if set(cfg) - allowed:
        raise ValueError("SIM_UNKNOWN_CONFIG_KEYS")
    validate_run_limits(cfg)
    for key in (
        "max_output_mib",
        "min_free_disk_mib",
        "max_rss_mib",
        "input_queue_frames",
        "writer_queue_records",
    ):
        if not isinstance(cfg[key], int) or cfg[key] <= 0:
            raise ValueError("SIM_RESOURCE_BUDGET_INVALID")
    frozen = json.loads(Path(contract).read_text())
    public = frozen["profiles"]
    cfg["profiles"] = {"M1-virtual": public["config"], "M1-live-mirror": public["config/m1-live"]}
    cfg["m234"] = public["m234"]
    cfg["contract_hash"] = digest(frozen)
    cfg["tick_reverification"] = dict(TICK_POLICY)
    cfg["model"] = MODEL
    cfg["input_schema"] = INPUT_SCHEMA
    cfg["implementation"] = {
        str(p.relative_to(ROOT)): _file_hash(p)
        for p in sorted((ROOT / "src/polymaker").rglob("*.py"))
    }
    return cfg


def preflight(config, contract):
    """Validate public-only settings and the full disk budget without starting a run."""
    cfg = settings(config, contract)
    for profile in cfg["profiles"].values():
        M1SimulationConfig.model_validate(profile)
    M2Config.model_validate(cfg["m234"])
    free = shutil.disk_usage(ROOT).free
    required = (cfg["max_output_mib"] + cfg["min_free_disk_mib"]) * 1024**2
    return {"status": "PASS" if free >= required else "FAIL",
            "mode": "SIMULATION_ONLY", "continuous": cfg.get("continuous", False),
            "markets": cfg["max_markets"], "observation_hours": cfg["max_markets"] / 12,
            "max_seconds": cfg["max_seconds"], "profiles": list(cfg["profiles"]) + ["M2", "M3", "M4"],
            "scenarios": list(cfg["scenarios"]), "available_disk_bytes": free,
            "required_disk_bytes": required, "max_output_mib": cfg["max_output_mib"],
            "min_free_disk_mib": cfg["min_free_disk_mib"], "max_rss_mib": cfg["max_rss_mib"],
            "contract_hash": cfg["contract_hash"], "implementation_hash": digest(cfg["implementation"]),
            "errors": [] if free >= required else ["SIM_INSUFFICIENT_PLANNED_DISK_BUDGET"],
            "real_orders": 0, "network_requests": 0}


class CaptureFeed(SessionFeed):
    """Reuse SessionFeed socket, heartbeat and reconnect protocol unchanged.

    The original socket consumer supplies raw receipts to a second bounded
    research queue. Actual processing time and both pending queues are recorded.
    No old observer or execution service is used as an input dependency.
    """

    def __init__(self, meta, target):
        super().__init__(meta, RDPObiConfig(), reader_yield_every=1)
        self.target = target
        self.m1 = SimpleNamespace(tick=lambda: None)
        self.failure = None

    def put(self, kind, **payload):
        try:
            self.target.put_nowait((kind, payload))
        except asyncio.QueueFull as exc:
            self.failure = "SIM_CAPTURE_QUEUE_OVERFLOW"
            self.stop_event.set()
            raise RuntimeError(self.failure) from exc

    def connect(self):
        self.connected = True
        self.generation += 1
        self.m1_pending_received.clear()
        self.put("CONNECT", transport_generation=self.generation)

    def invalidate(self, reason):
        self.connected = False
        self.put(
            "GAP",
            reason=reason,
            missing_since_received_mono=min(self.m1_pending_received, default=self.monotonic()),
        )

    def apply(self, raw, *, received_mono=None, received_wall=None):
        self.put(
            "FRAME",
            raw=raw.decode() if isinstance(raw, bytes) else raw,
            received_mono=received_mono,
            received_wall=received_wall,
        )


def source_failure(input_path):
    """Validate a recording package against its own adjacent database and failure facts."""
    input_path = Path(input_path)
    source = input_path.parent / "simulation.sqlite3"
    if not source.exists():
        source = input_path.parent / "snapshot.sqlite3"
    if not source.exists():
        return None
    state = read_state(source)
    first = next(iter(read_events(input_path)))
    if first["kind"] != "INIT" or digest(first["definition"]) != state["definition_hash"]:
        raise ValueError("SIM_RECOVERY_INPUT_DEFINITION_MISMATCH")
    validity = database_validity(source, state)
    if validity["status"] == "VALID":
        return None
    input_hash = _file_hash(input_path)
    failure_path = input_path.parent / "failure.json"
    if failure_path.exists():
        failure = json.loads(failure_path.read_text())
        if (failure.get("binding") == validity.get("evidence", {}).get("binding")
                and failure.get("input_hash") is not None and failure["input_hash"] != input_hash):
            raise ValueError("SIM_RECOVERY_INPUT_HASH_MISMATCH")
    return {"source_database": str(source.resolve()), "input_hash": input_hash,
            "snapshot_input_seq": state["input_seq"], "validity": validity}


def copy_replay_input(input_path, directory):
    target = Path(directory) / "input.jsonl.gz"
    if Path(input_path).resolve() != target.resolve():
        shutil.copyfile(input_path, target)


async def replay(input_path, directory):
    directory = output_path(directory)
    engine = Engine(directory)
    try:
        engine.source_failure = source_failure(input_path)
        if engine.source_failure is not None:
            raise ValueError("SIM_SOURCE_RUN_INVALID_OR_UNVERIFIED")
        copy_replay_input(input_path, directory)
        for event in read_events(input_path):
            await engine.ingest(event)
        if not engine.ended:
            engine.gap("RECORDING_ENDED_WITHOUT_END")
        state = engine.snapshot()
        await engine.close()
        write_json(directory / "state.json", state)
        report = summary(directory / "simulation.sqlite3")
        publish(directory, [report])
        result = {
            "status": report["status"],
            "validity": report["validity"],
            "evidence_schema": EVIDENCE_SCHEMA, "binding": engine.store.identity,
            "input_seq": engine.seq,
            "input_hash": _file_hash(input_path),
            "state_hash": digest(state),
            "audit_hash": digest(list(read_audit(directory / "simulation.sqlite3"))),
            "events": engine.seq,
            "directory": str(directory),
        }
        write_json(directory / "replay.json", result)
        return result
    except BaseException as exc:
        await failed_output(engine, directory, "OFFLINE_REPLAY", exc, input_path)
        raise


async def failed_output(engine, directory, mode, error, input_path):
    """Retain current failure even when the committed snapshot cannot be updated."""
    cleanup_errors = []

    def save(path, value):
        try:
            write_json(path, value)
            return True
        except Exception as exc:
            cleanup_errors.append(str(exc))
            return False

    if engine.definition is not None and engine.validity["status"] != "INVALID":
        try:
            engine.invalidate(error, mode)
        except Exception as exc:
            cleanup_errors.append(str(exc))
    tracker_saved = False
    if engine.definition is not None:
        try:
            engine.store.record_failure(engine.validity)
            tracker_saved = True
        except Exception as exc:
            cleanup_errors.append(str(exc))
    try:
        await engine.close()
    except Exception as exc:
        cleanup_errors.append(str(exc))
    validity = engine.validity
    if validity["status"] != "INVALID":
        validity = {**validity, "status": "INVALID", "failures": [{
            "id": digest({"input_seq": engine.seq, "reason": str(error)}),
            "input_seq": engine.seq, "reason": str(error), "input_kind": mode}]}
    try:
        input_hash = _file_hash(input_path)
    except OSError as exc:
        input_hash = None
        cleanup_errors.append("INPUT_HASH_UNAVAILABLE:" + str(exc))
    result = {
        "status": "FAIL", "mode": mode,
        "reason": validity["failures"][0]["reason"] if validity["failures"] else str(error),
        "trigger_error": str(error), "error_type": type(error).__name__,
        "input_seq": engine.seq, "validity": validity,
        "evidence_schema": EVIDENCE_SCHEMA, "binding": engine.store.identity,
        "source_run": engine.source_failure,
        "input_hash": input_hash,
        "cleanup_errors": cleanup_errors, "input": str(input_path), "output": str(directory),
        "diagnostic_basis": "Committed database prefix and separately bound current failure watermark",
    }
    failure_saved = save(directory / "failure.json", result)
    try:
        report = summary(engine.store.path)
        if report["validity"]["status"] == "VALID":
            raise ValueError("SIM_CURRENT_FAILURE_NOT_IN_COMMITTED_REPORT")
        publish(directory, [report])
    except Exception as exc:
        cleanup_errors.append(str(exc))
        save(directory / "latest.json", {
            "status": "FAIL", "validity": validity, "markets": [],
            "complete_windows": 0, "valid_complete_windows": 0,
            "input_seq": engine.seq, "binding": engine.store.identity,
            "diagnostic_error": str(exc), "cleanup_errors": cleanup_errors})
    filename = "replay.json" if mode == "OFFLINE_REPLAY" else "recovery.json"
    result_saved = save(directory / filename, result)
    if failure_saved:
        save(directory / "failure.json", result)
    if not (tracker_saved or failure_saved or result_saved):
        raise OSError("SIM_FAILURE_EVIDENCE_UNAVAILABLE:" + result["reason"]
                      + ":" + " | ".join(cleanup_errors))
    return result


def _file_hash(path):
    import hashlib

    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


class LiveRun:
    def __init__(self, directory, cfg):
        self.directory = output_path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        if (self.directory / "plan.json").exists():
            raise ValueError("SIM_RUN_ALREADY_EXISTS")
        self.cfg, self.stop = cfg, asyncio.Event()
        self.api = VirtualPublicAPI()
        self.engines, self.recorders, self.pending = [], {}, []
        self.resources = []
        self.stop_reason = "PLANNED_COMPLETE"
        self.started = time.monotonic()
        self.stop_at = self.started + cfg["max_seconds"]
        self.last_resource = 0
        self.clock = None
        self.clock_task = self.resolution_task = None
        self.resolution_due = 0
        self.report_cache, self.report_tasks = {}, {}
        self.cleanup_errors, self.cleanup_steps, self.cleanup_failures = [], [], []
        self.primary_error = None
        self.run_identity = run_identity(cfg, str(uuid4()))
        self.active_input = None
        self.tick_task = None
        self.retired_sizes = {}
        self.deferred_settlements = []
        self.logs = (self.directory / "runtime.jsonl").open("a")
        write_json(
            self.directory / "plan.json",
            {
                "run_schema": RUN_SCHEMA,
                "run_identity": self.run_identity,
                "mode": "SIMULATION_ONLY",
                "predeclared_max_markets": cfg["max_markets"],
                "max_seconds": cfg["max_seconds"],
                "target_complete_windows": cfg["max_markets"],
                "continuous": cfg.get("continuous", False),
                "warmup_before_t15": True,
                "new_orders": "[T15,T240)",
                "observation_until": "T300 minimum",
                "started_utc_epoch": time.time(),
                "settings": cfg,
                "settings_hash": digest(cfg),
                "real_order_transport": "ABSENT",
                "account_credentials": "NOT_LOADED",
            },
        )

    def log(self, kind, **payload):
        self.logs.write(
            json.dumps({"at": time.time(), "event": kind, **payload}, default=str) + "\n"
        )
        self.logs.flush()
        print(json.dumps({"event": kind, **payload}, default=str), flush=True)

    async def record(self, engine, kind, **payload):
        if getattr(engine, "retired", False):
            raise RuntimeError("SIM_RECORD_AFTER_RETIREMENT")
        now = Clock(time.monotonic(), time.time())
        if "pending" not in payload and self.active_input and self.active_input[0] is engine:
            _, feed, queue = self.active_input
            payload.update(self.capture_watermark(feed, queue))
        event = self.recorders[engine.directory.name].append(
            kind, now, generation=engine.coverage["generations"], **payload
        )
        await engine.ingest(event)
        return event

    def capture_watermark(self, feed, queue, *, current=None):
        if self.cfg.get('continuous', False) and isinstance(queue, CaptureQueue):
            return fifo_watermark(feed.m1_pending_received, queue.frame_receipts, current=current)
        values = chain(feed.m1_pending_received,
                       (p["received_mono"] for k, p in queue._queue if k == "FRAME"))
        return pending_watermark(values, current=current,
                                 compact=getattr(self, "cfg", {}).get("continuous", False))

    def capacity(self):
        if time.monotonic() - self.last_resource < 5:
            return
        self.last_resource = time.monotonic()
        # Only the new run is measured, never delete or truncate old files.
        retired = getattr(self, "retired_sizes", {})
        usage = sum(retired.values())
        for path in self.directory.iterdir():
            if path.name in retired:
                continue
            usage += directory_bytes(path) if path.is_dir() else path.stat().st_size
        free = shutil.disk_usage(self.directory).free
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        row = {"at": time.time(), "rss_peak_mib": rss, "output_bytes": usage, "free_bytes": free}
        if self.cfg.get("continuous", False):
            row.update(open_databases=sum(not e.store.closed for e in self.engines),
                       active_recorders=sum(r.thread.is_alive() for r in self.recorders.values()),
                       pending_settlements=len(self.pending), retired_markets=len(retired))
            if any(getattr(e, "only_m5_m10", False) for e in self.engines):
                row.update(
                    live_research_graphs=sum(getattr(e, "research_extension", None) is not None
                                             for e in self.engines),
                    retired_research_graphs=sum(getattr(e, "retired", False)
                                                and getattr(e, "research_extension", None) is not None
                                                for e in self.engines),
                    retirement_gc_max_seconds=max(
                        (getattr(e, "retirement_gc_seconds", 0) for e in self.engines), default=0),
                    retirement_collected_objects=sum(
                        getattr(e, "retirement_collected_objects", 0) for e in self.engines),
                    retirement_release_max_seconds=max(
                        (getattr(e, "retirement_release_seconds", 0) for e in self.engines), default=0),
                    retirement_release_cpu_max_seconds=max(
                        (getattr(e, "retirement_release_cpu_seconds", 0) for e in self.engines), default=0),
                )
        self.resources.append(row)
        if (
            usage >= self.cfg["max_output_mib"] * 1024**2
            or free < self.cfg["min_free_disk_mib"] * 1024**2
        ):
            raise RuntimeError("SIM_STORAGE_CAPACITY_EXHAUSTED")
        if rss > self.cfg["max_rss_mib"]:
            raise RuntimeError("SIM_MEMORY_CAPACITY_EXHAUSTED")
        for recorder in self.recorders.values():
            if recorder.failure:
                raise RuntimeError("SIM_RECORDING_FAILED")

    async def refresh(self, engine=None):
        if any(job[1].done() for job in self.report_tasks.values()):
            self.update_report()
        if self.clock_task and self.clock_task.done():
            try:
                self.clock = self.clock_task.result()
                if engine:
                    await self.record(engine, "CLOCK", official_clock=self.clock)
            except Exception as exc:
                self.log("CLOCK_READ_FAILED", error=str(exc))
            self.clock_task = None
        now = time.monotonic()
        if self.clock_task is None and now - self.clock[2] >= 30:
            self.clock_task = asyncio.create_task(self.api.clock())
        if now - self.clock[2] > 60:
            if engine is not None:
                raise RuntimeError("SIM_OFFICIAL_CLOCK_EXPIRED")
            # Report publication and retirement can use up the clock's
            # lifetime between markets. Reacquire official time before the
            # next market starts, retaining the guard during observation.
            try:
                self.clock = await asyncio.wait_for(self.clock_task, timeout=10)
            except Exception as exc:
                raise RuntimeError("SIM_OFFICIAL_CLOCK_EXPIRED") from exc
            finally:
                self.clock_task = None
            now = time.monotonic()
            if now - self.clock[2] > 60:
                raise RuntimeError("SIM_OFFICIAL_CLOCK_EXPIRED")
            self.log("CLOCK_REFRESH_AFTER_MARKET_BOUNDARY")
        if self.resolution_task and self.resolution_task.done():
            rows = self.resolution_task.result()
            for old, raw, error in rows:
                if error:
                    self.log("RESOLUTION_READ_FAILED", market=old.meta.slug, error=error)
                else:
                    await self.record(old, "SETTLEMENT", raw=raw)
                    if old.resolution:
                        self.pending.remove(old)
            self.resolution_task = None
            self.update_report()
        if self.resolution_task is None and now >= self.resolution_due and self.pending:
            self.resolution_due = now + 30
            self.resolution_task = asyncio.create_task(self.resolve_pending())
        if engine is not None:
            await self.refresh_ticks(engine)

    async def fetch_ticks(self, request):
        sent_mono, sent_wall = time.monotonic(), time.time()
        try:
            ticks = await asyncio.wait_for(
                self.api.ticks(request["tokens"]), timeout=TICK_POLICY["timeout_seconds"]
            )
            status, error = "SUCCESS", None
            ticks = {token: str(value) for token, value in ticks.items()}
        except TimeoutError:
            ticks, status, error = {}, "TIMEOUT", "TimeoutError"
        except Exception as exc:
            ticks, status, error = {}, "FAILED", type(exc).__name__
        return {
            "request_id": request["request_id"], "target_id": request["target_id"],
            "target": request["target"], "market": request["market"],
            "status": status, "ticks": ticks, "error_type": error,
            "source": "https://clob.polymarket.com/tick-size",
            "sent_mono": sent_mono, "sent_wall": sent_wall,
            "received_mono": time.monotonic(), "received_wall": time.time(),
        }

    async def refresh_ticks(self, engine):
        """Poll one bounded task; only recorded events can change verification."""
        if self.tick_task is not None:
            owner, _, task = self.tick_task
            if owner is not engine:
                raise RuntimeError("SIM_TICK_TASK_MARKET_IDENTITY")
            if task.done():
                self.tick_task = None
                await self.record(engine, "TICK_RESULT", result=task.result())
        if self.tick_task is None and not engine.ended:
            request = engine.tick_verification.propose(engine.signals, time.monotonic())
            if request is not None:
                await self.record(engine, "TICK_REQUEST", request=request)
                self.tick_task = (engine, dict(engine.tick_verification.active),
                                  asyncio.create_task(self.fetch_ticks(request)))

    async def finish_tick_request(self, engine):
        if self.tick_task is None:
            return
        owner, request, task = self.tick_task
        if owner is not engine:
            raise RuntimeError("SIM_TICK_TASK_MARKET_IDENTITY")
        self.tick_task = None
        if task.done() and not task.cancelled():
            result = task.result()
        else:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            now_mono, now_wall = time.monotonic(), time.time()
            result = {"request_id": request["request_id"], "target_id": request["target_id"],
                      "target": request["target"], "market": request["market"],
                      "status": "CANCELLED", "ticks": {}, "error_type": "MARKET_END",
                      "sent_mono": request["requested_mono"],
                      "sent_wall": request["requested_wall"],
                      "received_mono": now_mono, "received_wall": now_wall}
        await self.record(engine, "TICK_RESULT", result=result)

    async def resolve_pending(self):
        results = []
        for engine in list(self.pending):
            try:
                raw = await self.api.raw_market(engine.meta.slug, purpose="settlement")
                results.append((engine, raw, None))
            except Exception as exc:
                results.append((engine, None, str(exc)))
        return results

    def note_error(self, step, error, engine=None):
        row = {"step": step, "error_type": type(error).__name__, "error": str(error)}
        if engine is not None:
            row.update(market=engine.meta.slug if engine.definition else engine.directory.name,
                       input_seq=engine.seq, binding=engine.store.identity)
        self.cleanup_errors = getattr(self, "cleanup_errors", [])
        self.cleanup_failures = getattr(self, "cleanup_failures", [])
        self.cleanup_errors.append(row)
        self.cleanup_failures.append(error)
        return row

    async def cleanup(self, step, operation, engine=None, *, timeout=5):
        self.cleanup_steps = getattr(self, "cleanup_steps", [])
        row = {"step": step, "market": engine.directory.name if engine else None}
        try:
            result = operation()
            if inspect.isawaitable(result):
                await asyncio.wait_for(result, timeout=timeout)
        except BaseException as exc:
            row.update(status="ERROR", **self.note_error(step, exc, engine))
            self.cleanup_steps.append(row)
            return False
        row["status"] = "PASS"
        self.cleanup_steps.append(row)
        return True

    def current_run_failure(self):
        primary = getattr(self, "primary_error", None)
        if primary is None and not self.cleanup_errors:
            return None
        return {"run_schema": RUN_SCHEMA, "run_identity": getattr(self, "run_identity", None),
                "status": "FAIL", "primary_error": primary,
                "reason": self.stop_reason,
                "cleanup_errors": list(self.cleanup_errors)}

    def report_diagnostic(self, engine, validity, prefix=None):
        basis = "Committed report prefix with current failure"
        if prefix is None:
            basis = "Current engine failure; committed report unavailable"
            prefix = {"market": asdict(engine.meta), "coverage": dict(engine.coverage),
                      "official_result": engine.resolution, "accounts": []}
        value = invalidated_report(prefix, validity, basis=basis)
        value["committed_coverage"] = value["coverage"]
        value["coverage"] = dict(engine.coverage)
        value.update(engine_ended=engine.ended, current_input_seq=engine.seq)
        return value

    def update_report(self):
        reports = []
        for engine in self.engines:
            validity = getattr(engine, "validity", {})
            if not engine.ended and validity.get("status") != "INVALID":
                continue
            slug = engine.meta.slug
            stamp = ((engine.resolution or {}).get("winner"), digest(validity))
            pending = self.report_tasks.get(slug)
            if pending is not None and pending[1].done():
                try:
                    report = pending[1].result()
                except BaseException as exc:
                    self.note_error("REPORT_TASK", exc, engine)
                    unverified = {"status": "UNVERIFIED_EVIDENCE", "failures": [],
                                  "evidence": {"binding": engine.store.identity,
                                               "errors": [type(exc).__name__ + ":" + str(exc)]}}
                    report = self.report_diagnostic(engine, unverified)
                self.report_cache[slug] = (pending[0], report)
                del self.report_tasks[slug]
            cached = self.report_cache.get(slug)
            if (cached is None or cached[0] != stamp) and slug not in self.report_tasks:
                self.report_tasks[slug] = (
                    stamp,
                    asyncio.create_task(asyncio.to_thread(
                        summary, engine.directory / "simulation.sqlite3", self.cfg["markout_seconds"])),
                )
            if validity.get("status") == "INVALID":
                current = {**validity, "evidence": {"binding": engine.store.identity,
                           "failure_input_seq": engine.seq, "source": "CURRENT_ENGINE_MEMORY"}}
                reports.append(self.report_diagnostic(engine, current, cached[1] if cached else None))
            elif cached is not None:
                reports.append(cached[1])
        failure = self.current_run_failure() if hasattr(self, "cleanup_errors") else None
        # Full root publication inspects all member databases. Keep this work
        # between markets in continuous mode, outside the live input loop.
        if (not self.cfg.get("continuous", False) or self.active_input is None
                or failure is not None):
            publish(self.directory, reports, run_failure=failure)

    async def retire_market(self, engine, *, deferred=False):
        """Seal a finished member while retaining terminal/report identity."""
        if getattr(engine, "retired", False):
            return
        if not engine.ended or engine.validity["status"] != "VALID":
            raise RuntimeError("SIM_CANNOT_RETIRE_UNFINISHED_OR_INVALID_MARKET")
        committed = await self.cleanup("RETIRE_SNAPSHOT", engine.persist, engine)
        if not committed:
            await self.cleanup("RETIRE_ROLLBACK", engine.store.db.rollback, engine)
        backed_up = await self.cleanup("RETIRE_BACKUP",
            partial(engine.store.backup, engine.directory / "snapshot.sqlite3"), engine)
        closed = await self.cleanup("RETIRE_ENGINE_CLOSE", partial(engine.close, persist=False), engine)
        recorder = self.recorders[engine.directory.name]
        recorded = await self.cleanup("RETIRE_RECORDER_CLOSE", recorder.close, engine)
        if not all((committed, backed_up, closed, recorded)):
            raise self.cleanup_failures[-1]
        if deferred:
            self.deferred_settlements.append(engine.meta.slug)
            self.log("SETTLEMENT_DEFERRED", market=engine.meta.slug,
                     reason="PENDING_MARKET_RESOURCE_BOUND", input=str(recorder.path))
        engine.retired = True
        # Economic state is durable in the sealed database. Keep only the
        # small coverage/identity fields needed for the terminal and reports.
        engine.brokers.clear()
        engine.last_books = None
        engine.marks.clear()
        engine.mark_targets.clear()
        engine.tick_verification = None
        release_runtime = getattr(engine, "retire_runtime", None)
        if release_runtime is not None:
            release_runtime()
        self.retired_sizes[engine.directory.name] = directory_bytes(engine.directory)
        self.log("MARKET_RETIRED", market=engine.meta.slug, deferred_settlement=deferred)

    async def retire_finished(self):
        if not self.cfg.get("continuous", False):
            return
        limit = self.cfg.get("max_pending_markets", 12)
        # An in-flight public read holds references to pending engines. Cancel
        # it before deferring any of those members; no unrecorded result is a fact.
        if len(self.pending) > limit and self.resolution_task is not None:
            await cancel_owned(self.resolution_task)
            self.resolution_task = None
        for engine in self.engines:
            if engine.ended and engine.resolution and not getattr(engine, "retired", False):
                await self.retire_market(engine)
        while len(self.pending) > limit:
            engine = self.pending[0]
            await self.retire_market(engine, deferred=True)
            self.pending.remove(engine)
        self.update_report()

    async def finish_reports(self):
        self.update_report()
        while self.report_tasks:
            await asyncio.gather(*(job[1] for job in self.report_tasks.values()), return_exceptions=True)
            self.update_report()

    async def initial_ticks(self, meta):
        tokens = [token.token_id for token in meta.tokens]
        attempts = self.cfg.get("initial_tick_read_attempts", 1)
        if attempts == 1:
            return await self.api.ticks(tokens)
        for attempt in range(1, attempts + 1):
            try:
                return await asyncio.wait_for(self.api.ticks(tokens), timeout=4)
            except (TimeoutError, httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt == attempts:
                    self.log("INITIAL_TICKS_FAILED", market=meta.slug, attempt=attempt,
                             error=type(exc).__name__)
                    raise
                self.log("INITIAL_TICKS_RETRY", market=meta.slug, attempt=attempt,
                         error=type(exc).__name__)
                await asyncio.sleep(0.25)

    async def market(self, meta):
        ticks = await self.initial_ticks(meta)
        if self.stop.is_set() or time.monotonic() >= self.stop_at:
            return
        directory = self.directory / meta.slug
        recorder = Recorder(directory / "input.jsonl.gz", self.cfg["writer_queue_records"])
        self.recorders[meta.slug] = recorder
        engine = Engine(directory)
        engine.store.run_owner = getattr(self, "run_identity", None)
        self.engines.append(engine)
        definition = {
            "model": MODEL,
            "input_schema": INPUT_SCHEMA,
            "market": asdict(meta),
            "official_clock": self.clock,
            "ticks": {t: str(v) for t, v in ticks.items()},
            "settings": self.cfg,
        }
        write_json(directory / "definition.json", definition)
        await self.record(engine, "INIT", definition=definition)
        queue = CaptureQueue(self.cfg["input_queue_frames"])
        feed = CaptureFeed(meta, queue)
        self.active_input = (engine, feed, queue)
        task = asyncio.create_task(feed.run())
        self.log("MARKET_START", market=meta.slug, subscription_count=1)
        last_timer = last_log = time.monotonic()
        reason = "T300_OBSERVED"
        failure = None
        try:
            while not self.stop.is_set() and time.monotonic() < self.stop_at:
                self.capacity()
                await self.refresh(engine)
                if feed.failure:
                    raise RuntimeError(feed.failure)
                lower, _ = engine.bounds()
                # bounds() uses the last recorded event clock; update actual time
                # by emitting TIMER, then check T300 using that recorded time.
                if lower >= 300:
                    break
                try:
                    kind, payload = await asyncio.wait_for(queue.get(), timeout=0.005)
                except TimeoutError:
                    kind = None
                if kind:
                    if kind == "FRAME":
                        payload.update(self.capture_watermark(feed, queue,
                                                            current=payload["received_mono"]))
                    await self.record(engine, kind, **payload)
                    if engine.needs_reconnect:
                        feed.stop_event.set()
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                        while not queue.empty():
                            old_kind, old_payload = queue.get_nowait()
                            if old_kind == "FRAME":
                                old_payload["pending"] = [old_payload["received_mono"]]
                            await self.record(engine, old_kind, **old_payload)
                        feed = CaptureFeed(meta, queue)
                        self.active_input = (engine, feed, queue)
                        task = asyncio.create_task(feed.run())
                        self.log("PARSER_REBUILD_RECONNECT", market=meta.slug)
                now = time.monotonic()
                if now - last_timer >= 0.025:
                    await self.record(engine, "TIMER")
                    last_timer = time.monotonic()
                if now - last_log >= 30:
                    self.log(
                        "HEARTBEAT",
                        market=meta.slug,
                        elapsed=engine.bounds()[0],
                        frames=engine.coverage["frames"],
                        gaps=len(engine.coverage["gaps"]),
                        backlog=queue.qsize(),
                        rss_peak_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
                    )
                    last_log = now
            if self.stop.is_set() or time.monotonic() >= self.stop_at:
                reason = "MANUAL_STOP" if self.stop.is_set() else "MAX_DURATION"
                await self.record(engine, "GAP", reason=reason)
        except BaseException as exc:
            reason = type(exc).__name__ + ":" + str(exc)
            failure = exc
            await self.cleanup("MARKET_INVALIDATE", partial(engine.invalidate, exc, "RUNNER_PROCESSING"), engine)
        finally:
            feed.stop_event.set()
            await self.cleanup("FEED_STOP", lambda: cancel_owned(task), engine)
            await self.cleanup("TICK_FINISH", lambda: self.finish_tick_request(engine), engine)
            while not queue.empty():
                kind, payload = queue.get_nowait()
                if kind == "FRAME":
                    payload["pending"] = [payload["received_mono"]]
                try:
                    await self.record(engine, kind, **payload)
                except BaseException as exc:
                    failure = failure or exc
                    reason = type(failure).__name__ + ":" + str(failure)
                    self.note_error("QUEUE_DRAIN", exc, engine)
                    await self.cleanup("QUEUE_INVALIDATE",
                                       partial(engine.invalidate, exc, "RUNNER_QUEUE_DRAIN"), engine)
            if failure is None and engine.validity["status"] == "VALID":
                try:
                    await self.record(engine, "END", reason=reason)
                except BaseException as exc:
                    failure = exc
                    reason = type(exc).__name__ + ":" + str(exc)
                    self.note_error("OBSERVATION_END", exc, engine)
                    await self.cleanup("END_INVALIDATE", partial(engine.invalidate, exc, "RUNNER_END"), engine)
            self.active_input = None
            if engine.ended and engine.validity["status"] == "VALID":
                self.pending.append(engine)
            await self.cleanup("MARKET_REPORT", self.update_report, engine)
            await self.cleanup("MARKET_LOG", lambda: self.log(
                "MARKET_END" if engine.ended else "MARKET_ABORT", market=meta.slug, reason=reason,
                engine_ended=engine.ended, coverage=engine.coverage, validity=engine.validity), engine)
        if failure is not None:
            raise failure
        if engine.validity["status"] == "INVALID":
            raise ConstraintConflict(engine.validity["failures"])
        if getattr(self, "cleanup_failures", []):
            raise self.cleanup_failures[0]

    def terminal_record(self):
        members = []
        for engine in self.engines:
            members.append({"market": engine.meta.slug if engine.definition else engine.directory.name,
                "database": os.path.relpath(engine.store.path, self.directory),
                "binding": engine.store.identity, "input_seq": engine.seq, "ended": engine.ended,
                "validity": engine.validity, "database_closed": engine.store.closed,
                "policies_closed": [p.closed for p in engine.m1_policies],
                "close_errors": list(engine.close_errors)})
        recording = {key: {"thread_alive": recorder.thread.is_alive(), "failure": recorder.failure,
                          "recorded_input_seq": recorder.seq, "written_input_seq": recorder.written}
                     for key, recorder in self.recorders.items()}
        valid = self.stop_reason == "PLANNED_COMPLETE" and self.primary_error is None
        valid = valid and not self.cleanup_errors and all(e.validity["status"] == "VALID" for e in self.engines)
        return {"run_schema": RUN_SCHEMA, "run_identity": self.run_identity,
            "status": "PASS" if valid else "FAIL", "reason": self.stop_reason,
            "primary_error": self.primary_error, "cleanup_errors": list(self.cleanup_errors),
            "cleanup_steps": list(self.cleanup_steps), "members": members,
            "markets": len(self.engines),
            "complete_windows": sum(e.coverage["complete_window"] and e.validity["status"] == "VALID"
                                    for e in self.engines),
            "invalid_markets": [e.meta.slug for e in self.engines
                                if e.definition and e.validity["status"] == "INVALID"],
            "validity": {e.meta.slug: e.validity for e in self.engines if e.definition},
            "recording_errors": [key for key, row in recording.items() if row["failure"] or row["thread_alive"]],
            "recorders": recording, "logs_closed": self.logs.closed,
            "duration_seconds": time.monotonic() - self.started, "real_orders": 0,
            "deferred_settlements": list(getattr(self, "deferred_settlements", [])),
            "transport": "SimGateway only; public GETs and one market WS per active market"}

    async def finalize(self):
        for name in ("clock_task", "resolution_task"):
            task = getattr(self, name)
            if task is not None:
                await self.cleanup(name.upper() + "_STOP", partial(cancel_owned, task))
                setattr(self, name, None)
        if self.tick_task is not None:
            _, _, task = self.tick_task
            await self.cleanup("TICK_TASK_STOP", partial(cancel_owned, task))
            self.tick_task = None
        await self.cleanup("API_CLOSE", self.api.close)
        await self.cleanup("FINISH_REPORTS", self.finish_reports, timeout=30)
        for engine in self.engines:
            if getattr(engine, "retired", False):
                continue
            if engine.definition:
                committed = await self.cleanup("FINAL_SNAPSHOT", engine.persist, engine)
                if not committed:
                    await self.cleanup("FAILED_TRANSACTION_ROLLBACK", engine.store.db.rollback, engine)
                await self.cleanup("FINAL_BACKUP",
                                   partial(engine.store.backup, engine.directory / "snapshot.sqlite3"), engine)
            await self.cleanup("ENGINE_CLOSE", partial(engine.close, persist=False), engine)
        for slug, recorder in self.recorders.items():
            await self.cleanup("RECORDER_CLOSE:" + slug, recorder.close)
        await self.cleanup("FINAL_REPORT", self.update_report)
        if self.report_tasks:
            await self.cleanup("REPORT_DRAIN", lambda: asyncio.gather(
                *(job[1] for job in self.report_tasks.values()), return_exceptions=True), timeout=30)
            await self.cleanup("REPORT_PUBLISH", self.update_report)
        for _, task in list(self.report_tasks.values()):
            await self.cleanup("REPORT_TASK_STOP", partial(cancel_owned, task))
        self.report_tasks.clear()
        await self.cleanup("LOG_CLOSE", self.logs.close)
        await self.cleanup("RESOURCE_OUTPUT", lambda: write_json(self.directory / "resources.json", {
            "samples": self.resources, "limits": self.cfg,
            "recorder_high_water": {key: recorder.high_water for key, recorder in self.recorders.items()},
            "errors": list(self.cleanup_errors)}))
        self.terminal = self.terminal_record()
        saved = await self.cleanup("COMPLETION_OUTPUT", lambda: write_json(
            self.directory / "completion.json", self.terminal))
        if self.cleanup_errors or self.terminal["status"] == "FAIL":
            self.terminal = self.terminal_record()
            fallback = await self.cleanup("RUN_FAILURE_OUTPUT", lambda: write_json(
                failure_path(self.directory, self.run_identity), self.terminal))
            saved = saved or fallback
            await self.cleanup("FAILURE_REPORT", self.update_report)
            self.terminal = self.terminal_record()
            if saved:
                # Refresh a successful channel once to include later output errors.
                target = self.directory / "completion.json" if saved and (self.directory / "completion.json").exists() else failure_path(self.directory, self.run_identity)
                await self.cleanup("TERMINAL_REFRESH", lambda: write_json(target, self.terminal))
        self.terminal_saved = saved
        if not saved:
            raise RuntimeError("SIM_RUN_TERMINAL_EVIDENCE_UNAVAILABLE: run terminal could not be saved")

    async def run(self):
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self.stop.set)
        primary = None
        try:
            self.clock = await self.api.clock()
            server, _, received = self.clock
            epoch = int(server // 300) * 300
            if server - epoch >= 5:
                epoch += 300
            for _ in range(self.cfg["max_markets"]):
                if self.stop.is_set() or time.monotonic() >= self.stop_at:
                    break
                # Discover ahead of the new-order window, with bounded retries.
                slug = f"btc-updown-5m-{epoch}"
                meta = None
                while meta is None and not self.stop.is_set() and time.monotonic() < self.stop_at:
                    if self.cfg.get("continuous", False):
                        self.capacity()
                        await self.refresh()
                    try:
                        meta = await self.api.market(slug)
                    except Exception as exc:
                        self.log("MARKET_DISCOVERY_RETRY", market=slug, error=str(exc))
                        await asyncio.sleep(2)
                if meta is None:
                    break
                while (
                    self.clock[0] + time.monotonic() - self.clock[2] < epoch - 5
                    and not self.stop.is_set()
                    and time.monotonic() < self.stop_at
                ):
                    self.capacity()
                    await self.refresh()
                    await asyncio.sleep(0.1)
                if self.stop.is_set() or time.monotonic() >= self.stop_at:
                    break
                await self.market(meta)
                await self.retire_finished()
                epoch += 300
            if self.stop.is_set():
                self.stop_reason = "MANUAL_STOP"
            elif time.monotonic() >= self.stop_at:
                self.stop_reason = "MAX_DURATION"
            # Final pass checks every published result once; pending stays
            # pending and can be continued by the explicit settlement command.
            if self.resolution_task:
                rows = await self.resolution_task
                self.resolution_task = None
                for engine, raw, error in rows:
                    if not error:
                        await self.record(engine, "SETTLEMENT", raw=raw)
                        if engine.resolution and engine in self.pending:
                            self.pending.remove(engine)
            for engine, raw, error in await self.resolve_pending():
                if not error:
                    await self.record(engine, "SETTLEMENT", raw=raw)
            invalid = [engine for engine in self.engines
                       if engine.validity["status"] == "INVALID"]
            if invalid:
                raise ConstraintConflict(invalid[0].validity["failures"])
        except BaseException as exc:
            primary = exc
            self.primary_error = {"error_type": type(exc).__name__, "error": str(exc)}
            self.stop_reason = type(exc).__name__ + ":" + str(exc)
            await self.cleanup("RUN_FAILURE_LOG", lambda: self.log("RUN_FAILED", error=self.stop_reason))
        finally:
            await self.finalize()
        if primary is not None:
            raise primary
        if self.cleanup_failures:
            raise self.cleanup_failures[0]
        return self.terminal


async def recover(input_path, directory, *, settle=False):
    """Rebuild from durable inputs into a new revision, never edit history.

    During a missing interval queue position is unprovable. Retain reservations
    and mark uncertainty before any further action. v1 is non-random (seed saved).
    Settlement continuation fetches public official metadata only.
    """
    directory = output_path(directory)
    engine = Engine(directory)
    recorder = Recorder(directory / "input.jsonl.gz")
    api = None
    try:
        engine.source_failure = source_failure(input_path)
        if engine.source_failure is not None:
            raise ValueError("SIM_SOURCE_RUN_INVALID_OR_UNVERIFIED")
        last = None
        for event in read_events(input_path):
            payload = {
                k: v
                for k, v in event.items()
                if k
                not in {"schema", "seq", "kind", "available_mono", "available_wall", "generation"}
            }
            copied = recorder.append(
                event["kind"],
                Clock(event["available_mono"], event["available_wall"]),
                event["generation"],
                **payload,
            )
            await engine.ingest(copied)
            last = event
        if last is None:
            raise ValueError("SIM_EMPTY_RECOVERY_INPUT")
        wall = max(last["available_wall"], time.time()) if settle else last["available_wall"]
        clock = Clock(last["available_mono"] + (wall - last["available_wall"]), wall)
        if not engine.ended:
            await engine.ingest(
                recorder.append("GAP", clock, reason="RESTART_MISSING_INTERVAL_UNCERTAIN")
            )
            await engine.ingest(
                recorder.append("END", clock, reason="RECOVERY_INCOMPLETE_OBSERVATION")
            )
        if settle:
            api = VirtualPublicAPI()
            raw = await api.raw_market(engine.meta.slug, purpose="settlement")
            await engine.ingest(recorder.append("SETTLEMENT", clock, raw=raw))
        state = engine.snapshot()
        engine.store.backup(directory / "snapshot.sqlite3")
        await engine.close()
        recorder.close()
        report = summary(directory / "simulation.sqlite3")
        publish(directory, [report])
        value = {
            "status": report["status"],
            "validity": report["validity"],
            "evidence_schema": EVIDENCE_SCHEMA, "binding": engine.store.identity,
            "input_seq": engine.seq,
            "mode": "PUBLIC_SETTLEMENT_CONTINUATION" if settle else "OFFLINE_RECOVERY",
            "missing_interval": "UNKNOWN; no exact queue reconstruction claimed",
            "state_hash": digest(state),
            "input": str(input_path),
            "output": str(directory),
        }
        write_json(directory / "recovery.json", value)
        return value
    except BaseException as exc:
        mode = "PUBLIC_SETTLEMENT_CONTINUATION" if settle else "OFFLINE_RECOVERY"
        await failed_output(engine, directory, mode, exc, input_path)
        raise
    finally:
        recorder.close()
        if api:
            await api.close()
