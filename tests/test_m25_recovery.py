"""Deterministic regression tests for M2.5 connectivity races."""

from __future__ import annotations

import asyncio
import threading
import time

import httpx
import pytest

from polymaker.config import Config
from polymaker.domain import OpenOrder, OrderState, Side
from polymaker.execution.gateway import ExecutionGateway, GatewayReadError
from tests.test_engine import _engine_with_market, _feed_book


def _drop(eng) -> None:
    eng._market_ws_was_connected = True
    eng.md.connected = False
    eng._on_market_ws_connection(False)


def _reconnect(eng, meta, *, books: bool = True) -> None:
    eng.md.connection_generation += 1
    eng.md.connected_since = time.time()
    eng.md.connected = True
    eng._on_market_ws_connection(True)
    if books:
        _feed_book(eng, meta)
        for token in (meta.yes.token_id, meta.no.token_id):
            eng.md.book(token).snapshot_generation = eng.md.connection_generation


def _fresh_recovery(eng, meta) -> None:
    _drop(eng)
    _reconnect(eng, meta)
    eng._on_dirty(meta.condition_id, meta.yes.token_id)


async def _wait_thread_event(event: threading.Event) -> None:
    for _ in range(200):
        if event.is_set():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("timed out waiting for Gateway REST call")


async def test_disconnect_during_gateway_bucket_wait_aborts_submission(tmp_path, meta):
    eng = _engine_with_market(tmp_path, meta)
    _feed_book(eng, meta)
    entered = asyncio.Event()
    release = asyncio.Event()
    submitted = []
    original_paper_order = eng.gateway._paper_order

    async def blocked_acquire(_amount):
        entered.set()
        await release.wait()

    def record_paper_order(quote):
        submitted.append(quote)
        return original_paper_order(quote)

    eng.gateway._order_bucket.acquire = blocked_acquire
    eng.gateway._paper_order = record_paper_order
    recompute = asyncio.create_task(eng._recompute(meta.condition_id))
    await entered.wait()
    _drop(eng)
    release.set()
    await recompute

    assert submitted == []
    assert eng.state.orders == {}
    eng.state.close()
    eng.catalog.close()


async def test_connectivity_guard_allows_healthy_paper_placement(tmp_path, meta):
    eng = _engine_with_market(tmp_path, meta)
    _feed_book(eng, meta)
    await eng._recompute(meta.condition_id)
    assert eng.state.orders
    eng.state.close()
    eng.catalog.close()


async def test_only_current_recovery_reconciliation_can_resume(tmp_path, meta):
    eng = _engine_with_market(tmp_path, meta)
    _drop(eng)
    generation_1 = eng._market_recovery_generation
    _reconnect(eng, meta)
    eng._on_dirty(meta.condition_id, meta.yes.token_id)

    first_started = asyncio.Event()
    release_first = asyncio.Event()
    second_started = asyncio.Event()
    release_second = asyncio.Event()
    calls = 0

    async def positions():
        nonlocal calls
        calls += 1
        if calls == 1:
            first_started.set()
            await release_first.wait()
        elif calls == 2:
            second_started.set()
            await release_second.wait()
        return {}

    async def open_orders():
        return []

    eng.gateway.positions = positions
    eng.gateway.open_orders = open_orders
    eng._reconcile_now.set()
    reconcile_task = asyncio.create_task(eng._reconcile_loop())
    await first_started.wait()
    assert generation_1 is not None

    _drop(eng)
    generation_2 = eng._market_recovery_generation
    assert generation_2 > generation_1
    _reconnect(eng, meta)
    eng._on_dirty(meta.condition_id, meta.yes.token_id)
    release_first.set()
    await second_started.wait()

    assert eng._market_recovery_pending
    await eng._recompute(meta.condition_id)
    assert eng.state.orders == {}

    release_second.set()
    for _ in range(100):
        if not eng._market_recovery_pending:
            break
        await asyncio.sleep(0.005)
    assert not eng._market_recovery_pending
    await eng._recompute(meta.condition_id)
    assert eng.state.orders

    eng._running = False
    reconcile_task.cancel()
    await asyncio.gather(reconcile_task, return_exceptions=True)
    eng.state.close()
    eng.catalog.close()


async def test_one_fresh_outcome_book_does_not_start_recovery(tmp_path, meta):
    eng = _engine_with_market(tmp_path, meta)
    _feed_book(eng, meta)
    _drop(eng)
    _reconnect(eng, meta, books=False)
    eng.md.book(meta.yes.token_id).apply_snapshot(
        [(0.48, 500), (0.49, 500)], [(0.51, 500), (0.52, 500)], time.time()
    )
    eng.md.book(meta.yes.token_id).snapshot_generation = eng.md.connection_generation
    eng._on_dirty(meta.condition_id, meta.yes.token_id)

    assert not eng._market_books_fresh(meta.condition_id)
    assert not eng._reconcile_now.is_set()
    await eng._recompute(meta.condition_id)
    assert eng.state.orders == {}
    eng.state.close()
    eng.catalog.close()


async def test_old_snapshots_cannot_satisfy_new_connection_generation(tmp_path, meta):
    eng = _engine_with_market(tmp_path, meta)
    _feed_book(eng, meta)
    old_generation = eng.md.connection_generation
    _drop(eng)
    _reconnect(eng, meta, books=False)

    assert eng.md.connection_generation > old_generation
    assert not eng._market_books_fresh(meta.condition_id)
    await eng._recompute(meta.condition_id)
    assert eng.state.orders == {}
    eng.state.close()
    eng.catalog.close()


async def test_reconciliation_failure_stays_fail_closed(tmp_path, meta):
    eng = _engine_with_market(tmp_path, meta)
    _drop(eng)
    _reconnect(eng, meta)
    eng._on_dirty(meta.condition_id, meta.yes.token_id)

    failed = asyncio.Event()

    async def failed_positions():
        failed.set()
        raise RuntimeError("simulated REST failure")

    eng.gateway.positions = failed_positions
    eng._reconcile_now.set()
    task = asyncio.create_task(eng._reconcile_loop())
    await failed.wait()
    await asyncio.sleep(0)
    await eng._recompute(meta.condition_id)
    assert eng._market_recovery_pending
    assert eng.state.orders == {}
    eng._running = False
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    eng.state.close()
    eng.catalog.close()


async def test_shutdown_during_recovery_cannot_resume_trading(tmp_path, meta):
    eng = _engine_with_market(tmp_path, meta)
    _drop(eng)
    _reconnect(eng, meta)
    eng._on_dirty(meta.condition_id, meta.yes.token_id)
    started = asyncio.Event()

    async def delayed_positions():
        started.set()
        await asyncio.Future()

    eng.gateway.positions = delayed_positions
    eng._reconcile_now.set()
    task = asyncio.create_task(eng._reconcile_loop())
    await started.wait()
    eng._tasks["reconcile"] = task
    await eng.shutdown()
    assert task.done()
    assert not eng._running
    assert eng._market_recovery_pending
    assert eng.state.orders == {}


async def test_open_orders_rest_failure_keeps_recovery_pending(tmp_path, meta):
    eng = _engine_with_market(tmp_path, meta)
    _fresh_recovery(eng, meta)
    eng.cfg.engine.reconcile_interval_s = 1000.0
    eng.gateway._paper = False
    called = threading.Event()

    class FailingClient:
        def get_open_orders(self):
            called.set()
            raise OSError("simulated open-orders REST failure")

    eng.gateway._client = FailingClient()
    async def empty_positions():
        return {}

    eng.gateway.positions = empty_positions
    task = asyncio.create_task(eng._reconcile_loop())
    await _wait_thread_event(called)
    await asyncio.sleep(0)

    assert eng._market_recovery_pending
    await eng._recompute(meta.condition_id)
    assert eng.state.orders == {}
    eng._running = False
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    eng.state.close()
    eng.catalog.close()


async def test_positions_rest_failure_keeps_recovery_pending(tmp_path, meta, monkeypatch):
    eng = _engine_with_market(tmp_path, meta)
    _fresh_recovery(eng, meta)
    eng.cfg.engine.reconcile_interval_s = 1000.0
    eng.gateway._paper = False
    eng.gateway._funder = "0xFunder"
    called = asyncio.Event()

    class FailingResponse:
        def raise_for_status(self):
            raise OSError("simulated positions REST failure")

    class FailingHttp:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, *_args, **_kwargs):
            called.set()
            raise httpx.ConnectError("simulated positions REST failure")

    monkeypatch.setattr("polymaker.execution.gateway.httpx.AsyncClient", FailingHttp)
    task = asyncio.create_task(eng._reconcile_loop())
    await called.wait()
    await asyncio.sleep(0)

    assert eng._market_recovery_pending
    await eng._recompute(meta.condition_id)
    assert eng.state.orders == {}
    eng._running = False
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    eng.state.close()
    eng.catalog.close()


async def test_successful_empty_gateway_snapshots_are_valid(monkeypatch):
    gw = ExecutionGateway(Config(), paper=False)
    gw._client = type("EmptyClient", (), {"get_open_orders": lambda _self: []})()
    gw._funder = "0xFunder"

    class EmptyResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [], "pagination": {"next_cursor": None}}

    class EmptyHttp:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, *_args, **_kwargs):
            return EmptyResponse()

    monkeypatch.setattr("polymaker.execution.gateway.httpx.AsyncClient", EmptyHttp)
    assert await gw.open_orders() == []
    assert await gw.positions() == {}
    gw.close()


def _valid_open_order_row(order_id="exchange-order"):
    return {
        "id": order_id,
        "asset_id": "token",
        "side": "BUY",
        "price": "0.49",
        "original_size": "10",
        "size_matched": "2",
    }


@pytest.mark.parametrize("row", [
    {k: v for k, v in _valid_open_order_row().items() if k != "id"},
    {**_valid_open_order_row(), "id": None},
    {**_valid_open_order_row(), "id": ""},
    {**_valid_open_order_row(), "id": "   "},
])
async def test_gateway_rejects_open_order_without_valid_id(row):
    gw = ExecutionGateway(Config(), paper=False)
    gw._client = type("Client", (), {"get_open_orders": lambda _self: [row]})()

    with pytest.raises(GatewayReadError):
        await gw.open_orders()
    gw.close()


async def test_gateway_rejects_whole_snapshot_with_malformed_order_row():
    gw = ExecutionGateway(Config(), paper=False)
    rows = [_valid_open_order_row("one"), _valid_open_order_row("two"),
            {**_valid_open_order_row(), "id": None}]
    gw._client = type("Client", (), {"get_open_orders": lambda _self: rows})()

    with pytest.raises(GatewayReadError):
        await gw.open_orders()
    gw.close()


@pytest.mark.parametrize("asset_id", [None, "", "   "])
async def test_gateway_rejects_order_without_valid_asset_id(asset_id):
    gw = ExecutionGateway(Config(), paper=False)
    row = {**_valid_open_order_row(), "asset_id": asset_id}
    gw._client = type("Client", (), {"get_open_orders": lambda _self: [row]})()

    with pytest.raises(GatewayReadError):
        await gw.open_orders()
    gw.close()


async def test_gateway_parses_valid_exchange_order_id():
    gw = ExecutionGateway(Config(), paper=False)
    gw._client = type(
        "Client", (), {"get_open_orders": lambda _self: [_valid_open_order_row("order-123")]}
    )()

    orders = await gw.open_orders()
    assert len(orders) == 1
    assert orders[0].order_id == "order-123"
    assert orders[0].token_id == "token"
    assert orders[0].size == 8
    gw.close()


async def test_malformed_open_order_snapshot_keeps_recovery_pending_until_retry(
    tmp_path, meta,
):
    eng = _engine_with_market(tmp_path, meta)
    _fresh_recovery(eng, meta)
    eng.cfg.engine.reconcile_interval_s = 0.01
    eng.gateway._paper = False
    malformed_seen = threading.Event()
    retry_seen = threading.Event()
    release_retry = threading.Event()
    valid_seen = threading.Event()
    calls = 0

    class FlakyClient:
        def get_open_orders(self):
            nonlocal calls
            calls += 1
            if calls == 1:
                malformed_seen.set()
                return [_valid_open_order_row(), {**_valid_open_order_row(), "id": None}]
            retry_seen.set()
            if not release_retry.wait(timeout=2):
                raise TimeoutError("test did not release authoritative retry")
            valid_seen.set()
            return []

    eng.gateway._client = FlakyClient()

    async def empty_positions():
        return {}

    eng.gateway.positions = empty_positions
    placements = []

    async def spy_place(*_args, **_kwargs):
        placements.append(True)
        return []

    eng.gateway.place = spy_place
    task = asyncio.create_task(eng._reconcile_loop())
    await _wait_thread_event(malformed_seen)
    await _wait_thread_event(retry_seen)
    assert eng._market_recovery_pending
    assert placements == []

    release_retry.set()
    await _wait_thread_event(valid_seen)
    for _ in range(200):
        if not eng._market_recovery_pending:
            break
        await asyncio.sleep(0.005)
    assert not eng._market_recovery_pending
    assert placements == []

    eng._running = False
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    eng.state.close()
    eng.catalog.close()


async def test_cancel_failure_and_refresh_failure_preserve_order(tmp_path, meta):
    eng = _engine_with_market(tmp_path, meta)
    order = OpenOrder("resting", meta.yes.token_id, Side.BUY, 0.49, 10, OrderState.LIVE)
    eng.state.upsert_order(order)

    async def cancel_failed(_asset):
        return False

    async def refresh_failed():
        raise GatewayReadError("simulated open-orders REST failure")

    eng.gateway.cancel_asset = cancel_failed
    eng.gateway.open_orders = refresh_failed
    with pytest.raises(GatewayReadError):
        await eng._quarantine(meta, "test_uncertain_order")

    assert eng.state.orders_for(meta.yes.token_id) == [order]
    assert meta.condition_id in eng._order_state_uncertain
    placement_calls = []

    async def spy_place(*_args, **_kwargs):
        placement_calls.append(True)
        return []

    eng.gateway.place = spy_place
    with pytest.raises(GatewayReadError):
        await eng._recompute(meta.condition_id)
    assert placement_calls == []
    assert eng.state.orders_for(meta.yes.token_id) == [order]
    eng.state.close()
    eng.catalog.close()


async def test_cancel_failure_successful_refresh_converges_to_rest(tmp_path, meta):
    eng = _engine_with_market(tmp_path, meta)
    order = OpenOrder("resting", meta.yes.token_id, Side.BUY, 0.49, 10, OrderState.LIVE)
    eng.state.upsert_order(order)

    async def cancel_failed(_asset):
        return False

    async def refresh_present():
        return [order]

    eng.gateway.cancel_asset = cancel_failed
    eng.gateway.open_orders = refresh_present
    await eng._quarantine(meta, "test_rest_confirms_order")
    assert eng.state.orders_for(meta.yes.token_id) == [order]
    assert meta.condition_id not in eng._order_state_uncertain

    async def refresh_empty():
        return []

    eng.gateway.open_orders = refresh_empty
    await eng._quarantine(meta, "test_rest_confirms_empty")
    assert eng.state.orders_for(meta.yes.token_id) == []
    assert meta.condition_id not in eng._order_state_uncertain
    eng.state.close()
    eng.catalog.close()


async def test_recovery_retries_after_transient_open_orders_failure(tmp_path, meta):
    eng = _engine_with_market(tmp_path, meta)
    _fresh_recovery(eng, meta)
    eng.cfg.engine.reconcile_interval_s = 0.01
    eng.gateway._paper = False
    calls = 0
    succeeded = threading.Event()

    class FlakyClient:
        def get_open_orders(self):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("transient open-orders REST failure")
            succeeded.set()
            return []

    eng.gateway._client = FlakyClient()
    async def empty_positions():
        return {}

    eng.gateway.positions = empty_positions
    task = asyncio.create_task(eng._reconcile_loop())
    await _wait_thread_event(succeeded)
    for _ in range(200):
        if not eng._market_recovery_pending:
            break
        await asyncio.sleep(0.005)

    assert calls >= 2
    assert not eng._market_recovery_pending
    eng._running = False
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    eng.state.close()
    eng.catalog.close()
