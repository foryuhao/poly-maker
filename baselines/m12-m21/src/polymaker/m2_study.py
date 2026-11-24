"""Independent public M2 candidate collector. No account or execution client.

Run with ``python -m polymaker.m2_study``. Never modifies the M1 services.
"""

import argparse
import asyncio
import fcntl
import hashlib
import json
import shutil
import signal
import time
from contextlib import suppress
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from polymaker.config import RDPM1Config, RDPObiConfig
from polymaker.m1_virtual_api import VirtualPublicAPI
from polymaker.m2_config import M2Config
from polymaker.m2_memory import recycle_idle_memory
from polymaker.m2_resources import CPUHeadroom
from polymaker.m2_signal import M2EventBuffer, M2Signals
from polymaker.m2_store import MODEL, M2Store, encode
from polymaker.rdp_sm_marketdata import SessionFeed

ROOT = next(parent for parent in Path(__file__).resolve().parents
            if (parent/"pyproject.toml").is_file() and (parent/"src/polymaker").is_dir())
OUTPUT = ROOT/"research_outputs"/"m2_virtual"


def output_path(value):
    path = Path(value).resolve()
    if path != OUTPUT and OUTPUT not in path.parents:
        raise ValueError("M2_OUTPUT_MUST_BE_UNDER_RESEARCH_OUTPUTS_M2_VIRTUAL")
    return path


def host_cpu_pressure():
    """Linux PSI: percent of recent time at least one runnable task waited."""
    try:
        row = Path("/proc/pressure/cpu").read_text().splitlines()[0]
        fields = dict(item.split("=", 1) for item in row.split()[1:])
        value = float(fields["avg10"])
        return value if 0 <= value <= 100 else 100.
    except (OSError, KeyError, ValueError, IndexError):
        # Unknown capacity cannot justify starting another research feed.
        return 100.


def source_fingerprint(settings):
    root = Path(__file__).parent
    names = ("m2_config.py", "m2_signal.py", "m2_store.py", "m2_study.py", "m2_report.py", "m2_resources.py", "m2_memory.py",
             "rdp_sm_m1_signal.py", "rdp_sm_marketdata.py", "config.py",
             "m1_virtual_api.py", "rdp_sm_m1_observe.py", "dashboard/settlement.py")
    if settings.include_m3_m4:
        names += ("m234_signal.py", "m4_rounds.py", "maker_queue.py", "rdp_sm_ca.py", "rdp_sm_ba.py")
    if settings.m4_frequency_study:
        names += ("m4_frequency_signal.py",)
    sources = {name: hashlib.sha256((root/name).read_bytes()).hexdigest() for name in names}
    version = hashlib.sha256(encode({"settings": settings.fingerprint, "sources": sources}).encode()).hexdigest()
    return version, sources


def write_report(directory, report):
    # Only this observer owns these files; use replacement to avoid partial reads.
    latest = directory/"latest.json"
    temporary = directory/"latest.json.tmp"
    temporary.write_text(encode(report)+"\n")
    temporary.replace(latest)
    settings = report["settings"]
    lines = ["# M2 候选信号研究", "",
             f"全合法价格；单笔目标风险 {settings['risk_usdc']} USDC，实际成本向下取整；"
             f"单规则单场额度 {settings['market_budget_usdc']} USDC。",
             f"每个规则每场每方向最多 {settings['max_entries_per_direction']} 笔假设成交；"
             "超过上限的信号仅供研究，不计策略收益。",
             "按观察时的锚定价假设足额成交，持有至官方结算；不代表 Maker 实际成交。",
             "BUY 减弱比例为探索分组，尚未选择正式阈值；各组独立模拟，不能相加成一个组合。",
             "没有锚点位置门槛；进攻锚、承接锚与位置关系保留在账本。",
             "首次满足一项规则时每个 AP_BUY 只记一次；方向上限或额度外样本仍保留假设结果。",
             "异常/不完整市场保留并单列；未确认胜负保持待结算。", "",
             f"真实下单：0；场次：{report['market_count']}；候选：{report['candidate_count']}；"
             f"观察：{report['observation_count']}。", "",
             "| 版本 | 研究组 | 样本 | 假设成交 | 已结算 | 待结算 | 已结算成本 | 已结算收益 |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for row in report["rules"]:
        lines.append(f"| {row['version'][:8]} | {row['rule']} | {row['samples']} | "
                     f"{row['assumed_fills']} | {row['settled_fills']} | {row['pending_fills']} | "
                     f"{row['settled_cost']} | {row['settled_pnl']} |")
    if report.get('strategies'):
        lines.extend(['', '## 同源盘口三类信号', '',
            'M2主规则为all_candidates；减弱比例组保留研究标签。三类分别每场每边最多一手，风险至多5 USDC。',
            '均为原有假设成交口径，真实订单为0。', '',
            '| 策略 | 主样本 | 假设成交 | 已结算 | 待结算 | 已结算收益 |',
            '| --- | --- | --- | --- | --- | --- |'])
        for strategy, row in report['strategies'].items():
            lines.append(f"| {strategy} | {row['samples']} | {row['assumed_fills']} | "
                         f"{row['settled_fills']} | {row['pending_fills']} | {row['settled_pnl']} |")
    temporary = directory/"report.md.tmp"
    temporary.write_text("\n".join(lines)+"\n")
    temporary.replace(directory/"report.md")


class M2Study:
    def __init__(self, settings, directory, *, stop=None, api=None, feed_factory=SessionFeed,
                 resource_probe=None):
        self.settings, self.directory = settings, Path(directory)
        self.version, self.sources = source_fingerprint(settings)
        self.store = M2Store(directory, settings, self.version)
        self.store.recover()
        self.api = api or VirtualPublicAPI()
        if settings.include_m3_m4 and feed_factory is SessionFeed:
            from polymaker.m234_signal import M234Feed

            feed_factory = M234Feed
        self.feed_factory = feed_factory
        self.resource_probe = resource_probe if resource_probe is not None else CPUHeadroom()
        self.stop = stop or asyncio.Event()
        self.clock = None
        self.resolve_task = None
        self.resolve_slug = None
        self.retry_resolution = {}
        self.resolution_scan_due = 0.
        self.report_task = None
        self.pending_report = None
        self.runtime = {"state": "STARTING", "real_posts": 0}

    def log(self, event, **payload):
        print(encode({"event": event, "wall": time.time(), "mode": "M2_PUBLIC_RESEARCH",
                      "real_posts": 0, **payload}), flush=True)

    def publish(self):
        # One worker plus one replaceable pending snapshot: frequent resolutions
        # cannot enqueue unbounded reports or block first feed/market rollover.
        self.pending_report = (time.time(), deepcopy(self.runtime))
        if self.report_task is None:
            self.report_task = asyncio.create_task(self._publish_reports(), name="m234-report")

    async def _publish_reports(self):
        try:
            while self.pending_report is not None:
                observed_at, runtime = self.pending_report
                self.pending_report = None
                started = time.monotonic()
                try:
                    await asyncio.to_thread(self._write_report, observed_at, runtime)
                    self.log("REPORT_WRITTEN", duration_ms=(time.monotonic()-started)*1000,
                             runtime_observed_at=observed_at)
                except Exception as exc:
                    # A report failure is visible but must not stop collection
                    # or change official-result/entry eligibility.
                    self.log("REPORT_RETRY", reason=type(exc).__name__)
        finally:
            self.report_task = None

    def _write_report(self, observed_at, runtime):
        report = M2Store.read_report(self.directory, self.settings, self.version, runtime)
        report["runtime_observed_at"] = observed_at
        write_report(self.directory, report)

    async def close_reports(self):
        # Only shutdown waits for reporting; no thread may outlive the observer
        # lock and overwrite a replacement process's latest.json.
        while self.report_task is not None:
            await self.report_task

    def bounds(self, epoch, mono=None):
        server, sent, received = self.clock
        now = time.monotonic() if mono is None else mono
        return server+now-received-epoch, server+1+now-sent-epoch

    def recycle_memory(self, epoch, slug, run_id):
        """Prune only settled retry keys; pending official results stay intact."""
        lower, upper = self.bounds(epoch)
        try:
            settled = []
            for key in self.retry_resolution:
                if key == self.resolve_slug:
                    continue
                row = self.store.db.execute("SELECT winner FROM markets WHERE slug=?", (key,)).fetchone()
                if row is not None and row["winner"] is not None:
                    settled.append(key)
            for key in settled:
                self.retry_resolution.pop(key)
            metrics = recycle_idle_memory()
            after_lower, after_upper = self.bounds(epoch)
            self.log("MEMORY_RECYCLED", slug=slug, run_id=run_id,
                     elapsed_lower=lower, elapsed_upper=upper,
                     ended_elapsed_lower=after_lower, ended_elapsed_upper=after_upper,
                     settled_retry_keys_removed=len(settled), **metrics)
        except Exception as exc:
            self.log("MEMORY_RECYCLE_FAILED", slug=slug, run_id=run_id,
                     elapsed_lower=lower, elapsed_upper=upper, reason=type(exc).__name__)

    def storage_ok(self):
        cfg = self.settings
        size = sum(p.stat().st_size for p in self.directory.iterdir() if p.is_file())
        return (size < cfg.max_output_mib*1024**2
                and shutil.disk_usage(self.directory).free >= cfg.min_free_disk_mib*1024**2)

    def resource_reason(self, busy):
        if (self.settings.host_cpu_guard_enabled
                and busy > self.settings.max_host_cpu_busy_pct):
            return "M2_HOST_CPU_BUSY"
        live = getattr(self.resource_probe, "live_pressure_pct", None)
        if (self.settings.m1_cpu_guard_enabled
                and live is not None and live > self.settings.max_m1_cpu_pressure_pct):
            return "M2_M1_CPU_PRESSURE"
        return None

    def resource_metrics(self, busy):
        return {"host_cpu_busy_pct": busy,
                "live_cpu_pressure_pct": getattr(self.resource_probe, "live_pressure_pct", None),
                "global_cpu_pressure_pct": host_cpu_pressure()}

    async def wait(self, seconds):
        with suppress(TimeoutError):
            await asyncio.wait_for(self.stop.wait(), max(.001, seconds))

    async def resolutions(self):
        if self.resolve_task is not None and self.resolve_task.done():
            slug = self.resolve_slug
            try:
                result = self.resolve_task.result()
                if result:
                    self.store.settle(slug, result)
                    self.log("OFFICIAL_RESOLUTION", slug=slug, winner=result["winner"])
                    self.publish()
            except Exception as exc:
                self.log("RESOLUTION_RETRY", slug=slug, reason=type(exc).__name__)
            self.retry_resolution[slug] = time.monotonic()+30
            self.resolve_task = self.resolve_slug = None
        now = time.monotonic()
        if self.resolve_task is None and now >= self.resolution_scan_due:
            self.resolution_scan_due = now+1
            for row in self.store.pending():
                if self.retry_resolution.get(row["slug"], 0) > time.monotonic():
                    continue
                meta = json.loads(row["metadata"])
                expected = {"slug": row["slug"], "condition_id": meta["condition_id"],
                            "token_ids": {"YES": meta["tokens"][0]["token_id"],
                                          "NO": meta["tokens"][1]["token_id"]}}
                self.resolve_slug = row["slug"]
                self.resolve_task = asyncio.create_task(self.api.resolution(expected))
                break

    async def market(self, meta, *, stop_at):
        cfg, slug = self.settings, meta.slug
        epoch, run_id = int(slug.rsplit("-", 1)[1]), uuid4().hex
        self.store.start(run_id, asdict(meta))
        def context(mono):
            lower, upper = self.bounds(epoch, mono)
            return {"elapsed_lower": lower, "elapsed_upper": upper,
                    "clock_fresh": 0 <= mono-self.clock[2] <= 60,
                    "planned_shutdown": closing}

        buffer = M2EventBuffer(context=context)
        extra = {'research_settings': cfg} if cfg.include_m3_m4 else {}
        feed = self.feed_factory(meta, RDPObiConfig(), normal_entry_mode="m1_v1",
                                 m1_config=RDPM1Config(observation_only=True, record_events=False,
                                                      signal=cfg.signal), **extra)
        if cfg.include_m3_m4:
            feed.hub.legacy_record = buffer
        else:
            feed.m1 = M2Signals(feed, settings=cfg, record=buffer)
        self.runtime = {"state": "CONNECTING", "slug": slug, "run_id": run_id, "real_posts": 0}
        self.log("MARKET_START", slug=slug, run_id=run_id, settings_hash=cfg.fingerprint)
        full, complete, closing = False, False, False
        ready_logged = False
        memory_recycled = False
        clock_task = None
        last_flush = last_report = last_heartbeat = 0.
        clock_due = time.monotonic()+30
        loop_delay_max = 0.
        overload_since = None

        def drain():
            rows = list(buffer.rows)
            if rows:
                self.store.ingest(run_id, slug, rows, bounds=lambda mono: self.bounds(epoch, mono),
                                  minimum=meta.min_order_size)
                buffer.rows.clear()
            if cfg.include_m3_m4:
                feed.hub.observe_new(context)
                extra_rows = []
                for strategy in ('M3', 'M4'):
                    extra_rows.extend(feed.hub.events[strategy])
                    feed.hub.events[strategy].clear()
                self.store.ingest_extra(run_id, slug, extra_rows, minimum=meta.min_order_size)

        try:
            feed.verify_ticks(await self.api.ticks(feed.books))
            feed.task = asyncio.create_task(feed.run(), name="m2-only-public-feed")
            previous = time.monotonic()
            while not self.stop.is_set() and time.monotonic() < stop_at:
                now = time.monotonic()
                loop_delay_max = max(loop_delay_max, max(0., now-previous-.05)*1000)
                previous = now
                lower, upper = self.bounds(epoch)
                if lower >= cfg.close_sec:
                    complete = True
                    break
                if feed.m1.failure:
                    raise RuntimeError(feed.m1.failure)
                if cfg.include_m3_m4 and any(s != 'M4_V2' for s in feed.hub.failures):
                    raise RuntimeError('M234_OBSERVER_FAILURE:'+encode(feed.hub.failures))
                if cfg.include_m3_m4 and not closing:
                    feed.hub.observe_new(context)
                if upper <= cfg.start_sec and feed.connected and all(
                    b.snapshot_generation == feed.generation and b.bids and b.asks for b in feed.books.values()
                ):
                    full = True
                    if not ready_logged:
                        self.log("CAPTURE_READY", slug=slug, run_id=run_id,
                                 elapsed_lower=lower, elapsed_upper=upper)
                        ready_logged = True
                if upper >= cfg.cutoff_sec and not closing:
                    closing = True
                    await feed.close()
                    drain()
                    # These diagnostic rows have been persisted by drain().
                    feed.m1.events.clear()
                if (closing and not memory_recycled and cfg.cutoff_sec <= lower
                        and upper < cfg.close_sec-10 and 0 <= now-self.clock[2] <= 60):
                    memory_recycled = True
                    self.recycle_memory(epoch, slug, run_id)
                self.runtime.update(state="WAIT_MARKET_END" if closing else "OBSERVING",
                                    elapsed=round(lower, 3), connected=feed.connected,
                                    counts=dict(feed.m1.counts), max_loop_delay_ms=loop_delay_max,
                                    pending_frames=len(feed.m1_pending_received))
                if cfg.include_m3_m4:
                    self.runtime.update(enabled_strategies=['M2', 'M3', 'M4'],
                        m4_definition='m4-frequency-v2' if cfg.m4_frequency_study else 'm4-rf-bid-and-df-ask-v1',
                        shared_signal_counts=dict(feed.hub.counts),
                        observer_failures=dict(feed.hub.failures))
                if now-last_flush >= 1:
                    drain()
                    last_flush = now
                    busy = self.resource_probe()
                    self.runtime.update(self.resource_metrics(busy))
                    reason = self.resource_reason(busy)
                    if reason == "M2_HOST_CPU_BUSY":
                        if overload_since is None:
                            overload_since = now
                        if now-overload_since < cfg.cpu_busy_grace_sec:
                            reason = None
                    else:
                        overload_since = None
                    if not closing and reason:
                        self.runtime.update(reason=reason, resource_stop=True)
                        feed.m1.invalidate(reason)
                        self.log("RESOURCE_PAUSE", reason=reason, **self.resource_metrics(busy))
                        break
                if now-last_heartbeat >= 10:
                    self.log("HEARTBEAT", **self.runtime)
                    last_heartbeat = now
                if now-last_report >= 60:
                    if not self.storage_ok():
                        self.runtime.update(state="RESOURCE_STOP", reason="M2_STORAGE_LIMIT")
                        self.stop.set()
                        break
                    self.publish()
                    last_report = now
                if clock_task is not None and clock_task.done():
                    try:
                        self.clock = clock_task.result()
                    except Exception as exc:
                        self.log("CLOCK_RETRY", reason=type(exc).__name__)
                    clock_task = None
                if now >= clock_due and clock_task is None:
                    clock_task = asyncio.create_task(self.api.clock())
                    clock_due = now+30
                # A stale clock cannot authorize hypothetical entries.
                if now-self.clock[2] > 60:
                    feed.m1.invalidate("M2_CLOCK_STALE")
                await self.resolutions()
                await self.wait(.05 if not closing else .5)
        finally:
            if clock_task is not None:
                clock_task.cancel()
                await asyncio.gather(clock_task, return_exceptions=True)
            await feed.close()
            drain()
            counts = dict(feed.m1.counts)
            counts.update(max_loop_delay_ms=loop_delay_max, transport_failures=feed.failure_count,
                          invalid_reasons=dict(feed.m1.invalid_reasons),
                          resource_stop=self.runtime.get("resource_stop", False))
            if cfg.include_m3_m4:
                counts.update(shared_signal_counts=dict(feed.hub.counts), observer_failures=dict(feed.hub.failures))
            self.store.finish(run_id, slug, full_window=full, complete=complete, counts=counts)
            self.runtime.update(state="MARKET_CLOSED" if complete else "MARKET_INTERRUPTED")
            self.publish()
            self.log("MARKET_END", slug=slug, full_window=full and complete, counts=counts)

    async def run(self, *, once=False, max_seconds=None, max_markets=None):
        stop_at = time.monotonic()+(max_seconds if max_seconds is not None else float("inf"))
        observed = 0
        waiting_heartbeat = 0.
        self.log("BOOT", version=self.version, settings=self.settings.model_dump(mode="json"),
                 sources=self.sources, model=MODEL)
        (self.directory/f"definition-{self.version[:16]}.json").write_text(
            encode({"version": self.version, "sources": self.sources,
                    "settings": self.settings.model_dump(mode="json")})+"\n")
        self.publish()
        try:
            while not self.stop.is_set() and time.monotonic() < stop_at:
                if not self.storage_ok():
                    self.runtime = {"state": "RESOURCE_STOP", "reason": "M2_STORAGE_LIMIT"}
                    break
                busy = self.resource_probe()
                reason = self.resource_reason(busy)
                if reason:
                    self.runtime = {"state": "WAIT_RESOURCES", "reason": reason,
                                    **self.resource_metrics(busy), "real_posts": 0}
                    if time.monotonic()-waiting_heartbeat >= 30:
                        self.log("HEARTBEAT", **self.runtime)
                        self.publish()
                        waiting_heartbeat = time.monotonic()
                    await self.resolutions()
                    await self.wait(10)
                    continue
                try:
                    if self.clock is None or time.monotonic()-self.clock[2] >= 30:
                        self.clock = await self.api.clock()
                    server = self.clock[0]+time.monotonic()-self.clock[2]
                    epoch = int(server//300)*300
                    slug = f"btc-updown-5m-{epoch}"
                    await self.resolutions()
                    if server-epoch >= self.settings.cutoff_sec or self.store.has_market(slug):
                        self.runtime = {"state": "WAIT_NEXT_MARKET", "slug": slug}
                        if time.monotonic()-waiting_heartbeat >= 30:
                            self.log("HEARTBEAT", **self.runtime)
                            self.publish()
                            waiting_heartbeat = time.monotonic()
                        await self.wait(2)
                        continue
                    meta = await self.api.market(slug)
                    await self.market(meta, stop_at=stop_at)
                    observed += 1
                    if once or (max_markets is not None and observed >= max_markets):
                        break
                except Exception as exc:
                    self.log("OBSERVER_RECOVERY", reason=f"{type(exc).__name__}:{str(exc)[:160]}")
                    if once:
                        raise
                    await self.wait(2)
            # Keep pending results pending. No synthetic outcome on shutdown.
            await self.resolutions()
        finally:
            if self.resolve_task is not None:
                self.resolve_task.cancel()
                await asyncio.gather(self.resolve_task, return_exceptions=True)
            self.runtime = {**self.runtime, "stopped": True}
            self.publish()
            await self.close_reports()
            await self.api.close()
            self.store.close()


async def run(settings, output, *, once=False, max_seconds=None, max_markets=None):
    directory = output_path(output)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory/"observer.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        try:
            await M2Study(settings, directory, stop=stop).run(
                once=once, max_seconds=max_seconds, max_markets=max_markets)
        finally:
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.remove_signal_handler(sig)


def main():
    parser = argparse.ArgumentParser(description="M2 public candidate research; real orders unavailable")
    parser.add_argument("--settings", help="independent M2 TOML; never wallet/config.toml")
    parser.add_argument("--output-dir", default=str(OUTPUT))
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--max-seconds", type=float)
    parser.add_argument("--max-markets", type=int)
    parser.add_argument("--include-m3-m4", action="store_true",
                        help="observe M3 and M4 on the existing M2 public connection; no real orders")
    parser.add_argument("--m4-frequency-study", action="store_true",
                        help="use the M4 frequency-v2 definition; preserve M2/M3 and the existing public feed")
    parser.add_argument("--disable-host-cpu-guard", action="store_true",
                        help="record host CPU load without pausing M2; retain M1-specific protection")
    parser.add_argument("--disable-cpu-guards", action="store_true",
                        help="record CPU metrics without pausing M2 for host load or M1 pressure")
    args = parser.parse_args()
    if ((args.max_seconds is not None and args.max_seconds <= 0)
            or (args.max_markets is not None and args.max_markets <= 0)):
        parser.error("duration and market limit must be positive")
    settings = M2Config.load(args.settings)
    if args.include_m3_m4:
        settings = settings.model_copy(update={"include_m3_m4": True})
    if args.m4_frequency_study:
        settings = settings.model_copy(update={"include_m3_m4": True, "m4_frequency_study": True})
    if args.disable_host_cpu_guard:
        settings = settings.model_copy(update={"host_cpu_guard_enabled": False})
    if args.disable_cpu_guards:
        settings = settings.model_copy(update={"host_cpu_guard_enabled": False,
                                               "m1_cpu_guard_enabled": False})
    asyncio.run(run(settings, args.output_dir, once=args.once,
                    max_seconds=args.max_seconds, max_markets=args.max_markets))


if __name__ == "__main__":
    main()
