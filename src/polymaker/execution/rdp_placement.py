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
from polymaker.execution.rdp_lifecycle import RDPCloseResult, ReconciliationOutcome
from polymaker.logging import get_logger
from polymaker.risk.rdp_capital import RDPCapitalProjection, project_rdp_capital
from polymaker.strategy.rdp_accounting import snapshot_inventory

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
    closed_order_ids: tuple[str, ...]
    pending_anomalies: tuple[str, ...]
    unresolved: bool
    replacement_blocked: bool
    continuation: bool = False
    source_attempt_id: int | None = None
    mode: str = "NORMAL"
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
        self._attempts: dict[int, RDPPlacementAttempt] = {}
        self._close_proofs: dict[int, RDPCloseResult] = {}

    @property
    def current(self) -> RDPPlacementAttempt | None:
        return self._current

    @property
    def attempts(self) -> tuple[RDPPlacementAttempt, ...]:
        return tuple(self._attempts[key] for key in sorted(self._attempts))

    @property
    def registered_run_order_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._registered_run_ids()))

    @property
    def registered_run_order_sequence(self) -> tuple[str, ...]:
        return tuple(
            order_id for attempt_id in sorted(self._attempts)
            for order_id in self._attempts[attempt_id].acknowledged_order_ids
        )

    @property
    def blocks_initial_admission(self) -> bool:
        current = self._current
        return bool(current is not None and (
            current.unresolved or current.replacement_blocked or current.outcome == "ARMED"))

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
            closed_order_ids=(),
            pending_anomalies=(),
            unresolved=True,
            replacement_blocked=True,
        )
        self._next_attempt_id += 1
        self._current = attempt
        self._attempts[attempt.attempt_id] = attempt
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
                        order.side, order.size, order.price,
                        ("rdp_rebalance_continuation" if current.mode == "REBALANCE"
                         else "rdp_normal_continuation" if current.continuation
                         else "rdp_initial"),
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
            expected_count = 1 if current.mode == "REBALANCE" else 2
            if result_kind == "ACKNOWLEDGED" and len(safe_orders) == expected_count and not unresolved:
                result_kind = "COMPLETE"
            elif result_kind == "ACKNOWLEDGED":
                result_kind = "UNKNOWN" if not safe_orders else "PARTIAL"
                unresolved = True
                reason = reason or "ACKNOWLEDGMENT_MODE_CARDINALITY_INCOMPLETE"
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
        self._attempts[final.attempt_id] = final
        # A reliably empty continuation capture creates no new ownership. Keep
        # that immutable attempt in history, while restoring its fully closed
        # source as the current cumulative anchor for a fresh admission. This
        # lets the caller wait and re-admit after a clean pre-POST invalidation
        # without dropping prior close proofs or treating EMPTY as an order.
        if (final.continuation and final.outcome == "EMPTY"
                and not final.unresolved and not final.replacement_blocked
                and not final.acknowledged_order_ids and not final.observed_order_ids
                and not final.pending_anomalies):
            source = self._attempts.get(final.source_attempt_id)
            if (source is not None and source.outcome == "COMPLETE"
                    and not source.unresolved and not source.replacement_blocked
                    and set(source.closed_order_ids) == set(source.acknowledged_order_ids)
                    and source.attempt_id in self._close_proofs):
                self._current = source
            else:
                self._current = final
        else:
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

    def arm_continuation(
        self, admission: Any, *, current_public_generation: int,
        current_user_generation: int, session_healthy: bool, now: float,
    ) -> RDPPlacementAttempt:
        """Capture the next NORMAL pair only after this owner's prior proof."""
        previous = self._current
        if (previous is None or previous.replacement_blocked or previous.unresolved
                or previous.outcome != "COMPLETE"
                or set(previous.closed_order_ids) != set(previous.acknowledged_order_ids)):
            raise RDPPlacementError("PRIOR_PLACEMENT_NOT_CLOSED")
        if not session_healthy:
            raise RDPPlacementError("INPUT_SESSION_UNHEALTHY")
        if now >= self._bootstrap.end_epoch:
            raise RDPPlacementError("MARKET_EXPIRED")
        market = self._bootstrap.market
        if (getattr(admission, "eligible", False) is not True
                or getattr(admission, "execution_authorized", True) is not False
                or getattr(admission, "condition_id", None) != market.condition_id
                or getattr(admission, "slug", None) != market.slug
                or getattr(admission, "attempt_id", None) != previous.attempt_id
                or getattr(admission, "public_generation", None) != current_public_generation
                or getattr(admission, "user_generation", None) != current_user_generation
                or getattr(getattr(admission, "mode", None), "value", None) != "NORMAL"):
            raise RDPPlacementError("CONTINUATION_ADMISSION_SCOPE_MISMATCH")
        candidate = getattr(admission, "candidate", None)
        quotes = getattr(candidate, "quotes", None)
        run_ids = self._registered_run_ids()
        proof_ids = set(self._close_proofs)
        expected_proof_ids = {
            attempt_id for attempt_id, attempt in self._attempts.items()
            if attempt.acknowledged_order_ids
        }
        harmless_empty_ids = {
            attempt_id for attempt_id, attempt in self._attempts.items()
            if (attempt.outcome == "EMPTY" and not attempt.unresolved
                and not attempt.replacement_blocked
                and not attempt.acknowledged_order_ids
                and not attempt.observed_order_ids and not attempt.pending_anomalies)
        }
        if (not isinstance(quotes, tuple) or not _valid_continuation_pair(
                    market, quotes, run_ids, self._processor)
                or self.registered_run_order_sequence
                != tuple(getattr(admission, "run_order_ids", ()))
                or proof_ids != expected_proof_ids
                or set(self._attempts) != proof_ids | harmless_empty_ids):
            # The admission can cover only prior attempts, all of which must
            # already have independent retained close results.
            raise RDPPlacementError("CONTINUATION_SCOPE_OR_QUOTE_INVALID")
        if self._processor._pending or self._processor._pending_events:
            raise RDPPlacementError("PENDING_CAPTURE_ALREADY_ACTIVE")
        self._processor.arm_pending_many([(quote.token_id, quote.side) for quote in quotes])
        attempt = RDPPlacementAttempt(
            attempt_id=self._next_attempt_id, slug=market.slug,
            condition_id=market.condition_id,
            public_generation=current_public_generation,
            user_generation=current_user_generation, quotes=quotes,
            outcome="ARMED", reason=None, acknowledged_order_ids=(),
            observed_order_ids=(), closed_order_ids=(), pending_anomalies=(),
            unresolved=True, replacement_blocked=True, continuation=True,
            source_attempt_id=previous.attempt_id,
        )
        self._next_attempt_id += 1
        self._current = attempt
        self._attempts[attempt.attempt_id] = attempt
        log.info("rdp_continuation_capture_armed", condition_id=attempt.condition_id,
                 attempt_id=attempt.attempt_id, source_attempt_id=attempt.source_attempt_id,
                 acknowledged_order_ids=tuple(sorted(run_ids)), execution_authorized=False)
        return attempt

    def arm_rebalance_continuation(
        self, admission: Any, *, current_public_generation: int,
        current_user_generation: int, session_healthy: bool, now: float,
    ) -> RDPPlacementAttempt:
        """Capture exactly one deficient-side five-share repair order."""
        previous = self._current
        if (previous is None or previous.replacement_blocked or previous.unresolved
                or previous.outcome != "COMPLETE"
                or set(previous.closed_order_ids) != set(previous.acknowledged_order_ids)):
            raise RDPPlacementError("PRIOR_PLACEMENT_NOT_CLOSED")
        if not session_healthy:
            raise RDPPlacementError("INPUT_SESSION_UNHEALTHY")
        if now >= self._bootstrap.end_epoch:
            raise RDPPlacementError("MARKET_EXPIRED")
        market = self._bootstrap.market
        if (getattr(admission, "eligible", False) is not True
                or getattr(admission, "execution_authorized", True) is not False
                or getattr(admission, "condition_id", None) != market.condition_id
                or getattr(admission, "slug", None) != market.slug
                or getattr(admission, "attempt_id", None) != previous.attempt_id
                or getattr(admission, "public_generation", None) != current_public_generation
                or getattr(admission, "user_generation", None) != current_user_generation
                or getattr(getattr(admission, "mode", None), "value", None) != "REBALANCE"):
            raise RDPPlacementError("REBALANCE_ADMISSION_SCOPE_MISMATCH")
        candidate = getattr(admission, "candidate", None)
        quotes = getattr(candidate, "quotes", None)
        run_ids = self._registered_run_ids()
        inventory = snapshot_inventory(market, self._processor, run_ids)
        delta = Decimal(str(inventory.delta))
        expected_token = (market.no.token_id if delta >= 5 else
                          market.yes.token_id if delta <= -5 else None)
        quote = quotes[0] if isinstance(quotes, tuple) and len(quotes) == 1 else None
        proof_ids = set(self._close_proofs)
        expected_proof_ids = {key for key, value in self._attempts.items()
                              if value.acknowledged_order_ids}
        if (expected_token is None or not isinstance(quote, Quote)
                or quote.token_id != expected_token or quote.side is not Side.BUY
                or quote.size != 5 or not 0 < quote.price < 1
                or self.registered_run_order_sequence
                != tuple(getattr(admission, "run_order_ids", ()))
                or proof_ids != expected_proof_ids
                or set(self._attempts) != proof_ids | {
                    key for key, value in self._attempts.items()
                    if value.outcome == "EMPTY" and not value.unresolved
                    and not value.replacement_blocked and not value.acknowledged_order_ids
                    and not value.observed_order_ids and not value.pending_anomalies
                }):
            raise RDPPlacementError("REBALANCE_SCOPE_OR_QUOTE_INVALID")
        if self._processor._pending or self._processor._pending_events:
            raise RDPPlacementError("PENDING_CAPTURE_ALREADY_ACTIVE")
        self._processor.arm_pending_many([(quote.token_id, quote.side)])
        attempt = RDPPlacementAttempt(
            attempt_id=self._next_attempt_id, slug=market.slug,
            condition_id=market.condition_id,
            public_generation=current_public_generation,
            user_generation=current_user_generation, quotes=(quote,),
            outcome="ARMED", reason=None, acknowledged_order_ids=(),
            observed_order_ids=(), closed_order_ids=(), pending_anomalies=(),
            unresolved=True, replacement_blocked=True, continuation=True,
            source_attempt_id=previous.attempt_id, mode="REBALANCE",
        )
        self._next_attempt_id += 1
        self._current = attempt
        self._attempts[attempt.attempt_id] = attempt
        return attempt

    def _registered_run_ids(self) -> set[str]:
        return {
            order_id for attempt in self._attempts.values()
            for order_id in attempt.acknowledged_order_ids
        }

    @property
    def close_proofs(self) -> tuple[RDPCloseResult, ...]:
        return tuple(self._close_proofs[key] for key in sorted(self._close_proofs))

    def record_close(
        self, attempt: RDPPlacementAttempt, barrier: RDPCloseResult,
        capital: RDPCapitalProjection | None,
    ) -> RDPPlacementAttempt:
        """Record barrier evidence; only a current full-pair proof can unblock."""
        current = self._current
        if current is None or attempt is not current or current.outcome == "ARMED":
            raise RDPPlacementError("PLACEMENT_ATTEMPT_NOT_ACTIVE")
        if not isinstance(barrier, RDPCloseResult):
            raise RDPPlacementError("INVALID_CLOSE_EVIDENCE")

        acknowledged = set(current.acknowledged_order_ids)
        reconciled = {
            order_id for order_id, outcome in barrier.reconciliation_outcomes
            if outcome is ReconciliationOutcome.COMPLETE
        }
        closed = set(current.closed_order_ids)
        if (barrier.complete is True and barrier.reason is None
                and barrier.inventory is not None and reconciled == acknowledged
                and not barrier.affected_order_ids):
            closed.update(reconciled)

        market_tokens = {self._bootstrap.market.yes.token_id,
                         self._bootstrap.market.no.token_id}
        quote_tokens = {quote.token_id for quote in current.quotes}
        normal_shape = (
            current.mode == "NORMAL" and len(current.acknowledged_order_ids) == 2
            and quote_tokens == market_tokens
            and (not current.continuation and len(acknowledged) == 2
                 or current.continuation and _valid_continuation_pair(
                     self._bootstrap.market, current.quotes,
                     self._registered_run_ids() - acknowledged, self._processor,
                 ))
        )
        rebalance_shape = (
            current.mode == "REBALANCE" and current.continuation
            and len(acknowledged) == 1 and len(current.quotes) == 1
            and next(iter(quote_tokens), None)
            in (self._bootstrap.market.yes.token_id, self._bootstrap.market.no.token_id)
        )
        valid_pair = (
            current.outcome == "COMPLETE" and not current.unresolved
            and acknowledged == closed and (normal_shape or rebalance_shape)
        )
        capital_snapshot = getattr(capital, "snapshot", None)
        cumulative_ids = self._registered_run_ids()
        prior_proofs = self.close_proofs
        proofs = prior_proofs + (barrier,)
        fresh_capital = (
            project_rdp_capital(
                self._bootstrap.market, self._processor, cumulative_ids, [],
                market_state_reliable=True, order_state_reliable=True,
                close_evidence=proofs,
            ) if valid_pair and _registry_matches_attempt(
                self._bootstrap, self._processor, current,
            ) else None
        )
        try:
            current_inventory = snapshot_inventory(
                self._bootstrap.market, self._processor, acknowledged,
            ) if acknowledged else None
        except (TypeError, ValueError, OverflowError):
            current_inventory = None
        current_local_orders = getattr(self._store, "orders", None)
        current_local_open = (
            not isinstance(current_local_orders, dict)
            or bool(acknowledged & set(current_local_orders))
        )
        current_unresolved = any(
            order_id in self._processor.quarantined_orders
            or self._processor.has_unresolved_fill_key(order_id)
            or self._processor.has_failed_fill(order_id)
            for order_id in acknowledged
        )
        can_release = (
            valid_pair and barrier.complete is True and barrier.reason is None
            and barrier.inventory is not None and not barrier.affected_order_ids
            and current_inventory == barrier.inventory and not current_local_open
            and not current_unresolved
            and isinstance(capital, RDPCapitalProjection)
            and capital.certified and capital_snapshot is not None
            and isinstance(fresh_capital, RDPCapitalProjection)
            and fresh_capital.certified and fresh_capital.snapshot == capital_snapshot
            and set(capital_snapshot.run_order_ids) == cumulative_ids
            and not capital_snapshot.resting_order_ids
            and capital_snapshot.reserved_cash == 0
            and (current.continuation or capital_snapshot.inventory == barrier.inventory)
        )
        updated = replace(
            current,
            closed_order_ids=tuple(sorted(closed)),
            replacement_blocked=False if can_release else current.replacement_blocked,
        )
        self._current = updated
        self._attempts[updated.attempt_id] = updated
        if can_release:
            self._close_proofs[updated.attempt_id] = barrier
        return updated

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

        if len(acknowledgements) > (1 if attempt.mode == "REBALANCE" else 2):
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


def _valid_continuation_pair(
    market: Any, quotes: tuple[Any, ...], prior_ids: set[str], processor: Any,
) -> bool:
    """Validate the exact Frozen NORMAL inventory skew against prior fills."""
    if len(quotes) != 2 or not prior_ids:
        return False
    try:
        inventory = snapshot_inventory(market, processor, set(prior_ids))
        delta = Decimal(str(inventory.delta))
        expected = {
            market.yes.token_id: Decimal("5") + max(-delta, Decimal("0")),
            market.no.token_id: Decimal("5") + max(delta, Decimal("0")),
        }
        if not delta.is_finite() or abs(delta) >= Decimal("5"):
            return False
    except (DecimalException, TypeError, ValueError, OverflowError):
        return False
    by_token: dict[str, Any] = {}
    for quote in quotes:
        if (not isinstance(quote, Quote) or quote.side is not Side.BUY
                or quote.token_id in by_token or not 0 < quote.price < 1):
            return False
        if not _same_number(quote.size, expected.get(quote.token_id)):
            return False
        by_token[quote.token_id] = quote
    return set(by_token) == set(expected)


def _registry_matches_attempt(
    bootstrap: Any, processor: Any, attempt: RDPPlacementAttempt,
) -> bool:
    market = bootstrap.market
    quotes = {quote.token_id: quote for quote in attempt.quotes}
    expected = ({quote.token_id for quote in attempt.quotes}
                if attempt.mode == "REBALANCE" else
                {market.yes.token_id, market.no.token_id})
    if (attempt.condition_id != market.condition_id or attempt.slug != market.slug
            or len(quotes) != (1 if attempt.mode == "REBALANCE" else 2)
            or set(quotes) != expected):
        return False
    for order_id in attempt.acknowledged_order_ids:
        record = processor.owned_record(order_id)
        quote = quotes.get((record or {}).get("token_id"))
        if (record is None or quote is None
                or record.get("condition_id") != market.condition_id
                or record.get("side") != Side.BUY.value
                or not _same_number(record.get("submitted_qty"), quote.size)
                or not _same_number(record.get("submitted_price"), quote.price)):
            return False
    return True


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
