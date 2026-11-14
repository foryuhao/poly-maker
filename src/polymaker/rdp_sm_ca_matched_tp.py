"""One fixed opposite maker order after a positively observed owned CA fill.

This owner runs inside the opening window. MATCHED evidence admits only its
bounded companion, never general NORMAL/BA admission or a settlement proof.
The original close barrier runs after the companion has drained.
"""
from __future__ import annotations

import asyncio
import math
import uuid
from dataclasses import asdict
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from types import SimpleNamespace

from polymaker.domain import Side, TradeState
from polymaker.rdp_sm_execution import (
    MIN_MAKER_BUY_SHARES,
    RDP_TOLERANCE,
    Placement,
    WindowQuote,
    maker_buy_size,
)
from polymaker.rdp_sm_sl import SLExecution
from polymaker.rdp_sm_sl_ledger import fifo_residual
from polymaker.state.tracker import fill_cost

ZERO = Decimal(0)


def tp_buy_size(quantity, minimum, maximum):
    """Use BA maker sizing, with at most the shared quantity tolerance extra."""
    qty = maker_buy_size(ZERO, min(quantity, maximum), minimum)
    if qty > maximum or qty - quantity > RDP_TOLERANCE:
        return None
    return qty


def fixed_tp_price(entry, profit, book):
    """Keep the profit ceiling; fall back to Bid only for a crossing TP target."""
    target = ((1-entry-profit)/book.tick).to_integral_value(rounding=ROUND_FLOOR)*book.tick
    if not book.tick <= target <= 1-book.tick:
        return None, "TP_MAKER_PRICE_UNAVAILABLE"
    fallback = target >= book.best_ask
    price = book.best_bid if fallback else target
    if (price is None or not book.tick <= price <= target or price >= book.best_ask
            or price % book.tick):
        return None, "TP_MAKER_PRICE_UNAVAILABLE"
    return price, "BEST_BID" if fallback else "PROFIT_TARGET"


async def drain_tp_posts(owner, *, expired):
    """At expiry DELETE known signed IDs even while a POST response is pending.

    A DELETE before POST returns cannot prove a late acceptance absent. Shared
    submit clears that provisional cancel ACK; the final close path cancels again.
    """
    if expired:
        for a in owner.created:
            if a.post_started and owner.unresolved(a):
                owner.cancel_wanted.add(a.attempt_id)
                owner.cancels[a.attempt_id] = asyncio.create_task(owner.cancel(a))
    await asyncio.gather(*owner.posts.values(), *owner.cancels.values(), return_exceptions=True)


class CAMatchedTP(SLExecution):
    mode = "CA_MATCHED_TP"

    def __init__(self, lifecycle, intent, opening, post_done):
        e = lifecycle.executor
        controller = SimpleNamespace(executor=e, lifecycle=lifecycle,
                                     cfg=e.cfg.rdp_sm.repair, phase="REPAIR")
        controller.elapsed = lambda: (e.official_time + e.monotonic() - e.time_anchor
                                     - int(e.slug.rsplit("-", 1)[1]))
        super().__init__(controller, intent.window_id, inherited=opening, parent_done=post_done)
        self.intent = intent
        self.opening = tuple(opening)
        self.cfg = e.cfg.rdp_sm.ca_matched_tp
        self.target = None
        self.initial_cash = max(ZERO, Decimal(str((lifecycle.account or {}).get(
            "basic_spendable_budget_usd", 0))) - sum((a.maximum for a in opening), ZERO))
        self.fill_time = None
        self.expires_at = None
        self.ba_attempts = []
        self.handoff_episode = None
        self.ba_controller = None
        self.direction_proof_key = None
        if self.cfg.restore_after_ba:
            from polymaker.rdp_sm_opposite_ba import OppositeBAController

            self.ba_controller = OppositeBAController(e, lifecycle)

    def evidence(self):
        return {"opening_window": self.window_id, "elapsed_s": self.elapsed(),
                "profit_per_share": str(self.cfg.profit_per_share),
                "ttl_sec": self.cfg.ttl_sec, "expires_at_monotonic": self.expires_at,
                "committed_usdc": str(self.e.committed())}

    def tp_expired(self):
        return self.expires_at is not None and self.e.monotonic() >= self.expires_at

    def on_post_start(self, attempt, goal):
        started = self.e.monotonic()
        self.expires_at = started + self.cfg.ttl_sec
        for opening in self.opening:
            oid = self.oid(opening)
            self.expires_at = min(self.expires_at,
                                  self.e.ca_tp_deadlines.get(oid, self.expires_at))
        for opening in self.opening:
            self.e.ca_tp_deadlines[self.oid(opening)] = self.expires_at
        self.record("lease_started", {"order_id": self.oid(attempt),
                    "post_started_monotonic": started,
                    "opening_order_ids": [self.oid(a) for a in self.opening],
                    **self.evidence()})

    def opening_fills(self):
        ids = {self.oid(a) for a in self.opening}
        return [f for key, f in self.e.processor.fill_records.items()
                if f.maker_order_id in ids
                and f.status in {TradeState.MATCHED, TradeState.MINED, TradeState.CONFIRMED}
                and key not in self.e.processor.quarantined_fill_keys
                and not self.e.processor.has_failed_fill(f.maker_order_id)
                and not self.e.processor.has_unresolved_fill_key(f.maker_order_id)
                and f.maker_order_id not in self.e.processor.quarantined_orders
                and f.market_id == self.e.meta.condition_id and f.our_side is Side.BUY
                and f.token_id == self.opening[0].quote.token_id]

    def opening_problem(self):
        return any(self.e.processor.has_failed_fill(self.oid(a))
                   or self.e.processor.has_unresolved_fill_key(self.oid(a))
                   or self.oid(a) in self.e.processor.quarantined_orders for a in self.opening
                   ) or any(f.status is TradeState.RETRYING
                            for f in self.e.processor.fill_records.values()
                            if f.maker_order_id in {self.oid(a) for a in self.opening})

    def snapshot(self):
        return self.lifecycle.feed.snapshot(ba_cfg=self.e.cfg.rdp_sm.ba)

    def prove_ba_direction(self):
        """Publish this opening owner's actual FIFO direction before TP closes.

        A TP submitted on MATCHED can join opening ownership before CONFIRMED
        creates the episode. Keep its dual-order close barrier, while proving
        the opening side/time from economic fills, never the pair's quote order.
        """
        e, ep = self.e, self.e.repair_episode
        if (self.closed or not self.cfg.restore_after_ba or ep is None
                or ep["opening_window"] != self.window_id or "source_lots" in ep
                or len(self.opening) != 1 or len(self.created) != 1
                or not self.parent_done.is_set() or self.opening_problem()
                or any(a.outcome != "ACK" or not self.post_done(a) for a in self.orders)
                or ep.get("normal_entry_side") not in {None, self.intent.direction}):
            return False
        opening = self.opening[0]
        side = self.intent.direction
        token = e.meta.yes.token_id if side == "YES" else e.meta.no.token_id
        if (opening.quote.outcome != side or opening.quote.token_id != token
                or opening.quote.window_id != self.window_id or not opening.post_started
                or self.oid(opening) not in e.known
                or self.created[0].quote.outcome == side):
            return False
        y, n = e.economic_inventory()
        if y == n or (y > n) != (side == "YES"):
            return False
        fills = e.economic_fills()
        lots = fifo_residual(fills, e.fill_observed_at, e.meta.yes.token_id)
        if (not lots or sum((Decimal(str(f.size)) for f in lots), ZERO) != abs(y-n)
                or any(f.maker_order_id != self.oid(opening) or f.token_id != token
                       or f.market_id != e.meta.condition_id or f.our_side is not Side.BUY
                       for f in lots)):
            return False
        owned = [f for f in fills if f.maker_order_id == self.oid(opening)]
        stamps = [e.fill_observed_at.get((f.trade_id, f.maker_order_id), {}) for f in owned]
        if not stamps or any("fill_monotonic" not in s or "elapsed" not in s or "source" not in s
                             or not math.isfinite(s["fill_monotonic"])
                             or not 0 <= s["fill_monotonic"] <= e.monotonic() for s in stamps):
            return False
        first = min(stamps, key=lambda s: s["fill_monotonic"])
        qty = sum((Decimal(str(f.size)) for f in owned), ZERO)
        cost = sum((fill_cost(f) for f in owned), ZERO)
        ep.update(side=side, normal_entry_side=side, ca_tp_direction_owner=self.window_id,
                  first_fill_monotonic=first["fill_monotonic"], first_fill_elapsed=first["elapsed"],
                  first_fill_time_source=first["source"], opening_monotonic=first["fill_monotonic"],
                  original_qty=str(qty), original_cost=str(cost))
        chain = ep.setdefault("chain_windows", list(ep["windows"]))
        for attempt in (*self.opening, *self.created):
            if attempt.quote.window_id not in chain:
                chain.append(attempt.quote.window_id)
        key = (id(ep), side, qty, cost, abs(y-n), first["fill_monotonic"])
        if key != self.direction_proof_key:
            self.direction_proof_key = key
            self.record("direction_proven", {"opening_window": self.window_id,
                "side": side, "opening_order_id": self.oid(opening),
                "tp_order_id": self.oid(self.created[0]), "actual_delta": str(y-n),
                "first_fill_monotonic": first["fill_monotonic"],
                "source": "OWNED_ECONOMIC_OPENING_AND_FIFO_RESIDUAL",
                "dual_order_close_barrier_retained": True})
        return True

    def goal(self):
        fills = self.opening_fills()
        quantity = sum((Decimal(str(f.size)) for f in fills), ZERO)
        if not fills:
            return None, "WAIT_CA_MATCHED"
        if self.fill_time is None:
            self.fill_time = min(self.e.fill_observed_at[(f.trade_id, f.maker_order_id)][
                "observed_monotonic"] for f in fills)
        entry = sum((fill_cost(f) for f in fills), ZERO)/quantity
        if entry >= 1-self.cfg.profit_per_share:
            return None, "ENTRY_PRICE_TOO_HIGH"
        minimum = max(MIN_MAKER_BUY_SHARES, Decimal(str(self.e.meta.min_order_size)))
        maximum = max(sum((Decimal(str(a.quote.size)) for a in self.opening), ZERO),
                      quantity.quantize(Decimal('.01'), rounding=ROUND_CEILING))
        if self.e.cfg.rdp_sm.sizing_mode != "fixed_risk":
            maximum = min(self.e.cfg.rdp_sm.max_order_size, maximum)
        qty = tp_buy_size(quantity, minimum, maximum)
        if qty is None:
            return None, "WAIT_MINIMUM_MATCHED_QUANTITY"
        snapshot = self.snapshot()
        if not snapshot.valid:
            return None, snapshot.reason
        side = "NO" if self.intent.direction == "YES" else "YES"
        book = snapshot.no if side == "NO" else snapshot.yes
        price, source = fixed_tp_price(entry, self.cfg.profit_per_share, book)
        if price is None:
            return None, source
        self.price_source = source
        return (side, price, qty, snapshot), None

    def admission_reason(self, goal, *, reserved=None):
        side, price, qty, snapshot = goal
        e = self.e
        if self.closed or e.closed or e.fatal:
            return e.fatal or "CA_MATCHED_TP_CLOSED"
        if self.tp_expired():
            return "CA_MATCHED_TP_EXPIRED"
        if not self.start <= self.elapsed() < self.cutoff:
            return "MARKET_SUBMISSION_CUTOFF"
        if self.opening_problem():
            return "CA_OPENING_FILL_UNRESOLVED"
        if not e.journal.ready or e.merge_pending or e.inventory_mismatch is not None:
            return "CA_MATCHED_TP_EXECUTION_UNAVAILABLE"
        if e.gateway._client.pagination_failure:
            return e.gateway._client.pagination_failure
        if self.lifecycle.feed.opposite_ba is None:
            return "OPPOSITE_BA_UNAVAILABLE"
        if any(a.outcome not in {"ACK", "VERIFIED"} for a in self.opening) or not self.parent_done.is_set():
            return "CA_OPENING_POST_UNRESOLVED"
        if not self.health().allowed:
            return self.health().reason
        current = self.snapshot()
        if not current.valid:
            return current.reason
        if current.generation != self.intent.snapshot.generation:
            return "WS_GENERATION_CHANGED"
        book = current.no if side == "NO" else current.yes
        old_book = snapshot.no if side == "NO" else snapshot.yes
        if book.tick != old_book.tick or price % book.tick or price >= book.best_ask:
            return "TP_MAKER_PRICE_UNAVAILABLE"
        fills = self.opening_fills()
        quantity = sum((Decimal(str(f.size)) for f in fills), ZERO)
        minimum = max(MIN_MAKER_BUY_SHARES, Decimal(str(e.meta.min_order_size)))
        if (qty - quantity > RDP_TOLERANCE or qty < minimum or qty % Decimal(".01")
                or (e.cfg.rdp_sm.sizing_mode != "fixed_risk" and qty > e.cfg.rdp_sm.max_order_size)):
            return "CA_MATCHED_QUANTITY_CHANGED"
        cost = sum((fill_cost(f) for f in fills), ZERO)
        if quantity <= 0 or price > 1-cost/quantity-self.cfg.profit_per_share:
            return "CA_MATCHED_PRICE_CHANGED"
        # Only this owner's exact unsent INTENT receives its reservation back.
        if reserved is not None and not (
            reserved in self.created and any(a is reserved for a in e.attempts)
            and reserved.outcome == "INTENT" and not reserved.post_started
            and reserved.quote.outcome == side and Decimal(str(reserved.quote.price)) == price
            and Decimal(str(reserved.quote.size)) == qty
        ):
            return "CA_MATCHED_TP_RESERVATION_CHANGED"
        credit = reserved.maximum if reserved is not None else ZERO
        if e.committed()-credit+price*qty > e.capital_limit or price*qty > self.initial_cash:
            return "CA_MATCHED_TP_INSUFFICIENT_FUNDS"
        if e.monotonic()-e.time_anchor > e.cfg.risk.ws_stale_halt_s:
            return "OFFICIAL_CLOCK_STALE"
        return None

    def register(self, a):
        super().register(a)
        if a not in self.created or not a.post_started or a.outcome in {"REJECTED", "ABORTED"}:
            return
        # A later FAILED opening must leave a companion-only residual owned by
        # the ordinary FIFO/REPAIR path, rather than orphaning its inventory.
        opening = self.e.ba_opening
        if opening is not None and self.oid(a) not in opening["ids"]:
            opening["ids"] = [*opening["ids"], self.oid(a)]
            opening["quotes"] = (*opening["quotes"], a.quote)
        ep = self.e.repair_episode
        if ep is not None and ep["opening_window"] == self.window_id:
            ep["dual_normal"] = True

    def create(self, goal):
        side, price, qty, snapshot = goal
        quote = WindowQuote(self.e.meta.condition_id,
                            snapshot.no.token_id if side == "NO" else snapshot.yes.token_id,
                            side, Side.BUY, float(price), float(qty), self.window_id+":ca-tp")
        a = Placement(uuid.uuid4().hex, quote)
        self.e.attempts.append(a)
        self.orders.append(a)
        self.created.append(a)
        self.latest = a
        self.target = goal
        self.signal_times[a.attempt_id] = self.fill_time
        self.record("signal", {"window_id": quote.window_id, "side": side,
                    "price": str(price), "quantity": str(qty),
                    "fill_observed_monotonic": self.fill_time, "fixed_quote": True,
                    "notional_policy": "MATCHED_SHARES_ONLY_NO_NOTIONAL_UPSIZE",
                    "price_source": self.price_source,
                    **self.evidence()})
        self.posts[a.attempt_id] = asyncio.create_task(self.submit(goal, a), name="ca-matched-tp-post")

    def ba_handoff(self):
        if not self.e.cfg.rdp_sm.repair.ba_enabled or self.fill_time is None:
            return False
        feed = self.lifecycle.feed
        with feed.lock:
            if feed.opposite_ba is None:
                return False
            opposite = "NO" if self.intent.direction == "YES" else "YES"
            signal = feed.opposite_ba.evaluate(self.snapshot(), outcome=opposite)
            ep = feed.opposite_ba.selected
            eligible = bool(signal.direction == opposite and ep is not None
                            and ep.signal_start_mono >= self.fill_time)
            if eligible:
                self.record("ba_handoff", {"episode": asdict(ep), **self.evidence()})
            return eligible

    async def run(self):
        polling = asyncio.create_task(self.poll(), name="ca-matched-tp-proof")
        reason = None
        try:
            while reason is None:
                self.changed.clear()
                # The TP has its own lease, separate from the CA opening window.
                # Keep detector audit buffers draining while
                # waiting; otherwise valid BA handoff evidence can overflow.
                self.lifecycle.drain_ba()
                for a in self.orders:
                    self.register(a)
                current = self.snapshot()
                reason = next((name for name, failed in (
                    (self.e.fatal, bool(self.e.fatal)),
                    ("CA_MATCHED_TP_CLOSED", self.closed or self.e.closed),
                    ("ASYNC_JOURNAL_NOT_READY", not self.e.journal.ready),
                    ("MARKET_SUBMISSION_CUTOFF", self.elapsed() >= self.cutoff),
                    ("CA_MATCHED_TP_EXPIRED", self.tp_expired()),
                    ("CA_OPENING_FILL_UNRESOLVED", self.opening_problem()),
                    ("WS_GENERATION_CHANGED", current.generation != self.intent.snapshot.generation),
                    (current.reason, not current.valid and current.reason not in
                     {"WS_BOOK_STALE", "WS_TOP_PENDING"}),
                    ("NETWORK_EXECUTION_FAILURE", self.health().close_active),
                ) if failed), None)
                if reason:
                    break
                goal, wait = self.goal()
                if wait == "ENTRY_PRICE_TOO_HIGH":
                    reason = wait
                    break
                if self.ba_controller is not None:
                    from polymaker.rdp_sm_tp_ba_handoff import try_ba_before_tp_cancel

                    handoff = (self.prove_ba_direction()
                               and await try_ba_before_tp_cancel(self, self.ba_controller))
                else:
                    handoff = self.ba_handoff()
                if handoff:
                    reason = self.handoff_reason if self.ba_controller is not None else "OPPOSITE_BA_HANDOFF"
                    break
                if self.created:
                    a = self.created[0]
                    if self.post_done(a) and (a.outcome != "ACK" or not self.unresolved(a)):
                        reason = "TP_ORDER_TERMINAL" if a.outcome == "ACK" else "TP_POST_"+a.outcome
                        break
                elif goal is not None and self.parent_done.is_set():
                    failure = self.admission_reason(goal)
                    if failure:
                        self.wait(failure, goal)
                        if failure in {"CA_MATCHED_TP_INSUFFICIENT_FUNDS", "OPPOSITE_BA_UNAVAILABLE"}:
                            reason = failure
                            break
                    else:
                        self.create(goal)
                elif all(not self.unresolved(a) for a in self.opening):
                    # No fill or a terminal sub-minimum partial: the ordinary
                    # confirmation/BA path still owns its true residual.
                    if not self.opening_fills() or wait == "WAIT_MINIMUM_MATCHED_QUANTITY":
                        reason = wait
                        break
                await self.wait_changed()
        finally:
            self.closed = True  # Native POST guards see closure before any await.
            try:
                await drain_tp_posts(self, expired=reason == "CA_MATCHED_TP_EXPIRED")
                for a in self.created:
                    self.register(a)
                    if (a.post_started and self.unresolved(a)
                            and a.attempt_id not in self.cancel_ack):
                        try:
                            if reason == "OPPOSITE_BA_HANDOFF":
                                async def cancel_handoff(attempt=a):
                                    try:
                                        await self.cancel(attempt)
                                    finally:
                                        cache = self.e.shared_reads
                                        if cache is not None:
                                            oid = self.oid(attempt)
                                            after = cache.order_writes.get(oid, cache.last_write)
                                            # Queue the detail before waking the existing lanes.
                                            # MATCHED openings need proof even without a repair episode.
                                            cache.service.track_orders({oid: after})
                                            requested = cache.service.request_repair_refresh(
                                                ("orders", "account"))
                                            self.record("proof_refresh", {"order_id": oid,
                                                "operation_after_monotonic": after,
                                                "requested": requested})
                                    return self.cancel_ack.get(attempt.attempt_id)

                                await self.e._cancel_tp_for_ba_handoff(self.oid(a), cancel_handoff)
                            else:
                                await self.cancel(a)
                        except Exception as exc:
                            self.record("cancel_error", {"order_id": self.oid(a),
                                                       "error": type(exc).__name__})
            finally:
                polling.cancel()
                await asyncio.gather(polling, return_exceptions=True)
            self.reason = reason
            self.record("closed", {"reason": reason, "orders": [asdict(a) for a in self.created],
                                    "final_close_proof_required": True, **self.evidence()})
