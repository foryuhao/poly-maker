"""18:25 live failure: per-ID retry isolation, rejection proof, precise waits."""
import asyncio
import time
from dataclasses import replace
from decimal import Decimal

import httpx
import pytest

from polymaker.domain import Side
from polymaker.rdp_sm_execution import Placement, WindowQuote
from polymaker.rdp_sm_network import NetworkHealth
from polymaker.rdp_sm_sl import SLExecution
from polymaker.rdp_sm_sync import SyncSettings
from polymaker.state.tracker import TradeEvent
from test_rdp_sm_cached_reads import attach, finish
from test_rdp_sm_canceled_submission import canceled_reply
from test_rdp_sm_entry import boundary
from test_rdp_sm_m1 import ADDRESS
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_obi_execution import cleanup
from test_rdp_sm_path_repair import env as env
from test_rdp_sm_path_repair import setup_policy
from test_rdp_sm_repair import reflect_positions
from test_rdp_sm_sync import make_service, order, wait_rounds
from test_rdp_sm_sync import sync_env as sync_env
from test_repair_sl_execution import force_sl, until


@pytest.mark.parametrize("response", [None, {"error": "missing"}, 404, 503])
def test_bad_detail_retries_only_itself_while_clock_and_collections_refresh(sync_env, response):
    venue = sync_env[0]
    bad = "missing-signed-id"

    def reply(request):
        if request.url.path.endswith("/"+bad):
            return httpx.Response(response if isinstance(response, int) else 200,
                                  json=response if not isinstance(response, int) else {})

    venue.hook = reply
    service, _ = make_service(sync_env, SyncSettings(.03, .05))
    service.track_orders({bad: time.monotonic()})
    service.start()
    try:
        events = wait_rounds(service, count=7)
        rounds = [e for e in events if e["kind"] == "sync_round" and e["lane"] == "orders"]
        assert all(e["reads_complete"] for e in rounds)
        assert any(not e["detail_coverage_complete"] for e in rounds)
        assert sum(path.endswith("/"+bad) for path, _ in venue.calls) == 1
        assert sum(path == "/time" for path, _ in venue.calls) >= 7
        assert bad in service.latest().pending_ids
        assert not next(r for r in service.latest().attempts if r.name == "order:"+bad).complete
        assert "order:order-1" in {r.name for r in service.latest().last_good}
    finally:
        assert service.stop()


def test_invalid_http200_detail_cannot_starve_real_network_recovery(sync_env):
    venue, cfg = sync_env[:2]
    bad = "missing-signed-id"
    venue.hook = lambda r: httpx.Response(200, json=None) if r.url.path.endswith("/"+bad) else None
    service, _ = make_service(sync_env, SyncSettings(.03, .05))
    cfg.rdp_sm.obi.network_pause_sec = .05  # Test clock only; recovery rules unchanged.
    service.network = NetworkHealth(cfg.rdp_sm.obi)
    service.track_orders({bad: time.monotonic()})
    service.start()
    try:
        wait_rounds(service, count=12)
        service.network.observe("POST", .01, False)
        assert service.network.snapshot().state == "PAUSED"
        wait_rounds(service, count=8)
        health = service.network.snapshot()
        assert health.state == "PROBE" and health.allowed
        assert bad in service.latest().pending_ids
        assert sum(path.endswith("/"+bad) for path, _ in venue.calls) == 1
    finally:
        assert service.stop()


def test_proven_rejection_retires_work_and_positive_late_order_requeues(sync_env):
    venue = sync_env[0]
    bad = "missing-signed-id"
    venue.hook = lambda r: httpx.Response(200, json=None) if (
        r.url.path.endswith("/"+bad) and bad not in venue.orders) else None
    service, _ = make_service(sync_env, SyncSettings(.03, .05))
    service.track_orders({bad: time.monotonic()})
    service.start()
    try:
        wait_rounds(service, count=2)
        service.retire_rejected_orders({bad})
        wait_rounds(service, count=3)
        assert bad not in service.latest().pending_ids
        assert bad not in service._detail_requirements
        before = sum(path.endswith("/"+bad) for path, _ in venue.calls)
        wait_rounds(service, count=3)
        assert sum(path.endswith("/"+bad) for path, _ in venue.calls) == before
        venue.orders[bad] = order(bad)
        venue.opens.append(order(bad))
        wait_rounds(service, count=3)
        assert bad in service.latest().pending_ids
        assert bad not in service._retired_rejections
        assert next(r for r in service.latest().last_good if r.name == "order:"+bad).complete
    finally:
        assert service.stop()


async def test_live_canceled_sl_proves_and_retires_before_final_close(env, monkeypatch, tmp_path):
    setup, controller = await setup_policy(env, monkeypatch)
    v, e, _, lifecycle, _, wire, _, pump, _ = setup
    force_sl(controller)
    official, anchor = e.official_time, e.time_anchor
    state = {}

    def reads(request):
        if request.url.path == "/time":
            return httpx.Response(200, json=official+time.monotonic()-anchor)
        if state.get("bad") and request.url.path.endswith("/"+state["bad"]):
            return httpx.Response(200, json=None)

    companion, calls = await attach(setup, tmp_path, hook=reads)
    e.shared_reads.timeout_s = e.cfg.risk.ws_stale_halt_s

    def writes(request):
        raw = wire(request)
        if request.method == "POST":
            oid = e.gateway._client.batch_attempts[0].expected_order_id
            if len(v.post_requests) == 2:
                state["bad"] = oid
                v.rows.pop(oid)
                return httpx.Response(200, json=[canceled_reply(oid)], request=request)
            trade = v.fill(oid, "replacement-fill", 5)
            trade["trader_side"] = "MAKER"
            trade["maker_orders"][0]["maker_address"] = ADDRESS
            reflect_positions(v)
        return raw

    client = boundary(monkeypatch, v, writes)
    native_close = e._verify_close
    close_checks = []

    async def checked_close(*args, **kwargs):
        close_checks.append(state.get("bad") in e.canceled_submission_proofs)
        return await native_close(*args, **kwargs)

    monkeypatch.setattr(e, "_verify_close", checked_close)

    async def price_change():
        await until(lambda: state.get("bad") is not None)
        pump.update(bid=".40", yes_ask=".42", no_bid=".58", no_ask=".60")

    change = asyncio.create_task(price_change())
    try:
        began = time.monotonic()
        result, _ = await asyncio.wait_for(
            controller.repair(*e.economic_inventory(), "live-canceled-sl"), 4)
        await change
        assert time.monotonic()-began < 4
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert close_checks and all(close_checks)  # Proven inside actor, not final fallback.
        assert e.economic_inventory() == (Decimal(5), Decimal(5))
        assert len(v.post_requests) == 3
        bad = state["bad"]
        assert bad in companion.service._retired_rejections
        assert bad not in companion.service._detail_requirements
        assert sum(bad in ids for ids in v.cancels) <= 2
        await e.journal.flush()
        records = journal_records(e.journal.path)
        proof = next(r["data"]["payload"] for r in records
                     if r["kind"] == "sm_sl_canceled_submission_proven")
        assert all(r["started_monotonic"] >= proof["operation_after_monotonic"]
                   for r in proof["sources"].values())
        before = sum(path.endswith("/"+bad) for _, path, _ in calls)
        await asyncio.sleep(.12)
        assert sum(path.endswith("/"+bad) for _, path, _ in calls) <= before+1
        assert not e.fatal and not e.shared_reads.inflight
    finally:
        await asyncio.gather(change, return_exceptions=True)
        client.close()
        await finish(setup, companion)


@pytest.mark.parametrize("case", ["valid", "stale", "failed_page", "wrong_id", "not_found", "fill", "quarantine"])
async def test_canceled_sl_proof_requires_exact_response_and_fresh_complete_pages(
        env, monkeypatch, tmp_path, case):
    setup, controller = await setup_policy(env, monkeypatch)
    e = setup[1]
    companion, _ = await attach(setup, tmp_path)
    actor = SLExecution(controller, "strict-proof")
    goal, _ = actor.goal()
    oid = "0x"+"ed"*32
    quote = WindowQuote(e.meta.condition_id, e.meta.no.token_id, "NO", Side.BUY,
                        float(goal[1]), 5, "strict-proof")
    a = Placement("proof-candidate", quote, "UNKNOWN", oid, True,
                  "test-hash", [canceled_reply(oid)], oid)
    e.attempts.append(a)
    e.known.add(oid)
    e.gateway._client.known_ids.add(oid)
    e.processor.register_order(oid, e.meta.condition_id, quote.token_id,
                                Side.BUY, quote.size, quote.price, "rdp_sm_sl", quiet=True)
    try:
        snap = companion.service.latest()
        boundary = 0
        if case == "stale":
            boundary = time.monotonic()+1
        elif case == "failed_page":
            snap = replace(snap, attempts=tuple(replace(r, complete=False, data_json=None)
                           if r.name == "trades" else r for r in snap.attempts))
        elif case == "wrong_id":
            a.raw_response = [canceled_reply("0x"+"ef"*32)]
        elif case == "not_found":
            a.raw_response = [{"errorMsg": "order can't be found - already canceled or matched"}]
        elif case == "fill":
            from polymaker.domain import TradeState

            e.processor.on_trade(TradeEvent(quote.token_id, Side.BUY, quote.price, 1,
                "contradiction", TradeState.MATCHED, 0, oid, e.meta.condition_id), e.meta.condition_id)
        elif case == "quarantine":
            e.processor.quarantine_order(oid, "TEST_CONFLICT", notify=False)
        proven = await e.shared_reads.prove_sl_canceled_submission(a, snap, boundary)
        assert proven is (case == "valid")
        assert (oid in e.canceled_submission_proofs) is proven
        assert a.outcome == ("REJECTED" if proven else "UNKNOWN")
        if not proven:
            from polymaker.rdp_sm_sl_ledger import exposure

            assert exposure(e).reserved >= Decimal(str(quote.price))*5
    finally:
        await finish(setup, companion)


async def test_late_open_after_proven_sl_rejection_restores_full_reservation(env, monkeypatch, tmp_path):
    setup, controller = await setup_policy(env, monkeypatch)
    v, e = setup[:2]
    companion, _ = await attach(setup, tmp_path)
    actor = SLExecution(controller, "late-rejection")
    goal, _ = actor.goal()
    oid = "0x"+"ed"*32
    quote = WindowQuote(e.meta.condition_id, e.meta.no.token_id, "NO", Side.BUY,
                        float(goal[1]), 5, "late-rejection")
    a = Placement("proof-candidate", quote, "UNKNOWN", oid, True,
                  "test-hash", [canceled_reply(oid)], oid)
    e.attempts.append(a)
    e.known.add(oid)
    e.gateway._client.known_ids.add(oid)
    e.processor.register_order(oid, e.meta.condition_id, quote.token_id,
                                Side.BUY, quote.size, quote.price, "rdp_sm_sl", quiet=True)
    try:
        assert await e.shared_reads.prove_sl_canceled_submission(a, companion.service.latest(), 0)
        assert a.outcome == "REJECTED"
        v.rows[oid] = {"id": oid, "asset_id": quote.token_id, "market": e.meta.condition_id,
                       "side": "BUY", "price": str(quote.price), "original_size": "5",
                       "size_matched": "0", "status": "LIVE", "associate_trades": []}
        await until(lambda: any(r.name == "open_orders" and r.complete and any(
            row["order_id"] == oid for row in r.data) for r in companion.service.latest().attempts))
        with pytest.raises(ValueError, match="CANCELED_SUBMISSION_EVIDENCE_CHANGED"):
            await e.shared_reads.observe_sl(actor)
        assert oid not in e.canceled_submission_proofs and oid in e.known
        assert a.outcome == "UNKNOWN"
        assert actor.admission_reason(goal) == "CANCELED_SUBMISSION_EVIDENCE_CHANGED"
        from polymaker.rdp_sm_sl_ledger import exposure

        assert exposure(e).reserved == Decimal(str(quote.price))*5
    finally:
        await finish(setup, companion)


@pytest.mark.parametrize("failure,reason", [
    ("network", "NETWORK_PAUSED"),
    ("capital", "WAIT_CAPITAL_CAPACITY"),
    ("delta", "WAIT_DELTA_CAPACITY"),
    ("pagination", "TRADES_PAGINATION_INCOMPLETE"),
    ("clock", "OFFICIAL_CLOCK_STALE"),
])
async def test_sl_wait_uses_actual_admission_reason_and_records_target(env, monkeypatch, failure, reason):
    setup, controller = await setup_policy(env, monkeypatch)
    e = setup[1]
    actor = SLExecution(controller, "wait-reason")
    try:
        goal, _ = actor.goal()
        assert goal is not None and actor.allowed(goal)
        if failure == "network":
            e.reads.network.observe("POST", .001, False)
        elif failure == "capital":
            actor.capital = Decimal(0)
        elif failure == "delta":
            for i in range(2):
                e.attempts.append(Placement(str(i), WindowQuote(e.meta.condition_id,
                    e.meta.no.token_id, "NO", Side.BUY, float(goal[1]), 5, f"pending-{i}")))
        elif failure == "pagination":
            e.gateway._client._pagination_failures[("get_trades", ())] = None
        else:
            e.time_anchor -= e.cfg.risk.ws_stale_halt_s+1
        actual = actor.admission_reason(goal)
        assert actual == reason and not actor.allowed(goal)
        actor.wait(actual, goal)
        await e.journal.flush()
        row = next(r["data"]["payload"] for r in journal_records(e.journal.path)
                   if r["kind"] == "sm_sl_wait")
        assert row["reason"] == reason
        assert row["target"] == {"side": goal[0], "price": str(goal[1]),
                                 "quantity": str(goal[2]), "generation": goal[3].generation}
    finally:
        if failure == "delta":
            e.attempts[:] = e.attempts[:-2]  # Candidates were never submitted.
        e.gateway._client._pagination_failures.clear()
        await cleanup(setup)
