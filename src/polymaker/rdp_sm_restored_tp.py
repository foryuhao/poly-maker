"""Fixed default TP, with acknowledged BA replacement before TP cancellation."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import asdict
from decimal import Decimal

from polymaker.domain import Side
from polymaker.rdp_sm_ca_matched_tp import drain_tp_posts, fixed_tp_price, tp_buy_size
from polymaker.rdp_sm_execution import (
    MIN_MAKER_BUY_SHARES,
    RDP_TOLERANCE,
    Placement,
    WindowQuote,
    WindowResult,
)
from polymaker.rdp_sm_sl import SLExecution
from polymaker.rdp_sm_sl_ledger import exposure, fifo_residual
from polymaker.state.tracker import fill_unit_cost

ZERO = Decimal(0)


class RestoredTP(SLExecution):
    mode = "RESTORED_TP"

    def __init__(self, controller, window_id):
        super().__init__(controller, window_id)
        self.episode = self.e.repair_episode
        self.baseline_ids = frozenset(self.e.known)
        self.baseline_external_revision = self.e.external_revision
        self.baseline_revision = self.e.revision
        self.initial_lots = fifo_residual(
            self.e.economic_fills(), self.e.fill_observed_at, self.e.meta.yes.token_id
        )
        self.expires_at = min(
            (self.e.ca_tp_deadlines[f.maker_order_id] for f in self.initial_lots
             if f.maker_order_id in self.e.ca_tp_deadlines),
            default=None,
        )
        self.initial_cash = Decimal(
            str((self.lifecycle.account or {}).get("basic_spendable_budget_usd", 0))
        )
        self.target = None
        self.price_source = None
        self.handoff_episode = None
        self.ba_attempts = []

    def tp_expired(self):
        return self.expires_at is not None and self.e.monotonic() >= self.expires_at

    def evidence(self):
        return {**super().evidence(), "ca_tp_expires_at_monotonic": self.expires_at}

    def snapshot(self):
        return self.lifecycle.feed.snapshot(ba_cfg=self.controller.ba_config)

    def residual(self, quantity=None):
        e = self.e
        lots = fifo_residual(e.economic_fills(), e.fill_observed_at, e.meta.yes.token_id)
        y, n = e.economic_inventory()
        qty = sum((Decimal(str(f.size)) for f in lots), ZERO)
        if not lots or qty != abs(y - n):
            return None
        held = e.meta.yes.token_id if y > n else e.meta.no.token_id
        if any(f.token_id != held for f in lots):
            return None
        selected = qty if quantity is None else min(qty, quantity)
        left, cost = selected, ZERO
        for fill in lots:
            used = min(left, Decimal(str(fill.size)))
            cost += used * fill_unit_cost(fill)
            left -= used
            if not left:
                break
        return ("NO" if y > n else "YES"), qty, cost / selected

    def goal(self):
        if self.e.repair_episode is not self.episode:
            return None, "REPAIR_EPISODE_CHANGED"
        if self.tp_expired():
            return None, "CA_MATCHED_TP_EXPIRED"
        residual = self.residual()
        if residual is None:
            return None, "NO_PROVEN_RESIDUAL"
        side, remaining, _ = residual
        minimum = max(MIN_MAKER_BUY_SHARES, Decimal(str(self.e.meta.min_order_size)))
        qty = tp_buy_size(remaining, minimum, self.e.cfg.rdp_sm.max_order_size)
        if qty is None:
            return None, "TP_REMAINING_BELOW_MINIMUM"
        entry = self.residual(qty)[2]
        snapshot = self.snapshot()
        if not snapshot.valid:
            return None, snapshot.reason
        book = snapshot.yes if side == "YES" else snapshot.no
        price, source = fixed_tp_price(
            entry, self.e.cfg.rdp_sm.ca_matched_tp.profit_per_share, book
        )
        if price is None:
            return None, source
        self.price_source = source
        return (side, price, qty, snapshot), None

    def admission_reason(self, goal, *, reserved=None):
        e = self.e
        side, price, qty, snapshot = goal
        if self.closed or e.closed or e.fatal:
            return e.fatal or "RESTORED_TP_CLOSED"
        if self.tp_expired():
            return "CA_MATCHED_TP_EXPIRED"
        if not self.controller.restore_tp_enabled or not self.start <= self.elapsed() < self.cutoff:
            return "MARKET_SUBMISSION_CUTOFF"
        if e.repair_episode is not self.episode or e.cooldown.active:
            return "REPAIR_EPISODE_CHANGED"
        if not e.journal.ready or e.merge_pending or e.inventory_mismatch is not None:
            return "RESTORED_TP_EXECUTION_UNAVAILABLE"
        if e.gateway._client.pagination_failure:
            return e.gateway._client.pagination_failure
        # An absent/UNKNOWN old order is never sufficient to restore a TP.
        if (
            not self.baseline_ids <= set(e.proofs) & set(e.terminal)
            or e.revision != self.baseline_revision
            or e.external_revision != self.baseline_external_revision
            or e.known - self.baseline_ids - {self.oid(a) for a in self.created}
        ):
            return "PREVIOUS_WINDOW_UNRESOLVED"
        if any(
            a not in self.created and a.outcome not in {"ACK", "VERIFIED", "REJECTED", "ABORTED"}
            for a in e.attempts
        ):
            return "PREVIOUS_WINDOW_UNRESOLVED"
        if not self.health().allowed:
            return self.health().reason
        if e.monotonic() - e.time_anchor > e.cfg.risk.ws_stale_halt_s:
            return "OFFICIAL_CLOCK_STALE"
        current = self.snapshot()
        if not current.valid:
            return current.reason
        if current.generation != snapshot.generation:
            return "WS_GENERATION_CHANGED"
        book = current.yes if side == "YES" else current.no
        old = snapshot.yes if side == "YES" else snapshot.no
        if book.tick != old.tick or price <= 0 or price % book.tick or price >= book.best_ask:
            return "TP_MAKER_PRICE_UNAVAILABLE"
        minimum = max(MIN_MAKER_BUY_SHARES, Decimal(str(e.meta.min_order_size)))
        if qty < minimum or qty > e.cfg.rdp_sm.max_order_size or qty % Decimal(".01"):
            return "RESTORED_TP_QUANTITY_CHANGED"
        residual = self.residual(qty)
        if residual is None or residual[0] != side or qty - residual[1] > RDP_TOLERANCE:
            return "RESTORED_TP_QUANTITY_CHANGED"
        if price > 1 - residual[2] - e.cfg.rdp_sm.ca_matched_tp.profit_per_share:
            return "RESTORED_TP_COST_CHANGED"
        if self.controller.tp_ba_signal(current) is not None:
            return "OPPOSITE_BA_HANDOFF"
        if reserved is not None and not (
            any(a is reserved for a in self.created)
            and any(a is reserved for a in e.attempts)
            and reserved.outcome == "INTENT"
            and not reserved.post_started
            and reserved.quote.outcome == side
            and Decimal(str(reserved.quote.price)) == price
            and Decimal(str(reserved.quote.size)) == qty
        ):
            return "RESTORED_TP_RESERVATION_CHANGED"
        credit = reserved.maximum if reserved is not None else ZERO
        cash = min(
            self.initial_cash,
            Decimal(str((self.lifecycle.account or {}).get("basic_spendable_budget_usd", 0))),
        )
        if e.committed() - credit + price * qty > e.capital_limit or price * qty > cash:
            return "RESTORED_TP_INSUFFICIENT_FUNDS"
        state = exposure(e)
        if reserved is not None:
            from dataclasses import replace

            state = replace(
                state,
                reserved=state.reserved - credit,
                yes_pending=state.yes_pending - (qty if side == "YES" else ZERO),
                no_pending=state.no_pending - (qty if side == "NO" else ZERO),
            )
        return state.admission_reason(side, qty, price, self.capital)

    def create(self, goal):
        side, price, qty, snapshot = goal
        e = self.e
        if self.window_id in e.used_windows:
            raise ValueError("WINDOW_ID_ALREADY_USED")
        e.used_windows.add(self.window_id)
        quote = WindowQuote(
            e.meta.condition_id,
            e.meta.yes.token_id if side == "YES" else e.meta.no.token_id,
            side,
            Side.BUY,
            float(price),
            float(qty),
            self.window_id + ":restored-tp",
        )
        a = Placement(uuid.uuid4().hex, quote)
        e.attempts.append(a)
        self.orders.append(a)
        self.created.append(a)
        self.latest = a
        self.target = goal
        self.signal_times[a.attempt_id] = self.started_monotonic
        self.episode["submissions"] += 1
        self.episode.setdefault("windows", []).append(quote.window_id)
        self.episode.setdefault("chain_windows", []).append(quote.window_id)
        self.record(
            "signal",
            {
                "window_id": quote.window_id,
                "side": side,
                "price": str(price),
                "quantity": str(qty),
                "price_source": self.price_source,
                "fixed_quote": True,
                "profit_per_share": str(e.cfg.rdp_sm.ca_matched_tp.profit_per_share),
                "cost_basis": "FIFO_REMAINING_LOTS",
                "execution_role": self.mode,
                "notional_policy": "REMAINING_SHARES_ONLY_NO_UPSIZE",
                **self.evidence(),
            },
        )
        y, n = e.economic_inventory()
        e.record(
            "sm_strategy_window",
            {
                "window_id": self.window_id,
                "state": "REPAIR",
                "inventory_yes": str(y),
                "inventory_no": str(n),
                "delta": str(y - n),
                "elapsed_s": self.elapsed(),
                "quotes": [asdict(quote)],
                "sizing": {"policy": "default_tp_restore_v1", "execution_role": self.mode},
                "books": {
                    name: {
                        **b.as_book(),
                        "bid_levels_count": len(b.bids),
                        "ask_levels_count": len(b.asks),
                    }
                    for name, b in (("YES", snapshot.yes), ("NO", snapshot.no))
                },
                "decision_source": "DEFAULT_TP_RESTORED_AFTER_BA",
            },
        )
        self.posts[a.attempt_id] = asyncio.create_task(
            self.submit(goal, a), name="restored-tp-post"
        )

    async def run(self):
        polling = asyncio.create_task(self.poll(), name="restored-tp-proof")
        try:
            while self.reason is None:
                self.changed.clear()
                self.lifecycle.drain_ba()
                for a in self.orders:
                    self.register(a)
                current = self.snapshot()
                original = self.target[3] if self.target else current
                self.reason = next(
                    (
                        name
                        for name, bad in (
                            (self.e.fatal, bool(self.e.fatal)),
                            ("RESTORED_TP_CLOSED", self.closed or self.e.closed),
                            ("MARKET_SUBMISSION_CUTOFF", self.elapsed() >= self.cutoff),
                            ("CA_MATCHED_TP_EXPIRED", self.tp_expired()),
                            ("ASYNC_JOURNAL_NOT_READY", not self.e.journal.ready),
                            ("REPAIR_EPISODE_CHANGED", self.e.repair_episode is not self.episode),
                            ("WS_GENERATION_CHANGED", current.generation != original.generation),
                            (
                                "WS_TICK_CHANGED",
                                any(
                                    a.tick != b.tick
                                    for a, b in zip(
                                        (current.yes, current.no),
                                        (original.yes, original.no),
                                        strict=True,
                                    )
                                ),
                            ),
                            (
                                current.reason,
                                not current.valid
                                and current.reason not in {"WS_BOOK_STALE", "WS_TOP_PENDING"},
                            ),
                            ("NETWORK_EXECUTION_FAILURE", self.health().close_active),
                            (
                                "REPAIR_EXECUTION_UNRESOLVED",
                                any(
                                    self.e.processor.has_failed_fill(self.oid(a))
                                    or self.e.processor.has_unresolved_fill_key(self.oid(a))
                                    or self.oid(a) in self.e.processor.quarantined_orders
                                    for a in self.orders
                                ),
                            ),
                        )
                        if bad
                    ),
                    None,
                )
                if self.reason:
                    break
                from polymaker.rdp_sm_tp_ba_handoff import try_ba_before_tp_cancel

                if await try_ba_before_tp_cancel(self, self.controller):
                    self.reason = self.handoff_reason
                    break
                if self.created:
                    a = self.created[0]
                    if self.post_done(a):
                        if a.outcome != "ACK" or not self.unresolved(a):
                            self.reason = (
                                "TP_ORDER_TERMINAL"
                                if a.outcome == "ACK"
                                else "TP_POST_" + a.outcome
                            )
                        elif abs(exposure(self.e).execution) < Decimal(".1"):
                            self.reason = "TP_TARGET_COVERED"
                else:
                    goal, reason = self.goal()
                    if goal is None:
                        self.reason = reason
                        break
                    reason = self.admission_reason(goal)
                    if reason:
                        self.reason = reason
                        break
                    self.create(goal)
                if self.reason is None:
                    await self.wait_changed()
        finally:
            self.closed = True
            try:
                await drain_tp_posts(self, expired=self.reason == "CA_MATCHED_TP_EXPIRED")
                for a in self.created:
                    self.register(a)
                    if (a.post_started and self.unresolved(a)
                            and a.attempt_id not in self.cancel_ack):
                        try:
                            await self.cancel(a)
                        except Exception as exc:
                            self.record(
                                "cancel_error",
                                {"order_id": self.oid(a), "error": type(exc).__name__},
                            )
            finally:
                polling.cancel()
                await asyncio.gather(polling, return_exceptions=True)
                self.record(
                    "closed",
                    {
                        "reason": self.reason,
                        "orders": [asdict(a) for a in self.created],
                        "final_close_proof_required": True,
                        **self.evidence(),
                    },
                )
        return self.created+self.ba_attempts

    def record_result(self, safe):
        from polymaker.rdp_sm_tp_ba_handoff import record_ba_handoff_result, role_result

        self.record(
            "result",
            {
                "window_id": self.window_id,
                "execution_role": self.mode,
                **role_result(self.e, self.created, safe),
                "reason": self.reason,
                **self.evidence(),
            },
        )
        record_ba_handoff_result(self, safe)


async def execute_restored_tp(controller, window_id):
    e, lifecycle = controller.executor, controller.lifecycle
    if not e.safe_to_continue or lifecycle.busy or e.repair_episode is None:
        return None
    actor = RestoredTP(controller, window_id)
    goal, reason = actor.goal()
    if goal is not None and actor.rejected_target == goal[:3]:
        reason = "RESTORED_TP_RETRY_WAIT"
    if reason is None:
        reason = actor.admission_reason(goal)
    if reason:
        key = (id(e.repair_episode), reason, goal[:3] if goal else None)
        if key != getattr(controller, "restored_tp_last_wait", None):
            actor.wait(reason, goal)
            controller.restored_tp_last_wait = key
        await lifecycle.feed.wait_after(lifecycle.feed.sequence, 0.1)
        return None
    lifecycle.busy = True
    controller.restored_tp_last_wait = None
    try:
        created = await e._await_owned(actor.run(), propagate_cancel=False)
        if not created:
            return None
        safe = await e._await_owned(e._verify_close(cancel=True), propagate_cancel=False)
        if not safe and e.pending_confirmations:
            await e._await_owned(e._wait_for_confirmation(), propagate_cancel=False)
            safe = e.safe_to_continue
        e.update_repair_episode()
        if safe:
            controller.reconcile_episode()
        actor.record_result(safe)
        result = WindowResult(
            e.session_id,
            window_id,
            e.meta.condition_id,
            actor.started_wall,
            end_time=time.time(),
            requested_quotes=[a.quote for a in created],
            placement_results=[asdict(a) for a in created],
            known_order_ids=sorted(e.known),
            confirmed_fills=[asdict(f) for f in e.economic_fills()],
            yes_inventory=float(e.economic_inventory()[0]),
            no_inventory=float(e.economic_inventory()[1]),
            delta=float(e.economic_inventory()[0] - e.economic_inventory()[1]),
            capital_committed=float(e.committed()),
            close_barrier="SAFE_TO_CONTINUE" if safe else "BLOCKED",
            block_reason=e.reason,
            t0_monotonic=actor.started_monotonic,
            close_monotonic=e.monotonic(),
            fill_deadline_monotonic=(
                e.time_anchor + int(e.slug.rsplit("-", 1)[1]) + controller.cutoff - e.official_time
            ),
        )
        e.reads.network.complete_batch(actor.group, safe=safe)
        e.record("sm_window_result", result.to_dict())
        await e.journal.flush()
        return result, {
            "policy": "default_tp_restore_v1",
            "execution_role": "RESTORED_TP",
            "order_policy": "DEFAULT_TP_FIXED_PRICE",
            "price_source": actor.price_source,
            "reason": actor.reason,
            "handoff_episode_id": actor.handoff_episode,
            "handoff_policy": "POST_BA_ACK_THEN_CANCEL_TP",
            "ba_orders": [asdict(a) for a in actor.ba_attempts],
            "cost_basis": "FIFO_REMAINING_LOTS",
            "target_sizing_applied": False,
            "signed_target_delta": "0",
            "favorite_data_validity": "NOT_USED",
        }
    finally:
        lifecycle.busy = False
