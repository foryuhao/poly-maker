from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from polymaker.config import Config
from polymaker.domain import MarketMeta, TokenMeta
from polymaker.rdp_live import RDPBootstrap, TokenBookMetadata
from polymaker.rdp_marketdata import RDPFeedReadiness, TokenBookEvidence
from polymaker.rdp_runtime import RDPInputSession, RDPObservationError, observe_rdp_market
from polymaker.rdp_userstream import RDPAuthenticatedObservation, RDPUserChannelEvidence
from polymaker.risk.rdp_capital import RDPInitialBackingResult


def _bootstrap(end_epoch: int) -> RDPBootstrap:
    market = MarketMeta(
        condition_id="runtime-condition", question="BTC Up or Down",
        slug="btc-updown-5m-2000000100",
        tokens=(TokenMeta("runtime-up", "Up"), TokenMeta("runtime-down", "Down")),
        tick_size=.01, neg_risk=False, min_order_size=5, rewards_min_size=0,
        rewards_max_spread=0, rewards_daily_rate=0, maker_fee_bps=0,
        taker_fee_bps=0, fees_enabled=False,
        end_date_iso=datetime.fromtimestamp(end_epoch, UTC).isoformat(), event_id=None,
    )
    metadata = (TokenBookMetadata("Up", "runtime-up", .01, 5, False),
                TokenBookMetadata("Down", "runtime-down", .005, 5, False))
    return RDPBootstrap(market, end_epoch - 300, end_epoch, metadata, 1.0)


class FakePublicSession:
    def __init__(self, bootstrap, *, feed=None, single_attempt=False):
        self.bootstrap = bootstrap
        self.single_attempt = single_attempt
        self.active = False
        self.closed = False
        self.generation = 0
        self.failure = None
        self.terminated = False
        self.feed = feed or RDPFeedReadiness(
            bootstrap.market.condition_id, 1,
            (TokenBookEvidence("Up", "runtime-up", SimpleNamespace(), .01, "REST_BOOTSTRAP"),
             TokenBookEvidence("Down", "runtime-down", SimpleNamespace(), .005, "REST_BOOTSTRAP")),
            2.0,
        )

    async def start(self):
        self.active = True
        self.generation = 1

    async def wait_ready(self, **_kwargs):
        return self.feed

    def failure_category(self, expected_generation=None):
        if self.failure is not None:
            return self.failure
        if self.terminated:
            return "FEED_SERVICE_STOPPED"
        if not self.active:
            return "FEED_DISCONNECTED"
        if self.generation != expected_generation:
            return "FEED_GENERATION_CHANGED"
        return None

    async def aclose(self):
        self.active = False
        self.closed = True


class FakeUserSession:
    def __init__(self, bootstrap):
        channel = RDPUserChannelEvidence(
            bootstrap.market.condition_id, True, 1, True, 2.0, 2.1,
        )
        account = SimpleNamespace(orders_receipt_ts=2.0, positions_receipt_ts=2.0,
                                  collateral_receipt_ts=2.0)
        self.observation = RDPAuthenticatedObservation(account, channel)
        self.processor = object()
        self.active = True
        self.closed = False
        self.generation = 1
        self.failure = None
        self.terminated = False
        self.public_was_active = False

    def failure_category(self, expected_generation):
        if self.failure is not None:
            return self.failure
        if self.terminated:
            return "USER_STREAM_TERMINATED"
        if not self.active:
            return "USER_STREAM_DISCONNECTED"
        if self.generation != expected_generation:
            return "USER_STREAM_GENERATION_CHANGED"
        return None

    async def aclose(self):
        self.active = False
        self.closed = True


def _dependencies(clock, *, backing_eligible=True, public_holder=None, mutate_generation=False):
    bootstrap = _bootstrap(int(clock[0] + 10))
    public_sessions = []
    user_sessions = []

    async def bootstrap_factory(_cfg, slug, *, now):
        assert slug == bootstrap.market.slug
        assert now() < bootstrap.end_epoch
        return bootstrap

    def public_factory(value, **kwargs):
        session = FakePublicSession(value, **kwargs)
        public_sessions.append(session)
        if public_holder is not None:
            public_holder.append(session)
        return session

    async def user_factory(_cfg, value, *, now):
        assert public_sessions[0].active
        assert now() < value.end_epoch
        user = FakeUserSession(value)
        user.public_was_active = public_sessions[0].active
        if mutate_generation:
            public_sessions[0].generation += 1
        user_sessions.append(user)
        return user

    def backing(_bootstrap, _account, *, now, max_age_s):
        assert max_age_s == Config().risk.user_ws_blind_halt_s
        return RDPInitialBackingResult(backing_eligible,
                                       None if backing_eligible else "SELECTED_STATE_NOT_EMPTY",
                                       30, 0, 30, 30, 0, backing_eligible)

    return bootstrap, bootstrap_factory, public_factory, user_factory, backing, public_sessions, user_sessions


@pytest.mark.asyncio
async def test_observation_retains_one_scoped_pair_and_exact_user_processor():
    clock = [100.0]
    (bootstrap, boot, public_factory, user_factory, backing, publics,
     users) = _dependencies(clock)
    session = await RDPInputSession.open(
        Config(), bootstrap.market.slug, now=lambda: clock[0],
        bootstrap_factory=boot, public_session_factory=public_factory,
        user_session_factory=user_factory, backing_evaluator=backing,
    )
    try:
        assert len(publics) == len(users) == 1
        assert publics[0].active and users[0].active
        assert users[0].public_was_active
        assert session.processor is users[0].processor
        assert session.feed.condition_id == bootstrap.market.condition_id
        assert session.feed.connection_generation == session.public_generation == 1
        assert session.user_generation == 1
        assert session.backing.eligible
    finally:
        await session.aclose()
    assert publics[0].closed and users[0].closed


@pytest.mark.asyncio
async def test_quiet_healthy_observation_runs_to_market_end_and_cleans_up():
    clock = [100.0]
    (bootstrap, boot, public_factory, user_factory, backing, publics,
     users) = _dependencies(clock)

    async def advance(seconds):
        clock[0] += seconds
        await asyncio.sleep(0)

    session = await RDPInputSession.open(
        Config(), bootstrap.market.slug, now=lambda: clock[0], sleep=advance,
        poll_interval_s=1, bootstrap_factory=boot,
        public_session_factory=public_factory, user_session_factory=user_factory,
        backing_evaluator=backing,
    )
    result = await session.wait_until_end()
    assert result.category == "MARKET_END"
    assert result.mode == "READ_ONLY_OBSERVATION"
    assert result.real_money_trading_performed is False
    assert clock[0] == bootstrap.end_epoch
    await session.aclose()
    assert publics[0].closed and users[0].closed


@pytest.mark.asyncio
async def test_startup_backing_failure_preserves_reason_and_closes_both_inputs():
    clock = [100.0]
    (bootstrap, boot, public_factory, user_factory, backing, publics,
     users) = _dependencies(clock, backing_eligible=False)
    with pytest.raises(RDPObservationError) as caught:
        await RDPInputSession.open(
            Config(), bootstrap.market.slug, now=lambda clock=clock: clock[0],
            bootstrap_factory=boot, public_session_factory=public_factory,
            user_session_factory=user_factory, backing_evaluator=backing,
        )
    assert caught.value.category == "STARTUP_BACKING_INELIGIBLE"
    assert str(caught.value) == "SELECTED_STATE_NOT_EMPTY"
    assert publics[0].closed and users[0].closed


@pytest.mark.asyncio
async def test_generation_change_during_startup_capture_fails_and_cleans_up():
    clock = [100.0]
    (bootstrap, boot, public_factory, user_factory, backing, publics,
     users) = _dependencies(clock, mutate_generation=True)
    with pytest.raises(RDPObservationError) as caught:
        await RDPInputSession.open(
            Config(), bootstrap.market.slug, now=lambda: clock[0],
            bootstrap_factory=boot, public_session_factory=public_factory,
            user_session_factory=user_factory, backing_evaluator=backing,
        )
    assert caught.value.category == "FEED_GENERATION_CHANGED"
    assert publics[0].closed and users[0].closed


@pytest.mark.asyncio
async def test_public_disconnect_or_user_generation_change_ends_attempt():
    for change in ("public", "user"):
        clock = [100.0]
        (bootstrap, boot, public_factory, user_factory, backing, publics,
         users) = _dependencies(clock)
        session = await RDPInputSession.open(
            Config(), bootstrap.market.slug, now=lambda clock=clock: clock[0],
            bootstrap_factory=boot, public_session_factory=public_factory,
            user_session_factory=user_factory, backing_evaluator=backing,
        )
        if change == "public":
            publics[0].active = False
        else:
            users[0].generation = 2
        with pytest.raises(RDPObservationError) as caught:
            await session.wait_until_end()
        assert caught.value.category in {"FEED_DISCONNECTED", "USER_STREAM_GENERATION_CHANGED"}
        await session.aclose()
        assert publics[0].closed and users[0].closed


@pytest.mark.asyncio
@pytest.mark.parametrize(("owner", "category"), [
    ("public_fatal", "MALFORMED_SELECTED_BOOK"),
    ("public_task", "FEED_SERVICE_STOPPED"),
    ("user_heartbeat", "APPLICATION_PONG_TIMEOUT"),
    ("user_task", "USER_STREAM_TERMINATED"),
])
async def test_fatal_input_and_unexpected_task_termination_end_observation(owner, category):
    clock = [100.0]
    (bootstrap, boot, public_factory, user_factory, backing, publics,
     users) = _dependencies(clock)
    session = await RDPInputSession.open(
        Config(), bootstrap.market.slug, now=lambda: clock[0],
        bootstrap_factory=boot, public_session_factory=public_factory,
        user_session_factory=user_factory, backing_evaluator=backing,
    )
    if owner == "public_fatal":
        publics[0].failure = category
    elif owner == "public_task":
        publics[0].terminated = True
    elif owner == "user_heartbeat":
        users[0].failure = category
    else:
        users[0].terminated = True
    with pytest.raises(RDPObservationError) as caught:
        await session.wait_until_end()
    assert caught.value.category == category
    await session.aclose()


@pytest.mark.asyncio
async def test_expired_startup_rejects_before_public_service_creation():
    clock = [100.0]
    bootstrap = _bootstrap(100)
    created = []

    def forbidden_public(_bootstrap):
        created.append(True)
        raise AssertionError("expired market opened a public socket owner")

    async def bootstrap_factory(*_args, **_kwargs):
        return bootstrap

    with pytest.raises(Exception) as caught:
        await RDPInputSession.open(
            Config(), bootstrap.market.slug, now=lambda: clock[0],
            bootstrap_factory=bootstrap_factory,
            public_session_factory=forbidden_public,
        )
    assert getattr(caught.value, "category", None) == "MARKET_INTERVAL_ELAPSED"
    assert created == []


@pytest.mark.asyncio
async def test_partial_startup_failure_closes_already_started_public_service():
    clock = [100.0]
    bootstrap = _bootstrap(110)
    publics = []

    async def user_failure(*_args, **_kwargs):
        raise RDPObservationError("USER_START_FAILED", "controlled startup failure")

    def public_factory(value, **kwargs):
        service = FakePublicSession(value, **kwargs)
        publics.append(service)
        return service

    with pytest.raises(RDPObservationError, match="controlled startup failure"):
        await RDPInputSession.open(
            Config(), bootstrap.market.slug, now=lambda: clock[0],
            bootstrap_factory=lambda *_args, **_kwargs: _return(bootstrap),
            public_session_factory=public_factory,
            user_session_factory=user_failure,
            backing_evaluator=lambda *_args, **_kwargs: None,
        )
    assert publics[0].closed


@pytest.mark.asyncio
async def test_repeated_cancellation_drains_both_owned_inputs():
    clock = [100.0]
    (bootstrap, boot, public_factory, user_factory, backing, publics,
     users) = _dependencies(clock)
    poll_started = asyncio.Event()
    cleanup_started = asyncio.Event()
    cleanup_release = asyncio.Event()

    async def blocked_sleep(_seconds):
        poll_started.set()
        await asyncio.Event().wait()

    original_user_factory = user_factory

    async def delayed_close_user(*args, **kwargs):
        user = await original_user_factory(*args, **kwargs)
        original_close = user.aclose

        async def close():
            cleanup_started.set()
            await cleanup_release.wait()
            await original_close()

        user.aclose = close
        return user

    task = asyncio.create_task(
        observe_rdp_market(
            Config(), bootstrap.market.slug, now=lambda: clock[0], sleep=blocked_sleep,
            bootstrap_factory=boot, public_session_factory=public_factory,
            user_session_factory=delayed_close_user, backing_evaluator=backing,
        )
    )
    await poll_started.wait()
    task.cancel()
    await cleanup_started.wait()
    task.cancel()
    cleanup_release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert publics[0].closed and users[0].closed


def test_retained_processor_counts_only_synthetic_confirmed_owned_fill(tmp_path):
    from polymaker.domain import Side, TradeState
    from polymaker.state.store import StateStore
    from polymaker.state.tracker import TradeEvent, UserEventProcessor
    from polymaker.strategy.rdp_accounting import snapshot_inventory

    bootstrap = _bootstrap(2_000_000_400)
    store = StateStore(str(tmp_path / "observer-test.db"))
    processor = UserEventProcessor(store)
    try:
        processor.register_order("synthetic-order", bootstrap.market.condition_id,
                                 "runtime-up", Side.BUY, 5, .4)
        processor.on_trade(TradeEvent("runtime-up", Side.BUY, .4, 2, "synthetic-trade",
                                      TradeState.MATCHED, 1, "synthetic-order"),
                           bootstrap.market.condition_id)
        matched = snapshot_inventory(bootstrap.market, processor, {"synthetic-order"})
        assert (matched.qy, matched.cy) == (0, 0)
        processor.on_trade(TradeEvent("runtime-up", Side.BUY, .4, 2, "synthetic-trade",
                                      TradeState.CONFIRMED, 2, "synthetic-order"),
                           bootstrap.market.condition_id)
        confirmed = snapshot_inventory(bootstrap.market, processor, {"synthetic-order"})
        assert (confirmed.qy, confirmed.cy) == (2, .8)
    finally:
        store.close()


async def _return(value):
    return value


@pytest.mark.asyncio
async def test_public_failure_during_user_startup_cancels_and_drains_starter():
    clock = [100.0]
    bootstrap = _bootstrap(110)
    publics = []
    entered = asyncio.Event()
    closed = asyncio.Event()

    async def user_factory(*_args, **_kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()

    def public_factory(value, **kwargs):
        owner = FakePublicSession(value, **kwargs)
        publics.append(owner)
        return owner

    task = asyncio.create_task(RDPInputSession.open(
        Config(), bootstrap.market.slug, now=lambda: clock[0],
        bootstrap_factory=lambda *_args, **_kwargs: _return(bootstrap),
        public_session_factory=public_factory, user_session_factory=user_factory,
        backing_evaluator=lambda *_args, **_kwargs: None,
    ))
    await entered.wait()
    publics[0].failure = "MALFORMED_SELECTED_BOOK"
    with pytest.raises(RDPObservationError) as caught:
        await task
    assert caught.value.category == "MALFORMED_SELECTED_BOOK"
    assert closed.is_set() and publics[0].closed


@pytest.mark.asyncio
async def test_public_failure_wins_if_user_start_returns_simultaneously_and_owner_closes():
    clock = [100.0]
    bootstrap = _bootstrap(110)
    public = FakePublicSession(bootstrap)
    user = FakeUserSession(bootstrap)
    task_started = asyncio.Event()

    async def user_factory(*_args, **_kwargs):
        task_started.set()
        await asyncio.sleep(0)
        public.failure = "FEED_GENERATION_CHANGED"
        return user

    with pytest.raises(RDPObservationError) as caught:
        await RDPInputSession.open(
            Config(), bootstrap.market.slug, now=lambda: clock[0],
            bootstrap_factory=lambda *_args, **_kwargs: _return(bootstrap),
            public_session_factory=lambda *_args, **_kwargs: public,
            user_session_factory=user_factory,
            backing_evaluator=lambda *_args, **_kwargs: None,
        )
    assert task_started.is_set()
    assert caught.value.category == "FEED_GENERATION_CHANGED"
    assert public.closed and user.closed


@pytest.mark.asyncio
async def test_market_expiry_at_final_startup_certification_rejects_and_closes():
    clock = [100.0]
    bootstrap = _bootstrap(110)
    publics = []
    users = []
    user = FakeUserSession(bootstrap)
    public = FakePublicSession(bootstrap)
    publics.append(public)
    users.append(user)

    def backing(*_args, **_kwargs):
        clock[0] = bootstrap.end_epoch
        return RDPInitialBackingResult(True, None, 30, 0, 30, 30, 0, True)

    with pytest.raises(RDPObservationError) as caught:
        await RDPInputSession.open(
            Config(), bootstrap.market.slug, now=lambda: clock[0],
            bootstrap_factory=lambda *_args, **_kwargs: _return(bootstrap),
            public_session_factory=lambda *_args, **_kwargs: public,
            user_session_factory=lambda *_args, **_kwargs: _return(user),
            backing_evaluator=backing,
        )
    assert caught.value.category == "MARKET_INTERVAL_ELAPSED"
    assert publics[0].closed and users[0].closed


class _FakeRetainedSocket:
    def __init__(self, frames=()):
        self.frames = list(frames)
        self.queue = asyncio.Queue()
        self.active = False
        self.closed = False
        self.sent = []

    async def __aenter__(self):
        self.active = True
        return self

    async def __aexit__(self, *_args):
        await self.close()

    async def send(self, data):
        self.sent.append(data)
        if data == "PING":
            self.queue.put_nowait("PONG")

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.frames:
            return self.frames.pop(0)
        value = await self.queue.get()
        if value is None:
            raise StopAsyncIteration
        return value

    async def close(self):
        if not self.closed:
            self.closed = True
            self.active = False
            self.queue.put_nowait(None)


class _ReadOnlyFakeGateway:
    def __init__(self, config, *, paper):
        assert paper is False
        from eth_account import Account

        self.address = Account.from_key(config.secrets.pk).address
        self.funder = config.secrets.browser_address
        self.creds = SimpleNamespace(api_key="api-sentinel", api_secret="secret-sentinel",
                                     api_passphrase="pass-sentinel")
        self.sockets = None
        self.closed = False
        self.connect_modes = []
        self.selected_state = False
        self.block_account_read = False
        self.block_gateway_close = False
        self.read_entered = asyncio.Event()
        self.read_drained = asyncio.Event()
        self.close_entered = asyncio.Event()
        self.close_release = asyncio.Event()

    async def connect(self, *, derive_only):
        self.connect_modes.append(derive_only)

    async def open_orders(self, *, strict_account, exact_decimal):
        assert strict_account is True and exact_decimal is True
        assert self.sockets is not None and all(socket.active for socket in self.sockets)
        self.read_entered.set()
        if self.block_account_read:
            try:
                await asyncio.Event().wait()
            finally:
                self.read_drained.set()
            return []
        await asyncio.sleep(.02)
        self.read_drained.set()
        return []

    async def positions(self, *, strict_account):
        assert strict_account is True
        assert all(socket.active for socket in self.sockets)
        up_quantity = 1 if self.selected_state else 0
        return {"runtime-up": (up_quantity, 0), "runtime-down": (0, 0)}

    async def required_collateral_spenders(self, _market):
        return ("0x0000000000000000000000000000000000000001",)

    async def balance_allowance(self):
        return {"balance": "30000000",
                "allowances": {"0x0000000000000000000000000000000000000001": "30000000"}}

    async def aclose(self):
        if self.block_gateway_close:
            assert self.read_drained.is_set()
            self.close_entered.set()
            await self.close_release.wait()
        self.closed = True

    def place_order(self, *_args, **_kwargs):
        raise AssertionError("read-only observer attempted order placement")

    def cancel_order(self, *_args, **_kwargs):
        raise AssertionError("read-only observer attempted cancellation")


def _actual_read_only_config(log_dir):
    from eth_account import Account

    from polymaker.config import PathsConfig, Secrets, WalletConfig

    private_key = "0x" + "11" * 32
    address = Account.from_key(private_key).address
    return Config(secrets=Secrets(PK=private_key, BROWSER_ADDRESS=address),
                  wallet=WalletConfig(signature_type=0), paths=PathsConfig(log_dir=str(log_dir)))


def _network_socket_forbidden(monkeypatch):
    import socket

    attempts = []

    def forbidden(*_args, **_kwargs):
        attempts.append("socket_or_dns")
        raise AssertionError("controlled observation attempted real socket or DNS")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    return attempts


@pytest.mark.parametrize("scenario", [
    "market_end", "selected_state", "health_at_end", "cancel_account_drain",
])
def test_cli_observe_only_uses_real_retained_owners_and_ends_market(
    tmp_path, monkeypatch, caplog, scenario,
):
    from typer.testing import CliRunner

    import polymaker.rdp_runtime as rdp_runtime
    import polymaker.rdp_userstream as rdp_userstream
    from polymaker.cli import app
    from polymaker.marketdata import service as market_service
    from polymaker.rdp_live import TokenBookMetadata
    from polymaker.rdp_marketdata import RDPMarketDataSession as RealPublicOwner
    from polymaker.rdp_runtime import RDPInputSession as RealInputSession
    from polymaker.rdp_userstream import UserStream as RealUserStream

    network_attempts = _network_socket_forbidden(monkeypatch)
    end_epoch = int(time.time()) + (30 if scenario == "cancel_account_drain" else 3)
    base = _bootstrap(end_epoch)
    bootstrap = RDPBootstrap(
        base.market, base.epoch, end_epoch,
        (TokenBookMetadata("Up", "runtime-up", .01, 5, False),
         TokenBookMetadata("Down", "runtime-down", .005, 5, False)),
        time.time(),
    )
    cfg = _actual_read_only_config(tmp_path / "logs")
    (tmp_path / "logs").mkdir()
    sockets = []
    gateways = []
    gateway_ready = asyncio.Event()
    processors = []
    owners = []
    user_owners = []
    selected_state = [scenario == "selected_state"]

    def fake_connect(url, **_kwargs):
        if url.endswith("/ws/market"):
            frames = [json.dumps({
                "event_type": "book", "market": bootstrap.market.condition_id,
                "asset_id": token,
                "bids": [{"price": ".4", "size": "3"}],
                "asks": [{"price": ".6", "size": "4"}],
                "timestamp": "2000000101",
            }) for token in ("runtime-up", "runtime-down")]
            frames.append(json.dumps({
                "event_type": "tick_size_change", "asset_id": "runtime-up",
                "market": bootstrap.market.condition_id,
                "new_tick_size": ".002",
            }))
            socket = _FakeRetainedSocket(frames)
        else:
            socket = _FakeRetainedSocket()
        sockets.append(socket)
        return socket

    def gateway_factory(config, *, paper):
        gateway = _ReadOnlyFakeGateway(config, paper=paper)
        gateway.sockets = sockets
        gateway.selected_state = selected_state[0]
        gateway.block_account_read = scenario == "cancel_account_drain"
        gateway.block_gateway_close = scenario == "cancel_account_drain"
        gateways.append(gateway)
        gateway_ready.set()
        return gateway

    def stream_factory(creds, address, processor, **kwargs):
        processors.append(processor)
        return RealUserStream(creds, address, processor, **kwargs)

    def public_factory(value, **kwargs):
        owner = RealPublicOwner(value, **kwargs)
        owners.append(owner)
        return owner

    def input_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        owners.append(self)

    def user_owner_init(self, *args, **kwargs):
        original_user_owner_init(self, *args, **kwargs)
        user_owners.append(self)

    retained_processor_evidence = []

    async def observe_until_end_with_synthetic_fill(self):
        from dataclasses import replace
        from decimal import Decimal

        from polymaker.domain import Side, TradeState
        from polymaker.risk.rdp_capital import evaluate_initial_rdp_backing
        from polymaker.state.tracker import TradeEvent
        from polymaker.strategy.rdp_accounting import snapshot_inventory

        if scenario == "health_at_end":
            self.public_session.service.connection_generation += 1
            self._now = lambda: self.bootstrap.end_epoch
            return await original_wait_until_end(self)

        # Inject both a selected book delta and a new tick after successful
        # startup; they must update the retained service while it is owned.
        public_socket = sockets[0]
        public_socket.queue.put_nowait(json.dumps({
            "event_type": "tick_size_change", "asset_id": "runtime-up",
            "market": bootstrap.market.condition_id, "new_tick_size": ".003",
        }))
        public_socket.queue.put_nowait(json.dumps({
            "event_type": "price_change", "market": bootstrap.market.condition_id,
            "price_changes": [{"asset_id": "runtime-up", "side": "BUY",
                                "price": ".41", "size": "6"}],
        }))
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            book = self.public_session.service.book("runtime-up")
            if (book.tick_size == .003 and book.best_bid() is not None
                    and book.best_bid().price == .41):
                break
            await asyncio.sleep(.001)
        book = self.public_session.service.book("runtime-up")
        assert book.tick_size == .003 and book.best_bid().price == .41

        processor = self.user_session.processor
        synthetic_id = "observer-synthetic-order"
        processor.register_order(synthetic_id, bootstrap.market.condition_id,
                                 "runtime-up", Side.BUY, 5, .4)
        processor.on_trade(TradeEvent(
            "runtime-up", Side.BUY, .4, 2, "observer-synthetic-trade",
            TradeState.MATCHED, 1, synthetic_id,
        ), bootstrap.market.condition_id)
        matched = snapshot_inventory(bootstrap.market, processor, {synthetic_id})
        processor.on_trade(TradeEvent(
            "runtime-up", Side.BUY, .4, 2, "observer-synthetic-trade",
            TradeState.CONFIRMED, 2, synthetic_id,
        ), bootstrap.market.condition_id)
        confirmed = snapshot_inventory(bootstrap.market, processor, {synthetic_id})
        retained_processor_evidence.extend([
            (matched.qy, matched.cy), (confirmed.qy, confirmed.cy),
        ])
        selected = replace(self.account.selected_positions[0], quantity=Decimal("1"))
        selected_account = replace(
            self.account,
            selected_positions=(selected, self.account.selected_positions[1]),
        )
        rejected = evaluate_initial_rdp_backing(
            bootstrap, selected_account, now=time.time(),
            max_age_s=Config().risk.user_ws_blind_halt_s,
        )
        retained_processor_evidence.append((rejected.eligible, rejected.reason))
        return await original_wait_until_end(self)

    original_init = RealInputSession.__init__
    original_wait_until_end = RealInputSession.wait_until_end
    from polymaker.rdp_userstream import RDPUserChannelSession as RealUserOwner

    original_user_owner_init = RealUserOwner.__init__
    monkeypatch.setattr(rdp_runtime.RDPInputSession, "__init__", input_init)
    monkeypatch.setattr(RealUserOwner, "__init__", user_owner_init)
    monkeypatch.setattr(rdp_runtime.RDPInputSession, "wait_until_end",
                        observe_until_end_with_synthetic_fill)
    monkeypatch.setattr(rdp_runtime, "RDPMarketDataSession", public_factory)
    monkeypatch.setattr(rdp_runtime, "bootstrap_rdp_market",
                        lambda *_args, **_kwargs: _return(bootstrap))
    monkeypatch.setattr(rdp_userstream, "ExecutionGateway", gateway_factory)
    monkeypatch.setattr(rdp_userstream, "UserStream", stream_factory)
    monkeypatch.setattr(market_service.websockets, "connect", fake_connect)
    if scenario == "cancel_account_drain":
        from polymaker.state.store import StateStore as RealStateStore

        stores = []

        class TrackedStore(RealStateStore):
            def __init__(self, path):
                super().__init__(path)
                self.was_closed = False
                stores.append(self)

            def close(self):
                self.was_closed = True
                super().close()

        monkeypatch.setattr(rdp_userstream, "StateStore", TrackedStore)

        async def cancel_while_reads_drain():
            observer = asyncio.create_task(rdp_runtime.observe_rdp_market(
                cfg, bootstrap.market.slug,
            ))
            await gateway_ready.wait()
            gateway = gateways[0]
            await gateway.read_entered.wait()
            observer.cancel()
            await gateway.close_entered.wait()
            observer.cancel()
            await asyncio.sleep(.005)
            gateway.close_release.set()
            with pytest.raises(asyncio.CancelledError):
                await observer
            return gateway

        gateway = asyncio.run(cancel_while_reads_drain())
        assert gateway.closed and gateway.read_drained.is_set()
        assert stores and stores[0].was_closed
        assert len(sockets) == 2 and all(socket.closed for socket in sockets)
        assert "'category': 'CANCELLED'" in caplog.text
        assert network_attempts == []
        return

    import polymaker.cli as cli_module
    monkeypatch.setattr(cli_module.Config, "load", lambda *_args, **_kwargs: cfg)

    result = CliRunner().invoke(app, [
        "rdp-live", "--market", bootstrap.market.slug, "--observe-only",
        "--config-dir", str(tmp_path),
    ])
    expected_exit = 0 if scenario == "market_end" else 1
    assert result.exit_code == expected_exit, result.output
    if scenario == "market_end":
        assert "MARKET_END" in result.output
    elif scenario == "selected_state":
        assert "STARTUP_BACKING_INELIGIBLE" in result.output
    else:
        assert "FEED_GENERATION_CHANGED" in result.output
    assert len(gateways) == len(processors) == 1
    assert gateways[0].connect_modes == [True] and gateways[0].closed
    assert len(sockets) == 2 and all(socket.closed for socket in sockets)
    user_owner = user_owners[0]
    assert user_owner._closed and user_owner.stream_task.done()
    if scenario == "selected_state":
        assert user_owner.observation.account.selected_positions[0].quantity == 1
        assert not owners or not any(isinstance(owner, RealInputSession) for owner in owners)
    else:
        input_session = next(owner for owner in owners if isinstance(owner, RealInputSession))
        assert input_session.processor is processors[0]
        assert input_session.user_session.processor is processors[0]
        assert input_session.feed.connection_generation == 1
        assert input_session.backing.eligible is True
        if scenario == "market_end":
            assert input_session.public_session.service.book("runtime-up").tick_size == .003
            assert retained_processor_evidence == [(0, 0), (2, .8),
                                                   (False, "SELECTED_STATE_NOT_EMPTY")]
        else:
            assert input_session.public_generation == 1

    public_sub = json.loads(sockets[0].sent[0])
    user_sub = json.loads(sockets[1].sent[0])
    assert public_sub["assets_ids"] == ["runtime-up", "runtime-down"]
    assert public_sub["type"] == "market"
    assert user_sub["markets"] == [bootstrap.market.condition_id]
    assert "api-sentinel" not in repr(user_owner.observation)
    assert "secret-sentinel" not in repr(user_owner.observation)
    assert "api-sentinel" not in result.output
    assert "secret-sentinel" not in result.output
    assert network_attempts == []
    durable_log = (tmp_path / "logs" / "rdp-live-preflight.jsonl").read_text()
    assert "api-sentinel" not in durable_log and "secret-sentinel" not in durable_log
    assert bootstrap.market.condition_id in durable_log
    assert "public_generation" in durable_log and "user_generation" in durable_log


@pytest.mark.asyncio
async def test_observer_logs_categorized_startup_failure_without_untrusted_details(caplog):
    from polymaker.rdp_live import RDPBootstrapError

    bootstrap = _bootstrap(110)
    public = FakePublicSession(bootstrap)

    async def user_failure(*_args, **_kwargs):
        error = RDPBootstrapError("APPLICATION_PONG_TIMEOUT", "secret-sentinel account details")
        error.connection_generation = 7
        raise error

    with pytest.raises(RDPBootstrapError):
        await observe_rdp_market(
            Config(), bootstrap.market.slug, now=lambda: 100.0,
            bootstrap_factory=lambda *_args, **_kwargs: _return(bootstrap),
            public_session_factory=lambda *_args, **_kwargs: public,
            user_session_factory=user_failure,
            backing_evaluator=lambda *_args, **_kwargs: None,
        )
    assert public.closed
    assert "rdp_observation_terminated" in caplog.text
    assert "APPLICATION_PONG_TIMEOUT" in caplog.text
    assert "READ_ONLY_OBSERVATION" in caplog.text
    assert "'condition_id': 'runtime-condition'" in caplog.text
    assert "'public_generation': 1" in caplog.text
    assert "'user_generation': 7" in caplog.text
    assert "'real_money_trading_performed': False" in caplog.text
    assert "secret-sentinel" not in caplog.text


@pytest.mark.asyncio
async def test_observer_logs_and_propagates_cancellation_during_startup(caplog):
    bootstrap = _bootstrap(110)
    public = FakePublicSession(bootstrap)
    starter_entered = asyncio.Event()
    starter_drained = asyncio.Event()

    async def blocked_user_start(*_args, **_kwargs):
        starter_entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            starter_drained.set()

    task = asyncio.create_task(observe_rdp_market(
        Config(), bootstrap.market.slug, now=lambda: 100.0,
        bootstrap_factory=lambda *_args, **_kwargs: _return(bootstrap),
        public_session_factory=lambda *_args, **_kwargs: public,
        user_session_factory=blocked_user_start,
        backing_evaluator=lambda *_args, **_kwargs: None,
    ))
    await starter_entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert starter_drained.is_set() and public.closed
    assert "'category': 'CANCELLED'" in caplog.text
    assert "'real_money_trading_performed': False" in caplog.text


@pytest.mark.asyncio
async def test_owner_cancellation_during_failed_startup_drain_propagates_after_repeated_cancel(caplog):
    bootstrap = _bootstrap(110)
    public = FakePublicSession(bootstrap)
    starter_entered = asyncio.Event()
    drain_entered = asyncio.Event()
    drain_release = asyncio.Event()

    async def blocked_user_start(*_args, **_kwargs):
        starter_entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            drain_entered.set()
            await drain_release.wait()

    owner = asyncio.create_task(observe_rdp_market(
        Config(), bootstrap.market.slug, now=lambda: 100.0,
        bootstrap_factory=lambda *_args, **_kwargs: _return(bootstrap),
        public_session_factory=lambda *_args, **_kwargs: public,
        user_session_factory=blocked_user_start,
        backing_evaluator=lambda *_args, **_kwargs: None,
    ))
    await starter_entered.wait()
    public.failure = "FEED_DISCONNECTED"
    await drain_entered.wait()
    owner.cancel()
    await asyncio.sleep(.01)
    owner.cancel()
    drain_release.set()
    with pytest.raises(asyncio.CancelledError):
        await owner
    assert public.closed
    assert "'category': 'CANCELLED'" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(("trigger", "category"), [
    ("public_failure", "FEED_DISCONNECTED"),
    ("market_expiry", "MARKET_INTERVAL_ELAPSED"),
])
async def test_child_teardown_error_is_redacted_without_losing_category(monkeypatch, trigger, category):
    import traceback

    import polymaker.rdp_runtime as rdp_runtime

    bootstrap = _bootstrap(110)
    public = FakePublicSession(bootstrap)
    clock = [100.0]
    starter_entered = asyncio.Event()
    log_records = []
    monkeypatch.setattr(
        rdp_runtime.log, "error",
        lambda event, **fields: log_records.append((event, fields)),
    )

    async def failing_teardown(*_args, **_kwargs):
        starter_entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            raise RuntimeError("secret-sentinel teardown")

    task = asyncio.create_task(observe_rdp_market(
        Config(), bootstrap.market.slug, now=lambda: clock[0],
        bootstrap_factory=lambda *_args, **_kwargs: _return(bootstrap),
        public_session_factory=lambda *_args, **_kwargs: public,
        user_session_factory=failing_teardown,
        backing_evaluator=lambda *_args, **_kwargs: None,
    ))
    await starter_entered.wait()
    if trigger == "public_failure":
        public.failure = category
    else:
        clock[0] = bootstrap.end_epoch
    with pytest.raises(RDPObservationError) as caught:
        await task
    formatted = "".join(traceback.format_exception(caught.value))
    assert caught.value.category == category
    assert "secret-sentinel" not in str(caught.value)
    assert "secret-sentinel" not in formatted
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert caught.value.__suppress_context__ is True
    assert public.closed
    drain_logs = [fields for event, fields in log_records
                  if event == "rdp_user_startup_drain_failed"]
    assert len(drain_logs) == 1
    assert drain_logs[0]["category"] == "USER_STARTUP_DRAIN_FAILED"
    assert drain_logs[0]["exception_type"] == "RuntimeError"
    assert "secret-sentinel" not in repr(drain_logs[0])


@pytest.mark.asyncio
async def test_earlier_bootstrap_failure_log_uses_supplied_clock_without_scope(caplog):
    from polymaker.rdp_live import RDPBootstrapError

    async def bootstrap_failure(*_args, **_kwargs):
        raise RDPBootstrapError("INVALID_MARKET_SLUG", "secret-sentinel invalid input")

    with pytest.raises(RDPBootstrapError):
        await observe_rdp_market(
            Config(), "bad-slug", now=lambda: 123.5,
            bootstrap_factory=bootstrap_failure,
        )
    assert "'category': 'INVALID_MARKET_SLUG'" in caplog.text
    assert "'condition_id': None" in caplog.text
    assert "'terminated_at': 123.5" in caplog.text
    assert "secret-sentinel" not in caplog.text
