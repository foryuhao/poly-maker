"""Tests for market-WS parsing and the book service routing."""

from __future__ import annotations

import asyncio
import json

from polymaker.domain import Side
from polymaker.marketdata.parse import (
    parse_book,
    parse_last_trade,
    parse_price_changes,
    parse_tick_size_change,
)
from polymaker.marketdata.service import MarketDataService

# frames modeled on live captures (2026-07-05)
BOOK = {
    "event_type": "book",
    "market": "0xcond",
    "asset_id": "yes-tok",
    "timestamp": "1783270000000",
    "hash": "abc123",
    "tick_size": "0.01",
    "bids": [{"price": "0.48", "size": "100"}, {"price": "0.49", "size": "200"}],
    "asks": [{"price": "0.52", "size": "150"}, {"price": "0.51", "size": "80"}],
}
PRICE_CHANGE = {
    "event_type": "price_change",
    "market": "0xcond",
    "timestamp": "1783270001000",
    "price_changes": [
        {"asset_id": "yes-tok", "price": "0.49", "size": "0", "side": "BUY", "hash": "h"},
        {"asset_id": "yes-tok", "price": "0.50", "size": "300", "side": "BUY", "hash": "h"},
    ],
}
LAST_TRADE = {
    "event_type": "last_trade_price",
    "market": "0xcond",
    "asset_id": "yes-tok",
    "price": "0.50",
    "size": "42",
    "side": "BUY",
    "timestamp": "1783270002000",
}


def test_parse_book_converts_ms_and_levels():
    upd = parse_book(BOOK)
    assert upd is not None
    assert upd.asset_id == "yes-tok"
    assert upd.condition_id == "0xcond"
    assert (0.49, 200) in upd.bids
    assert upd.ts == 1783270000.0  # ms -> s
    assert upd.tick_size == 0.01


def test_parse_price_changes():
    changes = parse_price_changes(PRICE_CHANGE)
    assert len(changes) == 2
    assert changes[0].side is Side.BUY
    assert changes[1].price == 0.50 and changes[1].size == 300


def test_parse_last_trade_aggressor():
    tp = parse_last_trade(LAST_TRADE)
    assert tp is not None
    assert tp.aggressor is Side.BUY
    assert tp.size == 42


def test_parse_tick_size_change():
    tc = parse_tick_size_change(
        {"event_type": "tick_size_change", "asset_id": "yes-tok", "new_tick_size": "0.001"}
    )
    assert tc is not None and tc.tick_size == 0.001


def test_service_routes_book_and_wakes_quoter():
    woken: list[tuple[str, str]] = []
    svc = MarketDataService(on_dirty=lambda c, t: woken.append((c, t)))
    svc.set_markets([("0xcond", ["yes-tok", "no-tok"])])
    svc._dispatch(BOOK)
    book = svc.book("yes-tok")
    assert book is not None
    assert book.best_bid().price == 0.49
    assert book.best_ask().price == 0.51
    assert woken == [("0xcond", "yes-tok")]


def test_service_applies_price_change_delta():
    svc = MarketDataService()
    svc.set_markets([("0xcond", ["yes-tok"])])
    svc._dispatch(BOOK)
    svc._dispatch(PRICE_CHANGE)  # removes 0.49 bid, adds 0.50 bid
    book = svc.book("yes-tok")
    assert book.best_bid().price == 0.50
    assert 0.49 not in book.bids


def test_service_ignores_unsubscribed_asset():
    svc = MarketDataService()
    svc.set_markets([("0xcond", ["yes-tok"])])
    svc._dispatch({**BOOK, "asset_id": "stranger"})
    assert svc.book("stranger") is None


def test_service_forwards_trades_for_flow():
    trades = []
    svc = MarketDataService(on_trade=trades.append)
    svc.set_markets([("0xcond", ["yes-tok"])])
    svc._dispatch(LAST_TRADE)
    assert len(trades) == 1 and trades[0].aggressor is Side.BUY


async def test_market_ws_bounded_reader_queue_drains_bursts_for_both_tokens():
    svc = MarketDataService(queue_maxsize=256)
    svc.set_markets([("0xcond", ["up", "down"])])
    svc.connected = True
    svc.connection_generation = 3
    queue = asyncio.Queue(maxsize=svc._queue_maxsize)
    original = svc._process_raw

    async def slow_process(raw):
        await asyncio.sleep(0.0005)
        await original(raw)

    svc._process_raw = slow_process
    consumer = asyncio.create_task(svc._consume(queue, 3))
    latest = {"up": 0.70, "down": 0.30}
    for i in range(80):
        token = "up" if i % 2 == 0 else "down"
        price = 0.10 + i / 1000
        latest[token] = price
        frame = {**BOOK, "asset_id": token, "timestamp": str(1783270000000 + i),
                 "bids": [{"price": str(price), "size": "10"}],
                 "asks": [{"price": "0.99", "size": "10"}]}
        queue.put_nowait((3, asyncio.get_running_loop().time(), json.dumps(frame)))
        await asyncio.sleep(0)
    await queue.join()
    consumer.cancel()
    await asyncio.gather(consumer, return_exceptions=True)
    assert queue.maxsize == 256 and queue.qsize() == 0
    assert svc.metrics["processed"] == 80
    assert svc.book("up").best_bid().price == latest["up"]
    assert svc.book("down").best_bid().price == latest["down"]


async def test_market_ws_consumer_discards_old_generation_backlog():
    svc = MarketDataService(queue_maxsize=4)
    svc.set_markets([("0xcond", ["yes-tok"])])
    svc.connected = True
    svc.connection_generation = 8
    queue = asyncio.Queue(maxsize=4)
    queue.put_nowait((7, asyncio.get_running_loop().time(), json.dumps(BOOK)))
    consumer = asyncio.create_task(svc._consume(queue, 7))
    await queue.join()
    consumer.cancel()
    await asyncio.gather(consumer, return_exceptions=True)
    assert svc.metrics["processed"] == 0
    assert svc.book("yes-tok").snapshot_generation == 0


async def test_service_notifies_connection_transitions(monkeypatch):
    transitions: list[bool] = []

    class FakeSocket:
        async def send(self, _payload):
            return None

        def __aiter__(self):
            async def frames():
                if False:
                    yield ""
            return frames()

    class SocketContext:
        async def __aenter__(self):
            return FakeSocket()

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr("polymaker.marketdata.service.websockets.connect",
                        lambda *_args, **_kwargs: SocketContext())
    svc = MarketDataService(on_connection=transitions.append)
    svc.set_markets([("0xcond", ["yes-tok", "no-tok"])])
    await svc._connect_and_listen()
    assert transitions == [True, False]
    assert svc.connected is False
    assert svc.disconnected_since > 0
