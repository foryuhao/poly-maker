"""Near-minimum TP quantities through real signing, fills and close proofs."""

import asyncio
from decimal import Decimal as D

import pytest

from polymaker.rdp_sm_ca_matched_tp import tp_buy_size
from test_rdp_sm_ca_entry import confirm_ca
from test_rdp_sm_ca_matched_tp import fill, tp_setup
from test_rdp_sm_cached_reads import attach, finish
from test_rdp_sm_obi_execution import cleanup
from test_rdp_sm_opposite_ba import absorption
from test_rdp_sm_repair import env as env
from test_rdp_sm_repair import reflect_positions
from test_rdp_sm_restored_tp import filled, restored_setup


@pytest.mark.parametrize(
    "quantity,minimum,maximum,expected",
    [
        ("4.990314", "5", "15", "5.00"),
        ("4.994914", "5", "15", "5.00"),
        ("4.99", "5", "15", "5.00"),
        ("4.989999", "5", "15", None),
        ("4.98", "5", "15", None),
        ("3", "5", "15", None),
        ("0", "5", "15", None),
        ("5.000001", "5", "15", "5.01"),
        ("5.000001", "5", "5", "5.00"),
        ("10", "5", "5", "5.00"),
        ("5.990314", "6", "15", "6.00"),
        ("5.98", "6", "15", None),
        ("5", "5", "4.99", None),
    ],
)
def test_tp_quantity_boundaries(quantity, minimum, maximum, expected):
    qty = tp_buy_size(D(quantity), D(minimum), D(maximum))
    assert qty == (D(expected) if expected is not None else None)


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("token", ["1", "2"])
@pytest.mark.parametrize("quantity", ["4.990314", "4.994914", "4.99"])
async def test_near_five_matched_posts_five_before_confirmed_and_keeps_real_residual(
    env, monkeypatch, tmp_path, cached, token, quantity
):
    setup = await tp_setup(env, monkeypatch)
    v, e, feed, _, normal = setup[:5]
    e.cfg.rdp_sm.ca_matched_tp.restore_after_ba = True
    e.cfg.rdp_sm.ca_matched_tp.profit_per_share = D(".05")
    companion = (await attach(setup, tmp_path))[0] if cached else None
    native = e.gateway.send_prepared
    opening = []

    async def send(signed, **kw):
        raw = await native(signed, **kw)
        oid = signed.order_ids[0]
        if not opening:
            opening.append(fill(v, oid, "near-five-opening", float(quantity), status="MATCHED"))
            # The actual opening is short of five; its remaining quantity stays live.
            v.rows[oid]["status"] = "LIVE"
        else:
            assert opening[0]["status"] == "MATCHED"
            assert not e.confirmed()
            assert e.attempts[-1].quote.size == 5
            fill(v, oid, "rounded-tp", 5)
            v.rows[oid]["status"] = "MATCHED"
            opening[0]["status"] = "CONFIRMED"
        reflect_positions(v)
        return raw

    monkeypatch.setattr(e.gateway, "send_prepared", send)
    try:
        await confirm_ca(feed, token)
        result, _ = await asyncio.wait_for(normal.enter(D(0), D(0), "near-five-tp"), 5)
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert len(v.post_requests) == 2
        assert v.rows[e.attempts[0].order_id]["status"] == "CANCELED"
        assert e.attempts[1].quote.size == 5
        assert D(v.post_requests[1][0]["order"]["takerAmount"]) == 5_000_000
        expected = (D(quantity), D(5)) if token == "1" else (D(5), D(quantity))
        assert e.economic_inventory() == expected
        assert abs(result.delta) == pytest.approx(float(D(5) - D(quantity)), abs=1e-12)
        assert D(5) - D(quantity) <= D(".01")
        assert e.repair_episode is None
        assert len(e.economic_fills()) == 2
        assert all(row["postOnly"] for body in v.post_requests for row in body)
    finally:
        if companion:
            await finish(setup, companion)
        else:
            await cleanup(setup)


@pytest.mark.parametrize("quantity", ["4.989999", "4.98", "3"])
async def test_initial_tp_does_not_enlarge_beyond_quantity_tolerance(env, monkeypatch, quantity):
    setup = await tp_setup(env, monkeypatch)
    v, e, feed, _, normal = setup[:5]
    native = e.gateway.send_prepared

    async def send(signed, **kw):
        raw = await native(signed, **kw)
        fill(v, signed.order_ids[0], "short-opening", float(quantity))
        reflect_positions(v)
        return raw

    monkeypatch.setattr(e.gateway, "send_prepared", send)
    try:
        await confirm_ca(feed)
        result, sizing = await asyncio.wait_for(normal.enter(D(0), D(0), "too-short-tp"), 5)
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert len(v.post_requests) == 1
        assert e.economic_inventory() == (D(quantity), D(0))
        assert sizing["ca_matched_tp"]["reason"] == "WAIT_MINIMUM_MATCHED_QUANTITY"
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("partial", [".009686", ".01", ".010001", ".02"])
async def test_ba_partial_then_restored_tp_uses_tolerance_and_retains_reverse_dust(
    env, monkeypatch, partial
):
    setup, controller = await restored_setup(env, monkeypatch)
    v, e, feed = setup[:3]
    native = e.gateway.send_prepared

    async def send(signed, **kw):
        raw = await native(signed, **kw)
        oid = signed.order_ids[0]
        if len(v.post_requests) == 2:
            filled(v, oid, "small-ba-fill", float(partial))
        else:
            assert e.attempts[-1].quote.window_id.endswith(":restored-tp")
            assert e.attempts[-1].quote.size == 5
            filled(v, oid, "restored-five", 5)
        return raw

    monkeypatch.setattr(e.gateway, "send_prepared", send)
    try:
        absorption(feed, "2")
        ba, _ = await asyncio.wait_for(controller.repair(*e.economic_inventory(), "small-ba"), 5)
        assert ba.close_barrier == "SAFE_TO_CONTINUE", ba.block_reason
        assert e.economic_inventory() == (D(5), D(partial))
        restored = await asyncio.wait_for(
            controller.repair(*e.economic_inventory(), "near-five-restoration"), 5
        )
        if D(partial) <= D(".01"):
            result, sizing = restored
            assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
            assert sizing["execution_role"] == "RESTORED_TP"
            assert len(v.post_requests) == 3
            assert e.economic_inventory() == (D(5), D(5) + D(partial))
            assert result.delta == pytest.approx(-float(partial), abs=1e-12)
            assert e.repair_episode is None
            assert await controller.repair(*e.economic_inventory(), "dust-only") is None
            assert len(v.post_requests) == 3
        else:
            assert restored is None
            assert len(v.post_requests) == 2
            assert e.economic_inventory() == (D(5), D(partial))
    finally:
        await cleanup(setup)
