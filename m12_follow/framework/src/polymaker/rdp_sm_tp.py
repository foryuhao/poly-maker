"""Independent fixed-price TP execution, sharing SL's owned order I/O primitives."""
from __future__ import annotations

import asyncio
import uuid
from contextlib import suppress
from dataclasses import asdict, replace
from decimal import Decimal

from polymaker.domain import Side
from polymaker.rdp_sm_execution import Placement, WindowQuote
from polymaker.rdp_sm_sl import SLExecution
from polymaker.rdp_sm_sl_ledger import SMALL, exposure


class TPExecution(SLExecution):
    """Only one TP submission at a time; accepting it freezes its price.

    Independent POST/cancel/proof tasks preserve the existing ownership ledger.
    A transition to SL transfers every old attempt, including an in-flight POST,
    to SL's overlap-aware coordinator without waiting for a global close.
    """

    mode = "TP"

    def __init__(self, controller, window_id):
        super().__init__(controller, window_id)
        self.plan = dict(controller.plan)
        self.episode = self.e.repair_episode
        self.signal_observed = self.episode.get("tp_signal_observed_monotonic", self.started_monotonic)
        self.last_observation = None
        self.handoff = None
        self.parent_drained = asyncio.Event()

    def on_accepted(self, attempt):
        with self.controller._policy_lock:
            if self.e.repair_episode is self.episode:
                self.controller.policy.accepted_tp(Decimal(str(attempt.quote.price)),
                                                   Decimal(self.plan["tick"]))
                self.controller._persist()

    def goal(self):
        c, e = self.controller, self.e
        c.boundary_close_reason(None, self.elapsed())
        if e.repair_episode is not self.episode:
            return None, "REPAIR_EPISODE_CHANGED"
        if c.policy.mode == "SL" and e.cfg.rdp_sm.repair.path.sl_enabled:
            return None, "REPAIR_MODE_SL_CANCEL_OLD_STATE"
        if not e.cfg.rdp_sm.repair.path.tp_enabled:
            return None, "TP_DISABLED"
        if self.elapsed() >= 180:
            return None, "TP_SUBMISSION_CUTOFF"
        if c.policy.mode != "TP":
            return None, "TP_MODE_INACTIVE"
        state = exposure(e)
        if abs(state.actual) <= SMALL:
            return None, "REPAIR_SMALL_EXPOSURE"
        if (state.actual > 0) != (self.episode["side"] == "YES"):
            return None, "REPAIR_REVERSE_ACTUAL"
        if state.actual*state.execution <= 0:
            return None, "WAIT_DIRECTION_PROOF"
        # A partial MATCHED is pending, not yet the economic small-residual
        # stopping condition. Preserve the original TP until actual <=2.5;
        # full execution coverage can already cancel its redundant remainder.
        if abs(state.execution) < Decimal(".1"):
            return None, "MATCHED_COVERS_REPAIR"
        snapshot = self.lifecycle.snapshot(c, *e.economic_inventory())
        if not snapshot.valid:
            return None, snapshot.reason
        if not self.consistent(snapshot):
            return None, "REPAIR_PROBABILITY_INCONSISTENT"
        feed = self.lifecycle.feed
        with feed.lock:
            sequence = feed.repair_price_points[-1].sequence if feed.repair_price_points else -1
        key = (sequence, snapshot.generation, e.revision)
        if key != self.last_observation:
            try:
                decision, _ = c.observe(snapshot)
            except ValueError as exc:
                c._blocked(str(exc), discard_plan=False)
                return None, str(exc)
            self.last_observation = key
            if decision["mode"] != "TP":
                return None, ("REPAIR_MODE_SL_CANCEL_OLD_STATE"
                              if decision["mode"] == "SL" and e.cfg.rdp_sm.repair.path.sl_enabled
                              else "TP_MODE_INACTIVE")
        return (self.plan["side"], Decimal(str(self.plan["price"])),
                Decimal(str(self.plan["quantity"])), snapshot), None

    def admission_reason(self, goal, *, reserved=None):
        e, c = self.e, self.controller
        side, price, qty, snapshot = goal
        if self.closed or e.closed:
            return "TP_EXECUTION_CLOSED"
        if e.check_repair_abandonment():
            return "HIGH_ENTRY_REPAIR_ABANDONED"
        if e.cooldown.active:
            return "COOLDOWN_ACTIVE"
        if not e.cfg.rdp_sm.repair.path.tp_enabled:
            return "TP_DISABLED"
        if not self.start <= self.elapsed() < 180:
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
        if e.repair_episode is not self.episode or c.policy.mode != "TP":
            return "REPAIR_EPISODE_OR_MODE_CHANGED"
        if any(e.processor.has_failed_fill(self.oid(a))
               or e.processor.has_unresolved_fill_key(self.oid(a))
               or self.oid(a) in e.processor.quarantined_orders for a in self.orders if self.oid(a)):
            return "REPAIR_EXECUTION_UNRESOLVED"
        health = self.health()
        if not health.allowed:
            return health.reason
        if not self.recovery.allows(reserved):
            return "UNKNOWN_REPAIR_RECONCILING"
        if e.monotonic()-e.time_anchor > e.cfg.risk.ws_stale_halt_s:
            return "OFFICIAL_CLOCK_STALE"
        current = self.lifecycle.snapshot(c, *e.economic_inventory())
        if not current.valid:
            return current.reason
        if not self.consistent(current):
            return "REPAIR_PROBABILITY_INCONSISTENT"
        if current.generation != snapshot.generation:
            return "WS_GENERATION_CHANGED"
        book = current.yes if side == "YES" else current.no
        if (book.tick != Decimal(self.plan["tick"]) or price <= 0 or price >= 1
                or price % book.tick or price >= book.best_ask):
            return "TP_QUOTE_INVALIDATED"
        try:
            decision, _ = c.observe(current, project=True)
        except ValueError as exc:
            return str(exc)
        if decision["mode"] != "TP":
            return "REPAIR_MODE_SL_CANCEL_OLD_STATE"
        if c.policy.tp_price is None and c.sigma() is None:
            return "TP_VOLATILITY_UNKNOWN"
        state = exposure(e)
        if reserved is None:
            # TP never overlaps its own replacement; only SL permits this.
            if any(self.unresolved(a) for a in self.orders):
                return "WAIT_TP_ORDER_PROOF"
        else:
            state = replace(state, reserved=state.reserved-qty*price,
                            yes_pending=state.yes_pending-(qty if side == "YES" else Decimal(0)),
                            no_pending=state.no_pending-(qty if side == "NO" else Decimal(0)))
        if (abs(state.actual) <= SMALL or abs(state.execution) <= SMALL
                or state.actual*state.execution <= 0
                or (state.actual > 0) != (side == "NO")):
            return "TP_EXECUTION_TARGET_CHANGED"
        return state.admission_reason(side, qty, price, self.capital)

    def create(self, goal):
        side, price, qty, snapshot = goal
        wid = f"{self.window_id}:tp:{len(self.created)+1}"
        quote = WindowQuote(self.e.meta.condition_id,
            self.e.meta.yes.token_id if side == "YES" else self.e.meta.no.token_id,
            side, Side.BUY, float(price), float(qty), wid)
        attempt = Placement(uuid.uuid4().hex, quote)
        self.e.attempts.append(attempt)  # Reserve all possible exposure before I/O.
        self.e.inventory_lag.reserve(attempt)
        self.orders.append(attempt)
        self.created.append(attempt)
        self.latest = attempt
        self.signal_times[attempt.attempt_id] = self.signal_observed
        ep = self.episode
        ep["submissions"] += 1
        ep.setdefault("windows", []).append(wid)
        chain = ep.setdefault("chain_windows", list(ep["windows"]))
        if wid not in chain:
            chain.append(wid)
        now = self.e.monotonic()
        self.record("signal", {"window_id": wid, "mode": "TP", "side": side,
            "price": str(price), "quantity": str(qty), "signal_monotonic": self.signal_observed,
            "target_ready_monotonic": now, "sigma": self.controller.sigma(), **self.evidence()})
        yes, no = self.e.economic_inventory()
        self.last_plan = {"window_id": wid, "state": "REPAIR", "mode": "TP",
            "elapsed_s": self.elapsed(), "inventory_yes": str(yes), "inventory_no": str(no),
            "delta": str(yes-no), "quotes": [asdict(quote)],
            "books": {name: {**b.as_book(), "bid_levels_count": len(b.bids),
                "ask_levels_count": len(b.asks)} for name, b in (("YES", snapshot.yes), ("NO", snapshot.no))},
            "decision_source": "SIGNAL_DRIVEN_TP_FIXED_PRICE"}
        self.e.record("sm_strategy_window", self.last_plan)
        self.posts[attempt.attempt_id] = asyncio.create_task(self.submit(goal, attempt), name="tp-post")

    async def _drain_posts(self):
        await asyncio.gather(*self.posts.values(), return_exceptions=True)
        self.parent_drained.set()

    async def run(self):
        polling = asyncio.create_task(self.poll(), name="tp-proof-observer")
        draining = None
        try:
            while True:
                self.changed.clear()
                for attempt in self.orders:
                    self.register(attempt)
                abandoned = self.e.check_repair_abandonment()
                goal, reason = (None, "HIGH_ENTRY_REPAIR_ABANDONED") if abandoned else self.goal()
                health = self.health()
                lag_reason = (self.e.inventory_lag.maintenance_reason()
                              if self.e.inventory_mismatch is not None else None)
                stopping = (abandoned or self.closed or self.e.closed or self.e.fatal or self.elapsed() >= self.cutoff
                            or getattr(self.e.journal, "failure", None) or lag_reason)
                if (not stopping and reason == "REPAIR_MODE_SL_CANCEL_OLD_STATE"
                        and self.e.cfg.rdp_sm.repair.path.sl_enabled):
                    self.reason = reason
                    self.closed = True  # Prevent any old TP from starting after the handoff.
                    polling.cancel()
                    with suppress(asyncio.CancelledError):
                        await polling
                    draining = asyncio.create_task(self._drain_posts(), name="tp-post-drain")
                    self.handoff = SLExecution(self.controller, self.window_id,
                        inherited=self.orders, parent_done=self.parent_drained)
                    self.record("handoff", {"window_id": self.window_id, "reason": reason,
                        "inherited_order_ids": [self.oid(a) for a in self.orders], **self.evidence()})
                    # SL posts and cancels independently; all old attempts remain reserved.
                    await self.e._await_owned(self.handoff.run(), propagate_cancel=False)
                    break
                if stopping:
                    self.reason = self.e.fatal or ("HIGH_ENTRY_REPAIR_ABANDONED" if abandoned
                        else lag_reason or "MARKET_SUBMISSION_CUTOFF")
                    goal = None
                elif health.close_active:
                    reason, goal = "NETWORK_EXECUTION_FAILURE", None
                current = self.latest
                if current is not None and (not self.unresolved(current)
                        or current.attempt_id in self.cancel_wanted):
                    current = None
                if goal is not None and current is None and not self.created:
                    blocked = ("TP_TARGET_RETRY_WAIT" if self.rejected_target == goal[:3]
                               else self.admission_reason(goal))
                    if blocked is None:
                        self.create(goal)
                    else:
                        self.wait(blocked, goal)
                        self.reason = blocked
                        break
                elif goal is None:
                    self.reason = reason or self.reason or "NO_TP_TARGET"
                    self.cancel_old()
                    self.wait(self.reason)
                else:
                    # An accepted TP stays at its original price until risk/exit.
                    self.cancel_old(except_attempt=current)
                unresolved = [a for a in self.orders if self.unresolved(a)]
                pending = [task for task in self.posts.values() if not task.done()]
                if stopping and not pending and self.recovery.waiting():
                    break
                if not unresolved and not pending and (goal is None or self.created):
                    break
                waiting = unresolved and all(a.attempt_id in self.cancel_wanted for a in unresolved)
                if (waiting and not self.recovery.waiting()
                        and self.e.monotonic()-self.last_progress > self.e.cfg.risk.ws_stale_halt_s):
                    self.reason = "TP_ORDER_PROOF_UNRESOLVED"
                    break
                self.e.drain_network_events()
                await self.wait_changed()
        finally:
            self.closed = True
            self.cancel_old()
            await self._drain_posts()
            for attempt in self.orders:
                self.register(attempt)
            self.cancel_old()
            await asyncio.gather(*self.cancels.values(), return_exceptions=True)
            if draining is not None:
                await draining
            if not polling.done():
                polling.cancel()
            with suppress(asyncio.CancelledError):
                await polling
        return self.created+(self.handoff.created if self.handoff else [])
