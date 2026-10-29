"""Observed Already canceled POST reply, using the real SDK and close barrier."""

import asyncio
import threading
from dataclasses import replace
from decimal import Decimal

import httpx
import pytest

from polymaker.domain import Side, TradeState
from polymaker.rdp_sm_entry import AccountRiskPolicy
from polymaker.rdp_sm_execution import Placement, WindowQuote, _canceled_submission_response
from polymaker.rdp_sm_rebalance import RebalanceController
from polymaker.rdp_sm_recovery import retry_read
from polymaker.state.tracker import TradeEvent
from test_rdp_sm_entry import boundary
from test_rdp_sm_m1 import CID
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_obi_execution import cleanup, setup_obi
from test_rdp_sm_obi_execution import env as env


def canceled_reply(oid):
    return {
        "errorMsg": f"order {oid} is invalid. Already canceled.",
        "orderID": "", "takingAmount": "", "makingAmount": "", "status": "", "success": True,
    }


@pytest.mark.parametrize("changed", [
    {}, {"orderID": "accepted"}, {"status": "live"}, {"makingAmount": "1"},
    {"takingAmount": "5"}, {"tradeIDs": ["trade"]}, {"tradeIds": ["trade"]},
    {"transactionsHashes": ["tx"]}, {"success": False},
    {"errorMsg": "order can't be found - already canceled or matched"},
    {"errorMsg": "order 0x" + "cd" * 32 + " is invalid. Already canceled."},
    {"errorMsg": "prefix order 0x" + "ab" * 32 + " is invalid. Already canceled."},
    {"status": None},
])
def test_canceled_reply_requires_full_shape_and_exact_signed_identity(changed):
    oid = "0x" + "ab" * 32
    reply = canceled_reply(oid) | changed
    attempt = Placement("a", WindowQuote(CID, "1", "YES", Side.BUY, .78, 5, "w"),
                        "UNKNOWN", oid, True, "hash", [reply], oid)
    assert _canceled_submission_response(attempt) is (not changed)
    assert not _canceled_submission_response(replace(attempt, post_started=False))
    assert not _canceled_submission_response(replace(attempt, expected_order_id=None))
    assert not _canceled_submission_response(replace(attempt, raw_response=[reply, reply]))


def inject_reply(setup, monkeypatch, *, quantity=0, change=None, failed_read=None):
    """Only replace wire replies; real gateway, SDK, journal and tracker run."""
    v, e, _, _, _, wire, *_ = setup
    state = {"reply": True, "failed_read": failed_read}
    native = type(wire).__call__

    def respond(request):
        if request.method == "POST" and state["reply"]:
            native(wire, request)
            attempt = e.gateway._client.batch_attempts[0]
            oid = attempt.expected_order_id
            if quantity:
                v.fill(oid, "actual-fill", quantity)
            else:
                v.rows.pop(oid)
            reply = canceled_reply(oid)
            if change:
                reply.update(change)
            state["reply"] = False
            return httpx.Response(200, json=[reply], request=request)
        if not state["reply"] and request.method == "GET":
            if request.url.path in {"/data/orders", "/data/trades"}:
                if request.url.path == state["failed_read"]:
                    return httpx.Response(503, json={"error": "unavailable"}, request=request)
                if state["failed_read"] == "pagination" and request.url.path == "/data/trades":
                    return httpx.Response(200, json={"data": [], "next_cursor": "again"}, request=request)
            if request.url.path.startswith("/data/order/"):
                oid = request.url.path.rsplit("/", 1)[-1]
                if oid not in v.rows:
                    v.calls.append((request.url.host, request.url.path, dict(request.url.params)))
                    return httpx.Response(200, json=None, request=request)
        return native(wire, request)

    return state, boundary(monkeypatch, v, respond)


@pytest.mark.parametrize("direction", ["YES", "NO"])
async def test_inflight_reversal_canceled_reply_releases_risk_and_allows_next_episode(
    env, monkeypatch, direction,
):
    setup = await setup_obi(env, monkeypatch, horizon=.5)
    v, e, _, lifecycle, normal, wire, _, pump, _ = setup
    _, client = inject_reply(setup, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    wire.delay = lambda: (entered.set(), release.wait(3))
    task = None
    try:
        pump["direction"] = direction
        await asyncio.sleep(.025)
        task = asyncio.create_task(normal.enter(Decimal(0), Decimal(0), "cancel-before-post"))
        assert await asyncio.to_thread(entered.wait, 2)
        pump["direction"] = opposite = "NO" if direction == "YES" else "YES"
        for _ in range(100):
            if v.cancels:
                break
            await asyncio.sleep(.005)
        assert v.cancels and not v.rows
        release.set()
        result, _ = await task
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert result.placement_results[0]["outcome"] == "REJECTED"
        oid = result.placement_results[0]["expected_order_id"]
        assert oid in e.canceled_submission_proofs and oid not in e.known
        assert oid in e.gateway._client.known_ids  # Still observed after rejection.
        assert e.processor.owned_record(oid) is not None
        assert e.committed() == 0 and not e.confirmed()
        assert not any(path == f"/data/order/{oid}" for _, path, _ in v.calls)
        records = journal_records(e.journal.path)
        closing = next(r["data"]["payload"] for r in records if r["kind"] == "sm_obi_close_requested")
        assert closing["reason"] == "SIGNAL_REVERSED"
        assert len(v.post_requests) == 1
        wire.delay = None
        for _ in range(3):
            await e.reads.server_time()
        lifecycle.account = (await e._snapshot(include_books=False))[3]
        assert e.safe_to_continue
        next_result, _ = await normal.enter(Decimal(0), Decimal(0), "next-episode")
        assert next_result.close_barrier == "SAFE_TO_CONTINUE"
        assert next_result.requested_quotes[0].outcome == opposite
        assert len(v.post_requests) == 2
    finally:
        release.set()
        if task:
            await task
        await cleanup(setup)
        client.close()


@pytest.mark.parametrize("failed_read", ["/data/orders", "/data/trades", "pagination"])
async def test_canceled_reply_incomplete_proof_retains_risk_until_successful_retry(
    env, monkeypatch, failed_read,
):
    # Exercise rejected-order proof, rather than expiry during 120ms setup.
    setup = await setup_obi(env, monkeypatch, horizon=.5)
    v, e, _, _, normal, *_ = setup
    state, client = inject_reply(setup, monkeypatch, failed_read=failed_read)
    try:
        result, _ = await normal.enter(Decimal(0), Decimal(0), "incomplete-proof")
        assert len(v.post_requests) == 1
        assert result.close_barrier == "BLOCKED"
        assert e.attempts[-1].outcome == "UNKNOWN" and e.committed() == Decimal("2.5")
        assert not e.canceled_submission_proofs
        state["failed_read"] = None
        assert await e.verify_close(cancel=True), e.reason
        assert e.attempts[-1].outcome == "REJECTED" and e.committed() == 0
        assert len(v.post_requests) == 1
    finally:
        await cleanup(setup)
        client.close()


@pytest.mark.parametrize("quantity", [2, 5])
async def test_canceled_text_never_hides_real_partial_or_full_fill(env, monkeypatch, quantity):
    setup = await setup_obi(env, monkeypatch)
    v, e, _, _, normal, *_ = setup
    _, client = inject_reply(setup, monkeypatch, quantity=quantity)
    try:
        result, _ = await normal.enter(Decimal(0), Decimal(0), "actually-filled")
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert e.attempts[-1].outcome == "VERIFIED"
        assert sum(f.size for f in e.confirmed()) == quantity
        assert e.committed() == Decimal(str(quantity)) * Decimal(".5")
        assert not e.canceled_submission_proofs and len(v.post_requests) == 1
    finally:
        await cleanup(setup)
        client.close()


async def test_late_ws_fill_revokes_rejection_and_restores_maximum_risk(env, monkeypatch):
    setup = await setup_obi(env, monkeypatch)
    _, e, _, _, normal, *_ = setup
    _, client = inject_reply(setup, monkeypatch)
    try:
        result, _ = await normal.enter(Decimal(0), Decimal(0), "late-fill")
        assert result.close_barrier == "SAFE_TO_CONTINUE"
        a = e.attempts[-1]
        e.processor.on_trade(TradeEvent(a.quote.token_id, Side.BUY, a.quote.price, 2,
                                      "late-fill", TradeState.CONFIRMED, 0, a.order_id, CID), CID)
        assert not e.safe_to_continue and not e.canceled_submission_proofs
        assert a.outcome == "UNKNOWN" and a.order_id in e.known
        assert e.committed() == a.maximum
        assert ("late-fill", a.order_id) in e.processor.fill_records
        assert not await e.verify_close(cancel=True)
    finally:
        await cleanup(setup)
        client.close()


@pytest.mark.parametrize("entry", ["close", "snapshot"])
async def test_late_rest_fill_reconciles_instead_of_reusing_rejection(env, monkeypatch, entry):
    setup = await setup_obi(env, monkeypatch)
    v, e, _, _, normal, *_ = setup
    _, client = inject_reply(setup, monkeypatch)
    try:
        result, _ = await normal.enter(Decimal(0), Decimal(0), "late-rest-fill")
        assert result.close_barrier == "SAFE_TO_CONTINUE"
        a = e.attempts[-1]
        v.rows[a.order_id] = {
            "id": a.order_id, "asset_id": a.quote.token_id, "market": CID, "side": "BUY",
            "price": str(a.quote.price), "original_size": "5", "size_matched": "0",
            "status": "CANCELED", "associate_trades": [],
        }
        v.fill(a.order_id, "late-rest-fill", 2)
        if entry == "snapshot":
            with pytest.raises(ValueError, match="CLOSE_EVIDENCE_INVALIDATED") as failure:
                await e._snapshot(include_books=False)
            assert retry_read(failure.value)  # Main loop retries the complete close first.
            assert not e.safe_to_continue and a.outcome == "UNKNOWN"
            assert e.committed() == a.maximum
        assert await e.verify_close(), e.reason
        assert a.outcome == "VERIFIED" and not e.canceled_submission_proofs
        assert sum(f.size for f in e.confirmed()) == 2
        assert e.committed() == Decimal("1")
        assert len(v.post_requests) == 1
    finally:
        await cleanup(setup)
        client.close()


@pytest.mark.parametrize("failed_read", ["/data/orders", "/data/trades", "pagination"])
async def test_rejection_is_not_reused_when_next_snapshot_cannot_revalidate(
    env, monkeypatch, failed_read,
):
    setup = await setup_obi(env, monkeypatch)
    v, e, _, _, normal, *_ = setup
    state, client = inject_reply(setup, monkeypatch)
    try:
        result, _ = await normal.enter(Decimal(0), Decimal(0), "recheck-failure")
        assert result.close_barrier == "SAFE_TO_CONTINUE"
        state["failed_read"] = failed_read
        with pytest.raises(ValueError):
            await e._snapshot(include_books=False)
        assert not e.safe_to_continue and e.attempts[-1].outcome == "UNKNOWN"
        assert e.committed() == Decimal("2.5") and len(v.post_requests) == 1
    finally:
        await cleanup(setup)
        client.close()


async def add_closed_history(setup, *, count=12, rejected=1):
    """Seed terminal identities, then prove them through real SDK/FillTruth reads."""
    v, e, *_ = setup
    first = e.attempts[0]
    for index in range(count + rejected - 1):
        oid = "0x" + f"{index + 1:064x}"
        quote = replace(first.quote, window_id=f"history-{index}", token_id="1", outcome="YES", price=.5)
        rejection = index >= count
        e.attempts.append(Placement(
            f"history-{index}", quote, "UNKNOWN" if rejection else "ACK", oid, True,
            "history", [canceled_reply(oid)] if rejection else [{"success": True, "orderID": oid}], oid,
        ))
        e.known.add(oid)
        e.gateway._client.known_ids.add(oid)
        e.processor.register_order(oid, CID, "1", Side.BUY, 5, .5, "history", quiet=True)
        if not rejection:
            v.rows[oid] = {
                "id": oid, "asset_id": "1", "market": CID, "side": "BUY", "price": ".5",
                "original_size": "5", "size_matched": "0", "status": "CANCELED", "associate_trades": [],
            }
    assert await e.verify_close(), e.reason
    assert len(e.known) == count and len(e.canceled_submission_proofs) == rejected


@pytest.mark.parametrize("rejected", [1, 3])
@pytest.mark.parametrize("account_policy", [False, True])
async def test_many_closed_orders_and_slow_reads_allow_fresh_snapshot_and_repair(
    env, monkeypatch, rejected, account_policy,
):
    setup = await setup_obi(env, monkeypatch)
    v, e, _, lifecycle, normal, wire, _, _, _ = setup
    _, reply_client = inject_reply(setup, monkeypatch)
    latency_client, warmer = None, None
    advance, reads = [0.0], []
    native_clock = e.monotonic
    try:
        result, _ = await normal.enter(Decimal(0), Decimal(0), "rejected-first")
        assert result.close_barrier == "SAFE_TO_CONTINUE"
        await add_closed_history(setup, rejected=rejected)
        filled_oid = min(e.known)
        v.fill(filled_oid, "history-fill", 5)
        v.positions = [{"token_id": "1", "current_size": 5, "avg_price": .5}]
        assert await e.verify_close(), e.reason
        if account_policy:
            policy = AccountRiskPolicy(use_starting_balance=True)
            e.reads.account_policy = e.gateway._client.account_policy = policy

        def latency(request):
            if request.method == "GET":
                reads.append(request.url.path)
                advance[0] += .25
            return wire(request)

        latency_client = boundary(monkeypatch, v, latency)
        e.monotonic = lambda: native_clock() + advance[0]
        # The old snapshot's full-close prefix alone exceeds its 10 s cap.
        began = e.monotonic()
        assert await e._verify_close(), e.reason
        assert e.monotonic() - began > e.cfg.risk.ws_stale_halt_s
        assert sum(path.startswith("/data/order/") for path in reads) >= 36
        for _ in range(2):
            reads.clear()
            began = e.monotonic()
            lifecycle.account = (await e._snapshot(include_books=False))[3]
            assert e.monotonic() - began < e.cfg.risk.ws_stale_halt_s
            assert not any(path.startswith("/data/order/") for path in reads)
            # One shared rejection read, plus the unchanged account snapshot.
            assert reads.count("/data/orders") == 3
            assert reads.count("/data/trades") == 2
            assert e.safe_to_continue and len(e.canceled_submission_proofs) == rejected
            assert e.committed() == Decimal("2.5")

        e.time_anchor -= advance[0]
        e.monotonic = native_clock
        # Fresh snapshots must actually permit the missing-side order.
        async def warm():
            while True:
                await e.reads.server_time()
                await asyncio.sleep(.2)

        latency_client.close()
        latency_client = boundary(monkeypatch, v, wire)
        warmer = asyncio.create_task(warm())
        await asyncio.sleep(.025)
        repaired, _ = await RebalanceController(e, lifecycle).repair(
            Decimal(5), Decimal(0), "repair-after-rejection",
        )
        assert repaired.close_barrier == "SAFE_TO_CONTINUE", repaired.block_reason
        assert repaired.requested_quotes[0].outcome == "NO"
        assert len(v.post_requests) == 2
    finally:
        e.monotonic = native_clock
        if warmer:
            warmer.cancel()
            await asyncio.gather(warmer, return_exceptions=True)
        await cleanup(setup)
        reply_client.close()
        if latency_client:
            latency_client.close()


@pytest.mark.parametrize("target", ["rejected", "history"])
async def test_snapshot_rejection_refresh_cannot_bless_concurrent_ws_evidence(
    env, monkeypatch, target,
):
    setup = await setup_obi(env, monkeypatch)
    _, e, _, _, normal, *_ = setup
    _, client = inject_reply(setup, monkeypatch)
    try:
        await normal.enter(Decimal(0), Decimal(0), "recheck-ws")
        await add_closed_history(setup, count=1)
        oid = e.attempts[0].order_id if target == "rejected" else min(e.known)
        quote = next(a.quote for a in e.attempts if a.order_id == oid)
        original = e.gateway.get_trades

        async def concurrent_trade(**kwargs):
            rows = await original(**kwargs)
            e.processor.on_trade(TradeEvent(quote.token_id, Side.BUY, quote.price, 2,
                                          "concurrent", TradeState.CONFIRMED, 0, oid, CID), CID)
            return rows

        monkeypatch.setattr(e.gateway, "get_trades", concurrent_trade)
        with pytest.raises(ValueError, match="EVENT_DURING_CLOSE_REQUIRES_RECHECK") as failure:
            await e._snapshot(include_books=False)
        assert retry_read(failure.value)
        assert not e.safe_to_continue
        assert ("concurrent", oid) in e.processor.fill_records
        if target == "rejected":
            assert e.attempts[0].outcome == "UNKNOWN" and oid not in e.canceled_submission_proofs
        assert len(setup[0].post_requests) == 1
    finally:
        await cleanup(setup)
        client.close()


async def test_snapshot_refresh_cannot_repair_an_unrelated_invalid_close_proof(env, monkeypatch):
    setup = await setup_obi(env, monkeypatch)
    v, e, _, _, normal, *_ = setup
    _, client = inject_reply(setup, monkeypatch)
    try:
        await normal.enter(Decimal(0), Decimal(0), "invalid-old-proof")
        await add_closed_history(setup, count=1)
        e.invalidate("ORDER_EVIDENCE_CHANGED", min(e.known))
        with pytest.raises(ValueError, match="ORDER_EVIDENCE_CHANGED"):
            await e._snapshot(include_books=False)
        assert not e.safe_to_continue and len(v.post_requests) == 1
        assert await e.verify_close(), e.reason
        await e._snapshot(include_books=False)
        assert e.safe_to_continue
    finally:
        await cleanup(setup)
        client.close()


async def test_rejection_refresh_keeps_account_snapshot_freshness_limit(env, monkeypatch):
    setup = await setup_obi(env, monkeypatch)
    _, e, _, _, normal, *_ = setup
    _, client = inject_reply(setup, monkeypatch)
    advance = [0]
    native_clock = e.monotonic
    try:
        await normal.enter(Decimal(0), Decimal(0), "slow-account")
        original = e.reads.market

        async def slow_market(slug):
            result = await original(slug)
            advance[0] += 11
            return result

        e.monotonic = lambda: native_clock() + advance[0]
        monkeypatch.setattr(e.reads, "market", slow_market)
        with pytest.raises(ValueError, match="EXECUTION_DATA_STALE"):
            await e._snapshot(include_books=False)
    finally:
        e.monotonic = native_clock
        await cleanup(setup)
        client.close()


async def test_reused_signed_identity_cannot_be_closed_as_canceled_rejection(env, monkeypatch):
    setup = await setup_obi(env, monkeypatch)
    _, e, _, _, normal, *_ = setup
    _, client = inject_reply(setup, monkeypatch)
    try:
        result, _ = await normal.enter(Decimal(0), Decimal(0), "reused-id")
        assert result.close_barrier == "SAFE_TO_CONTINUE"
        e.attempts.append(replace(e.attempts[-1], attempt_id="duplicate"))
        assert not await e.verify_close()
        assert "CANCELED_SUBMISSION_ID_REUSED" in e.reason
        assert not e.safe_to_continue and e.committed() > 0
    finally:
        await cleanup(setup)
        client.close()


async def test_changed_reply_cannot_reuse_an_earlier_rejection_proof(env, monkeypatch):
    setup = await setup_obi(env, monkeypatch)
    _, e, _, _, normal, *_ = setup
    _, client = inject_reply(setup, monkeypatch)
    try:
        result, _ = await normal.enter(Decimal(0), Decimal(0), "changed-reply")
        assert result.close_barrier == "SAFE_TO_CONTINUE"
        e.attempts[-1].raw_response[0]["status"] = "live"
        assert not await e.verify_close()
        assert "CANCELED_SUBMISSION_RESPONSE_CHANGED" in e.reason
        assert not e.safe_to_continue and e.committed() == Decimal("2.5")
    finally:
        await cleanup(setup)
        client.close()
