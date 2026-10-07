from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime

import pytest

from polymaker.domain import MarketMeta, TokenMeta
from polymaker.rdp_live import RDPBootstrap, TokenBookMetadata
from polymaker.rdp_marketdata import RDPFeedError, RDPMarketDataService, await_rdp_feed


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
