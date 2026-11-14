"""Continuous live FAK: real SDK signing, isolated SQLite, synthetic HTTP only."""

import json
from dataclasses import replace
from decimal import Decimal as D

import httpx
import pytest
import test_m4_live_taker as legacy
from m4_helpers import close
from py_clob_client_v2.http_helpers import helpers

from polymaker.m4_live_config import M4LiveSettings, all_in_cost


def task_settings(mode="taker"):
    return M4LiveSettings(execution_revision="m4-taker-task-v1", execution={"mode": mode})


async def fixture(tmp_path, monkeypatch, **kwargs):
    monkeypatch.setattr(legacy, "settings", task_settings)
    result = await legacy.market(tmp_path, monkeypatch, **kwargs)
    result[1].gateway._address = result[1].gateway._client.get_address()
    return result


def evidence(m, e, oid, quantity, *, status="CONFIRMED", price=None):
    a = m.ledger.attempt(oid)
    plan, quote = json.loads(a["plan"]), json.loads(a["quote"])
    quantity, price = D(quantity), D(price or plan["limit_price"])
    tid = "trade-" + str(a["seq"])
    order = {
        "id": oid,
        "market": e.meta.condition_id,
        "asset_id": quote["token_id"],
        "maker_address": e.gateway.funder,
        "side": "BUY",
        "order_type": "FAK",
        "price": plan["limit_price"],
        "original_size": plan["quantity"],
        "size_matched": str(quantity),
        "status": "MATCHED" if quantity else "CANCELED",
        "associate_trades": [tid] if quantity else [],
    }
    trade = {
        "id": tid,
        "market": e.meta.condition_id,
        "asset_id": quote["token_id"],
        "trader_side": "TAKER",
        "taker_order_id": oid,
        "side": "BUY",
        "status": status,
        "price": str(price),
        "size": str(quantity),
        "maker_orders": [
            {
                "order_id": "external-maker",
                "asset_id": quote["token_id"],
                "side": "SELL",
                "price": str(price),
                "matched_amount": str(quantity),
            }
        ],
    }
    return order, trade


def serve(monkeypatch, previous, order, trade, *, missing=False, incomplete=False):
    reads = []

    def wire(request):
        if request.method == "POST":
            return previous._transport.handle_request(request)
        reads.append(str(request.url))
        if missing:
            return httpx.Response(404, json={"error": "not found"})
        if request.url.path.startswith("/data/order/"):
            return httpx.Response(200, json=order)
        assert request.url.path == "/data/trades"
        assert request.url.params["id"] == trade["id"]
        return httpx.Response(
            200,
            json={
                "data": [trade],
                "next_cursor": None if incomplete else "LTE=",
                "count": 1,
                "limit": 100,
            },
        )

    transport = httpx.Client(transport=httpx.MockTransport(wire))
    monkeypatch.setattr(helpers, "_http_client", transport)
    return transport, reads


async def test_exact_killed_retries_with_new_signed_id(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, response="KILLED")
    ep = next(iter(m.pending.values()))
    try:
        await m.submit(ep)
        first = m.ledger.attempts()[0]
        assert first["state"] == "TERMINAL" and m.ledger.committed() == 0
        await m.submit(ep)
        attempts = m.ledger.attempts()
        assert len(calls) == len(attempts) == 2
        assert attempts[0]["order_id"] != attempts[1]["order_id"]
        assert m.ledger.row(ep.episode_id)["filled"] == "0"
        assert all(x["orderType"] == "FAK" and x["postOnly"] is False for x in calls)
    finally:
        await close(m, e)
        t.close()


async def test_partial_confirmed_only_buys_residual_and_deduplicates(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".30")
    ep = next(iter(m.pending.values()))
    query = None
    try:
        await m.submit(ep)
        oid = m.ledger.row(ep.episode_id)["order_id"]
        target = D(m.ledger.root_plan(ep.episode_id)["quantity"])
        order, trade = evidence(m, e, oid, "5")
        query, reads = serve(monkeypatch, t, order, trade)
        await m.submit(ep)  # resolve, never combine query with another POST
        row = m.ledger.row(ep.episode_id)
        assert row["state"] == "RESERVED" and D(row["filled"]) == 5, row
        assert D(row["spent"]) == all_in_cost(".31", 5, legacy.FEES)
        await m.reconcile(oid)  # terminal idempotent; no second debit
        assert m.ledger.row(ep.episode_id)["spent"] == row["spent"]
        await m.submit(ep)
        assert len(calls) == 2
        assert D(calls[-1]["order"]["takerAmount"]) / 1_000_000 == target - 5
        assert all(oid in u or "id=trade-1" in u for u in reads)
        assert m.ledger.committed() <= 10
    finally:
        await close(m, e)
        t.close()
        if query:
            query.close()


@pytest.mark.parametrize(
    "fault",
    [
        "404",
        "missing_trade",
        "unfinal",
        "quantity",
        "foreign",
        "owner",
        "page",
        "id",
        "live",
        "failed",
    ],
)
async def test_unknown_cannot_authorize_another_post(tmp_path, monkeypatch, fault):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, response="TIMEOUT", price=".30")
    ep = next(iter(m.pending.values()))
    query = None
    try:
        await m.submit(ep)
        oid = m.ledger.row(ep.episode_id)["order_id"]
        reserve = m.ledger.committed()
        order, trade = evidence(m, e, oid, "5")
        if fault == "missing_trade":
            order["associate_trades"] = []
        if fault == "unfinal":
            trade["status"] = "MINED"
        if fault == "quantity":
            order["size_matched"] = "6"
        if fault == "foreign":
            trade["taker_order_id"] = "0x" + "f" * 64
        if fault == "owner":
            order["maker_address"] = "0x" + "f" * 40
        if fault == "id":
            order["id"] = "0x" + "f" * 64
        if fault == "live":
            order["status"] = "LIVE"
        if fault == "failed":
            trade["status"] = "RETRYING"
        query, _ = serve(
            monkeypatch, t, order, trade, missing=fault == "404", incomplete=fault == "page"
        )
        for _ in range(2):
            await m.submit(ep)
        assert len(calls) == 1 and m.ledger.committed() == reserve
        assert m.ledger.row(ep.episode_id)["state"] == "UNKNOWN"
    finally:
        await close(m, e)
        t.close()
        if query:
            query.close()


@pytest.mark.parametrize("qty,expected", [("6", "DUST"), ("2", "RESERVED"), ("1.15", "RESERVED")])
async def test_residual_minimum_or_notional_never_rounds_up(tmp_path, monkeypatch, qty, expected):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch)
    ep = next(iter(m.pending.values()))
    query = None
    try:
        await m.submit(ep)
        oid = m.ledger.row(ep.episode_id)["order_id"]
        order, trade = evidence(m, e, oid, qty)
        query, _ = serve(monkeypatch, t, order, trade)
        await m.submit(ep)
        await m.submit(ep)
        if expected == "DUST":
            assert len(calls) == 1 and m.ledger.row(ep.episode_id)["state"] == "DUST"
        else:
            assert len(calls) == 2
            q = D(calls[-1]["order"]["takerAmount"]) / 1_000_000
            target = D(m.ledger.root_plan(ep.episode_id)["quantity"])
            assert q == (target - D(qty)).quantize(D(".1"), rounding="ROUND_FLOOR")
            assert q >= 5 or q * D(".71") > 1
    finally:
        await close(m, e)
        t.close()
        if query:
            query.close()


async def test_restart_reconciles_owned_id_before_post(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, response="TIMEOUT", price=".30")
    ep = next(iter(m.pending.values()))
    await m.submit(ep)
    oid, reserve = m.ledger.row(ep.episode_id)["order_id"], m.ledger.committed()
    await close(m, e)
    t.close()
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, recover=True, price=".30")
    query = None
    try:
        assert m.ledger.committed() == reserve and m.ledger.owns_order(oid)
        order, trade = evidence(m, e, oid, "0")
        query, _ = serve(monkeypatch, t, order, trade)
        await m.submit(ep)
        assert not calls and m.ledger.committed() == 0
        await m.submit(ep)
        assert len(calls) == 1 and m.ledger.row(ep.episode_id)["order_id"] != oid
    finally:
        await close(m, e)
        t.close()
        if query:
            query.close()


@pytest.mark.parametrize("boundary", ["cutoff", "manual", "ask", "tick"])
async def test_no_residual_post_past_guards(tmp_path, monkeypatch, boundary):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, response="KILLED")
    ep = next(iter(m.pending.values()))
    try:
        await m.submit(ep)
        if boundary == "cutoff":
            e.official_time += 221
        if boundary == "manual":
            e.stop_event.set()
        if boundary == "ask":
            f.books[ep.token_id] = replace(f.books[ep.token_id], asks=((D(".99"), D(100)),))
        if boundary == "tick":
            f.books[ep.token_id] = replace(f.books[ep.token_id], tick=D(".001"))
        await m.submit(ep)
        assert len(calls) == 1
    finally:
        await close(m, e)
        t.close()


async def test_read_permission_exact_owned_only(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch)
    client = e.gateway._client
    try:
        for path, params in [
            ("/data/orders", None),
            ("/data/trades", None),
            ("/data/order/0x" + "f" * 64, None),
            (
                "/data/trades",
                {
                    "id": "foreign",
                    "market": e.meta.condition_id,
                    "asset_id": e.meta.yes.token_id,
                    "next_cursor": "MA==",
                },
            ),
        ]:
            with pytest.raises(ValueError):
                client._execution_get("https://clob.polymarket.com" + path, params=params)
        assert not calls
    finally:
        await close(m, e)
        t.close()


def test_minimum_or_contract_strict_one_dollar_boundary():
    from polymaker.m4_live_task import minimum_order_ok

    assert minimum_order_ok(D(".20"), D("5"))
    assert not minimum_order_ok(D(".50"), D("2"))
    assert minimum_order_ok(D(".50"), D("2.1"))
    assert not minimum_order_ok(D(".71"), D("1.4"))
    assert minimum_order_ok(D(".71"), D("1.5"))


@pytest.mark.parametrize("status", ["CONFIRMED", "FAILED"])
async def test_terminal_fill_or_failed_trade_restart_accounting(tmp_path, monkeypatch, status):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".30")
    ep = next(iter(m.pending.values()))
    await m.submit(ep)
    oid = m.ledger.row(ep.episode_id)["order_id"]
    order, trade = evidence(m, e, oid, "5", status=status)
    query, _ = serve(monkeypatch, t, order, trade)
    await m.submit(ep)
    row = m.ledger.row(ep.episode_id)
    assert D(row["filled"]) == (5 if status == "CONFIRMED" else 0)
    before = m.ledger.metric()
    await close(m, e)
    query.close()
    t.close()
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, recover=True, price=".30")
    try:
        assert m.ledger.metric() == before
        await m.submit(next(iter(m.pending.values())))
        assert len(calls) == 1
        target = D(m.ledger.root_plan(ep.episode_id)["quantity"])
        assert D(calls[0]["order"]["takerAmount"]) / 1_000_000 == target - (
            5 if status == "CONFIRMED" else 0
        )
    finally:
        await close(m, e)
        t.close()


async def test_price_improvement_extra_shares_stops_without_topup(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".98")
    ep = next(iter(m.pending.values()))
    query = None
    try:
        await m.submit(ep)
        oid = m.ledger.row(ep.episode_id)["order_id"]
        order, trade = evidence(m, e, oid, "9.9", price=".50")
        query, _ = serve(monkeypatch, t, order, trade)
        await m.submit(ep)
        row = m.ledger.row(ep.episode_id)
        assert row["state"] == "COMPLETE" and D(row["filled"]) == D("9.9")
        assert D(row["spent"]) == D("5.12325")  # previously accepted actual-debit waiver
        await m.submit(ep)
        assert len(calls) == 1
    finally:
        await close(m, e)
        t.close()
        if query:
            query.close()


async def test_post_matched_receipt_cannot_be_replaced_with_stale_zero(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, response="PARTIAL")
    ep = next(iter(m.pending.values()))
    query = None
    try:
        await m.submit(ep)
        oid = m.ledger.row(ep.episode_id)["order_id"]
        order, trade = evidence(m, e, oid, "0")
        query, _ = serve(monkeypatch, t, order, trade)
        await m.submit(ep)
        await m.submit(ep)
        assert len(calls) == 1 and m.ledger.row(ep.episode_id)["state"] == "UNKNOWN"
    finally:
        await close(m, e)
        t.close()
        if query:
            query.close()


async def test_matched_trade_can_confirm_but_not_disappear(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".30")
    ep = next(iter(m.pending.values()))
    query = None
    try:
        await m.submit(ep)
        oid = m.ledger.row(ep.episode_id)["order_id"]
        order, trade = evidence(m, e, oid, "5", status="MATCHED")
        query, _ = serve(monkeypatch, t, order, trade)
        await m.submit(ep)
        reserve = m.ledger.committed()
        assert m.ledger.row(ep.episode_id)["state"] == "UNKNOWN"
        order["size_matched"], order["associate_trades"], order["status"] = "0", [], "CANCELED"
        await m.submit(ep)
        assert m.ledger.committed() == reserve and len(calls) == 1
        order["size_matched"], order["associate_trades"], order["status"] = (
            "5",
            [trade["id"]],
            "MATCHED",
        )
        trade["status"] = "CONFIRMED"
        await m.submit(ep)
        assert D(m.ledger.row(ep.episode_id)["filled"]) == 5
    finally:
        await close(m, e)
        t.close()
        if query:
            query.close()


async def test_final_native_guard_rechecks_t240_after_signing(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch)
    ep = next(iter(m.pending.values()))
    original = e.gateway.prepare

    async def slow(*args, **kwargs):
        prepared = await original(*args, **kwargs)
        e.official_time += 221
        return prepared

    monkeypatch.setattr(e.gateway, "prepare", slow)
    try:
        await m.submit(ep)
        assert not calls and m.ledger.row(ep.episode_id)["state"] == "CUTOFF"
        assert m.ledger.attempts()[0]["state"] == "UNSENT"
    finally:
        await close(m, e)
        t.close()


async def test_after_t240_resolve_but_never_resend(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, response="TIMEOUT")
    ep = next(iter(m.pending.values()))
    query = None
    try:
        await m.submit(ep)
        oid = m.ledger.row(ep.episode_id)["order_id"]
        order, trade = evidence(m, e, oid, "0")
        query, _ = serve(monkeypatch, t, order, trade)
        e.official_time += 221
        await m.submit(ep)
        await m.submit(ep)
        assert len(calls) == 1 and m.ledger.row(ep.episode_id)["state"] == "CUTOFF"
        assert m.ledger.committed() == 0
    finally:
        await close(m, e)
        t.close()
        if query:
            query.close()


async def test_cash_shortage_waits_and_does_not_reset_task_budget(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".30")
    ep = next(iter(m.pending.values()))
    try:
        m.account["official_balance_usd"] = ".5"
        await m.submit(ep)
        row = m.ledger.row(ep.episode_id)
        assert not calls and row["state"] == "RESERVED"
        assert row["reason"] == "M4_WAIT_REMAINDER_FUNDS"
        assert ep.episode_id in m.pending
        m.account["official_balance_usd"] = "40"
        await m.submit(ep)
        assert len(calls) == 1
        plan = m.ledger.plan(ep.episode_id)
        assert D(plan["prior_spent_usdc"]) + D(plan["planned_cost_usdc"]) <= 5
    finally:
        await close(m, e)
        t.close()


async def test_direction_quota_survives_zero_fill_retry(tmp_path, monkeypatch):
    from m4_helpers import op

    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, response="KILLED")
    ep = next(iter(m.pending.values()))
    try:
        await m.submit(ep)
        second = op(98)
        m.ledger.observe(second, minimum=5, elapsed=20)
        m.ledger.plan_signal(second.episode_id, ".01", 5)
        assert not m.ledger.reserve(second.episode_id, spendable=100)
        assert m.ledger.row(second.episode_id)["reason"] == "M4_DIRECTION_OPPORTUNITY_LIMIT"
        await m.submit(ep)
        assert len(calls) == 2
    finally:
        await close(m, e)
        t.close()
