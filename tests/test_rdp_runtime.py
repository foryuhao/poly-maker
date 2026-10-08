from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace as dc_replace
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from polymaker.config import Config
from polymaker.domain import MarketMeta, TokenMeta
from polymaker.rdp_live import RDPBootstrap, RDPMarketStatusEvidence, TokenBookMetadata
from polymaker.rdp_marketdata import RDPFeedReadiness, RDPMarketDataSession, TokenBookEvidence
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
        self.service = SimpleNamespace(connection_generation=0)
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
        self.service.connection_generation = 1

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
@pytest.mark.parametrize("failure", [None, "FEED_DISCONNECTED", "USER_STREAM_TERMINATED", "EXPIRED", "CLOSED"])
async def test_retained_session_book_filter_result_requires_current_live_owners(failure):
    bootstrap = _bootstrap(int(time.time()) + 60)
    public = RDPMarketDataSession(bootstrap)
    public.service.connected = True
    public.service.connection_generation = 1
    public.task = asyncio.create_task(asyncio.Event().wait(), name="rdp-market-ws-fixture")
    user = FakeUserSession(bootstrap)
    feed = RDPFeedReadiness(bootstrap.market.condition_id, 1, (), time.time())
    backing = RDPInitialBackingResult(True, None, 30, 0, 30, 30, 0, True)
    wall = [time.time()]
    session = RDPInputSession(
        bootstrap, public_session=public, user_session=user, feed=feed,
        backing=backing, now=lambda: wall[0], sleep=asyncio.sleep,
        poll_interval_s=.1, started_at=wall[0],
    )
    service = public.service
    received = time.monotonic() - 2.5
    for token, tick in (("runtime-up", .01), ("runtime-down", .005)):
        book = service.book(token)
        assert book is not None
        book.set_tick_size(tick)
        book.apply_snapshot([(.5, 10)], [(.6, 10)], 1)
        book.snapshot_generation = 1
        service._snapshot_seen[token] = 1
        service.book_filter.observe_book(token, book, received, 1,
                                         condition_id=bootstrap.market.condition_id)
    result = session.book_filter_result()
    if failure is None:
        assert result.ready
    elif failure == "FEED_DISCONNECTED":
        service.connected = False
        assert not session.book_filter_result().ready
    elif failure == "USER_STREAM_TERMINATED":
        user.terminated = True
        assert not session.book_filter_result().ready
    elif failure == "EXPIRED":
        wall[0] = bootstrap.end_epoch
        assert not session.book_filter_result().ready
    else:
        await session.aclose()
        assert not session.book_filter_result().ready
    await session.aclose()


@pytest.mark.asyncio
async def test_closed_admission_rejections_keep_distinct_immutable_market_scope():
    now = time.time()
    first_bootstrap = _bootstrap(int(now) + 60)
    second_market = dc_replace(
        first_bootstrap.market,
        slug="btc-updown-5m-other",
        condition_id="other-condition",
    )
    second_bootstrap = dc_replace(first_bootstrap, market=second_market)

    def closed_session(bootstrap):
        public = FakePublicSession(bootstrap)
        user = FakeUserSession(bootstrap)
        feed = RDPFeedReadiness(bootstrap.market.condition_id, 1, (), now)
        backing = RDPInitialBackingResult(True, None, 30, 0, 30, 30, 0, True)
        session = RDPInputSession(
            bootstrap, public_session=public, user_session=user, feed=feed,
            backing=backing, now=lambda: now, sleep=asyncio.sleep,
            poll_interval_s=.1, started_at=now,
        )
        session._closed = True
        return session

    first = await closed_session(first_bootstrap).initial_normal_admission()
    second = await closed_session(second_bootstrap).initial_normal_admission()

    assert first.reason == second.reason == "SESSION_CLOSED"
    assert (first.slug, first.condition_id) == (
        first_bootstrap.market.slug, first_bootstrap.market.condition_id)
    assert (second.slug, second.condition_id) == (
        second_bootstrap.market.slug, second_bootstrap.market.condition_id)
    assert first != second
    assert first.execution_authorized is second.execution_authorized is False
    with pytest.raises((AttributeError, TypeError)):
        first.slug = "mutated"


@pytest.mark.asyncio
async def test_open_freezes_gamma_origin_and_status_limit_before_first_startup_await(monkeypatch):
    import polymaker.rdp_runtime as runtime
    from polymaker.state.store import StateStore
    from polymaker.state.tracker import UserEventProcessor

    clock = [100.0]
    (bootstrap, _boot, public_factory, _user_factory, backing, _publics,
     _users) = _dependencies(clock)
    cfg = Config()
    seen = []

    async def mutating_bootstrap_factory(_runtime_cfg, slug, *, now):
        assert slug == bootstrap.market.slug and now() < bootstrap.end_epoch
        cfg.wallet.gamma_host = "https://unapproved.example"
        cfg.risk.ws_stale_halt_s = 999
        return bootstrap

    async def fake_status(current_bootstrap, origin, *, now):
        seen.append((origin, now()))
        return RDPMarketStatusEvidence(
            current_bootstrap.market.slug, current_bootstrap.market.condition_id,
            tuple(token.token_id for token in current_bootstrap.market.tokens),
            tuple(token.outcome for token in current_bootstrap.market.tokens),
            current_bootstrap.market.neg_risk, current_bootstrap.end_epoch,
            True, False, now(),
        )

    monkeypatch.setattr(runtime, "read_rdp_market_status", fake_status)
    store = StateStore(":memory:")

    async def fake_user_factory(_cfg, current_bootstrap, *, now):
        user = FakeUserSession(current_bootstrap)
        user.processor = UserEventProcessor(store, retain_confirmed_receipts=True)
        user.store = store
        return user

    session = await RDPInputSession.open(
        cfg, bootstrap.market.slug, now=lambda: clock[0],
        bootstrap_factory=mutating_bootstrap_factory,
        public_session_factory=public_factory,
        user_session_factory=fake_user_factory,
        backing_evaluator=backing,
    )
    try:
        assert session._gamma_origin == "https://gamma-api.polymarket.com"
        assert session._market_status_max_age_s == Config().risk.ws_stale_halt_s
        admission = await session.initial_normal_admission()
        assert admission.reason == "ACCOUNT_GATEWAY_UNAVAILABLE"
        assert seen == [("https://gamma-api.polymarket.com", clock[0])]
    finally:
        await session.aclose()
        store.close()


@pytest.mark.asyncio
async def test_open_rejects_unapproved_gamma_origin_before_startup_requests():
    cfg = Config()
    cfg.wallet.gamma_host = "https://credential-sentinel@unapproved.example"
    called = []

    async def bootstrap_factory(*_args, **_kwargs):
        called.append(True)
        raise AssertionError("bootstrap must not run for an unapproved Gamma origin")

    with pytest.raises(RDPObservationError) as caught:
        await RDPInputSession.open(cfg, "btc-updown-5m-2000000100",
                                   bootstrap_factory=bootstrap_factory)
    assert caught.value.category == "UNAPPROVED_GAMMA_ORIGIN"
    assert "credential-sentinel" not in str(caught.value)
    assert called == []


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
    def __init__(self, config, *, paper, strict_order_producer=False):
        assert paper is False
        self._config = config
        from eth_account import Account

        self.address = Account.from_key(config.secrets.pk).address
        self.funder = config.secrets.browser_address
        self.creds = SimpleNamespace(api_key="api-sentinel", api_secret="secret-sentinel",
                                     api_passphrase="pass-sentinel")
        self.sockets = None
        self.closed = False
        self.connect_modes = []
        self.selected_state = False
        self.open_order_reads = 0
        self.open_order_hook = None
        self.captured_orders = []
        self.foreign_orders = []
        self.foreign_order_payload = []
        self.collateral_micros = 30_000_000
        self.allowance_micros = 30_000_000
        self.strict_order_producer = strict_order_producer
        self.fail_sdk_open_order = False
        self.order_producer = None
        self.order_producer_reads = []
        import threading

        self.block_sdk_open_order = False
        self.sdk_read_started = threading.Event()
        self.sdk_read_release = threading.Event()
        self.block_account_read = False
        self.block_open_order_read_number = None
        self.initial_read_entered = asyncio.Event()
        self.initial_read_release = asyncio.Event()
        self.initial_read_drained = asyncio.Event()
        self.block_gateway_close = False
        self.read_entered = asyncio.Event()
        self.read_drained = asyncio.Event()
        self.close_entered = asyncio.Event()
        self.close_release = asyncio.Event()

    async def connect(self, *, derive_only):
        self.connect_modes.append(derive_only)
        if self.strict_order_producer:
            from polymaker.execution.gateway import ExecutionGateway

            class FakeSDKClient:
                chain_id = 137

                def __init__(self, owner):
                    self.owner = owner

                def get_open_orders(self):
                    if (self.owner.fail_sdk_open_order
                            and self.owner.open_order_reads == 2):
                        raise RuntimeError("credential api_secret=credential-leak-sentinel")
                    if self.owner.block_sdk_open_order and self.owner.open_order_reads == 2:
                        self.owner.sdk_read_started.set()
                        self.owner.sdk_read_release.wait()
                    return self.owner.foreign_order_payload

            self.order_producer = ExecutionGateway(self._config, paper=False)
            self.order_producer._derive_only = derive_only
            self.order_producer._address = self.address
            self.order_producer._funder = self.funder
            self.order_producer._creds = self.creds
            self.order_producer._client = FakeSDKClient(self)

    async def open_orders(self, *, strict_account, exact_decimal):
        assert strict_account is True and exact_decimal is True
        assert self.sockets is not None and all(socket.active for socket in self.sockets)
        self.open_order_reads += 1
        if self.open_order_reads == 2 and callable(self.open_order_hook):
            self.open_order_hook()
        self.read_entered.set()
        if self.block_sdk_open_order and self.open_order_reads == 2:
            self.initial_read_entered.set()
        if self.open_order_reads == self.block_open_order_read_number:
            self.initial_read_entered.set()
            try:
                await self.initial_read_release.wait()
            finally:
                self.initial_read_drained.set()
        if self.block_account_read:
            try:
                await asyncio.Event().wait()
            finally:
                self.read_drained.set()
            return []
        if self.order_producer is not None:
            self.order_producer_reads.append((strict_account, exact_decimal))
            orders = await self.order_producer.open_orders(
                strict_account=strict_account, exact_decimal=exact_decimal)
            self.captured_orders.append(orders)
            self.read_drained.set()
            return orders
        await asyncio.sleep(.02)
        self.read_drained.set()
        return list(self.foreign_orders)

    async def positions(self, *, strict_account):
        assert strict_account is True
        assert all(socket.active for socket in self.sockets)
        up_quantity = 1 if self.selected_state else 0
        return {"runtime-up": (up_quantity, 0), "runtime-down": (0, 0)}

    async def required_collateral_spenders(self, _market):
        return ("0x0000000000000000000000000000000000000001",)

    async def balance_allowance(self):
        return {"balance": str(self.collateral_micros),
                "allowances": {"0x0000000000000000000000000000000000000001":
                               str(self.allowance_micros)}}

    async def aclose(self):
        if self.order_producer is not None:
            await self.order_producer.aclose()
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
    "initial_admission", "admission_fresh_selected", "admission_local_owned",
    "admission_minimum", "admission_price_gate", "admission_filter_warmup",
    "admission_generation_change", "admission_expiry", "admission_gamma_min_conflict",
    "admission_overlap_cancel", "admission_close_drain",
    "admission_local_pending", "admission_local_matched", "admission_local_confirmed",
    "admission_no_minimum", "admission_cash_shortfall", "admission_allowance_shortfall",
    "admission_tick_change", "admission_public_backlog",
    "admission_filter_inactive", "admission_filter_depth_extreme",
    "admission_filter_fast_move", "admission_price_gate_equality",
    "admission_user_failure", "admission_fresh_selected_order",
    "admission_executor_cancel", "admission_partial_foreign_buy",
    "admission_read_failure",
])
def test_cli_observe_only_uses_real_retained_owners_and_ends_market(
    tmp_path, monkeypatch, caplog, scenario,
):
    from typer.testing import CliRunner

    import polymaker.rdp_runtime as rdp_runtime
    import polymaker.rdp_userstream as rdp_userstream
    from polymaker.cli import app
    from polymaker.marketdata import service as market_service
    from polymaker.rdp_live import RDPMarketStatusEvidence, TokenBookMetadata
    from polymaker.rdp_marketdata import RDPMarketDataSession as RealPublicOwner
    from polymaker.rdp_runtime import RDPInputSession as RealInputSession
    from polymaker.rdp_userstream import UserStream as RealUserStream

    network_attempts = _network_socket_forbidden(monkeypatch)
    admission_scenario = scenario.startswith("admission_") or scenario == "initial_admission"
    end_epoch = int(time.time()) + (30 if scenario == "cancel_account_drain" or admission_scenario else 3)
    base = _bootstrap(end_epoch)
    if scenario == "admission_gamma_min_conflict":
        base = dc_replace(base, market=dc_replace(base.market, min_order_size=10))
    bootstrap = RDPBootstrap(
        base.market, base.epoch, end_epoch,
        (TokenBookMetadata("Up", "runtime-up", .01,
                           6 if scenario == "admission_minimum" else 5, False),
             TokenBookMetadata("Down", "runtime-down", .005,
                               6 if scenario == "admission_no_minimum" else 5, False)),
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
    admission_clock = [time.time()]

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
        gateway = _ReadOnlyFakeGateway(
            config, paper=paper,
            strict_order_producer=scenario in (
                "initial_admission", "admission_executor_cancel",
                "admission_partial_foreign_buy", "admission_read_failure",
            ))
        gateway.sockets = sockets
        gateway.selected_state = selected_state[0]
        if scenario == "initial_admission":
            gateway.foreign_order_payload = [{
                "id": "foreign-buy", "asset_id": "other-market-token", "side": "BUY",
                "original_size": "1", "size_matched": "0",
                "price": "0.1234567890123456789",
            }]
            gateway.collateral_micros = 31_000_000
            gateway.allowance_micros = 31_000_000
        if scenario == "admission_partial_foreign_buy":
            gateway.foreign_order_payload = [{
                "id": "partially-filled-foreign-buy", "asset_id": "other-market-token",
                "side": "BUY", "original_size": "1.1", "size_matched": "0.2",
                "price": "0.5",
            }]
            gateway.collateral_micros = 30_450_000
            gateway.allowance_micros = 30_450_000
        if scenario == "admission_read_failure":
            gateway.fail_sdk_open_order = True
        gateway.block_account_read = scenario == "cancel_account_drain"
        gateway.block_gateway_close = scenario == "cancel_account_drain"
        if scenario in ("admission_overlap_cancel", "admission_close_drain"):
            gateway.block_open_order_read_number = 2
        if scenario == "admission_executor_cancel":
            gateway.block_sdk_open_order = True
        if scenario == "admission_fresh_selected":
            gateway.open_order_hook = lambda: setattr(gateway, "selected_state", True)
        if scenario == "admission_generation_change":
            gateway.open_order_hook = lambda: setattr(
                owners[0].service, "connection_generation", 2)
        if scenario == "admission_expiry":
            def expire_during_read():
                admission_clock[0] = end_epoch

            gateway.open_order_hook = expire_during_read
        if scenario == "admission_cash_shortfall":
            gateway.open_order_hook = lambda: setattr(gateway, "collateral_micros", 29_000_000)
        if scenario == "admission_allowance_shortfall":
            gateway.open_order_hook = lambda: setattr(gateway, "allowance_micros", 29_000_000)
        if scenario == "admission_tick_change":
            gateway.open_order_hook = lambda: owners[0].service.book("runtime-up").set_tick_size(.004)
        if scenario == "admission_public_backlog":
            gateway.open_order_hook = lambda: owners[0].service.metrics.__setitem__(
                "received", owners[0].service.metrics["received"] + 1)
        if scenario == "admission_user_failure":
            gateway.open_order_hook = lambda: setattr(
                user_owners[0].stream, "failure_category", "USER_STREAM_TERMINATED")
        if scenario == "admission_fresh_selected_order":
            def add_selected_order():
                from polymaker.domain import Side
                from polymaker.execution.gateway import ExactOpenOrder

                gateway.foreign_orders = [ExactOpenOrder(
                    "selected-existing", "runtime-up", Side.BUY,
                    Decimal("0.4"), Decimal("1"),
                )]

            gateway.open_order_hook = add_selected_order
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

        if admission_scenario:
            # Establish a full two-second production filter history on the
            # current YES tick, then provide a recent real public trade for
            # each token before invoking the retained session method.
            public_socket = sockets[0]
            if scenario != "admission_filter_warmup":
                public_socket.queue.put_nowait(json.dumps({
                    "event_type": "book", "market": bootstrap.market.condition_id,
                    "asset_id": "runtime-up",
                    "bids": [{"price": ".4", "size": "3"}],
                    "asks": [{"price": ".6", "size": "4"}],
                    "timestamp": "2000000102",
                }))
                deadline = time.monotonic() + 1
                while time.monotonic() < deadline:
                    service = self.public_session.service
                    if (service.metrics["received"] == service.metrics["processed"]
                            and service.metrics["processed"] >= 4):
                        break
                    await asyncio.sleep(.001)
                await asyncio.sleep(2.1)
                if scenario == "admission_filter_depth_extreme":
                    public_socket.queue.put_nowait(json.dumps({
                        "event_type": "book", "market": bootstrap.market.condition_id,
                        "asset_id": "runtime-up", "bids": [{"price": ".4", "size": "60"}],
                        "asks": [{"price": ".6", "size": "4"}],
                        "timestamp": "2000000103",
                    }))
                elif scenario == "admission_filter_fast_move":
                    public_socket.queue.put_nowait(json.dumps({
                        "event_type": "book", "market": bootstrap.market.condition_id,
                        "asset_id": "runtime-up", "bids": [{"price": ".404", "size": "3"}],
                        "asks": [{"price": ".6", "size": "4"}],
                        "timestamp": "2000000103",
                    }))
                if scenario != "admission_filter_inactive":
                    for token in ("runtime-up", "runtime-down"):
                        public_socket.queue.put_nowait(json.dumps({
                            "event_type": "last_trade_price",
                            "market": bootstrap.market.condition_id,
                            "asset_id": token, "side": "BUY", "price": ".4",
                            "size": "1", "timestamp": "2000000103",
                        }))
                deadline = time.monotonic() + 1
                while time.monotonic() < deadline:
                    service = self.public_session.service
                    if service.metrics["received"] == service.metrics["processed"]:
                        break
                    await asyncio.sleep(.001)
            if scenario == "admission_price_gate_equality":
                no_book = self.public_session.service.book("runtime-down")
                no_book.apply_snapshot([(.6, 3)], [(.7, 4)], 2000000103)
                no_book.snapshot_generation = self.public_generation
            if scenario == "admission_local_owned":
                self.processor.register_order("existing-local", bootstrap.market.condition_id,
                                               "runtime-up", Side.BUY, 5, .3)
            if scenario == "admission_local_pending":
                self.processor.arm_pending("runtime-up", Side.BUY)
            if scenario in ("admission_local_matched", "admission_local_confirmed"):
                self.processor.register_order("existing-fill", bootstrap.market.condition_id,
                                               "runtime-up", Side.BUY, 5, .3)
                fill_state = (TradeState.MATCHED if scenario == "admission_local_matched"
                              else TradeState.CONFIRMED)
                self.processor.on_trade(TradeEvent(
                    "runtime-up", Side.BUY, .3, 1, "existing-trade", fill_state, 1,
                    "existing-fill",
                ), bootstrap.market.condition_id)
            if scenario == "admission_price_gate":
                no_book = self.public_session.service.book("runtime-down")
                no_book.apply_snapshot([(.61, 3)], [(.7, 4)], 2000000103)
                no_book.snapshot_generation = self.public_generation
            if scenario == "admission_expiry":
                self._now = lambda: admission_clock[0]
            if scenario == "admission_executor_cancel":
                gateway = gateways[0]
                gateway.read_drained.clear()
                first = asyncio.create_task(self.initial_normal_admission())
                await gateway.initial_read_entered.wait()
                assert await asyncio.to_thread(gateway.sdk_read_started.wait, 1)
                first.cancel()
                await asyncio.sleep(0)
                first.cancel()
                await asyncio.sleep(.005)
                assert not first.done()
                assert gateway.order_producer is not None
                assert gateway.order_producer._account_io
                assert not gateway.read_drained.is_set()
                gateway.sdk_read_release.set()
                with pytest.raises(asyncio.CancelledError):
                    await first
                assert gateway.read_drained.is_set()
                assert not gateway.order_producer._account_io
                retained_processor_evidence.append((True, True))
                self._now = lambda: self.bootstrap.end_epoch
                return await original_wait_until_end(self)
            if scenario in ("admission_overlap_cancel", "admission_close_drain"):
                gateway = gateways[0]
                gateway.read_drained.clear()
                first = asyncio.create_task(self.initial_normal_admission())
                await gateway.initial_read_entered.wait()
                if scenario == "admission_overlap_cancel":
                    overlap = await self.initial_normal_admission()
                    assert not overlap.eligible
                    assert overlap.reason == "INITIAL_ADMISSION_IN_PROGRESS"
                    assert overlap.slug == self.bootstrap.market.slug
                    assert overlap.condition_id == self.bootstrap.market.condition_id
                    assert overlap.execution_authorized is False
                    first.cancel()
                    await asyncio.sleep(0)
                    first.cancel()
                    await asyncio.sleep(.005)
                    assert not first.done()
                    assert not gateway.initial_read_drained.is_set()
                    gateway.initial_read_release.set()
                    with pytest.raises(asyncio.CancelledError):
                        await first
                    assert gateway.initial_read_drained.is_set()
                    assert not gateway.closed
                    retained_processor_evidence.append((overlap, True, True))
                    self._now = lambda: self.bootstrap.end_epoch
                    return await original_wait_until_end(self)
                else:
                    close_task = asyncio.create_task(self.aclose())
                    await asyncio.sleep(.005)
                    assert not close_task.done()
                    assert not gateway.closed
                    assert not gateway.initial_read_drained.is_set()
                    gateway.initial_read_release.set()
                    await close_task
                    with pytest.raises(asyncio.CancelledError):
                        await first
                    assert gateway.initial_read_drained.is_set() and gateway.closed
                    assert all(socket.closed for socket in sockets)
                    from polymaker.rdp_runtime import RDPObservationTermination

                    return RDPObservationTermination(
                        "READ_ONLY_OBSERVATION", bootstrap.market.slug,
                        bootstrap.market.condition_id, self.started_at, end_epoch,
                        "MARKET_END", "selected market interval ended",
                        self.public_generation, self.user_generation,
                    )
            decision = await self.initial_normal_admission()
            assert decision.slug == self.bootstrap.market.slug
            assert decision.condition_id == self.bootstrap.market.condition_id
            assert decision.execution_authorized is False
            if scenario == "admission_read_failure":
                assert not decision.eligible
                assert decision.reason == "ACCOUNT_READ_FAILED"
                assert self._account_read_task is None
                assert self._admission_task is None
                assert self._admission_active is False
                assert gateways[0].order_producer is not None
                assert not gateways[0].order_producer._account_io
            retained_processor_evidence.append((decision, tuple(self.processor._owned),
                                                len(self.processor.fill_records),
                                                len(self.processor.applied_fill_keys)))
            if scenario == "admission_generation_change":
                self.public_session.service.connection_generation = self.public_generation
            if scenario == "admission_user_failure":
                self.user_session.stream.failure_category = None
            self._now = lambda: self.bootstrap.end_epoch
            return await original_wait_until_end(self)

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

    async def fake_market_status(current_bootstrap, _origin, *, now):
        return RDPMarketStatusEvidence(
            slug=current_bootstrap.market.slug,
            condition_id=current_bootstrap.market.condition_id,
            token_ids=tuple(token.token_id for token in current_bootstrap.market.tokens),
            outcomes=tuple(token.outcome for token in current_bootstrap.market.tokens),
            neg_risk=current_bootstrap.market.neg_risk,
            end_epoch=current_bootstrap.end_epoch,
            accepting_orders=True,
            closed=False,
            receipt_ts=now(),
        )

    monkeypatch.setattr(rdp_runtime, "read_rdp_market_status", fake_market_status)
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
    expected_exit = (1 if scenario == "health_at_end" else
                     0 if scenario in ("market_end", "initial_admission") or admission_scenario else 1)
    assert result.exit_code == expected_exit, result.output
    if scenario in ("market_end", "initial_admission") or admission_scenario:
        assert "MARKET_END" in result.output
    elif scenario == "selected_state":
        assert "STARTUP_BACKING_INELIGIBLE" in result.output
    elif scenario == "health_at_end":
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
        assert input_session.placement_owner.current is None
        assert input_session.feed.connection_generation == 1
        assert input_session.backing.eligible is True
        if admission_scenario:
            if scenario == "admission_overlap_cancel":
                overlap, cancelled_and_drained, read_drained = retained_processor_evidence[0]
                assert overlap.reason == "INITIAL_ADMISSION_IN_PROGRESS"
                assert cancelled_and_drained and read_drained
                assert gateways[0].initial_read_drained.is_set()
                assert gateways[0].closed
            elif scenario == "admission_close_drain":
                assert gateways[0].initial_read_drained.is_set() and gateways[0].closed
                assert all(socket.closed for socket in sockets)
            elif scenario == "admission_executor_cancel":
                assert retained_processor_evidence == [(True, True)]
                assert gateways[0].order_producer is not None
                assert not gateways[0].order_producer._account_io
            else:
                decision, owned_after, fills_after, applied_after = retained_processor_evidence[0]
                assert decision.execution_authorized is False
                assert decision.slug == bootstrap.market.slug
                assert decision.condition_id == bootstrap.market.condition_id
                if scenario == "admission_local_owned":
                    assert owned_after == ("existing-local",)
                elif scenario in ("admission_local_matched", "admission_local_confirmed"):
                    assert owned_after == ("existing-fill",)
                else:
                    assert owned_after == () and fills_after == applied_after == 0
                if scenario in ("initial_admission", "admission_partial_foreign_buy",
                                "admission_generation_change", "admission_expiry",
                                "admission_gamma_min_conflict", "admission_price_gate_equality",
                                "admission_user_failure"):
                    # Generation/expiry failures occur after the retained read;
                    # ordinary success proves exact candidate and capital output.
                    if scenario in ("initial_admission", "admission_partial_foreign_buy",
                                    "admission_gamma_min_conflict",
                                    "admission_price_gate_equality"):
                        assert decision.eligible and decision.reason is None
                        assert decision.candidate is not None and decision.candidate.reason is None
                        expected_no_price = (.595 if scenario == "admission_price_gate_equality" else .395)
                        assert [(quote.token_id, quote.price, quote.size)
                                for quote in decision.candidate.quotes] == [
                                    ("runtime-up", .398, 5.0), ("runtime-down", expected_no_price, 5.0)]
                        assert decision.capital_admission.admitted
                        assert decision.capital_admission.protected_amount == 5
                        if scenario == "admission_price_gate_equality":
                            assert decision.capital_admission.free_cash_after_new == Decimal("25.0350")
                        else:
                            assert decision.capital_admission.free_cash_after_new == Decimal("26.0350")
                        assert decision.capital_projection.snapshot.capital == 30
                        assert decision.capital_projection.snapshot.inventory_cost == 0
                        assert decision.capital_projection.snapshot.reserved_cash == 0
                        assert decision.capital_projection.snapshot.free_cash == 30
                        projection = decision.capital_projection.snapshot
                        assert (projection.free_cash + projection.reserved_cash
                                + projection.inventory_cost) == Decimal("30.00")
                        assert decision.account_receipt_times is not None
                        if scenario == "initial_admission":
                            assert decision.backing.account_buy_commitment == Decimal("0.1234567890123456789")
                            assert gateways[0].order_producer is not None
                            assert gateways[0].order_producer_reads == [(True, True), (True, True)]
                        if scenario == "admission_partial_foreign_buy":
                            assert decision.backing.account_buy_commitment == Decimal("0.45")
                            assert decision.backing.observed_cash_backing == Decimal("30.00")
                            assert decision.backing.observed_allowance_backing == Decimal("30.00")
                            assert gateways[0].order_producer is not None
                            assert gateways[0].order_producer_reads == [(True, True), (True, True)]
                            parsed = gateways[0].captured_orders[1]
                            assert len(parsed) == 1
                            assert parsed[0].remaining_size == Decimal("0.9")
                            assert parsed[0].price == Decimal("0.5")
                        assert decision.token_ticks == (("runtime-up", .002), ("runtime-down", .005))
                        assert decision.minimum_sizes == (("runtime-up", 5.0), ("runtime-down", 5.0))
                        assert gateways[0].open_order_reads == 2
                        assert gateways[0].connect_modes == [True]
                        assert "HUMAN_LIVE_AUTHORIZATION" in decision.outstanding_execution_gates
                    else:
                        assert not decision.eligible
                        assert decision.reason in ("FEED_GENERATION_CHANGED", "MARKET_INTERVAL_ELAPSED",
                                                   "MARKET_EXPIRED",
                                                   "USER_STREAM_TERMINATED")
                elif scenario == "admission_fresh_selected":
                    assert not decision.eligible and decision.reason == "SELECTED_STATE_NOT_EMPTY"
                    assert decision.backing is not None and not decision.backing.selected_state_empty
                elif scenario == "admission_local_owned":
                    assert not decision.eligible and decision.reason == "LOCAL_SCOPE_NOT_EMPTY"
                    assert owned_after == ("existing-local",)
                    assert gateways[0].open_order_reads == 1
                elif scenario in ("admission_local_pending", "admission_local_matched",
                                  "admission_local_confirmed"):
                    assert not decision.eligible and decision.reason == "LOCAL_SCOPE_NOT_EMPTY"
                elif scenario == "admission_minimum":
                    assert not decision.eligible and decision.reason == "CLOB_MINIMUM_EXCEEDS_FROZEN_CLIP"
                    assert decision.minimum_sizes == (("runtime-up", 6.0), ("runtime-down", 5.0))
                elif scenario == "admission_price_gate":
                    assert not decision.eligible and decision.reason == "PRICE_GATE"
                elif scenario == "admission_filter_warmup":
                    assert not decision.eligible and decision.reason == "BOOK_FILTER_UNREADY"
                elif scenario in ("admission_cash_shortfall", "admission_allowance_shortfall"):
                    assert not decision.eligible
                    assert decision.reason == ("INSUFFICIENT_CASH_BACKING"
                                               if scenario == "admission_cash_shortfall"
                                               else "INSUFFICIENT_ALLOWANCE_BACKING")
                elif scenario == "admission_no_minimum":
                    assert not decision.eligible
                    assert decision.reason == "CLOB_MINIMUM_EXCEEDS_FROZEN_CLIP"
                    assert decision.minimum_sizes == (("runtime-up", 5.0), ("runtime-down", 6.0))
                elif scenario == "admission_tick_change":
                    assert not decision.eligible and decision.reason == "BOOK_FILTER_UNREADY"
                elif scenario == "admission_public_backlog":
                    assert not decision.eligible and decision.reason == "PUBLIC_INPUT_BACKLOG"
                elif scenario in ("admission_filter_inactive", "admission_filter_depth_extreme",
                                  "admission_filter_fast_move"):
                    assert not decision.eligible and decision.reason == "BOOK_FILTER_REJECTED"
                    reasons = decision.book_filter.reasons
                    if scenario == "admission_filter_inactive":
                        assert any(reason.startswith("INACTIVE_") for reason in reasons)
                    elif scenario == "admission_filter_depth_extreme":
                        assert "DEPTH_EXTREME" in reasons
                    else:
                        assert any(reason.startswith("FAST_MOVE_") for reason in reasons)
                elif scenario == "admission_fresh_selected_order":
                    assert not decision.eligible and decision.reason == "SELECTED_STATE_NOT_EMPTY"
                elif scenario == "admission_read_failure":
                    assert not decision.eligible and decision.reason == "ACCOUNT_READ_FAILED"
                    assert decision.backing is None
                    assert gateways[0].order_producer is not None
                    assert not gateways[0].order_producer._account_io
                    assert not input_session._admission_active
                    assert input_session._admission_task is None
                    assert input_session._account_read_task is None
        elif scenario == "market_end":
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
    if retained_processor_evidence and isinstance(retained_processor_evidence[0], tuple):
        first_evidence = retained_processor_evidence[0][0]
        if hasattr(first_evidence, "execution_authorized"):
            assert "api-sentinel" not in repr(first_evidence)
            assert "secret-sentinel" not in repr(first_evidence)
    assert "api-sentinel" not in caplog.text and "secret-sentinel" not in caplog.text
    assert network_attempts == []
    durable_log = (tmp_path / "logs" / "rdp-live-preflight.jsonl").read_text()
    assert "api-sentinel" not in durable_log and "secret-sentinel" not in durable_log
    if scenario == "admission_read_failure":
        assert "credential-leak-sentinel" not in result.output
        assert "credential-leak-sentinel" not in caplog.text
        assert "credential-leak-sentinel" not in durable_log
        decision = retained_processor_evidence[0][0]
        assert "credential-leak-sentinel" not in repr(decision)
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
