"""One public seven-day capture for independent M12, M21 and M24 simulations."""

import argparse
import asyncio
import fcntl
import hashlib
import json
import math
import resource
import shutil
import signal
import sys
import time
from collections import deque
from contextlib import suppress
from dataclasses import asdict
from pathlib import Path

import m1223_shared_storage
import websockets
from m1223_execution import ANCHOR_FILL_MODEL
from m1223_report import market_result, summary
from m1223_shared_storage import (
    FREE_RESERVE,
    PAIR,
    TAPE_RETENTION_SECONDS,
    Recorder,
    allocated_bytes,
    prune_expired_tapes,
    read_tape,
    seal_ledger,
    storage_status,
    verify_tape,
)
from m1223_sim import SopEngine, definition
from sop_signals import m12_overlap, m21, m24
from sop_signals.m12 import parameter_identity
from sop_signals.parameters import load_revision
from websockets.exceptions import WebSocketException

from polymaker.m1234_sim import INPUT_SCHEMA, MODEL, runner
from polymaker.m1234_sim.events import Clock, encode, write_json
from polymaker.rdp_sm_marketdata import WS_CLOSE_TIMEOUT_SEC, WS_HEARTBEAT_SEC

SCHEMA = "m122124-shared-public-72h-v1"
VARIANTS = {"M12": m12_overlap, "M21": m21, "M24": m24}
MARKETS = 7 * 24 * 12
LEGACY_MARKETS = 3 * 24 * 12
MARKET_SECONDS = 300
PREPARE_SECONDS = 25
PREWARM_SECONDS = 5
SETTLEMENT_SECONDS = 180
REPLAY_IDLE_SECONDS = 180
PUBLIC_ONLY_SAMPLE = {"status": "UNAVAILABLE", "reason": "PUBLIC_ONLY_NO_ACCOUNT_ACTIVITY_REVIEW"}


def configuration(strategies=tuple(VARIANTS)):
    root = runner.ROOT
    settings = runner.settings(
        root / "config/m1223-24h.toml", root / "config/m1234-sim-contract.json"
    )
    execution = {
        "version": "m122124-assumed-anchor-v1",
        "mode": "SIMULATION_ONLY_PUBLIC_DATA",
        "fill_model": ANCHOR_FILL_MODEL,
        "price": "ORIGINAL_FIXED_ANCHOR",
        "budget_usdc": "5",
        "session_budget_usdc": "10",
        "quantity_step": "0.1",
        "entry_market_age_seconds": [15, 240],
        "maximum_entries_per_market_direction": 1,
        "price_filter": None,
        "settlement": "HOLD_TO_OFFICIAL_SETTLEMENT_GROSS_PNL",
    }
    documents = {}
    versions = {}
    parameters = {}
    for strategy in strategies:
        module = VARIANTS[strategy]
        doc = {
            "strategy": strategy,
            "variant_id": module.VARIANT,
            "revision_round": 0,
            "sop_sha256": module.SOP_SHA256,
            "document_parameters_sha256": module.DOCUMENT_PARAMETERS_SHA256,
            "pnl_used_for_selection": False,
            "parameters": parameter_identity(module.Parameters())[0],
        }
        parameters[strategy], documents[strategy] = load_revision(doc)
        versions[strategy] = definition(parameters[strategy])
    settings.update(
        profiles={},
        scenarios={"anchor": {}},
        shared_capture=SCHEMA,
        max_markets=MARKETS,
        max_seconds=MARKETS * MARKET_SECONDS + MARKET_SECONDS,
        max_output_mib=None,
        min_free_disk_mib=FREE_RESERVE // 1024**2,
        tape_retention_seconds=TAPE_RETENTION_SECONDS,
        initial_tick_read_attempts=3,
        input_queue_frames=4096,
        ledger_timing="M12_DURING_CAPTURE_" + "_".join(strategies[1:]) + "_FROM_SEALED_TAPE",
        sop_execution=execution,
        strategy_definitions=versions,
        strategy_revisions=documents,
        coordinator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        storage_sha256=hashlib.sha256(Path(m1223_shared_storage.__file__).read_bytes()).hexdigest(),
        worker_sha256=hashlib.sha256(
            Path(__file__).with_name("m1223_shared_worker.py").read_bytes()
        ).hexdigest(),
        storage_encoding="ZSTD6_LONG24_ONE_INPUT_AND_ONE_LEDGER_PER_STRATEGY",
    )
    return settings, parameters


class MarketEngines:
    def __init__(self, directory, settings, parameters, owner):
        self.directory = Path(directory)
        self.engines = {}
        self.settings = settings
        self.parameters = parameters
        self.participants = tuple(parameters)
        self.owner = owner

    @property
    def primary(self):
        return self.engines["M12"]

    @property
    def needs_reconnect(self):
        return any(engine.needs_reconnect for engine in self.engines.values())

    async def ingest(self, event):
        if event["kind"] == "INIT":
            recorded = event["definition"]["settings"]
            if recorded != self.settings:
                raise ValueError("SHARED_BASELINE_SETTINGS_MISMATCH")
            for strategy, parameters in self.parameters.items():
                sop = recorded["strategy_definitions"][strategy]
                engine = SopEngine(
                    self.directory / strategy,
                    parameters=parameters,
                    execution=recorded["sop_execution"],
                    sop_definition=sop,
                )
                self.engines[strategy] = engine
                engine.store.run_owner = self.owner
                settings = {
                    **recorded,
                    "sop": sop,
                    "sop_parameter_revision": recorded["strategy_revisions"][strategy],
                }
                await engine.ingest(
                    {**event, "definition": {**event["definition"], "settings": settings}}
                )
            return
        if (
            event["kind"] == "FRAME"
            and isinstance(event["raw"], str)
            and event["raw"] not in ("PING", "PONG")
        ):
            # Every engine receives the original line if decoding fails.
            with suppress(ValueError):
                event = {**event, "raw": json.loads(event["raw"])}
        for engine in self.engines.values():
            await engine.ingest(event)
        if event["kind"] == "SETTLEMENT":
            first = next(iter(self.engines.values()))
            if any(engine.resolution != first.resolution for engine in self.engines.values()):
                raise ValueError("SHARED_OFFICIAL_RESULT_DIVERGED")

    async def close(self):
        errors = []
        for engine in self.engines.values():
            try:
                await engine.close()
                if engine.ended and engine.store.closed:
                    engine.retire_runtime()
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise ExceptionGroup("SHARED_LEDGER_CLOSE_FAILURES", errors)

    def report(self):
        return {
            name: market_result(
                self.directory / name / "simulation.sqlite3", None, eligibility=PUBLIC_ONLY_SAMPLE
            )
            for name in self.ledger_states()
        }

    def ledger_states(self):
        return {
            name: {
                "seq": engine.seq,
                "closed": engine.store.closed,
                "ended": engine.ended,
                "complete": engine.coverage["complete_window"],
                "resolution": engine.resolution,
            }
            for name, engine in self.engines.items()
        }


def seal_market(directory, tape, input_seq, slug, group):
    states = group.ledger_states()
    if set(states) != set(group.participants) or any(
        state["seq"] != input_seq or not state["closed"] or not state["ended"]
        or state["resolution"] != states["M12"]["resolution"]
        for state in states.values()
    ):
        raise ValueError("SHARED_SEAL_LEDGER_WATERMARK_OR_RESULT_MISMATCH")
    reports = group.report()
    tape_hash = verify_tape(tape)
    ledgers = {}
    for name in states:
        ledger = seal_ledger(directory / name / "simulation.sqlite3")
        ledgers[name] = {**ledger, "file": str(Path(name) / ledger["file"])}
        reports[name][0]["source_db"] = ledgers[name]["file"]
    result = {
        "market": slug,
        "input_seq": input_seq,
        "input_sha256": tape_hash,
        "input": tape.name,
        "ledgers": ledgers,
        "strategies": reports,
        "complete_window": all(s["complete"] for s in states.values()),
        "official_winner": (states["M12"]["resolution"] or {}).get("winner"),
    }
    if group.participants == PAIR:
        result["active_strategies"] = list(PAIR)
    write_json(directory / "market-report.json", result)
    return result


class StrategyReplayTimeout(RuntimeError):
    pass


class RecordedMarketEngines(MarketEngines):
    """Capture one authoritative timeline, then materialize the other ledgers."""

    def __init__(self, directory, settings, parameters, owner):
        super().__init__(directory, settings, {"M12": parameters["M12"]}, owner)
        self.participants = tuple(parameters)
        self.worker_states = {}
        self.replay_input_seq = 0

    async def materialize(self, tape, *, original_plan=None):
        worker = Path(__file__).with_name("m1223_shared_worker.py")
        options = []
        if self.participants == PAIR:
            options.append("--pair")
        if original_plan is not None:
            options.extend(("--original-plan", str(original_plan)))
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-B", str(worker), str(self.directory), str(tape),
            encode(self.owner), *options, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        )
        try:
            watermark, states = 0, None
            while states is None:
                try:
                    line = await asyncio.wait_for(process.stdout.readline(), timeout=REPLAY_IDLE_SECONDS)
                except TimeoutError as exc:
                    raise StrategyReplayTimeout(
                        f"SHARED_STRATEGY_REPLAY_STALLED_AT:{watermark}"
                    ) from exc
                if not line:
                    raise RuntimeError(f"SHARED_STRATEGY_REPLAY_EXITED_AT:{watermark}")
                event = json.loads(line)
                seq = event["input_seq"]
                if type(seq) is not int or seq < watermark or (seq == watermark and "states" not in event):
                    raise ValueError("SHARED_STRATEGY_REPLAY_PROGRESS_REGRESSION")
                watermark = seq
                self.replay_input_seq = seq
                states = event.get("states")
            try:
                await asyncio.wait_for(process.wait(), timeout=REPLAY_IDLE_SECONDS)
            except TimeoutError as exc:
                raise StrategyReplayTimeout(
                    f"SHARED_STRATEGY_REPLAY_EXIT_STALLED_AT:{watermark}"
                ) from exc
        finally:
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
        if process.returncode:
            raise RuntimeError(f"SHARED_STRATEGY_REPLAY_FAILED:{process.returncode}")
        self.worker_states = states

    def ledger_states(self):
        return {**super().ledger_states(), **self.worker_states}


class SharedCaptureFeed:
    """A bounded receiver that waits for capacity before reading the next frame."""

    def __init__(self, meta, target):
        self.meta, self.target = meta, target
        self.stop_event = asyncio.Event()
        self.m1_pending_received = deque()
        self.pending_event = None
        self.backpressure_count = 0
        self.max_backpressure_ms = 0.0
        self.generation = 0

    async def enqueue(self, kind, **payload):
        self.pending_event = (kind, payload)
        if kind == "FRAME":
            self.m1_pending_received.append(payload["received_mono"])
        blocked_since = time.monotonic() if self.target.full() else None
        if blocked_since is not None:
            self.backpressure_count += 1
        await self.target.put(self.pending_event)
        if blocked_since is not None:
            self.max_backpressure_ms = max(
                self.max_backpressure_ms, (time.monotonic() - blocked_since) * 1000
            )
        self.pending_event = None
        self.m1_pending_received.clear()

    async def receive(self, ws):
        async for raw in ws:
            received_mono, received_wall = time.monotonic(), time.time()
            await self.enqueue(
                "FRAME", raw=raw.decode() if isinstance(raw, bytes) else raw,
                received_mono=received_mono, received_wall=received_wall,
            )
            # A buffered read and an immediate put can both complete synchronously.
            await asyncio.sleep(0)

    async def heartbeat(self, ws):
        while not self.stop_event.is_set():
            await ws.send("PING")
            await asyncio.sleep(WS_HEARTBEAT_SEC)

    async def disconnected(self, reason):
        detected = time.monotonic()
        # Connection loss can interrupt a queue wait. Keep the frame whose
        # receipt was already timestamped before recording the gap.
        if self.pending_event is not None:
            kind, payload = self.pending_event
            self.m1_pending_received.clear()
            await self.enqueue(kind, **payload)
        await self.enqueue("GAP", reason=reason, missing_since_received_mono=detected)

    async def observe(self, ws):
        self.generation += 1
        await self.enqueue("CONNECT", transport_generation=self.generation)
        await ws.send(json.dumps({
            "assets_ids": [token.token_id for token in self.meta.tokens], "type": "market",
        }))
        tasks = (asyncio.create_task(self.receive(ws)), asyncio.create_task(self.heartbeat(ws)))
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def run(self):
        transport_errors = (WebSocketException, OSError, TimeoutError)
        while not self.stop_event.is_set():
            gap_reported = False
            try:
                async with websockets.connect(
                    "wss://ws-subscriptions-clob.polymarket.com/ws/market",
                    ping_interval=5, ping_timeout=10, open_timeout=10,
                    close_timeout=WS_CLOSE_TIMEOUT_SEC, proxy=None,
                ) as ws:
                    try:
                        await self.observe(ws)
                    except transport_errors as exc:
                        await self.disconnected("WS_RECONNECT_" + type(exc).__name__)
                    else:
                        await self.disconnected("WS_DISCONNECTED")
                    gap_reported = True
            except transport_errors as exc:
                if not gap_reported:
                    await self.disconnected("WS_RECONNECT_" + type(exc).__name__)
            with suppress(TimeoutError):
                await asyncio.wait_for(self.stop_event.wait(), timeout=1)


class MarketCapture:
    # Reuse generation-bound public tick requests and their recorded results.
    fetch_ticks = runner.LiveRun.fetch_ticks
    refresh_ticks = runner.LiveRun.refresh_ticks
    finish_tick_request = runner.LiveRun.finish_tick_request

    def __init__(self, session, meta, ticks):
        self.session, self.api, self.meta, self.cfg = session, session.api, meta, session.settings
        self.ticks, self.tick_task = ticks, None
        self.directory = session.output / meta.slug
        self.group = RecordedMarketEngines(
            self.directory, self.cfg, session.parameters, session.owner
        )
        self.recorder = Recorder(
            self.directory / "input.jsonl.zst", self.cfg["writer_queue_records"]
        )
        self.feed = self.task = self.queue = None

    async def record(self, engine, kind, **payload):
        if self.recorder.seq - self.recorder.written >= self.recorder.queue.maxsize:
            try:
                async with asyncio.timeout(10):
                    while self.recorder.seq - self.recorder.written >= self.recorder.queue.maxsize:
                        if self.recorder.failure:
                            raise RuntimeError("SHARED_TAPE_WRITER_FAILED")
                        await asyncio.sleep(0.001)
            except TimeoutError as exc:
                raise RuntimeError("SHARED_TAPE_WRITER_NOT_DRAINING") from exc
        if self.feed is not None and "pending" not in payload:
            payload.update(
                runner.fifo_watermark(
                    self.feed.m1_pending_received,
                    self.queue.frame_receipts,
                    current=payload.get("received_mono") if kind == "FRAME" else None,
                )
            )
        event = self.recorder.append(
            kind,
            Clock(time.monotonic(), time.time()),
            generation=engine.coverage["generations"] if engine else 0,
            **payload,
        )
        await self.group.ingest(event)

    def start_feed(self):
        self.queue = runner.CaptureQueue(self.cfg["input_queue_frames"])
        self.feed = self.session.feed_factory(self.meta, self.queue)
        self.task = asyncio.create_task(self.feed.run())

    async def stop_feed(self):
        self.feed.stop_event.set()
        self.task.cancel()
        replies = await asyncio.gather(self.task, return_exceptions=True)
        error = replies[0]
        if isinstance(error, Exception):
            raise error

    async def drain_queue(self, engine):
        while not self.queue.empty():
            kind, payload = self.queue.get_nowait()
            await self.record(engine, kind, **payload)
        if self.feed.pending_event is not None:
            kind, payload = self.feed.pending_event
            self.feed.pending_event = None
            self.feed.m1_pending_received.clear()
            await self.record(engine, kind, **payload)

    async def capture(self):
        observed_clock = self.session.public_clock
        await self.record(
            None,
            "INIT",
            definition={
                "model": MODEL,
                "input_schema": INPUT_SCHEMA,
                "market": asdict(self.meta),
                "official_clock": observed_clock,
                "ticks": {t: str(v) for t, v in self.ticks.items()},
                "settings": self.cfg,
            },
        )
        engine = self.group.primary
        self.start_feed()
        self.session.log(
            "MARKET_PREWARM",
            market=self.meta.slug,
            elapsed=self.session.server_now() - engine.epoch,
        )
        last_timer = last_pulse = time.monotonic()
        processed = 0
        log_warm = self.group.participants == PAIR
        try:
            while not self.session.stop.is_set():
                if self.task.done():
                    self.task.result()
                    raise RuntimeError("SHARED_CAPTURE_FEED_EXITED")
                if observed_clock != self.session.public_clock:
                    observed_clock = self.session.public_clock
                    await self.record(engine, "CLOCK", official_clock=observed_clock)
                await self.refresh_ticks(engine)
                if engine.bounds()[0] >= MARKET_SECONDS:
                    break
                try:
                    if self.queue.empty():
                        kind, payload = await asyncio.wait_for(self.queue.get(), timeout=0.005)
                    else:
                        kind, payload = self.queue.get_nowait()
                except TimeoutError:
                    pass
                else:
                    await self.record(engine, kind, **payload)
                    if log_warm and engine.coverage["warmed_before_t15"]:
                        self.session.log("MARKET_WARMED_T15", market=self.meta.slug)
                        log_warm = False
                    processed += 1
                    if processed % 8 == 0:
                        await asyncio.sleep(0)
                    if self.group.needs_reconnect:
                        await self.stop_feed()
                        await self.drain_queue(engine)
                        await self.record(engine, "GAP", reason="SHARED_PARSER_REBUILD")
                        self.start_feed()
                now = time.monotonic()
                if now - last_timer >= 0.025:
                    await self.record(engine, "TIMER")
                    last_timer = now
                if now - last_pulse >= 30:
                    self.session.log(
                        "HEARTBEAT",
                        market=self.meta.slug,
                        elapsed=engine.bounds()[0],
                        frames=engine.coverage["frames"],
                        backlog=self.queue.qsize(),
                        gaps=len(engine.coverage["gaps"]),
                        recorded=self.recorder.seq,
                        written=self.recorder.written,
                        receiver_backpressure=self.feed.backpressure_count,
                        max_receiver_wait_ms=self.feed.max_backpressure_ms,
                        max_processing_lag_ms=engine.coverage["max_processing_lag_ms"],
                    )
                    last_pulse = now
        finally:
            try:
                await self.stop_feed()
            finally:
                await self.finish_tick_request(engine)
        await self.drain_queue(engine)
        if self.session.stop.is_set():
            reason = self.session.stop_reason
            await self.record(engine, "GAP", reason=reason)
        else:
            reason = "T300_OBSERVED"
        await self.record(
            engine, "END", reason=reason, research_sample_eligibility=PUBLIC_ONLY_SAMPLE
        )
        self.session.log(
            "MARKET_END",
            market=self.meta.slug,
            reason=reason,
            warmed_before_t15=engine.coverage["warmed_before_t15"],
        )
        return engine

    async def settle(self, engine):
        deadline = time.monotonic() + SETTLEMENT_SECONDS
        while not self.session.stop.is_set() and time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(
                    self.api.raw_market(self.meta.slug, purpose="settlement"), timeout=5
                )
            except Exception as exc:
                self.session.log(
                    "SETTLEMENT_READ_FAILED", market=self.meta.slug, error=type(exc).__name__
                )
            else:
                await self.record(engine, "SETTLEMENT", raw=raw)
                if engine.resolution:
                    return
            with suppress(TimeoutError):
                await asyncio.wait_for(self.session.stop.wait(), timeout=10)

    async def close(self):
        try:
            await self.group.close()
        finally:
            await asyncio.to_thread(self.recorder.close)

    def seal(self):
        if self.recorder.written != self.recorder.seq:
            raise ValueError("SHARED_SEAL_RECORDING_WATERMARK_MISMATCH")
        return seal_market(self.directory, self.recorder.path, self.recorder.seq, self.meta.slug, self.group)


class SharedRun:
    def __init__(self, output, root, settings, parameters, api=None, feed_factory=None,
                 predecessor=None, *, scheduled_end=None):
        self.output, self.root, self.settings, self.parameters = output, root, settings, parameters
        self.api = api if api is not None else runner.VirtualPublicAPI()
        self.feed_factory = feed_factory or SharedCaptureFeed
        self.owner = {"schema": SCHEMA, "mode": "SIMULATION_ONLY_PUBLIC_DATA", "path": str(output)}
        self.predecessor = predecessor
        self.scheduled_end = scheduled_end
        self.stop, self.stop_reason = asyncio.Event(), None
        self.public_clock = None
        self.active, self.closed_markets, self.reports = {}, set(), {}
        self.replay_queue = asyncio.Queue(maxsize=settings["max_markets"] + 1)
        self.replaying = None
        self.errors = []
        self.observing = set()
        self.peak_bytes = 0
        self.stop_at = None

    def log(self, event, **payload):
        with (self.output / "runtime.jsonl").open("a") as log:
            log.write(json.dumps({"event": event, "at": time.time(), **payload}) + "\n")

    def request_stop(self, reason):
        if not self.stop.is_set():
            self.stop_reason = reason
            self.stop.set()
            self.log("STOP_REQUESTED", reason=reason)

    def server_now(self):
        server, _, received = self.public_clock
        return server + time.monotonic() - received

    async def wait_until(self, epoch):
        while not self.stop.is_set():
            delay = epoch - self.server_now()
            if delay <= 0:
                return True
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=min(1, delay))
            except TimeoutError:
                continue
        return False

    async def refresh_clock(self):
        while not self.stop.is_set():
            await asyncio.sleep(5)
            try:
                self.public_clock = await asyncio.wait_for(self.api.clock(), timeout=5)
            except Exception as exc:
                self.log("CLOCK_READ_FAILED", error=type(exc).__name__)
                if time.monotonic() - self.public_clock[2] > 60:
                    self.request_stop("PUBLIC_CLOCK_EXPIRED")

    async def watch_capacity(self):
        next_sample = 0.0
        while not self.stop.is_set():
            try:
                now = time.monotonic()
                if self.stop_at is not None and now >= self.stop_at:
                    self.request_stop("MAX_DURATION")
                    return
                current = storage_status(self.output, self.root)
                if now >= next_sample and not current["reason"]:
                    try:
                        reclaimed = await asyncio.to_thread(self.prune_tapes, self.server_now())
                        if reclaimed["removed_tapes"]:
                            self.log("TAPES_PRUNED", **reclaimed)
                    except OSError as exc:
                        self.log("TAPE_PRUNE_FAILED", error=str(exc))
                    used = await asyncio.to_thread(allocated_bytes, self.output)
                    self.peak_bytes = max(self.peak_bytes, used)
                    current = storage_status(self.output, self.root, used_bytes=used)
                if current["reason"] or now >= next_sample:
                    self.log("CAPACITY", **current, replay_market=(
                        self.replaying.meta.slug if self.replaying else None
                    ), pending_replays=self.replay_queue.qsize(), replay_input_seq=(
                        self.replaying.group.replay_input_seq if self.replaying else None
                    ))
                    next_sample = now + 60
                if current["reason"]:
                    self.request_stop(current["reason"])
                    return
                if (
                    resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
                    > self.settings["max_rss_mib"]
                ):
                    self.request_stop("MEMORY_BUDGET")
                    return
            except OSError as exc:
                self.errors.append({"stage": "DISK_CHECK", "error": str(exc)})
                self.request_stop("DISK_CHECK_FAILED")
                return
            await asyncio.sleep(1)

    async def prepare(self, slug, epoch):
        while not self.stop.is_set() and self.server_now() < epoch + 15:
            try:
                meta = await asyncio.wait_for(self.api.market(slug), timeout=4)
                if meta.slug != slug:
                    raise ValueError("SHARED_PUBLIC_MARKET_IDENTITY_CHANGED")
                requested = [token.token_id for token in meta.tokens]
                ticks = await asyncio.wait_for(self.api.ticks(requested), timeout=4)
                if set(ticks) != set(requested):
                    raise ValueError("SHARED_PUBLIC_TICK_TOKEN_SET_CHANGED")
                return meta, ticks
            except (TimeoutError, OSError, runner.httpx.TransportError) as exc:
                self.log("MARKET_PREPARE_RETRY", market=slug, error=type(exc).__name__)
                await asyncio.sleep(0.25)
            except runner.httpx.HTTPStatusError as exc:
                if exc.response.status_code not in (404, 429, 500, 502, 503, 504):
                    raise
                self.log("MARKET_PREPARE_RETRY", market=slug, status=exc.response.status_code)
                await asyncio.sleep(0.5)
        return None

    def prune_tapes(self, now):
        return prune_expired_tapes(self.output, now)

    async def market(self, slug, epoch):
        capture = None
        stage = "PREPARE"
        try:
            prepared = await self.prepare(slug, epoch)
            if not prepared or not await self.wait_until(epoch - PREWARM_SECONDS):
                if not self.stop.is_set():
                    self.log("MARKET_MISSING", market=slug, reason="PREWARM_METADATA_UNAVAILABLE")
                return
            if len(self.observing) >= 2:
                raise RuntimeError("SHARED_OVERLAP_BOUND_EXCEEDED")
            self.observing.add(slug)
            stage = "CAPTURE"
            capture = MarketCapture(self, *prepared)
            try:
                engine = await capture.capture()
            finally:
                self.observing.discard(slug)
            stage = "SETTLEMENT"
            await capture.settle(engine)
            stage = "CLOSE_CAPTURE"
            await capture.close()
            self.closed_markets.add(slug)
            self.replay_queue.put_nowait(capture)
            self.log("MARKET_TAPE_CLOSED", market=slug, input_seq=capture.recorder.seq,
                     pending_replays=self.replay_queue.qsize())
        except Exception as exc:
            failure = {"market": slug, "stage": stage, "error": f"{type(exc).__name__}:{exc}"}
            self.errors.append(failure)
            self.log("MARKET_PROCESSING_ERROR", **failure)
            self.request_stop("MARKET_PROCESSING_FAILED")
            if capture:
                try:
                    await capture.close()
                except Exception as close_error:
                    self.errors.append({"market": slug, "cleanup_error": str(close_error)})
        finally:
            self.active.pop(slug, None)

    async def materialize_closed_markets(self):
        while not self.stop.is_set():
            capture = await self.replay_queue.get()
            if capture is None:
                return
            if not self.accept_capture_for_replay(capture):
                continue
            self.replaying = capture
            slug = capture.meta.slug
            started = time.monotonic()
            try:
                await capture.group.materialize(capture.recorder.path)
                self.reports[slug] = await asyncio.to_thread(capture.seal)
                self.log("LEDGERS_MATERIALIZED", market=slug, seconds=round(time.monotonic() - started, 3))
                self.log("MARKET_SEALED", market=slug, input_seq=capture.recorder.seq)
            except Exception as exc:
                failure = {"market": slug, "stage": "LEDGER_REPLAY",
                           "error": f"{type(exc).__name__}:{exc}"}
                self.errors.append(failure)
                self.log("MARKET_LEDGER_REPLAY_FAILED", **failure)
            finally:
                self.replaying = None

    def accept_capture_for_replay(self, capture):
        return True

    async def run(self):
        loops = []
        epochs = []
        analysis = None
        try:
            self.public_clock = await asyncio.wait_for(self.api.clock(), timeout=5)
            prior_epochs = self.predecessor["market_epochs"] if self.predecessor else []
            first = prior_epochs[-1] + MARKET_SECONDS if prior_epochs else (
                math.ceil((self.server_now() + PREPARE_SECONDS + 5) / MARKET_SECONDS)
                * MARKET_SECONDS
            )
            if prior_epochs and self.server_now() >= first - PREPARE_SECONDS:
                raise ValueError("SHARED_CONTINUATION_MISSED_PREPARE_WINDOW")
            remaining = self.settings["max_markets"] - len(prior_epochs)
            if self.scheduled_end is not None:
                remaining = (self.scheduled_end - first) // MARKET_SECONDS
                if remaining <= 0:
                    raise ValueError("SHARED_CONTINUATION_NO_FUTURE_MARKETS")
            self.stop_at = (
                time.monotonic()
                + max(0, first - self.server_now())
                + remaining * MARKET_SECONDS
                + MARKET_SECONDS
            )
            capture_epochs = list(range(first, first + remaining * MARKET_SECONDS, MARKET_SECONDS))
            epochs = [*prior_epochs, *capture_epochs]
            plan = {**self.owner, "market_epochs": epochs, "settings": self.settings}
            if self.predecessor:
                plan.update(
                    settings=self.predecessor["settings"],
                    continuation_start_epoch=first,
                    continuation_settings=self.settings,
                )
            write_json(self.output / "plan.json", plan)
            loops = [
                asyncio.create_task(self.refresh_clock()),
                asyncio.create_task(self.watch_capacity()),
            ]
            analysis = asyncio.create_task(self.materialize_closed_markets())
            for epoch in capture_epochs:
                if not await self.wait_until(epoch - PREPARE_SECONDS):
                    break
                if len(self.active) >= self.settings["max_pending_markets"]:
                    self.request_stop("PENDING_MARKET_BOUND")
                    break
                slug = f"btc-updown-5m-{epoch}"
                self.active[slug] = asyncio.create_task(self.market(slug, epoch))
            await asyncio.gather(*self.active.values())
            self.stop_at = None
            if not self.stop.is_set():
                self.replay_queue.put_nowait(None)
                stopping = asyncio.create_task(self.stop.wait())
                try:
                    done, _ = await asyncio.wait((analysis, stopping), return_when=asyncio.FIRST_COMPLETED)
                    if analysis in done:
                        await analysis
                finally:
                    stopping.cancel()
                    await asyncio.gather(stopping, return_exceptions=True)
        except BaseException as exc:
            self.errors.append({"error": f"{type(exc).__name__}:{exc}"})
            self.request_stop("RUN_FAILED")
            await asyncio.gather(*self.active.values(), return_exceptions=True)
            raise
        finally:
            if analysis is not None:
                analysis.cancel()
                await asyncio.gather(analysis, return_exceptions=True)
            for loop in loops:
                loop.cancel()
            await asyncio.gather(*loops, return_exceptions=True)
            try:
                await self.api.close()
            finally:
                if epochs or not self.predecessor:
                    terminal = self.finish(epochs)
        return terminal

    def finish(self, epochs):
        if self.predecessor:
            for epoch in self.predecessor["market_epochs"]:
                slug = f"btc-updown-5m-{epoch}"
                directory = self.output / slug
                try:
                    self.reports[slug] = json.loads((directory / "market-report.json").read_text())
                except FileNotFoundError:
                    if (directory / "input.jsonl.zst").is_file():
                        self.closed_markets.add(slug)
            try:
                previous = json.loads((self.output / "completion.json").read_text())
            except FileNotFoundError:
                pass
            else:
                self.errors[:0] = previous["errors"]
                self.peak_bytes = max(self.peak_bytes, previous["peak_bytes"])
        common = [
            row
            for row in self.reports.values()
            if row["complete_window"] and row["official_winner"]
        ]
        metrics = {}
        for name in self.parameters:
            orders = [order for row in common for order in row["strategies"][name][1]]
            signals = sum(row["strategies"][name][0]["signal_count"] for row in common)
            metrics[name] = {"anchor": summary(orders, signals, "anchor")}
        expected = [f"btc-updown-5m-{epoch}" for epoch in epochs]
        terminal = {
            **self.owner,
            "status": "PASS"
            if len(common) == len(epochs) and epochs and not self.stop_reason
            else "PARTIAL",
            "reason": self.stop_reason or "PLANNED_WINDOW_ENDED",
            "planned_markets": len(epochs),
            "retained_markets": sorted(self.reports),
            "captured_unsealed_markets": sorted(self.closed_markets - self.reports.keys()),
            "common_complete_settled_markets": len(common),
            "incomplete_markets": sorted(
                row["market"] for row in self.reports.values() if not row["complete_window"]
            ),
            "pending_settlement": sorted(
                row["market"] for row in self.reports.values() if not row["official_winner"]
            ),
            "missing_markets": [slug for slug in expected if slug not in self.reports],
            "metrics": metrics,
            "fill_model": ANCHOR_FILL_MODEL,
            "sample_scope": PUBLIC_ONLY_SAMPLE,
            "peak_bytes": self.peak_bytes,
            "errors": self.errors,
            "real_orders": 0,
        }
        name = "continuation-completion.json" if self.predecessor else "completion.json"
        write_json(self.output / name, terminal)
        return terminal


async def replay(tape, output, settings, parameters):
    output.mkdir(parents=True, exist_ok=False)
    group = MarketEngines(
        output, settings, parameters, {"schema": SCHEMA, "mode": "OFFLINE_SHARED_REPLAY"}
    )
    try:
        for event in read_tape(tape):
            await group.ingest(event)
        if set(group.engines) != set(parameters) or not all(
            e.ended for e in group.engines.values()
        ):
            raise ValueError("SHARED_REPLAY_SOURCE_NOT_FINISHED")
    finally:
        await group.close()
    result = group.report()
    write_json(output / "market-report.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "run", "extend", "prune", "replay"),
                        default="check", nargs="?")
    parser.add_argument(
        "--storage-root",
        type=Path,
        required=True,
        help="Dedicated directory named m122124_baseline",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path("/home/ubuntu/polymaker"),
        help="Filesystem to protect even when the benchmark output is on another volume",
    )
    parser.add_argument("--run-name")
    parser.add_argument("--tape", type=Path)
    args = parser.parse_args()
    root = args.storage_root.resolve(strict=True)
    if root.name != "m122124_baseline" or not root.is_dir():
        parser.error("--storage-root must be a dedicated existing m122124_baseline directory")
    if not shutil.which("zstd"):
        parser.error("zstd executable is required")
    settings, parameters = configuration()
    project = args.project_root.resolve(strict=True)
    if args.action == "check":
        storage = storage_status(root, project, used_bytes=0)
        print(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "status": "FAIL" if storage["reason"] else "PASS",
                    "market_count": MARKETS,
                    "strategies": list(parameters),
                    "fill_model": ANCHOR_FILL_MODEL,
                    "storage": storage,
                    "real_orders": 0,
                }
            )
        )
        return
    if (
        not args.run_name
        or Path(args.run_name).name != args.run_name
        or args.run_name in {".", ".."}
    ):
        parser.error("a single --run-name is required")
    output = root / args.run_name
    if output.is_symlink():
        parser.error("--run-name must not resolve through a symlink")
    if args.action == "prune":
        plan = json.loads((output / "plan.json").read_text())
        if plan["schema"] != SCHEMA or plan["mode"] != "SIMULATION_ONLY_PUBLIC_DATA":
            parser.error("prune requires a shared baseline recording")
        print(json.dumps(prune_expired_tapes(output, time.time())), flush=True)
        return
    if args.action == "replay":
        if args.tape is None:
            parser.error("--tape is required for replay")
        asyncio.run(replay(args.tape, output, settings, parameters))
        return
    def run(predecessor):
        disk = storage_status(root, project, used_bytes=0)
        if disk["reason"]:
            raise SystemExit(json.dumps(disk))

        async def supervise():
            session = SharedRun(output, project, settings, parameters, predecessor=predecessor)
            for sig in (signal.SIGINT, signal.SIGTERM):
                asyncio.get_running_loop().add_signal_handler(
                    sig, session.request_stop, "MANUAL_STOP"
                )
            return await session.run()

        if asyncio.run(supervise())["status"] != "PASS":
            raise SystemExit(2)

    # The legacy runner holds runner.lock while its last markets settle. The
    # extension captures only the previously unscheduled tail of that run.
    with (root / "seven-day.lock").open("a") as extension_lock:
        fcntl.flock(extension_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.action == "extend":
            prior = json.loads((output / "plan.json").read_text())
            completion = output / "completion.json"
            if completion.is_file() and json.loads(completion.read_text())["reason"] == "MANUAL_STOP":
                parser.error("cannot extend a manually stopped baseline")
            epochs = prior["market_epochs"]
            if (prior["schema"] != SCHEMA or prior["mode"] != "SIMULATION_ONLY_PUBLIC_DATA"
                    or prior["path"] != str(output) or "continuation_start_epoch" in prior
                    or len(epochs) != LEGACY_MARKETS or epochs != list(range(
                        epochs[0], epochs[0] + LEGACY_MARKETS * MARKET_SECONDS, MARKET_SECONDS
                    ))):
                parser.error("extend requires the original contiguous 72-hour plan")
            for key in ("strategy_definitions", "strategy_revisions", "sop_execution"):
                if prior["settings"][key] != settings[key]:
                    parser.error(f"cannot extend with a different {key}")
            run(prior)
        else:
            with (root / "runner.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                output.mkdir(exist_ok=False)
                run(None)


if __name__ == "__main__":
    main()
