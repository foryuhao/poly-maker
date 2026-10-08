"""One-market read-only owner for the existing RDP market and user inputs."""

from __future__ import annotations

import asyncio
import copy
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from polymaker.config import Config
from polymaker.logging import get_logger
from polymaker.rdp_live import RDPBootstrap, RDPBootstrapError, bootstrap_rdp_market
from polymaker.rdp_marketdata import (
    RDPFeedReadiness,
    RDPMarketDataSession,
    _readiness_deadline_s,
)
from polymaker.rdp_userstream import start_rdp_user_channel_session
from polymaker.risk.rdp_capital import RDPInitialBackingResult, evaluate_initial_rdp_backing

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


class RDPInputSession:
    """Retain one public service and one authenticated user/account owner."""

    def __init__(self, bootstrap: RDPBootstrap, *,
                 public_session: Any, user_session: Any,
                 feed: RDPFeedReadiness, backing: RDPInitialBackingResult,
                 now: Callable[[], float], sleep: Callable[[float], Any],
                 poll_interval_s: float, started_at: float) -> None:
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
        self._closed = False

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
        await _close_owned(self.public_session, self.user_session)

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
