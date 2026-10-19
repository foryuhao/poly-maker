"""Release acceptance: production entry/loop/SDK/WAL, simulated venue boundaries.

No external sockets are permitted by the inherited fixture. Clock jumps only
accelerate idle market phases; controller, admission and accounting stay real.
"""

import asyncio
import hashlib
import json
import threading
import time
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from polymaker.config import RDPRepairPathConfig
from polymaker.rdp_sm_entry import run_current_market
from polymaker.rdp_sm_execution import SMWindowExecutor
from polymaker.rdp_sm_fence import create_fence, recover_fence
from polymaker.rdp_sm_loop import SMTradingLoop
from polymaker.rdp_sm_marketdata import SessionFeed
from test_rdp_sm_entry import boundary
from test_rdp_sm_m1 import CID, START
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_m3_loop import env as loop_env
from test_rdp_sm_obi_execution import Wire, cleanup
from test_rdp_sm_path_repair import setup_policy
from test_rdp_sm_repair import reflect_positions


@pytest.fixture
def env(monkeypatch, tmp_path):
    yield from loop_env.__wrapped__(monkeypatch, tmp_path)


async def until(predicate, timeout=12):
    async with asyncio.timeout(timeout):
        while not predicate():  # noqa: ASYNC110 - observe independent venue/loop tasks
            await asyncio.sleep(.01)


@pytest.mark.parametrize("lost_tp_ack", [False, True])
async def test_production_session_hd_tp_partial_late_fill_sl_next_loop_cutoffs_merge(
        env, monkeypatch, lost_tp_ack, cached=False):
    v, cfg = env
    cfg.rdp_sm.normal_entry_mode = "obi_v1"
    cfg.rdp_sm.repair.policy = "hd_tp_sl_v1"
    cfg.rdp_sm.repair.path.calibration_status = "REPLAY_VALIDATED"
    cfg.rdp_sm.repair.path.validation_reference = "offline-acceptance-fixture"
    cfg.rdp_sm.obi.signal_horizon_sec = .5
    cfg.rdp_sm.obi.max_signal_age_ms = 100
    cfg.rdp_sm.obi.cancel_retry_sec = .02
    cfg.rdp_sm.obi.network_min_samples = cfg.rdp_sm.obi.network_recovery_samples = 1
    cfg.rdp_sm.obi.network_probe_interval_sec = .05
    cfg.rdp_sm.sync_observe.enabled = cfg.rdp_sm.sync_observe.use_for_execution = cached
    anchor = time.monotonic()
    def clock():
        return v.time + time.monotonic() - anchor

    monkeypatch.setattr(time, "time", clock)
    state = {"p": Decimal(".31"), "direction": "YES", "stage": "FIRST",
             "seeded": False, "late": False, "frame": 0}
    feeds, loops, wires, sockets, posts, inventory = [], [], [], [], [], []
    native_loop, native_open = SMTradingLoop, SMWindowExecutor.open

    def loop_factory(*args, **kwargs):
        loop = native_loop(*args, **kwargs)
        loops.append(loop)
        return loop

    monkeypatch.setattr("polymaker.rdp_sm_entry.SMTradingLoop", loop_factory)

    def feed_factory(*args, **kwargs):
        feed = SessionFeed(*args, wall=clock, **kwargs)
        feeds.append(feed)
        return feed

    monkeypatch.setattr("polymaker.rdp_sm_marketdata.SessionFeed", feed_factory)

    async def observe_open(cls, *args, **kwargs):
        e = await native_open(*args, **kwargs)
        v.executor = e
        wires.append(Wire(v, e))
        return e

    monkeypatch.setattr(SMWindowExecutor, "open", classmethod(observe_open))

    def fill(oid, tid, quantity, status="CONFIRMED"):
        trade = v.fill(oid, tid, quantity, status)
        trade["trader_side"] = "MAKER"
        trade["maker_orders"][0]["maker_address"] = v.funder
        reflect_positions(v)
        y = sum(Decimal(str(p["current_size"])) for p in v.positions if p["token_id"] == "1")
        n = sum(Decimal(str(p["current_size"])) for p in v.positions if p["token_id"] == "2")
        inventory.append((y, n))
        assert abs(y-n) <= 5

    def order_read(venue, oid):
        row = venue.rows[oid]
        if row["status"] != "LIVE" or row["associate_trades"]:
            return
        if row["asset_id"] == "1":
            fill(oid, f"opening-{state['stage']}", 5, "MATCHED" if state["stage"] == "FIRST" else "CONFIRMED")
            state["p"] = Decimal(".29")
        elif state["stage"] == "FIRST":
            # Leave 3 shares after the late TP fill, above the new 2.5-share
            # repair threshold, so this scenario still exercises SL + next loop.
            fill(oid, "tp-partial" if len(posts) == 2 else "sl-partial-to-pair",
                 1 if len(posts) == 2 else 3)

    v.on_order_read = order_read

    def cancel(venue):
        for post in posts:
            row = venue.rows[post["id"]]
            if post["mode"] == "NORMAL" and not row["associate_trades"]:
                fill(post["id"], f"opening-{state['stage']}", 5,
                     "MATCHED" if state["stage"] == "FIRST" else "CONFIRMED")
                state["p"] = Decimal(".29")
        if len(posts) >= 2 and state["stage"] == "FIRST" and not state["late"]:
            # A last TP share arrives while its cancellation is being resolved.
            oid = posts[1]["id"]
            if venue.rows[oid]["associate_trades"]:
                state["late"] = True
                fill(oid, "tp-late-on-cancel", 1)

    v.on_cancel = cancel

    class Socket:
        closed = False

        async def __aenter__(self):
            sockets.append(self)
            return self

        async def __aexit__(self, *_):
            self.closed = True

        async def send(self, payload):
            if payload != "PING":
                assert json.loads(payload)["assets_ids"] == ["1", "2"]

        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.sleep(.01)
            state["frame"] += 1
            n = state["frame"] % 2
            feed = feeds[-1]
            if not state["seeded"]:
                # Recorded pre-opening one-second observations warm the REAL
                # volatility estimator, rather than replacing its output.
                with feed.lock:
                    now = time.monotonic()
                    for i in range(30, 0, -1):
                        feed.probability_points.append((int(clock())-i, now-i,
                                                       .30+.02*(i % 2), feed.generation))
                state["seeded"] = True
            rows = []
            for token, mid in (("1", state["p"]), ("2", 1-state["p"])):
                strong = token == "1" and state["direction"] == "YES"
                sizes = (80+n, 20+n) if strong else ((50+n, 50+n) if state["direction"] == "NONE" else (20+n, 80+n))
                rows.append({"event_type": "book", "market": CID, "asset_id": token,
                             "timestamp": str(clock()*1000),
                             "bids": [{"price": str(mid-Decimal(".01")), "size": str(sizes[0])}],
                             "asks": [{"price": str(mid+Decimal(".01")), "size": str(sizes[1])}]})
            return json.dumps(rows)

    monkeypatch.setattr("polymaker.rdp_sm_marketdata.websockets.connect", lambda *a, **kw: Socket())

    def http(request):
        if request.url.path == "/time":
            return httpx.Response(200, json=clock(), request=request)
        if request.method in {"POST", "DELETE"}:
            response = wires[-1](request)
            if request.method == "POST":
                e = v.executor
                a = e.gateway._client.batch_attempts[0]
                posts.append({"id": a.expected_order_id, "price": a.quote.price,
                              "size": a.quote.size, "side": a.quote.outcome,
                              "elapsed": clock()-START, "mono": time.monotonic(),
                              "mode": (e.repair_episode or {}).get("policy_state", {}).get("mode", "NORMAL")})
                if posts[-1]["mode"] == "NORMAL":
                    # The bid is hit on a falling market; give the WS frame time
                    # to arrive before the actual fill at cancellation.
                    state["p"] = Decimal(".29")
                assert e.committed() <= 15
                assert all(p["postOnly"] and p["order"]["side"] == "BUY" for p in json.loads(request.content))
                if lost_tp_ack and len(posts) == 2:
                    fill(a.expected_order_id, "tp-partial", 1)
                    raise httpx.ReadTimeout("accepted TP with lost acknowledgement", request=request)
            return response
        return None

    def mode():
        return (getattr(v.executor, "repair_episode", None) or {}).get("policy_state", {}).get("mode")

    def jump(elapsed):
        v.time = START+elapsed-(time.monotonic()-anchor)
        loops[-1].market_deadline = time.monotonic()+300-elapsed

    async def drive():
        await until(lambda: mode() == "HD")
        state["p"] = Decimal(".50")
        await until(lambda: len(posts) >= 2)
        if not lost_tp_ack:
            # Several reviews and changing BBO must preserve the accepted TP.
            await asyncio.sleep(cfg.rdp_sm.obi.rebalance_order_ttl_sec+.25)
            state["p"] = Decimal(".52")
            await asyncio.sleep(.25)
            assert len(posts) == 2 and v.rows[posts[1]["id"]]["status"] == "LIVE"
            state["p"] = Decimal(".38")
        await until(lambda: len(posts) >= 3)
        assert v.rows[posts[1]["id"]]["status"] == "CANCELED"
        await until(lambda: v.executor.economic_inventory() == (Decimal(5), Decimal(5)))
        assert state["late"]
        # Late confirmation must promote status without adding quantities again.
        v.trades[0]["status"] = "CONFIRMED"
        state.update(stage="SECOND", direction="NONE", p=Decimal(".31"))
        await asyncio.sleep(.15)
        state["direction"] = "YES"
        await until(lambda: len(posts) >= 4 and mode() == "HD")
        jump(120.1)
        await asyncio.sleep(.15)
        assert len(posts) == 4  # no new NORMAL after T120
        jump(180.05)
        await until(lambda: len(posts) >= 5)
        assert posts[4]["mode"] == "SL"  # time force, without a price shock
        jump(240.05)
        await until(lambda: v.rows[posts[4]["id"]]["status"] == "CANCELED")
        await until(lambda: bool(v.merge_submissions))
        assert len(posts) == 5
        jump(300.05)

    client = boundary(monkeypatch, v, http)
    if cached:
        from polymaker.rdp_sm_sync_companion import SyncCompanion
        from polymaker.rdp_sm_sync_reads import ObserverReader

        def response(request):
            altered = http(request)
            return altered if altered is not None else v.response(request)

        def companion(config, session):
            return SyncCompanion(config, session, reader_factory=lambda emit, stop:
                ObserverReader(config, emit=emit, stop=stop,
                               transport=httpx.MockTransport(response)))

        monkeypatch.setattr("polymaker.rdp_sm_sync_companion.SyncCompanion", companion)
    driver = asyncio.create_task(drive())
    run = asyncio.create_task(run_current_market(cfg))
    try:
        async with asyncio.timeout(45):
            done, _ = await asyncio.wait((driver, run), return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            await driver
            result = await run
        assert result.execution_status == "COMPLETED", result.to_dict()
        assert result.final_close_barrier == "SAFE_TO_CONTINUE"
        assert [p["mode"] for p in posts] == ["NORMAL", "TP", "TP" if lost_tp_ack else "SL", "NORMAL", "SL"]
        assert posts[2]["size"] == 5  # minimum, despite only three remaining shares
        assert result.merged_pairs == 5 and result.delta == 5
        assert result.confirmed_yes == 5 and result.confirmed_no == 0
        assert result.stop_elapsed_s >= 300 and all(p["elapsed"] < 240 for p in posts)
        assert all(p["elapsed"] < 120 for p in posts if p["mode"] == "NORMAL")
        assert all(row["status"] == "CANCELED" for row in v.rows.values())
        total_cost = sum(Decimal(leg["price"])*Decimal(leg["matched_amount"])
                         for t in v.trades for leg in t["maker_orders"])
        assert total_cost <= 15 and v.executor.committed() == Decimal("1.5")
        assert result.realized_merge_pnl == pytest.approx(float(5-(total_cost-Decimal("1.5"))))
        records = journal_records(v.executor.journal.path)
        assert any(r["kind"] == "sm_position_reconciled" for r in records)
        assert any(r["kind"] == "sm_merge_result" for r in records)
        assert all(s.closed for s in sockets) and feeds[-1].task.done()
        # Make latency and actual quote sequence inspectable in pytest evidence.
        for post in posts:
            decisions = [r for r in records if r["kind"] == "sm_repair_decision"
                         and r["data"]["payload"].get("action") == "BUY"
                         and r["data"]["payload"].get("mode") == post["mode"]
                         and r["data"].get("occurred_monotonic", float("inf")) <= post["mono"]]
            if decisions:
                post["decision_to_mock_post_ms"] = 1000*(post["mono"]-decisions[-1]["data"]["occurred_monotonic"])
        closes = [r["data"]["payload"] for r in records if r["kind"] == "sm_obi_close_requested"]
        journal_bytes = await asyncio.to_thread(Path(v.executor.journal.path).read_bytes)
        evidence = {"scope": "SIMULATED_VENUE_PRODUCTION_ENTRY_AND_EXECUTION",
                    "lost_tp_ack": lost_tp_ack, "cached_execution": cached,
                    "orders": posts, "close_requests": closes,
                    "inventory": [[str(y), str(n)] for y, n in inventory],
                    "result": result.to_dict(),
                    "journal_sha256": hashlib.sha256(journal_bytes).hexdigest()}
        evidence_path = Path(v.executor.journal.path).parent.parent / "acceptance_result.json"
        await asyncio.to_thread(evidence_path.write_text, json.dumps(evidence, indent=2)+"\n")
    finally:
        driver.cancel()
        if not run.done():
            run.cancel()
        await asyncio.gather(driver, run, return_exceptions=True)
        client.close()


async def test_cached_execution_production_session_with_live_poll_intervals(env, monkeypatch):
    await test_production_session_hd_tp_partial_late_fill_sl_next_loop_cutoffs_merge(
        env, monkeypatch, lost_tp_ack=False, cached=True)


async def move_to_cutoff(setup, monkeypatch, elapsed):
    v, e, feed = setup[:3]
    v.time = START+elapsed
    e.official_time, e.time_anchor = START+elapsed, time.monotonic()
    official, anchor = e.official_time, e.time_anchor

    async def server_time():
        e.reads.network.observe("GET", .001, endpoint="/time")
        return official+time.monotonic()-anchor

    monkeypatch.setattr(e.reads, "server_time", server_time)
    e.cfg.rdp_sm.repair.path = RDPRepairPathConfig()
    feed.connect()
    await asyncio.sleep(.03)


@pytest.mark.parametrize("cancel_failures", [0, 2])
async def test_new_sl_t240_cancels_without_quotes_or_sigma_and_never_reposts(
        env, monkeypatch, cancel_failures):
    setup, controller = await setup_policy(env, monkeypatch)
    v, e, _, _, _, wire, _, pump, _ = setup
    await move_to_cutoff(setup, monkeypatch, 239.5)
    monkeypatch.setattr(controller, "sigma", lambda: None)
    wire.cancel_failures = cancel_failures
    wire.delay = lambda: pump.update(running=False)
    try:
        result, _ = await controller.repair(*e.economic_inventory(), "new-sl-cutoff")
        if result.close_barrier == "BLOCKED":
            assert cancel_failures and not e.safe_to_continue and e.committed() > Decimal("1.5")
            # The session coordinator retries the unresolved close barrier;
            # it must never interpret HTTP 200 / not_canceled as terminal.
            for _ in range(cancel_failures+1):
                if await e.verify_close(cancel=True):
                    break
            assert e.safe_to_continue, e.reason
        else:
            assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert controller.policy.mode == "SL" and len(v.post_requests) == 2
        records = journal_records(e.journal.path)
        close = [r["data"]["payload"] for r in records if r["kind"] == "sm_obi_close_requested"
                 and r["data"]["payload"]["window_id"] == "new-sl-cutoff"][0]
        assert close["reason"] in {"ORDER_DEADLINE", "MARKET_SUBMISSION_CUTOFF"}
        assert 0 <= close["requested_monotonic"]-close["deadline"] < .15
        assert all(row["status"] == "CANCELED" for row in v.rows.values())
        assert await controller.repair(*e.economic_inventory(), "after-240") is None
        assert len(v.post_requests) == 2 and e.economic_inventory() == (Decimal(5), Decimal(0))
        assert len(v.cancels) >= cancel_failures+1
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("fault", ["post_only_reject", "disconnect", "cancelled_owner"])
async def test_new_sl_failure_keeps_inventory_and_closes_possible_orders(env, monkeypatch, fault):
    setup, controller = await setup_policy(env, monkeypatch)
    v, e, feed, _, _, wire, _, pump, _ = setup
    await move_to_cutoff(setup, monkeypatch, 180)
    entered, release = threading.Event(), threading.Event()
    if fault == "post_only_reject":
        wire.reject_index = 0
    else:
        wire.delay = lambda: (entered.set(), release.wait(3))
    task = asyncio.create_task(controller.repair(*e.economic_inventory(), "sl-fault"))
    prior_cancels = len(v.cancels)
    try:
        if fault != "post_only_reject":
            assert await asyncio.to_thread(entered.wait, 2)
            if fault == "disconnect":
                pump["running"] = False
                feed.invalidate("WS_DISCONNECTED")
            else:
                task.cancel()
            await asyncio.sleep(.06)
            assert len(v.cancels) > prior_cancels  # cancellation need not wait for the POST ACK
            release.set()
        result, _ = await asyncio.wait_for(task, 5)
        assert result.close_barrier == ("BLOCKED" if fault == "cancelled_owner" else "SAFE_TO_CONTINUE"), result.block_reason
        assert len(v.post_requests) == 2  # original opening plus exactly one SL
        assert e.economic_inventory() == (Decimal(5), Decimal(0))
        assert all(row["status"] == "CANCELED" for row in v.rows.values())
        assert e.committed() == Decimal("1.5")
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await cleanup(setup)


async def test_new_sl_crash_after_prepared_before_ack_recovers_cancel_only(env, monkeypatch):
    setup, controller = await setup_policy(env, monkeypatch)
    v, e, feed, lifecycle, *_ = setup
    await move_to_cutoff(setup, monkeypatch, 180)
    try:
        snapshot = feed.snapshot(repair=True)
        decision, point = controller.observe(snapshot)
        controller.plan = controller._make_plan(snapshot, decision, point)
        assert controller.plan["mode"] == "SL"
        slot = await lifecycle.prepare(controller, *e.economic_inventory(), "sl-crash")
        create_fence(e.cfg, e.gateway.funder, e.meta, e.session_id, e.journal.path)
        await e.journal.flush()
        candidate = slot.candidates[0]
        oid, q = candidate.signed.order_ids[0], candidate.quotes[0]
        v.rows[oid] = {"id": oid, "market": e.meta.condition_id, "asset_id": q.token_id,
                       "side": "BUY", "price": str(q.price), "original_size": str(q.size),
                       "size_matched": "0", "status": "LIVE", "associate_trades": []}
        before = len(v.post_requests)
        with pytest.raises(ValueError, match="OBI_RESTART_NORMAL_DISABLED"):
            await recover_fence(e.reads, e.meta)
        assert v.rows[oid]["status"] == "CANCELED" and len(v.post_requests) == before
        prepared_ids = {oid for record in journal_records(e.journal.path)
                        if record["kind"] == "sm_prepared"
                        for c in record["data"]["payload"]["candidates"] for oid in c["order_ids"]}
        assert set(v.cancels[-1]) == prepared_ids and oid in prepared_ids
        assert e.economic_inventory() == (Decimal(5), Decimal(0))
    finally:
        await cleanup(setup)
