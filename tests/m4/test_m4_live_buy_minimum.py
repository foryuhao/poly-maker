"""Exchange BUY principal floor: synthetic HTTP and disposable ledgers only."""

import json
from dataclasses import asdict
from decimal import Decimal as D

import pytest
from m4_helpers import close
from test_m4_live_best_ask import fixture, move
from test_m4_live_task import evidence, serve

from polymaker.domain import Side
from polymaker.m4_live_budget import minimum_fak_buy_ok
from polymaker.m4_live_task import minimum_order_ok
from polymaker.rdp_sm_execution import WindowQuote


@pytest.mark.parametrize("price,quantity,allowed", [
    (".08", "6", False), (".07", "7", False), (".04", "12.5", False),
    (".03", "16", False), (".02", "25", False),
    (".20", "5", True),  # Exchange permits exactly $1; original shares OR still holds.
    (".50", "2", False),  # Original strategy requires strictly >$1 below5shares.
    (".50", "2.1", True), (".85", "1.2", True),
])
def test_exchange_floor_preserves_original_strategy_minimum(price, quantity, allowed):
    assert minimum_fak_buy_ok(D(price), D(quantity)) is allowed
    if D(price) * D(quantity) < 1:
        assert minimum_order_ok(D(price), D(quantity))  # Reproduces the missed old guard.


@pytest.mark.parametrize("status", ["CONFIRMED", "MINED"])
async def test_sub_dollar_remainder_stops_POSTs_but_pending_failure_releases_cash(
    tmp_path, monkeypatch, status,
):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".61")
    ep = move(m, f, c, age=12, ask=".73")
    q = None
    try:
        await m.refresh_account()
        await m.submit(ep)
        oid = m.ledger.attempts()[0]["order_id"]
        order, trade = evidence(m, e, oid, "6", price=".73", status=status)
        q, _ = serve(monkeypatch, t, order, trade)
        await m.reconcile(oid)
        row = m.ledger.row(ep.episode_id)
        assert D(".01") < 5 - m.ledger.budget_used(ep.episode_id) < 1
        if status == "CONFIRMED":
            assert row["state"] == "DUST"
            assert row["reason"] == "M4_REMAINING_CASH_BELOW_FAK_BUY_MINIMUM"
        else:
            assert row["state"] == "WAIT_SETTLEMENT" and m.ledger.pending_cost(ep.episode_id) > 4
        quotes = len(m.ledger.facts("taker_pricing_quote"))
        for age, ask in [(14, ".08"), (15, ".07"), (16, ".04"), (17, ".03"), (18, ".02")]:
            move(m, f, c, age=age, ask=ask)
            await m.refresh_account()
            await m.submit(ep)
        assert len(calls) == len(m.ledger.attempts()) == 1
        assert len(m.ledger.facts("taker_pricing_quote")) == quotes
        if status == "MINED":
            trade["status"] = "FAILED"
            await m.reconcile(oid)
            assert m.ledger.row(ep.episode_id)["state"] == "RESERVED"
            assert m.ledger.budget_used(ep.episode_id) == 0
            move(m, f, c, age=20, ask=".08")
            await m.refresh_account()
            await m.submit(ep)
            assert len(calls) == 2
            plan = json.loads(m.ledger.attempts()[-1]["plan"])
            assert D(plan["principal_usdc"]) >= 1 and D(plan["planned_cost_usdc"]) <= 5
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()
        if q:
            q.close()


async def test_new_prepare_and_send_guard_reject_sub_dollar_even_with_six_shares(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch)
    ep = move(m, f, c, age=12, ask=".08")
    try:
        await m.refresh_account()
        assert m.guard(ep)  # A full cash target can legally trade at the low price.
        assert m.ledger.reserve(ep.episode_id, spendable=40)
        assert m.ledger.next_plan(ep.episode_id, minimum=5, spendable=40)
        quote = WindowQuote(e.meta.condition_id, ep.token_id, ep.outcome, Side.BUY, .08, 6, ep.episode_id)
        assert not m.guard(ep, quote)
        assert m.guard_failure(ep.episode_id) == "M4_FAK_BUY_MINIMUM"
        with pytest.raises(ValueError, match="M4_FAK_BUY_MINIMUM"):
            m.ledger.prepare(ep.episode_id, "0x" + "a" * 64, asdict(quote))
        assert not calls and not m.ledger.attempts()
    finally:
        await close(m, e)
        t.close()


async def test_UNKNOWN_is_not_dust_or_retried_at_low_ask(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, response="TIMEOUT")
    ep = move(m, f, c, age=12, ask=".73")
    try:
        await m.refresh_account()
        await m.submit(ep)
        before = m.ledger.row(ep.episode_id)
        assert before["state"] == "UNKNOWN" and D(before["reserved"]) > 0
        move(m, f, c, age=20, ask=".03")
        await m.refresh_account()
        await m.submit(ep)
        after = m.ledger.row(ep.episode_id)
        assert len(calls) == 1 and after["state"] == "UNKNOWN"
        assert after["reserved"] == before["reserved"]
    finally:
        await close(m, e)
        t.close()


async def test_accepted_partial_topup_below_five_shares_still_posts(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".76")
    ep = move(m, f, c, age=12, ask=".77")
    q = None
    try:
        await m.refresh_account()
        await m.submit(ep)
        oid = m.ledger.attempts()[0]["order_id"]
        order, trade = evidence(m, e, oid, "5", price=".77")
        q, _ = serve(monkeypatch, t, order, trade)
        await m.reconcile(oid)
        assert m.ledger.row(ep.episode_id)["state"] == "RESERVED"
        move(m, f, c, age=14, ask=".85")
        await m.refresh_account()
        await m.submit(ep)
        plan = json.loads(m.ledger.attempts()[-1]["plan"])
        assert len(calls) == 2 and D(plan["quantity"]) == D("1.2")
        assert D(plan["principal_usdc"]) == D("1.02")
        assert D(plan["prior_spent_usdc"]) + D(plan["planned_cost_usdc"]) <= 5
    finally:
        await close(m, e)
        t.close()
        if q:
            q.close()
