"""Network delay and reconciliation scheduling regressions; synthetic HTTP only."""

import asyncio
import json
from decimal import Decimal as D
from threading import Event

import httpx
import pytest
from m4_helpers import close, op
from py_clob_client_v2.http_helpers import helpers
from test_m4_live_budget import fixture
from test_m4_live_recovery_progress import fresh
from test_m4_live_task import evidence

from polymaker.m4_live_task import FAK_NO_MATCH
from polymaker.rdp_sm_network import NetworkHealth


async def test_old_settling_polls_do_not_postpone_zero_fill_retry(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".30")
    ep = next(iter(m.pending.values()))
    posts = []
    first_oid = None

    def wire(req):
        if req.method == "POST":
            posts.append(json.loads(req.content))
            oid = e.gateway._client._operation_value("attempt").expected_order_id
            if len(posts) == 1:
                return httpx.Response(200, json=[{"success": True, "orderID": oid}])
            return httpx.Response(200, json=[{"success": False, "errorMsg": FAK_NO_MATCH}])
        order, trade = evidence(m, e, first_oid, "5", status="MINED")
        if req.url.path.startswith("/data/order/"):
            return httpx.Response(200, json=order)
        return httpx.Response(200, json={"data": [trade], "next_cursor": "LTE="})

    q = httpx.Client(transport=httpx.MockTransport(wire))
    monkeypatch.setattr(helpers, "_http_client", q)
    monkeypatch.setattr(m, "collect", lambda: None)
    try:
        await m.submit(ep)
        first_oid = m.ledger.attempts()[0]["order_id"]
        await m.read_order(first_oid)
        assert m.ledger.attempt(first_oid)["state"] == "SETTLING"
        # Two residual FAKs get definitive zero-fill responses: retry backoff is .2s.
        await m.submit(ep)
        await m.submit(ep)
        due = m.retry_after[ep.episode_id]
        assert len(posts) == 3 and due > c.now + .15
        # An unchanged older MINED trade is polled every .1s. It must not restart
        # the residual order's backoff indefinitely while settlement is delayed.
        for _ in range(3):
            c.now += .1
            fresh(f, c, ".31")
            await m.read_order(first_oid)
        assert m.retry_after[ep.episode_id] == due
        await m.advance_entries(c.now)
        assert ep.episode_id in m.post_jobs
        await m.post_jobs[ep.episode_id]
        assert len(posts) == 4
        assert m.ledger.pending_cost(ep.episode_id) > 0
        assert m.ledger.committed() <= 10
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()
        await asyncio.to_thread(q.close)


@pytest.mark.parametrize("fault", ["read_timeout", "write_timeout", "disconnect", "500", "408"])
async def test_lost_post_response_waits_for_owned_proof_and_network_recovery(
    tmp_path, monkeypatch, fault
):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".30")
    ep = next(iter(m.pending.values()))
    network = NetworkHealth(e.gateway._client.sm_config.obi, monotonic=lambda: c.now)
    e.reads.network = e.gateway._client.network = network
    posts, reads = [], []
    phase = "order_missing"

    def probes():
        for _ in range(max(network.cfg.network_min_samples, network.cfg.network_recovery_samples)):
            network.observe("GET", .001, endpoint="/time")

    def wire(req):
        if req.method == "POST":
            posts.append(json.loads(req.content))
            if len(posts) == 1:
                errors = {"read_timeout": httpx.ReadTimeout, "write_timeout": httpx.WriteTimeout,
                          "disconnect": httpx.RemoteProtocolError}
                if fault in errors:
                    raise errors[fault]("synthetic lost reply", request=req)
                return httpx.Response(int(fault), json={"error": "synthetic uncertain response"})
            oid = e.gateway._client._operation_value("attempt").expected_order_id
            return httpx.Response(200, json=[{"success": True, "orderID": oid}])
        reads.append(str(req.url))
        oid = m.ledger.attempts()[0]["order_id"]
        order, trade = evidence(m, e, oid, "5", status="MINED")
        if req.url.path.startswith("/data/order/"):
            if phase == "order_missing":
                return httpx.Response(404, json={"error": "not indexed yet"})
            return httpx.Response(200, json=order)
        assert req.url.path == "/data/trades" and req.url.params["id"] == trade["id"]
        return httpx.Response(200, json={"data": [trade] if phase == "ready" else [],
                                        "next_cursor": "LTE="})

    q = httpx.Client(transport=httpx.MockTransport(wire))
    monkeypatch.setattr(helpers, "_http_client", q)
    monkeypatch.setattr(m, "collect", lambda: None)
    try:
        probes()
        assert network.snapshot().allowed
        await m.submit(ep)
        oid = m.ledger.attempts()[0]["order_id"]
        reserve = m.ledger.committed()
        assert m.ledger.attempt(oid)["state"] == "UNKNOWN"
        assert network.state == "PAUSED"
        # 404 and missing trade rows cannot authorize another native POST.
        await m.submit(ep)
        phase = "trade_missing"
        await m.read_order(oid)
        assert m.ledger.unresolved(ep.episode_id) and m.ledger.committed() == reserve
        # Network health alone is insufficient: the order must also be resolved.
        c.now = network.pause_until + .01
        probes()
        fresh(f, c, ".31")
        await m.refresh_account()
        assert network.snapshot().allowed
        await m.submit(ep)
        assert len(posts) == 1 and m.ledger.unresolved(ep.episode_id)
        phase = "ready"
        await m.read_order(oid)
        assert m.ledger.attempt(oid)["state"] == "SETTLING"
        assert not m.ledger.unresolved(ep.episode_id)
        c.now = m.retry_after[ep.episode_id] + .001
        fresh(f, c, ".31")
        await m.advance_entries(c.now)
        await m.post_jobs[ep.episode_id]
        assert len(posts) == 2 and m.ledger.attempts()[-1]["order_id"] != oid
        assert m.ledger.pending_cost(ep.episode_id) > 0
        assert (m.ledger.budget_used(ep.episode_id)
                + D(m.ledger.plan(ep.episode_id)["planned_cost_usdc"]) <= 5)
        assert all("/data/order/" in r or "/data/trades?" in r for r in reads)
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()
        await asyncio.to_thread(q.close)


async def test_slow_native_get_does_not_hold_other_signals_post(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch)
    first = next(iter(m.pending.values()))
    entered, release = asyncio.Event(), Event()
    loop = asyncio.get_running_loop()
    reader = None

    def wire(req):
        if req.method == "POST":
            return t._transport.handle_request(req)
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5), "test did not release synthetic query"
        return httpx.Response(404, json={"error": "delayed index"})

    q = httpx.Client(transport=httpx.MockTransport(wire))
    monkeypatch.setattr(helpers, "_http_client", q)
    try:
        await m.submit(first)
        oid = m.ledger.attempts()[0]["order_id"]
        reader = asyncio.create_task(m.read_order(oid))
        await asyncio.wait_for(entered.wait(), 2)
        second = op("native-get-second", price=".709", token="2")
        m.ledger.observe(second, minimum=5, elapsed=m.elapsed())
        m.ledger.plan_signal(second.episode_id, f.books["2"].tick, 5)
        await asyncio.wait_for(m.submit(second), 2)
        assert len(calls) == 2 and not reader.done()
        assert m.ledger.row(second.episode_id)["state"] == "ACK"
        release.set()
        await reader
        assert m.ledger.unresolved(first.episode_id)
    finally:
        release.set()
        if reader:
            await reader
        await close(m, e)
        t.close()
        await asyncio.to_thread(q.close)


async def test_settling_query_recovery_requeues_task_once(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".30")
    ep = next(iter(m.pending.values()))
    oid = None
    missing = False

    def wire(req):
        if req.method == "POST":
            return t._transport.handle_request(req)
        if missing:
            return httpx.Response(404, json={"error": "transient missing index"})
        order, trade = evidence(m, e, oid, "5", status="MINED")
        if req.url.path.startswith("/data/order/"):
            return httpx.Response(200, json=order)
        return httpx.Response(200, json={"data": [trade], "next_cursor": "LTE="})

    q = httpx.Client(transport=httpx.MockTransport(wire))
    monkeypatch.setattr(helpers, "_http_client", q)
    try:
        await m.submit(ep)
        oid = m.ledger.attempts()[0]["order_id"]
        await m.read_order(oid)
        missing = True
        await m.read_order(oid)
        assert m.ledger.unresolved(ep.episode_id)
        assert m.ledger.row(ep.episode_id)["state"] == "WAIT_SETTLEMENT"
        missing = False
        c.now += .05
        fresh(f, c, ".31")
        await m.read_order(oid)
        due = m.retry_after[ep.episode_id]
        assert due == pytest.approx(c.now + .01)
        assert not m.ledger.unresolved(ep.episode_id)
        c.now += .02
        await m.read_order(oid)
        assert m.retry_after[ep.episode_id] == due
        # A restored ready task not currently queued must also be scheduled.
        m.pending.pop(ep.episode_id)
        await m.read_order(oid)
        assert ep.episode_id in m.pending
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()
        await asyncio.to_thread(q.close)
