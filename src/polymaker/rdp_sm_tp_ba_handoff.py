"""Post one fresh repair BA before canceling its owned default TP.

The TP owner remains alive on rejection or uncertain acceptance. Both orders
retain their full reservations until terminal order/trade evidence releases
them. No subsequent opportunity may post while this handoff is unresolved.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import asdict, replace
from decimal import Decimal

from polymaker.domain import Side, TradeState
from polymaker.rdp_sm_ba import entry_admission
from polymaker.rdp_sm_execution import Placement, WindowQuote, repair_buy_quantity
from polymaker.rdp_sm_sl import SLExecution
from polymaker.rdp_sm_sl_ledger import SMALL, exposure, fifo_paired_quantities, fifo_pairs

ZERO = Decimal(0)


class TPMakerHandoff(SLExecution):
    mode = "TP_BA_HANDOFF"

    def __init__(self, owner, controller, episode):
        done = asyncio.Event()
        done.set()  # try_ba_before_tp_cancel checks every inherited POST first.
        super().__init__(controller, owner.window_id, inherited=owner.orders, parent_done=done)
        # This transition never spends an absence-based UNKNOWN release. Keep
        # both reservations until positive terminal/trade proof, even if the
        # general repair path enables its separate recovery policy.
        self.recovery.cfg = self.recovery.cfg.model_copy(update={"enabled": False})
        self.owner = owner
        self.episode = episode
        self.claim = None
        self.accepted = False
        self.stop_owner = False
        self.sizing_overshoot = ZERO
        self.baseline_ids = frozenset(self.e.known)
        self.inherited_ids = {self.oid(a) for a in self.inherited}
        self.inherited_attempts = frozenset(id(a) for a in self.e.attempts)
        self.tp_orders = tuple(owner.created)
        self.tp_matched_at_plan = sum((self.observed_quantity(a) for a in self.tp_orders), ZERO)
        self.generation = owner.snapshot().generation
        self.ticks = (owner.snapshot().yes.tick, owner.snapshot().no.tick)
        self.initial_cash = Decimal(
            str((self.lifecycle.account or {}).get("basic_spendable_budget_usd", 0))
        )
        self.deadline = min(
            episode.order_deadline, self.e.monotonic() + self.cutoff - self.elapsed()
        )

    def snapshot(self, *, active=False):
        return self.lifecycle.feed.snapshot(
            ba_cfg=self.controller.ba_config, allow_pending_top=active
        )

    def goal(self):
        state = exposure(self.e, retain_unknown_reservations=True)
        side = self.episode.outcome
        if (
            abs(state.actual) <= SMALL
            or abs(state.execution) <= SMALL
            or state.actual * state.execution <= 0
            or (state.actual > 0) != (side == "NO")
        ):
            return None, "TP_BA_POSITION_ALREADY_COVERED"
        snapshot = self.snapshot()
        if not snapshot.valid:
            return None, snapshot.reason
        book = snapshot.no if side == "NO" else snapshot.yes
        price = (
            book.best_ask - book.tick
            if getattr(self.controller.episodes, "fixed_ask_quote", False)
            else self.episode.anchor_bid_price
        )
        cash = min(self.initial_cash, max(ZERO, self.capital - state.spent - state.reserved))
        try:
            qty = repair_buy_quantity(self.e, side, price, state.execution, cash)
        except ValueError as exc:
            return None, str(exc)
        return (side, price, qty, snapshot), None

    def admission_reason(self, goal, *, reserved=None):
        e = self.e
        side, price, qty, original = goal
        if self.closed or self.owner.closed or e.closed or e.fatal:
            return e.fatal or "TP_BA_HANDOFF_CLOSED"
        direction_prover = getattr(self.owner, "prove_ba_direction", None)
        if direction_prover is not None and not direction_prover():
            return "CA_TP_DIRECTION_UNPROVEN"
        if not self.controller.restore_tp_enabled or not 15 <= self.elapsed() < self.cutoff:
            return "MARKET_SUBMISSION_CUTOFF"
        if e.check_repair_abandonment():
            return "HIGH_ENTRY_REPAIR_ABANDONED"
        if (
            e.cooldown.active
            or not e.journal.ready
            or e.merge_pending
            or e.inventory_mismatch is not None
        ):
            return "TP_BA_EXECUTION_UNAVAILABLE"
        if e.gateway._client.pagination_failure or not self.health().allowed:
            return e.gateway._client.pagination_failure or self.health().reason
        if e.monotonic() - e.time_anchor > e.cfg.risk.ws_stale_halt_s:
            return "OFFICIAL_CLOCK_STALE"
        if any(id(a) not in self.inherited_attempts and a is not reserved for a in e.attempts):
            return "TP_BA_FOREIGN_ATTEMPT"
        # Only this owner's acknowledged live TP/opening may cross the serial
        # barrier. Every older window still needs its complete close proof.
        previous_ba_terminal = {
            self.oid(a)
            for a in self.owner.ba_attempts
            if a.outcome == "VERIFIED" and self.oid(a) in e.sl_terminal_quantities
        }
        if not self.baseline_ids - self.inherited_ids - previous_ba_terminal <= set(e.proofs) & set(
            e.terminal
        ):
            return "PREVIOUS_WINDOW_UNRESOLVED"
        if e.known - self.baseline_ids - {self.oid(a) for a in self.created}:
            return "TP_BA_FOREIGN_ORDER"
        if any(
            a.outcome not in {"ACK", "VERIFIED", "REJECTED", "ABORTED"}
            for a in e.attempts
            if a is not reserved
        ):
            return "PREVIOUS_WINDOW_UNRESOLVED"
        if any(
            e.processor.has_failed_fill(self.oid(a))
            or e.processor.has_unresolved_fill_key(self.oid(a))
            or self.oid(a) in e.processor.quarantined_orders
            for a in self.orders
        ):
            return "TP_BA_FILL_UNRESOLVED"
        if not any(a.outcome == "ACK" and self.owner.unresolved(a) for a in self.tp_orders):
            return "TP_BA_NO_LIVE_TP"
        if (
            sum((self.observed_quantity(a) for a in self.tp_orders), ZERO)
            != self.tp_matched_at_plan
        ):
            return "TP_BA_TP_FILL_CHANGED"
        current = self.snapshot()
        if not current.valid:
            return current.reason
        if current.generation != self.generation or current.generation != original.generation:
            return "WS_GENERATION_CHANGED"
        book = current.no if side == "NO" else current.yes
        old = original.no if side == "NO" else original.yes
        wanted = (
            book.best_ask - book.tick
            if getattr(self.controller.episodes, "fixed_ask_quote", False)
            else self.episode.anchor_bid_price
        )
        if (
            book.tick != old.tick
            or price != wanted
            or price <= 0
            or price >= book.best_ask
            or price % book.tick
        ):
            return "TP_BA_MAKER_QUOTE_CHANGED"
        with self.lifecycle.feed.lock:
            direction, _ = self.controller.select(current)
            ep = self.controller.episodes.selected
            if direction != side or ep is not self.episode or ep.consumed:
                return "TP_BA_SIGNAL_CHANGED"
            if self.claim is not None and not self.controller.episodes.current(self.claim):
                return "TP_BA_CLAIM_CHANGED"
            timing = entry_admission(
                self.controller.ba_config,
                t0=ep.signal_start_mono,
                deadline=self.deadline,
                writes=e.reads.network.recent_writes(),
            )
            if timing.reason:
                return timing.reason
        state = exposure(e, retain_unknown_reservations=True)
        if reserved is not None:
            if not (
                any(a is reserved for a in self.created)
                and any(a is reserved for a in e.attempts)
                and reserved.outcome == "INTENT"
                and not reserved.post_started
                and (
                    reserved.quote.outcome,
                    Decimal(str(reserved.quote.price)),
                    Decimal(str(reserved.quote.size)),
                )
                == (side, price, qty)
            ):
                return "TP_BA_RESERVATION_CHANGED"
            state = replace(
                state,
                reserved=state.reserved - reserved.maximum,
                yes_pending=state.yes_pending - (qty if side == "YES" else ZERO),
                no_pending=state.no_pending - (qty if side == "NO" else ZERO),
            )
        if (
            abs(state.actual) <= SMALL
            or abs(state.execution) <= SMALL
            or state.actual * state.execution <= 0
            or (state.actual > 0) != (side == "NO")
        ):
            return "TP_BA_POSITION_ALREADY_COVERED"
        cash = min(
            self.initial_cash,
            Decimal(str((self.lifecycle.account or {}).get("basic_spendable_budget_usd", 0))),
            max(ZERO, self.capital - state.spent - state.reserved),
        )
        try:
            expected = repair_buy_quantity(e, side, price, state.execution, cash)
        except ValueError as exc:
            return str(exc)
        if qty != expected:
            return "TP_BA_QUANTITY_CHANGED"
        # The old TP reservation is deliberately NOT credited. Both may fill.
        return state.admission_reason(
            side, qty, price, self.capital, e.cfg.rdp_sm.repair.delta_limit
        )

    def on_post_start(self, attempt, goal):
        with self.lifecycle.feed.lock:
            reason = self.admission_reason(goal, reserved=attempt)
            if reason:
                raise ValueError(reason)
            self.controller.episodes.consume(self.claim)

    def on_accepted(self, attempt):
        self.accepted = True
        self.e.repair_order_started[self.oid(attempt)] = self.e.monotonic()
        self.record(
            "accepted",
            {
                "episode_id": self.episode.episode_id,
                "ba_order_id": self.oid(attempt),
                "tp_order_ids": [self.oid(a) for a in self.tp_orders],
                "action": "CANCEL_TP_AFTER_BA_ACK",
                **self.evidence(),
            },
        )
        # Schedule DELETE immediately at ACK, independently of the proof lane.
        self.cancel_tp()

    async def cancel(self, attempt):
        # The TP owner's observer runs concurrently. Publish the same DELETE
        # boundary to both observers so neither can release a stale remainder.
        cache = self.e.shared_reads
        oid = self.oid(attempt)
        write = cache.begin_write("DELETE", [oid]) if cache is not None else None
        try:
            return await super().cancel(attempt)
        finally:
            if write is not None:
                cache.end_write(write)
                cache.service.track_orders({oid: cache.order_writes[oid]})
                cache.service.request_repair_refresh(("orders", "account"))

    def cancel_tp(self):
        for a in self.tp_orders:
            self.cancel_wanted.add(a.attempt_id)
        # inherited opening orders are not canceled by this transition.
        for a in self.tp_orders:
            task = self.cancels.get(a.attempt_id)
            if (
                a.post_started
                and self.unresolved(a)
                and a.attempt_id not in self.cancel_ack
                and (task is None or task.done())
                and self.e.monotonic() - self.cancel_after.get(a.attempt_id, -float("inf"))
                >= self.e.cfg.rdp_sm.obi.cancel_retry_sec
            ):
                self.cancels[a.attempt_id] = asyncio.create_task(
                    self.cancel(a), name="ba-ack-cancel-tp"
                )

    def cancel_marked(self):
        # SL.cancel_old marks every inherited order for cancellation. This
        # coordinator must preserve the TP until positive BA acceptance.
        for a in self.orders:
            aid = a.attempt_id
            task = self.cancels.get(aid)
            if (
                aid in self.cancel_wanted
                and a.post_started
                and self.unresolved(a)
                and aid not in self.cancel_ack
                and (task is None or task.done())
                and self.e.monotonic() - self.cancel_after.get(aid, -float("inf"))
                >= self.e.cfg.rdp_sm.obi.cancel_retry_sec
            ):
                self.cancels[aid] = asyncio.create_task(self.cancel(a), name="tp-ba-cancel-marked")

    def observed_quantity(self, a):
        oid = self.oid(a)
        fills = self.e.processor.economic_fills_for_order(oid)
        # MATCHED/MINED/RETRYING must stop further buying even before settlement.
        observed = {
            key: f
            for key, f in self.e.processor.fill_records.items()
            if f.maker_order_id == oid and f.status is not TradeState.FAILED
        }
        observed.update({(f.trade_id, oid): f for f in fills})
        return sum((Decimal(str(f.size)) for f in observed.values()), ZERO)

    def remaining(self, a):
        return max(ZERO, Decimal(str(a.quote.size)) - self.observed_quantity(a))

    def safety_reason(self):
        e = self.e
        snapshot = self.snapshot(active=True)
        if e.fatal or e.closed or self.owner.closed or not e.journal.ready:
            return e.fatal or "TP_BA_EXECUTION_CLOSED"
        if self.elapsed() >= self.cutoff:
            return "MARKET_SUBMISSION_CUTOFF"
        if e.check_repair_abandonment():
            return "HIGH_ENTRY_REPAIR_ABANDONED"
        if (
            e.inventory_mismatch is not None
            or e.merge_pending
            or e.gateway._client.pagination_failure
        ):
            return "TP_BA_EXECUTION_UNAVAILABLE"
        if e.monotonic() - e.time_anchor > e.cfg.risk.ws_stale_halt_s:
            return "OFFICIAL_CLOCK_STALE"
        if snapshot.generation != self.generation:
            return "WS_GENERATION_CHANGED"
        if (snapshot.yes.tick, snapshot.no.tick) != self.ticks:
            return "WS_TICK_CHANGED"
        if not snapshot.valid and snapshot.reason not in {"WS_BOOK_STALE", "WS_TOP_PENDING"}:
            return snapshot.reason
        if e.reads.network.continuation(self.group).close_active:
            return "NETWORK_EXECUTION_FAILURE"
        if any(
            e.processor.has_failed_fill(self.oid(old))
            or e.processor.has_unresolved_fill_key(self.oid(old))
            or self.oid(old) in e.processor.quarantined_orders
            for old in self.orders
        ):
            return "TP_BA_FILL_UNRESOLVED"
        return None

    def close_reason(self, a):
        e = self.e
        snapshot = self.snapshot(active=True)
        safety = self.safety_reason()
        if safety:
            return safety
        with self.lifecycle.feed.lock:
            reason = self.controller.episodes.active_reason(self.claim, snapshot)
        if reason:
            return reason
        if e.monotonic() >= self.deadline:
            return "ORDER_DEADLINE"
        state = exposure(e, retain_unknown_reservations=True)
        sign = 1 if a.quote.outcome == "YES" else -1
        if (
            sum((self.observed_quantity(old) for old in self.tp_orders), ZERO)
            != self.tp_matched_at_plan
        ):
            return "TP_BA_TP_FILLED_DURING_HANDOFF"
        # A TP fill can reduce the BA target while DELETE is still in flight.
        # Cancel the entire BA remainder; never resize/repost before full proof.
        if (
            state.execution * sign >= 0
            or self.remaining(a) > abs(state.execution) + self.sizing_overshoot
        ):
            return "TP_BA_TARGET_COVERED_OR_REVERSED"
        if abs(state.actual) <= SMALL:
            return "REPAIR_SMALL_EXPOSURE"
        return None

    async def run(self, goal):
        side, price, qty, snapshot = goal
        self.sizing_overshoot = max(ZERO, qty - abs(exposure(self.e).execution))
        with self.lifecycle.feed.lock:
            direction, signal = self.controller.select(snapshot)
            if direction != side or self.controller.episodes.selected is not self.episode:
                return False
            self.claim = self.controller.episodes.claim(signal)
            if self.claim is None:
                return False
        a = Placement(
            uuid.uuid4().hex,
            WindowQuote(
                self.e.meta.condition_id,
                snapshot.no.token_id if side == "NO" else snapshot.yes.token_id,
                side,
                Side.BUY,
                float(price),
                float(qty),
                f"{self.window_id}:ba-handoff:{len(self.owner.ba_attempts) + 1}",
            ),
        )
        self.e.attempts.append(a)
        self.orders.append(a)
        self.created.append(a)
        self.owner.ba_attempts.append(a)
        self.signal_times[a.attempt_id] = self.episode.signal_start_mono
        ep = self.e.repair_episode
        if ep is not None:
            ep["submissions"] += 1
            ep.setdefault("windows", []).append(a.quote.window_id)
            ep.setdefault("chain_windows", []).append(a.quote.window_id)
        sizing = {
            "policy": "opposite_ba_v1",
            "execution_role": "BA",
            "episode_id": self.episode.episode_id,
            "handoff_policy": "POST_BA_ACK_THEN_CANCEL_TP",
            "order_policy": "FIXED_ASK_MINUS_TICK"
            if getattr(self.controller.episodes, "fixed_ask_quote", False)
            else "OPPOSITE_BA_ANCHOR_BID",
            "original_signal_start_monotonic": self.episode.signal_start_mono,
            "original_order_deadline": self.episode.order_deadline,
            "anchor_bid_price": str(self.episode.anchor_bid_price),
            "quote_price": str(price),
            "quote_fixed": True,
            "price_tolerance": None,
            "max_price": None,
            "target_sizing_applied": False,
            "signed_target_delta": "0",
            "favorite_data_validity": "NOT_USED",
        }
        yes, no = self.e.economic_inventory()
        self.e.record(
            "sm_strategy_window",
            {
                "window_id": a.quote.window_id,
                "state": "REPAIR",
                "inventory_yes": str(yes),
                "inventory_no": str(no),
                "delta": str(yes - no),
                "quotes": [asdict(a.quote)],
                "books": {
                    name: {
                        **book.as_book(),
                        "bid_levels_count": len(book.bids),
                        "ask_levels_count": len(book.asks),
                    }
                    for name, book in (("YES", snapshot.yes), ("NO", snapshot.no))
                },
                "sizing": sizing,
                "decision_source": "BA_MAKER_BEFORE_TP_CANCEL",
                **self.evidence(),
            },
        )
        self.record(
            "signal", {**sizing, "quantity": str(qty), "price": str(price), **self.evidence()}
        )
        self.posts[a.attempt_id] = asyncio.create_task(
            self.submit(goal, a), name="tp-ba-maker-post"
        )
        polling = asyncio.create_task(self.poll(), name="tp-ba-proof")
        try:
            while True:
                self.changed.clear()
                self.lifecycle.drain_ba()
                for old in self.orders:
                    self.register(old)
                safety = self.safety_reason()
                if safety:
                    self.reason, self.stop_owner = safety, True
                    self.cancel_wanted.add(a.attempt_id)
                    self.cancel_tp()
                    self.cancel_marked()
                if self.post_done(a):
                    if a.outcome in {"REJECTED", "ABORTED"}:
                        self.reason = "BA_POST_" + a.outcome
                        break
                    if a.outcome != "ACK" and not self.accepted:
                        if not self.stop_owner:
                            self.reason = "BA_POST_UNKNOWN_KEEP_TP"
                        self.cancel_wanted.add(a.attempt_id)
                    else:
                        reason = self.close_reason(a)
                        if reason:
                            self.reason = reason
                            self.cancel_wanted.add(a.attempt_id)
                        self.cancel_tp()
                    self.cancel_marked()
                    if not self.unresolved(a):
                        if a.outcome == "UNKNOWN":
                            a.outcome = "VERIFIED"
                            self.record(
                                "unknown_terminal",
                                {
                                    "order_id": self.oid(a),
                                    "source": "OWNED_TERMINAL_AND_COMPLETE_TRADE_SET",
                                    "tp_preserved": not self.stop_owner,
                                    **self.evidence(),
                                },
                            )
                        break
                if self.e.monotonic() - self.last_progress > self.e.cfg.risk.ws_stale_halt_s:
                    self.reason = "TP_BA_ORDER_PROOF_UNRESOLVED"
                    break
                self.e.drain_network_events()
                await self.wait_changed()
        finally:
            self.closed = True
            await asyncio.gather(*self.posts.values(), return_exceptions=True)
            # An UNKNOWN late acceptance gets its own DELETE; it never cancels
            # a healthy TP merely because the request was sent.
            if a.post_started and self.unresolved(a):
                self.cancel_wanted.add(a.attempt_id)
            if self.accepted or self.stop_owner:
                self.cancel_tp()
            self.cancel_marked()
            await asyncio.gather(*self.cancels.values(), return_exceptions=True)
            polling.cancel()
            await asyncio.gather(polling, return_exceptions=True)
            self.controller.episodes.release(self.claim)
            self.record(
                "closed",
                {
                    "episode_id": self.episode.episode_id,
                    "reason": self.reason,
                    "ba_accepted": self.accepted,
                    "ba_outcome": a.outcome,
                    "tp_preserved": not (self.accepted or self.stop_owner),
                    "ba_terminal_proven": not self.unresolved(a),
                    **self.evidence(),
                },
            )
        # Unknown risk must leave through the parent's full close barrier.
        return self.accepted or self.stop_owner or (a.post_started and self.unresolved(a))


async def try_ba_before_tp_cancel(owner, controller):
    """Return True only when the TP owner must close/reconcile the whole group."""
    if not controller.restore_tp_enabled or not owner.created:
        return False
    if not hasattr(owner, "ba_attempts"):
        owner.ba_attempts = []
    if not all(owner.post_done(a) for a in owner.orders):
        return False
    if not any(a.outcome == "ACK" and owner.unresolved(a) for a in owner.created):
        return False
    with owner.lifecycle.feed.lock:
        episode = controller.tp_ba_signal(
            owner.lifecycle.feed.snapshot(ba_cfg=controller.ba_config)
        )
    if episode is None:
        return False
    previous_recovery = getattr(owner.e, "unknown_repair_recovery", None)
    actor = TPMakerHandoff(owner, controller, episode)
    try:
        goal, reason = actor.goal()
        reason = reason or actor.admission_reason(goal)
        if reason:
            owner.wait("KEEP_TP_" + reason, goal)
            return False
        controller.tp_handled_ba[episode.episode_id] = episode.order_deadline
        owner.record(
            "ba_handoff",
            {
                "episode_id": episode.episode_id,
                "policy": "POST_BA_ACK_THEN_CANCEL_TP",
                "stage": "ATTEMPT_BA_KEEP_TP",
                **owner.evidence(),
            },
        )
        close = await actor.run(goal)
        if close:
            owner.handoff_episode = episode.episode_id
            owner.handoff_reason = "OPPOSITE_BA_HANDOFF" if actor.accepted else actor.reason
        return close
    finally:
        owner.e.unknown_repair_recovery = previous_recovery


def role_result(executor, attempts, safe):
    """Allocate PnL only to the actual FIFO closing order, including overlap."""
    ids = {a.order_id or a.expected_order_id for a in attempts}
    all_fills = executor.economic_fills()
    fills = [f for f in all_fills if f.maker_order_id in ids]
    pairs = [
        (old, close, qty)
        for old, close, qty in fifo_pairs(
            all_fills, executor.fill_observed_at, executor.meta.yes.token_id
        )
        if close.maker_order_id in ids
    ]
    paired = fifo_paired_quantities(
        all_fills, executor.fill_observed_at, executor.meta.yes.token_id
    )
    pnl = sum(
        (
            qty * (1 - Decimal(str(old.price)) - Decimal(str(close.price)))
            for old, close, qty in pairs
        ),
        ZERO,
    )
    return {
        "submitted_orders": sum(a.post_started for a in attempts),
        "filled_orders": len({f.maker_order_id for f in fills}),
        "filled_shares": str(sum((Decimal(str(f.size)) for f in fills), ZERO)),
        "paired_shares": str(sum((qty for _, _, qty in pairs), ZERO)),
        "unpaired_shares": str(
            sum(
                (
                    Decimal(str(f.size)) - paired.get((f.trade_id, f.maker_order_id), ZERO)
                    for f in fills
                ),
                ZERO,
            )
        ),
        "pair_pnl_usdc": str(pnl) if safe else None,
        "pnl_verified": bool(safe),
        "cost_basis": "ACTUAL_FIFO_CLOSING_LEGS",
    }


def record_ba_handoff_result(owner, safe):
    if owner.ba_attempts:
        owner.e.record(
            "sm_tp_ba_handoff_result",
            {
                "window_id": owner.window_id,
                "execution_role": "BA",
                "handoff_policy": "POST_BA_ACK_THEN_CANCEL_TP",
                "order_ids": [a.order_id or a.expected_order_id for a in owner.ba_attempts],
                **role_result(owner.e, owner.ba_attempts, safe),
            },
        )
