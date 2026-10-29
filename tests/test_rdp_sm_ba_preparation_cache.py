"""BA repair cached preparation avoids worker hops without relaxing guards."""
from decimal import Decimal as D

import pytest

from polymaker.rdp_sm import _Issue
from test_rdp_sm_obi_execution import cleanup
from test_rdp_sm_opposite_ba import absorption, setup_exit
from test_rdp_sm_repair import env as env


def track_reads(monkeypatch, lifecycle):
    calls = []
    native = lifecycle._read_preparation_constraint

    async def read(function, *args):
        calls.append(function.__name__)
        return await native(function, *args)

    monkeypatch.setattr(lifecycle, "_read_preparation_constraint", read)
    return calls


async def test_warm_ba_repair_avoids_all_three_constraint_worker_round_trips(env, monkeypatch):
    setup, controller = await setup_exit(env, monkeypatch)
    venue, executor, feed, lifecycle = setup[:4]
    try:
        calls = track_reads(monkeypatch, lifecycle)
        absorption(feed, "2")
        ep = feed.opposite_ba.states["2"]
        deadline = ep.order_deadline
        slot = await lifecycle.prepare(controller, *executor.economic_inventory(), "warm")
        assert slot is not None
        assert calls == []
        quote = slot.candidates[0].quotes[0]
        assert quote.size == 5 and quote.outcome == "NO"
        assert D(str(quote.price)) == ep.anchor_bid_price
        assert ep.order_deadline == deadline and not ep.consumed
        assert len(venue.post_requests) == 1  # Only the prior opening reached POST.
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("fault", ["missing_tick", "wrong_tick", "missing_version"])
async def test_incomplete_cache_keeps_original_constraint_read_path(env, monkeypatch, fault):
    setup, controller = await setup_exit(env, monkeypatch)
    venue, executor, feed, lifecycle = setup[:4]
    client = executor.gateway._client
    try:
        if fault == "missing_tick":
            client._ClobClient__tick_sizes.pop("2")
        elif fault == "wrong_tick":
            client._ClobClient__tick_sizes["2"] = "0.1"
        else:
            client._ClobClient__cached_version = None
        calls = track_reads(monkeypatch, lifecycle)
        absorption(feed, "2")
        slot = await lifecycle.prepare(controller, *executor.economic_inventory(), fault)
        assert slot is not None
        assert calls == ["get_tick_size", "get_tick_size", "__resolve_version"]
        assert all(D(client._ClobClient__tick_sizes[t]) == b.tick for t, b in feed.books.items())
        assert len(venue.post_requests) == 1
    finally:
        await cleanup(setup)


async def test_unverified_ws_tick_still_blocks_preparation(env, monkeypatch):
    setup, controller = await setup_exit(env, monkeypatch)
    venue, executor, feed, lifecycle = setup[:4]
    try:
        absorption(feed, "2")
        feed.tick_verified.pop("2")
        assert lifecycle._cached_repair_ticks(controller, executor.gateway._client) is None
        assert await lifecycle.prepare(controller, *executor.economic_inventory(), "unverified") is None
        assert len(venue.post_requests) == 1
    finally:
        await cleanup(setup)


async def test_tick_change_after_cache_lookup_still_rejects_prepared_candidate(env, monkeypatch):
    setup, controller = await setup_exit(env, monkeypatch)
    venue, executor, feed, lifecycle = setup[:4]
    verify = feed.verify_ticks
    try:
        absorption(feed, "2")
        calls = track_reads(monkeypatch, lifecycle)

        def changed(ticks):
            old = feed.books["2"].tick
            feed.apply({"event_type": "tick_size_change", "market": feed.meta.condition_id,
                        "asset_id": "2", "old_tick_size": str(old), "new_tick_size": "0.1"})
            return verify(ticks)

        monkeypatch.setattr(feed, "verify_ticks", changed)
        assert await lifecycle.prepare(controller, *executor.economic_inventory(), "changed") is None
        assert calls == [] and len(venue.post_requests) == 1
    finally:
        await cleanup(setup)


async def test_fast_path_keeps_sdk_signing_version_check(env, monkeypatch):
    setup, controller = await setup_exit(env, monkeypatch)
    venue, executor, feed, lifecycle = setup[:4]
    try:
        absorption(feed, "2")
        calls = track_reads(monkeypatch, lifecycle)
        monkeypatch.setattr(executor.gateway._client, "_ClobClient__resolve_version", lambda: 1)
        with pytest.raises(ValueError, match="PREPARED_SIGNING_MODE_UNSUPPORTED"):
            await lifecycle.prepare(controller, *executor.economic_inventory(), "version-changed")
        assert calls == [] and len(venue.post_requests) == 1
    finally:
        await cleanup(setup)


async def test_fast_path_does_not_bypass_previous_window_proof(env, monkeypatch):
    setup, controller = await setup_exit(env, monkeypatch)
    venue, executor, feed, lifecycle = setup[:4]
    try:
        absorption(feed, "2")
        executor.invalidate("CLOSE_EVIDENCE_INVALIDATED")
        with pytest.raises(_Issue, match="PREVIOUS_WINDOW_UNRESOLVED"):
            await lifecycle.prepare(controller, *executor.economic_inventory(), "blocked")
        assert len(venue.post_requests) == 1
    finally:
        await cleanup(setup)
