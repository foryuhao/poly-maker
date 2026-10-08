"""One-market read-only owner for the existing RDP market and user inputs."""

from __future__ import annotations

import asyncio
import copy
import math
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

from polymaker.config import Config
from polymaker.domain import OpenOrder, OrderState, Side, TradeState
from polymaker.execution.gateway import ExactOpenOrder
from polymaker.execution.rdp_fill_window import RDPFillWindow, RDPFillWindowDecision
from polymaker.execution.rdp_lifecycle import RDPCloseResult, close_rdp_cycle
from polymaker.execution.rdp_placement import (
    RDPPlacementAttempt,
    RDPPlacementError,
    RDPPlacementOwner,
)
from polymaker.logging import get_logger
from polymaker.rdp_account import RDPAccountError, _capture_account_with_gateway
from polymaker.rdp_live import (
    RDPBootstrap,
    RDPBootstrapError,
    RDPMarketStatusEvidence,
    bootstrap_rdp_market,
    read_rdp_market_status,
    validate_rdp_market_status_config,
)
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
from polymaker.strategy.rdp_accounting import snapshot_inventory
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
    market_status: RDPMarketStatusEvidence | None = None
    execution_authorized: bool = field(default=False, init=False)


@dataclass(frozen=True, slots=True)
class RDPInitialPreFillDecision:
    """Read-only validity evidence for the retained acknowledged initial pair."""

    action: str
    reason: str
    attempt_id: int
    slug: str
    condition_id: str
    acknowledged_order_ids: tuple[str, ...]
    submitted_quotes: tuple[Any, ...]
    public_generation: int
    user_generation: int
    captured_at: float | None
    market_status: RDPMarketStatusEvidence | None
    order_receipt_ts: float | None
    order_age_s: float | None
    book_filter: RDPBookFilterResult | None
    inventory: Any | None
    capital_projection: RDPCapitalProjection | None
    current_candidate: RDPQuoteDecision | None
    fill_window: RDPFillWindowDecision | None
    execution_authorized: bool = field(default=False, init=False)


@dataclass(frozen=True, slots=True)
class RDPInitialPlacementClose:
    """Immutable evidence from one explicit, scoped placement close."""

    attempt_id: int
    slug: str
    condition_id: str
    owned_order_ids: tuple[str, ...]
    closed_order_ids: tuple[str, ...]
    barrier: RDPCloseResult | None
    capital: RDPCapitalProjection | None
    barrier_complete: bool
    placement_unresolved: bool
    replacement_blocked: bool
    reason: str | None
    next_mode: RDPMode | None
    execution_authorized: bool = field(default=False, init=False)


@dataclass(frozen=True, slots=True)
class RDPFillWindowCompletion:
    """Retained result of one explicitly activated initial FillWindow close."""

    attempt_id: int
    slug: str
    condition_id: str
    acknowledged_order_ids: tuple[str, ...]
    trigger: str
    window_deadline_monotonic_ns: int
    operational_close_deadline: float
    close_result: RDPInitialPlacementClose
    started_monotonic_ns: int
    completed_monotonic_ns: int
    execution_authorized: bool = field(default=False, init=False)


class RDPInputSession:
    """Retain one public service and one authenticated user/account owner."""

    def __init__(self, bootstrap: RDPBootstrap, *,
                 public_session: Any, user_session: Any,
                 feed: RDPFeedReadiness, backing: RDPInitialBackingResult,
                 now: Callable[[], float], sleep: Callable[[float], Any],
                 poll_interval_s: float, started_at: float,
                 account_max_age_s: float | None = None,
                 market_data_max_age_s: float | None = None,
                 fill_window_s: float = 3.0,
                 monotonic_ns: Callable[[], int] | None = None,
                 gamma_origin: str | None = None,
                 market_status_max_age_s: float | None = None) -> None:
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
        self._monotonic_ns = monotonic_ns or time.monotonic_ns
        self._poll_interval_s = poll_interval_s
        self._account_max_age_s = (Config().risk.user_ws_blind_halt_s
                                   if account_max_age_s is None else account_max_age_s)
        self._market_data_max_age_s = (
            Config().risk.ws_stale_halt_s
            if market_data_max_age_s is None else market_data_max_age_s
        )
        if (isinstance(self._market_data_max_age_s, bool)
                or not isinstance(self._market_data_max_age_s, (int, float))
                or not math.isfinite(self._market_data_max_age_s)
                or self._market_data_max_age_s <= 0):
            raise RDPObservationError(
                "INVALID_MARKET_DATA_MAX_AGE",
                "market-data freshness limit must be finite and positive",
            )
        self._market_data_max_age_s = float(self._market_data_max_age_s)
        default_gamma_origin, default_status_max_age = validate_rdp_market_status_config(Config())
        self._gamma_origin = default_gamma_origin if gamma_origin is None else gamma_origin
        status_max_age = default_status_max_age if market_status_max_age_s is None else market_status_max_age_s
        if (isinstance(status_max_age, bool) or not isinstance(status_max_age, (int, float))
                or not math.isfinite(status_max_age) or status_max_age <= 0):
            raise RDPObservationError(
                "INVALID_MARKET_STATUS_MAX_AGE", "market-status freshness limit must be finite and positive",
            )
        self._market_status_max_age_s = float(status_max_age)
        self._fill_window_s = RDPFillWindow(fill_window_s).duration_s
        self._fill_windows: dict[int, RDPFillWindow] = {}
        self._closed = False
        self._admission_active = False
        self._admission_task: asyncio.Task[Any] | None = None
        self._account_read_task: asyncio.Task[Any] | None = None
        self._market_status_read_task: asyncio.Task[Any] | None = None
        self._placement_close_task: asyncio.Task[RDPInitialPlacementClose] | None = None
        self._fill_window_task: asyncio.Task[RDPFillWindowCompletion] | None = None
        self._prefill_task: asyncio.Task[RDPInitialPreFillDecision] | None = None
        self._prefill_read_task: asyncio.Task[Any] | None = None
        self._prefill_order_read_started_at: float | None = None
        self._fill_window_result: RDPFillWindowCompletion | None = None
        self._fill_window_cancel_requested: str | None = None
        self._fill_window_wakeup = asyncio.Event()
        self.placement_owner = RDPPlacementOwner(
            bootstrap, self.processor,
            getattr(self.user_session, "store", getattr(self.processor, "_store", None)),
            initial_scope_empty=lambda: _initial_processor_is_empty(self.processor),
        )

    @classmethod
    async def open(
        cls,
        cfg: Config,
        slug: str,
        *,
        now: Callable[[], float] | None = None,
        sleep: Callable[[float], Any] | None = None,
        poll_interval_s: float = 0.05,
        fill_window_s: float = 3.0,
        monotonic_ns: Callable[[], int] | None = None,
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
            RDPFillWindow(fill_window_s)
        except (TypeError, ValueError):
            raise RDPObservationError(
                "INVALID_FILL_WINDOW", "FillWindow duration must be finite and positive",
            ) from None
        try:
            runtime_cfg = copy.deepcopy(cfg)
        except Exception as exc:  # noqa: BLE001
            raise RDPObservationError("CONFIGURATION_CAPTURE_FAILED", "RDP configuration could not be captured") from exc

        try:
            gamma_origin, market_status_max_age_s = validate_rdp_market_status_config(runtime_cfg)
        except RDPBootstrapError as exc:
            raise RDPObservationError(
                exc.category, "official Gamma origin or market-status freshness configuration is invalid",
            ) from None

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
                market_data_max_age_s=runtime_cfg.risk.ws_stale_halt_s,
                gamma_origin=gamma_origin,
                market_status_max_age_s=market_status_max_age_s,
                fill_window_s=fill_window_s,
                monotonic_ns=monotonic_ns,
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
        market_status: RDPMarketStatusEvidence | None = None
        market_status_verified = False

        def rejected(reason: str, **kwargs: Any) -> RDPInitialNormalAdmission:
            nonlocal market_status, market_status_verified
            if market_status is None:
                market_status_verified = False
            else:
                try:
                    capture_time = self._now()
                except Exception:  # noqa: BLE001 - rejection remains fail-closed
                    market_status = replace(
                        market_status, age_s=None,
                        max_age_s=self._market_status_max_age_s,
                    )
                    market_status_verified = False
                else:
                    market_status, status_failure = self._evaluate_market_status_freshness(
                        market_status, at=capture_time,
                    )
                    market_status_verified = (
                        status_failure is None and capture_time < self.bootstrap.end_epoch
                    )
            kwargs["market_status"] = market_status
            kwargs["market_status_verified"] = market_status_verified
            return self._admission_rejected(reason, **kwargs)

        task = asyncio.current_task()
        if self._closed:
            return rejected("SESSION_CLOSED")
        if self._admission_active:
            return rejected("INITIAL_ADMISSION_IN_PROGRESS")
        self._admission_active = True
        self._admission_task = task
        try:
            reason = self._admission_precondition_failure()
            if reason is not None:
                return rejected(reason)

            status_task = asyncio.create_task(
                read_rdp_market_status(
                    self.bootstrap, self._gamma_origin, now=self._now,
                ),
                name="rdp-initial-admission-market-status-read",
            )
            self._market_status_read_task = status_task
            try:
                market_status = await asyncio.shield(status_task)
            except asyncio.CancelledError:
                await _drain_read_task(status_task)
                raise
            except RDPBootstrapError as exc:
                precondition = self._admission_precondition_failure()
                if precondition is not None:
                    return rejected(precondition)
                log.warning(
                    "rdp_initial_market_status", result="REJECTED", category=exc.category,
                    slug=self.bootstrap.market.slug,
                    condition_id=self.bootstrap.market.condition_id,
                    real_money_trading_performed=False,
                )
                return rejected(exc.category)
            except Exception as exc:  # noqa: BLE001 - sanitize unexpected HTTP/parser errors
                precondition = self._admission_precondition_failure()
                if precondition is not None:
                    return rejected(precondition)
                reason = f"MARKET_STATUS_READ_FAILED:{type(exc).__name__}"
                log.warning(
                    "rdp_initial_market_status", result="REJECTED", category=reason,
                    slug=self.bootstrap.market.slug,
                    condition_id=self.bootstrap.market.condition_id,
                    real_money_trading_performed=False,
                )
                return rejected(reason)
            finally:
                if status_task.done():
                    self._market_status_read_task = None

            reason = self._admission_precondition_failure()
            if reason is not None:
                return rejected(reason)
            market_status, status_failure = self._evaluate_market_status_freshness(market_status)
            if status_failure is not None:
                market_status_verified = False
                return rejected(status_failure)
            market_status_verified = True
            log.info(
                "rdp_initial_market_status", result="SUCCESS", category="MARKET_STATUS_FRESH",
                slug=market_status.slug, condition_id=market_status.condition_id,
                token_ids=market_status.token_ids, outcomes=market_status.outcomes,
                neg_risk=market_status.neg_risk, end_epoch=market_status.end_epoch,
                accepting_orders=market_status.accepting_orders, closed=market_status.closed,
                receipt_ts=market_status.receipt_ts, age_s=market_status.age_s,
                max_age_s=market_status.max_age_s, real_money_trading_performed=False,
            )

            account = self.observation.account
            gateway = getattr(self.user_session, "gateway", None)
            if gateway is None:
                return rejected("ACCOUNT_GATEWAY_UNAVAILABLE")
            signer = getattr(account, "signer", None)
            funder = getattr(account, "funder", None)
            if (not isinstance(signer, str) or not signer.strip()
                    or not isinstance(funder, str) or not funder.strip()):
                return rejected("ACCOUNT_IDENTITY_UNAVAILABLE")
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
                return rejected(self._admission_precondition_failure() or exc.category)
            except Exception as exc:  # noqa: BLE001 - sanitize unexpected read errors
                return rejected(
                    self._admission_precondition_failure()
                    or f"ACCOUNT_READ_FAILED:{type(exc).__name__}"
                )
            finally:
                if read_task.done():
                    self._account_read_task = None

            # The read may have taken time. Nothing after this point awaits;
            # every predicate and output below describes one synchronous capture.
            reason = self._admission_precondition_failure()
            if reason is not None:
                return rejected(reason)
            market_status, status_failure = self._evaluate_market_status_freshness(
                market_status,
            )
            if status_failure is not None:
                market_status_verified = False
                return rejected(status_failure, account=current_account)
            backing = evaluate_initial_rdp_backing(
                self.bootstrap, current_account, now=self._now(),
                max_age_s=self._account_max_age_s,
            )
            if not backing.eligible:
                return rejected(
                    backing.reason or "INITIAL_BACKING_INELIGIBLE", backing=backing,
                    account=current_account,
                )
            if not backing.selected_state_empty:
                return rejected(
                    "SELECTED_STATE_NOT_EMPTY", backing=backing, account=current_account,
                )

            reason = self._admission_precondition_failure()
            if reason is not None:
                return rejected(
                    reason, backing=backing, account=current_account,
                )
            service = self.public_session.service
            metrics = service.metrics
            if _has_public_input_backlog(metrics):
                return rejected(
                    "PUBLIC_INPUT_BACKLOG", backing=backing, account=current_account,
                )
            filter_result = self.book_filter_result()
            if not filter_result.ready:
                return rejected(
                    _filter_rejection_reason(filter_result), backing=backing,
                    account=current_account, book_filter=filter_result,
                )
            if filter_result.rejected:
                return rejected(
                    "BOOK_FILTER_REJECTED", backing=backing, account=current_account,
                    book_filter=filter_result,
                )

            yes_book = service.book(self.bootstrap.market.yes.token_id)
            no_book = service.book(self.bootstrap.market.no.token_id)
            if (yes_book is None or no_book is None
                    or yes_book.snapshot_generation != self.public_generation
                    or no_book.snapshot_generation != self.public_generation):
                return rejected(
                    "BOOK_UNAVAILABLE", backing=backing, account=current_account,
                    book_filter=filter_result,
                )
            yes_view, no_view = yes_book.view(), no_book.view()
            ticks = ((self.bootstrap.market.yes.token_id, yes_book.tick_size),
                     (self.bootstrap.market.no.token_id, no_book.tick_size))
            metadata = _validated_token_metadata(self.bootstrap)
            if metadata is None:
                return rejected(
                    "INVALID_TOKEN_METADATA", backing=backing, account=current_account,
                    book_filter=filter_result, token_ticks=ticks,
                )
            minimums = tuple((token, metadata[token][1]) for token in (
                self.bootstrap.market.yes.token_id, self.bootstrap.market.no.token_id))
            if any(size > 5 for _token, size in minimums):
                return rejected(
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
                return rejected(
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
                return rejected(
                    candidate.reason, backing=backing, account=current_account,
                    book_filter=filter_result, capital_projection=projection,
                    candidate=candidate, token_ticks=ticks, minimum_sizes=minimums,
                )
            capital = evaluate_rdp_capital_gate(RDPMode.NORMAL, candidate, projection)
            if not capital.admitted:
                return rejected(
                    capital.reason or "CAPITAL_GATE_REJECTED", backing=backing,
                    account=current_account, book_filter=filter_result,
                    capital_projection=projection, capital_admission=capital,
                    candidate=candidate, token_ticks=ticks, minimum_sizes=minimums,
                )
            # Final health/generation/expiry/backlog checks are synchronous and
            # immediately precede returning the immutable decision.
            decision_now = self._now()
            market_status, status_failure = self._evaluate_market_status_freshness(
                market_status, at=decision_now,
            )
            if status_failure is not None:
                market_status_verified = False
                return rejected(
                    status_failure, backing=backing, account=current_account,
                    book_filter=filter_result, capital_projection=projection,
                    capital_admission=capital, candidate=candidate,
                    token_ticks=ticks, minimum_sizes=minimums,
                )
            final_backing = evaluate_initial_rdp_backing(
                self.bootstrap, current_account, now=decision_now,
                max_age_s=self._account_max_age_s,
            )
            if not final_backing.eligible or not final_backing.selected_state_empty:
                return rejected(
                    final_backing.reason or "ACCOUNT_EVIDENCE_STALE",
                    backing=final_backing, account=current_account,
                    book_filter=filter_result, capital_projection=projection,
                    capital_admission=capital, candidate=candidate,
                    token_ticks=ticks, minimum_sizes=minimums,
                )
            reason = self._admission_precondition_failure()
            if reason is None and _has_public_input_backlog(metrics):
                reason = "PUBLIC_INPUT_BACKLOG"
            if reason is not None:
                return rejected(
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
                book_filter=filter_result,
                capital_projection=projection,
                capital_admission=capital,
                public_generation=self.public_generation,
                user_generation=self.user_generation,
                account_receipt_times=_account_receipt_times(current_account),
                backing=final_backing,
                token_ticks=ticks,
                minimum_sizes=minimums,
                market_status=market_status,
                outstanding_execution_gates=(
                    "CURRENT_CYCLE_RECONCILIATION",
                    "OPERATIONAL_TIME_SUFFICIENCY", "FINAL_SUBMIT_REVALIDATION",
                    "HUMAN_LIVE_AUTHORIZATION",
                ),
            )
        finally:
            self._account_read_task = None
            self._market_status_read_task = None
            self._admission_task = None
            self._admission_active = False

    def _evaluate_market_status_freshness(
        self,
        evidence: RDPMarketStatusEvidence,
        *,
        at: float | None = None,
    ) -> tuple[RDPMarketStatusEvidence, str | None]:
        at = self._now() if at is None else at
        receipt_ts = evidence.receipt_ts
        if (isinstance(receipt_ts, bool) or not isinstance(receipt_ts, (int, float))
                or not math.isfinite(receipt_ts)):
            category = "INVALID_MARKET_STATUS_RECEIPT"
            age = None
        elif (isinstance(at, bool) or not isinstance(at, (int, float))
                or not math.isfinite(at)):
            category = "INVALID_MARKET_STATUS_EVALUATION_TIME"
            age = None
        else:
            exact_age = exact_limit = None
            try:
                exact_age = Decimal(str(at)) - Decimal(str(receipt_ts))
                exact_limit = Decimal(str(self._market_status_max_age_s))
                age = float(exact_age)
            except (InvalidOperation, OverflowError, ValueError):
                exact_age = None
                exact_limit = None
                age = None
            if (exact_age is None or exact_limit is None or not exact_age.is_finite()
                    or not exact_limit.is_finite() or not math.isfinite(age)):
                category = "INVALID_MARKET_STATUS_AGE"
            elif exact_age < 0:
                category = "MARKET_STATUS_RECEIPT_IN_FUTURE"
            elif exact_age >= exact_limit:
                category = "MARKET_STATUS_STALE"
            else:
                evaluated = replace(
                    evidence, age_s=age, max_age_s=self._market_status_max_age_s,
                )
                return evaluated, None
        if age is not None and math.isfinite(age):
            evaluated = replace(
                evidence, age_s=age, max_age_s=self._market_status_max_age_s,
            )
        else:
            evaluated = replace(evidence, age_s=age, max_age_s=self._market_status_max_age_s)
        log.warning(
            "rdp_initial_market_status", result="REJECTED", category=category,
            slug=evidence.slug, condition_id=evidence.condition_id,
            token_ids=evidence.token_ids, outcomes=evidence.outcomes,
            neg_risk=evidence.neg_risk, end_epoch=evidence.end_epoch,
            accepting_orders=evidence.accepting_orders, closed=evidence.closed,
            receipt_ts=evidence.receipt_ts, age_s=evaluated.age_s,
            max_age_s=evaluated.max_age_s, real_money_trading_performed=False,
        )
        return evaluated, category

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
        if self.placement_owner.blocks_initial_admission:
            return "INITIAL_PLACEMENT_UNRESOLVED"
        if not _initial_processor_is_empty(self.processor):
            return "LOCAL_SCOPE_NOT_EMPTY"
        return None

    def arm_initial_normal_placement(
        self, admission: RDPInitialNormalAdmission,
    ) -> RDPPlacementAttempt:
        """Arm existing processor capture for an admitted pair; never sends it."""
        if self._admission_active:
            raise RDPPlacementError("INITIAL_ADMISSION_IN_PROGRESS")
        current_attempt = self.placement_owner.current
        if current_attempt is not None and current_attempt.replacement_blocked:
            category = ("PLACEMENT_ATTEMPT_IN_PROGRESS"
                        if current_attempt.outcome == "ARMED"
                        else "PLACEMENT_REPLACEMENT_BLOCKED")
            raise RDPPlacementError(category)
        failure = self._admission_precondition_failure()
        if failure is not None:
            raise RDPPlacementError(failure)
        return self.placement_owner.arm(
            admission,
            current_public_generation=self.public_session.service.connection_generation,
            current_user_generation=(
                self.user_session.observation.user_channel.connection_generation),
            session_healthy=self._health_failure() is None,
            now=self._now(),
        )

    def resolve_initial_normal_placement(
        self, attempt: RDPPlacementAttempt, outcome: Any,
    ) -> RDPPlacementAttempt:
        """Resolve typed acknowledgment evidence into retained processor ownership."""
        return self.placement_owner.resolve(attempt, outcome)

    async def evaluate_initial_prefill(
        self, attempt: RDPPlacementAttempt,
    ) -> RDPInitialPreFillDecision:
        """Evaluate the retained acknowledged pair without taking an order action."""
        active = self._prefill_task
        if active is not None and not active.done():
            return self._prefill_decision("CLOSE", "PREFILL_EVALUATION_IN_PROGRESS", attempt)
        if (self._admission_active
                or (self._placement_close_task is not None
                    and not self._placement_close_task.done())
                or (self._fill_window_task is not None and not self._fill_window_task.done())):
            return self._prefill_decision("CLOSE", "PLACEMENT_COORDINATION_IN_PROGRESS", attempt)
        task = asyncio.create_task(
            self._run_initial_prefill_evaluation(attempt),
            name="rdp-initial-prefill-evaluation",
        )
        self._prefill_task = task
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await _drain_read_task(task)
            raise
        finally:
            if task.done() and self._prefill_task is task:
                self._prefill_task = None
                self._prefill_order_read_started_at = None

    def _prefill_decision(
        self, action: str, reason: str, attempt: Any, *,
        captured_at: float | None = None,
        market_status: RDPMarketStatusEvidence | None = None,
        order_receipt_ts: float | None = None,
        order_age_s: float | None = None,
        book_filter: RDPBookFilterResult | None = None,
        inventory: Any | None = None,
        capital_projection: RDPCapitalProjection | None = None,
        current_candidate: RDPQuoteDecision | None = None,
        fill_window: RDPFillWindowDecision | None = None,
    ) -> RDPInitialPreFillDecision:
        # Every terminal decision is itself an evidence capture point. Refresh
        # timestamps here as well as on the happy path so a read/health failure
        # reports the age of evidence that was actually available at failure.
        # Keep the caller's primary categorical reason unchanged, and never
        # manufacture a receipt for an order read that did not succeed.
        try:
            terminal_capture = (captured_at if action != "CLOSE" and captured_at is not None
                                else self._now())
            if (isinstance(terminal_capture, bool)
                    or not isinstance(terminal_capture, (int, float))
                    or not math.isfinite(terminal_capture)):
                terminal_capture = None
        except Exception:  # noqa: BLE001 - failure reason remains primary
            terminal_capture = None
        captured_at = terminal_capture
        if market_status is not None:
            if terminal_capture is None:
                market_status = replace(
                    market_status, age_s=None,
                    max_age_s=self._market_status_max_age_s,
                )
            else:
                try:
                    market_status, _freshness_failure = self._evaluate_market_status_freshness(
                        market_status, at=terminal_capture,
                    )
                except Exception:  # noqa: BLE001 - preserve primary decision category
                    market_status = replace(
                        market_status, age_s=None,
                        max_age_s=self._market_status_max_age_s,
                    )
        if order_receipt_ts is not None:
            if terminal_capture is None:
                order_age_s = None
            else:
                order_age_s, _age_failure = _freshness_age(
                    (self._prefill_order_read_started_at
                     if self._prefill_order_read_started_at is not None
                     else order_receipt_ts),
                    terminal_capture, self._account_max_age_s,
                    "ORDER_STATE",
                )
        try:
            attempt_id = attempt.attempt_id
            slug = attempt.slug
            condition_id = attempt.condition_id
            order_ids = tuple(attempt.acknowledged_order_ids)
            submitted_quotes = tuple(getattr(attempt, "quotes", ()))
            public_generation = attempt.public_generation
            user_generation = attempt.user_generation
        except (AttributeError, TypeError):
            attempt_id = 0
            slug = self.bootstrap.market.slug
            condition_id = self.bootstrap.market.condition_id
            order_ids = ()
            submitted_quotes = ()
            public_generation = self.public_generation
            user_generation = self.user_generation
        snapshot = (capital_projection.snapshot
                    if capital_projection is not None else None)
        current_inventory = inventory or (snapshot.inventory if snapshot is not None else None)
        result = RDPInitialPreFillDecision(
            action, reason, attempt_id, slug, condition_id, order_ids, submitted_quotes,
            public_generation, user_generation, captured_at, market_status,
            order_receipt_ts, order_age_s, book_filter, inventory,
            capital_projection, current_candidate, fill_window,
        )
        log.info(
            "rdp_initial_prefill_decision", action=action, reason=reason,
            slug=slug, condition_id=condition_id, attempt_id=attempt_id,
            acknowledged_order_ids=order_ids,
            submitted_quotes=tuple(
                (quote.token_id, quote.side.value, quote.price, quote.size)
                for quote in submitted_quotes
            ),
            public_generation=public_generation, user_generation=user_generation,
            captured_at=captured_at,
            market_status_receipt_ts=(market_status.receipt_ts if market_status else None),
            market_status_age_s=(market_status.age_s if market_status else None),
            order_receipt_ts=order_receipt_ts, order_age_s=order_age_s,
            book_filter_ready=(book_filter.ready if book_filter else None),
            book_filter_reasons=(book_filter.reasons if book_filter else ()),
            candidate_reason=(current_candidate.reason if current_candidate else None),
            candidate_quotes=(
                tuple((quote.token_id, quote.side.value, quote.price, quote.size)
                      for quote in current_candidate.quotes)
                if current_candidate else ()
            ),
            inventory_qy=(str(current_inventory.qy) if current_inventory is not None else None),
            inventory_qn=(str(current_inventory.qn) if current_inventory is not None else None),
            inventory_delta=(str(current_inventory.delta) if current_inventory is not None else None),
            capital_certified=(capital_projection.certified
                               if capital_projection is not None else None),
            capital_reason=(capital_projection.reason if capital_projection is not None else None),
            free_cash=(str(snapshot.free_cash) if snapshot is not None else None),
            reserved_cash=(str(snapshot.reserved_cash) if snapshot is not None else None),
            inventory_cost=(str(snapshot.inventory_cost) if snapshot is not None else None),
            fill_window_started=(fill_window.started if fill_window else None),
            fill_window_first_receipt_monotonic_ns=(
                fill_window.first_receipt_monotonic_ns if fill_window else None
            ),
            fill_window_deadline_monotonic_ns=(
                fill_window.deadline_monotonic_ns if fill_window else None
            ),
            execution_authorized=False,
        )
        return result

    def _prefill_scope_failure(
        self, attempt: RDPPlacementAttempt,
        registry: tuple[tuple[Any, ...], ...] | None = None,
    ) -> tuple[str | None, tuple[tuple[Any, ...], ...] | None]:
        if self.placement_owner.current is not attempt:
            return "PLACEMENT_ATTEMPT_CHANGED", None
        if (attempt.slug != self.bootstrap.market.slug
                or attempt.condition_id != self.bootstrap.market.condition_id
                or not _is_complete_initial_pair(self.bootstrap, attempt)
                or attempt.pending_anomalies or not attempt.replacement_blocked
                or attempt.execution_authorized):
            return "PLACEMENT_SCOPE_UNRESOLVED", None
        current_registry = _placement_registry_snapshot(
            self.processor, self.bootstrap, attempt,
        )
        if current_registry is None:
            return "OWNERSHIP_SCOPE_CONTRADICTION", None
        if registry is not None and current_registry != registry:
            return "OWNERSHIP_SCOPE_CHANGED", None
        return None, current_registry

    def _prefill_system_failure(
        self, attempt: RDPPlacementAttempt,
        registry: tuple[tuple[Any, ...], ...],
        *, at: float | None = None,
    ) -> str | None:
        if self._closed:
            return "SESSION_CLOSED"
        try:
            current_time = self._now() if at is None else at
            if (isinstance(current_time, bool)
                    or not isinstance(current_time, (int, float))
                    or not math.isfinite(current_time)):
                return "INVALID_EVALUATION_TIME"
            if current_time >= self.bootstrap.end_epoch:
                return "MARKET_EXPIRED"
            failure = self._health_failure()
        except Exception as exc:  # noqa: BLE001 - never expose source exception text
            return f"INPUT_HEALTH_FAILED:{type(exc).__name__}"
        if failure is not None:
            return failure
        try:
            if (self.public_session.service.connection_generation != self.public_generation
                    or self.user_session.observation.user_channel.connection_generation
                    != self.user_generation
                    or attempt.public_generation != self.public_generation
                    or attempt.user_generation != self.user_generation):
                return "INPUT_GENERATION_CHANGED"
            scope_failure, _ = self._prefill_scope_failure(attempt, registry)
            if scope_failure is not None:
                return scope_failure
            if _has_public_input_backlog(self.public_session.service.metrics):
                return "PUBLIC_INPUT_BACKLOG"
        except Exception as exc:  # noqa: BLE001 - corrupted input scope is not safe
            return f"INPUT_STATE_FAILED:{type(exc).__name__}"
        return None

    def _prefill_terminal_read_check(
        self, attempt: RDPPlacementAttempt,
        registry: tuple[tuple[Any, ...], ...],
        market_status: RDPMarketStatusEvidence,
        order_receipt_ts: float,
    ) -> tuple[float | None, RDPMarketStatusEvidence, float | None, str | None]:
        """Validate all read-based positive evidence at one final capture time."""
        try:
            captured_at = self._now()
        except Exception as exc:  # noqa: BLE001 - failed time is not positive evidence
            return None, market_status, None, f"EVALUATION_TIME_FAILED:{type(exc).__name__}"
        if (isinstance(captured_at, bool) or not isinstance(captured_at, (int, float))
                or not math.isfinite(captured_at)):
            return None, market_status, None, "INVALID_EVALUATION_TIME"

        # Evaluate every available age against this same timestamp even when
        # another system failure is already primary, so result/log evidence is
        # internally consistent on every terminal path.
        system_failure = self._prefill_system_failure(attempt, registry, at=captured_at)
        try:
            market_status, status_failure = self._evaluate_market_status_freshness(
                market_status, at=captured_at,
            )
        except Exception as exc:  # noqa: BLE001 - freshness evidence fails closed
            status_failure = f"MARKET_STATUS_FRESHNESS_FAILED:{type(exc).__name__}"
            market_status = replace(
                market_status, age_s=None, max_age_s=self._market_status_max_age_s,
            )
        order_age, order_failure = _freshness_age(
            (self._prefill_order_read_started_at
             if self._prefill_order_read_started_at is not None else order_receipt_ts),
            captured_at, self._account_max_age_s, "ORDER_STATE",
        )
        return (captured_at, market_status, order_age,
                system_failure or status_failure or order_failure)

    def _prefill_status_identity_failure(
        self, evidence: RDPMarketStatusEvidence,
    ) -> str | None:
        market = self.bootstrap.market
        if (evidence.slug != market.slug
                or evidence.condition_id != market.condition_id
                or evidence.token_ids != tuple(token.token_id for token in market.tokens)
                or evidence.outcomes != tuple(token.outcome for token in market.tokens)
                or evidence.neg_risk is not market.neg_risk
                or evidence.end_epoch != self.bootstrap.end_epoch):
            return "MARKET_STATUS_IDENTITY_MISMATCH"
        if evidence.accepting_orders is not True or evidence.closed is not False:
            return "CURRENT_MARKET_TRADABILITY"
        return None

    async def _run_initial_prefill_evaluation(
        self, attempt: RDPPlacementAttempt,
    ) -> RDPInitialPreFillDecision:
        scope_failure, registry = self._prefill_scope_failure(attempt)
        if scope_failure is not None or registry is None:
            return self._prefill_decision("CLOSE", scope_failure or "OWNERSHIP_SCOPE_CONTRADICTION", attempt)
        if self._admission_active:
            return self._prefill_decision("CLOSE", "INITIAL_ADMISSION_IN_PROGRESS", attempt)
        failure = self._prefill_system_failure(attempt, registry)
        if failure is not None:
            return self._prefill_decision("CLOSE", failure, attempt)

        window = self.initial_fill_window()
        if not window.reliable:
            return self._prefill_decision("CLOSE", window.reason or "FILL_EVIDENCE_UNRELIABLE",
                                          attempt, fill_window=window)
        if window.started:
            return self._prefill_decision("FILL_WINDOW", "CONFIRMED_FILL_HANDOFF", attempt,
                                          fill_window=window)

        status_task = asyncio.create_task(
            read_rdp_market_status(self.bootstrap, self._gamma_origin, now=self._now),
            name="rdp-initial-prefill-market-status-read",
        )
        self._market_status_read_task = status_task
        try:
            try:
                market_status = await asyncio.shield(status_task)
            except asyncio.CancelledError:
                await _drain_read_task(status_task)
                raise
            except RDPBootstrapError as exc:
                return self._prefill_decision("CLOSE", exc.category, attempt)
            except Exception as exc:  # noqa: BLE001 - categorical diagnostic only
                return self._prefill_decision(
                    "CLOSE", f"MARKET_STATUS_READ_FAILED:{type(exc).__name__}", attempt,
                )
        finally:
            if status_task.done() and self._market_status_read_task is status_task:
                self._market_status_read_task = None
        failure = self._prefill_system_failure(attempt, registry)
        if failure is not None:
            return self._prefill_decision("CLOSE", failure, attempt, market_status=market_status)
        try:
            status_checked_at = self._now()
        except Exception as exc:  # noqa: BLE001 - evidence time must fail closed
            return self._prefill_decision(
                "CLOSE", f"EVALUATION_TIME_FAILED:{type(exc).__name__}", attempt,
                market_status=market_status,
            )
        market_status, status_failure = self._evaluate_market_status_freshness(
            market_status, at=status_checked_at,
        )
        if status_failure is not None:
            return self._prefill_decision(
                "CLOSE", status_failure, attempt, captured_at=status_checked_at,
                market_status=market_status,
            )
        status_identity_failure = self._prefill_status_identity_failure(market_status)
        if status_identity_failure is not None:
            return self._prefill_decision(
                "CLOSE", status_identity_failure, attempt,
                captured_at=status_checked_at, market_status=market_status,
            )

        gateway = getattr(self.user_session, "gateway", None)
        if gateway is None:
            return self._prefill_decision(
                "CLOSE", "ORDER_GATEWAY_UNAVAILABLE", attempt, market_status=market_status,
            )
        try:
            order_read_started_at = self._now()
        except Exception as exc:  # noqa: BLE001 - do not start an unowned read
            return self._prefill_decision(
                "CLOSE", f"EVALUATION_TIME_FAILED:{type(exc).__name__}", attempt,
                market_status=market_status,
            )
        self._prefill_order_read_started_at = order_read_started_at
        order_task = asyncio.create_task(
            _StrictCloseGateway(gateway).open_orders(),
            name="rdp-initial-prefill-open-orders-read",
        )
        self._prefill_read_task = order_task
        try:
            try:
                open_orders = await asyncio.shield(order_task)
                order_receipt_ts = self._now()
            except asyncio.CancelledError:
                await _drain_read_task(order_task)
                raise
            except Exception as exc:  # noqa: BLE001 - categorical diagnostic only
                return self._prefill_decision(
                    "CLOSE", f"ORDER_STATE_READ_FAILED:{type(exc).__name__}", attempt,
                    market_status=market_status,
                )
        finally:
            if order_task.done() and self._prefill_read_task is order_task:
                self._prefill_read_task = None

        try:
            captured_at = self._now()
        except Exception as exc:  # noqa: BLE001
            return self._prefill_decision(
                "CLOSE", f"EVALUATION_TIME_FAILED:{type(exc).__name__}", attempt,
                market_status=market_status, order_receipt_ts=order_receipt_ts,
            )
        failure = self._prefill_system_failure(attempt, registry)
        if failure is not None:
            return self._prefill_decision(
                "CLOSE", failure, attempt, captured_at=captured_at,
                market_status=market_status, order_receipt_ts=order_receipt_ts,
            )

        market_status, status_failure = self._evaluate_market_status_freshness(
            market_status, at=captured_at,
        )
        if status_failure is not None:
            return self._prefill_decision(
                "CLOSE", status_failure, attempt, captured_at=captured_at,
                market_status=market_status, order_receipt_ts=order_receipt_ts,
            )
        status_identity_failure = self._prefill_status_identity_failure(market_status)
        if status_identity_failure is not None:
            return self._prefill_decision(
                "CLOSE", status_identity_failure, attempt,
                captured_at=captured_at, market_status=market_status,
                order_receipt_ts=order_receipt_ts,
            )

        order_age, order_failure = _freshness_age(
            order_read_started_at, captured_at, self._account_max_age_s,
            "ORDER_STATE",
        )
        if order_failure is not None:
            return self._prefill_decision(
                "CLOSE", order_failure, attempt, captured_at=captured_at,
                market_status=market_status, order_receipt_ts=order_receipt_ts,
                order_age_s=order_age,
            )
        window = self.initial_fill_window()
        if not window.reliable:
            return self._prefill_decision(
                "CLOSE", window.reason or "FILL_EVIDENCE_UNRELIABLE", attempt,
                captured_at=captured_at, market_status=market_status,
                order_receipt_ts=order_receipt_ts, order_age_s=order_age,
                fill_window=window,
            )

        tokens = {self.bootstrap.market.yes.token_id, self.bootstrap.market.no.token_id}
        if not isinstance(open_orders, list) or any(
            not isinstance(row, OpenOrder)
            or not isinstance(row.order_id, str) or not row.order_id.strip()
            or not isinstance(row.token_id, str) or not row.token_id.strip()
            or not isinstance(row.side, Side)
            or not isinstance(row.price, Decimal) or not row.price.is_finite()
            or not isinstance(row.size, Decimal)
            or not row.size.is_finite() or row.size <= 0
            for row in open_orders
        ):
            return self._prefill_decision(
                "CLOSE", "MALFORMED_OPEN_ORDER_EVIDENCE", attempt,
                captured_at=captured_at, market_status=market_status,
                order_receipt_ts=order_receipt_ts, order_age_s=order_age,
            )
        # Validate identity globally before selecting this market's tokens.
        # An acknowledged ID repeated on a foreign token is still a collision,
        # while uniquely identified unrelated foreign orders remain ignored.
        order_ids = [row.order_id for row in open_orders]
        if len(order_ids) != len(set(order_ids)):
            return self._prefill_decision(
                "CLOSE", "DUPLICATE_OPEN_ORDER_ID", attempt, captured_at=captured_at,
                market_status=market_status, order_receipt_ts=order_receipt_ts,
                order_age_s=order_age,
            )
        owned_ids = set(attempt.acknowledged_order_ids)
        quotes_by_id: dict[str, Any] = {}
        registry_by_id = {row[0]: row for row in registry}
        for order_id in attempt.acknowledged_order_ids:
            owned = registry_by_id.get(order_id)
            quote = next((item for item in attempt.quotes
                          if owned is not None and item.token_id == owned[2]), None)
            if quote is None:
                return self._prefill_decision(
                    "CLOSE", "OWNERSHIP_SCOPE_CONTRADICTION", attempt,
                    captured_at=captured_at, market_status=market_status,
                    order_receipt_ts=order_receipt_ts, order_age_s=order_age,
                )
            quotes_by_id[order_id] = quote
        for row in open_orders:
            quote = quotes_by_id.get(row.order_id)
            if quote is not None and (
                row.token_id != quote.token_id
                or row.side is not quote.side
                or not _same_decimal(row.price, quote.price)
            ):
                return self._prefill_decision(
                    "CLOSE", "OPEN_ORDER_IDENTITY_OR_PRICE_MISMATCH", attempt,
                    captured_at=captured_at, market_status=market_status,
                    order_receipt_ts=order_receipt_ts, order_age_s=order_age,
                )
        selected_orders = [row for row in open_orders if row.token_id in tokens]
        if any(row.order_id not in owned_ids for row in selected_orders):
            return self._prefill_decision(
                "CLOSE", "FOREIGN_SELECTED_OPEN_ORDER", attempt, captured_at=captured_at,
                market_status=market_status, order_receipt_ts=order_receipt_ts,
                order_age_s=order_age,
            )
        orders_by_id = {row.order_id: row for row in selected_orders}
        expected_remaining: dict[str, Decimal] = {}
        for order_id, quote in quotes_by_id.items():
            confirmed = _prefill_confirmed_quantity(self.processor, order_id)
            if confirmed is None:
                return self._prefill_decision(
                    "CLOSE", "FILL_EVIDENCE_UNRELIABLE", attempt,
                    captured_at=captured_at, market_status=market_status,
                    order_receipt_ts=order_receipt_ts, order_age_s=order_age,
                )
            submitted = _decimal_value(quote.size)
            if submitted is None or confirmed > submitted:
                return self._prefill_decision(
                    "CLOSE", "FILL_ECONOMICS_UNRELIABLE", attempt,
                    captured_at=captured_at, market_status=market_status,
                    order_receipt_ts=order_receipt_ts, order_age_s=order_age,
                )
            with localcontext() as context:
                context.prec = 64
                expected_remaining[order_id] = submitted - confirmed
            row = orders_by_id.get(order_id)
            if expected_remaining[order_id] == 0:
                if row is not None:
                    return self._prefill_decision(
                        "CLOSE", "FULLY_FILLED_ORDER_STILL_OPEN", attempt,
                        captured_at=captured_at, market_status=market_status,
                        order_receipt_ts=order_receipt_ts, order_age_s=order_age,
                        fill_window=window,
                    )
                continue
            if row is None:
                return self._prefill_decision(
                    "CLOSE", "ACKNOWLEDGED_ORDER_MISSING", attempt,
                    captured_at=captured_at, market_status=market_status,
                    order_receipt_ts=order_receipt_ts, order_age_s=order_age,
                    fill_window=window,
                )
            if (row.token_id != quote.token_id or row.side is not quote.side
                    or not _same_decimal(row.price, quote.price)):
                return self._prefill_decision(
                    "CLOSE", "OPEN_ORDER_IDENTITY_OR_PRICE_MISMATCH", attempt,
                    captured_at=captured_at, market_status=market_status,
                    order_receipt_ts=order_receipt_ts, order_age_s=order_age,
                    fill_window=window,
                )
            if not _same_decimal(row.size, expected_remaining[order_id]):
                return self._prefill_decision(
                    "CLOSE", "OPEN_ORDER_REMAINING_QUANTITY_CHANGED", attempt,
                    captured_at=captured_at, market_status=market_status,
                    order_receipt_ts=order_receipt_ts, order_age_s=order_age,
                    fill_window=window,
                )

        failure = self._prefill_system_failure(attempt, registry)
        if failure is not None:
            return self._prefill_decision(
                "CLOSE", failure, attempt, captured_at=captured_at,
                market_status=market_status, order_receipt_ts=order_receipt_ts,
                order_age_s=order_age, fill_window=window,
            )
        if not window.reliable:
            return self._prefill_decision(
                "CLOSE", window.reason or "FILL_EVIDENCE_UNRELIABLE", attempt,
                captured_at=captured_at, market_status=market_status,
                order_receipt_ts=order_receipt_ts, order_age_s=order_age,
                fill_window=window,
            )
        if window.started:
            terminal_at, terminal_status, terminal_order_age, terminal_failure = (
                self._prefill_terminal_read_check(
                    attempt, registry, market_status, order_receipt_ts,
                )
            )
            if terminal_failure is not None:
                return self._prefill_decision(
                    "CLOSE", terminal_failure, attempt, captured_at=terminal_at,
                    market_status=terminal_status, order_receipt_ts=order_receipt_ts,
                    order_age_s=terminal_order_age, fill_window=window,
                )
            return self._prefill_decision(
                "FILL_WINDOW", "CONFIRMED_FILL_HANDOFF", attempt,
                captured_at=terminal_at, market_status=terminal_status,
                order_receipt_ts=order_receipt_ts, order_age_s=terminal_order_age,
                inventory=window.inventory,
                fill_window=window,
            )

        inventory = None
        projection = project_rdp_capital(
            self.bootstrap.market, self.processor, owned_ids, selected_orders,
            market_state_reliable=True, order_state_reliable=True,
        )
        if not projection.certified or projection.snapshot is None:
            return self._prefill_decision(
                "CLOSE", projection.reason or "CAPITAL_PROJECTION_UNRELIABLE", attempt,
                captured_at=captured_at, market_status=market_status,
                order_receipt_ts=order_receipt_ts, order_age_s=order_age,
                capital_projection=projection, fill_window=window,
            )
        inventory = projection.snapshot.inventory

        service = self.public_session.service
        books = (service.book(self.bootstrap.market.yes.token_id),
                 service.book(self.bootstrap.market.no.token_id))
        if any(book is None for book in books):
            return self._prefill_decision(
                "CLOSE", "BOOK_UNAVAILABLE", attempt, captured_at=captured_at,
                market_status=market_status, order_receipt_ts=order_receipt_ts,
                order_age_s=order_age, inventory=inventory,
                capital_projection=projection, fill_window=window,
            )
        yes_book, no_book = books
        assert yes_book is not None and no_book is not None
        if any(book.snapshot_generation != self.public_generation for book in books):
            return self._prefill_decision(
                "CLOSE", "BOOK_GENERATION_CHANGED", attempt, captured_at=captured_at,
                market_status=market_status, order_receipt_ts=order_receipt_ts,
                order_age_s=order_age, inventory=inventory,
                capital_projection=projection, fill_window=window,
            )
        for book in books:
            age, age_failure = _freshness_age(
                book.local_ts, captured_at, self._market_data_max_age_s, "MARKET_DATA",
            )
            if age_failure is not None:
                return self._prefill_decision(
                    "CLOSE", age_failure, attempt, captured_at=captured_at,
                    market_status=market_status, order_receipt_ts=order_receipt_ts,
                    order_age_s=order_age, inventory=inventory,
                    capital_projection=projection, fill_window=window,
                )

        try:
            book_filter = self.book_filter_result()
        except Exception as exc:  # noqa: BLE001 - filter failures are not safe to rest
            return self._prefill_decision(
                "CLOSE", f"BOOK_FILTER_FAILED:{type(exc).__name__}", attempt,
                captured_at=captured_at, market_status=market_status,
                order_receipt_ts=order_receipt_ts, order_age_s=order_age,
                inventory=inventory, capital_projection=projection,
                fill_window=window,
            )
        if not book_filter.ready:
            return self._prefill_decision(
                "CLOSE", _filter_rejection_reason(book_filter), attempt,
                captured_at=captured_at, market_status=market_status,
                order_receipt_ts=order_receipt_ts, order_age_s=order_age,
                book_filter=book_filter, inventory=inventory,
                capital_projection=projection, fill_window=window,
            )
        if book_filter.rejected:
            return self._prefill_decision(
                "CLOSE", _filter_rejection_reason(book_filter), attempt,
                captured_at=captured_at, market_status=market_status,
                order_receipt_ts=order_receipt_ts, order_age_s=order_age,
                book_filter=book_filter, inventory=inventory,
                capital_projection=projection, fill_window=window,
            )
        metadata = _validated_token_metadata(self.bootstrap)
        if metadata is None:
            return self._prefill_decision(
                "CLOSE", "INVALID_TOKEN_METADATA", attempt, captured_at=captured_at,
                market_status=market_status, order_receipt_ts=order_receipt_ts,
                order_age_s=order_age, book_filter=book_filter, inventory=inventory,
                capital_projection=projection, fill_window=window,
            )
        decision_market = replace(
            self.bootstrap.market,
            min_order_size=max(metadata[token][1] for token in tokens),
        )
        candidate = build_normal_quotes(
            decision_market, inventory, yes_book.view(), no_book.view(),
            yes_book.tick_size, no_book.tick_size,
        )
        if candidate.reason is not None:
            return self._prefill_decision(
                "CLOSE", candidate.reason, attempt, captured_at=captured_at,
                market_status=market_status, order_receipt_ts=order_receipt_ts,
                order_age_s=order_age, book_filter=book_filter, inventory=inventory,
                capital_projection=projection, current_candidate=candidate,
                fill_window=window,
            )
        current_quotes = {quote.token_id: quote for quote in candidate.quotes}
        for quote in attempt.quotes:
            current = current_quotes.get(quote.token_id)
            if current is None or not _same_decimal(current.price, quote.price):
                return self._prefill_decision(
                    "CLOSE", "QUOTE_PRICE_CHANGED", attempt, captured_at=captured_at,
                    market_status=market_status, order_receipt_ts=order_receipt_ts,
                    order_age_s=order_age, book_filter=book_filter, inventory=inventory,
                    capital_projection=projection, current_candidate=candidate,
                    fill_window=window,
                )
            if not _same_decimal(current.size, quote.size):
                return self._prefill_decision(
                    "CLOSE", "QUOTE_SIZE_CHANGED", attempt, captured_at=captured_at,
                    market_status=market_status, order_receipt_ts=order_receipt_ts,
                    order_age_s=order_age, book_filter=book_filter, inventory=inventory,
                    capital_projection=projection, current_candidate=candidate,
                    fill_window=window,
                )
        captured_at, final_status, order_age, failure = self._prefill_terminal_read_check(
            attempt, registry, market_status, order_receipt_ts,
        )
        if failure is not None:
            return self._prefill_decision(
                "CLOSE", failure, attempt, captured_at=captured_at,
                market_status=final_status, order_receipt_ts=order_receipt_ts,
                order_age_s=order_age, book_filter=book_filter, inventory=inventory,
                capital_projection=projection, current_candidate=candidate,
                fill_window=window,
            )
        if not window.reliable:
            return self._prefill_decision(
                "CLOSE", window.reason or "FILL_EVIDENCE_UNRELIABLE", attempt,
                captured_at=captured_at, market_status=final_status,
                order_receipt_ts=order_receipt_ts, order_age_s=order_age,
                book_filter=book_filter, inventory=inventory,
                capital_projection=projection, current_candidate=candidate,
                fill_window=window,
            )
        if window.started:
            return self._prefill_decision(
                "FILL_WINDOW", "CONFIRMED_FILL_HANDOFF", attempt,
                captured_at=captured_at, market_status=final_status,
                order_receipt_ts=order_receipt_ts, order_age_s=order_age,
                book_filter=book_filter, inventory=inventory,
                capital_projection=projection, current_candidate=candidate,
                fill_window=window,
            )
        return self._prefill_decision(
            "REST", "ACKNOWLEDGED_PAIR_STILL_VALID", attempt,
            captured_at=captured_at, market_status=final_status,
            order_receipt_ts=order_receipt_ts, order_age_s=order_age,
            book_filter=book_filter, inventory=inventory,
            capital_projection=projection, current_candidate=candidate,
            fill_window=window,
        )

    def initial_fill_window(
        self, *, now_monotonic_ns: int | None = None,
    ) -> RDPFillWindowDecision:
        """Read the current attempt's receipt-time window without external calls."""
        now_ns = self._monotonic_ns() if now_monotonic_ns is None else now_monotonic_ns
        if isinstance(now_ns, bool) or not isinstance(now_ns, int) or now_ns < 0:
            raise ValueError("monotonic timestamp must be a non-negative integer")
        attempt = self.placement_owner.current
        if attempt is None:
            return RDPFillWindowDecision(
                0, self.bootstrap.market.slug, self.bootstrap.market.condition_id,
                (), False, "NO_CURRENT_PLACEMENT", False, None,
                self._fill_window_s, None, False, None,
            )
        window = self._fill_windows.get(attempt.attempt_id)
        if window is None:
            window = RDPFillWindow(self._fill_window_s)
            self._fill_windows[attempt.attempt_id] = window
        window.bind(attempt.attempt_id)

        reason: str | None = None
        if self.placement_owner.current is not attempt:
            reason = "PLACEMENT_ATTEMPT_CHANGED"
        elif (attempt.slug != self.bootstrap.market.slug
              or attempt.condition_id != self.bootstrap.market.condition_id
              or not _is_complete_initial_pair(self.bootstrap, attempt)
              or not attempt.replacement_blocked
              or attempt.execution_authorized):
            reason = "PLACEMENT_SCOPE_UNRESOLVED"
        elif self._closed:
            reason = "SESSION_CLOSED"
        elif self._now() >= self.bootstrap.end_epoch:
            reason = "MARKET_EXPIRED"
        else:
            try:
                reason = self._health_failure()
            except Exception as exc:  # noqa: BLE001 - fail closed, expose category only
                reason = f"INPUT_HEALTH_FAILED:{type(exc).__name__}"
            if reason is None and (
                self.public_session.service.connection_generation != self.public_generation
                or self.user_session.observation.user_channel.connection_generation != self.user_generation
                or attempt.public_generation != self.public_generation
                or attempt.user_generation != self.user_generation
            ):
                reason = "INPUT_GENERATION_CHANGED"

        order_ids = tuple(attempt.acknowledged_order_ids)
        processor = self.processor
        if reason is None and (
            getattr(processor, "_pending", None)
            or getattr(processor, "_pending_events", None)
            or getattr(processor, "_pending_overflow", False)
        ):
            reason = "PENDING_CAPTURE_UNRESOLVED"
        if reason is None and (
            set(order_ids) & set(getattr(processor, "quarantined_orders", ()))
        ):
            reason = "FILL_EVIDENCE_QUARANTINED"

        accepted_confirmations: list[tuple[int, int, str]] = []
        receipt_map = getattr(processor, "confirmed_receipts", None)
        if reason is None:
            if not isinstance(receipt_map, dict):
                if any(oid in set(order_ids) for _tid, oid in processor.applied_fill_keys):
                    reason = "CONFIRMED_RECEIPT_UNAVAILABLE"
            else:
                for key in processor.applied_fill_keys:
                    trade_id, order_id = key
                    if order_id not in order_ids:
                        continue
                    event = processor.fill_records.get(key)
                    if event is None or event.status is not TradeState.CONFIRMED:
                        continue
                    if (key in processor.quarantined_fill_keys
                            or processor.has_failed_fill(order_id)
                            or processor.has_unresolved_fill_key(order_id)):
                        reason = "FILL_EVIDENCE_UNRELIABLE"
                        break
                    receipt = receipt_map.get(key)
                    if not isinstance(receipt, tuple) or len(receipt) != 4:
                        reason = "CONFIRMED_RECEIPT_UNAVAILABLE"
                        break
                    receipt_us, receipt_ns, generation, source = receipt
                    if (isinstance(receipt_us, bool) or not isinstance(receipt_us, int)
                            or receipt_us <= 0 or isinstance(receipt_ns, bool)
                            or not isinstance(receipt_ns, int) or receipt_ns <= 0
                            or isinstance(generation, bool) or not isinstance(generation, int)
                            or generation != self.user_generation
                            or source != "WS"):
                        reason = "CONFIRMED_RECEIPT_INVALID"
                        break
                    if receipt_ns > now_ns:
                        reason = "CONFIRMED_RECEIPT_IN_FUTURE"
                        break
                    accepted_confirmations.append((receipt_ns, receipt_us, trade_id))

        inventory = None
        if reason is None:
            try:
                inventory = snapshot_inventory(
                    self.bootstrap.market, processor, set(order_ids),
                )
            except Exception as exc:  # noqa: BLE001 - accounting evidence must fail closed
                reason = f"INVENTORY_UNRELIABLE:{type(exc).__name__}"

        if reason is None and accepted_confirmations:
            earliest = min(receipt_ns for receipt_ns, _wall_us, _trade_id in accepted_confirmations)
            try:
                window.start(attempt.attempt_id, (earliest,))
            except (TypeError, ValueError):
                reason = "CONFIRMED_RECEIPT_INVALID"
            if reason is None and window.timing_contradiction:
                reason = "CONFIRMED_RECEIPT_PRECEDES_ESTABLISHED_WINDOW"

        deadline = window.deadline_monotonic_ns
        started = window.first_receipt_monotonic_ns is not None
        due = reason is None and deadline is not None and now_ns >= deadline
        if reason is None and started and not window._logged_start:
            window._logged_start = True
            log.info(
                "rdp_fill_window_started", condition_id=attempt.condition_id,
                attempt_id=attempt.attempt_id, acknowledged_order_ids=order_ids,
                public_generation=attempt.public_generation,
                user_generation=attempt.user_generation,
                first_receipt_monotonic_ns=window.first_receipt_monotonic_ns,
                duration_s=window.duration_s, deadline_monotonic_ns=deadline,
                reliable=True,
            )
        if due and not window._logged_expiry:
            window._logged_expiry = True
            log.info(
                "rdp_fill_window_expired", condition_id=attempt.condition_id,
                attempt_id=attempt.attempt_id, acknowledged_order_ids=order_ids,
                public_generation=attempt.public_generation,
                user_generation=attempt.user_generation,
                first_receipt_monotonic_ns=window.first_receipt_monotonic_ns,
                duration_s=window.duration_s, deadline_monotonic_ns=deadline,
                reliable=True,
            )
        return RDPFillWindowDecision(
            attempt_id=attempt.attempt_id, slug=attempt.slug,
            condition_id=attempt.condition_id,
            acknowledged_order_ids=order_ids, reliable=reason is None,
            reason=reason or (None if started else "AWAITING_CONFIRMED_FILL"),
            started=started,
            first_receipt_monotonic_ns=window.first_receipt_monotonic_ns,
            duration_s=window.duration_s, deadline_monotonic_ns=deadline,
            closure_due=due, inventory=inventory,
        )

    @property
    def fill_window_completion_result(self) -> RDPFillWindowCompletion | None:
        """Return the last completed explicitly activated FillWindow action."""
        return self._fill_window_result

    async def complete_initial_fill_window(
        self, attempt: RDPPlacementAttempt, *, close_budget_s: float,
    ) -> RDPFillWindowCompletion:
        """Wait for the retained window, then run its existing scoped close once."""
        budget_s = _positive_finite_seconds(close_budget_s, "close budget")
        if self._prefill_task is not None and not self._prefill_task.done():
            raise RDPPlacementError("PREFILL_EVALUATION_IN_PROGRESS")
        active = self._fill_window_task
        if active is not None and not active.done():
            raise RDPPlacementError("FILL_WINDOW_COORDINATOR_IN_PROGRESS")
        if self._fill_window_result is not None:
            if (self._fill_window_result.attempt_id == attempt.attempt_id
                    and self.placement_owner.current is attempt):
                return self._fill_window_result
            raise RDPPlacementError("FILL_WINDOW_COORDINATOR_ALREADY_USED")
        current = self.placement_owner.current
        if (self._closed or current is None or attempt is not current
                or attempt.slug != self.bootstrap.market.slug
                or attempt.condition_id != self.bootstrap.market.condition_id
                or not _is_complete_initial_pair(self.bootstrap, attempt)
                or not attempt.replacement_blocked or attempt.execution_authorized):
            raise RDPPlacementError("FILL_WINDOW_ATTEMPT_SCOPE_INVALID")
        if self._placement_close_task is not None and not self._placement_close_task.done():
            raise RDPPlacementError("PLACEMENT_CLOSE_IN_PROGRESS")
        registry_snapshot = _placement_registry_snapshot(
            self.processor, self.bootstrap, attempt,
        )
        if registry_snapshot is None:
            raise RDPPlacementError("FILL_WINDOW_REGISTRY_MISMATCH")
        decision = self.initial_fill_window()
        if (decision.attempt_id != attempt.attempt_id or not decision.started
                or decision.deadline_monotonic_ns is None):
            raise RDPPlacementError("FILL_WINDOW_NOT_STARTED")

        self._fill_window_cancel_requested = None
        self._fill_window_wakeup.clear()
        task = asyncio.create_task(
            self._run_initial_fill_window_completion(
                attempt, registry_snapshot, decision.deadline_monotonic_ns, budget_s,
            ),
            name="rdp-initial-fill-window-completion",
        )
        self._fill_window_task = task
        log.info(
            "rdp_fill_window_completion_started", condition_id=attempt.condition_id,
            attempt_id=attempt.attempt_id,
            acknowledged_order_ids=attempt.acknowledged_order_ids,
            window_deadline_monotonic_ns=decision.deadline_monotonic_ns,
            close_budget_s=budget_s, execution_authorized=False,
        )
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            self._fill_window_cancel_requested = "CALLER_CANCELLED"
            self._fill_window_wakeup.set()
            await _drain_read_task(task)
            raise

    async def _run_initial_fill_window_completion(
        self, attempt: RDPPlacementAttempt,
        registry_snapshot: tuple[tuple[Any, ...], ...],
        fixed_deadline_ns: int, close_budget_s: float,
    ) -> RDPFillWindowCompletion:
        started_ns = self._monotonic_ns()
        trigger: str | None = None
        while trigger is None:
            if self._fill_window_cancel_requested is not None:
                trigger = self._fill_window_cancel_requested
                break
            if self._closed:
                trigger = "SESSION_TEARDOWN"
                break
            if self.placement_owner.current is not attempt:
                trigger = "ATTEMPT_SCOPE_CHANGED"
                break
            if _placement_registry_snapshot(self.processor, self.bootstrap, attempt) != registry_snapshot:
                trigger = "REGISTRY_SCOPE_CHANGED"
                break
            try:
                decision = self.initial_fill_window(now_monotonic_ns=self._monotonic_ns())
            except Exception as exc:  # noqa: BLE001 - fail closed to scoped closure
                trigger = f"WINDOW_EVALUATION_FAILED:{type(exc).__name__}"
                break
            if self._now() >= self.bootstrap.end_epoch or decision.reason == "MARKET_EXPIRED":
                trigger = "MARKET_END"
                break
            if decision.reason == "SESSION_CLOSED":
                trigger = "SESSION_TEARDOWN"
                break
            if not decision.reliable:
                trigger = f"WINDOW_UNRELIABLE:{decision.reason or 'UNSPECIFIED'}"
                break
            if (not decision.started or decision.deadline_monotonic_ns != fixed_deadline_ns):
                trigger = "WINDOW_DEADLINE_CHANGED"
                break
            if decision.closure_due:
                trigger = "WINDOW_EXPIRED"
                break
            remaining_ns = fixed_deadline_ns - self._monotonic_ns()
            market_remaining = self.bootstrap.end_epoch - self._now()
            if remaining_ns <= 0:
                continue
            wait_s = min(
                self._poll_interval_s,
                remaining_ns / 1_000_000_000,
                max(0.0, market_remaining),
            )
            if wait_s <= 0:
                trigger = "MARKET_END" if market_remaining <= 0 else "WINDOW_EXPIRED"
                break
            try:
                wait_failure = await self._wait_for_fill_window_wakeup(wait_s)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - avoid leaking waiter details
                wait_failure = f"WINDOW_WAIT_FAILED:{type(exc).__name__}"
            if self._fill_window_cancel_requested is not None:
                continue
            if wait_failure is not None:
                trigger = wait_failure

        operational_deadline = asyncio.get_running_loop().time() + close_budget_s
        close_result: RDPInitialPlacementClose
        try:
            close_result = await self.close_initial_normal_placement(
                attempt, deadline=operational_deadline,
                _window_continuation_safe=(trigger == "WINDOW_EXPIRED"),
            )
        except asyncio.CancelledError:
            # Coordinator ownership is retained by the session. This task is
            # only cancelled by external teardown bugs; never retry closure.
            raise
        except Exception as exc:  # noqa: BLE001 - retain a sanitized immutable result
            close_result = self._placement_close_result(
                attempt, None, None,
                reason=f"COORDINATED_CLOSE_FAILED:{type(exc).__name__}",
                barrier_complete=False,
            )
        result = RDPFillWindowCompletion(
            attempt_id=attempt.attempt_id, slug=attempt.slug,
            condition_id=attempt.condition_id,
            acknowledged_order_ids=attempt.acknowledged_order_ids,
            trigger=trigger or "WINDOW_EXPIRED",
            window_deadline_monotonic_ns=fixed_deadline_ns,
            operational_close_deadline=operational_deadline,
            close_result=close_result, started_monotonic_ns=started_ns,
            completed_monotonic_ns=self._monotonic_ns(),
        )
        self._fill_window_result = result
        log.info(
            "rdp_fill_window_completion_finished", condition_id=attempt.condition_id,
            attempt_id=attempt.attempt_id,
            acknowledged_order_ids=attempt.acknowledged_order_ids,
            window_deadline_monotonic_ns=fixed_deadline_ns,
            operational_close_deadline=operational_deadline,
            trigger=result.trigger,
            barrier_complete=close_result.barrier_complete,
            barrier_reason=(close_result.barrier.reason if close_result.barrier else None),
            capital_certified=(close_result.capital.certified if close_result.capital else False),
            replacement_blocked=close_result.replacement_blocked,
            next_mode=(close_result.next_mode.value if close_result.next_mode else None),
            execution_authorized=False,
        )
        return result

    async def _wait_for_fill_window_wakeup(self, timeout_s: float) -> str | None:
        self._fill_window_wakeup.clear()
        sleeper: asyncio.Future[Any] | None = None
        wake: asyncio.Task[bool] | None = None
        failure: str | None = None
        try:
            try:
                sleeper = asyncio.ensure_future(self._sleep(timeout_s))
                wake = asyncio.create_task(self._fill_window_wakeup.wait())
            except Exception as exc:  # noqa: BLE001 - only a sanitized category escapes
                failure = f"WINDOW_WAIT_FAILED:{type(exc).__name__}"
            if sleeper is not None and wake is not None:
                await asyncio.wait({sleeper, wake}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            tasks = [task for task in (sleeper, wake) if task is not None]
            pending = [task for task in tasks if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            # Retrieve every completed task outcome. In particular, a failed
            # injected sleeper must not become an unhandled asyncio exception.
            for task in tasks:
                if not task.done() or task.cancelled():
                    continue
                try:
                    task.result()
                except Exception as exc:  # noqa: BLE001 - discard raw waiter error
                    if task is sleeper and failure is None:
                        failure = f"WINDOW_WAIT_FAILED:{type(exc).__name__}"
        return failure

    async def close_initial_normal_placement(
        self, attempt: RDPPlacementAttempt, *, deadline: float,
        _window_continuation_safe: bool = True,
    ) -> RDPInitialPlacementClose:
        """Explicitly close this attempt's known owned IDs through the existing barrier."""
        current = self.placement_owner.current
        if (current is None or attempt is not current
                or attempt.slug != self.bootstrap.market.slug
                or attempt.condition_id != self.bootstrap.market.condition_id
                or attempt.outcome == "ARMED"):
            raise RDPPlacementError("PLACEMENT_ATTEMPT_NOT_ACTIVE")
        coordinator = self._fill_window_task
        if (coordinator is not None and not coordinator.done()
                and asyncio.current_task() is not coordinator):
            raise RDPPlacementError("FILL_WINDOW_COORDINATOR_IN_PROGRESS")
        prefill = self._prefill_task
        if (prefill is not None and not prefill.done()
                and asyncio.current_task() is not prefill):
            raise RDPPlacementError("PREFILL_EVALUATION_IN_PROGRESS")
        active = self._placement_close_task
        if active is not None and not active.done():
            raise RDPPlacementError("PLACEMENT_CLOSE_IN_PROGRESS")

        order_ids = tuple(sorted(set(attempt.acknowledged_order_ids)))
        if not order_ids:
            return self._placement_close_result(
                current, None, None,
                reason=("PLACEMENT_OUTCOME_UNRESOLVED" if current.unresolved
                        else "NO_ATTRIBUTABLE_ORDERS"),
                barrier_complete=False,
            )

        registry_snapshot = _placement_registry_snapshot(
            self.processor, self.bootstrap, attempt,
        )
        if registry_snapshot is None:
            return self._placement_close_result(
                current, None, None, reason="OWNERSHIP_SCOPE_CONTRADICTION",
                barrier_complete=False,
            )
        task = asyncio.create_task(
            self._close_initial_normal_placement(
                attempt, order_ids, registry_snapshot, deadline,
                continuation_safe=_window_continuation_safe,
            ),
            name="rdp-initial-placement-close",
        )
        self._placement_close_task = task
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            task.cancel()
            await _drain_read_task(task)
            raise
        finally:
            if self._placement_close_task is task:
                self._placement_close_task = None

    async def _close_initial_normal_placement(
        self, attempt: RDPPlacementAttempt, order_ids: tuple[str, ...],
        registry_snapshot: tuple[tuple[Any, ...], ...], deadline: float,
        *, continuation_safe: bool = True,
    ) -> RDPInitialPlacementClose:
        gateway = getattr(self.user_session, "gateway", None)
        if gateway is None:
            return self._placement_close_result(
                attempt, None, None, reason="CLOSE_GATEWAY_UNAVAILABLE",
                barrier_complete=False,
            )
        try:
            barrier = await close_rdp_cycle(
                self.bootstrap.market, self.processor, _StrictCloseGateway(gateway),
                set(order_ids), set(order_ids), deadline,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - expose only the error category
            barrier = RDPCloseResult(
                False, f"CLOSE_OPERATION_FAILED:{type(exc).__name__}",
                order_ids, (), None,
            )

        current = self.placement_owner.current
        if current is not attempt:
            return self._placement_close_result(
                current or attempt, barrier, None,
                reason="PLACEMENT_ATTEMPT_CHANGED", barrier_complete=False,
            )
        if _placement_registry_snapshot(self.processor, self.bootstrap, attempt) != registry_snapshot:
            return self._placement_close_result(
                attempt, barrier, None, reason="OWNERSHIP_SCOPE_CHANGED",
                barrier_complete=barrier.complete,
            )

        capital: RDPCapitalProjection | None = None
        next_mode: RDPMode | None = None
        reason = barrier.reason
        full_pair = _is_complete_initial_pair(self.bootstrap, attempt)
        state_reliable, health_reason = self._close_state_reliable()
        if not continuation_safe:
            # Risk closure/reconciliation still runs and records known inventory,
            # but an interrupted or unreliable FillWindow cannot certify the next
            # action or release the placement owner.
            state_reliable = False
            health_reason = health_reason or "FILL_WINDOW_CONTINUATION_UNRELIABLE"
        if barrier.complete and full_pair:
            capital = project_rdp_capital(
                self.bootstrap.market, self.processor, set(order_ids), [],
                market_state_reliable=state_reliable,
                order_state_reliable=True,
                close_evidence=barrier,
            )
            if capital.certified and state_reliable:
                delta = capital.snapshot.inventory.delta
                try:
                    delta_value = Decimal(str(delta))
                except (InvalidOperation, ValueError, TypeError):
                    delta_value = None
                if delta_value is not None and delta_value.is_finite():
                    next_mode = (RDPMode.NORMAL if abs(delta_value) < Decimal("5")
                                 else RDPMode.REBALANCE)
            if not capital.certified:
                reason = capital.reason or "CAPITAL_CERTIFICATION_FAILED"
            elif next_mode is None:
                reason = health_reason or "CLOSE_STATE_UNRELIABLE"
        elif barrier.complete and attempt.unresolved:
            reason = "PLACEMENT_OUTCOME_UNRESOLVED"
        elif barrier.complete and not state_reliable:
            reason = health_reason or "CLOSE_STATE_UNRELIABLE"

        try:
            updated = self.placement_owner.record_close(attempt, barrier, capital)
        except RDPPlacementError:
            return self._placement_close_result(
                attempt, barrier, capital, reason="PLACEMENT_CLOSE_EVIDENCE_REJECTED",
                barrier_complete=barrier.complete,
            )
        if updated.replacement_blocked and reason is None:
            reason = ("PLACEMENT_OUTCOME_UNRESOLVED" if updated.unresolved
                      else "CLOSE_PROOF_NOT_RELEASED")
        return self._placement_close_result(
            updated, barrier, capital, reason=reason,
            barrier_complete=barrier.complete, next_mode=next_mode,
        )

    def _close_state_reliable(self) -> tuple[bool, str | None]:
        if self._closed:
            return False, "SESSION_CLOSED"
        if self._now() >= self.bootstrap.end_epoch:
            return False, "MARKET_EXPIRED"
        try:
            failure = self._health_failure()
        except Exception as exc:  # noqa: BLE001 - only a sanitized category escapes
            return False, f"INPUT_HEALTH_FAILED:{type(exc).__name__}"
        if failure is not None:
            return False, failure
        if (self.public_session.service.connection_generation != self.public_generation
                or self.user_session.observation.user_channel.connection_generation
                != self.user_generation):
            return False, "INPUT_GENERATION_CHANGED"
        return True, None

    @staticmethod
    def _placement_close_result(
        attempt: RDPPlacementAttempt, barrier: RDPCloseResult | None,
        capital: RDPCapitalProjection | None, *, reason: str | None,
        barrier_complete: bool, next_mode: RDPMode | None = None,
    ) -> RDPInitialPlacementClose:
        current = attempt
        log.info(
            "rdp_initial_placement_close", attempt_id=current.attempt_id,
            condition_id=current.condition_id,
            owned_order_ids=current.acknowledged_order_ids,
            closed_order_ids=current.closed_order_ids,
            barrier_complete=barrier_complete,
            barrier_reason=(barrier.reason if barrier is not None else None),
            capital_certified=(capital.certified if capital is not None else False),
            capital_reason=(capital.reason if capital is not None else None),
            placement_unresolved=current.unresolved,
            replacement_blocked=current.replacement_blocked,
            reason=reason, next_mode=(next_mode.value if next_mode is not None else None),
            execution_authorized=False,
        )
        return RDPInitialPlacementClose(
            current.attempt_id, current.slug, current.condition_id,
            current.acknowledged_order_ids, current.closed_order_ids,
            barrier, capital, barrier_complete, current.unresolved,
            current.replacement_blocked, reason, next_mode,
        )

    def _admission_rejected(self, reason: str, *, backing=None, account=None,
                            book_filter=None, capital_projection=None,
                            capital_admission=None, candidate=None,
                            token_ticks=(), minimum_sizes=(), market_status=None,
                            market_status_verified=False) -> RDPInitialNormalAdmission:
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
            market_status=market_status,
            outstanding_execution_gates=(
                *(("CURRENT_MARKET_TRADABILITY",) if not market_status_verified else ()),
                "CURRENT_CYCLE_RECONCILIATION",
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
        interrupted = False
        prefill = self._prefill_task
        if (prefill is not None and prefill is not asyncio.current_task()
                and not prefill.done()):
            interrupted = await _drain_read_task(prefill)
        coordinator = self._fill_window_task
        if (coordinator is not None and coordinator is not asyncio.current_task()
                and not coordinator.done()):
            self._fill_window_cancel_requested = "SESSION_TEARDOWN"
            self._fill_window_wakeup.set()
            interrupted = await _drain_read_task(coordinator)
        armed = self.placement_owner.current
        if armed is not None and armed.outcome == "ARMED":
            self.placement_owner.resolve(armed, asyncio.CancelledError())
        close_task = self._placement_close_task
        if (close_task is not None and close_task is not asyncio.current_task()
                and not close_task.done()):
            close_task.cancel()
            interrupted = await _drain_read_task(close_task)
        active = self._admission_task
        if active is not None and active is not asyncio.current_task() and not active.done():
            active.cancel()
            interrupted = await _drain_read_task(active) or interrupted
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
        if getattr(store, "orders", None):
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


class _StrictCloseGateway:
    """Request exact authenticated open orders; delegate the barrier's other reads."""

    def __init__(self, gateway: Any) -> None:
        self._gateway = gateway

    @staticmethod
    async def _call(method: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        operation = asyncio.create_task(
            method(*args, safe_diagnostics=True, **kwargs),
            name="rdp-close-gateway-operation",
        )
        interrupted = False
        while not operation.done():
            try:
                await asyncio.shield(operation)
            except asyncio.CancelledError:
                interrupted = True
            except BaseException:
                break
        if interrupted:
            # Consume a terminal SDK error but preserve the caller interruption.
            with suppress(BaseException):
                operation.result()
            raise asyncio.CancelledError
        return operation.result()

    async def open_orders(self) -> list[OpenOrder]:
        rows = await self._call(
            self._gateway.open_orders, strict_account=True, exact_decimal=True,
        )
        if not isinstance(rows, list):
            raise ValueError("strict open-order response is malformed")
        converted: list[OpenOrder] = []
        for row in rows:
            if (not isinstance(row, ExactOpenOrder)
                    or not isinstance(row.order_id, str) or not row.order_id.strip()
                    or not isinstance(row.token_id, str) or not row.token_id.strip()
                    or not isinstance(row.side, Side)
                    or not isinstance(row.price, Decimal)
                    or not isinstance(row.remaining_size, Decimal)
                    or not row.price.is_finite() or not row.remaining_size.is_finite()
                    or not Decimal("0") < row.price < Decimal("1")
                    or row.remaining_size <= 0):
                raise ValueError("strict open-order row is malformed")
            # OpenOrder is the existing barrier interface. Retain Decimal values
            # in its numeric fields instead of rounding exact account evidence.
            converted.append(OpenOrder(
                row.order_id, row.token_id, row.side, row.price,
                row.remaining_size, OrderState.LIVE,
            ))
        return converted

    async def cancel(self, order_ids: list[str]) -> bool:
        return await self._call(self._gateway.cancel, order_ids)

    async def get_order(self, order_id: str) -> Any:
        return await self._call(self._gateway.get_order, order_id)

    async def get_trades(self, *, market: str | None = None,
                         asset_id: str | None = None) -> Any:
        return await self._call(
            self._gateway.get_trades, market=market, asset_id=asset_id,
        )


def _placement_registry_snapshot(
    processor: Any, bootstrap: RDPBootstrap, attempt: RDPPlacementAttempt,
) -> tuple[tuple[Any, ...], ...] | None:
    market = bootstrap.market
    snapshot = []
    for order_id in attempt.acknowledged_order_ids:
        record = processor.owned_record(order_id)
        quote = next((item for item in attempt.quotes
                      if item.token_id == (record or {}).get("token_id")), None)
        if (record is None or quote is None
                or record.get("condition_id") != market.condition_id
                or record.get("side") != "BUY"
                or not _same_decimal(record.get("submitted_qty"), quote.size)
                or not _same_decimal(record.get("submitted_price"), quote.price)):
            return None
        snapshot.append((
            order_id, record.get("condition_id"), record.get("token_id"),
            record.get("side"), str(record.get("submitted_qty")),
            str(record.get("submitted_price")),
        ))
    if len(snapshot) != len(set(attempt.acknowledged_order_ids)):
        return None
    return tuple(sorted(snapshot))


def _same_decimal(actual: Any, expected: Any) -> bool:
    if isinstance(actual, bool) or isinstance(expected, bool):
        return False
    try:
        actual_value = actual if isinstance(actual, Decimal) else Decimal(str(actual))
        expected_value = expected if isinstance(expected, Decimal) else Decimal(str(expected))
    except (InvalidOperation, TypeError, ValueError):
        return False
    return (actual_value.is_finite() and expected_value.is_finite()
            and actual_value == expected_value)


def _freshness_age(
    receipt: Any, captured_at: Any, max_age_s: Any, label: str,
) -> tuple[float | None, str | None]:
    """Compare receipt freshness exactly while exposing only a category."""
    if (isinstance(receipt, bool) or not isinstance(receipt, (int, float))
            or not math.isfinite(receipt)):
        return None, f"INVALID_{label}_RECEIPT"
    if (isinstance(captured_at, bool) or not isinstance(captured_at, (int, float))
            or not math.isfinite(captured_at)):
        return None, f"INVALID_{label}_EVALUATION_TIME"
    try:
        age = Decimal(str(captured_at)) - Decimal(str(receipt))
        limit = Decimal(str(max_age_s))
        age_float = float(age)
    except (InvalidOperation, OverflowError, TypeError, ValueError):
        return None, f"INVALID_{label}_AGE"
    if (not age.is_finite() or not limit.is_finite() or not math.isfinite(age_float)
            or limit <= 0):
        return age_float if math.isfinite(age_float) else None, f"INVALID_{label}_AGE"
    if age < 0:
        return age_float, f"{label}_RECEIPT_IN_FUTURE"
    if age >= limit:
        return age_float, f"{label}_STALE"
    return age_float, None


def _decimal_value(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _prefill_confirmed_quantity(processor: Any, order_id: str) -> Decimal | None:
    """Sum only applied confirmed fills and reject unadmitted order evidence."""
    try:
        applied = set(processor.applied_fill_keys)
        for key, event in processor.fill_records.items():
            if event.maker_order_id != order_id:
                continue
            if (event.status in (TradeState.MATCHED, TradeState.MINED, TradeState.RETRYING)
                    or (event.status is TradeState.CONFIRMED and key not in applied)):
                return None
        total = Decimal("0")
        with localcontext() as context:
            context.prec = 64
            for event in processor.economic_fills_for_order(order_id):
                if event.status is not TradeState.CONFIRMED:
                    continue
                quantity = _decimal_value(event.size)
                if quantity is None or quantity <= 0:
                    return None
                total += quantity
        return total if total.is_finite() else None
    except Exception:  # noqa: BLE001 - malformed processor state is not certifiable
        return None


def _positive_finite_seconds(value: object, label: str) -> float:
    if isinstance(value, bool):
        raise RDPPlacementError(f"INVALID_{label.upper().replace(' ', '_')}")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise RDPPlacementError(f"INVALID_{label.upper().replace(' ', '_')}") from None
    if not number.is_finite() or number <= 0:
        raise RDPPlacementError(f"INVALID_{label.upper().replace(' ', '_')}")
    result = float(number)
    if not math.isfinite(result) or result <= 0:
        raise RDPPlacementError(f"INVALID_{label.upper().replace(' ', '_')}")
    return result


def _is_complete_initial_pair(
    bootstrap: RDPBootstrap, attempt: RDPPlacementAttempt,
) -> bool:
    expected_tokens = {bootstrap.market.yes.token_id, bootstrap.market.no.token_id}
    return (
        attempt.outcome == "COMPLETE" and not attempt.unresolved
        and not attempt.pending_anomalies and not attempt.observed_order_ids
        and len(attempt.acknowledged_order_ids) == 2
        and len(set(attempt.acknowledged_order_ids)) == 2
        and {quote.token_id for quote in attempt.quotes} == expected_tokens
        and len({quote.token_id for quote in attempt.quotes}) == 2
    )


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
