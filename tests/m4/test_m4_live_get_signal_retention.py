"""Signals arriving during blocked native GETs; synthetic SDK/HTTP only."""

import asyncio
import sqlite3
from contextlib import closing
from threading import Event
from types import MethodType

import httpx
import pytest
import test_m4_live_taker as legacy
from m4_helpers import close, form
from py_clob_client_v2.config import get_contract_config
from py_clob_client_v2.http_helpers import helpers
from test_m4_live_budget import fixture

from polymaker.m4_execution import M4Executor
from polymaker.rdp_sm import _Reads
from polymaker.rdp_sm_network import NetworkHealth


@pytest.mark.parametrize("endpoint", ["/time", "/balance-allowance"])
@pytest.mark.parametrize("outcome", ["timeout", "late_success"])
@pytest.mark.parametrize("recovery", ["same_process", "restart", "after_t240"])
async def test_new_signal_is_durable_during_get_and_resumes_until_t240(
    tmp_path, monkeypatch, endpoint, outcome, recovery
):
    # Start without a signal. Form a real detector signal only after the native
    # GET worker is blocked, rather than injecting an already admitted task.
    monkeypatch.setattr(legacy, "form", lambda *_: None)
    m, e, f, c, calls, transport = await fixture(tmp_path, monkeypatch)
    original_transport = transport
    reader_entered, release = asyncio.Event(), Event()
    loop = asyncio.get_running_loop()
    blocked_once = False
    spender = get_contract_config(137).exchange_v2
    network = NetworkHealth(e.gateway._client.sm_config.obi, monotonic=lambda: c.now)
    e.reads.network = e.gateway._client.network = network
    for _ in range(max(network.cfg.network_min_samples, network.cfg.network_recovery_samples)):
        network.observe("GET", .001, endpoint="/time")

    def wire(request):
        nonlocal blocked_once
        path = request.url.path
        if request.method == "POST":
            return original_transport._transport.handle_request(request)
        assert request.method == "GET" and path in {"/time", "/balance-allowance"}
        if path == endpoint and not blocked_once:
            blocked_once = True
            loop.call_soon_threadsafe(reader_entered.set)
            assert release.wait(5), "synthetic GET was not released"
            if outcome == "timeout":
                raise httpx.ReadTimeout("synthetic five-second GET timeout", request=request)
        if path == "/time":
            return httpx.Response(200, json=int(f.wall()))
        return httpx.Response(200, json={
            "balance": "40000000", "allowances": {spender: "40000000"},
        })

    query_transport = httpx.Client(transport=httpx.MockTransport(wire))
    monkeypatch.setattr(helpers, "_http_client", query_transport)
    # Exercise production account/clock gathering and SDK thread offloading,
    # rather than the fixture's immediate cash stub.
    e.account_snapshot = MethodType(M4Executor.account_snapshot, e)
    e.reads.server_time = MethodType(_Reads.server_time, e.reads)
    m.account, m.account_reason = None, "M4_ACCOUNT_WARMUP"
    try:
        assert not m.ledger.rows() and not m.pending
        await m.advance()
        await asyncio.wait_for(reader_entered.wait(), 2)
        assert m.account_task is not None and not m.account_task.done()

        form(f, c)
        signal = f.m1.selected
        assert signal is not None
        await asyncio.wait_for(m.advance(), 1)
        assert not m.account_task.done() and not calls
        assert m.ledger.row(signal.episode_id)["state"] == "OBSERVED"
        assert signal.episode_id in m.pending
        # A second connection proves this is persisted, not only an in-memory
        # selection that disappears at the end of its short signal lifetime.
        with closing(sqlite3.connect(m.ledger.path.as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("PRAGMA query_only=ON")
            assert db.execute("SELECT id FROM opportunities").fetchall() == [(signal.episode_id,)]

        c.now = max(c.now + 5.1, signal.order_deadline + .01)
        f.m1.invalidate("SYNTHETIC_EVIDENCE_ENDED")
        release.set()
        await asyncio.wait_for(m.account_task, 2)
        assert not m.account_current() and m.account_reason
        assert not calls and signal.episode_id in m.pending
        assert m.ledger.row(signal.episode_id)["state"] == "OBSERVED"
        if outcome == "timeout":
            assert network.state == "PAUSED"
        else:
            assert m.account_reason == "M4_ACCOUNT_READ_TOO_OLD"

        c.now = max(c.now, network.pause_until + .01)
        if recovery == "after_t240":
            c.now = max(c.now, m.settings.cutoff_sec - 20 + 1)
        # Drive successful production GETs: neither one successful time probe
        # nor an old balance is used to bypass the recovery/account guards.
        for _ in range(max(network.cfg.network_min_samples, network.cfg.network_recovery_samples) + 1):
            await m.refresh_account()
        assert m.account_current() and network.snapshot().allowed

        if recovery == "restart":
            old_now = c.now
            await close(m, e)
            transport.close()
            m, e, f, c, calls, transport = await fixture(tmp_path, monkeypatch, recover=True)
            c.now = old_now
            await m.refresh_account()
            assert signal.episode_id in m.pending and not calls
            assert not f.m1.frequency.candidates

        if recovery == "after_t240":
            await m.advance()
            m.finish_pending()
            assert not calls and not m.ledger.attempts()
            row = m.ledger.row(signal.episode_id)
            assert row["state"] == "CUTOFF" and row["reason"] == "M4_T240_RESIDUAL"
        else:
            assert c.now > signal.order_deadline
            await m.advance()
            assert signal.episode_id in m.post_jobs
            await m.post_jobs[signal.episode_id]
            assert len(calls) == 1
            signed = calls[0]["order"]
            assert signed["makerAmount"] == "4260000"
            assert signed["takerAmount"] == "6000000"
            row = m.ledger.row(signal.episode_id)
            assert row["posts"] == 1 and row["state"] == "ACK"
            # ACK remains unresolved: scheduling another pass cannot repeat it.
            await m.advance_entries(c.now)
            assert len(calls) == 1 and m.ledger.unresolved(signal.episode_id)
        m.ledger.validate_recovery()
    finally:
        release.set()
        await close(m, e)
        transport.close()
        original_transport.close()
        await asyncio.to_thread(query_transport.close)
