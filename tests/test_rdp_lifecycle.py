from __future__ import annotations

import asyncio
from copy import deepcopy
import time

import pytest

from polymaker.domain import MarketMeta, OpenOrder, OrderState, Side, TokenMeta, TradeState
from polymaker.execution.filltruth import ReconciliationOutcome
from polymaker.execution.rdp_lifecycle import close_rdp_cycle
from polymaker.state.store import StateStore
from polymaker.state.tracker import OrderEvent, TradeEvent, UserEventProcessor


def make_market():
    return MarketMeta("cid", "BTC up", "btc-up", (TokenMeta("yes", "Yes"), TokenMeta("no", "No")),
        .01, False, 5, 0, 0, 0, 0, 0, False, None, None)


def order_row(oid="o1", token="yes", *, size=5, matched=0, status="LIVE", price=.2):
    return {"id": oid, "asset_id": token, "side": "BUY", "price": str(price),
        "original_size": str(size), "size_matched": str(matched), "status": status,
        "market": "cid"}


def trade_row(oid="o1", trade_id="t1", token="yes", *, qty=2, price=.2, status="CONFIRMED"):
    return {"id": trade_id, "status": status, "market": "cid", "asset_id": "other",
        "side": "SELL", "maker_orders": [{"order_id": oid, "asset_id": token,
            "side": "BUY", "matched_amount": str(qty), "price": str(price)}]}


class FakeGateway:
    """In-memory official-interface fake with injectable read/cancel behavior."""
    def __init__(self, rows=None, trades=None):
        self.rows = {row["id"]: deepcopy(row) for row in (rows or [order_row()])}
        self.trades = list(trades or [])
        self.calls = []
        self.cancel_result = True
        self.cancel_error = None
        self.cancel_mutates = True
        self.cancel_hook = None
        self.open_values = []
        self.read_errors = {}
        self.order_values = []
        self.block = {}
        self.finished = []
        self.hooks = {}
        self.blocking_delays = {}

    async def _gate(self, name):
        self.calls.append(name)
        occurrence = sum(call == name for call in self.calls)
        gate = self.block.get((name, occurrence))
        if gate:
            started, release = gate
            started.set()
            try:
                await release.wait()
            finally:
                self.finished.append((name, occurrence))
        delay = self.blocking_delays.get((name, occurrence))
        if delay:
            time.sleep(delay)
        error = self.read_errors.get((name, occurrence), self.read_errors.get(name))
        if error:
            raise error

    async def open_orders(self):
        await self._gate("open_orders")
        occurrence = sum(call == "open_orders" for call in self.calls)
        hook = self.hooks.get(("open_orders", occurrence))
        if hook:
            hook()
        if self.open_values:
            val = self.open_values.pop(0)
            return deepcopy(val)
        result = []
        for row in self.rows.values():
            remaining = float(row["original_size"]) - float(row["size_matched"])
            if row["status"].upper() not in {"CANCELED", "CANCELLED", "MATCHED"} and remaining > 1e-6:
                result.append(OpenOrder(row["id"], row["asset_id"], Side(row["side"]),
                    float(row["price"]), remaining, OrderState.LIVE))
        return result

    async def cancel(self, order_ids):
        await self._gate("cancel")
        if self.cancel_hook:
            self.cancel_hook()
        for oid in order_ids:
            if self.cancel_mutates and oid in self.rows:
                self.rows[oid]["status"] = "CANCELED"
        if self.cancel_error:
            raise self.cancel_error
        return self.cancel_result

    async def get_order(self, order_id):
        await self._gate("get_order")
        if self.order_values:
            return deepcopy(self.order_values.pop(0))
        if order_id not in self.rows:
            return None
        return deepcopy(self.rows[order_id])

    async def get_trades(self, *, market=None, asset_id=None):
        await self._gate("get_trades")
        return deepcopy(self.trades)

    async def place(self, *_args, **_kwargs):
        raise AssertionError("close barrier must never place orders")


@pytest.fixture
def ctx():
    store = StateStore(":memory:")
    processor = UserEventProcessor(store)
    market = make_market()
    yield store, processor, market
    store.close()


def register(proc, oid="o1", token="yes", size=5, price=.2):
    proc.register_order(oid, "cid", token, Side.BUY, size, price, "rdp")


def deadline():
    return asyncio.get_running_loop().time() + 2


@pytest.mark.asyncio
async def test_zero_fill_canceled_cycle_and_call_order(ctx):
    store, proc, market = ctx
    register(proc)
    store.upsert_order(OpenOrder("o1", "yes", Side.BUY, .2, 5, OrderState.LIVE))
    gw = FakeGateway()
    result = await close_rdp_cycle(market, proc, gw, {"o1"}, {"o1"}, deadline())
    assert result.complete and result.reason is None
    assert result.inventory.qy == result.inventory.qn == 0
    assert gw.calls == ["open_orders", "cancel", "open_orders", "get_order", "get_trades",
                        "get_order", "open_orders"]
    assert store.orders_for("yes") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["CANCELED", "CANCELLED"])
async def test_confirmed_partial_cancel_and_late_rest_fill_are_counted_once(ctx, terminal_status):
    store, proc, market = ctx
    register(proc)
    gw = FakeGateway(trades=[trade_row(qty=2)])
    gw.rows["o1"]["size_matched"] = "2"
    gw.rows["o1"]["status"] = terminal_status
    result = await close_rdp_cycle(market, proc, gw, {"o1"}, {"o1"}, deadline())
    assert result.complete
    assert result.inventory.qy == pytest.approx(2)
    assert store.position("yes").size == pytest.approx(2)


@pytest.mark.asyncio
async def test_fill_discovered_during_cancel_and_duplicate_ws_rest_evidence(ctx):
    store, proc, market = ctx
    register(proc)
    gw = FakeGateway(trades=[trade_row(qty=2)])
    gw.rows["o1"]["size_matched"] = "2"
    def late_fill():
        proc.on_trade(TradeEvent("yes", Side.BUY, .2, 2, "t1", TradeState.CONFIRMED, 1, "o1"), "cid")
    gw.cancel_hook = late_fill
    result = await close_rdp_cycle(market, proc, gw, {"o1"}, {"o1"}, deadline())
    assert result.complete
    assert result.inventory.qy == pytest.approx(2)
    assert proc.tracked_trade_qty("o1") == pytest.approx(2)


@pytest.mark.asyncio
async def test_fully_matched_order_completes_without_cancel(ctx):
    _store, proc, market = ctx
    register(proc)
    gw = FakeGateway(rows=[order_row(matched=5, status="MATCHED")],
                     trades=[trade_row(qty=5)])
    result = await close_rdp_cycle(market, proc, gw, {"o1"}, set(), deadline())
    assert result.complete and result.inventory.qy == pytest.approx(5)
    assert "cancel" not in gw.calls


@pytest.mark.asyncio
async def test_complete_reconciliation_with_nonconfirmed_trade_blocks_then_retry_completes(ctx):
    _store, proc, market = ctx
    register(proc)
    gw = FakeGateway(rows=[order_row(matched=2, status="CANCELED")],
        trades=[trade_row(qty=2, status="MATCHED")])
    first = await close_rdp_cycle(market, proc, gw, {"o1"}, set(), deadline())
    assert not first.complete and first.inventory is None and first.reason == "PENDING_CONFIRMATION"
    gw.trades = [trade_row(qty=2, status="CONFIRMED")]
    second = await close_rdp_cycle(market, proc, gw, {"o1"}, set(), deadline())
    assert second.complete and second.inventory.qy == pytest.approx(2)


@pytest.mark.asyncio
@pytest.mark.parametrize("pending_status", ["MINED", "RETRYING"])
async def test_mined_and_retrying_fills_require_confirmation_before_inventory(ctx, pending_status):
    _store, proc, market = ctx
    register(proc)
    gw = FakeGateway(rows=[order_row(matched=2, status="CANCELED")],
        trades=[trade_row(qty=2, status=pending_status)])
    first = await close_rdp_cycle(market, proc, gw, {"o1"}, set(), deadline())
    assert not first.complete and first.inventory is None
    assert first.reason == ("RECONCILIATION_FAILED" if pending_status == "RETRYING"
                            else "PENDING_CONFIRMATION")
    gw.trades = [trade_row(qty=2, status="CONFIRMED")]
    second = await close_rdp_cycle(market, proc, gw, {"o1"}, set(), deadline())
    assert second.complete and second.inventory.qy == pytest.approx(2)


@pytest.mark.asyncio
async def test_admitted_retrying_fill_with_complete_recovery_waits_for_confirmation(ctx):
    store, proc, market = ctx
    register(proc)
    proc.on_trade(TradeEvent("yes", Side.BUY, .2, 2, "retry-fill",
        TradeState.MATCHED, 1, "o1"), "cid")
    proc.on_trade(TradeEvent("yes", Side.BUY, .2, 2, "retry-fill",
        TradeState.RETRYING, 2, "o1"), "cid")
    gw = FakeGateway(rows=[order_row(matched=2, status="CANCELED")],
        trades=[trade_row(trade_id="retry-fill", qty=2, status="RETRYING")])
    first = await close_rdp_cycle(market, proc, gw, {"o1"}, set(), deadline())
    assert first.reconciliation_outcomes == (("o1", ReconciliationOutcome.COMPLETE),)
    assert not first.complete and first.reason == "PENDING_CONFIRMATION"
    assert first.inventory is None
    assert store.position("yes").size == pytest.approx(2)

    gw.trades = [trade_row(trade_id="retry-fill", qty=2, status="CONFIRMED")]
    second = await close_rdp_cycle(market, proc, gw, {"o1"}, set(), deadline())
    assert second.complete and second.inventory.qy == pytest.approx(2)
    assert store.position("yes").size == pytest.approx(2)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_result,cancel_error", [(False, None), (True, RuntimeError("cancel"))])
async def test_uncertain_cancel_never_completes_even_if_recovered_truth_is_terminal(ctx, cancel_result, cancel_error):
    _store, proc, market = ctx
    register(proc)
    gw = FakeGateway()
    gw.cancel_result, gw.cancel_error = cancel_result, cancel_error
    result = await close_rdp_cycle(market, proc, gw, {"o1"}, {"o1"}, deadline())
    assert not result.complete and result.inventory is None
    assert "CANCELLATION_UNCERTAIN" == result.reason


@pytest.mark.asyncio
async def test_uncertain_cancel_preserves_confirmed_recovered_fills(ctx):
    store, proc, market = ctx
    register(proc)
    gw = FakeGateway(rows=[order_row(matched=2)], trades=[trade_row(qty=2)])
    gw.cancel_result = False
    result = await close_rdp_cycle(market, proc, gw, {"o1"}, {"o1"}, deadline())
    assert not result.complete and result.inventory is None
    assert result.reason == "CANCELLATION_UNCERTAIN"
    assert store.position("yes").size == pytest.approx(2)


@pytest.mark.asyncio
@pytest.mark.parametrize("read_name,bad_value", [
    ("open_orders", RuntimeError("read")),
])
async def test_initial_open_read_failure_fails_closed_before_cancel(ctx, read_name, bad_value):
    _store, proc, market = ctx
    register(proc)
    gw = FakeGateway()
    gw.read_errors[read_name] = bad_value
    result = await close_rdp_cycle(market, proc, gw, {"o1"}, {"o1"}, deadline())
    assert not result.complete and result.inventory is None
    assert "cancel" not in gw.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["", "OPEN", "UNKNOWN", "MINED"])
async def test_unknown_or_nonterminal_order_snapshot_blocks(ctx, status):
    _store, proc, market = ctx
    register(proc)
    gw = FakeGateway(rows=[order_row(status=status)])
    result = await close_rdp_cycle(market, proc, gw, {"o1"}, set(), deadline())
    assert not result.complete and result.inventory is None
    assert result.reason == "TERMINAL_STATE_UNCERTAIN"


@pytest.mark.asyncio
async def test_malformed_and_failed_order_trade_reads_fail_closed(ctx):
    for failed in ("get_order", "get_trades"):
        _store, proc, market = ctx
        register(proc)
        gw = FakeGateway(rows=[order_row(status="CANCELED")], trades=[])
        gw.read_errors[failed] = RuntimeError("bad read")
        result = await close_rdp_cycle(market, proc, gw, {"o1"}, set(), deadline())
        assert not result.complete and result.inventory is None
        assert result.reason in {"RECONCILIATION_FAILED", "TERMINAL_STATE_UNCERTAIN"}

    _store, proc, market = ctx
    gw = FakeGateway(rows=[order_row(status="CANCELED")])
    gw.order_values = [order_row(status="CANCELED"), {"id": "o1", "side": "?"}]
    malformed = await close_rdp_cycle(market, proc, gw, {"o1"}, set(), deadline())
    assert not malformed.complete and malformed.inventory is None


@pytest.mark.asyncio
async def test_malformed_trade_payload_and_unresolved_quarantine_block(ctx):
    _store, proc, market = ctx
    register(proc)
    gw = FakeGateway(rows=[order_row(matched=1, status="CANCELED")],
                     trades=[{"id": "bad", "status": "CONFIRMED", "maker_orders": [None]}])
    result = await close_rdp_cycle(market, proc, gw, {"o1"}, set(), deadline())
    assert not result.complete and result.inventory is None
    assert result.reason == "RECONCILIATION_FAILED"


@pytest.mark.asyncio
async def test_cancel_acknowledgment_with_order_still_open_blocks_closure(ctx):
    _store, proc, market = ctx
    register(proc)
    gw = FakeGateway()
    gw.cancel_result = False
    gw.cancel_mutates = False
    result = await close_rdp_cycle(market, proc, gw, {"o1"}, {"o1"}, deadline())
    assert not result.complete and result.inventory is None
    assert result.reason == "CANCELLATION_UNCERTAIN"
    assert "o1" in {row.order_id for row in await gw.open_orders()}


@pytest.mark.asyncio
async def test_foreign_condition_registered_scope_fails_before_gateway(ctx):
    _store, proc, market = ctx
    proc.register_order("other", "other-condition", "yes", Side.BUY, 5, .2, "rdp")
    gw = FakeGateway()
    result = await close_rdp_cycle(market, proc, gw, {"other"}, set(), deadline())
    assert not result.complete and result.reason == "INVALID_SCOPE"
    assert gw.calls == []


@pytest.mark.asyncio
async def test_existing_quarantine_can_be_resolved_by_exact_recovery(ctx):
    _store, proc, market = ctx
    register(proc)
    proc.quarantine_order("o1", "recoverable prior state", notify=False)
    gw = FakeGateway(rows=[order_row(matched=1, status="CANCELED")],
                     trades=[trade_row(qty=1)])
    result = await close_rdp_cycle(market, proc, gw, {"o1"}, set(), deadline())
    assert result.complete and result.inventory.qy == pytest.approx(1)
    assert "o1" not in proc.quarantined_orders


@pytest.mark.asyncio
async def test_malformed_open_read_blocks_and_foreign_token_order_is_never_canceled(ctx):
    _store, proc, market = ctx
    register(proc)
    gw = FakeGateway()
    gw.open_values = [[object()]]
    result = await close_rdp_cycle(market, proc, gw, {"o1"}, {"o1"}, deadline())
    assert not result.complete and "cancel" not in gw.calls

    gw = FakeGateway(rows=[order_row("foreign", "yes")])
    result = await close_rdp_cycle(market, proc, gw, {"o1"}, set(), deadline())
    assert not result.complete and result.inventory is None
    assert "cancel" not in gw.calls
    assert result.reason == "OPEN_ORDER_CONFLICT"


@pytest.mark.asyncio
async def test_scope_validation_rejects_unknown_foreign_sell_and_bad_subset_before_calls(ctx):
    _store, proc, market = ctx
    register(proc)
    register(proc, "foreign", token="other")
    register(proc, "sell", token="yes")
    # Replace the owned registry's side with a real SELL registration.
    proc.register_order("sell", "cid", "yes", Side.SELL, 5, .2, "rdp")
    for run, closing in [({"unknown"}, set()), ({"foreign"}, set()),
                         ({"o1"}, {"unknown"}), ({"sell"}, set())]:
        gw = FakeGateway()
        result = await close_rdp_cycle(market, proc, gw, run, closing, deadline())
        assert not result.complete and result.reason == "INVALID_SCOPE"
        assert gw.calls == []


@pytest.mark.asyncio
async def test_already_closed_run_order_remains_in_final_inventory(ctx):
    _store, proc, market = ctx
    register(proc, "old")
    proc.on_trade(TradeEvent("yes", Side.BUY, .2, 2, "old-fill", TradeState.MATCHED, 1, "old"), "cid")
    proc.on_trade(TradeEvent("yes", Side.BUY, .2, 2, "old-fill", TradeState.CONFIRMED, 2, "old"), "cid")
    gw = FakeGateway(rows=[order_row("old", matched=2, status="CANCELED")],
                     trades=[trade_row("old", "old-fill", qty=2)])
    result = await close_rdp_cycle(market, proc, gw, {"old"}, set(), deadline())
    assert result.complete and result.inventory.qy == pytest.approx(2)


@pytest.mark.asyncio
async def test_unadmitted_fill_record_blocks_closure(ctx):
    _store, proc, market = ctx
    register(proc)
    # This intentionally bypasses processor admission to represent a confirmed record
    # whose inventory mutation has not been admitted by the authoritative path.
    from polymaker.state.tracker import TradeEvent
    ev = TradeEvent("yes", Side.BUY, .2, 1, "unadmitted", TradeState.CONFIRMED, 1, "o1")
    proc.fill_records[("unadmitted", "o1")] = ev
    gw = FakeGateway(rows=[order_row(matched=0, status="CANCELED")])
    result = await close_rdp_cycle(market, proc, gw, {"o1"}, set(), deadline())
    assert not result.complete and result.reason == "UNADMITTED_FILL"


@pytest.mark.asyncio
async def test_unadmitted_confirmed_record_is_admitted_by_existing_recovery_on_retry(ctx):
    store, proc, market = ctx
    register(proc)
    ev = TradeEvent("yes", Side.BUY, .2, 1, "recover-me", TradeState.CONFIRMED, 1, "o1")
    proc.fill_records[("recover-me", "o1")] = ev
    gw = FakeGateway(rows=[order_row(matched=0, status="CANCELED")])
    first = await close_rdp_cycle(market, proc, gw, {"o1"}, set(), deadline())
    assert not first.complete and first.reason == "UNADMITTED_FILL"
    assert store.position("yes").size == pytest.approx(0)
    gw.rows["o1"]["size_matched"] = "1"
    gw.trades = [trade_row(trade_id="recover-me", qty=1)]
    second = await close_rdp_cycle(market, proc, gw, {"o1"}, set(), deadline())
    assert second.complete and second.inventory.qy == pytest.approx(1)
    assert store.position("yes").size == pytest.approx(1)


@pytest.mark.asyncio
async def test_terminal_checksum_change_while_final_read_awaits_fails_closed(ctx):
    _store, proc, market = ctx
    register(proc)
    gw = FakeGateway(rows=[order_row(matched=1, status="CANCELED")], trades=[trade_row(qty=1)])
    started, release = asyncio.Event(), asyncio.Event()
    gw.block[("open_orders", 3)] = (started, release)
    task = asyncio.create_task(close_rdp_cycle(market, proc, gw, {"o1"}, set(), deadline()))
    await started.wait()
    proc.on_trade(TradeEvent("yes", Side.BUY, .2, 1, "late", TradeState.MATCHED, 3, "o1"), "cid")
    proc.on_trade(TradeEvent("yes", Side.BUY, .2, 1, "late", TradeState.CONFIRMED, 4, "o1"), "cid")
    # The REST terminal snapshot was size 1; authoritative accepted fills are now 2.
    release.set()
    result = await task
    assert not result.complete and result.inventory is None
    assert result.reason in {"TERMINAL_CHECKSUM_CHANGED", "UNRESOLVED_EVIDENCE"}


@pytest.mark.asyncio
@pytest.mark.parametrize("block_name,occurrence", [
    ("open_orders", 1), ("cancel", 1), ("open_orders", 2),
    ("get_order", 1), ("get_trades", 1), ("get_order", 2), ("open_orders", 3),
])
async def test_every_gateway_operation_is_deadline_bounded_and_quiescent(ctx, block_name, occurrence):
    _store, proc, market = ctx
    register(proc)
    gw = FakeGateway()
    started, release = asyncio.Event(), asyncio.Event()
    gw.block[(block_name, occurrence)] = (started, release)
    task = asyncio.create_task(close_rdp_cycle(market, proc, gw, {"o1"}, {"o1"},
        asyncio.get_running_loop().time() + .03))
    await started.wait()
    result = await task
    assert not result.complete and result.inventory is None
    assert result.reason == "OPERATIONAL_DEADLINE"
    calls = list(gw.calls)
    await asyncio.sleep(0)
    assert gw.calls == calls
    assert (block_name, occurrence) in gw.finished


@pytest.mark.asyncio
async def test_task_cancellation_propagates_and_stops_active_operation(ctx):
    _store, proc, market = ctx
    register(proc)
    gw = FakeGateway()
    started, release = asyncio.Event(), asyncio.Event()
    gw.block[("get_trades", 1)] = (started, release)
    task = asyncio.create_task(close_rdp_cycle(market, proc, gw, {"o1"}, {"o1"}, deadline()))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    calls = list(gw.calls)
    await asyncio.sleep(0)
    assert gw.calls == calls
    assert ("get_trades", 1) in gw.finished


@pytest.mark.asyncio
async def test_deadline_timeout_cancels_active_gateway_coroutine_without_background_work(ctx):
    _store, proc, market = ctx
    register(proc)
    gw = FakeGateway()
    started, release = asyncio.Event(), asyncio.Event()
    gw.block[("open_orders", 1)] = (started, release)
    task = asyncio.create_task(close_rdp_cycle(market, proc, gw, {"o1"}, {"o1"},
        asyncio.get_running_loop().time() + .03))
    await started.wait()
    result = await task
    assert not result.complete and result.inventory is None
    assert result.reason == "OPERATIONAL_DEADLINE"
    assert ("open_orders", 1) in gw.finished
    await asyncio.sleep(0)
    assert len([t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_deadline", [float("nan"), float("inf"), "later"])
async def test_nonfinite_or_unusable_deadline_fails_before_gateway_calls(ctx, bad_deadline):
    _store, proc, market = ctx
    register(proc)
    gw = FakeGateway()
    result = await close_rdp_cycle(market, proc, gw, {"o1"}, {"o1"}, bad_deadline)
    assert not result.complete and result.reason == "INVALID_DEADLINE"
    assert gw.calls == []


@pytest.mark.asyncio
async def test_already_expired_deadline_makes_no_gateway_calls_even_for_empty_scope(ctx):
    _store, proc, market = ctx
    gw = FakeGateway(rows=[])
    result = await close_rdp_cycle(market, proc, gw, set(), set(),
        asyncio.get_running_loop().time() - 1)
    assert not result.complete and result.reason == "OPERATIONAL_DEADLINE"
    assert gw.calls == []


@pytest.mark.asyncio
async def test_expiry_after_non_yielding_final_read_cannot_release_closure(ctx):
    _store, proc, market = ctx
    register(proc)
    gw = FakeGateway(rows=[order_row(status="CANCELED")])
    gw.blocking_delays[("open_orders", 3)] = .01
    result = await close_rdp_cycle(market, proc, gw, {"o1"}, set(),
        asyncio.get_running_loop().time() + .005)
    assert not result.complete and result.inventory is None
    assert result.reason == "OPERATIONAL_DEADLINE"


@pytest.mark.asyncio
async def test_late_live_order_observation_blocks_then_repeated_close_recovers(ctx):
    _store, proc, market = ctx
    register(proc)
    gw = FakeGateway(rows=[order_row(status="CANCELED")])
    gw.hooks[("open_orders", 3)] = lambda: proc.on_order(
        OrderEvent("o1", "yes", Side.BUY, .2, 5, False, 5, 0, "LIVE", (), "cid"), "cid")
    first = await close_rdp_cycle(market, proc, gw, {"o1"}, set(), deadline())
    assert not first.complete and first.inventory is None
    assert first.reason == "LOCAL_ORDER_REMAINS"
    assert [order.order_id for order in proc._store.orders_for("yes")] == ["o1"]
    second = await close_rdp_cycle(market, proc, gw, {"o1"}, {"o1"}, deadline())
    assert second.complete and proc._store.orders_for("yes") == []
