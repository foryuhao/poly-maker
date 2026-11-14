"""Delayed evidence, durable response recovery and cutoff; all HTTP is synthetic."""

import asyncio
from decimal import Decimal as D
from threading import Event

import httpx
import pytest
from m4_helpers import close
from py_clob_client_v2.http_helpers import helpers
from test_m4_live_budget import fixture
from test_m4_live_recovery_progress import fresh
from test_m4_live_task import evidence

from polymaker.m4_live_task import FAK_NO_MATCH


async def test_partial_receipt_money_cannot_be_erased_by_stale_zero_query(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".30")
    ep = next(iter(m.pending.values()))
    posts = []
    qty = "0"

    def wire(req):
        if req.method == "POST":
            oid = e.gateway._client._operation_value("attempt").expected_order_id
            posts.append(oid)
            # Incomplete receipt: positive principal, missing quantity and trade IDs.
            return httpx.Response(200, json=[{"success": True, "orderID": oid,
                "status": "matched", "makingAmount": "1.55"}])
        order, trade = evidence(m, e, posts[0], qty)
        if req.url.path.startswith("/data/order/"):
            return httpx.Response(200, json=order)
        return httpx.Response(200, json={"data": [trade], "next_cursor": "LTE="})

    q = httpx.Client(transport=httpx.MockTransport(wire))
    monkeypatch.setattr(helpers, "_http_client", q)
    try:
        await m.submit(ep)
        reserve = m.ledger.committed()
        await m.reconcile(posts[0])
        assert m.ledger.unresolved(ep.episode_id), "positive receipt money was treated as zero"
        await m.submit(ep)
        assert len(posts) == 1 and m.ledger.committed() == reserve
        qty = "5"
        await m.reconcile(posts[0])
        assert not m.ledger.unresolved(ep.episode_id)
        assert D(m.ledger.row(ep.episode_id)["spent"]) == D("1.62487")
        await m.submit(ep)
        assert len(posts) == 2 and posts[0] != posts[1]
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()
        await asyncio.to_thread(q.close)


@pytest.mark.parametrize("receipt", [
    {"success": False, "errorMsg": FAK_NO_MATCH, "makingAmount": ".1"},
    {"success": False, "errorMsg": FAK_NO_MATCH, "takingAmount": "1"},
    {"success": True, "status": "matched", "makingAmount": ".1"},
    {"success": True, "status": "matched", "orderID": "0x" + "f" * 64},
])
async def test_conflicting_or_idless_receipt_cannot_authorize_zero_retry(
    tmp_path, monkeypatch, receipt
):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".30")
    ep = next(iter(m.pending.values()))
    posts = []

    def wire(req):
        if req.method == "POST":
            posts.append(req.content)
            return httpx.Response(200, json=[receipt])
        oid = m.ledger.attempts()[0]["order_id"]
        order, _ = evidence(m, e, oid, "0")
        assert req.url.path.startswith("/data/order/")
        return httpx.Response(200, json=order)

    q = httpx.Client(transport=httpx.MockTransport(wire))
    monkeypatch.setattr(helpers, "_http_client", q)
    try:
        await m.submit(ep)
        reserve = m.ledger.committed()
        for _ in range(2):
            await m.submit(ep)
        assert len(posts) == 1 and m.ledger.unresolved(ep.episode_id)
        assert m.ledger.committed() == reserve
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()
        await asyncio.to_thread(q.close)


class InterruptedAfterReceipt(BaseException):
    pass


@pytest.mark.parametrize("kind", ["killed", "no_match", "rejected"])
@pytest.mark.parametrize("elapsed", [20, 240])
async def test_restart_replays_durable_zero_receipt_before_queries(tmp_path, monkeypatch, kind, elapsed):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".30")
    ep = next(iter(m.pending.values()))
    saved = m.ledger.post_receipt

    def persist_then_interrupt(oid, raw):
        saved(oid, raw)
        raise InterruptedAfterReceipt()

    def wire(req):
        assert req.method == "POST"
        oid = e.gateway._client._operation_value("attempt").expected_order_id
        if kind == "killed":
            raw = {"success": True, "orderID": oid, "status": "", "makingAmount": "",
                   "takingAmount": "", "tradeIDs": [], "transactionsHashes": [],
                   "errorMsg": "no orders found to match with FAK order. FAK orders are partially filled or killed if no match is found."}
        else:
            raw = {"success": False, "errorMsg": FAK_NO_MATCH if kind == "no_match"
                   else "not enough balance / allowance"}
        return httpx.Response(200, json=[raw])

    q = httpx.Client(transport=httpx.MockTransport(wire))
    monkeypatch.setattr(helpers, "_http_client", q)
    monkeypatch.setattr(m.ledger, "post_receipt", persist_then_interrupt)
    try:
        with pytest.raises(InterruptedAfterReceipt):
            await m.submit(ep)
        oid = m.ledger.attempts()[0]["order_id"]
        assert m.ledger.attempt(oid)["state"] == "UNKNOWN"
        assert len(m.ledger.facts("owned_post_receipt")) == 1
    finally:
        await close(m, e)
        t.close()
        await asyncio.to_thread(q.close)
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, recover=True, price=".30")
    queries = []

    def missing(req):
        if req.method == "POST":
            return t._transport.handle_request(req)
        queries.append(str(req.url))
        return httpx.Response(404, json={"error": "rejected orders not indexed"})

    q = httpx.Client(transport=httpx.MockTransport(missing))
    monkeypatch.setattr(helpers, "_http_client", q)
    try:
        e.official_time = int(e.meta.slug.rsplit("-", 1)[1]) + elapsed
        e.time_anchor = c.now
        await m.read_order(oid)
        assert m.ledger.attempt(oid)["state"] == "TERMINAL"
        assert not queries and m.ledger.committed() == 0
        await m.submit(ep)
        if elapsed < 240:
            assert len(calls) == 1 and m.ledger.attempts()[-1]["order_id"] != oid
        else:
            assert not calls and m.ledger.row(ep.episode_id)["state"] == "CUTOFF"
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()
        await asyncio.to_thread(q.close)


@pytest.mark.parametrize("conflict", ["matched_amount", "linked_trade"])
async def test_durable_zero_cannot_erase_prior_positive_evidence(tmp_path, monkeypatch, conflict):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, response="TIMEOUT")
    ep = next(iter(m.pending.values()))
    try:
        await m.submit(ep)
        oid = m.ledger.attempts()[0]["order_id"]
        reserve = m.ledger.committed()
        m.ledger.post_receipt(oid, [{"success": False, "errorMsg": FAK_NO_MATCH}])
        if conflict == "matched_amount":
            m.ledger.set("matched_lower_bound:" + oid, "1")
        else:
            m.ledger.link_trades(oid, ["earlier-match"])
        await m.read_order(oid)
        await m.submit(ep)
        assert len(calls) == 1 and m.ledger.unresolved(ep.episode_id)
        assert m.ledger.committed() == reserve
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()


@pytest.mark.parametrize("elapsed,uncertainty,can_post", [
    (239.5, 0, True), (239.7, 0, False), (239.5, .12, False),
    (240, 0, False), (300, 0, False),
])
@pytest.mark.parametrize("trade_state", ["MINED", "CONFIRMED"])
@pytest.mark.parametrize("initial_response", ["ACK", "TIMEOUT"])
async def test_delayed_partial_read_rechecks_cutoff_before_residual_post(
    tmp_path, monkeypatch, elapsed, uncertainty, can_post, trade_state, initial_response
):
    m, e, f, c, calls, t = await fixture(
        tmp_path, monkeypatch, price=".30", response=initial_response)
    ep = next(iter(m.pending.values()))
    entered, release = asyncio.Event(), Event()
    loop = asyncio.get_running_loop()
    oid, job = None, None

    def wire(req):
        if req.method == "POST":
            return t._transport.handle_request(req)
        order, trade = evidence(m, e, oid, "5", status=trade_state)
        if req.url.path.startswith("/data/order/"):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5), "test did not release delayed order response"
            return httpx.Response(200, json=order)
        return httpx.Response(200, json={"data": [trade], "next_cursor": "LTE="})

    q = httpx.Client(transport=httpx.MockTransport(wire))
    monkeypatch.setattr(helpers, "_http_client", q)
    monkeypatch.setattr(m, "collect", lambda: None)
    try:
        await m.submit(ep)
        oid = m.ledger.attempts()[0]["order_id"]
        job = asyncio.create_task(m.read_order(oid))
        await asyncio.wait_for(entered.wait(), 2)
        e.official_time = int(e.meta.slug.rsplit("-", 1)[1]) + elapsed
        e.time_anchor = c.now
        e.official_time_uncertainty_s = uncertainty
        release.set()
        await job
        c.now += .02
        fresh(f, c, ".31")
        await m.advance_entries(c.now)
        if m.post_jobs:
            await asyncio.gather(*m.post_jobs.values())
        assert len(calls) == (2 if can_post else 1)
        assert m.ledger.attempt(oid)["state"] == (
            "SETTLING" if trade_state == "MINED" else "TERMINAL")
        assert m.ledger.budget_used(ep.episode_id) > 0
        if can_post:
            plan = m.ledger.plan(ep.episode_id)
            assert m.ledger.budget_used(ep.episode_id) + D(plan["planned_cost_usdc"]) <= 5
        if elapsed >= 240:
            assert m.ledger.row(ep.episode_id)["state"] == "CUTOFF"
        m.ledger.validate_recovery()
    finally:
        release.set()
        if job:
            await job
        await close(m, e)
        t.close()
        await asyncio.to_thread(q.close)
