"""One-market read-only owner for the existing RDP market and user inputs."""

from __future__ import annotations

import asyncio
import copy
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from polymaker.config import Config
from polymaker.logging import get_logger
from polymaker.rdp_account import RDPAccountError, _capture_account_with_gateway
from polymaker.rdp_live import RDPBootstrap, RDPBootstrapError, bootstrap_rdp_market
from polymaker.rdp_marketdata import (
    RDPFeedReadiness,
    RDPMarketDataSession,
    _readiness_deadline_s,
)
from polymaker.rdp_userstream import start_rdp_user_channel_session
from polymaker.risk.rdp_capital import (
    RDPCapitalAdmission,
    RDPCapitalProjection,
    RDPInitialBackingResult,
    RDPMode,
    evaluate_initial_rdp_backing,
    evaluate_rdp_capital_gate,
    project_rdp_capital,
)
from polymaker.strategy.rdp_book_filter import RDPBookFilterResult
from polymaker.strategy.rdp_quotes import RDPQuoteDecision, build_normal_quotes

log = get_logger("rdp.runtime")


class RDPObservationError(RuntimeError):
    """Sanitized, categorized failure of the read-only input observation."""

    def __init__(self, category: str, reason: str) -> None:
        super().__init__(reason)
        self.category = category


@dataclass(frozen=True, slots=True)
class RDPObservationTermination:
    mode: str
    slug: str
    condition_id: str
    started_at: float
    terminated_at: float
    category: str
    reason: str
    public_generation: int
    user_generation: int
    real_money_trading_performed: bool = False


@dataclass(frozen=True, slots=True)
class RDPInitialNormalAdmission:
    """Immutable initial NORMAL evidence; eligibility never grants send authority."""

    slug: str
    condition_id: str
    eligible: bool
    reason: str | None
    candidate: RDPQuoteDecision | None
    backing: RDPInitialBackingResult | None
    book_filter: RDPBookFilterResult | None
    capital_projection: RDPCapitalProjection | None
    capital_admission: RDPCapitalAdmission | None
    public_generation: int
    user_generation: int
    account_receipt_times: tuple[float, float, float] | None
    token_ticks: tuple[tuple[str, float], ...]
    minimum_sizes: tuple[tuple[str, float], ...]
    outstanding_execution_gates: tuple[str, ...]
    execution_authorized: bool = field(default=False, init=False)


class RDPInputSession:
    """Retain one public service and one authenticated user/account owner."""

    def __init__(self, bootstrap: RDPBootstrap, *,
                 public_session: Any, user_session: Any,
                 feed: RDPFeedReadiness, backing: RDPInitialBackingResult,
                 now: Callable[[], float], sleep: Callable[[float], Any],
                 poll_interval_s: float, started_at: float,
                 account_max_age_s: float | None = None) -> None:
        self.bootstrap = bootstrap
        self.public_session = public_session
        self.user_session = user_session
        self.feed = feed
        self.observation = user_session.observation
        self.account = self.observation.account
        self.backing = backing
        self.processor = user_session.processor
        self.public_generation = feed.connection_generation
        self.user_generation = self.observation.user_channel.connection_generation
        self.started_at = started_at
        self._now = now
        self._sleep = sleep
        self._poll_interval_s = poll_interval_s
        self._account_max_age_s = (Config().risk.user_ws_blind_halt_s
                                   if account_max_age_s is None else account_max_age_s)
        self._closed = False
        self._admission_active = False
        self._admission_task: asyncio.Task[Any] | None = None
        self._account_read_task: asyncio.Task[Any] | None = None

    @classmethod
    async def open(
        cls,
        cfg: Config,
        slug: str,
        *,
        now: Callable[[], float] | None = None,
        sleep: Callable[[float], Any] | None = None,
        poll_interval_s: float = 0.05,
        feed_timeout_s: float = 15.0,
        bootstrap_factory: Callable[..., Any] | None = None,
        public_session_factory: Callable[..., Any] | None = None,
        user_session_factory: Callable[..., Any] | None = None,
        user_session_options: dict[str, Any] | None = None,
        backing_evaluator: Callable[..., RDPInitialBackingResult] | None = None,
        _termination_context: dict[str, Any] | None = None,
    ) -> RDPInputSession:
        now = now or time.time
        sleep = sleep or asyncio.sleep
        bootstrap_factory = bootstrap_factory or bootstrap_rdp_market
        public_session_factory = public_session_factory or RDPMarketDataSession
        user_session_factory = user_session_factory or start_rdp_user_channel_session
        backing_evaluator = backing_evaluator or evaluate_initial_rdp_backing
        if not math.isfinite(poll_interval_s) or poll_interval_s <= 0:
            raise RDPObservationError("INVALID_OBSERVATION_INTERVAL", "observation polling interval must be positive")
        try:
            runtime_cfg = copy.deepcopy(cfg)
        except Exception as exc:  # noqa: BLE001
            raise RDPObservationError("CONFIGURATION_CAPTURE_FAILED", "RDP configuration could not be captured") from exc

        bootstrap = await bootstrap_factory(runtime_cfg, slug, now=now)
        if _termination_context is not None:
            _termination_context["condition_id"] = bootstrap.market.condition_id
        _readiness_deadline_s(bootstrap, feed_timeout_s, now)
        public_session = public_session_factory(bootstrap, single_attempt=True)
        user_session = None
        feed = None
        try:
            await public_session.start()
            feed = await public_session.wait_ready(timeout_s=feed_timeout_s, now=now)
            if _termination_context is not None:
                _termination_context["public_generation"] = feed.connection_generation
            user_task = asyncio.create_task(
                user_session_factory(runtime_cfg, bootstrap, now=now,
                                     **(user_session_options or {})),
                name="rdp-user-session-startup",
            )
            user_session = await _await_user_start_with_public_health(
                user_task, public_session, feed.connection_generation,
                bootstrap, now,
            )
            if _termination_context is not None:
                _termination_context["user_generation"] = (
                    user_session.observation.user_channel.connection_generation)
            backing = backing_evaluator(
                bootstrap, user_session.observation.account, now=now(),
                max_age_s=runtime_cfg.risk.user_ws_blind_halt_s,
            )
            if not backing.eligible:
                reason = backing.reason or "INITIAL_BACKING_INELIGIBLE"
                raise RDPObservationError("STARTUP_BACKING_INELIGIBLE", reason)
            started_at = now()
            if started_at >= bootstrap.end_epoch:
                raise RDPObservationError("MARKET_INTERVAL_ELAPSED", "market ended before startup certification")
            session = cls(
                bootstrap, public_session=public_session,
                user_session=user_session, feed=feed, backing=backing,
                now=now, sleep=sleep, poll_interval_s=poll_interval_s,
                started_at=started_at,
                account_max_age_s=runtime_cfg.risk.user_ws_blind_halt_s,
            )
            failure = session._health_failure()
            if failure is not None:
                raise RDPObservationError(failure, "input health changed during startup capture")
            started_at = now()
            if started_at >= bootstrap.end_epoch:
                raise RDPObservationError("MARKET_INTERVAL_ELAPSED", "market ended at startup certification")
            log.info(
                "rdp_observation_started", mode="READ_ONLY_OBSERVATION",
                result="SUCCESS", slug=bootstrap.market.slug,
                condition_id=bootstrap.market.condition_id,
                public_generation=session.public_generation,
                user_generation=session.user_generation,
                backing_eligible=backing.eligible, backing_reason=backing.reason,
                public_receipt_ts=feed.receipt_ts,
                user_capture_ts=session.observation.user_channel.capture_ts,
                account_receipt_times={
                    "orders": session.account.orders_receipt_ts,
                    "positions": session.account.positions_receipt_ts,
                    "collateral": session.account.collateral_receipt_ts,
                },
                backing_read_age_s=backing.read_age_s,
                tick_metadata=[{
                    "outcome": item.outcome, "token_id": item.token_id,
                    "tick_size": item.tick_size, "min_order_size": item.min_order_size,
                } for item in bootstrap.token_metadata],
                real_money_trading_performed=False,
            )
            return session
        except BaseException as exc:
            if _termination_context is not None:
                if feed is not None:
                    _termination_context["public_generation"] = feed.connection_generation
                else:
                    service = getattr(public_session, "service", None)
                    generation = getattr(service, "connection_generation", None)
                    if generation is None:
                        generation = getattr(public_session, "generation", None)
                    if isinstance(generation, int) and generation > 0:
                        _termination_context["public_generation"] = generation
                user_generation = getattr(exc, "connection_generation", None)
                if user_generation is None and user_session is not None:
                    user_generation = getattr(
                        getattr(getattr(user_session, "observation", None), "user_channel", None),
                        "connection_generation", None)
                if isinstance(user_generation, int) and user_generation > 0:
                    _termination_context["user_generation"] = user_generation
            await _close_owned(public_session, user_session)
            raise

    def _health_failure(self) -> str | None:
        public_failure = self.public_session.failure_category(self.public_generation)
        if public_failure is not None:
            return public_failure
        return self.user_session.failure_category(self.user_generation)

    def book_filter_result(self) -> RDPBookFilterResult:
        """Return filter evidence only while the retained input owners remain authoritative."""
        service = self.public_session.service
        generation = service.connection_generation
        ticks: dict[str, float] = {}
        for token in self.public_session.service.token_ids:
            book = service.book(token)
            if book is None:
                break
            ticks[token] = book.tick_size
        reliable = (
            not self._closed
            and self._now() < self.bootstrap.end_epoch
            and self._health_failure() is None
            and generation == self.public_generation
            and set(ticks) == set(service.token_ids)
        )
        return service.book_filter.evaluate(
            time.monotonic(), generation=generation, ticks=ticks,
            state_reliable=reliable,
        )

    async def initial_normal_admission(self) -> RDPInitialNormalAdmission:
        """Capture one read-only initial NORMAL decision from retained inputs."""
        task = asyncio.current_task()
        if self._closed:
            return self._admission_rejected("SESSION_CLOSED")
        if self._admission_active:
            return self._admission_rejected("INITIAL_ADMISSION_IN_PROGRESS")
        self._admission_active = True
        self._admission_task = task
        try:
            reason = self._admission_precondition_failure()
            if reason is not None:
                return self._admission_rejected(reason)

            account = self.observation.account
            gateway = getattr(self.user_session, "gateway", None)
            if gateway is None:
                return self._admission_rejected("ACCOUNT_GATEWAY_UNAVAILABLE")
            signer = getattr(account, "signer", None)
            funder = getattr(account, "funder", None)
            if (not isinstance(signer, str) or not signer.strip()
                    or not isinstance(funder, str) or not funder.strip()):
                return self._admission_rejected("ACCOUNT_IDENTITY_UNAVAILABLE")
            read_task = asyncio.create_task(
                _capture_account_with_gateway(
                    self.bootstrap, gateway, signer, funder,
                    now=self._now,
                ),
                name="rdp-initial-admission-account-read",
            )
            self._account_read_task = read_task
            try:
                current_account = await asyncio.shield(read_task)
            except asyncio.CancelledError:
                await _drain_read_task(read_task)
                raise
            except RDPAccountError as exc:
                return self._admission_rejected(exc.category)
            except Exception as exc:  # noqa: BLE001 - sanitize unexpected read errors
                return self._admission_rejected(f"ACCOUNT_READ_FAILED:{type(exc).__name__}")
            finally:
                if read_task.done():
                    self._account_read_task = None

            # The read may have taken time. Nothing after this point awaits;
            # every predicate and output below describes one synchronous capture.
            reason = self._admission_precondition_failure()
            if reason is not None:
                return self._admission_rejected(reason)
            backing = evaluate_initial_rdp_backing(
                self.bootstrap, current_account, now=self._now(),
                max_age_s=self._account_max_age_s,
            )
            if not backing.eligible:
                return self._admission_rejected(
                    backing.reason or "INITIAL_BACKING_INELIGIBLE", backing=backing,
                    account=current_account,
                )
            if not backing.selected_state_empty:
                return self._admission_rejected(
                    "SELECTED_STATE_NOT_EMPTY", backing=backing, account=current_account,
                )

            reason = self._admission_precondition_failure()
            if reason is not None:
                return self._admission_rejected(
                    reason, backing=backing, account=current_account,
                )
            service = self.public_session.service
            metrics = service.metrics
            if _has_public_input_backlog(metrics):
                return self._admission_rejected(
                    "PUBLIC_INPUT_BACKLOG", backing=backing, account=current_account,
                )
            filter_result = self.book_filter_result()
            if not filter_result.ready:
                return self._admission_rejected(
                    _filter_rejection_reason(filter_result), backing=backing,
                    account=current_account, book_filter=filter_result,
                )
            if filter_result.rejected:
                return self._admission_rejected(
                    "BOOK_FILTER_REJECTED", backing=backing, account=current_account,
                    book_filter=filter_result,
                )

            yes_book = service.book(self.bootstrap.market.yes.token_id)
            no_book = service.book(self.bootstrap.market.no.token_id)
            if (yes_book is None or no_book is None
                    or yes_book.snapshot_generation != self.public_generation
                    or no_book.snapshot_generation != self.public_generation):
                return self._admission_rejected(
                    "BOOK_UNAVAILABLE", backing=backing, account=current_account,
                    book_filter=filter_result,
                )
            yes_view, no_view = yes_book.view(), no_book.view()
            ticks = ((self.bootstrap.market.yes.token_id, yes_book.tick_size),
                     (self.bootstrap.market.no.token_id, no_book.tick_size))
            metadata = _validated_token_metadata(self.bootstrap)
            if metadata is None:
                return self._admission_rejected(
                    "INVALID_TOKEN_METADATA", backing=backing, account=current_account,
                    book_filter=filter_result, token_ticks=ticks,
                )
            minimums = tuple((token, metadata[token][1]) for token in (
                self.bootstrap.market.yes.token_id, self.bootstrap.market.no.token_id))
            if any(size > 5 for _token, size in minimums):
                return self._admission_rejected(
                    "CLOB_MINIMUM_EXCEEDS_FROZEN_CLIP", backing=backing,
                    account=current_account, book_filter=filter_result,
                    token_ticks=ticks, minimum_sizes=minimums,
                )
            decision_market = replace(
                self.bootstrap.market,
                min_order_size=max(size for _token, size in minimums),
            )
            inventory_ids: set[str] = set()
            projection = project_rdp_capital(
                decision_market, self.processor, inventory_ids, [],
                market_state_reliable=True, order_state_reliable=True,
            )
            if not projection.certified:
                return self._admission_rejected(
                    projection.reason or "CAPITAL_PROJECTION_REJECTED",
                    backing=backing, account=current_account, book_filter=filter_result,
                    capital_projection=projection, token_ticks=ticks,
                    minimum_sizes=minimums,
                )
            candidate = build_normal_quotes(
                decision_market, projection.snapshot.inventory,
                yes_view, no_view, yes_book.tick_size, no_book.tick_size,
            )
            if candidate.reason is not None:
                return self._admission_rejected(
                    candidate.reason, backing=backing, account=current_account,
                    book_filter=filter_result, capital_projection=projection,
                    candidate=candidate, token_ticks=ticks, minimum_sizes=minimums,
                )
            capital = evaluate_rdp_capital_gate(RDPMode.NORMAL, candidate, projection)
            if not capital.admitted:
                return self._admission_rejected(
                    capital.reason or "CAPITAL_GATE_REJECTED", backing=backing,
                    account=current_account, book_filter=filter_result,
                    capital_projection=projection, capital_admission=capital,
                    candidate=candidate, token_ticks=ticks, minimum_sizes=minimums,
                )
            # Final health/generation/expiry/backlog checks are synchronous and
            # immediately precede returning the immutable decision.
            reason = self._admission_precondition_failure()
            if reason is None and _has_public_input_backlog(metrics):
                reason = "PUBLIC_INPUT_BACKLOG"
            if reason is not None:
                return self._admission_rejected(
                    reason, backing=backing, account=current_account,
                    book_filter=filter_result, capital_projection=projection,
                    capital_admission=capital, candidate=candidate,
                    token_ticks=ticks, minimum_sizes=minimums,
                )
            return RDPInitialNormalAdmission(
                slug=self.bootstrap.market.slug,
                condition_id=self.bootstrap.market.condition_id,
                eligible=True,
                reason=None,
                candidate=candidate,
                backing=backing,
                book_filter=filter_result,
                capital_projection=projection,
                capital_admission=capital,
                public_generation=self.public_generation,
                user_generation=self.user_generation,
                account_receipt_times=_account_receipt_times(current_account),
                token_ticks=ticks,
                minimum_sizes=minimums,
                outstanding_execution_gates=(
                    "CURRENT_MARKET_TRADABILITY", "CURRENT_CYCLE_RECONCILIATION",
                    "OPERATIONAL_TIME_SUFFICIENCY", "FINAL_SUBMIT_REVALIDATION",
                    "HUMAN_LIVE_AUTHORIZATION",
                ),
            )
        finally:
            self._account_read_task = None
            self._admission_task = None
            self._admission_active = False

    def _admission_precondition_failure(self) -> str | None:
        if self._closed:
            return "SESSION_CLOSED"
        if self._now() >= self.bootstrap.end_epoch:
            return "MARKET_EXPIRED"
        failure = self._health_failure()
        if failure is not None:
            return failure
        if self.public_session.service.connection_generation != self.public_generation:
            return "FEED_GENERATION_CHANGED"
        if self.user_session.observation.user_channel.connection_generation != self.user_generation:
            return "USER_STREAM_GENERATION_CHANGED"
        if not _initial_processor_is_empty(self.processor):
            return "LOCAL_SCOPE_NOT_EMPTY"
        return None

    def _admission_rejected(self, reason: str, *, backing=None, account=None,
                            book_filter=None, capital_projection=None,
                            capital_admission=None, candidate=None,
                            token_ticks=(), minimum_sizes=()) -> RDPInitialNormalAdmission:
        return RDPInitialNormalAdmission(
            slug=self.bootstrap.market.slug,
            condition_id=self.bootstrap.market.condition_id,
            eligible=False,
            reason=reason,
            candidate=candidate,
            backing=backing,
            book_filter=book_filter,
            capital_projection=capital_projection,
            capital_admission=capital_admission,
            public_generation=self.public_generation,
            user_generation=self.user_generation,
            account_receipt_times=(
                _account_receipt_times(account) if account is not None else None
            ),
            token_ticks=tuple(token_ticks),
            minimum_sizes=tuple(minimum_sizes),
            outstanding_execution_gates=(
                "CURRENT_MARKET_TRADABILITY", "CURRENT_CYCLE_RECONCILIATION",
                "OPERATIONAL_TIME_SUFFICIENCY", "FINAL_SUBMIT_REVALIDATION",
                "HUMAN_LIVE_AUTHORIZATION",
            ),
        )

    async def wait_until_end(self) -> RDPObservationTermination:
        while True:
            failure = self._health_failure()
            if failure is not None:
                raise RDPObservationError(failure, "read-only input health was lost")
            now = self._now()
            if now >= self.bootstrap.end_epoch:
                termination = RDPObservationTermination(
                    "READ_ONLY_OBSERVATION", self.bootstrap.market.slug,
                    self.bootstrap.market.condition_id, self.started_at, now,
                    "MARKET_END", "selected market interval ended",
                    self.public_generation, self.user_generation,
                )
                log.info(
                    "rdp_observation_terminated", mode=termination.mode,
                    result="SUCCESS", category=termination.category,
                    reason=termination.reason, slug=termination.slug,
                    condition_id=termination.condition_id,
                    public_generation=termination.public_generation,
                    user_generation=termination.user_generation,
                    terminated_at=termination.terminated_at,
                    real_money_trading_performed=False,
                )
                return termination
            await self._sleep(min(self._poll_interval_s, self.bootstrap.end_epoch - now))

    def _log_failure(self, category: str, reason: str) -> None:
        log.error(
            "rdp_observation_terminated", mode="READ_ONLY_OBSERVATION",
            result="FAILURE", category=category, reason=reason,
            slug=self.bootstrap.market.slug,
            condition_id=self.bootstrap.market.condition_id,
            public_generation=self.public_generation,
            user_generation=self.user_generation,
            terminated_at=self._now(), real_money_trading_performed=False,
        )

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        active = self._admission_task
        interrupted = False
        if active is not None and active is not asyncio.current_task() and not active.done():
            active.cancel()
            interrupted = await _drain_read_task(active)
        try:
            await _close_owned(self.public_session, self.user_session)
        except asyncio.CancelledError:
            interrupted = True
        if interrupted:
            raise asyncio.CancelledError

    async def __aenter__(self) -> RDPInputSession:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()


async def _close_owned(public_session: Any, user_session: Any | None) -> None:
    async def close_all() -> None:
        try:
            await public_session.aclose()
        finally:
            if user_session is not None:
                await user_session.aclose()

    cleanup = asyncio.create_task(close_all(), name="rdp-input-session-cleanup")
    interrupted = False
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            interrupted = True
    cleanup.result()
    if interrupted:
        raise asyncio.CancelledError


async def _drain_read_task(task: asyncio.Task[Any]) -> bool:
    """Wait for owned read work without letting repeated cancellation detach it."""
    interrupted = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            owner = asyncio.current_task()
            if owner is not None and owner.cancelling():
                interrupted = True
            continue
        except BaseException:
            break
    return interrupted


def _initial_processor_is_empty(processor: Any) -> bool:
    """Require the retained processor/store to have no prior local run scope."""
    try:
        if any((processor._owned, processor._pending, processor._pending_events,
                processor._pending_malformed_order_ids, processor.fill_records,
                processor.applied_fill_keys, processor.quarantined_fill_keys,
                processor.quarantined_orders, processor._pending_overflow)):
            return False
        store = processor._store
        if store.owned_orders():
            return False
        # Pending placement can be durable in a store implementation even when
        # the processor's in-memory capture is empty.
        if callable(getattr(store, "open_orders", None)) and store.open_orders():
            return False
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False
    return True


def _validated_token_metadata(bootstrap: RDPBootstrap) -> dict[str, tuple[float, float]] | None:
    rows = getattr(bootstrap, "token_metadata", None)
    if not isinstance(rows, tuple) or len(rows) != 2:
        return None
    market_tokens = {
        bootstrap.market.yes.token_id: bootstrap.market.yes.outcome,
        bootstrap.market.no.token_id: bootstrap.market.no.outcome,
    }
    if (not all(isinstance(token, str) and token for token in market_tokens)
            or len(market_tokens) != 2):
        return None
    parsed: dict[str, tuple[float, float]] = {}
    try:
        for item in rows:
            token_id = item.token_id
            tick = item.tick_size
            minimum = item.min_order_size
            if (token_id not in market_tokens
                    or item.outcome != market_tokens[token_id]
                    or token_id in parsed
                    or isinstance(tick, bool)
                    or not isinstance(tick, (int, float))
                    or not math.isfinite(tick) or tick <= 0
                    or isinstance(minimum, bool)
                    or not isinstance(minimum, (int, float))
                    or not math.isfinite(minimum) or minimum <= 0):
                return None
            parsed[token_id] = (float(tick), float(minimum))
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None
    return parsed if set(parsed) == set(market_tokens) else None


def _has_public_input_backlog(metrics: Any) -> bool:
    if not isinstance(metrics, dict):
        return True
    received, processed = metrics.get("received"), metrics.get("processed")
    if (isinstance(received, bool) or not isinstance(received, int) or received < 0
            or isinstance(processed, bool) or not isinstance(processed, int) or processed < 0):
        return True
    return received != processed


def _account_receipt_times(account: Any) -> tuple[float, float, float] | None:
    try:
        values = (account.orders_receipt_ts, account.positions_receipt_ts,
                  account.collateral_receipt_ts)
        if all(isinstance(value, (int, float)) and not isinstance(value, bool)
               and math.isfinite(value) for value in values):
            return tuple(float(value) for value in values)
    except (AttributeError, TypeError, ValueError):
        pass
    return None


def _filter_rejection_reason(result: RDPBookFilterResult) -> str:
    if not result.ready:
        return "BOOK_FILTER_UNREADY"
    if result.reasons:
        return result.reasons[0]
    return "BOOK_FILTER_REJECTED"


async def _await_user_start_with_public_health(
    task: asyncio.Task[Any], public_session: Any, public_generation: int,
    bootstrap: RDPBootstrap, now: Callable[[], float],
) -> Any:
    try:
        while True:
            failure = public_session.failure_category(public_generation)
            if failure is not None:
                interrupted, drain_error = await _cancel_and_drain_user_start(task)
                if drain_error is not None:
                    _log_user_start_drain_error(drain_error)
                if interrupted:
                    raise asyncio.CancelledError
                if drain_error is not None:
                    raise RDPObservationError(
                        failure, "public input failed and user startup teardown failed") from None
                raise RDPObservationError(failure, "public input failed during authenticated startup")
            if task.done():
                try:
                    result = task.result()
                except BaseException:
                    # Prefer a simultaneous public failure as the observer's
                    # categorized cause; otherwise preserve the starter cause.
                    failure = public_session.failure_category(public_generation)
                    if failure is not None:
                        raise RDPObservationError(
                            failure, "public input failed during authenticated startup") from None
                    raise
                # Recheck after task completion so simultaneous startup and
                # public failure keeps the public failure as the final cause.
                failure = public_session.failure_category(public_generation)
                if failure is not None:
                    await _close_owned(_NoopOwner(), result)
                    raise RDPObservationError(failure, "public input failed during authenticated startup")
                if now() >= bootstrap.end_epoch:
                    await _close_owned(_NoopOwner(), result)
                    raise RDPObservationError("MARKET_INTERVAL_ELAPSED", "market ended during authenticated startup")
                return result
            remaining = bootstrap.end_epoch - now()
            if remaining <= 0:
                interrupted, drain_error = await _cancel_and_drain_user_start(task)
                if drain_error is not None:
                    _log_user_start_drain_error(drain_error)
                if interrupted:
                    raise asyncio.CancelledError
                if drain_error is not None:
                    raise RDPObservationError(
                        "MARKET_INTERVAL_ELAPSED", "market ended and user startup teardown failed") from None
                raise RDPObservationError("MARKET_INTERVAL_ELAPSED", "market ended during authenticated startup")
            await asyncio.wait({task}, timeout=min(0.025, remaining))
    except asyncio.CancelledError:
        _, drain_error = await _cancel_and_drain_user_start(task)
        if drain_error is not None:
            _log_user_start_drain_error(drain_error)
        raise


class _NoopOwner:
    async def aclose(self) -> None:
        return None


async def _cancel_and_drain_user_start(
    task: asyncio.Task[Any],
) -> tuple[bool, BaseException | None]:
    if not task.done():
        task.cancel()
    interrupted = False
    drain_error: BaseException | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            owner = asyncio.current_task()
            if owner is not None and owner.cancelling():
                interrupted = True
            continue
        except BaseException:
            # A child teardown exception is inspected and reported below after
            # the task is fully done, without confusing it with owner cancel.
            break
    if not task.cancelled():
        try:
            result = task.result()
        except BaseException as exc:
            drain_error = exc
        else:
            close = getattr(result, "aclose", None)
            if callable(close):
                try:
                    await _close_owned(_NoopOwner(), result)
                except asyncio.CancelledError:
                    interrupted = True
                except BaseException as exc:
                    drain_error = exc
    return interrupted, drain_error


def _log_user_start_drain_error(error: BaseException) -> None:
    log.error(
        "rdp_user_startup_drain_failed", category="USER_STARTUP_DRAIN_FAILED",
        exception_type=type(error).__name__,
        mode="READ_ONLY_OBSERVATION", real_money_trading_performed=False,
    )


async def observe_rdp_market(
    cfg: Config,
    slug: str,
    **session_options: Any,
) -> RDPObservationTermination:
    """Run a bounded read-only observation through selected market end."""
    session = None
    observer_clock = session_options.get("now") or time.time
    context: dict[str, Any] = {"slug": slug}
    session_options["_termination_context"] = context
    try:
        session = await RDPInputSession.open(cfg, slug, **session_options)
        return await session.wait_until_end()
    except asyncio.CancelledError:
        _log_observation_termination("CANCELLED", "read-only observation was cancelled",
                                     slug, session, context=context, now=observer_clock)
        raise
    except RDPBootstrapError as exc:
        _log_observation_termination(exc.category, "read-only observation failed",
                                     slug, session, context=context, now=observer_clock)
        raise
    except RDPObservationError as exc:
        _log_observation_termination(exc.category, "read-only observation failed",
                                     slug, session, context=context, now=observer_clock)
        raise
    finally:
        if session is not None:
            try:
                await session.aclose()
            except asyncio.CancelledError:
                _log_observation_termination("CANCELLED", "observation cleanup was cancelled",
                                             slug, session, context=context, now=observer_clock)
                raise


def _log_observation_termination(category: str, reason: str, slug: str,
                                 session: RDPInputSession | None, *,
                                 context: dict[str, Any] | None = None,
                                 now: Callable[[], float] | None = None) -> None:
    bootstrap = session.bootstrap if session is not None else None
    context = context or {}
    log.error(
        "rdp_observation_terminated", mode="READ_ONLY_OBSERVATION",
        result="FAILURE", category=category, reason=reason, slug=slug,
        condition_id=(bootstrap.market.condition_id if bootstrap is not None
                      else context.get("condition_id")),
        public_generation=(session.public_generation if session is not None
                           else context.get("public_generation")),
        user_generation=(session.user_generation if session is not None
                         else context.get("user_generation")),
        terminated_at=(session._now() if session is not None
                       else (now or time.time)()),
        real_money_trading_performed=False,
    )
