"""Real owned-fill transitions must not spin durable pricing on unusable cash."""

import json
from decimal import Decimal as D

import pytest
from m4_helpers import close
from test_m4_live_best_ask import fixture, move
from test_m4_live_task import evidence, serve


@pytest.mark.parametrize("status", ["CONFIRMED", "MINED"])
async def test_sub_cent_residue_stops_capture_but_pending_failure_releases_budget(tmp_path, monkeypatch, status):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch)
    ep = move(m, f, c, age=12, ask=".82")
    q = None
    try:
        await m.refresh_account()
        await m.submit(ep)
        oid = m.ledger.attempts()[0]["order_id"]
        order, trade = evidence(m, e, oid, "6.227847", price=".79", status=status)
        q, _ = serve(monkeypatch, t, order, trade)
        await m.reconcile(oid)
        row = m.ledger.row(ep.episode_id)
        assert 0 < 5-m.ledger.budget_used(ep.episode_id) < D(".01")
        if status == "CONFIRMED":
            assert row["state"] == "DUST"
            assert row["reason"] == "M4_REMAINING_CASH_BELOW_USDC_CENT"
        else:
            assert row["state"] == "WAIT_SETTLEMENT" and m.ledger.pending_cost(ep.episode_id) > 4
        count = len(m.ledger.facts("taker_pricing_quote"))
        for _ in range(10):
            await m.submit(ep)
        assert len(calls) == 1 and len(m.ledger.facts("taker_pricing_quote")) == count
        if status == "MINED":
            trade["status"] = "FAILED"
            await m.reconcile(oid)
            assert m.ledger.row(ep.episode_id)["state"] == "RESERVED"
            assert m.ledger.budget_used(ep.episode_id) == 0
            move(m, f, c, age=14, ask=".81")
            await m.refresh_account()
            await m.submit(ep)
            assert len(calls) == 2
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()
        if q:
            q.close()


async def test_no_durable_quote_until_current_ask_has_legal_quantity(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch)
    ep = move(m, f, c, age=10, ask=".71")
    q = None
    try:
        await m.refresh_account()
        await m.submit(ep)
        oid = m.ledger.attempts()[0]["order_id"]
        order, trade = evidence(m, e, oid, "5", price=".71")
        q, _ = serve(monkeypatch, t, order, trade)
        await m.reconcile(oid)
        assert m.ledger.row(ep.episode_id)["state"] == "RESERVED"
        count = len(m.ledger.facts("taker_pricing_quote"))
        move(m, f, c, age=12, ask=".99")
        await m.refresh_account()
        for _ in range(10):
            await m.submit(ep)
        assert len(calls) == 1 and len(m.ledger.facts("taker_pricing_quote")) == count
        assert m.guard_failure(ep.episode_id) == "M4_REMAINDER_WAIT_LEGAL_REPRICE"
        assert m.retry_after[ep.episode_id] >= c.now+.5
        move(m, f, c, age=13, ask=".80")
        await m.submit(ep)
        assert len(calls) == 2
        plan = json.loads(m.ledger.attempts()[-1]["plan"])
        assert D(plan["limit_price"]) == D(".80")
        assert D(plan["prior_spent_usdc"]) + D(plan["planned_cost_usdc"]) <= 5
        assert len(m.ledger.facts("taker_pricing_quote")) == count+1
    finally:
        await close(m, e)
        t.close()
        if q:
            q.close()


async def test_ten_second_wakeup_does_not_defeat_subsequent_quote_wait_backoff(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, response="KILLED")
    ep = move(m, f, c, age=0)
    try:
        await m.submit(ep)
        move(m, f, c, age=10)
        f.connected = False
        m.retry_after[ep.episode_id] = c.now+2
        assert m.entry_retry_ready(m.ledger.row(ep.episode_id), c.now)
        await m.refresh_account()
        await m.submit(ep)
        assert len(calls) == 1
        assert not m.entry_retry_ready(m.ledger.row(ep.episode_id), c.now+.01)
        assert m.entry_retry_ready(m.ledger.row(ep.episode_id), c.now+.101)
        f.connected = True
        move(m, f, c, age=10.2)
        await m.submit(ep)
        assert len(calls) == 2
        assert json.loads(m.ledger.attempts()[-1]["plan"])["pricing"]["stage"] == "BEST_ASK"
    finally:
        await close(m, e)
        t.close()
