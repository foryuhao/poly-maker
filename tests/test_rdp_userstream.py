from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from polymaker.config import Config, Secrets, WalletConfig
from polymaker.domain import MarketMeta, Side, TokenMeta
from polymaker.execution.gateway import ExactOpenOrder
from polymaker.rdp_live import RDPBootstrap, TokenBookMetadata
from polymaker.rdp_userstream import (
    RDPUserChannelError,
    observe_rdp_account_and_user_channel,
)

PRIVATE_KEY = "0x" + "1" * 64
CONDITION = "condition-rdp-user"


def _inputs():
    from eth_account import Account

    signer = Account.from_key(PRIVATE_KEY).address
    cfg = Config(secrets=Secrets(PK=PRIVATE_KEY, BROWSER_ADDRESS=signer),
                 wallet=WalletConfig(signature_type=0))
    end = int(time.time()) + 120
    market = MarketMeta(
        condition_id=CONDITION, question="BTC Up or Down", slug="btc-updown-5m-2000000100",
        tokens=(TokenMeta("up", "Up"), TokenMeta("down", "Down")), tick_size=.01,
        neg_risk=False, min_order_size=5, rewards_min_size=0, rewards_max_spread=0,
        rewards_daily_rate=0, maker_fee_bps=0, taker_fee_bps=0, fees_enabled=False,
        end_date_iso=datetime.fromtimestamp(end, UTC).isoformat(), event_id=None,
    )
    bootstrap = RDPBootstrap(
        market, end - 300, end,
        (TokenBookMetadata("Up", "up", .01, 5, False),
         TokenBookMetadata("Down", "down", .01, 5, False)), time.time(),
    )
    return cfg, signer, bootstrap


class FakeGateway:
    def __init__(self, cfg, *, paper):
        self.address = cfg.secrets.browser_address
        self.funder = cfg.secrets.browser_address
        self.creds = SimpleNamespace(api_key="sentinel-key", api_secret="sentinel-secret",
                                     api_passphrase="sentinel-passphrase")
        self.paper = paper
        self.connected = False
        self.closed = False
        self.calls = []
        self.stream = None
        self.read_started: asyncio.Event | None = None
        self.read_release: asyncio.Event | None = None
        self.fail_orders: Exception | None = None
        self.on_open_orders = None
        self.on_balance = None
        self.close_started: asyncio.Event | None = None
        self.close_release: asyncio.Event | None = None

    async def connect(self, *, derive_only=False):
        self.calls.append(("connect", derive_only))
        assert derive_only is True
        self.connected = True

    async def open_orders(self, *, strict_account=False, exact_decimal=False):
        self.calls.append(("open_orders", strict_account, exact_decimal))
        assert self.stream is not None and self.stream.connected
        assert strict_account and exact_decimal
        if self.read_started is not None:
            self.read_started.set()
        if self.read_release is not None:
            await self.read_release.wait()
        if self.on_open_orders is not None:
            self.on_open_orders()
        if self.fail_orders is not None:
            raise self.fail_orders
        await asyncio.sleep(.025)
        return [ExactOpenOrder("foreign-order", "foreign-token", Side.BUY,
                               Decimal(".4"), Decimal("2"))]

    async def positions(self, *, strict_account=False):
        self.calls.append(("positions", strict_account))
        assert self.stream is not None and self.stream.connected
        assert strict_account
        return {"up": (0, 0), "down": (0, 0)}

    async def required_collateral_spenders(self, _market):
        self.calls.append(("spenders",))
        return ("0x" + "a" * 40,)

    async def balance_allowance(self):
        self.calls.append(("balance_allowance",))
        assert self.stream is not None and self.stream.connected
        if self.on_balance is not None:
            self.on_balance()
        return {"balance": "30000000", "allowances": {"0x" + "a" * 40: "30000000"}}

    async def place_order(self, *_args, **_kwargs):
        raise AssertionError("read-only preflight attempted order placement")

    async def cancel_order(self, *_args, **_kwargs):
        raise AssertionError("read-only preflight attempted order cancellation")

    async def aclose(self):
        if self.close_started is not None:
            self.close_started.set()
        if self.close_release is not None:
            await self.close_release.wait()
        self.closed = True

    def close(self):
        self.closed = True


class FakeWebSocket:
    def __init__(self, *, pong=True, pong_bytes=True, rejection=None, fail_send=False,
                 receive_exception=None, extra_frames=(), pong_delay_s=0.0,
                 receive_exception_after_frames=0, close_exception=None):
        self.queue: asyncio.Queue[str | bytes | None] = asyncio.Queue()
        self.sent: list[str] = []
        self.active = False
        self.pong = pong
        self.pong_bytes = pong_bytes
        self.rejection = rejection
        self.fail_send = fail_send
        self.receive_exception = receive_exception
        self.extra_frames = extra_frames
        self.pong_delay_s = pong_delay_s
        self.receive_exception_after_frames = receive_exception_after_frames
        self.close_exception = close_exception
        self._received_frames = 0
        self._close_exception_pending = False
        self._timers = []
        self.ping_times: list[float] = []
        self.ping_event = asyncio.Event()
        self.sent_event = asyncio.Event()

    async def send(self, raw: str):
        if self.fail_send and raw == "PING":
            raise RuntimeError("sentinel-secret send failure")
        self.sent.append(raw)
        self.sent_event.set()
        if raw == "PING":
            self.ping_times.append(time.monotonic())
            self.ping_event.set()
            if self.rejection is not None:
                await self.queue.put(json.dumps(self.rejection))
            elif self.pong:
                pong = b"PONG" if self.pong_bytes else "PONG"
                if self.pong_delay_s:
                    timer = asyncio.get_running_loop().call_later(
                        self.pong_delay_s, self.queue.put_nowait, pong,
                    )
                    self._timers.append(timer)
                else:
                    await self.queue.put(pong)
            for frame in self.extra_frames:
                await self.queue.put(frame)

    async def close(self):
        if self.active:
            self.active = False
            for timer in self._timers:
                timer.cancel()
            self._close_exception_pending = self.close_exception is not None
            await self.queue.put(None)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._close_exception_pending:
            self._close_exception_pending = False
            raise self.close_exception
        if (self.receive_exception is not None
                and self._received_frames >= self.receive_exception_after_frames):
            raise self.receive_exception
        value = await self.queue.get()
        if value is None:
            raise StopAsyncIteration
        self._received_frames += 1
        return value


class FakeConnect:
    def __init__(self, ws, url, kwargs):
        self.ws = ws
        self.url = url
        self.kwargs = kwargs

    async def __aenter__(self):
        self.ws.active = True
        return self.ws

    async def __aexit__(self, *_exc):
        await self.ws.close()


class TrackingStore:
    def __init__(self, path):
        from polymaker.state.store import StateStore

        assert path == ":memory:"
        self.inner = StateStore(path)
        self.closed = False
        self.owned_orders_at_close = None
        self.orders_at_close = None

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def close(self):
        self.closed = True
        self.owned_orders_at_close = self.inner.owned_orders()
        self.orders_at_close = dict(self.inner.orders)
        self.inner.close()


class GenerationChangingStream:
    def __init__(self, *_args, **_kwargs):
        self.connected = True
        self.connection_generation = 1
        self.heartbeat_generation = 1
        self.heartbeat_receipt_ts = time.time()
        self.subscription_sent = True
        self.failure_category = None
        self._closed = asyncio.Event()

    def set_markets(self, condition_ids):
        self.markets = condition_ids

    @property
    def heartbeat_current(self):
        return self.connected and self.connection_generation == self.heartbeat_generation

    async def wait_for_application_pong(self, _timeout):
        return None

    async def run_once(self):
        await self._closed.wait()


class CleanupBlockingStream(GenerationChangingStream):
    def __init__(self, *_args, **_kwargs):
        super().__init__()
        self.started = asyncio.Event()
        self.draining = asyncio.Event()
        self.drain_release = asyncio.Event()

    async def run_once(self):
        self.started.set()
        try:
            await self._closed.wait()
        except asyncio.CancelledError:
            self.draining.set()
            await self.drain_release.wait()
            self.connected = False
            raise

    def stop_run(self):
        self._closed.set()


@pytest.mark.asyncio
@pytest.mark.parametrize("pong_bytes", [False, True])
async def test_composed_account_capture_uses_one_gateway_and_current_user_stream(
    monkeypatch, pong_bytes,
):
    from polymaker.userstream.client import UserStream

    cfg, signer, bootstrap = _inputs()
    ws = FakeWebSocket(pong_bytes=pong_bytes)
    ws.extra_frames = (json.dumps({
        "event_type": "order", "id": "unowned-order", "asset_id": "up",
        "side": "BUY", "price": ".5", "original_size": "5",
        "size_matched": "0", "status": "LIVE",
    }),)
    made = []
    connect_calls = []
    store = TrackingStore(":memory:")

    def gateway_factory(config, *, paper):
        gateway = FakeGateway(config, paper=paper)
        made.append(gateway)
        return gateway

    def stream_factory(*args, **kwargs):
        kwargs["heartbeat_interval_s"] = .01
        kwargs["heartbeat_timeout_s"] = .15
        stream = UserStream(*args, **kwargs)
        made[0].stream = stream
        return stream

    def connect(url, **kwargs):
        connect_calls.append((url, kwargs))
        return FakeConnect(ws, url, kwargs)

    monkeypatch.setattr("polymaker.userstream.client.websockets.connect", connect)
    monkeypatch.setattr("polymaker.rdp_userstream.UserStream", stream_factory)
    result = await observe_rdp_account_and_user_channel(
        cfg, bootstrap, gateway_factory=gateway_factory,
        store_factory=lambda path: store,
        user_stream_factory=stream_factory,
    )

    sub = json.loads(ws.sent[0])
    assert ws.sent[1] == "PING"
    assert ws.sent.count("PING") >= 2
    assert connect_calls[0][0] == "wss://ws-subscriptions-clob.polymarket.com/ws/user"
    assert connect_calls[0][1]["ping_interval"] == 5
    assert connect_calls[0][1]["ping_timeout"] == 10
    assert sub["markets"] == [CONDITION]
    assert sub["auth"] == {"apiKey": "sentinel-key", "secret": "sentinel-secret",
                          "passphrase": "sentinel-passphrase"}
    assert made[0].calls == [("connect", True), ("open_orders", True, True),
                             ("positions", True), ("spenders",), ("balance_allowance",)]
    assert result.account.signer == signer
    assert result.user_channel.condition_id == CONDITION
    assert result.user_channel.subscription_frame_sent
    assert result.user_channel.application_pong_received
    assert result.user_channel.connection_generation == 1
    assert result.user_channel.explicit_server_authentication_acknowledgment is False
    assert "sentinel" not in repr(result)
    assert store.owned_orders_at_close == []
    assert store.orders_at_close == {}
    assert made[0].closed and store.closed and not ws.active


@pytest.mark.asyncio
async def test_composed_capture_rejects_json_pong_and_sanitizes_failure(monkeypatch, caplog):
    from polymaker.userstream.client import UserStream

    cfg, _signer, bootstrap = _inputs()
    ws = FakeWebSocket(pong=False)
    made = []

    def gateway_factory(config, *, paper):
        gateway = FakeGateway(config, paper=paper)
        made.append(gateway)
        return gateway

    def stream_factory(*args, **kwargs):
        kwargs["heartbeat_timeout_s"] = .03
        stream = UserStream(*args, **kwargs)
        made[0].stream = stream
        return stream

    monkeypatch.setattr(
        "polymaker.userstream.client.websockets.connect",
        lambda url, **kwargs: FakeConnect(ws, url, kwargs),
    )
    monkeypatch.setattr("polymaker.rdp_userstream.UserStream", stream_factory)
    # A JSON frame that resembles a protocol pong is not the required text PONG.
    async def send(raw):
        if raw != "PING":
            ws.sent.append(raw)
            return
        ws.sent.append(raw)
        if raw == "PING":
            await ws.queue.put('{"type":"PONG"}')
    ws.send = send
    with pytest.raises(RDPUserChannelError) as caught:
        await observe_rdp_account_and_user_channel(cfg, bootstrap,
                                                   gateway_factory=gateway_factory,
                                                   user_stream_factory=stream_factory)
    assert caught.value.category == "APPLICATION_PONG_TIMEOUT"
    assert "sentinel" not in str(caught.value)
    assert "sentinel" not in caplog.text
    assert made[0].closed and not ws.active
    assert made[0].calls == [("connect", True)]


@pytest.mark.asyncio
async def test_generation_change_before_final_capture_rejects_stale_pong():
    cfg, _signer, bootstrap = _inputs()
    made = []
    streams = []

    def gateway_factory(config, *, paper):
        gateway = FakeGateway(config, paper=paper)
        made.append(gateway)
        return gateway

    def stream_factory(*args, **kwargs):
        stream = GenerationChangingStream(*args, **kwargs)
        streams.append(stream)
        made[0].stream = stream
        return stream

    def reconnect_before_capture():
        streams[0].connection_generation = 2
        # The old receipt remains tagged to generation 1.

    made_gateway = None

    def factory(config, *, paper):
        nonlocal made_gateway
        made_gateway = gateway_factory(config, paper=paper)
        made_gateway.on_balance = reconnect_before_capture
        return made_gateway

    with pytest.raises(RDPUserChannelError) as caught:
        await observe_rdp_account_and_user_channel(
            cfg, bootstrap, gateway_factory=factory,
            user_stream_factory=stream_factory,
        )
    assert caught.value.category == "USER_CHANNEL_NOT_CURRENT_AT_CAPTURE"
    assert streams[0].connection_generation == 2
    assert streams[0].heartbeat_generation == 1
    assert made_gateway is not None and made_gateway.closed


@pytest.mark.asyncio
async def test_subscription_rejection_fails_closed_without_secret_payload(monkeypatch):
    from polymaker.userstream.client import UserStream

    cfg, _signer, bootstrap = _inputs()
    ws = FakeWebSocket(rejection={"type": "auth_error", "error": "sentinel-secret"})
    made = []

    def gateway_factory(config, *, paper):
        gateway = FakeGateway(config, paper=paper)
        made.append(gateway)
        return gateway

    def stream_factory(*args, **kwargs):
        stream = UserStream(*args, **kwargs)
        made[0].stream = stream
        return stream

    monkeypatch.setattr(
        "polymaker.userstream.client.websockets.connect",
        lambda url, **kwargs: FakeConnect(ws, url, kwargs),
    )
    monkeypatch.setattr("polymaker.rdp_userstream.UserStream", stream_factory)
    with pytest.raises(RDPUserChannelError) as caught:
        await observe_rdp_account_and_user_channel(cfg, bootstrap,
                                                   gateway_factory=gateway_factory,
                                                   user_stream_factory=stream_factory)
    assert caught.value.category == "USER_SUBSCRIPTION_REJECTED"
    assert "sentinel" not in str(caught.value)
    assert made[0].closed and not ws.active


@pytest.mark.asyncio
async def test_heartbeat_send_failure_is_sanitized_and_gateway_closes(monkeypatch):
    from polymaker.userstream.client import UserStream

    cfg, _signer, bootstrap = _inputs()
    ws = FakeWebSocket(fail_send=True)
    made = []

    def gateway_factory(config, *, paper):
        gateway = FakeGateway(config, paper=paper)
        made.append(gateway)
        return gateway

    def stream_factory(*args, **kwargs):
        stream = UserStream(*args, **kwargs)
        made[0].stream = stream
        return stream

    monkeypatch.setattr(
        "polymaker.userstream.client.websockets.connect",
        lambda url, **kwargs: FakeConnect(ws, url, kwargs),
    )
    monkeypatch.setattr("polymaker.rdp_userstream.UserStream", stream_factory)
    with pytest.raises(RDPUserChannelError) as caught:
        await observe_rdp_account_and_user_channel(cfg, bootstrap,
                                                   gateway_factory=gateway_factory,
                                                   user_stream_factory=stream_factory)
    assert caught.value.category == "APPLICATION_PING_SEND_FAILED"
    assert "sentinel" not in str(caught.value)
    assert made[0].closed and not ws.active


@pytest.mark.asyncio
async def test_receive_failure_is_sanitized_and_gateway_closes(monkeypatch, caplog):
    from polymaker.userstream.client import UserStream

    cfg, _signer, bootstrap = _inputs()
    ws = FakeWebSocket(receive_exception=RuntimeError("sentinel-secret receive failure"))
    made = []

    def gateway_factory(config, *, paper):
        gateway = FakeGateway(config, paper=paper)
        made.append(gateway)
        return gateway

    def stream_factory(*args, **kwargs):
        stream = UserStream(*args, **kwargs)
        made[0].stream = stream
        return stream

    monkeypatch.setattr(
        "polymaker.userstream.client.websockets.connect",
        lambda url, **kwargs: FakeConnect(ws, url, kwargs),
    )
    monkeypatch.setattr("polymaker.rdp_userstream.UserStream", stream_factory)
    with pytest.raises(RDPUserChannelError) as caught:
        await observe_rdp_account_and_user_channel(
            cfg, bootstrap, gateway_factory=gateway_factory,
            user_stream_factory=stream_factory,
        )
    assert caught.value.category in {"USER_STREAM_CONNECTION_FAILED", "USER_STREAM_DISCONNECTED"}
    assert "sentinel" not in str(caught.value) and "sentinel" not in caplog.text
    assert made[0].closed and not ws.active


@pytest.mark.asyncio
async def test_subscription_rejection_category_survives_following_receive_error(monkeypatch, caplog):
    from polymaker.userstream.client import UserStream

    cfg, _signer, bootstrap = _inputs()
    ws = FakeWebSocket(
        rejection={"type": "auth_error", "error": "sentinel-secret"},
        receive_exception=RuntimeError("sentinel-secret teardown"),
        receive_exception_after_frames=1,
    )
    made = []

    def gateway_factory(config, *, paper):
        gateway = FakeGateway(config, paper=paper)
        made.append(gateway)
        return gateway

    def stream_factory(*args, **kwargs):
        stream = UserStream(*args, **kwargs)
        made[0].stream = stream
        return stream

    monkeypatch.setattr(
        "polymaker.userstream.client.websockets.connect",
        lambda url, **kwargs: FakeConnect(ws, url, kwargs),
    )
    with pytest.raises(RDPUserChannelError) as caught:
        await observe_rdp_account_and_user_channel(
            cfg, bootstrap, gateway_factory=gateway_factory,
            user_stream_factory=stream_factory,
        )
    assert caught.value.category == "USER_SUBSCRIPTION_REJECTED"
    assert "sentinel" not in str(caught.value) and "sentinel" not in caplog.text
    assert made[0].closed and not ws.active


@pytest.mark.asyncio
async def test_heartbeat_timeout_category_survives_teardown_error(monkeypatch, caplog):
    from polymaker.userstream.client import UserStream

    cfg, _signer, bootstrap = _inputs()
    ws = FakeWebSocket(pong=False, close_exception=RuntimeError("sentinel-secret teardown"))
    made = []

    def gateway_factory(config, *, paper):
        gateway = FakeGateway(config, paper=paper)
        made.append(gateway)
        return gateway

    def stream_factory(*args, **kwargs):
        kwargs["heartbeat_interval_s"] = .01
        kwargs["heartbeat_timeout_s"] = .04
        stream = UserStream(*args, **kwargs)
        made[0].stream = stream
        return stream

    monkeypatch.setattr(
        "polymaker.userstream.client.websockets.connect",
        lambda url, **kwargs: FakeConnect(ws, url, kwargs),
    )
    with pytest.raises(RDPUserChannelError) as caught:
        await observe_rdp_account_and_user_channel(
            cfg, bootstrap, gateway_factory=gateway_factory,
            user_stream_factory=stream_factory,
        )
    assert caught.value.category == "APPLICATION_PONG_TIMEOUT"
    assert "sentinel" not in str(caught.value) and "sentinel" not in caplog.text
    assert made[0].closed and not ws.active


@pytest.mark.asyncio
async def test_delayed_pong_does_not_shift_fixed_send_cadence(monkeypatch):
    from polymaker.state.store import StateStore
    from polymaker.state.tracker import UserEventProcessor
    from polymaker.userstream.client import UserStream

    ws = FakeWebSocket(pong_delay_s=.035)
    store = StateStore(":memory:")
    stream = UserStream(
        SimpleNamespace(api_key="key", api_secret="secret", api_passphrase="passphrase"),
        "wallet", UserEventProcessor(store), other_token=lambda _token: None,
        condition_of_token=lambda _token: None, application_heartbeat=True,
        heartbeat_interval_s=.012, heartbeat_timeout_s=.08, sanitize_errors=True,
    )
    stream.set_markets([CONDITION])
    monkeypatch.setattr(
        "polymaker.userstream.client.websockets.connect",
        lambda url, **kwargs: FakeConnect(ws, url, kwargs),
    )
    task = asyncio.create_task(stream.run_once())
    try:
        async with asyncio.timeout(.3):
            while len(ws.ping_times) < 4:
                ws.ping_event.clear()
                await ws.ping_event.wait()
        intervals = [right - left for left, right in
                     zip(ws.ping_times, ws.ping_times[1:], strict=False)]
        assert all(interval < .025 for interval in intervals[:3])
        assert stream.heartbeat_current
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        store.close()


@pytest.mark.asyncio
async def test_silent_heartbeat_fails_at_oldest_outstanding_deadline(monkeypatch):
    from polymaker.state.store import StateStore
    from polymaker.state.tracker import UserEventProcessor
    from polymaker.userstream.client import UserStream

    ws = FakeWebSocket(pong=False)
    store = StateStore(":memory:")
    stream = UserStream(
        SimpleNamespace(api_key="key", api_secret="secret", api_passphrase="passphrase"),
        "wallet", UserEventProcessor(store), other_token=lambda _token: None,
        condition_of_token=lambda _token: None, application_heartbeat=True,
        heartbeat_interval_s=.008, heartbeat_timeout_s=.045, sanitize_errors=True,
    )
    stream.set_markets([CONDITION])
    monkeypatch.setattr(
        "polymaker.userstream.client.websockets.connect",
        lambda url, **kwargs: FakeConnect(ws, url, kwargs),
    )
    started = time.monotonic()
    try:
        await stream.run_once()
        elapsed = time.monotonic() - started
        assert stream.failure_category == "APPLICATION_PONG_TIMEOUT"
        assert .035 <= elapsed < .12
        assert len(ws.ping_times) >= 3
    finally:
        store.close()


@pytest.mark.asyncio
async def test_pong_at_send_slot_boundary_starts_next_unanswered_deadline(monkeypatch):
    import polymaker.userstream.client as client
    from polymaker.state.store import StateStore
    from polymaker.state.tracker import UserEventProcessor
    from polymaker.userstream.client import UserStream

    clock = SimpleNamespace(now=0.0)
    ws = SimpleNamespace(sent=[])

    async def send(frame):
        ws.sent.append((frame, clock.now))

    async def close():
        return None

    ws.send = send
    ws.close = close
    store = StateStore(":memory:")
    stream = UserStream(
        SimpleNamespace(api_key="key", api_secret="secret", api_passphrase="passphrase"),
        "wallet", UserEventProcessor(store), other_token=lambda _token: None,
        condition_of_token=lambda _token: None, application_heartbeat=True,
        heartbeat_interval_s=10, heartbeat_timeout_s=15,
    )
    stream.connected = True
    stream.connection_generation = 1
    waits = []

    async def controlled_wait_for(awaitable, *, timeout):
        waits.append(timeout)
        # At t=10 the cadence wait wins scheduling, but a valid response for
        # the first PING is delivered before the heartbeat task resumes.
        if len(waits) == 1:
            clock.now = 10.0
            stream._heartbeat_pending = False
            stream.heartbeat_generation = 1
            stream.heartbeat_receipt_monotonic = 10.0
            stream._heartbeat_event.set()
        elif len(waits) == 2:
            clock.now = 20.0
        elif len(waits) == 3:
            clock.now = 25.0
        else:
            pytest.fail("heartbeat loop waited beyond oldest unanswered PONG deadline")
        awaitable.close()
        raise TimeoutError

    monkeypatch.setattr(client, "time", SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(
        client, "asyncio",
        SimpleNamespace(wait_for=controlled_wait_for, CancelledError=asyncio.CancelledError),
    )
    try:
        await stream._application_heartbeat_loop(ws, 1)
        assert ws.sent == [("PING", 0.0), ("PING", 10.0), ("PING", 20.0)]
        assert waits == [10.0, 10.0, 5.0]
        assert stream.failure_category == "APPLICATION_PONG_TIMEOUT"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_account_failure_with_secret_exception_is_sanitized(monkeypatch, caplog):
    from polymaker.userstream.client import UserStream

    cfg, _signer, bootstrap = _inputs()
    ws = FakeWebSocket()
    made = []

    def gateway_factory(config, *, paper):
        gateway = FakeGateway(config, paper=paper)
        gateway.fail_orders = RuntimeError("sentinel-secret account read failure")
        made.append(gateway)
        return gateway

    def stream_factory(*args, **kwargs):
        stream = UserStream(*args, **kwargs)
        made[0].stream = stream
        return stream

    monkeypatch.setattr(
        "polymaker.userstream.client.websockets.connect",
        lambda url, **kwargs: FakeConnect(ws, url, kwargs),
    )
    with pytest.raises(RDPUserChannelError) as caught:
        await observe_rdp_account_and_user_channel(
            cfg, bootstrap, gateway_factory=gateway_factory,
            user_stream_factory=stream_factory,
        )
    assert caught.value.category == "ACCOUNT_READ_FAILED"
    assert "sentinel" not in str(caught.value) and "sentinel" not in caplog.text
    assert made[0].closed and not ws.active


@pytest.mark.asyncio
async def test_disconnect_during_account_reads_invalidates_capture(monkeypatch):
    from polymaker.userstream.client import UserStream

    cfg, _signer, bootstrap = _inputs()
    ws = FakeWebSocket()
    made = []
    read_started = asyncio.Event()
    never_release = asyncio.Event()

    def gateway_factory(config, *, paper):
        gateway = FakeGateway(config, paper=paper)
        gateway.read_started = read_started
        gateway.read_release = never_release
        made.append(gateway)
        return gateway

    def stream_factory(*args, **kwargs):
        stream = UserStream(*args, **kwargs)
        made[0].stream = stream
        return stream

    monkeypatch.setattr(
        "polymaker.userstream.client.websockets.connect",
        lambda url, **kwargs: FakeConnect(ws, url, kwargs),
    )
    task = asyncio.create_task(observe_rdp_account_and_user_channel(
        cfg, bootstrap, gateway_factory=gateway_factory,
        user_stream_factory=stream_factory,
    ))
    await asyncio.wait_for(read_started.wait(), timeout=1)
    await ws.close()
    with pytest.raises(RDPUserChannelError) as caught:
        await task
    assert caught.value.category == "USER_STREAM_DISCONNECTED"
    assert made[0].closed and not ws.active
    assert made[0].calls == [("connect", True), ("open_orders", True, True)]


@pytest.mark.asyncio
async def test_repeated_cancellation_drains_stream_gateway_and_store(monkeypatch):
    from polymaker.userstream.client import UserStream

    cfg, _signer, bootstrap = _inputs()
    ws = FakeWebSocket()
    made = []
    read_started = asyncio.Event()
    never_release = asyncio.Event()
    store = TrackingStore(":memory:")

    def gateway_factory(config, *, paper):
        gateway = FakeGateway(config, paper=paper)
        gateway.read_started = read_started
        gateway.read_release = never_release
        made.append(gateway)
        return gateway

    def stream_factory(*args, **kwargs):
        stream = UserStream(*args, **kwargs)
        made[0].stream = stream
        return stream

    monkeypatch.setattr(
        "polymaker.userstream.client.websockets.connect",
        lambda url, **kwargs: FakeConnect(ws, url, kwargs),
    )
    task = asyncio.create_task(observe_rdp_account_and_user_channel(
        cfg, bootstrap, gateway_factory=gateway_factory, store_factory=lambda _p: store,
        user_stream_factory=stream_factory,
    ))
    await asyncio.wait_for(read_started.wait(), timeout=1)
    task.cancel()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)
    assert made[0].closed and store.closed and not ws.active


@pytest.mark.asyncio
async def test_cancellation_during_success_cleanup_propagates_after_all_drains():
    cfg, _signer, bootstrap = _inputs()
    gateway_box = []
    stream_box = []
    stream_created = asyncio.Event()
    store = TrackingStore(":memory:")
    gateway_close_started = asyncio.Event()
    gateway_close_release = asyncio.Event()

    def gateway_factory(config, *, paper):
        gateway = FakeGateway(config, paper=paper)
        gateway.close_started = gateway_close_started
        gateway.close_release = gateway_close_release
        gateway_box.append(gateway)
        return gateway

    def stream_factory(*_args, **_kwargs):
        stream = CleanupBlockingStream()
        stream_box.append(stream)
        gateway_box[0].stream = stream
        stream_created.set()
        return stream

    task = asyncio.create_task(observe_rdp_account_and_user_channel(
        cfg, bootstrap, gateway_factory=gateway_factory,
        user_stream_factory=stream_factory, store_factory=lambda _p: store,
    ))
    stream = None
    async with asyncio.timeout(1):
        await stream_created.wait()
        stream = stream_box[0]
        await stream.started.wait()
        await stream.draining.wait()

    # First cancellation arrives after successful capture, while stream task
    # draining is already underway; repeat it before releasing that drain.
    task.cancel()
    task.cancel()
    assert not task.done()
    stream.drain_release.set()
    await asyncio.wait_for(gateway_close_started.wait(), timeout=1)

    # A further cancellation during gateway aclose must also be remembered.
    task.cancel()
    assert not task.done()
    gateway_close_release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)
    assert stream.connected is False
    assert gateway_box[0].closed
    assert store.closed


@pytest.mark.asyncio
async def test_market_expiry_during_account_capture_fails_closed(monkeypatch):
    from polymaker.userstream.client import UserStream

    cfg, _signer, bootstrap = _inputs()
    ws = FakeWebSocket()
    made = []
    clock = [time.time()]

    def gateway_factory(config, *, paper):
        gateway = FakeGateway(config, paper=paper)
        gateway.on_open_orders = lambda: clock.__setitem__(0, bootstrap.end_epoch)
        made.append(gateway)
        return gateway

    def stream_factory(*args, **kwargs):
        stream = UserStream(*args, **kwargs)
        made[0].stream = stream
        return stream

    monkeypatch.setattr(
        "polymaker.userstream.client.websockets.connect",
        lambda url, **kwargs: FakeConnect(ws, url, kwargs),
    )
    with pytest.raises(RDPUserChannelError) as caught:
        await observe_rdp_account_and_user_channel(
            cfg, bootstrap, now=lambda: clock[0], gateway_factory=gateway_factory,
            user_stream_factory=stream_factory,
        )
    assert caught.value.category == "MARKET_INTERVAL_ELAPSED"
    assert made[0].closed and not ws.active


@pytest.mark.asyncio
async def test_expired_market_is_rejected_before_gateway_construction():
    cfg, _signer, bootstrap = _inputs()
    expired = RDPBootstrap(bootstrap.market, bootstrap.epoch, 1,
                           bootstrap.token_metadata, bootstrap.receipt_ts)
    constructed = []

    def gateway_factory(*_args, **_kwargs):
        constructed.append(True)
        raise AssertionError("expired market constructed a gateway")

    with pytest.raises(RDPUserChannelError) as caught:
        await observe_rdp_account_and_user_channel(
            cfg, expired, now=lambda: 2, gateway_factory=gateway_factory,
        )
    assert caught.value.category == "MARKET_INTERVAL_ELAPSED"
    assert not constructed


@pytest.mark.asyncio
async def test_public_path_does_not_construct_authenticated_user_channel(monkeypatch):
    from polymaker.rdp_live import preflight_rdp_market

    cfg, _signer, bootstrap = _inputs()
    feed = SimpleNamespace(condition_id=CONDITION, connection_generation=1,
                           receipt_ts=time.time(), tokens=())

    async def bootstrap_fn(*_args, **_kwargs):
        return bootstrap

    async def feed_fn(*_args, **_kwargs):
        return feed

    def forbidden(*_args, **_kwargs):
        raise AssertionError("public-only preflight entered authenticated path")

    monkeypatch.setattr("polymaker.rdp_live.bootstrap_rdp_market", bootstrap_fn)
    monkeypatch.setattr("polymaker.rdp_marketdata.await_rdp_feed", feed_fn)
    monkeypatch.setattr("polymaker.rdp_userstream.observe_rdp_account_and_user_channel", forbidden)
    result = await preflight_rdp_market(cfg, bootstrap.market.slug, account_check=False)
    assert result.account is None and result.user_channel is None


@pytest.mark.asyncio
async def test_default_userstream_mode_sends_no_application_heartbeat(monkeypatch):
    from polymaker.state.store import StateStore
    from polymaker.state.tracker import UserEventProcessor
    from polymaker.userstream.client import UserStream

    ws = FakeWebSocket()
    store = StateStore(":memory:")
    stream = UserStream(
        SimpleNamespace(api_key="key", api_secret="secret", api_passphrase="passphrase"),
        "wallet", UserEventProcessor(store), other_token=lambda _token: None,
        condition_of_token=lambda _token: None,
    )
    stream.set_markets([CONDITION])
    connect_options = []
    monkeypatch.setattr(
        "polymaker.userstream.client.websockets.connect",
        lambda url, **kwargs: (connect_options.append(kwargs) or FakeConnect(ws, url, kwargs)),
    )
    task = asyncio.create_task(stream._connect_and_listen())
    try:
        for _ in range(100):
            if ws.sent:
                break
            await asyncio.sleep(.001)
        assert len(ws.sent) == 1
        assert json.loads(ws.sent[0])["markets"] == [CONDITION]
        assert stream._heartbeat_task is None
        assert stream.connected
        assert "logger" not in connect_options[0]
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        store.close()
    assert not ws.active


@pytest.mark.asyncio
async def test_rdp_protocol_logger_redacts_installed_frame_and_exception_logs(monkeypatch, caplog):
    from websockets.asyncio.client import ClientConnection
    from websockets.client import ClientProtocol
    from websockets.frames import Frame, Opcode
    from websockets.protocol import State
    from websockets.uri import parse_uri

    from polymaker.state.store import StateStore
    from polymaker.state.tracker import UserEventProcessor
    from polymaker.userstream.client import UserStream

    ws = FakeWebSocket()
    store = StateStore(":memory:")
    stream = UserStream(
        SimpleNamespace(api_key="sentinel-api-key", api_secret="sentinel-secret",
                        api_passphrase="sentinel-passphrase"),
        "wallet", UserEventProcessor(store), other_token=lambda _token: None,
        condition_of_token=lambda _token: None, application_heartbeat=True,
        sanitize_errors=True,
    )
    stream.set_markets([CONDITION])
    connect_options = []

    def connect(url, **kwargs):
        connect_options.append(kwargs)
        return FakeConnect(ws, url, kwargs)

    monkeypatch.setattr("polymaker.userstream.client.websockets.connect", connect)
    caplog.set_level("DEBUG", logger="polymaker.userstream.rdp_protocol")
    task = asyncio.create_task(stream.run_once())
    try:
        async with asyncio.timeout(.5):
            await ws.sent_event.wait()
        protocol_logger = connect_options[0]["logger"]
        protocol = ClientProtocol(
            parse_uri("wss://ws-subscriptions-clob.polymarket.com/ws/user"),
            state=State.OPEN, logger=protocol_logger,
        )
        connection = ClientConnection(protocol)
        def fail_protocol(_frame):
            raise RuntimeError("sentinel-protocol-exception")

        protocol.recv_frame = fail_protocol
        wire_secret = b'{"apiKey":"sentinel-api-key","secret":"sentinel-secret"}'
        protocol.send_text(wire_secret)
        protocol.receive_data(Frame(Opcode.TEXT, wire_secret).serialize(mask=False))
        try:
            raise RuntimeError("sentinel-connection-error")
        except RuntimeError:
            connection.logger.exception("sentinel-connection-error")
        assert protocol.parser_exc is not None
        assert "user_ws_protocol_frame_redacted" in caplog.text
        assert "user_ws_protocol_error_redacted" in caplog.text
        errors = [record for record in caplog.records
                  if record.msg == "user_ws_protocol_error_redacted"]
        assert errors and errors[-1].exception_type == "RuntimeError"
        assert "sentinel" not in caplog.text
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        store.close()
