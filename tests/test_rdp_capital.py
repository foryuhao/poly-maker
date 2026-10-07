from __future__ import annotations

import asyncio
from dataclasses import asdict
from decimal import Decimal

import pytest

from polymaker.domain import MarketMeta, OpenOrder, OrderState, Quote, Side, TokenMeta, TradeState
from polymaker.execution.filltruth import ReconciliationOutcome
from polymaker.execution.rdp_lifecycle import RDPCloseResult, close_rdp_cycle
from polymaker.marketdata.orderbook import BookView
from polymaker.risk.rdp_capital import (
    CAPITAL,
    NORMAL_REPAIR_RESERVE,
    RDPMode,
    evaluate_rdp_capital_gate,
    project_rdp_capital,
)
from polymaker.state.store import StateStore
from polymaker.state.tracker import OrderEvent, TradeEvent, UserEventProcessor
from polymaker.strategy.rdp_accounting import snapshot_inventory
from polymaker.strategy.rdp_quotes import RDPQuoteDecision, build_normal_quotes


def market(condition="cid", yes="yes", no="no"):
    return MarketMeta(condition, "BTC up", "btc-up", (TokenMeta(yes, "Yes"), TokenMeta(no, "No")),
        .01, False, 5, 0, 0, 0, 0, 0, False, None, None)


def trade_row(oid, trade_id, token, qty, price, status="CONFIRMED"):
    return {"id": trade_id, "status": status, "market": "cid", "asset_id": "other",
        "side": "SELL", "maker_orders": [{"order_id": oid, "asset_id": token,
            "side": "BUY", "matched_amount": str(qty), "price": str(price)}]}


def order_row(oid, token, qty, price, matched, status):
    return {"id": oid, "asset_id": token, "side": "BUY", "price": str(price),
        "original_size": str(qty), "size_matched": str(matched), "status": status,
        "market": "cid"}


class FakeCloseGateway:
    def __init__(self, rows, trades=(), *, cancel_result=True, cancel_mutates=True):
        self.rows = {r["id"]: dict(r) for r in rows}
        self.trades = list(trades)
        self.cancel_result = cancel_result
        self.cancel_mutates = cancel_mutates
        self.calls = []

    async def open_orders(self):
        self.calls.append("open_orders")
        live = []
        for row in self.rows.values():
            remaining = float(row["original_size"]) - float(row["size_matched"])
            if row["status"].upper() not in {"CANCELED", "CANCELLED", "MATCHED"} and remaining > 0:
                live.append(OpenOrder(row["id"], row["asset_id"], Side(row["side"]),
                    float(row["price"]), remaining, OrderState.LIVE))
        return live

    async def cancel(self, order_ids):
        self.calls.append(("cancel", tuple(order_ids)))
        if self.cancel_mutates:
            for oid in order_ids:
                self.rows[oid]["status"] = "CANCELED"
        return self.cancel_result

    async def get_order(self, order_id):
        self.calls.append(("get_order", order_id))
        return dict(self.rows[order_id])

    async def get_trades(self, *, market=None, asset_id=None):
        self.calls.append(("get_trades", market))
        return list(self.trades)

    async def place(self, *_args, **_kwargs):
        raise AssertionError("capital projection must never place orders")


@pytest.fixture
def ctx():
    store = StateStore(":memory:")
    m = market()
    proc = UserEventProcessor(store)
    yield store, proc, m
    store.close()


def register(proc, oid, token="yes", qty=5, price=.4, condition="cid", side=Side.BUY):
    proc.register_order(oid, condition, token, side, qty, price, "rdp")


def add_fill(proc, oid, token, qty, price, trade_id=None, *, status=TradeState.CONFIRMED):
    tid = trade_id or f"{oid}-trade"
    proc.on_trade(TradeEvent(token, Side.BUY, price, qty, tid, TradeState.MATCHED, 1, oid), "cid")
    if status is not TradeState.MATCHED:
        proc.on_trade(TradeEvent(token, Side.BUY, price, qty, tid, status, 2, oid), "cid")


def projection(m, proc, run, orders=(), *, market_ok=True, order_ok=True, close=None):
    return project_rdp_capital(m, proc, set(run), list(orders),
        market_state_reliable=market_ok, order_state_reliable=order_ok, close_evidence=close)


def normal_candidate(yes_price=.29, no_price=.59, yes_qty=5, no_qty=5):
    return RDPQuoteDecision((Quote("yes", Side.BUY, yes_price, yes_qty),
        Quote("no", Side.BUY, no_price, no_qty)), None)


def rebalance_candidate(token="no", price=.98, qty=5):
    return RDPQuoteDecision((Quote(token, Side.BUY, price, qty),), None)


async def close_evidence(m, proc, rows, trades=(), *, cancel_result=True, cancel_mutates=True):
    ids = {row["id"] for row in rows}
    gateway = FakeCloseGateway(rows, trades, cancel_result=cancel_result,
                               cancel_mutates=cancel_mutates)
    return await close_rdp_cycle(m, proc, gateway, ids, ids,
        asyncio.get_running_loop().time() + 2)


async def seed_closed_inventory(m, proc, fills):
    rows, trades, ids = [], [], set()
    for oid, token, qty, fill_price, order_price in fills:
        register(proc, oid, token, qty, order_price)
        add_fill(proc, oid, token, qty, fill_price)
        ids.add(oid)
        rows.append(order_row(oid, token, qty, order_price, qty, "MATCHED"))
        trades.append(trade_row(oid, f"{oid}-trade", token, qty, fill_price))
    result = await close_evidence(m, proc, rows, trades)
    assert result.complete
    return ids, result


def test_empty_run_projects_exact_thirty_dollars(ctx):
    _store, proc, m = ctx
    result = projection(m, proc, set())
    assert result.certified
    snap = result.snapshot
    assert (snap.capital, snap.inventory_cost, snap.reserved_cash, snap.free_cash) == (
        Decimal("30.00"), Decimal("0"), Decimal("0"), Decimal("30.00"))


def test_confirmed_cost_and_two_resting_orders_have_no_double_count(ctx):
    _store, proc, m = ctx
    register(proc, "a", "yes", 5, .4)
    register(proc, "b", "no", 5, .5)
    add_fill(proc, "a", "yes", 2, .3)
    orders = [OpenOrder("a", "yes", Side.BUY, .4, 3),
              OpenOrder("b", "no", Side.BUY, .5, 5)]
    result = projection(m, proc, {"a", "b"}, orders)
    assert result.certified
    snap = result.snapshot
    assert snap.inventory_cost == Decimal("0.6")
    assert snap.reserved_cash == Decimal("3.7")
    assert snap.free_cash == Decimal("25.7")
    assert snap.capital == snap.inventory_cost + snap.reserved_cash + snap.free_cash


def test_partial_confirmed_fill_uses_actual_improved_cost_and_remaining_posted_reserve(ctx):
    _store, proc, m = ctx
    register(proc, "o", "yes", 5, .4)
    add_fill(proc, "o", "yes", 2, .3)
    result = projection(m, proc, {"o"}, [OpenOrder("o", "yes", Side.BUY, .4, 3)])
    assert result.certified
    assert result.snapshot.inventory_cost == Decimal("0.6")
    assert result.snapshot.reserved_cash == Decimal("1.2")
    assert result.snapshot.free_cash == Decimal("28.2")


@pytest.mark.parametrize("status", [TradeState.MATCHED, TradeState.MINED, TradeState.RETRYING])
def test_pending_fill_evidence_rejects_even_if_caller_marks_state_reliable(ctx, status):
    _store, proc, m = ctx
    register(proc, "o")
    add_fill(proc, "o", "yes", 1, .3, status=status)
    result = projection(m, proc, {"o"}, [OpenOrder("o", "yes", Side.BUY, .4, 4)])
    assert not result.certified and result.snapshot is None
    assert result.reason == "PENDING_FILL"


def test_failed_quarantined_unresolved_and_unadmitted_fills_reject(ctx):
    _store, proc, m = ctx
    register(proc, "failed")
    proc.on_trade(TradeEvent("yes", Side.BUY, .3, 1, "f", TradeState.FAILED, 1, "failed"), "cid")
    assert projection(m, proc, {"failed"}).reason == "FAILED_OR_QUARANTINED_EVIDENCE"

    register(proc, "quarantine")
    proc.quarantine_order("quarantine", "unresolved", notify=False)
    assert projection(m, proc, {"quarantine"}).reason == "FAILED_OR_QUARANTINED_EVIDENCE"

    register(proc, "unresolved")
    proc.fill_records[("bad", "unresolved")] = TradeEvent(
        "yes", Side.BUY, .3, 1, "bad", TradeState.CONFIRMED, 1, "unresolved")
    proc.quarantined_fill_keys.add(("bad", "unresolved"))
    assert projection(m, proc, {"unresolved"}).reason == "UNRESOLVED_EVIDENCE"

    register(proc, "unadmitted")
    proc.fill_records[("only-confirmed", "unadmitted")] = TradeEvent(
        "yes", Side.BUY, .3, 1, "only-confirmed", TradeState.CONFIRMED, 1, "unadmitted")
    assert projection(m, proc, {"unadmitted"}).reason == "UNADMITTED_FILL"


def test_resting_order_checksum_duplicate_invalid_and_foreign_rows_reject(ctx):
    _store, proc, m = ctx
    register(proc, "o", "yes", 5, .4)
    assert projection(m, proc, {"o"}, [OpenOrder("o", "yes", Side.BUY, .4, 4)]).reason == \
        "OPEN_ORDER_CHECKSUM_MISMATCH"
    duplicate = OpenOrder("o", "yes", Side.BUY, .4, 5)
    assert projection(m, proc, {"o"}, [duplicate, duplicate]).reason == \
        "DUPLICATE_OR_INVALID_OPEN_ORDER"
    assert projection(m, proc, {"o"}, [OpenOrder("o", "yes", Side.SELL, .4, 5)]).reason == \
        "INVALID_OPEN_ORDER_STATE"
    assert projection(m, proc, {"o"}, [OpenOrder("o", "yes", Side.BUY, float("nan"), 5)]).reason == \
        "INVALID_OPEN_ORDER_ECONOMICS"
    assert projection(m, proc, {"o"}, [OpenOrder("manual", "yes", Side.BUY, .4, 5)]).reason == \
        "FOREIGN_OPEN_ORDER"
    assert projection(m, proc, {"o"}, [OpenOrder("o", "third", Side.BUY, .4, 5)]).reason == \
        "OPEN_ORDER_OUTSIDE_MARKET"


def test_foreign_condition_sell_foreign_token_and_unreliable_inputs_reject(ctx):
    _store, proc, m = ctx
    register(proc, "foreign-condition", condition="other")
    assert projection(m, proc, {"foreign-condition"}).reason == "FOREIGN_CONDITION"
    register(proc, "sell", side=Side.SELL)
    assert projection(m, proc, {"sell"}).reason == "NON_BUY_ORDER"
    register(proc, "foreign-token", token="third")
    assert projection(m, proc, {"foreign-token"}).reason == "FOREIGN_TOKEN"
    register(proc, "o")
    assert projection(m, proc, {"o"}, market_ok=False).reason == "UNRELIABLE_STATE"
    assert projection(m, proc, {"o"}, order_ok=False).reason == "UNRELIABLE_STATE"


@pytest.mark.parametrize("qty,price", [(0, .4), (-1, .4), (5, 0), (5, 1),
                                        (float("nan"), .4), (5, float("inf"))])
def test_invalid_registered_submitted_economics_reject(ctx, qty, price):
    _store, proc, m = ctx
    register(proc, "bad", qty=qty, price=price)
    result = projection(m, proc, {"bad"})
    assert not result.certified and result.reason == "INVALID_SUBMITTED_ECONOMICS"


def test_missing_order_requires_complete_close_evidence(ctx):
    _store, proc, m = ctx
    register(proc, "o")
    result = projection(m, proc, {"o"})
    assert not result.certified and result.reason == "UNPROVED_ORDER_ABSENCE"


@pytest.mark.asyncio
async def test_real_close_barrier_releases_canceled_reservation_and_uncertain_close_does_not(ctx):
    _store, proc, m = ctx
    register(proc, "closed", qty=5, price=.4)
    row = order_row("closed", "yes", 5, .4, 0, "LIVE")
    successful = await close_evidence(m, proc, [row])
    assert successful.complete
    released = projection(m, proc, {"closed"}, close=successful)
    assert released.certified
    assert (released.snapshot.reserved_cash, released.snapshot.free_cash) == (Decimal("0"), CAPITAL)

    register(proc, "uncertain", qty=5, price=.4)
    uncertain = await close_evidence(m, proc,
        [order_row("uncertain", "yes", 5, .4, 0, "LIVE")],
        cancel_result=False, cancel_mutates=False)
    assert not uncertain.complete
    open_state = [OpenOrder("uncertain", "yes", Side.BUY, .4, 5)]
    still_reserved = projection(m, proc, {"uncertain"}, open_state)
    assert still_reserved.certified and still_reserved.snapshot.reserved_cash == Decimal("2.0")
    no_proof = projection(m, proc, {"uncertain"}, [])
    assert not no_proof.certified


@pytest.mark.asyncio
async def test_reappeared_local_order_blocks_old_close_until_a_renewed_close(ctx):
    _store, proc, m = ctx
    register(proc, "closed", qty=5, price=.4)
    first_close = await close_evidence(m, proc,
        [order_row("closed", "yes", 5, .4, 0, "LIVE")])
    assert first_close.complete

    # A newer accepted local LIVE observation contradicts the old close. Keep
    # it intact and fail closed even if the supplied official snapshot is empty.
    proc.on_order(OrderEvent("closed", "yes", Side.BUY, .4, 5, False, 5, 0,
        "LIVE", (), "cid"), "cid")
    assert [order.order_id for order in proc._store.orders_for("yes")] == ["closed"]
    stale = projection(m, proc, {"closed"}, [], close=first_close)
    assert not stale.certified and stale.snapshot is None
    assert stale.reason == "CLOSED_ORDER_LOCAL_OPEN"
    assert [order.order_id for order in proc._store.orders_for("yes")] == ["closed"]

    renewed = await close_evidence(m, proc,
        [order_row("closed", "yes", 5, .4, 0, "LIVE")])
    assert renewed.complete
    recovered = projection(m, proc, {"closed"}, [], close=renewed)
    assert recovered.certified
    assert recovered.snapshot.reserved_cash == Decimal("0")
    assert recovered.snapshot.free_cash == Decimal("30.00")
    assert proc._store.orders_for("yes") == []


@pytest.mark.asyncio
async def test_fresh_confirmed_evidence_invalidates_prior_close_evidence(ctx):
    _store, proc, m = ctx
    register(proc, "old", qty=5, price=.4)
    add_fill(proc, "old", "yes", 1, .3)
    closed = await close_evidence(m, proc,
        [order_row("old", "yes", 5, .4, 1, "CANCELED")],
        [trade_row("old", "old-trade", "yes", 1, .3)])
    assert closed.complete
    proc.on_trade(TradeEvent("yes", Side.BUY, .3, 1, "late", TradeState.MATCHED, 3, "old"), "cid")
    proc.on_trade(TradeEvent("yes", Side.BUY, .3, 1, "late", TradeState.CONFIRMED, 4, "old"), "cid")
    stale = projection(m, proc, {"old"}, close=closed)
    assert not stale.certified and stale.reason == "STALE_CLOSE_EVIDENCE"


@pytest.mark.asyncio
async def test_prior_closed_subset_and_subsequent_registered_resting_cycle(ctx):
    _store, proc, m = ctx
    prior_ids, prior = await seed_closed_inventory(m, proc, [
        ("old", "yes", 2, .3, .4)])
    register(proc, "next", "no", 5, .5)
    result = projection(m, proc, prior_ids | {"next"},
        [OpenOrder("next", "no", Side.BUY, .5, 5)], close=prior)
    assert result.certified
    assert result.snapshot.inventory_cost == Decimal("0.6")
    assert result.snapshot.reserved_cash == Decimal("2.5")


@pytest.mark.asyncio
async def test_normal_exact_cash_boundary_passes_and_just_below_fails(ctx):
    store, proc, m = ctx
    ids, close = await seed_closed_inventory(m, proc, [
        ("y", "yes", 20, .515, .515), ("n", "no", 20, .515, .515)])
    assert close.inventory.inventory_cost == pytest.approx(20.6)
    projected = projection(m, proc, ids, close=close)
    assert projected.certified and projected.snapshot.free_cash == Decimal("9.400")
    candidate = normal_candidate(.29, .59)
    admitted = evaluate_rdp_capital_gate(RDPMode.NORMAL, candidate, projected)
    assert admitted.admitted and admitted.new_notional == Decimal("4.40")
    assert admitted.free_cash_after_new == Decimal("5.000")
    assert admitted.protected_amount == NORMAL_REPAIR_RESERVE

    # Raise inventory cost by one cent while keeping all fill evidence valid.
    _store2, proc2, m2 = ctx
    ids2, close2 = await seed_closed_inventory(m2, proc2, [
        ("y2", "yes", 20, .51525, .51525), ("n2", "no", 20, .51525, .51525)])
    below = projection(m2, proc2, ids2, close=close2)
    assert below.certified and below.snapshot.free_cash == Decimal("9.39000")
    rejected = evaluate_rdp_capital_gate(RDPMode.NORMAL, candidate, below)
    assert not rejected.admitted and rejected.reason == "NORMAL_REPAIR_RESERVE"
    assert rejected.free_cash_after_new == Decimal("4.99000")
    almost_boundary = normal_candidate(.29, .590000000000002)
    exact_rejection = evaluate_rdp_capital_gate(RDPMode.NORMAL, almost_boundary, projected)
    assert not exact_rejection.admitted and exact_rejection.reason == "NORMAL_REPAIR_RESERVE"
    assert exact_rejection.free_cash_after_new == Decimal("4.999999999999990")


@pytest.mark.asyncio
async def test_decimal_money_sum_handles_existing_float_accounting_producer(ctx):
    _store, proc, m = ctx
    ids, close = await seed_closed_inventory(m, proc, [
        ("fraction-a", "yes", .1, .1, .1),
        ("fraction-b", "no", .2, .1, .1),
    ])
    # Existing float accounting accumulates binary products; the capital
    # projection converts the same accepted source fills before summing.
    assert close.inventory.inventory_cost == pytest.approx(.03)
    result = projection(m, proc, ids, close=close)
    assert result.certified and result.snapshot.inventory_cost == Decimal("0.03")
    assert result.snapshot.free_cash == Decimal("29.97")


@pytest.mark.asyncio
async def test_approved_23_cost_fixture_rejects_normal_even_if_candidate_prices_change(ctx):
    _store, proc, m = ctx
    ids, close = await seed_closed_inventory(m, proc, [
        ("y", "yes", 25, .46, .46), ("n", "no", 25, .46, .46)])
    projected = projection(m, proc, ids, close=close)
    assert projected.certified
    assert projected.snapshot.inventory_cost == Decimal("23.00")
    assert projected.snapshot.free_cash == Decimal("7.00")
    rejected = evaluate_rdp_capital_gate(RDPMode.NORMAL, normal_candidate(), projected)
    assert not rejected.admitted and rejected.reason == "NORMAL_REPAIR_RESERVE"
    assert rejected.new_notional == Decimal("4.40")
    assert rejected.free_cash_after_new == Decimal("2.60")
    cheaper = evaluate_rdp_capital_gate(RDPMode.NORMAL,
        normal_candidate(.01, .01), projected)
    assert cheaper.admitted and cheaper.protected_amount == Decimal("5.00")
    assert rejected.protected_amount == cheaper.protected_amount == Decimal("5.00")


@pytest.mark.asyncio
async def test_filled_no_clip_and_verified_yes_cancel_keep_cash_above_reserve(ctx):
    _store, proc, m = ctx
    base_fills = [("old-y", "yes", 20, .515, .515), ("old-n", "no", 20, .515, .515)]
    prior_ids = set()
    base_rows, base_trades = [], []
    for oid, token, qty, fill_price, order_price in base_fills:
        register(proc, oid, token, qty, order_price)
        add_fill(proc, oid, token, qty, fill_price)
        prior_ids.add(oid)
        base_rows.append(order_row(oid, token, qty, order_price, qty, "MATCHED"))
        base_trades.append(trade_row(oid, f"{oid}-trade", token, qty, fill_price))
    register(proc, "yes-rest", "yes", 5, .29)
    register(proc, "no-fill", "no", 5, .59)
    add_fill(proc, "no-fill", "no", 5, .59)
    cycle_close = await close_evidence(m, proc, base_rows + [
        order_row("yes-rest", "yes", 5, .29, 0, "LIVE"),
        order_row("no-fill", "no", 5, .59, 5, "MATCHED")],
        base_trades + [trade_row("no-fill", "no-fill-trade", "no", 5, .59)])
    assert cycle_close.complete
    run = prior_ids | {"yes-rest", "no-fill"}
    updated = projection(m, proc, run, [], close=cycle_close)
    assert updated.certified
    assert updated.snapshot.inventory_cost == Decimal("23.55")
    assert updated.snapshot.free_cash == Decimal("6.45")
    assert updated.snapshot.reserved_cash == Decimal("0")
    assert store_position(proc, "no") == pytest.approx(25)


def store_position(proc, token):
    return proc._store.position(token).size


@pytest.mark.asyncio
@pytest.mark.parametrize(("token", "fill_qty", "fill_cost", "remaining"), [
    ("yes", 5, Decimal("1.45"), Decimal("0")),
    ("yes", 2.5, Decimal("0.725"), Decimal("2.5")),
    ("no", 5, Decimal("2.95"), Decimal("0")),
    ("no", 1.25, Decimal("0.7375"), Decimal("3.75")),
])
async def test_admitted_normal_dual_pair_fill_outcomes_preserve_reserve_and_release(
    token, fill_qty, fill_cost, remaining, ctx,
):
    _store, proc, m = ctx
    base_rows, base_trades, base_ids = [], [], set()
    for oid, side, qty, price in (
        ("base-y", "yes", 20, .515), ("base-n", "no", 20, .515),
    ):
        register(proc, oid, side, qty, price)
        add_fill(proc, oid, side, qty, price)
        base_ids.add(oid)
        base_rows.append(order_row(oid, side, qty, price, qty, "MATCHED"))
        base_trades.append(trade_row(oid, f"{oid}-trade", side, qty, price))
    base_close = await close_evidence(m, proc, base_rows, base_trades)
    assert base_close.complete

    before = projection(m, proc, base_ids, [], close=base_close)
    assert before.certified
    assert before.snapshot.inventory_cost == Decimal("20.60")
    assert before.snapshot.reserved_cash == Decimal("0")
    assert before.snapshot.free_cash == Decimal("9.40")
    candidate = normal_candidate(.29, .59, 5, 5)
    admitted = evaluate_rdp_capital_gate(RDPMode.NORMAL, candidate, before)
    assert admitted.admitted
    assert admitted.new_notional == Decimal("4.40")
    assert admitted.free_cash_after_new == Decimal("5.00")

    # Model the already-admitted actual NORMAL pair as registered owned orders.
    yes_id, no_id = f"pair-{token}-yes", f"pair-{token}-no"
    register(proc, yes_id, "yes", 5, .29)
    register(proc, no_id, "no", 5, .59)
    run_ids = base_ids | {yes_id, no_id}
    filled_id = yes_id if token == "yes" else no_id
    other_id = no_id if token == "yes" else yes_id
    filled_price = .29 if token == "yes" else .59
    other_token = "no" if token == "yes" else "yes"
    other_price = .59 if token == "yes" else .29
    add_fill(proc, filled_id, token, fill_qty, filled_price)
    filled_trade = trade_row(filled_id, f"{filled_id}-trade", token,
        fill_qty, filled_price)

    # A full fill has no resting row; prove that order absent while retaining
    # the opposite maker order as a live reservation. A partial fill remains
    # visible in the authoritative order snapshot with only its remainder.
    live_rows = []
    if remaining > 0:
        live_rows.append(OpenOrder(filled_id, token, Side.BUY, filled_price, float(remaining)))
    live_rows.append(OpenOrder(other_id, other_token, Side.BUY, other_price, 5))
    if remaining == 0:
        partial_close = await close_evidence(m, proc, base_rows + [
            order_row(filled_id, token, 5, filled_price, fill_qty, "MATCHED")],
            base_trades + [filled_trade])
        assert partial_close.complete
    else:
        partial_close = base_close
    after_fill = projection(m, proc, run_ids, live_rows, close=partial_close)
    assert after_fill.certified
    assert after_fill.snapshot.inventory_cost == Decimal("20.60") + fill_cost
    expected_reserved = Decimal("4.40") - fill_cost
    assert after_fill.snapshot.reserved_cash == expected_reserved
    assert after_fill.snapshot.free_cash == Decimal("5.00")

    # Closing both remaining orders through the real close barrier releases
    # only unspent reservation; confirmed acquisition cost stays in inventory.
    pair_rows = [
        order_row(yes_id, "yes", 5, .29,
            fill_qty if token == "yes" else 0,
            "MATCHED" if token == "yes" and remaining == 0 else "LIVE"),
        order_row(no_id, "no", 5, .59,
            fill_qty if token == "no" else 0,
            "MATCHED" if token == "no" and remaining == 0 else "LIVE"),
    ]
    terminal_close = await close_evidence(m, proc, base_rows + pair_rows,
        base_trades + [filled_trade])
    assert terminal_close.complete
    after_close = projection(m, proc, run_ids, [], close=terminal_close)
    assert after_close.certified
    assert after_close.snapshot.inventory_cost == Decimal("20.60") + fill_cost
    assert after_close.snapshot.reserved_cash == Decimal("0")
    assert after_close.snapshot.free_cash == Decimal("9.40") - fill_cost
    assert after_close.snapshot.free_cash >= Decimal("5.00")


@pytest.mark.asyncio
async def test_rebalance_spends_reserve_at_exact_cash_and_rejects_less(ctx):
    _store, proc, m = ctx
    ids, close = await seed_closed_inventory(m, proc, [
        ("y", "yes", 25, .6, .6), ("n", "no", 20, .505, .505)])
    projected = projection(m, proc, ids, close=close)
    assert projected.certified and projected.snapshot.inventory.delta == 5
    assert projected.snapshot.free_cash == Decimal("4.900")
    passed = evaluate_rdp_capital_gate(RDPMode.REBALANCE,
        rebalance_candidate("no", .98, 5), projected)
    assert passed.admitted
    assert passed.new_notional == Decimal("4.90")
    assert passed.free_cash_after_new == Decimal("0.000")
    assert passed.protected_amount == Decimal("0")

    _s2, p2, m2 = ctx
    ids2, close2 = await seed_closed_inventory(m2, p2, [
        ("y2", "yes", 25, .6, .6), ("n2", "no", 20, .5055, .5055)])
    less = projection(m2, p2, ids2, close=close2)
    assert less.certified and less.snapshot.free_cash == Decimal("4.8900")
    denied = evaluate_rdp_capital_gate(RDPMode.REBALANCE,
        rebalance_candidate("no", .98, 5), less)
    assert not denied.admitted and denied.reason == "INSUFFICIENT_FREE_CASH"
    assert denied.protected_amount == Decimal("0")


@pytest.mark.asyncio
async def test_fractional_real_accounting_and_quote_candidate_interoperate(ctx):
    _store, proc, m = ctx
    register(proc, "fy", "yes", .3, .5)
    register(proc, "fn", "no", .1, .5)
    add_fill(proc, "fy", "yes", .3, .4)
    add_fill(proc, "fn", "no", .1, .3)
    closed = await close_evidence(m, proc, [
        order_row("fy", "yes", .3, .5, .3, "MATCHED"),
        order_row("fn", "no", .1, .5, .1, "MATCHED")], [
        trade_row("fy", "fy-trade", "yes", .3, .4),
        trade_row("fn", "fn-trade", "no", .1, .3)])
    assert closed.complete
    register(proc, "fr", "yes", .125, .5)
    result = projection(m, proc, {"fy", "fn", "fr"}, [
        OpenOrder("fr", "yes", Side.BUY, .5, .125)], close=closed)
    assert result.certified and result.snapshot.inventory_cost == Decimal("0.15")
    yes_book = BookView(.56, 10, .60, 10, None, None, 10, 10)
    no_book = BookView(.42, 10, .48, 10, None, None, 10, 10)
    normal = build_normal_quotes(m, result.snapshot.inventory, yes_book, no_book, .01, .01)
    assert normal.has_candidate
    gate = evaluate_rdp_capital_gate(RDPMode.NORMAL, normal, result)
    assert gate.admitted and gate.new_notional == Decimal("4.882")


@pytest.mark.parametrize("candidate", [
    RDPQuoteDecision((), None),
    RDPQuoteDecision((Quote("yes", Side.SELL, .2, 1),), None),
    RDPQuoteDecision((Quote("third", Side.BUY, .2, 1),), None),
    RDPQuoteDecision((Quote("yes", Side.BUY, float("nan"), 1),), None),
    RDPQuoteDecision((Quote("yes", Side.BUY, .2, float("inf")),), None),
    RDPQuoteDecision((Quote("yes", Side.BUY, .2, 1),), None, False),
    RDPQuoteDecision((Quote("yes", Side.BUY, .2, 1),), "candidate rejected"),
])
def test_invalid_or_empty_candidate_rejects(ctx, candidate):
    _store, proc, m = ctx
    projected = projection(m, proc, set())
    result = evaluate_rdp_capital_gate(RDPMode.NORMAL, candidate, projected)
    assert not result.admitted


def test_invalid_mode_uncertified_projection_and_mode_delta_mismatch_reject(ctx):
    _store, proc, m = ctx
    projected = projection(m, proc, set())
    candidate = normal_candidate(.2, .2, 1, 1)
    assert evaluate_rdp_capital_gate("STOP", candidate, projected).reason == "INVALID_MODE"
    uncertified = project_rdp_capital(m, proc, {"missing"}, [],
        market_state_reliable=True, order_state_reliable=True)
    assert evaluate_rdp_capital_gate(RDPMode.NORMAL, candidate, uncertified).reason == \
        "UNCERTIFIED_CAPITAL"
    assert evaluate_rdp_capital_gate(RDPMode.REBALANCE, candidate, projected).reason == \
        "MODE_INVENTORY_MISMATCH"


def test_overcommitment_and_invalid_projection_inputs_fail_closed(ctx):
    _store, proc, m = ctx
    register(proc, "o", "yes", 40, .9)
    add_fill(proc, "o", "yes", 40, .9)
    evidence = RDPCloseResult(True, None, (), (("o", ReconciliationOutcome.COMPLETE),),
                              snapshot_inventory(m, proc, {"o"}))
    over = projection(m, proc, {"o"}, close=evidence)
    assert not over.certified and over.reason == "OVERCOMMITTED"
    assert project_rdp_capital(m, proc, {"o"}, [], market_state_reliable=True,
        order_state_reliable=True).reason == "UNPROVED_ORDER_ABSENCE"
    assert project_rdp_capital(m, proc, {"o"}, (), market_state_reliable=True,
        order_state_reliable=True).reason == "INVALID_INPUT"


def _capture(store, proc, orders, close):
    return (
        tuple(sorted((key, repr(value)) for key, value in store.positions.items())),
        tuple(sorted((key, repr(value)) for key, value in store.orders.items())),
        tuple(sorted(proc.fill_records.items())),
        frozenset(proc.applied_fill_keys), frozenset(proc.quarantined_fill_keys),
        frozenset(proc.quarantined_orders),
        tuple((o.order_id, o.token_id, o.side, o.price, o.size, o.state) for o in orders),
        asdict(close) if close is not None else None,
    )


def test_accepted_and_rejected_projections_preserve_independent_input_captures(ctx):
    store, proc, m = ctx
    register(proc, "o", "yes", 5, .4)
    orders = [OpenOrder("o", "yes", Side.BUY, .4, 5)]
    before = _capture(store, proc, orders, None)
    good = projection(m, proc, {"o"}, orders)
    bad = projection(m, proc, {"o"}, [OpenOrder("manual", "yes", Side.BUY, .4, 1)])
    assert good.certified and not bad.certified
    assert _capture(store, proc, orders, None) == before
