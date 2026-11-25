"""Translate admitted baseline intents into the copied M42112 execution contract."""
import asyncio
from collections import Counter
from dataclasses import asdict
from decimal import Decimal
import json

from m12_follow.transport import stable_id
from m12_follow.sqlite_source import BASELINE_VERSIONS
from polymaker.m21_live import M21Feed
from polymaker.m4_live_numeric import age_us, live_book_legal
from polymaker.m4_signals import M4Feed
from polymaker.m4_types import M421_LEDGER, Opportunity
from polymaker.taker_runner import SharedTakerFeed, TakerSocket


class BaselineIntents:
    """Reader.read() checks live ownership, watermark, freshness and host boot."""
    m21_only = True  # M42112 live execution port, as on CombinedSignals
    market_scoped = False

    def __init__(self, feed, settings, reader, run_id):
        self.feed, self.settings, self.reader, self.run_id = feed, settings, reader, run_id
        self.strategy = getattr(reader, "strategy", "M12")
        self.version = BASELINE_VERSIONS[self.strategy]
        self.price_risk = settings.live_taker_price_risk
        self.record = self.failure = self.selected = None
        self.counts = Counter()
        self.observed = {}
        self.claimed = set()
        self.active = set()
        self.sequence = {}
        self.source_generation = None
        self.source_watermark = -1
        self.blocked_generations = set()

    def emit(self, event, **fields):
        self.counts[event] += 1
        if self.record and self.record({"event": event, "strategy": self.strategy,
                "available_monotonic": self.feed.monotonic(), **fields}) is False:
            self.failure = "FOLLOW_INTENT_JOURNAL_FAILED"

    def diagnostics(self):
        return {"strategy": f"{self.strategy}_BASELINE_FOLLOW", "failure": self.failure, "counts": dict(self.counts)}

    def invalidate(self, reason, token=None):
        self.selected = None
        self.active.clear()
        self.claimed.update(k for k, o in self.observed.items() if token in (None, o.token_id))

    def reset(self):
        self.invalidate("EXECUTION_FEED_RECONNECTED")

    def legal(self, book, now):
        return (live_book_legal(self, book, now) and self.feed.research.good[book.token_id]
                and self.feed.research.legal(book.token_id))

    def refresh(self):
        self.active.clear()
        if self.failure or self.feed.runtime_released or self.feed.stop_event.is_set():
            return
        snap = self.reader.read()
        if snap is None:
            return
        if snap.generation != self.source_generation:
            if self.source_generation is not None:
                self.blocked_generations.add(self.source_generation)
            self.source_generation, self.source_watermark = snap.generation, -1
        if snap.generation in self.blocked_generations or snap.input_seq < self.source_watermark:
            self.failure = "FOLLOW_SOURCE_SEQUENCE_REGRESSION"
            return
        self.source_watermark = snap.input_seq
        for sequence, intent in enumerate(snap.intents, 1):
            raw_id, version = intent["opportunity"], intent["version"]
            token = intent["token"]
            if version != self.version or self.feed.outcomes.get(token) != intent["outcome"]:
                self.failure = "FOLLOW_INTENT_IDENTITY_CONFLICT"
                self.active.clear()
                return
            key = stable_id(self.run_id, self.feed.meta.slug, version, raw_id)
            root = stable_id(self.run_id, self.feed.meta.slug, version, intent["root_id"])
            if key not in self.observed:
                op = Opportunity(key, token, intent["outcome"], Decimal(intent["price"]),
                    intent["confirmed"], min(intent["deadline"], intent["confirmed"] + 3),
                    snap.generation, root, self.feed.generation, self.settings.signal_price_risk(self.strategy),
                    self.settings.taker_strategy, self.feed.meta.condition_id)
                self.observed[key] = op
                self.sequence[key] = sequence
                self.emit("BASELINE_INTENT_RECEIVED", opportunity=asdict(op), baseline=intent,
                          source_run=self.run_id, source_generation=snap.generation, input_seq=snap.input_seq)
            op = self.observed[key]
            if (op.support_id == snap.generation and raw_id in snap.active_ids and self.feed.connected
                    and op.generation == self.feed.generation and age_us(op.order_deadline, self.feed.monotonic()) > 0):
                self.active.add(key)

    def evaluate(self, snapshot=None):
        self.refresh()
        self.selected = next((op for k, op in self.observed.items()
                              if k in self.active and k not in self.claimed), None)

    def active_reason(self, claim, snapshot=None):
        self.refresh()
        return None if not self.failure and claim[0] in self.active else "BASELINE_INTENT_INACTIVE"

    def restored_reason(self, op):
        # Persisted support_id binds a task to the publishing generation even across restarts.
        reason = self.active_reason((op.episode_id,))
        if reason or self.observed[op.episode_id].support_id != op.support_id:
            return reason or "BASELINE_PUBLISHER_GENERATION_CHANGED"
        return None

    def take(self, op):
        if op.episode_id in self.claimed or self.active_reason((op.episode_id,)):
            return False
        self.claimed.add(op.episode_id)
        return True

    def resume_unplanned(self, ledger, op):
        """Finish a cut-short admission only on a fresh read of the exact same intent."""
        if self.restored_reason(op):
            return None
        key = op.episode_id
        with ledger.lock, ledger.db:
            saved = ledger.db.execute("SELECT evidence FROM opportunities WHERE id=? "
                                      "AND state='SKIPPED' AND reason='M4_INCOMPLETE_UNSENT_INTENT'", (key,)).fetchone()
            if not saved:
                return None
            evidence = json.loads(saved[0])
            # Connection generation belongs to this local client. Publisher generation,
            # original signal times, both identities and all economic terms must match.
            expected = json.loads(json.dumps(asdict(op), default=str))
            transient = {"generation"}
            if self.market_scoped:
                transient.update(("signal_start_mono", "order_deadline"))
            if any(evidence.get(k) != v for k, v in expected.items() if k not in transient):
                return None
            updated = ledger.db.execute("""
                UPDATE opportunities SET state='OBSERVED', reason=NULL,evidence=?
                WHERE id=? AND posts=0 AND started=0 AND order_id IS NULL
                  AND CAST(reserved AS NUMERIC)=0 AND CAST(spent AS NUMERIC)=0 AND CAST(filled AS NUMERIC)=0
                  AND NOT EXISTS (SELECT 1 FROM taker_attempts WHERE task_id=?)
                  AND NOT EXISTS (SELECT 1 FROM uncertain_posts WHERE opportunity_id=?)
                  AND NOT EXISTS (SELECT 1 FROM facts WHERE kind='taker_plan' AND identity=?)
            """, (json.dumps(expected) if self.market_scoped else saved[0], key, key, key, key))
            return ledger.row(key) if updated.rowcount else None

    def provenance(self, key):
        return self.sequence[key], self.strategy


class SQLiteBaselineIntents(BaselineIntents):
    market_scoped = True

    def __init__(self, feed, settings, reader, run_id):
        super().__init__(feed, settings, reader, run_id)
        self.latest_key = None
        self.available = False
        self.max_overpay_ticks = reader.max_overpay_ticks

    def invalidate(self, reason, token=None):
        # A local quote outage suspends sends. A committed market task survives.
        self.selected = None
        self.active.clear()

    def refresh(self, *, force=False):
        self.active.clear()
        self.available = False
        if self.failure or self.feed.runtime_released or self.feed.stop_event.is_set():
            return
        snap = self.reader.read(force=force)
        if snap is None or not snap.intents:
            return
        intent, = snap.intents
        version, raw_id, token = intent["version"], intent["opportunity"], intent["token"]
        if version != self.version or self.feed.outcomes.get(token) != intent["outcome"]:
            self.failure = "FOLLOW_INTENT_IDENTITY_CONFLICT"
            return
        key = stable_id(self.run_id, self.feed.meta.slug, version, raw_id)
        price = Decimal(intent["price"])
        if not price.is_finite() or not 0 < price < 1:
            self.failure = "FOLLOW_INTENT_PRICE_INVALID"
            return
        if key not in self.observed:
            now = self.feed.monotonic()
            remaining = int(self.feed.meta.slug.rsplit("-", 1)[1]) + self.settings.cutoff_sec - self.feed.wall()
            op = Opportunity(key, token, intent["outcome"], price, now, now + remaining,
                snap.generation, key, self.feed.generation, self.settings.signal_price_risk(self.strategy),
                self.settings.taker_strategy, self.feed.meta.condition_id)
            self.observed[key] = op
            self.sequence[key] = intent["audit_seq"]
            if self.latest_key:
                self.emit("BASELINE_INTENT_SUPERSEDED", previous_id=self.latest_key,
                          opportunity_id=key, audit_seq=intent["audit_seq"])
            self.emit("BASELINE_INTENT_RECEIVED", opportunity=asdict(op), baseline=intent,
                      source_run=self.run_id, source_generation=snap.generation,
                      input_seq=intent["input_seq"], audit_seq=intent["audit_seq"])
        self.latest_key = key
        self.available = True
        if self.feed.connected or (getattr(self.reader, "follow_limit", False)
                                   and self.feed.tick_verified.get(token) == self.feed.books[token].tick
                                   and self.feed.books[token].tick > 0):
            self.active.add(key)

    def evaluate(self, snapshot=None):
        super().evaluate(snapshot)
        if (self.selected is not None and
                self.feed.tick_verified.get(self.selected.token_id) != self.feed.books[self.selected.token_id].tick):
            self.selected = None

    def record_admissions(self, ledger):
        # Snapshot reads can coalesce several commits. Only the latest executes,
        # but every baseline admission contributes to the coverage denominator.
        with self.feed.lock:
            pending = getattr(self.reader, "pending_admissions", None)
            if not pending:
                return
            facts = []
            for intent in pending:
                key = stable_id(self.run_id, self.feed.meta.slug, intent["version"], intent["opportunity"])
                facts.append(("m12_follow_admission", key, {
                    "id": key, "baseline_id": intent["opportunity"], "audit_seq": intent["audit_seq"],
                    "source_run": self.run_id, "anchor": intent["price"], "baseline_quantity": intent["quantity"],
                    "outcome": intent["outcome"], "token": intent["token"],
                    **({"strategy": self.strategy} if self.strategy != "M12" else {}),
                }))
            ledger.fact_batch(facts)
            pending.clear()

    def active_reason(self, claim, snapshot=None, *, force=False):
        self.refresh(force=force)
        key = claim[0]
        if key in self.active and not self.failure:
            return None
        if self.latest_key and key != self.latest_key:
            return "M12_BASELINE_INTENT_SUPERSEDED"
        return self.failure or "M12_BASELINE_OR_BOOK_UNAVAILABLE"

    def restored_reason(self, op, *, force=False):
        reason = self.active_reason((op.episode_id,), force=force)
        if reason is None and self.observed[op.episode_id].support_id != op.support_id:
            return "M12_BASELINE_DATABASE_CHANGED"
        return reason


class QuoteOnlyCore:
    """Discard strategy hooks in the shared L2 validator. No detector receives trades."""
    def __init__(self):
        self.quality = {}
        self.rounds = {}

    def ignored(self, *args, **kwargs):
        pass

    clock = close = publish = new_generation = level_missing = depth = ignored
    invalidate = observe_unproven_buy = observe_direct_trade = release = trade = ignored

    def requires_pre_hit_for_trade(self, **kwargs):
        return False


class FollowQuoteFeed(TakerSocket, M21Feed):
    def __init__(self, meta, cfg, *, taker_settings, reader, run_id, **kwargs):
        super().__init__(meta, cfg, taker_settings=taker_settings, **kwargs)
        self.research.core = QuoteOnlyCore()
        kind = SQLiteBaselineIntents if getattr(reader, "sqlite_source", False) else BaselineIntents
        self.m1 = kind(self, taker_settings, reader, run_id)
        self.research.emit = self.quote_event
        self.m12_manifest = {"source": "BASELINE_SQLITE" if kind.market_scoped else "LIVE_BASELINE_ADMITTED_INTENT",
                             "version": self.m1.version,
                             "run_id": run_id}
        if self.m1.strategy != "M12":
            self.m12_manifest["strategy"] = self.m1.strategy
        self.observe_until_market_close = True
        self.follow_market = None
        self.execution_token = None

    def wanted_token(self):
        signal, market = self.m1, self.follow_market
        if signal.available and market is not None and market.intent_needs_quotes(signal.latest_key):
            return signal.observed[signal.latest_key].token_id
        return None

    def quote_event(self, kind, evidence):
        if evidence["event"] == "LIVE_INPUT_CLOCK_REJECTION":
            self.m1.emit(kind, evidence=evidence)

    def apply_signal_frame(self, raw, *, now, wall, received, pending):
        self.research.apply(raw, now=now, wall=wall, received=received, seq=self.m1_frame, pending=pending)

    def invalidate(self, reason):
        with self.lock:
            super().invalidate(reason)
            if not self.runtime_released:
                self.research.gap(reason)

    async def close(self):
        try:
            await super().close()
        finally:
            with self.lock:
                if self.m1.market_scoped:
                    self.m1.reader.close()

    def release_signal_memory(self):
        with self.lock:
            if not self.stop_event.is_set():
                raise RuntimeError("FOLLOW_FEED_STILL_RUNNING")
            self.runtime_released = True
            self.m1.active.clear()
            self.m1.observed.clear()
            if self.m1.market_scoped:
                self.m1.reader.close()
            return self.research.release_runtime()


class FollowSocket(SharedTakerFeed):
    """Lead socket owns networking/account IO; only the follower child owns execution."""
    def __init__(self, meta, cfg, *, follow, m421_settings, **kwargs):
        M4Feed.__init__(self, meta, cfg, **kwargs)
        self.execution_disabled = self.transport_only = self.observe_until_market_close = True
        # A bounded trial reconciles its own two ledgers after the trading window.
        self.defer_archive_reconciliation = getattr(follow, "trial_window", None) is not None
        self.m6 = None
        reader = follow.reader(meta)
        child = FollowQuoteFeed(meta, cfg, taker_settings=m421_settings, reader=reader,
                                run_id=follow.run_id, monotonic=self.monotonic, wall=self.wall)
        child.follow_market_entry_block = follow.market_entry_block(meta.slug)
        child.m12_manifest["market_entry_block"] = child.follow_market_entry_block
        self.secondary = {M421_LEDGER: child}
        child.lock = self.lock
        child.pending_receipts = self.pending_receipts
        child.shared_frame_processing_policy = "BASELINE_INTENT_AND_LIVE_EXECUTION_BOOK_V1"

    def follow_account_demand(self):
        child = self.secondary[M421_LEDGER]
        market = child.follow_market
        return (not child.m1.market_scoped or child.execution_token is not None
                or (market is not None and market.owned_order_work))

    async def run(self):
        child = self.secondary[M421_LEDGER]
        signal = child.m1
        if not signal.market_scoped:
            await super().run()
            return
        task = token = None
        cutoff = int(self.meta.slug.rsplit("-", 1)[1]) + signal.settings.cutoff_sec
        try:
            while not self.stop_event.is_set() and self.wall() < cutoff:
                with self.lock:
                    signal.refresh()
                    wanted = child.wanted_token()
                    child.execution_token = wanted
                if wanted != token:
                    if task is not None:
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                    token, task = wanted, None
                    if token is not None:
                        self.subscription_token_ids = (token,)
                        task = asyncio.create_task(super().run(), name="m12-intent-token-book")
                if task is not None and task.done():
                    task.result()
                    break
                await asyncio.sleep(.025)
        finally:
            child.execution_token = None
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    def _apply(self, raw, *, received_mono=None, received_wall=None):
        with self.lock:
            now = self.monotonic()
            received = now if received_mono is None else received_mono
            receive_wall = self.wall() if received_wall is None else received_wall
            self.validate_receipt_clock(now, received, receive_wall)
            self.last_receipt = self.last_transport_receipt = received
            self.last_wall = receive_wall
            if hasattr(self, "subscription_token_ids") and raw not in ("PING", "PONG"):
                token, = self.subscription_token_ids
                rows = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
                relevant = []
                for row in rows if isinstance(rows, list) else [rows]:
                    if row.get("event_type") == "price_change":
                        changes = [change for change in row["price_changes"] if change["asset_id"] == token]
                        if changes:
                            relevant.append({**row, "price_changes": changes})
                    elif row.get("asset_id") == token and row.get("event_type") in {
                            "book", "best_bid_ask", "tick_size_change",
                    }:
                        relevant.append(row)
                if not relevant:
                    return
                raw = relevant
            self.mirror("_apply", raw, received_mono=received, received_wall=receive_wall)

    def maintain_signals(self):
        # Advance the execution book validity clock, without generating local signals.
        child = self.secondary[M421_LEDGER]
        with self.lock:
            if not child.runtime_released and not child.stop_event.is_set():
                child.research.timer(now=self.monotonic(), wall=self.wall(), seq=child.m1_frame,
                                     pending=self.pending_receipts())
