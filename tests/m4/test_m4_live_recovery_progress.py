"""Owned-order recovery and bounded liquidity retries; all HTTP is synthetic."""

import asyncio
import json
from dataclasses import replace
from decimal import Decimal as D
from types import SimpleNamespace

import httpx
import pytest
from m4_helpers import close
from py_clob_client_v2.http_helpers import helpers
from test_m4_live_budget import fixture
from test_m4_live_task import evidence

from polymaker.m4_live_task import FAK_NO_MATCH, zero_rejection


@pytest.mark.parametrize("status", [200, 400])
async def test_documented_no_match_releases_cash_and_retries_new_id(tmp_path, monkeypatch, status):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch)
    posts = []

    def wire(req):
        assert req.method == "POST"
        posts.append(json.loads(req.content)[0])
        body = {"error": FAK_NO_MATCH} if status == 400 else [
            {"success": False, "errorMsg": FAK_NO_MATCH, "orderID": "",
             "makingAmount": "0", "takingAmount": "0", "tradeIDs": [], "status": ""}]
        return httpx.Response(status, json=body)

    q = httpx.Client(transport=httpx.MockTransport(wire))
    monkeypatch.setattr(helpers, "_http_client", q)
    try:
        ep = next(iter(m.pending.values()))
        for _ in range(2):
            await m.submit(ep)
            assert m.ledger.row(ep.episode_id)["state"] == "RESERVED"
            assert m.ledger.committed() == 0 and not m.ledger.unresolved(ep.episode_id)
        attempts = m.ledger.attempts()
        assert len(posts) == 2 and attempts[0]["order_id"] != attempts[1]["order_id"]
        assert all(json.loads(a["proof"])["kind"] == "FAK_NO_MATCH" for a in attempts)
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()
        await asyncio.to_thread(q.close)


@pytest.mark.parametrize("conflict", [
    {"takingAmount": "1"}, {"makingAmount": "0.01"}, {"makingAmount": "NaN"},
    {"orderID": "0x" + "f" * 64}, {"orderId": "other"}, {"status": "matched"},
    {"tradeIDs": ["trade-1"]}, {"transactionsHashes": ["tx"]}, {"success": True},
    {"errorMsg": "no orders found"}, {"errorMsg": FAK_NO_MATCH + " order match delayed"},
])
def test_no_match_requires_unambiguous_zero(conflict):
    assert zero_rejection({"success": False, "errorMsg": FAK_NO_MATCH, **conflict}, D(5)) is None


@pytest.mark.parametrize("status,body", [
    (500, {"error": FAK_NO_MATCH}),
    (400, {"error": "order match delayed due to market conditions"}),
    (400, {"error": "not enough balance / allowance", "takingAmount": "1"}),
    (400, {"error": FAK_NO_MATCH, "makingAmount": "1"}),
])
async def test_uncertain_or_conflicting_rejection_never_reposts(tmp_path, monkeypatch, status, body):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch)
    posts = []

    def wire(req):
        if req.method == "POST":
            posts.append(req.content)
            return httpx.Response(status, json=body)
        return httpx.Response(404, json={"error": "not found"})

    q = httpx.Client(transport=httpx.MockTransport(wire))
    monkeypatch.setattr(helpers, "_http_client", q)
    try:
        ep = next(iter(m.pending.values()))
        await m.submit(ep)
        await m.submit(ep)
        assert len(posts) == 1 and m.ledger.unresolved(ep.episode_id)
        assert m.ledger.committed() > 0
    finally:
        await close(m, e)
        t.close()
        await asyncio.to_thread(q.close)


async def receipt_case(tmp_path, monkeypatch, *, state="LIVE", conflict=None):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".30")
    posts, reads = [], []

    def wire(req):
        if req.method == "POST":
            posts.append(json.loads(req.content)[0])
            oid = e.gateway._client._operation_value("attempt").expected_order_id
            receipt = {"success": True, "orderID": oid, "status": "matched", "errorMsg": "",
                       "makingAmount": "1.55", "takingAmount": "5", "tradeIDs": ["trade-1"]}
            if conflict == "receipt_principal":
                receipt["makingAmount"] = "1.54"
            if conflict and conflict.startswith("missing_receipt_"):
                receipt.pop(conflict.removeprefix("missing_receipt_"))
            return httpx.Response(200, json=[receipt])
        reads.append(str(req.url))
        oid = m.ledger.attempts()[0]["order_id"]
        order, trade = evidence(m, e, oid, "5", status="MINED")
        if state != "MATCHED":
            order.update(size_matched="0", associate_trades=[], status=state)
        changes = {
            "id": {"id": "0x" + "f" * 64}, "token": {"asset_id": "2"},
            "market": {"market": "other"}, "owner": {"maker_address": "other"},
            "price": {"price": ".32"}, "size": {"original_size": "1"},
            "extra_quantity": {"size_matched": "6"},
            "extra_trade": {"associate_trades": ["trade-2"]},
            "terminal_zero": {"status": "CANCELED"},
            "invalid_list": {"associate_trades": ["../foreign"]},
        }
        order.update(changes.get(conflict, {}))
        if req.url.path.startswith("/data/order/"):
            return httpx.Response(200, json=order)
        assert req.url.params["id"] == "trade-1"
        if conflict == "foreign_trade":
            trade["taker_order_id"] = "0x" + "f" * 64
        trades = [] if conflict == "missing_trade" else [trade]
        return httpx.Response(200, json={"data": trades, "next_cursor": "LTE=",
                                        "count": len(trades), "limit": 100})

    q = httpx.Client(transport=httpx.MockTransport(wire))
    monkeypatch.setattr(helpers, "_http_client", q)
    return m, e, f, c, t, q, posts, reads


@pytest.mark.parametrize("state", ["LIVE", "DELAYED"])
async def test_complete_receipt_recovers_lagging_order_and_funds_remainder(tmp_path, monkeypatch, state):
    m, e, f, c, t, q, posts, reads = await receipt_case(tmp_path, monkeypatch, state=state)
    try:
        ep = next(iter(m.pending.values()))
        await m.submit(ep)
        oid = m.ledger.row(ep.episode_id)["order_id"]
        await m.reconcile(oid)
        a = m.ledger.attempt(oid)
        assert a["state"] == "SETTLING" and m.ledger.row(ep.episode_id)["state"] == "RESERVED"
        assert m.ledger.pending_cost(ep.episode_id) == D("1.624881")
        assert json.loads(a["proof"])["lagging_order_observation"]["status"] == state
        assert len(reads) == 2 and "/data/trades?" in reads[-1]
        await m.submit(ep)
        assert len(posts) == 2 and m.ledger.attempts()[-1]["order_id"] != oid
        plan = m.ledger.plan(ep.episode_id)
        assert D(plan["planned_cost_usdc"]) + m.ledger.pending_cost(ep.episode_id) <= 5
        assert m.ledger.committed() <= 10
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()
        await asyncio.to_thread(q.close)


@pytest.mark.parametrize("missing", ["tradeIDs", "makingAmount", "takingAmount"])
async def test_complete_query_recovers_incomplete_post_receipt(tmp_path, monkeypatch, missing):
    m, e, f, c, t, q, posts, reads = await receipt_case(
        tmp_path, monkeypatch, state="MATCHED", conflict="missing_receipt_" + missing)
    try:
        ep = next(iter(m.pending.values()))
        await m.submit(ep)
        oid = m.ledger.row(ep.episode_id)["order_id"]
        await m.reconcile(oid)
        assert m.ledger.attempt(oid)["state"] == "SETTLING"
        assert not m.ledger.unresolved(ep.episode_id)
        assert m.ledger.row(ep.episode_id)["state"] == "RESERVED"
        assert m.ledger.pending_cost(ep.episode_id) == D("1.624881")
    finally:
        await close(m, e)
        t.close()
        await asyncio.to_thread(q.close)


@pytest.mark.parametrize("conflict", [
    "id", "token", "market", "owner", "price", "size", "extra_quantity", "extra_trade",
    "terminal_zero", "invalid_list", "receipt_principal", "missing_trade", "foreign_trade",
])
async def test_receipt_recovery_cannot_erase_conflicts_or_missing_trades(tmp_path, monkeypatch, conflict):
    m, e, f, c, t, q, posts, reads = await receipt_case(tmp_path, monkeypatch, conflict=conflict)
    try:
        ep = next(iter(m.pending.values()))
        await m.submit(ep)
        await m.reconcile(m.ledger.row(ep.episode_id)["order_id"])
        assert m.ledger.unresolved(ep.episode_id)
        await m.submit(ep)
        assert len(posts) == 1 and m.ledger.committed() > 0
    finally:
        await close(m, e)
        t.close()
        await asyncio.to_thread(q.close)


def fresh(f, c, ask, size=100):
    f.books["1"] = replace(f.books["1"], source_time=f.wall(), received_mono=c.now,
                            asks=((D(ask), D(size)),))


async def backed_off(tmp_path, monkeypatch):
    result = await fixture(tmp_path, monkeypatch, response="KILLED")
    m, e, f, c, calls, t = result
    monkeypatch.setattr(m, "collect", lambda: None)
    ep = next(iter(m.pending.values()))
    for _ in range(6):
        await m.submit(ep)
    assert m.retry_after[ep.episode_id] - c.now == pytest.approx(2)
    return result, ep


@pytest.mark.parametrize("recovery", ["price", "depth"])
async def test_better_liquidity_wakes_proven_zero_before_two_second_retry(tmp_path, monkeypatch, recovery):
    (m, e, f, c, calls, t), ep = await backed_off(tmp_path, monkeypatch)
    try:
        fresh(f, c, ".72" if recovery == "price" else ".71")
        await m.advance_entries(c.now)
        assert not m.post_jobs
        c.now += .101
        fresh(f, c, ".71", 101 if recovery == "depth" else 100)
        await m.advance_entries(c.now)
        assert len(m.post_jobs) == 1
        await asyncio.gather(*m.post_jobs.values())
        assert len(calls) == 7
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()


async def test_price_wakeup_preserves_minimum_interval_and_no_change_backoff(tmp_path, monkeypatch):
    (m, e, f, c, calls, t), ep = await backed_off(tmp_path, monkeypatch)
    try:
        for _ in range(5):
            c.now += .01
            fresh(f, c, ".71")
            await m.advance_entries(c.now)
        assert not m.post_jobs
        fresh(f, c, ".72")
        await m.advance_entries(c.now)
        c.now += .01
        fresh(f, c, ".71")
        await m.advance_entries(c.now)
        assert not m.post_jobs
        c.now += .05
        fresh(f, c, ".71")
        await m.advance_entries(c.now)
        await asyncio.gather(*m.post_jobs.values())
        assert len(calls) == 7
    finally:
        await close(m, e)
        t.close()


@pytest.mark.parametrize("block", ["rate_limit", "unknown", "network", "stale", "cutoff", "stop"])
async def test_liquidity_wakeup_never_bypasses_other_gates(tmp_path, monkeypatch, block):
    (m, e, f, c, calls, t), ep = await backed_off(tmp_path, monkeypatch)
    try:
        if block == "rate_limit":
            # Prior HTTP 429 rejection is not a no-liquidity retry class.
            with m.ledger.lock, m.ledger.db:
                m.ledger.db.execute("UPDATE taker_attempts SET proof=? WHERE order_id=?",
                                    (json.dumps({"kind": "HTTP_429"}), m.ledger.attempts()[-1]["order_id"]))
        if block == "unknown":
            with m.ledger.lock, m.ledger.db:
                m.ledger.db.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)",
                                    ("blocked:" + m.ledger.attempts()[-1]["order_id"], '{"reason":"conflict"}'))
        fresh(f, c, ".72")
        await m.advance_entries(c.now)
        c.now += .101
        fresh(f, c, ".71")
        if block == "network":
            e.reads.network.snapshot = lambda: SimpleNamespace(allowed=False, reason="NETWORK_BLOCK")
        elif block == "stale":
            f.books["1"] = replace(f.books["1"], source_time=f.wall() - 1)
        elif block == "cutoff":
            e.official_time += 240
        elif block == "stop":
            m.stop.set()
        await m.advance_entries(c.now)
        await asyncio.gather(*m.post_jobs.values())
        assert len(calls) == 6
    finally:
        await close(m, e)
        t.close()
