"""Isolated M6 baseline admission/submission; its gateway is always virtual."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict
from decimal import Decimal

from polymaker.domain import Quote, Side
from polymaker.m2_memory import recycle_idle_memory
from polymaker.m6_baseline.config import STRATEGY
from polymaker.m6_baseline.state import decimal
from polymaker.m6_baseline.types import Opportunity
from polymaker.maker_retry import post_exception_rejection, rejection, uncertain_kind
from polymaker.rdp_sm import _require
from polymaker.rdp_sm_execution import (
    Placement,
    SMWindowExecutor,
    WindowQuote,
)

# Scheduling lag of already received frames, distinct from source timestamp
# reordering. RF/EX sealing still uses the detector's original watermark rules.
SUBMIT_PENDING_TOLERANCE_SEC = .020


class M6Executor:
    """Submit-and-hold adapter, with no shared strategy lifecycle or fill tracker."""

    _placement_outcomes = SMWindowExecutor._placement_outcomes

    def __init__(self, cfg, slug, session_id, reads, journal, *, ledger,
                 monotonic=time.monotonic, sleep=asyncio.sleep, display_events=None):
        self.cfg, self.slug, self.session_id = cfg, slug, session_id
        self.reads, self.gateway, self.journal = reads, reads.gateway, journal
        self.ledger, self.monotonic, self.sleep = ledger, monotonic, sleep
        self.display_events = display_events
        self.attempts, self.known = [], set()
        self.fatal, self.closed = None, False
        self.meta = None
        self.official_time = self.official_time_uncertainty_s = 0.
        self.time_anchor = monotonic()

    def committed(self):
        return self.ledger.committed()

    async def account_snapshot(self):
        return {'official_balance_usd': '10', 'applicable_allowance_usd': '10',
                'source': 'MODELED_GROSS_BUDGET_10_USDC_PER_MARKET'}

    def drain_network_events(self):
        return None

    def record(self, kind, payload, *, event_wall=None):
        if kind.startswith("sm_merge_"):
            raise RuntimeError("M6_MERGE_DISABLED")
        try:
            self.journal.write(kind, {"session_id": self.session_id, "payload": payload},
                               time.time() if event_wall is None else event_wall)
        except Exception:
            self.fatal = "M6_JOURNAL_WRITE_FAILED"
            raise
        if self.display_events is not None:
            self.display_events.append((kind, payload))

    def close(self):
        self.closed = True
        try:
            self.reads.close()
        finally:
            self.journal.close()


class M6Market:
    def __init__(self, executor, settings, stop, *, display=print, public_api=None):
        self.e, self.settings, self.stop, self.display = executor, settings, stop, display
        self.ledger = executor.ledger
        self.feed = executor.reads.feed
        self.state = None
        self.account = None
        self.account_reason = "M6_ACCOUNT_WARMUP"
        self.account_at = 0.
        self.ledger.set("fill_tracking", "NOT_TRACKED")
        self.pending = {}
        self.retry_after = {}
        self.retry_input_revision = {}
        self.submit_lock = asyncio.Lock()
        self.guard_reason = None
        self.pending_input_age_ms = 0.
        self.guard_input_revision = 0
        self.guard_input_state = {}
        self.post_task = self.account_task = None
        self.last_account = self.last_heartbeat = -float("inf")
        self.account_revision = 0
        self.account_read_ms = None
        self.public_api = public_api
        self.tick_task, self.last_tick_refresh = None, -float('inf')
        self.feed_closed = self.memory_recycled = False
        self.memory_recycle = None

        # The runner installs the same M6 detector in both modes before this engine.
        if not getattr(self.feed.m1, "m6_only", False):
            raise ValueError("M6_DETECTOR_REQUIRED")

    def elapsed(self):
        e = self.e
        return e.official_time+e.monotonic()-e.time_anchor-int(e.slug.rsplit("-", 1)[1])

    def transition(self, state):
        if self.state != state:
            self.e.record("sm_m6_state", {"previous": self.state, "state": state})
            self.state = state

    @staticmethod
    def restored_opportunity(row):
        evidence = json.loads(row["evidence"])
        evidence.pop("elapsed_s", None)
        evidence["anchor_bid_price"] = decimal(evidence["anchor_bid_price"])
        opportunity = Opportunity(**evidence)
        _require(opportunity.episode_id == row["id"] and opportunity.token_id == row["token"]
                 and opportunity.anchor_bid_price == decimal(row["price"]), "M6_RETRY_RECOVERY_IDENTITY")
        return opportunity

    async def restore(self):
        for old in self.ledger.uncertain_posts():
            quote = json.loads(old["quote"])
            quote["side"] = Side(quote["side"])
            if old["order_id"] not in self.e.known:
                self.register(Placement(old["opportunity_id"]+":uncertain", WindowQuote(**quote), outcome="UNKNOWN",
                    expected_order_id=old["order_id"], order_id=old["order_id"], post_started=True))
        for row in self.ledger.rows():
            if not row["started"]:
                if row["posts"] and row["state"] in {"RESERVED", "PREPARED", "ABORTED"} and not self.e.inhibited() and self.elapsed() < self.settings.cutoff_sec:
                    proof = next((p for kind, p in reversed(self.ledger.events("m6_retry_authorized"))
                                  if p["opportunity_id"] == row["id"] and p['post_number'] == row['posts']), None)
                    _require(proof is not None, "M6_RETRY_RECOVERY_PROOF_MISSING")
                    if row["state"] == "PREPARED":
                        self.ledger.outcome(row["id"], "ABORTED", "RESTART_UNSENT_RETRY_PREPARATION")
                    if row["state"] in {"PREPARED", "ABORTED"} and not self.ledger.retry(row["id"], proof={"kind": "NOT_SENT"}):
                        continue
                    self.pending[row["id"]] = self.restored_opportunity(row)
                    continue
                if row["state"] not in {"REJECTED", "SKIPPED", "ABORTED"}:
                    self.ledger.outcome(row["id"], "ABORTED", "RESTART_BEFORE_NATIVE_POST")
                continue
            if row["state"] == "REJECTED":
                proof = json.loads(row['failure_proof']) if row['failure_proof'] else None
                if proof and proof.get('kind') == 'REJECTED_RESPONSE' and proof.get('reprice') is True:
                    await self.queue_retry(self.restored_opportunity(row), proof=proof)
                continue
            quote = json.loads(row["quote"])
            quote["side"] = Side(quote["side"])
            attempt = Placement(row["id"], WindowQuote(**quote), outcome="UNKNOWN",
                expected_order_id=row["order_id"], order_id=row["order_id"], post_started=True)
            if row["order_id"] not in self.e.known:
                self.register(attempt)
            if row["state"] == "UNKNOWN" and row["uncertain_retries"] == 0 and not self.e.inhibited() and self.elapsed() < self.settings.cutoff_sec:
                self.pending[row["id"]] = self.restored_opportunity(row)

    def register(self, attempt):
        e, oid = self.e, attempt.expected_order_id
        e.attempts.append(attempt)
        e.known.add(oid)
        e.gateway._client.known_ids.add(oid)

    async def refresh_account(self):
        e = self.e
        if not self.account_current():
            self.account_reason = "M6_ACCOUNT_REFRESHING"
        began = e.monotonic()
        try:
            account = await e.account_snapshot()
            _require(all(decimal(account[k]) >= 0 for k in
                         ("official_balance_usd", "applicable_allowance_usd")), "CAPITAL_READ_UNKNOWN")
            _require(e.monotonic()-began <= self.settings.account_max_age_sec, "M6_ACCOUNT_READ_TOO_OLD")
            await e.balance_observed(account)
            self.account, self.account_at = account, began
            self.account_reason = None
            self.account_read_ms = (e.monotonic()-began)*1000
            e.record("sm_m6_account_snapshot", {"duration_ms": self.account_read_ms,
                "source": account["source"],
                "fresh_until_monotonic": began+self.settings.account_max_age_sec})
        except Exception as exc:
            self.account_reason = str(exc) or type(exc).__name__
            e.record("sm_m6_wait", {"reason": self.account_reason})
        finally:
            self.account_revision += 1

    def account_current(self):
        return (self.account is not None and self.account_reason is None and
                self.e.monotonic()-self.account_at <= self.settings.account_max_age_sec)

    def spendable(self):
        if not self.account_current():
            return Decimal(0)
        # Conservatively reserve full own targets, including all UNKNOWNs; an
        # official balance may already reflect their fills, never assume a refund.
        cash = min(decimal(self.account["official_balance_usd"]),
                   decimal(self.account["applicable_allowance_usd"]))
        # No fill-driven release. Keep all local commitments until this market
        # ends, even when the cash read may already reflect a completed BUY.
        return max(Decimal(0), cash-self.ledger.committed())

    async def persist(self, method, *args, **kwargs):
        """Await FULL-synchronous SQLite commits without stopping WS consumption.

        Calls never hold feed.lock. Cancellation drains the worker before any
        caller may close the ledger; it cannot turn a pending commit into a
        claim that persistence failed or an authorization to send again.
        """
        began, committed = time.monotonic(), False
        worker = {}

        def work():
            worker['began'] = time.monotonic()
            try:
                return method(*args, **kwargs)
            finally:
                worker['ended'] = time.monotonic()

        job = asyncio.create_task(asyncio.to_thread(work))
        try:
            result = await asyncio.shield(job)
            committed = True
            return result
        except asyncio.CancelledError:
            await job
            committed = True
            raise
        finally:
            try:
                ended = time.monotonic()
                key = getattr(args[0], 'episode_id', args[0]) if args else None
                self.e.record('sm_m6_persistence', {'operation': method.__name__,
                    'opportunity_id': key if isinstance(key, str) else None,
                    'duration_ms': (ended-began)*1000,
                    'worker_queue_ms': (worker.get('began', ended)-began)*1000,
                    'worker_execution_ms': (worker.get('ended', ended)-worker.get('began', ended))*1000,
                    'result_delivery_ms': (ended-worker.get('ended', ended))*1000,
                    'durability': 'SQLITE_FULL', 'committed': committed})
            except Exception:
                # Diagnostic failure closes further admission, but must not
                # disguise an already committed ACK as a transport failure.
                self.e.fatal = self.e.fatal or 'M6_PERSISTENCE_DIAGNOSTIC_FAILED'

    def capital_covers(self, row, price):
        if not self.account_current():
            return False
        required = self.ledger.committed()
        if row['state'] == 'RESERVED' and price is not None and self.settings.minimum_price <= price < self.settings.maximum_price:
            required += price*self.ledger.order_quantity(price, self.e.meta.min_order_size)-decimal(row['reserved'])
        return required <= min(decimal(self.account['official_balance_usd']),
                               decimal(self.account['applicable_allowance_usd']))

    async def collect(self):
        elapsed = self.elapsed()
        if not self.entry_window_open():
            return

        def skipped(opportunity, reason):
            if self.feed.m1.take(opportunity):
                self.e.record("sm_m6_candidate_skipped", {"opportunity": asdict(opportunity),
                                                        "reason": reason, "elapsed": elapsed})

        # Capture immutable evidence under the feed lock, then release it for
        # disk IO. Revalidate after persistence: a vanished signal is audited,
        # never executed using the earlier snapshot.
        for _ in range(2):
            with self.feed.lock:
                if not self.entry_window_open() or self.stop.is_set() or self.e.inhibited():
                    break
                elapsed = self.elapsed()
                self.feed.m1.evaluate()
                opportunity = self.feed.m1.selected
                if opportunity is None:
                    break
                if not self.settings.minimum_price <= opportunity.anchor_bid_price < self.settings.maximum_price:
                    skipped(opportunity, "PRICE_OUTSIDE_TEST_BAND")
                    continue
                book = self.feed.books[opportunity.token_id]
                if (not self.feed.m1.legal(book, self.e.monotonic())
                        or book.best_ask <= opportunity.anchor_bid_price
                        or opportunity.anchor_bid_price % book.tick):
                    skipped(opportunity, "MAKER_QUOTE_NOT_AVAILABLE")
                    continue
                try:
                    self.ledger.order_quantity(opportunity.anchor_bid_price, self.e.meta.min_order_size)
                except ValueError:
                    skipped(opportunity, "VENUE_MINIMUM_OR_PRECISION")
                    continue
            row = await self.persist(self.ledger.observe, opportunity,
                                     minimum=self.e.meta.min_order_size, elapsed=elapsed)
            if row is not None:
                self.e.record("sm_m6_baseline_signal", row)
            with self.feed.lock:
                taken = self.feed.m1.take(opportunity)
                reason = ('M6_STOPPING_DURING_PERSISTENCE' if self.stop.is_set() or self.e.inhibited() else
                          'M6_ENTRY_WINDOW_ENDED_DURING_PERSISTENCE' if not self.entry_window_open() else
                          'M6_SIGNAL_ENDED_DURING_PERSISTENCE' if not taken else None)
                active = reason is None
                if taken and row is not None and active:
                    self.pending[row["id"]] = opportunity
            if row is not None and not (taken and active):
                await self.persist(self.ledger.outcome, row['id'], 'SKIPPED', reason)

    def entry_window_open(self):
        lower = self.elapsed()
        upper = lower+getattr(self.e, "official_time_uncertainty_s", 0.)
        return self.settings.start_sec <= lower and upper < self.settings.cutoff_sec

    def quote_price(self, opportunity):
        with self.feed.lock:
            price = opportunity.anchor_bid_price
            row = self.ledger.row(opportunity.episode_id)
            if row["retry_pricing"] == "BEST_BID_ON_REJECTION":
                best = self.feed.books[opportunity.token_id].best_bid
                if (best is not None and row["repriced_from"] is not None
                        and best < price and best < decimal(row["repriced_from"])):
                    price = best
                else:
                    return None  # Rejection authorizes only a strictly lower Best bid.
            return price

    def post_budget_ms(self):
        return max(self.settings.post_budget_floor_ms,
                   self.e.reads.network.recent_writes().post.max_ms or 0)

    def initial_post_budget_available(self, opportunity):
        return (opportunity.order_deadline-self.e.monotonic())*1000 >= self.post_budget_ms()

    def guard(self, opportunity, quote=None, *, stage='ADMISSION'):
        e = self.e
        with self.feed.lock:
            book = self.feed.books[opportunity.token_id]
            row = self.ledger.row(opportunity.episode_id)
            retrying = bool(row["posts"])
            price = self.quote_price(opportunity)
            now = e.monotonic()
            self.guard_input_revision = self.feed.input_revision
            pending = ([self.feed.m1_pending_received[0]] if self.feed.m1_pending_received else [])
            if self.feed.m1_processing_received is not None:
                pending.append(self.feed.m1_processing_received)
            age = now-min(pending) if pending else 0.
            self.pending_input_age_ms = age*1000
            self.guard_input_state = {'revision': self.guard_input_revision,
                'sequence': self.feed.sequence, 'generation': self.feed.generation,
                'pending_count': len(self.feed.m1_pending_received),
                'oldest_received_monotonic': min(pending) if pending else None,
                'processing_received_monotonic': self.feed.m1_processing_received,
                'checked_monotonic': now}
            signal_reason = None if retrying else self.feed.m1.active_reason((opportunity.episode_id,), None)
            health = e.reads.network.snapshot()
            checks = [
                ("M6_STOPPING", not self.stop.is_set() and not e.closed and not e.inhibited()),
                (e.fatal or "M6_FATAL_EXECUTION_STATE", e.fatal is None),
                ("M6_JOURNAL_UNAVAILABLE", e.journal.ready),
                (self.account_reason or "M6_ACCOUNT_SNAPSHOT_STALE", self.account_current()),
                ("M6_ACCOUNT_BUDGET", self.capital_covers(row, price)),
                ("M6_ENTRY_WINDOW_ENDED", self.entry_window_open()),
                (signal_reason or "M6_SIGNAL_ENDED", signal_reason is None),
                ("M6_SUBMIT_INPUT_BACKLOG", -1e-9 <= age <= SUBMIT_PENDING_TOLERANCE_SEC+1e-9),
                ("M6_BOOK_UNAVAILABLE", self.feed.m1.legal(book, now)),
                ("M6_ANCHOR_UNAVAILABLE", retrying or any(p == opportunity.anchor_bid_price and q > 0 for p, q in book.bids)),
                ("M6_MAKER_PRICE_UNAVAILABLE", price is not None and book.best_ask is not None
                    and self.settings.minimum_price <= price < self.settings.maximum_price
                    and price < book.best_ask and not price % book.tick),
                ("M6_PREPARED_PRICE_CHANGED", quote is None or decimal(quote.price) == price),
                # Signing, journal flush and worker queueing consume the same
                # RF/EX window. Recheck the existing budget at native POST.
                ("M6_POST_LATENCY_BUDGET", quote is None and stage != 'AFTER_RESERVATION' or retrying
                    or (opportunity.order_deadline-now)*1000 >= self.post_budget_ms()),
                ("M6_SESSION_BUDGET", self.ledger.committed() <= self.settings.session_budget_usdc),
                (health.reason, health.allowed),
            ]
            self.guard_reason = next((reason for reason, valid in checks if not valid), None)
            allowed = self.guard_reason is None
            diagnostic = {'opportunity_id': opportunity.episode_id, 'stage': stage,
                'allowed': allowed, 'reason': self.guard_reason,
                'checked_monotonic': now,
                'remaining_ms': (opportunity.order_deadline-now)*1000,
                'required_remaining_ms': self.post_budget_ms(),
                'signal_reason': signal_reason, 'input_state': dict(self.guard_input_state)}
        # Queue-only diagnostics; never hold the input lock for serialization/fsync.
        e.record('sm_m6_submit_check', diagnostic)
        return allowed

    async def stop_unsent(self, opportunity):
        """Retain the actual failed check, including a recoverable unsent block."""
        row = self.ledger.row(opportunity.episode_id)
        reason = self.guard_reason or 'M6_ADMISSION_ENDED'
        await self.persist(self.ledger.outcome, opportunity.episode_id,
            'SKIPPED' if row['state'] == 'OBSERVED' else 'ABORTED', reason)
        if reason != 'M6_POST_LATENCY_BUDGET':
            await self.queue_retry(opportunity, proof={'kind': 'NOT_SENT'})

    def may_retry(self, opportunity):
        row = self.ledger.row(opportunity.episode_id)
        return (not self.stop.is_set() and self.e.fatal is None and self.e.journal.ready
                and not self.e.inhibited() and self.elapsed() < self.settings.cutoff_sec
                and (row["posts"] > 0 or (self.e.monotonic() < opportunity.order_deadline
                    and self.feed.m1.active_reason((opportunity.episode_id,), None) is None)))

    async def queue_retry(self, opportunity, *, proof=None):
        key = opportunity.episode_id
        if not self.may_retry(opportunity):
            return False
        row = self.ledger.row(key)
        uncertain = proof is not None and proof.get("kind", "").startswith("UNCERTAIN_")
        if uncertain and row["uncertain_retries"] >= 1:
            return False
        deferred = False
        if proof is not None:
            if uncertain and not self.account_current():
                deferred = True
            elif not await self.persist(self.ledger.retry, key, proof=proof, spendable=self.spendable(),
                                        direction_limit=self.settings.max_opportunities_per_direction):
                if not uncertain:
                    return False
                deferred = True  # Keep the allowed resend while cash is unavailable.
        count = row["posts"]
        delay = min(self.settings.retry_max_sec, self.settings.retry_initial_sec*2**min(10, max(0, count-1)))
        # A locally blocked, never-sent order has no network attempt to back
        # off. Wait for actual input progress, then run every guard again.
        input_wait = (self.guard_reason == 'M6_SUBMIT_INPUT_BACKLOG'
                      and (proof is None or proof.get('kind') == 'NOT_SENT') and not row['started'])
        if input_wait:
            delay = 0.
            self.retry_input_revision[key] = self.guard_input_revision
        else:
            self.retry_input_revision.pop(key, None)
        self.pending[key] = opportunity
        self.retry_after[key] = self.e.monotonic()+delay
        self.e.record("sm_m6_retry_wait", {"opportunity_id": key, "posts": count,
            "retry_after_sec": delay, "original_price": str(opportunity.anchor_bid_price),
            "cutoff_sec": self.settings.cutoff_sec, "proof": proof, "deferred_for_funds": deferred,
            "reason": self.guard_reason, "pending_input_age_ms": self.pending_input_age_ms,
            "wake_on_input_progress": input_wait,
            "guard_input_revision": self.guard_input_revision, "input_state": self.guard_input_state})
        return True

    async def submit(self, opportunity):
        async with self.submit_lock:
            await self._submit(opportunity)

    async def _submit(self, opportunity):
        e, key = self.e, opportunity.episode_id
        row = self.ledger.row(key)
        if row["state"] == "UNKNOWN":
            if row["uncertain_retries"] >= 1:
                return
            if self.guard(opportunity):
                await self.queue_retry(opportunity, proof={"kind": "UNCERTAIN_RESTART"})
            else:
                await self.queue_retry(opportunity)
            return
        if row["state"] not in {"OBSERVED", "RESERVED"}:
            return
        if not self.guard(opportunity):
            if not await self.queue_retry(opportunity):
                await self.persist(self.ledger.outcome, key, "SKIPPED" if row["state"] == "OBSERVED" else "ABORTED",
                                    self.guard_reason or self.account_reason or "M6_ADMISSION_ENDED")
            return
        if not row["posts"] and not self.initial_post_budget_available(opportunity):
            await self.persist(self.ledger.outcome, key, "SKIPPED" if row["state"] == "OBSERVED" else "ABORTED", "M6_POST_LATENCY_BUDGET")
            return
        if row["state"] == "OBSERVED" and not await self.persist(self.ledger.reserve, key, spendable=self.spendable(),
                direction_limit=self.settings.max_opportunities_per_direction):
            return
        if not self.guard(opportunity, stage='AFTER_RESERVATION'):
            await self.stop_unsent(opportunity)
            return
        row = self.ledger.row(key)
        price = self.quote_price(opportunity)
        if price is None:
            await self.queue_retry(opportunity)
            return
        quantity = self.ledger.order_quantity(price, e.meta.min_order_size)
        unchanged = (row['retry_pricing'] == 'SIGNAL' and price == decimal(row['price'])
                     and quantity == decimal(row['quantity'])
                     and price*quantity == decimal(row['reserved']))
        if not unchanged and not await self.persist(self.ledger.reserve_quote, key, price=price, quantity=quantity, spendable=self.spendable()):
            self.guard_reason = "M6_REPRICED_BUDGET_UNAVAILABLE"
            if not await self.queue_retry(opportunity):
                await self.persist(self.ledger.outcome, key, "ABORTED", self.guard_reason)
            return
        quote = WindowQuote(e.meta.condition_id, row["token"], row["outcome"], Side.BUY,
                            float(price), float(quantity), key)
        attempt = None
        outcome_recorded = False
        try:
            ticks = {token: float(book.tick) for token, book in self.feed.books.items()}
            began = time.monotonic()
            prepared = await e.gateway.prepare([Quote(quote.token_id, Side.BUY, quote.price, quote.size)], e.meta, ticks)
            e.record('sm_m6_prepare_timing', {'opportunity_id': key,
                'duration_ms': (time.monotonic()-began)*1000,
                'signer': getattr(getattr(e.gateway, 'signer', None), 'last_prepare_timing', None)})
            if not self.guard(opportunity, quote, stage='AFTER_SIGNING'):
                await self.stop_unsent(opportunity)
                outcome_recorded = True
                return
            oid = prepared.order_ids[0]
            await self.persist(self.ledger.prepare, key, oid, asdict(quote))
            e.record("sm_prepared", {"candidates": [{"order_ids": [oid], "quotes": [asdict(quote)],
                                                     "body_hash": prepared.body_hash}],
                                    "normal_entry_mode": STRATEGY})
            if not self.guard(opportunity, quote, stage='BEFORE_JOURNAL_BARRIER'):
                await self.stop_unsent(opportunity)
                outcome_recorded = True
                return
            began = time.monotonic()
            await e.journal.flush()
            e.record('sm_m6_journal_barrier', {'opportunity_id': key,
                'duration_ms': (time.monotonic()-began)*1000})
            attempt = Placement(f"{key}:post{row['posts']+1}", quote, expected_order_id=oid, order_id=oid)
            self.register(attempt)

            def post_start():
                observer = getattr(e.reads, 'fill_model', None)
                if observer is not None:
                    observer.post_start(attempt, cutoff_monotonic=e.time_anchor+
                        int(e.slug.rsplit('-', 1)[1])+self.settings.cutoff_sec-e.official_time)
                else:
                    self.ledger.post_start(key)
                e.record("sm_m6_native_post", {"opportunity_id": key, "order_id": oid,
                    "post_number": row["posts"]+1, "retry": bool(row["posts"]),
                    "signal_to_post_ms": (e.monotonic()-opportunity.signal_start_mono)*1000,
                    "rf_ex_remaining_ms": (opportunity.order_deadline-e.monotonic())*1000,
                    "quote": asdict(quote)})
                e.record("sm_placement_result", {**asdict(attempt), "post_started": True})

            # The real SDK executes the final guard in its worker immediately
            # before POST. A retry has its existing task's T240 authorization;
            # the original RF/EX times remain unchanged in the audit trail.
            raw = await e.gateway.send_prepared(prepared, submit_guard=lambda: self.guard(opportunity, quote, stage='NATIVE_POST'),
                operation_context={"attempt": attempt, "batch_attempts": [attempt],
                                   "network_batch_id": None, "on_post_start": post_start})
            if not attempt.post_started:
                attempt.outcome = "ABORTED"
                budget_ended = self.guard_reason == "M6_POST_LATENCY_BUDGET"
                await self.persist(self.ledger.outcome, key, "ABORTED", self.guard_reason or "M6_FINAL_GUARD_ENDED")
                outcome_recorded = True
                self.unregister_unsent(attempt)
                if not budget_ended:
                    await self.queue_retry(opportunity, proof={"kind": "NOT_SENT"})
            else:
                outcome, order = e._placement_outcomes(raw, [quote])[0]
                proof = rejection(raw, attempt.principal_limit) if outcome == "REJECTED" else None
                if outcome == "REJECTED" and proof is None:
                    outcome = "UNKNOWN"
                reason = "M6_ABNORMAL_POST_RESPONSE" if outcome == "UNKNOWN" else None
                if order is not None and order.order_id != oid:
                    outcome, reason = "UNKNOWN", "M6_ACK_SIGNED_ID_CONFLICT"
                attempt.outcome = outcome
                await self.persist(self.ledger.outcome, key, outcome, reason, proof=proof)
                outcome_recorded = True
                if outcome == "REJECTED":
                    self.unregister_unsent(attempt)
                    if proof.get("reprice"):
                        await self.queue_retry(opportunity, proof=proof)
                elif outcome == "UNKNOWN":
                    await self.queue_retry(opportunity, proof={"kind": "UNCERTAIN_RESPONSE"})
                e.record("sm_m6_baseline_post", {"opportunity_id": key, "order_id": oid,
                                            "outcome": outcome, "quote": asdict(quote)})
        except Exception as exc:
            if outcome_recorded:
                # Logging or retry bookkeeping after a durable response is not
                # another transport failure. Never downgrade ACK or lose proof.
                e.fatal = e.fatal or 'M6_OUTCOME_FOLLOWUP_FAILED'
                raise
            row = self.ledger.row(key)
            # The durable start bit is authoritative even if the SDK failed
            # between the callback and its in-memory post_started assignment.
            started = row["started"] or bool(attempt and attempt.post_started)
            proof = post_exception_rejection(exc, attempt.principal_limit) if started and attempt else None
            if not started:
                proof = {"kind": "NOT_SENT"}
            outcome = "REJECTED" if started and proof else "UNKNOWN" if started else "ABORTED"
            budget_ended = not started and self.guard_reason == "M6_POST_LATENCY_BUDGET"
            await self.persist(self.ledger.outcome, key, outcome, self.guard_reason if budget_ended else type(exc).__name__, proof=proof)
            if attempt:
                attempt.outcome = outcome
                if outcome in {"REJECTED", "ABORTED"}:
                    self.unregister_unsent(attempt)
            if proof is not None and not budget_ended:
                if not started or proof.get("reprice"):
                    await self.queue_retry(opportunity, proof=proof)
            elif started:
                await self.queue_retry(opportunity, proof={"kind": uncertain_kind(exc)})
            e.record("sm_m6_baseline_post_error", {"opportunity_id": key, "started": bool(started),
                                               "error_type": type(exc).__name__})
        finally:
            if attempt is not None:
                e.record("sm_placement_result", asdict(attempt))

    def unregister_unsent(self, attempt):
        oid = attempt.order_id
        self.e.known.discard(oid)
        self.e.gateway._client.known_ids.discard(oid)

    def finish_pending(self):
        reason = "M6_MANUAL_STOP_BEFORE_ACCEPTANCE" if self.stop.is_set() else "M6_T240_ENTRY_ENDED"
        for row in self.ledger.rows():
            if row["state"] == "OBSERVED":
                self.ledger.outcome(row["id"], "SKIPPED", reason)
            elif row["state"] in {"RESERVED", "PREPARED"} and not row["started"]:
                if self.stop.is_set() and row["posts"]:
                    continue  # Resume this existing failed task, never a new signal.
                self.ledger.outcome(row["id"], "ABORTED", reason)
        self.pending.clear()
        self.retry_after.clear()
        self.retry_input_revision.clear()

    def heartbeat(self):
        payload = {"strategy": STRATEGY, "slug": self.e.slug, "state": self.state,
            "elapsed_s": round(self.elapsed(), 1), "account_reason": self.account_reason,
            "signal_failure": self.feed.m1.failure, "reason": self.feed.m1.failure,
            "market_data_closed": self.feed_closed, "memory_recycle": self.memory_recycle,
            "signals": self.ledger.metric(), "signal_counts": dict(self.feed.m1.counts),
            "account_read_ms": self.account_read_ms,
            "account_age_ms": (self.e.monotonic()-self.account_at)*1000 if self.account else None,
            "submitted_unverified": self.ledger.metric()["submitted_unverified"]}
        observer = getattr(self.e.reads, 'fill_model', None)
        if observer is not None:
            payload['baseline_execution'] = observer.summary()
        self.e.record("sm_m6_heartbeat", payload)
        self.display(payload)

    async def refresh_ticks(self, tokens):
        try:
            ticks = await self.public_api.ticks(tokens)
            if set(ticks) != set(tokens):
                raise ValueError('M6_PUBLIC_TICK_INCOMPLETE')
            self.feed.verify_ticks(ticks)
            self.e.record('sm_m6_tick_verified', {'ticks': ticks})
        except Exception as exc:
            # Stale/missing REST data cannot overwrite the newer WS tick.
            # The existing book guard stays closed until both sources agree.
            self.e.record('sm_m6_tick_wait', {'error_type': type(exc).__name__})

    async def advance(self):
        """One shared pre-POST scheduling step for both live and virtual runners."""
        now = self.e.monotonic()
        if self.account_task is not None and self.account_task.done():
            await self.account_task
            self.account_task = None
        if self.post_task is not None and self.post_task.done():
            await self.post_task
            self.post_task = None
        if self.tick_task is not None and self.tick_task.done():
            await self.tick_task
            self.tick_task = None
        observer = getattr(self.e.reads, 'fill_model', None)
        if observer is not None:
            observer.poll()
        if self.elapsed() >= self.settings.cutoff_sec:
            await self.recycle_after_cutoff()
        else:
            await self.advance_entries(now)
        # Cash protection and stop controls remain active after feed shutdown.
        if (self.account_task is None and self.post_task is None
                and now-self.last_account >= self.settings.account_poll_sec):
            self.last_account = now
            self.account_task = asyncio.create_task(self.refresh_account())

    async def advance_entries(self, now):
        if (self.public_api is not None and self.tick_task is None and now-self.last_tick_refresh >= .5
                and self.elapsed() < self.settings.cutoff_sec and not self.stop.is_set() and not self.e.inhibited()):
            with self.feed.lock:
                tokens = tuple(t for t, b in self.feed.books.items() if self.feed.tick_verified.get(t) != b.tick)
            if tokens:
                self.last_tick_refresh = now
                self.tick_task = asyncio.create_task(self.refresh_ticks(tokens))
        await self.collect()
        now = self.e.monotonic()
        ready = [key for key, ep in self.pending.items() if self.retry_after.get(key, 0) <= now
                 and (self.feed.input_revision != self.retry_input_revision.get(key, -1)
                      or not self.ledger.row(key)['posts'] and now >= ep.order_deadline)
                 and (self.account_current() or self.elapsed() >= self.settings.cutoff_sec
                      or not self.ledger.row(key)["posts"] and now >= ep.order_deadline)]
        if self.post_task is None and ready:
            key = ready[0]
            self.retry_input_revision.pop(key, None)
            self.retry_after.pop(key, None)
            self.post_task = asyncio.create_task(self.submit(self.pending.pop(key)))

    async def recycle_after_cutoff(self):
        """T240 ends entry; pending baseline evidence drains before feed cleanup."""
        if self.elapsed() < self.settings.cutoff_sec or self.memory_recycled:
            return
        observer = getattr(self.e.reads, 'fill_model', None)
        if observer is not None:
            observer.poll()
            # A queued POST worker may still be arming its durable observer.
            if observer.pending or self.post_task is not None:
                return
        if not self.feed_closed:
            await self.feed.close()
            self.feed_closed = True
            self.e.record('sm_m6_feed_stopped', {'reason': 'M6_T240_ENTRY_ENDED',
                                               'elapsed_s': self.elapsed()})
        # Closing WS must not cancel a signing/POST worker or erase its inputs.
        # advance() reaps these tasks; heartbeats/stop polling keep running.
        if any(job is not None for job in (self.post_task, self.account_task, self.tick_task)):
            return
        self.finish_pending()
        # A failed durability barrier is fatal, never reported as successful GC.
        await self.e.journal.flush()
        self.memory_recycled = True
        began = self.elapsed()
        try:
            released = self.feed.release_signal_memory()
            self.memory_recycle = {'status': 'OK', 'elapsed_s': began,
                                   'released': released, **recycle_idle_memory()}
        except Exception as exc:
            self.memory_recycle = {'status': 'FAILED', 'elapsed_s': began,
                                   'error_type': type(exc).__name__}
        self.e.record('sm_m6_memory_recycled', self.memory_recycle)
        await self.e.journal.flush()

    async def run(self):
        e = self.e
        self.transition("PREPARING")
        await self.restore()
        try:
            while not self.stop.is_set() and self.elapsed() < self.settings.close_sec:
                if e.inhibited():
                    self.stop.set()
                    break
                now = e.monotonic()
                input_sequence = self.feed.sequence
                self.transition("PREPARING" if self.elapsed() < self.settings.start_sec else
                                "OPEN" if self.elapsed() < self.settings.cutoff_sec else "DRAINING")
                await self.advance()
                if now-self.last_heartbeat >= self.settings.heartbeat_sec:
                    self.last_heartbeat = now
                    self.heartbeat()
                e.drain_network_events()
                await self.wait_for_progress(input_sequence)
            # Never cancel SDK workers: a cancelled await does not stop POST.
            await self.drain()
            # A POST may span the whole quiet window. Reclaim only after its
            # durable outcome, before another market's feed can be started.
            await self.recycle_after_cutoff()
            self.finish_pending()
            if self.stop.is_set():
                self.transition("RETAINED")
                return False
            self.complete_window()
            return True
        finally:
            await self.drain()
            observer = getattr(e.reads, 'fill_model', None)
            if observer is not None:
                observer.stop('RUN_ENDED_BEFORE_OBSERVATION_END')
            await e.journal.flush()

    async def wait_for_progress(self, input_sequence):
        if self.feed_closed:
            await asyncio.sleep(.01)
            return
        # Remember the sequence BEFORE advance(): persistence can receive input
        # while yielding. Clearing the event against a newer sequence loses it.
        waiter = asyncio.create_task(self.feed.wait_after(input_sequence, .01))
        try:
            jobs = [waiter]+[job for job in (self.post_task, self.account_task, self.tick_task)
                             if job is not None]
            await asyncio.wait(jobs, return_when=asyncio.FIRST_COMPLETED)
        finally:
            # Only the local waiter is expendable; never cancel an SDK/commit.
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)

    def complete_window(self):
        """Record local observation completion; never send a market-end action."""
        if self.ledger.get("complete"):
            return
        e = self.e
        # Keep submitted IDs and commitments untouched. Neither ACK nor UNKNOWN
        # creates any cancellation, order query, Merge or redemption request.
        baseline = self.ledger.get('identity')['mode'] == 'VIRTUAL'
        settlement_policy = "PUBLIC_RESOLUTION_OF_MODELED_RECEIPTS" if baseline else "PLATFORM_AUTO_SETTLEMENT"
        settlement_status = "AWAITING_PUBLIC_RESOLUTION" if baseline else "AWAITING_PLATFORM_SETTLEMENT_UNVERIFIED"
        self.ledger.set("settlement_policy", settlement_policy)
        self.ledger.set("remaining_positions", None)
        self.ledger.set("settlement_status", settlement_status)
        self.ledger.set("market_end_actions", "PLATFORM_MANAGED")
        self.ledger.set("complete", True)
        self.transition("COMPLETE")
        self.heartbeat()
        e.record("sm_run_result", {"market_id": e.meta.condition_id, "slug": e.slug,
            "execution_status": "SUBMISSION_WINDOW_COMPLETE",
            "stop_reason": "M6_OBSERVATION_WINDOW_ENDED", "market_end_actions": "PLATFORM_MANAGED",
            "fill_tracking": "NOT_TRACKED", "positions_verified": False,
            "settlement_policy": settlement_policy,
            "settlement_status": settlement_status,
            "m6_submissions": self.ledger.metric()})
        return True

    async def drain(self):
        # Manual stop is an Event, never cancellation of an in-flight SDK call.
        jobs = [job for job in (self.post_task, self.account_task, self.tick_task) if job is not None]
        if jobs:
            await asyncio.gather(*jobs)
        self.post_task = self.account_task = self.tick_task = None
