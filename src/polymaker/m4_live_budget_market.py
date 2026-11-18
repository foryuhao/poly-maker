"""Live-only budget-task scheduling. Queries never occupy the POST lane."""

import asyncio
import copy
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from threading import local
from types import SimpleNamespace
from uuid import uuid4

from polymaker.domain import MarketMeta, TokenMeta
from polymaker.m4_live_budget import (
    FAK_BUY_MIN_PRINCIPAL,
    FAK_BUY_MINIMUM_POLICY,
    BudgetLedger,
    minimum_fak_buy_ok,
)
from polymaker.m4_live_budget_reconcile import reconcile_owned
from polymaker.m4_live_config import M4LiveSettings, all_in_cost, gross_risk_cost
from polymaker.m4_live_numeric import age_us, budget_quantity, install_live_clock_boundaries
from polymaker.m4_live_price_band import (
    ABANDON_BEST_ASK_BAND, ABANDON_FIRST_SIGNED_BAND, ANCHOR_PRICE_FLOOR, POST_PRICE_OUTSIDE_BAND,
)
from polymaker.m4_live_pricing import BOOT_ID, WAIT_US, origin, quote_valid, rejected_price_band_valid, signal_age
from polymaker.m4_live_task import TERMINAL_TASKS, TaskMarket
from polymaker.m4_state import decimal
from polymaker.m4_types import M4_LEDGER, M6_LEDGER
from polymaker.rdp_sm_network import NetworkHealth


class BudgetMarket(TaskMarket):
    async def run(self):
        """Live lifecycle: latch any failure before draining outstanding workers.

        Keep this boundary live-only. The baseline's observation lifecycle and
        fill model continue to use M4Market.run unchanged.
        """
        e = self.e
        try:
            self.transition("PREPARING")
            self.restore()
            while not self.stop.is_set() and self.elapsed() < self.settings.close_sec:
                if e.inhibited():
                    self.stop.set()
                    break
                now = e.monotonic()
                self.transition("HALTED_READ_ONLY" if e.fatal else
                                "PREPARING" if self.elapsed() < self.settings.start_sec else
                                "OPEN" if self.elapsed() < self.settings.cutoff_sec else "DRAINING")
                await self.advance()
                if now - self.last_heartbeat >= self.settings.heartbeat_sec:
                    self.last_heartbeat = now
                    self.heartbeat()
                if self.account_owner is None:
                    e.drain_network_events()
                await asyncio.sleep(.01)
            await self.drain()
            await self.recycle_after_cutoff()
            self.finish_pending()
            if self.stop.is_set():
                self.transition("RETAINED")
                return False
            self.complete_window()
            return True
        except Exception:
            e.fatal = e.fatal or "M4_MARKET_RUNTIME_FAILED"
            raise
        finally:
            # Do not cancel a native call; let its exact outcome become durable.
            try:
                await self.drain()
                await e.journal.flush()
            except Exception:
                e.fatal = e.fatal or "M4_MARKET_DRAIN_FAILED"
                raise

    def __init__(self, *args, opening=None, account_owner=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.opening, self.account_owner = opening, account_owner
        if self.feed.m1.price_risk != self.settings.live_taker_price_risk:
            raise ValueError("M4_OPPORTUNITY_PRICE_RISK_CONFLICT")
        if not self.ledger.signal_limited:
            install_live_clock_boundaries(self.feed)
        self.read_jobs = {}
        self.read_after = {}
        self.read_failures = {}
        self.read_cursor = 0
        self.posting_keys = set()
        self.post_jobs = {}
        self.task_locks = {}
        self.post_slots = asyncio.Semaphore(2)
        self.guard_results = {}
        self.retry_book_states = {}
        self.retry_wake_after = {}
        self.retry_recovered = set()
        self.pricing_due_woken = set()
        # Initialize once on the event loop, before concurrent worker threads run.
        client = self.e.gateway._client
        if not hasattr(client, "operation_local"):
            client.operation_local = local()
        client.m4_native_guard = self.native_guard
        client.m4_verified_tick_provider = self.verified_sdk_tick
        self.guard_counts = {}
        self.last_guard_log = {}
        self.price_band_waits = {}
        self.archive_task = None
        self.last_archive = -float("inf")
        self.archive_cursor = 0
        self.archive_network = NetworkHealth(client.sm_config.obi)
        if self.e.fatal:
            self.ledger.set("market_halt_reason", self.e.fatal)
            self.ledger.set("buy_closed", True)
            client.m4_read_only = True
        modules = (
            "m4_live_budget.py",
            "m4_live_budget_market.py",
            "m4_live_budget_reconcile.py",
            "m4_live_numeric.py",
            "m4_live_pricing.py",
            "m4_live_http.py",
            "m4_live_price_band.py",
            "m4_live_config.py",
            "m4_live_task.py",
            "m4_state.py",
        )
        self.e.record(
            "sm_m4_budget_execution_policy",
            {
                "target_all_in_usdc": str(self.ledger.order_budget),
                "taker_strategy": self.ledger.get("settings", {}).get("taker_strategy"),
                "task_policy": self.ledger.task_policy,
                "post_marketdata_policy": (
                    "FAK_FRESH_BEST_ASK_UNTIL_SIGNAL_DEADLINE_V1" if self.ledger.signal_limited
                    else "ADMITTED_FAK_ANCHOR_10S_THEN_BEST_ASK_V1" if self.ledger.dynamic_pricing
                    else "ADMITTED_FAK_FIXED_CAP_EXCHANGE_MATCH_V1"),
                "pricing_policy": self.settings.execution.taker.pricing_policy,
                "anchor_wait_sec": 10 if self.ledger.dynamic_pricing and not self.ledger.signal_limited else None,
                "amount_precision": "FAK_BUY_CENTS_SHARES_0_1_V1",
                "remainder_policy": "LEGAL_ORDER_ONLY_QUOTE_CAPTURE_CENT_DUST_V1",
                "fak_buy_minimum_policy": FAK_BUY_MINIMUM_POLICY,
                "fak_buy_min_principal_usdc": str(FAK_BUY_MIN_PRINCIPAL),
                "sdk_tick_policy": "OFFICIAL_VERIFIED_BOOK_TICK_V1",
                "post_price_band_policy": (self.ledger.post_price_band or {}).get("policy", ANCHOR_PRICE_FLOOR),
                "post_minimum_price": (self.ledger.post_price_band or {}).get("minimum"),
                "post_maximum_price": (self.ledger.post_price_band or {}).get("maximum"),
                "signal_anchor_price_filter": not bool(self.ledger.post_price_band),
                "post_price_band_retry_policy": self.settings.post_price_band_retry_policy,
                "post_price_band_source": (self.settings.live_taker_price_risk.plan_fields()["post_price_band_source"]
                                           if self.settings.live_taker_price_risk else "SAVED_EXECUTION_SETTINGS"),
                "source_sha256": {
                    name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                    for name in modules
                },
            },
        )

    def admission_allowed(self):
        return True

    def entry_block(self):
        return self.opening.entry_block(self.ledger.ledger_strategy) if self.opening else None

    def account_cash(self):
        owner = self.account_owner or self
        if not owner.account_current():
            return 0
        cash = min(decimal(owner.account[k]) for k in ("official_balance_usd", "applicable_allowance_usd"))
        return self.opening.strategy_cash(self.ledger.ledger_strategy, cash, self.elapsed()) if self.opening else cash

    def account_current(self):
        return self.account_owner.account_current() if self.account_owner else super().account_current()

    def spendable(self):
        return max(0, self.account_cash() - self.ledger.committed())

    def lifetime_block(self, opportunity, root):
        if not self.ledger.signal_limited:
            return None
        if (root["pricing_origin"]["boot_id"] != BOOT_ID
                or self.e.monotonic() >= opportunity.order_deadline):
            return "M6_SIGNAL_DEADLINE"
        return self.feed.m1.active_reason((opportunity.episode_id,))

    def expire_signal(self, opportunity, reason):
        key = opportunity.episode_id
        if self.ledger.filled_or_matched(key):
            self.pending.pop(key, None)
            return
        root = self.ledger.root_plan(key)
        wait = self.ledger.get("m6_execution_wait:" + key)
        if wait and wait["reason"] == POST_PRICE_OUTSIDE_BAND and not (
                self.ledger.attempts(key) or self.ledger.get("post_price_band_eligible:" + key)):
            reason = POST_PRICE_OUTSIDE_BAND
        elif wait and wait["reason"] == "M6_NO_LEGAL_FAK_QUANTITY":
            reason = wait["reason"]
        if not self.ledger.row(key)["order_id"] and not self.ledger.unresolved(key):
            self.ledger.finish(key, "CUTOFF", reason)
            self.e.record("sm_m6_signal_finished", {"opportunity_id": key, "reason": reason,
                                                   "wait": wait, "pricing_origin": root["pricing_origin"]})
            self.pending.pop(key, None)

    def verified_sdk_tick(self, token_id):
        """Read the same officially verified tick as the final POST guard.

        This is called inside the SDK signing worker. No new HTTP call, local
        liquidity/freshness gate, or mutation of the SDK's private cache.
        """
        with self.feed.lock:
            if token_id not in {t.token_id for t in self.e.meta.tokens}:
                raise ValueError("M4_TAKER_TICK_TOKEN_FOREIGN")
            book = self.feed.books.get(token_id)
            if (book is None or book.tick <= 0
                    or self.feed.tick_verified.get(token_id) != book.tick):
                raise ValueError("M4_TAKER_TICK_UNVERIFIED")
            return format(book.tick.normalize(), "f")

    def collect(self):
        if (self.e.fatal or self.ledger.get("buy_closed") or not self.entry_window_open()
                or self.e.inhibited() or self.stop.is_set()):
            return
        with self.feed.lock:
            for _ in range(2):
                if not self.admission_allowed():
                    break
                self.feed.m1.evaluate(self.feed.snapshot())
                opportunity = self.feed.m1.selected
                if opportunity is None:
                    break
                # Claim while valid, before synchronous durability work. Once claimed,
                # expiration of the detector evidence cannot orphan this durable intent.
                if not self.feed.m1.take(opportunity):
                    break
                row = None
                try:
                    row = self.ledger.observe(
                        opportunity, minimum=self.e.meta.min_order_size, elapsed=self.elapsed()
                    )
                    if row is None:
                        continue
                    self.ledger.plan_signal(
                        row["id"],
                        self.feed.books[opportunity.token_id].tick,
                        self.e.meta.min_order_size,
                        **({"pricing_origin": origin(opportunity.signal_start_mono,
                            self.e.monotonic(), self.elapsed(),
                            deadline=opportunity.order_deadline if self.ledger.signal_limited else None)}
                           if self.ledger.dynamic_pricing else {}),
                    )
                    if reason := self.entry_block():
                        self.ledger.finish(row["id"], "SKIPPED", reason)
                        self.e.record("sm_m4_live_signal", self.ledger.row(row["id"]))
                        continue
                    # An invalid initial cap is terminal under the new policy,
                    # before account/network readiness can defer a first attempt.
                    now, elapsed = self.e.monotonic(), self.elapsed()
                    root = self.ledger.root_plan(row["id"])
                    if not self.reject_initial_price_band(opportunity, now,
                            signal_age(root, now, elapsed) if self.ledger.dynamic_pricing else 0, elapsed):
                        self.pending[row["id"]] = opportunity
                    self.e.record("sm_m4_live_signal", self.ledger.row(row["id"]))
                except ValueError as exc:
                    if str(exc) not in {
                        "M4_TAKER_MINIMUM_EXCEEDS_ALL_IN_BUDGET",
                        "M4_TAKER_ANCHOR_PLUS_TICK_INVALID",
                        "M4_PRICE_OUTSIDE_BAND",
                    }:
                        self.e.fatal = "M4_INTENT_DURABILITY_FAILED"
                        raise
                    if row:
                        self.ledger.finish(row["id"], "SKIPPED", str(exc))
                    self.e.record(
                        "sm_m4_candidate_skipped",
                        {"opportunity": asdict(opportunity), "reason": str(exc)},
                    )
                except Exception:
                    self.e.fatal = "M4_INTENT_DURABILITY_FAILED"
                    raise

    def guard(self, opportunity, quote=None, *, native_order_id=None):
        e, key = self.e, opportunity.episode_id
        with self.feed.lock:
            if self.ledger.price_band_abandonment(key):
                self.guard_reason = self.ledger.price_band_stop_reason
                self.guard_results[key] = (self.guard_reason, 0)
                return False
            row, plan = self.ledger.row(key), self.ledger.plan(key)
            root = self.ledger.root_plan(key)
            book, now = self.feed.books[opportunity.token_id], e.monotonic()
            entry_block = self.entry_block()
            pricing_reason = entry_block or self.lifetime_block(opportunity, root)
            if self.ledger.dynamic_pricing and pricing_reason is None:
                captured_elapsed = self.elapsed()
                age = signal_age(root, now, captured_elapsed)
                if (quote is None and not row["order_id"] and not self.ledger.unresolved(key)
                        and self.entry_window_open()):
                    pricing_reason = self.select_pricing(opportunity, book, now, age, captured_elapsed)
                    plan = {**self.ledger.plan(key), "limit_price": str(self.ledger.selected_price(key))}
                elif (quote is not None and plan.get("pricing", {}).get("stage") == "ANCHOR"
                      and age >= WAIT_US):
                    # Signing/fsync crossed the switch: leave this intent UNSENT,
                    # then capture a fresh ask for a different signed order.
                    pricing_reason = "M4_ANCHOR_WAIT_ENDED_REPRICE"
            queued = list(self.feed.m1_pending_received)
            if self.feed.m1_processing_received is not None:
                queued.append(self.feed.m1_processing_received)
            age = age_us(now, min(queued)) if queued else 0
            price = decimal(plan["limit_price"])
            signed_price = decimal(quote.price) if quote is not None else price
            band_allowed = self.ledger.post_price_allowed(signed_price, root)
            if not band_allowed and pricing_reason != POST_PRICE_OUTSIDE_BAND:
                self.ledger.record_price_band_wait(key, signed_price, plan.get("pricing", {}).get("stage", "ANCHOR"), root)
                self.price_band_waits[key] = {"signed_limit": str(signed_price)}
            health = e.reads.network.snapshot()
            cash = self.account_cash()
            checks = [
                ("M4_STOPPING", not self.stop.is_set() and not e.closed and not e.inhibited()),
                (e.fatal or "M4_FATAL_EXECUTION_STATE", e.fatal is None),
                ("M4_JOURNAL_UNAVAILABLE", e.journal.ready),
                (pricing_reason, pricing_reason is None),
                (
                    "M4_UNRESOLVED_PRIOR_ATTEMPT",
                    self.ledger.native_ready(key, native_order_id) if native_order_id else (
                        not row["started"]
                        and row["state"] in {"OBSERVED", "RESERVED", "PREPARED"}
                        and not self.ledger.unresolved(key) and not self.ledger.filled_or_matched(key)
                    ),
                ),
                (self.account_reason or "M4_ACCOUNT_SNAPSHOT_STALE", self.account_current()),
                ("M4_ACCOUNT_BUDGET", self.ledger.committed() <= cash),
                (POST_PRICE_OUTSIDE_BAND,
                 band_allowed),
                ("M4_ENTRY_WINDOW_ENDED", self.entry_window_open()
                 and not self.ledger.get("buy_closed") and not self.ledger.get("complete")),
                (
                    "M4_POST_LATENCY_BUDGET",
                    quote is None or self.ledger.signal_limited or self.initial_post_budget_available(opportunity),
                ),
                # This task was durably admitted from a valid signal. A FAK's
                # immutable signed cap protects its execution price even when
                # local depth is queued/stale or currently shows no liquidity.
                # Do not lose the first attempt by imposing Maker freshness
                # gates again after signing/fsync; let the exchange match it.
                ("M4_TAKER_TICK_UNVERIFIED",
                 self.feed.tick_verified.get(opportunity.token_id) == book.tick),
                ("M6_BEST_ASK_CHANGED_OR_STALE", not self.ledger.signal_limited or quote is None
                 or (self.feed.m1.legal(book, now) and book.best_ask == signed_price)),
                # Frozen cap stays fixed; a verified finer tick must not disable it.
                (
                    "M4_TAKER_CAP_OFF_CURRENT_TICK",
                    book.tick > 0
                    and price % book.tick == 0
                    and book.tick <= price <= 1 - book.tick,
                ),
                ("M4_PREPARED_PRICE_CHANGED", quote is None or decimal(quote.price) == price),
                ("M4_FAK_BUY_MINIMUM", quote is None
                 or minimum_fak_buy_ok(price, decimal(quote.size))),
                (
                    "M4_TASK_BUDGET",
                    quote is None
                    or self.ledger.budget_used(key) + all_in_cost(price, quote.size, plan["fees"])
                    <= self.ledger.order_budget,
                ),
                ("M4_SESSION_BUDGET", self.ledger.committed() <= self.ledger.limit),
                (health.reason, health.allowed),
            ]
            self.guard_reason = next((r for r, ok in checks if not ok), None)
            self.pending_input_age_ms = age / 1000
            self.guard_results[key] = (self.guard_reason, self.pending_input_age_ms)
            return self.guard_reason is None

    def reject_initial_price_band(self, opportunity, now, age, elapsed):
        key = opportunity.episode_id
        root = self.ledger.root_plan(key)
        if root.get("post_price_band_retry_policy") != ABANDON_FIRST_SIGNED_BAND:
            return False
        price = decimal(root["limit_price"])
        if self.ledger.post_price_allowed(price, root):
            return False
        if not self.ledger.price_band_abandonment(key):
            captured = self.pricing_capture(key, opportunity.token_id, None, now, age, elapsed, "INITIAL", price)
            proof = self.ledger.abandon_price_band(key, captured)
            self.e.record("sm_m4_signal_price_band_abandoned", proof)
        return True

    def select_pricing(self, opportunity, book, now, age, elapsed):
        key = opportunity.episode_id
        root = self.ledger.root_plan(key)
        # Also cover a restored task whose first check was interrupted/delayed.
        # Never substitute a now-legal ask for its rejected original cap.
        if self.reject_initial_price_band(opportunity, now, age, elapsed):
            return POST_PRICE_OUTSIDE_BAND
        stage = "BEST_ASK" if self.ledger.signal_limited or age >= WAIT_US else "ANCHOR"
        if age < 0:
            return "M4_SIGNAL_CLOCK_INVALID"
        if stage == "BEST_ASK" and not (
            book.token_id == opportunity.token_id and self.feed.m1.legal(book, now)
            and book.best_ask % book.tick == 0
            and book.tick <= book.best_ask <= 1 - book.tick
        ):
            return "M4_BEST_ASK_REPRICE_WAIT_FRESH_BOOK"
        price = decimal(book.best_ask if stage == "BEST_ASK" else root["limit_price"])
        if not self.ledger.post_price_allowed(price, root):
            self.ledger.record_price_band_wait(key, price, stage, root)
            self.price_band_waits[key] = {"signed_limit": str(price), "stage": stage,
                                         "signal_age_us": age}
            if self.ledger.signal_limited:
                self.remember_signal_wait(key, POST_PRICE_OUTSIDE_BAND, price, book.tick)
            if (stage == "BEST_ASK" and root.get("post_price_band_retry_policy")
                    in {ABANDON_BEST_ASK_BAND, ABANDON_FIRST_SIGNED_BAND}):
                captured = self.pricing_capture(key, opportunity.token_id, book, now, age, elapsed, stage, price)
                if not rejected_price_band_valid(root, captured, opportunity.token_id):
                    return "M4_BEST_ASK_REPRICE_WAIT_FRESH_BOOK"
                proof = self.ledger.abandon_price_band(key, captured)
                self.e.record("sm_m4_signal_price_band_abandoned", proof)
            return POST_PRICE_OUTSIDE_BAND
        qty = budget_quantity(price, self.ledger.order_budget - self.ledger.budget_used(key),
                              self.ledger.fees)
        if self.ledger.signal_limited:
            while qty > 0 and (gross_risk_cost(price, qty, self.ledger.fees) > self.ledger.order_budget
                               or qty * price % decimal(".01")):
                qty -= decimal(".1")
            if not minimum_fak_buy_ok(price, qty):
                self.remember_signal_wait(key, "M6_NO_LEGAL_FAK_QUANTITY", price, book.tick)
                return "M6_NO_LEGAL_FAK_QUANTITY"
        if not minimum_fak_buy_ok(price, qty):
            # A restored pre-fix RESERVED row may still hold a confirmed cash
            # remainder below the exchange floor. Reclassify it without saving
            # a quote; pending/UNKNOWN budget protections remain authoritative.
            with self.ledger.lock, self.ledger.db:
                self.ledger.refresh_totals(key)
            # Keep a potentially executable intent, but no durable price fact
            # is needed until there is a legal order to prepare at that ask.
            return "M4_REMAINDER_WAIT_LEGAL_REPRICE"
        q = self.pricing_capture(key, opportunity.token_id, book, now, age, elapsed, stage, price)
        # Only selecting a new price requires a fresh book. Signing, fsync and
        # native POST then use this immutable cap, regardless of queue backlog.
        if not quote_valid(root, {**root, "pricing": q, "limit_price": q["limit_price"]},
                           opportunity.token_id, q):
            return "M4_BEST_ASK_REPRICE_WAIT_FRESH_BOOK"
        self.ledger.select_pricing(key, q)
        if self.ledger.signal_limited and self.ledger.get("m6_execution_wait:" + key):
            self.ledger.set("m6_execution_wait:" + key, None)
        self.e.record("sm_m4_pricing_selected", q)
        return None

    def remember_signal_wait(self, key, reason, price, tick):
        name = "m6_execution_wait:" + key
        item = {"opportunity_id": key, "reason": reason, "signed_limit": str(price), "tick": str(tick)}
        if item != self.ledger.get(name):
            self.ledger.set(name, item)
            self.e.record("sm_m6_execution_wait", item)

    def pricing_capture(self, key, token, book, now, age, elapsed, stage, price):
        q = {"id": uuid4().hex, "opportunity_id": key, "token_id": token,
             "stage": stage, "signal_age_us": age, "captured_monotonic": now,
             "captured_elapsed_s": elapsed, "boot_id": BOOT_ID,
             "limit_price": str(price)}
        if stage == "BEST_ASK":
            q.update(tick=str(book.tick), best_bid=str(book.best_bid), best_ask=str(book.best_ask),
                     connected=self.feed.connected, generation=self.feed.generation,
                     snapshot_generation=book.snapshot_generation,
                     verified_tick=str(self.feed.tick_verified.get(book.token_id)),
                     received_monotonic=book.received_mono, captured_wall=self.feed.wall(),
                     source_wall=book.source_time, reported_prices_agree=True)
        return q

    def guard_failure(self, key):
        return self.guard_results.get(key, (None, 0))[0]

    def native_guard(self, attempt):
        # This is called after FULL-sync post_start, immediately before transport.
        # Revalidate all admission conditions; exempt only this exact local intent
        # from UNKNOWN, never an earlier order or a reconciliation conflict.
        if attempt is None or not attempt.post_started:
            return "M4_NATIVE_INTENT_MISSING"
        key, oid = attempt.quote.window_id, attempt.expected_order_id
        with self.feed.lock, self.ledger.lock:
            opportunity = self.restored_opportunity(self.ledger.row(key))
            if not self.guard(opportunity, attempt.quote, native_order_id=oid):
                return self.guard_failure(key)
            # No disk writes follow these final time/stop checks.
            if self.stop.is_set() or self.e.closed or self.e.inhibited():
                return "M4_STOPPING"
            if self.e.fatal:
                return self.e.fatal
            if not self.entry_window_open():
                return "M4_ENTRY_WINDOW_ENDED"
            if self.ledger.signal_limited and self.e.monotonic() >= opportunity.order_deadline:
                return "M6_SIGNAL_DEADLINE"
        return None

    def entry_retry_delay(self, key):
        sent = [a for a in self.ledger.attempts(key) if a["started"]]
        if sent and (decimal(sent[-1]["filled"]) > 0 or sent[-1]["state"] == "SETTLING"):
            return 0.01
        zeros = 0
        for a in reversed(sent):
            if a["state"] != "TERMINAL" or decimal(a["filled"]) != 0:
                break
            zeros += 1
        return min(
            self.settings.retry_max_sec,
            self.settings.retry_initial_sec * 2 ** min(10, max(0, zeros - 1)),
        )

    def schedule(self, opportunity):
        row = self.ledger.row(opportunity.episode_id)
        if self.ledger.price_band_abandonment(row["id"]):
            self.pending.pop(row["id"], None)
            return
        if (self.e.fatal or self.ledger.get("buy_closed")
                or row["state"] in TERMINAL_TASKS or self.stop.is_set() or self.e.inhibited()):
            return
        if self.ledger.signal_limited and not row["order_id"] and not self.ledger.unresolved(row["id"]):
            if reason := self.lifetime_block(opportunity, self.ledger.root_plan(row["id"])):
                self.expire_signal(opportunity, reason)
                return
        if not row["order_id"] and not self.entry_window_open():
            self.ledger.finish(row["id"], "CUTOFF", "M4_T240_RESIDUAL")
            return
        self.pending[row["id"]] = opportunity
        # Queries use their own pacing. Known residuals are not delayed by a read backoff.
        delay = self.entry_retry_delay(row["id"])
        reason, input_age = self.guard_results.get(row["id"], (None, 0))
        if self.ledger.signal_limited and reason in {
                "M6_NO_LEGAL_FAK_QUANTITY", POST_PRICE_OUTSIDE_BAND, "M4_BEST_ASK_REPRICE_WAIT_FRESH_BOOK"}:
            # Quote changes can restore eligibility within the original subsecond window.
            delay = .01
        elif reason in {"M4_REMAINDER_WAIT_LEGAL_REPRICE", POST_PRICE_OUTSIDE_BAND}:
            delay = max(delay, 0.5)
        elif reason == "M4_BEST_ASK_REPRICE_WAIT_FRESH_BOOK":
            delay = max(delay, self.settings.retry_initial_sec)
        self.retry_after[row["id"]] = self.e.monotonic() + delay
        self.retry_wake_after[row["id"]] = self.e.monotonic() + self.settings.retry_initial_sec
        self.retry_book_states[row["id"]] = self.retry_book_state(opportunity)
        self.retry_recovered.discard(row["id"])
        if reason:
            self.guard_counts[reason] = self.guard_counts.get(reason, 0) + 1
            if (
                self.e.monotonic() - self.last_guard_log.get((row["id"], reason), -float("inf"))
                >= 1
            ):
                self.last_guard_log[row["id"], reason] = self.e.monotonic()
                self.e.record(
                    "sm_m4_execution_wait",
                    {
                        "opportunity_id": row["id"],
                        "reason": reason,
                        "pending_input_age_ms": input_age,
                        "count": self.guard_counts[reason],
                        **({"post_price_band": self.ledger.root_plan(row["id"]).get("post_price_band"),
                            **self.price_band_waits.get(row["id"], {})}
                           if reason == POST_PRICE_OUTSIDE_BAND else {}),
                    },
                )

    def retry_book_state(self, opportunity):
        with self.feed.lock:
            book = self.feed.books[opportunity.token_id]
            cap = self.ledger.selected_price(opportunity.episode_id)
            usable = (self.feed.m1.legal(book, self.e.monotonic())
                      and book.best_ask is not None and book.best_ask <= cap)
            return usable, book.best_ask, book.best_ask_size

    def entry_retry_ready(self, row, now):
        key = row["id"]
        if self.ledger.price_band_abandonment(key):
            return False
        if (self.ledger.dynamic_pricing and not self.ledger.signal_limited
                and not row["order_id"] and not self.ledger.unresolved(key)):
            selected = self.ledger.get("pricing_selection:" + key)
            if (key not in self.pricing_due_woken
                    and (selected is None or selected["stage"] == "ANCHOR")
                    and signal_age(self.ledger.root_plan(key), now, self.elapsed()) >= WAIT_US):
                self.pricing_due_woken.add(key)
                return True
        if self.retry_after.get(key, 0) <= now:
            return True
        sent = [a for a in self.ledger.attempts(key) if a["started"]]
        if not sent:
            return False
        last = sent[-1]
        proof = json.loads(last["proof"]) if last["proof"] else {}
        # Only a proven zero liquidity result may shorten its retry delay.
        # HTTP 425/429, transport errors and unresolved attempts retain theirs.
        if not (last["state"] == "TERMINAL" and decimal(last["filled"]) == 0 and (
            proof.get("kind") in {"EXACT_FAK_KILLED", "FAK_NO_MATCH"}
            or (proof.get("kind") == "OWNED_TERMINAL_ORDER_AND_TRADES"
                and decimal(proof["order"]["size_matched"]) == 0)
        )):
            return False
        opportunity = self.pending[key]
        current = self.retry_book_state(opportunity)
        previous = self.retry_book_states.get(key)
        self.retry_book_states[key] = current
        if not current[0]:
            self.retry_recovered.discard(key)
        elif previous and (not previous[0] or current[1] < previous[1]
                           or (current[1] == previous[1] and current[2] > previous[2])):
            self.retry_recovered.add(key)
        if (key in self.retry_recovered and now >= self.retry_wake_after.get(key, now)
                and self.guard(opportunity)):
            self.retry_recovered.discard(key)
            self.retry_after[key] = now
            self.e.record("sm_m4_liquidity_retry_wakeup", {"opportunity_id": key,
                          "previous_order_id": last["order_id"], "elapsed_s": self.elapsed()})
            return True
        return False

    async def reconcile(self, oid):
        await reconcile_owned(self, oid)

    async def submit(self, opportunity):
        key = opportunity.episode_id
        async with self.task_locks.setdefault(key, asyncio.Lock()), self.post_slots:
            await self._submit(opportunity)

    async def _submit(self, opportunity):
        # A successful exchange match can become queryable before its POST response
        # returns. Finish recording that response before querying this new attempt;
        # otherwise two writers could race to finalize the same attempt.
        self.posting_keys.add(opportunity.episode_id)
        try:
            await super()._submit(opportunity)
        finally:
            self.posting_keys.discard(opportunity.episode_id)

    async def read_order(self, oid):
        key = self.ledger.attempt(oid)["task_id"]
        was_ready = (self.ledger.row(key)["state"] == "RESERVED"
                     and not self.ledger.unresolved(key))
        await self.reconcile(oid)
        a = self.ledger.attempt(oid)
        blocked = a["state"] == "UNKNOWN" or bool(self.ledger.get("blocked:" + oid))
        self.read_failures[oid] = self.read_failures.get(oid, 0) + 1 if blocked else 0
        self.read_after[oid] = self.e.monotonic() + min(
            2, 0.1 * 2 ** min(5, self.read_failures[oid])
        )
        row = self.ledger.row(a["task_id"])
        # Repeated reads of an older SETTLING order must not keep pushing a
        # ready residual's retry deadline into the future. Schedule on recovery
        # or when the task is missing from the entry queue, not on every poll.
        if row["state"] == "RESERVED" and (not was_ready or key not in self.pending):
            self.schedule(self.restored_opportunity(row))

    async def advance_entries(self, now):
        if self.e.fatal or self.ledger.get("buy_closed"):
            return
        if (
            self.public_api is not None
            and self.tick_task is None
            and now - self.last_tick_refresh >= 0.5
            and self.entry_window_open()
            and not self.stop.is_set()
            and not self.e.inhibited()
        ):
            with self.feed.lock:
                tokens = tuple(
                    t
                    for t, b in self.feed.books.items()
                    if self.feed.tick_verified.get(t) != b.tick
                )
            if tokens:
                self.last_tick_refresh = now
                self.tick_task = asyncio.create_task(self.refresh_ticks(tokens))
        self.collect()
        if self.ledger.signal_limited:
            for key, opportunity in tuple(self.pending.items()):
                row = self.ledger.row(key)
                if row["state"] in TERMINAL_TASKS:
                    self.pending.pop(key)
                elif not row["order_id"] and not self.ledger.unresolved(key):
                    if reason := self.lifetime_block(opportunity, self.ledger.root_plan(key)):
                        self.expire_signal(opportunity, reason)
        ready = [
            r
            for r in self.ledger.rows()
            if r["id"] in self.pending
            and r["state"] in {"OBSERVED", "RESERVED"}
            and not r["order_id"]
            and not self.ledger.unresolved(r["id"])
            and r["id"] not in self.post_jobs
            and r["id"] not in self.posting_keys
            and self.entry_retry_ready(r, now)
        ]
        if self.account_current():
            ready.sort(key=lambda r: (bool(r["posts"]), self.retry_after.get(r["id"], 0)))
            for row in ready[:max(0, 2 - len(self.post_jobs))]:
                key = row["id"]
                self.post_jobs[key] = asyncio.create_task(self.submit(self.pending.pop(key)))

    async def advance(self):
        try:
            await self._advance()
        except Exception:
            # Close the buy gate before run() drains any other scheduled workers.
            self.e.fatal = self.e.fatal or "M4_MARKET_RUNTIME_FAILED"
            raise

    async def _advance(self):
        for key, job in list(self.post_jobs.items()):
            if job.done():
                await job
                del self.post_jobs[key]
        for name in ("account_task", "tick_task", "archive_task"):
            job = getattr(self, name)
            if job is not None and job.done():
                await job
                setattr(self, name, None)
        for oid, job in list(self.read_jobs.items()):
            if job.done():
                await job
                del self.read_jobs[oid]
        if self.stop.is_set() or self.e.inhibited():
            return
        now = self.e.monotonic()
        if self.opening and self.account_owner is None and (
                self.opening.epoch + self.elapsed() >= getattr(self, "_next_pool_release", 0)):
            self.opening.pool.release(self.opening.funder, self.opening.epoch + self.elapsed())
            self._next_pool_release = min(
                (p["release_at"] for p in self.opening.payload["older"]
                 if p["release_at"] > self.opening.epoch + self.elapsed()), default=float("inf"))
        # Cash refresh also runs independently from potentially slow owned-order reads.
        if self.account_owner is not None:
            self.account, self.account_at = self.account_owner.account, self.account_owner.account_at
            self.account_reason = self.account_owner.account_reason
            self.account_read_ms = self.account_owner.account_read_ms
        if (self.account_owner is None and self.account_task is None
                and now - self.last_account >= self.settings.account_poll_sec):
            self.last_account = now
            self.account_task = asyncio.create_task(self.refresh_account())
        if self.e.fatal:
            self.ledger.set("market_halt_reason", self.e.fatal)
            self.ledger.set("buy_closed", True)
        elif self.elapsed() < self.settings.cutoff_sec:
            await self.advance_entries(now)
        else:
            self.ledger.set("buy_closed", True)
            await self.recycle_after_cutoff()
        attempts = self.ledger.attempts()
        offset = self.read_cursor % max(1, len(attempts))
        for a in attempts[offset:] + attempts[:offset]:
            if len(self.read_jobs) >= 2:
                break
            if (
                a["started"]
                and a["state"] not in {"TERMINAL", "UNSENT"}
                and not (a["task_id"] in self.posting_keys and a["state"] in {"ACK", "UNKNOWN"})
                and a["order_id"] not in self.read_jobs
                and now >= self.read_after.get(a["order_id"], 0)
            ):
                self.read_jobs[a["order_id"]] = asyncio.create_task(self.read_order(a["order_id"]))
                self.read_cursor = (attempts.index(a) + 1) % len(attempts)
        if self.account_owner is None and self.archive_task is None and now - self.last_archive >= 5:
            self.last_archive = now
            self.archive_task = asyncio.create_task(self.reconcile_archive())

    async def reconcile_archive(self):
        """Resume unresolved ended markets with a separate strictly read-only client."""
        paths = sorted(self.ledger.path.parent.glob("*.sqlite3"))
        if self.ledger.ledger_strategy == M4_LEDGER:
            paths += sorted((self.ledger.path.parent.parent / "m6-ledgers").glob("*.sqlite3"))
        if not paths:
            return
        paths = paths[self.archive_cursor :] + paths[: self.archive_cursor]
        for path in paths:
            self.archive_cursor = (self.archive_cursor + 1) % len(paths)
            if (
                path == self.ledger.path
                or path.is_symlink()
                or self.stop.is_set()
                or self.e.inhibited()
            ):
                continue
            try:
                saved = BudgetLedger.metadata(path, strategy=M6_LEDGER if path.parent.name == "m6-ledgers" else M4_LEDGER)
                ended = int(
                    saved.get("market", {}).get("slug", "").rsplit("-", 1)[-1]
                ) + 300 <= int(self.e.meta.slug.rsplit("-", 1)[-1])
            except Exception as exc:
                self.e.record(
                    "sm_m4_archive_reconcile_wait", {"ledger": str(path), "reason": str(exc)}
                )
                continue
            identity = saved.get("identity", {})
            if (
                not (saved.get("complete") or ended)
                or (
                    saved.get("complete") and saved.get("owned_orders_need_reconciliation") is False
                )
                or identity.get("mode") != "LIVE"
                or identity.get("funder") != self.ledger.get("identity")["funder"]
                or saved.get("settings", {}).get("execution_revision") != BudgetLedger.revision
            ):
                continue
            ledger = None
            try:
                settings = M4LiveSettings.model_validate(saved["settings"])
                # The constructor recreates and validates limit/budget identity fields.
                ident = {
                    k: identity[k]
                    for k in ("strategy", "mode", "funder", "market_id", "settings_hash")
                }
                ledger = BudgetLedger(path, ident, settings=settings, fees=saved["taker_fees"])
                ledger.set("buy_closed", True)
                raw = saved["market"]
                meta = MarketMeta(**{**raw, "tokens": tuple(TokenMeta(**t) for t in raw["tokens"])})
                client = copy.copy(self.e.gateway._client)
                # Historical recovery must not feed failures/backoff into the
                # current market's admission circuit or share mutable read state.
                client.network = self.archive_network
                client.operation_local = local()
                client.known_ids = set()
                client.m4_taker_ids = {}
                client.observed_open_rows = {}
                client.observed_trade_legs = {}
                client._pagination_failures = {}
                client.m4_read_only = True
                client.m4_ledger = ledger
                client.session_market_id = meta.condition_id
                client.m4_execution = settings.execution
                gateway = SimpleNamespace(
                    _client=client, _io=self.e.gateway._io, funder=self.e.gateway.funder
                )
                context = SimpleNamespace(
                    ledger=ledger,
                    e=SimpleNamespace(gateway=gateway, meta=meta, record=self.e.record, fatal=None),
                )
                orders = [
                    a
                    for a in ledger.attempts()
                    if a["started"] and a["state"] not in {"TERMINAL", "UNSENT"}
                ]
                offset = int(ledger.get("owned_reconcile_cursor", 0)) % max(1, len(orders))
                rotated = orders[offset:] + orders[:offset]
                for a in rotated[:2]:
                    if self.stop.is_set() or self.e.inhibited():
                        break
                    await reconcile_owned(context, a["order_id"])
                ledger.set("owned_reconcile_cursor", (offset + 2) % max(1, len(orders)))
                ledger.set(
                    "owned_orders_need_reconciliation",
                    any(
                        a["started"] and a["state"] not in {"TERMINAL", "UNSENT"}
                        for a in ledger.attempts()
                    ),
                )
                if not ledger.get("owned_orders_need_reconciliation"):
                    ledger.set("complete", True)
            except Exception as exc:
                self.e.record(
                    "sm_m4_archive_reconcile_wait", {"ledger": str(path), "reason": str(exc)}
                )
            finally:
                try:
                    for kind, payload, wall in self.archive_network.drain_events():
                        self.e.record("sm_m4_archive_network", {
                            "ledger": str(path), "kind": kind, "payload": payload,
                        }, event_wall=wall)
                finally:
                    if ledger is not None:
                        ledger.close()
            break  # At most one ended market per scheduling cycle.

    def finish_pending(self):
        if self.elapsed() >= self.settings.cutoff_sec:
            self.ledger.set("buy_closed", True)
        super().finish_pending()

    async def recycle_after_cutoff(self):
        if self.post_jobs or self.posting_keys:
            return
        await super().recycle_after_cutoff()

    async def drain(self):
        # Never cancel native worker calls, including on manual shutdown.
        jobs = (list(self.post_jobs.values()) + list(self.read_jobs.values())
                + ([self.archive_task] if self.archive_task else []))
        if jobs:
            results = await asyncio.gather(*jobs, return_exceptions=True)
        else:
            results = []
        self.post_jobs.clear()
        self.read_jobs.clear()
        self.archive_task = None
        await super().drain()
        for result in results:
            if isinstance(result, BaseException):
                raise result
