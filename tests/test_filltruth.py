from __future__ import annotations

import asyncio

import pytest

import polymaker.execution.filltruth as filltruth_module
from polymaker.domain import Side, TradeState
from polymaker.execution.filltruth import (
    ReconciliationOutcome, reconcile_owned_order, reconcile_owned_order_once,
)
from polymaker.state.store import StateStore
from polymaker.state.tracker import OrderEvent, TradeEvent, UserEventProcessor
from polymaker.userstream.parse import MalformedMakerLeg, normalize_order, parse_trade


def _trade(status="MATCHED", order="ours", amount="5"):
    return {"id": "trade-x", "status": status, "market": "condition",
        "asset_id": "taker-token", "side": "SELL", "maker_orders": [
            {"order_id": order, "asset_id": "maker-token", "side": "BUY",
             "matched_amount": amount, "price": "0.27"}]}


def test_current_v2_maker_legs_override_taker_fields_and_support_multiple():
    parsed = parse_trade({**_trade(), "maker_orders": [
        _trade()["maker_orders"][0],
        {"order_id": "ours-2", "asset_id": "other-token", "side": "SELL",
         "matched_amount": "2", "price": "0.41"}]})
    assert (parsed.maker_legs[0].side, parsed.maker_legs[0].asset_id,
            parsed.maker_legs[0].matched_amount, parsed.maker_legs[0].price) == (
                Side.BUY, "maker-token", 5, .27)
    assert len(parsed.maker_legs) == 2
    assert parse_trade({**_trade(), "maker_orders": []}).maker_legs == ()


@pytest.mark.parametrize("leg", [
    {"asset_id": "token", "side": "BUY", "matched_amount": "1", "price": ".2"},
    {"order_id": "o", "asset_id": "token", "side": "BUY", "matched_amount": "bad", "price": ".2"},
])
def test_malformed_maker_legs_are_not_normalized(leg):
    with pytest.raises(MalformedMakerLeg):
        parse_trade({"id": "t", "status": "MATCHED", "maker_orders": [leg]})


class FakeGateway:
    def __init__(self, *, fail_trades=False, status="MATCHED"):
        self.fail_trades = fail_trades
        self.status = status

    async def get_order(self, order_id):
        return {"id": order_id, "asset_id": "maker-token", "side": "BUY",
            "price": ".27", "original_size": "5", "size_matched": "5", "status": "MATCHED"}

    async def get_trades(self, *, market=None, asset_id=None):
        if self.fail_trades:
            raise RuntimeError("read failed")
        return [_trade(order="ours", status=self.status), _trade(order="foreign")]


@pytest.mark.asyncio
async def test_targeted_rest_recovery_filters_exact_order_and_deduplicates():
    store = StateStore(":memory:")
    try:
        proc = UserEventProcessor(store)
        proc.register_order("ours", "condition", "maker-token", Side.BUY, 5, .27, "filltest")
        proc.on_order  # checksum path is exercised by reconciliation below
        assert await reconcile_owned_order("ours", FakeGateway(), proc) is ReconciliationOutcome.COMPLETE
        assert store.position("maker-token").size == 5
        assert await reconcile_owned_order("ours", FakeGateway(), proc) is ReconciliationOutcome.COMPLETE
        proc.on_trade(TradeEvent("maker-token", Side.BUY, .27, 5, "trade-x",
            TradeState.MATCHED, 1, "ours"), "condition")
        assert store.position("maker-token").size == 5
        assert proc.tracked_trade_qty("ours") == 5
    finally:
        store.close()


@pytest.mark.asyncio
async def test_rest_read_failure_keeps_order_quarantined():
    store = StateStore(":memory:")
    try:
        proc = UserEventProcessor(store)
        proc.register_order("ours", "condition", "maker-token", Side.BUY, 5, .27)
        assert await reconcile_owned_order("ours", FakeGateway(fail_trades=True), proc) is ReconciliationOutcome.UNRESOLVED
        assert "ours" in proc.quarantined_orders
        assert store.position("maker-token").size == 0
    finally:
        store.close()


@pytest.mark.asyncio
async def test_failed_ws_lifecycle_stays_quarantined_until_rest_confirms():
    store = StateStore(":memory:")
    try:
        proc = UserEventProcessor(store)
        proc.register_order("ours", "condition", "maker-token", Side.BUY, 5, .27)
        proc.on_trade(TradeEvent("maker-token", Side.BUY, .27, 5, "trade-x",
            TradeState.FAILED, 1, "ours"), "condition")
        assert await reconcile_owned_order("ours", FakeGateway(), proc) is ReconciliationOutcome.UNRESOLVED
        assert "ours" in proc.quarantined_orders
        assert await reconcile_owned_order("ours", FakeGateway(status="CONFIRMED"), proc) is ReconciliationOutcome.COMPLETE
        assert "ours" not in proc.quarantined_orders
        assert store.position("maker-token").size == 5
    finally:
        store.close()


def test_same_trade_different_maker_order_ids_are_independent():
    store = StateStore(":memory:")
    try:
        proc = UserEventProcessor(store)
        for oid in ("a", "b"):
            proc.register_order(oid, "condition", "token", Side.BUY, 2, .2)
            proc.on_trade(TradeEvent("token", Side.BUY, .2, 1, "same", TradeState.MATCHED, 1, oid), "condition")
        assert store.position("token").size == 2
    finally:
        store.close()


class LagGateway:
    def __init__(self, side, quantities, trades):
        self.side, self.quantities, self.trades = side, list(quantities), trades
        self.order_reads = self.trade_reads = 0

    async def get_order(self, order_id):
        matched = self.quantities[min(self.order_reads, len(self.quantities) - 1)]
        self.order_reads += 1
        return {"id": order_id, "asset_id": "maker-token", "side": self.side,
            "price": ".16" if self.side == "SELL" else ".27", "original_size": "5",
            "size_matched": str(matched), "status": "MATCHED"}

    async def get_trades(self, *, market=None, asset_id=None):
        self.trade_reads += 1
        return self.trades


def _exact_trade(side, token="maker-token", price=None):
    price = price or (".16" if side == "SELL" else ".27")
    return {"id": "exact-trade", "status": "MATCHED", "market": "condition",
        "asset_id": "counter-token", "side": "BUY", "maker_orders": [
            {"order_id": "ours", "asset_id": token, "side": side,
             "matched_amount": "5", "price": price}]}


@pytest.mark.asyncio
@pytest.mark.parametrize("side", ["BUY", "SELL"])
async def test_order_state_lag_converges_from_fresh_reads_without_second_fill(side):
    store = StateStore(":memory:")
    try:
        proc = UserEventProcessor(store)
        price = .16 if side == "SELL" else .27
        proc.register_order("ours", "condition", "maker-token", Side(side), 5, price)
        if side == "SELL":
            store.set_position("maker-token", 5, .2)
        proc.on_trade(TradeEvent("maker-token", Side(side), price, 5, "exact-trade",
            TradeState.MATCHED, 1, "ours"), "condition")
        initial = store.position("maker-token").size
        gw = LagGateway(side, [0, 5], [_exact_trade(side)])
        delays = []
        async def no_wait(delay): delays.append(delay)
        assert await reconcile_owned_order("ours", gw, proc, sleep=no_wait) is ReconciliationOutcome.COMPLETE
        assert delays == [.5]
        assert gw.order_reads == gw.trade_reads == 2
        assert proc.tracked_trade_qty("ours") == 5
        assert store.position("maker-token").size == initial
        assert "ours" not in proc.quarantined_orders
    finally:
        store.close()


@pytest.mark.asyncio
async def test_order_state_lag_timeout_stays_unresolved():
    store = StateStore(":memory:")
    try:
        proc = UserEventProcessor(store)
        proc.register_order("ours", "condition", "maker-token", Side.SELL, 5, .16)
        store.set_position("maker-token", 5, .2)
        proc.on_trade(TradeEvent("maker-token", Side.SELL, .16, 5, "exact-trade",
            TradeState.MATCHED, 1, "ours"), "condition")
        gw = LagGateway("SELL", [0], [_exact_trade("SELL")])
        async def no_wait(_): pass
        assert await reconcile_owned_order("ours", gw, proc, sleep=no_wait,
            retry_delays=(0.1, 0.2)) is ReconciliationOutcome.UNRESOLVED
        assert gw.order_reads == gw.trade_reads == 3
        assert "ours" in proc.quarantined_orders
        assert store.position("maker-token").size == 0
    finally:
        store.close()


@pytest.mark.asyncio
async def test_active_deadline_capped_sleep_does_not_start_retry():
    store = StateStore(":memory:")
    try:
        proc = UserEventProcessor(store)
        proc.register_order("ours", "condition", "maker-token", Side.SELL, 5, .16)
        store.set_position("maker-token", 5, .2)
        proc.on_trade(TradeEvent("maker-token", Side.SELL, .16, 5, "exact-trade",
            TradeState.MATCHED, 1, "ours"), "condition")
        gw = LagGateway("SELL", [0], [_exact_trade("SELL")])
        loop = asyncio.get_running_loop()
        deadline = loop.time() + .03  # Reviewer case: 0.3s remaining, 2s backoff.
        sleeps = []
        async def capped_sleep(delay):
            sleeps.append(delay)
            await asyncio.sleep(delay + .01)  # scheduler returns just after cutoff
        assert await reconcile_owned_order("ours", gw, proc, sleep=capped_sleep,
            retry_delays=(2.0,), deadline=deadline) is ReconciliationOutcome.ACTIVE_DEADLINE
        assert len(sleeps) == 1 and 0 < sleeps[0] <= .03
        assert gw.order_reads == gw.trade_reads == 1
        assert "ours" in proc.quarantined_orders
        assert store.position("maker-token").size == 0
    finally:
        store.close()


@pytest.mark.asyncio
async def test_active_deadline_late_scheduler_wakeup_does_not_start_retry():
    store = StateStore(":memory:")
    try:
        proc = UserEventProcessor(store)
        proc.register_order("ours", "condition", "maker-token", Side.BUY, 5, .27)
        proc.on_trade(TradeEvent("maker-token", Side.BUY, .27, 5, "exact-trade",
            TradeState.MATCHED, 1, "ours"), "condition")
        gw = LagGateway("BUY", [0], [_exact_trade("BUY")])
        loop = asyncio.get_running_loop()
        deadline = loop.time() + .02
        async def scheduler_late(delay):
            await asyncio.sleep(deadline - loop.time() + .02)
        assert await reconcile_owned_order("ours", gw, proc, sleep=scheduler_late,
            retry_delays=(.01,), deadline=deadline) is ReconciliationOutcome.ACTIVE_DEADLINE
        assert gw.order_reads == gw.trade_reads == 1
        assert "ours" in proc.quarantined_orders
    finally:
        store.close()


@pytest.mark.asyncio
async def test_deadline_already_expired_after_initial_lag_is_explicit_outcome():
    store = StateStore(":memory:")
    try:
        proc = UserEventProcessor(store)
        proc.register_order("ours", "condition", "maker-token", Side.BUY, 5, .27)
        proc.on_trade(TradeEvent("maker-token", Side.BUY, .27, 5, "exact-trade",
            TradeState.MATCHED, 1, "ours"), "condition")
        gw = LagGateway("BUY", [0], [_exact_trade("BUY")])
        loop = asyncio.get_running_loop()
        assert await reconcile_owned_order("ours", gw, proc,
            deadline=loop.time() - 1) is ReconciliationOutcome.ACTIVE_DEADLINE
        assert gw.order_reads == gw.trade_reads == 0
        assert "ours" in proc.quarantined_orders
    finally:
        store.close()


@pytest.mark.asyncio
async def test_task_queued_before_cutoff_checks_deadline_when_it_starts():
    import time

    store = StateStore(":memory:")
    try:
        proc = UserEventProcessor(store)
        proc.register_order("ours", "condition", "maker-token", Side.BUY, 5, .27)
        proc.quarantine_order("ours", "fill truth pending", notify=False)
        gw = LagGateway("BUY", [5], [_exact_trade("BUY")])
        loop = asyncio.get_running_loop()
        scheduled = loop.time()
        task_box = []
        def queue_then_advance():
            # Queue while before cutoff, then keep the event loop from starting
            # the task until the monotonic deadline has passed.
            task_box.append(asyncio.create_task(reconcile_owned_order(
                "ours", gw, proc, deadline=scheduled + .02)))
            time.sleep(.04)
        loop.call_soon(queue_then_advance)
        await asyncio.sleep(0)
        assert await task_box[0] is ReconciliationOutcome.ACTIVE_DEADLINE
        assert gw.order_reads == gw.trade_reads == 0
        assert "ours" in proc.quarantined_orders
    finally:
        store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("stalled_read", ["get_order", "get_trades"])
async def test_active_attempt_timeout_cancels_stalled_rest_read(stalled_read):
    store = StateStore(":memory:")
    try:
        proc = UserEventProcessor(store)
        proc.register_order("ours", "condition", "maker-token", Side.SELL, 5, .16)
        store.set_position("maker-token", 5, .2)
        # Existing exact WS fill remains economic truth while the REST attempt
        # is abandoned; timeout must not reverse it or mark it reconciled.
        proc.on_trade(TradeEvent("maker-token", Side.SELL, .16, 5, "exact-trade",
            TradeState.MATCHED, 1, "ours"), "condition")
        started, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        class StalledGateway(LagGateway):
            async def get_order(self, order_id):
                self.order_reads += 1
                if stalled_read == "get_order":
                    started.set()
                    try:
                        await release.wait()
                    finally:
                        cancelled.set()
                return {"id": order_id, "asset_id": "maker-token", "side": "SELL",
                    "price": ".16", "original_size": "5", "size_matched": "5"}

            async def get_trades(self, *, market=None, asset_id=None):
                self.trade_reads += 1
                if stalled_read == "get_trades":
                    started.set()
                    try:
                        await release.wait()
                    finally:
                        cancelled.set()
                return [_exact_trade("SELL")]

        gw = StalledGateway("SELL", [], [])
        loop = asyncio.get_running_loop()
        outcome = await reconcile_owned_order("ours", gw, proc,
            deadline=loop.time() + .03)
        assert outcome is ReconciliationOutcome.ACTIVE_DEADLINE
        assert started.is_set() and cancelled.is_set()
        assert "ours" in proc.quarantined_orders
        assert proc.tracked_trade_qty("ours") == 5
        assert store.position("maker-token").size == 0
        # Releasing the test gate after timeout cannot revive the cancelled
        # attempt or apply its late response.
        release.set()
        await asyncio.sleep(0)
        assert proc.tracked_trade_qty("ours") == 5
        assert "ours" in proc.quarantined_orders
    finally:
        store.close()


def _foreign_trade(index):
    return {"id": f"foreign-{index}", "status": "MATCHED", "market": "condition",
        "maker_orders": [{"order_id": f"foreign-order-{index}", "asset_id": "other-token",
            "side": "BUY", "matched_amount": "1", "price": ".27"}]}


class ForeignRowsGateway:
    def __init__(self, rows, *, matched="0"):
        self.rows = rows
        self.matched = matched
        self.order_reads = self.trade_reads = 0

    async def get_order(self, order_id):
        self.order_reads += 1
        return {"id": order_id, "asset_id": "maker-token", "side": "BUY",
            "price": ".27", "original_size": "5", "size_matched": self.matched}

    async def get_trades(self, *, market=None, asset_id=None):
        self.trade_reads += 1
        return self.rows


@pytest.mark.asyncio
async def test_ten_thousand_foreign_rows_stop_deterministically_at_active_deadline(monkeypatch):
    store = StateStore(":memory:")
    try:
        proc = UserEventProcessor(store)
        proc.register_order("ours", "condition", "maker-token", Side.BUY, 5, .27)
        proc.quarantine_order("ours", "fill truth pending", notify=False)
        rows = [_foreign_trade(i) for i in range(10_000)]
        gw = ForeignRowsGateway(rows)
        original_parse = filltruth_module._parse_trade_cooperatively
        original_check = filltruth_module._check_active_order_deadline
        processed = 0
        forced_expiry = False

        async def counted_parse(raw, deadline):
            nonlocal processed, forced_expiry
            parsed = await original_parse(raw, deadline)
            processed += 1
            if processed == 40:
                forced_expiry = True
            return parsed

        def controlled_check(deadline):
            if forced_expiry:
                raise filltruth_module._ActiveOrderDeadlineExpired
            original_check(deadline)

        monkeypatch.setattr(filltruth_module, "_parse_trade_cooperatively", counted_parse)
        monkeypatch.setattr(filltruth_module, "_check_active_order_deadline", controlled_check)
        loop = asyncio.get_running_loop()
        outcome = await reconcile_owned_order("ours", gw, proc,
            deadline=loop.time() + .01)
        assert outcome is ReconciliationOutcome.ACTIVE_DEADLINE
        assert processed == 40 < len(rows)
        assert gw.order_reads == gw.trade_reads == 1
        assert "ours" in proc.quarantined_orders
    finally:
        store.close()


@pytest.mark.asyncio
async def test_large_trade_list_yields_to_event_loop(monkeypatch):
    store = StateStore(":memory:")
    try:
        proc = UserEventProcessor(store)
        proc.register_order("ours", "condition", "maker-token", Side.BUY, 5, .27)
        rows = [_foreign_trade(i) for i in range(10_000)]
        gw = ForeignRowsGateway(rows)
        original_parse = filltruth_module._parse_trade_cooperatively
        parsed_rows = 0
        marker_at = []
        marker_task = None

        async def marker():
            marker_at.append(parsed_rows)

        async def observed_parse(raw, deadline):
            nonlocal parsed_rows, marker_task
            parsed = await original_parse(raw, deadline)
            parsed_rows += 1
            if marker_task is None:
                marker_task = asyncio.create_task(marker())
            return parsed

        monkeypatch.setattr(filltruth_module, "_parse_trade_cooperatively", observed_parse)
        assert await reconcile_owned_order_once("ours", gw, proc) is ReconciliationOutcome.COMPLETE
        assert marker_at and 0 < marker_at[0] < len(rows)
        assert parsed_rows == len(rows)
    finally:
        store.close()


@pytest.mark.asyncio
async def test_deadline_is_checked_immediately_after_trade_read_before_parsing(monkeypatch):
    store = StateStore(":memory:")
    try:
        proc = UserEventProcessor(store)
        proc.register_order("ours", "condition", "maker-token", Side.BUY, 5, .27)
        proc.quarantine_order("ours", "fill truth pending", notify=False)
        expired = False
        parses = 0
        original_parse = filltruth_module.parse_trade
        original_check = filltruth_module._check_active_order_deadline

        class CrossingGateway(ForeignRowsGateway):
            async def get_trades(self, *, market=None, asset_id=None):
                nonlocal expired
                rows = await super().get_trades(market=market, asset_id=asset_id)
                expired = True
                return rows

        def deadline_check(deadline):
            if expired:
                raise filltruth_module._ActiveOrderDeadlineExpired
            original_check(deadline)

        def count_parse(raw):
            nonlocal parses
            parses += 1
            return original_parse(raw)

        monkeypatch.setattr(filltruth_module, "_check_active_order_deadline", deadline_check)
        monkeypatch.setattr(filltruth_module, "parse_trade", count_parse)
        gw = CrossingGateway([_exact_trade("BUY")])
        outcome = await reconcile_owned_order_once("ours", gw, proc,
            active_order_deadline=asyncio.get_running_loop().time() + 5)
        assert outcome is ReconciliationOutcome.ACTIVE_DEADLINE
        assert gw.order_reads == gw.trade_reads == 1
        assert parses == 0
        assert "ours" in proc.quarantined_orders
    finally:
        store.close()


@pytest.mark.asyncio
async def test_large_maker_leg_list_yields_and_stops_at_active_deadline(monkeypatch):
    store = StateStore(":memory:")
    try:
        proc = UserEventProcessor(store)
        proc.register_order("ours", "condition", "maker-token", Side.BUY, 5, .27)
        proc.quarantine_order("ours", "fill truth pending", notify=False)
        maker_rows = [{"order_id": f"foreign-order-{i}", "asset_id": "other-token",
            "side": "BUY", "matched_amount": "1", "price": ".27"} for i in range(256)]
        gw = ForeignRowsGateway([{"id": "many-makers", "status": "MATCHED",
            "market": "condition", "maker_orders": maker_rows}])
        original_parse = filltruth_module.parse_trade
        original_check = filltruth_module._check_active_order_deadline
        leg_parses = 0
        forced_expiry = False
        marker_at = []
        marker_task = None

        async def marker():
            marker_at.append(leg_parses)

        def counted_parse(raw):
            nonlocal leg_parses, marker_task, forced_expiry
            rows = raw.get("maker_orders")
            if isinstance(rows, list) and len(rows) == 1:
                leg_parses += 1
                if marker_task is None:
                    marker_task = asyncio.create_task(marker())
                if leg_parses == 40:
                    forced_expiry = True
            return original_parse(raw)

        def controlled_check(deadline):
            if forced_expiry:
                raise filltruth_module._ActiveOrderDeadlineExpired
            original_check(deadline)

        monkeypatch.setattr(filltruth_module, "parse_trade", counted_parse)
        monkeypatch.setattr(filltruth_module, "_check_active_order_deadline", controlled_check)
        loop = asyncio.get_running_loop()
        outcome = await reconcile_owned_order("ours", gw, proc, deadline=loop.time() + .01)
        assert outcome is ReconciliationOutcome.ACTIVE_DEADLINE
        assert leg_parses == 40 < len(maker_rows)
        assert marker_at and 0 < marker_at[0] < len(maker_rows)
        assert "ours" in proc.quarantined_orders
    finally:
        store.close()


@pytest.mark.asyncio
async def test_deadline_at_final_complete_gate_never_clears_quarantine(monkeypatch):
    store = StateStore(":memory:")
    try:
        proc = UserEventProcessor(store)
        proc.register_order("ours", "condition", "maker-token", Side.BUY, 5, .27)
        proc.on_trade(TradeEvent("maker-token", Side.BUY, .27, 5, "exact-trade",
            TradeState.MATCHED, 1, "ours"), "condition")
        proc.quarantine_order("ours", "fill truth pending", notify=False)
        gw = ForeignRowsGateway([_exact_trade("BUY")], matched="5")
        expired = False
        original_check = filltruth_module._check_active_order_deadline
        original_failed = proc.has_failed_fill
        transitions = []
        original_mark = store.mark_owned_order_unresolved
        original_log = filltruth_module.log
        original_info = original_log.info
        complete_logs = []

        def mark_unresolved(order_id, value):
            transitions.append(value)
            original_mark(order_id, value)

        def has_failed(order_id):
            nonlocal expired
            result = original_failed(order_id)
            expired = True
            return result

        def final_gate(deadline):
            if expired:
                raise filltruth_module._ActiveOrderDeadlineExpired
            original_check(deadline)

        def track_info(event, *args, **kwargs):
            if event == "order_reconcile_complete":
                complete_logs.append(event)
            return original_info(event, *args, **kwargs)

        monkeypatch.setattr(filltruth_module, "_check_active_order_deadline", final_gate)
        monkeypatch.setattr(proc, "has_failed_fill", has_failed)
        monkeypatch.setattr(store, "mark_owned_order_unresolved", mark_unresolved)
        class ObservedLog:
            info = staticmethod(track_info)

            def __getattr__(self, name):
                return getattr(original_log, name)

        # Patching a lazy logger's synthesized .info attribute makes monkeypatch
        # restore a permanently bound pre-CLI PrintLogger method. Replace the
        # module reference so teardown restores the original proxy itself.
        monkeypatch.setattr(filltruth_module, "log", ObservedLog())
        loop = asyncio.get_running_loop()
        result = await reconcile_owned_order("ours", gw, proc, deadline=loop.time() + 5)
        assert result is ReconciliationOutcome.ACTIVE_DEADLINE
        assert "ours" in proc.quarantined_orders
        assert False not in transitions
        assert not complete_logs
        assert proc.tracked_trade_qty("ours") == 5
    finally:
        store.close()


@pytest.mark.asyncio
async def test_fill_before_mid_list_deadline_is_kept_and_later_rows_do_not_apply(monkeypatch):
    store = StateStore(":memory:")
    try:
        proc = UserEventProcessor(store)
        proc.register_order("ours", "condition", "maker-token", Side.BUY, 5, .27)
        proc.quarantine_order("ours", "fill truth pending", notify=False)
        rows = [_exact_trade("BUY"), _foreign_trade(1), _exact_trade("BUY")]
        rows[2] = {**rows[2], "id": "late-trade"}
        gw = ForeignRowsGateway(rows, matched="5")
        original_parse = filltruth_module._parse_trade_cooperatively
        original_check = filltruth_module._check_active_order_deadline
        processed = 0
        forced_expiry = False

        async def expire_after_second_row(raw, deadline):
            nonlocal processed, forced_expiry
            parsed = await original_parse(raw, deadline)
            processed += 1
            if processed == 2:
                forced_expiry = True
            return parsed

        def controlled_check(deadline):
            if forced_expiry:
                raise filltruth_module._ActiveOrderDeadlineExpired
            original_check(deadline)

        monkeypatch.setattr(filltruth_module, "_parse_trade_cooperatively", expire_after_second_row)
        monkeypatch.setattr(filltruth_module, "_check_active_order_deadline", controlled_check)
        loop = asyncio.get_running_loop()
        result = await reconcile_owned_order("ours", gw, proc, deadline=loop.time() + 5)
        assert result is ReconciliationOutcome.ACTIVE_DEADLINE
        assert processed == 2
        assert proc.tracked_trade_qty("ours") == 5
        assert store.position("maker-token").size == 5
        assert "ours" in proc.quarantined_orders

        # Post-cleanup replay sees all evidence and deduplicates the already
        # applied fill before completing authoritative reconciliation.
        monkeypatch.setattr(filltruth_module, "_parse_trade_cooperatively", original_parse)
        monkeypatch.setattr(filltruth_module, "_check_active_order_deadline", original_check)
        gw.rows = [_exact_trade("BUY")]
        assert await reconcile_owned_order_once("ours", gw, proc) is ReconciliationOutcome.COMPLETE
        assert proc.tracked_trade_qty("ours") == 5
        assert store.position("maker-token").size == 5
        assert "ours" not in proc.quarantined_orders
    finally:
        store.close()


@pytest.mark.asyncio
async def test_retry_before_active_deadline_converges_and_final_grace_remains():
    store = StateStore(":memory:")
    try:
        proc = UserEventProcessor(store)
        proc.register_order("ours", "condition", "maker-token", Side.SELL, 5, .16)
        store.set_position("maker-token", 5, .2)
        proc.on_trade(TradeEvent("maker-token", Side.SELL, .16, 5, "exact-trade",
            TradeState.MATCHED, 1, "ours"), "condition")
        gw = LagGateway("SELL", [0, 5], [_exact_trade("SELL")])
        loop = asyncio.get_running_loop()
        async def short_sleep(_): await asyncio.sleep(0)
        assert await reconcile_owned_order("ours", gw, proc, sleep=short_sleep,
            retry_delays=(.01,), deadline=loop.time() + 2) is ReconciliationOutcome.COMPLETE
        assert gw.order_reads == gw.trade_reads == 2

        # A new active-order sequence can stop at cutoff without clearing
        # quarantine; a separate post-cleanup call then gets bounded grace.
        proc2_store = StateStore(":memory:")
        try:
            proc2 = UserEventProcessor(proc2_store)
            proc2.register_order("ours", "condition", "maker-token", Side.BUY, 5, .27)
            proc2.on_trade(TradeEvent("maker-token", Side.BUY, .27, 5, "exact-trade",
                TradeState.MATCHED, 1, "ours"), "condition")
            delayed = LagGateway("BUY", [0, 0, 5], [_exact_trade("BUY")])
            stop_at = asyncio.get_running_loop().time() + .02
            async def cross_cutoff(delay): await asyncio.sleep(delay + .01)
            assert await reconcile_owned_order("ours", delayed, proc2,
                sleep=cross_cutoff, retry_delays=(1.0,), deadline=stop_at) is ReconciliationOutcome.ACTIVE_DEADLINE
            assert delayed.order_reads == delayed.trade_reads == 1
            assert "ours" in proc2.quarantined_orders
            assert await reconcile_owned_order("ours", delayed, proc2, sleep=short_sleep,
                retry_delays=(.01,)) is ReconciliationOutcome.COMPLETE
            assert delayed.order_reads == delayed.trade_reads == 3
            assert "ours" not in proc2.quarantined_orders
            assert proc2.tracked_trade_qty("ours") == 5
        finally:
            proc2_store.close()
    finally:
        store.close()


@pytest.mark.asyncio
async def test_order_state_lag_requires_rest_trade_and_matching_economics():
    store = StateStore(":memory:")
    try:
        proc = UserEventProcessor(store)
        proc.register_order("ours", "condition", "maker-token", Side.BUY, 5, .27)
        proc.on_trade(TradeEvent("maker-token", Side.BUY, .27, 5, "exact-trade",
            TradeState.MATCHED, 1, "ours"), "condition")
        empty = LagGateway("BUY", [0], [])
        assert await reconcile_owned_order_once("ours", empty, proc) == "UNRESOLVED"
        contradictory = LagGateway("BUY", [0], [_exact_trade("SELL")])
        assert await reconcile_owned_order_once("ours", contradictory, proc) == "UNRESOLVED"
        wrong_token = LagGateway("BUY", [0], [_exact_trade("BUY", token="other")])
        assert await reconcile_owned_order_once("ours", wrong_token, proc) == "UNRESOLVED"
        assert await reconcile_owned_order("ours", wrong_token, proc) \
            is ReconciliationOutcome.UNRESOLVED
        assert await reconcile_owned_order("ours", wrong_token, proc,
            deadline=asyncio.get_running_loop().time() + 5) \
            is ReconciliationOutcome.UNRESOLVED
    finally:
        store.close()


@pytest.mark.asyncio
async def test_duplicate_rest_retry_rows_apply_one_economic_fill():
    store = StateStore(":memory:")
    try:
        proc = UserEventProcessor(store)
        proc.register_order("ours", "condition", "maker-token", Side.BUY, 5, .27)
        gw = LagGateway("BUY", [0, 5], [_exact_trade("BUY"), _exact_trade("BUY")])
        async def no_wait(_): pass
        assert await reconcile_owned_order("ours", gw, proc, sleep=no_wait) is ReconciliationOutcome.COMPLETE
        assert proc.tracked_trade_qty("ours") == 5
        assert store.position("maker-token").size == 5
    finally:
        store.close()


def test_order_cumulative_quantity_is_a_checksum_and_gap_has_no_synthetic_fill():
    store = StateStore(":memory:")
    try:
        requested = []
        proc = UserEventProcessor(store, on_reconcile=requested.append)
        proc.register_order("ours", "condition", "token", Side.BUY, 5, .2)
        proc.on_order(OrderEvent("ours", "token", Side.BUY, .2, 0,
            original_size=5, size_matched=5, status="UPDATE"), "condition")
        assert requested == ["ours"]
        assert "ours" in proc.quarantined_orders
        assert store.position("token").size == 0
        proc.on_trade(TradeEvent("token", Side.BUY, .2, 5, "t", TradeState.MATCHED, 1, "ours"), "condition")
        assert proc.tracked_trade_qty("ours") == 5
        assert store.position("token").size == 5
    finally:
        store.close()


@pytest.mark.parametrize("side", [Side.BUY, Side.SELL])
def test_registered_order_economics_accept_matching_buy_and_sell(side):
    store = StateStore(":memory:")
    try:
        proc = UserEventProcessor(store)
        proc.register_order("ours", "condition", "token", side, 5, .2)
        proc.on_trade(TradeEvent("token", side, .2, 2, "trade", TradeState.MATCHED, 1, "ours"), "condition")
        assert proc.tracked_trade_qty("ours") == 2
        assert "ours" not in proc.quarantined_orders
    finally:
        store.close()


@pytest.mark.parametrize("token,side", [
    ("wrong-token", Side.BUY), ("token", Side.SELL), ("wrong-token", Side.SELL),
])
def test_registered_order_economic_contradiction_quarantines_without_applying(token, side):
    store = StateStore(":memory:")
    calls = []
    try:
        proc = UserEventProcessor(store, on_reconcile=calls.append)
        proc.register_order("ours", "condition", "token", Side.BUY, 5, .2)
        proc.on_trade(TradeEvent(token, side, .2, 5, "trade", TradeState.MATCHED, 1, "ours"), "condition")
        assert store.position("token").size == 0
        assert proc.tracked_trade_qty("ours") == 0
        assert "ours" in proc.quarantined_orders
        assert calls == ["ours"]
    finally:
        store.close()


def test_ws_and_rest_trade_market_contradictions_share_registered_order_validation():
    store = StateStore(":memory:")
    try:
        proc = UserEventProcessor(store)
        proc.register_order("ours", "condition", "token", Side.BUY, 5, .2)
        proc.on_trade(TradeEvent("token", Side.BUY, .2, 5, "ws", TradeState.MATCHED,
            1, "ours", "other-condition"), "condition")
        assert store.position("token").size == 0
        assert "ours" in proc.quarantined_orders
    finally:
        store.close()


def test_malformed_ws_trade_quarantines_only_exact_registered_maker_order():
    from polymaker.userstream.client import UserStream
    store = StateStore(":memory:")
    calls = []
    try:
        proc = UserEventProcessor(store, on_reconcile=calls.append)
        proc.register_order("ours", "condition", "maker-token", Side.BUY, 5, .27)
        stream = UserStream(object(), "wallet", proc, other_token=lambda _t: None,
            condition_of_token=lambda _t: "condition")
        malformed_owned = {"event_type": "trade", "id": "t1", "status": "MATCHED",
            "market": "condition", "condition_id": "other", "maker_orders": [{
                "order_id": "ours", "asset_id": "maker-token", "side": "BUY",
                "matched_amount": "5", "price": ".27"}]}
        stream._on_trade(malformed_owned)
        assert "ours" in proc.quarantined_orders
        assert calls == ["ours"]
        assert store.position("maker-token").size == 0

        calls.clear()
        proc.quarantined_orders.clear()
        store.mark_owned_order_unresolved("ours", False)
        conflicting_aliases = {"event_type": "trade", "id": "t-alias", "status": "MATCHED",
            "maker_orders": [{"order_id": "foreign", "orderID": "ours",
                "asset_id": "maker-token", "side": "BUY", "matched_amount": "5",
                "price": ".27"}]}
        stream._on_trade(conflicting_aliases)
        assert calls == ["ours"]
        assert "ours" in proc.quarantined_orders

        calls.clear()
        proc.quarantined_orders.clear()
        store.mark_owned_order_unresolved("ours", False)
        malformed_foreign = {**malformed_owned, "id": "t2", "maker_orders": [{
            "order_id": "foreign", "asset_id": "maker-token", "side": "BUY",
            "matched_amount": "bad", "price": ".27"}]}
        stream._on_trade(malformed_foreign)
        assert calls == []
        assert "ours" not in proc.quarantined_orders
    finally:
        store.close()


@pytest.mark.parametrize("aliases,expected_owned", [
    ({"order_id": "FOREIGN_ORDER", "orderId": "ours"}, {"ours"}),
    ({"order_id": "ours", "orderId": "FOREIGN_ORDER"}, {"ours"}),
    ({"orderID": "FOREIGN_ORDER", "orderId": "ours"}, {"ours"}),
    ({"order_id": "ours-a", "orderId": "ours-b"}, {"ours-a", "ours-b"}),
    ({"order_id": "FOREIGN_A", "orderId": "FOREIGN_B"}, set()),
    ({"order_id": "ours", "orderId": ""}, {"ours"}),
    ({"order_id": "ours", "orderId": None}, {"ours"}),
    ({"order_id": "ours", "orderId": 12345}, {"ours"}),
])
def test_malformed_ws_recovers_all_exact_owned_ids_from_canonical_aliases(aliases, expected_owned):
    from polymaker.userstream.client import UserStream
    store = StateStore(":memory:")
    calls = []
    try:
        proc = UserEventProcessor(store, on_reconcile=calls.append)
        for oid in ("ours", "ours-a", "ours-b"):
            proc.register_order(oid, "condition", "maker-token", Side.BUY, 5, .27)
        stream = UserStream(object(), "wallet", proc, other_token=lambda _t: None,
            condition_of_token=lambda _t: "condition")
        stream._on_trade({"event_type": "trade", "id": "bad-alias", "status": "MATCHED",
            "maker_orders": [{**aliases, "asset_id": "maker-token", "side": "BUY",
                "matched_amount": "5", "price": ".27"}]})
        assert set(calls) == expected_owned
        assert proc.quarantined_orders == expected_owned
        assert all(store.position("maker-token").size == 0 for _ in expected_owned)
    finally:
        store.close()


def test_cancel_then_later_exact_owned_trade_applies_once():
    store = StateStore(":memory:")
    try:
        proc = UserEventProcessor(store)
        proc.register_order("ours", "condition", "token", Side.BUY, 5, .2)
        proc.on_order(OrderEvent("ours", "token", Side.BUY, .2, 0, is_cancel=True,
            original_size=5, size_matched=0), "condition")
        ev = TradeEvent("token", Side.BUY, .2, 2, "late", TradeState.MATCHED, 1, "ours")
        proc.on_trade(ev, "condition")
        proc.on_trade(ev, "condition")
        assert proc.tracked_trade_qty("ours") == 2
        assert store.position("token").size == 2
    finally:
        store.close()


def test_failed_unapplied_record_does_not_count_toward_checksum():
    store = StateStore(":memory:")
    try:
        proc = UserEventProcessor(store)
        proc.register_order("ours", "condition", "token", Side.BUY, 5, .2)
        proc.on_trade(TradeEvent("token", Side.BUY, .2, 5, "failed", TradeState.FAILED, 1, "ours"), "condition")
        assert proc.tracked_trade_qty("ours") == 0
        assert store.position("token").size == 0
    finally:
        store.close()


def test_owned_order_event_metadata_contradiction_quarantines():
    store = StateStore(":memory:")
    try:
        proc = UserEventProcessor(store)
        proc.register_order("ours", "condition", "token", Side.BUY, 5, .2)
        proc.on_order(OrderEvent("ours", "token", Side.BUY, .2, 5,
            original_size=6, size_matched=1), "condition")
        assert "ours" in proc.quarantined_orders
        assert store.position("token").size == 0
    finally:
        store.close()


def test_out_of_order_settlement_status_is_remembered_until_matched_and_applied_once():
    store = StateStore(":memory:")
    try:
        proc = UserEventProcessor(store)
        proc.register_order("ours", "condition", "token", Side.BUY, 5, .2)
        for status in (TradeState.CONFIRMED, TradeState.MATCHED, TradeState.MATCHED):
            proc.on_trade(TradeEvent("token", Side.BUY, .2, 5, "trade", status, 1, "ours"), "condition")
        assert store.position("token").size == 5
        assert proc.tracked_trade_qty("ours") == 5
        assert proc.fill_records[("trade", "ours")].status is TradeState.CONFIRMED
    finally:
        store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("row", [
    {"id": "target", "status": "MATCHED"},
    {"id": "target", "status": "MATCHED", "maker_orders": None},
    {"status": "MATCHED", "maker_orders": [{"order_id": "ours"}]},
])
async def test_malformed_potentially_relevant_rest_trade_keeps_reconciliation_unresolved(row):
    class MalformedRows(FakeGateway):
        async def get_trades(self, **_kwargs): return [row]
    store = StateStore(":memory:")
    try:
        proc = UserEventProcessor(store)
        proc.register_order("ours", "condition", "maker-token", Side.BUY, 5, .27)
        assert await reconcile_owned_order("ours", MalformedRows(), proc) is ReconciliationOutcome.UNRESOLVED
        assert "ours" in proc.quarantined_orders
        assert store.position("maker-token").size == 0
    finally:
        store.close()


@pytest.mark.asyncio
async def test_foreign_malformed_semantics_can_be_ignored_when_order_identity_proves_foreign():
    class ForeignMalformed(FakeGateway):
        async def get_trades(self, **_kwargs):
            return [{"id": "foreign", "status": "UNKNOWN", "maker_orders": [
                {"order_id": "foreign-order"}]}]
        async def get_order(self, order_id):
            return {"id": order_id, "asset_id": "maker-token", "side": "BUY",
                "price": ".27", "original_size": "5", "size_matched": "0"}
    store = StateStore(":memory:")
    try:
        proc = UserEventProcessor(store)
        proc.register_order("ours", "condition", "maker-token", Side.BUY, 5, .27)
        assert await reconcile_owned_order("ours", ForeignMalformed(), proc) is ReconciliationOutcome.COMPLETE
        assert store.position("maker-token").size == 0
    finally:
        store.close()


@pytest.mark.asyncio
async def test_rest_conflicting_maker_alias_containing_target_stays_unresolved_at_zero_qty():
    class ConflictingAliases(FakeGateway):
        async def get_order(self, order_id):
            return {"id": order_id, "asset_id": "maker-token", "side": "BUY",
                "price": ".27", "original_size": "5", "size_matched": "0"}
        async def get_trades(self, **_kwargs):
            return [{"id": "conflict", "status": "MATCHED", "maker_orders": [{
                "order_id": "FOREIGN_ORDER", "orderId": "ours", "asset_id": "maker-token",
                "side": "BUY", "matched_amount": "5", "price": ".27"}]}]

    store = StateStore(":memory:")
    try:
        proc = UserEventProcessor(store)
        proc.register_order("ours", "condition", "maker-token", Side.BUY, 5, .27)
        assert await reconcile_owned_order("ours", ConflictingAliases(), proc) is ReconciliationOutcome.UNRESOLVED
        assert "ours" in proc.quarantined_orders
        assert proc.tracked_trade_qty("ours") == 0
        assert store.position("maker-token").size == 0
    finally:
        store.close()


@pytest.mark.asyncio
async def test_rest_recognizes_orderId_only_as_owned_maker_identity():
    class CamelCaseMaker(FakeGateway):
        async def get_trades(self, **_kwargs):
            return [{"id": "camel", "status": "MATCHED", "maker_orders": [{
                "orderId": "ours", "asset_id": "maker-token", "side": "BUY",
                "matched_amount": "5", "price": ".27"}]}]
    store = StateStore(":memory:")
    try:
        proc = UserEventProcessor(store)
        proc.register_order("ours", "condition", "maker-token", Side.BUY, 5, .27)
        assert await reconcile_owned_order("ours", CamelCaseMaker(), proc) is ReconciliationOutcome.COMPLETE
        assert proc.tracked_trade_qty("ours") == 5
        assert store.position("maker-token").size == 5
    finally:
        store.close()


@pytest.mark.asyncio
async def test_rest_cannot_bypass_registered_order_economics_validation():
    class Contradictory(FakeGateway):
        async def get_trades(self, **_kwargs):
            return [{"id": "trade-x", "status": "MATCHED", "maker_orders": [{
                "order_id": "ours", "asset_id": "wrong-token", "side": "SELL",
                "matched_amount": "5", "price": ".27"}]}]
    store = StateStore(":memory:")
    try:
        proc = UserEventProcessor(store)
        proc.register_order("ours", "condition", "maker-token", Side.BUY, 5, .27)
        assert await reconcile_owned_order("ours", Contradictory(), proc) is ReconciliationOutcome.UNRESOLVED
        assert store.position("maker-token").size == 0
        assert "ours" in proc.quarantined_orders
    finally:
        store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("market,expected", [("condition", True), (None, True), ("other-condition", False)])
async def test_rest_trade_market_is_checked_as_observed_evidence(market, expected):
    class MarketGateway(FakeGateway):
        async def get_trades(self, **_kwargs):
            row = _trade(order="ours")
            if market is None:
                row.pop("market", None)
            else:
                row["market"] = market
            return [row]
    store = StateStore(":memory:")
    try:
        proc = UserEventProcessor(store)
        proc.register_order("ours", "condition", "maker-token", Side.BUY, 5, .27)
        outcome = await reconcile_owned_order("ours", MarketGateway(), proc)
        assert outcome is (ReconciliationOutcome.COMPLETE if expected
                           else ReconciliationOutcome.UNRESOLVED)
        assert store.position("maker-token").size == (5 if expected else 0)
        assert ("ours" in proc.quarantined_orders) is (not expected)
    finally:
        store.close()


@pytest.mark.asyncio
async def test_present_malformed_associate_trades_keeps_order_unknown():
    class BadAssociates(FakeGateway):
        async def get_order(self, order_id):
            row = await super().get_order(order_id)
            row["size_matched"] = "0"
            row["associate_trades"] = [None]
            return row
        async def get_trades(self, **_kwargs): return []
    store = StateStore(":memory:")
    try:
        proc = UserEventProcessor(store)
        proc.register_order("ours", "condition", "maker-token", Side.BUY, 5, .27)
        assert await reconcile_owned_order("ours", BadAssociates(), proc) is ReconciliationOutcome.UNRESOLVED
        assert "ours" in proc.quarantined_orders
    finally:
        store.close()


@pytest.mark.asyncio
async def test_boolean_size_matched_is_unknown_not_clean_zero():
    class BooleanCumulative(FakeGateway):
        async def get_order(self, order_id):
            row = await super().get_order(order_id)
            row["size_matched"] = False
            return row
        async def get_trades(self, **_kwargs): return []
    store = StateStore(":memory:")
    try:
        proc = UserEventProcessor(store)
        proc.register_order("ours", "condition", "maker-token", Side.BUY, 5, .27)
        assert normalize_order(await BooleanCumulative().get_order("ours")) is None
        assert await reconcile_owned_order("ours", BooleanCumulative(), proc) is ReconciliationOutcome.UNRESOLVED
        assert "ours" in proc.quarantined_orders
        assert proc.tracked_trade_qty("ours") == 0
    finally:
        store.close()


@pytest.mark.asyncio
async def test_contradictory_dual_market_rest_row_does_not_apply_fill():
    class DualMarket(FakeGateway):
        async def get_trades(self, **_kwargs):
            row = _trade(order="ours")
            row["condition_id"] = "other-condition"
            return [row]
    store = StateStore(":memory:")
    try:
        proc = UserEventProcessor(store)
        proc.register_order("ours", "condition", "maker-token", Side.BUY, 5, .27)
        assert await reconcile_owned_order("ours", DualMarket(), proc) is ReconciliationOutcome.UNRESOLVED
        assert "ours" in proc.quarantined_orders
        assert store.position("maker-token").size == 0
    finally:
        store.close()
