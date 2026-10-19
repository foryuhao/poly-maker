"""Explicit POST rejection must not keep advancing the cache write boundary."""

import asyncio
import time
from decimal import Decimal

import httpx
import pytest

from polymaker.config import RDPRepairPathConfig
from polymaker.rdp_sm_cached_reads import CachedExecutionReads
from polymaker.rdp_sm_entry import AccountRiskPolicy
from polymaker.rdp_sm_normal import NormalController
from polymaker.rdp_sm_sync_companion import SyncCompanion
from polymaker.rdp_sm_sync_reads import ObserverReader
from test_rdp_sm_cached_reads import attach, finish
from test_rdp_sm_entry import boundary
from test_rdp_sm_m1 import CID
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_obi_execution import setup_obi
from test_rdp_sm_path_repair import setup_policy
from test_rdp_sm_repair import env as env
from test_rdp_sm_repair import reflect_positions


async def test_sl_native_rejection_closes_on_fresh_account_without_delete_loop(
        env, monkeypatch, tmp_path, record_property):
    setup, repair = await setup_policy(env, monkeypatch)
    v, e, feed, lifecycle, _, wire, _, pump, _ = setup
    e.cfg.rdp_sm.repair.path = RDPRepairPathConfig()
    e.cfg.rdp_sm.obi.cancel_retry_sec = .25
    repair.observe(feed.snapshot(repair=True))
    pump.update(bid=".39", yes_ask=".41", no_bid=".59", no_ask=".61")
    await asyncio.sleep(.03)
    repair.observe(feed.snapshot(repair=True))
    assert repair.policy.mode == "SL"

    cfg = e.cfg
    cfg.rdp_sm.sync_observe.enabled = cfg.rdp_sm.sync_observe.use_for_execution = True
    # Production cadence and observed live request latency, without real I/O.
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
    # Shorten only the test's failure deadline; the production timeout stays 10s.
    e.shared_reads.timeout_s = 4.5
    wire.reject_index = 0
    post_returned = []

    def writes(request):
        if request.method in {"POST", "DELETE"}:
            time.sleep(.2)
        response = wire(request)
        if request.method == "POST" and wire.reject_index == 0:
            post_returned.append(time.monotonic())
            return httpx.Response(200, json=[{
                "success": True, "errorMsg": "invalid post-only order: order crosses book",
                "orderID": "", "status": "", "makingAmount": "", "takingAmount": "",
            }], request=request)
        return response

    client = boundary(monkeypatch, v, writes)
    try:
        lifecycle.account = (await e._snapshot(include_books=False))[3]
        result, _ = await repair.repair(*e.economic_inventory(), "sl-native-rejected")
        duration = time.monotonic()-post_returned[0]
        rejected_id = e.attempts[-1].expected_order_id
        deletes = sum(rejected_id in ids for ids in v.cancels)
        record_property("post_response_to_close_sec", duration)
        record_property("rejected_id_delete_count", deletes)
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert result.placement_results[-1]["outcome"] == "REJECTED"
        assert deletes == 0
        assert rejected_id not in e.known and rejected_id not in v.rows
        assert e.processor.owned_record(rejected_id) is None
        assert e.economic_inventory() == (Decimal(5), Decimal(0))
        assert e.committed() == Decimal("1.5") and repair.policy.mode == "SL"
        assert duration < e.shared_reads.timeout_s
        assert not e.shared_reads.inflight

        await e.journal.flush()
        closes = [r["data"]["payload"] for r in journal_records(e.journal.path)
                  if r["kind"] == "sm_sync_cache_read"
                  and r["data"]["payload"]["purpose"] == "close"]
        assert closes
        close = closes[-1]
        assert close["operation_after_monotonic"] >= e.shared_reads.post_returns[rejected_id]
        assert set(close["sources"]) == {"time", "open_orders", "trades", "capital", "positions"}
        assert all(source["started_monotonic"] >= close["operation_after_monotonic"]
                   for source in close["sources"].values())

        wire.reject_index = None

        def fill_retry(venue, oid):
            row = venue.rows[oid]
            if row["asset_id"] == "2" and row["status"] == "LIVE" and not row["associate_trades"]:
                venue.fill(oid, "sl-retry-fill", 5)
                reflect_positions(venue)

        v.on_order_read = fill_retry
        lifecycle.account = (await e._snapshot(include_books=False))[3]
        retry = await repair.repair(*e.economic_inventory(), "sl-after-rejection")
        assert retry is not None
        assert retry[0].close_barrier == "SAFE_TO_CONTINUE", retry[0].block_reason
        assert len(v.post_requests) == 3  # opening, rejected SL, accepted SL
        assert e.economic_inventory() == (Decimal(5), Decimal(5))
        assert e.repair_episode is None and e.committed() < 15
    finally:
        client.close()
        await finish(setup, companion)


@pytest.mark.parametrize("rejected_leg", [0, 1])
async def test_mixed_batch_cancels_only_the_accepted_leg(env, monkeypatch, tmp_path, rejected_leg):
    setup = await setup_obi(env, monkeypatch, horizon=.6)
    v, e, _, lifecycle, _, wire, *_ = setup
    companion, _ = await attach(setup, tmp_path)
    wire.reject_index = rejected_leg

    class PairController(NormalController):
        """Exercise the shared batch primitive, without changing live NORMAL."""

        def candidates(self, snapshot, yes, no, window_id):
            plans = super().candidates(snapshot, yes, no, window_id)
            return {"YES": (plans["YES"][0]+plans["NO"][0], {})}

        def matches(self, intent, snapshot):
            direction, _ = self.select(snapshot)
            return (direction == intent.direction and
                    self.candidates(snapshot, intent.yes, intent.no, intent.window_id)[direction][0]
                    == intent.quotes)

    try:
        result, _ = await PairController(e, lifecycle).enter(Decimal(0), Decimal(0), "mixed")
        rejected = e.attempts[rejected_leg].expected_order_id
        accepted = e.attempts[1-rejected_leg].expected_order_id
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert result.placement_results[rejected_leg]["outcome"] == "REJECTED"
        assert v.cancels and all(ids == [accepted] for ids in v.cancels)
        assert rejected not in e.known and e.known == {accepted}
        assert v.rows[accepted]["status"] == "CANCELED" and e.committed() == 0
    finally:
        await finish(setup, companion)


@pytest.mark.parametrize("lost_ack", [False, True])
async def test_ambiguous_post_keeps_canceling_owned_signed_id(env, monkeypatch, tmp_path, lost_ack):
    setup = await setup_obi(env, monkeypatch)
    v, e, _, _, normal, wire, *_ = setup
    companion, _ = await attach(setup, tmp_path)
    wire.fail_ack = lost_ack

    def ambiguous(request):
        response = wire(request)
        if request.method == "POST":
            # The venue accepted it, but the reply cannot prove acceptance or rejection.
            return httpx.Response(200, json=[{
                "success": True, "errorMsg": "internal processing error", "orderID": "",
                "status": "", "makingAmount": "", "takingAmount": "",
            }], request=request)
        return response

    client = boundary(monkeypatch, v, ambiguous)
    try:
        result, _ = await normal.enter(Decimal(0), Decimal(0), "ambiguous-signed-id")
        oid = e.attempts[-1].expected_order_id
        assert oid in e.known and e.processor.owned_record(oid)
        assert any(oid in ids for ids in v.cancels)
        assert v.rows[oid]["status"] == "CANCELED"
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert e.safe_to_continue and not e.shared_reads.inflight
    finally:
        client.close()
        await finish(setup, companion)
