"""Standalone public-data M1 observer. No wallet, account lease or trade engine.

Run: python -m polymaker.rdp_sm_m1_observe --config-dir config-m1
WOULD_POST covers public signal/quote/timing checks only, never account admission.
"""

import argparse
import asyncio
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import httpx

from polymaker.catalog.gamma import GammaClient, parse_market
from polymaker.config import RDPBAConfig, RDPM1Config, RDPObiConfig
from polymaker.rdp_sm_ca import plain
from polymaker.rdp_sm_m1_policy import budget_quantity
from polymaker.rdp_sm_m1_writer import M1ObserverWriter
from polymaker.rdp_sm_marketdata import SessionFeed

OUTPUT_ROOT = Path("research_outputs/m1_isolated")
MIB = 1024 * 1024


def load_settings(directory):
    # Intentionally bypass Config.load: no .env, Secrets, account or DB settings.
    raw = (Path(directory) / "config.toml").read_bytes()
    strategy = tomllib.loads(raw.decode()).get("rdp_sm", {})
    if strategy.get("normal_entry_mode") != "m1_v1" or strategy.get("m1", {}).get("observation_only") is not True:
        raise ValueError("M1_PUBLIC_OBSERVER_REQUIRES_EXPLICIT_OBSERVATION_MODE")
    return (RDPM1Config.model_validate(strategy["m1"]),
            RDPObiConfig.model_validate(strategy.get("obi", {})),
            RDPBAConfig.model_validate(strategy.get("ba", {})), raw)


def public_market(raw, slug):
    """Map Up/Down by outcome, never Gamma's array order or market price."""
    meta = parse_market(raw) if isinstance(raw, dict) else None
    if meta is None or meta.slug != slug or raw.get("closed") is not False or raw.get("active") is not True:
        raise ValueError("M1_PUBLIC_MARKET_UNAVAILABLE")
    match = re.fullmatch(r"btc-updown-5m-([0-9]+)", slug)
    if not match or int(match[1]) % 300 or not re.fullmatch(r"0x[0-9a-fA-F]{64}", meta.condition_id):
        raise ValueError("M1_PUBLIC_MARKET_IDENTITY_INVALID")
    end = datetime.fromisoformat(meta.end_date_iso.replace("Z", "+00:00"))
    if end.tzinfo is None or end.timestamp() != int(match[1]) + 300:
        raise ValueError("M1_PUBLIC_MARKET_DURATION_INVALID")
    tokens = {t.outcome.casefold(): t for t in meta.tokens}
    if set(tokens) != {"up", "down"} or len({t.token_id for t in meta.tokens}) != 2:
        raise ValueError("M1_PUBLIC_TOKEN_MAPPING_INVALID")
    if any(not t.token_id.isdecimal() or not 0 < int(t.token_id) < 2**256 for t in meta.tokens):
        raise ValueError("M1_PUBLIC_TOKEN_ID_INVALID")
    return replace(meta, tokens=(tokens["up"], tokens["down"]))


class PublicClob:
    """Only the two unauthenticated GETs needed by the observer exist here."""

    def __init__(self):
        self.client = httpx.AsyncClient(base_url="https://clob.polymarket.com", timeout=5, trust_env=False)

    async def clock(self):
        started = time.monotonic()
        response = await self.client.get("/time")
        response.raise_for_status()
        now = response.json()
        if isinstance(now, bool) or not isinstance(now, (int, float)) or not 0 < now < 10**11:
            raise ValueError("M1_PUBLIC_TIME_INVALID")
        # Conservative bounds include integer-second precision and GET duration.
        return float(now), started, time.monotonic()

    async def ticks(self, tokens):
        result = {}
        for token in tokens:
            response = await self.client.get("/tick-size", params={"token_id": token})
            response.raise_for_status()
            tick = Decimal(str(response.json()["minimum_tick_size"]))
            if tick not in {Decimal(".1"), Decimal(".01"), Decimal(".001"), Decimal(".0001")}:
                raise ValueError("M1_PUBLIC_TICK_INVALID")
            result[token] = tick
        return result

    async def close(self):
        await self.client.aclose()


class PublicProposal:
    """Independent opportunity markers; no simulated orders, funds or fills."""

    def __init__(self, feed, cfg, ba_cfg):
        self.feed, self.cfg, self.ba_cfg = feed, cfg, ba_cfg
        self.marked_rf, self.marked_ex = set(), set()
        self.count = 0
        self.reason = "M1_NO_OPPORTUNITY"

    def poll(self, elapsed_lower, elapsed_upper):
        feed, cfg = self.feed, self.cfg
        with feed.lock:
            feed.m1.evaluate(feed.snapshot(ba_cfg=self.ba_cfg))
            ep = feed.m1.selected
            self.reason = "M1_NO_OPPORTUNITY"
            if ep is None:
                return None
            self.reason = "M1_OBSERVATION_WINDOW"
            if not cfg.entry_start_sec <= elapsed_lower or elapsed_upper >= cfg.cutoff_sec:
                return None
            self.reason = "M1_INPUT_PENDING"
            if feed.m1_pending_received or feed.m1_processing_received is not None:
                return None
            snapshot = feed.snapshot(repair_token=ep.token_id, ba_cfg=self.ba_cfg)
            self.reason = snapshot.reason
            if not snapshot.valid:
                return None
            self.reason = "M1_EPISODE_ALREADY_MARKED"
            if ep.rf_id in self.marked_rf or ep.ex_id in self.marked_ex:
                return None
            remaining = min((ep.order_deadline-feed.monotonic())*1000,
                            (cfg.cutoff_sec-elapsed_upper)*1000)
            self.reason = "M1_EXECUTION_BUDGET_INSUFFICIENT"
            if remaining < cfg.post_budget_floor_ms + cfg.min_live_budget_ms:
                return None
            book = feed.books[ep.token_id]
            self.reason = "M1_ANCHOR_UNAVAILABLE"
            if not any(p == ep.anchor_bid_price and q > 0 for p, q in book.bids):
                return None
            if not feed.m1.cfg.track_visible_anchor and book.best_bid != ep.anchor_bid_price:
                return None
            try:
                quantity = budget_quantity(ep.anchor_bid_price, feed.meta.min_order_size)
            except (ValueError, ArithmeticError) as exc:
                self.reason = str(exc)
                return None
            self.marked_rf.add(ep.rf_id)
            self.marked_ex.add(ep.ex_id)
            self.count += 1
            self.reason = "WOULD_POST_PUBLIC_CHECKS_ONLY"
            row = {"scope": "PUBLIC_SIGNAL_QUOTE_TIMING_ONLY", "account_admission": "NOT_CHECKED",
                   "inventory": "NOT_READ", "post_started": False, "observation_only": True,
                   "side": "BUY", "post_only": True, "order_budget_usdc": "5",
                   "price": str(ep.anchor_bid_price), "size": str(quantity),
                   "remaining_ms": remaining, "elapsed_lower": elapsed_lower,
                   "elapsed_upper": elapsed_upper, "opportunity": plain(asdict(ep)),
                   "parameter_hash": feed.m1.parameter_hash}
            # Deliberately do not consume/clear the detector as if a fill happened.
            # Markers are independent diagnostics, not a $20 execution ledger.
            feed.m1.emit("WOULD_POST", **row)
            return row


def lower_priority():
    if hasattr(os, "nice") and os.nice(0) < 15:
        os.nice(15-os.nice(0))
    if shutil.which("ionice"):
        subprocess.run(["ionice", "-c", "3", "-p", str(os.getpid())],
                       check=True, capture_output=True, timeout=2)


def output_usage(root):
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())


@contextmanager
def observer_lock():
    # Serializes only M1 public observers; never uses CA's account identity/lock.
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    with (OUTPUT_ROOT / ".public_observer.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("M1_PUBLIC_OBSERVER_ALREADY_RUNNING") from exc
        yield


def prepare_output(output_root, max_output_mib, raw_config):
    root = Path(output_root).resolve()
    # A caller cannot redirect observation files into CA's journal/config tree.
    if root != OUTPUT_ROOT.resolve() and OUTPUT_ROOT.resolve() not in root.parents:
        raise ValueError("M1_OUTPUT_MUST_BE_UNDER_RESEARCH_OUTPUTS_M1_ISOLATED")
    root.mkdir(parents=True, exist_ok=True)
    if output_usage(root) >= max_output_mib*MIB or shutil.disk_usage(root).free < 256*MIB:
        raise RuntimeError("M1_OUTPUT_BUDGET_REACHED")
    session = root / f"{time.time_ns()}-{uuid4().hex[:8]}"
    session.mkdir()
    (session / "config.toml").write_bytes(raw_config)
    return root, session


async def observe(config_dir, *, max_seconds=360, output_root=OUTPUT_ROOT, max_output_mib=256,
                  paper_mode=False, post_latency_ms=None, cancel_latency_ms=200):
    cfg, obi, ba, raw_config = load_settings(config_dir)
    root, session = prepare_output(output_root, max_output_mib, raw_config)
    stop_at = time.monotonic() + max_seconds
    public, feed, proposal, paper = PublicClob(), None, None, None
    reason = "M1_DURATION_LIMIT"
    try:
        server, sent, received = await public.clock()
        epoch = int(server//300)*300
        if server-epoch >= cfg.cutoff_sec:
            epoch += 300
        slug = f"btc-updown-5m-{epoch}"
        while server+time.monotonic()-received < epoch and time.monotonic() < stop_at:
            print(f"M1 Heartbeat | public_only=true waiting={slug}", file=sys.stderr, flush=True)
            await asyncio.sleep(min(5, max(.001, stop_at-time.monotonic())))
        if time.monotonic() >= stop_at:
            result = {"execution_status": "PUBLIC_OBSERVATION_ONLY", "stop_reason": reason,
                      "session_dir": str(session), "account_admission": "NOT_CHECKED",
                      "m1_observation": {"counts": {}}}
            (session / "result.json").write_text(json.dumps(result))
            return result
        async with GammaClient(timeout=5) as gamma:
            meta = public_market(await gamma.market_by_slug(slug), slug)
        # Keep the existing signal/feed code, but give its writer a smaller queue.
        # BA limits are passed explicitly to snapshots. Do not instantiate the
        # unrelated BA detector and run it for every M1 public depth update.
        feed = SessionFeed(meta, obi, normal_entry_mode="m1_v1",
                           m1_config=cfg.model_copy(update={"record_events": False}))
        feed.m1_writer = M1ObserverWriter(session / "events.jsonl.gz", capacity=2048)
        feed.verify_ticks(await public.ticks(feed.books))
        if paper_mode:
            from polymaker.rdp_sm_m1_paper import PaperBroker

            paper = PaperBroker(feed, cfg, cutoff_mono=sent+epoch+cfg.cutoff_sec-server-1,
                emit=feed.m1_writer.submit, post_latency_ms=post_latency_ms,
                cancel_latency_ms=cancel_latency_ms)
            feed.m1_writer.listener = paper.on_record
        feed_start_elapsed = server+time.monotonic()-received-epoch
        (session / "metadata.json").write_text(json.dumps(plain({
            "market": asdict(meta), "ticks": feed.tick_verified, "clock": [server, sent, received],
            "scope": "PUBLIC_PAPER" if paper_mode else "PUBLIC_ONLY",
            "feed_start_elapsed": feed_start_elapsed, "account_admission": "NOT_CHECKED"})))
        proposal = PublicProposal(feed, cfg, ba)
        feed.task = asyncio.create_task(feed.run(), name="m1-public-feed")
        heartbeat = resource_check = 0.
        revision = feed.tick_revision
        while time.monotonic() < stop_at:
            now = time.monotonic()
            lower, upper = server+now-received-epoch, server+1+now-sent-epoch
            if paper is not None:
                paper.advance()
            if upper >= cfg.cutoff_sec:
                reason = "M1_OBSERVATION_CUTOFF"
                if paper is not None:
                    paper.cancel("PAPER_TIME_CUTOFF")
                if paper is None or paper.active is None or upper >= cfg.cutoff_sec+1:
                    break
            if feed.tick_revision != revision:
                # No private SDK cache or account sync is used on a tick change.
                feed.verify_ticks(await public.ticks(feed.books))
                revision = feed.tick_revision
            row = proposal.poll(lower, upper) if upper < cfg.cutoff_sec else None
            if row is not None:
                print("M1 WOULD_POST | "+json.dumps(row), file=sys.stderr, flush=True)
                if paper is not None:
                    paper.post(row)
            if now >= resource_check:
                resource_check = now + 1
                path = session / "events.jsonl.gz"
                if path.exists() and path.stat().st_size >= 32*MIB:
                    reason = "M1_SESSION_LOG_BUDGET_REACHED"
                    break
                if output_usage(root) >= max_output_mib*MIB or shutil.disk_usage(root).free < 256*MIB:
                    reason = "M1_OUTPUT_BUDGET_REACHED"
                    break
                if feed.m1_writer.failure or feed.m1.failure:
                    reason = feed.m1_writer.failure or feed.m1.failure
                    break
            if now >= heartbeat:
                heartbeat = now + 5
                counts = feed.m1.counts
                print(f"M1 Heartbeat | public_only=true elapsed={lower:.1f} connected={feed.connected} "
                      f"AP_SELL={counts['AP_CONFIRMED_SELL']} RF={counts['RF_CONFIRMED']} "
                      f"EX={counts['EX_CONFIRMED']} WOULD_POST={proposal.count} "
                      f"reason={proposal.reason}", file=sys.stderr, flush=True)
            if feed.task.done():
                feed.task.result()
                reason = "M1_FEED_STOPPED"
                break
            # Signal maintenance already runs at 25ms; evaluate proposals at
            # that cadence, not again for every book event in a busy stream.
            await asyncio.sleep(.025)
    finally:
        if paper is not None:
            paper.finish()
        if feed is not None:
            await feed.close()
        await public.close()
    if feed and feed.m1_writer.failure:
        reason = feed.m1_writer.failure
    result = {"execution_status": "PUBLIC_PAPER_ONLY" if paper_mode else "PUBLIC_OBSERVATION_ONLY", "stop_reason": reason,
              "session_dir": str(session), "account_admission": "NOT_CHECKED",
              "slug": slug, "market_epoch": epoch, "feed_start_elapsed": feed_start_elapsed,
              "paper": paper.summary() if paper is not None else None,
              "m1_observation": feed.m1.diagnostics() if feed else {"counts": {}},
              "writer": feed.m1_writer.status() if feed else None}
    (session / "result.json").write_text(json.dumps(plain(result)))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=Path("config-m1"))
    parser.add_argument("--max-seconds", type=float, default=360)
    parser.add_argument("--paper", action="store_true")
    parser.add_argument("--output-budget-mib", type=int, default=256)
    args = parser.parse_args()
    if not 0 < args.max_seconds <= 360:
        parser.error("--max-seconds must be in (0, 360]")
    lower_priority()
    try:
        with observer_lock():
            result = asyncio.run(observe(args.config_dir, max_seconds=args.max_seconds,
                paper_mode=args.paper, max_output_mib=args.output_budget_mib))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(json.dumps({"execution_status": "M1_PUBLIC_OBSERVER_FAILED", "error": type(exc).__name__,
                          "stop_reason": str(exc)}))
        return 1
    print(json.dumps(plain(result)))
    return 0 if result["stop_reason"] in {"M1_DURATION_LIMIT", "M1_OBSERVATION_CUTOFF"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
