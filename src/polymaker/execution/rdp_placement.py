"""RDP-local ownership capture for one initial NORMAL pair outcome.

This module deliberately does not submit, sign, cancel, reconcile, or retry.
It binds already-admitted quotes to the existing processor's pre-ack capture
and applies typed gateway acknowledgment evidence to the existing store.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from decimal import Decimal, DecimalException
from typing import Any

from polymaker.domain import OpenOrder, OrderState, Quote, Side
from polymaker.execution.gateway import PlacementUnknown
from polymaker.logging import get_logger

log = get_logger("execution.rdp_placement")


class RDPPlacementError(RuntimeError):
    """Sanitized rejection of an RDP placement-capture operation."""

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


@dataclass(frozen=True, slots=True)
class RDPPlacementAttempt:
    attempt_id: int
    slug: str
    condition_id: str
    public_generation: int
    user_generation: int
    quotes: tuple[Quote, ...]
    outcome: str
    reason: str | None
    acknowledged_order_ids: tuple[str, ...]
    observed_order_ids: tuple[str, ...]
    pending_anomalies: tuple[str, ...]
    unresolved: bool
    replacement_blocked: bool
    execution_authorized: bool = field(default=False, init=False)


class RDPPlacementOwner:
    """Bind one initial pair to processor capture and typed gateway outcomes."""

    def __init__(self, bootstrap: Any, processor: Any, store: Any, *,
                 initial_scope_empty: Callable[[], bool]) -> None:
        self._bootstrap = bootstrap
        self._processor = processor
        self._store = store
        self._initial_scope_empty = initial_scope_empty
        self._next_attempt_id = 1
        self._current: RDPPlacementAttempt | None = None

    @property
    def current(self) -> RDPPlacementAttempt | None:
        return self._current

    @property
    def blocks_initial_admission(self) -> bool:
        current = self._current
        return bool(current is not None and (current.unresolved or current.outcome == "ARMED"))

    def arm(self, admission: Any, *, current_public_generation: int,
            current_user_generation: int, session_healthy: bool,
            now: float) -> RDPPlacementAttempt:
        current = self._current
        if current is not None and current.replacement_blocked:
            raise RDPPlacementError("PLACEMENT_REPLACEMENT_BLOCKED")
        if current is not None and current.outcome == "ARMED":
            raise RDPPlacementError("PLACEMENT_ATTEMPT_IN_PROGRESS")
        if not session_healthy:
            raise RDPPlacementError("INPUT_SESSION_UNHEALTHY")
        if now >= self._bootstrap.end_epoch:
            raise RDPPlacementError("MARKET_EXPIRED")
        market = self._bootstrap.market
        if (getattr(admission, "eligible", False) is not True
                or getattr(admission, "execution_authorized", True) is not False
                or getattr(admission, "condition_id", None) != market.condition_id
                or getattr(admission, "slug", None) != market.slug
                or getattr(admission, "public_generation", None) != current_public_generation
                or getattr(admission, "user_generation", None) != current_user_generation):
            raise RDPPlacementError("ADMISSION_SCOPE_OR_GENERATION_MISMATCH")
        candidate = getattr(admission, "candidate", None)
        quotes = getattr(candidate, "quotes", None)
        if not isinstance(quotes, tuple) or not _valid_initial_pair(market, quotes):
            raise RDPPlacementError("INVALID_INITIAL_PAIR")
        if not self._initial_scope_empty():
            raise RDPPlacementError("LOCAL_SCOPE_NOT_EMPTY")
        if self._processor._pending or self._processor._pending_events:
            raise RDPPlacementError("PENDING_CAPTURE_ALREADY_ACTIVE")

        self._processor.arm_pending_many([(quote.token_id, quote.side) for quote in quotes])
        attempt = RDPPlacementAttempt(
            attempt_id=self._next_attempt_id,
            slug=market.slug,
            condition_id=market.condition_id,
            public_generation=current_public_generation,
            user_generation=current_user_generation,
            quotes=quotes,
            outcome="ARMED",
            reason=None,
            acknowledged_order_ids=(),
            observed_order_ids=(),
            pending_anomalies=(),
            unresolved=True,
            replacement_blocked=True,
        )
        self._next_attempt_id += 1
        self._current = attempt
        log.info("rdp_placement_capture_armed", condition_id=attempt.condition_id,
                 public_generation=attempt.public_generation,
                 user_generation=attempt.user_generation, outcome=attempt.outcome,
                 execution_authorized=False)
        return attempt

    def resolve(self, attempt: RDPPlacementAttempt, outcome: Any) -> RDPPlacementAttempt:
        current = self._current
        if (current is None or attempt is not current
                or current.outcome != "ARMED"):
            raise RDPPlacementError("PLACEMENT_ATTEMPT_NOT_ACTIVE")

        # Snapshot these before register_order: that method replays buffered
        # events and can automatically discard the processor's pending buffer.
        anomaly_snapshot: tuple[str, ...] = ()
        observed_ids: tuple[str, ...] = ()
        reason: str | None = None
        unresolved = False
        result_kind = "ACKNOWLEDGED"
        if isinstance(outcome, PlacementUnknown):
            acknowledgements = outcome.acknowledged_orders
            observed_ids, observed_invalid = _safe_ids(outcome.observed_order_ids)
            reason = _safe_reason(outcome.reason) or "PLACEMENT_OUTCOME_UNKNOWN"
            unresolved = True
            result_kind = "PARTIAL" if acknowledgements else "UNKNOWN"
            if observed_invalid:
                anomaly_snapshot += ("INVALID_OBSERVED_ORDER_IDS",)
        elif isinstance(outcome, list):
            acknowledgements = tuple(outcome)
            if not outcome:
                result_kind = "EMPTY"
        elif isinstance(outcome, BaseException):
            acknowledgements = ()
            unresolved = True
            result_kind = "UNKNOWN"
            reason = ("PLACEMENT_INTERRUPTED" if isinstance(outcome, asyncio.CancelledError)
                      else f"PLACEMENT_ERROR:{type(outcome).__name__}")
        else:
            acknowledgements = ()
            unresolved = True
            result_kind = "UNKNOWN"
            reason = "INVALID_TYPED_PLACEMENT_OUTCOME"

        safe_orders, invalid_order_evidence = self._validate_acknowledgements(
            current, acknowledgements,
        )
        if invalid_order_evidence:
            anomaly_snapshot += tuple(invalid_order_evidence)
            unresolved = True
            result_kind = "UNKNOWN" if not safe_orders else "PARTIAL"
            reason = reason or "ACKNOWLEDGMENT_EVIDENCE_UNSAFE"

        safe_ids = tuple(order.order_id for order in safe_orders)
        if observed_ids:
            observed_ids = tuple(order_id for order_id in observed_ids
                                 if order_id not in safe_ids)
        pending_anomalies = _pending_anomalies(
            self._processor, current.quotes, current.condition_id, safe_ids,
        )
        anomaly_snapshot = tuple(dict.fromkeys(anomaly_snapshot + pending_anomalies))
        if anomaly_snapshot:
            unresolved = True
            reason = reason or "PENDING_CAPTURE_ANOMALY"
            if result_kind == "ACKNOWLEDGED":
                result_kind = "PARTIAL" if safe_orders else "UNKNOWN"
            elif result_kind == "EMPTY":
                result_kind = "UNKNOWN"

        if not safe_orders and result_kind == "EMPTY" and not unresolved:
            # A clean, authoritative empty result may release the capture.
            self._processor.discard_pending()
            blocked = False
        else:
            register_anomalies: list[str] = []
            for order in safe_orders:
                try:
                    self._store.upsert_order(order)
                    self._processor.register_order(
                        order.order_id, current.condition_id, order.token_id,
                        order.side, order.size, order.price, "rdp_initial",
                    )
                except Exception as exc:  # noqa: BLE001 - retain safe partial ownership
                    register_anomalies.append(f"REGISTRATION_FAILED:{type(exc).__name__}")
                    unresolved = True
                    reason = reason or "ORDER_REGISTRATION_INCOMPLETE"
                    break
            if register_anomalies:
                anomaly_snapshot = tuple(dict.fromkeys(anomaly_snapshot + tuple(register_anomalies)))
            # A gateway exception, partial result, or any contradictory capture
            # remains blocked. Do not clear pending evidence as generic cleanup.
            if result_kind == "ACKNOWLEDGED" and len(safe_orders) == 2 and not unresolved:
                result_kind = "COMPLETE"
            elif result_kind == "ACKNOWLEDGED":
                result_kind = "UNKNOWN" if not safe_orders else "PARTIAL"
                unresolved = True
                reason = reason or "ACKNOWLEDGMENT_PAIR_INCOMPLETE"
            blocked = unresolved or bool(safe_orders)

        # Processor replay can discover malformed economics and quarantine an
        # attributed order. Preserve this after replay without replacing its
        # potentially updated local OpenOrder with the original acknowledgment.
        post_replay_anomalies = tuple(
            f"PROCESSOR_QUARANTINE:{order_id}"
            for order_id in safe_ids
            if (order_id in self._processor.quarantined_orders
                or any(key[1] == order_id for key in self._processor.quarantined_fill_keys))
        )
        if post_replay_anomalies:
            anomaly_snapshot = tuple(dict.fromkeys(anomaly_snapshot + post_replay_anomalies))
            unresolved = True
            blocked = True
            reason = reason or "PROCESSOR_REPLAY_QUARANTINE"
            result_kind = "PARTIAL" if safe_orders else "UNKNOWN"

        final = replace(
            current,
            outcome=result_kind,
            reason=reason,
            acknowledged_order_ids=safe_ids,
            observed_order_ids=observed_ids,
            pending_anomalies=anomaly_snapshot,
            unresolved=unresolved,
            replacement_blocked=blocked,
        )
        self._current = final
        log.info("rdp_placement_capture_resolved", condition_id=final.condition_id,
                 public_generation=final.public_generation,
                 user_generation=final.user_generation, outcome=final.outcome,
                 reason=final.reason, acknowledged_order_ids=final.acknowledged_order_ids,
                 observed_order_ids=final.observed_order_ids,
                 pending_anomalies=final.pending_anomalies,
                 replacement_blocked=final.replacement_blocked,
                 execution_authorized=False)
        return final

    def _validate_acknowledgements(
        self, attempt: RDPPlacementAttempt, acknowledgements: Any,
    ) -> tuple[tuple[OpenOrder, ...], tuple[str, ...]]:
        if not isinstance(acknowledgements, (tuple, list)):
            return (), ("ACKNOWLEDGMENTS_NOT_A_SEQUENCE",)
        expected = {quote.token_id: quote for quote in attempt.quotes}
        candidates: dict[str, OpenOrder] = {}
        seen_ids: set[str] = set()
        duplicate_ids: set[str] = set()
        token_occurrences: dict[str, int] = {}
        errors: list[str] = []
        for order in acknowledgements:
            if not isinstance(order, OpenOrder):
                errors.append("ACKNOWLEDGMENT_NOT_OPEN_ORDER")
                continue
            order_id = order.order_id
            quote = expected.get(order.token_id)
            if quote is not None:
                token_occurrences[order.token_id] = token_occurrences.get(order.token_id, 0) + 1
            if (not isinstance(order_id, str) or not order_id.strip()
                    or order_id != order_id.strip()):
                errors.append("ACKNOWLEDGMENT_ID_INVALID")
                continue
            if order_id in seen_ids:
                errors.append(f"DUPLICATE_ACKNOWLEDGMENT_ID:{order_id}")
                duplicate_ids.add(order_id)
                candidates.pop(order_id, None)
                continue
            seen_ids.add(order_id)
            if self._store.owned_order(order_id) is not None or order_id in self._processor._owned:
                errors.append(f"ACKNOWLEDGMENT_ID_ALREADY_OWNED:{order_id}")
                continue
            if order_id in getattr(self._store, "orders", {}):
                errors.append(f"ACKNOWLEDGMENT_ID_ALREADY_IN_STORE:{order_id}")
                continue
            if (quote is None or order.side is not Side.BUY or order.state is not OrderState.LIVE
                    or not _same_number(order.price, quote.price)
                    or not _same_number(order.size, quote.size)):
                errors.append(f"ACKNOWLEDGMENT_ECONOMICS_MISMATCH:{order_id}")
                continue
            candidates[order_id] = order

        if len(acknowledgements) > 2:
            errors.append("TOO_MANY_ACKNOWLEDGMENTS")
        # When duplicate IDs occur, the gateway evidence cannot safely identify
        # either occurrence. No one may overwrite the existing registry entry.
        safe_by_token: dict[str, OpenOrder] = {}
        for order_id, order in candidates.items():
            if order_id in duplicate_ids:
                continue
            safe_by_token[order.token_id] = order
        ambiguous_tokens = {
            token for token, count in token_occurrences.items() if count > 1
        }
        for token in ambiguous_tokens:
            errors.append(f"DUPLICATE_ACKNOWLEDGMENT_TOKEN:{token}")
            safe_by_token.pop(token, None)
        return tuple(safe_by_token[token] for token in expected if token in safe_by_token), tuple(errors)


def _valid_initial_pair(market: Any, quotes: tuple[Any, ...]) -> bool:
    if len(quotes) != 2:
        return False
    by_token: dict[str, Quote] = {}
    for quote in quotes:
        if (not isinstance(quote, Quote) or quote.side is not Side.BUY
                or quote.token_id in by_token or quote.size != 5
                or not 0 < quote.price < 1):
            return False
        by_token[quote.token_id] = quote
    return set(by_token) == {market.yes.token_id, market.no.token_id}


def _same_number(actual: Any, expected: Any) -> bool:
    if isinstance(actual, bool) or not isinstance(actual, (int, float, Decimal)):
        return False
    if isinstance(expected, bool) or not isinstance(expected, (int, float, Decimal)):
        return False
    try:
        left, right = Decimal(str(actual)), Decimal(str(expected))
    except (DecimalException, ValueError, TypeError, OverflowError):
        return False
    return left.is_finite() and right.is_finite() and left == right


def _safe_ids(values: Any) -> tuple[tuple[str, ...], bool]:
    if not isinstance(values, (list, tuple)):
        return (), True
    output: list[str] = []
    invalid = False
    for value in values:
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            invalid = True
        elif value not in output:
            output.append(value)
        else:
            invalid = True
    return tuple(output), invalid


def _safe_reason(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    if value.replace("_", "").isalnum() and len(value) <= 80:
        return value
    return "PLACEMENT_OUTCOME_UNKNOWN"


def _pending_anomalies(processor: Any, quotes: tuple[Quote, ...],
                       condition_id: str, acknowledged_ids: tuple[str, ...]) -> tuple[str, ...]:
    anomalies: list[str] = []
    if getattr(processor, "_pending_overflow", False):
        anomalies.append("PENDING_BUFFER_OVERFLOW")
    for order_id in sorted(getattr(processor, "_pending_malformed_order_ids", ())):
        anomalies.append(f"PENDING_MALFORMED_MAKER_ID:{order_id}")
    candidate_tokens = {quote.token_id for quote in quotes}
    for event, _receipt_us, _receipt_ns, _generation in tuple(
            getattr(processor, "_pending_events", ())):
        if (event.token_id not in candidate_tokens or event.our_side is not Side.BUY
                or event.market_id not in (None, condition_id)):
            continue
        if event.maker_order_id not in acknowledged_ids:
            anomalies.append(f"UNMATCHED_CANDIDATE_TRADE:{event.trade_id}")
    return tuple(dict.fromkeys(anomalies))
