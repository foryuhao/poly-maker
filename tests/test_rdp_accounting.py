from __future__ import annotations

import pytest

from polymaker.domain import MarketMeta, OpenOrder, OrderState, Side, TokenMeta, TradeState
from polymaker.execution.filltruth import ReconciliationOutcome, reconcile_owned_order
from polymaker.state.store import StateStore
from polymaker.state.tracker import TradeEvent, UserEventProcessor
from polymaker.strategy.rdp_accounting import snapshot_inventory


def market(condition="cid", yes="yes", no="no"):
    return MarketMeta(condition, "BTC up", "btc-up", (TokenMeta(yes, "Yes"), TokenMeta(no, "No")),
        .01, False, 5, 0, 0, 0, 0, 0, False, None, None)


def add_fill(proc, order_id, token, qty, price, trade_id=None):
    proc.on_trade(TradeEvent(token, Side.BUY, price, qty, trade_id or order_id,
        TradeState.MATCHED, 1.0, order_id), "cid")
    proc.on_trade(TradeEvent(token, Side.BUY, price, qty, trade_id or order_id,
        TradeState.CONFIRMED, 2.0, order_id), "cid")


def setup(tmp_path):
    store = StateStore(tmp_path / "rdp.db")
    return store, UserEventProcessor(store), market()


def test_empty_snapshot_has_zero_economics(tmp_path):
    store, proc, m = setup(tmp_path)
    try:
        s = snapshot_inventory(m, proc, set())
        assert (s.qy, s.qn, s.cy, s.cn, s.inventory_cost) == (0, 0, 0, 0, 0)
        assert (s.avg_yes_cost, s.avg_no_cost) == (None, None)
        assert (s.balanced_qty, s.residual_qty, s.locked_pnl,
                s.worst_settlement_pnl, s.best_settlement_pnl) == (0, 0, 0, 0, 0)
    finally:
        store.close()


@pytest.mark.parametrize("token,expected_delta", [("yes", 4), ("no", -4)])
def test_one_sided_inventory_and_unavailable_opposite_average(tmp_path, token, expected_delta):
    store, proc, m = setup(tmp_path)
    try:
        proc.register_order("o", "cid", token, Side.BUY, 4, .4)
        add_fill(proc, "o", token, 4, .4)
        s = snapshot_inventory(m, proc, {"o"})
        assert s.delta == expected_delta
        assert s.balanced_qty == 0 and s.balanced_cost == 0
        assert s.residual_qty == 4 and s.residual_cost == pytest.approx(1.6)
        assert s.worst_settlement_pnl == pytest.approx(-1.6)
        assert s.best_settlement_pnl == pytest.approx(2.4)
        assert (s.avg_no_cost if token == "yes" else s.avg_yes_cost) is None
    finally:
        store.close()


def test_fixed_residual_fixture_uses_actual_fill_prices_and_winner(tmp_path):
    store, proc, m = setup(tmp_path)
    try:
        for oid, token, qty, price in (("y1", "yes", 2, .2), ("y2", "yes", 3, .4),
                                       ("n1", "no", 3, .5)):
            proc.register_order(oid, "cid", token, Side.BUY, qty, .8)
            add_fill(proc, oid, token, qty, price)
        s = snapshot_inventory(m, proc, {"y1", "y2", "n1"})
        assert (s.qy, s.qn, s.cy, s.cn) == (5, 3, pytest.approx(1.6), pytest.approx(1.5))
        assert s.delta == 2 and s.balanced_qty == 3
        assert s.avg_yes_cost == pytest.approx(.32) and s.avg_no_cost == pytest.approx(.5)
        assert s.balanced_cost == pytest.approx(2.46)
        assert s.residual_cost == pytest.approx(.64)
        assert s.locked_pnl == pytest.approx(.54)
        assert s.worst_settlement_pnl == pytest.approx(-.1)
        assert s.best_settlement_pnl == pytest.approx(1.9)
        assert s.market_pnl("yes") == pytest.approx(1.9)
        assert s.market_pnl("no") == pytest.approx(-.1)
        with pytest.raises(ValueError, match="outside this market"):
            s.market_pnl("other")
    finally:
        store.close()


def test_negative_locked_pnl_is_retained_and_balanced(tmp_path):
    store, proc, m = setup(tmp_path)
    try:
        for oid, token in (("y", "yes"), ("n", "no")):
            proc.register_order(oid, "cid", token, Side.BUY, 2, .8)
            add_fill(proc, oid, token, 2, .8)
        s = snapshot_inventory(m, proc, {"y", "n"})
        assert s.locked_pnl == pytest.approx(-1.2)
        assert s.worst_settlement_pnl == pytest.approx(-1.2)
    finally:
        store.close()


def test_profitable_balanced_inventory_has_locked_settlement_value(tmp_path):
    store, proc, m = setup(tmp_path)
    try:
        for oid, token, price in (("yes", "yes", .2), ("no", "no", .3)):
            proc.register_order(oid, "cid", token, Side.BUY, 4, .8)
            add_fill(proc, oid, token, 4, price)
        s = snapshot_inventory(m, proc, {"yes", "no"})
        assert (s.qy, s.qn, s.cy, s.cn) == (4, 4, pytest.approx(.8), pytest.approx(1.2))
        assert s.delta == 0 and s.balanced_qty == 4
        assert s.balanced_yes_cost == pytest.approx(.8)
        assert s.balanced_no_cost == pytest.approx(1.2)
        assert s.balanced_cost == pytest.approx(2)
        assert s.residual_qty == 0 and s.residual_cost == pytest.approx(0)
        assert s.locked_pnl == pytest.approx(2)
        assert s.worst_settlement_pnl == pytest.approx(2)
        assert s.best_settlement_pnl == pytest.approx(2)
        assert s.market_pnl("yes") == pytest.approx(2)
        assert s.market_pnl("no") == pytest.approx(2)
    finally:
        store.close()


def test_mirrored_residual_no_fixture(tmp_path):
    store, proc, m = setup(tmp_path)
    try:
        for oid, token, qty, price in (("y1", "yes", 3, .5), ("n1", "no", 2, .2),
                                       ("n2", "no", 3, .4)):
            proc.register_order(oid, "cid", token, Side.BUY, qty, .8)
            add_fill(proc, oid, token, qty, price)
        s = snapshot_inventory(m, proc, {"y1", "n1", "n2"})
        assert (s.qy, s.qn, s.cy, s.cn) == (3, 5, pytest.approx(1.5), pytest.approx(1.6))
        assert s.delta == -2 and s.balanced_qty == 3
        assert s.residual_yes == 0 and s.residual_no == 2 and s.residual_qty == 2
        assert s.avg_yes_cost == pytest.approx(.5) and s.avg_no_cost == pytest.approx(.32)
        assert s.balanced_yes_cost == pytest.approx(1.5)
        assert s.balanced_no_cost == pytest.approx(.96)
        assert s.balanced_cost == pytest.approx(2.46)
        assert s.residual_cost == pytest.approx(.64)
        assert s.locked_pnl == pytest.approx(.54)
        assert s.residual_worst_loss == pytest.approx(.64)
        assert s.residual_best_pnl == pytest.approx(1.36)
        assert s.worst_settlement_pnl == pytest.approx(-.1)
        assert s.best_settlement_pnl == pytest.approx(1.9)
        assert s.market_pnl("yes") == pytest.approx(-.1)
        assert s.market_pnl("no") == pytest.approx(1.9)
    finally:
        store.close()


def test_fractional_inventory_and_multiple_prices_are_preserved(tmp_path):
    store, proc, m = setup(tmp_path)
    try:
        for oid, qty, price in (("y1", .125, .23), ("y2", .375, .41), ("n1", .25, .52)):
            token = "no" if oid == "n1" else "yes"
            proc.register_order(oid, "cid", token, Side.BUY, qty, .8)
            add_fill(proc, oid, token, qty, price)
        s = snapshot_inventory(m, proc, {"y1", "y2", "n1"})
        assert s.qy == pytest.approx(.5) and s.qn == pytest.approx(.25)
        assert s.cy == pytest.approx(.1825) and s.cn == pytest.approx(.13)
        assert s.avg_yes_cost == pytest.approx(.365)
    finally:
        store.close()


def test_matched_is_excluded_until_confirmed_then_replay_counts_once(tmp_path):
    store, proc, m = setup(tmp_path)
    changes = []
    proc._on_change = changes.append
    try:
        proc.register_order("o", "cid", "yes", Side.BUY, 5, .5)
        matched = TradeEvent("yes", Side.BUY, .4, 5, "t", TradeState.MATCHED, 1, "o")
        proc.on_trade(matched, "cid")
        before = snapshot_inventory(m, proc, {"o"})
        assert (before.qy, before.cy) == (0, 0)
        assert store.position("yes").size == 5
        confirmed = TradeEvent("yes", Side.BUY, .4, 5, "t", TradeState.CONFIRMED, 2, "o")
        proc.on_trade(confirmed, "cid")
        proc.on_trade(matched, "cid")  # older replay cannot lower status
        proc.on_trade(confirmed, "cid")
        callback_count = len(changes)
        after = snapshot_inventory(m, proc, {"o"})
        assert (after.qy, after.cy) == (5, 2)
        assert snapshot_inventory(m, proc, {"o"}) == after
        assert len(changes) == callback_count
    finally:
        store.close()


@pytest.mark.parametrize("intermediate", [TradeState.MINED, TradeState.RETRYING])
def test_mined_and_retrying_evidence_wait_for_confirmation(tmp_path, intermediate):
    store, proc, m = setup(tmp_path)
    try:
        proc.register_order("o", "cid", "yes", Side.BUY, 3, .5)
        proc.on_trade(TradeEvent("yes", Side.BUY, .4, 3, "t", TradeState.MATCHED, 1, "o"), "cid")
        matched = snapshot_inventory(m, proc, {"o"})
        assert (matched.qy, matched.cy) == (0, 0)
        proc.on_trade(TradeEvent("yes", Side.BUY, .4, 3, "t", intermediate, 2, "o"), "cid")
        pending = snapshot_inventory(m, proc, {"o"})
        assert (pending.qy, pending.cy, pending.inventory_cost) == (0, 0, 0)
        proc.on_trade(TradeEvent("yes", Side.BUY, .4, 3, "t", TradeState.CONFIRMED, 3, "o"), "cid")
        confirmed = snapshot_inventory(m, proc, {"o"})
        assert confirmed.qy == 3
        assert confirmed.cy == pytest.approx(1.2)
        assert confirmed.inventory_cost == pytest.approx(1.2)
    finally:
        store.close()


def test_unadmitted_confirmed_only_event_is_not_synthesized(tmp_path):
    store, proc, m = setup(tmp_path)
    try:
        proc.register_order("o", "cid", "yes", Side.BUY, 2, .5)
        proc.on_trade(TradeEvent("yes", Side.BUY, .3, 2, "t", TradeState.CONFIRMED, 2, "o"), "cid")
        s = snapshot_inventory(m, proc, {"o"})
        assert s.qy == 0 and s.cy == 0
    finally:
        store.close()


def test_distinct_maker_orders_with_same_trade_id_both_count(tmp_path):
    store, proc, m = setup(tmp_path)
    try:
        for oid in ("a", "b"):
            proc.register_order(oid, "cid", "yes", Side.BUY, 2, .5)
            add_fill(proc, oid, "yes", 1, .3, trade_id="same")
        s = snapshot_inventory(m, proc, {"a", "b"})
        assert s.qy == 2 and s.cy == pytest.approx(.6)
    finally:
        store.close()


def test_explicit_scope_excludes_other_registered_orders_and_positions(tmp_path):
    store, proc, m = setup(tmp_path)
    try:
        proc.register_order("ours", "cid", "yes", Side.BUY, 2, .5)
        proc.register_order("other", "cid", "no", Side.BUY, 2, .5)
        add_fill(proc, "ours", "yes", 1, .3)
        add_fill(proc, "other", "no", 2, .4)
        store.upsert_order(OpenOrder("resting", "yes", Side.BUY, .2, 1, OrderState.LIVE))
        before = snapshot_inventory(m, proc, {"ours"})
        orders_before = tuple(sorted(
            (order_id, order.token_id, order.side.value, order.price, order.size,
             order.state.value, order.created_ts)
            for order_id, order in store.orders.items()))
        store.set_position("yes", 999, .99)
        store.set_position("no", 77, .8)
        after = snapshot_inventory(m, proc, {"ours"})
        assert before == after
        orders_after = tuple(sorted(
            (order_id, order.token_id, order.side.value, order.price, order.size,
             order.state.value, order.created_ts)
            for order_id, order in store.orders.items()))
        assert orders_after == orders_before
        assert after.qn == 0 and after.qy == 1
        with pytest.raises(ValueError, match="another condition"):
            snapshot_inventory(market(condition="else"), proc, {"ours"})
        proc.register_order("foreign-token", "cid", "third", Side.BUY, 1, .2)
        with pytest.raises(ValueError, match="token is outside"):
            snapshot_inventory(m, proc, {"foreign-token"})
    finally:
        store.close()


def test_invalid_scopes_failed_and_quarantined_evidence_reject(tmp_path):
    store, proc, m = setup(tmp_path)
    try:
        with pytest.raises(ValueError, match="unknown owned order"):
            snapshot_inventory(m, proc, {"unknown"})
        proc.register_order("sell", "cid", "yes", Side.SELL, 2, .5)
        with pytest.raises(ValueError, match="non-BUY"):
            snapshot_inventory(m, proc, {"sell"})
        proc.register_order("bad", "cid", "yes", Side.BUY, 2, .5)
        proc.on_trade(TradeEvent("yes", Side.BUY, .3, 1, "bad-fill", TradeState.FAILED, 1, "bad"), "cid")
        with pytest.raises(ValueError, match="failed or quarantined"):
            snapshot_inventory(m, proc, {"bad"})
        proc.register_order("contradictory", "cid", "yes", Side.BUY, 2, .5)
        proc.on_trade(TradeEvent("yes", Side.BUY, .8, 1, "wrong-price", TradeState.MATCHED,
                                 1, "contradictory"), "cid")
        with pytest.raises(ValueError, match="failed or quarantined"):
            snapshot_inventory(m, proc, {"contradictory"})
    finally:
        store.close()


@pytest.mark.parametrize("qty,price", [(1, 0.0), (1, -.1), (True, .2)])
def test_admitted_invalid_confirmed_economics_reject_in_projection(tmp_path, qty, price):
    store, proc, m = setup(tmp_path)
    try:
        proc.register_order("o", "cid", "yes", Side.BUY, 2, .9)
        proc.on_trade(TradeEvent("yes", Side.BUY, price, qty, "t", TradeState.MATCHED, 1, "o"), "cid")
        proc.on_trade(TradeEvent("yes", Side.BUY, price, qty, "t", TradeState.CONFIRMED, 2, "o"), "cid")
        assert ("t", "o") in proc.applied_fill_keys
        assert ("t", "o") not in proc.quarantined_fill_keys
        assert "o" not in proc.quarantined_orders
        with pytest.raises(ValueError):
            snapshot_inventory(m, proc, {"o"})
    finally:
        store.close()


@pytest.mark.parametrize("qty,price", [(float("nan"), .2), (1, float("inf")), (1, 1.0)])
def test_tracker_quarantined_invalid_economics_reject(tmp_path, qty, price):
    store, proc, m = setup(tmp_path)
    try:
        proc.register_order("o", "cid", "yes", Side.BUY, 2, .9)
        proc.on_trade(TradeEvent("yes", Side.BUY, price, qty, "t", TradeState.MATCHED, 1, "o"), "cid")
        proc.on_trade(TradeEvent("yes", Side.BUY, price, qty, "t", TradeState.CONFIRMED, 2, "o"), "cid")
        with pytest.raises(ValueError, match="failed or quarantined"):
            snapshot_inventory(m, proc, {"o"})
    finally:
        store.close()


def test_snapshot_reads_leave_authoritative_state_and_callbacks_unchanged(tmp_path):
    store = StateStore(tmp_path / "rdp.db")
    m = market()
    callbacks = {name: [] for name in ("change", "fill", "reconcile", "maker_fill")}
    proc = UserEventProcessor(store,
        on_change=callbacks["change"].append,
        on_fill=callbacks["fill"].append,
        on_reconcile=callbacks["reconcile"].append,
        on_maker_fill=callbacks["maker_fill"].append)

    def capture():
        return {
            "positions": {key: (value.token_id, value.size, value.avg_price)
                          for key, value in store.positions.items()},
            "orders": tuple(sorted(
                (order_id, order.token_id, order.side.value, order.price, order.size,
                 order.state.value, order.created_ts)
                for order_id, order in store.orders.items())),
            "registry": tuple(sorted(tuple(sorted(row.items())) for row in store.owned_orders())),
            "fill_records": dict(proc.fill_records),
            "applied": set(proc.applied_fill_keys),
            "quarantined_fill_keys": set(proc.quarantined_fill_keys),
            "quarantined_orders": set(proc.quarantined_orders),
            "callbacks": {key: list(values) for key, values in callbacks.items()},
        }

    try:
        proc.register_order("o", "cid", "yes", Side.BUY, 3, .5)
        add_fill(proc, "o", "yes", 2, .3)
        store.upsert_order(OpenOrder("resting", "yes", Side.BUY, .2, 1, OrderState.LIVE))
        before = capture()
        expected = snapshot_inventory(m, proc, {"o"})
        assert snapshot_inventory(m, proc, {"o"}) == expected
        assert capture() == before
        with pytest.raises(ValueError, match="unknown owned order"):
            snapshot_inventory(m, proc, {"unknown"})
        assert capture() == before
    finally:
        store.close()


@pytest.mark.asyncio
async def test_exact_confirmed_rest_recovery_contributes_once(tmp_path):
    store, proc, m = setup(tmp_path)

    class Gateway:
        async def get_order(self, order_id):
            return {"id": order_id, "asset_id": "yes", "side": "BUY", "price": ".5",
                "original_size": "2", "size_matched": "2", "status": "MATCHED"}

        async def get_trades(self, *, market=None, asset_id=None):
            return [{"id": "rest-fill", "status": "CONFIRMED", "market": "cid",
                "asset_id": "no", "side": "SELL", "maker_orders": [{"order_id": "o",
                    "asset_id": "yes", "side": "BUY", "matched_amount": "2", "price": ".3"}]}]

    try:
        proc.register_order("o", "cid", "yes", Side.BUY, 2, .5)
        first = await reconcile_owned_order("o", Gateway(), proc, retry_delays=())
        assert first is ReconciliationOutcome.COMPLETE
        one = snapshot_inventory(m, proc, {"o"})
        second = await reconcile_owned_order("o", Gateway(), proc, retry_delays=())
        two = snapshot_inventory(m, proc, {"o"})
        assert second is ReconciliationOutcome.COMPLETE
        assert (one.qy, one.cy) == (2, pytest.approx(.6))
        assert two == one
    finally:
        store.close()
