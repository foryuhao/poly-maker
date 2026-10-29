"""TP cancellation ownership survives BA handoff; ACK never grants admission."""

import asyncio
from decimal import Decimal as D

import pytest

from polymaker.execution.gateway import CancelResult
from polymaker.rdp_sm_opposite_ba import OppositeBAController
from test_rdp_sm_ba import absorption, trade
from test_rdp_sm_ca import buy
from test_rdp_sm_ca_entry import env as env
from test_rdp_sm_ca_matched_tp import fill, tp_setup
from test_rdp_sm_cached_reads import attach, finish
from test_rdp_sm_obi_execution import cleanup
from test_rdp_sm_repair import reflect_positions


async def test_parent_joins_inflight_tp_cancel_and_still_cancels_other_orders(env, monkeypatch):
    setup = await tp_setup(env, monkeypatch)
    e = setup[1]
    began, release = asyncio.Event(), asyncio.Event()
    calls, sends = [], []

    async def tp_cancel():
        sends.append("tp")
        began.set()
        await release.wait()
        return e.monotonic()

    async def cancel(ids):
        calls.append(ids)
        return CancelResult(tuple(ids), (), ())

    monkeypatch.setattr(e.gateway, "cancel_detailed", cancel)
    e.known.update({"tp", "other"})
    e.cfg.rdp_sm.obi.cancel_retry_sec = 1
    owner = asyncio.create_task(e._cancel_tp_for_ba_handoff("tp", tp_cancel))
    parent = duplicate = None
    try:
        await began.wait()
        duplicate = asyncio.create_task(e._cancel_tp_for_ba_handoff("tp", tp_cancel))
        parent = asyncio.create_task(e._cancel_known())
        await asyncio.sleep(.01)
        assert not parent.done() and not duplicate.done()
        assert sends == ["tp"] and not calls
        release.set()
        await asyncio.gather(owner, duplicate, parent)
        assert sends == ["tp"]
        assert calls == [["other"]]
        assert not e.safe_to_continue
        assert not e.proofs and not e.terminal
    finally:
        release.set()
        await asyncio.gather(owner, *[t for t in (parent, duplicate) if t], return_exceptions=True)
        await cleanup(setup)


@pytest.mark.parametrize("result", ["ack", "unknown", "error"])
async def test_only_positive_ack_is_reused_and_original_retry_deadline_is_inclusive(
    env, monkeypatch, result
):
    setup = await tp_setup(env, monkeypatch)
    e = setup[1]
    clock, calls = [100.0], []
    monkeypatch.setattr(e, "monotonic", lambda: clock[0])
    e.cfg.rdp_sm.obi.cancel_retry_sec = .25
    e.known.add("tp")

    async def tp_cancel():
        if result == "error":
            raise OSError("cancel failed")
        return clock[0] if result == "ack" else None

    async def cancel(ids):
        calls.append(ids)
        return CancelResult(tuple(ids), (), ())

    monkeypatch.setattr(e.gateway, "cancel_detailed", cancel)
    try:
        if result == "error":
            with pytest.raises(OSError):
                await e._cancel_tp_for_ba_handoff("tp", tp_cancel)
        else:
            await e._cancel_tp_for_ba_handoff("tp", tp_cancel)
        clock[0] = 100.249
        await e._cancel_known()
        assert calls == ([] if result == "ack" else [["tp"]])
        assert not e.safe_to_continue
        if result == "ack":
            clock[0] = 100.25
            await e._cancel_known()
            assert calls == [["tp"]]
        # Ordinary parent retries retain their old behavior after handoff expires.
        await e._cancel_known()
        assert calls == [["tp"], ["tp"]]
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("waiter", ["owner", "parent"])
async def test_cancelled_waiter_drains_same_real_cancellation(env, monkeypatch, waiter):
    setup = await tp_setup(env, monkeypatch)
    e = setup[1]
    began, release = asyncio.Event(), asyncio.Event()
    sends = []
    e.known.add("tp")
    e.cfg.rdp_sm.obi.cancel_retry_sec = 1

    async def tp_cancel():
        sends.append("tp")
        began.set()
        await release.wait()
        return e.monotonic()

    async def unexpected_cancel(ids):
        pytest.fail(f"duplicate DELETE: {ids}")

    monkeypatch.setattr(e.gateway, "cancel_detailed", unexpected_cancel)
    owner = asyncio.create_task(e._cancel_tp_for_ba_handoff("tp", tp_cancel))
    parent = None
    try:
        await began.wait()
        parent = asyncio.create_task(e._cancel_known())
        await asyncio.sleep(.01)
        selected = owner if waiter == "owner" else parent
        selected.cancel()
        await asyncio.sleep(.01)
        assert not selected.done()
        assert not e._tp_ba_cancel_jobs["tp"].done()
        release.set()
        results = await asyncio.gather(owner, parent, return_exceptions=True)
        assert sends == ["tp"] and e._tp_ba_cancel_jobs["tp"].done()
        if waiter == "owner":
            assert isinstance(results[0], asyncio.CancelledError)
        else:
            assert e.fatal == "ABNORMAL_EXIT"
        assert not e.safe_to_continue
    finally:
        release.set()
        await asyncio.gather(owner, *([parent] if parent else []), return_exceptions=True)
        await cleanup(setup)


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("fault", ["none", "partial_fill", "ack_but_live", "unknown_ack"])
async def test_real_tp_ba_handoff_shares_delete_but_keeps_full_close_proof(
    env, monkeypatch, tmp_path, cached, fault
):
    setup = await tp_setup(env, monkeypatch)
    v, e, f, lifecycle, normal = setup[:5]
    # Give collection reads time to finish; no artificial sleep is added to handoff.
    e.cfg.rdp_sm.obi.cancel_retry_sec = 2
    companion = (await attach(setup, tmp_path))[0] if cached else None
    native = e.gateway.send_prepared
    ready = asyncio.Event()
    task = None

    async def send(signed, **kw):
        raw = await native(signed, **kw)
        oid = signed.order_ids[0]
        if len(v.post_requests) == 1:
            fill(v, oid, "opening")
            v.rows[oid]["status"] = "MATCHED"
            reflect_positions(v)
        else:
            ready.set()
        return raw

    def canceled(venue):
        if len(e.attempts) < 2:
            return
        oid = e.attempts[1].expected_order_id
        if oid not in venue.cancels[-1]:
            return
        if fault == "partial_fill" and not venue.rows[oid]["associate_trades"]:
            fill(venue, oid, "cancel-race", 2)
            reflect_positions(venue)
        elif fault == "ack_but_live":
            venue.rows[oid]["status"] = "LIVE"

    monkeypatch.setattr(e.gateway, "send_prepared", send)
    v.on_cancel = canceled
    if fault == "unknown_ack":
        # Venue receives DELETE, but its response does not positively ACK this ID.
        native_cancel = e.gateway.cancel_detailed

        async def unknown_once(ids):
            ack = await native_cancel(ids)
            tp = e.attempts[1].expected_order_id if len(e.attempts) > 1 else None
            if ids == [tp] and sum(tp in group for group in v.cancels) == 1:
                return CancelResult((), (), (tp,), ack.sdk_started_monotonic, ack.ack_monotonic)
            return ack

        monkeypatch.setattr(e.gateway, "cancel_detailed", unknown_once)
    try:
        f.apply([trade(f), buy(f)])
        task = asyncio.create_task(normal.enter(D(0), D(0), "tp-ba-shared-cancel"))
        await asyncio.wait_for(ready.wait(), 3)
        tp = e.attempts[1].expected_order_id
        absorption(f, "2")
        result, sizing = await asyncio.wait_for(task, 5)
        assert sizing["ca_matched_tp"]["reason"] == "OPPOSITE_BA_HANDOFF"
        count = sum(tp in ids for ids in v.cancels)
        if fault == "unknown_ack":
            assert count >= (1 if cached else 2)
        else:
            assert count == 1
        assert len(v.post_requests) == 2
        assert all(row["postOnly"] for body in v.post_requests for row in body)
        if fault == "ack_but_live":
            assert result.close_barrier != "SAFE_TO_CONTINUE"
            assert not e.safe_to_continue and tp not in e.proofs
            assert e.committed() >= e.attempts[1].maximum
            with pytest.raises(ValueError, match="PREVIOUS_WINDOW_UNRESOLVED"):
                await OppositeBAController(e, lifecycle).repair(D(5), D(0), "must-not-post")
            assert len(v.post_requests) == 2
        else:
            assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
            assert e.economic_inventory() == (D(5), D(2 if fault == "partial_fill" else 0))
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if companion:
            await finish(setup, companion)
        else:
            await cleanup(setup)
