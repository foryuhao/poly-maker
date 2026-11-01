"""Real M2/Gateway/SDK/tracker, injected official HTTP/socket/disk boundaries."""

import asyncio
import hashlib
import json
import threading
import time
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from eth_abi import encode
from eth_utils import keccak

from polymaker.rdp_sm_lifecycle import BatchLifecycle
from polymaker.rdp_sm_marketdata import SessionFeed
from polymaker.rdp_sm_network import NetworkHealth
from polymaker.rdp_sm_normal import NormalController
from polymaker.rdp_sm_writer import AsyncRiskWriter
from test_rdp_sm_entry import boundary
from test_rdp_sm_m1 import CID
from test_rdp_sm_m2_execution import env as old_env
from test_rdp_sm_m2_execution import journal_records, open_executor


@pytest.fixture
def env(monkeypatch, tmp_path):
    yield from old_env.__wrapped__(monkeypatch, tmp_path)


class Wire:
    def __init__(self, venue, executor):
        self.v, self.e = venue, executor
        self.delay = None
        self.fail_ack = False
        self.reject_index = None
        self.cancel_failures = 0
        self.timeline = []

    def __call__(self, request):
        self.timeline.append((request.method, request.url.path, time.monotonic()))
        if request.method == "POST":
            body = json.loads(request.content)
            self.v.post_requests.append(body)
            # A concurrent diagnostic append may leave a partial final line.
            # Prepared WAL is already durable and newline-terminated before
            # native POST. Still parse/assert every complete record strictly.
            snapshot = Path(self.e.journal.path).read_bytes()
            records = [json.loads(line) for line in snapshot.splitlines(keepends=True)
                       if line.endswith(b"\n")]
            prepared = [r["data"]["payload"] for r in records if r["kind"] == "sm_prepared"]
            assert prepared
            candidates = [c for p in prepared for c in p["candidates"]]
            assert any(
                c["body_hash"] == hashlib.sha256(request.content).hexdigest() for c in candidates
            )
            if self.delay:
                self.delay()
            replies = []
            for i, a in enumerate(self.e.gateway._client.batch_attempts):
                if self.reject_index == i:
                    replies.append({"success": False, "errorMsg": "post only"})
                    continue
                q, oid = a.quote, a.expected_order_id
                self.v.rows[oid] = {
                    "id": oid,
                    "asset_id": q.token_id,
                    "market": CID,
                    "side": "BUY",
                    "price": str(q.price),
                    "original_size": str(q.size),
                    "size_matched": "0",
                    "status": "LIVE",
                    "associate_trades": [],
                }
                replies.append({"success": True, "orderID": oid})
            if self.fail_ack:
                raise httpx.ReadTimeout("lost ACK after venue acceptance")
            return httpx.Response(200, json=replies, request=request)
        if request.method == "DELETE":
            ids = json.loads(request.content)
            self.v.cancels.append(ids)
            canceled, failed = [], {}
            for oid in ids:
                if self.cancel_failures:
                    failed[oid] = "temporary cancellation failure"
                elif oid in self.v.rows:
                    self.v.rows[oid]["status"] = "CANCELED"
                    canceled.append(oid)
                else:
                    failed[oid] = "not found"
            self.cancel_failures = max(0, self.cancel_failures - 1)
            if self.v.on_cancel:
                self.v.on_cancel(self.v)
            return httpx.Response(
                200, json={"canceled": canceled, "not_canceled": failed}, request=request
            )
        if request.url.path == "/book":
            raise AssertionError("OBI trading read REST book")
        return None


async def setup_obi(env, monkeypatch, horizon=0.12, *, warm_sigma=True):
    v, e = await open_executor(env)
    e.monotonic, e.sleep = time.monotonic, asyncio.sleep
    await e._snapshot(include_books=False)
    cfg = e.cfg.rdp_sm
    cfg.normal_entry_mode = "obi_v1"
    cfg.obi.signal_horizon_sec = horizon
    cfg.obi.max_signal_age_ms = min(100, horizon * 500)
    cfg.obi.cancel_retry_sec = 0.02
    e.journal = AsyncRiskWriter(e.journal)
    network = NetworkHealth(cfg.obi)
    for _ in range(10):
        network.observe("GET", 0.001)
    e.reads.network, e.gateway._client.network = network, network
    anchor = time.monotonic()
    native_server_time = e.reads.server_time

    async def advancing_server_time():
        # A frozen START+60 response moves the confirmation band's start to
        # every account read. Match the socket's advancing official clock.
        return await native_server_time() + time.monotonic() - anchor

    monkeypatch.setattr(e.reads, "server_time", advancing_server_time)
    feed = SessionFeed(e.meta, cfg.obi, wall=lambda: v.time + time.monotonic() - anchor)
    feed.verify_ticks(v.ticks)
    feed.connect()
    e.reads.feed = feed
    wire = Wire(v, e)
    client = boundary(monkeypatch, v, wire)
    pump_state = {"running": True, "direction": "YES", "bid": ".50", "count": 0}

    async def pump():
        while pump_state["running"]:
            pump_state["count"] += 1
            n = pump_state["count"] % 2
            direction = pump_state["direction"]
            rows = []
            for t in ("1", "2"):
                strong = (t == "1") == (direction == "YES")
                bid_q, ask_q = (
                    ((80 + n, 20 + n) if strong else (20 + n, 80 + n))
                    if direction != "NONE"
                    else (50 + n, 50 + n)
                )
                rows.append(
                    {
                        "event_type": "book",
                        "asset_id": t,
                        "market": CID,
                        "timestamp": str(feed.wall() * 1000),
                        "bids": [
                            {"price": pump_state["bid"] if t == "1" else pump_state.get("no_bid", ".49"), "size": str(bid_q)}
                        ],
                        "asks": [{"price": pump_state.get("yes_ask", ".52") if t == "1" else pump_state.get("no_ask", ".51"), "size": str(ask_q)}],
                    }
                )
            feed.apply(rows)
            await asyncio.sleep(0.01)

    pump_task = asyncio.create_task(pump())
    # Reach the existing late-band three distinct confirmations before cases
    # that exercise execution, rather than racing a 10ms socket pump.
    await asyncio.sleep(0.04)
    if warm_sigma:
        # Existing execution/guard cases start after NORMAL's sigma warmup.
        # Keep the separate readiness test able to exercise a cold feed.
        now, wall = feed.monotonic(), feed.wall()
        feed.probability_points.extendleft(reversed([
            (int(wall)-age, now-age, .51, feed.generation) for age in range(11, 0, -1)
        ]))
    lifecycle = BatchLifecycle(e)
    lifecycle.account = (await e._snapshot(include_books=False))[3]
    normal = NormalController(e, lifecycle)
    return v, e, feed, lifecycle, normal, wire, client, pump_state, pump_task


async def cleanup(setup):
    _, e, feed, _, _, _, client, state, task = setup
    state["running"] = False
    await task
    await feed.close()
    await e.journal.flush()
    client.close()


async def test_preparation_tick_replica_conflict_waits_and_recovers(env, monkeypatch):
    setup = await setup_obi(env, monkeypatch)
    v, e, feed, lifecycle, normal, *_ = setup
    native = feed.verify_ticks
    failures = []

    def conflict_once(ticks):
        if not failures:
            failures.append(True)
            raise ValueError('WS_OFFICIAL_TICK_CONFLICT')
        native(ticks)

    monkeypatch.setattr(feed, 'verify_ticks', conflict_once)
    try:
        assert await normal.enter(Decimal(0), Decimal(0), 'tick-retry') is None
        assert not v.post_requests and e.fatal is None
        outcome = await normal.enter(Decimal(0), Decimal(0), 'tick-retry')
        assert outcome and outcome[0].close_barrier == 'SAFE_TO_CONTINUE'
        assert len(v.post_requests) == 1
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("direction,token,price", [("YES", "1", .5), ("NO", "2", .49)])
async def test_prepared_signal_order_deadline_and_one_shot_episode(
    env, monkeypatch, direction, token, price
):
    setup = await setup_obi(env, monkeypatch, horizon=2)
    v, e, feed, lifecycle, normal, wire, _, state, _ = setup
    try:
        state["direction"] = direction
        await asyncio.sleep(.025)
        outcome = await normal.enter(Decimal(0), Decimal(0), "obi-w1")
        result, sizing = outcome
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert result.fill_deadline_monotonic == result.t0_monotonic + 2
        assert len(v.post_requests) == 1 and len(v.post_requests[0]) == 1
        assert [(q.outcome, q.price) for q in result.requested_quotes] == [(direction, price)]
        assert v.post_requests[0][0]["order"]["tokenId"] == token
        opposite = "no" if direction == "YES" else "yes"
        assert Decimal(sizing[f"planned_{opposite}_size"]) == 0
        assert all(a["order_id"] == a["expected_order_id"] for a in result.placement_results)
        post = next(i for i, t in enumerate(wire.timeline) if t[0] == "POST")
        delete = next(i for i, t in enumerate(wire.timeline) if t[0] == "DELETE")
        assert all(t[0] != "GET" for t in wire.timeline[post + 1 : delete])
        # This isolated executor has no production background /time warmer.
        for _ in range(3):
            await e.reads.server_time()
        assert await normal.enter(Decimal(0), Decimal(0), "obi-w2") is None
        assert len(v.post_requests) == 1
        state["direction"] = "NONE"
        await asyncio.sleep(0.025)
        state["direction"] = direction
        await asyncio.sleep(0.025)
        third = await normal.enter(Decimal(0), Decimal(0), "obi-w3")
        assert third
        assert len(v.post_requests) == 2, (third[0].to_dict(), e.reads.network.snapshot())
    finally:
        await cleanup(setup)


async def test_true_inflight_delete_before_acceptance_and_late_ack_recancel(env, monkeypatch):
    setup = await setup_obi(env, monkeypatch)
    v, e, _, _, normal, wire, *_ = setup
    entered, release = threading.Event(), threading.Event()
    wire.delay = lambda: (entered.set(), release.wait(3))
    try:
        task = asyncio.create_task(normal.enter(Decimal(0), Decimal(0), "slow"))
        assert await asyncio.to_thread(entered.wait, 2)
        await asyncio.sleep(0.18)
        assert v.cancels and not v.rows  # DELETE preceded actual acceptance
        release.set()
        result, _ = await task
        assert all(row["status"] == "CANCELED" for row in v.rows.values())
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert len(v.post_requests) == 1 and len(v.cancels) >= 2
    finally:
        release.set()
        await cleanup(setup)


async def test_lost_ack_resolves_by_signed_identity_without_synthetic_ack(env, monkeypatch):
    setup = await setup_obi(env, monkeypatch)
    v, _, _, _, normal, wire, *_ = setup
    wire.fail_ack = True
    try:
        result, _ = await normal.enter(Decimal(0), Decimal(0), "lost")
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert {a["outcome"] for a in result.placement_results} == {"VERIFIED"}
        assert result.capital_committed == 0 and len(v.post_requests) == 1
    finally:
        await cleanup(setup)


async def test_signal_order_rejection_consumes_episode_without_opposite_order(env, monkeypatch):
    setup = await setup_obi(env, monkeypatch)
    v, _, _, _, normal, wire, *_ = setup
    wire.reject_index = 0
    try:
        result, _ = await normal.enter(Decimal(0), Decimal(0), "reject")
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert len(result.placement_results) == 1
        assert result.placement_results[0]["outcome"] == "REJECTED"
        assert result.requested_quotes[0].outcome == "YES" and not v.rows
        assert await normal.enter(Decimal(0), Decimal(0), "again") is None
        assert len(v.post_requests) == 1
    finally:
        await cleanup(setup)


async def test_http_200_cancel_failure_cannot_prove_close_and_can_retry(env, monkeypatch):
    setup = await setup_obi(env, monkeypatch)
    v, e, _, _, normal, wire, *_ = setup
    wire.cancel_failures = 99
    try:
        result, _ = await normal.enter(Decimal(0), Decimal(0), "cancel-fail")
        assert result.close_barrier == "BLOCKED" and result.capital_committed > 0
        for _ in range(3):
            await e.reads.server_time()
        assert e.reads.network.snapshot().active_batch == "cancel-fail"
        with pytest.raises(ValueError, match="PREVIOUS_WINDOW_UNRESOLVED"):
            await normal.enter(Decimal(0), Decimal(0), "cannot-bypass-close")
        assert len(v.post_requests) == 1
        wire.cancel_failures = 0
        assert await e.verify_close(cancel=True)
        assert e.reads.network.snapshot().active_batch is None
        assert e.committed() == 0 and len(v.post_requests) == 1
    finally:
        await cleanup(setup)


async def test_final_guard_rejects_price_change_during_order_bucket_wait(env, monkeypatch):
    setup = await setup_obi(env, monkeypatch)
    v, _, _, lifecycle, normal, _, _, state, _ = setup
    try:
        slot = await lifecycle.prepare(normal, Decimal(0), Decimal(0), "move")
        state["bid"] = ".51"
        assert await lifecycle.activate(normal, slot) is None
        assert not v.post_requests and not setup[2].episodes.consumed
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("direction", ["YES", "NO"])
async def test_neutral_and_same_direction_keep_original_deadline_without_requote(
    env, monkeypatch, direction
):
    setup = await setup_obi(env, monkeypatch, horizon=0.3)
    v, e, _, _, normal, wire, _, state, _ = setup
    try:
        state["direction"] = direction
        await asyncio.sleep(.025)
        task = asyncio.create_task(normal.enter(Decimal(0), Decimal(0), "soft-changes"))
        for _ in range(100):
            if v.post_requests:
                break
            await asyncio.sleep(0.005)
        assert len(v.post_requests) == 1
        for next_direction in ("NONE", direction):
            state["direction"] = next_direction
            await asyncio.sleep(0.025)
        assert len(v.post_requests) == 1 and not v.cancels
        result, _ = await task
        assert result.close_barrier == "SAFE_TO_CONTINUE"
        first_delete = next(t[2] for t in wire.timeline if t[0] == "DELETE")
        assert first_delete >= result.fill_deadline_monotonic
        changes = next(
            r["data"]["payload"]["changes"]
            for r in journal_records(e.journal.path)
            if r["kind"] == "sm_obi_signal_changes"
        )
        assert {"NONE", f"VALID_{direction}"} <= {row[3] for row in changes}
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("direction", ["YES", "NO"])
@pytest.mark.parametrize("inflight", [False, True])
async def test_valid_reversal_cancels_without_opposite_post_and_closes_late_acceptance(
    env, monkeypatch, direction, inflight
):
    setup = await setup_obi(env, monkeypatch, horizon=.7)
    v, e, _, _, normal, wire, _, state, _ = setup
    entered, release = threading.Event(), threading.Event()
    if inflight:
        wire.delay = lambda: (entered.set(), release.wait(3))
    task = None
    try:
        state["direction"] = direction
        await asyncio.sleep(.025)
        task = asyncio.create_task(normal.enter(Decimal(0), Decimal(0), "reversal"))
        for _ in range(200):
            if v.post_requests:
                break
            await asyncio.sleep(.005)
        assert len(v.post_requests) == 1
        if inflight:
            assert await asyncio.to_thread(entered.wait, 2)
        state["direction"] = opposite = "NO" if direction == "YES" else "YES"
        for _ in range(100):
            if v.cancels:
                break
            await asyncio.sleep(.005)
        assert v.cancels
        if inflight:
            assert not v.rows  # Cancellation also runs before a late acceptance.
        release.set()
        result, _ = await task
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert len(v.post_requests) == 1
        assert all(row["status"] == "CANCELED" for row in v.rows.values())
        assert result.fill_deadline_monotonic == result.t0_monotonic + .7
        first_delete = next(t[2] for t in wire.timeline if t[0] == "DELETE")
        assert first_delete < result.fill_deadline_monotonic
        closing = [r["data"]["payload"] for r in journal_records(e.journal.path)
                   if r["kind"] == "sm_obi_close_requested"]
        assert len(closing) == 1 and closing[0]["reason"] == "SIGNAL_REVERSED"
        assert closing[0]["original_direction"] == direction
        assert closing[0]["signal"]["label"] == f"VALID_{opposite}"
        assert 0 <= closing[0]["signal_age_ms"] <= 100
        assert closing[0]["snapshot"]["valid"]
    finally:
        release.set()
        if task:
            await task
        await cleanup(setup)


@pytest.mark.parametrize("direction", ["YES", "NO"])
async def test_quiet_stale_book_keeps_normal_order_until_original_deadline(
    env, monkeypatch, direction
):
    setup = await setup_obi(env, monkeypatch, horizon=.35)
    v, e, feed, _, normal, wire, _, state, pump = setup
    task = None
    try:
        state["direction"] = direction
        # NO needs three fresh directional samples; a fixed 25ms sleep can
        # deliver only two when the socket pump is scheduled late.
        for _ in range(200):
            if normal.select(feed.snapshot())[0] == direction:
                break
            await asyncio.sleep(.005)
        assert normal.select(feed.snapshot())[0] == direction
        task = asyncio.create_task(normal.enter(Decimal(0), Decimal(0), "quiet-book"))
        for _ in range(200):
            if v.post_requests:
                break
            await asyncio.sleep(.005)
        assert v.post_requests
        state["running"] = False
        await pump
        await asyncio.sleep(.14)
        assert feed.snapshot().reason == "WS_BOOK_STALE"
        assert not v.cancels
        result, _ = await task
        assert result.close_barrier == "SAFE_TO_CONTINUE"
        assert next(t[2] for t in wire.timeline if t[0] == "DELETE") >= result.fill_deadline_monotonic
        closing = [r["data"]["payload"] for r in journal_records(e.journal.path)
                   if r["kind"] == "sm_obi_close_requested"]
        assert len(closing) == 1 and closing[0]["reason"] == "ORDER_DEADLINE"
    finally:
        if task:
            await task
        await cleanup(setup)


@pytest.mark.parametrize("invalid", ["WS_BOOK_STALE", "SIGNAL_TOO_OLD"])
async def test_stale_opposite_evidence_does_not_count_as_reversal(env, monkeypatch, invalid):
    setup = await setup_obi(env, monkeypatch, horizon=.3)
    v, e, _, lifecycle, normal, wire, _, state, _ = setup
    task = None
    try:
        task = asyncio.create_task(normal.enter(Decimal(0), Decimal(0), "old-opposite"))
        for _ in range(200):
            if v.post_requests:
                break
            await asyncio.sleep(.005)
        assert v.post_requests
        original = lifecycle.snapshot

        def stale_opposite(*args):
            snapshot = original(*args)
            if invalid == "WS_BOOK_STALE":
                return replace(snapshot, valid=False, reason=invalid)
            return replace(snapshot, signal_event=time.monotonic() - .2)

        monkeypatch.setattr(lifecycle, "snapshot", stale_opposite)
        state["direction"] = "NO"
        await asyncio.sleep(.05)
        assert not v.cancels
        result, _ = await task
        assert result.close_barrier == "SAFE_TO_CONTINUE"
        assert next(t[2] for t in wire.timeline if t[0] == "DELETE") >= result.fill_deadline_monotonic
        closing = [r["data"]["payload"] for r in journal_records(e.journal.path)
                   if r["kind"] == "sm_obi_close_requested"]
        assert closing[0]["reason"] == "ORDER_DEADLINE"
    finally:
        if task:
            await task
        await cleanup(setup)


async def test_explicit_disconnect_still_cancels_normal_before_deadline(env, monkeypatch):
    setup = await setup_obi(env, monkeypatch, horizon=.7)
    v, e, feed, _, normal, wire, _, state, pump = setup
    task = None
    try:
        task = asyncio.create_task(normal.enter(Decimal(0), Decimal(0), "disconnect"))
        for _ in range(200):
            if v.post_requests:
                break
            await asyncio.sleep(.005)
        assert v.post_requests
        state["running"] = False
        await pump
        feed.invalidate("WS_DISCONNECTED")
        result, _ = await task
        assert result.close_barrier == "SAFE_TO_CONTINUE"
        assert next(t[2] for t in wire.timeline if t[0] == "DELETE") < result.fill_deadline_monotonic
        closing = [r["data"]["payload"] for r in journal_records(e.journal.path)
                   if r["kind"] == "sm_obi_close_requested"]
        assert closing[0]["reason"] == "WS_DISCONNECTED"
    finally:
        if task:
            await task
        await cleanup(setup)


@pytest.mark.parametrize("elapsed", [60, 200])
@pytest.mark.parametrize("direction,token,delta,repair_side", [
    ("YES", "1", 5, "NO"), ("NO", "2", -5, "YES"),
])
async def test_rebalance_has_independent_three_second_lease_without_obi(
    env, monkeypatch, elapsed, direction, token, delta, repair_side,
):
    from polymaker.rdp_sm_rebalance import RebalanceController
    from test_rdp_sm_m1 import START

    setup = await setup_obi(env, monkeypatch)
    v, e, feed, lifecycle, normal, wire, _, state, _ = setup
    filled = False

    def fill_once(venue):
        nonlocal filled
        if not filled:
            filled = True
            oid = next(oid for oid, row in venue.rows.items() if row["asset_id"] == token)
            venue.fill(oid, "confirmed-up", 5)

    v.on_cancel = fill_once
    warmer = None
    try:
        state["direction"] = direction
        await asyncio.sleep(.025)
        result, _ = await normal.enter(Decimal(0), Decimal(0), "normal-fill")
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert len(result.requested_quotes) == 1 and result.requested_quotes[0].outcome == direction
        assert result.delta == delta
        v.positions = [{"token_id": token, "current_size": 5,
                        "avg_price": result.requested_quotes[0].price}]
        v.time = START + elapsed
        feed.invalidate("TEST_CLOCK_ADVANCE")
        feed.connect()
        state["direction"] = "NONE"
        await asyncio.sleep(0.02)
        lifecycle.account = (await e._snapshot(include_books=False))[3]

        async def warm():
            while True:
                await e.reads.server_time()
                await asyncio.sleep(0.4)

        warmer = asyncio.create_task(warm())
        repair = RebalanceController(e, lifecycle)
        yes, no = (Decimal(5), Decimal(0)) if delta > 0 else (Decimal(0), Decimal(5))
        repaired, _ = await repair.repair(yes, no, "repair")
        assert repaired.close_barrier == "SAFE_TO_CONTINUE", repaired.block_reason
        assert len(v.post_requests) == 2 and len(v.post_requests[-1]) == 1
        assert repaired.requested_quotes[0].outcome == repair_side
        assert repaired.fill_deadline_monotonic == repaired.t0_monotonic + 3
        first_delete = next(
            t[2] for t in wire.timeline if t[0] == "DELETE" and t[2] >= repaired.t0_monotonic
        )
        assert first_delete >= repaired.fill_deadline_monotonic
    finally:
        if warmer:
            warmer.cancel()
            await asyncio.gather(warmer, return_exceptions=True)
        await cleanup(setup)


@pytest.mark.parametrize("signature_type", [0, 1, 2])
@pytest.mark.parametrize("neg_risk", [False, True])
@pytest.mark.parametrize("cash_buy", [False, True])
async def test_v2_signed_identity_matches_independent_official_contract_hash(
    env, signature_type, neg_risk, cash_buy
):
    _, e = await open_executor(env)
    from py_clob_client_v2.config import get_contract_config

    from polymaker.domain import Quote, Side

    meta, _, books, _ = await e._snapshot(include_books=False)
    e.gateway._client.builder.signature_type = signature_type
    meta = replace(meta, neg_risk=neg_risk)
    prepared = await e.gateway.prepare(
        [Quote("1", Side.BUY, .27 if cash_buy else .49, 5.074 if cash_buy else 5)],
        meta, {"1": .01},
        **({'immediate_order_type': 'FAK', 'buy_amount_usdc': '1.37'} if cash_buy else {}))
    order = prepared.orders[0].order
    contracts = get_contract_config(137)
    exchange = contracts.neg_risk_exchange_v2 if neg_risk else contracts.exchange_v2
    # Mirrors the public contract's ABI structHash, independently of SDK's
    # encode_typed_data/build_order_hash implementation.
    domain = keccak(
        encode(
            ["bytes32", "bytes32", "bytes32", "uint256", "address"],
            [
                keccak(
                    text="EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
                ),
                keccak(text="Polymarket CTF Exchange"),
                keccak(text="2"),
                137,
                exchange,
            ],
        )
    )
    order_type = "Order(uint256 salt,address maker,address signer,uint256 tokenId,uint256 makerAmount,uint256 takerAmount,uint8 side,uint8 signatureType,uint256 timestamp,bytes32 metadata,bytes32 builder)"
    struct = keccak(
        encode(
            [
                "bytes32",
                "uint256",
                "address",
                "address",
                "uint256",
                "uint256",
                "uint256",
                "uint8",
                "uint8",
                "uint256",
                "bytes32",
                "bytes32",
            ],
            [
                keccak(text=order_type),
                int(order.salt),
                order.maker,
                order.signer,
                int(order.tokenId),
                int(order.makerAmount),
                int(order.takerAmount),
                int(order.side),
                int(order.signatureType),
                int(order.timestamp),
                bytes.fromhex(order.metadata[2:]),
                bytes.fromhex(order.builder[2:]),
            ],
        )
    )
    assert prepared.order_ids == ("0x" + keccak(b"\x19\x01" + domain + struct).hex(),)


async def test_one_slow_post_does_not_cancel_before_original_signal_deadline(env, monkeypatch):
    setup = await setup_obi(env, monkeypatch, horizon=.3)
    v, e, _, _, normal, wire, *_ = setup
    e.cfg.rdp_sm.obi.max_rtt_p95_ms = 50
    wire.delay = lambda: time.sleep(.07)
    try:
        result, _ = await normal.enter(Decimal(0), Decimal(0), "isolated-slow-post")
        assert result.close_barrier == "SAFE_TO_CONTINUE"
        first_delete = next(t[2] for t in wire.timeline if t[0] == "DELETE")
        assert first_delete >= result.fill_deadline_monotonic
        records = journal_records(e.journal.path)
        closing = [r['data']['payload'] for r in records if r['kind'] == 'sm_obi_close_requested']
        assert len(closing) == 1 and closing[0]['reason'] == 'ORDER_DEADLINE'
        requests = [r['data']['payload'] for r in records if r['kind'] == 'sm_network_request']
        posts = [r for r in requests if r['method'] == 'POST']
        assert posts[0]['duration_ms'] >= 50
        assert posts[0]['batch_id'] == 'isolated-slow-post'
        assert posts[0]['outcome'] == 'SUCCESS'
        assert v.post_requests and not e.reads.network.snapshot().close_active
    finally:
        await cleanup(setup)


async def test_recovery_probe_batch_can_post_and_only_recovers_after_official_close(env, monkeypatch):
    setup = await setup_obi(env, monkeypatch, horizon=.15)
    v, e, _, _, normal, wire, *_ = setup
    network = e.reads.network
    e.cfg.rdp_sm.obi.network_pause_sec = .02
    network.observe("GET", .01, False)
    try:
        assert await normal.enter(Decimal(0), Decimal(0), "cooldown") is None
        assert not v.post_requests
        await asyncio.sleep(.03)
        for _ in range(3):
            await e.reads.server_time()
        assert network.snapshot().state == "PROBE"
        result, _ = await normal.enter(Decimal(0), Decimal(0), "recovery")
        assert result.close_barrier == "SAFE_TO_CONTINUE"
        assert next(t[2] for t in wire.timeline if t[0] == "DELETE") >= result.fill_deadline_monotonic
        assert network.snapshot().state == "NORMAL"
        records = journal_records(e.journal.path)
        transitions = [r['data']['payload'] for r in records if r['kind'] == 'sm_network_transition']
        assert [r['state'] for r in transitions] == ['PAUSED', 'PROBE', 'NORMAL']
        assert transitions[-1]['reason'] == 'NETWORK_WRITE_RECOVERED'
        assert transitions[-1]['event_monotonic'] >= result.fill_deadline_monotonic
    finally:
        await cleanup(setup)


@pytest.mark.parametrize('status,outcome,paused', [
    (400, 'HTTP_REJECTION', False), (401, 'AUTHENTICATION_ERROR', False),
    (429, 'RATE_LIMITED', True), (503, 'SERVER_ERROR', True),
    (None, 'TRANSPORT_ERROR', True),
])
async def test_http_errors_are_classified_without_logging_error_bodies(env, monkeypatch, status, outcome, paused):
    from py_clob_client_v2.exceptions import PolyApiException

    setup = await setup_obi(env, monkeypatch)
    _, e, *_ = setup
    network = e.reads.network
    error = (PolyApiException(error_msg='secret-error-body') if status is None else
             PolyApiException(httpx.Response(status, json={'error': 'secret-error-body'})))
    try:
        e.gateway._client._observe_http('POST', 'https://clob.polymarket.com/orders',
                                       time.monotonic(), time.time(), error=error)
        assert (network.snapshot().state == 'PAUSED') is paused
        e.drain_network_events()
        await e.journal.flush()
        records = journal_records(e.journal.path)
        requests = [r['data']['payload'] for r in records if r['kind'] == 'sm_network_request']
        assert requests[-1]['outcome'] == outcome
        assert requests[-1]['http_status'] == status
        assert 'secret-error-body' not in json.dumps(records)
    finally:
        await cleanup(setup)


@pytest.mark.parametrize('controller_batch', [True, False])
async def test_cancel_scopes_share_owner_and_capture_batch_before_sdk_returns(env, monkeypatch, controller_batch):
    from py_clob_client_v2.client import ClobClient

    setup = await setup_obi(env, monkeypatch)
    _, e, *_ = setup
    client, network = e.gateway._client, e.reads.network
    client.known_ids.update({'old-A', 'current-B'})
    assert network.begin_batch('close-B')
    client.network_batch_id = 'close-B' if controller_batch else None

    def sdk_delete(_self, _endpoint, headers=None, data=None, params=None):
        # This mutation stands in for a coordinator advancing its context while
        # a worker is still waiting for HTTP. Ownership belongs to request start.
        client.network_batch_id = 'unrelated-next-context'
        return {'canceled': json.loads(data), 'not_canceled': {}}

    monkeypatch.setattr(ClobClient, '_delete', sdk_delete)
    try:
        for ids in (['current-B'], ['old-A', 'current-B']):
            client.network_batch_id = 'close-B' if controller_batch else None
            response = await e.gateway.cancel_detailed(ids)
            assert set(response.canceled) == set(ids)
        e.drain_network_events()
        await e.journal.flush()
        requests = [r['data']['payload'] for r in journal_records(e.journal.path)
                    if r['kind'] == 'sm_network_request' and r['data']['payload']['method'] == 'DELETE']
        assert len(requests) == 2
        assert {r['batch_id'] for r in requests} == {'close-B'}
        assert {r['operation_id'] for r in requests} == {'cancel-batch:close-B'}
        assert len({r['request_scope_id'] for r in requests}) == 2
    finally:
        await cleanup(setup)
