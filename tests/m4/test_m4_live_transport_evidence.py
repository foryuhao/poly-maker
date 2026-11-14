"""Delayed/contradictory HTTP evidence: mock transport, real SDK, no sockets."""

import asyncio
import json
from decimal import Decimal as D
from types import SimpleNamespace

import httpx
import pytest
from m4_helpers import close
from py_clob_client_v2.http_helpers import helpers
from test_m4_live_budget import fixture
from test_m4_live_task import evidence

from polymaker.m4_live_task import FAK_NO_MATCH, taker_exception_rejection


@pytest.mark.parametrize("status", [425, 429])
@pytest.mark.parametrize("conflict", [
    {"makingAmount": "1.55"}, {"takingAmount": "5"}, {"makingAmount": "NaN"},
    {"orderID": "0x" + "f" * 64}, {"tradeIDs": ["trade-1"]},
    {"transactions_hashes": ["tx"]}, {"status": "matched"}, {"success": True},
    {"error": "duplicate order already exists"},
])
def test_http_rejection_must_not_override_acceptance_evidence(status, conflict):
    exc = SimpleNamespace(status_code=status, error_msg={"error": "rate limit", **conflict})
    assert taker_exception_rejection(exc, D(5)) is None


@pytest.mark.parametrize("status", [425, 429])
@pytest.mark.parametrize("body", ["rate limited", {"error": "rate limited"},
                                 {"success": False, "error": "rate limited",
                                  "makingAmount": "0", "takingAmount": "0"}])
def test_unambiguous_http_rejection_keeps_zero_retry(status, body):
    proof = taker_exception_rejection(SimpleNamespace(status_code=status, error_msg=body), D(5))
    assert proof["kind"] == f"HTTP_{status}"


@pytest.mark.parametrize("status", [400, 425, 429, 500])
@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("elapsed", [20, 240])
async def test_error_receipt_survives_stale_zero_then_recovers_partial(
    tmp_path, monkeypatch, status, restart, elapsed
):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".30")
    transports = [t]
    posts = []
    phase = "stale"

    def wire(req):
        if req.method == "POST":
            posts.append(json.loads(req.content)[0])
            if len(posts) == 1:
                return httpx.Response(status, json={"error": "not enough balance / allowance",
                                                    "makingAmount": "1.55"})
            return t._transport.handle_request(req)
        oid = m.ledger.attempts()[0]["order_id"]
        order, trade = evidence(m, e, oid, "0" if phase == "stale" else "5")
        if req.url.path.startswith("/data/order/"):
            return httpx.Response(200, json=order)
        return httpx.Response(200, json={"data": [trade], "next_cursor": "LTE="})

    q = httpx.Client(transport=httpx.MockTransport(wire))
    monkeypatch.setattr(helpers, "_http_client", q)
    transports.append(q)
    try:
        ep = next(iter(m.pending.values()))
        await m.submit(ep)
        oid = m.ledger.attempts()[0]["order_id"]
        assert m.ledger.attempt(oid)["state"] == "UNKNOWN"
        if restart:
            await close(m, e)
            m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".30", recover=True)
            transports.append(t)
            monkeypatch.setattr(helpers, "_http_client", q)
            ep = next(iter(m.pending.values()))
        await m.reconcile(oid)
        assert m.ledger.unresolved(ep.episode_id)
        assert m.ledger.committed() > 5  # full worst-case reservation stays held
        await m.submit(ep)
        assert len(posts) == 1
        phase = "complete"
        e.official_time = int(e.meta.slug.rsplit("-", 1)[1]) + elapsed
        e.time_anchor = c.now
        await m.read_order(oid)
        assert m.ledger.attempt(oid)["state"] == "TERMINAL"
        assert D(m.ledger.row(ep.episode_id)["spent"]) == D("1.62487")
        await m.submit(ep)
        assert len(posts) == (2 if elapsed < 240 else 1)
        if elapsed < 240:
            assert m.ledger.attempts()[-1]["order_id"] != oid
            plan = m.ledger.plan(ep.episode_id)
            assert m.ledger.budget_used(ep.episode_id) + D(plan["planned_cost_usdc"]) <= 5
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        for transport in transports:
            await asyncio.to_thread(transport.close)


@pytest.mark.parametrize("empty", [None, {}])
@pytest.mark.parametrize("elapsed", [20, 240, 300])
async def test_complete_receipt_and_trades_recover_empty_order_reply(
    tmp_path, monkeypatch, empty, elapsed
):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".30")
    posts = []
    ids = []

    def wire(req):
        if req.method == "POST":
            posts.append(json.loads(req.content)[0])
            oid = e.gateway._client._operation_value("attempt").expected_order_id
            ids.append(oid)
            return httpx.Response(200, json=[{"success": True, "status": "matched",
                "orderID": oid, "makingAmount": "1.55", "takingAmount": "5",
                "tradeIDs": ["trade-1"]}])
        if req.url.path.startswith("/data/order/"):
            return httpx.Response(200, content=json.dumps(empty),
                                  headers={"content-type": "application/json"})
        assert req.url.params["id"] == "trade-1"
        _, trade = evidence(m, e, ids[0], "5")
        return httpx.Response(200, json={"data": [trade], "next_cursor": "LTE="})

    q = httpx.Client(transport=httpx.MockTransport(wire))
    monkeypatch.setattr(helpers, "_http_client", q)
    try:
        ep = next(iter(m.pending.values()))
        await m.submit(ep)
        e.official_time = int(e.meta.slug.rsplit("-", 1)[1]) + elapsed
        e.time_anchor = c.now
        await m.read_order(ids[0])
        assert m.ledger.attempt(ids[0])["state"] == "TERMINAL"
        assert D(m.ledger.row(ep.episode_id)["spent"]) == D("1.62487")
        await m.submit(ep)
        assert len(posts) == (2 if elapsed < 240 else 1)
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()
        await asyncio.to_thread(q.close)


@pytest.mark.parametrize("fault", ["no_receipt", "incomplete_receipt", "missing_trade", "foreign_trade"])
async def test_empty_query_recovery_requires_complete_owned_evidence(tmp_path, monkeypatch, fault):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".30")
    posts = []
    oid = None

    def wire(req):
        nonlocal oid
        if req.method == "POST":
            posts.append(req.content)
            oid = e.gateway._client._operation_value("attempt").expected_order_id
            receipt = {"success": True, "orderID": oid}
            if fault != "no_receipt":
                receipt.update(status="matched", makingAmount="1.55", takingAmount="5",
                               tradeIDs=["trade-1"])
            if fault == "incomplete_receipt":
                receipt.pop("makingAmount")
            return httpx.Response(200, json=[receipt])
        if req.url.path.startswith("/data/order/"):
            return httpx.Response(200, json={})
        _, trade = evidence(m, e, oid, "5")
        if fault == "foreign_trade":
            trade["taker_order_id"] = "0x" + "f" * 64
        return httpx.Response(200, json={"data": [] if fault == "missing_trade" else [trade],
                                        "next_cursor": "LTE="})

    q = httpx.Client(transport=httpx.MockTransport(wire))
    monkeypatch.setattr(helpers, "_http_client", q)
    try:
        ep = next(iter(m.pending.values()))
        await m.submit(ep)
        await m.reconcile(oid)
        await m.submit(ep)
        assert len(posts) == 1 and m.ledger.unresolved(ep.episode_id)
        assert D(m.ledger.row(ep.episode_id)["spent"]) == 0
        assert m.ledger.committed() > 5
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()
        await asyncio.to_thread(q.close)


@pytest.mark.parametrize("status", [408, 500])
async def test_saved_ambiguous_error_is_not_zero_proof_after_restart(
    tmp_path, monkeypatch, status
):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".30")
    posts = []

    def wire(req):
        if req.method == "POST":
            posts.append(req.content)
            return httpx.Response(status, json={"success": False, "error": FAK_NO_MATCH})
        return httpx.Response(404, json={"error": "not found"})

    q = httpx.Client(transport=httpx.MockTransport(wire))
    monkeypatch.setattr(helpers, "_http_client", q)
    t2 = None
    try:
        ep = next(iter(m.pending.values()))
        await m.submit(ep)
        await close(m, e)
        m, e, f, c, calls, t2 = await fixture(tmp_path, monkeypatch, price=".30", recover=True)
        monkeypatch.setattr(helpers, "_http_client", q)
        ep = next(iter(m.pending.values()))
        await m.submit(ep)
        assert len(posts) == 1 and m.ledger.unresolved(ep.episode_id)
    finally:
        await close(m, e)
        t.close()
        if t2:
            t2.close()
        await asyncio.to_thread(q.close)
