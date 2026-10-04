"""Integration test: one full engine recompute cycle in paper mode (no network)."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from polymaker.config import Config, PathsConfig, StrategyProfile
from polymaker.domain import Side
from polymaker.engine import Engine
from polymaker.strategy.regime import RegimeMachine


def _engine_with_market(tmp_path, meta) -> Engine:
    cfg = Config(paths=PathsConfig(db=str(tmp_path / "state.db"),
                                   journal_dir=str(tmp_path / "j"),
                                   log_dir=str(tmp_path / "l")))
    cfg.engine.journal = False
    eng = Engine(cfg, paper=True)
    cid = meta.condition_id
    # inject one market directly, bypassing network resolution
    eng.metas[cid] = meta
    eng.profiles[cid] = StrategyProfile()
    eng.est[cid] = Engine._make_estimators(eng.profiles[cid])
    eng.regime_m[cid] = RegimeMachine()
    eng._dirty[cid] = asyncio.Event()
    eng._locks[cid] = asyncio.Lock()
    for tok in (meta.yes.token_id, meta.no.token_id):
        eng._token_cid[tok] = cid
    eng.md.set_markets([(cid, [meta.yes.token_id, meta.no.token_id])])
    eng.md.connected = True
    eng.md.connected_since = 0.0
    eng._running = True
    return eng


def _feed_book(eng, meta):
    now = time.time()  # fresh ts so the ws_stale guard doesn't HALT the market
    yb = eng.md.book(meta.yes.token_id)
    yb.apply_snapshot(bids=[(0.48, 500), (0.49, 500)], asks=[(0.51, 500), (0.52, 500)], ts=now)
    nb = eng.md.book(meta.no.token_id)
    nb.apply_snapshot(bids=[(0.48, 500), (0.49, 500)], asks=[(0.51, 500), (0.52, 500)], ts=now)


async def test_recompute_places_two_sided_paper_quotes(tmp_path, meta):
    eng = _engine_with_market(tmp_path, meta)
    _feed_book(eng, meta)
    await eng._recompute(meta.condition_id)

    yes_orders = eng.state.orders_for(meta.yes.token_id)
    no_orders = eng.state.orders_for(meta.no.token_id)
    assert yes_orders, "no YES quotes placed"
    assert no_orders, "no NO quotes placed"
    # entry quotes are BUYs on both tokens (the canonical two-sided quote)
    assert all(o.side is Side.BUY for o in yes_orders)
    assert all(o.side is Side.BUY for o in no_orders)
    eng.state.close()
    eng.catalog.close()


async def test_recompute_is_idempotent_within_tolerance(tmp_path, meta):
    eng = _engine_with_market(tmp_path, meta)
    _feed_book(eng, meta)
    await eng._recompute(meta.condition_id)
    n_after_first = len(eng.state.orders)
    # same book -> reconcile should be a no-op, order count unchanged
    await eng._recompute(meta.condition_id)
    assert len(eng.state.orders) == n_after_first
    eng.state.close()
    eng.catalog.close()


async def test_recompute_skips_when_book_empty(tmp_path, meta):
    eng = _engine_with_market(tmp_path, meta)
    # no book fed
    await eng._recompute(meta.condition_id)
    assert len(eng.state.orders) == 0
    eng.state.close()
    eng.catalog.close()


@pytest.mark.asyncio
async def test_live_position_reconcile_uses_data_api_without_chain_balance(tmp_path, meta):
    eng = _engine_with_market(tmp_path, meta)
    called = asyncio.Event()
    gateway = SimpleNamespace(
        positions=AsyncMock(return_value={meta.yes.token_id: (7.0, 0.4)}),
        open_orders=AsyncMock(return_value=[]),
        token_balances=AsyncMock(side_effect=AssertionError("RPC MUST NOT BE CALLED")),
    )
    eng.gateway = gateway
    eng._reconcile_now.set()
    original_positions = gateway.positions

    async def positions_and_signal():
        result = await original_positions()
        called.set()
        return result

    gateway.positions = AsyncMock(side_effect=positions_and_signal)
    task = asyncio.create_task(eng._reconcile_loop())
    try:
        await asyncio.wait_for(called.wait(), timeout=2.0)
        assert eng.state.position(meta.yes.token_id).size == 7.0
        gateway.token_balances.assert_not_awaited()
        assert not hasattr(eng, "_check_position_divergence")
    finally:
        eng._running = False
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        eng.state.close()
        eng.catalog.close()


@pytest.mark.asyncio
async def test_live_auto_merge_is_disabled_for_paired_inventory(tmp_path, meta):
    eng = _engine_with_market(tmp_path, meta)
    eng.paper = False
    eng.state.reconcile_positions({
        meta.yes.token_id: (3.0, 0.5),
        meta.no.token_id: (4.0, 0.5),
    })
    profile = StrategyProfile(merge_min_size=2.0)
    eng.gateway.token_balances = AsyncMock(
        side_effect=AssertionError("RPC MUST NOT BE CALLED FOR AUTO-MERGE"),
    )
    eng._merge_task = AsyncMock()
    eng.merger.merge = Mock(side_effect=AssertionError("MERGER MUST NOT BE CALLED"))
    try:
        eng._maybe_merge(
            meta.condition_id, meta, profile,
            eng.state.position(meta.yes.token_id).size,
            eng.state.position(meta.no.token_id).size,
        )
        await asyncio.sleep(0)
        eng._merge_task.assert_not_awaited()
        eng.gateway.token_balances.assert_not_awaited()
        eng.merger.merge.assert_not_called()
        assert eng._aux_tasks == []
        assert eng._merging == set()
    finally:
        eng.state.close()
        eng.catalog.close()


class _StartupGateway:
    def __init__(self, *, matched=5, trades=None, fail=False, active=()):
        from polymaker.domain import OpenOrder, OrderState, Side
        self.active = list(active)
        self.matched = matched
        self.trades = trades if trades is not None else [{"id": "t1", "status": "MATCHED",
            "maker_orders": [{"order_id": "ours", "asset_id": "yes-token", "side": "BUY",
                "matched_amount": str(matched), "price": ".2"}]}] if matched else []
        self.fail = fail
        self.sequence = []
    async def open_orders(self):
        self.sequence.append("open_orders")
        return list(self.active)
    async def cancel_all(self):
        self.sequence.append("cancel_all")
        self.active = []
    async def positions(self):
        self.sequence.append("positions")
        return {}
    async def get_order(self, oid):
        self.sequence.append("get_order")
        if self.fail: raise RuntimeError("order REST down")
        return {"id": oid, "asset_id": "yes-token", "side": "BUY", "price": ".2",
            "original_size": "5", "size_matched": str(self.matched)}
    async def get_trades(self, **_kwargs):
        self.sequence.append("get_trades")
        if self.fail: raise RuntimeError("trade REST down")
        return self.trades


def _startup_engine_with_persisted_order(path, gateway, meta):
    from polymaker.domain import Side
    from polymaker.state.store import StateStore
    from polymaker.state.tracker import UserEventProcessor
    eng = Engine.__new__(Engine)
    eng.state = StateStore(path)
    eng.gateway = gateway
    eng.metas = {meta.condition_id: meta}
    eng._token_cid = {meta.yes.token_id: meta.condition_id}
    eng._fill_recovery_pending = set()
    eng._fill_recovery_orders = {}
    eng.alerter = type("A", (), {"alert": lambda *_a, **_k: None})()
    eng.state.register_owned_order("ours", meta.condition_id, meta.yes.token_id,
                                   Side.BUY, 5, .2, "engine")
    eng.user_proc = UserEventProcessor(eng.state)
    return eng


@pytest.mark.asyncio
async def test_startup_recovers_missed_fill_before_recovery_state_clears(tmp_path, meta):
    from polymaker.domain import OpenOrder, OrderState, Side
    gateway = _StartupGateway(active=[OpenOrder("ours", "yes-token", Side.BUY, .2, 5, OrderState.LIVE)])
    eng = _startup_engine_with_persisted_order(tmp_path / "state.db", gateway, meta)
    try:
        await eng._startup_reconcile()
        assert eng.state.position("yes-token").size == 5
        assert eng._fill_recovery_pending == set()
        assert gateway.sequence.index("get_order") < gateway.sequence.index("positions")
    finally:
        eng.state.close()


@pytest.mark.asyncio
async def test_startup_clean_order_reconciles_without_new_fill(tmp_path, meta):
    gateway = _StartupGateway(matched=0, trades=[])
    eng = _startup_engine_with_persisted_order(tmp_path / "state.db", gateway, meta)
    try:
        await eng._startup_reconcile()
        assert eng.state.position("yes-token").size == 0
        assert not eng._fill_recovery_pending
    finally:
        eng.state.close()


@pytest.mark.asyncio
async def test_startup_rest_failure_prevents_recovery_completion(tmp_path, meta):
    gateway = _StartupGateway(fail=True)
    eng = _startup_engine_with_persisted_order(tmp_path / "state.db", gateway, meta)
    try:
        import pytest
        with pytest.raises(RuntimeError, match="startup fill reconciliation unresolved"):
            await eng._startup_reconcile()
        assert eng._fill_recovery_pending == {meta.condition_id}
        assert gateway.sequence[-1] != "positions"
    finally:
        eng.state.close()


class _PublicStartGateway(_StartupGateway):
    def __init__(self, events, *, fail=False):
        super().__init__(matched=5, fail=fail)
        self.events = events
        self.creds, self.address = object(), "wallet"
    async def connect(self): self.events.append("connect")
    async def cancel_all(self): self.events.append("cancel_all"); self.active = []
    async def get_order(self, oid):
        self.events.append("get_order")
        if self.fail: raise RuntimeError("REST unavailable")
        return {"id": oid, "asset_id": "yes-token", "side": "BUY", "price": ".2",
            "original_size": "5", "size_matched": "5"}
    async def get_trades(self, **_kwargs):
        self.events.append("get_trades")
        return [{"id": "offline-fill", "status": "MATCHED", "market": "0xcond",
            "maker_orders": [{"order_id": "ours", "asset_id": "yes-token", "side": "BUY",
                "matched_amount": "5", "price": ".2"}]}]


def _public_start_engine(tmp_path, meta, *, fail=False):
    from polymaker.state.tracker import UserEventProcessor
    cfg = Config(paths=PathsConfig(db=str(tmp_path / "public-start.db"),
        journal_dir=str(tmp_path / "j"), log_dir=str(tmp_path / "l")))
    cfg.engine.journal = False
    eng = Engine(cfg, paper=True)
    events = []
    eng.state.register_owned_order("ours", meta.condition_id, meta.yes.token_id,
        Side.BUY, 5, .2, "engine")
    eng.user_proc = UserEventProcessor(eng.state)
    gw = _PublicStartGateway(events, fail=fail)
    eng.gateway = gw
    eng.metas = {meta.condition_id: meta}
    eng._token_cid = {meta.yes.token_id: meta.condition_id, meta.no.token_id: meta.condition_id}
    async def resolve(): events.append("resolve")
    async def refresh(): events.append("refresh")
    eng._resolve_markets = resolve
    eng.refresh_market_metadata = refresh
    spawned = []
    def spawn(name, factory):
        events.append("spawn:" + name)
        spawned.append(name)
    eng._spawn = spawn
    eng._public_test_events = events
    return eng, spawned


@pytest.mark.asyncio
async def test_engine_start_public_path_recovers_offline_fill_before_spawning_risk(tmp_path, meta):
    eng, spawned = _public_start_engine(tmp_path, meta)
    try:
        await eng.start()
        events = eng._public_test_events
        assert eng.state.position(meta.yes.token_id).size == 5
        assert events.index("get_trades") < next(i for i, x in enumerate(events) if x.startswith("spawn:"))
        assert "spawn:quote:0xcond" in events
        assert not any(x.startswith("placement") for x in events)
    finally:
        eng.state.close(); eng.catalog.close()


@pytest.mark.asyncio
async def test_engine_start_public_path_fails_closed_before_quote_tasks_on_rest_error(tmp_path, meta):
    eng, spawned = _public_start_engine(tmp_path, meta, fail=True)
    try:
        with pytest.raises(RuntimeError, match="startup fill reconciliation unresolved"):
            await eng.start()
        assert spawned == []
        assert not any(x.startswith("spawn:") for x in eng._public_test_events)
        assert eng.state.position(meta.yes.token_id).size == 0
    finally:
        eng.state.close(); eng.catalog.close()


async def test_market_ws_disconnect_wakes_and_cancels_quotes(tmp_path, meta):
    eng = _engine_with_market(tmp_path, meta)
    _feed_book(eng, meta)
    await eng._recompute(meta.condition_id)
    assert eng.state.orders
    ev = eng._dirty[meta.condition_id]
    ev.clear()

    eng._market_ws_was_connected = True
    eng.md.connected = False
    eng.md.disconnected_since = time.time()
    eng._on_market_ws_connection(False)
    assert ev.is_set()
    assert eng._market_recovery_pending
    await eng._recompute(meta.condition_id)
    assert eng.state.orders == {}
    eng.state.close()
    eng.catalog.close()


async def test_market_ws_reconnect_waits_for_both_fresh_books_and_reconcile(tmp_path, meta):
    eng = _engine_with_market(tmp_path, meta)
    _feed_book(eng, meta)
    await eng._recompute(meta.condition_id)
    eng._market_ws_was_connected = True
    eng._market_recovery_pending = True
    eng._market_connectivity_generation = 1
    eng._market_recovery_generation = 1
    eng.md.connected = True
    eng.md.connected_since = time.time() + 1.0
    eng._on_market_ws_connection(True)
    await eng._recompute(meta.condition_id)
    assert eng.state.orders == {}

    eng.md.connected_since = time.time() - 1.0
    _feed_book(eng, meta)
    eng._on_dirty(meta.condition_id, meta.yes.token_id)
    assert eng._reconcile_now.is_set()
    assert eng._market_recovery_pending  # socket + books alone do not resume

    async def positions():
        return {}

    async def open_orders():
        return []

    eng.gateway.positions = positions  # type: ignore[method-assign]
    eng.gateway.open_orders = open_orders  # type: ignore[method-assign]
    eng._reconcile_now.set()
    task = asyncio.create_task(eng._reconcile_loop())
    for _ in range(50):
        if not eng._market_recovery_pending:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert not eng._market_recovery_pending
    await eng._recompute(meta.condition_id)
    assert eng.state.orders
    eng._running = False
    eng.state.close()
    eng.catalog.close()


async def test_market_ws_repeated_drop_recovery_is_idempotent(tmp_path, meta):
    eng = _engine_with_market(tmp_path, meta)
    for _ in range(3):
        eng._market_ws_was_connected = True
        eng.md.connected = False
        eng._on_market_ws_connection(False)
        assert eng._market_recovery_pending
        eng.md.connected = True
        eng.md.connected_since = time.time() - 1.0
        _feed_book(eng, meta)
        eng._on_market_ws_connection(True)
        assert eng._market_books_fresh(meta.condition_id)
        eng._market_recovery_pending = False  # model successful authoritative reconcile
    eng._wake_all()
    assert len(eng._tasks) == 0  # transitions do not spawn background tasks
    eng.state.close()
    eng.catalog.close()
