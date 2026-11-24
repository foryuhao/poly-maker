import asyncio
from contextlib import closing
import sqlite3
from threading import RLock
from types import SimpleNamespace

import pytest

from m12_follow.adapter import FollowQuoteFeed, FollowSocket
from polymaker.m4_live_budget_market import BudgetMarket
from polymaker.m4_types import M421_LEDGER
from polymaker.taker_runner import SharedTakerFeed


@pytest.mark.asyncio
async def test_sqlite_follower_subscribes_only_after_intent_and_switches_tokens(monkeypatch):
    epoch = 1795414500
    clock = SimpleNamespace(now=epoch + 15)
    feed = object.__new__(FollowSocket)
    signal = SimpleNamespace(market_scoped=True, settings=SimpleNamespace(cutoff_sec=240),
                             latest_key=None, observed={}, available=False, refresh=lambda: None)
    child = object.__new__(FollowQuoteFeed)
    market = object.__new__(BudgetMarket)
    market.e = SimpleNamespace(fatal=None)
    market.stop = asyncio.Event()
    market.owned_order_work = False
    child.m1, child.follow_market, child.execution_token = signal, market, None
    feed.secondary = {M421_LEDGER: child}
    feed.meta = SimpleNamespace(slug=f"btc-updown-5m-{epoch}")
    feed.stop_event = asyncio.Event()
    feed.lock = RLock()
    feed.wall = lambda: clock.now
    opened, closed = asyncio.Queue(), asyncio.Queue()

    async def quote_socket(self):
        tokens = self.subscription_token_ids
        await opened.put(tokens)
        try:
            await asyncio.Event().wait()
        finally:
            await closed.put(tokens)

    monkeypatch.setattr(SharedTakerFeed, "run", quote_socket)
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("CREATE TABLE opportunities(id,state,reason)")
        market.ledger = SimpleNamespace(db=db, lock=RLock(), get=lambda _: False)
        task = asyncio.create_task(feed.run())
        try:
            await asyncio.sleep(.04)
            assert opened.empty()
            assert not feed.follow_account_demand()
            market.owned_order_work = True
            assert feed.follow_account_demand()
            market.owned_order_work = False

            signal.observed["yes"] = SimpleNamespace(token_id="1")
            signal.latest_key, signal.available = "yes", True
            assert await asyncio.wait_for(opened.get(), 1) == ("1",)
            assert feed.follow_account_demand()
            db.execute("INSERT INTO opportunities VALUES ('yes','RESERVED','RESIDUAL')")
            await asyncio.sleep(.04)
            assert closed.empty()
            db.execute("UPDATE opportunities SET state='BUDGET_EXHAUSTED' WHERE id='yes'")
            assert await asyncio.wait_for(closed.get(), 1) == ("1",)
            await asyncio.sleep(.04)
            assert opened.empty()
            assert not feed.follow_account_demand()

            signal.observed["no"] = SimpleNamespace(token_id="2")
            signal.latest_key = "no"
            assert await asyncio.wait_for(opened.get(), 1) == ("2",)
            clock.now = epoch + 240
            await asyncio.wait_for(task, 1)
            assert closed.get_nowait() == ("2",)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


def test_token_scoped_socket_only_mirrors_execution_book_updates():
    feed = object.__new__(FollowSocket)
    feed.lock = RLock()
    feed.monotonic = lambda: 1
    feed.wall = lambda: 100
    feed.validate_receipt_clock = lambda *args: None
    feed.subscription_token_ids = ("1",)
    mirrored = []
    feed.mirror = lambda name, rows, **times: mirrored.append(rows)
    kept = {"event_type": "book", "asset_id": "1"}
    feed._apply([kept, {"event_type": "book", "asset_id": "2"},
                 {"event_type": "last_trade_price", "asset_id": "1"},
                 {"event_type": "price_change", "price_changes": [{"asset_id": "1"}, {"asset_id": "2"}]}])
    assert mirrored == [[kept, {"event_type": "price_change", "price_changes": [{"asset_id": "1"}]}]]
