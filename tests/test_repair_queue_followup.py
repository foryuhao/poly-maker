"""03:25 live session: preserve TP on pure confirmation; drain canceled POST."""

import asyncio
import time
from dataclasses import replace
from decimal import Decimal

import httpx
import pytest

from polymaker.domain import TradeState
from polymaker.rdp_sm_cached_reads import CachedExecutionReads
from polymaker.rdp_sm_entry import AccountRiskPolicy
from polymaker.rdp_sm_path_repair import PathRepairController
from polymaker.rdp_sm_sync_companion import SyncCompanion
from polymaker.rdp_sm_sync_reads import ObserverReader
from test_rdp_sm_cached_reads import attach, finish
from test_rdp_sm_canceled_submission import canceled_reply
from test_rdp_sm_entry import boundary
from test_rdp_sm_m1 import ADDRESS, CID
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_obi_execution import cleanup, setup_obi
from test_rdp_sm_repair import env as env
from test_rdp_sm_repair import open_yes, reflect_positions


async def until(predicate):
    async with asyncio.timeout(3):
        while not predicate():  # noqa: ASYNC110 - independent SDK/cache producers
            await asyncio.sleep(.005)


@pytest.mark.parametrize("source", ["REST", "WS"])
async def test_position_proven_confirmation_preserves_same_tp_queue(env, monkeypatch, tmp_path, source):
    setup = await setup_obi(env, monkeypatch)
    v, e, _, lifecycle, _, _, _, pump, _ = setup
    official, anchor = e.official_time, e.time_anchor

    async def server_time():
        e.reads.network.observe("GET", .001, endpoint="/time")
        return official+time.monotonic()-anchor

    monkeypatch.setattr(e.reads, "server_time", server_time)
    await open_yes(setup, status="MINED")
    v.trades[0]["trader_side"] = "MAKER"
    v.trades[0]["maker_orders"][0]["maker_address"] = ADDRESS
    opening = e.attempts[0].order_id
    key = next(iter(e.position_reconciled))
    assert e.proofs[opening]["outcome"] == "POSITION_RECONCILED"

    def clock(request):
        if request.url.path == "/time":
            return httpx.Response(200, json=official+time.monotonic()-anchor)

    companion, _ = await attach(setup, tmp_path, hook=clock)
    repair = PathRepairController(e, lifecycle)
    monkeypatch.setattr(repair, "sigma", lambda: .02)
    pump.update(bid=".50", yes_ask=".52", no_bid=".48", no_ask=".50")
    await asyncio.sleep(.03)
    observations = {}

    async def venue_events():
        await until(lambda: len(e.attempts) == 2 and e.attempts[-1].outcome == "ACK")
        oid = e.attempts[-1].order_id
        v.trades[0]["status"] = "CONFIRMED"
        if source == "WS":
            e.processor.on_trade(replace(e.position_reconciled[key], status=TradeState.CONFIRMED), CID)
        await until(lambda: e.processor.fill_records[key].status is TradeState.CONFIRMED)
        observations["safe_before_fresh_close"] = e.safe_to_continue
        await asyncio.sleep(.12)
        observations["queue_status"] = v.rows[oid]["status"]
        if v.rows[oid]["status"] == "LIVE":
            trade = v.fill(oid, "same-tp-fill", 5)
            trade["trader_side"] = "MAKER"
            trade["maker_orders"][0]["maker_address"] = ADDRESS
            reflect_positions(v)

    task = asyncio.create_task(venue_events())
    try:
        lifecycle.account = (await e._snapshot(include_books=False))[3]
        result, _ = await repair.repair(*e.economic_inventory(), "same-tp")
        await task
        assert observations["queue_status"] == "LIVE"
        assert not observations["safe_before_fresh_close"]
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert e.economic_inventory() == (Decimal(5), Decimal(5))
        assert len(v.post_requests) == 2 and e.repair_episode is None
        await e.journal.flush()
        reasons = [r["data"]["payload"]["reason"] for r in journal_records(e.journal.path)
                   if r["kind"] == "sm_obi_close_requested"]
        assert "SESSION_ECONOMICS_CHANGED" not in reasons
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await finish(setup, companion)


async def test_identity_bound_canceled_post_allows_fresh_account_without_timeout(
        env, monkeypatch, tmp_path, record_property):
    setup = await setup_obi(env, monkeypatch, horizon=.15)
    v, e, _, lifecycle, normal, wire, *_ = setup
    cfg = e.cfg
    cfg.rdp_sm.obi.cancel_retry_sec = .25
    cfg.rdp_sm.sync_observe.enabled = cfg.rdp_sm.sync_observe.use_for_execution = True
    cfg.rdp_sm.sync_observe.order_interval_s = 1.
    cfg.rdp_sm.sync_observe.account_interval_s = 2.
    cfg.rdp_sm.sync_observe.output_dir = str(tmp_path / "cache")
    official, anchor = e.official_time, e.time_anchor

    def reads(request):
        time.sleep(.2)
        if request.url.path == "/time":
            return httpx.Response(200, json=official+time.monotonic()-anchor)
        return v.response(request)

    companion = SyncCompanion(cfg, e.session_id, reader_factory=lambda emit, stop:
        ObserverReader(cfg, emit=emit, stop=stop, transport=httpx.MockTransport(reads)))
    companion.bind_market(e.meta, int(e.slug.rsplit("-", 1)[1]))
    companion.start()
    policy = AccountRiskPolicy(use_starting_balance=True, sync_observer=companion)
    policy.market_id = CID
    e.reads.account_policy = e.gateway._client.account_policy = policy
    e.shared_reads = CachedExecutionReads(e, companion)
    e.shared_reads.timeout_s = 4.5
    returned, deletes = [], []

    def writes(request):
        if request.method == "POST":
            time.sleep(.87)  # observed live POST delay; early DELETE precedes reply
            wire(request)
            oid = e.gateway._client.batch_attempts[0].expected_order_id
            v.rows.pop(oid)
            returned.append(time.monotonic())
            return httpx.Response(200, json=[canceled_reply(oid)], request=request)
        if request.method == "DELETE":
            deletes.append(time.monotonic())
            time.sleep(.2)
        return wire(request)

    client = boundary(monkeypatch, v, writes)
    try:
        lifecycle.account = (await e._snapshot(include_books=False))[3]
        result, _ = await normal.enter(Decimal(0), Decimal(0), "inflight-canceled")
        duration = time.monotonic()-returned[0]
        after = sum(t >= returned[0] for t in deletes)
        record_property("post_response_to_close_sec", duration)
        record_property("deletes_after_post_response", after)
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        a = e.attempts[-1]
        assert a.outcome == "REJECTED" and a.order_id in e.canceled_submission_proofs
        assert a.order_id not in e.known and e.processor.owned_record(a.order_id)
        assert any(t < returned[0] for t in deletes)
        assert after <= 2
        assert e.committed() == 0 and not e.economic_fills()
        assert duration < e.shared_reads.timeout_s and not e.shared_reads.inflight
        await e.journal.flush()
        closing = [r["data"]["payload"] for r in journal_records(e.journal.path)
                   if r["kind"] == "sm_sync_cache_read" and r["data"]["payload"]["purpose"] == "close"][-1]
        assert all(r["started_monotonic"] >= closing["operation_after_monotonic"]
                   for r in closing["sources"].values())
    finally:
        client.close()
        await finish(setup, companion)


@pytest.mark.parametrize("changed", [
    {"price": .299}, {"size": 4.99}, {"trade_id": "new-fill", "size": .01},
    {"status": TradeState.FAILED}, {"unproven": True},
])
async def test_real_fill_changes_still_invalidate_order_proof(env, monkeypatch, changed):
    setup = await setup_obi(env, monkeypatch)
    e = setup[1]
    try:
        await open_yes(setup, status="MINED")
        key, proven = next(iter(e.position_reconciled.items()))
        changes = dict(changed)
        if changes.pop("unproven", False):
            e.position_reconciled.clear()
        ev = replace(proven, **({"status": TradeState.CONFIRMED} | changes))
        e.processor.on_trade(ev, CID, source="REST")
        assert proven.maker_order_id not in e.proofs
        assert not e.safe_to_continue
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("scenario", ["partial", "full", "failed_page", "wrong_id"])
async def test_canceled_reply_still_requires_complete_uncontradicted_evidence(
        env, monkeypatch, tmp_path, scenario):
    setup = await setup_obi(env, monkeypatch)
    v, e, _, _, normal, wire, *_ = setup
    state = {"after": False, "fail": scenario == "failed_page"}

    def reads(request):
        if state["after"] and state["fail"] and request.url.path == "/data/trades":
            return httpx.Response(503, json={"error": "unavailable"})

    companion, _ = await attach(setup, tmp_path, hook=reads)

    def writes(request):
        response = wire(request)
        if request.method == "POST":
            oid = e.gateway._client.batch_attempts[0].expected_order_id
            if scenario in {"partial", "full"}:
                trade = v.fill(oid, "contradiction", 2 if scenario == "partial" else 5)
                trade["trader_side"] = "MAKER"
                trade["maker_orders"][0]["maker_address"] = ADDRESS
                reflect_positions(v)
            elif scenario == "failed_page":
                v.rows.pop(oid)
            state["after"] = True
            reply = canceled_reply("0x"+"ab"*32 if scenario == "wrong_id" else oid)
            return httpx.Response(200, json=[reply], request=request)
        return response

    client = boundary(monkeypatch, v, writes)
    try:
        result, _ = await normal.enter(Decimal(0), Decimal(0), "canceled-proof-guard")
        a = e.attempts[-1]
        if scenario == "failed_page":
            assert result.close_barrier == "BLOCKED"
            assert a.outcome == "UNKNOWN" and e.committed() == a.maximum
            assert not e.canceled_submission_proofs
            state["fail"] = False
            # The observer retains its existing failure backoff; no bypass or
            # immediate network refresh is requested by the close consumer.
            async with asyncio.timeout(5):
                while not await e.verify_close(cancel=True):
                    assert a.outcome == "UNKNOWN" and e.committed() == a.maximum
                    await asyncio.sleep(.01)
            assert a.outcome == "REJECTED" and e.committed() == 0
        else:
            assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
            assert a.outcome == "VERIFIED" and not e.canceled_submission_proofs
            quantity = 2 if scenario == "partial" else 5 if scenario == "full" else 0
            assert e.economic_inventory() == (Decimal(quantity), Decimal(0))
            assert e.committed() == Decimal(quantity)*Decimal(".5")
            assert any(a.expected_order_id in ids for ids in v.cancels)
        assert len(v.post_requests) == 1
    finally:
        client.close()
        await finish(setup, companion)


async def test_late_open_order_restarts_cancel_while_canceled_proof_is_pending(env, monkeypatch, tmp_path):
    setup = await setup_obi(env, monkeypatch)
    v, e, _, _, normal, wire, *_ = setup
    companion, _ = await attach(setup, tmp_path)
    state = {}

    def writes(request):
        response = wire(request)
        if request.method == "POST":
            oid = e.gateway._client.batch_attempts[0].expected_order_id
            state.update(oid=oid, row=v.rows.pop(oid), late=False)
            return httpx.Response(200, json=[canceled_reply(oid)], request=request)
        if request.method == "DELETE" and state and not state["late"]:
            # The first DELETE misses, then an accepted LIVE order appears in
            # the next cache frame before the canceled-submission proof closes.
            v.rows[state["oid"]] = state["row"]
            state["late"] = True
        return response

    client = boundary(monkeypatch, v, writes)
    try:
        result, _ = await normal.enter(Decimal(0), Decimal(0), "late-open-during-proof")
        assert state["late"] and v.rows[state["oid"]]["status"] == "CANCELED"
        if result.close_barrier != "SAFE_TO_CONTINUE":
            assert "ORDER_NOT_TERMINAL" in result.block_reason
            assert await e.verify_close(cancel=True), e.reason
        assert e.attempts[-1].outcome == "VERIFIED"
        assert not e.canceled_submission_proofs and e.committed() == 0
        assert len(v.cancels) >= 2 and len(v.post_requests) == 1
    finally:
        client.close()
        await finish(setup, companion)
