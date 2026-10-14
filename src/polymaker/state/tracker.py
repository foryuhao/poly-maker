"""Order/trade lifecycle processing over the StateStore.

Consumes *normalized* user-stream events (the wire-format extraction lives in
userstream/, so this is unit-testable with synthetic events) and drives the
state machine from the README:

    Trade:  MATCHED -> MINED -> CONFIRMED
                 └──────────-> FAILED (roll back the optimistic fill, reconcile)

Because we quote post-only, we are always the maker; `our_side` is our side of
each match. We apply the fill optimistically at MATCHED and reverse it on FAILED.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from decimal import Decimal

from polymaker.domain import Fill, OpenOrder, OrderState, Side, TradeState
from polymaker.logging import get_logger
from polymaker.research.capture import CausalCapture, exchange_us
from polymaker.state.store import StateStore

log = get_logger("state.tracker")


def within_tolerance(left, right, tolerance: Decimal) -> bool:
    left, right = Decimal(str(left)), Decimal(str(right))
    return left.is_finite() and right.is_finite() and abs(left - right) <= tolerance


def exceeds_tolerance(actual, expected, tolerance: Decimal) -> bool:
    actual, expected = Decimal(str(actual)), Decimal(str(expected))
    return (not actual.is_finite() or not expected.is_finite()
            or actual - expected > tolerance)


@dataclass(frozen=True, slots=True)
class TradeEvent:
    token_id: str
    our_side: Side
    price: float
    size: float
    trade_id: str
    status: TradeState
    ts: float
    maker_order_id: str | None = None
    market_id: str | None = None


@dataclass(frozen=True, slots=True)
class OrderEvent:
    order_id: str
    token_id: str
    side: Side
    price: float
    remaining_size: float  # original - matched
    is_cancel: bool = False
    original_size: float = 0.0
    size_matched: float = 0.0
    status: str = ""
    associate_trades: tuple[str, ...] = ()
    market_id: str | None = None


class UserEventProcessor:
    """Applies normalized trade/order events to the store."""

    # RDP overrides these; other strategies keep their existing precision.
    quantity_tolerance = Decimal("0.000001")
    price_tolerance = Decimal("0.000001")
    single_fill_tolerance = Decimal(0)

    def __init__(
        self,
        store: StateStore,
        on_change: Callable[[str], None] | None = None,
        on_fill: Callable[[Fill], None] | None = None,
        on_reconcile: Callable[[str], None] | None = None,
        on_maker_fill: Callable[[TradeEvent], None] | None = None,
        research: CausalCapture | None = None,
        fill_truth_ready: Callable[[str], bool] | None = None,
    ) -> None:
        self._store = store
        self._on_change = on_change or (lambda _cid: None)
        self._on_fill = on_fill or (lambda _fill: None)
        self._on_reconcile = on_reconcile or (lambda _oid: None)
        self._on_maker_fill = on_maker_fill or (lambda _ev: None)
        self._research = research
        self._fill_truth_ready = fill_truth_ready
        self.fill_records: dict[tuple[str, str], TradeEvent] = {}
        self.applied_fill_keys: set[tuple[str, str]] = set()
        self.quarantined_fill_keys: set[tuple[str, str]] = set()
        self.quarantined_orders: set[str] = set()
        self._owned = {str(x["order_id"]) for x in store.owned_orders()}
        self._pending: set[tuple[str, Side]] = set()
        self._pending_remaining = 0
        self._pending_events: list[tuple[TradeEvent, int | None, int | None, int | None]] = []
        self._pending_malformed_order_ids: set[str] = set()
        self._pending_limit = 128
        self._pending_overflow = False

    def arm_pending(self, token_id: str, side: Side) -> None:
        self.arm_pending_many([(token_id, side)])

    def arm_pending_many(self, orders: list[tuple[str, Side]]) -> None:
        if self._pending:
            raise RuntimeError("placement capture already armed")
        self._pending = set(orders)
        self._pending_remaining = len(orders)
        self._pending_events = []
        self._pending_overflow = False

    def discard_pending(self) -> None:
        self._pending, self._pending_events, self._pending_remaining = set(), [], 0
        self._pending_overflow = False
        self._pending_malformed_order_ids.clear()

    def owned_record(self, order_id: str) -> dict[str, object] | None:
        return self._store.owned_order(order_id)

    def owned_candidates(self, token_id: str, side: Side) -> list[str]:
        return [str(r["order_id"]) for r in self._store.owned_orders()
                if r["token_id"] == token_id and r["side"] == side.value]

    def tracked_trade_qty(self, order_id: str) -> float:
        return sum(self.fill_records[key].size for key in self.applied_fill_keys
                   if key[1] == order_id and key in self.fill_records
                   and key not in self.quarantined_fill_keys
                   and self.fill_records[key].status is not TradeState.FAILED)

    def economic_fills_for_order(self, order_id: str) -> list[TradeEvent]:
        return [self.fill_records[key] for key in self.applied_fill_keys
                if key[1] == order_id and key in self.fill_records
                and key not in self.quarantined_fill_keys
                and self.fill_records[key].status is not TradeState.FAILED]

    def economic_fills_for_orders(self, order_ids: set[str]) -> list[TradeEvent]:
        return [fill for oid in order_ids for fill in self.economic_fills_for_order(oid)]

    def has_quarantine_for_condition(self, condition_id: str) -> bool:
        return any((row := self._store.owned_order(oid)) is not None
                   and row["condition_id"] == condition_id for oid in self.quarantined_orders)

    def has_unresolved_fill_key(self, order_id: str) -> bool:
        return any(oid == order_id for _tid, oid in self.quarantined_fill_keys)

    def has_failed_fill(self, order_id: str) -> bool:
        return any(oid == order_id and ev.status is TradeState.FAILED
                   for (_trade_id, oid), ev in self.fill_records.items())

    @staticmethod
    def _advanced_status(prior: TradeEvent | None, event: TradeEvent) -> TradeEvent:
        """Keep the furthest observed non-failed lifecycle state on replay."""
        if prior is None or event.status is TradeState.FAILED:
            return event
        if prior.status is TradeState.FAILED:
            return prior
        rank = {TradeState.RETRYING: 1.5, TradeState.MATCHED: 1,
                TradeState.MINED: 2, TradeState.CONFIRMED: 3}
        return replace(event, status=max((prior.status, event.status), key=rank.__getitem__),
                       ts=prior.ts if prior.ts > 0 else event.ts)

    def quarantine_order(self, order_id: str, reason: str, *, notify: bool = True) -> None:
        if order_id not in self._owned:
            return
        self.quarantined_orders.add(order_id)
        self._store.mark_owned_order_unresolved(order_id, True)
        self._order_observe(order_id, {"transition": "QUARANTINED", "reason": reason})
        log.error("trade_quarantined", order_id=order_id, reason=reason)
        if notify:
            self._on_reconcile(order_id)

    def on_malformed_trade(self, order_ids: set[str] | list[str], reason: str) -> None:
        """Quarantine only exact registered maker IDs from an unparseable event."""
        for order_id in set(order_ids):
            if not isinstance(order_id, str) or not order_id.strip():
                continue
            order_id = order_id.strip()
            if order_id in self._owned:
                self.quarantine_order(order_id, reason)
            elif self._pending:
                # A fill can arrive before POST returns its ID. Hold only the
                # raw exact ID; registration will establish ownership later.
                self._pending_malformed_order_ids.add(order_id)

    def register_order(self, order_id: str, condition_id: str, token_id: str,
                       side: Side, qty: float, price: float, source: str = "engine") -> None:
        self._store.register_owned_order(order_id, condition_id, token_id, side, qty, price, source)
        self._owned.add(order_id)
        self._order_observe(order_id, {"transition": "REGISTERED", "token_id": token_id,
            "side": side.value, "qty": qty, "price": price, "source": source})
        log.info("owned_order_registered", order_id=order_id, token=token_id[:12], source=source)
        if order_id in self._pending_malformed_order_ids:
            self._pending_malformed_order_ids.discard(order_id)
            self.quarantine_order(order_id, "malformed WS trade referenced this owned order")
        events = self._pending_events
        for ev, receipt_us, receipt_ns, generation in events:
            if ev.maker_order_id == order_id:
                self.on_trade(ev, condition_id, received_at_us=receipt_us,
                    received_monotonic_ns=receipt_ns,
                    connection_generation=generation)
        self._pending_remaining = max(0, self._pending_remaining - 1)
        if self._pending_overflow:
            self.quarantine_order(order_id, "pending maker-event buffer overflow")
        if self._pending_remaining == 0:
            self.discard_pending()

    def on_trade(self, ev: TradeEvent, condition_id: str, *, source: str = "WS",
                 received_at_us: int | None = None,
                 received_monotonic_ns: int | None = None,
                 connection_generation: int | None = None) -> None:
        oid = ev.maker_order_id
        if not oid or oid not in self._owned:
            if self._pending:
                if len(self._pending_events) < self._pending_limit:
                    self._pending_events.append((ev, received_at_us,
                        received_monotonic_ns, connection_generation))
                else:
                    self._pending_overflow = True
            log.debug("foreign_trade_ignored", trade_id=ev.trade_id, order_id=oid)
            return
        key = (ev.trade_id, oid)
        owned = self._store.owned_order(oid)
        assert owned is not None
        try:
            expected_side = Side(str(owned["side"]))
            expected_qty = float(owned["submitted_qty"])
            expected_price = float(owned["submitted_price"])
        except (ValueError, TypeError, KeyError):
            expected_side, expected_qty, expected_price = None, -1.0, float("nan")
        observed_prior = sum(Decimal(str(event.size)) for prior_key, event in self.fill_records.items()
                             if prior_key[1] == oid and prior_key in self.applied_fill_keys
                             and prior_key not in self.quarantined_fill_keys
                             and event.status is not TradeState.FAILED and prior_key != key)
        mismatch = (
            ev.token_id != owned["token_id"]
            or ev.our_side is not expected_side
            or condition_id != owned["condition_id"]
            or (ev.market_id is not None and ev.market_id != owned["condition_id"])
            or ev.size < 0 or exceeds_tolerance(ev.size, expected_qty, self.single_fill_tolerance)
            or (ev.our_side is Side.BUY and exceeds_tolerance(ev.price, expected_price, self.price_tolerance))
            or (ev.our_side is Side.SELL and exceeds_tolerance(expected_price, ev.price, self.price_tolerance))
            or exceeds_tolerance(observed_prior + Decimal(str(ev.size)), expected_qty, self.quantity_tolerance)
        )
        prior = self.fill_records.get(key)
        if prior is not None and (prior.token_id != ev.token_id or prior.our_side is not ev.our_side
                                  or not within_tolerance(prior.price, ev.price, self.price_tolerance)
                                  or not within_tolerance(prior.size, ev.size, self.quantity_tolerance)):
            mismatch = True
        if mismatch:
            self.fill_records.setdefault(key, ev)
            self.quarantined_fill_keys.add(key)
            self.quarantined_orders.add(oid)
            self._store.mark_owned_order_unresolved(oid, True)
            self._fill_observe(ev, condition_id, source, received_at_us,
                connection_generation, "QUARANTINED", received_monotonic_ns)
            log.error("trade_quarantined", trade_id=ev.trade_id, order_id=oid,
                      reason="maker economics contradict registered order")
            if source == "WS":
                self._on_reconcile(oid)
            return
        if ev.size == 0:
            return
        log.info("owned_maker_fill_seen", trade_id=ev.trade_id, order_id=oid,
                 token=ev.token_id[:12], side=ev.our_side.value, qty=ev.size,
                 price=ev.price, source=source, status=ev.status.value)
        if source == "REST" and ev.status in (TradeState.MINED, TradeState.CONFIRMED) and key not in self.applied_fill_keys:
            matched = replace(ev, status=TradeState.MATCHED)
            self.on_trade(matched, condition_id, source="REST",
                received_at_us=received_at_us,
                received_monotonic_ns=received_monotonic_ns)
            self.fill_records[key] = replace(ev, ts=matched.ts)
            self._fill_observe(ev, condition_id, source, received_at_us,
                connection_generation, "ACCEPTED", received_monotonic_ns)
            if ev.status is TradeState.CONFIRMED:
                self._store.clear_inflight(ev.token_id)
            return
        if ev.status is TradeState.MATCHED:
            if key in self.applied_fill_keys:
                self.fill_records[key] = self._advanced_status(self.fill_records.get(key), ev)
                self._fill_observe(ev, condition_id, source, received_at_us,
                    connection_generation, "DUPLICATE", received_monotonic_ns)
                return
            fill = Fill(ev.token_id, ev.our_side, ev.price, ev.size,
                        f"{len(ev.trade_id)}:{ev.trade_id}{oid}", ev.ts, is_maker=True)
            if not self._store.apply_fill(fill):
                # duplicate at the persistent layer (replay after CONFIRMED or
                # across restarts): restore evidence in this session but apply
                # NO economic side effects.
                prior = self.fill_records.get(key)
                self.fill_records[key] = self._advanced_status(prior, ev)
                self.applied_fill_keys.add(key)
                self._fill_observe(ev, condition_id, source, received_at_us,
                    connection_generation, "RESTORED", received_monotonic_ns)
                return
            self._store.mark_inflight(ev.token_id)
            prior = self.fill_records.get(key)
            self.fill_records[key] = self._advanced_status(prior, ev)
            self.applied_fill_keys.add(key)
            self._fill_observe(ev, condition_id, source, received_at_us,
                connection_generation, "ACCEPTED", received_monotonic_ns)
            log.info("owned_maker_fill_applied", trade_id=ev.trade_id, order_id=oid,
                     token=ev.token_id[:12], side=ev.our_side.value, qty=ev.size,
                     price=ev.price, source=source, status=ev.status.value)
            self._on_fill(fill)
            self._on_maker_fill(ev)
            self._on_change(condition_id)

        elif ev.status in (TradeState.CONFIRMED, TradeState.MINED):
            prior = self.fill_records.get(key)
            self.fill_records[key] = (replace(ev, ts=prior.ts if prior and prior.ts > 0 else ev.ts)
                                      if source == "REST" and ev.status is TradeState.CONFIRMED
                                      else self._advanced_status(prior, ev))
            if key in self.fill_records and ev.status is TradeState.CONFIRMED:
                self._store.clear_inflight(ev.token_id)
                self._on_change(condition_id)
            log.info("trade_status_updated", trade_id=ev.trade_id, order_id=oid,
                     status=ev.status.value)
            self._fill_observe(ev, condition_id, source, received_at_us,
                connection_generation, "STATUS", received_monotonic_ns)

        elif ev.status is TradeState.RETRYING:
            self.fill_records[key] = self._advanced_status(self.fill_records.get(key), ev)
            # tx being retried on-chain — it may still succeed. Keep the
            # optimistic fill and the inflight guard; only FAILED is terminal.
            log.warning("trade_retrying", trade_id=ev.trade_id, order_id=oid, token=ev.token_id[:12])
            self._fill_observe(ev, condition_id, source, received_at_us,
                connection_generation, "STATUS", received_monotonic_ns)

        elif ev.status is TradeState.FAILED:
            if key in self.fill_records:
                self.fill_records[key] = replace(self.fill_records[key], status=ev.status)
            else:
                self.fill_records[key] = ev
            self.quarantined_orders.add(oid)
            self._store.mark_owned_order_unresolved(oid, True)
            self._fill_observe(ev, condition_id, source, received_at_us,
                connection_generation, "FAILED", received_monotonic_ns)
            log.error("trade_quarantined", trade_id=ev.trade_id, order_id=oid, reason="FAILED")
            if source == "WS":
                self._on_reconcile(oid)

    def on_order(self, ev: OrderEvent, condition_id: str, *,
                 received_at_us: int | None = None,
                 received_monotonic_ns: int | None = None,
                 connection_generation: int | None = None) -> None:
        if ev.order_id not in self._owned:
            return
        owned = self._store.owned_order(ev.order_id)
        if (owned is None or ev.token_id != owned["token_id"]
                or ev.side.value != owned["side"]
                or condition_id != owned["condition_id"]
                or (ev.market_id is not None and ev.market_id != owned["condition_id"])
                or not within_tolerance(ev.original_size, owned["submitted_qty"], self.quantity_tolerance)
                or not within_tolerance(ev.price, owned["submitted_price"], self.price_tolerance)):
            self.quarantine_order(ev.order_id, "order event economics contradict registry")
            return
        qty = self.tracked_trade_qty(ev.order_id)
        gap = not within_tolerance(ev.size_matched, qty, self.quantity_tolerance)
        missing_assoc = bool(ev.associate_trades) and not set(ev.associate_trades).issubset(
            {tid for tid, oid in self.applied_fill_keys if oid == ev.order_id
             and (tid, oid) not in self.quarantined_fill_keys
             and self.fill_records.get((tid, oid), None) is not None
             and self.fill_records[(tid, oid)].status is not TradeState.FAILED})
        log.info("order_match_checksum", order_id=ev.order_id, venue_qty=ev.size_matched,
                 tracked_qty=qty, status=ev.status)
        if gap or missing_assoc:
            self.quarantined_orders.add(ev.order_id)
            self._store.mark_owned_order_unresolved(ev.order_id, True)
            log.warning("fill_gap_detected", order_id=ev.order_id, venue_qty=ev.size_matched,
                        tracked_qty=qty, associate_trades_missing=missing_assoc)
            self._on_reconcile(ev.order_id)
        if ev.is_cancel or ev.remaining_size <= 0:
            self._store.remove_order(ev.order_id)
        else:
            state = OrderState.LIVE
            self._store.upsert_order(
                OpenOrder(ev.order_id, ev.token_id, ev.side, ev.price, ev.remaining_size, state)
            )
        self._order_observe(ev.order_id, {"transition": "UPDATE", "status": ev.status,
            "remaining_size": ev.remaining_size, "size_matched": ev.size_matched,
            "cancel": ev.is_cancel, "quarantined": ev.order_id in self.quarantined_orders},
            received_at_us=received_at_us, received_monotonic_ns=received_monotonic_ns,
            connection_generation=connection_generation)
        self._on_change(condition_id)

    def _order_observe(self, order_id: str, payload: dict[str, object], *,
                       received_at_us: int | None = None,
                       received_monotonic_ns: int | None = None,
                       connection_generation: int | None = None) -> None:
        if self._research is None:
            return
        try:
            owned = self._store.owned_order(order_id)
            if owned is None:
                return
            role = {"favorite_first_leg": "FIRST", "favorite_pair_leg": "PAIR"}.get(str(owned["source"]))
            self._research.emit("ORDER_STATE", str(owned["condition_id"]),
                {"order_id": order_id, "role": role, **payload},
                source="USER_WS" if received_at_us is not None else "ENGINE",
                received_at_us=received_at_us, received_monotonic_ns=received_monotonic_ns,
                connection_generation=connection_generation)
        except Exception:
            self._research.failures += 1

    def _fill_observe(self, ev: TradeEvent, cid: str, source: str,
                      received_at_us: int | None, generation: int | None,
                      disposition: str, received_monotonic_ns: int | None) -> None:
        if self._research is None or ev.maker_order_id is None:
            return
        try:
            key = (ev.trade_id, ev.maker_order_id)
            owned = self._store.owned_order(ev.maker_order_id)
            role = ({"favorite_first_leg": "FIRST", "favorite_pair_leg": "PAIR"}.get(str(owned["source"]))
                    if owned is not None else None)
            effective = self.fill_records.get(key)
            excluded = (key in self.quarantined_fill_keys or effective is None
                        or effective.status is TradeState.FAILED)
            accepted = key in self.applied_fill_keys and not excluded
            self._research.emit("FILL_REVISION", cid, {
                "trade_id": ev.trade_id, "maker_order_id": ev.maker_order_id,
                "token_id": ev.token_id, "side": ev.our_side.value,
                "price": ev.price, "qty": ev.size, "role": role,
                "status": ev.status.value, "disposition": disposition,
                "accepted": accepted,
                "quarantined": key in self.quarantined_fill_keys
                    or ev.maker_order_id in self.quarantined_orders,
                "fill_truth_ready": (self._fill_truth_ready(cid)
                    if self._fill_truth_ready is not None else None),
            }, source="REST" if source == "REST" else "USER_WS",
                exchange_at_us=exchange_us(ev.ts), received_at_us=received_at_us,
                received_monotonic_ns=received_monotonic_ns,
                connection_generation=generation)
        except Exception:
            self._research.failures += 1
