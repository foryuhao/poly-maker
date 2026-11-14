"""Repeatable venue-state sequences with an independent cash and POST oracle."""

import asyncio
import json
import random
from collections import Counter
from dataclasses import replace
from decimal import ROUND_FLOOR, ROUND_HALF_UP
from decimal import Decimal as D

import httpx
import pytest
from m4_helpers import close, op
from py_clob_client_v2.http_helpers import helpers
from test_m4_live_budget import fixture
from test_m4_live_task import evidence


@pytest.mark.parametrize("seed", range(12))
async def test_delay_finality_restart_sequences_never_duplicate_or_overbook(tmp_path, monkeypatch, seed):
    rng = random.Random(seed)
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".30")
    transports = [t]
    second = op("sequence-other-side", token="2", price=".309")
    m.ledger.observe(second, minimum=5, elapsed=20)
    m.ledger.plan_signal(second.episode_id, D(".001"), 5)
    m.pending[second.episode_id] = second
    keys = [r["id"] for r in m.ledger.rows()]
    venue, posts = {}, []
    fault = "normal"

    def wire(req):
        if req.method == "POST":
            attempt = e.gateway._client._operation_value("attempt")
            oid = attempt.expected_order_id
            posts.append(oid)
            assert oid not in venue
            assert m.elapsed() < 240
            wire_order = json.loads(req.content)[0]
            qty = D(wire_order["order"]["takerAmount"]) / 1_000_000
            price = D(str(attempt.quote.price))
            assert qty >= 5 or qty * price > 1
            fraction = rng.choice([D(0), D(".2"), D(".5"), D(1)])
            fill = (qty * fraction / D(".1")).to_integral_value(rounding=ROUND_FLOOR) * D(".1")
            actual = price - rng.choice([D(0), D(".02")])
            fee = (fill * D(".07") * actual * (1 - actual)).quantize(D(".00001"), rounding=ROUND_HALF_UP)
            venue[oid] = {"qty": fill, "price": actual, "status": "MINED",
                          "cost": fill * actual + fee, "key": attempt.quote.window_id}
            response = rng.choice(["ack", "timeout", "500"])
            if response == "timeout":
                raise httpx.ReadTimeout("synthetic lost POST response", request=req)
            return httpx.Response(500 if response == "500" else 200,
                                  json={"error": "temporary failure"} if response == "500"
                                  else [{"success": True, "orderID": oid}])
        if req.url.path.startswith("/data/order/"):
            oid = req.url.path.rsplit("/", 1)[1]
            v = venue[oid]
            if fault == "404":
                return httpx.Response(404, json={"error": "index lag"})
            order, _ = evidence(m, e, oid, v["qty"], price=v["price"])
            if fault == "live":
                order.update(status="LIVE", size_matched="0", associate_trades=[])
            return httpx.Response(200, json=order)
        tid = req.url.params["id"]
        oid = next(a["order_id"] for a in m.ledger.attempts() if "trade-" + str(a["seq"]) == tid)
        v = venue[oid]
        status = "MINED" if fault == "old_trade" else v["status"]
        _, trade = evidence(m, e, oid, v["qty"], price=v["price"], status=status)
        return httpx.Response(200, json={"data": [] if fault == "missing_trade" else [trade],
                                        "next_cursor": "LTE="})

    q = httpx.Client(transport=httpx.MockTransport(wire))
    transports.append(q)
    monkeypatch.setattr(helpers, "_http_client", q)
    try:
        for step in range(60):
            if step in {12, 27, 46}:
                await close(m, e)
                m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".30", recover=True)
                transports.append(t)
                monkeypatch.setattr(helpers, "_http_client", q)
            elapsed = 20 + step if step < 48 else 240 + step - 48
            e.official_time = int(e.meta.slug.rsplit("-", 1)[1]) + elapsed
            e.time_anchor = c.now
            f.books = {k: replace(b, source_time=f.wall(), received_mono=c.now)
                       for k, b in f.books.items()}
            await m.refresh_account()
            pending = [v for v in venue.values() if v["status"] == "MINED"]
            if pending and rng.random() < .55:
                rng.choice(pending)["status"] = rng.choice(["CONFIRMED", "FAILED"])
            fault = rng.choice(["normal", "normal", "404", "live", "missing_trade", "old_trade"])
            active = [a for a in m.ledger.attempts() if a["state"] in {"ACK", "UNKNOWN", "SETTLING"}]
            if active:
                await m.read_order(rng.choice(active)["order_id"])
            before = len(posts)
            key = rng.choice(keys)
            await m.submit(m.restored_opportunity(m.ledger.row(key)))
            if elapsed >= 240:
                assert len(posts) == before
            assert all(n == 1 for n in Counter(posts).values())
            m.ledger.validate_recovery()
            assert m.ledger.committed() <= 10
            liability = sum((v["cost"] for v in venue.values() if v["status"] != "FAILED"), D(0))
            assert liability <= m.ledger.committed()
            for key in keys:
                actual = sum((v["cost"] for v in venue.values()
                              if v["key"] == key and v["status"] != "FAILED"), D(0))
                assert actual <= 5  # Fixed-size improved fills in this oracle cannot overrun the plan.
        # Resolve everything after the buy deadline: only accounting may change.
        fault = "normal"
        for v in venue.values():
            if v["status"] == "MINED":
                v["status"] = "CONFIRMED"
        before = len(posts)
        for a in m.ledger.attempts():
            await m.read_order(a["order_id"])
        for key in keys:
            row = m.ledger.row(key)
            expected = sum((v["cost"] for v in venue.values()
                            if v["key"] == key and v["status"] == "CONFIRMED"), D(0))
            assert D(row["spent"]) == expected
            await m.submit(m.restored_opportunity(row))
        assert len(posts) == before and len(posts) >= 2
        m.ledger.validate_recovery()
    finally:
        if not e.closed:
            await close(m, e)
        for transport in transports:
            await asyncio.to_thread(transport.close)
