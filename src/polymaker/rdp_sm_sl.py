"""Signal-driven SL coordination and reusable owned order I/O primitives.

The coordinator serializes local reservation/target changes; POST, DELETE and
targeted reads have independent tasks. It never runs the session close barrier
while a replacement is active. TP reuses its I/O with a separate fixed-price
coordinator; only the SL run loop permits replacement overlap.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import suppress
from dataclasses import asdict
from decimal import ROUND_FLOOR, Decimal

from polymaker.domain import Quote, Side
from polymaker.rdp_sm_execution import (
    Placement,
    WindowQuote,
    _canceled_submission_response,
    _terminal,
)
from polymaker.rdp_sm_recovery import retry_read
from polymaker.rdp_sm_sl_ledger import SMALL, exposure
from polymaker.rdp_sm_unknown_recovery import UnknownRepairRecovery
from polymaker.userstream.parse import normalize_order, parse_trade

ZERO = Decimal(0)


class SLExecution:
    mode = "SL"

    def __init__(self, controller, window_id, *, inherited=(), parent_done=None, reads_ready=None):
        self.controller = controller
        self.e = controller.executor
        self.lifecycle = controller.lifecycle
        self.window_id = window_id
        self.started_wall, self.started_monotonic = time.time(), self.e.monotonic()
        self.inherited = list(inherited)
        self.parent_done = parent_done
        self.reads_ready = reads_ready
        self.group = self.e.reads.network.snapshot().active_batch or window_id
        self.orders = list(inherited)
        self.created = []
        self.posts = {}
        self.cancels = {}
        self.cancel_ack = {}
        self.cancel_after = {}
        self.cancel_started = {}
        self.cancel_completed = {}
        self.cancel_revision = {}
        self.cancel_wanted = set()
        self.post_returned = {}
        self.post_started = {}
        self.signal_times = {}
        self.proof_after = {}
        self.rejected_target = (getattr(controller, self.mode.lower()+"_rejected_target", None)
            if self.e.monotonic() < getattr(controller, self.mode.lower()+"_retry_after", 0) else None)
        self.latest = None
        self.last_plan = None
        self.guard_failures = {}
        self.closed = False
        self.reason = None
        self.last_progress = self.e.monotonic()
        self.last_wait = None
        self.cutoff_logged = False
        self.changed = asyncio.Event()
        self.e.sl_terminal_quantities = getattr(self.e, "sl_terminal_quantities", {})
        self.e.sl_order_ids = getattr(self.e, "sl_order_ids", set())
        ep = self.e.repair_episode
        if ep is not None:
            chain = ep.setdefault("chain_windows", list(ep.get("windows", [])))
            chain.extend(w for w in ep.get("windows", []) if w not in chain)
        ledger = exposure(self.e)
        account = self.lifecycle.account or {}
        cash = Decimal(str(account.get("basic_spendable_budget_usd", 0)))
        own_reserved = min(ledger.reserved, Decimal(str(account.get("execution_reserved_usd", 0))))
        self.capital = min(self.e.capital_limit, ledger.spent+own_reserved+cash,
                           self.e.starting_collateral if self.e.starting_collateral is not None
                           else ledger.spent+own_reserved+cash)
        self.recovery = UnknownRepairRecovery(self)

    def health(self):
        return self.recovery.health()

    async def wait_changed(self):
        # Keep the existing market/safety polling ceiling; completed commands
        # and published proofs can wake the coordinator sooner.
        with suppress(TimeoutError):
            await asyncio.wait_for(self.changed.wait(), .005)

    def elapsed(self):
        return self.controller.elapsed()

    def record(self, event, payload):
        self.e.record(f"sm_{self.mode.lower()}_{event}", payload)

    def on_accepted(self, attempt):
        """Mode-specific acceptance policy; shared I/O never changes SL pricing."""

    @staticmethod
    def consistent(snapshot):
        total = (snapshot.yes.best_bid+snapshot.yes.best_ask
                 +snapshot.no.best_bid+snapshot.no.best_ask)/2
        return abs(total-1) <= Decimal(".02")

    @staticmethod
    def oid(a):
        return a.order_id or a.expected_order_id

    def post_done(self, a):
        if a in self.inherited:
            return self.parent_done is None or self.parent_done.is_set()
        task = self.posts.get(a.attempt_id)
        return task is not None and task.done()

    def register(self, a):
        oid = self.oid(a)
        if not oid or not a.post_started or a.outcome in {"REJECTED", "ABORTED"}:
            return
        self.e.known.add(oid)
        a.order_id = oid
        if self.e.processor.owned_record(oid) is None:
            self.e.processor.register_order(oid, self.e.meta.condition_id, a.quote.token_id,
                Side.BUY, a.quote.size, a.quote.price, "rdp_sm_"+self.mode.lower(), quiet=True)

    def unresolved(self, a):
        if a.outcome in {"REJECTED", "ABORTED"}:
            return False
        oid = self.oid(a)
        return not (self.post_done(a) and (oid in self.e.sl_terminal_quantities
                    or oid in self.e.proofs and oid in self.e.terminal))

    def wait(self, reason, goal=None):
        key = (reason, goal[:3] if goal is not None else None)
        if key != self.last_wait:
            self.record("wait", {"reason": reason, "window_id": self.window_id,
                                          "target": ({"side": goal[0], "price": str(goal[1]),
                                                      "quantity": str(goal[2]),
                                                      "generation": goal[3].generation}
                                                     if goal is not None else None),
                                          **self.evidence()})
            self.last_wait = key

    def evidence(self):
        s = exposure(self.e)
        return {"actual_delta": str(s.actual), "execution_delta": str(s.execution),
                "delta_interval": [str(x) for x in s.interval],
                "spent_usdc": str(s.spent), "reserved_usdc": str(s.reserved),
                "capital_limit_usdc": str(self.capital), "elapsed_s": self.elapsed()}

    def goal(self):
        e = self.e
        if not e.cfg.rdp_sm.repair.path.exit_enabled(self.mode):
            return None, self.mode+"_DISABLED"
        state = exposure(e)
        side = e.repair_episode["side"] if e.repair_episode else None
        if abs(state.actual) <= SMALL:
            return None, "REPAIR_SMALL_EXPOSURE"
        if (state.actual > 0) != (side == "YES"):
            return None, "REPAIR_REVERSE_ACTUAL"
        if state.actual*state.execution <= 0:
            return None, "WAIT_DIRECTION_PROOF"
        if abs(state.execution) <= SMALL:
            return None, "MATCHED_COVERS_REPAIR"
        snapshot = self.lifecycle.snapshot(self.controller, *e.economic_inventory())
        if not snapshot.valid:
            return None, snapshot.reason
        if not self.consistent(snapshot):
            return None, "REPAIR_PROBABILITY_INCONSISTENT"
        outcome = "NO" if state.actual > 0 else "YES"
        book = snapshot.no if outcome == "NO" else snapshot.yes
        price = book.best_ask-book.tick
        if price <= 0 or price >= 1 or price % book.tick:
            return None, "REPAIR_PRICE_OUT_OF_RANGE"
        from polymaker.rdp_sm_execution import maker_buy_size

        qty = maker_buy_size(price, abs(state.execution), Decimal(str(e.meta.min_order_size)))
        if abs(state.actual) > e.cfg.rdp_sm.repair.delta_limit:
            cash = max(ZERO, self.capital-state.spent-state.reserved)
            qty = min(qty, e.cfg.rdp_sm.max_order_size,
                      (cash/price).quantize(Decimal(".01"), rounding=ROUND_FLOOR))
            if qty < maker_buy_size(price, ZERO, Decimal(str(e.meta.min_order_size))):
                return None, "LIMIT_RECOVERY_MINIMUM_NOT_FEASIBLE"
        if qty > e.cfg.rdp_sm.max_order_size:
            return None, "MINIMUM_ORDER_EXCEEDS_MAXIMUM"
        return (outcome, price, qty, snapshot), None

    def allowed(self, goal, *, reserved=None):
        return self.admission_reason(goal, reserved=reserved) is None

    def admission_reason(self, goal, *, reserved=None):
        outcome, price, qty, snapshot = goal
        e = self.e
        if self.closed or e.closed:
            return "SL_EXECUTION_CLOSED"
        if e.check_repair_abandonment():
            return "HIGH_ENTRY_REPAIR_ABANDONED"
        if e.cooldown.active:
            return "COOLDOWN_ACTIVE"
        if not e.cfg.rdp_sm.repair.path.exit_enabled(self.mode):
            return self.mode+"_DISABLED"
        if not 15 <= self.elapsed() < 240:
            return "MARKET_SUBMISSION_CUTOFF"
        if e.fatal:
            return e.fatal
        if e.inventory_mismatch is not None:
            reason = e.inventory_lag.admission_reason(self, goal, reserved)
            if reason:
                return reason
        if not e.journal.ready:
            return "ASYNC_JOURNAL_NOT_READY"
        if e.gateway._client.pagination_failure is not None:
            return e.gateway._client.pagination_failure
        if e.merge_pending:
            return "MERGE_PENDING"
        if e.reason == "CANCELED_SUBMISSION_EVIDENCE_CHANGED":
            return e.reason
        if any(e.processor.has_failed_fill(self.oid(a))
               or e.processor.has_unresolved_fill_key(self.oid(a))
               or self.oid(a) in e.processor.quarantined_orders for a in self.orders if self.oid(a)):
            return "REPAIR_EXECUTION_UNRESOLVED"
        health = self.health()
        if not health.allowed:
            return health.reason
        if not self.recovery.allows(reserved):
            return "UNKNOWN_REPAIR_RECONCILING"
        current = self.lifecycle.snapshot(self.controller, *e.economic_inventory())
        book = current.yes if outcome == "YES" else current.no
        if not current.valid:
            return current.reason
        if not self.consistent(current):
            return "REPAIR_PROBABILITY_INCONSISTENT"
        if current.generation != snapshot.generation:
            return "WS_GENERATION_CHANGED"
        if price != book.best_ask-book.tick:
            return "SL_QUOTE_CHANGED"
        if e.monotonic()-e.time_anchor > e.cfg.risk.ws_stale_halt_s:
            return "OFFICIAL_CLOCK_STALE"
        if reserved is None:
            return exposure(e).admission_reason(outcome, qty, price, self.capital)
        # The candidate is already fully reserved; never add its amount twice.
        state = exposure(e)
        if (abs(state.actual) <= SMALL or abs(state.execution) <= SMALL
                or state.actual*state.execution <= 0
                or (state.actual > 0) != (outcome == "NO")):
            return "SL_EXECUTION_TARGET_CHANGED"
        from dataclasses import replace

        state = replace(state, reserved=state.reserved-qty*price,
                        yes_pending=state.yes_pending-(qty if outcome == "YES" else ZERO),
                        no_pending=state.no_pending-(qty if outcome == "NO" else ZERO))
        return state.admission_reason(outcome, qty, price, self.capital)

    async def submit(self, goal, a):
        e = self.e
        outcome, price, qty, snapshot = goal
        began = e.monotonic()
        prepared_at = persisted_at = None
        registered = False
        try:
            ticks = {snapshot.yes.token_id: float(snapshot.yes.tick),
                     snapshot.no.token_id: float(snapshot.no.tick)}
            signed = await e.gateway.prepare([Quote(a.quote.token_id, Side.BUY,
                                                    float(price), float(qty))], e.meta, ticks)
            prepared_at = e.monotonic()
            a.expected_order_id = signed.order_ids[0]
            if (a.expected_order_id in self.lifecycle.prepared_ids
                    or a.expected_order_id in e.gateway._client.known_ids):
                e.fatal = "PREPARED_ID_ALREADY_USED"
                raise ValueError("PREPARED_ID_ALREADY_USED")
            self.lifecycle.prepared_ids.add(a.expected_order_id)
            e.sl_order_ids.add(a.expected_order_id)
            e.preparation_started = True
            await e.journal.durable("sm_prepared", {"session_id": e.session_id, "payload": {
                "slot_id": a.attempt_id, "window_id": a.quote.window_id, "phase": "REPAIR",
                "candidates": [{"direction": outcome, "order_ids": signed.order_ids,
                    "quotes": [asdict(a.quote)], "body_hash": signed.body_hash}],
                "risk_upper_bound": str(a.maximum), "status": "POSSIBLY_SENT; NOT_AN_ACK"}}, time.time())
            persisted_at = e.monotonic()
            e.gateway._client.known_ids.add(a.expected_order_id)
            e.processor.register_order(a.expected_order_id, e.meta.condition_id, a.quote.token_id,
                Side.BUY, float(qty), float(price), "rdp_sm_"+self.mode.lower(), quiet=True)
            registered = True

            def guard():
                if a.attempt_id in self.cancel_wanted:
                    self.guard_failures[a.attempt_id] = "SUPERSEDED_OR_CANCELED"
                    return False
                reason = self.admission_reason(goal, reserved=a)
                if reason is not None:
                    self.guard_failures[a.attempt_id] = reason
                return reason is None

            def started():
                if e.check_repair_abandonment():
                    raise ValueError("HIGH_ENTRY_REPAIR_ABANDONED")
                network = e.reads.network
                if network.snapshot().active_batch is None and not network.begin_batch(self.group):
                    raise ValueError("NETWORK_ADMISSION_CHANGED")
                if not self.health().allowed or not self.recovery.allows(a):
                    raise ValueError("NETWORK_ADMISSION_CHANGED")
                e.order_post_elapsed[a.expected_order_id] = self.elapsed()
                self.post_started[a.attempt_id] = e.monotonic()
                a.order_id = a.expected_order_id
                e.known.add(a.order_id)

            context = {"attempt": a, "batch_attempts": [a], "network_batch_id": self.group,
                       "on_post_start": started}
            write = e.shared_reads.begin_write("POST", signed.order_ids) if e.shared_reads else None
            raw = None
            try:
                raw = await e.gateway.send_prepared(signed, submit_guard=guard, operation_context=context)
            except Exception as exc:
                a.unknown_recovery_eligible = retry_read(exc)
                self.record("post_error", {"order_id": a.expected_order_id,
                                                   "error": type(exc).__name__})
            finally:
                if write is not None:
                    e.shared_reads.end_write(write)
            a.outcome, order = (e._placement_outcomes(raw, signed.quotes)[0]
                                if a.post_started else ("ABORTED", None))
            self.register(a)
            if a.outcome == "ACK":
                if order.order_id != a.expected_order_id:
                    a.outcome, e.fatal = "UNKNOWN", "SIGNED_ORDER_ID_MISMATCH"
                else:
                    e.store.upsert_order(order)
                    self.on_accepted(a)
            elif a.outcome == "REJECTED":
                if any(f.maker_order_id == a.expected_order_id for f in e.processor.fill_records.values()):
                    a.outcome, e.fatal = "UNKNOWN", "REJECTED_ORDER_FILL_CONTRADICTION"
                else:
                    e.known.discard(a.expected_order_id)
                    self.rejected_target = (outcome, price, qty)
            if a.outcome in {"UNKNOWN", "ABORTED"}:
                self.cancel_wanted.add(a.attempt_id)
                self.rejected_target = (outcome, price, qty)
            self.e.record("sm_placement_result", asdict(a))
            self.record("command", {"event": "POST_RETURNED", "window_id": a.quote.window_id,
                "order_id": a.expected_order_id, "outcome": a.outcome, "mode": self.mode,
                "side": outcome, "price": str(price), "quantity": str(qty),
                "signal_observed_monotonic": self.signal_times.get(a.attempt_id, began),
                "dispatch_monotonic": began, "prepared_monotonic": prepared_at,
                "persisted_monotonic": persisted_at,
                "post_started_monotonic": self.post_started.get(a.attempt_id),
                "post_started": a.post_started,
                "aborted_reason": self.guard_failures.get(a.attempt_id),
                "returned_monotonic": e.monotonic(), **self.evidence()})
        except Exception as exc:
            a.outcome = "UNKNOWN" if a.post_started else "ABORTED"
            self.cancel_wanted.add(a.attempt_id)
            self.rejected_target = (outcome, price, qty)
            self.record("post_error", {"attempt_id": a.attempt_id, "error": type(exc).__name__})
        finally:
            if registered and a.outcome in {"REJECTED", "ABORTED"} and a.expected_order_id:
                e.processor.retire_unaccepted_order(a.expected_order_id, source="rdp_sm_"+self.mode.lower())
            self.post_returned[a.attempt_id] = e.monotonic()
            if self.rejected_target is not None:
                setattr(self.controller, self.mode.lower()+"_rejected_target", self.rejected_target)
                setattr(self.controller, self.mode.lower()+"_retry_after", e.monotonic()+self.controller.cfg.review_sec)
            self.last_progress = e.monotonic()
            # A cancellation before POST drained never covers a late acceptance.
            if a.attempt_id in self.cancel_wanted:
                self.cancel_ack.pop(a.attempt_id, None)
            self.changed.set()

    async def cancel(self, a):
        oid = self.oid(a)
        began = self.e.monotonic()
        self.cancel_started[a.attempt_id] = began
        self.cancel_revision[a.attempt_id] = self.e.revision
        self.cancel_completed[a.attempt_id] = False
        try:
            ack = await self.e.gateway.cancel_detailed([oid])
            self.cancel_completed[a.attempt_id] = True
            if oid in ack.canceled and self.post_done(a):
                self.cancel_ack[a.attempt_id] = self.e.monotonic()
            self.e.record("sm_cancel_ack", {"ids": [oid], "result": asdict(ack),
                "delete_requested": began, "delete_sdk_started": ack.sdk_started_monotonic,
                "delete_ack": ack.ack_monotonic, "scope": self.mode+"_ORDER"})
        finally:
            self.cancel_after[a.attempt_id] = self.e.monotonic()
            self.changed.set()

    def cancel_old(self, *, except_attempt=None):
        for a in self.orders:
            if a is not except_attempt and self.unresolved(a):
                self.cancel_wanted.add(a.attempt_id)
        now = self.e.monotonic()
        for a in self.orders:
            aid = a.attempt_id
            task = self.cancels.get(aid)
            if (aid not in self.cancel_wanted or not a.post_started or not self.unresolved(a)
                    or not self.oid(a) or task is not None and not task.done()
                    or aid in self.cancel_ack
                    or now-self.cancel_after.get(aid, -float("inf")) < self.e.cfg.rdp_sm.obi.cancel_retry_sec):
                continue
            if (self.post_done(a) and _canceled_submission_response(a)
                    and not self.e._canceled_submission_observed(self.oid(a))):
                continue  # Keep full risk while awaiting strict rejection proof.
            if self.recovery.suppress_cancel(a):
                continue  # Fresh positive/changed evidence immediately resumes DELETE.
            self.cancels[aid] = asyncio.create_task(self.cancel(a), name=self.mode.lower()+"-cancel")

    async def poll(self):
        """Targeted reads run independently of the operation coordinator."""
        e = self.e
        while not self.closed:
            try:
                for a in self.orders:
                    self.register(a)
                if e.shared_reads is not None:
                    version = e.shared_reads.service.latest().version
                    await e.shared_reads.observe_sl(self)
                    self.recovery.observe()
                    self.changed.set()
                elif self.reads_ready is None or self.reads_ready():
                    trades = await e.gateway.get_trades(market=e.meta.condition_id)
                    now = e.monotonic()
                    official = await e.reads.server_time()
                    e.official_time, e.time_anchor = official, e.monotonic()
                    for a in list(self.orders):
                        if not a.post_started or not self.post_done(a):
                            continue
                        oid = self.oid(a)
                        if a.outcome in {"REJECTED", "ABORTED"}:
                            continue
                        if _canceled_submission_response(a) and await e._verify_canceled_submission(a):
                            self.last_progress = e.monotonic()
                            continue
                        row = await e.gateway.get_order(oid)
                        # A fresh trade page must follow the terminal read, too.
                        trades = await e.gateway.get_trades(market=e.meta.condition_id)
                        self.observe_order(a, row, trades, now)
                if self.lifecycle.snapshot(self.controller, *e.economic_inventory()).reason == "WS_TICK_UNVERIFIED":
                    await e.reads.refresh_feed_ticks()
                if e.shared_reads is not None:
                    await e.shared_reads.service.wait_after_async(version, .05)
                else:
                    await asyncio.sleep(.05)
            except Exception as exc:
                self.wait(self.mode+"_PROOF_"+type(exc).__name__)
                await asyncio.sleep(.1)

    def observe_order(self, a, row, trades, read_started):
        e, oid = self.e, self.oid(a)
        ev = normalize_order(row)
        if ev is None:
            return
        if not (ev.order_id == oid and ev.market_id == e.meta.condition_id
                and ev.token_id == a.quote.token_id and ev.side is Side.BUY
                and Decimal(str(ev.original_size)) == Decimal(str(a.quote.size))
                and Decimal(str(ev.price)) == Decimal(str(a.quote.price))):
            e.fatal = self.mode+"_ORDER_IDENTITY_CONFLICT"
            e.processor.quarantine_order(oid, e.fatal, notify=False)
            return
        previous = e.sl_terminal_quantities.get(oid)
        if previous is not None and (not _terminal(ev, e.matched_remaining_tolerance_shares)
                or Decimal(str(ev.size_matched)) != previous):
            e.sl_terminal_quantities.pop(oid, None)
            e.fatal = self.mode+"_TERMINAL_EVIDENCE_CONTRADICTION"
            return
        legs = {}
        for raw in trades:
            parsed = parse_trade(raw)
            if parsed.market != e.meta.condition_id:
                continue
            for leg in parsed.maker_legs:
                if leg.order_id != oid:
                    continue
                from polymaker.state.tracker import TradeEvent

                fill = TradeEvent(leg.asset_id, leg.side, leg.price, leg.matched_amount,
                                  parsed.trade_id, parsed.status, parsed.timestamp, oid, parsed.market)
                e.processor.on_trade(fill, parsed.market, source="REST")
                legs[(parsed.trade_id, oid)] = fill
        qty = sum((Decimal(str(f.size)) for f in legs.values()), ZERO)
        if (not self.post_done(a) or not _terminal(ev, e.matched_remaining_tolerance_shares)
                or "associate_trades" not in row
                or set(ev.associate_trades) != {k[0] for k in legs}
                or abs(Decimal(str(ev.size_matched))-qty) > Decimal(".000001")
                or e.processor.has_failed_fill(oid) or e.processor.has_unresolved_fill_key(oid)
                or oid in e.processor.quarantined_orders
                or read_started < self.cancel_ack.get(a.attempt_id, 0)):
            return
        observed = e.processor.economic_fills_for_order(oid)
        if {(f.trade_id, oid) for f in observed} != set(legs):
            return
        if e.sl_terminal_quantities.get(oid) != qty:
            e.sl_terminal_quantities[oid] = qty
            self.proof_after[oid] = read_started
            self.last_progress = e.monotonic()
            self.changed.set()
            self.record("order_release", {"order_id": oid, "matched_quantity": str(qty),
                "unfilled_released": str(Decimal(str(a.quote.size))-qty),
                "source": "OWNED_TERMINAL_AND_COMPLETE_TRADE_SET", **self.evidence()})

    async def run(self):
        polling = asyncio.create_task(self.poll(), name="sl-proof-observer")
        try:
            while True:
                self.changed.clear()
                for a in self.orders:
                    self.register(a)
                abandoned = self.e.check_repair_abandonment()
                goal, reason = (None, "HIGH_ENTRY_REPAIR_ABANDONED") if abandoned else self.goal()
                health = self.health()
                lag_reason = (self.e.inventory_lag.maintenance_reason()
                              if self.e.inventory_mismatch is not None else None)
                stopping = (abandoned or self.closed or self.elapsed() >= 240 or self.e.closed or self.e.fatal
                            or getattr(self.e.journal, "failure", None) or lag_reason)
                if stopping:
                    self.reason = self.e.fatal or ("HIGH_ENTRY_REPAIR_ABANDONED" if abandoned
                        else lag_reason or "MARKET_SUBMISSION_CUTOFF")
                    goal = None
                    if not self.cutoff_logged:
                        self.cutoff_logged = True
                        self.e.record("sm_obi_close_requested", {
                            "window_id": self.window_id, "reason": self.reason,
                            "requested_monotonic": self.e.monotonic(),
                            "deadline": self.e.monotonic()+240-self.elapsed()})
                elif health.close_active:
                    reason, goal = "NETWORK_EXECUTION_FAILURE", None
                current = self.latest
                if current is not None and (not self.unresolved(current)
                        or current.attempt_id in self.cancel_wanted):
                    current = None
                book = ((goal[3].yes if goal[0] == "YES" else goal[3].no) if goal else None)
                needs = goal is not None and (current is None
                    or Decimal(str(current.quote.price)) <= book.best_ask-2*book.tick)
                blocked = ("SL_TARGET_RETRY_WAIT" if goal is not None and self.rejected_target == goal[:3]
                           else self.admission_reason(goal) if needs else None)
                if needs and blocked is None:
                    outcome, price, qty, snap = goal
                    wid = f"{self.window_id}:sl:{len(self.created)+1}"
                    quote = WindowQuote(self.e.meta.condition_id,
                        self.e.meta.yes.token_id if outcome == "YES" else self.e.meta.no.token_id,
                        outcome, Side.BUY, float(price), float(qty), wid)
                    a = Placement(uuid.uuid4().hex, quote)
                    self.e.attempts.append(a)  # Atomic reservation before any await.
                    self.e.inventory_lag.reserve(a)
                    self.recovery.reserve(a)
                    self.orders.append(a)
                    self.created.append(a)
                    self.latest = a
                    self.signal_times[a.attempt_id] = self.e.monotonic()
                    ep = self.e.repair_episode
                    if ep is not None:
                        ep["submissions"] += 1
                        ep.setdefault("windows", []).append(wid)
                        ep["chain_windows"].append(wid)
                    self.e.record("sm_sl_signal", {"window_id": wid, "mode": "SL", "side": outcome,
                        "price": str(price), "quantity": str(qty),
                        "signal_monotonic": self.e.monotonic(), **self.evidence()})
                    yes, no = self.e.economic_inventory()
                    self.last_plan = {"window_id": wid, "state": "REPAIR", "mode": "SL",
                        "elapsed_s": self.elapsed(), "inventory_yes": str(yes), "inventory_no": str(no),
                        "delta": str(yes-no), "quotes": [asdict(quote)],
                        "books": {side: {**b.as_book(), "bid_levels_count": len(b.bids),
                            "ask_levels_count": len(b.asks)} for side, b in (("YES", snap.yes), ("NO", snap.no))},
                        "decision_source": "SIGNAL_DRIVEN_SL"}
                    self.e.record("sm_strategy_window", self.last_plan)
                    self.posts[a.attempt_id] = asyncio.create_task(self.submit(goal, a), name="sl-post")
                    self.cancel_old(except_attempt=a)
                elif goal is None or needs:
                    self.cancel_old()
                    if goal is None:
                        self.wait(reason or self.reason or "NO_SL_TARGET")
                    else:
                        self.wait(blocked, goal)
                else:
                    self.cancel_old(except_attempt=current)
                unresolved = [a for a in self.orders if self.unresolved(a)]
                pending_posts = [t for t in self.posts.values() if not t.done()]
                if stopping and not pending_posts and self.recovery.waiting():
                    break  # Final cleanup/closeout retains all unresolved risk.
                if not unresolved and not pending_posts:
                    self.e.reads.network.complete_batch(self.group, safe=True)
                    if (stopping or goal is None or needs and not self.allowed(goal)
                            or self.rejected_target == goal[:3]):
                        break
                waiting_cancel = unresolved and all(a.attempt_id in self.cancel_wanted for a in unresolved)
                if (waiting_cancel and not self.recovery.waiting()
                        and self.e.monotonic()-self.last_progress > self.e.cfg.risk.ws_stale_halt_s):
                    self.reason = "SL_ORDER_PROOF_UNRESOLVED"
                    break
                self.e.drain_network_events()
                await self.wait_changed()
        finally:
            self.closed = True
            self.cancel_old()
            await asyncio.gather(*self.posts.values(), return_exceptions=True)
            # Late acceptance after an early cancellation always gets a new DELETE.
            for a in self.orders:
                self.register(a)
            self.cancel_old()
            await asyncio.gather(*self.cancels.values(), return_exceptions=True)
            polling.cancel()
            with suppress(asyncio.CancelledError):
                await polling
        return self.created
