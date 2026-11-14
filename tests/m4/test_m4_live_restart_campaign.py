"""Crash and delayed settlement interleavings, using only synthetic transport."""

import asyncio
import json
import subprocess
import sys
from decimal import Decimal as D
from pathlib import Path

import pytest
from m4_helpers import close, op
from test_m4_live_budget import fixture
from test_m4_live_task import evidence, serve


@pytest.mark.parametrize("old_status", ["MINED", "CONFIRMED", "FAILED"])
async def test_old_settlement_during_signing_preserves_prepared_reservation(
    tmp_path, monkeypatch, old_status
):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".30")
    queries = []
    ep = next(iter(m.pending.values()))
    try:
        await m.submit(ep)
        first = m.ledger.attempts()[0]["order_id"]
        order, trade = evidence(m, e, first, "5", status="MINED")
        q, _ = serve(monkeypatch, t, order, trade)
        queries.append(q)
        await m.read_order(first)
        original_prepare = e.gateway.prepare
        prepared_rows = []

        async def sign_with_delayed_settlement(*args, **kwargs):
            prepared = await original_prepare(*args, **kwargs)
            trade["status"] = old_status
            await m.read_order(first)
            return prepared

        original_ledger_prepare = m.ledger.prepare

        def check_durable_state(*args, **kwargs):
            original_ledger_prepare(*args, **kwargs)
            row = m.ledger.row(ep.episode_id)
            plan = m.ledger.plan(ep.episode_id)
            prepared_rows.append(row)
            assert D(row["reserved"]) == m.ledger.pending_cost(row["id"]) + D(plan["commitment_usdc"])
            m.ledger.validate_recovery()

        monkeypatch.setattr(e.gateway, "prepare", sign_with_delayed_settlement)
        monkeypatch.setattr(m.ledger, "prepare", check_durable_state)
        await m.submit(ep)
        assert prepared_rows
        assert len(calls) == 2  # A harmless old-order read cannot repeatedly suppress a top-up.
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()
        for q in queries:
            await asyncio.to_thread(q.close)


@pytest.mark.parametrize("restart_point", ["next_plan", "prepare", "post_start", "receipt", "terminal"])
async def test_cash_task_restart_at_durable_boundaries(tmp_path, monkeypatch, restart_point):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".30")
    transports = [t]
    ep = next(iter(m.pending.values()))
    original = getattr(m.ledger, restart_point if restart_point != "receipt" else "post_receipt")

    class SimulatedCrash(BaseException):
        pass

    def crash_after_commit(*args, **kwargs):
        result = original(*args, **kwargs)
        raise SimulatedCrash(result)

    name = restart_point if restart_point != "receipt" else "post_receipt"
    try:
        if restart_point == "terminal":
            await m.submit(ep)
            oid = m.ledger.attempts()[0]["order_id"]
            order, trade = evidence(m, e, oid, "5")
            q, _ = serve(monkeypatch, t, order, trade)
            transports.append(q)
        monkeypatch.setattr(m.ledger, name, crash_after_commit)
        with pytest.raises(SimulatedCrash):
            if restart_point == "terminal":
                await m.reconcile(oid)
            else:
                await m.submit(ep)
        saved = m.ledger.attempts()
        sent_before = len(calls)
        await close(m, e)
        m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".30", recover=True)
        transports.append(t)
        ep = next(iter(m.pending.values()))
        m.ledger.validate_recovery()
        if restart_point in {"post_start", "receipt"}:
            oid = saved[-1]["order_id"]
            # An unavailable order index proves nothing, even if local POST was armed.
            order, trade = evidence(m, e, oid, "0")
            q, _ = serve(monkeypatch, t, order, trade, missing=True)
            transports.append(q)
            await m.submit(ep)
            assert not calls and m.ledger.unresolved(ep.episode_id)
            # Subsequent owned partial evidence can recover without resending that ID.
            order, trade = evidence(m, e, oid, "5")
            q, _ = serve(monkeypatch, t, order, trade)
            transports.append(q)
            await m.read_order(oid)
        await m.submit(ep)
        assert len(calls) == 1
        newest = m.ledger.attempts()[-1]
        assert not saved or newest["order_id"] != saved[-1]["order_id"]
        if restart_point in {"post_start", "receipt", "terminal"}:
            assert D(m.ledger.row(ep.episode_id)["spent"]) == D("1.62487")
            assert D(json.loads(newest["plan"])["quantity"]) == D("10.0")
        assert sent_before == (1 if restart_point in {"receipt", "terminal"} else 0)
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        for q in transports:
            await asyncio.to_thread(q.close)


@pytest.mark.parametrize("boundary", [
    "next_plan", "prepare", "post_start", "transport", "post_receipt",
    "match_bound", "link_trades", "remember_fill", "settling", "terminal",
])
@pytest.mark.parametrize("elapsed", [20, 240, 300])
async def test_abrupt_process_restart_and_cutoff(tmp_path, monkeypatch, boundary, elapsed):
    worker = Path(__file__).with_name("m4_crash_worker.py")
    result = await asyncio.to_thread(subprocess.run,
        [sys.executable, str(worker), str(tmp_path), boundary],
        capture_output=True, text=True, timeout=20)
    assert result.returncode == 71, result.stdout + result.stderr
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".30", recover=True)
    transports = [t]
    try:
        ep = next(iter(m.pending.values()))
        e.official_time = int(e.meta.slug.rsplit("-", 1)[1]) + elapsed
        e.time_anchor = c.now
        m.ledger.validate_recovery()
        old = m.ledger.attempts()
        if old and old[-1]["started"] and old[-1]["state"] not in {"TERMINAL", "SETTLING"}:
            oid = old[-1]["order_id"]
            order, trade = evidence(m, e, oid, "5")
            q, _ = serve(monkeypatch, t, order, trade, missing=True)
            transports.append(q)
            await m.read_order(oid)
            await m.submit(ep)
            assert not calls and m.ledger.unresolved(ep.episode_id)
            q, _ = serve(monkeypatch, t, order, trade)
            transports.append(q)
            await m.read_order(oid)
        elif old and old[-1]["state"] == "SETTLING":
            oid = old[-1]["order_id"]
            order, trade = evidence(m, e, oid, "5")
            q, _ = serve(monkeypatch, t, order, trade)
            transports.append(q)
            await m.read_order(oid)
        await m.submit(ep)
        assert len(calls) == (1 if elapsed < 240 else 0)
        if calls:
            assert not old or m.ledger.attempts()[-1]["order_id"] not in {a["order_id"] for a in old}
        m.ledger.validate_recovery()
        if boundary not in {"next_plan", "prepare"}:
            assert D(m.ledger.row(ep.episode_id)["spent"]) == D("1.62487")
    finally:
        await close(m, e)
        for q in transports:
            await asyncio.to_thread(q.close)


async def test_reacquire_after_delayed_read_cannot_overbook_other_signal(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".30")
    queries = []
    ep = next(iter(m.pending.values()))
    second = op("other-direction", token="2", price=".30")
    try:
        await m.submit(ep)
        oid = m.ledger.attempts()[0]["order_id"]
        order, trade = evidence(m, e, oid, "5", status="MINED")
        q, _ = serve(monkeypatch, t, order, trade)
        queries.append(q)
        await m.read_order(oid)
        original = e.gateway.prepare

        async def release_and_reserve_elsewhere(*args, **kwargs):
            prepared = await original(*args, **kwargs)
            await m.read_order(oid)
            m.ledger.observe(second, minimum=5, elapsed=20)
            m.ledger.plan_signal(second.episode_id, D(".01"), 5)
            assert m.ledger.reserve(second.episode_id, spendable=40, direction_limit=1)
            m.ledger.next_plan(second.episode_id, minimum=5, spendable=40)
            return prepared

        monkeypatch.setattr(e.gateway, "prepare", release_and_reserve_elsewhere)
        await m.submit(ep)
        assert m.ledger.committed() <= 10
        m.ledger.validate_recovery()
        # If the concurrent reservation consumed the room, retry sizes down.
        monkeypatch.setattr(e.gateway, "prepare", original)
        if len(calls) == 1:
            await m.submit(ep)
        assert len(calls) == 2
        assert m.ledger.committed() <= 10
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()
        for q in queries:
            await asyncio.to_thread(q.close)
