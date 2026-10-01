"""Tests for StateStore, the user-event tracker, and the reconciler."""

from __future__ import annotations

from polymaker.domain import (
    Fill,
    OpenOrder,
    OrderState,
    Quote,
    Regime,
    Side,
    TargetQuotes,
    TradeState,
)
from polymaker.execution.reconciler import reconcile
from polymaker.state.store import StateStore
from polymaker.state.tracker import OrderEvent, TradeEvent, UserEventProcessor

# ── StateStore ──────────────────────────────────────────────────────────────


def test_apply_fill_updates_size_and_avg(tmp_path):
    s = StateStore(tmp_path / "s.db")
    s.apply_fill(Fill("tok", Side.BUY, 0.50, 100, "t1"))
    assert s.position("tok").size == 100
    assert s.position("tok").avg_price == 0.50
    # buy more at a higher price -> weighted avg
    s.apply_fill(Fill("tok", Side.BUY, 0.60, 100, "t2"))
    assert s.position("tok").size == 200
    assert abs(s.position("tok").avg_price - 0.55) < 1e-9
    # sell reduces size, avg unchanged
    s.apply_fill(Fill("tok", Side.SELL, 0.70, 50, "t3"))
    assert s.position("tok").size == 150
    assert abs(s.position("tok").avg_price - 0.55) < 1e-9
    s.close()


def test_sell_to_flat_resets_avg(tmp_path):
    s = StateStore(tmp_path / "s.db")
    s.apply_fill(Fill("tok", Side.BUY, 0.5, 100, "t1"))
    s.apply_fill(Fill("tok", Side.SELL, 0.6, 100, "t2"))
    assert s.position("tok").size == 0
    assert s.position("tok").avg_price == 0.0
    s.close()


def test_reconcile_positions_skips_inflight_and_recent(tmp_path):
    s = StateStore(tmp_path / "s.db")
    s.mark_inflight("tok")
    s.reconcile_positions({"tok": (999.0, 0.9)})  # ignored: in-flight
    assert s.position("tok").size == 0
    s.clear_inflight("tok")
    # still recent fill guard: simulate no recent fill by using a fresh token
    s.reconcile_positions({"other": (42.0, 0.3)})
    assert s.position("other").size == 42.0
    s.close()


def test_recovery_selection_never_caps_unresolved_or_active(tmp_path):
    s = StateStore(tmp_path / "recovery.db")
    try:
        for i in range(600):
            s.register_owned_order(f"unresolved-{i}", "cid", "tok", Side.BUY, 1, .2, "test")
        rows = s.owned_orders_for_recovery(since_ts=10**12, active_ids=set(), condition_ids={"cid"})
        assert len(rows) == 600
    finally:
        s.close()


def test_recovery_selection_caps_only_recent_resolved_and_keeps_active(tmp_path):
    import time
    s = StateStore(tmp_path / "recovery.db")
    try:
        for i in range(600):
            oid = f"recent-{i}"
            s.register_owned_order(oid, "cid", "tok", Side.BUY, 1, .2, "test")
            s.mark_owned_order_unresolved(oid, False)
        s.register_owned_order("active-old", "cid", "tok", Side.BUY, 1, .2, "test")
        s.mark_owned_order_unresolved("active-old", False)
        s._conn.execute("UPDATE owned_orders SET created_ts=0 WHERE order_id='active-old'")
        rows = s.owned_orders_for_recovery(since_ts=time.time()-10,
            active_ids={"active-old"}, condition_ids={"cid"}, limit=500)
        ids = {str(r["order_id"]) for r in rows}
        assert len(ids) == 501
        assert "active-old" in ids
    finally:
        s.close()


def test_state_persists_across_restart(tmp_path):
    db = tmp_path / "s.db"
    s = StateStore(db)
    s.apply_fill(Fill("tok", Side.BUY, 0.5, 100, "t1"))
    s.close()
    s2 = StateStore(db)
    assert s2.position("tok").size == 100
    s2.close()


# ── UserEventProcessor ───────────────────────────────────────────────────────


def test_matched_then_confirmed(tmp_path):
    s = StateStore(tmp_path / "s.db")
    changed: list[str] = []
    p = UserEventProcessor(s, on_change=changed.append)
    p.register_order("o1", "cid", "tok", Side.BUY, 100, .5)
    p.on_trade(TradeEvent("tok", Side.BUY, 0.5, 100, "trade1", TradeState.MATCHED, 1.0, "o1"), "cid")
    assert s.position("tok").size == 100
    assert s.inflight("tok") == 1
    p.on_trade(TradeEvent("tok", Side.BUY, 0.5, 100, "trade1", TradeState.CONFIRMED, 2.0, "o1"), "cid")
    assert s.inflight("tok") == 0
    assert s.position("tok").size == 100  # settled
    assert changed == ["cid", "cid"]
    s.close()


def test_matched_is_idempotent(tmp_path):
    s = StateStore(tmp_path / "s.db")
    p = UserEventProcessor(s)
    p.register_order("o1", "cid", "tok", Side.BUY, 100, .5)
    ev = TradeEvent("tok", Side.BUY, 0.5, 100, "trade1", TradeState.MATCHED, 1.0, "o1")
    p.on_trade(ev, "cid")
    p.on_trade(ev, "cid")  # duplicate MATCHED for same trade id
    assert s.position("tok").size == 100  # not doubled
    s.close()


def test_failed_trade_quarantines_without_blind_reversal(tmp_path):
    s = StateStore(tmp_path / "s.db")
    p = UserEventProcessor(s)
    p.register_order("o1", "cid", "tok", Side.BUY, 100, .5)
    p.on_trade(TradeEvent("tok", Side.BUY, 0.5, 100, "trade1", TradeState.MATCHED, 1.0, "o1"), "cid")
    assert s.position("tok").size == 100
    p.on_trade(TradeEvent("tok", Side.BUY, 0.5, 100, "trade1", TradeState.FAILED, 2.0, "o1"), "cid")
    assert s.position("tok").size == 100
    assert s.inflight("tok") == 1
    assert "o1" in p.quarantined_orders
    s.close()


def test_order_event_upsert_and_cancel(tmp_path):
    s = StateStore(tmp_path / "s.db")
    p = UserEventProcessor(s)
    p.register_order("o1", "cid", "tok", Side.BUY, 100, .49)
    p.on_order(OrderEvent("o1", "tok", Side.BUY, 0.49, 100, original_size=100,
                          size_matched=0), "cid")
    assert len(s.orders_for("tok")) == 1
    p.on_order(OrderEvent("o1", "tok", Side.BUY, 0.49, 0, is_cancel=True,
                          original_size=100, size_matched=100), "cid")
    assert len(s.orders_for("tok")) == 0
    s.close()


# ── reconciler ───────────────────────────────────────────────────────────────


def _live(order_id, token, side, price, size):
    return OpenOrder(order_id, token, side, price, size, OrderState.LIVE)


def test_reconcile_places_when_no_live():
    tq = TargetQuotes("cid", Regime.QUIET, (Quote("tok", Side.BUY, 0.49, 100),))
    plan = reconcile(tq, [], tick=0.01, reprice_ticks=2, resize_frac=0.15)
    assert len(plan.to_place) == 1
    assert plan.to_cancel == []


def test_reconcile_keeps_close_order():
    tq = TargetQuotes("cid", Regime.QUIET, (Quote("tok", Side.BUY, 0.49, 100),))
    live = [_live("o1", "tok", Side.BUY, 0.49, 102)]  # within tolerances
    plan = reconcile(tq, live, tick=0.01, reprice_ticks=2, resize_frac=0.15)
    assert plan.is_noop


def test_reconcile_reprices_when_far():
    tq = TargetQuotes("cid", Regime.QUIET, (Quote("tok", Side.BUY, 0.45, 100),))
    live = [_live("o1", "tok", Side.BUY, 0.49, 100)]  # 4 ticks away > 2
    plan = reconcile(tq, live, tick=0.01, reprice_ticks=2, resize_frac=0.15)
    assert plan.to_cancel == ["o1"]
    assert len(plan.to_place) == 1


def test_reconcile_resizes_when_size_drifts():
    tq = TargetQuotes("cid", Regime.QUIET, (Quote("tok", Side.BUY, 0.49, 100),))
    live = [_live("o1", "tok", Side.BUY, 0.49, 50)]  # 50% smaller > 15%
    plan = reconcile(tq, live, tick=0.01, reprice_ticks=2, resize_frac=0.15)
    assert plan.to_cancel == ["o1"]
    assert len(plan.to_place) == 1


def test_reconcile_cancels_all_when_target_empty():
    tq = TargetQuotes("cid", Regime.EVENT, ())
    live = [_live("o1", "tok", Side.BUY, 0.49, 100), _live("o2", "tok", Side.SELL, 0.55, 50)]
    plan = reconcile(tq, live, tick=0.01, reprice_ticks=2, resize_frac=0.15)
    assert set(plan.to_cancel) == {"o1", "o2"}
    assert plan.to_place == []


def test_reconcile_matches_layers_one_to_one():
    tq = TargetQuotes("cid", Regime.QUIET, (
        Quote("tok", Side.BUY, 0.49, 100),
        Quote("tok", Side.BUY, 0.47, 100),
    ))
    live = [_live("o1", "tok", Side.BUY, 0.49, 100)]  # only the top layer exists
    plan = reconcile(tq, live, tick=0.01, reprice_ticks=2, resize_frac=0.15)
    assert plan.to_cancel == []
    assert len(plan.to_place) == 1  # only the missing deeper layer
    assert plan.to_place[0].price == 0.47
