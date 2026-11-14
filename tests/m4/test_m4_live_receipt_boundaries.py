"""Owned receipt evidence must survive delayed replicas and the T240 boundary."""

import asyncio
import json
from decimal import Decimal as D

import httpx
import pytest
from m4_helpers import close
from py_clob_client_v2.http_helpers import helpers
from test_m4_live_budget import fixture
from test_m4_live_task import evidence

from polymaker.m4_live_task import FAK_NO_MATCH, zero_rejection


@pytest.mark.parametrize("message", [FAK_NO_MATCH, "not enough balance / allowance"])
def test_rejection_with_transaction_reference_is_not_zero(message):
    assert zero_rejection({"success": False, "errorMsg": message,
                           "transactions_hashes": ["tx-1"]}, D(5)) is None


@pytest.mark.parametrize("hint", [
    {"status": "matched"}, {"status": "MATCHED"},
    {"tradeIDs": ["trade-1"]}, {"tradeIds": ["trade-1"]},
    {"trade_ids": ["trade-1"]}, {"transactions_hashes": ["tx-1"]},
])
@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("elapsed", [20, 240])
async def test_positive_hint_survives_stale_zero_and_recovers_partial(
    tmp_path, monkeypatch, hint, restart, elapsed
):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".30")
    transports, posts = [t], []
    phase = "stale"

    def wire(req):
        if req.method == "POST":
            posts.append(json.loads(req.content)[0])
            if len(posts) == 1:
                oid = e.gateway._client._operation_value("attempt").expected_order_id
                return httpx.Response(200, json=[{"success": True, "orderID": oid, **hint}])
            return t._transport.handle_request(req)
        oid = m.ledger.attempts()[0]["order_id"]
        order, trade = evidence(m, e, oid, "0" if phase == "stale" else "5")
        if req.url.path.startswith("/data/order/"):
            return httpx.Response(200, json=order)
        trade["transaction_hash"] = "tx-1"
        return httpx.Response(200, json={"data": [trade], "next_cursor": "LTE="})

    q = httpx.Client(transport=httpx.MockTransport(wire))
    transports.append(q)
    monkeypatch.setattr(helpers, "_http_client", q)
    try:
        ep = next(iter(m.pending.values()))
        await m.submit(ep)
        oid = m.ledger.attempts()[0]["order_id"]
        if restart:
            await close(m, e)
            m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".30", recover=True)
            transports.append(t)
            monkeypatch.setattr(helpers, "_http_client", q)
            ep = next(iter(m.pending.values()))
        await m.read_order(oid)
        assert m.ledger.unresolved(ep.episode_id), m.ledger.attempt(oid)
        assert m.ledger.committed() > 5
        await m.submit(ep)
        assert len(posts) == 1
        phase = "complete"
        e.official_time = int(e.meta.slug.rsplit("-", 1)[1]) + elapsed
        e.time_anchor = c.now
        await m.read_order(oid)
        assert m.ledger.attempt(oid)["state"] == "TERMINAL"
        assert D(m.ledger.row(ep.episode_id)["spent"]) == D("1.62487")
        await m.read_order(oid)  # Duplicate reads cannot debit or release money twice.
        assert D(m.ledger.row(ep.episode_id)["spent"]) == D("1.62487")
        await m.submit(ep)
        assert len(posts) == (2 if elapsed < 240 else 1)
        if elapsed < 240:
            plan = m.ledger.plan(ep.episode_id)
            assert D(plan["quantity"]) == D("10.0")  # .31 * 10.3 is sub-cent.
            assert D(plan["limit_price"]) == D(".31")
            assert m.ledger.budget_used(ep.episode_id) + D(plan["planned_cost_usdc"]) <= 5
            assert m.ledger.attempts()[-1]["order_id"] != oid
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        for transport in transports:
            await asyncio.to_thread(transport.close)


@pytest.mark.parametrize("field", ["tradeIds", "trade_ids"])
async def test_complete_aliased_receipt_recovers_when_order_index_lags(tmp_path, monkeypatch, field):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".30")
    oid = None

    def wire(req):
        nonlocal oid
        if req.method == "POST":
            oid = e.gateway._client._operation_value("attempt").expected_order_id
            return httpx.Response(200, json=[{"success": True, "orderID": oid,
                "status": "matched", "makingAmount": "1.55", "takingAmount": "5",
                field: ["trade-1"]}])
        if req.url.path.startswith("/data/order/"):
            return httpx.Response(200, json={})
        assert req.url.params["id"] == "trade-1"
        _, trade = evidence(m, e, oid, "5")
        return httpx.Response(200, json={"data": [trade], "next_cursor": "LTE="})

    q = httpx.Client(transport=httpx.MockTransport(wire))
    monkeypatch.setattr(helpers, "_http_client", q)
    try:
        await m.submit(next(iter(m.pending.values())))
        await m.read_order(oid)
        assert m.ledger.attempt(oid)["state"] == "TERMINAL"
        assert D(m.ledger.attempt(oid)["spent"]) == D("1.62487")
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()
        await asyncio.to_thread(q.close)


async def test_multiple_results_for_single_post_cannot_hide_positive_evidence(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".30")
    posts = []

    def wire(req):
        if req.method == "POST":
            posts.append(req.content)
            oid = e.gateway._client._operation_value("attempt").expected_order_id
            return httpx.Response(200, json=[{"success": True, "orderID": oid,
                                            "makingAmount": "1.55"}, {}])
        oid = m.ledger.attempts()[0]["order_id"]
        order, _ = evidence(m, e, oid, "0")
        return httpx.Response(200, json=order)

    q = httpx.Client(transport=httpx.MockTransport(wire))
    monkeypatch.setattr(helpers, "_http_client", q)
    try:
        ep = next(iter(m.pending.values()))
        await m.submit(ep)
        await m.read_order(m.ledger.attempts()[0]["order_id"])
        assert m.ledger.unresolved(ep.episode_id)
        await m.submit(ep)
        assert len(posts) == 1
    finally:
        await close(m, e)
        t.close()
        await asyncio.to_thread(q.close)


@pytest.mark.parametrize("status", [200, 400])
async def test_transaction_reference_overrides_rejection_text_on_transport(
    tmp_path, monkeypatch, status
):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".30")
    posts, phase = [], "stale"

    def wire(req):
        if req.method == "POST":
            posts.append(req.content)
            return httpx.Response(status, json={"success": False, "errorMsg": FAK_NO_MATCH,
                                               "transactions_hashes": ["tx-1"]})
        oid = m.ledger.attempts()[0]["order_id"]
        order, trade = evidence(m, e, oid, "0" if phase == "stale" else "5", status="FAILED")
        if req.url.path.startswith("/data/order/"):
            return httpx.Response(200, json=order)
        trade["transaction_hash"] = "tx-1"
        return httpx.Response(200, json={"data": [trade], "next_cursor": "LTE="})

    q = httpx.Client(transport=httpx.MockTransport(wire))
    monkeypatch.setattr(helpers, "_http_client", q)
    try:
        ep = next(iter(m.pending.values()))
        await m.submit(ep)
        oid = m.ledger.attempts()[0]["order_id"]
        assert m.ledger.attempt(oid)["state"] == "UNKNOWN"
        await m.read_order(oid)
        await m.submit(ep)
        assert len(posts) == 1 and m.ledger.unresolved(ep.episode_id)
        phase = "complete"
        await m.read_order(oid)
        assert m.ledger.attempt(oid)["state"] == "TERMINAL"
        assert m.ledger.committed() == 0
        assert D(m.ledger.row(ep.episode_id)["spent"]) == 0
        await m.submit(ep)  # Exact owned FAILED trade evidence permits a fresh buy.
        assert len(posts) == 2
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()
        await asyncio.to_thread(q.close)


@pytest.mark.parametrize("references", [
    {"tradeIDs": ["trade-1"], "trade_ids": ["trade-2"]},
    {"tradeIDs": [], "tradeIds": ["trade-1"]},
])
async def test_conflicting_reference_aliases_do_not_release_reservation(
    tmp_path, monkeypatch, references
):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".30")
    try:
        ep = next(iter(m.pending.values()))
        await m.submit(ep)
        oid = m.ledger.attempts()[0]["order_id"]
        # Isolated crash-recovery fixture: replace only its synthetic saved receipt.
        with m.ledger.lock, m.ledger.db:
            m.ledger.db.execute("DELETE FROM facts WHERE kind='owned_post_receipt'")
        m.ledger.post_receipt(oid, {"success": True, "orderID": oid, **references})
        await m.read_order(oid)
        assert m.ledger.unresolved(ep.episode_id)
        assert m.ledger.committed() > 5
        assert "M4_RECEIPT_REFERENCE_CONFLICT" in m.ledger.attempt(oid)["proof"]
    finally:
        await close(m, e)
        t.close()
