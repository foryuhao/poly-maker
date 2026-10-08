from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import polymaker.rdp_runtime as runtime_module
from polymaker.domain import MarketMeta, TokenMeta
from polymaker.rdp_live import RDPBootstrap, TokenBookMetadata
from polymaker.rdp_marketdata import (
    RDPFeedError,
    RDPMarketDataService,
    RDPMarketDataSession,
    await_rdp_feed,
)
from polymaker.rdp_runtime import RDPInputSession
from polymaker.risk.rdp_capital import RDPInitialBackingResult


def bootstrap(*, end_in: float = 60) -> RDPBootstrap:
    market = MarketMeta(
        condition_id="condition-selected", question="BTC Up or Down", slug="btc-updown-5m-2000000100",
        tokens=(TokenMeta("up", "Up"), TokenMeta("down", "Down")), tick_size=0.01,
        neg_risk=False, min_order_size=5, rewards_min_size=0, rewards_max_spread=0,
        rewards_daily_rate=0, maker_fee_bps=0, taker_fee_bps=0, fees_enabled=False,
        end_date_iso=datetime.fromtimestamp(time.time() + end_in, UTC).isoformat(), event_id=None,
    )
    return RDPBootstrap(
        market, int(time.time()), int(time.time() + end_in),
        (TokenBookMetadata("Up", "up", .01, 5, False),
         TokenBookMetadata("Down", "down", .02, 5, False)), time.time(),
    )


def snapshot(token: str, *, bids=None, asks=None, market="condition-selected", tick="__MISSING__"):
    msg = {
        "event_type": "book", "market": market, "asset_id": token,
        "bids": bids if bids is not None else [{"price": "0.4", "size": "3"}],
        "asks": asks if asks is not None else [{"price": "0.6", "size": "4"}],
        "timestamp": "2000000101",
    }
    if tick != "__MISSING__":
        msg["tick_size"] = tick
    return msg


class FakeWS:
    def __init__(self, frames):
        self.frames = list(frames)
        self.sent = []
        self.hold = asyncio.Event()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def send(self, value):
        self.sent.append(json.loads(value))

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.frames:
            return json.dumps(self.frames.pop(0))
        await self.hold.wait()
        raise StopAsyncIteration


class QueuedFakeWS(FakeWS):
    """A fake socket whose frames can be released at controlled receipt times."""

    def __init__(self):
        super().__init__([])
        self.incoming = asyncio.Queue()

    async def __anext__(self):
        frame = await self.incoming.get()
        if frame is None:
            raise StopAsyncIteration
        return json.dumps(frame)


async def _queued_service(monkeypatch, service=None):
    from polymaker.marketdata import service as marketdata_service

    clock = [1.0]
    socket = QueuedFakeWS()
    receipts = asyncio.Queue()
    monkeypatch.setattr(marketdata_service, "time",
                        SimpleNamespace(monotonic=lambda: clock[0], time=time.time))
    monkeypatch.setattr(marketdata_service.websockets, "connect", lambda *_args, **_kwargs: socket)
    svc = service or RDPMarketDataService(bootstrap(), single_attempt=True)
    svc._test_receipts = receipts
    svc._test_queue_ready = asyncio.Event()
    original = svc._process_raw
    svc._test_processed = 0
    svc._test_processed_event = asyncio.Event()

    async def capture(raw):
        await original(raw)
        svc._test_processed += 1
        svc._test_processed_event.set()

    svc._process_raw = capture
    consume = svc._consume

    async def capture_queue(queue, generation):
        put_nowait = queue.put_nowait

        def acknowledged_put(item):
            put_nowait(item)
            receipts.put_nowait(item)

        queue.put_nowait = acknowledged_put
        svc._test_queue_ready.set()
        await consume(queue, generation)

    svc._consume = capture_queue
    task = asyncio.create_task(svc.run())
    await svc._test_queue_ready.wait()
    await svc._changed.wait()
    assert svc.connected

    async def wait_processed(target):
        while svc._test_processed < target:
            await svc._test_processed_event.wait()
            svc._test_processed_event.clear()

    async def send(frame, at):
        target = svc._test_processed + 1
        clock[0] = at
        await socket.incoming.put(frame)
        receipt = await receipts.get()
        assert receipt[1] == at
        await wait_processed(target)

    return svc, socket, task, clock, send, wait_processed


def install_ws(monkeypatch, frames, sessions=None):
    from polymaker.marketdata import service

    created = []

    def connect(url, **kwargs):
        assert url == "wss://ws-subscriptions-clob.polymarket.com/ws/market"
        assert kwargs["open_timeout"] == 10
        groups = sessions if sessions is not None else [frames]
        ws = FakeWS(groups[min(len(created), len(groups) - 1)])
        created.append(ws)
        return ws

    monkeypatch.setattr(service.websockets, "connect", connect)
    return created


@pytest.mark.asyncio
async def test_readiness_subscribes_exact_tokens_and_captures_same_generation(monkeypatch):
    ws_frames = [snapshot("up", tick="0.005"), snapshot("down")]
    sessions = install_ws(monkeypatch, ws_frames)
    result = await await_rdp_feed(bootstrap(), timeout_s=2)
    assert sessions[0].sent == [{"assets_ids": ["up", "down"], "type": "market"}]
    assert result.condition_id == "condition-selected"
    assert result.connection_generation == 1
    assert [row.view.best_bid for row in result.tokens] == [.4, .4]
    assert [row.tick_source for row in result.tokens] == ["WS_BOOK_SNAPSHOT", "REST_BOOTSTRAP"]
    assert result.tokens[0].tick_size == .005


@pytest.mark.asyncio
async def test_rdp_single_attempt_transport_does_not_restart_after_disconnect(monkeypatch):
    service = RDPMarketDataService(bootstrap(), single_attempt=True)
    calls = []

    async def disconnected():
        calls.append("connection-ended")

    monkeypatch.setattr(service, "_connect_and_listen", disconnected)
    await service.run()
    assert calls == ["connection-ended"]
    assert service.single_attempt is True
    assert RDPMarketDataService(bootstrap()).single_attempt is False


@pytest.mark.asyncio
@pytest.mark.parametrize("frames", [
    [], [snapshot("up")],
    [snapshot("up", bids=[], asks=[]), snapshot("down")],
    [snapshot("up", bids=[{"price": "0.7", "size": "1"}], asks=[{"price": "0.6", "size": "1"}]), snapshot("down")],
    [snapshot("other"), snapshot("down")],
])
async def test_connection_alone_one_empty_crossed_or_foreign_books_timeout(monkeypatch, frames):
    sessions = install_ws(monkeypatch, frames)
    with pytest.raises(RDPFeedError) as caught:
        await await_rdp_feed(bootstrap(), timeout_s=.03)
    assert caught.value.category == "FEED_STARTUP_TIMEOUT"
    assert sessions[0].sent == [{"assets_ids": ["up", "down"], "type": "market"}]


@pytest.mark.asyncio
async def test_websocket_connection_failure_times_out_and_cleans_up(monkeypatch):
    from polymaker.marketdata import service as marketdata_service

    def fail_connect(*_args, **_kwargs):
        raise OSError("fixture connection failure")

    monkeypatch.setattr(marketdata_service.websockets, "connect", fail_connect)
    with pytest.raises(RDPFeedError) as caught:
        await await_rdp_feed(bootstrap(), timeout_s=.03)
    assert caught.value.category == "FEED_STARTUP_TIMEOUT"
    assert not any(task.get_name() == "rdp-market-ws"
                   for task in asyncio.all_tasks() if task is not asyncio.current_task())


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_frame", [
    snapshot("up", market="wrong"),
    snapshot("up", bids=[{"price": True, "size": "1"}]),
    snapshot("up", asks=[{"price": "NaN", "size": "1"}]),
    snapshot("up", bids=[{"price": "0.4", "size": -1}]),
    snapshot("up", tick="Infinity"),
    {"event_type": "tick_size_change", "market": "condition-selected", "asset_id": "up", "new_tick_size": "1"},
])
async def test_malformed_selected_frames_fail_closed(monkeypatch, bad_frame):
    service = RDPMarketDataService(bootstrap())
    service.connected = True
    service.connection_generation = 1
    before = service.view("up")
    with pytest.raises(RDPFeedError):
        await service._process_raw(json.dumps(bad_frame))
    assert service.view("up") == before
    assert service._fatal is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("literal", ["0.4000000000000000001", "3.0000000000000000001"])
async def test_unrepresentable_json_numeric_literal_rejected_before_mutation(literal):
    service = RDPMarketDataService(bootstrap())
    service.connected = True
    service.connection_generation = 1
    field = "price" if literal.startswith("0.") else "size"
    bid = (f'{{"price":{literal},"size":"3"}}' if field == "price"
           else f'{{"price":"0.4","size":{literal}}}')
    raw = (f'{{"event_type":"book","market":"condition-selected","asset_id":"up",'
           f'"bids":[{bid}],"asks":[{{"price":"0.6","size":"4"}}]}}')
    with pytest.raises(RDPFeedError):
        await service._process_raw(raw)
    assert not service.book("up").bids and not service.book("up").asks


@pytest.mark.asyncio
async def test_unrepresentable_later_numeric_frame_is_atomic_across_batch():
    service = RDPMarketDataService(bootstrap())
    service.connected = True
    service.connection_generation = 1
    raw = ('[{"event_type":"price_change","market":"condition-selected","timestamp":"1",'
           '"price_changes":[{"asset_id":"up","side":"BUY","price":"0.5","size":"2"}]},'
           '{"event_type":"book","market":"condition-selected","asset_id":"down",'
           '"bids":[{"price":0.4000000000000000001,"size":"3"}],'
           '"asks":[{"price":"0.6","size":"4"}]}]')
    with pytest.raises(RDPFeedError):
        await service._process_raw(raw)
    assert not service.book("up").bids and not service.book("down").bids


@pytest.mark.asyncio
async def test_absent_snapshot_tick_is_allowed_but_explicit_null_is_rejected():
    service = RDPMarketDataService(bootstrap())
    service.connected = True
    service.connection_generation = 1
    await service._process_raw(json.dumps(snapshot("up")))
    assert service.book("up").tick_size == .01
    assert service._tick_source["up"] == "REST_BOOTSTRAP"
    fresh = RDPMarketDataService(bootstrap())
    fresh.connected = True
    fresh.connection_generation = 1
    with pytest.raises(RDPFeedError):
        await fresh._process_raw('{"event_type":"book","market":"condition-selected",'
                                 '"asset_id":"up","bids":[{"price":"0.4","size":"3"}],'
                                 '"asks":[{"price":"0.6","size":"4"}],"tick_size":null}')
    assert not fresh.book("up").bids and not fresh.book("up").asks


@pytest.mark.asyncio
async def test_invalid_later_delta_entry_cannot_partially_mutate_selected_book():
    service = RDPMarketDataService(bootstrap())
    service.connected = True
    service.connection_generation = 1
    frame = {"event_type": "price_change", "market": "condition-selected", "timestamp": "1",
             "price_changes": [
                 {"asset_id": "up", "side": "BUY", "price": "0.5", "size": "2"},
                 {"asset_id": "up", "side": "INVALID", "price": "0.4", "size": "1"},
             ]}
    with pytest.raises(RDPFeedError):
        await service._process_raw(json.dumps(frame))
    assert service.book("up").bids == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", [
    {"asset_id": "up", "side": "BUY", "price": True, "size": "1"},
    {"asset_id": "up", "side": "BUY", "price": "Infinity", "size": "1"},
    {"asset_id": "up", "side": "SELL", "price": "0.5", "size": -1},
    {"asset_id": "up", "side": "OTHER", "price": "0.5", "size": "1"},
])
async def test_invalid_selected_delta_values_reject_before_mutation(entry):
    service = RDPMarketDataService(bootstrap())
    service.connected = True
    service.connection_generation = 1
    frame = {"event_type": "price_change", "market": "condition-selected", "timestamp": "1",
             "price_changes": [entry]}
    with pytest.raises(RDPFeedError):
        await service._process_raw(json.dumps(frame))
    assert not service.book("up").bids and not service.book("up").asks


@pytest.mark.asyncio
async def test_reconnect_requires_both_books_from_new_live_generation(monkeypatch):
    from polymaker.marketdata import service as marketdata_service

    class DisconnectingWS(FakeWS):
        def __init__(self, frames, disconnect=False):
            super().__init__(frames)
            if disconnect:
                self.hold.set()

        async def __anext__(self):
            if self.frames:
                return json.dumps(self.frames.pop(0))
            await asyncio.sleep(.03)
            raise StopAsyncIteration

    sockets = []

    def connect(*_args, **_kwargs):
        index = len(sockets)
        frames = [snapshot("up")] if index == 0 else [snapshot("up"), snapshot("down")]
        ws = DisconnectingWS(frames, disconnect=index == 0)
        sockets.append(ws)
        return ws

    monkeypatch.setattr(marketdata_service.websockets, "connect", connect)
    result = await await_rdp_feed(bootstrap(), timeout_s=4)
    assert result.connection_generation == 2
    assert len(sockets) == 2
    assert all(socket.sent == [{"assets_ids": ["up", "down"], "type": "market"}]
               for socket in sockets)
    assert not any(task.get_name() in {"rdp-market-ws", "market-ws-consumer"}
                   for task in asyncio.all_tasks() if task is not asyncio.current_task())


@pytest.mark.asyncio
async def test_old_first_generation_snapshot_cannot_pair_with_new_second_snapshot(monkeypatch):
    service = RDPMarketDataService(bootstrap())
    service.connected = True
    service.connection_generation = 1
    await service._process_raw(json.dumps(snapshot("up")))
    service.connected = False
    service.connection_generation = 2
    service.connected = True
    await service._process_raw(json.dumps(snapshot("down")))
    assert not service._ready()


@pytest.mark.asyncio
async def test_tick_change_wakes_observer_and_capture_is_immutable(monkeypatch):
    service = RDPMarketDataService(bootstrap())
    service.connected = True
    service.connection_generation = 1
    service._changed.clear()
    await service._process_raw(json.dumps([snapshot("up"), snapshot("down")]))
    captured = service._capture()
    original_bid = captured.tokens[0].view.best_bid
    await service._process_raw(json.dumps({"event_type": "tick_size_change", "market": "condition-selected",
                                           "asset_id": "up", "new_tick_size": "0.005"}))
    await service._process_raw(json.dumps({"event_type": "price_change", "market": "condition-selected",
                                           "timestamp": "2", "price_changes": [
                                               {"asset_id": "up", "side": "BUY", "price": "0.5", "size": "7"},
                                           ]}))
    assert service._changed.is_set()
    assert service.book("up").tick_size == .005
    assert service.book("up").best_bid().price == .5
    assert service._tick_source["up"] == "WS_TICK_SIZE_CHANGE"
    assert captured.tokens[0].view.best_bid == original_bid
    with pytest.raises((AttributeError, TypeError)):
        captured.tokens[0].tick_size = .5


@pytest.mark.asyncio
async def test_market_expiry_and_timeout_are_categorized_without_leaked_task(monkeypatch):
    sessions = install_ws(monkeypatch, [])
    with pytest.raises(RDPFeedError) as caught:
        await await_rdp_feed(bootstrap(end_in=-1), timeout_s=1)
    assert caught.value.category == "MARKET_INTERVAL_ELAPSED"
    assert sessions == []


@pytest.mark.asyncio
async def test_caller_cancellation_stops_owned_service_task(monkeypatch):
    sessions = install_ws(monkeypatch, [])
    task = asyncio.create_task(await_rdp_feed(bootstrap(), timeout_s=10))
    await asyncio.sleep(.02)
    assert sessions
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sessions[0].sent == [{"assets_ids": ["up", "down"], "type": "market"}]
    assert not any(task.get_name() in {"rdp-market-ws", "market-ws-consumer"}
                   for task in asyncio.all_tasks() if task is not asyncio.current_task())


@pytest.mark.asyncio
async def test_selected_frame_failure_closes_owned_service_task(monkeypatch):
    sessions = install_ws(monkeypatch, [snapshot("up", market="wrong")])
    with pytest.raises(RDPFeedError) as caught:
        await await_rdp_feed(bootstrap(), timeout_s=2)
    assert caught.value.category == "SELECTED_FRAME_IDENTITY_MISMATCH"
    assert sessions[0].sent == [{"assets_ids": ["up", "down"], "type": "market"}]
    assert not any(task.get_name() in {"rdp-market-ws", "market-ws-consumer"}
                   for task in asyncio.all_tasks() if task is not asyncio.current_task())


@pytest.mark.asyncio
async def test_socket_queue_receipt_drives_existing_filter_and_preserves_aggregate_variation(monkeypatch):
    svc, socket, task, _clock, send, _wait_processed = await _queued_service(monkeypatch)
    await send([snapshot("up"), snapshot("down")], 1.0)
    await send({"event_type": "price_change", "market": "condition-selected", "timestamp": "2",
                "price_changes": [{"asset_id": "up", "side": "BUY", "price": "0.4", "size": "9"}]}, 1.25)
    await send({"event_type": "price_change", "market": "condition-selected", "timestamp": "2",
                "price_changes": [{"asset_id": "up", "side": "BUY", "price": "0.4", "size": "3"}]}, 1.5)
    await send([snapshot("up", bids=[{"price": "0.4", "size": "3"}]), snapshot("down")], 2.0)
    warming = svc.book_filter.evaluate(2.9, generation=1,
        ticks={"up": svc.book("up").tick_size, "down": svc.book("down").tick_size},
        state_reliable=True)
    assert not warming.ready and "NOT_READY:WARMUP" in warming.reasons
    result = svc.book_filter.evaluate(3.0, generation=1,
        ticks={"up": svc.book("up").tick_size, "down": svc.book("down").tick_size},
        state_reliable=True)
    assert result.ready
    assert result.sides[0].depth_change_2s == 12
    assert svc._processing_receipt_monotonic is None
    await socket.incoming.put(None)
    await task
    assert svc._fatal is None


@pytest.mark.asyncio
@pytest.mark.parametrize(("delta_size", "expected", "inactive"), [
    ("3", 0, True), ("8", 5, False),
])
async def test_socket_queue_unchanged_or_exact_five_variation(monkeypatch, delta_size, expected, inactive):
    svc, socket, task, _clock, send, _wait_processed = await _queued_service(monkeypatch)
    await send([snapshot("up"), snapshot("down")], 1.0)
    await send({"event_type": "price_change", "market": "condition-selected", "timestamp": "2",
                "price_changes": [{"asset_id": "up", "side": "BUY", "price": "0.4",
                                    "size": delta_size}]}, 1.5)
    await send([snapshot("up", bids=[{"price": "0.4", "size": delta_size}]), snapshot("down")], 2.0)
    result = svc.book_filter.evaluate(3.0, generation=1,
        ticks={"up": svc.book("up").tick_size, "down": svc.book("down").tick_size},
        state_reliable=True)
    assert result.ready
    assert result.sides[0].depth_change_2s == expected
    assert result.sides[0].inactive is inactive
    await socket.incoming.put(None)
    await task


@pytest.mark.asyncio
async def test_one_aggregated_price_change_message_exposes_only_final_callback_state(monkeypatch):
    svc, socket, task, _clock, send, _wait_processed = await _queued_service(monkeypatch)
    await send([snapshot("up"), snapshot("down")], 1.0)
    await send({"event_type": "price_change", "market": "condition-selected", "timestamp": "2",
                "price_changes": [
                    {"asset_id": "up", "side": "BUY", "price": "0.4", "size": "9"},
                    {"asset_id": "up", "side": "BUY", "price": "0.4", "size": "3"},
                ]}, 1.5)
    await send([snapshot("up"), snapshot("down")], 3.0)
    result = svc.book_filter.evaluate(3.1, generation=1,
        ticks={"up": svc.book("up").tick_size, "down": svc.book("down").tick_size},
        state_reliable=True)
    assert result.ready
    assert result.sides[0].depth_change_2s == 0
    await socket.incoming.put(None)
    await task


@pytest.mark.asyncio
async def test_reversing_messages_in_one_frame_keep_each_callback_state(monkeypatch):
    svc, socket, task, _clock, send, _wait_processed = await _queued_service(monkeypatch)
    await send([snapshot("up"), snapshot("down")], 1.0)
    await send([
        {"event_type": "price_change", "market": "condition-selected", "timestamp": "2",
         "price_changes": [{"asset_id": "up", "side": "BUY", "price": "0.4", "size": "9"}]},
        {"event_type": "price_change", "market": "condition-selected", "timestamp": "2",
         "price_changes": [{"asset_id": "up", "side": "BUY", "price": "0.4", "size": "3"}]},
    ], 1.5)
    await send([snapshot("up"), snapshot("down")], 3.0)
    result = svc.book_filter.evaluate(3.1, generation=1,
        ticks={"up": svc.book("up").tick_size, "down": svc.book("down").tick_size},
        state_reliable=True)
    assert result.ready
    assert result.sides[0].depth_change_2s == 12
    await socket.incoming.put(None)
    await task


@pytest.mark.asyncio
@pytest.mark.parametrize("condition", ["depth", "fast_move"])
async def test_depth_extreme_and_fast_move_reach_filter_through_socket_queue(monkeypatch, condition):
    svc, socket, task, _clock, send, _wait_processed = await _queued_service(monkeypatch)
    up_size = "60" if condition == "depth" else "3"
    await send([
        snapshot("up", bids=[{"price": "0.4", "size": up_size}]),
        snapshot("down", bids=[{"price": "0.4", "size": "3"}]),
    ], 1.0)
    if condition == "fast_move":
        await send([snapshot("up"), snapshot("down")], 2.0)
        await send([snapshot("up", bids=[{"price": "0.42", "size": "3"}]), snapshot("down")], 2.5)
    else:
        await send([snapshot("up", bids=[{"price": "0.4", "size": "60"}]), snapshot("down")], 2.0)
    result = svc.book_filter.evaluate(3.1, generation=1,
        ticks={"up": svc.book("up").tick_size, "down": svc.book("down").tick_size},
        state_reliable=True)
    assert result.ready
    if condition == "depth":
        assert result.depth_extreme is True
    else:
        assert result.sides[0].fast_move is True
    await socket.incoming.put(None)
    await task


@pytest.mark.asyncio
async def test_queue_uses_socket_receipt_not_delayed_trade_processing_time(monkeypatch):
    svc, socket, task, clock, send, wait_processed = await _queued_service(monkeypatch)
    entered = asyncio.Event()
    release = asyncio.Event()
    original = svc._process_raw
    blocked = True

    async def block_first(raw):
        nonlocal blocked
        if blocked:
            blocked = False
            entered.set()
            await release.wait()
        await original(raw)

    svc._process_raw = block_first
    clock[0] = 0.5
    await socket.incoming.put([snapshot("up"), snapshot("down")])
    first_receipt = await svc._test_receipts.get()
    assert first_receipt[1] == .5
    await entered.wait()
    clock[0] = 1.0
    await socket.incoming.put({"event_type": "last_trade_price", "market": "condition-selected",
                               "asset_id": "up", "price": "0.4", "size": "1",
                               "side": "BUY", "timestamp": "2"})
    trade_receipt = await svc._test_receipts.get()
    assert trade_receipt[1] == 1.0
    clock[0] = 7.1
    await socket.incoming.put([snapshot("up"), snapshot("down")])
    anchor_receipt = await svc._test_receipts.get()
    assert anchor_receipt[1] == 7.1
    clock[0] = 7.2
    await socket.incoming.put([snapshot("up"), snapshot("down")])
    latest_receipt = await svc._test_receipts.get()
    assert latest_receipt[1] == 7.2
    clock[0] = 9.0
    release.set()
    await wait_processed(4)
    result = svc.book_filter.evaluate(9.1, generation=1,
        ticks={"up": svc.book("up").tick_size, "down": svc.book("down").tick_size},
        state_reliable=True)
    assert result.ready
    assert result.sides[0].real_trade_in_2s is False
    assert svc._processing_receipt_monotonic is None
    await socket.incoming.put(None)
    await task


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_receipt", [None, True, float("nan")])
async def test_invalid_receipt_context_invalidates_warmed_filter_without_fallback(monkeypatch, invalid_receipt):
    svc, socket, task, _clock, send, _wait_processed = await _queued_service(monkeypatch)
    await send([snapshot("up"), snapshot("down")], 1.0)
    await send([snapshot("up"), snapshot("down")], 1.1)
    before = svc.book_filter.evaluate(3.1, generation=1,
        ticks={"up": svc.book("up").tick_size, "down": svc.book("down").tick_size},
        state_reliable=True)
    assert before.ready
    svc._processing_receipt_monotonic = invalid_receipt
    await svc._process_raw(json.dumps(snapshot("up")))
    svc._processing_receipt_monotonic = None
    after = svc.book_filter.evaluate(3.2, generation=1,
        ticks={"up": svc.book("up").tick_size, "down": svc.book("down").tick_size},
        state_reliable=True)
    assert not after.ready and after.rejected
    assert "NOT_READY:INVALID_BOOK_EVIDENCE" in after.reasons
    await socket.incoming.put(None)
    await task


@pytest.mark.asyncio
@pytest.mark.parametrize("trade", [
    {"event_type": "last_trade_price", "market": "condition-selected", "asset_id": "up",
     "price": "0.4", "size": "1", "timestamp": "1"},
    {"event_type": "last_trade_price", "market": "condition-selected", "asset_id": "up",
     "price": "0.4", "size": "1", "side": True, "timestamp": "1"},
    {"event_type": "last_trade_price", "market": "condition-selected", "asset_id": "up",
     "price": True, "size": "1", "side": "BUY", "timestamp": "1"},
    {"event_type": "last_trade_price", "market": "condition-selected", "asset_id": "up",
     "price": "NaN", "size": "1", "side": "BUY", "timestamp": "1"},
    {"event_type": "last_trade_price", "market": "condition-selected", "asset_id": "up",
     "price": "0.4", "size": "0", "side": "SELL", "timestamp": "1"},
    {"event_type": "last_trade_price", "market": "condition-selected", "asset_id": "up",
     "price": "0.4", "size": -1, "side": "BUY", "timestamp": "1"},
    {"event_type": "last_trade_price", "market": "condition-selected", "asset_id": "up",
     "price": "0.4", "size": "Infinity", "side": "BUY", "timestamp": "1"},
    {"event_type": "last_trade_price", "market": "condition-selected", "asset_id": "up",
     "price": "0.4000000000000000001", "size": "1", "side": "BUY", "timestamp": "1"},
    {"event_type": "last_trade_price", "market": "wrong", "asset_id": "up",
     "price": "0.4", "size": "1", "side": "BUY", "timestamp": "1"},
    {"event_type": "last_trade_price", "market": "condition-selected", "price": "0.4",
     "size": "1", "side": "BUY", "timestamp": "1"},
])
async def test_invalid_selected_trade_rejects_whole_frame_before_book_or_filter_mutation(trade):
    svc = RDPMarketDataService(bootstrap())
    svc.connected = True
    svc.connection_generation = 1
    before = svc.view("up")
    raw = json.dumps([snapshot("up"), trade])
    with pytest.raises(RDPFeedError):
        await svc._process_raw(raw)
    assert svc.view("up") == before
    assert svc._fatal is not None
    assert not svc.book_filter._history["up"].samples


@pytest.mark.asyncio
async def test_unrepresentable_json_trade_number_rejected_before_frame_mutation():
    svc = RDPMarketDataService(bootstrap())
    svc.connected = True
    svc.connection_generation = 1
    raw = ('[{"event_type":"book","market":"condition-selected","asset_id":"up",'
           '"bids":[{"price":"0.4","size":"3"}],"asks":[{"price":"0.6","size":"4"}]},'
           '{"event_type":"last_trade_price","market":"condition-selected","asset_id":"up",'
           '"price":0.4000000000000000001,"size":1,"side":"BUY"}]')
    with pytest.raises(RDPFeedError) as caught:
        await svc._process_raw(raw)
    assert caught.value.category == "MALFORMED_SELECTED_TRADE"
    assert svc.book("up").bids == {}
    assert not svc.book_filter._history["up"].samples


@pytest.mark.asyncio
@pytest.mark.parametrize("selected_token", ["up", "down"])
async def test_selected_and_foreign_trades_observe_only_selected_outcome(monkeypatch, selected_token):
    svc, socket, task, _clock, send, _wait_processed = await _queued_service(monkeypatch)
    await send([snapshot("up"), snapshot("down")], 1.0)
    await send([
        {"event_type": "last_trade_price", "market": "condition-selected", "asset_id": selected_token,
         "price": "0.4", "size": "1", "side": "SELL", "timestamp": "2"},
        {"event_type": "last_trade_price", "market": "foreign", "asset_id": "foreign-token",
         "price": "0.4", "size": "1", "side": "BUY", "timestamp": "2"},
    ], 1.5)
    await send([snapshot("up"), snapshot("down")], 2.5)
    result = svc.book_filter.evaluate(3.1, generation=1,
        ticks={"up": svc.book("up").tick_size, "down": svc.book("down").tick_size},
        state_reliable=True)
    assert result.ready
    assert result.sides[0].real_trade_in_2s is (selected_token == "up")
    assert result.sides[1].real_trade_in_2s is (selected_token == "down")
    await socket.incoming.put(None)
    await task


@pytest.mark.asyncio
async def test_tick_change_in_production_queue_resets_filter_history(monkeypatch):
    svc, socket, task, _clock, send, _wait_processed = await _queued_service(monkeypatch)
    await send([snapshot("up"), snapshot("down")], 1.0)
    await send([{"event_type": "tick_size_change", "market": "condition-selected",
                 "asset_id": "up", "new_tick_size": "0.005"},
                snapshot("up", tick="0.005")], 3.1)
    result = svc.book_filter.evaluate(3.2, generation=1,
        ticks={"up": svc.book("up").tick_size, "down": svc.book("down").tick_size},
        state_reliable=True)
    assert not result.ready and result.rejected
    assert svc.book("up").tick_size == .005
    await socket.incoming.put(None)
    await task


@pytest.mark.asyncio
async def test_stale_generation_queue_item_cannot_observe_filter_evidence():
    svc = RDPMarketDataService(bootstrap())
    svc.connected = True
    svc.connection_generation = 2
    queue = asyncio.Queue()
    queue.put_nowait((1, 5.0, json.dumps([snapshot("up"), snapshot("down")])))
    consumer = asyncio.create_task(svc._consume(queue, 1))
    await queue.join()
    consumer.cancel()
    await asyncio.gather(consumer, return_exceptions=True)
    assert not svc.book_filter._history["up"].samples
    assert svc._processing_receipt_monotonic is None


@pytest.mark.asyncio
async def test_warmed_generation_one_history_and_old_backlog_cannot_certify_generation_two(monkeypatch):
    svc, socket, task, clock, send, _wait_processed = await _queued_service(monkeypatch)
    await send([snapshot("up"), snapshot("down")], 1.0)
    await send([snapshot("up"), snapshot("down")], 3.0)
    before = svc.book_filter.evaluate(3.1, generation=1,
        ticks={"up": svc.book("up").tick_size, "down": svc.book("down").tick_size},
        state_reliable=True)
    assert before.ready
    await socket.incoming.put(None)
    await task

    svc.connection_generation = 2
    svc.connected = True
    clock[0] = 3.2
    queue = asyncio.Queue()
    old_snapshot = snapshot("up", bids=[{"price": "0.5", "size": "10"}])
    queue.put_nowait((1, 3.05, json.dumps(old_snapshot)))
    queue.put_nowait((2, 3.2, json.dumps([snapshot("up"), snapshot("down")])))
    consumer = asyncio.create_task(svc._consume(queue, 2))
    await queue.join()
    consumer.cancel()
    await asyncio.gather(consumer, return_exceptions=True)
    after = svc.book_filter.evaluate(3.3, generation=2,
        ticks={"up": svc.book("up").tick_size, "down": svc.book("down").tick_size},
        state_reliable=True)
    assert not after.ready and after.rejected
    assert "NOT_READY:WARMUP" in after.reasons
    assert len(svc.book_filter._history["up"].samples) == 1
    assert svc.book("up").best_bid().price == .4


@pytest.mark.asyncio
async def test_queue_processing_error_clears_receipt_context():
    svc = RDPMarketDataService(bootstrap())
    svc.connected = True
    svc.connection_generation = 1
    queue = asyncio.Queue()
    bad = {"event_type": "last_trade_price", "market": "condition-selected", "asset_id": "up",
           "price": "0.4", "size": "0", "side": "BUY"}
    queue.put_nowait((1, 5.0, json.dumps(bad)))
    consumer = asyncio.create_task(svc._consume(queue, 1))
    await queue.join()
    result = await asyncio.gather(consumer, return_exceptions=True)
    assert isinstance(result[0], RDPFeedError)
    assert svc._processing_receipt_monotonic is None


@pytest.mark.asyncio
async def test_queued_frames_reach_retained_input_session_filter_result(monkeypatch):
    async def async_noop():
        return None

    selected = bootstrap()
    public = RDPMarketDataSession(selected, single_attempt=True)
    svc, socket, task, _clock, send, _wait_processed = await _queued_service(
        monkeypatch, service=public.service)
    public.task = task
    await send([snapshot("up"), snapshot("down")], 1.0)
    await send([snapshot("up"), snapshot("down")], 1.1)
    await send([snapshot("up"), snapshot("down")], 3.0)
    feed = svc._capture()
    user = SimpleNamespace(
        observation=SimpleNamespace(
            account=object(), user_channel=SimpleNamespace(connection_generation=1)),
        processor=object(),
        failure_category=lambda _generation: None,
        aclose=async_noop,
    )
    backing = RDPInitialBackingResult(True, None, 30, 0, 30, 30, 0, True)
    wall = time.time()
    session = RDPInputSession(
        selected, public_session=public, user_session=user, feed=feed,
        backing=backing, now=lambda: wall, sleep=asyncio.sleep,
        poll_interval_s=.1, started_at=wall,
    )
    monkeypatch.setattr(runtime_module, "time",
                        SimpleNamespace(monotonic=lambda: 3.1, time=time.time))
    result = session.book_filter_result()
    assert result.ready
    assert result.generation == 1
    await session.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("notify", [False, True])
async def test_owned_service_failure_after_populating_books_never_certifies(monkeypatch, notify):
    async def fail_after_populating(service):
        service.connected = True
        service.connection_generation = 1
        for token in service.token_ids:
            book = service.book(token)
            book.apply_snapshot([(0.4, 3)], [(0.6, 4)], 1)
            book.snapshot_generation = 1
            service._snapshot_seen[token] = 1
        if notify:
            service._changed.set()
        raise RuntimeError("controlled service failure")

    monkeypatch.setattr(RDPMarketDataService, "run", fail_after_populating)
    with pytest.raises(RDPFeedError) as caught:
        await await_rdp_feed(bootstrap(), timeout_s=1)
    assert caught.value.category == "FEED_SERVICE_FAILED"
    assert not any(task.get_name() == "rdp-market-ws"
                   for task in asyncio.all_tasks() if task is not asyncio.current_task())


@pytest.mark.asyncio
async def test_late_publication_after_monotonic_deadline_fails_and_cleans_up(monkeypatch):
    published = asyncio.Event()
    cleaned = asyncio.Event()

    async def publish_late(service):
        try:
            # Block the loop briefly to reproduce a late wake/publication after
            # the caller's monotonic deadline has already elapsed.
            time.sleep(.03)  # noqa: ASYNC251 - reproduce event-loop-blocked late publication
            service.connected = True
            service.connection_generation = 1
            for token in service.token_ids:
                book = service.book(token)
                book.apply_snapshot([(0.4, 3)], [(0.6, 4)], 1)
                book.snapshot_generation = 1
                service._snapshot_seen[token] = 1
            service._changed.set()
            published.set()
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    monkeypatch.setattr(RDPMarketDataService, "run", publish_late)
    with pytest.raises(RDPFeedError) as caught:
        await await_rdp_feed(bootstrap(), timeout_s=.005)
    assert caught.value.category == "FEED_STARTUP_TIMEOUT"
    assert published.is_set()
    assert cleaned.is_set()
    assert not any(task.get_name() == "rdp-market-ws"
                   for task in asyncio.all_tasks() if task is not asyncio.current_task())
