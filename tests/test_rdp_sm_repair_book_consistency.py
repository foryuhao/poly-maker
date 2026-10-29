"""REPAIR keeps a submitted fixed quote through depth/BBO misalignment."""

import asyncio
import time
from dataclasses import replace
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from test_rdp_sm_ba import depth
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_obi_execution import cleanup
from test_rdp_sm_opposite_ba import absorption
from test_rdp_sm_opposite_ba_loop import until
from test_rdp_sm_repair import env as env
from test_rdp_sm_repair import reflect_positions
from test_rdp_sm_repair_ba_tolerance import book, fixture, tolerant_setup


def mismatch(f, kind="depth", *, apply=None):
    b = f.books["2"]
    if kind == "depth":
        row = depth(f, b.best_bid_size, "2")
        row["price_changes"][0].update(best_bid=str(b.best_bid + b.tick), best_ask=str(b.best_ask))
    else:
        row = {
            "event_type": "best_bid_ask",
            "market": f.meta.condition_id,
            "asset_id": "2",
            "timestamp": str(f.wall() * 1000),
            "best_bid": str(b.best_bid + b.tick),
            "best_ask": str(b.best_ask),
        }
    (apply or f.apply)(row)


def claimed(*, consumed):
    c, f, clock = fixture()
    clock.now = 0.01
    absorption(f, "2")
    signal = c.episodes.evaluate(f.snapshot(), outcome="NO")
    claim = c.episodes.claim(signal)
    assert claim
    ep = c.episodes.selected
    if consumed:
        c.episodes.consume(claim)
    intent = SimpleNamespace(claim=claim, yes=D(5), no=D(0))
    return c, f, clock, ep, intent


@pytest.mark.parametrize("kind", ["depth", "bbo"])
def test_unsubmitted_claim_still_rejects_inconsistent_book(kind):
    c, f, _, ep, intent = claimed(consumed=False)
    mismatch(f, kind)
    assert ep.invalid_reason
    assert not c.episodes.current(intent.claim)
    assert not c.candidates(f.snapshot(), D(5), D(0), "before-post")
    assert not any(
        e["event"] == "BOOK_CHECK_IGNORED_AFTER_SUBMIT" for e in c.episodes.drain_execution()
    )


@pytest.mark.parametrize("kind", ["depth", "bbo"])
def test_sent_opportunity_survives_consistency_failure_without_renewal_or_reuse(kind):
    c, f, _, ep, intent = claimed(consumed=True)
    deadline = ep.order_deadline
    mismatch(f, kind)
    assert ep.invalid_reason is None
    assert c.episodes.active_reason(intent.claim, f.snapshot()) is None
    assert c.active_snapshot(intent).valid
    assert ep.order_deadline == deadline
    assert not c.episodes.evaluate(f.snapshot(), outcome="NO").direction
    events = c.episodes.drain_execution()
    assert any(e["event"] == "BOOK_CHECK_IGNORED_AFTER_SUBMIT" for e in events)
    for _ in range(20):
        c.episodes.active_reason(intent.claim, f.snapshot())
    assert not c.episodes.drain_execution()  # Bound repeated diagnostic volume.


@pytest.mark.parametrize("move", ["-.01", ".06"])
def test_trusted_down_break_still_closes_but_upward_move_has_no_cap(move):
    c, f, clock, ep, intent = claimed(consumed=True)
    b = f.books["2"]
    bid = ep.anchor_bid_price + D(move)
    f.books["2"] = replace(
        b, bids=((bid, D(80)),), asks=((bid + D(".02"), D(20)),), reported_bid=b.best_bid
    )
    assert c.episodes.active_reason(intent.claim, f.snapshot()) is None
    assert ep.invalid_reason is None
    clock.now = 0.02
    f.apply(book(f, bid, bid + D(".02")))
    assert c.episodes.active_reason(intent.claim, c.active_snapshot(intent)) == (
        "REPAIR_BA_BID_BELOW_ANCHOR" if D(move) < 0 else None
    )


@pytest.mark.parametrize("fault", ["deadline", "stale", "tick", "disconnect", "crossed"])
def test_consistency_exemption_does_not_hide_other_close_conditions(fault):
    c, f, clock, ep, intent = claimed(consumed=True)
    mismatch(f)
    if fault == "deadline":
        clock.now = ep.order_deadline
    elif fault == "stale":
        clock.now = 0.3
    elif fault == "tick":
        f.tick_verified.pop("2")
    elif fault == "disconnect":
        f.invalidate("WS_DISCONNECTED")
    else:
        clock.now = 0.02
        f.apply(book(f, ep.anchor_bid_price, ep.anchor_bid_price))
    assert c.episodes.active_reason(intent.claim, c.active_snapshot(intent)) is not None


def test_pending_top_cannot_mask_other_side_staleness_in_active_snapshot():
    c, f, clock, _, intent = claimed(consumed=True)
    mismatch(f)
    # Skip the tolerated top mismatch while still inspecting both books.
    f.books["1"] = replace(f.books["1"], received_mono=clock.now - 1)
    assert c.active_snapshot(intent).reason == "WS_BOOK_STALE"


@pytest.mark.parametrize("stage", ["inflight", "ack"])
@pytest.mark.parametrize("filled", [False, True])
async def test_real_order_keeps_original_price_until_deadline_despite_pending_top(
    env, monkeypatch, stage, filled
):
    setup, c = await tolerant_setup(env, monkeypatch)
    v, e, f, _, _, wire = setup[:6]
    driver = None
    try:
        absorption(f, "2")
        ep = next(iter(c.episodes.opportunities.values()))
        quoted = f.books["2"].best_ask - f.books["2"].tick
        apply = f.apply
        enabled = False

        def persist_mismatch(*args, **kwargs):
            apply(*args, **kwargs)
            if enabled:
                mismatch(f, apply=apply)

        monkeypatch.setattr(f, "apply", persist_mismatch)

        def inject():
            nonlocal enabled
            enabled = True
            mismatch(f, apply=apply)
            assert f.snapshot(ba_cfg=c.ba_config).reason == "WS_TOP_PENDING"

        if stage == "inflight":

            def delayed_post():
                inject()
                time.sleep(0.04)  # Let the real watcher see the mismatch before ACK.

            wire.delay = delayed_post
        else:

            async def after_ack():
                await until(lambda: len(e.attempts) >= 2 and e.attempts[-1].outcome == "ACK")
                inject()

            driver = asyncio.create_task(after_ack())

        if filled:

            def fill_on_close(venue):
                oid = e.attempts[-1].expected_order_id
                if not venue.rows[oid]["associate_trades"]:
                    venue.fill(oid, "consistent-fixed-fill", 5)
                reflect_positions(venue)

            v.on_cancel = fill_on_close
        result, sizing = await c.repair(*e.economic_inventory(), "keep-pending-top")
        if driver:
            await driver
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert D(str(result.requested_quotes[0].price)) == quoted
        assert sizing["quote_fixed"] and len(v.post_requests) == 2
        assert result.fill_deadline_monotonic == ep.order_deadline
        # Ignore opening DELETEs, which occurred before this repair POST.
        post = next(t[2] for t in reversed(wire.timeline) if t[0] == "POST")
        deletes = [t for t in wire.timeline if t[0] == "DELETE" and t[2] >= post]
        assert deletes and all(t[2] >= ep.order_deadline for t in deletes)
        logs = journal_records(e.journal.path)
        close = next(
            r["data"]["payload"]
            for r in logs
            if r["kind"] in {"sm_ba_close_requested", "sm_obi_close_requested"}
            and r["data"]["payload"]["window_id"] == "keep-pending-top"
        )
        assert close["reason"] == "ORDER_DEADLINE"
        assert any(
            r["kind"] == "sm_opposite_ba_execution"
            and r["data"]["payload"]["event"] == "BOOK_CHECK_IGNORED_AFTER_SUBMIT"
            for r in logs
        )
        assert e.economic_inventory() == (D(5), D(5) if filled else D(0))
        assert await c.repair(*e.economic_inventory(), "no-reuse") is None
        assert len(v.post_requests) == 2
    finally:
        if driver and not driver.done():
            driver.cancel()
            await asyncio.gather(driver, return_exceptions=True)
        await cleanup(setup)


async def test_real_native_post_guard_still_rejects_a_new_inconsistent_quote(env, monkeypatch):
    setup, c = await tolerant_setup(env, monkeypatch)
    v, e, f = setup[:3]
    try:
        absorption(f, "2")
        send = e.gateway.send_prepared

        async def invalidate_before_guard(*args, **kwargs):
            mismatch(f)
            return await send(*args, **kwargs)

        monkeypatch.setattr(e.gateway, "send_prepared", invalidate_before_guard)
        result, _ = await c.repair(*e.economic_inventory(), "strict-new-order")
        assert len(v.post_requests) == 1  # Only the prior opening reached POST.
        assert not any(a["post_started"] for a in result.placement_results)
        assert e.economic_inventory() == (D(5), D(0))
    finally:
        await cleanup(setup)
