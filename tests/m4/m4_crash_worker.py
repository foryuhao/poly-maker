"""Offline subprocess fixture: abruptly exit without cleanup after a durable boundary."""

import asyncio
import os
import socket
import sys
from pathlib import Path

import httpx
import pytest
from py_clob_client_v2.http_helpers import helpers
from test_m4_live_budget import fixture
from test_m4_live_task import evidence, serve


async def main(directory, boundary):
    monkeypatch = pytest.MonkeyPatch()

    def forbidden(*args, **kwargs):
        raise AssertionError("crash worker forbids all network connections")

    for name in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, name, forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    m, e, f, c, calls, t = await fixture(directory, monkeypatch, price=".30")
    ep = next(iter(m.pending.values()))

    def end():
        os._exit(71)  # No finally, journal flush, connection close, or Python cleanup.

    def hook(name):
        original = getattr(m.ledger, name)

        def stop_after(*args, **kwargs):
            original(*args, **kwargs)
            end()

        monkeypatch.setattr(m.ledger, name, stop_after)

    if boundary == "transport":
        def wire(req):
            assert req.method == "POST"
            end()  # The request may have reached the venue; no response survived.

        q = httpx.Client(transport=httpx.MockTransport(wire))
        monkeypatch.setattr(helpers, "_http_client", q)
    elif boundary in {"next_plan", "prepare", "post_start", "post_receipt"}:
        hook(boundary)
    if boundary in {"next_plan", "prepare", "post_start", "post_receipt", "transport"}:
        await m.submit(ep)
    else:
        await m.submit(ep)
        oid = m.ledger.attempts()[0]["order_id"]
        status = "MINED" if boundary == "settling" else "CONFIRMED"
        order, trade = evidence(m, e, oid, "5", status=status)
        serve(monkeypatch, t, order, trade)
        if boundary == "match_bound":
            original = m.ledger.set

            def stop_after_bound(key, value):
                original(key, value)
                if key == "matched_lower_bound:" + oid:
                    end()

            monkeypatch.setattr(m.ledger, "set", stop_after_bound)
        else:
            hook(boundary)
        await m.reconcile(oid)
    raise AssertionError("crash boundary was never reached: " + boundary)


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]), sys.argv[2]))
