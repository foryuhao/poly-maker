"""Single-sided NORMAL admission with the shared Sigma warmup still required."""

import asyncio
import math
from decimal import Decimal

import pytest

from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_obi_execution import cleanup, setup_obi
from test_rdp_sm_obi_execution import env as env


@pytest.mark.parametrize("direction,token", [("YES", "1"), ("NO", "2")])
async def test_sigma_warmup_then_only_signal_side_is_signed_and_posted(env, monkeypatch, direction, token):
    setup = await setup_obi(env, monkeypatch)
    venue, executor, feed, _, normal, _, _, pump, _ = setup
    try:
        pump["direction"] = direction
        await asyncio.sleep(0.025)
        assert await normal.enter(Decimal(0), Decimal(0), "warming") is None
        assert not venue.post_requests
        assert normal.quote_block_reason == "NORMAL_VOLATILITY_UNKNOWN"

        # Supply ten observed seconds without waiting in real time. The real
        # estimator, immutable snapshot guards, signing and POST path all run.
        now = feed.monotonic()
        wall = feed.wall()
        with feed.lock:
            feed.probability_points.extendleft(reversed([
                (math.floor(wall)-age, now-age, .51, feed.generation)
                for age in range(11, 0, -1)
            ]))
        result, sizing = await normal.enter(Decimal(0), Decimal(0), "single")
        assert result.close_barrier == "SAFE_TO_CONTINUE"
        assert len(venue.post_requests) == 1
        assert len(venue.post_requests[0]) == 1
        assert venue.post_requests[0][0]["order"]["tokenId"] == token
        assert len(result.requested_quotes) == 1
        assert result.requested_quotes[0].outcome == direction
        assert sizing["order_policy"] == "SIGNAL_SIDE_ONLY"
        opposite = "no" if direction == "YES" else "yes"
        assert Decimal(sizing[f"planned_{opposite}_size"]) == 0
        openings = [r["data"]["payload"] for r in journal_records(executor.journal.path)
                    if r["kind"] == "sm_repair_episode"
                    and r["data"]["payload"].get("event") == "OPENING_SUBMITTED"]
        assert len(openings) == 1 and not openings[0]["dual_normal"]

        feed.connect()
        assert not feed.probability_points
        assert normal.volatility(feed.snapshot()).sigma is None
    finally:
        await cleanup(setup)
