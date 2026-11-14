"""Live cash-target execution: synthetic SDK/HTTP, no real account access."""

import asyncio
from dataclasses import replace
from decimal import Decimal as D

import httpx
import pytest
import test_m4_live_taker as legacy
from m4_helpers import close, op
from py_clob_client_v2.http_helpers import helpers
from test_m4_live_task import evidence, serve

from polymaker.m4_live_config import M4LiveSettings, all_in_cost


def settings(mode="taker"):
    return M4LiveSettings(
        execution_revision="m4-taker-budget-v1" if mode == "taker" else "m4-maker-v1",
        execution={"mode": mode},
    )


async def fixture(tmp_path, monkeypatch, **kwargs):
    monkeypatch.setattr(legacy, "settings", settings)
    result = await legacy.market(tmp_path, monkeypatch, **kwargs)
    result[1].gateway._address = result[1].gateway._client.get_address()
    return result


async def test_budget_continues_after_old_share_target(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch)
    ep = next(iter(m.pending.values()))
    queries = []
    try:
        for qty, price in [("5", ".40"), ("1.9", ".71")]:
            await m.submit(ep)
            oid = m.ledger.row(ep.episode_id)["order_id"]
            order, trade = evidence(m, e, oid, qty, price=price)
            query, _ = serve(monkeypatch, t, order, trade)
            queries.append(query)
            await m.submit(ep)
        row = m.ledger.row(ep.episode_id)
        assert D(row["filled"]) == D("6.9") and D(row["spent"]) == D("3.46038")
        assert row["state"] == "RESERVED"
        await m.submit(ep)
        size = D(calls[-1]["order"]["takerAmount"]) / 1_000_000
        assert size == D("2.0") and all_in_cost(".71", size, legacy.FEES) + D(row["spent"]) <= 5
        assert len(calls) == 3
    finally:
        await close(m, e)
        t.close()
        for q in queries:
            q.close()


async def test_pending_matches_reserve_cash_but_allow_proven_residual(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".30")
    queries = []
    ep = next(iter(m.pending.values()))
    try:
        await m.submit(ep)
        oid = m.ledger.row(ep.episode_id)["order_id"]
        order, trade = evidence(m, e, oid, "5", status="MINED")
        q, _ = serve(monkeypatch, t, order, trade)
        queries.append(q)
        await m.submit(ep)
        row = m.ledger.row(ep.episode_id)
        assert (
            row["state"] == "RESERVED"
            and row["filled"] == "0"
            and m.ledger.pending_cost(row["id"]) > D("1.62")
        )
        await m.submit(ep)
        plan = m.ledger.plan(row["id"])
        assert (
            len(calls) == 2 and D(plan["planned_cost_usdc"]) + m.ledger.pending_cost(row["id"]) <= 5
        )
        trade["status"] = "CONFIRMED"
        await m.reconcile(oid)
        row = m.ledger.row(row["id"])
        assert D(row["filled"]) == 5 and row["order_id"] != oid and row["state"] == "ACK"
        assert m.ledger.pending_cost(row["id"]) == 0
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()
        for q in queries:
            q.close()


async def test_fee_estimate_to_confirmed_uses_same_tolerance(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".30")
    queries = []
    ep = next(iter(m.pending.values()))
    try:
        await m.submit(ep)
        oid = m.ledger.row(ep.episode_id)["order_id"]
        order, trade = evidence(m, e, oid, "5", status="MINED")
        q, _ = serve(monkeypatch, t, order, trade)
        queries.append(q)
        await m.reconcile(oid)
        trade["status"] = "CONFIRMED"
        trade["fee_usdc"] = ".07486"
        await m.reconcile(oid)
        assert m.ledger.attempt(oid)["state"] == "TERMINAL"
        assert D(m.ledger.row(ep.episode_id)["spent"]) == D("1.62486")
        await m.reconcile(oid)
        assert D(m.ledger.row(ep.episode_id)["spent"]) == D("1.62486")
    finally:
        await close(m, e)
        t.close()
        for q in queries:
            q.close()


async def test_clock_boundary_and_valid_finer_tick(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch)
    ep = next(iter(m.pending.values()))
    try:
        f.books["1"] = replace(f.books["1"], source_time=f.wall() + 0.002, tick=D(".001"))
        f.verify_ticks({"1": D(".001")})
        assert m.guard(ep), m.guard_reason
        f.books["1"] = replace(f.books["1"], source_time=f.wall() + 0.00201)
        assert not f.m1.legal(f.books[ep.token_id], c.now)
        assert m.guard(ep)  # An already admitted capped FAK remains executable.
    finally:
        await close(m, e)
        t.close()


async def test_claim_survives_deadline_during_durable_plan(tmp_path, monkeypatch):
    from polymaker.m4_live_budget_market import BudgetMarket

    original = BudgetMarket.collect
    monkeypatch.setattr(BudgetMarket, "collect", lambda self: None)
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch)
    ep = f.m1.selected
    planning = m.ledger.plan_signal

    def expire(*args):
        result = planning(*args)
        c.now = ep.order_deadline + 0.001
        return result

    monkeypatch.setattr(m.ledger, "plan_signal", expire)
    try:
        original(m)
        assert ep.episode_id in m.pending and m.ledger.row(ep.episode_id)["state"] == "OBSERVED"
        assert f.m1.active_reason((ep.episode_id,), None) == "M4_OPPORTUNITY_EXPIRED"
    finally:
        await close(m, e)
        t.close()


async def test_slow_owned_read_does_not_block_other_signal_first_post(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    try:
        first = next(iter(m.pending.values()))
        await m.submit(first)
        second = op("second", price=".709", token="2")
        m.ledger.observe(second, minimum=5, elapsed=m.elapsed())
        m.ledger.plan_signal(second.episode_id, f.books["2"].tick, 5)
        m.pending[second.episode_id] = second
        monkeypatch.setattr(m, "collect", lambda: None)

        async def slow(oid):
            entered.set()
            await release.wait()

        monkeypatch.setattr(m, "reconcile", slow)
        await m.advance()
        await asyncio.wait_for(entered.wait(), 2)
        assert second.episode_id in m.post_jobs
        await asyncio.wait_for(m.post_jobs[second.episode_id], 2)
        assert len(calls) == 2 and m.ledger.row(second.episode_id)["posts"] == 1
        assert m.ledger.committed() <= 10
    finally:
        release.set()
        await close(m, e)
        t.close()


@pytest.mark.parametrize(
    "fault", ["404", "quantity", "owner", "foreign", "missing_trade", "page", "live", "id", "fee"]
)
async def test_unknown_never_rebuys_without_proof(tmp_path, monkeypatch, fault):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, response="TIMEOUT", price=".30")
    q = None
    ep = next(iter(m.pending.values()))
    try:
        await m.submit(ep)
        oid = m.ledger.row(ep.episode_id)["order_id"]
        reserve = m.ledger.committed()
        order, trade = evidence(m, e, oid, "5")
        if fault == "quantity":
            order["size_matched"] = "6"
        if fault == "owner":
            order["maker_address"] = "0x" + "f" * 40
        if fault == "foreign":
            trade["taker_order_id"] = "0x" + "f" * 64
        if fault == "missing_trade":
            order["associate_trades"] = []
        if fault == "live":
            order["status"] = "LIVE"
        if fault == "id":
            order["id"] = "0x" + "f" * 64
        if fault == "fee":
            trade["fee_usdc"] = ".07489"
        q, _ = serve(
            monkeypatch, t, order, trade, missing=fault == "404", incomplete=fault == "page"
        )
        await m.submit(ep)
        await m.submit(ep)
        assert len(calls) == 1 and m.ledger.committed() == reserve
        assert m.ledger.row(ep.episode_id)["state"] == "UNKNOWN"
    finally:
        await close(m, e)
        t.close()
        if q:
            q.close()


async def test_zero_fill_retries_unique_orders_and_one_budget(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, response="KILLED")
    try:
        ep = next(iter(m.pending.values()))
        for delay in (0.1, 0.2, 0.4, 0.8):
            await m.submit(ep)
            assert m.retry_after[ep.episode_id] - c.now == pytest.approx(delay)
        orders = m.ledger.attempts()
        assert len(calls) == len({a["order_id"] for a in orders}) == 4
        assert m.ledger.committed() == 0 and m.ledger.row(ep.episode_id)["spent"] == "0.000000"
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()


async def test_pending_then_conflicting_fee_blocks_then_recovers(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".30")
    q = None
    ep = next(iter(m.pending.values()))
    try:
        await m.submit(ep)
        oid = m.ledger.row(ep.episode_id)["order_id"]
        order, trade = evidence(m, e, oid, "5", status="MINED")
        q, _ = serve(monkeypatch, t, order, trade)
        await m.reconcile(oid)
        reserve = m.ledger.committed()
        trade["fee_usdc"] = ".09"
        trade["status"] = "CONFIRMED"
        await m.reconcile(oid)
        await m.submit(ep)
        assert (
            len(calls) == 1
            and m.ledger.committed() == reserve
            and m.ledger.unresolved(ep.episode_id)
        )
        trade["fee_usdc"] = ".07486"
        await m.reconcile(oid)
        await m.submit(ep)
        assert len(calls) == 2 and D(m.ledger.row(ep.episode_id)["spent"]) == D("1.62486")
    finally:
        await close(m, e)
        t.close()
        if q:
            q.close()


async def test_restart_preserves_pending_and_active_reservations(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".30")
    ep = next(iter(m.pending.values()))
    await m.submit(ep)
    oid = m.ledger.row(ep.episode_id)["order_id"]
    order, trade = evidence(m, e, oid, "5", status="MINED")
    q, _ = serve(monkeypatch, t, order, trade)
    await m.reconcile(oid)
    await m.submit(ep)
    before = m.ledger.metric()
    await close(m, e)
    q.close()
    t.close()
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, recover=True, price=".30")
    try:
        assert m.ledger.metric() == before
        assert m.ledger.pending_cost(ep.episode_id) > 0
        # No order proof in this transport; restore must keep the current order unresolved.
        await m.submit(ep)
        assert not calls
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()


async def test_out_of_order_confirmations_preserve_total_budget(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".30")
    queries = []
    ep = next(iter(m.pending.values()))
    try:
        await m.submit(ep)
        first = m.ledger.row(ep.episode_id)["order_id"]
        order1, trade1 = evidence(m, e, first, "5", status="MINED")
        q, _ = serve(monkeypatch, t, order1, trade1)
        queries.append(q)
        await m.reconcile(first)
        await m.submit(ep)
        second = m.ledger.row(ep.episode_id)["order_id"]
        qty = m.ledger.plan(ep.episode_id)["quantity"]
        order2, trade2 = evidence(m, e, second, qty)
        q, _ = serve(monkeypatch, t, order2, trade2)
        queries.append(q)
        await m.reconcile(second)
        assert m.ledger.row(ep.episode_id)["state"] == "WAIT_SETTLEMENT"
        trade1["status"] = "CONFIRMED"
        q, _ = serve(monkeypatch, t, order1, trade1)
        queries.append(q)
        await m.reconcile(first)
        row = m.ledger.row(ep.episode_id)
        assert row["state"] == "DUST" and D(row["spent"]) < 5
        assert D(row["filled"]) == 5 + D(qty) and m.ledger.pending_cost(ep.episode_id) == 0
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()
        for q in queries:
            q.close()


async def test_failed_settlement_releases_only_its_own_pending_cost(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".30")
    q = None
    ep = next(iter(m.pending.values()))
    try:
        await m.submit(ep)
        oid = m.ledger.row(ep.episode_id)["order_id"]
        order, trade = evidence(m, e, oid, "5", status="RETRYING")
        q, _ = serve(monkeypatch, t, order, trade)
        await m.reconcile(oid)
        assert m.ledger.pending_cost(ep.episode_id) > 0
        trade["status"] = "FAILED"
        await m.reconcile(oid)
        assert m.ledger.committed() == 0 and D(m.ledger.row(ep.episode_id)["spent"]) == 0
        await m.submit(ep)
        assert len(calls) == 2 and m.ledger.attempts()[-1]["order_id"] != oid
    finally:
        await close(m, e)
        t.close()
        if q:
            q.close()


async def test_actual_debit_waiver_stops_further_buys(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".98")
    q = None
    ep = next(iter(m.pending.values()))
    try:
        await m.submit(ep)
        oid = m.ledger.row(ep.episode_id)["order_id"]
        order, trade = evidence(m, e, oid, "9.9", price=".50")
        q, _ = serve(monkeypatch, t, order, trade)
        await m.reconcile(oid)
        await m.submit(ep)
        assert len(calls) == 1 and D(m.ledger.row(ep.episode_id)["spent"]) == D("5.12325")
        assert m.ledger.row(ep.episode_id)["state"] == "BUDGET_EXHAUSTED"
    finally:
        await close(m, e)
        t.close()
        if q:
            q.close()


async def test_final_native_guard_stops_t240_even_after_signing(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch)
    ep = next(iter(m.pending.values()))
    original = e.gateway.prepare

    async def late(*args, **kwargs):
        signed = await original(*args, **kwargs)
        c.now += 240
        return signed

    monkeypatch.setattr(e.gateway, "prepare", late)
    try:
        await m.submit(ep)
        assert not calls and all(a["state"] == "UNSENT" for a in m.ledger.attempts())
    finally:
        await close(m, e)
        t.close()


async def test_fak_receipt_recovers_order_404_without_account_scan(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".30")
    q = None
    ep = next(iter(m.pending.values()))
    try:
        await m.submit(ep)
        oid = m.ledger.row(ep.episode_id)["order_id"]
        order, trade = evidence(m, e, oid, "5")
        # Simulate the complete positive receipt persisted before the order endpoint lags.
        original_facts = m.ledger.facts
        receipt = {
            "success": True,
            "orderID": oid,
            "status": "matched",
            "makingAmount": "1.55",
            "takingAmount": "5",
            "tradeIDs": [trade["id"]],
        }
        monkeypatch.setattr(
            m.ledger,
            "facts",
            lambda kind: (
                [{"order_id": oid, "response": receipt}]
                if kind == "owned_post_receipt"
                else original_facts(kind)
            ),
        )
        reads = []

        def wire(req):
            reads.append(str(req.url))
            if req.url.path.startswith("/data/order/"):
                return httpx.Response(404, json={"error": "not found"})
            assert req.url.path == "/data/trades" and req.url.params["id"] == trade["id"]
            return httpx.Response(
                200, json={"data": [trade], "next_cursor": "LTE=", "count": 1, "limit": 100}
            )

        q = httpx.Client(transport=httpx.MockTransport(wire))
        monkeypatch.setattr(helpers, "_http_client", q)
        await m.reconcile(oid)
        assert (
            m.ledger.attempt(oid)["state"] == "TERMINAL"
            and D(m.ledger.row(ep.episode_id)["filled"]) == 5
        )
        assert len(reads) == 2
    finally:
        await close(m, e)
        t.close()
        if q:
            q.close()


@pytest.mark.parametrize("completed", [True, False])
async def test_ended_market_reconciles_read_only_after_t300(tmp_path, monkeypatch, completed):
    old, e0, f0, c0, calls0, t0 = await fixture(tmp_path / "past", monkeypatch, price=".30")
    ep0 = next(iter(old.pending.values()))
    await old.submit(ep0)
    oid = old.ledger.row(ep0.episode_id)["order_id"]
    order, trade = evidence(old, e0, oid, "5")
    if completed:
        old.complete_window()
    else:
        # Crash after a POST, before complete_window. The market epoch still proves
        # that only read recovery, never new buying, is permitted.
        meta = old.ledger.get("market")
        stem, stamp = meta["slug"].rsplit("-", 1)
        meta["slug"] = f"{stem}-{int(stamp) - 300}"
        old.ledger.set("market", meta)
    await close(old, e0)
    t0.close()
    (tmp_path / "past/risk.sqlite3").rename(tmp_path / "ended.sqlite3")
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".30")
    q = None
    try:
        order["maker_address"] = e.gateway.funder
        q, reads = serve(monkeypatch, t, order, trade)
        await m.reconcile_archive()
        from polymaker.m4_live_budget import BudgetLedger

        saved = BudgetLedger.metadata(tmp_path / "ended.sqlite3")
        assert saved["buy_closed"] is True and saved["owned_orders_need_reconciliation"] is False
        assert not calls and len(reads) == 2
        assert any("id=trade-1" in u for u in reads)
    finally:
        await close(m, e)
        t.close()
        if q:
            q.close()


async def test_archive_client_cannot_post(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch)
    try:
        e.gateway._client.m4_read_only = True
        await m.submit(next(iter(m.pending.values())))
        assert not calls and all(a["state"] == "UNSENT" for a in m.ledger.attempts())
    finally:
        await close(m, e)
        t.close()


@pytest.mark.parametrize(
    "kind,amount,allowed",
    [
        ("book", 0.200, True),
        ("book", 0.20001, False),
        ("queue", 0.020, True),
        ("queue", 0.02001, False),
        ("source", 0.500, True),
        ("source", 0.50001, False),
        ("future", 0.002, True),
        ("future", 0.00201, False),
    ],
)
async def test_admitted_task_does_not_reapply_signal_marketdata_age_limits(
    tmp_path, monkeypatch, kind, amount, allowed
):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch)
    try:
        ep = next(iter(m.pending.values()))
        if kind == "book":
            c.now += amount
        if kind == "queue":
            f.m1_pending_received.append(c.now - amount)
        if kind == "source":
            f.books["1"] = replace(f.books["1"], source_time=f.wall() - amount)
        if kind == "future":
            f.books["1"] = replace(f.books["1"], source_time=f.wall() + amount)
        # The detector still applies its unchanged market-data age limits;
        # a durable capped FAK task is allowed to submit without fresh depth.
        assert m.guard(ep)
    finally:
        await close(m, e)
        t.close()


@pytest.mark.parametrize("amount,allowed", [(0.002, True), (0.00201, False)])
async def test_source_acceptance_uses_same_clock_tolerance(tmp_path, monkeypatch, amount, allowed):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch)
    try:
        result = f.m1.before_event(
            "book",
            {"timestamp": str(D(str(f.wall() + amount)) * 1000)},
            f.books["1"],
            c.now,
            f.wall(),
        )
        assert result is allowed
    finally:
        await close(m, e)
        t.close()


@pytest.mark.parametrize("cost,expected_quantity", [("3.5", "2.0"), ("3.9", "0"), ("4", "0")])
async def test_cash_remainder_minimum_never_rounds_up(
    tmp_path, monkeypatch, cost, expected_quantity
):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch)
    q = None
    ep = next(iter(m.pending.values()))
    try:
        await m.submit(ep)
        oid = m.ledger.row(ep.episode_id)["order_id"]
        # Inject a ledger-level verified debit to isolate minimum-order planning.
        m.ledger.terminal(oid, "5", cost, {"kind": "SYNTHETIC_VERIFIED_DEBIT"})
        await m.submit(ep)
        if expected_quantity == "0":
            assert len(calls) == 1 and m.ledger.row(ep.episode_id)["state"] == "DUST"
        else:
            assert len(calls) == 2 and D(calls[-1]["order"]["takerAmount"]) / 1_000_000 == D(
                expected_quantity
            )
            assert all_in_cost(".71", expected_quantity, legacy.FEES) + D(cost) <= 5
    finally:
        await close(m, e)
        t.close()
        if q:
            q.close()


async def test_manual_stop_blocks_both_post_and_new_reads(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch)
    try:
        ep = next(iter(m.pending.values()))
        m.stop.set()
        await m.submit(ep)
        await m.advance()
        assert not calls and not m.read_jobs and m.archive_task is None
    finally:
        await close(m, e)
        t.close()


async def test_allowed_fee_updates_keep_pending_bound_stable(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".30")
    q = None
    try:
        ep = next(iter(m.pending.values()))
        await m.submit(ep)
        oid = m.ledger.row(ep.episode_id)["order_id"]
        order, trade = evidence(m, e, oid, "5", status="MINED")
        q, _ = serve(monkeypatch, t, order, trade)
        trade["fee_usdc"] = ".07486"
        await m.reconcile(oid)
        bound = m.ledger.pending_cost(ep.episode_id)
        trade["fee_usdc"] = ".07488"
        await m.reconcile(oid)
        assert not m.ledger.unresolved(ep.episode_id)
        assert m.ledger.pending_cost(ep.episode_id) == bound
        trade["status"] = "CONFIRMED"
        await m.reconcile(oid)
        assert D(m.ledger.row(ep.episode_id)["spent"]) == D("1.62488")
    finally:
        await close(m, e)
        t.close()
        if q:
            q.close()


async def test_recovery_rejects_understated_pending_reservation(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".30")
    q = None
    try:
        ep = next(iter(m.pending.values()))
        await m.submit(ep)
        oid = m.ledger.row(ep.episode_id)["order_id"]
        order, trade = evidence(m, e, oid, "5", status="MINED")
        q, _ = serve(monkeypatch, t, order, trade)
        await m.reconcile(oid)
        m.ledger.set("settling:" + oid, {"quantity": "5", "cost_bound": "0"})
        with pytest.raises(ValueError, match="M4_TASK_RECOVERY_PENDING_COST"):
            m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()
        if q:
            q.close()


async def test_recovery_rejects_missing_prior_attempt_in_budget_basis(tmp_path, monkeypatch):
    import json

    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, response="KILLED")
    try:
        ep = next(iter(m.pending.values()))
        await m.submit(ep)
        await m.submit(ep)
        a = m.ledger.attempts()[-1]
        plan = json.loads(a["plan"])
        plan["budget_basis"] = []
        m.ledger.db.execute(
            "UPDATE taker_attempts SET plan=? WHERE order_id=?", (json.dumps(plan), a["order_id"])
        )
        m.ledger.db.commit()
        with pytest.raises(ValueError, match="M4_BUDGET_BASIS_INCOMPLETE"):
            m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()


async def test_foreign_order_and_trade_reads_remain_forbidden(tmp_path, monkeypatch):
    from py_clob_client_v2.clob_types import TradeParams

    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch)
    try:
        with pytest.raises(Exception, match="M4_OWNED_ORDER_READ_SCOPE_INVALID"):
            await e.gateway._io(e.gateway._client.get_order, "0x" + "f" * 64)
        with pytest.raises(Exception, match="M4_OWNED_ORDER_READ_SCOPE_INVALID"):
            await e.gateway._io(
                e.gateway._client.get_trades_paginated,
                TradeParams(id="foreign", market=e.meta.condition_id, asset_id="1"),
                None,
            )
        assert not calls
    finally:
        await close(m, e)
        t.close()


def test_live_clock_adapter_preserves_signal_features():
    from m4_helpers import fixture_feed, form

    from polymaker.m4_live_numeric import install_live_clock_boundaries

    baseline, cb = fixture_feed()
    live, cl = fixture_feed()
    install_live_clock_boundaries(live)
    form(baseline, cb)
    form(live, cl)
    a, b = baseline.m1.selected, live.m1.selected
    assert a and b and a.anchor_bid_price == b.anchor_bid_price
    assert a.order_deadline == b.order_deadline and a.signal_start_mono == b.signal_start_mono
    assert baseline.m1.counts == live.m1.counts


async def test_round_layer_accepts_same_two_ms_boundary(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch)
    try:
        f.books["1"] = replace(f.books["1"], source_time=f.wall() + 0.002)
        book = f.books["1"]
        row = {"timestamp": str(D(str(book.source_time)) * 1000)}
        assert f.m1.before_event("book", row, book, c.now, f.wall())
        assert f.m1.rounds.before("book", row, book, c.now, f.wall())
        assert f.m1.legal(book, c.now)
    finally:
        await close(m, e)
        t.close()


async def test_unknown_detected_at_native_start_prevents_topup(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".30")
    q = None
    try:
        ep = next(iter(m.pending.values()))
        await m.submit(ep)
        oid = m.ledger.row(ep.episode_id)["order_id"]
        order, trade = evidence(m, e, oid, "5", status="MINED")
        q, _ = serve(monkeypatch, t, order, trade)
        await m.reconcile(oid)
        original = m.ledger.post_start

        def late_unknown(key, **kwargs):
            m.ledger.block_reconciliation(oid, {"reason": "SIMULATED_NEW_CONFLICT"})
            return original(key, **kwargs)

        monkeypatch.setattr(m.ledger, "post_start", late_unknown)
        await m.submit(ep)
        assert len(calls) == 1 and m.ledger.unresolved(ep.episode_id)
        assert m.ledger.attempts()[-1]["state"] == "UNSENT"
    finally:
        await close(m, e)
        t.close()
        if q:
            q.close()


async def test_current_post_finishes_receipt_before_owned_queries(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch)
    sent, release = asyncio.Event(), asyncio.Event()
    native = e.gateway.send_prepared
    reads = []
    monkeypatch.setattr(m, "collect", lambda: None)

    async def delayed(*args, **kwargs):
        response = await native(*args, **kwargs)
        sent.set()
        await release.wait()
        return response

    async def read(oid):
        reads.append(oid)

    monkeypatch.setattr(e.gateway, "send_prepared", delayed)
    monkeypatch.setattr(m, "reconcile", read)
    try:
        await m.advance()
        await asyncio.wait_for(sent.wait(), 2)
        ep = next(iter(m.posting_keys))
        assert m.ledger.row(ep)["state"] == "UNKNOWN"
        await m.advance()
        await asyncio.sleep(0)
        assert not reads
        release.set()
        await m.post_jobs[ep]
        assert m.ledger.row(ep)["state"] == "ACK" and e.fatal is None
        await m.advance()
        await asyncio.gather(*m.read_jobs.values())
        assert reads == [m.ledger.row(ep)["order_id"]] and len(calls) == 1
    finally:
        release.set()
        await close(m, e)
        t.close()


async def test_current_queries_rotate_instead_of_starving_later_orders(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".30")
    ep = next(iter(m.pending.values()))
    q = None
    try:
        for i in range(3):
            await m.submit(ep)
            oid = m.ledger.row(ep.episode_id)["order_id"]
            order, trade = evidence(m, e, oid, "1", status="MINED")
            trade["id"] = f"trade-{i}"
            order["associate_trades"] = [trade["id"]]
            if q:
                q.close()
            q, _ = serve(monkeypatch, t, order, trade)
            await m.reconcile(oid)
        ids = [a["order_id"] for a in m.ledger.attempts()]
        assert len(ids) == 3
        monkeypatch.setattr(m, "collect", lambda: None)
        # Isolate read scheduling; no fourth buy or archive work in this test.
        m.pending.clear()
        m.last_archive = c.now
        queried = []

        async def slow(oid):
            queried.append(oid)
            c.now += 1

        monkeypatch.setattr(m, "reconcile", slow)
        await m.advance()
        await asyncio.gather(*m.read_jobs.values())
        # Both first orders are eligible again before scheduling the third.
        c.now += 3
        m.last_account = c.now
        await m.advance()
        await asyncio.gather(*m.read_jobs.values())
        assert queried[:3] == ids
    finally:
        await close(m, e)
        t.close()
        if q:
            q.close()


@pytest.mark.parametrize("extra", ["0", ".000001"])
def test_cash_proof_and_ledger_share_hard_reservation_boundary(extra):
    from types import SimpleNamespace

    from polymaker.m4_live_config import gross_risk_cost
    from polymaker.m4_live_numeric import validate_cash_fills

    spec = {"price": D(".31"), "quantity": D("5"), "fees": legacy.FEES}
    gross = gross_risk_cost(spec["price"], spec["quantity"], spec["fees"])
    fill = SimpleNamespace(principal_usdc="1.55", fee_usdc=str(gross - D("1.55") + D(extra)))
    if D(extra):
        with pytest.raises(ValueError, match="M4_FILL_EXCEEDS_RESERVED_CASH"):
            validate_cash_fills([fill], spec)
    else:
        validate_cash_fills([fill], spec)


async def test_queried_zero_fill_uses_backoff_before_next_post(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch)
    q = None
    try:
        ep = next(iter(m.pending.values()))
        await m.submit(ep)
        oid = m.ledger.row(ep.episode_id)["order_id"]
        order, trade = evidence(m, e, oid, "0")
        order.update(status="CANCELED", associate_trades=[])
        q, _ = serve(monkeypatch, t, order, trade)
        await m.read_order(oid)
        assert m.ledger.attempt(oid)["state"] == "TERMINAL"
        assert m.retry_after[ep.episode_id] - c.now == pytest.approx(0.1)
        monkeypatch.setattr(m, "collect", lambda: None)
        await m.advance_entries(c.now)
        assert not m.post_jobs and len(calls) == 1
    finally:
        await close(m, e)
        t.close()
        if q:
            q.close()
