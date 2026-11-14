"""Shared M1 signal-to-POST engine, plus the explicit opt-in real-account runner.

Imports are inert. Both modes share admission and submission. Market-end order
handling and settlement belong to the platform; no active cleanup is performed.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import signal
import time
import uuid
from contextlib import suppress
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

from polymaker.domain import Quote, Side
from polymaker.m1_live_account import account_snapshot
from polymaker.m1_live_config import M1LiveConfig
from polymaker.m1_live_http import sdk_http1_transport
from polymaker.m1_live_journal import M1RiskJournal
from polymaker.m1_live_retry import post_exception_rejection, rejection, uncertain_kind
from polymaker.m1_live_signal import M1LiveSignals
from polymaker.m1_live_state import M1Ledger, decimal
from polymaker.rdp_sm import OFFICIAL_HOSTS, _require
from polymaker.rdp_sm_entry import discover_current_market
from polymaker.rdp_sm_execution import (
    Placement,
    SMWindowExecutor,
    WindowQuote,
    _execution_identity,
    _ExecutionReads,
)
from polymaker.rdp_sm_m1_signal import Opportunity
from polymaker.rdp_sm_writer import AsyncRiskWriter

STRATEGY = "m1_live_v1"
# Scheduling lag of already received frames, distinct from source timestamp
# reordering. RF/EX sealing still uses the detector's original watermark rules.
SUBMIT_PENDING_TOLERANCE_SEC = .020


class M1Executor:
    """Submit-and-hold adapter, with no shared strategy lifecycle or fill tracker."""

    _placement_outcomes = SMWindowExecutor._placement_outcomes
    drain_network_events = SMWindowExecutor.drain_network_events

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
        # Public time probes let the existing network guard recover after a
        # failed POST. No order, trade or position reads are involved.
        values = await asyncio.gather(account_snapshot(self.reads, self.meta),
                                      self.reads.server_time(), return_exceptions=True)
        for value in values:
            if isinstance(value, BaseException):
                raise value
        return values[0]

    def record(self, kind, payload, *, event_wall=None):
        if kind.startswith("sm_merge_"):
            raise RuntimeError("M1_MERGE_DISABLED")
        try:
            self.journal.write(kind, {"session_id": self.session_id, "payload": payload},
                               time.time() if event_wall is None else event_wall)
        except Exception:
            self.fatal = "M1_JOURNAL_WRITE_FAILED"
            raise
        if self.display_events is not None:
            self.display_events.append((kind, payload))

    def close(self):
        self.closed = True
        try:
            self.reads.close()
        finally:
            self.journal.close()


class M1Market:
    def __init__(self, executor, settings, stop, *, display=print):
        self.e, self.settings, self.stop, self.display = executor, settings, stop, display
        self.ledger = executor.ledger
        self.feed = executor.reads.feed
        self.state = None
        self.account = None
        self.account_reason = "M1_ACCOUNT_WARMUP"
        self.account_at = 0.
        self.ledger.set("fill_tracking", "NOT_TRACKED")
        self.pending = {}
        self.retry_after = {}
        self.submit_lock = asyncio.Lock()
        self.guard_reason = None
        self.pending_input_age_ms = 0.
        self.post_task = self.account_task = None
        self.last_account = self.last_heartbeat = -float("inf")
        self.account_revision = 0
        self.account_read_ms = None

        def record_signal(row):
            if row["event"] in {"RF_CONFIRMED", "EX_CONFIRMED", "AP_CONFIRMED",
                                 "GAP", "UNAVAILABLE", "OPPORTUNITY_IDENTIFIED", "SIGNAL_INVALIDATED"}:
                executor.record("sm_m1_signal", row)
            return executor.journal.ready

        self.feed.m1 = M1LiveSignals(self.feed, signal_config=settings.signal,
            out_of_order_tolerance_ms=settings.out_of_order_tolerance_ms, record=record_signal)

    def elapsed(self):
        e = self.e
        return e.official_time+e.monotonic()-e.time_anchor-int(e.slug.rsplit("-", 1)[1])

    def transition(self, state):
        if self.state != state:
            self.e.record("sm_m1_state", {"previous": self.state, "state": state})
            self.state = state

    @staticmethod
    def restored_opportunity(row):
        evidence = json.loads(row["evidence"])
        evidence.pop("elapsed_s", None)
        evidence["anchor_bid_price"] = decimal(evidence["anchor_bid_price"])
        opportunity = Opportunity(**evidence)
        _require(opportunity.episode_id == row["id"] and opportunity.token_id == row["token"]
                 and opportunity.anchor_bid_price == decimal(row["price"]), "M1_RETRY_RECOVERY_IDENTITY")
        return opportunity

    def restore(self):
        for old in self.ledger.uncertain_posts():
            quote = json.loads(old["quote"])
            quote["side"] = Side(quote["side"])
            if old["order_id"] not in self.e.known:
                self.register(Placement(old["opportunity_id"]+":uncertain", WindowQuote(**quote), outcome="UNKNOWN",
                    expected_order_id=old["order_id"], order_id=old["order_id"], post_started=True))
        for row in self.ledger.rows():
            if not row["started"]:
                if row["posts"] and row["state"] in {"RESERVED", "PREPARED"} and self.elapsed() < self.settings.cutoff_sec:
                    proof = next((p for kind, p in reversed(self.ledger.events("m1_retry_authorized"))
                                  if p["opportunity_id"] == row["id"]), None)
                    _require(proof is not None, "M1_RETRY_RECOVERY_PROOF_MISSING")
                    if row["state"] == "PREPARED":
                        self.ledger.outcome(row["id"], "ABORTED", "RESTART_UNSENT_RETRY_PREPARATION")
                        _require(self.ledger.retry(row["id"], proof={"kind": "NOT_SENT"}), "M1_RETRY_RECOVERY_BUDGET")
                    self.pending[row["id"]] = self.restored_opportunity(row)
                    continue
                if row["state"] not in {"REJECTED", "SKIPPED", "ABORTED"}:
                    self.ledger.outcome(row["id"], "ABORTED", "RESTART_BEFORE_NATIVE_POST")
                continue
            if row["state"] == "REJECTED":
                continue
            quote = json.loads(row["quote"])
            quote["side"] = Side(quote["side"])
            attempt = Placement(row["id"], WindowQuote(**quote), outcome="UNKNOWN",
                expected_order_id=row["order_id"], order_id=row["order_id"], post_started=True)
            if row["order_id"] not in self.e.known:
                self.register(attempt)
            if row["state"] == "UNKNOWN" and row["uncertain_retries"] == 0 and self.elapsed() < self.settings.cutoff_sec:
                self.pending[row["id"]] = self.restored_opportunity(row)

    def register(self, attempt):
        e, oid = self.e, attempt.expected_order_id
        e.attempts.append(attempt)
        e.known.add(oid)
        e.gateway._client.known_ids.add(oid)

    async def refresh_account(self):
        e = self.e
        if not self.account_current():
            self.account_reason = "M1_ACCOUNT_REFRESHING"
        began = e.monotonic()
        try:
            account = await e.account_snapshot()
            _require(all(decimal(account[k]) >= 0 for k in
                         ("official_balance_usd", "applicable_allowance_usd")), "CAPITAL_READ_UNKNOWN")
            _require(e.monotonic()-began <= self.settings.account_max_age_sec, "M1_ACCOUNT_READ_TOO_OLD")
            self.account, self.account_at = account, began
            self.account_reason = None
            self.account_read_ms = (e.monotonic()-began)*1000
            e.record("sm_m1_account_snapshot", {"duration_ms": self.account_read_ms,
                "source": account["source"],
                "fresh_until_monotonic": began+self.settings.account_max_age_sec})
        except Exception as exc:
            self.account_reason = str(exc) or type(exc).__name__
            e.record("sm_m1_wait", {"reason": self.account_reason})
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

    def collect(self):
        elapsed = self.elapsed()
        if not self.entry_window_open():
            return

        def skipped(opportunity, reason):
            if self.feed.m1.take(opportunity):
                self.e.record("sm_m1_candidate_skipped", {"opportunity": asdict(opportunity),
                                                        "reason": reason, "elapsed": elapsed})

        with self.feed.lock:
            snapshot = self.feed.snapshot()
            # select() materializes both token-local candidates. Consume each
            # only after recording its immutable target in the durable ledger.
            for _ in range(2):
                self.feed.m1.evaluate(snapshot)
                opportunity = self.feed.m1.selected
                if opportunity is None:
                    break
                if not self.settings.minimum_price <= opportunity.anchor_bid_price <= self.settings.maximum_price:
                    skipped(opportunity, "PRICE_OUTSIDE_TEST_BAND")
                    continue
                book = self.feed.books[opportunity.token_id]
                if (not self.feed.m1.legal(book, self.e.monotonic())
                        or book.best_ask <= opportunity.anchor_bid_price
                        or opportunity.anchor_bid_price % book.tick):
                    skipped(opportunity, "MAKER_QUOTE_NOT_AVAILABLE")
                    continue
                try:
                    row = self.ledger.observe(opportunity, minimum=self.e.meta.min_order_size, elapsed=elapsed)
                except ValueError:
                    skipped(opportunity, "VENUE_MINIMUM_OR_PRECISION")
                    continue
                if self.feed.m1.take(opportunity) and row is not None:
                    self.pending[row["id"]] = opportunity
                    self.e.record("sm_m1_live_signal", row)

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

    def guard(self, opportunity, quote=None):
        e = self.e
        with self.feed.lock:
            book = self.feed.books[opportunity.token_id]
            retrying = bool(self.ledger.row(opportunity.episode_id)["posts"])
            price = self.quote_price(opportunity)
            now = e.monotonic()
            pending = ([self.feed.m1_pending_received[0]] if self.feed.m1_pending_received else [])
            if self.feed.m1_processing_received is not None:
                pending.append(self.feed.m1_processing_received)
            age = now-min(pending) if pending else 0.
            self.pending_input_age_ms = age*1000
            signal_reason = None if retrying else self.feed.m1.active_reason((opportunity.episode_id,), None)
            health = e.reads.network.snapshot()
            checks = [
                ("M1_STOPPING", not self.stop.is_set() and not e.closed),
                (e.fatal or "M1_FATAL_EXECUTION_STATE", e.fatal is None),
                ("M1_JOURNAL_UNAVAILABLE", e.journal.ready),
                (self.account_reason or "M1_ACCOUNT_SNAPSHOT_STALE", self.account_current()),
                ("M1_ENTRY_WINDOW_ENDED", self.entry_window_open()),
                (signal_reason or "M1_SIGNAL_ENDED", signal_reason is None),
                ("M1_SUBMIT_INPUT_BACKLOG", -1e-9 <= age <= SUBMIT_PENDING_TOLERANCE_SEC+1e-9),
                ("M1_BOOK_UNAVAILABLE", self.feed.m1.legal(book, now)),
                ("M1_ANCHOR_UNAVAILABLE", retrying or any(p == opportunity.anchor_bid_price and q > 0 for p, q in book.bids)),
                ("M1_MAKER_PRICE_UNAVAILABLE", price is not None and book.best_ask is not None
                    and self.settings.minimum_price <= price <= self.settings.maximum_price
                    and price < book.best_ask and not price % book.tick),
                ("M1_PREPARED_PRICE_CHANGED", quote is None or decimal(quote.price) == price),
                # Signing, journal flush and worker queueing consume the same
                # RF/EX window. Recheck the existing budget at native POST.
                ("M1_POST_LATENCY_BUDGET", quote is None or retrying
                    or (opportunity.order_deadline-now)*1000 >= self.post_budget_ms()),
                ("M1_SESSION_BUDGET", self.ledger.committed() <= self.settings.session_budget_usdc),
                (health.reason, health.allowed),
            ]
            self.guard_reason = next((reason for reason, valid in checks if not valid), None)
            return self.guard_reason is None

    def may_retry(self, opportunity):
        row = self.ledger.row(opportunity.episode_id)
        return (not self.stop.is_set() and self.e.fatal is None and self.e.journal.ready
                and self.elapsed() < self.settings.cutoff_sec
                and (row["posts"] > 0 or (self.e.monotonic() < opportunity.order_deadline
                    and self.feed.m1.active_reason((opportunity.episode_id,), None) is None)))

    def queue_retry(self, opportunity, *, proof=None):
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
            elif not self.ledger.retry(key, proof=proof, spendable=self.spendable(),
                                       direction_limit=self.settings.max_opportunities_per_direction):
                if not uncertain:
                    return False
                deferred = True  # Keep the allowed resend while cash is unavailable.
        count = row["posts"]
        delay = min(self.settings.retry_max_sec, self.settings.retry_initial_sec*2**min(10, max(0, count-1)))
        self.pending[key] = opportunity
        self.retry_after[key] = self.e.monotonic()+delay
        self.e.record("sm_m1_retry_wait", {"opportunity_id": key, "posts": count,
            "retry_after_sec": delay, "original_price": str(opportunity.anchor_bid_price),
            "cutoff_sec": self.settings.cutoff_sec, "proof": proof, "deferred_for_funds": deferred,
            "reason": self.guard_reason, "pending_input_age_ms": self.pending_input_age_ms})
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
                self.queue_retry(opportunity, proof={"kind": "UNCERTAIN_RESTART"})
            else:
                self.queue_retry(opportunity)
            return
        if row["state"] not in {"OBSERVED", "RESERVED"}:
            return
        if not self.guard(opportunity):
            if not self.queue_retry(opportunity):
                self.ledger.outcome(key, "SKIPPED" if row["state"] == "OBSERVED" else "ABORTED",
                                    self.guard_reason or self.account_reason or "M1_ADMISSION_ENDED")
            return
        if not row["posts"] and (opportunity.order_deadline-e.monotonic())*1000 < self.post_budget_ms():
            self.ledger.outcome(key, "SKIPPED" if row["state"] == "OBSERVED" else "ABORTED", "M1_POST_LATENCY_BUDGET")
            return
        if row["state"] == "OBSERVED" and not self.ledger.reserve(key, spendable=self.spendable(),
                direction_limit=self.settings.max_opportunities_per_direction):
            return
        row = self.ledger.row(key)
        price = self.quote_price(opportunity)
        if price is None:
            self.queue_retry(opportunity)
            return
        quantity = self.ledger.order_quantity(price, e.meta.min_order_size)
        if not self.ledger.reserve_quote(key, price=price, quantity=quantity, spendable=self.spendable()):
            self.guard_reason = "M1_REPRICED_BUDGET_UNAVAILABLE"
            if not self.queue_retry(opportunity):
                self.ledger.outcome(key, "ABORTED", self.guard_reason)
            return
        quote = WindowQuote(e.meta.condition_id, row["token"], row["outcome"], Side.BUY,
                            float(price), float(quantity), key)
        attempt = None
        try:
            ticks = {token: float(book.tick) for token, book in self.feed.books.items()}
            prepared = await e.gateway.prepare([Quote(quote.token_id, Side.BUY, quote.price, quote.size)], e.meta, ticks)
            oid = prepared.order_ids[0]
            self.ledger.prepare(key, oid, asdict(quote))
            e.record("sm_prepared", {"candidates": [{"order_ids": [oid], "quotes": [asdict(quote)],
                                                     "body_hash": prepared.body_hash}],
                                    "normal_entry_mode": STRATEGY})
            await e.journal.flush()
            attempt = Placement(f"{key}:post{row['posts']+1}", quote, expected_order_id=oid, order_id=oid)
            self.register(attempt)

            def post_start():
                self.ledger.post_start(key)
                e.record("sm_m1_native_post", {"opportunity_id": key, "order_id": oid,
                    "post_number": row["posts"]+1, "retry": bool(row["posts"]),
                    "signal_to_post_ms": (e.monotonic()-opportunity.signal_start_mono)*1000,
                    "rf_ex_remaining_ms": (opportunity.order_deadline-e.monotonic())*1000,
                    "quote": asdict(quote)})
                e.record("sm_placement_result", {**asdict(attempt), "post_started": True})

            # The real SDK executes the final guard in its worker immediately
            # before POST. A retry has its existing task's T240 authorization;
            # the original RF/EX times remain unchanged in the audit trail.
            raw = await e.gateway.send_prepared(prepared, submit_guard=lambda: self.guard(opportunity, quote),
                operation_context={"attempt": attempt, "batch_attempts": [attempt],
                                   "network_batch_id": None, "on_post_start": post_start})
            if not attempt.post_started:
                attempt.outcome = "ABORTED"
                budget_ended = self.guard_reason == "M1_POST_LATENCY_BUDGET"
                self.ledger.outcome(key, "ABORTED", self.guard_reason if budget_ended else "M1_FINAL_GUARD_ENDED")
                self.unregister_unsent(attempt)
                if not budget_ended:
                    self.queue_retry(opportunity, proof={"kind": "NOT_SENT"})
            else:
                outcome, order = e._placement_outcomes(raw, [quote])[0]
                proof = rejection(raw, attempt.principal_limit) if outcome == "REJECTED" else None
                if outcome == "REJECTED" and proof is None:
                    outcome = "UNKNOWN"
                reason = "M1_ABNORMAL_POST_RESPONSE" if outcome == "UNKNOWN" else None
                if order is not None and order.order_id != oid:
                    outcome, reason = "UNKNOWN", "M1_ACK_SIGNED_ID_CONFLICT"
                attempt.outcome = outcome
                self.ledger.outcome(key, outcome, reason)
                if outcome == "REJECTED":
                    self.unregister_unsent(attempt)
                    if proof["retryable"]:
                        self.queue_retry(opportunity, proof=proof)
                elif outcome == "UNKNOWN":
                    self.queue_retry(opportunity, proof={"kind": "UNCERTAIN_RESPONSE"})
                e.record("sm_m1_live_post", {"opportunity_id": key, "order_id": oid,
                                            "outcome": outcome, "quote": asdict(quote)})
        except Exception as exc:
            row = self.ledger.row(key)
            # The durable start bit is authoritative even if the SDK failed
            # between the callback and its in-memory post_started assignment.
            started = row["started"] or bool(attempt and attempt.post_started)
            proof = post_exception_rejection(exc, attempt.principal_limit) if started and attempt else None
            if not started:
                proof = {"kind": "NOT_SENT"}
            outcome = "REJECTED" if started and proof else "UNKNOWN" if started else "ABORTED"
            budget_ended = not started and self.guard_reason == "M1_POST_LATENCY_BUDGET"
            self.ledger.outcome(key, outcome, self.guard_reason if budget_ended else type(exc).__name__)
            if attempt:
                attempt.outcome = outcome
                if outcome in {"REJECTED", "ABORTED"}:
                    self.unregister_unsent(attempt)
            if proof is not None and not budget_ended:
                if proof.get("retryable", True):
                    self.queue_retry(opportunity, proof=proof)
            elif started:
                self.queue_retry(opportunity, proof={"kind": uncertain_kind(exc)})
            e.record("sm_m1_live_post_error", {"opportunity_id": key, "started": bool(started),
                                               "error_type": type(exc).__name__})
        finally:
            if attempt is not None:
                e.record("sm_placement_result", asdict(attempt))

    def unregister_unsent(self, attempt):
        oid = attempt.order_id
        self.e.known.discard(oid)
        self.e.gateway._client.known_ids.discard(oid)

    def finish_pending(self):
        reason = "M1_MANUAL_STOP_BEFORE_ACCEPTANCE" if self.stop.is_set() else "M1_T240_ENTRY_ENDED"
        for row in self.ledger.rows():
            if row["state"] == "OBSERVED":
                self.ledger.outcome(row["id"], "SKIPPED", reason)
            elif row["state"] in {"RESERVED", "PREPARED"} and not row["started"]:
                if self.stop.is_set() and row["posts"]:
                    continue  # Resume this existing failed task, never a new signal.
                self.ledger.outcome(row["id"], "ABORTED", reason)
        self.pending.clear()
        self.retry_after.clear()

    def heartbeat(self):
        payload = {"strategy": STRATEGY, "slug": self.e.slug, "state": self.state,
            "elapsed_s": round(self.elapsed(), 1), "account_reason": self.account_reason,
            "signal_failure": self.feed.m1.failure, "reason": self.feed.m1.failure,
            "signals": self.ledger.metric(), "signal_counts": dict(self.feed.m1.counts),
            "account_read_ms": self.account_read_ms,
            "account_age_ms": (self.e.monotonic()-self.account_at)*1000 if self.account else None,
            "submitted_unverified": self.ledger.metric()["submitted_unverified"]}
        self.e.record("sm_m1_heartbeat", payload)
        self.display(payload)

    async def advance(self):
        """One shared pre-POST scheduling step for both live and virtual runners."""
        now = self.e.monotonic()
        if self.account_task is not None and self.account_task.done():
            await self.account_task
            self.account_task = None
        if self.post_task is not None and self.post_task.done():
            await self.post_task
            self.post_task = None
        self.collect()
        ready = [key for key, ep in self.pending.items() if self.retry_after.get(key, 0) <= now
                 and (self.account_current() or self.elapsed() >= self.settings.cutoff_sec
                      or not self.ledger.row(key)["posts"] and now >= ep.order_deadline)]
        if self.post_task is None and ready:
            key = ready[0]
            self.post_task = asyncio.create_task(self.submit(self.pending.pop(key)))
        if (self.account_task is None and self.post_task is None
                and now-self.last_account >= self.settings.account_poll_sec):
            self.last_account = now
            self.account_task = asyncio.create_task(self.refresh_account())

    async def run(self):
        e = self.e
        self.transition("PREPARING")
        self.restore()
        try:
            while not self.stop.is_set() and self.elapsed() < self.settings.close_sec:
                now = e.monotonic()
                self.transition("PREPARING" if self.elapsed() < self.settings.start_sec else
                                "OPEN" if self.elapsed() < self.settings.cutoff_sec else "DRAINING")
                await self.advance()
                if now-self.last_heartbeat >= self.settings.heartbeat_sec:
                    self.last_heartbeat = now
                    self.heartbeat()
                e.drain_network_events()
                await asyncio.sleep(.01)
            # Never cancel SDK workers: a cancelled await does not stop POST.
            await self.drain()
            self.finish_pending()
            if self.stop.is_set():
                self.transition("RETAINED")
                return False
            self.complete_window()
            return True
        finally:
            await self.drain()
            await e.journal.flush()

    def complete_window(self):
        """Record local observation completion; never send a market-end action."""
        if self.ledger.get("complete"):
            return
        e = self.e
        # Keep submitted IDs and commitments untouched. Neither ACK nor UNKNOWN
        # creates any cancellation, order query, Merge or redemption request.
        self.ledger.set("settlement_policy", "PLATFORM_AUTO_SETTLEMENT")
        self.ledger.set("remaining_positions", None)
        self.ledger.set("settlement_status", "AWAITING_PLATFORM_SETTLEMENT_UNVERIFIED")
        self.ledger.set("market_end_actions", "PLATFORM_MANAGED")
        self.ledger.set("complete", True)
        self.transition("COMPLETE")
        self.heartbeat()
        e.record("sm_run_result", {"market_id": e.meta.condition_id, "slug": e.slug,
            "execution_status": "SUBMISSION_WINDOW_COMPLETE",
            "stop_reason": "M1_OBSERVATION_WINDOW_ENDED", "market_end_actions": "PLATFORM_MANAGED",
            "fill_tracking": "NOT_TRACKED", "positions_verified": False,
            "settlement_policy": "PLATFORM_AUTO_SETTLEMENT",
            "settlement_status": "AWAITING_PLATFORM_SETTLEMENT_UNVERIFIED",
            "m1_submissions": self.ledger.metric()})
        return True

    async def drain(self):
        # Manual stop is an Event, never cancellation of an in-flight SDK call.
        jobs = [job for job in (self.post_task, self.account_task) if job is not None]
        if jobs:
            await asyncio.gather(*jobs)
        self.post_task = self.account_task = None


async def run_m1(original, settings=None, *, live=False, once=False, preflight_only=False,
                 stop=None, display=print):
    # Real transport requires an explicit caller opt-in. Existing fault-injection
    # tests retain the real SDK behind an in-memory transport without that opt-in.
    import httpx
    from py_clob_client_v2.http_helpers import helpers

    _require(live is True or isinstance(helpers._http_client._transport, httpx.MockTransport),
             "M1_REAL_POST_DISABLED_REQUIRES_EXPLICIT_LIVE")
    with sdk_http1_transport():
        return await _run_m1(original, settings, live=live, once=once,
                             preflight_only=preflight_only, stop=stop, display=display)


async def _run_m1(original, settings=None, *, live=False, once=False, preflight_only=False,
                  stop=None, display=print):
    settings = settings or M1LiveConfig()
    cfg = settings.execution_config(original)
    _require(all(getattr(cfg.wallet, k).rstrip("/") == v for k, v in OFFICIAL_HOSTS.items())
             and cfg.wallet.chain_id == 137, "OFFICIAL_ENDPOINT_CONFIGURATION_REQUIRED")
    stop = stop or asyncio.Event()
    loop = asyncio.get_running_loop()
    installed = []
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop.set)
            installed.append(signum)
        except (NotImplementedError, ValueError):
            pass
    reads = None
    executor = None
    ledger = None
    completed = 0
    last_wait_log = -float("inf")
    source_sha256 = {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
        for name in ("m1_live.py", "m1_live_config.py", "m1_live_state.py", "m1_live_signal.py",
                     "m1_live_account.py", "m1_live_retry.py", "m1_live_journal.py",
                     "rdp_sm_writer.py", "rdp_sm_execution.py", "m1_live_http.py")}
    try:
        reads = _ExecutionReads(cfg)
        await reads.connect_account()
        _execution_identity(reads)
        while not stop.is_set():
            meta, _, now, _ = await discover_current_market(cfg)
            digest = hashlib.sha256(f"{reads.gateway.funder.lower()}:{meta.condition_id}".encode()).hexdigest()
            directory = await asyncio.to_thread(Path(cfg.paths.journal_dir).resolve)
            path = directory/f"m1_live_{digest}.sqlite3"
            recovering = await asyncio.to_thread(path.exists)
            if preflight_only:
                account = await account_snapshot(reads, meta)
                return {"status": "READ_ONLY", "recovering": recovering, "slug": meta.slug,
                        "settings": settings.model_dump(mode="json"), "account": account}
            identity = {"strategy": STRATEGY, "funder": reads.gateway.funder.lower(),
                        "market_id": meta.condition_id, "settings_hash": settings.fingerprint}
            ledger = M1Ledger(path, identity)
            if ledger.get("complete"):
                ledger.close()
                ledger = None
                if once:
                    return {"status": "MARKET_ALREADY_COMPLETE", "markets_completed": completed}
                if time.monotonic()-last_wait_log >= settings.heartbeat_sec:
                    last_wait_log = time.monotonic()
                    display({"strategy": STRATEGY, "state": "WAIT_NEXT_MARKET", "slug": meta.slug,
                             "reason": "M1_SUBMISSION_WINDOW_ALREADY_COMPLETE"})
                wait = min(5., max(.01, int(meta.slug.rsplit("-", 1)[1])+300-now))
                with suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=wait)
                continue
            sid = ledger.get("session_id")
            if sid is None:
                sid = "m1_live_"+uuid.uuid4().hex
                ledger.set("session_id", sid)
            backing = M1RiskJournal(cfg.paths.journal_dir, reads.gateway.funder, session_id=sid, resume=recovering)
            journal = AsyncRiskWriter(backing, batch_size=128)
            journal.accepted = journal.written = backing.last_writer_sequence
            executor = M1Executor(cfg, meta.slug, sid, reads, journal, ledger=ledger)
            executor.meta, executor.session_market_id = meta, meta.condition_id
            clock_started = time.monotonic()
            now = await reads.server_time()
            executor.official_time, executor.time_anchor = float(now), time.monotonic()
            executor.official_time_read_duration_s = executor.time_anchor-clock_started
            executor.official_time_uncertainty_s = 1+executor.official_time_read_duration_s
            if float(now) < int(meta.slug.rsplit("-", 1)[1])+settings.close_sec:
                await reads.ensure_feed(meta)
            else:
                # A retained ended market can be closed without a functioning
                # public subscription or a tick read from an inactive market.
                from polymaker.rdp_sm_marketdata import SessionFeed

                reads.feed = SessionFeed(meta, cfg.rdp_sm.obi, normal_entry_mode="m1_v1",
                                         m1_config=cfg.rdp_sm.m1)
            executor.record("sm_session", {"normal_entry_mode": "m1_v1", "strategy": STRATEGY,
                "execution_mode": "LIVE" if live else "OFFLINE_TEST", "source_sha256": source_sha256,
                "slug": meta.slug, "market_id": meta.condition_id, "settings": settings.model_dump(mode="json"),
                "m1": settings.model_dump(mode="json"), "m1_signal_spec": settings.signal.model_dump(mode="json"),
                "m1_execution_policy": {"account_reads": "BALANCE_ALLOWANCE_ONLY",
                    "account_mutex": False, "fill_tracking": "NOT_TRACKED",
                    "max_opportunities": 5, "uncertain_retries_per_opportunity": 1,
                    "retry_price": "SIGNAL_UNLESS_CROSSING_REJECTION",
                    "market_end_actions": "PLATFORM_MANAGED",
                    "account_max_age_sec": settings.account_max_age_sec,
                    "submit_pending_tolerance_ms": SUBMIT_PENDING_TOLERANCE_SEC*1000,
                    "initial_post_requires_rf_ex": True, "failed_task_retry_cutoff_sec": settings.cutoff_sec},
                "test_capital_limit_usdc": settings.session_budget_usdc,
                "state_path": str(ledger.path), "recovery": recovering})
            market = M1Market(executor, settings, stop, display=display)
            clean = await market.run()
            await reads.feed.close()
            if clean:
                completed += 1
            metric = ledger.metric()
            executor.close()
            executor = None
            ledger.close()
            ledger = None
            reads = None
            if not clean or once or stop.is_set():
                return {"status": "COMPLETE" if clean else "RETAINED" if stop.is_set() else "RECOVERY_BLOCKED",
                        "markets_completed": completed, "signals": metric}
            reads = _ExecutionReads(cfg)
            await reads.connect_account()
            _execution_identity(reads)
        return {"status": "STOPPED", "markets_completed": completed}
    finally:
        if executor is not None:
            if reads and reads.feed:
                await reads.feed.close()
            executor.close()
        elif reads is not None:
            reads.close()
        if ledger is not None:
            ledger.close()
        for signum in installed:
            loop.remove_signal_handler(signum)
