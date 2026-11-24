"""Continuous public-only M1 signals, virtual orders and official settlement.

There is no trading SDK, private key, signing, account or order endpoint here.
Both the transport and CLI forbid live POST. PnL assumes full fills explicitly.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import gc
import hashlib
import json
import multiprocessing
import os
import signal
import time
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager, suppress
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from polymaker.config import RDPM1Config, RDPObiConfig
from polymaker.m1_live_config import M1LiveConfig
from polymaker.m1_live_signal import M1LiveSignals
from polymaker.m1_virtual_api import VirtualPublicAPI
from polymaker.m1_virtual_execution import SharedVirtualPolicy as VirtualPolicy
from polymaker.m1_virtual_state import VirtualStore
from polymaker.rdp_sm_ca import plain
from polymaker.rdp_sm_ca_writer import CAWriter
from polymaker.rdp_sm_m1_policy import (
    BASELINE_MAX_INSTRUCTIONS,
    BASELINE_QUANTITY_STEP,
    BASELINE_TARGET_USDC,
)
from polymaker.rdp_sm_marketdata import SessionFeed

TZ = ZoneInfo("Asia/Shanghai")


class GCDiagnostics:
    """Measure interpreter pauses without logging or allocating large rows inside GC."""

    def __init__(self):
        self.started = {}
        self.events = deque(maxlen=64)
        self.during_entry = False

    def callback(self, phase, info):
        generation = info["generation"]
        if generation < 1:
            return
        if phase == "start":
            self.started[generation] = (time.monotonic(), time.thread_time())
        elif generation in self.started:
            before, cpu = self.started.pop(generation)
            duration = time.monotonic()-before
            if duration > .020:
                self.events.append({"duration_ms": duration*1000,
                    "cpu_ms": (time.thread_time()-cpu)*1000, "generation_collected": generation,
                    "collected": info.get("collected"), "uncollectable": info.get("uncollectable"),
                    "wall": time.time(), "during_entry": self.during_entry})


class GCWindow:
    """Defer cyclic GC until market entry ends; reference counting stays active."""

    def __init__(self, diagnostics):
        self.diagnostics, self.was_enabled, self.released = diagnostics, gc.isenabled(), False
        gc.disable()
        try:
            gc.collect()  # Before subscribing, with no live frames to delay.
        except BaseException:
            if self.was_enabled:
                gc.enable()
            raise
        diagnostics.during_entry = True

    def release(self):
        if self.released:
            return
        self.released = True
        self.diagnostics.during_entry = False
        if self.was_enabled:
            gc.enable()
            gc.collect()  # Only after the public feed has stopped.


@contextmanager
def measured(operation, emit, **context):
    started, cpu = time.monotonic(), time.thread_time()
    try:
        yield
    finally:
        duration = time.monotonic()-started
        if duration > .020:
            emit("LOCAL_OPERATION_SLOW", {"operation": operation, "duration_ms": duration*1000,
                "cpu_ms": (time.thread_time()-cpu)*1000, "wall": time.time(), **context})


class VirtualFeed(SessionFeed):
    def apply(self, raw, **kwargs):
        def diagnostic(kind, data):
            self.m1.emit(kind, **data)
        with measured("public_frame_apply", diagnostic, pending_frames=len(self.m1_pending_received),
                      frame_size=len(raw) if isinstance(raw, (str, bytes, list)) else None):
            return super().apply(raw, **kwargs)


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix+".tmp")
    temporary.write_text(json.dumps(plain(value), ensure_ascii=False, indent=2)+"\n")
    temporary.replace(path)


def deployment_fingerprint(settings):
    root = Path(__file__).parent
    names = ("m1_virtual.py", "m1_virtual_state.py", "m1_virtual_api.py", "m1_live_signal.py",
             "m1_virtual_execution.py", "m1_live.py", "m1_live_state.py", "m1_live_account.py",
             "m1_live_retry.py", "rdp_sm_execution.py", "execution/gateway.py",
             "rdp_sm_m1_signal.py", "m1_live_config.py", "rdp_sm_marketdata.py", "rdp_sm_m1_policy.py",
             "rdp_sm_ca_writer.py", "config.py", "dashboard/settlement.py")
    return {"settings": settings.fingerprint,
            "source": {name: hashlib.sha256((root/name).read_bytes()).hexdigest() for name in names}}


def baseline_settings(settings):
    return {**settings.model_dump(mode="json", exclude={"order_budget_usdc", "session_budget_usdc"}),
        "order_amount_mode": "TARGET_MINIMUM",
        "target_spend_usdc": str(BASELINE_TARGET_USDC),
        "quantity_step": str(BASELINE_QUANTITY_STEP), "quantity_rounding": "CEILING",
        "max_successful_instructions": BASELINE_MAX_INSTRUCTIONS*2,
        "max_opportunities": BASELINE_MAX_INSTRUCTIONS,
        "max_uncertain_retries_per_opportunity": 1, "retry_price": "SIGNAL_UNLESS_CROSSING_REJECTION",
        "prepost_engine": "M1Market", "account_mutex": False, "fill_tracking": "NOT_TRACKED",
        "hold_policy": "PLATFORM_AUTO_SETTLEMENT", "market_end_actions": "PLATFORM_MANAGED",
        "account_source": "MODELED_ACCOUNT_20_USDC_PER_MARKET",
        "signer_source": "DISPOSABLE_UNFUNDED_KEY", "execution_boundary": "NATIVE_ORDER_POST"}


def timestamp(wall):
    return datetime.fromtimestamp(wall, TZ).isoformat(timespec="milliseconds")


def write_report(directory, report, runtime=None):
    report.update(observed_at=time.time(), runtime=runtime)
    atomic_json(directory/"latest.json", report)
    lines = ["# M1 虚拟执行验收", "", f"更新：{timestamp(report['observed_at'])}", "",
             "只读公共行情；真实 POST 为 0。按指令价足额成交、持有至官方结算的假设计算；不含手续费、返佣和 gas。", "",
             f"连续正常场：{report['continuous_normal_markets']}；连续完整结算报告："
             f"{report['consecutive_complete_reports']} / 12。阶段仍待用户确认。", "",
             f"已结算模拟 PnL：{report['settled_pnl']} USDC。", "",
             "| 场次（北京时间） | 完整窗口 | 问题数 | 信号／虚拟成功 | 官方胜方 | 模拟 PnL |",
             "| --- | --- | --- | --- | --- | --- |"]
    for market in report["markets"]:
        orders = market["instructions"]
        posted = sum(o["status"] == "VIRTUAL_SUCCESS" for o in orders)
        signals = len({json.loads(o["evidence"]).get("source_opportunity_id", o["id"]) for o in orders})
        lines.append(f"| {timestamp(market['epoch'])} | {bool(market['full_window'])} | {market['issues']} | "
                     f"{signals}／{posted} | {market['winner'] or '待结算'} | "
                     f"{market['pnl'] if market['pnl'] is not None else '待确认'} |")
    for market in report["markets"]:
        lines += ["", f"## {market['slug']}", ""]
        for event in market["timeline"]:
            data = event["data"]
            text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
            lines.append(f"- {timestamp(event['wall'])} {event['kind']}：{text}")
    target = directory/"report.md"
    temporary = target.with_suffix(".md.tmp")
    temporary.write_text("\n".join(lines)+"\n")
    temporary.replace(target)
    return report


def refresh_report(directory, runtime):
    # Do not transfer a large report graph back to the market process.
    write_report(directory, VirtualStore.read_report(directory), runtime)


def initialize_report_worker():
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    gc.enable()
    os.nice(10)


class VirtualSignals(M1LiveSignals):
    """Unchanged detector decisions with bounded rejected-input diagnostics."""

    def tick(self, *, books=None):
        def diagnostic(kind, data):
            if not self.failure:
                self.emit(kind, **data)
        with measured("signal_tick", diagnostic, pending_frames=len(self.feed.m1_pending_received)):
            return super().tick(books=books)

    def before_event(self, kind, row, book, received, wall, *, books=None):
        accepted = super().before_event(kind, row, book, received, wall, books=books)
        if not accepted and self.decision_reason in {
            "M1_SOURCE_AGE", "M1_SOURCE_REGRESSION", "M1_PROCESSING_GAP",
        }:
            now = self.feed.monotonic()
            last = getattr(self, "_diagnostic_last", {})
            key = (self.decision_reason, book.token_id)
            if now-last.get(key, -1e9) < 1:
                return accepted
            last[key] = now
            self._diagnostic_last = last
            self.emit("INPUT_REJECTED", kind=kind, reason=self.decision_reason, token_id=book.token_id,
                source_timestamp=row.get("timestamp"), received_wall=wall,
                source_age_ms=(self.feed.wall()-float(row["timestamp"])/1000)*1000,
                processing_age_ms=(self.feed.monotonic()-received)*1000,
                pending_frames=len(self.feed.m1_pending_received),
                row_changes=len(row.get("price_changes", [])), frame=self.feed.m1_frame)
        return accepted


class VirtualService:
    def __init__(self, settings, output, *, stop=None, display=None, api_factory=VirtualPublicAPI,
                 feed_factory=VirtualFeed, exercise_failover=False):
        self.settings, self.store = settings, VirtualStore(output)
        self.stop, self.display = stop or asyncio.Event(), display or self.print_event
        self.api_factory, self.feed_factory = api_factory, feed_factory
        self.api = self.new_api()
        self.clock = None
        self.current_slug = None
        self.resolve_task = None
        self.report_task = None
        self.report_pool = ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"),
                                               initializer=initialize_report_worker)
        self.resolve_slug = None
        self.resolve_retry = {}
        self.exercise_failover = exercise_failover
        self.clock_poll_sec = 10
        self.gc_diagnostics = GCDiagnostics()
        self.runtime = {"state": "STARTING", "mode": "VIRTUAL_ONLY", "real_posts": 0}

    def new_api(self):
        return self.api_factory(on_failover=self.failover)

    def failover(self, kind, reason, context=None):
        context = context or {}
        payload = {"reason": str(reason)[:200], "real_post": False, **context}
        if context.get("purpose") == "settlement":
            # A retry of an old result neither loses current public frames nor
            # makes their market abnormal. Final report counting already waits
            # for official settlement rather than inventing a zero outcome.
            self.store.event(kind, {**payload, "streak_reset": False}, context["slug"])
        else:
            self.store.incident(kind, reason, self.current_slug or context.get("slug"))
        self.emit(kind, payload)

    def emit(self, kind, data):
        self.display(kind, plain(data))

    def publish(self):
        with measured("report_metrics", self.emit, slug=self.current_slug):
            report = self.store.report(details=False)
        if self.report_task is None or self.report_task.done():
            if self.report_task:
                self.report_task.result()
            self.report_task = asyncio.get_running_loop().run_in_executor(
                self.report_pool, refresh_report, self.store.directory, dict(self.runtime))
        return report

    async def close_reporter(self):
        if self.report_task:
            await self.report_task
        await asyncio.to_thread(self.report_pool.shutdown, wait=True)

    @staticmethod
    def print_event(kind, data):
        print("M1 VIRTUAL_ONLY | "+kind+" | "+json.dumps(plain(data), ensure_ascii=False), flush=True)

    async def wait(self, seconds):
        with suppress(TimeoutError):
            await asyncio.wait_for(self.stop.wait(), timeout=seconds)

    async def refresh_clock(self):
        self.clock = await self.api.clock()
        return self.clock

    async def clock_probe(self, lower):
        if self.exercise_failover and lower > 35 and not self.store.get("http_drill_done"):
            self.store.set("http_drill_done", True)
            raise TimeoutError("CONTROLLED_PUBLIC_READ_FAILOVER_TEST")
        if self.exercise_failover and lower > 55 and not self.store.get("http_400_drill_done"):
            self.store.set("http_400_drill_done", True)
            request = httpx.Request("GET", "https://clob.polymarket.com/time")
            response = httpx.Response(400, request=request, text="CONTROLLED_CLOUDFLARE_400_TEST")
            response.raise_for_status()
        return await self.refresh_clock()

    def bounds(self, epoch):
        server, sent, received = self.clock
        now = time.monotonic()
        return server+now-received-epoch, server+1+now-sent-epoch

    async def recover_api(self, stage, exc):
        self.runtime.update(state="RECOVERING", reason=f"{stage}:{type(exc).__name__}")
        self.failover("PUBLIC_READ_RECOVERY", f"{stage}:{type(exc).__name__}:{str(exc)[:160]}")
        with suppress(Exception):
            await self.api.close()
        self.api = self.new_api()
        self.publish()
        await self.wait(2)

    async def resolutions(self, *, schedule=True):
        if self.resolve_task and self.resolve_task.done():
            slug = self.resolve_slug
            try:
                result = self.resolve_task.result()
                if result is not None:
                    with measured("settlement_commit", self.emit, slug=slug):
                        self.store.settle(slug, result)
                    self.emit("OFFICIAL_RESOLUTION", {"slug": slug, "winner": result["winner"],
                        "market_pnl": self.store.db.execute("SELECT pnl FROM markets WHERE slug=?", (slug,)).fetchone()[0],
                        "simulation": True, "real_post": False})
                self.resolve_retry[slug] = time.monotonic()+15
            except Exception as exc:
                self.store.event("RESOLUTION_RETRY", {"reason": type(exc).__name__, "pnl": None}, slug)
                with self.store.db:
                    self.store.db.execute("UPDATE markets SET resolution_error=? WHERE slug=?", (type(exc).__name__, slug))
                self.resolve_retry[slug] = time.monotonic()+15
            self.resolve_task = self.resolve_slug = None
            self.publish()
        if schedule and self.resolve_task is None:
            for row in self.store.pending_resolutions():
                if self.resolve_retry.get(row["slug"], 0) > time.monotonic():
                    continue
                meta = json.loads(row["metadata"])
                expected = {"slug": row["slug"], "condition_id": meta["condition_id"],
                            "token_ids": {"YES": meta["tokens"][0]["token_id"], "NO": meta["tokens"][1]["token_id"]}}
                self.resolve_slug = row["slug"]
                self.resolve_task = asyncio.create_task(self.api.resolution(expected))
                break

    async def market(self, meta):
        settings, slug = self.settings, meta.slug
        epoch = int(slug.rsplit("-", 1)[1])
        self.current_slug = slug
        lower, _ = self.bounds(epoch)
        self.store.add_market(asdict(meta), lower)
        folder = self.store.directory/"sessions"/f"{slug}-{time.time_ns()}"
        folder.mkdir(parents=True)
        writer = CAWriter(folder/"signals.jsonl", capacity=4096)
        feed = self.feed_factory(meta, RDPObiConfig(), normal_entry_mode="m1_v1",
                                m1_config=RDPM1Config(observation_only=True, signal=settings.signal,
                                    out_of_order_tolerance_ms=settings.out_of_order_tolerance_ms, record_events=False))
        closing, components, incidents, last_diagnostic = False, deque(), deque(), {}

        def record(row):
            kind = row["event"]
            observed_wall = time.time()
            if kind == "GAP" and not closing:
                incidents.append(("WS_FAILOVER", row.get("reason"), slug, observed_wall))
            if kind == "SIGNAL_INVALIDATED" and not closing and row.get("reason") in {
                "M1_SOURCE_REGRESSION", "M1_SOURCE_AGE", "M1_PROCESSING_GAP", "M1_INPUT_BACKLOG",
                "M1_ROUND_PROCESSING_LATE", "M1_EVENT_RECORDING_FAILED",
            }:
                incidents.append(("DATA_INTEGRITY_RESET", row["reason"], slug, observed_wall))
            if kind in {"RF_CONFIRMED", "EX_CONFIRMED", "AP_CONFIRMED", "OPPORTUNITY_IDENTIFIED", "GAP", "SIGNAL_INVALIDATED", "INPUT_REJECTED", "LOCAL_OPERATION_SLOW", "LOOP_DELAY"}:
                if kind == "SIGNAL_INVALIDATED":
                    key = (row.get("reason"), row.get("token_id"))
                    if row["available_monotonic"]-last_diagnostic.get(key, -1e9) < 1:
                        return writer.failure is None
                    last_diagnostic[key] = row["available_monotonic"]
                payload = {**plain(row), "planned_shutdown": closing, "observed_wall": observed_wall}
                if kind == "GAP":
                    payload["transport_failure"] = feed.last_failure
                    if not closing:
                        self.emit("WS_RECOVERY", {"slug": slug, "generation": feed.generation,
                            "reason": row.get("reason"), "last_failure": feed.last_failure})
                if kind == "SIGNAL_INVALIDATED":
                    payload["pending_age_ms"] = (feed.monotonic()-feed.m1_watermark())*1000
                if kind != "AP_CONFIRMED":
                    components.append((kind, payload))
                return writer.submit(payload)
            return writer.failure is None

        feed.m1 = VirtualSignals(feed, signal_config=settings.signal, record=record,
                              out_of_order_tolerance_ms=settings.out_of_order_tolerance_ms)
        full_window, done = False, False
        heartbeat, clock_refresh = 0., time.monotonic()+self.clock_poll_sec
        clock_task = None
        lag_task = None
        revision = 0
        gc_window = None
        policy = None
        try:
            gc_window = GCWindow(self.gc_diagnostics)
            feed.verify_ticks(await self.api.ticks(feed.books))
            revision = feed.tick_revision
            feed.task = asyncio.create_task(feed.run(), name="m1-virtual-public-feed")

            async def watch_loop():
                while not self.stop.is_set() and not closing:
                    before, cpu = time.monotonic(), time.thread_time()
                    await asyncio.sleep(.025)
                    lag = time.monotonic()-before-.025
                    if lag > .050:
                        feed.m1.emit("LOOP_DELAY", delay_ms=lag*1000,
                            cpu_ms=(time.thread_time()-cpu)*1000,
                            pending_frames=len(feed.m1_pending_received),
                            pending_age_ms=max(0., feed.monotonic()-feed.m1_watermark())*1000)

            lag_task = asyncio.create_task(watch_loop(), name="m1-virtual-loop-diagnostics")
            policy = VirtualPolicy(feed, settings, self.store, self.emit, stop=self.stop)
            while not self.stop.is_set():
                lower, upper = self.bounds(epoch)
                now = time.monotonic()
                if lower >= settings.close_sec:
                    done = True
                    break
                self.runtime = {"state": "OBSERVING" if upper < settings.cutoff_sec else "WAIT_SETTLEMENT",
                    "slug": slug, "elapsed": round(lower, 2), "connected": feed.connected,
                    "mode": "VIRTUAL_ONLY", "real_posts": 0}
                if not full_window and upper <= settings.start_sec and feed.connected and all(
                    b.snapshot_generation == feed.generation and b.bids and b.asks for b in feed.books.values()):
                    full_window = True
                if clock_task is not None and clock_task.done():
                    try:
                        clock_task.result()
                    except Exception as exc:
                        await self.recover_api("clock", exc)
                    clock_task = None
                if now >= clock_refresh and clock_task is None:
                    clock_refresh = now+self.clock_poll_sec
                    clock_task = asyncio.create_task(self.clock_probe(lower), name="m1-virtual-public-clock")
                if now-self.clock[2] > 60:
                    await self.wait(.25)
                    continue
                while self.gc_diagnostics.events:
                    data = self.gc_diagnostics.events.popleft()
                    feed.m1.emit("LOCAL_OPERATION_SLOW", operation="interpreter_gc", **data)
                if upper < settings.cutoff_sec:
                    if feed.tick_revision != revision:
                        try:
                            feed.verify_ticks(await self.api.ticks(feed.books))
                            revision = feed.tick_revision
                        except Exception as exc:
                            await self.recover_api("tick", exc)
                            continue
                    with measured("instruction_poll_and_commit", self.emit, slug=slug):
                        await policy.poll(lower, upper)
                elif not closing:
                    closing = True
                    await feed.close()
                    gc_window.release()
                    self.store.event("ENTRY_CUTOFF", {"elapsed": lower, "real_posts": 0}, slug)
                if (self.exercise_failover and lower > 70 and feed.ws is not None
                        and not self.store.get("ws_drill_done")):
                    self.store.set("ws_drill_done", True)
                    self.store.incident("CONTROLLED_WS_FAILOVER_TEST", "Close only this public socket", slug)
                    await feed.ws.close()
                if components or incidents:
                    events = [(kind, data, slug, data["observed_wall"]) for kind, data in components]
                    with measured("observation_commit", self.emit, slug=slug):
                        self.store.observation_batch(events, list(incidents))
                    components.clear()
                    incidents.clear()
                await self.resolutions()
                if writer.failure:
                    raise RuntimeError(writer.failure)
                if not closing and feed.task.done():
                    feed.task.result()
                    raise RuntimeError("PUBLIC_FEED_TASK_EXITED")
                if now >= heartbeat:
                    heartbeat = now+settings.heartbeat_sec
                    commands = self.store.instructions(slug)
                    self.runtime.update(signals_found=len({json.loads(r["evidence"]).get("source_opportunity_id", r["id"])
                                                          for r in commands}),
                        virtual_posts=sum(r["status"] == "VIRTUAL_SUCCESS" for r in commands),
                        signal_counts=dict(feed.m1.counts))
                    report = self.publish()
                    self.emit("HEARTBEAT", {k:v for k,v in self.runtime.items() if k != "signal_counts"} |
                        {"continuous_normal_markets": report["continuous_normal_markets"],
                         "complete_reports": report["consecutive_complete_reports"], "settled_pnl": report["settled_pnl"],
                         "AP_SELL": feed.m1.counts["AP_CONFIRMED_SELL"], "RF": feed.m1.counts["RF_CONFIRMED"],
                         "EX": feed.m1.counts["EX_CONFIRMED"], "rejected_inputs": feed.m1.counts["UNAVAILABLE"]})
                await self.wait(.01)
        except Exception as exc:
            self.store.incident("MARKET_RECOVERY", f"{type(exc).__name__}:{str(exc)[:160]}", slug)
            self.emit("MARKET_RECOVERY", {"slug": slug, "reason": type(exc).__name__})
        finally:
            closing = True
            if policy is not None:
                await policy.close(completed=done)
            if clock_task is not None:
                clock_task.cancel()
                await asyncio.gather(clock_task, return_exceptions=True)
            if lag_task is not None:
                lag_task.cancel()
                await asyncio.gather(lag_task, return_exceptions=True)
            await feed.close()
            if gc_window is not None:
                gc_window.release()
            if components or incidents:
                self.store.observation_batch(
                    [(kind, data, slug, data["observed_wall"]) for kind, data in components], list(incidents))
            await asyncio.to_thread(writer.close)
            counts = dict(feed.m1.counts)
            counts.update(_transport_failures=feed.failure_count, _last_transport_failure=feed.last_failure,
                          _ws_generation=feed.generation)
            self.store.finish_market(slug, full_window=full_window, counts=counts, interrupted=not done)
            self.publish()
            self.current_slug = None

    async def run(self, *, once=False):
        self.store.boot(deployment_fingerprint(self.settings))
        self.store.set("settings", baseline_settings(self.settings))
        self.emit("SERVICE_BOOT", {"mode": "VIRTUAL_ONLY", "real_posts": 0,
            "target_spend_usdc": str(BASELINE_TARGET_USDC),
            "quantity_rounding": "CEILING", "quantity_step": str(BASELINE_QUANTITY_STEP),
            "max_successful_instructions": BASELINE_MAX_INSTRUCTIONS*2,
            "max_opportunities": BASELINE_MAX_INSTRUCTIONS,
            "required_consecutive_reports": 12, "user_confirmation_required": True})
        retry = 0
        waiting_heartbeat = 0.
        gc_callback = self.gc_diagnostics.callback
        gc.callbacks.append(gc_callback)
        try:
            while not self.stop.is_set():
                try:
                    server, _, _ = await self.refresh_clock()
                    epoch = int(server//300)*300
                    slug = f"btc-updown-5m-{epoch}"
                    existing = self.store.db.execute("SELECT state FROM markets WHERE slug=?", (slug,)).fetchone()
                    if server-epoch >= self.settings.cutoff_sec or existing:
                        await self.resolutions()
                        self.runtime = {"state": "WAIT_NEXT_MARKET", "slug": slug, "mode": "VIRTUAL_ONLY", "real_posts": 0}
                        if time.monotonic() >= waiting_heartbeat:
                            waiting_heartbeat = time.monotonic()+self.settings.heartbeat_sec
                            report = self.publish()
                            self.emit("HEARTBEAT", self.runtime | {"complete_reports": report["consecutive_complete_reports"]})
                        await self.wait(1)
                        if once and existing:
                            break
                        continue
                    meta = await self.api.market(slug)
                    retry = 0
                    await self.market(meta)
                    if once:
                        break
                except Exception as exc:
                    retry += 1
                    await self.recover_api("discovery", exc)
                    await self.wait(min(20, 2**min(retry, 4)))
        finally:
            gc.callbacks.remove(gc_callback)
            if self.resolve_task:
                with suppress(Exception):
                    await self.resolve_task
                await self.resolutions(schedule=False)
            await self.api.close()
            self.runtime = {"state": "STOPPED", "mode": "VIRTUAL_ONLY", "real_posts": 0}
            if self.report_task:
                await self.report_task
            await asyncio.get_running_loop().run_in_executor(
                self.report_pool, refresh_report, self.store.directory, self.runtime)
            await self.close_reporter()
            self.store.close()


async def run_virtual(config_dir="config", output="research_outputs/m1_virtual", *, once=False,
                      preflight_only=False, exercise_failover=False):
    settings = M1LiveConfig.load(config_dir)  # Deliberately never Config.load/.env/Secrets.
    if preflight_only:
        api = VirtualPublicAPI()
        try:
            server, _, _ = await api.clock()
            return {"mode": "VIRTUAL_ONLY", "real_post_enabled": False, "official_time": server,
                    "settings": baseline_settings(settings)}
        finally:
            await api.close()
    directory = await asyncio.to_thread(Path(output).resolve)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory/"observer.lock").open("a+") as lock:  # noqa: ASYNC240 -- Acquire the local process lock before service startup.
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        handlers = []
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signum, stop.set)
                handlers.append(signum)
            except (NotImplementedError, ValueError):
                pass
        service = VirtualService(settings, directory, stop=stop, exercise_failover=exercise_failover)
        try:
            await service.run(once=once)
        finally:
            for signum in handlers:
                loop.remove_signal_handler(signum)
    return {"mode": "VIRTUAL_ONLY", "real_posts": 0, "output": str(directory)}


def main():
    parser = argparse.ArgumentParser(description="Read-only M1 virtual execution and settlement")
    parser.add_argument("--config-dir", default="config")
    parser.add_argument("--output-dir", default="research_outputs/m1_virtual")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--exercise-failover", action="store_true")
    args = parser.parse_args()
    from polymaker.m4_control import legacy_entry_guard
    legacy_entry_guard()
    asyncio.run(run_virtual(args.config_dir, args.output_dir, once=args.once,
                            exercise_failover=args.exercise_failover))


if __name__ == "__main__":
    main()
