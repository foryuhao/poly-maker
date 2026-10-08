"""Bounded, read-only RDP user-channel observation composed with account reads."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from polymaker.config import Config
from polymaker.execution.gateway import ExecutionGateway
from polymaker.rdp_account import (
    RDPAccountError,
    RDPAccountSnapshot,
    _capture_account_with_gateway,
    _prepare_account_config,
    _validate_connected_gateway,
)
from polymaker.rdp_live import RDPBootstrap, RDPBootstrapError
from polymaker.state.store import StateStore
from polymaker.state.tracker import UserEventProcessor
from polymaker.userstream.client import UserStream, UserStreamError

_USER_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/user"
_INITIAL_PONG_TIMEOUT_S = 15.0


class RDPUserChannelError(RDPBootstrapError):
    """A sanitized, categorized failure of the bounded user-channel check."""

    def __init__(self, category: str, message: str) -> None:
        super().__init__(category, message)
        self.subscription_frame_sent = False
        self.connection_generation: int | None = None
        self.application_pong_received = False
        self.heartbeat_receipt_ts: float | None = None


def _attach_stream_evidence(error: RDPUserChannelError, stream: UserStream | None) -> None:
    if stream is None:
        return
    error.subscription_frame_sent = stream.subscription_sent
    error.connection_generation = stream.connection_generation
    error.application_pong_received = stream.heartbeat_generation == stream.connection_generation
    error.heartbeat_receipt_ts = stream.heartbeat_receipt_ts


@dataclass(frozen=True, slots=True)
class RDPUserChannelEvidence:
    condition_id: str
    subscription_frame_sent: bool
    connection_generation: int
    application_pong_received: bool
    heartbeat_receipt_ts: float
    capture_ts: float
    transport_evidence: str = "CURRENT_GENERATION_APPLICATION_PONG_AND_ACCOUNT_READS"
    explicit_server_authentication_acknowledgment: bool = False


@dataclass(frozen=True, slots=True)
class RDPAuthenticatedObservation:
    account: RDPAccountSnapshot
    user_channel: RDPUserChannelEvidence


class RDPUserChannelSession:
    """Own one authenticated read-only stream, processor, store, and gateway."""

    def __init__(self, gateway, store, processor, stream, stream_task,
                 observation: RDPAuthenticatedObservation) -> None:
        self.gateway = gateway
        self.store = store
        self.processor = processor
        self.stream = stream
        self.stream_task = stream_task
        self.observation = observation
        self._closed = False

    def failure_category(self, expected_generation: int) -> str | None:
        if self.stream.failure_category is not None:
            return self.stream.failure_category
        if self.stream_task.done():
            return "USER_STREAM_TERMINATED"
        if not self.stream.connected:
            return "USER_STREAM_DISCONNECTED"
        if self.stream.connection_generation != expected_generation:
            return "USER_STREAM_GENERATION_CHANGED"
        if not self.stream.heartbeat_current:
            return "APPLICATION_HEARTBEAT_STALE"
        return None

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        interrupted = await _drain_tasks((self.stream_task,))
        try:
            self.store.close()
        finally:
            close = getattr(self.gateway, "aclose", None)
            if callable(close):
                interrupted = await _finish_cleanup(close()) or interrupted
            else:
                self.gateway.close()
        if interrupted:
            raise asyncio.CancelledError


async def _drain_tasks(tasks: tuple[asyncio.Task[Any] | None, ...]) -> bool:
    pending = [task for task in tasks if task is not None]
    for task in pending:
        if not task.done():
            task.cancel()
    if not pending:
        return False
    drain = asyncio.gather(*pending, return_exceptions=True)
    cleanup = asyncio.ensure_future(drain)
    interrupted = False
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            current = asyncio.current_task()
            interrupted = interrupted or bool(current and current.cancelling())
            continue
    return interrupted


async def _finish_cleanup(awaitable: Any) -> bool:
    task = asyncio.ensure_future(awaitable)
    interrupted = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            current = asyncio.current_task()
            interrupted = interrupted or bool(current and current.cancelling())
            continue
    task.result()
    return interrupted


async def start_rdp_user_channel_session(
    cfg: Config,
    bootstrap: RDPBootstrap,
    *,
    now: Callable[[], float] = time.time,
    monotonic: Callable[[], float] = time.monotonic,
    gateway_factory: Callable[..., ExecutionGateway] | None = None,
    user_stream_factory: Callable[..., UserStream] | None = None,
    store_factory: Callable[..., StateStore] | None = None,
) -> RDPUserChannelSession:
    """Open retained read-only user/account ownership and capture startup state."""
    gateway_factory = gateway_factory or ExecutionGateway
    user_stream_factory = user_stream_factory or UserStream
    store_factory = store_factory or StateStore
    try:
        config, signer, funder = _prepare_account_config(cfg, bootstrap, now)
    except RDPAccountError as exc:
        raise RDPUserChannelError(exc.category, str(exc)) from None
    condition_id = bootstrap.market.condition_id
    token_ids = tuple(token.token_id for token in bootstrap.market.tokens)
    gateway = gateway_factory(config, paper=False)
    store: StateStore | None = None
    stream: UserStream | None = None
    stream_task: asyncio.Task[None] | None = None
    pong_task: asyncio.Task[None] | None = None
    account_task: asyncio.Task[RDPAccountSnapshot] | None = None
    retained = False
    try:
        await gateway.connect(derive_only=True)
        _validate_connected_gateway(gateway, signer, funder)
        store = store_factory(":memory:")
        processor = UserEventProcessor(store, retain_confirmed_receipts=True)
        token_pair = {token_ids[0]: token_ids[1], token_ids[1]: token_ids[0]}
        token_conditions = {token_id: condition_id for token_id in token_ids}
        stream = user_stream_factory(
            gateway.creds,
            gateway.address,
            processor,
            other_token=lambda token: token_pair.get(token),
            condition_of_token=lambda token: token_conditions.get(token),
            url=_USER_WS,
            application_heartbeat=True,
            heartbeat_interval_s=10.0,
            heartbeat_timeout_s=15.0,
            sanitize_errors=True,
            capture_receipts=True,
        )
        stream.set_markets([condition_id])
        market_deadline = monotonic() + max(0.0, bootstrap.end_epoch - now())
        pong_deadline = min(market_deadline, monotonic() + _INITIAL_PONG_TIMEOUT_S)
        if pong_deadline <= monotonic():
            raise RDPUserChannelError("MARKET_INTERVAL_ELAPSED", "market interval ended before user-channel observation")
        stream_task = asyncio.create_task(stream.run_once(), name="rdp-user-channel-stream")
        pong_task = asyncio.create_task(
            stream.wait_for_application_pong(max(0.0, pong_deadline - monotonic())),
            name="rdp-user-channel-pong-wait",
        )
        done, _ = await asyncio.wait(
            {stream_task, pong_task}, timeout=max(0.0, pong_deadline - monotonic()),
            return_when=asyncio.FIRST_COMPLETED,
        )
        if stream_task in done:
            if pong_task in done:
                await pong_task
            raise RDPUserChannelError(
                stream.failure_category or "USER_STREAM_ENDED_BEFORE_CAPTURE",
                "user stream ended before current-generation heartbeat readiness",
            )
        if pong_task not in done:
            raise RDPUserChannelError("APPLICATION_PONG_TIMEOUT", "current-generation application PONG was not observed")
        try:
            await pong_task
        except UserStreamError as exc:
            raise RDPUserChannelError(exc.category, "user-channel heartbeat readiness failed") from None
        pong_task = None
        if now() >= bootstrap.end_epoch or monotonic() >= market_deadline:
            raise RDPUserChannelError("MARKET_INTERVAL_ELAPSED", "market interval ended before account capture")

        generation = stream.connection_generation
        if stream.heartbeat_receipt_ts is None or not stream.subscription_sent or not stream.heartbeat_current:
            raise RDPUserChannelError("CURRENT_GENERATION_PONG_UNAVAILABLE", "user-channel health is not current")
        account_task = asyncio.create_task(
            _capture_account_with_gateway(bootstrap, gateway, signer, funder, now=now),
            name="rdp-user-channel-account-reads",
        )
        done, _ = await asyncio.wait(
            {stream_task, account_task},
            timeout=max(0.0, min(market_deadline - monotonic(), bootstrap.end_epoch - now())),
            return_when=asyncio.FIRST_COMPLETED,
        )
        if stream_task in done:
            if account_task in done:
                # Surface a categorized stream failure regardless of whether
                # the final HTTP read happened to complete in the same turn.
                account_task.result()
            raise RDPUserChannelError(
                stream.failure_category or "USER_STREAM_DISCONNECTED",
                "user stream ended during account capture",
            )
        if account_task not in done:
            raise RDPUserChannelError("ACCOUNT_CAPTURE_TIMEOUT", "account capture exceeded the bounded observation")
        account = account_task.result()
        account_task = None
        receipt_ts = stream.heartbeat_receipt_ts
        if (now() >= bootstrap.end_epoch or monotonic() >= market_deadline
                or stream_task.done() or not stream.connected
                or stream.connection_generation != generation
                or not stream.heartbeat_current or receipt_ts is None):
            raise RDPUserChannelError("USER_CHANNEL_NOT_CURRENT_AT_CAPTURE", "user-channel evidence was not current at capture")
        capture_ts = now()
        if capture_ts >= bootstrap.end_epoch:
            raise RDPUserChannelError("MARKET_INTERVAL_ELAPSED", "market interval ended at capture")
        evidence = RDPUserChannelEvidence(
            condition_id=condition_id,
            subscription_frame_sent=stream.subscription_sent,
            connection_generation=generation,
            application_pong_received=True,
            heartbeat_receipt_ts=receipt_ts,
            capture_ts=capture_ts,
        )
        observation = RDPAuthenticatedObservation(account=account, user_channel=evidence)
        assert store is not None and stream is not None
        session = RDPUserChannelSession(gateway, store, processor, stream, stream_task, observation)
        retained = True
        return session
    except RDPAccountError as exc:
        error = RDPUserChannelError(exc.category, str(exc))
        _attach_stream_evidence(error, stream)
        raise error from None
    except RDPUserChannelError as exc:
        _attach_stream_evidence(exc, stream)
        raise
    except RDPBootstrapError:
        raise
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - never expose SDK/network details
        category = exc.category if isinstance(exc, UserStreamError) else "AUTHENTICATED_OBSERVATION_FAILED"
        error = RDPUserChannelError(category, f"read-only observation failed ({type(exc).__name__})")
        _attach_stream_evidence(error, stream)
        raise error from None
    finally:
        if not retained:
            cleanup_cancelled = await _drain_tasks((pong_task, account_task, stream_task))
            try:
                if store is not None:
                    store.close()
            finally:
                close = getattr(gateway, "aclose", None)
                if callable(close):
                    cleanup_cancelled = await _finish_cleanup(close()) or cleanup_cancelled
                else:
                    gateway.close()
            if cleanup_cancelled:
                raise asyncio.CancelledError


async def observe_rdp_account_and_user_channel(
    cfg: Config,
    bootstrap: RDPBootstrap,
    *,
    now: Callable[[], float] = time.time,
    monotonic: Callable[[], float] = time.monotonic,
    gateway_factory: Callable[..., ExecutionGateway] | None = None,
    user_stream_factory: Callable[..., UserStream] | None = None,
    store_factory: Callable[..., StateStore] | None = None,
) -> RDPAuthenticatedObservation:
    """Preserve the bounded account/user check API and its complete cleanup."""
    session = await start_rdp_user_channel_session(
        cfg, bootstrap, now=now, monotonic=monotonic,
        gateway_factory=gateway_factory, user_stream_factory=user_stream_factory,
        store_factory=store_factory,
    )
    try:
        return session.observation
    finally:
        await session.aclose()
