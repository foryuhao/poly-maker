"""MATCHED TP -> BA wakes existing proof lanes without granting admission."""

import asyncio
import time
from decimal import Decimal as D

import pytest

import test_rdp_sm_cached_reads as cached_tests
from polymaker.execution.gateway import CancelResult
from polymaker.rdp_sm_opposite_ba import OppositeBAController
from test_rdp_sm_ba import absorption, trade
from test_rdp_sm_ca import buy
from test_rdp_sm_ca_entry import env as env
from test_rdp_sm_ca_matched_tp import fill, tp_setup
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_repair import reflect_positions


@pytest.mark.parametrize("fault", ["none", "unknown_ack", "exception", "partial_fill", "live", "declined"])
async def test_handoff_requests_post_cancel_proof_even_before_repair_episode(
    env, monkeypatch, tmp_path, fault, record_property
):
    native_companion = cached_tests.SyncCompanion

    def slow_companion(cfg, *args, **kwargs):
        cfg.rdp_sm.sync_observe.order_interval_s = .4
        cfg.rdp_sm.sync_observe.account_interval_s = .6
        cfg.rdp_sm.obi.network_probe_interval_sec = .04
        return native_companion(cfg, *args, **kwargs)

    monkeypatch.setattr(cached_tests, "SyncCompanion", slow_companion)
    setup = await tp_setup(env, monkeypatch)
    v, e, f, lifecycle, normal = setup[:5]
    e.cfg.rdp_sm.obi.cancel_retry_sec = 2
    companion, calls = await cached_tests.attach(setup, tmp_path)
    cache = e.shared_reads
    cache.timeout_s = 1.5
    ready, requested = asyncio.Event(), asyncio.Event()
    opening, returned, observations, tracks, direct = [], [], [], [], []
    native_send = e.gateway.send_prepared
    native_cancel = e.gateway.cancel_detailed
    native_refresh = cache.service.request_repair_refresh
    native_track = cache.service.track_orders
    native_get = e.gateway._client._get
    task = None

    async def send(signed, **kw):
        raw = await native_send(signed, **kw)
        oid = signed.order_ids[0]
        if len(v.post_requests) == 1:
            opening.append(fill(v, oid, "opening-matched", status="MATCHED"))
            v.rows[oid]["status"] = "MATCHED"
            # MATCHED admits its TP, but no economic inventory or repair episode yet.
        else:
            ready.set()
        return raw

    async def cancel(ids):
        ack = await native_cancel(ids)
        tp = e.attempts[1].expected_order_id if len(e.attempts) > 1 else None
        if ids == [tp]:
            returned.append(time.monotonic())
            if fault == "declined":
                cache.service.network = None
            elif fault == "unknown_ack":
                return CancelResult((), (), (tp,), ack.sdk_started_monotonic, ack.ack_monotonic)
            elif fault == "exception" and len(returned) == 1:
                raise OSError("DELETE response lost")
        return ack

    def on_cancel(venue):
        if len(e.attempts) < 2:
            return
        tp = e.attempts[1].expected_order_id
        if tp not in venue.cancels[-1]:
            return
        if fault == "partial_fill" and not venue.rows[tp]["associate_trades"]:
            fill(venue, tp, "cancel-race", 2)
        elif fault == "live":
            venue.rows[tp]["status"] = "LIVE"

    def track(requirements):
        tracks.append(dict(requirements))
        return native_track(requirements)

    def refresh(lanes=("orders",)):
        if returned:
            tp = e.attempts[1].expected_order_id
            observations.append({"lanes": tuple(lanes), "after": cache.order_writes[tp],
                "last_track": tracks[-1] if tracks else None,
                "safe": e.safe_to_continue, "repair_episode": e.repair_episode,
                "at": time.monotonic()})
            requested.set()
        return native_refresh(lanes)

    def foreground_get(endpoint, **kwargs):
        direct.append(endpoint)
        return native_get(endpoint, **kwargs)

    monkeypatch.setattr(e.gateway, "send_prepared", send)
    monkeypatch.setattr(e.gateway, "cancel_detailed", cancel)
    monkeypatch.setattr(cache.service, "request_repair_refresh", refresh)
    monkeypatch.setattr(cache.service, "track_orders", track)
    monkeypatch.setattr(e.gateway._client, "_get", foreground_get)
    v.on_cancel = on_cancel
    try:
        f.apply([trade(f), buy(f)])
        task = asyncio.create_task(normal.enter(D(0), D(0), "tp-ba-proof-refresh"))
        await asyncio.wait_for(ready.wait(), 3)
        assert e.repair_episode is None and not e.confirmed()
        tp = e.attempts[1].expected_order_id
        absorption(f, "2")
        await asyncio.wait_for(requested.wait(), 1)
        first = observations[0]
        assert first["lanes"] == ("orders", "account")
        assert first["last_track"] == {tp: first["after"]}
        assert not first["safe"]
        if fault != "partial_fill":
            assert first["repair_episode"] is None
        assert first["after"] <= returned[0] <= first["at"]
        # Only independent official evidence can finish the original barrier.
        opening[0]["status"] = "CONFIRMED"
        reflect_positions(v)
        result, sizing = await asyncio.wait_for(task, 4)
        assert sizing["ca_matched_tp"]["reason"] == "OPPOSITE_BA_HANDOFF"
        assert len(v.post_requests) == 2
        assert all("/tick-size" in endpoint or "/version" in endpoint for endpoint in direct)
        assert all(method == "GET" for method, _, _ in calls)
        await e.journal.flush()
        refreshes = [r["data"]["payload"] for r in journal_records(e.journal.path)
                     if r["kind"] == "sm_ca_matched_tp_proof_refresh"]
        assert len(refreshes) == 1
        assert refreshes[0]["operation_after_monotonic"] == first["after"]
        assert refreshes[0]["requested"] is (fault != "declined")
        if fault == "live":
            assert result.close_barrier != "SAFE_TO_CONTINUE"
            assert not e.safe_to_continue and tp not in e.proofs
            with pytest.raises(ValueError, match="PREVIOUS_WINDOW_UNRESOLVED"):
                await OppositeBAController(e, lifecycle).repair(D(5), D(0), "no-early-ba")
            assert len(v.post_requests) == 2
        else:
            assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
            assert e.economic_inventory() == (D(5), D(2 if fault == "partial_fill" else 0))
            record_property("cancel_return_to_safe_s", result.close_monotonic-returned[0])
        # The detail used for closure must cross the actual write boundary.
        if fault not in {"live", "exception", "unknown_ack"}:
            detail = next(r for r in cache.service.latest().last_good if r.name == "order:"+tp)
            assert detail.started_monotonic >= first["after"]
    finally:
        if task is not None and not task.done():
            opening[0]["status"] = "CONFIRMED"
            reflect_positions(v)
            await asyncio.gather(task, return_exceptions=True)
        await cached_tests.finish(setup, companion)
