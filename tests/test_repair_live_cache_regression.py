"""First HD/TP/SL live session: stale position proofs starved newer order IDs."""

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import httpx
import pytest

from polymaker.rdp_sm_path_repair import PathRepairController
from polymaker.rdp_sm_sync import SyncObserver
from polymaker.rdp_sm_sync_reads import ObservedMarket
from test_rdp_sm_cached_reads import attach, finish
from test_rdp_sm_m1 import ADDRESS, CID, START
from test_rdp_sm_obi_execution import setup_obi
from test_rdp_sm_repair import env as env
from test_rdp_sm_repair import reflect_positions
from test_rdp_sm_sync import order, trade
from test_rdp_sm_sync import sync_env as sync_fixture


@pytest.fixture
def sync_env(monkeypatch, tmp_path):
    yield from sync_fixture.__wrapped__(monkeypatch, tmp_path)


async def test_invalidated_proofs_are_requeued_once_so_new_detail_is_not_starved(sync_env):
    venue, _, reader, _ = sync_env
    old_ids = [f"a-old-{i}" for i in range(4)]
    venue.opens = []
    venue.orders = {oid: order(oid, status="CANCELED") for oid in old_ids}
    service = SyncObserver(lambda *_: reader)
    service.reader = reader
    service._market = ObservedMarket(f"btc-updown-5m-{START}", CID, START, ("1", "2"))
    service._anchor = (venue.time, time.monotonic())
    service._loop = asyncio.get_running_loop()
    with ThreadPoolExecutor(max_workers=6) as pool:
        service._pool = pool
        await service._account_round()
        service.track_orders(dict.fromkeys(old_ids, 0.))
        await service._orders_round()
        service.accept_position_proofs(service.latest(), old_ids)
        assert len(service._position_proofs) == 4 and not service.latest().pending_ids

        def changed_account(request):
            if request.url.path == "/v2/positions":
                return httpx.Response(200, json={"data": [
                    {"token_id": "1", "current_size": "5", "avg_price": ".4"}],
                    "pagination": {"next_cursor": None}})

        venue.hook = changed_account
        new_id = "z-new-opening"
        venue.orders[new_id] = order(new_id, status="MATCHED", matched=5)
        t = trade()
        t["maker_orders"][0]["order_id"] = new_id
        venue.trades = [t]
        await service._account_round()
        service.track_orders({new_id: time.monotonic()})
        # Four old proofs require a recheck, then the NEW order must get a
        # slot on the next periodic round, even if its ID sorts after all four.
        await service._orders_round()
        await service._orders_round()
        good = {r.name: r for r in service.latest().last_good}
        assert "order:"+new_id in good
        assert good["order:"+new_id].data["size_matched"] == 5
        assert not service.latest().pending_ids
        assert not service._position_proofs  # never retain invalid economic proof
        previous = len(venue.calls)
        await service._orders_round()
        assert not any(path.startswith('/data/order/') for path, _ in venue.calls[previous:])


async def test_real_cached_repair_and_next_opening_after_four_old_orders(env, monkeypatch, tmp_path):
    setup = await setup_obi(env, monkeypatch, horizon=.2)
    v, e, _, lifecycle, normal, _, _, pump, _ = setup
    official, anchor = v.time, time.monotonic()

    def clock(request):
        if request.url.path == "/time":
            return httpx.Response(200, json=official+time.monotonic()-anchor)

    companion, _ = await attach(setup, tmp_path, hook=clock)
    # This production-path regression covers several fsync/SDK/reader workers.
    # Use the real freshness deadline instead of attach()'s 0.6s unit-test cap;
    # the deterministic test above separately checks detail-queue starvation.
    e.shared_reads.timeout_s = e.cfg.risk.ws_stale_halt_s
    pump.update(bid=".30", yes_ask=".32", no_bid=".68", no_ask=".70")

    async def rearm():
        pump["direction"] = "NONE"
        await asyncio.sleep(.03)
        pump["direction"] = "YES"
        await asyncio.sleep(.03)
        lifecycle.account = (await e._snapshot(include_books=False))[3]

    def filled(oid, tid, status):
        t = v.fill(oid, tid, 5, status)
        t["trader_side"] = "MAKER"
        t["maker_orders"][0]["maker_address"] = ADDRESS
        reflect_positions(v)

    try:
        for i in range(4):
            await rearm()
            result, _ = await normal.enter(*e.economic_inventory(), f"empty-{i}")
            assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert len(v.post_requests) == 4

        def fill_opening(venue):
            for a in e.attempts:
                if (a.quote.window_id in {"opening", "next-opening"} and a.order_id
                        and not venue.rows[a.order_id]["associate_trades"]):
                    filled(a.order_id, a.quote.window_id, "MATCHED")

        v.on_cancel = fill_opening
        await rearm()
        result, _ = await normal.enter(*e.economic_inventory(), "opening")
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert len(companion.service._position_proofs) >= 5
        assert e.economic_inventory() == (Decimal(5), Decimal(0))

        def fill_repair(venue, oid):
            row = venue.rows[oid]
            if row["asset_id"] == "2" and row["status"] == "LIVE" and not row["associate_trades"]:
                filled(oid, "repair", "MINED")

        v.on_order_read = fill_repair
        pump.update(bid=".50", yes_ask=".52", no_bid=".48", no_ask=".50")
        await asyncio.sleep(.03)
        lifecycle.account = (await e._snapshot(include_books=False))[3]
        repair = PathRepairController(e, lifecycle)
        monkeypatch.setattr(repair, "sigma", lambda: .02)
        result, _ = await repair.repair(*e.economic_inventory(), "repair")
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert e.economic_inventory() == (Decimal(5), Decimal(5)) and e.repair_episode is None
        assert len(v.post_requests) == 6

        await rearm()
        result, _ = await normal.enter(*e.economic_inventory(), "next-opening")
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert e.economic_inventory() == (Decimal(10), Decimal(5))
        assert e.repair_episode["opening_window"] == "next-opening"
        assert len(v.post_requests) == 7 and e.committed() < 15
    finally:
        await finish(setup, companion)
