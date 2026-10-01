"""M4A.2 applied-event and knowledge-time regressions."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from polymaker.config import Config, PathsConfig
from polymaker.domain import Quote, Side, TradeState
from polymaker.engine import Engine
from polymaker.execution.gateway import ExecutionGateway
from polymaker.marketdata.service import MarketDataService
from polymaker.research.capture import CausalCapture
from polymaker.research.observer import ResearchConfig, ResearchObserver
from polymaker.state.store import StateStore
from polymaker.state.tracker import TradeEvent, UserEventProcessor
from polymaker.strategy.favorite_first import FavoriteFirstConfig
from tests.test_engine import _engine_with_market, _feed_book
from tests.test_marketdata import BOOK, PRICE_CHANGE


def capture():
    observer = ResearchObserver(ResearchConfig(enabled=True, queue_capacity=256))
    return CausalCapture(observer, "causal-session")


def events(cap):
    queue = cap.observer.queue
    assert queue is not None
    rows = []
    while not queue.empty():
        rows.append(queue.get())
        queue.task_done()
    return rows


def test_engine_constructor_wires_one_capture_to_existing_paths(tmp_path):
    observer = ResearchObserver(ResearchConfig(enabled=True))
    cfg = Config(paths=PathsConfig(db=str(tmp_path / "s.db"),
        journal_dir=str(tmp_path / "j"), log_dir=str(tmp_path / "l")))
    eng = Engine(cfg, paper=True, research_observer=observer,
                 research_session_id="wired-session")
    try:
        assert eng.research is not None
        assert eng.research.session_id == "wired-session"
        assert eng.md._research is eng.research
        assert eng.gateway._research is eng.research
        assert eng.user_proc._research is eng.research
    finally:
        eng.gateway.close()
        eng.state.close()
        eng.catalog.close()


def test_concurrent_sources_admit_one_strict_sequence():
    cap = capture()
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda n: cap.emit("SAFETY", "condition", {"n": n}), range(64)))
    rows = events(cap)
    assert [row.event_seq for row in rows] == list(range(1, 65))
    assert [row.available_at_us for row in rows] == sorted(row.available_at_us for row in rows)


def test_market_applied_events_keep_both_tokens_and_generation():
    cap = capture()
    svc = MarketDataService(research=cap)
    svc.set_markets([("0xcond", ["yes-tok", "no-tok"])])
    svc.connection_generation = 1
    svc._receipt_us = 101
    svc._dispatch(BOOK)
    svc._dispatch({**BOOK, "asset_id": "no-tok", "bids": [{"price": "0.40", "size": "9"}]})
    svc._dispatch(PRICE_CHANGE)
    svc._observe("CONNECTION", "", {"stream": "market", "state": "DOWN"})
    svc.connection_generation = 2
    svc._observe("CONNECTION", "", {"stream": "market", "state": "UP"})
    svc._dispatch({**BOOK, "asset_id": "no-tok"})
    rows = events(cap)
    assert [r.event_seq for r in rows] == list(range(1, len(rows) + 1))
    books = [r for r in rows if r.kind == "BOOK"]
    assert books[0].payload["token_id"] == "yes-tok"
    assert books[1].payload["token_id"] == "no-tok"
    assert books[1].payload["bids"][0][0] == 0.40
    assert books[-1].connection_generation == 2
    assert books[0].received_at_us == 101
    assert books[0].exchange_at_us == 1783270000000000
    assert books[0].available_at_us > books[0].received_at_us


@pytest.mark.parametrize("timestamp", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_source_time_cannot_block_applied_book_wake(timestamp):
    cap = capture()
    wakes = []
    svc = MarketDataService(on_dirty=lambda cid, tok: wakes.append((cid, tok)), research=cap)
    svc.set_markets([("0xcond", ["yes-tok", "no-tok"])])
    svc._dispatch({**BOOK, "timestamp": timestamp})
    assert wakes == [("0xcond", "yes-tok")]
    book = svc.book("yes-tok")
    assert book is not None and book.best_bid().price == .49
    row = next(r for r in events(cap) if r.kind == "BOOK")
    assert row.exchange_at_us is None
    assert row.available_at_us > 0


def test_late_fill_status_revisions_and_role(monkeypatch):
    cap = capture()
    import polymaker.research.capture as capture_module
    t0 = 1_000_000
    monkeypatch.setattr(capture_module, "clock", lambda: (t0 + 4_000_000, 9_000_000))
    store = StateStore(":memory:")
    try:
        proc = UserEventProcessor(store, research=cap)
        proc.register_order("first-order", "condition", "token", Side.BUY, 5, .27,
                            "favorite_first_leg")
        ev = TradeEvent("token", Side.BUY, .27, 5, "trade", TradeState.MATCHED, 1.0,
                        "first-order")
        proc.on_trade(ev, "condition", source="REST", received_at_us=t0 + 4_000_000)
        proc.on_trade(replace(ev, status=TradeState.MINED), "condition", source="REST")
        proc.on_trade(replace(ev, status=TradeState.CONFIRMED), "condition", source="REST")
        rows = [r for r in events(cap) if r.kind == "FILL_REVISION"]
        assert [r.payload["status"] for r in rows] == ["MATCHED", "MINED", "CONFIRMED"]
        assert all(r.payload["role"] == "FIRST" for r in rows)
        assert all(r.exchange_at_us == t0 for r in rows)
        assert all(r.available_at_us == t0 + 4_000_000 for r in rows)
        assert [r.event_seq for r in rows] == sorted(r.event_seq for r in rows)
        assert store.position("token").size == 5
        proc.register_order("pair-order", "condition", "other-token", Side.BUY, 5, .35,
                            "favorite_pair_leg")
        proc.on_trade(TradeEvent("other-token", Side.BUY, .35, 5, "pair-trade",
            TradeState.MATCHED, 2.0, "pair-order"), "condition", source="REST")
        pair = [r for r in events(cap) if r.kind == "FILL_REVISION"]
        assert len(pair) == 1 and pair[0].payload["role"] == "PAIR"
    finally:
        store.close()


def test_pre_ack_buffer_preserves_original_receipt_clock():
    cap = capture()
    store = StateStore(":memory:")
    try:
        proc = UserEventProcessor(store, research=cap)
        proc.arm_pending("token", Side.BUY)
        proc.on_trade(TradeEvent("token", Side.BUY, .27, 2, "early",
            TradeState.MATCHED, 1.0, "owned"), "condition",
            received_at_us=123, received_monotonic_ns=456,
            connection_generation=3)
        assert not any(r.kind == "FILL_REVISION" for r in events(cap))
        proc.register_order("owned", "condition", "token", Side.BUY, 5, .27,
                            "favorite_first_leg")
        fill = next(r for r in events(cap) if r.kind == "FILL_REVISION")
        assert fill.received_at_us == 123
        assert fill.payload["received_monotonic_ns"] == 456
        assert fill.connection_generation == 3
        assert fill.available_at_us > fill.received_at_us
    finally:
        store.close()


async def test_order_intent_ack_and_cancel_remain_separate(tmp_path, meta):
    cap = capture()
    cfg = Config(paths=PathsConfig(db=str(tmp_path / "s.db"),
        journal_dir=str(tmp_path / "j"), log_dir=str(tmp_path / "l")))
    gateway = ExecutionGateway(cfg, paper=True, research=cap)
    try:
        placed = await gateway.place([Quote(meta.yes.token_id, Side.BUY, .4, 5)], meta)
        assert placed
        class Client:
            def cancel_orders(self, _ids):
                return None

        async def local_io(fn):
            return fn()

        gateway._paper = False
        gateway._client = Client()
        gateway._io = local_io
        assert await gateway.cancel([placed[0].order_id])
        rows = events(cap)
        assert [r.kind for r in rows] == ["ORDER_SUBMIT", "ORDER_ACK", "CANCEL_REQUEST", "CANCEL_ACK"]
        assert rows[0].payload["quotes"][0]["token_id"] == meta.yes.token_id
        assert rows[1].payload["order_ids"] == (placed[0].order_id,)
        assert rows[0].payload["request_id"] == rows[1].payload["request_id"]
        assert rows[2].payload["request_id"] == rows[3].payload["request_id"]
        assert rows[0].payload["request_id"] != rows[2].payload["request_id"]
    finally:
        gateway.close()


async def test_live_submit_and_cancel_request_ids_cover_success_and_unknown(tmp_path, meta):
    cap = capture()
    cfg = Config(paths=PathsConfig(db=str(tmp_path / "s.db"),
        journal_dir=str(tmp_path / "j"), log_dir=str(tmp_path / "l")))
    gateway = ExecutionGateway(cfg, research=cap)
    class Client:
        fail_post = False
        fail_sign = False
        fail_cancel = False
        def create_order(self, *_args, **_kwargs):
            if self.fail_sign:
                raise RuntimeError("signing failed")
            return "signed"
        def post_orders(self, *_args, **_kwargs):
            if self.fail_post:
                raise RuntimeError("timeout")
            return {"success": True, "orderID": "venue-order"}
        def cancel_orders(self, _ids):
            if self.fail_cancel:
                raise RuntimeError("timeout")
    client = Client()
    gateway._client = client
    async def local_io(fn):
        return fn()
    gateway._io = local_io
    quote = Quote(meta.yes.token_id, Side.BUY, .4, 5)
    try:
        assert len(await gateway.place([quote], meta)) == 1
        client.fail_post = True
        assert await gateway.place([quote], meta) == []
        client.fail_post = False
        client.fail_sign = True
        assert await gateway.place([quote], meta) == []
        assert await gateway.cancel(["venue-order"])
        client.fail_cancel = True
        assert not await gateway.cancel(["venue-order"])
        rows = events(cap)
        assert [r.kind for r in rows] == ["ORDER_SUBMIT", "ORDER_ACK"] * 3 + [
            "CANCEL_REQUEST", "CANCEL_ACK"] * 2
        ids = [r.payload["request_id"] for r in rows]
        assert all(ids[i] == ids[i + 1] for i in range(0, 10, 2))
        assert len(set(ids[::2])) == 5
        assert [rows[i].payload["outcome"] for i in (1, 3, 5, 7, 9)] == [
            "ACK", "UNKNOWN", "FAILED", "ACK", "UNKNOWN"]
    finally:
        gateway.close()


async def test_rest_replacement_records_only_applied_levels(tmp_path, meta):
    eng = _engine_with_market(tmp_path, meta)
    cap = capture()
    eng.research = cap

    async def full_book(_token):
        return ([(.31, 7), (.30, 9)], [(.35, 4)], "rest-hash")

    eng.gateway.get_full_book = full_book
    try:
        token = meta.yes.token_id
        await eng._refresh_book(token)
        rows = events(cap)
        assert len(rows) == 1 and rows[0].kind == "REST_BOOK"
        assert rows[0].payload["bids"] == ((.31, 7), (.30, 9))
        assert rows[0].payload["book_hash"] == "rest-hash"
        assert rows[0].source == "REST" and rows[0].received_at_us is not None
        await eng._refresh_book(token)
        assert events(cap) == []
    finally:
        eng.gateway.close()
        eng.state.close()
        eng.catalog.close()


async def test_disabled_engine_path_parity_and_research_failure_isolation(tmp_path, meta):
    for name in ("left", "right", "enabled"):
        (tmp_path / name).mkdir()
    left = _engine_with_market(tmp_path / "left", meta)
    right = _engine_with_market(tmp_path / "right", meta)
    right.research = CausalCapture(ResearchObserver(), "disabled")
    enabled = _engine_with_market(tmp_path / "enabled", meta)
    cap = capture()
    enabled.research = cap
    enabled.gateway._research = cap
    enabled.user_proc._research = cap
    _feed_book(left, meta)
    _feed_book(right, meta)
    _feed_book(enabled, meta)
    try:
        await left._recompute(meta.condition_id)
        await right._recompute(meta.condition_id)
        assert [(o.token_id, o.side, o.price, o.size) for o in left.state.orders.values()] == [
            (o.token_id, o.side, o.price, o.size) for o in right.state.orders.values()]

        def broken(_record):
            raise RuntimeError("research failed")

        cap.observer.observe = broken
        await enabled._recompute(meta.condition_id)
        assert [(o.token_id, o.side, o.price, o.size) for o in enabled.state.orders.values()] == [
            (o.token_id, o.side, o.price, o.size) for o in left.state.orders.values()]
        assert cap.failures > 0
    finally:
        for eng in (left, right, enabled):
            eng.gateway.close()
            eng.state.close()
            eng.catalog.close()


async def test_favorite_decision_and_effective_quote_are_distinct(tmp_path, meta):
    eng = _engine_with_market(tmp_path, meta)
    cap = capture()
    eng.research = cap
    eng.user_proc._research = cap
    eng.gateway._research = cap
    cid = meta.condition_id
    now = time.time()
    eng.attach_favorite_first(cid, now - 40,
        FavoriteFirstConfig(max_pair_loss_usd=.50, favorite_confirm_s=0))
    yes = eng.md.book(meta.yes.token_id)
    no = eng.md.book(meta.no.token_id)
    assert yes is not None and no is not None
    yes.apply_snapshot([(.64, 20)], [(.66, 20)], now)
    no.apply_snapshot([(.34, 20)], [(.36, 20)], now)
    try:
        await eng._recompute(cid)
        rows = events(cap)
        baseline = [r for r in rows if r.kind == "BASELINE_DECISION"]
        assert len(baseline) >= 2
        assert "snapshot" in baseline[0].payload
        assert baseline[1].payload["phase"] == "ORDER_PLAN"
        assert baseline[1].payload["stage"] == "RECONCILED_PLAN"
        assert baseline[1].payload["quote"] == baseline[1].payload["to_place"][0]
        await eng._recompute(cid)
        second = [r for r in events(cap) if r.kind == "BASELINE_DECISION"]
        plan = next(r for r in second if r.payload.get("phase") == "ORDER_PLAN")
        assert plan.payload["target_quote"] is not None
        assert plan.payload["quote"] is None
        assert plan.payload["is_noop"] is True
        effective = next(r for r in second if r.payload.get("phase") == "EFFECTIVE_QUOTE")
        assert effective.payload["stage"] == "NOOP"
        assert effective.payload["quote"] is None
        assert any(r.kind == "SAFETY" for r in rows)
        assert cap.failures == 0
    finally:
        eng.gateway.close()
        eng.state.close()
        eng.catalog.close()


async def test_final_favorite_submit_guard_suppresses_planned_quote(tmp_path, meta):
    eng = _engine_with_market(tmp_path, meta)
    cap = capture()
    eng.research = cap
    eng.gateway._research = cap
    cid = meta.condition_id
    now = time.time()
    eng.attach_favorite_first(cid, now - 40,
        FavoriteFirstConfig(max_pair_loss_usd=.50, favorite_confirm_s=0))
    yes = eng.md.book(meta.yes.token_id)
    no = eng.md.book(meta.no.token_id)
    assert yes is not None and no is not None
    yes.apply_snapshot([(.64, 20)], [(.66, 20)], now)
    no.apply_snapshot([(.34, 20)], [(.36, 20)], now)
    eng._favorite_submit_ok = lambda *_args: False
    try:
        await eng._recompute(cid)
        rows = events(cap)
        plan = next(r for r in rows if r.kind == "BASELINE_DECISION"
                    and r.payload.get("phase") == "ORDER_PLAN")
        effective = next(r for r in rows if r.kind == "BASELINE_DECISION"
                         and r.payload.get("phase") == "EFFECTIVE_QUOTE")
        assert plan.payload["quote"] is not None
        assert effective.payload["stage"] == "PLACEMENT_RESULT"
        assert effective.payload["quote"] is None
        assert eng.state.orders == {}
        assert not any(r.kind == "ORDER_SUBMIT" for r in rows)
    finally:
        eng.gateway.close()
        eng.state.close()
        eng.catalog.close()


async def test_research_order_snapshot_failure_does_not_interrupt_rest_resync(tmp_path, meta):
    eng = _engine_with_market(tmp_path, meta)
    cap = capture()
    eng.research = cap
    from polymaker.domain import OpenOrder, OrderState
    order = OpenOrder("owned", meta.yes.token_id, Side.BUY, .2, 5, OrderState.LIVE)
    async def open_orders():
        return [order]
    eng.gateway.open_orders = open_orders
    original_owned = eng.state.owned_order
    def broken_owned(_oid):
        raise RuntimeError("Research lookup failed")
    eng.state.owned_order = broken_owned
    try:
        await eng._refresh_token_orders(meta)
        assert eng.state.orders_for(meta.yes.token_id) == [order]
        assert eng.state.orders_for(meta.no.token_id) == []
        assert cap.failures >= 1
    finally:
        eng.state.owned_order = original_owned
        eng.gateway.close()
        eng.state.close()
        eng.catalog.close()


def test_failed_and_quarantined_revisions_use_economic_truth_and_current_readiness():
    cap = capture()
    store = StateStore(":memory:")
    ready = False
    proc = UserEventProcessor(store, research=cap, fill_truth_ready=lambda _cid: ready)
    try:
        proc.register_order("owned", "condition", "token", Side.BUY, 5, .2,
                            "favorite_first_leg")
        matched = TradeEvent("token", Side.BUY, .2, 1, "trade", TradeState.MATCHED, 1,
                             "owned")
        proc.on_trade(matched, "condition")
        ready = True
        proc.on_trade(replace(matched, status=TradeState.MINED), "condition")
        proc.on_trade(replace(matched, status=TradeState.CONFIRMED), "condition")
        ready = False
        proc.on_trade(replace(matched, status=TradeState.FAILED), "condition")
        rows = [r for r in events(cap) if r.kind == "FILL_REVISION"]
        assert [r.payload["status"] for r in rows] == ["MATCHED", "MINED", "CONFIRMED", "FAILED"]
        assert [r.payload["fill_truth_ready"] for r in rows] == [False, True, True, False]
        assert [r.payload["accepted"] for r in rows] == [True, True, True, False]
        assert rows[-1].payload["quarantined"] is True
        assert proc.economic_fills_for_order("owned") == []
        proc.on_trade(TradeEvent("token", Side.BUY, .3, 1, "bad", TradeState.MATCHED,
            2, "owned"), "condition")
        quarantined = [r for r in events(cap) if r.kind == "FILL_REVISION"]
        assert len(quarantined) == 1
        assert quarantined[0].payload["accepted"] is False
        assert quarantined[0].payload["quarantined"] is True
    finally:
        store.close()
