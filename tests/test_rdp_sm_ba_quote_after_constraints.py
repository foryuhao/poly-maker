"""BA repair prices after SDK constraints; original admission and leases remain."""

import asyncio
from decimal import Decimal as D

import pytest

from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_obi_execution import cleanup, setup_obi
from test_rdp_sm_repair import env as env
from test_rdp_sm_repair_ba_tolerance import absorption, book, tolerant_setup


@pytest.mark.parametrize("stage", ["tick_read", "version_read", "warm_verify"])
async def test_real_fixed_maker_uses_book_after_constraint_preparation(env, monkeypatch, stage):
    setup, controller = await tolerant_setup(env, monkeypatch)
    venue, e, feed, lifecycle, _, wire, _, state = setup[:8]
    client = e.gateway._client
    native_read, native_verify = lifecycle._read_preparation_constraint, feed.verify_ticks
    native_candidates = controller.candidates
    events = []
    new_ask = feed.books["2"].best_ask + D(".02")
    expected_price = float(new_ask-feed.books["2"].tick)

    def move():
        state["no_ask"] = str(new_ask)
        feed.apply(book(feed, feed.books["2"].best_bid, new_ask))
        events.append("constraints_done")

    async def read(function, *args):
        result = await native_read(function, *args)
        if (stage == "tick_read" and function.__name__ == "get_tick_size" and args == ("2",)
                or stage == "version_read" and function.__name__ == "__resolve_version"):
            move()
        return result

    def verify(ticks):
        result = native_verify(ticks)
        if stage == "warm_verify":
            move()
        return result

    def candidates(*args):
        events.append("quote")
        return native_candidates(*args)

    if stage == "tick_read":
        client._ClobClient__tick_sizes.pop("2")
    elif stage == "version_read":
        client._ClobClient__cached_version = None
    monkeypatch.setattr(lifecycle, "_read_preparation_constraint", read)
    monkeypatch.setattr(feed, "verify_ticks", verify)
    monkeypatch.setattr(controller, "candidates", candidates)
    try:
        absorption(feed, "2")
        ep = controller.episodes.selected
        if ep is None:
            controller.select(lifecycle.snapshot(controller, *e.economic_inventory()))
            ep = controller.episodes.selected
        original_t0, deadline, episode_id = ep.signal_start_mono, ep.order_deadline, ep.episode_id
        slot = await lifecycle.prepare(controller, *e.economic_inventory(), "late-quote")
        assert slot is not None, (lifecycle.wait_reason, events, controller.signal_block_reason,
                                  controller.quote_block_reason, feed.snapshot())
        assert events == ["constraints_done", "quote"]
        assert slot.snapshot.no.best_ask == new_ask
        assert slot.candidates[0].quotes[0].price == expected_price
        assert ep.signal_start_mono == original_t0 and ep.order_deadline == deadline
        assert not ep.consumed and len(venue.post_requests) == 1

        def after_post_guard():
            state["no_ask"] = str(new_ask+D(".03"))
            feed.apply(book(feed, feed.books["2"].best_bid, new_ask+D(".03")))

        wire.delay = after_post_guard
        result, _ = await lifecycle.activate(controller, slot)
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert result.requested_quotes[0].price == expected_price
        assert result.requested_quotes[0].size == 5
        assert result.t0_monotonic == original_t0
        assert result.fill_deadline_monotonic == deadline
        assert ep.episode_id == episode_id and ep.consumed
        assert len(venue.post_requests) == 2
        assert venue.post_requests[-1][0]["postOnly"] is True
        assert await controller.repair(*e.economic_inventory(), "same-ba-no-repost") is None
        assert len(venue.post_requests) == 2
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("fault", ["crossed", "disconnect", "tick", "expired", "new_episode", "entry_stale"])
async def test_invalidated_during_constraints_never_signs_or_writes_candidate(env, monkeypatch, fault):
    setup, controller = await tolerant_setup(env, monkeypatch)
    venue, e, feed, lifecycle = setup[:4]
    client = e.gateway._client
    native_read, native_prepare = lifecycle._read_preparation_constraint, e.gateway.prepare
    signed = []
    client._ClobClient__cached_version = None
    if fault == "entry_stale":
        controller.ba_config.max_entry_age_ms = 20

    async def read(function, *args):
        result = await native_read(function, *args)
        if function.__name__ != "__resolve_version":
            return result
        if fault == "crossed":
            bid = feed.books["2"].best_bid
            feed.apply(book(feed, bid, bid))
        elif fault == "disconnect":
            feed.invalidate("WS_DISCONNECTED")
        elif fault == "tick":
            feed.apply({"event_type": "tick_size_change", "market": feed.meta.condition_id,
                        "asset_id": "2", "old_tick_size": str(feed.books["2"].tick),
                        "new_tick_size": ".1"})
        elif fault == "expired":
            await asyncio.sleep(.31)
        elif fault == "entry_stale":
            await asyncio.sleep(.04)
        else:
            feed.opposite_ba.invalidate("TEST_EPISODE_REPLACED", "2")
            absorption(feed, "2")
        return result

    async def prepare(*args, **kwargs):
        signed.append(True)
        return await native_prepare(*args, **kwargs)

    monkeypatch.setattr(lifecycle, "_read_preparation_constraint", read)
    monkeypatch.setattr(e.gateway, "prepare", prepare)
    try:
        absorption(feed, "2")
        slot = await lifecycle.prepare(controller, *e.economic_inventory(), "invalid-after-read")
        assert slot is None
        assert not signed and len(venue.post_requests) == 1
        await e.journal.flush()
        assert not any(r["kind"] == "sm_prepared"
                       and r["data"]["payload"]["window_id"] == "invalid-after-read"
                       for r in journal_records(e.journal.path))
        if fault == "entry_stale":
            assert lifecycle.wait_reason[0] == "BA_ENTRY_STALE"
    finally:
        await cleanup(setup)


async def test_no_ba_does_not_start_constraint_reads(env, monkeypatch):
    setup, controller = await tolerant_setup(env, monkeypatch)
    venue, e, _, lifecycle = setup[:4]
    calls = []

    async def unexpected_read(*args):
        calls.append(args)
        pytest.fail("no BA must not warm SDK constraints")

    monkeypatch.setattr(lifecycle, "_read_preparation_constraint", unexpected_read)
    e.gateway._client._ClobClient__cached_version = None
    try:
        assert await lifecycle.prepare(controller, *e.economic_inventory(), "no-ba") is None
        assert not calls and len(venue.post_requests) == 1
    finally:
        await cleanup(setup)


async def test_normal_keeps_quote_before_constraint_order(env, monkeypatch):
    setup = await setup_obi(env, monkeypatch)
    venue, _, _, lifecycle, normal = setup[:5]
    native_candidates, native_read = normal.candidates, lifecycle._read_preparation_constraint
    events = []

    def candidates(*args):
        events.append("quote")
        return native_candidates(*args)

    async def read(function, *args):
        events.append(function.__name__)
        return await native_read(function, *args)

    monkeypatch.setattr(normal, "candidates", candidates)
    monkeypatch.setattr(lifecycle, "_read_preparation_constraint", read)
    try:
        assert await lifecycle.prepare(normal, D(0), D(0), "normal-unchanged") is not None
        assert events[0] == "quote" and "get_tick_size" in events[1:]
        assert not venue.post_requests
    finally:
        await cleanup(setup)
