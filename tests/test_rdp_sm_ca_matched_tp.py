"""Owned MATCHED -> opposite fixed maker, using the real offline SDK path."""
import asyncio
from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from polymaker.config import RDPCAMatchedTPConfig, RDPSMConfig
from polymaker.rdp_sm_opposite_ba import OppositeBAController
from polymaker.rdp_sm_repair_ba import RepairBidAbsorption
from test_rdp_sm_ba import absorption, trade
from test_rdp_sm_ca import buy
from test_rdp_sm_ca_entry import ca_setup
from test_rdp_sm_ca_entry import env as env
from test_rdp_sm_cached_reads import attach, finish
from test_rdp_sm_m1 import ADDRESS, CID
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_obi_execution import cleanup
from test_rdp_sm_repair import reflect_positions


async def tp_setup(env, monkeypatch):
    setup = await ca_setup(env, monkeypatch)
    _, e, f = setup[:3]
    e.cfg.rdp_sm.ca_matched_tp.enabled = True
    e.cfg.rdp_sm.repair.policy = "opposite_ba_v1"
    e.cfg.rdp_sm.repair.opposite_ba = f.ba.cfg
    f.opposite_ba = RepairBidAbsorption(f, f.ba.cfg, e.cfg.rdp_sm.repair.opposite_ba_execution)
    return setup


def prices(f, entry):
    entry = D(entry)
    for token, bid in (("1", entry), ("2", 1-entry-D(".01"))):
        f.apply({"event_type": "book", "market": CID, "asset_id": token,
                 "timestamp": str(f.wall()*1000),
                 "bids": [{"price": str(bid), "size": "80"}],
                 "asks": [{"price": str(bid+D('.01')), "size": "20"}]})


def fill(v, oid, tid, qty=5, status="CONFIRMED"):
    t = v.fill(oid, tid, qty, status=status)
    t["trader_side"] = "MAKER"
    t["maker_orders"][0]["maker_address"] = ADDRESS
    return t


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("token", ["1", "2"])
@pytest.mark.parametrize("entry", [".50", ".64", ".79"])
async def test_matched_posts_same_quantity_before_confirmation_and_pairs(
    env, monkeypatch, tmp_path, entry, token, cached
):
    setup = await tp_setup(env, monkeypatch)
    v, e, f, _, normal = setup[:5]
    e.cfg.rdp_sm.ca_matched_tp.enabled = True
    e.cfg.rdp_sm.repair.policy = "opposite_ba_v1"
    prices(f, entry if token == "1" else str(1-D(entry)-D('.01')))
    companion = (await attach(setup, tmp_path))[0] if cached else None
    native = e.gateway.send_prepared
    opening = []
    observed = []

    async def send(signed, **kw):
        if opening:
            assert opening[0]["status"] == "MATCHED"
            assert not e.confirmed()
            observed.append(True)
        raw = await native(signed, **kw)
        oid = signed.order_ids[0]
        if not opening:
            opening.append(fill(v, oid, "opening", status="MATCHED"))
        else:
            fill(v, oid, "companion")
            opening[0]["status"] = "CONFIRMED"
        v.rows[oid]["status"] = "MATCHED"
        reflect_positions(v)
        return raw

    monkeypatch.setattr(e.gateway, "send_prepared", send)
    try:
        f.apply([trade(f, token), buy(f, token)])
        result, sizing = await asyncio.wait_for(normal.enter(D(0), D(0), "matched-tp"), 5)
        assert observed == [True]
        assert len(v.post_requests) == 2
        quote = e.attempts[1].quote
        assert quote.outcome == ("NO" if token == "1" else "YES")
        assert D(str(quote.price)) == D('.80')-D(entry)
        assert quote.size == 5  # .64 -> .16 x5 is deliberately below $1.
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert e.economic_inventory() == (D(5), D(5))
        assert e.repair_episode is None
        assert sizing["ca_matched_tp"]["reason"] == "TP_ORDER_TERMINAL"
        assert all(row["postOnly"] for body in v.post_requests for row in body)
    finally:
        if companion:
            await finish(setup, companion)
        else:
            await cleanup(setup)


@pytest.mark.parametrize("entry", [".80", ".90"])
async def test_high_price_skips_companion(env, monkeypatch, entry):
    setup = await tp_setup(env, monkeypatch)
    v, e, f, _, normal = setup[:5]
    e.cfg.rdp_sm.ca_matched_tp.enabled = True
    prices(f, entry)
    native = e.gateway.send_prepared

    async def send(signed, **kw):
        raw = await native(signed, **kw)
        oid = signed.order_ids[0]
        v.fill(oid, "high-entry", 5)
        v.rows[oid]["status"] = "MATCHED"
        reflect_positions(v)
        return raw

    monkeypatch.setattr(e.gateway, "send_prepared", send)
    try:
        f.apply([trade(f), buy(f)])
        result, sizing = await asyncio.wait_for(normal.enter(D(0), D(0), "high-entry"), 5)
        assert len(v.post_requests) == 1
        assert result.close_barrier == "SAFE_TO_CONTINUE"
        assert sizing["ca_matched_tp"]["reason"] == "ENTRY_PRICE_TOO_HIGH"
    finally:
        await cleanup(setup)


async def test_no_opening_fill_does_not_post_companion(env, monkeypatch):
    setup = await tp_setup(env, monkeypatch)
    v, e, f, _, normal = setup[:5]
    e.cfg.rdp_sm.ca_matched_tp.enabled = True
    try:
        f.apply([trade(f), buy(f)])
        result, _ = await asyncio.wait_for(normal.enter(D(0), D(0), "no-fill"), 5)
        assert len(v.post_requests) == 1
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert e.economic_inventory() == (D(0), D(0))
    finally:
        await cleanup(setup)


async def test_companion_survives_ca_deadline_then_ba_cancels_before_handoff(env, monkeypatch):
    setup = await tp_setup(env, monkeypatch)
    v, e, f, _, normal = setup[:5]
    e.cfg.rdp_sm.ca_matched_tp.enabled = True
    native = e.gateway.send_prepared
    companion_ready = asyncio.Event()

    async def send(signed, **kw):
        raw = await native(signed, **kw)
        oid = signed.order_ids[0]
        if len(v.post_requests) == 1:
            v.fill(oid, "opening", 5)
            v.rows[oid]["status"] = "MATCHED"
            reflect_positions(v)
        elif len(v.post_requests) == 2:
            companion_ready.set()
        else:
            fill(v, oid, "ba-repair")
            v.rows[oid]["status"] = "MATCHED"
            reflect_positions(v)
        return raw

    monkeypatch.setattr(e.gateway, "send_prepared", send)
    try:
        f.apply([trade(f), buy(f)])
        task = asyncio.create_task(normal.enter(D(0), D(0), "lease"))
        await asyncio.wait_for(companion_ready.wait(), 3)
        await asyncio.sleep(.35)
        tp = e.attempts[1].expected_order_id
        assert v.rows[tp]["status"] == "LIVE"
        assert all(tp not in ids for ids in v.cancels)
        assert not task.done()
        absorption(f, "1")  # Same-side BA cannot take over this opposite TP.
        assert f.ba.events
        await asyncio.sleep(.02)
        assert not f.ba.events  # Long TP leases must keep audit queues draining.
        assert v.rows[tp]["status"] == "LIVE"
        absorption(f, "2")
        result, sizing = await asyncio.wait_for(task, 4)
        assert sizing["ca_matched_tp"]["reason"] == "OPPOSITE_BA_HANDOFF"
        assert any(tp in ids for ids in v.cancels)
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert e.economic_inventory() == (D(5), D(0))
        await e.journal.flush()
        assert any(r["kind"] == "sm_ca_matched_tp_closed" for r in journal_records(e.journal.path))
        setup[3].account = (await e._snapshot(include_books=False))[3]
        repaired, _ = await OppositeBAController(e, setup[3]).repair(D(5), D(0), "ba-after-tp")
        assert repaired.close_barrier == "SAFE_TO_CONTINUE", repaired.block_reason
        assert e.economic_inventory() == (D(5), D(5))
        assert len(v.post_requests) == 3
        assert not f.opposite_ba.selected or f.opposite_ba.selected.consumed
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("profit", ["0", "1", "-0.2", "NaN", "Infinity"])
def test_profit_config_rejects_invalid_values(profit):
    with pytest.raises(ValidationError):
        RDPCAMatchedTPConfig(profit_per_share=profit)


def test_config_feature_is_opt_in_and_requires_ca_opposite_ba():
    assert not RDPSMConfig().ca_matched_tp.enabled
    with pytest.raises(ValidationError):
        RDPSMConfig(ca_matched_tp={"enabled": True})


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("fault", ["rejected", "lost_ack"])
async def test_companion_post_failure_is_not_retried_or_assumed_absent(
    env, monkeypatch, tmp_path, cached, fault
):
    setup = await tp_setup(env, monkeypatch)
    v, e, f, _, normal, wire = setup[:6]
    companion = (await attach(setup, tmp_path))[0] if cached else None
    native = e.gateway.send_prepared

    async def send(signed, **kw):
        if v.post_requests:
            wire.reject_index = 0 if fault == "rejected" else None
            wire.fail_ack = fault == "lost_ack"
        raw = await native(signed, **kw)
        if len(v.post_requests) == 1:
            oid = signed.order_ids[0]
            fill(v, oid, "opening")
            v.rows[oid]["status"] = "MATCHED"
            reflect_positions(v)
        return raw

    monkeypatch.setattr(e.gateway, "send_prepared", send)
    try:
        f.apply([trade(f), buy(f)])
        result, _ = await asyncio.wait_for(normal.enter(D(0), D(0), "post-fault"), 5)
        assert len(v.post_requests) == 2
        assert e.economic_inventory() == (D(5), D(0))
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        a = e.attempts[1]
        if fault == "rejected":
            assert a.outcome == "REJECTED" and a.expected_order_id not in e.known
        else:
            assert a.outcome == "VERIFIED" and a.expected_order_id in e.known
            assert any(a.expected_order_id in ids for ids in v.cancels)
            assert v.rows[a.expected_order_id]["status"] == "CANCELED"
    finally:
        if companion:
            await finish(setup, companion)
        else:
            await cleanup(setup)


async def test_partial_opening_accumulates_without_upsize_or_duplicate_post(env, monkeypatch):
    setup = await tp_setup(env, monkeypatch)
    v, e, f, _, normal = setup[:5]
    native = e.gateway.send_prepared
    ready = asyncio.Event()

    async def send(signed, **kw):
        raw = await native(signed, **kw)
        oid = signed.order_ids[0]
        if len(v.post_requests) == 1:
            fill(v, oid, "partial-a", 2, status="MATCHED")
            ready.set()
        else:
            fill(v, oid, "companion")
            for t in v.trades:
                t["status"] = "CONFIRMED"
            v.rows[oid]["status"] = "MATCHED"
        reflect_positions(v)
        return raw

    monkeypatch.setattr(e.gateway, "send_prepared", send)
    task = None
    try:
        f.apply([trade(f), buy(f)])
        task = asyncio.create_task(normal.enter(D(0), D(0), "partial"))
        await asyncio.wait_for(ready.wait(), 2)
        await asyncio.sleep(.06)
        assert len(v.post_requests) == 1
        oid = e.attempts[0].expected_order_id
        fill(v, oid, "partial-b", 3, status="MATCHED")
        v.rows[oid]["status"] = "MATCHED"
        reflect_positions(v)
        result, _ = await asyncio.wait_for(task, 4)
        assert len(v.post_requests) == 2 and e.attempts[1].quote.size == 5
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert e.economic_inventory() == (D(5), D(5))
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await cleanup(setup)


async def test_failed_opening_after_companion_fill_keeps_reverse_residual_owned(env, monkeypatch):
    setup = await tp_setup(env, monkeypatch)
    v, e, f, _, normal = setup[:5]
    native = e.gateway.send_prepared
    opening = []

    async def send(signed, **kw):
        raw = await native(signed, **kw)
        oid = signed.order_ids[0]
        if len(v.post_requests) == 1:
            opening.append(fill(v, oid, "opening-fails", status="MATCHED"))
        else:
            fill(v, oid, "companion-survives")
            opening[0]["status"] = "FAILED"
            v.positions = [{"token_id": "2", "current_size": "5", "avg_price": ".3"}]
            v.capital["balance"] = "98500000"
        v.rows[oid]["status"] = "MATCHED"
        return raw

    monkeypatch.setattr(e.gateway, "send_prepared", send)
    try:
        f.apply([trade(f), buy(f)])
        result, _ = await asyncio.wait_for(normal.enter(D(0), D(0), "failed-opening"), 5)
        assert len(v.post_requests) == 2
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert e.economic_inventory() == (D(0), D(5))
        assert e.repair_episode is not None
        assert e.repair_episode["side"] == "NO"
        assert e.repair_episode["original_qty"] == "5.0"
    finally:
        await cleanup(setup)


async def test_companion_fill_during_ba_cancel_is_counted_before_new_repair(env, monkeypatch):
    setup = await tp_setup(env, monkeypatch)
    v, e, f, _, normal = setup[:5]
    native = e.gateway.send_prepared
    ready = asyncio.Event()

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

    def cancel(venue):
        if len(e.attempts) > 1:
            oid = e.attempts[1].expected_order_id
            if oid in venue.rows and venue.rows[oid]["status"] == "CANCELED" and not venue.rows[oid]["associate_trades"]:
                fill(venue, oid, "cancel-race", 2)
                reflect_positions(venue)

    monkeypatch.setattr(e.gateway, "send_prepared", send)
    v.on_cancel = cancel
    task = None
    try:
        f.apply([trade(f), buy(f)])
        task = asyncio.create_task(normal.enter(D(0), D(0), "cancel-race"))
        await asyncio.wait_for(ready.wait(), 2)
        absorption(f, "2")
        result, sizing = await asyncio.wait_for(task, 4)
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert len(v.post_requests) == 2
        assert e.economic_inventory() == (D(5), D(2))
        assert e.repair_episode["side"] == "YES"
        assert e.repair_episode["original_qty"] == "3.0"
        assert sizing["ca_matched_tp"]["reason"] == "OPPOSITE_BA_HANDOFF"
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await cleanup(setup)


@pytest.mark.parametrize("fault", ["insufficient_funds", "disconnect_signing", "cutoff_signing", "partial_below_minimum"])
async def test_companion_never_bypasses_funding_market_health_or_minimum_shares(env, monkeypatch, fault):
    setup = await tp_setup(env, monkeypatch)
    v, e, f, lifecycle, normal = setup[:5]
    native_send, native_prepare = e.gateway.send_prepared, e.gateway.prepare

    async def send(signed, **kw):
        raw = await native_send(signed, **kw)
        oid = signed.order_ids[0]
        fill(v, oid, "opening", 2 if fault == "partial_below_minimum" else 5)
        if fault != "partial_below_minimum":
            v.rows[oid]["status"] = "MATCHED"
        if fault == "insufficient_funds":
            lifecycle.account["basic_spendable_budget_usd"] = D('2.50')
        reflect_positions(v)
        return raw

    async def prepare(*args, **kw):
        signed = await native_prepare(*args, **kw)
        if v.post_requests:
            if fault == "disconnect_signing":
                setup[-2]["running"] = False
                f.invalidate("WS_DISCONNECTED")
            elif fault == "cutoff_signing":
                e.official_time = int(e.slug.rsplit('-', 1)[1])+240
                e.time_anchor = e.monotonic()
        return signed

    monkeypatch.setattr(e.gateway, "send_prepared", send)
    monkeypatch.setattr(e.gateway, "prepare", prepare)
    try:
        f.apply([trade(f), buy(f)])
        result, sizing = await asyncio.wait_for(normal.enter(D(0), D(0), "safe-skip"), 5)
        assert len(v.post_requests) == 1
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert result.yes_inventory == (2 if fault == "partial_below_minimum" else 5)
        if fault == "insufficient_funds":
            assert sizing["ca_matched_tp"]["reason"] == "CA_MATCHED_TP_INSUFFICIENT_FUNDS"
        assert all(not a.post_started for a in e.attempts[1:])
    finally:
        await cleanup(setup)
